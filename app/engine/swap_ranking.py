"""Classement des échanges valides par **maximin d'espacement**.

Arbitrage du client du 04/09/2026 (lot 3, point 6) :

    Classer les échanges valides en simulant les deux plannings et en calculant
    les quatre intervalles avant/après les nouvelles gardes. Maximiser d'abord le
    plus petit intervalle, puis le deuxième plus petit, etc. Le calcul consulte
    également les gardes des trimestres adjacents. Tirage auditable uniquement en
    cas d'égalité parfaite.

Conception :

* un échange déplace **deux** gardes. On simule donc les deux plannings, celui
  du demandeur et celui du partenaire, chacun privé de la garde qu'il cède et
  augmenté de celle qu'il reprend ;
* pour chaque nouvelle garde, on mesure l'intervalle **avant** et **après**, soit
  quatre mesures ; l'absence de voisin n'est pas une contrainte et vaut donc
  l'infini ;
* le classement est un maximin lexicographique sur ces quatre valeurs triées :
  on préfère l'échange dont le plus petit intervalle est le plus grand, puis on
  départage sur le deuxième plus petit, et ainsi de suite ;
* deux échanges parfaitement à égalité sur les quatre valeurs sont départagés par
  un tirage auditable, jamais par l'ordre d'apparition.

Module **pur** : aucune dépendance à la base ni à HTTP.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime

#: Valeur retenue lorsqu'il n'existe aucun voisin : l'absence de contrainte ne
#: doit jamais pénaliser une solution.
AUCUNE_CONTRAINTE = math.inf

#: Sentinelle de **chevauchement**. Distincte d'un espacement nul : deux gardes
#: contiguës (fin de l'une = début de l'autre) ont un espacement de 0 h et sont
#: parfaitement licites, c'est le cas d'un week-end complet. Un chevauchement,
#: lui, rend l'échange impossible.
CHEVAUCHEMENT = -1.0


@dataclass(frozen=True)
class Creneau:
    """Intervalle occupé, indépendant de tout modèle de persistance."""

    start_at: datetime
    end_at: datetime
    reference: str = ""

    def chevauche(self, autre: "Creneau") -> bool:
        return self.start_at < autre.end_at and autre.start_at < self.end_at


def _heures(delta) -> float:
    return delta.total_seconds() / 3600.0


def intervalles_autour(
    agenda: list[Creneau], nouvelle: Creneau
) -> tuple[float, float]:
    """Intervalles, en heures, avant et après ``nouvelle`` dans cet agenda.

    ``agenda`` ne doit pas contenir la garde cédée : c'est un agenda **simulé**.
    Un chevauchement retourne la sentinelle ``CHEVAUCHEMENT`` des deux côtés, ce
    qui exclut l'échange. Une simple contiguïté retourne 0.0, ce qui est licite
    et correspond au week-end complet.
    """
    avant = AUCUNE_CONTRAINTE
    apres = AUCUNE_CONTRAINTE
    for creneau in agenda:
        if creneau.chevauche(nouvelle):
            return CHEVAUCHEMENT, CHEVAUCHEMENT
        if creneau.end_at <= nouvelle.start_at:
            avant = min(avant, _heures(nouvelle.start_at - creneau.end_at))
        elif creneau.start_at >= nouvelle.end_at:
            apres = min(apres, _heures(creneau.start_at - nouvelle.end_at))
    return avant, apres


def agenda_simule(
    agenda: list[Creneau], cedee: Creneau, reprise: Creneau
) -> list[Creneau]:
    """Agenda de la personne après l'échange : sans la cédée, avec la reprise."""
    reste = [
        c
        for c in agenda
        if not (c.start_at == cedee.start_at and c.end_at == cedee.end_at)
    ]
    return sorted(reste + [reprise], key=lambda c: c.start_at)


@dataclass
class CandidatEchange:
    """Un échange possible, avec ses quatre mesures d'espacement."""

    identifiant: str
    #: Agendas **avant** échange, trimestres adjacents inclus.
    agenda_demandeur: list[Creneau]
    agenda_partenaire: list[Creneau]
    garde_demandeur: Creneau
    garde_partenaire: Creneau
    intervalles: tuple[float, float, float, float] = field(init=False)

    def __post_init__(self) -> None:
        # Le demandeur cède sa garde et reprend celle du partenaire.
        simule_d = agenda_simule(
            self.agenda_demandeur, self.garde_demandeur, self.garde_partenaire
        )
        avant_d, apres_d = intervalles_autour(
            [c for c in simule_d if c is not self.garde_partenaire],
            self.garde_partenaire,
        )
        # Le partenaire cède la sienne et reprend celle du demandeur.
        simule_p = agenda_simule(
            self.agenda_partenaire, self.garde_partenaire, self.garde_demandeur
        )
        avant_p, apres_p = intervalles_autour(
            [c for c in simule_p if c is not self.garde_demandeur],
            self.garde_demandeur,
        )
        self.intervalles = (avant_d, apres_d, avant_p, apres_p)

    @property
    def cle_maximin(self) -> tuple[float, ...]:
        """Clé de tri : les quatre intervalles **triés croissant**.

        Comparée lexicographiquement, elle réalise exactement le maximin
        demandé : maximiser le plus petit, puis le deuxième plus petit, etc.
        """
        return tuple(sorted(self.intervalles))

    @property
    def realisable(self) -> bool:
        """Faux **seulement** en cas de chevauchement réel.

        Un espacement nul entre deux gardes contiguës reste licite.
        """
        return CHEVAUCHEMENT not in self.intervalles

    @property
    def cote_en_chevauchement(self) -> str | None:
        """Qui subit le chevauchement, pour un motif d'écart nominatif."""
        avant_d, apres_d, avant_p, apres_p = self.intervalles
        demandeur = avant_d == CHEVAUCHEMENT
        partenaire = avant_p == CHEVAUCHEMENT
        if demandeur and partenaire:
            return "les deux"
        if demandeur:
            return "demandeur"
        if partenaire:
            return "partenaire"
        return None

    def as_dict(self) -> dict:
        def lisible(valeur: float) -> float | str | None:
            if valeur == AUCUNE_CONTRAINTE:
                return None
            if valeur == CHEVAUCHEMENT:
                return "chevauchement"
            return round(valeur, 3)

        avant_d, apres_d, avant_p, apres_p = self.intervalles
        return {
            "identifiant": self.identifiant,
            "intervalle_demandeur_avant_h": lisible(avant_d),
            "intervalle_demandeur_apres_h": lisible(apres_d),
            "intervalle_partenaire_avant_h": lisible(avant_p),
            "intervalle_partenaire_apres_h": lisible(apres_p),
            "cle_maximin": [lisible(v) for v in self.cle_maximin],
            "realisable": self.realisable,
        }


def classer(candidats: list[CandidatEchange]) -> list[CandidatEchange]:
    """Trie du meilleur au moins bon selon le maximin lexicographique.

    Le tri est **stable et déterministe** : à clé identique, l'ordre suit
    l'identifiant, ce qui garantit la reproductibilité. Le départage réel entre
    ex æquo parfaits est fait par tirage auditable, en dehors de cette fonction.
    """
    realisables = [c for c in candidats if c.realisable]
    return sorted(
        realisables,
        key=lambda c: (
            tuple(-v if v != AUCUNE_CONTRAINTE else -1e18 for v in c.cle_maximin),
            c.identifiant,
        ),
    )


def ex_aequo_de_tete(candidats: list[CandidatEchange]) -> list[CandidatEchange]:
    """Candidats **parfaitement** à égalité en tête du classement.

    Un tirage n'a lieu que si cette liste compte plus d'un élément.
    """
    ordonnes = classer(candidats)
    if not ordonnes:
        return []
    meilleure = ordonnes[0].cle_maximin
    return [c for c in ordonnes if c.cle_maximin == meilleure]
