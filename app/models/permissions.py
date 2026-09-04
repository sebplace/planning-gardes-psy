"""Permissions applicatives distinctes et traçables.

Exigence P1.19. Six permissions ont été demandées par le client, chacune
attribuable séparément, datée et journalisée :

* ``RESP_L1`` : responsable des gardes de première ligne ;
* ``RESP_L2`` : responsable des gardes de deuxième ligne ;
* ``CHEF_SERVICE`` : chef de service ;
* ``GESTION_COMPTES`` : création et désactivation des comptes ;
* ``PUBLICATION`` : validation et publication d'un planning ;
* ``CONSULTATION_AUDIT`` : lecture du journal d'audit.

Elles sont **indépendantes du statut professionnel** : un médecin peut exercer
une fonction administrative sans être administrateur global, et un
administrateur n'est pas nécessairement médecin.

Confirmé par le client le 04/09/2026 : les trois **fonctions** que sont
responsable des gardes 1, responsable des gardes 2 et chef de service ouvrent
l'**accès administratif** nécessaire à leur exercice. Les autres médecins
restent non administrateurs. Les trois fonctions gardent des périmètres
distincts et chaque attribution reste datée et journalisée.
"""

from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import Boolean, Date, DateTime, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base, TimestampMixin

RESP_L1 = "RESP_L1"
RESP_L2 = "RESP_L2"
CHEF_SERVICE = "CHEF_SERVICE"
GESTION_COMPTES = "GESTION_COMPTES"
PUBLICATION = "PUBLICATION"
CONSULTATION_AUDIT = "CONSULTATION_AUDIT"

PERMISSIONS = (
    RESP_L1,
    RESP_L2,
    CHEF_SERVICE,
    GESTION_COMPTES,
    PUBLICATION,
    CONSULTATION_AUDIT,
)

#: Fonctions qui ouvrent l'**accès administratif** à l'application, confirmées par
#: le client le 04/09/2026 : responsable des gardes de première ligne, responsable
#: des gardes de deuxième ligne, chef de service. Les autres médecins restent non
#: administrateurs. Chaque fonction reste attribuée séparément, datée et
#: journalisée : elles sont distinctes et traçables, jamais fusionnées.
ROLES_ADMINISTRATIFS = (RESP_L1, RESP_L2, CHEF_SERVICE)

#: Périmètre de ligne propre à chaque fonction. C'est ce qui distingue
#: concrètement les trois rôles administratifs les uns des autres.
LIGNES_SUPERVISEES = {
    RESP_L1: ("L1",),
    RESP_L2: ("L2",),
    CHEF_SERVICE: ("L1", "L2"),
}

#: Actions administratives nommées. Elles servent de vocabulaire commun à la
#: matrice route × action × rôle × ligne exigée par le client le 04/09/2026.
ACTION_SIMULER = "SIMULER"
ACTION_BROUILLON = "BROUILLON"
ACTION_OPERATIONNEL = "OPERATIONNEL"
ACTION_QUOTAS_SAISIR = "QUOTAS_SAISIR"
ACTION_QUOTAS_VALIDER = "QUOTAS_VALIDER"
ACTION_PUBLIER = "PUBLIER"
ACTION_DEROGER = "DEROGER"
ACTION_CONSULTER_AUDIT = "CONSULTER_AUDIT"

ACTIONS = (
    ACTION_SIMULER,
    ACTION_BROUILLON,
    ACTION_OPERATIONNEL,
    ACTION_QUOTAS_SAISIR,
    ACTION_QUOTAS_VALIDER,
    ACTION_PUBLIER,
    ACTION_DEROGER,
    ACTION_CONSULTER_AUDIT,
)

ACTIONS_LIBELLES = {
    ACTION_SIMULER: "Produire des simulations",
    ACTION_BROUILLON: "Produire et corriger des brouillons",
    ACTION_OPERATIONNEL: "Actions opérationnelles sur une ligne",
    ACTION_QUOTAS_SAISIR: "Saisir les quotas d'une ligne",
    ACTION_QUOTAS_VALIDER: "Valider les quotas",
    ACTION_PUBLIER: "Publication finale du planning",
    ACTION_DEROGER: "Dérogations transversales",
    ACTION_CONSULTER_AUDIT: "Consulter le journal d'audit",
}

#: Actions ouvertes **à toute fonction administrative**, sans distinction de
#: ligne. Les trois fonctions peuvent simuler et travailler des brouillons.
ACTIONS_COMMUNES_AUX_FONCTIONS = (ACTION_SIMULER, ACTION_BROUILLON)

#: Actions **portées par une ligne** : le périmètre de la fonction s'applique.
ACTIONS_PORTEES_PAR_LA_LIGNE = (ACTION_OPERATIONNEL, ACTION_QUOTAS_SAISIR)

#: Actions réservées au chef de service parmi les trois fonctions.
ACTIONS_CHEF_DE_SERVICE = (ACTION_QUOTAS_VALIDER,)

#: Actions qui exigent une **permission explicite**, sans pouvoir implicite lié
#: au simple accès à l'espace administratif.
ACTIONS_A_PERMISSION_EXPLICITE = {
    ACTION_PUBLIER: PUBLICATION,
    ACTION_DEROGER: PUBLICATION,
    ACTION_CONSULTER_AUDIT: CONSULTATION_AUDIT,
}

LIBELLES = {
    RESP_L1: "Responsable des gardes de première ligne",
    RESP_L2: "Responsable des gardes de deuxième ligne",
    CHEF_SERVICE: "Chef de service",
    GESTION_COMPTES: "Gestion des comptes",
    PUBLICATION: "Validation et publication du planning",
    CONSULTATION_AUDIT: "Consultation du journal d'audit",
}


class PermissionGrant(Base, TimestampMixin):
    """Attribution datée d'une permission à un compte.

    Une attribution n'est jamais supprimée : elle est révoquée en posant une date
    de fin, afin que l'historique des droits reste lisible a posteriori.
    """

    __tablename__ = "permission_grants"
    __table_args__ = (
        UniqueConstraint("user_id", "code", "start_date", name="uq_permission_grant"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    code: Mapped[str] = mapped_column(String(40), nullable=False)
    start_date: Mapped[date] = mapped_column(Date, nullable=False)
    end_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    granted_by_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id"), nullable=True
    )
    granted_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    comment: Mapped[str | None] = mapped_column(Text, nullable=True)

    user = relationship("User", foreign_keys=[user_id])

    def covers(self, jour: date) -> bool:
        if not self.active:
            return False
        if jour < self.start_date:
            return False
        if self.end_date is not None and jour > self.end_date:
            return False
        return True

    @property
    def label(self) -> str:
        return LIBELLES.get(self.code, self.code)
