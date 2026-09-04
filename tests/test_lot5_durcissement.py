"""Lot 5 — durcissement HTTP : CSRF, débit, session, en-têtes, journal.

Contre-audit du 04/09/2026, points 2, 3 et 6. Données fictives.
"""

from __future__ import annotations

import re

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.main import app
from app.models import AuditEvent, permissions
from app.services import http_security, notification_service, permission_service


def _client(world, utilisateur=None):
    world.session.commit()
    client = TestClient(app)
    if utilisateur is not None:
        reponse = client.post(
            "/api/v1/auth/login",
            json={"email": utilisateur.email, "password": "demo"},
        )
        assert reponse.status_code == 200, reponse.text
    return client


def _jeton(client) -> str:
    page = client.get("/connexion")
    trouve = re.search(
        rf'name="{http_security.CHAMP_CSRF}" value="([^"]+)"', page.text
    )
    assert trouve, "aucun jeton anti-rejeu dans le formulaire"
    return trouve.group(1)


# --------------------------------------------------------------------------- #
# CSRF
# --------------------------------------------------------------------------- #


def test_tous_les_formulaires_portent_le_jeton(world):
    """Le champ caché est présent partout, sinon l'interface serait inutilisable."""
    from pathlib import Path

    racine = Path(__file__).resolve().parents[1] / "app" / "web" / "templates"
    manquants = []
    for gabarit in racine.rglob("*.html"):
        texte = gabarit.read_text(encoding="utf-8")
        for formulaire in re.finditer(r'<form[^>]*method="post"[^>]*>', texte):
            suite = texte[formulaire.end() : formulaire.end() + 200]
            if http_security.CHAMP_CSRF not in suite:
                manquants.append(f"{gabarit.name}:{formulaire.group(0)[:40]}")
    assert manquants == [], manquants


def test_un_post_sans_jeton_est_refuse(world):
    client = _client(world, world.admin)
    reponse = client.post(
        "/deconnexion",
        headers={http_security.ENTETE_CSRF: ""},
        follow_redirects=False,
    )
    assert reponse.status_code == 403
    assert "anti-rejeu" in reponse.text


def test_un_post_avec_un_faux_jeton_est_refuse(world):
    client = _client(world, world.admin)
    reponse = client.post(
        "/deconnexion",
        headers={http_security.ENTETE_CSRF: "jeton-forge"},
        follow_redirects=False,
    )
    assert reponse.status_code == 403


def test_un_post_avec_le_bon_jeton_passe(world):
    client = _client(world, world.admin)
    jeton = _jeton(client)
    reponse = client.post(
        "/deconnexion",
        headers={http_security.ENTETE_CSRF: jeton},
        follow_redirects=False,
    )
    assert reponse.status_code == 303


def test_l_api_json_n_est_pas_soumise_au_jeton(world):
    """Exemption assumée et bornée : l'API n'a pas de formulaire ambiant."""
    client = TestClient(app)
    world.session.commit()
    reponse = client.post(
        "/api/v1/auth/login",
        json={"email": world.admin.email, "password": "demo"},
    )
    assert reponse.status_code == 200


def test_le_jeton_est_compare_a_temps_constant():
    faux = {http_security.CLE_CSRF: "attendu"}
    assert http_security.csrf_valide(faux, "attendu") is True
    assert http_security.csrf_valide(faux, "attend") is False
    assert http_security.csrf_valide({}, "quelconque") is False
    assert http_security.csrf_valide(faux, None) is False


def test_le_jeton_tourne_a_la_connexion(world):
    client = TestClient(app)
    world.session.commit()
    avant = _jeton(client)
    client.post(
        "/api/v1/auth/login",
        json={"email": world.admin.email, "password": "demo"},
    )
    apres = _jeton(client)
    assert avant != apres, "le jeton doit être renouvelé à l'ouverture de session"


# --------------------------------------------------------------------------- #
# Déconnexion
# --------------------------------------------------------------------------- #


def test_la_deconnexion_par_lien_est_refusee(world):
    client = _client(world, world.admin)
    reponse = client.get("/deconnexion", follow_redirects=False)
    assert reponse.status_code == 405


def test_la_deconnexion_par_post_fonctionne(world):
    client = _client(world, world.admin)
    jeton = _jeton(client)
    assert client.post(
        "/deconnexion",
        headers={http_security.ENTETE_CSRF: jeton},
        follow_redirects=False,
    ).status_code == 303
    # La session est bien vidée.
    assert client.get("/tableau-de-bord", follow_redirects=False).status_code in (303, 307, 401, 403)


# --------------------------------------------------------------------------- #
# Limitation de débit et journal d'authentification
# --------------------------------------------------------------------------- #


def test_les_echecs_repetes_finissent_bloques(world):
    http_security.limiteur.vider()
    client = TestClient(app)
    world.session.commit()
    codes = []
    for _ in range(http_security.MAX_ECHECS_PAR_IDENTIFIANT + 2):
        codes.append(
            client.post(
                "/api/v1/auth/login",
                json={"email": world.admin.email, "password": "mauvais"},
            ).status_code
        )
    assert 401 in codes
    assert 429 in codes, codes
    http_security.limiteur.vider()


def test_un_succes_reinitialise_le_compteur(world):
    http_security.limiteur.vider()
    client = TestClient(app)
    world.session.commit()
    for _ in range(2):
        client.post(
            "/api/v1/auth/login",
            json={"email": world.admin.email, "password": "mauvais"},
        )
    ok = client.post(
        "/api/v1/auth/login",
        json={"email": world.admin.email, "password": "demo"},
    )
    assert ok.status_code == 200
    assert http_security.limiteur.bloque(world.admin.email, "testclient") is None
    http_security.limiteur.vider()


def test_les_identifiants_demesures_sont_refuses(world):
    client = TestClient(app)
    world.session.commit()
    reponse = client.post(
        "/api/v1/auth/login",
        json={"email": "a" * 5000 + "@x.invalid", "password": "demo"},
    )
    assert reponse.status_code == 400


def test_les_bornes_sont_verifiees_avant_tout_traitement():
    assert http_security.identifiants_hors_bornes("a" * 300, "x") is not None
    assert http_security.identifiants_hors_bornes("a@b.c", "x" * 1000) is not None
    assert http_security.identifiants_hors_bornes("a@b.c", "x") is None


def test_le_journal_d_authentification_trace_succes_et_echecs(world):
    http_security.limiteur.vider()
    client = TestClient(app)
    world.session.commit()
    client.post(
        "/api/v1/auth/login",
        json={"email": world.admin.email, "password": "mauvais"},
    )
    client.post(
        "/api/v1/auth/login",
        json={"email": world.admin.email, "password": "demo"},
    )
    actions = [
        e.action
        for e in world.session.execute(select(AuditEvent)).scalars()
    ]
    assert "AUTHENTIFICATION_ECHEC" in actions
    assert "AUTHENTIFICATION_SUCCES" in actions
    http_security.limiteur.vider()


def test_le_journal_d_authentification_ne_contient_aucun_secret(world):
    http_security.limiteur.vider()
    client = TestClient(app)
    world.session.commit()
    client.post(
        "/api/v1/auth/login",
        json={"email": world.admin.email, "password": "un-secret-tres-visible"},
    )
    for evenement in world.session.execute(select(AuditEvent)).scalars():
        assert "un-secret-tres-visible" not in (evenement.payload_json or "")
    http_security.limiteur.vider()


# --------------------------------------------------------------------------- #
# Durée de session
# --------------------------------------------------------------------------- #


def test_une_session_administrative_expire_plus_vite():
    standard = http_security.durees_pour(False)
    administrative = http_security.durees_pour(True)
    assert administrative.inactivite < standard.inactivite
    assert administrative.absolue < standard.absolue


def test_l_inactivite_ferme_la_session():
    s = {}
    http_security.ouvrir_session(s, maintenant=1000.0)
    assert http_security.session_expiree(s, True, maintenant=1000.0) is None
    trop_tard = 1000.0 + http_security.INACTIVITE_ADMINISTRATIVE + 1
    assert "inactive" in http_security.session_expiree(s, True, maintenant=trop_tard)


def test_la_duree_absolue_ferme_la_session():
    s = {}
    http_security.ouvrir_session(s, maintenant=1000.0)
    # Activité régulière, mais durée absolue dépassée.
    fin = 1000.0 + http_security.ABSOLUE_ADMINISTRATIVE + 1
    http_security.marquer_activite(s, maintenant=fin - 10)
    assert "maximale" in http_security.session_expiree(s, True, maintenant=fin)


def test_une_session_sans_horodatage_est_consideree_expiree():
    """Fail-closed : une session héritée n'obtient pas une durée infinie."""
    assert http_security.session_expiree({}, False) is not None


def test_la_session_expiree_deconnecte_reellement(world, monkeypatch):
    client = _client(world, world.admin)
    assert client.get("/tableau-de-bord", follow_redirects=False).status_code == 200

    monkeypatch.setattr(
        http_security, "session_expiree", lambda *a, **k: "expiration forcée"
    )
    reponse = client.get("/tableau-de-bord", follow_redirects=False)
    assert reponse.status_code in (303, 307, 401, 403)


# --------------------------------------------------------------------------- #
# En-têtes de sécurité
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "entete, motif",
    [
        ("X-Content-Type-Options", "nosniff"),
        ("Referrer-Policy", "no-referrer"),
        ("X-Frame-Options", "DENY"),
        ("Content-Security-Policy", "frame-ancestors 'none'"),
        ("Permissions-Policy", "geolocation=()"),
    ],
)
def test_les_entetes_de_securite_sont_presents(world, entete, motif):
    client = TestClient(app)
    reponse = client.get("/connexion")
    assert motif in (reponse.headers.get(entete) or "")


def test_la_politique_csp_interdit_l_encadrement(world):
    client = TestClient(app)
    csp = client.get("/connexion").headers.get("Content-Security-Policy") or ""
    assert "frame-ancestors 'none'" in csp
    assert "object-src 'none'" in csp
    assert "form-action 'self'" in csp


# --------------------------------------------------------------------------- #
# Collision de notification : savepoint, pas rollback global
# --------------------------------------------------------------------------- #


def test_une_collision_de_notification_n_annule_pas_l_operation(world):
    """Reproduit le défaut signalé, au niveau du service."""
    session = world.session
    profil = world.seniors[0]
    cle = "collision:test:1"

    premiere = notification_service.enqueue(
        session, "CAMPAGNE_OUVERTURE", cle, profil, {"quarter": "T1"}
    )
    assert premiere is not None

    # Opération métier en cours, puis collision d'idempotence.
    profil.code = "MODIFIE-01"
    session.flush()
    seconde = notification_service.enqueue(
        session, "CAMPAGNE_OUVERTURE", cle, profil, {"quarter": "T1"}
    )
    assert seconde is None

    # L'opération métier a survécu.
    session.flush()
    assert profil.code == "MODIFIE-01"
    from app.models import Notification

    total = len(
        list(
            session.execute(
                select(Notification).where(Notification.idempotency_key == cle)
            ).scalars()
        )
    )
    assert total == 1
