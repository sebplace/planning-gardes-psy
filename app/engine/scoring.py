"""Critères souples.

Les critères souples ne servent **qu'à départager des solutions déjà réalisables**.
Leurs poids proviennent d'un profil de règles versionné, jamais de constantes enfouies.
"""

from __future__ import annotations

from collections import defaultdict

from .context import Context, State
from .types import Color, Line, PersonIn, PostIn, Status

CRITERIA = ("S01_orange", "S02_quota", "S03_rattrapage", "S05_espacement", "S06_concentration", "S07_penibilite")


def soft_multiplier(ctx: Context, person: PersonIn) -> float:
    """S04 — priorité des préférences souples des seniors (DECISIONS.md M-006).

    Ne s'applique qu'aux critères de confort. Jamais aux rouges, à la sécurité,
    au repos, aux éligibilités ni aux plafonds fermes.
    """
    if person.status is Status.SENIOR:
        return ctx.profile.senior_soft_priority_multiplier
    return 1.0


def person_terms(ctx: Context, state: State, profile_id: int) -> dict[str, float]:
    """Termes de coût attribuables à une personne dans l'état courant."""
    p = ctx.profile
    person = ctx.people[profile_id]
    mult = soft_multiplier(ctx, person)
    posts = state.by_person.get(profile_id, [])

    # --- S01 : privilégier le vert, l'orange n'est utilisé que si nécessaire ---
    # DISPO_DEFAUT est scorée exactement comme un vert : la non-réponse ne pénalise
    # ni n'avantage la personne (DECISIONS.md M-008).
    orange = 0.0
    for post in posts:
        color = ctx.color_for(profile_id, post.occurrence_id, post.line)
        if color is Color.ORANGE:
            orange += p.w_orange * mult

    # --- S02 / S03 : progression vers les quotas et prévention des rattrapages ---
    quota_dev = 0.0
    catchup = 0.0
    keys = {(post.category_code, post.line) for post in posts}
    keys |= {
        (cat, Line(line))
        for (pid, cat, line) in ctx.prior_load
        if pid == profile_id
    }
    keys |= {
        (q.category_code, q.line)
        for q in ctx.input.quotas
        if q.profile_id == profile_id and q.target > 0
    }
    for category, line in sorted(keys, key=lambda k: (k[0], k[1].value)):
        target = ctx.effective_target(profile_id, _ref_day(posts, category, line), category, line)
        expected = target * ctx.input.year_fraction_elapsed
        load = state.person_load(profile_id, category, line)
        quota_dev += p.w_quota * abs(load - expected)
        catchup += p.w_catchup * max(0.0, expected - load)

    # --- S05 : maximiser l'espacement ---
    spacing = 0.0
    target_spacing = max(p.target_spacing_days, 0.001)
    intervals = state.intervals(profile_id)
    for previous, current in zip(intervals, intervals[1:]):
        gap_days = (current[0] - previous[1]).total_seconds() / 86400.0
        if gap_days < target_spacing:
            spacing += p.w_spacing * mult * (target_spacing - max(gap_days, 0.0)) / target_spacing

    # --- S06 : limiter gardes rapprochées, nuits concentrées, week-ends successifs ---
    concentration = 0.0
    for post in posts:
        count = state.count_in_window(profile_id, post.local_date, p.concentration_window_days)
        excess = max(0, count - p.concentration_threshold)
        concentration += p.w_concentration * mult * excess
    weekend_run = state.max_consecutive_weekends(profile_id)
    if weekend_run > p.max_consecutive_weekends_soft:
        concentration += p.w_concentration * mult * (weekend_run - p.max_consecutive_weekends_soft)

    painful_raw = sum(post.painful_weight * post.count_weight for post in posts)

    return {
        "S01_orange": orange,
        "S02_quota": quota_dev,
        "S03_rattrapage": catchup,
        "S05_espacement": spacing,
        "S06_concentration": concentration,
        "_penibilite_brute": painful_raw,
    }


def _ref_day(posts: list[PostIn], category: str, line: Line):
    for post in posts:
        if post.category_code == category and post.line is line:
            return post.local_date
    if posts:
        return posts[0].local_date
    from datetime import date

    return date.today()


def total_score(ctx: Context, state: State) -> tuple[float, dict[str, float]]:
    """Score global (plus bas = meilleur) et ventilation par critère."""
    breakdown: dict[str, float] = {key: 0.0 for key in CRITERIA}
    painful: dict[int, float] = {}
    for profile_id in sorted(ctx.people):
        terms = person_terms(ctx, state, profile_id)
        for key in CRITERIA:
            if key in terms:
                breakdown[key] += terms[key]
        painful[profile_id] = terms["_penibilite_brute"]

    # --- S07 : équilibrer les catégories pénibles (dispersion autour de la moyenne) ---
    concerned = [v for pid, v in painful.items() if v > 0 or _has_target(ctx, pid)]
    if concerned:
        mean = sum(concerned) / len(concerned)
        breakdown["S07_penibilite"] = ctx.profile.w_painful * sum(
            abs(v - mean) for v in concerned
        )
    total = sum(breakdown.values())
    return total, breakdown


def _has_target(ctx: Context, profile_id: int) -> bool:
    return any(q.profile_id == profile_id and q.target > 0 for q in ctx.input.quotas)


def marginal_cost(
    ctx: Context, state: State, post: PostIn, person: PersonIn
) -> tuple[float, dict[str, float]]:
    """Coût marginal exact d'une affectation, calculé comme delta des termes de la personne."""
    before = person_terms(ctx, state, person.profile_id)
    state.assign(post, person.profile_id)
    after = person_terms(ctx, state, person.profile_id)
    state.unassign(post)

    criteria = {
        key: round(after.get(key, 0.0) - before.get(key, 0.0), 6)
        for key in CRITERIA
        if key in after
    }
    criteria["S07_penibilite"] = round(
        ctx.profile.w_painful
        * (after["_penibilite_brute"] - before["_penibilite_brute"]),
        6,
    )
    return sum(criteria.values()), criteria


def quota_gaps(ctx: Context, state: State) -> dict[str, float]:
    """Écart final aux quotas, clé « profil:catégorie:ligne »."""
    gaps: dict[str, float] = {}
    seen: set[tuple[int, str, str]] = set()
    for q in ctx.input.quotas:
        seen.add((q.profile_id, q.category_code, q.line.value))
    for (pid, cat, line), _ in list(state.load.items()):
        seen.add((pid, cat, line))
    for pid, cat, line in sorted(seen):
        target = ctx.effective_target(pid, _today(), cat, Line(line))
        expected = target * ctx.input.year_fraction_elapsed
        load = state.person_load(pid, cat, Line(line))
        gaps[f"{pid}:{cat}:{line}"] = round(load - expected, 4)
    return gaps


def _today():
    from datetime import date

    return date.today()


def tensions(ctx: Context, state: State) -> list[str]:
    """Signaux de tension lisibles, sans comparaison nominative publique."""
    out: list[str] = []
    by_category: dict[str, int] = defaultdict(int)
    for post in ctx.ordered_posts:
        if post.post_id not in state.assignments:
            by_category[post.category_code] += 1
    for category, count in sorted(by_category.items()):
        out.append(f"{count} poste(s) non pourvu(s) dans la catégorie {category}")

    orange_count = 0
    for post_id, profile_id in state.assignments.items():
        post = ctx.posts[post_id]
        if ctx.color_for(profile_id, post.occurrence_id, post.line) is Color.ORANGE:
            orange_count += 1
    if orange_count:
        out.append(f"{orange_count} affectation(s) sur une disponibilité orange")

    for profile_id in sorted(ctx.people):
        run = state.max_consecutive_weekends(profile_id)
        if run > ctx.profile.max_consecutive_weekends_soft:
            out.append(
                f"{ctx.people[profile_id].code} : {run} week-ends consécutifs "
                f"(seuil souple {ctx.profile.max_consecutive_weekends_soft})"
            )
    return out
