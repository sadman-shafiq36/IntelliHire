# deterministic parts of the co-pilot: which requirements to probe next, and checking
# what the model suggested before an interviewer sees it

import math
import re
from collections.abc import Callable
from typing import Any

from app.agents.screening.checks import normalise, quote_in_text

MAX_TARGETS = 4
SUGGESTION_COUNT = 3
MAX_QUESTION_CHARS = 300
# token overlap above which two questions count as the same question
DUPLICATE_OVERLAP = 0.7
# an exchange is one interviewer turn and the candidate turns after it
MAX_EXCHANGE_CHARS = 1200

STATUS_POINTS = {"unmet": 3, "partially_met": 2, "met": 0}

# questions an interviewer must not ask, whatever the model thinks of them. phrased so
# technical words stay allowed: race condition, child nodes, cors origin, max-age
_PROTECTED_RE = re.compile(
    r"\b(how old|your age|years old|date of birth|birthday|where were you born|married|"
    r"marital|spouse|husband|wife|your children|have children|kids|pregnan\w*|religio\w*|"
    r"church|mosque|temple|pray\w*|nationality|citizenship|ethnicity|ethnic|racial|"
    r"where are you from|country of origin|national origin|gender|sexual\w*|disabilit\w*|"
    r"health condition|illness)\b",
    re.I,
)

Embedder = Callable[[list[str]], list[list[float]]]


def exchanges(turns: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for t in turns:
        if t["speaker_role"] == "interviewer" or not out:
            out.append({"turn_id": t["id"], "text": t["text"]})
        else:
            out[-1]["text"] = f"{out[-1]['text']} {t['text']}"
    for e in out:
        e["text"] = e["text"][:MAX_EXCHANGE_CHARS]
    return out


def cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=False))
    norm = math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b))
    return dot / norm if norm else 0.0


def discussed(
    requirements: list[dict[str, Any]],
    talk: list[dict[str, Any]],
    embedder: Embedder,
    threshold: float,
) -> dict[int, int | None]:
    # requirement id -> the turn that opened the exchange closest to it, if close enough.
    # one embed call for requirements and exchanges together
    if not talk:
        return {r["id"]: None for r in requirements}
    vectors = embedder([r["text"] for r in requirements] + [e["text"] for e in talk])
    req_vectors, talk_vectors = vectors[: len(requirements)], vectors[len(requirements) :]
    found: dict[int, int | None] = {}
    for req, rv in zip(requirements, req_vectors, strict=True):
        best, best_turn = 0.0, None
        for exchange, tv in zip(talk, talk_vectors, strict=True):
            score = cosine(rv, tv)
            if score > best:
                best, best_turn = score, exchange["turn_id"]
        found[req["id"]] = best_turn if best >= threshold else None
    return found


def plan_targets(
    requirements: list[dict[str, Any]],
    screening: dict[str, dict[str, Any]],
    talked: dict[int, int | None] | None,
) -> list[dict[str, Any]]:
    # gaps first: unmet before partly met before met, must-haves before nice-to-haves,
    # not yet discussed before discussed. talked is None when it couldn't be worked out
    ranked = []
    for order, req in enumerate(requirements):
        result = screening.get(str(req["id"])) or {}
        status = result.get("status")
        turn_id = talked.get(req["id"]) if talked is not None else None
        is_discussed = None if talked is None else turn_id is not None
        priority = STATUS_POINTS.get(status, 2) + int(req["is_mandatory"])
        priority += {False: 2, None: 1, True: 0}[is_discussed]
        ranked.append(
            (
                -priority,
                order,
                {
                    "requirement_id": req["id"],
                    "text": req["text"],
                    "category": req["category"],
                    "must_have": req["is_mandatory"],
                    "screening": status,
                    "cv_quote": result.get("evidence_quote") or None,
                    "discussed": is_discussed,
                    "discussed_turn_id": turn_id,
                },
            )
        )
    ranked.sort(key=lambda r: (r[0], r[1]))
    return [r[2] for r in ranked[:MAX_TARGETS]]


def protected_topics(text: str) -> list[str]:
    return sorted({m.group(1).lower() for m in _PROTECTED_RE.finditer(text)})


def _tokens(text: str) -> set[str]:
    return {t for t in normalise(text).split() if len(t) > 2}


def same_question(a: str, b: str) -> bool:
    ta, tb = _tokens(a), _tokens(b)
    if not ta or not tb:
        return False
    return len(ta & tb) / len(ta | tb) >= DUPLICATE_OVERLAP


def verify_suggestions(
    answers: list[dict[str, Any]],
    targets: list[dict[str, Any]],
    cv_text: str,
    turns: list[dict[str, Any]],
    passages: list[dict[str, Any]],
    already_asked: list[str],
) -> tuple[list[dict[str, Any]], list[str]]:
    # returns the suggestions that pass, cleaned, and problems phrased for a retry.
    # evidence or answer points that can't be traced are stripped, not sent back
    target_ids = {t["requirement_id"] for t in targets}
    turn_text = {t["id"]: t["text"] for t in turns}
    passage_ids = {p["chunk_id"] for p in passages}
    kept: list[dict[str, Any]] = []
    problems: list[str] = []
    for answer in answers:
        question = " ".join((answer.get("question") or "").split())
        req_id = answer.get("requirement_id")
        if req_id not in target_ids:
            problems.append(
                f"Requirement {req_id} is not one of the requirements to probe. Use only those ids."
            )
            continue
        if not question or len(question) > MAX_QUESTION_CHARS:
            problems.append(
                f"Each question must be one short question under {MAX_QUESTION_CHARS} characters."
            )
            continue
        topics = protected_topics(f"{question} {answer.get('why') or ''}")
        if topics:
            problems.append(
                f"Never ask about personal characteristics ({', '.join(topics)}). "
                "Ask only about the job's requirements."
            )
            continue
        if any(same_question(question, q) for q in already_asked + [k["question"] for k in kept]):
            problems.append(f'"{question}" repeats a question already asked or suggested.')
            continue

        evidence = []
        for item in answer.get("evidence") or []:
            quote = (item.get("quote") or "").strip()
            if item.get("source") == "cv" and quote_in_text(quote, cv_text):
                evidence.append({"source": "cv", "quote": quote, "turn_id": None})
            elif item.get("source") == "transcript" and quote_in_text(
                quote, turn_text.get(item.get("turn_id"), "")
            ):
                evidence.append(
                    {"source": "transcript", "quote": quote, "turn_id": item["turn_id"]}
                )
        points = []
        for point in answer.get("answer_points") or []:
            ids = [i for i in point.get("passage_ids") or [] if i in passage_ids]
            text = (point.get("point") or "").strip()
            if text and ids:
                points.append({"point": text, "passage_ids": list(dict.fromkeys(ids))})
        kept.append(
            {
                "requirement_id": req_id,
                "question": question,
                "why": (answer.get("why") or "").strip(),
                "evidence": evidence,
                "answer_points": points,
            }
        )
    return kept[:SUGGESTION_COUNT], problems
