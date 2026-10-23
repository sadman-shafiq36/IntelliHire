# deterministic parts of checking cv claims against the interview: a claim has to be quoted
# from the cv, a verdict's evidence has to be the candidate's own words in the line named,
# and the wording has to read as a prompt for the interviewer, never an accusation

import re
from typing import Any

from app.agents.assessment.checks import verdict_words
from app.agents.copilot.checks import gendered_words, protected_topics, same_question
from app.agents.screening.checks import quote_in_text

MAX_CLAIMS = 10
STATUSES = ("supported", "contradiction", "unverified", "not_discussed")
# a status that says what the interview showed has to point at it
NEEDS_EVIDENCE = ("supported", "contradiction", "unverified")

_ACCUSING = re.compile(
    r"\b(lie|lies|lied|lying|liar|dishonest\w*|fabricat\w*|fake|faked|false claims?|"
    r"deceiv\w*|deception|deceptive|mislead\w*|misled|exaggerat\w*|inflat\w*|untruthful|"
    r"cheat\w*|fraud\w*|made up|padded)\b",
    re.I,
)


def accusing_words(text: str) -> list[str]:
    return sorted({m.group(1).lower() for m in _ACCUSING.finditer(text)})


def check_claims(raw: list[dict[str, Any]], cv_text: str) -> tuple[list[dict[str, Any]], list[str]]:
    # returns the claims whose quote is in the cv, and problems to send back
    kept: list[dict[str, Any]] = []
    problems: list[str] = []
    for item in raw:
        claim = " ".join((item.get("claim") or "").split())
        quote = " ".join((item.get("cv_quote") or "").split())
        topic = " ".join((item.get("topic") or "").split())[:80]
        if not claim or not quote:
            continue
        if protected_topics(f"{claim} {topic}"):
            problems.append(f'"{claim[:60]}" is about a personal characteristic; leave it out.')
            continue
        if not quote_in_text(quote, cv_text):
            problems.append(
                f'"{quote[:60]}" is not in the CV word for word; copy the quote exactly.'
            )
            continue
        if any(same_question(claim, k["claim"]) or quote == k["cv_quote"] for k in kept):
            continue
        kept.append({"claim": claim[:300], "cv_quote": quote[:400], "topic": topic or claim[:40]})
    return kept[:MAX_CLAIMS], problems


def _template_follow_up(claim: dict[str, Any]) -> str:
    return f'Your CV mentions "{claim["cv_quote"][:120]}". Could you walk me through that?'


def _clean(text: str | None) -> str:
    return " ".join((text or "").split())


def check_verdicts(
    raw: list[dict[str, Any]],
    claims: list[dict[str, Any]],
    turns: list[dict[str, Any]],
) -> tuple[dict[str, dict[str, Any]], list[str]]:
    # keyed by claim index (str, checkpoints are json). evidence is kept only when it is the
    # candidate's line and the quote is in it
    lines = {t["id"]: t["text"] for t in turns if t["speaker_role"] == "candidate"}
    out: dict[str, dict[str, Any]] = {}
    problems: list[str] = []
    for item in raw:
        index = item.get("claim")
        if not isinstance(index, int) or not 0 <= index < len(claims) or str(index) in out:
            problems.append(f"Verdict for claim {index} doesn't match a claim, or repeats one.")
            continue
        status = item.get("status")
        if status not in STATUSES:
            problems.append(f"Claim {index}: status must be one of {', '.join(STATUSES)}.")
            continue
        evidence = []
        for e in item.get("evidence") or []:
            turn_id, quote = e.get("turn_id"), _clean(e.get("quote"))
            if turn_id in lines and quote_in_text(quote, lines[turn_id]):
                evidence.append({"turn_id": turn_id, "quote": quote[:400]})
        if status in NEEDS_EVIDENCE and not evidence:
            problems.append(
                f"Claim {index}: {status} needs the candidate's exact words and the id of the "
                "candidate line they are in. If the candidate never talked about it, use "
                "not_discussed."
            )
            continue
        if status == "not_discussed" and evidence:
            problems.append(
                f"Claim {index}: you quoted the candidate on it, so it was discussed; pick "
                "supported, contradiction or unverified."
            )
            continue
        explanation = _clean(item.get("explanation"))[:600]
        follow_up = _clean(item.get("follow_up"))[:300]
        wording = f"{explanation} {follow_up}"
        if accusing_words(wording) or verdict_words(wording) or protected_topics(wording):
            problems.append(
                f"Claim {index}: describe what was said neutrally, as something to follow up; "
                "never accuse, judge the person or recommend a decision."
            )
            continue
        if gendered_words(explanation):
            problems.append(f'Claim {index}: call the person "the candidate", not he or she.')
            continue
        if status != "supported" and (not follow_up or not follow_up.endswith("?")):
            follow_up = _template_follow_up(claims[index])
        out[str(index)] = {
            "status": status,
            "evidence": evidence,
            "explanation": explanation,
            "follow_up": follow_up if status != "supported" else "",
        }
    return out, problems


def settle(
    claims: list[dict[str, Any]], verdicts: dict[str, dict[str, Any]]
) -> list[dict[str, Any]]:
    # one row per claim; a claim the ai never gave a usable verdict for says so, it is not
    # silently counted as anything
    out = []
    for index, claim in enumerate(claims):
        v = verdicts.get(str(index))
        if v is None:
            v = {
                "status": "not_checked",
                "evidence": [],
                "explanation": "This claim couldn't be checked against the transcript.",
                "follow_up": _template_follow_up(claim),
            }
        out.append({**claim, **v})
    return out
