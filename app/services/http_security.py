"""Durcissement HTTP : CSRF, limitation de débit, durée de session.

Lot 5 du contre-audit du 04/09/2026, points 6 et 3.

Conception :

* **CSRF** : jeton par session, déposé dans la session signée et exigé sur tout
  POST d'interface. L'API JSON en est exemptée, car elle ne s'appuie pas sur le
  cookie ambiant pour un navigateur tiers ; elle reste protégée par
  ``SameSite=Lax`` et par l'absence de formulaire HTML.
* **Limitation de débit** : compteur en mémoire par identifiant et par adresse,
  avec fenêtre glissante. Suffisant pour un prototype mono-instance ; à remplacer
  par un magasin partagé avant toute mise en service réelle, ce qui est consigné.
* **Durée de session** : inactivité et durée absolue, plus courtes pour un compte
  disposant d'un accès administratif.
* **Journal d'authentification** : chaque succès et chaque échec est tracé.

Aucune donnée patient, aucune donnée réelle.
"""

from __future__ import annotations

import hmac
import secrets
import time
from collections import defaultdict, deque
from dataclasses import dataclass

CLE_CSRF = "csrf"
CHAMP_CSRF = "csrf_token"
ENTETE_CSRF = "X-CSRF-Token"

CLE_DERNIERE_ACTIVITE = "vu_a"
CLE_DEBUT_SESSION = "ouverte_a"

#: Durées de session, en secondes. Valeurs de démonstration, administrables.
INACTIVITE_STANDARD = 8 * 3600
ABSOLUE_STANDARD = 24 * 3600
INACTIVITE_ADMINISTRATIVE = 30 * 60
ABSOLUE_ADMINISTRATIVE = 8 * 3600

#: Limitation de débit sur l'authentification.
FENETRE_SECONDES = 300
MAX_ECHECS_PAR_IDENTIFIANT = 5
MAX_TENTATIVES_PAR_ADRESSE = 20

#: Longueur maximale acceptée pour un identifiant, afin de borner le coût du
#: hachage et la taille des journaux.
LONGUEUR_MAX_IDENTIFIANT = 254
LONGUEUR_MAX_MOT_DE_PASSE = 256


# --------------------------------------------------------------------------- #
# CSRF
# --------------------------------------------------------------------------- #


def jeton_csrf(session_navigateur: dict) -> str:
    """Jeton de la session, créé à la volée s'il n'existe pas encore."""
    jeton = session_navigateur.get(CLE_CSRF)
    if not jeton:
        jeton = secrets.token_urlsafe(32)
        session_navigateur[CLE_CSRF] = jeton
    return jeton


def csrf_valide(session_navigateur: dict, fourni: str | None) -> bool:
    """Comparaison à temps constant entre le jeton de session et celui fourni."""
    attendu = session_navigateur.get(CLE_CSRF)
    if not attendu or not fourni:
        return False
    return hmac.compare_digest(str(attendu), str(fourni))


def rotation_csrf(session_navigateur: dict) -> str:
    """Régénère le jeton, par exemple après une connexion réussie."""
    session_navigateur.pop(CLE_CSRF, None)
    return jeton_csrf(session_navigateur)


# --------------------------------------------------------------------------- #
# Durée de session
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class DureesSession:
    inactivite: int
    absolue: int


def durees_pour(acces_administratif: bool) -> DureesSession:
    if acces_administratif:
        return DureesSession(INACTIVITE_ADMINISTRATIVE, ABSOLUE_ADMINISTRATIVE)
    return DureesSession(INACTIVITE_STANDARD, ABSOLUE_STANDARD)


def session_expiree(
    session_navigateur: dict, acces_administratif: bool, maintenant: float | None = None
) -> str | None:
    """Motif d'expiration, ou ``None`` si la session est encore valable."""
    maintenant = maintenant if maintenant is not None else time.time()
    durees = durees_pour(acces_administratif)

    ouverte_a = session_navigateur.get(CLE_DEBUT_SESSION)
    vu_a = session_navigateur.get(CLE_DERNIERE_ACTIVITE)
    if ouverte_a is None or vu_a is None:
        # Session sans horodatage : héritée d'une version antérieure. On la
        # considère comme expirée plutôt que de lui accorder une durée infinie.
        return "session sans horodatage"
    if maintenant - float(ouverte_a) > durees.absolue:
        return "durée maximale de session atteinte"
    if maintenant - float(vu_a) > durees.inactivite:
        return "session inactive trop longtemps"
    return None


def marquer_activite(session_navigateur: dict, maintenant: float | None = None) -> None:
    maintenant = maintenant if maintenant is not None else time.time()
    session_navigateur.setdefault(CLE_DEBUT_SESSION, maintenant)
    session_navigateur[CLE_DERNIERE_ACTIVITE] = maintenant


def ouvrir_session(session_navigateur: dict, maintenant: float | None = None) -> None:
    """Ouvre une session neuve : horodatages remis à zéro et jeton renouvelé."""
    maintenant = maintenant if maintenant is not None else time.time()
    session_navigateur[CLE_DEBUT_SESSION] = maintenant
    session_navigateur[CLE_DERNIERE_ACTIVITE] = maintenant
    rotation_csrf(session_navigateur)


# --------------------------------------------------------------------------- #
# Limitation de débit
# --------------------------------------------------------------------------- #


class LimiteurMemoire:
    """Fenêtre glissante en mémoire.

    Limite volontairement assumée : le compteur est local au processus. Sur une
    instance multi-conteneurs, la limitation devient partielle. C'est acceptable
    pour un prototype, et consigné comme porte à franchir avant toute donnée
    réelle.
    """

    def __init__(self) -> None:
        self._echecs: dict[str, deque[float]] = defaultdict(deque)
        self._tentatives: dict[str, deque[float]] = defaultdict(deque)

    @staticmethod
    def _purger(file: deque[float], maintenant: float) -> None:
        while file and maintenant - file[0] > FENETRE_SECONDES:
            file.popleft()

    def bloque(
        self, identifiant: str, adresse: str, maintenant: float | None = None
    ) -> str | None:
        maintenant = maintenant if maintenant is not None else time.time()
        cle = (identifiant or "").strip().lower()
        echecs = self._echecs[cle]
        tentatives = self._tentatives[adresse or "?"]
        self._purger(echecs, maintenant)
        self._purger(tentatives, maintenant)
        if len(echecs) >= MAX_ECHECS_PAR_IDENTIFIANT:
            return "trop de tentatives infructueuses pour cet identifiant"
        if len(tentatives) >= MAX_TENTATIVES_PAR_ADRESSE:
            return "trop de tentatives depuis cette adresse"
        return None

    def enregistrer_tentative(
        self, adresse: str, maintenant: float | None = None
    ) -> None:
        maintenant = maintenant if maintenant is not None else time.time()
        self._tentatives[adresse or "?"].append(maintenant)

    def enregistrer_echec(
        self, identifiant: str, maintenant: float | None = None
    ) -> None:
        maintenant = maintenant if maintenant is not None else time.time()
        self._echecs[(identifiant or "").strip().lower()].append(maintenant)

    def reinitialiser(self, identifiant: str) -> None:
        self._echecs.pop((identifiant or "").strip().lower(), None)

    def vider(self) -> None:
        self._echecs.clear()
        self._tentatives.clear()


limiteur = LimiteurMemoire()


# --------------------------------------------------------------------------- #
# Bornes d'entrée
# --------------------------------------------------------------------------- #


def identifiants_hors_bornes(email: str | None, mot_de_passe: str | None) -> str | None:
    """Refuse une entrée démesurée avant tout traitement coûteux."""
    if email is not None and len(email) > LONGUEUR_MAX_IDENTIFIANT:
        return "identifiant trop long"
    if mot_de_passe is not None and len(mot_de_passe) > LONGUEUR_MAX_MOT_DE_PASSE:
        return "mot de passe trop long"
    return None
