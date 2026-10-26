# deterministic parts of the candidate feedback composer: shaping the stored evidence per
# stage, screening the notes people wrote, picking the points a letter may make (each one a
# real requirement and a stored row), the sentences code owns (the outcome, the fallbacks),
# and the guardrail rules a draft or an edit has to pass before anyone can approve it

import re
from typing import Any

from app.agents.copilot.checks import protected_topics
from app.agents.screening.checks import normalise

STAGES = ("screening", "interview")
OUTCOMES = {
    "screening": ("not_progressing",),
    "interview": ("not_progressing", "on_hold", "next_round"),
}
MAX_STRENGTHS = {"screening": 1, "interview": 2}
MAX_AREAS = {"not_progressing": 3, "on_hold": 1, "next_round": 2}
# words the ai writes, not counting the sentences code owns
MAX_WORDS = {"screening": 110, "interview": 200}
PLACEHOLDERS = {"{candidate_name}", "{signature}"}
# fallbacks caused by the evidence; regenerating can't fix those
EVIDENCE_REASONS = ("low_confidence", "unreadable", "no_evidence")

DIMENSION_FOR = {
    "technical_skill": "technical_skills",
    "experience": "relevant_experience",
    "qualification": "qualifications",
}
CV_RANK = {"unmet": 0, "partially_met": 1, "met": 2}

RULES = {
    "protected": "Protected characteristics and proxies",
    "scores": "Scores, rankings and percentages",
    "others": "Other candidates",
    "timestamps": "Dates, times and interview moments",
    "personal_data": "Personal data",
    "promises": "Promises about the future",
    "offer": "Offer, salary or onboarding",
    "outcome": "Agrees with the decided outcome",
    "unsupported": "Every point rests on stored evidence",
    "length": "Length",
}
# the judge's categories, shown under the rule they belong to
JUDGE_RULE = {
    "protected_characteristic": "protected",
    "score_or_ranking": "scores",
    "other_candidates": "others",
    "timestamp": "timestamps",
    "personal_data": "personal_data",
    "promise": "promises",
    "offer_or_salary": "offer",
    "contradicts_outcome": "outcome",
    "unsupported_claim": "unsupported",
    "harsh_tone": "unsupported",
}

# things that stand in for a protected characteristic. "race condition", "foreign key" and
# "CORS origin" are ordinary technical words and must stay allowed
_PROXY_RE = re.compile(
    r"\b(young\w*|youthful|old(er)?|elderly|aged?\s+\d+|\d+\s+years? old|energetic|"
    r"digital native|fresh blood|mature (candidate|person|applicant|lady|man|woman)|"
    r"gen z|millennials?|boomers?|retire\w*|graduat\w* in (19|20)\d\d|too senior|"
    r"overqualified|over-qualified|culture fit|cultural fit|fit in with|accent\w*|"
    r"first language|mother tongue|native (speaker|english)|foreigner|foreign national|"
    r"immigra\w*|visa|work permit|skin|appearance|photo\w*|attractive|"
    r"family (commitments|responsibilities|plans)|childcare|maternity|paternity|"
    r"career break|commut\w*|neighbou?rhood|postcode|wheelchair|medical|sick leave|"
    r"boyfriend|girlfriend|husband|wife|guy|girl|lady|gentleman)\b",
    re.I,
)
# a letter says "you"; a pronoun in it is a guess at gender. notes may use them neutrally
_PRONOUN_RE = re.compile(r"\b(he|she|him|his|her|hers|himself|herself)\b", re.I)
_SCORES_RE = re.compile(
    r"\b\d+(\.\d+)?\s*(%|percent\b|per cent\b|/\s*\d+|out of\s+\d+|points?\b)|"
    r"\b(score[sd]?|scoring|rank(ed|ing|ings|s)?|percentile|rating|rated|top\s+\d+|"
    r"shortlist\w*)\b",
    re.I,
)
_OTHERS_RE = re.compile(
    r"\b(other|another|fellow|remaining|successful|stronger|competing|best)\s+"
    r"(candidates?|applicants?|people|profiles?)\b|\bcandidate pool\b|"
    r"\b(than|compared (to|with)|against) (other|the other|others|everyone)\b|"
    r"\b(many|a large number of|a high volume of|so many) applica\w+\b",
    re.I,
)
_WHEN_RE = re.compile(
    r"\b\d{1,2}[:.]\d{2}\b|\b\d{1,2}\s?(am|pm)\b|"
    r"\b(monday|tuesday|wednesday|thursday|friday|saturday|sunday|yesterday|today|"
    r"tomorrow|last week|next week)\b|"
    r"\b\d{1,2}(st|nd|rd|th)?\s+(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\b|"
    r"\b(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\s+\d{1,2}\b|"
    r"\b\d{4}-\d{2}-\d{2}\b|\b\d{1,2}/\d{1,2}(/\d{2,4})?\b|"
    r"\b\d+\s*(minutes?|mins?|seconds?)\s+(in|into)\b|\[T\d+\]|"
    r"\b(at|in) the (start|beginning|end) of (the|your) (interview|call)\b",
    re.I,
)
_PERSONAL_RE = re.compile(
    r"[\w.+-]+@[\w-]+\.[\w.-]+|\+?\d[\d\s().-]{7,}\d|https?://|www\.|"
    r"\[(name|email|phone|redacted)[^\]]*\]|"
    r"\b\d+\s+\w+\s+(street|st|road|rd|avenue|ave|lane)\b",
    re.I,
)
_PLACEHOLDER_RE = re.compile(r"\{[^{}\s]*\}")
_PROMISE_RE = re.compile(
    r"\b(guarantee\w*|promis\w*|assure\w*|certainly|definitely|"
    r"keep (your|you|the) (cv|details|application|profile) on (file|record)|"
    r"future (roles?|openings?|vacanc\w+|opportunit\w+|positions?)|"
    r"(when|once|if) (a|another|a similar) (role|position|vacancy) (opens|comes up|becomes)|"
    r"(will|'ll|shall) (be|get) (in touch|back to you) (soon|shortly|within)|"
    r"(you will|you'll) (be|get|receive) (an? )?(offer|hired|the (role|job|position))|"
    r"(we|i) (will|'ll) (consider|hire|offer|reconsider))\b",
    re.I,
)
_OFFER_RE = re.compile(
    r"\b(offer (letter|of employment)|job offer|(make|extend|send)( you)? an offer|"
    r"offer you (the|a|this) (role|job|position)|salary|salaries|compensation|"
    r"remuneration|pay (rate|range|band)|payroll|(salary|benefits|compensation) package|"
    r"employee benefits|bonus\w*|equity|stock options?|start(ing)? date|onboard\w*|"
    r"induction|employment contract|contract of employment|welcome (aboard|to the team)|"
    r"first day|notice period|relocation)\b",
    re.I,
)
_CONTRADICTS = {
    "not_progressing": re.compile(
        r"\b(next (round|stage|step) of|invite you (to|for)|"
        r"(progress|advanc|mov)\w* (you )?(to|into) the next|on hold|"
        r"under consideration|still considering)\b",
        re.I,
    ),
    "on_hold": re.compile(
        r"\b(unsuccessful|decided not to|won't be (taking|moving)|"
        r"not (be )?(moving|taking) (it|you|your application) forward|"
        r"next (round|stage|step) of|invite you (to|for))\b",
        re.I,
    ),
    "next_round": re.compile(
        r"\b(unsuccessful|decided not to|won't be (taking|moving)|"
        r"not (be )?(moving|taking) (it|you|your application) forward|regret|on hold|"
        r"under consideration)\b",
        re.I,
    ),
}
_CV_FRAMING = re.compile(r"\b(your (cv|application|résumé|resume))\b", re.I)


# --- the evidence ----------------------------------------------------------------------


def screening_evidence(
    sources: dict[str, Any], min_confidence: float, min_cv_chars: int
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], str | None]:
    # returns the requirements with their screening result, the score dimensions, and why the
    # letter has to be the generic one, if it has to
    results = {r["requirement_id"]: r for r in sources.get("results") or []}
    requirements = []
    for r in sources.get("requirements") or []:
        res = results.get(r["id"])
        requirements.append(
            {
                **r,
                "cv_status": res["status"] if res else None,
                "cv_result_id": res["id"] if res else None,
                "cv_quote": res["quote"] if res and res.get("verified") else None,
                "cv_reasoning": res.get("reasoning") if res else None,
                "confidence": res.get("confidence") if res else None,
            }
        )
    dimensions = [d for d in sources.get("dimensions") or [] if d.get("justification")]
    if (
        sources.get("screening_status") in (None, "failed")
        or (sources.get("cv_chars") or 0) < min_cv_chars
    ):
        return requirements, dimensions, "unreadable"
    if not results:
        return requirements, dimensions, "no_evidence"
    confidences = [r["confidence"] for r in results.values() if r.get("confidence") is not None]
    if confidences and sum(confidences) / len(confidences) < min_confidence:
        return requirements, dimensions, "low_confidence"
    return requirements, dimensions, None


def interview_evidence(
    sources: dict[str, Any],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], str | None]:
    # each requirement with its cv result and the best q&a pair that covered it; the
    # assessment's findings with the requirements they name
    results = {r["requirement_id"]: r for r in sources.get("results") or []}
    order = {"covered": 2, "partially_covered": 1}
    best: dict[int, dict[str, Any]] = {}
    for p in sources.get("pairs") or []:
        for link in p.get("links") or []:
            rid, cov = link["requirement_id"], link["coverage"]
            held = best.get(rid)
            if order.get(cov, 0) > order.get(held["coverage"] if held else "", 0):
                best[rid] = {"pair_id": p["id"], "coverage": cov, "question": p["question"],
                             "justification": p.get("justification") or ""}  # fmt: skip
    requirements = []
    for r in sources.get("requirements") or []:
        res, pair = results.get(r["id"]), best.get(r["id"])
        requirements.append(
            {
                **r,
                "cv_status": res["status"] if res else None,
                "cv_result_id": res["id"] if res else None,
                "cv_quote": res["quote"] if res and res.get("verified") else None,
                "cv_reasoning": res.get("reasoning") if res else None,
                "interview_status": pair["coverage"] if pair else "not_explored",
                "pair": pair,
            }
        )
    known = {r["id"] for r in requirements}
    findings = [
        {**f, "requirement_ids": [i for i in f.get("requirement_ids") or [] if i in known]}
        for f in sources.get("findings") or []
    ]
    reason = None if requirements else "no_evidence"
    return requirements, findings, reason


def _strip_refs(text: str) -> str:
    # transcript and source markers mean nothing to the drafter and invite timestamps
    return " ".join(re.sub(r"\s*\[(T|S)\d+\]", "", text or "").split())


def _order(requirements: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(requirements, key=lambda r: (not r["is_mandatory"], -(r.get("weight") or 0)))


def select_points(
    stage: str,
    outcome: str,
    requirements: list[dict[str, Any]],
    findings: list[dict[str, Any]],
    dimensions: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    # code decides what the letter may talk about; the model only words it
    points: list[dict[str, Any]] = []
    by_dimension = {d["dimension"]: d for d in dimensions}

    def add(kind: str, r: dict[str, Any], ref_type: str, ref_id: int, evidence: str) -> None:
        support = []
        dim = by_dimension.get(DIMENSION_FOR.get(r.get("category") or "", ""))
        if dim:
            support.append({"type": "score_dimension", "id": dim["id"]})
        for f in findings:
            if r["id"] in f.get("requirement_ids", []):
                support.append({"type": "assessment_finding", "id": f["id"]})
        points.append(
            {
                "id": f"P{len(points) + 1}",
                "kind": kind,
                "requirement_id": r["id"],
                "requirement": r["text"],
                "evidence_ref": {"type": ref_type, "id": ref_id},
                "evidence": _strip_refs(evidence)[:400],
                "support": support,
            }
        )

    ordered = _order(requirements)
    if stage == "screening":
        strengths = [r for r in ordered if r["cv_status"] == "met" and r.get("cv_quote")]
        for r in strengths[: MAX_STRENGTHS["screening"]]:
            add("strength", r, "screening_result", r["cv_result_id"], f'CV: "{r["cv_quote"]}"')
        gaps = sorted(
            (r for r in ordered if r["cv_status"] in ("unmet", "partially_met")),
            key=lambda r: (CV_RANK[r["cv_status"]], not r["is_mandatory"]),
        )
        for r in gaps[: MAX_AREAS["not_progressing"]]:
            add(
                "development", r, "screening_result", r["cv_result_id"], r.get("cv_reasoning") or ""
            )
        return points

    covered = [r for r in ordered if r["interview_status"] == "covered"]
    for r in covered[: MAX_STRENGTHS["interview"]]:
        add("strength", r, "qa_pair", r["pair"]["pair_id"], r["pair"]["justification"])
    used = {p["requirement_id"] for p in points}
    areas: list[tuple[dict[str, Any], str, int, str]] = []
    for r in ordered:
        if r["id"] in used:
            continue
        if r["interview_status"] == "partially_covered":
            areas.append((r, "qa_pair", r["pair"]["pair_id"], r["pair"]["justification"]))
    for f in findings:
        if f["kind"] not in ("weakness", "evidence_gap"):
            continue
        for rid in f["requirement_ids"]:
            r = next(x for x in requirements if x["id"] == rid)
            if rid not in used and all(a[0]["id"] != rid for a in areas):
                areas.append((r, "assessment_finding", f["id"], f["text"]))
    if outcome != "not_progressing":
        # next round and on hold may name what is still open, not only what went badly
        listed = {a[0]["id"] for a in areas}
        for r in ordered:
            if (
                r["interview_status"] == "not_explored"
                and r["id"] not in used | listed
                and r.get("cv_result_id")
            ):
                areas.append(
                    (r, "screening_result", r["cv_result_id"], "Not discussed in the interview.")
                )
    kind = "development" if outcome == "not_progressing" else "explore_next"
    for r, ref_type, ref_id, evidence in areas[: MAX_AREAS[outcome]]:
        add(kind, r, ref_type, ref_id, evidence)
    return points


# --- the notes -------------------------------------------------------------------------


def note_problems(text: str) -> list[str]:
    found = set(protected_topics(text))
    found.update(m.group(0).lower() for m in _PROXY_RE.finditer(text))
    return sorted(found)


# --- the letter ------------------------------------------------------------------------


def subject(stage: str, job_title: str) -> str:
    if stage == "screening":
        return f"Your application for the {job_title} role"
    return f"Your interview for the {job_title} role"


def outcome_sentence(stage: str, outcome: str, job_title: str) -> str:
    if stage == "screening":
        return (
            f"Thank you for applying for the {job_title} role. After reviewing your application "
            "against the role's requirements, we won't be taking it forward to interview."
        )
    opening = f"Thank you for interviewing with us for the {job_title} role."
    return {
        "not_progressing": f"{opening} We've decided not to move forward with your application.",
        "on_hold": f"{opening} We haven't reached a decision yet, and your application is "
        "still under consideration.",
        "next_round": f"{opening} We'd like to invite you to the next stage of the process, and "
        "we'll be in touch separately to arrange it.",
    }[outcome]


FALLBACK = {
    "screening": (
        "We weren't able to put together detailed feedback on your CV this time, and we're "
        "sorry we can't say more about this decision.",
        "Thank you again for your interest in working with us.",
    ),
    "not_progressing": (
        "Thank you for the time you spent with us and for the thought you put into your answers.",
        "We wish you every success in your search.",
    ),
    "on_hold": (
        "Thank you for the time you spent with us and for the thought you put into your answers.",
        "Thank you for your patience.",
    ),
    "next_round": (
        "Thank you for the time you spent with us and for the thought you put into your answers.",
        "We look forward to speaking with you again.",
    ),
}


def compose(
    stage: str, outcome: str, job_title: str, sentences: list[dict[str, Any]], closing: str
) -> dict[str, Any]:
    # the name and the signature are placeholders filled when it is sent, so neither is ever
    # in the draft, a checkpoint or a prompt
    middle = " ".join(" ".join(s["text"].split()) for s in sentences)
    parts = ["Dear {candidate_name},", outcome_sentence(stage, outcome, job_title)]
    if middle:
        parts.append(middle)
    if closing.strip():
        parts.append(" ".join(closing.split()))
    parts.append("Best regards,\n{signature}")
    return {
        "subject": subject(stage, job_title),
        "body": "\n\n".join(parts),
        "sentences": sentences,
        "closing": closing.strip(),
    }


def fallback_draft(stage: str, outcome: str, job_title: str) -> dict[str, Any]:
    text, closing = FALLBACK["screening" if stage == "screening" else outcome]
    return compose(stage, outcome, job_title, [{"text": text, "point_ids": []}], closing)


def _hits(pattern: re.Pattern[str], text: str) -> list[str]:
    return sorted({m.group(0).strip().lower() for m in pattern.finditer(text)})


def check_text(subject_line: str, body: str, outcome: str) -> dict[str, list[str]]:
    # rule -> what was found. runs on the whole letter: drafts, fallbacks and a person's edits
    text = f"{subject_line}\n{body}"
    hits = {
        "protected": sorted(
            set(protected_topics(text))
            | set(_hits(_PROXY_RE, text))
            | set(_hits(_PRONOUN_RE, text))
        ),
        "scores": _hits(_SCORES_RE, text),
        "others": _hits(_OTHERS_RE, text),
        "timestamps": _hits(_WHEN_RE, text),
        "personal_data": _hits(_PERSONAL_RE, text)
        + sorted(set(_PLACEHOLDER_RE.findall(text)) - PLACEHOLDERS),
        "promises": _hits(_PROMISE_RE, text),
        "offer": _hits(_OFFER_RE, text),
        "outcome": _hits(_CONTRADICTS[outcome], text),
    }
    return {k: v for k, v in hits.items() if v}


def check_sentences(
    draft: dict[str, Any], points: list[dict[str, Any]], stage: str
) -> dict[str, list[str]]:
    # what only a fresh draft can be checked for: every sentence rests on a selected point,
    # and it isn't longer than the stage allows
    ids = {p["id"]: p for p in points}
    hits: dict[str, list[str]] = {}
    unsupported = []
    for s in draft.get("sentences") or []:
        cited = s.get("point_ids") or []
        if (
            not cited
            or any(c not in ids for c in cited)
            or (
                stage == "screening"
                and any(ids[c]["kind"] == "development" for c in cited)
                and not _CV_FRAMING.search(s["text"])
            )
        ):
            unsupported.append(s["text"][:80])
    if unsupported:
        hits["unsupported"] = unsupported
    words = sum(len(s["text"].split()) for s in draft.get("sentences") or [])
    words += len((draft.get("closing") or "").split())
    if words > MAX_WORDS[stage]:
        hits["length"] = [f"{words} words"]
    return hits


def fixes(hits: dict[str, list[str]], issues: list[dict[str, Any]], stage: str) -> list[str]:
    # problems phrased as instructions for the redraft
    out = []
    for rule, found in hits.items():
        if rule == "unsupported":
            framing = (
                " Gaps are about the CV: say what your CV didn't clearly show."
                if stage == "screening"
                else ""
            )
            out.append(
                "Every sentence must name the point ids it rests on, and only those points: "
                + "; ".join(f'"{f}"' for f in found)
                + "."
                + framing
            )
        elif rule == "length":
            out.append(f"Too long ({found[0]}); stay under {MAX_WORDS[stage]} words.")
        else:
            out.append(f"Remove anything about {RULES[rule].lower()}: {', '.join(found)}.")
    for i in issues:
        out.append(f'"{i["quote"][:80]}": {i["explanation"]}')
    return out


def found_in(quote: str, text: str) -> bool:
    q = normalise(quote)
    return bool(q) and q in normalise(text)


def keep_found(issues: list[dict[str, Any]], text: str) -> list[dict[str, Any]]:
    # a judge's finding counts only when the words it objects to are really in the letter
    return [
        {"category": i["category"], "quote": i["quote"], "explanation": i["explanation"]}
        for i in issues
        if i.get("category") in JUDGE_RULE and found_in(i.get("quote") or "", text)
    ]


def report(
    hits: dict[str, list[str]],
    judge: dict[str, Any],
    target: str,
    attempts: int,
) -> dict[str, Any]:
    by_rule: dict[str, list[str]] = {k: list(v) for k, v in hits.items()}
    for i in judge.get("issues") or []:
        by_rule.setdefault(JUDGE_RULE[i["category"]], [])
    rules = [
        {
            "rule": rule,
            "label": label,
            "passed": rule not in by_rule,
            "found": by_rule.get(rule, []),
            "judge": [i for i in judge.get("issues") or [] if JUDGE_RULE[i["category"]] == rule],
        }
        for rule, label in RULES.items()
    ]
    return {
        "passed": not by_rule,
        "rules": rules,
        "judge": judge,
        "checked": target,
        "attempts": attempts,
    }
