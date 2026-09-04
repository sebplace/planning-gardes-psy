"""Périmètre de lecture : qui a le droit de voir quoi, et avec quel niveau de détail.

Lot A du contre-audit du 04/09/2026. Trois principes, appliqués au **niveau
métier** pour que l'interface et l'API ne puissent pas diverger :

1. **Aucune énumération d'identifiants.** Une ressource que la personne n'a pas
   le droit de voir répond ``404`` exactement comme une ressource inexistante :
   la réponse ne révèle donc pas l'existence de l'objet.
2. **Acteurs légitimes seulement.** Une reprise, une candidature, un tirage, une
   exclusion ou un échange n'est lisible que par les personnes réellement
   concernées, plus les responsables compétents sur la ligne concernée.
3. **Contrat d'anonymat honnête.** Le planning publié étant nominatif, prétendre
   masquer le demandeur d'une reprise serait une fausse promesse : le titulaire
   de la garde est déductible. Le contrat réellement tenable est plus étroit et
   il est écrit noir sur blanc dans ``CONTRAT_ANONYMAT`` : la sollicitation ne
   mentionne ni le nom du demandeur ni son motif. Les motifs, commentaires et
   détails administratifs restent, eux, réellement restreints.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import (
    Candidacy,
    Draw,
    HandoverRequest,
    HandoverWave,
    ProfessionalProfile,
    ScheduleState,
    ScheduleVersion,
    SwapProposal,
    SwapSearch,
    User,
    WaveSolicitation,
)
from ..models import permissions
from . import permission_service

#: Contrat d'anonymat réellement tenu par l'application, à afficher tel quel.
CONTRAT_ANONYMAT = (
    "La sollicitation ne mentionne ni le nom du demandeur ni son motif. "
    "Le planning publié restant nominatif, l'application ne prétend pas rendre "
    "le titulaire d'une garde indevinable : elle garantit que le message envoyé "
    "n'expose ni identité ni motif, et que les commentaires et pièces "
    "administratives restent réservés au demandeur et aux responsables "
    "compétents."
)

#: Message unique de ressource non lisible. Volontairement identique à celui
#: d'une ressource inexistante.
INTROUVABLE = "Ressource introuvable."


class RessourceInvisible(Exception):
    """Ressource inexistante **ou** hors du périmètre de lecture de la personne.

    Le même message est utilisé dans les deux cas afin de ne pas révéler
    l'existence d'un identifiant par différence de réponse.
    """

    def __init__(self, message: str = INTROUVABLE) -> None:
        super().__init__(message)


# --------------------------------------------------------------------------- #
# Utilitaires
# --------------------------------------------------------------------------- #


def _profile_id_of(session: Session, user: User | None) -> int | None:
    if user is None or not user.is_medecin:
        return None
    profile = session.execute(
        select(ProfessionalProfile).where(ProfessionalProfile.user_id == user.id)
    ).scalar_one_or_none()
    return profile.id if profile else None


def _supervise(session: Session, user: User | None, ligne: str) -> bool:
    """Responsable compétent **sur cette ligne**, ou chef de service."""
    return permission_service.supervises_line(session, user, ligne)


# --------------------------------------------------------------------------- #
# Versions de planning
# --------------------------------------------------------------------------- #


def version_lisible(
    session: Session, user: User | None, version: ScheduleVersion | None
) -> bool:
    """Une version publiée est lisible par tout compte authentifié.

    Toute autre version (brouillon, en révision, remplacée, archivée) est un
    document de travail administratif : elle exige l'action ``BROUILLON``.
    """
    if user is None or version is None:
        return False
    if version.state is ScheduleState.PUBLIE:
        return True
    return permission_service.may(session, user, permissions.ACTION_BROUILLON)


def assert_version_lisible(
    session: Session, user: User | None, version: ScheduleVersion | None
) -> ScheduleVersion:
    if not version_lisible(session, user, version):
        raise RessourceInvisible()
    return version


# --------------------------------------------------------------------------- #
# Reprises
# --------------------------------------------------------------------------- #


def _profils_impliques(session: Session, request: HandoverRequest) -> set[int]:
    """Toutes les personnes réellement concernées par une demande de reprise."""
    impliques: set[int] = {request.requester_profile_id}
    if request.result_profile_id:
        impliques.add(request.result_profile_id)
    if request.assignment is not None:
        impliques.add(request.assignment.profile_id)
    vagues = [w.id for w in request.waves]
    if vagues:
        impliques.update(
            session.execute(
                select(WaveSolicitation.profile_id).where(
                    WaveSolicitation.wave_id.in_(vagues)
                )
            ).scalars()
        )
        impliques.update(
            session.execute(
                select(Candidacy.profile_id).where(Candidacy.wave_id.in_(vagues))
            ).scalars()
        )
    return impliques


def reprise_lisible(
    session: Session, user: User | None, request: HandoverRequest | None
) -> bool:
    """Lecture d'une demande de reprise.

    Sont légitimes : les personnes réellement impliquées (demandeur, titulaire,
    sollicités, candidats, bénéficiaire) et les trois fonctions administratives.
    Lire n'est pas agir : le périmètre de **ligne** ne restreint pas la lecture
    mais reste opposable à l'avancement et aux détails restreints, afin qu'un
    responsable de première ligne reçoive bien un refus explicite ``403`` sur
    une reprise de deuxième ligne plutôt qu'un ``404`` trompeur.
    """
    if user is None or request is None:
        return False
    if permission_service.has_administrative_access(session, user):
        return True
    profile_id = _profile_id_of(session, user)
    if profile_id is None:
        return False
    return profile_id in _profils_impliques(session, request)


def assert_reprise_lisible(
    session: Session, user: User | None, request: HandoverRequest | None
) -> HandoverRequest:
    if not reprise_lisible(session, user, request):
        raise RessourceInvisible()
    return request


def details_reprise_visibles(
    session: Session, user: User | None, request: HandoverRequest | None
) -> bool:
    """Commentaire, motif administratif et exclusions nominatives des autres.

    Réservés au demandeur et au responsable compétent sur la ligne.
    """
    if user is None or request is None:
        return False
    if _supervise(session, user, request.assignment.post.line.value):
        return True
    profile_id = _profile_id_of(session, user)
    return profile_id is not None and profile_id == request.requester_profile_id


def reprises_visibles(session: Session, user: User | None) -> list[HandoverRequest]:
    """Liste filtrée côté serveur, jamais filtrée seulement à l'affichage."""
    if user is None:
        return []
    toutes = list(
        session.execute(
            select(HandoverRequest).order_by(HandoverRequest.id.desc())
        ).scalars()
    )
    return [d for d in toutes if reprise_lisible(session, user, d)]


def assert_vague_lisible(
    session: Session, user: User | None, wave: HandoverWave | None
) -> HandoverWave:
    if wave is None:
        raise RessourceInvisible()
    assert_reprise_lisible(session, user, wave.request)
    return wave


def assert_tirage_lisible(
    session: Session, user: User | None, draw: Draw | None
) -> Draw:
    if draw is None:
        raise RessourceInvisible()
    assert_reprise_lisible(session, user, draw.wave.request)
    return draw


# --------------------------------------------------------------------------- #
# Échanges
# --------------------------------------------------------------------------- #


def _profils_echange(proposal: SwapProposal) -> set[int]:
    return {
        proposal.proposer_profile_id,
        proposal.announced_profile_a_id,
        proposal.announced_profile_b_id,
        proposal.assignment_a.profile_id,
        proposal.assignment_b.profile_id,
    }


def echange_lisible(
    session: Session, user: User | None, proposal: SwapProposal | None
) -> bool:
    if user is None or proposal is None:
        return False
    if permission_service.has_administrative_access(session, user):
        return True
    profile_id = _profile_id_of(session, user)
    return profile_id is not None and profile_id in _profils_echange(proposal)


def assert_echange_lisible(
    session: Session, user: User | None, proposal: SwapProposal | None
) -> SwapProposal:
    if not echange_lisible(session, user, proposal):
        raise RessourceInvisible()
    return proposal


def echanges_visibles(session: Session, user: User | None) -> list[SwapProposal]:
    if user is None:
        return []
    toutes = list(
        session.execute(
            select(SwapProposal).order_by(SwapProposal.id.desc())
        ).scalars()
    )
    return [p for p in toutes if echange_lisible(session, user, p)]


# --------------------------------------------------------------------------- #
# Recherches d'échange (parcours nominal, lot B)
# --------------------------------------------------------------------------- #


def recherche_lisible(
    session: Session, user: User | None, search: SwapSearch | None
) -> bool:
    if user is None or search is None:
        return False
    if permission_service.has_administrative_access(session, user):
        return True
    profile_id = _profile_id_of(session, user)
    if profile_id is None:
        return False
    if profile_id == search.requester_profile_id:
        return True
    return any(c.profile_id == profile_id for c in search.candidates)


def assert_recherche_lisible(
    session: Session, user: User | None, search: SwapSearch | None
) -> SwapSearch:
    if not recherche_lisible(session, user, search):
        raise RessourceInvisible()
    return search


def details_recherche_visibles(
    session: Session, user: User | None, search: SwapSearch | None
) -> bool:
    """Commentaire, classement complet et exclusions nominatives.

    Réservés au demandeur et au responsable compétent sur la ligne de la garde
    cédée. Un partenaire sollicité voit sa propre proposition, pas le détail des
    autres.
    """
    if user is None or search is None:
        return False
    if _supervise(session, user, search.assignment.post.line.value):
        return True
    profile_id = _profile_id_of(session, user)
    return profile_id is not None and profile_id == search.requester_profile_id


def recherches_visibles(session: Session, user: User | None) -> list[SwapSearch]:
    if user is None:
        return []
    toutes = list(
        session.execute(select(SwapSearch).order_by(SwapSearch.id.desc())).scalars()
    )
    return [s for s in toutes if recherche_lisible(session, user, s)]
