# deterministic parts of the interview analysis: the plain question-answer split used when
# the model's isn't usable, checking the model's split and its evaluations against the
# transcript, and working out requirement coverage

import re
from typing import Any

from app.agents.copilot.checks import protected_topics, same_question
from app.agents.screening.checks import quote_in_text

SCORE_FIELDS = ("relevance", "accuracy", "completeness", "articulation")
COVERAGE_RANK = {"covered": 2, "partially_covered": 1}
ACCURACY_BASES = ("reference", "general", "not_applicable")
MAX_FOLLOW_UPS = 5

_TURN = re.compile(r"\[T(\d+)\]")
_PASSAGE = re.compile(r"\[S(\d+)\]")


def exchanges_as_pairs(turns: list[dict[str, Any]]) -> list[dict[str, Any]]:
    # interviewer lines in a row are the question, the candidate lines after them the answer
    pairs: list[dict[str, Any]] = []
    for t in turns:
        if t["speaker_role"] == "interviewer":
            if pairs and not pairs[-1]["answer_turn_ids"]:
                pairs[-1]["question_turn_ids"].append(t["id"])
                pairs[-1]["question"] += f" {t['text']}"
            else:
                pairs.append(
                    {
                        "question": t["text"],
                        "question_turn_ids": [t["id"]],
                        "answer_turn_ids": [],
                        "follow_up_of": None,
                    }
                )
        elif pairs:
            pairs[-1]["answer_turn_ids"].append(t["id"])
    return pairs


def check_segments(
    raw: list[dict[str, Any]], turns: list[dict[str, Any]]
) -> tuple[list[dict[str, Any]], list[str], list[str]]:
    # returns (pairs, problems to send back, warnings). a question must be copied from the
    # interviewer's lines and an answer must be the candidate's, said after the question
    by_id = {t["id"]: t for t in turns}
    order = {t["id"]: i for i, t in enumerate(turns)}
    pairs: list[dict[str, Any]] = []
    problems: list[str] = []
    warnings: list[str] = []
    for index, item in enumerate(raw):
        q_ids = [i for i in item.get("question_turn_ids") or [] if i in by_id]
        a_ids = [i for i in item.get("answer_turn_ids") or [] if i in by_id]
        if not q_ids or any(by_id[i]["speaker_role"] != "interviewer" for i in q_ids):
            problems.append(f"Pair {index}: question_turn_ids must be existing interviewer lines.")
            continue
        if any(by_id[i]["speaker_role"] != "candidate" for i in a_ids):
            problems.append(f"Pair {index}: answer_turn_ids must be candidate lines only.")
            continue
        if a_ids and min(order[i] for i in a_ids) < min(order[i] for i in q_ids):
            problems.append(f"Pair {index}: the answer can't come before the question.")
            continue
        asked = " ".join(by_id[i]["text"] for i in sorted(q_ids, key=order.get))
        question = " ".join((item.get("question") or "").split())
        if not question or not quote_in_text(question, asked):
            # the words have to be the interviewer's; fall back to the whole line
            warnings.append(
                "A question was reworded by the AI; the interviewer's own words are used."
            )
            question = asked
        follow = item.get("follow_up_of")
        pairs.append(
            {
                "question": question,
                "question_turn_ids": sorted(set(q_ids), key=order.get),
                "answer_turn_ids": sorted(set(a_ids), key=order.get),
                # only an earlier pair can be followed up; the index is into the kept pairs
                "follow_up_of": follow
                if isinstance(follow, int) and 0 <= follow < len(pairs)
                else None,
            }
        )
    if not pairs and raw:
        problems.append("No pair passed the checks.")
    return pairs, problems, sorted(set(warnings))


def answer_text(pair: dict[str, Any], turns: list[dict[str, Any]]) -> str:
    by_id = {t["id"]: t["text"] for t in turns}
    return " ".join(by_id[i] for i in pair["answer_turn_ids"] if i in by_id)


def _snap(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(round(max(0.0, min(100.0, float(value))) / 5) * 5)
    except (TypeError, ValueError):
        return None


def check_evaluations(
    raw: list[dict[str, Any]],
    pairs: list[dict[str, Any]],
    requirements: list[dict[str, Any]],
    passages: list[dict[str, Any]],
) -> tuple[dict[str, dict[str, Any]], list[str]]:
    # keyed by pair index (as str, checkpoints are json). citations that point outside the
    # pair or at passages that weren't given are stripped; a justification left with no
    # transcript citation is sent back
    req_ids = {r["id"] for r in requirements}
    labels = {f"S{i}" for i in range(1, len(passages) + 1)}
    out: dict[str, dict[str, Any]] = {}
    problems: list[str] = []
    for item in raw:
        index = item.get("pair")
        if not isinstance(index, int) or not 0 <= index < len(pairs) or str(index) in out:
            problems.append(f"Evaluation for pair {index} doesn't match a pair, or repeats one.")
            continue
        pair = pairs[index]
        own = set(pair["question_turn_ids"]) | set(pair["answer_turn_ids"])

        def turn(m: re.Match, own: set[int] = own) -> str:
            return m.group(0) if int(m.group(1)) in own else ""

        def passage(m: re.Match) -> str:
            return m.group(0) if f"S{m.group(1)}" in labels else ""

        justification = _PASSAGE.sub(passage, _TURN.sub(turn, item.get("justification") or ""))
        justification = re.sub(r"[ \t]+([.,;:])", r"\1", " ".join(justification.split()))
        cited = {int(t) for t in _TURN.findall(justification)}
        if pair["answer_turn_ids"] and not cited:
            problems.append(
                f"Pair {index}: the justification must cite the answer's lines, e.g. "
                f"[T{pair['answer_turn_ids'][0]}]."
            )
            continue
        basis = item.get("accuracy_basis")
        if basis not in ACCURACY_BASES:
            basis = "general"
        if basis == "reference" and not _PASSAGE.search(justification):
            # claimed a reference but cites none that exists
            basis = "general"
        scores = {f: _snap(item.get(f)) for f in SCORE_FIELDS}
        if basis == "not_applicable":
            scores["accuracy"] = None
        links = []
        for link in item.get("requirements") or []:
            rid, coverage = link.get("requirement_id"), link.get("coverage")
            if rid in req_ids and coverage in COVERAGE_RANK:
                links.append(
                    {
                        "requirement_id": rid,
                        "coverage": coverage,
                        "note": (link.get("note") or "")[:500],
                    }
                )
            elif rid not in req_ids:
                problems.append(
                    f"Pair {index}: requirement {rid} doesn't exist; use the ids given."
                )
        if not pair["answer_turn_ids"]:
            # nothing was said in reply, so there is nothing to judge
            scores = dict.fromkeys(SCORE_FIELDS)
        out[str(index)] = {
            **scores,
            "accuracy_basis": basis,
            "justification": justification,
            "requirements": links,
            "cited_turn_ids": sorted(cited),
        }
    return out, problems


def coverage(
    requirements: list[dict[str, Any]], evaluations: dict[str, dict[str, Any]]
) -> list[dict[str, Any]]:
    # the best coverage any pair gave each requirement; none at all means never explored
    out = []
    for r in requirements:
        best, pair_ids = None, []
        for index, ev in sorted(evaluations.items(), key=lambda kv: int(kv[0])):
            for link in ev["requirements"]:
                if link["requirement_id"] == r["id"]:
                    pair_ids.append(int(index))
                    if best is None or COVERAGE_RANK[link["coverage"]] > COVERAGE_RANK[best]:
                        best = link["coverage"]
        out.append(
            {
                "requirement_id": r["id"],
                "status": best or "not_explored",
                "pairs": sorted(set(pair_ids)),
            }
        )
    return out


def check_follow_ups(
    raw: list[dict[str, Any]],
    covered: list[dict[str, Any]],
    asked: list[str],
) -> list[dict[str, Any]]:
    # follow-ups are only for requirements not fully covered, never about personal topics
    open_ids = {c["requirement_id"] for c in covered if c["status"] != "covered"}
    kept: list[dict[str, Any]] = []
    for item in raw:
        question = " ".join((item.get("question") or "").split())
        rid = item.get("requirement_id")
        if (
            rid not in open_ids
            or not question
            or protected_topics(question)
            or any(same_question(question, q) for q in asked + [k["question"] for k in kept])
        ):
            continue
        kept.append({"requirement_id": rid, "question": question})
    return kept[:MAX_FOLLOW_UPS]
