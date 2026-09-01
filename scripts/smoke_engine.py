"""Vérification rapide du moteur pur (hors pytest)."""

from __future__ import annotations

import sys
from datetime import date, datetime, time, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.engine import (  # noqa: E402
    AvailabilityIn,
    Color,
    CoverageMode,
    EngineInput,
    Enforcement,
    Line,
    PersonIn,
    PostIn,
    QuotaIn,
    RestRuleIn,
    RuleProfile,
    Status,
    impossibility_report,
    solve,
)
from app.engine.projection import (  # noqa: E402
    AssistantGroup,
    CategoryVolume,
    ScenarioParams,
    SeniorGroup,
    build_synthetic_input,
    project_structural,
    sensitivity_matrix,
)


def build_case(n_occurrences: int = 30) -> EngineInput:
    people = [
        PersonIn(profile_id=i, code=f"SEN-{i:02d}", status=Status.SENIOR,
                 eligible_l1=True, eligible_l2=True, quotite_tenths=10)
        for i in range(1, 9)
    ] + [
        PersonIn(profile_id=100 + i, code=f"ASS-{i:02d}", status=Status.ASSISTANT,
                 eligible_l1=True, eligible_l2=False)
        for i in range(1, 5)
    ]

    posts: list[PostIn] = []
    avail: list[AvailabilityIn] = []
    post_id = 1
    start = date(2027, 1, 4)
    for k in range(n_occurrences):
        day = start + timedelta(days=k * 3)
        start_at = datetime.combine(day, time(20, 0))
        end_at = start_at + timedelta(hours=12)
        mode = CoverageMode.B if k % 2 == 0 else CoverageMode.A
        specs = (
            [(Line.L1, Status.ASSISTANT), (Line.L2, Status.SENIOR)]
            if mode is CoverageMode.B
            else [(Line.L1, Status.SENIOR)]
        )
        for line, required in specs:
            posts.append(PostIn(
                post_id=post_id, occurrence_id=k + 1, type_code="NUIT_SEMAINE",
                category_code="NUITS_LJ", line=line, required_status=required,
                start_at=start_at, end_at=end_at, local_date=day,
                coverage_mode=mode, is_weekend_block=day.weekday() >= 5,
            ))
            post_id += 1
        for person in people:
            color = Color.ROUGE if (k + person.profile_id) % 11 == 0 else (
                Color.ORANGE if (k + person.profile_id) % 5 == 0 else Color.VERT
            )
            avail.append(AvailabilityIn(person.profile_id, k + 1, color))

    quotas = [
        QuotaIn(p.profile_id, "NUITS_LJ", Line.L1, target=4.0)
        for p in people
    ] + [
        QuotaIn(p.profile_id, "NUITS_LJ", Line.L2, target=3.0)
        for p in people if p.status is Status.SENIOR
    ]

    return EngineInput(
        posts=posts, people=people, availabilities=avail, quotas=quotas,
        rest_rules=[RestRuleIn("REPOS_24H", "Repos 24 h", Enforcement.FERME,
                               min_hours_between=24.0)],
        profile=RuleProfile(name="operationnel_demo", version="v1"),
        seed=20260901, year_fraction_elapsed=1.0,
    )


def main() -> None:
    inp = build_case()
    solutions = solve(inp, variants=3, min_diversity=0.10)
    print(f"variantes obtenues : {len(solutions)}")
    for s in solutions:
        print(
            f"  variante {s.variant_index} graine={s.seed} score={s.score_total:.1f} "
            f"réalisable={s.feasible} orange={len(s.orange_used)} "
            f"non pourvus={len(s.unfilled)}"
        )
    best = solutions[0]
    print("  ventilation :", best.score_breakdown)

    # Contrôles fermes de base
    for post_id, pid in best.assignments.items():
        post = next(p for p in inp.posts if p.post_id == post_id)
        person = next(p for p in inp.people if p.profile_id == pid)
        assert not (post.line is Line.L2 and person.status is Status.ASSISTANT)
        color = next(
            (a.color for a in inp.availabilities
             if a.profile_id == pid and a.occurrence_id == post.occurrence_id), None
        )
        assert color is not Color.ROUGE, "AFFECTATION SUR ROUGE — inacceptable"
    print("  contrôles fermes : OK")

    # Reproductibilité
    again = solve(build_case(), variants=1)[0]
    assert again.assignments == solutions[0].assignments, "non reproductible"
    print("  reproductibilité même graine : OK")

    example = next(iter(best.explanations.values()))
    print("  exemple d'explication :", example.to_text())

    report = impossibility_report(best)
    print("  rapport d'impossibilité :", report.summary or "aucun poste non pourvu")

    # Projections
    params = ScenarioParams(
        name="demo",
        categories=(
            CategoryVolume("NUITS_LJ", "Nuits lundi-jeudi", 52, 1.0),
            CategoryVolume("WEEKENDS", "Week-ends et veilles", 26, 0.5, painful_weight=1.5),
            CategoryVolume("FERIES", "Jours fériés", 5, 0.4, painful_weight=2.0),
        ),
        assistants=AssistantGroup(count=5, guards_per_assistant=8),
        seniors=SeniorGroup(quotite_tenths=(10,) * 10 + (8, 8, 5, 5),
                            exemption_ratios=(0.0,) * 12 + (0.5, 1.0),
                            max_total_per_full_time=12.0),
        senior_load_threshold=10.0,
    )
    proj = project_structural(params)
    print(f"\nprojection : {proj.verdict}")
    print(f"  postes requis={proj.posts_required} répartis={proj.posts_assigned} "
          f"non couverts={proj.posts_uncovered} identité={proj.arithmetic_identity_holds}")
    print(f"  senior L1={proj.senior_l1} L2={proj.senior_l2} total={proj.senior_total} "
          f"moyenne={proj.mean_per_senior} min={proj.min_per_senior} max={proj.max_per_senior}")
    for r in proj.reasons:
        print("  raison :", r)

    cells = sensitivity_matrix(params, [2, 4, 6], [4, 8, 12])
    print("  matrice de sensibilité (extrait) :")
    for c in cells[:4]:
        print(f"    {c.assistants} assist. × {c.guards_per_assistant} → "
              f"L1 senior résiduelle {c.senior_l1_residual}, L2 {c.senior_l2_volume}")

    sim_input = build_synthetic_input(params, date(2027, 1, 4), weeks=13)
    sim = solve(sim_input, variants=1)[0]
    print(f"  simulation de faisabilité : réalisable={sim.feasible} "
          f"non pourvus={len(sim.unfilled)} score={sim.score_total:.0f}")
    print("\nOK")


if __name__ == "__main__":
    main()
