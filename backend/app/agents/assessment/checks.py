# deterministic parts of the consolidated assessment: the component scores, the overall
# position and the cap, and checking that every finding the model wrote rests on evidence
# that is really there

import re
from typing import Any

from app.agents.copilot.checks import protected_topics
from app.agents.screening.checks import quote_in_text

# shown on the report, so the overall number is never opaque
WEIGHTS = {"cv_fit": 40, "interview_answers": 40, "requirement_coverage": 20}
STRONG, POSSIBLE = 70.0, 50.0
KINDS = ("strength", "weakness", "evidence_gap", "area_to_verify")
MAX_PER_KIND = 4

CV_POINTS = {"met": 1.0, "partially_met": 0.5, "unmet": 0.0}
INTERVIEW_POINTS = {"covered": 1.0, "partially_covered": 0.5, "not_explored": 0.0}

# a recommendation is the interviewer's call, not the report's
_VERDICT = re.compile(
    r"\b(should|must|would)\s+(not\s+)?(be\s+)?(hire|hired|reject|rejected)\b|"
    r"\brecommend(ed|s)?\s+(hiring|rejecting|them|him|her|the candidate)\b|"
    r"\b(hire|reject)\s+(him|her|them|the candidate)\b|\bnot\s+a\s+hire\b",
    re.I,
)


def requirement_evidence(requirements: list[dict[str, Any]]) -> list[dict[str, Any]]:
    # each requirement's best evidence from either stage
    out = []
    for r in requirements:
        cv = CV_POINTS.get(r.get("cv_status") or "", 0.0)
        interview = INTERVIEW_POINTS.get(r.get("interview_status") or "not_explored", 0.0)
        out.append({**r, "points": max(cv, interview)})
    return out


def compute(
    cv_score: float | None,
    answer_quality: float | None,
    requirements: list[dict[str, Any]],
) -> tuple[dict[str, dict[str, Any]], float | None, str, list[str]]:
    # returns components, overall, position and the must-haves that capped it
    evidence = requirement_evidence(requirements)
    total_weight = sum(r["weight"] for r in evidence) or 0.0
    coverage = (
        round(100 * sum(r["points"] * r["weight"] for r in evidence) / total_weight, 1)
        if total_weight
        else None
    )
    raw = {
        "cv_fit": cv_score,
        "interview_answers": answer_quality,
        "requirement_coverage": coverage,
    }
    # a component that couldn't be measured gives its weight to the others
    present = {k: v for k, v in raw.items() if v is not None}
    weight_sum = sum(WEIGHTS[k] for k in present)
    components = {
        k: {
            "score": round(raw[k], 1) if raw[k] is not None else None,
            "weight": WEIGHTS[k],
            "effective_weight": round(100 * WEIGHTS[k] / weight_sum, 1) if k in present else 0.0,
            "contribution": round(raw[k] * WEIGHTS[k] / weight_sum, 1) if k in present else 0.0,
        }
        for k in WEIGHTS
    }
    overall = round(sum(c["contribution"] for c in components.values()), 1) if present else None
    if overall is None:
        return components, None, "not_assessed", []
    position = (
        "strong_fit"
        if overall >= STRONG
        else "possible_fit"
        if overall >= POSSIBLE
        else "not_a_fit"
    )
    capped = [r["text"] for r in evidence if r["is_mandatory"] and r["points"] == 0]
    if capped and position == "strong_fit":
        position = "possible_fit"
    return components, overall, position, capped


def verdict_words(text: str) -> bool:
    return bool(_VERDICT.search(text))


def check_findings(
    answer: dict[str, Any],
    cv_text: str,
    turns: list[dict[str, Any]],
    requirements: list[dict[str, Any]],
    cv_document_id: int | None,
) -> tuple[str, list[dict[str, Any]], list[str]]:
    # returns the checked summary, the findings that rest on found evidence, and problems
    turn_text = {t["id"]: t["text"] for t in turns}
    req_text = {r["id"]: r["text"] for r in requirements}
    problems: list[str] = []
    summary = " ".join((answer.get("summary") or "").split())
    if verdict_words(summary):
        problems.append(
            "The summary must not recommend hiring or rejecting; describe the evidence."
        )
        summary = ""
    if protected_topics(summary):
        problems.append("The summary must not mention personal characteristics.")
        summary = ""

    kept: list[dict[str, Any]] = []
    per_kind: dict[str, int] = {}
    for f in answer.get("findings") or []:
        kind, text = f.get("kind"), " ".join((f.get("text") or "").split())
        if kind not in KINDS or not text:
            continue
        if verdict_words(text) or protected_topics(text):
            problems.append(f'"{text[:60]}" judges the person or recommends a decision; remove it.')
            continue
        evidence = []
        for e in f.get("evidence") or []:
            source, quote = e.get("source"), (e.get("quote") or "").strip()
            if source == "cv" and quote_in_text(quote, cv_text):
                evidence.append({"type": "cv", "ref_id": cv_document_id, "quote": quote})
            elif source == "transcript" and quote_in_text(
                quote, turn_text.get(e.get("turn_id"), "")
            ):
                evidence.append({"type": "transcript", "ref_id": e["turn_id"], "quote": quote})
            elif source == "requirement" and e.get("requirement_id") in req_text:
                rid = e["requirement_id"]
                evidence.append({"type": "score", "ref_id": rid, "quote": req_text[rid]})
        if not evidence:
            problems.append(
                f'"{text[:60]}" has no evidence that could be found. Quote the CV or a '
                "transcript line exactly, or name the requirement."
            )
            continue
        if per_kind.get(kind, 0) >= MAX_PER_KIND:
            continue
        per_kind[kind] = per_kind.get(kind, 0) + 1
        kept.append({"kind": kind, "text": text, "evidence": evidence})
    if not summary and not problems:
        problems.append("Write a short summary.")
    return summary, kept, problems
