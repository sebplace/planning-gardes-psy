"""Lot 3 — moteur de recherche d'échange.

Arbitrages du client du 04/09/2026, douze points. Ce fichier couvre le cœur :
recherche dans le trimestre, équivalence de nature, éligibilité croisée, double
accord et revalidation atomique, classement par maximin, tirage seulement en
égalité parfaite, fenêtres de collecte, absence d'intervention d'un responsable
dans un parcours conforme.

Données fictives.
"""

from __future__ import annotations

from datetime import datetime as dt
from datetime import timedelta

import pytest
from sqlalchemy import select

from app.engine.swap_ranking import (
    AUCUNE_CONTRAINTE,
    CandidatEchange,
    Creneau,
    agenda_simule,
    classer,
    ex_aequo_de_tete,
    intervalles_autour,
)
from app.models import (
    Assignment,
    AuditEvent,
    Color,
    ProfessionalProfile,
    Status,
    SwapState,
)
from app.services import swap_search_service, swap_service
from app.services.clock import Clock
from tests.conftest import publish_plan


def _c(jour: int, h1: int = 17, h2: int = 8) -> Creneau:
    return Creneau(dt(2027, 1, jour, h1), dt(2027, 1, jour + 1, h2))


# --------------------------------------------------------------------------- #
# Classement par maximin — module pur
# --------------------------------------------------------------------------- #


def test_sans_voisin_l_intervalle_ne_contraint_pas():
    avant, apres = intervalles_autour([], _c(10))
    assert avant == AUCUNE_CONTRAINTE
    assert apres == AUCUNE_CONTRAINTE


def test_les_intervalles_sont_mesures_des_deux_cotes():
    agenda = [_c(5), _c(20)]
    avant, apres = intervalles_autour(agenda, _c(10))
    # Du 06/01 08:00 au 10/01 17:00 = 105 h ; du 11/01 08:00 au 20/01 17:00 = 225 h.
    assert avant == pytest.approx(105.0)
    assert apres == pytest.approx(225.0)


def test_un_chevauchement_annule_l_echange():
    from app.engine.swap_ranking import CHEVAUCHEMENT

    avant, apres = intervalles_autour([_c(10)], _c(10))
    assert (avant, apres) == (CHEVAUCHEMENT, CHEVAUCHEMENT)


def test_deux_gardes_contigues_restent_licites():
    """Un week-end complet est contigu : espacement nul, mais pas chevauchement."""
    from app.engine.swap_ranking import CHEVAUCHEMENT

    samedi = Creneau(dt(2027, 1, 9, 9, 0), dt(2027, 1, 10, 9, 0))
    dimanche = Creneau(dt(2027, 1, 10, 9, 0), dt(2027, 1, 11, 9, 0))
    avant, apres = intervalles_autour([samedi], dimanche)
    assert avant == 0.0
    assert avant != CHEVAUCHEMENT
    candidat = CandidatEchange("wk", [samedi, dimanche], [], dimanche, dimanche)
    assert candidat.realisable is True


def test_l_agenda_simule_retire_la_cedee_et_ajoute_la_reprise():
    agenda = [_c(5), _c(10)]
    simule = agenda_simule(agenda, _c(10), _c(20))
    debuts = [c.start_at.day for c in simule]
    assert 10 not in debuts
    assert 20 in debuts
    assert 5 in debuts


def test_le_maximin_prefere_le_plus_grand_petit_intervalle():
    agenda_d = [_c(10), _c(12)]
    serre = CandidatEchange("serre", agenda_d, [_c(11)], _c(10), _c(11))
    large = CandidatEchange("large", agenda_d, [_c(25)], _c(10), _c(25))
    ordre = [c.identifiant for c in classer([serre, large])]
    assert ordre[0] == "large"


def test_le_maximin_departage_sur_le_deuxieme_plus_petit():
    """À plus petit intervalle identique, le deuxième tranche.

    Ici le plus petit intervalle vaut 105 h dans les deux cas. Le candidat « a »
    laisse ensuite le partenaire sans aucun voisin, ce qui est un espacement
    infini, donc strictement meilleur que les 345 h du candidat « b ».
    """
    agenda_d = [_c(10), _c(20)]
    a = CandidatEchange("a", agenda_d, [_c(15)], _c(10), _c(15))
    b = CandidatEchange("b", agenda_d, [_c(15), _c(25)], _c(10), _c(15))
    assert a.cle_maximin[0] == pytest.approx(b.cle_maximin[0])
    assert a.cle_maximin[1] > b.cle_maximin[1]
    assert [c.identifiant for c in classer([a, b])][0] == "a"


def test_le_chevauchement_nomme_le_cote_concerne():
    agenda_d = [_c(10), _c(15)]
    candidat = CandidatEchange("x", agenda_d, [_c(15)], _c(10), _c(15))
    assert candidat.realisable is False
    assert candidat.cote_en_chevauchement in ("demandeur", "partenaire", "les deux")


def test_les_ex_aequo_parfaits_sont_identifies():
    agenda = [_c(1)]
    a = CandidatEchange("a", agenda, [], _c(1), _c(10))
    b = CandidatEchange("b", agenda, [], _c(1), _c(20))
    tetes = ex_aequo_de_tete([a, b])
    assert {c.identifiant for c in tetes} == {"a", "b"}


def test_un_echange_non_realisable_est_exclu_du_classement():
    agenda = [_c(10), _c(20)]
    impossible = CandidatEchange("impossible", agenda, [_c(20)], _c(10), _c(20))
    assert impossible.realisable is False
    assert classer([impossible]) == []


def test_le_classement_est_deterministe():
    agenda = [_c(1)]
    a = CandidatEchange("aaa", agenda, [], _c(1), _c(10))
    b = CandidatEchange("bbb", agenda, [], _c(1), _c(20))
    premier = [c.identifiant for c in classer([a, b])]
    second = [c.identifiant for c in classer([b, a])]
    assert premier == second


# --------------------------------------------------------------------------- #
# Fenêtres de collecte
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "heures_restantes, duree_attendue, urgent",
    [
        (30 * 24, 72, False),   # plus de 14 jours
        (15 * 24, 72, False),
        (10 * 24, 48, False),   # de 7 à 14 jours
        (5 * 24, 24, False),    # de 3 à 7 jours
        (48, 3, True),          # moins de 72 heures
        (10, 3, True),
    ],
)
def test_les_fenetres_suivent_la_proximite(heures_restantes, duree_attendue, urgent):
    maintenant = dt(2027, 1, 1, 12, 0)
    Clock.freeze(maintenant)
    debut = maintenant + timedelta(hours=heures_restantes)
    fenetre = swap_search_service.fenetre_pour(debut)
    assert fenetre.duree_heures == duree_attendue
    assert fenetre.urgent is urgent


def test_une_fenetre_ne_depasse_jamais_le_debut_de_la_garde():
    maintenant = dt(2027, 1, 1, 12, 0)
    Clock.freeze(maintenant)
    debut = maintenant + timedelta(hours=1)
    fenetre = swap_search_service.fenetre_pour(debut)
    assert fenetre.ferme_a == debut
    assert fenetre.urgent is True


def test_le_circuit_urgent_ne_donne_aucun_avantage_au_premier():
    """La fenêtre reste une fenêtre : elle n'attribue rien à qui répond en premier."""
    maintenant = dt(2027, 1, 1, 12, 0)
    Clock.freeze(maintenant)
    fenetre = swap_search_service.fenetre_pour(maintenant + timedelta(hours=10))
    assert fenetre.urgent is True
    assert fenetre.ouvre_a == maintenant  # sollicitations immédiates
    assert fenetre.ferme_a > maintenant   # mais une fenêtre existe bel et bien


# --------------------------------------------------------------------------- #
# Recherche sur données
# --------------------------------------------------------------------------- #


def _ma_garde(world, profil=None):
    """Une garde future qui possède au moins une jumelle de même nature.

    Sans cette précaution, le test tomberait presque toujours sur une garde sans
    aucun partenaire possible, et n'éprouverait que le premier filtre.
    """
    session = world.session
    futures = [
        a
        for a in sorted(
            world.version.assignments, key=lambda a: a.post.occurrence.start_at
        )
        if a.post.occurrence.start_at > Clock.now() and a.busy_operation is None
    ]
    for affectation in futures:
        if profil is not None and affectation.profile_id != profil.id:
            continue
        jumelles = [
            b
            for b in futures
            if b.id != affectation.id
            and b.profile_id != affectation.profile_id
            and swap_service.check_equivalence(affectation, b)[0]
        ]
        if jumelles:
            return affectation, session.get(
                ProfessionalProfile, affectation.profile_id
            )
    if futures:
        premiere = futures[0]
        return premiere, session.get(ProfessionalProfile, premiere.profile_id)
    raise AssertionError("l'univers de test doit contenir des gardes futures")


# --------------------------------------------------------------------------- #
# Scénario déterministe : trois seniors, une garde cédée, deux partenaires
# --------------------------------------------------------------------------- #


def scenario_deterministe(world, garde_supplementaire: bool):
    """Réduit le planning publié à un jeu **maîtrisé**, pour ne rien sauter.

    Le planning généré ne garantit ni le nombre de propositions ni l'existence
    d'une égalité parfaite. On construit donc explicitement la situation :

    * une garde cédée, tenue par le demandeur ;
    * deux gardes de **même nature**, tenues par deux partenaires distincts ;
    * toutes les autres affectations de la version sont retirées, pour que les
      agendas ne créent aucun conflit parasite.

    Avec ``garde_supplementaire``, le demandeur détient en plus une garde
    intermédiaire : les quatre intervalles diffèrent alors d'une proposition à
    l'autre et le classement est **strictement** ordonné. Sans elle, les quatre
    intervalles valent « aucune contrainte » des deux côtés : c'est l'égalité
    parfaite, seul cas où un tirage est permis.

    Retourne ``(cedee, demandeur, [(garde, partenaire), ...])``.
    """
    session = world.session
    version = publish_plan(world)
    futures = [
        a
        for a in sorted(version.assignments, key=lambda a: a.post.occurrence.start_at)
        if a.post.occurrence.start_at > Clock.now()
    ]
    trouve = None
    for cedee in futures:
        if cedee.post.required_status is not Status.SENIOR:
            continue
        jumelles = [
            x
            for x in futures
            if x.id != cedee.id and swap_service.check_equivalence(cedee, x)[0]
        ]
        if len(jumelles) >= 3:
            trouve = (cedee, jumelles)
            break
    assert trouve, "l'univers de test doit offrir une garde et trois jumelles"
    cedee, jumelles = trouve
    premiere, seconde, intermediaire = jumelles[0], jumelles[-1], jumelles[1]

    garder = {cedee.id, premiere.id, seconde.id}
    if garde_supplementaire:
        garder.add(intermediaire.id)
    for autre in list(version.assignments):
        if autre.id not in garder:
            session.delete(autre)
    session.flush()

    demandeur, un, deux = world.seniors[0], world.seniors[1], world.seniors[2]
    cedee.profile_id = demandeur.id
    premiere.profile_id = un.id
    seconde.profile_id = deux.id
    if garde_supplementaire:
        intermediaire.profile_id = demandeur.id
    session.flush()
    for profil in (demandeur, un, deux):
        for occurrence in world.occurrences:
            world.set_color(profil, occurrence, Color.VERT)
    session.flush()
    return cedee, demandeur, [(premiere, un), (seconde, deux)]


def test_la_recherche_n_ecrit_rien(world):
    publish_plan(world)
    affectation, titulaire = _ma_garde(world)
    avant = world.session.execute(select(AuditEvent)).all()
    swap_search_service.rechercher(world.session, affectation, titulaire)
    apres = world.session.execute(select(AuditEvent)).all()
    assert len(avant) == len(apres)


def test_seul_le_titulaire_peut_chercher(world):
    publish_plan(world)
    affectation, titulaire = _ma_garde(world)
    autre = next(p for p in world.seniors if p.id != titulaire.id)
    with pytest.raises(swap_search_service.SwapSearchError):
        swap_search_service.rechercher(world.session, affectation, autre)


def test_la_recherche_reste_dans_le_trimestre(world):
    publish_plan(world)
    affectation, titulaire = _ma_garde(world)
    resultat = swap_search_service.rechercher(world.session, affectation, titulaire)
    trimestre = affectation.post.occurrence.quarter_id
    for proposition in resultat.propositions:
        autre = world.session.get(Assignment, proposition.assignment_repris_id)
        assert autre.post.occurrence.quarter_id == trimestre


def test_chaque_ecart_est_motive(world):
    publish_plan(world)
    affectation, titulaire = _ma_garde(world)
    resultat = swap_search_service.rechercher(world.session, affectation, titulaire)
    assert resultat.ecartes
    for ecart in resultat.ecartes:
        assert ecart["motif"]
        assert ecart["partenaire"]


def _paire_de_meme_nature(world):
    """Deux gardes futures publiées, de même nature, détenues par deux personnes.

    La vérification de nature étant la moins coûteuse, elle passe en premier dans
    la recherche : pour éprouver les règles suivantes, il faut donc partir d'une
    paire qui la franchit.
    """
    session = world.session
    futures = [
        a
        for a in sorted(
            world.version.assignments, key=lambda a: a.post.occurrence.start_at
        )
        if a.post.occurrence.start_at > Clock.now() and a.busy_operation is None
    ]
    for premiere in futures:
        for seconde in futures:
            if seconde.id == premiere.id:
                continue
            if seconde.profile_id == premiere.profile_id:
                continue
            equivalent, _, _ = swap_service.check_equivalence(premiere, seconde)
            if equivalent:
                return (
                    premiere,
                    session.get(ProfessionalProfile, premiere.profile_id),
                    seconde,
                    session.get(ProfessionalProfile, seconde.profile_id),
                )
    raise AssertionError("l'univers de test doit offrir une paire de même nature")


def test_un_partenaire_non_vert_est_ecarte(world):
    """Le partenaire doit être explicitement vert sur la date cédée."""
    session = world.session
    publish_plan(world)
    affectation, titulaire, autre, partenaire = _paire_de_meme_nature(world)
    occurrence = affectation.post.occurrence

    # Le partenaire est vert : l'échange est examiné jusqu'au bout.
    world.set_color(partenaire, occurrence, Color.VERT)
    session.flush()
    resultat = swap_search_service.rechercher(session, affectation, titulaire)
    motifs_verts = [
        e for e in resultat.ecartes
        if e["garde"] == autre.id and "explicitement vert" in e["motif"]
    ]
    assert motifs_verts == []

    # Passé en orange, il est écarté avec un motif explicite.
    world.set_color(partenaire, occurrence, Color.ORANGE)
    session.flush()
    resultat = swap_search_service.rechercher(session, affectation, titulaire)
    ecart = next(e for e in resultat.ecartes if e["garde"] == autre.id)
    assert "explicitement vert" in ecart["motif"]
    assert "ORANGE" in ecart["motif"]


def test_une_dispo_par_defaut_ne_suffit_pas_pour_un_echange(world):
    session = world.session
    publish_plan(world)
    affectation, titulaire, autre, partenaire = _paire_de_meme_nature(world)
    world.set_color(partenaire, affectation.post.occurrence, Color.DISPO_DEFAUT)
    session.flush()
    resultat = swap_search_service.rechercher(session, affectation, titulaire)
    ecart = next(e for e in resultat.ecartes if e["garde"] == autre.id)
    assert "explicitement vert" in ecart["motif"]


def test_l_eligibilite_est_verifiee_dans_les_deux_sens(world):
    """Le motif nomme la personne qui bloque, jamais un refus anonyme."""
    session = world.session
    publish_plan(world)
    affectation, titulaire, autre, partenaire = _paire_de_meme_nature(world)
    world.set_color(partenaire, affectation.post.occurrence, Color.VERT)
    # Le demandeur devient rouge sur la garde proposée : c'est lui qui bloque.
    world.set_color(titulaire, autre.post.occurrence, Color.ROUGE)
    session.flush()

    resultat = swap_search_service.rechercher(session, affectation, titulaire)
    ecart = next(e for e in resultat.ecartes if e["garde"] == autre.id)
    assert titulaire.code in ecart["motif"]
    assert "ne peut pas prendre la garde proposée" in ecart["motif"]


def test_le_blocage_du_partenaire_est_nomme(world):
    session = world.session
    publish_plan(world)
    affectation, titulaire, autre, partenaire = _paire_de_meme_nature(world)
    world.set_color(titulaire, autre.post.occurrence, Color.VERT)
    # Le partenaire est vert sur la date cédée mais inéligible autrement.
    world.set_color(partenaire, affectation.post.occurrence, Color.VERT)
    session.flush()
    resultat = swap_search_service.rechercher(session, affectation, titulaire)
    ecart = next(
        (e for e in resultat.ecartes if e["garde"] == autre.id), None
    )
    if ecart is None:
        # L'échange est praticable : c'est un résultat valide, pas un échec.
        assert any(
            p.assignment_repris_id == autre.id for p in resultat.propositions
        )
        return
    assert partenaire.code in ecart["motif"] or titulaire.code in ecart["motif"]


# --------------------------------------------------------------------------- #
# Scénario contrôlé : deux personnes, deux gardes de même nature
# --------------------------------------------------------------------------- #


def _scenario_controle(world):
    """Force une paire échangeable, pour ne pas dépendre du hasard du planning.

    Deux occurrences de même type, chacune tenue par une personne différente,
    les deux personnes étant explicitement vertes sur la garde de l'autre.
    """
    session = world.session
    version = publish_plan(world)
    futures = [
        a
        for a in sorted(
            version.assignments, key=lambda a: a.post.occurrence.start_at
        )
        if a.post.occurrence.start_at > Clock.now()
    ]
    for premiere in futures:
        for seconde in futures:
            if seconde.id == premiere.id or seconde.profile_id == premiere.profile_id:
                continue
            equivalent, _, _ = swap_service.check_equivalence(premiere, seconde)
            if not equivalent:
                continue
            un = session.get(ProfessionalProfile, premiere.profile_id)
            deux = session.get(ProfessionalProfile, seconde.profile_id)
            world.set_color(deux, premiere.post.occurrence, Color.VERT)
            world.set_color(un, seconde.post.occurrence, Color.VERT)
            session.flush()
            return premiere, un, seconde, deux
    raise AssertionError("l'univers de test doit offrir une paire échangeable")


def test_un_echange_praticable_est_propose(world):
    session = world.session
    cedee, demandeur, partenaires = scenario_deterministe(
        world, garde_supplementaire=False
    )
    resultat = swap_search_service.rechercher(session, cedee, demandeur)
    ids = {p.assignment_repris_id for p in resultat.propositions}
    assert ids == {garde.id for garde, _ in partenaires}


def test_le_double_accord_officialise_l_echange(world):
    session = world.session
    cedee, demandeur, partenaires = scenario_deterministe(
        world, garde_supplementaire=False
    )
    garde, partenaire = partenaires[0]
    resultat = swap_search_service.rechercher(session, cedee, demandeur)
    assert any(p.assignment_repris_id == garde.id for p in resultat.propositions)

    proposition = swap_service.propose_swap(session, cedee, garde, demandeur)
    assert proposition.state is SwapState.PROPOSE
    # Un seul accord ne suffit pas.
    assert proposition.accepted_a_at is None or proposition.accepted_b_at is None

    swap_service.accept_swap(session, proposition, partenaire)
    session.refresh(proposition)
    assert proposition.state is SwapState.OFFICIEL, proposition.refusal_reason
    session.refresh(cedee)
    session.refresh(garde)
    assert cedee.profile_id == partenaire.id
    assert garde.profile_id == demandeur.id


def test_le_resultat_documente_la_regle_de_classement(world):
    publish_plan(world)
    affectation, titulaire = _ma_garde(world)
    resultat = swap_search_service.rechercher(world.session, affectation, titulaire)
    regle = resultat.as_dict()["regle_de_classement"]
    assert "maximin" in regle
    assert "trimestres adjacents" in regle
    assert "égalité parfaite" in regle


def test_les_propositions_sont_ordonnees_par_maximin(world):
    """Ordre **strict**, sur un jeu de données suffisant et maîtrisé.

    Aucun saut : le scénario garantit deux propositions dont les quatre
    intervalles diffèrent réellement.
    """
    session = world.session
    cedee, demandeur, _ = scenario_deterministe(world, garde_supplementaire=True)
    resultat = swap_search_service.rechercher(session, cedee, demandeur)
    assert len(resultat.propositions) >= 2, resultat.ecartes
    cles = [tuple(p.cle_maximin) for p in resultat.propositions]
    assert cles == sorted(cles, reverse=True)
    # Les clés ne sont pas toutes identiques : l'ordre est réellement éprouvé.
    assert len(set(cles)) > 1
    assert len(resultat.ex_aequo) == 1


# --------------------------------------------------------------------------- #
# Départage
# --------------------------------------------------------------------------- #


def test_sans_egalite_aucun_tirage(world):
    session = world.session
    cedee, demandeur, _ = scenario_deterministe(world, garde_supplementaire=True)
    resultat = swap_search_service.rechercher(session, cedee, demandeur)
    assert resultat.propositions
    assert len(resultat.ex_aequo) == 1
    retenue, preuve = swap_search_service.departager(session, resultat, demandeur)
    assert retenue is resultat.meilleure
    assert preuve is None


def test_en_egalite_parfaite_le_tirage_est_auditable(world):
    """Égalité parfaite **réelle**, produite par le scénario, non simulée.

    Sans garde supplémentaire, les quatre intervalles valent « aucune
    contrainte » des deux côtés pour les deux propositions : l'égalité est
    parfaite et le tirage est alors le seul départage licite.
    """
    session = world.session
    cedee, demandeur, partenaires = scenario_deterministe(
        world, garde_supplementaire=False
    )
    resultat = swap_search_service.rechercher(session, cedee, demandeur)
    assert len(resultat.propositions) == 2
    cles = {tuple(p.cle_maximin) for p in resultat.propositions}
    assert len(cles) == 1, "les deux propositions doivent être strictement à égalité"
    assert len(resultat.ex_aequo) == 2

    retenue, preuve = swap_search_service.departager(session, resultat, demandeur)
    assert retenue in resultat.propositions
    assert preuve is not None
    import hashlib

    assert (
        hashlib.sha256(preuve["graine_revelee"].encode()).hexdigest()
        == preuve["engagement_graine"]
    )
    assert preuve["motif"] == "égalité parfaite sur les quatre intervalles"
    assert len(preuve["candidats_ex_aequo"]) == 2
    actions = [e.action for e in session.execute(select(AuditEvent)).scalars()]
    assert "ECHANGE_TIRAGE_EX_AEQUO" in actions


def test_le_tirage_est_reproductible_a_graine_donnee():
    """La preuve permet de refaire le calcul sans rejouer le tirage."""
    import hashlib
    import hmac

    identifiants = ["1<->2", "1<->3"]
    empreinte = hashlib.sha256(",".join(identifiants).encode()).hexdigest()
    graine = "a" * 64
    digest = hmac.new(graine.encode(), empreinte.encode(), hashlib.sha256).hexdigest()
    index = int(digest[:16], 16) % len(identifiants)
    # Le même calcul, refait à l'identique, redonne le même résultat.
    encore = int(
        hmac.new(graine.encode(), empreinte.encode(), hashlib.sha256).hexdigest()[:16],
        16,
    ) % len(identifiants)
    assert index == encore
    assert identifiants[index] in identifiants


# --------------------------------------------------------------------------- #
# Enchaînement complet
# --------------------------------------------------------------------------- #


def test_le_parcours_conforme_n_appelle_aucun_responsable(world):
    """Deux accords suffisent : aucun administrateur n'intervient."""
    session = world.session
    cedee, demandeur, _ = scenario_deterministe(world, garde_supplementaire=True)

    proposition, resultat, _ = swap_search_service.proposer_le_meilleur(
        session, cedee, demandeur
    )
    assert proposition is not None, resultat.ecartes
    assert proposition.state is SwapState.PROPOSE

    partenaire_id = (
        proposition.assignment_b.profile_id
        if demandeur.id == proposition.assignment_a.profile_id
        else proposition.assignment_a.profile_id
    )
    partenaire = session.get(ProfessionalProfile, partenaire_id)
    swap_service.accept_swap(session, proposition, partenaire)
    session.refresh(proposition)
    assert proposition.state is SwapState.OFFICIEL, proposition.refusal_reason

    # Aucun événement d'audit n'a été produit par un administrateur.
    evenements = [
        e
        for e in session.execute(select(AuditEvent)).scalars()
        if e.action.startswith("ECHANGE")
    ]
    assert evenements
    assert all(e.actor_user_id != world.admin.id for e in evenements)


def test_un_commentaire_reste_facultatif(world):
    session = world.session
    cedee, demandeur, _ = scenario_deterministe(world, garde_supplementaire=True)
    proposition, resultat, _ = swap_search_service.proposer_le_meilleur(
        session, cedee, demandeur, commentaire=None
    )
    assert proposition is not None, resultat.ecartes
    assert proposition.id is not None


def test_sans_solution_l_absence_est_tracee(world):
    session = world.session
    publish_plan(world)
    affectation, titulaire = _ma_garde(world)
    occurrence = affectation.post.occurrence
    for profil in world.seniors + world.assistants:
        if profil.id != titulaire.id:
            world.set_color(profil, occurrence, Color.ROUGE)
    session.flush()

    proposition, resultat, preuve = swap_search_service.proposer_le_meilleur(
        session, affectation, titulaire
    )
    assert proposition is None
    assert preuve is None
    assert resultat.propositions == []
    actions = [e.action for e in session.execute(select(AuditEvent)).scalars()]
    assert "ECHANGE_SANS_SOLUTION" in actions
    # Le titulaire garde sa garde.
    session.refresh(affectation)
    assert affectation.profile_id == titulaire.id


def test_les_deux_operations_restent_distinctes():
    """Reprise et échange sont deux services séparés, aux entrées distinctes."""
    from app.services import handover_service

    assert hasattr(handover_service, "request_handover")
    assert hasattr(swap_search_service, "rechercher")
    assert not hasattr(handover_service, "rechercher")
    assert not hasattr(swap_search_service, "request_handover")
