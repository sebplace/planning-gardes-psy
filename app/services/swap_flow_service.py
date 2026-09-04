"""Parcours nominal d'échange : de « je cède ma garde » à l'officialisation.

Lot B du contre-audit du 04/09/2026. Le moteur de recherche existait mais
n'était appelé par aucune page ni aucune route ; l'interface demandait encore à
la personne de désigner elle-même la « garde souhaitée », c'est-à-dire
d'enregistrer un accord déjà trouvé hors application. Ce module raccorde le
parcours réel :

1. depuis **sa propre** garde future, la personne clique sur « Échange » sans
   choisir ni collègue ni garde de contrepartie ;
2. l'application cherche, dans le même trimestre, les collègues explicitement
   verts à la date cédée détenant une garde réellement reprenable par le
   demandeur (même nature, éligibilité croisée vérifiée séparément) ;
3. tous les partenaires éligibles sont sollicités **simultanément**, sans
   avantage à la rapidité et sans divulgation de motif ;
4. à la clôture, seules les réponses positives sont retenues, classées par
   maximin sur les quatre intervalles avant/après, trimestres adjacents inclus ;
   un tirage auditable ne départage qu'en cas d'**égalité parfaite** ;
5. le consentement des deux parties est explicite — le demandeur en ouvrant, le
   partenaire en répondant favorablement — puis les deux affectations sont
   revalidées **atomiquement** et l'échange est officialisé exactement une fois ;
6. refus, retrait, expiration, annulation, conflit concurrent et absence de
   solution sont des états modélisés, pas des cas implicites.

Aucun responsable n'intervient dans un parcours conforme.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import secrets

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from ..engine.swap_ranking import CandidatEchange, classer, ex_aequo_de_tete
from ..models import (
    Assignment,
    ProfessionalProfile,
    ScheduleState,
    ScheduleVersion,
    SwapCandidate,
    SwapCandidateState,
    SwapSearch,
    SwapSearchState,
    SwapState,
    User,
)
from . import audit_service, notification_service, swap_search_service, swap_service
from .clock import Clock, format_date_fr, format_local

#: Verrou métier posé sur la garde cédée pendant la collecte.
VERROU = "ECHANGE"

ALGORITHME_TIRAGE = swap_search_service.ALGORITHME_TIRAGE


class SwapFlowError(Exception):
    pass


class SwapFlowPermissionError(SwapFlowError):
    """Refus de droit, distinct d'un refus métier."""


# --------------------------------------------------------------------------- #
# Utilitaires
# --------------------------------------------------------------------------- #


def _guard(session: Session, model, pk: int, field: str, expected, new) -> bool:
    """Transition d'état gardée côté serveur : une seule opération l'emporte."""
    result = session.execute(
        update(model)
        .where(model.id == pk, getattr(model, field) == expected)
        .values(**{field: new})
    )
    if result.rowcount == 1:
        obj = session.get(model, pk)
        if obj is not None:
            session.expire(obj)
        return True
    return False


def _contexte(session: Session, search: SwapSearch, candidate: SwapCandidate | None = None) -> dict:
    cedee = search.assignment.post.occurrence
    contexte = {
        "date_cedee": format_date_fr(cedee.local_date),
        "type_label": cedee.garde_type.label,
        "line": search.assignment.post.line.value,
        "closes_at": format_local(search.closes_at) if search.closes_at else "—",
        "palier": search.window_label or "—",
    }
    if candidate is not None:
        reprise = candidate.assignment.post.occurrence
        contexte["date_reprise"] = format_date_fr(reprise.local_date)
    else:
        contexte["date_reprise"] = "—"
    return contexte


def _liberer(session: Session, assignment_ids: list[int]) -> None:
    session.execute(
        update(Assignment)
        .where(Assignment.id.in_(assignment_ids), Assignment.busy_operation == VERROU)
        .values(busy_operation=None)
    )


# --------------------------------------------------------------------------- #
# Ouverture
# --------------------------------------------------------------------------- #


def ouvrir(
    session: Session,
    assignment: Assignment,
    demandeur: ProfessionalProfile,
    commentaire: str | None = None,
) -> SwapSearch:
    """Ouvre une recherche d'échange à partir de la seule garde à céder.

    Aucun motif n'est exigé : toute personne titulaire d'une garde future peut
    demander un échange. Le commentaire éventuel reste facultatif, tronqué, et
    n'est jamais diffusé aux partenaires sollicités.
    """
    if assignment is None:
        raise SwapFlowError("Garde inconnue.")
    if assignment.profile_id != demandeur.id:
        raise SwapFlowError("Seul le titulaire d'une garde peut demander un échange.")
    version = session.get(ScheduleVersion, assignment.schedule_version_id)
    if version is None or version.state is not ScheduleState.PUBLIE:
        raise SwapFlowError("Seule une garde d'un planning publié peut être échangée.")
    if assignment.post.occurrence.start_at <= Clock.now():
        raise SwapFlowError("La garde a déjà commencé.")

    verrou = session.execute(
        update(Assignment)
        .where(Assignment.id == assignment.id, Assignment.busy_operation.is_(None))
        .values(busy_operation=VERROU)
    )
    if verrou.rowcount != 1:
        raise SwapFlowError(
            "Cette garde participe déjà à une autre opération (reprise ou échange en cours)."
        )
    session.expire(assignment)

    resultat = swap_search_service.rechercher(
        session, assignment, demandeur, verrou_propre=VERROU
    )
    search = SwapSearch(
        assignment_id=assignment.id,
        requester_profile_id=demandeur.id,
        comment=(commentaire or "").strip()[:300] or None,
        state=SwapSearchState.BROUILLON,
        opens_at=resultat.fenetre.ouvre_a,
        closes_at=resultat.fenetre.ferme_a,
        window_label=resultat.fenetre.libelle,
        urgent=resultat.fenetre.urgent,
        ranking_json=swap_search_service.resume_json(resultat),
    )
    session.add(search)
    session.flush()

    if not resultat.propositions:
        _sans_solution(
            session,
            search,
            "Aucun échange praticable n'a été trouvé dans le trimestre : "
            "aucun collègue explicitement vert à cette date ne détient une garde "
            "de même nature que vous puissiez reprendre.",
        )
        return search

    for proposition in resultat.propositions:
        session.add(
            SwapCandidate(
                search_id=search.id,
                profile_id=proposition.partenaire_profile_id,
                assignment_id=proposition.assignment_repris_id,
                state=SwapCandidateState.SOLLICITE,
                ranking_key_json=json.dumps(proposition.cle_maximin, default=str),
            )
        )
    session.flush()
    search.solicited_count = len(resultat.propositions)
    search.state = SwapSearchState.COLLECTE
    session.flush()

    for candidate in search.candidates:
        notification_service.enqueue(
            session,
            "ECHANGE_SOLLICITATION",
            f"echange:{search.id}:sollicitation:{candidate.id}",
            candidate.profile,
            _contexte(session, search, candidate),
            anonymised=True,
        )
    audit_service.record(
        session,
        "ECHANGE_RECHERCHE_OUVERTE",
        "swap_search",
        search.id,
        {
            "garde_cedee": assignment.id,
            "sollicites": search.solicited_count,
            "fenetre": resultat.fenetre.as_dict(),
            "ecartes": resultat.ecartes[:10],
            "note": (
                "sollicitation simultanée ; ni le nom du demandeur ni son motif "
                "ne sont diffusés ; la rapidité de réponse ne procure aucun avantage"
            ),
        },
        actor=demandeur.user,
    )
    return search


# --------------------------------------------------------------------------- #
# Réponses
# --------------------------------------------------------------------------- #


def _assert_collecte_ouverte(session: Session, search: SwapSearch, action: str) -> None:
    """Refuse et **trace** toute réponse postérieure au gel ou à l'échéance."""
    session.refresh(search)
    motif = None
    if search.state is not SwapSearchState.COLLECTE:
        motif = f"collecte close (état {search.state.value})"
    elif search.closes_at is not None and Clock.now() > search.closes_at:
        motif = "fenêtre de réponse expirée"
    if motif is None:
        return
    audit_service.record(
        session,
        "ECHANGE_REPONSE_TARDIVE_REFUSEE",
        "swap_search",
        search.id,
        {"action": action, "motif": motif},
        actor_label="SYSTEME",
    )
    raise SwapFlowError(
        f"Réponse refusée : {motif}. La liste des accords est figée."
    )


def repondre(
    session: Session,
    search: SwapSearch,
    profile: ProfessionalProfile,
    favorable: bool,
    assignment_id: int | None = None,
) -> list[SwapCandidate]:
    """Accord ou refus explicite d'un partenaire sollicité.

    ``assignment_id`` cible une permutation précise lorsque la personne détient
    plusieurs gardes reprenables ; sans lui, la réponse vaut pour toutes ses
    propositions dans cette recherche.
    """
    _assert_collecte_ouverte(session, search, "accord" if favorable else "refus")
    concernes = [
        c
        for c in search.candidates
        if c.profile_id == profile.id
        and (assignment_id is None or c.assignment_id == assignment_id)
    ]
    if not concernes:
        raise SwapFlowError("Vous n'avez pas été sollicité·e pour cet échange.")

    for candidate in concernes:
        if candidate.state in (SwapCandidateState.RETIRE, SwapCandidateState.REFUS):
            if favorable:
                raise SwapFlowError(
                    "Vous avez déjà refusé ou retiré cette proposition : elle ne "
                    "peut pas être redéposée sur cette collecte."
                )
            continue
        if favorable:
            candidate.state = SwapCandidateState.FAVORABLE
            candidate.exclusion_reason = None
        else:
            # Un refus postérieur à un accord est un **retrait** : il doit rendre
            # la proposition définitivement non retenable, pas seulement muette.
            candidate.state = (
                SwapCandidateState.RETIRE
                if candidate.state is SwapCandidateState.FAVORABLE
                else SwapCandidateState.REFUS
            )
            candidate.exclusion_reason = (
                "Refus ou retrait explicite : proposition non retenable."
            )
        candidate.responded_at = Clock.now()
    session.flush()
    audit_service.record(
        session,
        "ECHANGE_ACCORD" if favorable else "ECHANGE_REFUS_PARTENAIRE",
        "swap_search",
        search.id,
        {
            "partenaire": profile.code,
            "propositions": [c.assignment_id for c in concernes],
            "note": "l'ordre et la vitesse de réponse n'influencent pas le classement",
        },
        actor=profile.user,
    )
    return concernes


def toutes_les_reponses_recues(search: SwapSearch) -> bool:
    return all(
        c.state is not SwapCandidateState.SOLLICITE for c in search.candidates
    )


# --------------------------------------------------------------------------- #
# Clôture : gel, revalidation, classement, tirage éventuel, officialisation
# --------------------------------------------------------------------------- #


def _sans_solution(session: Session, search: SwapSearch, motif: str) -> SwapSearch:
    search.state = SwapSearchState.SANS_SOLUTION
    search.outcome_reason = motif[:2000]
    search.closed_at = Clock.now()
    _liberer(session, [search.assignment_id])
    session.flush()
    notification_service.enqueue(
        session,
        "ECHANGE_SANS_SOLUTION",
        f"echange:{search.id}:sans_solution",
        search.requester,
        _contexte(session, search) | {"reason": motif},
    )
    audit_service.record(
        session,
        "ECHANGE_SANS_SOLUTION",
        "swap_search",
        search.id,
        {
            "motif": motif,
            "note": (
                "titulaire inchangé ; aucune surcharge imposée ; la garde reste "
                "à la charge de la personne initialement affectée"
            ),
        },
        actor_label="SYSTEME",
    )
    return search


def _empreinte(valeurs: list[int]) -> str:
    return hashlib.sha256(
        ",".join(str(v) for v in sorted(valeurs)).encode()
    ).hexdigest()


def cloturer(session: Session, search: SwapSearch) -> SwapSearch:
    """Gel, revalidation, classement, tirage éventuel puis officialisation.

    Une seule clôture officielle est possible : la transition est gardée côté
    serveur, donc deux exécutions concurrentes ne peuvent pas officialiser deux
    fois.
    """
    if not _guard(
        session,
        SwapSearch,
        search.id,
        "state",
        SwapSearchState.COLLECTE,
        SwapSearchState.LISTE_FIGEE,
    ):
        raise SwapFlowError(
            "La collecte a déjà été close par une autre opération."
        )
    session.refresh(search)

    # 1. Gel de la liste des accords reçus, **avant** tout calcul de classement.
    figes = [
        c for c in search.candidates if c.state is SwapCandidateState.FAVORABLE
    ]
    liste_hash = _empreinte([c.id for c in figes])
    graine = secrets.token_hex(32)
    engagement = hashlib.sha256(graine.encode()).hexdigest()
    search.frozen_at = Clock.now()
    search.list_hash = liste_hash
    search.seed_commitment = engagement
    session.flush()
    audit_service.record(
        session,
        "ECHANGE_LISTE_FIGEE",
        "swap_search",
        search.id,
        {
            "accords_figes": [c.id for c in figes],
            "empreinte_liste": liste_hash,
            "engagement_graine": engagement,
            "note": (
                "l'engagement sur la graine est enregistré avant le classement ; "
                "seule son empreinte est publiée à ce stade"
            ),
        },
        actor_label="SYSTEME",
    )

    if not figes:
        return _sans_solution(
            session,
            search,
            "Aucun accord favorable valide à la clôture : le titulaire publié "
            "reste responsable de la garde.",
        )

    # 2. Revalidation de chaque accord figé, dans les deux sens.
    cedee = search.assignment
    demandeur = search.requester
    occurrence_cedee = cedee.post.occurrence
    valides: list[SwapCandidate] = []
    exclusions: list[dict] = []
    for candidate in figes:
        partenaire = candidate.profile
        reprise = candidate.assignment
        motif = swap_search_service._motif_d_ecart(
            session, cedee, reprise, demandeur, partenaire, occurrence_cedee,
            verrou_propre=VERROU,
        )
        if motif is None:
            candidate.state = SwapCandidateState.FAVORABLE
            valides.append(candidate)
        else:
            candidate.state = SwapCandidateState.EXCLU
            candidate.exclusion_reason = motif[:300]
            exclusions.append(
                {
                    "candidat": candidate.id,
                    "partenaire": partenaire.code,
                    "motif": motif,
                }
            )
    session.flush()

    if not valides:
        return _sans_solution(
            session,
            search,
            "Tous les accords ont été écartés à la revalidation : "
            + " ; ".join(e["motif"] for e in exclusions[:5]),
        )

    # 3. Classement par maximin, trimestres adjacents inclus.
    quarter_ids = swap_search_service.trimestres_adjacents(
        session, _quarter_de(session, occurrence_cedee)
    )
    agenda_demandeur = swap_search_service.agenda_de(
        session, demandeur.id, quarter_ids
    )
    creneau_cede = swap_search_service._creneau_de(cedee)
    candidats: list[CandidatEchange] = []
    index: dict[str, SwapCandidate] = {}
    for candidate in valides:
        identifiant = f"{cedee.id}<->{candidate.assignment_id}"
        candidats.append(
            CandidatEchange(
                identifiant=identifiant,
                agenda_demandeur=agenda_demandeur,
                agenda_partenaire=swap_search_service.agenda_de(
                    session, candidate.profile_id, quarter_ids
                ),
                garde_demandeur=creneau_cede,
                garde_partenaire=swap_search_service._creneau_de(candidate.assignment),
            )
        )
        index[identifiant] = candidate

    ordonnes = classer(candidats)
    if not ordonnes:
        for candidate in valides:
            candidate.state = SwapCandidateState.EXCLU
            candidate.exclusion_reason = (
                "Chevauchement créé chez l'une des deux personnes."
            )
        session.flush()
        return _sans_solution(
            session,
            search,
            "Tous les accords créeraient un chevauchement : aucune permutation "
            "praticable.",
        )

    tetes = sorted(c.identifiant for c in ex_aequo_de_tete(candidats))
    classement = [
        {
            "identifiant": c.identifiant,
            "partenaire": index[c.identifiant].profile.code,
            "garde_reprise": index[c.identifiant].assignment_id,
            "cle_maximin": [str(v) for v in c.cle_maximin],
        }
        for c in ordonnes
    ]

    # 4. Tirage auditable **uniquement** en cas d'égalité parfaite.
    preuve = None
    if len(tetes) > 1:
        empreinte = hashlib.sha256(",".join(tetes).encode()).hexdigest()
        digest = hmac.new(
            graine.encode(), empreinte.encode(), hashlib.sha256
        ).hexdigest()
        position = int(digest[:16], 16) % len(tetes)
        retenu_id = tetes[position]
        preuve = {
            "motif": "égalité parfaite sur les quatre intervalles",
            "candidats_ex_aequo": tetes,
            "empreinte_candidats": empreinte,
            "engagement_graine": engagement,
            "graine_revelee": graine,
            "hmac": digest,
            "index": position,
            "retenu": retenu_id,
            "algorithme": ALGORITHME_TIRAGE,
            "verification": (
                "sha256(graine_revelee) doit égaler l'engagement ; "
                "index = int(HMAC-SHA256(graine, empreinte_candidats)[0:16],16) mod n"
            ),
        }
    else:
        retenu_id = ordonnes[0].identifiant

    retenu = index[retenu_id]
    search.ranking_json = json.dumps(
        {
            "classement": classement,
            "ex_aequo_de_tete": tetes,
            "exclusions": exclusions,
            "empreinte_liste": liste_hash,
            "engagement_graine": engagement,
            "graine_revelee": graine,
            "regle": (
                "maximin lexicographique sur les quatre intervalles avant/après "
                "les nouvelles gardes, trimestres adjacents inclus ; tirage "
                "auditable uniquement en cas d'égalité parfaite"
            ),
        },
        ensure_ascii=False,
        default=str,
    )
    search.draw_json = json.dumps(preuve, ensure_ascii=False) if preuve else None
    session.flush()
    audit_service.record(
        session,
        "ECHANGE_CLASSEMENT",
        "swap_search",
        search.id,
        {
            "classement": classement,
            "ex_aequo_de_tete": tetes,
            "tirage": preuve,
            "retenu": retenu_id,
        },
        actor_label="SYSTEME",
    )

    return _officialiser(session, search, retenu, exclusions)


def _quarter_de(session: Session, occurrence):
    from ..models import Quarter

    return session.get(Quarter, occurrence.quarter_id)


def _officialiser(
    session: Session,
    search: SwapSearch,
    retenu: SwapCandidate,
    exclusions: list[dict],
) -> SwapSearch:
    """Consentements réunis, revalidation atomique, officialisation unique."""
    cedee = search.assignment
    partenaire_assignment = retenu.assignment

    # Le verrou de la recherche est relâché juste avant la prise de verrou de la
    # proposition, dans la **même** transaction : aucune fenêtre ouverte.
    _liberer(session, [cedee.id])
    proposition = swap_service.propose_swap(
        session, cedee, partenaire_assignment, search.requester
    )
    if proposition.state is SwapState.REFUSE:
        retenu.state = SwapCandidateState.EXCLU
        retenu.exclusion_reason = (proposition.refusal_reason or "")[:300]
        session.flush()
        return _sans_solution(
            session,
            search,
            "La permutation retenue a été refusée à la revalidation : "
            + (proposition.refusal_reason or "motif non précisé"),
        )

    # Le consentement du partenaire est **déjà** explicite : il a répondu
    # favorablement pendant la collecte. Son accord est donc enregistré ici, ce
    # qui déclenche la revalidation atomique des deux affectations.
    proposition = swap_service.accept_swap(session, proposition, retenu.profile)
    search.retained_proposal_id = proposition.id
    session.flush()

    if proposition.state is not SwapState.OFFICIEL:
        retenu.state = SwapCandidateState.EXCLU
        retenu.exclusion_reason = (proposition.refusal_reason or "")[:300]
        session.flush()
        return _sans_solution(
            session,
            search,
            "La permutation retenue n'a pas passé la revalidation atomique : "
            + (proposition.refusal_reason or "motif non précisé"),
        )

    retenu.state = SwapCandidateState.RETENU
    for candidate in search.candidates:
        if candidate.id != retenu.id and candidate.state is SwapCandidateState.FAVORABLE:
            candidate.state = SwapCandidateState.NON_RETENU
    search.state = SwapSearchState.OFFICIALISEE
    search.closed_at = Clock.now()
    session.flush()

    contexte = _contexte(session, search, retenu)
    for profil in (search.requester, retenu.profile):
        notification_service.enqueue(
            session,
            "ECHANGE_RECHERCHE_OFFICIELLE",
            f"echange:{search.id}:officiel:{profil.id}",
            profil,
            contexte,
        )
    for candidate in search.candidates:
        if candidate.state is SwapCandidateState.NON_RETENU:
            notification_service.enqueue(
                session,
                "ECHANGE_NON_RETENU",
                f"echange:{search.id}:non_retenu:{candidate.id}",
                candidate.profile,
                contexte,
                anonymised=True,
            )
    audit_service.record(
        session,
        "ECHANGE_RECHERCHE_OFFICIALISEE",
        "swap_search",
        search.id,
        {
            "proposition": proposition.id,
            "garde_cedee": cedee.id,
            "garde_reprise": partenaire_assignment.id,
            "partenaire": retenu.profile.code,
            "exclusions": exclusions,
            "note": (
                "consentement explicite des deux parties, revalidation atomique "
                "puis officialisation exactement une fois"
            ),
        },
        actor_label="SYSTEME",
    )
    return search


# --------------------------------------------------------------------------- #
# Orchestration et annulation
# --------------------------------------------------------------------------- #


def avancer(session: Session, search: SwapSearch) -> SwapSearch:
    """Fait progresser la recherche : clôture dès l'échéance ou dès complétude."""
    session.refresh(search)
    if not search.is_open:
        return search
    if search.state is SwapSearchState.COLLECTE:
        echu = search.closes_at is not None and Clock.now() >= search.closes_at
        if not (echu or toutes_les_reponses_recues(search)):
            return search
        return cloturer(session, search)
    return search


def annuler(
    session: Session, search: SwapSearch, actor: User | None = None
) -> bool:
    """Retrait de la demande par son auteur, tant que rien n'est officialisé."""
    if not _guard(
        session,
        SwapSearch,
        search.id,
        "state",
        SwapSearchState.COLLECTE,
        SwapSearchState.ANNULEE,
    ):
        return False
    session.refresh(search)
    search.closed_at = Clock.now()
    search.outcome_reason = "Recherche annulée par son auteur."
    _liberer(session, [search.assignment_id])
    session.flush()
    audit_service.record(
        session, "ECHANGE_RECHERCHE_ANNULEE", "swap_search", search.id, {}, actor=actor
    )
    return True


def expirer(session: Session, search: SwapSearch) -> bool:
    """Expiration sans accord : la garde reste au titulaire publié."""
    if search.state is not SwapSearchState.COLLECTE:
        return False
    if search.closes_at is None or Clock.now() < search.closes_at:
        return False
    cloturer(session, search)
    return True


def recherches_de(session: Session, profile: ProfessionalProfile) -> list[SwapSearch]:
    return list(
        session.execute(
            select(SwapSearch)
            .where(SwapSearch.requester_profile_id == profile.id)
            .order_by(SwapSearch.id.desc())
        ).scalars()
    )


def sollicitations_de(
    session: Session, profile: ProfessionalProfile
) -> list[SwapCandidate]:
    return list(
        session.execute(
            select(SwapCandidate)
            .join(SwapSearch, SwapCandidate.search_id == SwapSearch.id)
            .where(
                SwapCandidate.profile_id == profile.id,
                SwapSearch.state == SwapSearchState.COLLECTE,
            )
            .order_by(SwapCandidate.id)
        ).scalars()
    )
