"""Lot 1.4 — les garde-fous destructifs sont fail-closed.

Contre-audit du 04/09/2026 : trois défauts de conception étaient présents.

1. Une valeur inconnue de ``GARDES_ENVIRONMENT`` retombait silencieusement sur
   « démonstration », donc autorisait un seed destructif.
2. L'échec du recensement des comptes rendait ``None``, et le verrou destructif
   était alors purement et simplement sauté.
3. La présence d'**un seul** compte fictif suffisait à autoriser la destruction,
   même si la base contenait par ailleurs des comptes réels.

Aucune donnée réelle n'est utilisée : tous les tests portent sur des refus.
"""

from __future__ import annotations

import pytest

from app.config import settings
from app.services import environment as envsvc

STRONG_SECRET = "x" * 64


# --------------------------------------------------------------------------- #
# 1. Environnement inconnu ou ambigu
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "valeur", ["", "   ", "inconnu", "PRODUCTIONN", "dev", "qa", "staging2", "null"]
)
def test_un_environnement_inconnu_leve_une_erreur(monkeypatch, valeur):
    monkeypatch.setattr(settings, "environment", valeur)
    with pytest.raises(envsvc.EnvironmentError_):
        envsvc.env_name()


@pytest.mark.parametrize("valeur", ["", "inconnu", "dev"])
def test_un_environnement_inconnu_bloque_le_seed_destructif(monkeypatch, valeur):
    monkeypatch.setattr(settings, "environment", valeur)
    with pytest.raises(SystemExit) as exc:
        envsvc.assert_destructive_seed_allowed()
    assert "inconnue" in str(exc.value)


def test_un_environnement_inconnu_bloque_le_demarrage(monkeypatch):
    monkeypatch.setattr(settings, "environment", "n_importe_quoi")
    with pytest.raises(SystemExit) as exc:
        envsvc.assert_startup_safe()
    assert "inconnue" in str(exc.value)


@pytest.mark.parametrize(
    "alias, attendu",
    [
        ("demo", envsvc.DEMONSTRATION),
        ("DEMO", envsvc.DEMONSTRATION),
        (" prod ", envsvc.PRODUCTION),
        ("preprod", envsvc.STAGING),
    ],
)
def test_les_alias_explicites_restent_acceptes(monkeypatch, alias, attendu):
    """Le fail-closed ne casse pas les alias volontairement prévus."""
    monkeypatch.setattr(settings, "environment", alias)
    assert envsvc.env_name() == attendu


# --------------------------------------------------------------------------- #
# 2. Base impossible à contrôler
# --------------------------------------------------------------------------- #


def test_une_base_injoignable_bloque_le_seed(monkeypatch):
    def _explose():
        raise envsvc.EnvironmentError_(
            "Base de données injoignable ou illisible (OperationalError). "
            "Le contexte est ambigu : aucune opération destructive n'est autorisée."
        )

    monkeypatch.setattr(settings, "environment", "demonstration")
    monkeypatch.setattr(envsvc, "account_census", _explose)
    with pytest.raises(SystemExit) as exc:
        envsvc.assert_destructive_seed_allowed()
    assert "ambigu" in str(exc.value)


def test_une_url_de_base_sans_nom_bloque_le_seed(monkeypatch):
    monkeypatch.setattr(settings, "environment", "demonstration")
    monkeypatch.setattr(settings, "database_url", "sqlite://")
    with pytest.raises(SystemExit) as exc:
        envsvc.assert_destructive_seed_allowed()
    assert "identifier la base" in str(exc.value)


def test_l_empreinte_de_base_ne_contient_aucun_secret(monkeypatch):
    monkeypatch.setattr(
        settings,
        "database_url",
        "postgresql+psycopg://utilisateur:motdepasse@hote:5432/base_demo",
    )
    empreinte = envsvc.database_fingerprint()
    assert empreinte == "base_demo"
    assert "motdepasse" not in empreinte
    assert "utilisateur" not in empreinte
    assert "hote" not in empreinte


# --------------------------------------------------------------------------- #
# 3. Allowlist explicite des bases fictives
# --------------------------------------------------------------------------- #


def test_une_base_hors_allowlist_bloque_le_seed(monkeypatch):
    monkeypatch.setattr(settings, "environment", "demonstration")
    monkeypatch.setattr(
        settings, "database_url", "postgresql+psycopg://u:p@h:5432/base_inconnue"
    )
    monkeypatch.setattr(settings, "demo_database_allowlist", "")
    with pytest.raises(SystemExit) as exc:
        envsvc.assert_destructive_seed_allowed()
    assert "allowlist" in str(exc.value)
    assert "base_inconnue" in str(exc.value)


def test_une_base_declaree_explicitement_est_acceptee(world, monkeypatch):
    monkeypatch.setattr(settings, "environment", "demonstration")
    monkeypatch.setattr(
        settings, "demo_database_allowlist", "autre.db, test_gardes.db"
    )
    assert "test_gardes.db" in envsvc.demo_database_allowlist()
    envsvc.assert_destructive_seed_allowed()


def test_l_allowlist_ne_devine_jamais(monkeypatch):
    """Une base au nom « rassurant » n'est pas autorisée pour autant."""
    monkeypatch.setattr(settings, "environment", "demonstration")
    monkeypatch.setattr(settings, "demo_database_allowlist", "")
    for nom in ("demo_prod", "gardes_demo", "fictif", "sandbox"):
        monkeypatch.setattr(
            settings, "database_url", f"postgresql+psycopg://u:p@h:5432/{nom}"
        )
        with pytest.raises(SystemExit):
            envsvc.assert_destructive_seed_allowed()


# --------------------------------------------------------------------------- #
# 4. Coexistence de comptes fictifs et non fictifs
# --------------------------------------------------------------------------- #


def test_un_seul_compte_reel_suffit_a_bloquer(world, monkeypatch):
    """Le défaut central : la coexistence ne vaut pas autorisation."""
    from app.models import User
    from app.services import security

    world.session.add(
        User(
            email="personne.reelle@exemple.be",
            display_name="Compte non fictif",
            password_hash=security.hash_password("x"),
        )
    )
    world.session.commit()

    monkeypatch.setattr(settings, "environment", "demonstration")
    with pytest.raises(SystemExit) as exc:
        envsvc.assert_destructive_seed_allowed()
    assert "non fictif" in str(exc.value)


def test_une_base_entierement_fictive_est_acceptee(world, monkeypatch):
    monkeypatch.setattr(settings, "environment", "demonstration")
    total, fictifs = envsvc.account_census()
    assert total == fictifs > 0
    envsvc.assert_destructive_seed_allowed()


def test_une_base_vide_est_acceptee(session, monkeypatch):
    """Premier peuplement d'une base neuve : autorisé, car sans ambiguïté."""
    monkeypatch.setattr(settings, "environment", "demonstration")
    total, fictifs = envsvc.account_census()
    assert (total, fictifs) == (0, 0)
    envsvc.assert_destructive_seed_allowed()


def test_la_production_reste_interdite_meme_si_tout_est_fictif(world, monkeypatch):
    monkeypatch.setattr(settings, "environment", "production")
    with pytest.raises(SystemExit) as exc:
        envsvc.assert_destructive_seed_allowed()
    assert "production" in str(exc.value)


# --------------------------------------------------------------------------- #
# Cumul des verrous
# --------------------------------------------------------------------------- #


def test_les_motifs_de_refus_sont_cumules(world, monkeypatch):
    """Un refus nomme toutes ses causes, pas seulement la première."""
    from app.models import User
    from app.services import security

    world.session.add(
        User(
            email="reel@exemple.be",
            display_name="Compte non fictif",
            password_hash=security.hash_password("x"),
        )
    )
    world.session.commit()
    monkeypatch.setattr(settings, "environment", "production")
    monkeypatch.setattr(settings, "demo_database_allowlist", "")
    monkeypatch.setattr(
        settings, "database_url", "postgresql+psycopg://u:p@h:5432/base_reelle"
    )

    problemes = envsvc.destructive_seed_problems()
    assert len(problemes) >= 2
    joint = " ".join(problemes)
    assert "production" in joint
    assert "allowlist" in joint
