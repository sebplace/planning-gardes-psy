"""Tests des quotas, de la confidentialité et des effets d'une reprise.

Couvre les exigences §22 : 10, 11, 16.
"""

from __future__ import annotations

from sqlalchemy import select

from app.models import (
    Draw,
    Line,
    ProfessionalProfile,
    QuotaAdjustment,
    QuotaCategory,
    WaveKind,
    WaveSolicitation,
)
from app.services import handover_service, quota_service
from tests.conftest import publish_plan


def test_10_quotas_separes_par_categorie_et_ligne(world):
    session = world.session
    resume = quota_service.summary(session, world.seniors[0], world.year)
    cles = {(ligne.category_code, ligne.line) for ligne in resume.lines}
    assert ("NUITS_LJ", "L1") in cles
    assert ("NUITS_LJ", "L2") in cles
    assert ("WEEKENDS_VEILLES", "L1") in cles
    assert ("WEEKENDS_VEILLES", "L2") in cles
    assert len(cles) == len(resume.lines), "Chaque couple catégorie × ligne est distinct."

    # Modifier une cible n'affecte aucune autre combinaison.
    categories = {c.code: c for c in session.execute(select(QuotaCategory)).scalars()}
    quota_service.set_target(
        session, world.seniors[0], world.year, categories["NUITS_LJ"], Line.L1,
        3.0, world.admin,
    )
    resume = quota_service.summary(session, world.seniors[0], world.year)
    valeurs = {(l.category_code, l.line): l.target for l in resume.lines}
    assert valeurs[("NUITS_LJ", "L1")] == 3.0
    assert valeurs[("NUITS_LJ", "L2")] == 12.0
    assert valeurs[("WEEKENDS_VEILLES", "L1")] == 12.0


def test_10b_realise_et_programme_separes_par_categorie_et_ligne(world):
    session = world.session
    publish_plan(world)
    resume = quota_service.summary(session, world.seniors[0], world.year)
    total = sum(ligne.realise + ligne.programme for ligne in resume.lines)
    affectations = [
        a for a in world.version.assignments if a.profile_id == world.seniors[0].id
    ]
    assert total == len(affectations)
    for ligne in resume.lines:
        assert ligne.realise >= 0 and ligne.programme >= 0


def test_11_confidentialite_des_quotas(world):
    """Un médecin ne voit que ses propres quotas ; un administrateur voit le pool."""
    from fastapi.testclient import TestClient

    from app.main import app

    session = world.session
    session.commit()
    client = TestClient(app)

    moi = world.seniors[0]
    autre = world.seniors[1]

    reponse = client.post(
        "/api/v1/auth/login",
        json={"email": world.user_of(moi).email, "password": "demo"},
    )
    assert reponse.status_code == 200

    assert client.get(f"/api/v1/quotas/{moi.id}").status_code == 200
    interdit = client.get(f"/api/v1/quotas/{autre.id}")
    assert interdit.status_code == 403
    assert "collègue" in interdit.json()["detail"]

    client.post("/api/v1/auth/logout")
    client.post(
        "/api/v1/auth/login",
        json={"email": world.admin.email, "password": "demo"},
    )
    assert client.get(f"/api/v1/quotas/{autre.id}").status_code == 200


def test_16_quotas_apres_reprise(world):
    """La garde est comptabilisée à la personne qui l'assure réellement,
    retirée du compteur de la personne remplacée, et l'écart est reporté."""
    session = world.session
    publish_plan(world)

    demande = None
    wave = None
    titulaire = None
    for assignment in sorted(
        world.version.assignments, key=lambda a: a.post.occurrence.start_at
    ):
        session.refresh(assignment)
        if assignment.busy_operation is not None:
            continue
        titulaire = session.get(ProfessionalProfile, assignment.profile_id)
        demande = handover_service.request_handover(session, assignment, titulaire)
        wave = handover_service.open_wave(session, demande, WaveKind.VERTE)
        if wave.solicited_count >= 1:
            break
        handover_service.cancel_request(session, demande, world.admin)
        demande = None
    assert demande is not None

    avant_titulaire = quota_service.summary(session, titulaire, world.year).total_done

    solicites = [
        session.get(ProfessionalProfile, s.profile_id)
        for s in session.execute(
            select(WaveSolicitation).where(WaveSolicitation.wave_id == wave.id)
        ).scalars()
    ]
    for profile in solicites:
        handover_service.submit_candidacy(session, wave, profile)
    handover_service.advance(session, demande)

    draw = session.execute(select(Draw).where(Draw.wave_id == wave.id)).scalar_one()
    repreneur = session.get(ProfessionalProfile, draw.winner_profile_id)

    apres_titulaire = quota_service.summary(session, titulaire, world.year).total_done
    assert apres_titulaire == avant_titulaire - 1, (
        "La garde doit être retirée du compteur de la personne remplacée."
    )

    # La garde est comptée à la personne qui l'assure réellement.
    session.refresh(demande.assignment)
    assert demande.assignment.profile_id == repreneur.id
    resume_repreneur = quota_service.summary(session, repreneur, world.year)
    post = demande.assignment.post
    categorie = post.occurrence.garde_type.category.code
    ligne = next(
        l for l in resume_repreneur.lines
        if l.category_code == categorie and l.line == post.line.value
    )
    assert ligne.realise + ligne.programme >= 1

    # L'écart est reporté pour la campagne suivante, sans remanier le planning publié.
    ajustement = session.execute(
        select(QuotaAdjustment).where(QuotaAdjustment.profile_id == titulaire.id)
    ).scalars().first()
    assert ajustement is not None
    assert ajustement.carried_to_next_campaign is True
    assert repreneur.code in ajustement.reason
    assert world.version.state.value == "PUBLIE", (
        "Le planning publié n'est pas remanié : seule l'affectation concernée change."
    )
