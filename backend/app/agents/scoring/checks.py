# deterministic parts of scoring: the evidence-based starting score per dimension, the
# ats-style match, checking the ai's adjustments, and the weighted aggregate

import math
from typing import Any

from app.agents.screening.checks import POINTS, normalise, quote_in_text

TECHNICAL = "technical_skills"
EXPERIENCE = "relevant_experience"
QUALIFICATIONS = "qualifications"
JD_MATCH = "jd_match"
DIMENSIONS = (TECHNICAL, EXPERIENCE, QUALIFICATIONS, JD_MATCH)
# the ai may adjust these; the description match is mechanical on purpose
ASSESSED = (TECHNICAL, EXPERIENCE, QUALIFICATIONS)

CATEGORY_DIMENSION = {
    "technical_skill": TECHNICAL,
    "experience": EXPERIENCE,
    "responsibility": EXPERIENCE,
    "other": EXPERIENCE,
    "qualification": QUALIFICATIONS,
}

DEFAULT_WEIGHTS = {TECHNICAL: 40, EXPERIENCE: 30, QUALIFICATIONS: 15, JD_MATCH: 15}

MAX_ADJUSTMENT = 15
# adjustments snap to 5s so near-identical runs land on the same number
ADJUSTMENT_STEP = 5

# gemini embeddings of a cv and a description of the same field land around 0.70-0.82
# (measured on the synthetic backend batch), so the raw cosine is stretched over this band
SIMILARITY_FLOOR = 0.60
SIMILARITY_CEILING = 0.85
EMBED_MAX_CHARS = 8000


def dimension_for(category: str) -> str:
    return CATEGORY_DIMENSION.get(category, EXPERIENCE)


def evidence_scores(
    results: list[dict[str, Any]],
    requirements: list[dict[str, Any]],
    years: float | None,
    min_years: int,
) -> dict[str, dict[str, Any]]:
    # the share of each dimension's requirements met (weighted, partly met = half),
    # straight from the verified screening. a dimension with nothing to judge is absent
    by_id = {r["requirement_id"]: r for r in results}
    items: dict[str, list[dict[str, Any]]] = {}
    for req in requirements:
        result = by_id.get(req["id"], {})
        status = result.get("status", "unmet")
        items.setdefault(dimension_for(req["category"]), []).append(
            {
                "requirement_id": req["id"],
                "text": req["text"],
                "status": status,
                "weight": req["weight"],
                "points": POINTS[status],
                "evidence_quote": result.get("evidence_quote") or "",
            }
        )
    if min_years > 0:
        have = max(0.0, float(years or 0))
        items.setdefault(EXPERIENCE, []).append(
            {
                "requirement_id": None,
                "text": f"At least {min_years} years of experience",
                "status": "met" if have >= min_years else ("partially_met" if have else "unmet"),
                "weight": 1.0,
                "points": min(1.0, have / min_years),
                "evidence_quote": "",
                "years": have,
            }
        )

    out = {}
    for dim, rows in items.items():
        total = sum(r["weight"] for r in rows) or 1.0
        got = sum(r["weight"] * r["points"] for r in rows)
        out[dim] = {"score": round(100 * got / total), "items": rows, "basis": _basis(rows)}
    return out


def _basis(rows: list[dict[str, Any]]) -> str:
    met = sum(1 for r in rows if r["status"] == "met")
    partly = sum(1 for r in rows if r["status"] == "partially_met")
    text = f"{met} of {len(rows)} met"
    if partly:
        text += f", {partly} partly met"
    return text + " in the screening, with the CV quotes checked."


# --- description match ------------------------------------------------------------


def contains_term(term: str, text_normalised: str) -> bool:
    needle = normalise(term)
    return bool(needle) and f" {needle} " in text_normalised


def keyword_coverage(keywords: list[dict[str, Any]], cv_text: str) -> tuple[list[str], list[str]]:
    haystack = f" {normalise(cv_text)} "
    found, missing = [], []
    for kw in keywords:
        terms = [kw["term"], *kw.get("aliases", [])]
        (found if any(contains_term(t, haystack) for t in terms) else missing).append(kw["term"])
    return found, missing


def cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=False))
    norm = math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b))
    return dot / norm if norm else 0.0


def similarity_score(similarity: float) -> int:
    span = SIMILARITY_CEILING - SIMILARITY_FLOOR
    return round(100 * min(1.0, max(0.0, (similarity - SIMILARITY_FLOOR) / span)))


def description_match(
    keywords: list[dict[str, Any]], cv_text: str, similarity: float
) -> dict[str, Any]:
    # like an applicant tracking system: are the description's key terms in the cv, and
    # how close is the cv's wording overall. half each when there are key terms
    found, missing = keyword_coverage(keywords, cv_text)
    semantic = similarity_score(similarity)
    if keywords:
        coverage = round(100 * len(found) / len(keywords))
        score = round((coverage + semantic) / 2)
        basis = f"Has {len(found)} of the description's {len(keywords)} key terms"
        basis += f" (missing: {', '.join(missing)})." if missing else "."
    else:
        coverage = None
        score = semantic
        basis = "No key terms were set for this job, so only the overall wording is compared."
    basis += f" Overall wording similarity {similarity:.2f}, which scores {semantic}."
    return {
        "score": score,
        "basis": basis,
        "found": found,
        "missing": missing,
        "coverage": coverage,
        "similarity": round(similarity, 3),
        "semantic": semantic,
    }


# --- the ai's adjustments -----------------------------------------------------------


def snap(adjustment: Any) -> int:
    try:
        value = float(adjustment)
    except (TypeError, ValueError):
        return 0
    value = max(-MAX_ADJUSTMENT, min(MAX_ADJUSTMENT, value))
    return int(ADJUSTMENT_STEP * round(value / ADJUSTMENT_STEP))


def verify_assessment(
    answers: list[dict[str, Any]], applicable: list[str], cv_text: str
) -> tuple[dict[str, dict[str, Any]], list[str]]:
    # returns the checked answer per dimension plus problems phrased as fixes.
    # a quote counts only if it is in the cv; an adjustment needs at least one
    checked: dict[str, dict[str, Any]] = {}
    problems: list[str] = []
    for a in answers:
        dim = a.get("dimension")
        if dim not in applicable:
            continue
        if dim in checked:
            problems.append(f"{dim} was answered twice; answer it once.")
            continue
        quotes = [q.strip() for q in a.get("evidence_quotes") or [] if q and q.strip()]
        verified = [q for q in quotes if quote_in_text(q, cv_text)]
        adjustment = snap(a.get("adjustment"))
        if len(verified) < len(quotes):
            problems.append(
                f"{dim}: these quotes are not copied word for word from the CV: "
                + "; ".join(f'"{q}"' for q in quotes if q not in verified)
                + ". Quote the CV exactly or leave them out."
            )
        if adjustment and not verified:
            problems.append(
                f"{dim}: an adjustment of {adjustment:+d} needs at least one exact quote from "
                "the CV that supports it. Add one, or set the adjustment to 0."
            )
        if not (a.get("justification") or "").strip():
            problems.append(f"{dim}: the justification is empty.")
        checked[dim] = {
            "adjustment": adjustment,
            "justification": (a.get("justification") or "").strip(),
            "quotes": verified,
            "rejected_quotes": [q for q in quotes if q not in verified],
        }
    for dim in applicable:
        if dim not in checked:
            problems.append(f"{dim} has no answer.")
    return checked, problems


def settle_assessment(
    checked: dict[str, dict[str, Any]], applicable: list[str]
) -> tuple[dict[str, dict[str, Any]], list[str]]:
    # after the last attempt: an adjustment without a verified quote is dropped, so
    # nothing unproven moves a score
    settled, warnings = {}, []
    for dim in applicable:
        a = checked.get(dim)
        if a is None:
            settled[dim] = {"adjustment": 0, "justification": "", "quotes": []}
            warnings.append(
                f"The AI gave no assessment for {label(dim)}; the evidence score is used."
            )
        elif a["adjustment"] and not a["quotes"]:
            settled[dim] = {"adjustment": 0, "justification": "", "quotes": []}
            warnings.append(
                f"The AI's change to {label(dim)} had no quote from the CV, so it was not applied."
            )
        else:
            settled[dim] = a
    return settled, warnings


def combine(
    base: dict[str, dict[str, Any]],
    assessment: dict[str, dict[str, Any]],
    match: dict[str, Any] | None,
) -> dict[str, dict[str, Any]]:
    dims: dict[str, dict[str, Any]] = {}
    for dim in ASSESSED:
        if dim not in base:
            continue
        a = assessment.get(dim) or {"adjustment": 0, "justification": "", "quotes": []}
        b = base[dim]
        score = max(0, min(100, b["score"] + a["adjustment"]))
        dims[dim] = {
            "score": score,
            "base_score": b["score"],
            # what was applied: +5 on an evidence score of 100 changes nothing
            "adjustment": score - b["score"],
            "justification": a["justification"] or b["basis"],
            "details": {
                "basis": b["basis"],
                "quotes": a["quotes"],
                "requirements": [
                    {k: r[k] for k in ("requirement_id", "text", "status", "evidence_quote")}
                    for r in b["items"]
                ],
            },
        }
    if match is not None:
        dims[JD_MATCH] = {
            "score": match["score"],
            "base_score": match["score"],
            "adjustment": 0,
            "justification": match["basis"],
            "details": {k: v for k, v in match.items() if k not in ("score", "basis")}
            | {"basis": match["basis"]},
        }
    return dims


# --- weights and the aggregate --------------------------------------------------------


def normalise_weights(weights: dict[str, float], applicable: list[str]) -> dict[str, int]:
    # whole percentages that add up to 100 over the dimensions that can be scored
    raw = {d: max(0.0, float(weights.get(d) or 0)) for d in DIMENSIONS}
    for d in DIMENSIONS:
        if d not in applicable:
            raw[d] = 0.0
    total = sum(raw.values())
    if total <= 0:
        raw = {d: float(DEFAULT_WEIGHTS[d]) if d in applicable else 0.0 for d in DIMENSIONS}
        total = sum(raw.values()) or 1.0
    exact = {d: 100 * v / total for d, v in raw.items()}
    out = {d: math.floor(v) for d, v in exact.items()}
    # largest remainder, ties in dimension order, so the result is stable
    short = 100 - sum(out.values())
    order = sorted(DIMENSIONS, key=lambda d: (-(exact[d] - out[d]), DIMENSIONS.index(d)))
    for d in order[:short]:
        out[d] += 1
    return out


def aggregate(scores: dict[str, float], weights: dict[str, float]) -> float:
    # weights of dimensions this candidate has no score for are left out, not counted as 0
    used = {d: w for d, w in weights.items() if d in scores and w > 0}
    total = sum(used.values())
    if not total:
        return 0.0
    return round(sum(scores[d] * w for d, w in used.items()) / total, 1)


LABELS = {
    TECHNICAL: "technical skills",
    EXPERIENCE: "relevant experience",
    QUALIFICATIONS: "qualifications",
    JD_MATCH: "description match",
}


def label(dim: str) -> str:
    return LABELS.get(dim, dim)
