"""Contraintes fermes.

Vérifiées **avant** toute optimisation. Aucune n'est jamais relâchée automatiquement,
et aucune ne dispose d'un mécanisme de dérogation (cf. DECISIONS.md M-005).
"""

from __future__ import annotations

from .context import Context, State
from .types import (
    H_ASSISTANT_L2,
    H_CHEVAUCHEMENT,
    H_DOUBLE_POSTE,
    H_ELIGIBILITE,
    H_EXEMPTION,
    H_INACTIF,
    H_INCOMPATIBILITE,
    H_L2_NON_SENIOR,
    H_MAX_FERME,
    H_NON_RENSEIGNE,
    H_ORANGE_L1,
    H_REPOS,
    H_ROUGE,
    H_STATUT_POSTE,
    Color,
    Line,
    PersonIn,
    PostIn,
    Rejection,
    Status,
)


def hard_violation(ctx: Context, state: State, post: PostIn, person: PersonIn) -> Rejection | None:
    """Retourne la première contrainte ferme violée, ou ``None`` si la personne est admissible.

    L'ordre des contrôles est choisi pour produire le motif le plus explicite possible
    dans le rapport d'impossibilité.
    """

    def rej(code: str, detail: str) -> Rejection:
        return Rejection(
            profile_id=person.profile_id,
            profile_code=person.code,
            constraint_code=code,
            detail=detail,
        )

    # ---- H03 / H04 / H10 : statut, ligne et mode de couverture ------------- #
    if post.line is Line.L2 and person.status is Status.ASSISTANT:
        return rej(H_ASSISTANT_L2, "Un assistant n'est jamais affecté en deuxième ligne.")
    if post.line is Line.L2 and person.status is not Status.SENIOR:
        return rej(H_L2_NON_SENIOR, "La deuxième ligne est réservée aux seniors.")
    if post.required_status is not None and person.status is not post.required_status:
        return rej(
            H_STATUT_POSTE,
            f"Le poste exige le statut {post.required_status.value}, "
            f"la personne est {person.status.value}.",
        )

    # ---- H07b : éligibilité métier ---------------------------------------- #
    if post.line is Line.L1 and not person.eligible_l1:
        return rej(H_ELIGIBILITE, "Non éligible à la première ligne.")
    if post.line is Line.L2 and not person.eligible_l2:
        return rej(H_ELIGIBILITE, "Non éligible à la deuxième ligne.")
    if post.type_code in person.excluded_type_codes:
        return rej(H_ELIGIBILITE, f"Non éligible au type de garde « {post.type_code} ».")

    # ---- H07 : activité du compte ----------------------------------------- #
    if not person.is_active_on(post.local_date):
        return rej(
            H_INACTIF,
            f"Compte inactif ou hors période d'activité au {post.local_date.isoformat()}.",
        )
    if not person.is_active_on(post.end_at.date()):
        return rej(
            H_INACTIF,
            f"Période d'activité terminée avant la fin de la garde "
            f"({post.end_at.date().isoformat()}).",
        )

    # ---- H06b : incompatibilité déclarée ---------------------------------- #
    if (person.profile_id, post.occurrence_id) in ctx.incompatibilities:
        return rej(H_INCOMPATIBILITE, "Incompatibilité déclarée sur cette occurrence.")

    # ---- H02 : couleur ----------------------------------------------------- #
    color = ctx.color_for(person.profile_id, post.occurrence_id, post.line)
    if color is None:
        return rej(
            H_NON_RENSEIGNE,
            "Disponibilité non renseignée : la génération doit être débloquée en amont.",
        )
    if color is Color.ROUGE:
        return rej(
            H_ROUGE,
            "Indisponibilité rouge : seule la personne concernée peut la modifier. "
            "Aucune dérogation n'existe.",
        )
    # ---- H02c : orange -> deuxième ligne uniquement ------------------------ #
    if color is Color.ORANGE and post.line is Line.L1:
        return rej(
            H_ORANGE_L1,
            "Disponibilité orange : possible en deuxième ligne uniquement, "
            "jamais en première ligne.",
        )

    # ---- H08 : exemptions, quota nul, maximum ferme ------------------------ #
    exemption = ctx.total_exemption(
        person.profile_id, post.local_date, post.category_code, post.line
    )
    if exemption is not None:
        return rej(H_EXEMPTION, "Exemption totale en vigueur sur cette catégorie/ligne.")

    quota = ctx.quota_for(person.profile_id, post.category_code, post.line)
    if quota is not None:
        if quota.target <= 0.0 and (quota.maximum is None or quota.maximum <= 0.0):
            return rej(H_EXEMPTION, "Quota nul sur cette catégorie/ligne.")
        if quota.hard_maximum and quota.maximum is not None:
            current = state.person_load(person.profile_id, post.category_code, post.line)
            if current + post.count_weight > quota.maximum + 1e-9:
                return rej(
                    H_MAX_FERME,
                    f"Maximum ferme atteint ({current:.2f} + {post.count_weight:.2f} "
                    f"> {quota.maximum:.2f}).",
                )

    # ---- H11 : deux postes de la même occurrence --------------------------- #
    if (person.profile_id, post.occurrence_id) in state.occupied_occurrences:
        return rej(H_DOUBLE_POSTE, "Déjà affectée sur un autre poste de la même occurrence.")

    # ---- H06 : chevauchement ---------------------------------------------- #
    for start, end, label in state.intervals(person.profile_id):
        if post.start_at < end and start < post.end_at:
            return rej(
                H_CHEVAUCHEMENT,
                f"Chevauchement avec « {label} » "
                f"({start.isoformat()} → {end.isoformat()}).",
            )

    # ---- H09 : règles de repos fermes -------------------------------------- #
    for rule in ctx.hard_rest_rules:
        detail = _rest_violation(state, post, person.profile_id, rule)
        if detail is not None:
            return rej(H_REPOS, f"{rule.label} : {detail}")

    return None


def _rest_violation(state: State, post: PostIn, profile_id: int, rule) -> str | None:
    if rule.min_hours_between is not None:
        for start, end, label in state.intervals(profile_id):
            if post.start_at >= end:
                gap_h = (post.start_at - end).total_seconds() / 3600.0
            elif start >= post.end_at:
                gap_h = (start - post.end_at).total_seconds() / 3600.0
            else:
                gap_h = 0.0
            if gap_h < rule.min_hours_between - 1e-9:
                return (
                    f"repos de {gap_h:.1f} h avec « {label} », "
                    f"minimum exigé {rule.min_hours_between:.1f} h"
                )
    if rule.max_count_in_days is not None:
        window_days, max_count = rule.max_count_in_days
        current = state.count_in_window(profile_id, post.local_date, window_days)
        if current + 1 > max_count:
            return (
                f"{current + 1} gardes sur une fenêtre de {window_days} jours, "
                f"maximum {max_count}"
            )
    if rule.max_consecutive_weekends is not None and post.is_weekend_block:
        trial = state.clone()
        trial.assign(post, profile_id)
        run = trial.max_consecutive_weekends(profile_id)
        if run > rule.max_consecutive_weekends:
            return f"{run} week-ends consécutifs, maximum {rule.max_consecutive_weekends}"
    return None


def feasible_candidates(
    ctx: Context, state: State, post: PostIn
) -> tuple[list[PersonIn], list[Rejection]]:
    """Sépare candidats admissibles et personnes écartées, avec leur motif."""
    ok: list[PersonIn] = []
    rejected: list[Rejection] = []
    for profile_id in sorted(ctx.people):
        person = ctx.people[profile_id]
        violation = hard_violation(ctx, state, post, person)
        if violation is None:
            ok.append(person)
        else:
            rejected.append(violation)
    return ok, rejected
