"""Lot F — non-régression du défaut trouvé par la recette navigateur.

Le contrôle anti-rejeu lisait le formulaire pour y chercher le jeton, ce qui
**consommait** le corps de la requête : la route en aval ne recevait plus aucun
champ et répondait 422. Les tests ne l'avaient pas vu parce qu'ils transmettent
le jeton par en-tête, chemin qui ne lit pas le corps.

Ce fichier éprouve explicitement le chemin **formulaire**, celui qu'utilise un
vrai navigateur.

Données entièrement fictives.
"""

from __future__ import annotations

import re

from fastapi.testclient import TestClient

from app.main import app
from app.services import http_security


def _jeton(client: TestClient) -> str:
    page = client.get("/connexion")
    trouve = re.search(
        rf'name="{http_security.CHAMP_CSRF}" value="([^"]+)"', page.text
    )
    assert trouve, "le formulaire doit porter un jeton anti-rejeu"
    return trouve.group(1)


def test_F_le_jeton_dans_le_formulaire_ne_consomme_pas_le_corps(world):
    """Le chemin navigateur : jeton dans le corps, champs métier préservés."""
    world.session.commit()
    client = TestClient(app)
    jeton = _jeton(client)
    utilisateur = world.user_of(world.seniors[0])
    reponse = client.post(
        "/connexion",
        data={
            http_security.CHAMP_CSRF: jeton,
            "email": utilisateur.email,
            "mot_de_passe": "demo",
        },
        follow_redirects=False,
    )
    assert reponse.status_code != 422, reponse.text
    assert reponse.status_code in (303, 307), reponse.text


def test_F_un_jeton_de_formulaire_faux_est_toujours_refuse(world):
    world.session.commit()
    client = TestClient(app)
    _jeton(client)
    reponse = client.post(
        "/connexion",
        data={
            http_security.CHAMP_CSRF: "jeton-invente",
            "email": "sen01@demo.invalid",
            "mot_de_passe": "demo",
        },
        follow_redirects=False,
    )
    assert reponse.status_code == 403, reponse.text


def test_F_l_extraction_du_jeton_couvre_les_deux_encodages():
    corps = b"csrf_token=abc123&email=x%40y.invalid"
    assert (
        http_security.jeton_du_corps(corps, "application/x-www-form-urlencoded")
        == "abc123"
    )
    multipart = (
        b"--X\r\nContent-Disposition: form-data; name=\"csrf_token\"\r\n\r\n"
        b"def456\r\n--X--\r\n"
    )
    assert (
        http_security.jeton_du_corps(multipart, "multipart/form-data; boundary=X")
        == "def456"
    )
    assert http_security.jeton_du_corps(b"", "application/x-www-form-urlencoded") is None
