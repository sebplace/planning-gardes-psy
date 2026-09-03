"""Contexte indexé et état mutable du solveur. Paquet pur (aucun accès base)."""

from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, timedelta

from .types import (
    AvailabilityIn,
    BusyIntervalIn,
    Color,
    ContinuousDutyRuleIn,
    EngineInput,
    Enforcement,
    ExemptionIn,
    Line,
    MonthlyCapIn,
    PersonIn,
    PostIn,
    QuotaIn,
    RestRuleIn,
    Status,
)


class Context:
    """Indexation en lecture seule des entrées du moteur."""

    def __init__(self, inp: EngineInput) -> None:
        self.input = inp
        self.profile = inp.profile

        self.people: dict[int, PersonIn] = {p.profile_id: p for p in inp.people}
        self.posts: dict[int, PostIn] = {p.post_id: p for p in inp.posts}
        self.ordered_posts: list[PostIn] = sorted(inp.posts, key=lambda p: p.key)
        self.ordered_post_ids: list[int] = [p.post_id for p in self.ordered_posts]

        # Disponibilités : (profil, occurrence, ligne|None) -> couleur
        self._avail: dict[tuple[int, int, str | None], Color] = {}
        for a in inp.availabilities:
            self._avail[(a.profile_id, a.occurrence_id, a.line.value if a.line else None)] = a.color

        self._quotas: dict[tuple[int, str, str], QuotaIn] = {
            (q.profile_id, q.category_code, q.line.value): q for q in inp.quotas
        }
        self._exemptions: dict[int, list[ExemptionIn]] = defaultdict(list)
        for e in inp.exemptions:
            self._exemptions[e.profile_id].append(e)

        self.hard_rest_rules: list[RestRuleIn] = [
            r for r in inp.rest_rules if r.enforcement is Enforcement.FERME
        ]
        self.soft_rest_rules: list[RestRuleIn] = [
            r for r in inp.rest_rules if r.enforcement is Enforcement.SOUPLE
        ]

        self._busy: dict[int, list[BusyIntervalIn]] = defaultdict(list)
        for b in inp.busy_intervals:
            self._busy[b.profile_id].append(b)

        # Plafonds mensuels : seuls ceux qui franchissent les trois verrous sont
        # opposables ; les autres restent purement informatifs.
        self.monthly_caps: list[MonthlyCapIn] = list(inp.monthly_caps)
        self.enforceable_monthly_caps: list[MonthlyCapIn] = [
            c for c in self.monthly_caps if c.is_enforceable
        ]
        self.continuous_duty = inp.continuous_duty

        self.incompatibilities = set(inp.incompatibilities)
        self.locked = dict(inp.locked)
        self.prior_load = dict(inp.prior_load)

        # Postes groupés par occurrence, utile pour le mode A/B et les doubles postes.
        self.posts_by_occurrence: dict[int, list[PostIn]] = defaultdict(list)
        for p in inp.posts:
            self.posts_by_occurrence[p.occurrence_id].append(p)

    # ---------------------------------------------------------------- #

    def color_for(self, profile_id: int, occurrence_id: int, line: Line) -> Color | None:
        """Couleur applicable. ``None`` = non renseigné (distinct de tout vert)."""
        specific = self._avail.get((profile_id, occurrence_id, line.value))
        if specific is not None:
            return specific
        return self._avail.get((profile_id, occurrence_id, None))

    def quota_for(self, profile_id: int, category: str, line: Line) -> QuotaIn | None:
        return self._quotas.get((profile_id, category, line.value))

    def total_exemption(self, profile_id: int, day: date, category: str, line: Line) -> ExemptionIn | None:
        for e in self._exemptions.get(profile_id, ()):
            if e.total and e.applies(day, category, line):
                return e
        return None

    def reduction_ratio(self, profile_id: int, day: date, category: str, line: Line) -> float:
        ratio = 0.0
        for e in self._exemptions.get(profile_id, ()):
            if not e.total and e.applies(day, category, line):
                ratio = max(ratio, e.reduction_ratio)
        return min(ratio, 1.0)

    def effective_target(self, profile_id: int, day: date, category: str, line: Line) -> float:
        q = self.quota_for(profile_id, category, line)
        if q is None:
            return 0.0
        return q.target * (1.0 - self.reduction_ratio(profile_id, day, category, line))

    def busy_intervals(self, profile_id: int) -> list[tuple[datetime, datetime, str]]:
        return [(b.start_at, b.end_at, b.label) for b in self._busy.get(profile_id, ())]

    def monthly_caps_for(self, person: PersonIn) -> list[MonthlyCapIn]:
        """Plafonds mensuels **opposables** applicables à une personne."""
        return [c for c in self.enforceable_monthly_caps if c.applies_to(person)]

    def eligible_people_for(self, post: PostIn) -> list[PersonIn]:
        """Candidats potentiels, hors contraintes dépendant de l'état courant."""
        return [self.people[pid] for pid in sorted(self.people)]


class State:
    """État mutable d'une construction de solution."""

    def __init__(self, ctx: Context) -> None:
        self.ctx = ctx
        self.assignments: dict[int, int] = {}
        self.by_person: dict[int, list[PostIn]] = defaultdict(list)
        self.load: dict[tuple[int, str, str], float] = defaultdict(float)
        self.occupied_occurrences: dict[tuple[int, int], int] = {}

    # ---------------------------------------------------------------- #

    def clone(self) -> "State":
        s = State(self.ctx)
        s.assignments = dict(self.assignments)
        s.by_person = {k: list(v) for k, v in self.by_person.items()}
        s.load = defaultdict(float, self.load)
        s.occupied_occurrences = dict(self.occupied_occurrences)
        return s

    def assign(self, post: PostIn, profile_id: int) -> None:
        self.assignments[post.post_id] = profile_id
        posts = self.by_person[profile_id]
        posts.append(post)
        posts.sort(key=lambda p: p.start_at)
        self.load[(profile_id, post.category_code, post.line.value)] += post.count_weight
        self.occupied_occurrences[(profile_id, post.occurrence_id)] = post.post_id

    def unassign(self, post: PostIn) -> int | None:
        profile_id = self.assignments.pop(post.post_id, None)
        if profile_id is None:
            return None
        self.by_person[profile_id] = [
            p for p in self.by_person[profile_id] if p.post_id != post.post_id
        ]
        self.load[(profile_id, post.category_code, post.line.value)] -= post.count_weight
        self.occupied_occurrences.pop((profile_id, post.occurrence_id), None)
        return profile_id

    # ---------------------------------------------------------------- #

    def person_load(self, profile_id: int, category: str, line: Line) -> float:
        prior = self.ctx.prior_load.get((profile_id, category, line.value), 0.0)
        return prior + self.load[(profile_id, category, line.value)]

    def intervals(self, profile_id: int) -> list[tuple[datetime, datetime, str]]:
        out = [
            (p.start_at, p.end_at, f"{p.type_code} {p.line.value}")
            for p in self.by_person.get(profile_id, ())
        ]
        out.extend(self.ctx.busy_intervals(profile_id))
        out.sort()
        return out

    def min_gap_days(self, profile_id: int, post: PostIn) -> float | None:
        """Écart minimal en jours entre ce poste et les autres gardes de la personne."""
        best: float | None = None
        for start, end, _ in self.intervals(profile_id):
            if start == post.start_at and end == post.end_at:
                continue
            if post.start_at >= end:
                gap = (post.start_at - end).total_seconds() / 86400.0
            elif start >= post.end_at:
                gap = (start - post.end_at).total_seconds() / 86400.0
            else:
                gap = 0.0
            best = gap if best is None else min(best, gap)
        return best

    def count_in_window(self, profile_id: int, day: date, window_days: int) -> int:
        delta = timedelta(days=window_days)
        return sum(
            1
            for p in self.by_person.get(profile_id, ())
            if abs(p.local_date - day) < delta
        )

    def count_in_month(self, profile_id: int, day: date) -> float:
        """Charge déjà posée sur le mois calendaire de ``day``, en poids de décompte."""
        return sum(
            p.count_weight
            for p in self.by_person.get(profile_id, ())
            if p.local_date.year == day.year and p.local_date.month == day.month
        )

    def continuous_chain(
        self, profile_id: int, post: PostIn
    ) -> tuple[float, set[date]]:
        """Durée du bloc de service continu incluant ``post``, et jours concernés.

        Deux gardes sont dites contiguës lorsque la seconde commence exactement à
        la fin de la première, ou plus tôt. Aucune tolérance n'est introduite :
        une coupure, même d'une heure, brise la continuité.
        """
        segments = [(post.start_at, post.end_at, post.local_date)]
        for p in self.by_person.get(profile_id, ()):
            if p.post_id == post.post_id:
                continue
            segments.append((p.start_at, p.end_at, p.local_date))
        for start, end, _ in self.ctx.busy_intervals(profile_id):
            segments.append((start, end, start.date()))
        segments.sort()

        best_hours = 0.0
        best_days: set[date] = set()
        courant_debut = courant_fin = None
        courant_jours: set[date] = set()
        contient_post = False

        for start, end, day in segments:
            if courant_fin is None or start > courant_fin:
                if contient_post and courant_debut is not None:
                    heures = (courant_fin - courant_debut).total_seconds() / 3600.0
                    if heures > best_hours:
                        best_hours, best_days = heures, set(courant_jours)
                courant_debut, courant_fin = start, end
                courant_jours = {day}
                contient_post = start == post.start_at and end == post.end_at
            else:
                courant_fin = max(courant_fin, end)
                courant_jours.add(day)
                if start == post.start_at and end == post.end_at:
                    contient_post = True

        if contient_post and courant_debut is not None:
            heures = (courant_fin - courant_debut).total_seconds() / 3600.0
            if heures > best_hours:
                best_hours, best_days = heures, set(courant_jours)
        return best_hours, best_days

    def weekend_weeks(self, profile_id: int) -> list[tuple[int, int]]:
        weeks = {
            p.local_date.isocalendar()[:2]
            for p in self.by_person.get(profile_id, ())
            if p.is_weekend_block
        }
        return sorted(weeks)

    def max_consecutive_weekends(self, profile_id: int) -> int:
        weeks = self.weekend_weeks(profile_id)
        if not weeks:
            return 0
        best = run = 1
        for prev, cur in zip(weeks, weeks[1:]):
            consecutive = (cur[0] == prev[0] and cur[1] == prev[1] + 1) or (
                cur[0] == prev[0] + 1 and prev[1] >= 52 and cur[1] == 1
            )
            run = run + 1 if consecutive else 1
            best = max(best, run)
        return best
