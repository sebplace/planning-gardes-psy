"""Solveur déterministe.

Écart assumé au brief : `ortools` / CP-SAT n'est pas installable sur la plateforme
cible (Windows ARM64). Le moteur retenu est un **glouton contraint + recherche locale**,
entièrement déterministe à graine fixée (cf. DECISIONS.md D-003).

Limites honnêtes de cette approche, à ne pas masquer :
  * il ne prouve pas l'optimalité de la solution ;
  * il ne prouve pas l'infaisabilité globale du problème ; il rapporte une
    **impossibilité constatée**, poste par poste, avec les motifs d'exclusion.
Aucune contrainte ferme n'est jamais relâchée automatiquement.
"""

from __future__ import annotations

import random
from typing import Protocol

from .context import Context, State
from .hard import feasible_candidates, hard_violation
from .scoring import (
    CRITERIA,
    marginal_cost,
    person_terms,
    quota_gaps,
    tensions,
    total_score,
)
from .types import (
    H_CHEVAUCHEMENT,
    H_DOUBLE_POSTE,
    H_MAX_FERME,
    H_REPOS,
    Color,
    EngineInput,
    Explanation,
    ImpossibilityReport,
    Line,
    PersonIn,
    PostIn,
    Rejection,
    Solution,
    UnfilledPost,
    diversity_distance,
)

ENGINE_VERSION = "0.1.0"

# Codes d'exclusion dépendant de l'état : seuls ceux-là justifient une tentative de réparation.
REPAIRABLE_CODES = frozenset({H_CHEVAUCHEMENT, H_DOUBLE_POSTE, H_REPOS, H_MAX_FERME})


class SolverBackend(Protocol):
    """Interface d'isolation du solveur : un `CpSatBackend` peut être branché ici."""

    def solve(
        self, inp: EngineInput, variants: int = 1, min_diversity: float = 0.15
    ) -> list[Solution]: ...


class GreedyLocalSearchBackend:
    """Glouton contraint + recherche locale bornée, déterministe."""

    name = "glouton_recherche_locale"

    def __init__(self, local_search_passes: int = 3, swap_window: int = 30) -> None:
        self.local_search_passes = local_search_passes
        self.swap_window = swap_window

    # ------------------------------------------------------------------ #
    # Point d'entrée
    # ------------------------------------------------------------------ #

    def solve(
        self, inp: EngineInput, variants: int = 1, min_diversity: float = 0.15
    ) -> list[Solution]:
        ctx = Context(inp)
        snapshot = inp.snapshot_hash()
        kept: list[Solution] = []
        attempt = 0
        max_attempts = max(variants * 4, variants + 3)

        while len(kept) < variants and attempt < max_attempts:
            seed = inp.seed + attempt * 7919
            perturbation = 0.0 if attempt == 0 else 0.02 + 0.01 * attempt
            state = self._build(ctx, seed, perturbation)
            solution = self._finalise(ctx, state, len(kept), seed, snapshot)

            if not kept:
                kept.append(solution)
            else:
                distances = [
                    diversity_distance(solution, other, ctx.ordered_post_ids) for other in kept
                ]
                if min(distances) >= min_diversity:
                    solution.variant_index = len(kept)
                    kept.append(solution)
            attempt += 1

        return kept

    # ------------------------------------------------------------------ #
    # Construction gloutonne
    # ------------------------------------------------------------------ #

    def _build(self, ctx: Context, seed: int, perturbation: float) -> State:
        state = State(ctx)
        state._unfilled = []  # type: ignore[attr-defined]
        state._lock_errors = []  # type: ignore[attr-defined]

        # 1. Affectations verrouillées : respectées, mais jamais au mépris d'une
        #    contrainte ferme. Un verrou invalide est signalé, pas appliqué.
        for post_id in sorted(ctx.locked):
            profile_id = ctx.locked[post_id]
            post = ctx.posts.get(post_id)
            person = ctx.people.get(profile_id)
            if post is None or person is None:
                continue
            violation = hard_violation(ctx, state, post, person)
            if violation is None:
                state.assign(post, profile_id)
            else:
                state._lock_errors.append((post, violation))  # type: ignore[attr-defined]

        # 2. Ordonnancement déterministe : postes les plus contraints d'abord.
        empty = State(ctx)
        scarcity: dict[int, int] = {}
        for post in ctx.ordered_posts:
            if post.post_id in state.assignments:
                continue
            candidates, _ = feasible_candidates(ctx, empty, post)
            scarcity[post.post_id] = len(candidates)
        remaining = sorted(
            (p for p in ctx.ordered_posts if p.post_id not in state.assignments),
            key=lambda p: (scarcity[p.post_id], p.key),
        )

        # 3. Affectation gloutonne.
        for post in remaining:
            if post.post_id in state.assignments:
                continue
            candidates, rejections = feasible_candidates(ctx, state, post)
            if not candidates:
                if self._repair(ctx, state, post, rejections, seed):
                    continue
                state._unfilled.append(  # type: ignore[attr-defined]
                    UnfilledPost(
                        post_id=post.post_id,
                        occurrence_id=post.occurrence_id,
                        line=post.line.value,
                        local_date=post.local_date,
                        type_code=post.type_code,
                        rejections=rejections,
                    )
                )
                continue
            chosen = self._choose(ctx, state, post, candidates, seed, perturbation)
            state.assign(post, chosen.profile_id)

        # 4. Recherche locale bornée.
        self._local_search(ctx, state)
        return state

    # ------------------------------------------------------------------ #

    def _choose(
        self,
        ctx: Context,
        state: State,
        post: PostIn,
        candidates: list[PersonIn],
        seed: int,
        perturbation: float,
    ) -> PersonIn:
        """Départage §11.3 : coût souple, retard au quota, espacement, puis
        pseudo-aléatoire reproductible."""
        scored: list[tuple[tuple, PersonIn]] = []
        for person in candidates:
            cost, _ = marginal_cost(ctx, state, post, person)
            noise = _stable_random(seed, post.post_id, person.profile_id)
            if perturbation:
                cost = cost * (1.0 + perturbation * (noise - 0.5) * 2.0)

            target = ctx.effective_target(
                person.profile_id, post.local_date, post.category_code, post.line
            )
            expected = target * ctx.input.year_fraction_elapsed
            load = state.person_load(person.profile_id, post.category_code, post.line)
            lag = expected - load  # positif = en retard sur sa cible

            state.assign(post, person.profile_id)
            gap = state.min_gap_days(person.profile_id, post)
            state.unassign(post)
            gap_value = 9999.0 if gap is None else gap

            scored.append(
                ((round(cost, 6), -round(lag, 6), -round(gap_value, 4), noise, person.profile_id), person)
            )
        scored.sort(key=lambda item: item[0])
        return scored[0][1]

    # ------------------------------------------------------------------ #

    def _repair(
        self,
        ctx: Context,
        state: State,
        post: PostIn,
        rejections: list[Rejection],
        seed: int,
    ) -> bool:
        """Retour arrière borné (profondeur 1).

        Ne tente une réparation que pour les personnes écartées par une contrainte
        **dépendante de l'état** (chevauchement, double poste, repos, maximum ferme).
        Une personne rouge, non éligible ou exemptée n'est jamais « réparée » :
        sa contrainte est ferme et le reste.
        """
        blocked = [r for r in rejections if r.constraint_code in REPAIRABLE_CODES]
        blocked.sort(key=lambda r: (r.constraint_code, r.profile_id))

        for rejection in blocked:
            person = ctx.people[rejection.profile_id]
            conflicting = sorted(
                state.by_person.get(person.profile_id, []), key=lambda p: p.key
            )
            for victim in conflicting:
                if victim.post_id in ctx.locked:
                    continue
                state.unassign(victim)
                if hard_violation(ctx, state, post, person) is not None:
                    state.assign(victim, person.profile_id)
                    continue
                state.assign(post, person.profile_id)
                replacements, _ = feasible_candidates(ctx, state, victim)
                replacements = [c for c in replacements if c.profile_id != person.profile_id]
                if replacements:
                    substitute = self._choose(ctx, state, victim, replacements, seed, 0.0)
                    state.assign(victim, substitute.profile_id)
                    return True
                state.unassign(post)
                state.assign(victim, person.profile_id)
        return False

    # ------------------------------------------------------------------ #

    def _local_search(self, ctx: Context, state: State) -> None:
        """Améliorations déterministes bornées : déplacements puis échanges deux à deux."""
        for _ in range(self.local_search_passes):
            improved = False
            improved |= self._moves_pass(ctx, state)
            improved |= self._swaps_pass(ctx, state)
            if not improved:
                break

    def _painful_term(self, ctx: Context, painful: dict[int, float]) -> float:
        concerned = [
            v
            for pid, v in painful.items()
            if v > 0 or any(q.profile_id == pid and q.target > 0 for q in ctx.input.quotas)
        ]
        if not concerned:
            return 0.0
        mean = sum(concerned) / len(concerned)
        return ctx.profile.w_painful * sum(abs(v - mean) for v in concerned)

    def _painful_vector(self, ctx: Context, state: State) -> dict[int, float]:
        return {
            pid: sum(p.painful_weight * p.count_weight for p in state.by_person.get(pid, []))
            for pid in ctx.people
        }

    @staticmethod
    def _core(terms: dict[str, float]) -> float:
        return sum(terms.get(key, 0.0) for key in CRITERIA if key in terms)

    def _moves_pass(self, ctx: Context, state: State) -> bool:
        improved = False
        for post in ctx.ordered_posts:
            holder = state.assignments.get(post.post_id)
            if holder is None or post.post_id in ctx.locked:
                continue
            state.unassign(post)
            candidates, _ = feasible_candidates(ctx, state, post)
            state.assign(post, holder)
            best_gain = -1e-9
            best_pid: int | None = None
            for person in candidates:
                if person.profile_id == holder:
                    continue
                gain = self._move_gain(ctx, state, post, holder, person.profile_id)
                if gain < best_gain - 1e-9:
                    best_gain = gain
                    best_pid = person.profile_id
            if best_pid is not None:
                state.unassign(post)
                state.assign(post, best_pid)
                improved = True
        return improved

    def _move_gain(
        self, ctx: Context, state: State, post: PostIn, holder: int, new_pid: int
    ) -> float:
        painful_before = self._painful_vector(ctx, state)
        before = self._core(person_terms(ctx, state, holder)) + self._core(
            person_terms(ctx, state, new_pid)
        ) + self._painful_term(ctx, painful_before)

        state.unassign(post)
        state.assign(post, new_pid)
        painful_after = self._painful_vector(ctx, state)
        after = self._core(person_terms(ctx, state, holder)) + self._core(
            person_terms(ctx, state, new_pid)
        ) + self._painful_term(ctx, painful_after)
        state.unassign(post)
        state.assign(post, holder)
        return after - before

    def _swaps_pass(self, ctx: Context, state: State) -> bool:
        improved = False
        posts = ctx.ordered_posts
        for i, post_a in enumerate(posts):
            pid_a = state.assignments.get(post_a.post_id)
            if pid_a is None or post_a.post_id in ctx.locked:
                continue
            for post_b in posts[i + 1 : i + 1 + self.swap_window]:
                pid_b = state.assignments.get(post_b.post_id)
                if pid_b is None or pid_b == pid_a or post_b.post_id in ctx.locked:
                    continue
                if post_b.line is not post_a.line:
                    continue
                if not self._swap_is_feasible(ctx, state, post_a, pid_a, post_b, pid_b):
                    continue
                gain = self._swap_gain(ctx, state, post_a, pid_a, post_b, pid_b)
                if gain < -1e-9:
                    state.unassign(post_a)
                    state.unassign(post_b)
                    state.assign(post_a, pid_b)
                    state.assign(post_b, pid_a)
                    improved = True
                    break
        return improved

    def _swap_is_feasible(
        self, ctx: Context, state: State, post_a: PostIn, pid_a: int, post_b: PostIn, pid_b: int
    ) -> bool:
        state.unassign(post_a)
        state.unassign(post_b)
        ok = (
            hard_violation(ctx, state, post_a, ctx.people[pid_b]) is None
            and hard_violation(ctx, state, post_b, ctx.people[pid_a]) is None
        )
        if ok:
            state.assign(post_a, pid_b)
            ok = hard_violation(ctx, state, post_b, ctx.people[pid_a]) is None
            state.unassign(post_a)
        state.assign(post_a, pid_a)
        state.assign(post_b, pid_b)
        return ok

    def _swap_gain(
        self, ctx: Context, state: State, post_a: PostIn, pid_a: int, post_b: PostIn, pid_b: int
    ) -> float:
        painful_before = self._painful_vector(ctx, state)
        before = self._core(person_terms(ctx, state, pid_a)) + self._core(
            person_terms(ctx, state, pid_b)
        ) + self._painful_term(ctx, painful_before)

        state.unassign(post_a)
        state.unassign(post_b)
        state.assign(post_a, pid_b)
        state.assign(post_b, pid_a)
        painful_after = self._painful_vector(ctx, state)
        after = self._core(person_terms(ctx, state, pid_a)) + self._core(
            person_terms(ctx, state, pid_b)
        ) + self._painful_term(ctx, painful_after)

        state.unassign(post_a)
        state.unassign(post_b)
        state.assign(post_a, pid_a)
        state.assign(post_b, pid_b)
        return after - before

    # ------------------------------------------------------------------ #
    # Finalisation
    # ------------------------------------------------------------------ #

    def _finalise(
        self, ctx: Context, state: State, variant_index: int, seed: int, snapshot: str
    ) -> Solution:
        score, breakdown = total_score(ctx, state)
        explanations: dict[int, Explanation] = {}
        orange_used: list[int] = []
        default_used: list[int] = []

        for post in ctx.ordered_posts:
            profile_id = state.assignments.get(post.post_id)
            if profile_id is None:
                continue
            person = ctx.people[profile_id]
            color = ctx.color_for(profile_id, post.occurrence_id, post.line)
            if color is Color.ORANGE:
                orange_used.append(post.post_id)
            if color is Color.DISPO_DEFAUT:
                default_used.append(post.post_id)

            state.unassign(post)
            cost, criteria = marginal_cost(ctx, state, post, person)
            target = ctx.effective_target(
                profile_id, post.local_date, post.category_code, post.line
            )
            expected = target * ctx.input.year_fraction_elapsed
            load_before = state.person_load(profile_id, post.category_code, post.line)
            _, rejections = feasible_candidates(ctx, state, post)
            state.assign(post, profile_id)
            gap = state.min_gap_days(profile_id, post)

            explanations[post.post_id] = Explanation(
                post_id=post.post_id,
                profile_id=profile_id,
                profile_code=person.code,
                status=person.status.value,
                line=post.line.value,
                color=(color.value if color else "NON_RENSEIGNE"),
                color_is_declared=bool(color and color.is_declared),
                quota_target=round(expected, 3),
                quota_before=round(load_before, 3),
                quota_lag=round(expected - load_before, 3),
                spacing_days=None if gap is None else round(gap, 2),
                criteria=criteria,
                rejected_candidates=rejections,
                notes=[f"coût souple marginal : {cost:.2f}"],
            )

        unfilled: list[UnfilledPost] = list(getattr(state, "_unfilled", []))
        lock_errors = list(getattr(state, "_lock_errors", []))
        extra_tensions = [
            f"Affectation verrouillée refusée sur le poste {post.post_id} "
            f"({rejection.profile_code}) : {rejection.label}"
            for post, rejection in lock_errors
        ]
        for post, rejection in lock_errors:
            unfilled.append(
                UnfilledPost(
                    post_id=post.post_id,
                    occurrence_id=post.occurrence_id,
                    line=post.line.value,
                    local_date=post.local_date,
                    type_code=post.type_code,
                    rejections=[rejection],
                )
            )

        # Postes restés sans affectation et sans motif enregistré (sécurité).
        known = {u.post_id for u in unfilled}
        for post in ctx.ordered_posts:
            if post.post_id not in state.assignments and post.post_id not in known:
                _, rejections = feasible_candidates(ctx, state, post)
                unfilled.append(
                    UnfilledPost(
                        post_id=post.post_id,
                        occurrence_id=post.occurrence_id,
                        line=post.line.value,
                        local_date=post.local_date,
                        type_code=post.type_code,
                        rejections=rejections,
                    )
                )

        unfilled.sort(key=lambda u: (u.local_date, u.line, u.post_id))

        return Solution(
            variant_index=variant_index,
            seed=seed,
            assignments=dict(state.assignments),
            explanations=explanations,
            unfilled=unfilled,
            score_total=round(score, 4),
            score_breakdown={k: round(v, 4) for k, v in breakdown.items()},
            orange_used=sorted(orange_used),
            default_availability_used=sorted(default_used),
            quota_gaps=quota_gaps(ctx, state),
            tensions=tensions(ctx, state) + extra_tensions,
            input_snapshot_hash=snapshot,
            ruleset_version=ctx.input.ruleset_version,
            engine_version=ENGINE_VERSION,
            profile_name=f"{ctx.profile.name}@{ctx.profile.version}",
        )


def _stable_random(seed: int, post_id: int, profile_id: int) -> float:
    """Pseudo-aléatoire reproductible, dépendant uniquement de la graine et des clés."""
    return random.Random(f"{seed}:{post_id}:{profile_id}").random()


def impossibility_report(solution: Solution) -> ImpossibilityReport:
    summary: list[str] = []
    if solution.unfilled:
        summary.append(
            f"{len(solution.unfilled)} poste(s) n'ont pas pu être pourvus sans violer "
            "une contrainte ferme. Aucune contrainte ferme n'a été relâchée."
        )
        by_code: dict[str, int] = {}
        for post in solution.unfilled:
            for rejection in post.rejections:
                by_code[rejection.label] = by_code.get(rejection.label, 0) + 1
        for label, count in sorted(by_code.items(), key=lambda kv: (-kv[1], kv[0])):
            summary.append(f"{count} exclusion(s) : {label}")
    return ImpossibilityReport(unfilled=solution.unfilled, summary=summary)


def solve(
    inp: EngineInput, variants: int = 1, min_diversity: float = 0.15
) -> list[Solution]:
    """Point d'entrée par défaut du moteur."""
    return GreedyLocalSearchBackend().solve(inp, variants=variants, min_diversity=min_diversity)
