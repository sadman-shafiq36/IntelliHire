# deterministic parts of the interview score: what the analysis already measured for each
# dimension, checking the ai's judgement against the candidate's own lines, the capped
# adjustment and the weighted overall

from statistics import mean
from typing import Any

from app.agents.copilot.checks import gendered_words, protected_topics
from app.agents.screening.checks import quote_in_text

# shown on the screen, so the overall number is never opaque
WEIGHTS = {
    "technical_competency": 25,
    "problem_solving": 20,
    "depth_of_experience": 15,
    "job_alignment": 15,
    "relevance": 15,
    "communication": 10,
}
LABELS = {
    "technical_competency": "Technical competency",
    "problem_solving": "Problem-solving approach",
    "depth_of_experience": "Depth of experience shown",
    "job_alignment": "Alignment with the job's requirements",
    "relevance": "Relevance of answers",
    "communication": "Communication",
}
# how far the ai may move a score the analysis measured, and only with quoted evidence
MAX_ADJUSTMENT = 10
COVERAGE_POINTS = {"covered": 1.0, "partially_covered": 0.5}


def _snap(value: Any) -> int | None:
    try:
        return int(round(max(0.0, min(100.0, float(value))) / 5) * 5)
    except (TypeError, ValueError):
        return None


def _mean(values: list[float]) -> float | None:
    return round(mean(values), 1) if values else None


def baselines(
    pairs: list[dict[str, Any]], requirements: list[dict[str, Any]]
) -> dict[str, dict[str, Any]]:
    # problem solving and depth have no measured base: the ai scores them from quotes
    answered = [p for p in pairs if p.get("answer_turn_ids")]
    accuracy = [p["accuracy"] for p in answered if p.get("accuracy") is not None]
    relevance = [p["relevance"] for p in answered if p.get("relevance") is not None]
    articulation = [p["articulation"] for p in answered if p.get("articulation") is not None]
    best: dict[int, float] = {}
    for p in pairs:
        for link in p.get("requirements") or []:
            points = COVERAGE_POINTS.get(link["coverage"], 0.0)
            best[link["requirement_id"]] = max(best.get(link["requirement_id"], 0.0), points)
    total = sum(r["weight"] for r in requirements)
    alignment = (
        round(100 * sum(best.get(r["id"], 0.0) * r["weight"] for r in requirements) / total, 1)
        if total
        else None
    )
    return {
        "technical_competency": {
            "base": _mean(accuracy),
            "basis": f"mean accuracy of {len(accuracy)} answers",
        },
        "problem_solving": {"base": None, "basis": "judged from the candidate's answers"},
        "depth_of_experience": {"base": None, "basis": "judged from the candidate's answers"},
        "job_alignment": {
            "base": alignment,
            "basis": "requirements the interview covered, by requirement weight",
        },
        "relevance": {
            "base": _mean(relevance),
            "basis": f"mean relevance of {len(relevance)} answers",
        },
        "communication": {
            "base": _mean(articulation),
            "basis": f"mean articulation of {len(articulation)} answers",
        },
    }


def check_judgements(
    raw: list[dict[str, Any]],
    base: dict[str, dict[str, Any]],
    turns: list[dict[str, Any]],
) -> tuple[dict[str, dict[str, Any]], list[str]]:
    # a measured dimension moves by at most MAX_ADJUSTMENT and only with a quote that is
    # there; an unmeasured one needs quotes to get a score at all
    lines = {t["id"]: t["text"] for t in turns if t["speaker_role"] == "candidate"}
    out: dict[str, dict[str, Any]] = {}
    problems: list[str] = []
    for item in raw:
        name = item.get("dimension")
        if name not in WEIGHTS or name in out:
            continue
        reason = " ".join((item.get("reason") or "").split())[:600]
        if protected_topics(reason):
            problems.append(f"{LABELS[name]}: judge job-relevant content only.")
            continue
        if gendered_words(reason):
            problems.append(f'{LABELS[name]}: call the person "the candidate", not he or she.')
            continue
        evidence = [
            {"turn_id": e["turn_id"], "quote": " ".join(e["quote"].split())[:400]}
            for e in item.get("evidence") or []
            if e.get("turn_id") in lines
            and quote_in_text(e.get("quote") or "", lines[e["turn_id"]])
        ]
        proposed = _snap(item.get("score")) if item.get("score") is not None else None
        measured = base[name]["base"]
        if measured is None:
            if proposed is not None and not evidence:
                problems.append(
                    f"{LABELS[name]}: a score needs the candidate's exact words and the line id; "
                    "use null if the interview didn't show it."
                )
                continue
            final, adjustment = (proposed if evidence else None), 0.0
        else:
            adjustment = 0.0
            if proposed is not None and evidence:
                adjustment = max(-MAX_ADJUSTMENT, min(MAX_ADJUSTMENT, proposed - measured))
            final = round(max(0.0, min(100.0, measured + adjustment)), 1)
        out[name] = {
            "score": final,
            "base": measured,
            "adjustment": round(adjustment, 1),
            "reason": reason,
            "evidence": evidence,
        }
    missing = [n for n in WEIGHTS if n not in out]
    if missing:
        problems.append(f"Judge every dimension; missing: {', '.join(missing)}.")
    return out, problems


def fill_missing(
    dimensions: dict[str, dict[str, Any]], base: dict[str, dict[str, Any]]
) -> dict[str, dict[str, Any]]:
    # without a usable judgement a measured dimension keeps its measured score
    out = dict(dimensions)
    for name in WEIGHTS:
        if name not in out:
            out[name] = {
                "score": base[name]["base"],
                "base": base[name]["base"],
                "adjustment": 0.0,
                "reason": "",
                "evidence": [],
            }
    return out


def aggregate(dimensions: dict[str, dict[str, Any]]) -> tuple[float | None, dict[str, float]]:
    # a dimension the interview didn't show gives its weight to the others
    present = {n: d["score"] for n, d in dimensions.items() if d.get("score") is not None}
    total = sum(WEIGHTS[n] for n in present)
    if not total:
        return None, dict.fromkeys(WEIGHTS, 0.0)
    effective = {n: round(100 * WEIGHTS[n] / total, 1) if n in present else 0.0 for n in WEIGHTS}
    overall = round(sum(present[n] * WEIGHTS[n] / total for n in present), 1)
    return overall, effective
