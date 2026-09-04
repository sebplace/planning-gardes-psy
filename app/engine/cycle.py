"""Cycle de quota canonique et périodes de comptage.

Arbitrage du client du 04/09/2026 (lot 3, point 10) :

    Le quota canonique suit un cycle allant du **premier lundi d'octobre inclus**
    au **premier lundi d'octobre suivant exclu**. Pour 2026-2027 : dates de
    service du 05/10/2026 au 03/10/2027 inclus ; le 04/10/2027 ouvre le cycle
    suivant. Une garde commencée le dimanche et finissant le lundi matin
    appartient au cycle du dimanche.

Deux conséquences de conception :

* le rattachement se fait sur la **date de service** (le jour où la garde
  commence), jamais sur la date de fin. C'est ce qui règle le cas de la garde du
  dimanche soir qui déborde sur le lundi matin ;
* un cycle est à cheval sur deux années civiles. Les trimestres restent les
  unités de campagne et de publication, mais les compteurs de référence sont
  annuels et suivent ce cycle.

Ce module est **pur** : aucune dépendance à la base ni à HTTP.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

MOIS_DE_BASCULE = 10  # octobre
LUNDI = 0


def premier_lundi_d_octobre(annee: int) -> date:
    """Premier lundi d'octobre de l'année civile donnée."""
    jour = date(annee, MOIS_DE_BASCULE, 1)
    decalage = (LUNDI - jour.weekday()) % 7
    return jour + timedelta(days=decalage)


@dataclass(frozen=True)
class CycleQuota:
    """Période de comptage canonique, bornes de **dates de service**.

    ``debut`` est inclus, ``fin_exclue`` est exclue. ``fin`` est le dernier jour
    de service appartenant au cycle, fourni pour la lisibilité des écrans.
    """

    debut: date
    fin_exclue: date

    @property
    def fin(self) -> date:
        return self.fin_exclue - timedelta(days=1)

    @property
    def label(self) -> str:
        return f"{self.debut.year}-{self.fin.year}"

    @property
    def jours(self) -> int:
        return (self.fin_exclue - self.debut).days

    @property
    def semaines(self) -> float:
        return self.jours / 7.0

    def contient(self, date_de_service: date) -> bool:
        """Vrai si cette **date de service** appartient au cycle."""
        return self.debut <= date_de_service < self.fin_exclue

    def as_dict(self) -> dict:
        return {
            "libelle": self.label,
            "debut_inclus": self.debut.isoformat(),
            "fin_incluse": self.fin.isoformat(),
            "fin_exclue": self.fin_exclue.isoformat(),
            "jours": self.jours,
            "semaines": round(self.semaines, 3),
        }


def cycle_commencant_en(annee: int) -> CycleQuota:
    """Cycle ouvert par le premier lundi d'octobre de ``annee``."""
    return CycleQuota(
        debut=premier_lundi_d_octobre(annee),
        fin_exclue=premier_lundi_d_octobre(annee + 1),
    )


def cycle_pour(date_de_service: date) -> CycleQuota:
    """Cycle auquel appartient une **date de service**.

    Le rattachement ne regarde jamais la date de fin de la garde : une garde
    commencée le dimanche 03/10/2027 appartient au cycle 2026-2027, même si elle
    se termine le lundi 04/10/2027 qui ouvre le cycle suivant.
    """
    candidat = cycle_commencant_en(date_de_service.year)
    if candidat.contient(date_de_service):
        return candidat
    return cycle_commencant_en(date_de_service.year - 1)


def cycle_courant(aujourd_hui: date) -> CycleQuota:
    return cycle_pour(aujourd_hui)


def cycles_couvrant(debut: date, fin: date) -> list[CycleQuota]:
    """Tous les cycles touchés par un intervalle de dates de service, bornes incluses."""
    out: list[CycleQuota] = []
    courant = cycle_pour(debut)
    while courant.debut <= fin:
        out.append(courant)
        courant = cycle_commencant_en(courant.debut.year + 1)
    return out
