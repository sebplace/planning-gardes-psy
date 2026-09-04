"""Recherche d'échange : le pendant de la reprise, côté « je propose la mienne ».

Arbitrages du client du 04/09/2026 (lot 3) :

1. « Reprise » et « Échange » restent **deux opérations distinctes** ;
2. « Échange » lance une **recherche à l'intérieur du trimestre** ; ce n'est pas
   un formulaire d'enregistrement d'un accord trouvé hors application ;
3. à partir de la garde à céder, on cherche les personnes **explicitement
   vertes** à cette date qui détiennent, dans le même trimestre, une garde de
   même nature que le demandeur peut reprendre ;
4. même nature = même ligne, catégorie comptable, durée/classe horaire, mode de
   couverture et statut ; l'éligibilité croisée des deux personnes est vérifiée
   **séparément** ;
5. les deux accords sont recueillis, puis les deux gardes sont **revalidées
   atomiquement** avant officialisation ;
6. les échanges valides sont classés par maximin d'espacement, trimestres
   adjacents inclus ; tirage auditable **uniquement** en cas d'égalité parfaite ;
7. aucun responsable n'intervient dans un parcours conforme ;
8. toute personne titulaire d'une garde future peut demander une reprise sans
   exposer de motif ; un commentaire éventuel reste facultatif et non clinique ;
9. les fenêtres de collecte suivent la proximité de la garde.

Rien n'est écrit par cette recherche : elle **propose**, elle ne décide pas.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import secrets
from dataclasses import dataclass
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..engine.swap_ranking import CandidatEchange, Creneau, classer, ex_aequo_de_tete
from ..models import (
    Assignment,
    Color,
    CoveragePost,
    GardeOccurrence,
    GardeType,
    ProfessionalProfile,
    Quarter,
    ScheduleState,
    ScheduleVersion,
)
from . import audit_service, engine_bridge
from .clock import Clock
from .swap_service import check_equivalence

#: Fenêtres de collecte selon la proximité de la garde (lot 3, point 9).
#: Bornes exprimées en heures avant le début de la garde.
FENETRES_COLLECTE = (
    # (délai minimal restant, durée de la fenêtre, libellé)
    (14 * 24, 72, "plus de 14 jours"),
    (7 * 24, 48, "de 7 à 14 jours"),
    (3 * 24, 24, "de 3 à 7 jours"),
)
#: En deçà de 72 h, circuit urgent : sollicitations immédiates, fenêtre courte.
FENETRE_URGENTE_HEURES = 3
LIBELLE_URGENT = "moins de 72 heures — circuit urgent"

ALGORITHME_TIRAGE = "HMAC-SHA256(graine, empreinte_candidats) mod n"


class SwapSearchError(Exception):
    pass


# --------------------------------------------------------------------------- #
# Fenêtre de collecte
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class FenetreCollecte:
    libelle: str
    duree_heures: float
    urgent: bool
    ouvre_a: object
    ferme_a: object

    def as_dict(self) -> dict:
        return {
            "palier": self.libelle,
            "duree_heures": self.duree_heures,
            "circuit_urgent": self.urgent,
            "ouvre_a": self.ouvre_a.isoformat(),
            "ferme_a": self.ferme_a.isoformat(),
        }


def fenetre_pour(debut_de_garde) -> FenetreCollecte:
    """Fenêtre de collecte applicable, jamais au-delà du début de la garde."""
    maintenant = Clock.now()
    restant = (debut_de_garde - maintenant).total_seconds() / 3600.0
    for seuil, duree, libelle in FENETRES_COLLECTE:
        if restant > seuil:
            ferme = min(maintenant + timedelta(hours=duree), debut_de_garde)
            return FenetreCollecte(libelle, duree, False, maintenant, ferme)
    ferme = min(
        maintenant + timedelta(hours=FENETRE_URGENTE_HEURES), debut_de_garde
    )
    return FenetreCollecte(
        LIBELLE_URGENT, FENETRE_URGENTE_HEURES, True, maintenant, ferme
    )


# --------------------------------------------------------------------------- #
# Agendas, trimestres adjacents inclus
# --------------------------------------------------------------------------- #


def trimestres_adjacents(session: Session, quarter: Quarter) -> list[int]:
    """Identifiants du trimestre courant et de ses voisins immédiats.

    Le classement doit consulter les gardes des trimestres adjacents : un
    échange peut créer une concentration juste avant ou juste après la frontière.
    """
    voisins = list(
        session.execute(
            select(Quarter.id).where(
                Quarter.start_date <= quarter.end_date + timedelta(days=100),
                Quarter.end_date >= quarter.start_date - timedelta(days=100),
            )
        ).scalars()
    )
    return sorted(set(voisins) | {quarter.id})


def agenda_de(
    session: Session, profile_id: int, quarter_ids: list[int]
) -> list[Creneau]:
    """Gardes publiées d'une personne sur les trimestres considérés."""
    rows = session.execute(
        select(GardeOccurrence.start_at, GardeOccurrence.end_at, Assignment.id)
        .join(CoveragePost, Assignment.post_id == CoveragePost.id)
        .join(GardeOccurrence, CoveragePost.occurrence_id == GardeOccurrence.id)
        .join(ScheduleVersion, Assignment.schedule_version_id == ScheduleVersion.id)
        .where(
            Assignment.profile_id == profile_id,
            ScheduleVersion.state == ScheduleState.PUBLIE,
            GardeOccurrence.quarter_id.in_(quarter_ids),
        )
    ).all()
    return [
        Creneau(start_at=s, end_at=e, reference=f"affectation:{aid}")
        for s, e, aid in rows
    ]


def _creneau_de(assignment: Assignment) -> Creneau:
    occurrence = assignment.post.occurrence
    return Creneau(
        start_at=occurrence.start_at,
        end_at=occurrence.end_at,
        reference=f"affectation:{assignment.id}",
    )


# --------------------------------------------------------------------------- #
# Recherche
# --------------------------------------------------------------------------- #


@dataclass
class PropositionEchange:
    """Un échange possible, entièrement vérifié, prêt à être soumis aux accords."""

    assignment_cede_id: int
    assignment_repris_id: int
    partenaire_code: str
    partenaire_profile_id: int
    date_cedee: str
    date_reprise: str
    intervalles: dict
    cle_maximin: list

    def as_dict(self) -> dict:
        return {
            "garde_cedee": self.assignment_cede_id,
            "garde_reprise": self.assignment_repris_id,
            "partenaire": self.partenaire_code,
            "date_cedee": self.date_cedee,
            "date_reprise": self.date_reprise,
            **self.intervalles,
        }


@dataclass
class ResultatRecherche:
    fenetre: FenetreCollecte
    propositions: list[PropositionEchange]
    ecartes: list[dict]
    ex_aequo: list[str]

    @property
    def meilleure(self) -> PropositionEchange | None:
        return self.propositions[0] if self.propositions else None

    def as_dict(self) -> dict:
        return {
            "fenetre": self.fenetre.as_dict(),
            "propositions": [p.as_dict() for p in self.propositions],
            "ecartees": self.ecartes,
            "ex_aequo_de_tete": self.ex_aequo,
            "regle_de_classement": (
                "maximin lexicographique sur les quatre intervalles avant/après "
                "les nouvelles gardes, trimestres adjacents inclus ; tirage "
                "auditable uniquement en cas d'égalité parfaite"
            ),
        }


def rechercher(
    session: Session,
    assignment: Assignment,
    demandeur: ProfessionalProfile,
    verrou_propre: str | None = None,
) -> ResultatRecherche:
    """Cherche, dans le trimestre, tous les échanges réellement praticables.

    Aucune écriture. Chaque candidat écarté l'est avec un motif explicite.

    ``verrou_propre`` désigne le verrou déjà posé sur la garde cédée par
    l'opération appelante : sans lui, une recherche lancée après la prise de
    verrou s'écarterait elle-même en croyant que la garde participe à une autre
    opération.
    """
    if assignment.profile_id != demandeur.id:
        raise SwapSearchError(
            "Seul le titulaire d'une garde peut chercher un échange pour elle."
        )
    version = session.get(ScheduleVersion, assignment.schedule_version_id)
    if version is None or version.state is not ScheduleState.PUBLIE:
        raise SwapSearchError("La garde n'appartient pas à un planning publié.")
    occurrence_cedee = assignment.post.occurrence
    if occurrence_cedee.start_at <= Clock.now():
        raise SwapSearchError("La garde n'est plus future.")

    quarter = session.get(Quarter, occurrence_cedee.quarter_id)
    quarter_ids = trimestres_adjacents(session, quarter)
    fenetre = fenetre_pour(occurrence_cedee.start_at)

    # Toutes les gardes publiées du **même trimestre**, hors celle du demandeur.
    autres = list(
        session.execute(
            select(Assignment)
            .join(CoveragePost, Assignment.post_id == CoveragePost.id)
            .join(GardeOccurrence, CoveragePost.occurrence_id == GardeOccurrence.id)
            .join(ScheduleVersion, Assignment.schedule_version_id == ScheduleVersion.id)
            .where(
                ScheduleVersion.state == ScheduleState.PUBLIE,
                GardeOccurrence.quarter_id == quarter.id,
                Assignment.id != assignment.id,
                Assignment.profile_id != demandeur.id,
                GardeOccurrence.start_at > Clock.now(),
            )
            .order_by(Assignment.id)
        ).scalars()
    )

    agenda_demandeur = agenda_de(session, demandeur.id, quarter_ids)
    creneau_cede = _creneau_de(assignment)

    candidats: list[CandidatEchange] = []
    index: dict[str, tuple[Assignment, ProfessionalProfile]] = {}
    ecartes: list[dict] = []

    for autre in autres:
        partenaire = session.get(ProfessionalProfile, autre.profile_id)
        motif = _motif_d_ecart(
            session, assignment, autre, demandeur, partenaire, occurrence_cedee,
            verrou_propre=verrou_propre,
        )
        if motif is not None:
            ecartes.append(
                {
                    "garde": autre.id,
                    "partenaire": partenaire.code,
                    "date": autre.post.occurrence.local_date.isoformat(),
                    "motif": motif,
                }
            )
            continue

        identifiant = f"{assignment.id}<->{autre.id}"
        candidats.append(
            CandidatEchange(
                identifiant=identifiant,
                agenda_demandeur=agenda_demandeur,
                agenda_partenaire=agenda_de(session, partenaire.id, quarter_ids),
                garde_demandeur=creneau_cede,
                garde_partenaire=_creneau_de(autre),
            )
        )
        index[identifiant] = (autre, partenaire)

    ordonnes = classer(candidats)
    tetes = {c.identifiant for c in ex_aequo_de_tete(candidats)}

    propositions = []
    for candidat in ordonnes:
        autre, partenaire = index[candidat.identifiant]
        propositions.append(
            PropositionEchange(
                assignment_cede_id=assignment.id,
                assignment_repris_id=autre.id,
                partenaire_code=partenaire.code,
                partenaire_profile_id=partenaire.id,
                date_cedee=occurrence_cedee.local_date.isoformat(),
                date_reprise=autre.post.occurrence.local_date.isoformat(),
                intervalles=candidat.as_dict(),
                cle_maximin=list(candidat.cle_maximin),
            )
        )

    # Les candidats non réalisables sont signalés comme écartés, pas oubliés.
    for candidat in candidats:
        if candidat.realisable:
            continue
        autre, partenaire = index[candidat.identifiant]
        cote = candidat.cote_en_chevauchement
        qui = {
            "demandeur": demandeur.code,
            "partenaire": partenaire.code,
            "les deux": f"{demandeur.code} et {partenaire.code}",
        }.get(cote or "", "l'une des deux personnes")
        ecartes.append(
            {
                "garde": autre.id,
                "partenaire": partenaire.code,
                "date": autre.post.occurrence.local_date.isoformat(),
                "motif": f"Chevauchement créé chez {qui}.",
            }
        )

    return ResultatRecherche(
        fenetre=fenetre,
        propositions=propositions,
        ecartes=ecartes,
        ex_aequo=sorted(tetes),
    )


def _motif_d_ecart(
    session: Session,
    cedee: Assignment,
    reprise: Assignment,
    demandeur: ProfessionalProfile,
    partenaire: ProfessionalProfile,
    occurrence_cedee: GardeOccurrence,
    verrou_propre: str | None = None,
) -> str | None:
    """Motif d'exclusion d'un candidat, ou ``None`` si l'échange est praticable."""
    # (a) Même nature, contrôlée par le service d'échange existant.
    equivalent, differences, _ = check_equivalence(cedee, reprise)
    if not equivalent:
        return "Gardes de nature différente : " + " ; ".join(differences)

    # (b) Le partenaire doit être explicitement VERT sur la date cédée.
    couleur = engine_bridge.current_color(
        session, partenaire.id, occurrence_cedee.id, cedee.post.line
    )
    if couleur is not Color.VERT:
        libelle = couleur.value if couleur is not None else "non renseignée"
        return (
            f"Le partenaire n'est pas explicitement vert sur la date cédée "
            f"(couleur : {libelle})."
        )

    # (c) Éligibilité croisée, vérifiée **séparément** dans les deux sens.
    refus_partenaire = engine_bridge.check_assignment(
        session, cedee.post, partenaire, ignore_assignment_ids={cedee.id, reprise.id}
    )
    if refus_partenaire is not None:
        return (
            f"{partenaire.code} ne peut pas prendre la garde cédée — "
            f"{refus_partenaire.label} : {refus_partenaire.detail}"
        )
    refus_demandeur = engine_bridge.check_assignment(
        session, reprise.post, demandeur, ignore_assignment_ids={cedee.id, reprise.id}
    )
    if refus_demandeur is not None:
        return (
            f"{demandeur.code} ne peut pas prendre la garde proposée — "
            f"{refus_demandeur.label} : {refus_demandeur.detail}"
        )

    # (d) Aucune des deux gardes ne doit déjà participer à une autre opération.
    #     Le verrou posé par l'opération appelante elle-même ne compte pas.
    for assignment, etiquette in ((cedee, "cédée"), (reprise, "proposée")):
        occupation = assignment.busy_operation
        if occupation is None:
            continue
        if assignment.id == cedee.id and occupation == verrou_propre:
            continue
        return (
            f"La garde {etiquette} participe déjà à une autre opération "
            f"({occupation})."
        )
    return None


# --------------------------------------------------------------------------- #
# Départage auditable des ex æquo parfaits
# --------------------------------------------------------------------------- #


def departager(
    session: Session,
    resultat: ResultatRecherche,
    demandeur: ProfessionalProfile,
) -> tuple[PropositionEchange | None, dict | None]:
    """Retient une proposition. Tirage **uniquement** en cas d'égalité parfaite.

    La preuve suit le même schéma que le tirage des reprises : engagement sur
    l'empreinte de la graine, puis révélation.
    """
    if not resultat.propositions:
        return None, None
    if len(resultat.ex_aequo) <= 1:
        return resultat.meilleure, None

    identifiants = sorted(resultat.ex_aequo)
    empreinte = hashlib.sha256(",".join(identifiants).encode()).hexdigest()
    graine = secrets.token_hex(32)
    engagement = hashlib.sha256(graine.encode()).hexdigest()
    digest = hmac.new(graine.encode(), empreinte.encode(), hashlib.sha256).hexdigest()
    index = int(digest[:16], 16) % len(identifiants)
    retenu = identifiants[index]

    proposition = next(
        p
        for p in resultat.propositions
        if f"{p.assignment_cede_id}<->{p.assignment_repris_id}" == retenu
    )
    preuve = {
        "motif": "égalité parfaite sur les quatre intervalles",
        "candidats_ex_aequo": identifiants,
        "empreinte_candidats": empreinte,
        "engagement_graine": engagement,
        "graine_revelee": graine,
        "hmac": digest,
        "index": index,
        "retenu": retenu,
        "algorithme": ALGORITHME_TIRAGE,
        "verification": (
            "sha256(graine_revelee) doit égaler l'engagement ; "
            "index = int(HMAC-SHA256(graine, empreinte_candidats)[0:16],16) mod n"
        ),
    }
    audit_service.record(
        session,
        "ECHANGE_TIRAGE_EX_AEQUO",
        "assignment",
        resultat.propositions[0].assignment_cede_id,
        preuve,
        actor=demandeur.user if demandeur else None,
    )
    return proposition, preuve


def resume_json(resultat: ResultatRecherche) -> str:
    return json.dumps(resultat.as_dict(), ensure_ascii=False)


# --------------------------------------------------------------------------- #
# Enchaînement complet : recherche puis proposition
# --------------------------------------------------------------------------- #


def proposer_le_meilleur(
    session: Session,
    assignment: Assignment,
    demandeur: ProfessionalProfile,
    commentaire: str | None = None,
):
    """Cherche, retient la meilleure solution et ouvre la proposition d'échange.

    C'est le parcours nominal : **aucun responsable n'intervient**. La recherche
    ne décide pas seule pour autant, puisque la proposition n'est officialisée
    qu'après les **deux** accords et une revalidation atomique.

    Retourne ``(proposition_metier, resultat_de_recherche, preuve_de_tirage)``.
    ``preuve_de_tirage`` n'est renseignée qu'en cas d'égalité parfaite.

    ``commentaire`` est **facultatif** : personne n'a à exposer de motif. Il est
    tronqué et rien n'y est interprété.
    """
    from . import swap_service

    resultat = rechercher(session, assignment, demandeur)
    retenue, preuve = departager(session, resultat, demandeur)
    if retenue is None:
        audit_service.record(
            session,
            "ECHANGE_SANS_SOLUTION",
            "assignment",
            assignment.id,
            {
                "candidats_examines": len(resultat.ecartes),
                "motifs": [e["motif"] for e in resultat.ecartes[:10]],
                "fenetre": resultat.fenetre.as_dict(),
            },
            actor=demandeur.user if demandeur else None,
        )
        return None, resultat, None

    partenaire_assignment = session.get(Assignment, retenue.assignment_repris_id)
    proposition = swap_service.propose_swap(
        session, assignment, partenaire_assignment, demandeur
    )
    audit_service.record(
        session,
        "ECHANGE_RECHERCHE",
        "swap_proposal",
        proposition.id,
        {
            "garde_cedee": assignment.id,
            "garde_retenue": partenaire_assignment.id,
            "classement": [p.as_dict() for p in resultat.propositions[:5]],
            "ex_aequo": resultat.ex_aequo,
            "tirage": preuve,
            "fenetre": resultat.fenetre.as_dict(),
            "commentaire": (commentaire or "").strip()[:300] or None,
        },
        actor=demandeur.user if demandeur else None,
    )
    return proposition, resultat, preuve
