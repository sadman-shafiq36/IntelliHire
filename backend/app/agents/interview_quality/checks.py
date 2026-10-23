# deterministic parts of the interview quality review: speaking time, overlaps, coverage,
# questions that gave little to go on, consistency with the job's other interviews, the
# issues these raise, checking the ai's review, and the sub-scores

import re
from statistics import mean
from typing import Any

from app.agents.assessment.checks import verdict_words
from app.agents.copilot.checks import gendered_words, protected_topics

SIGNALS = {"high": 100, "medium": 60, "low": 20}
STYLES = ("open", "closed", "leading", "multi_part", "unclear")
# share of speaking time the candidate should have in a structured interview
BALANCE = (60, 85)
# a line's end time includes the pause that closed it, so overlaps allow for that pause
OVERLAP_MARGIN_MS = 300
SHORT_ANSWER_WORDS = 12
LOW_RELEVANCE = 50
MAX_PER_KIND = 5
MAX_RECOMMENDATIONS = 8

_TURN = re.compile(r"\[T(\d+)\]")


def _words(text: str) -> int:
    return len(text.split())


def measure(
    turns: list[dict[str, Any]],
    pairs: list[dict[str, Any]],
    requirements: list[dict[str, Any]],
    peers: list[dict[str, Any]],
    pause_ms: int = 0,
) -> dict[str, Any]:
    # pause_ms is the silence that ends a line: inside its end time, but not speech
    talk = {"interviewer": 0, "candidate": 0}
    longest = 0
    for t in turns:
        ms = max(0, t["end_offset_ms"] - t["start_offset_ms"] - pause_ms)
        talk[t["speaker_role"]] = talk.get(t["speaker_role"], 0) + ms
        if t["speaker_role"] == "interviewer":
            longest = max(longest, ms)
    total = talk["interviewer"] + talk["candidate"]
    ordered = sorted(turns, key=lambda t: t["start_offset_ms"])
    overlaps = sum(
        1
        for a, b in zip(ordered, ordered[1:], strict=False)
        if a["speaker_role"] != b["speaker_role"]
        and b["start_offset_ms"] < a["end_offset_ms"] - pause_ms - OVERLAP_MARGIN_MS
    )

    text = {t["id"]: t["text"] for t in turns}
    answers = [" ".join(text.get(i, "") for i in p["answer_turn_ids"]) for p in pairs]
    low_signal = []
    for index, (p, answer) in enumerate(zip(pairs, answers, strict=True)):
        reasons = []
        if not p["answer_turn_ids"]:
            reasons.append("not answered")
        elif _words(answer) < SHORT_ANSWER_WORDS:
            reasons.append("very short answer")
        if p.get("relevance") is not None and p["relevance"] < LOW_RELEVANCE:
            reasons.append("answer off the question")
        if not p.get("requirements"):
            reasons.append("touched no requirement")
        if reasons:
            low_signal.append({"pair": index, "reasons": reasons})

    explored = {link["requirement_id"] for p in pairs for link in p.get("requirements") or []}
    weight = sum(r["weight"] for r in requirements)
    coverage = (
        round(100 * sum(r["weight"] for r in requirements if r["id"] in explored) / weight, 1)
        if weight
        else None
    )
    req_ids = [r["id"] for r in requirements]
    similarity = []
    for peer in peers:
        theirs = set(peer["explored"]) & set(req_ids)
        union = explored | theirs
        similarity.append(len(explored & theirs) / len(union) if union else 1.0)
    peer_share = {
        rid: round(100 * sum(1 for p in peers if rid in p["explored"]) / len(peers))
        for rid in req_ids
    } if peers else {}  # fmt: skip

    return {
        "speaking_ms": talk,
        "candidate_share": round(100 * talk["candidate"] / total) if total else None,
        "longest_interviewer_turn_s": round(longest / 1000),
        "overlaps": overlaps,
        "questions": len(pairs),
        "follow_ups": sum(1 for p in pairs if p.get("follow_up_of") is not None),
        "unanswered": sum(1 for p in pairs if not p["answer_turn_ids"]),
        "average_answer_words": round(mean(_words(a) for a in answers)) if answers else 0,
        "explored": sorted(explored),
        "coverage": coverage,
        "must_haves": sum(1 for r in requirements if r["is_mandatory"]),
        "must_haves_explored": sum(
            1 for r in requirements if r["is_mandatory"] and r["id"] in explored
        ),
        "low_signal": low_signal,
        "peers": len(peers),
        "consistency": round(100 * mean(similarity), 1) if similarity else None,
        "peer_share": {str(k): v for k, v in peer_share.items()},
    }


def issues(
    metrics: dict[str, Any], pairs: list[dict[str, Any]], requirements: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    # each issue has an id the ai's recommendation must name, the evidence, and a plain
    # recommendation used when the ai's is missing or refused
    out: list[dict[str, Any]] = []
    share = metrics["candidate_share"]
    if share is not None and share < BALANCE[0]:
        # the direction is in the title: a model read "the candidate spoke 56%" as too much
        out.append({
            "id": "balance",
            "title": f"The interviewer did {100 - share}% of the talking",
            "evidence": f"The candidate spoke for only {share}% of the time (aim for "
                        f"{BALANCE[0]}-{BALANCE[1]}%). Interviewer "
                        f"{metrics['speaking_ms']['interviewer'] // 1000} s, candidate "
                        f"{metrics['speaking_ms']['candidate'] // 1000} s; longest interviewer "
                        f"turn {metrics['longest_interviewer_turn_s']} s.",
            "default": "Let the candidate do most of the talking: ask shorter questions and "
                       "leave room for longer answers.",
        })  # fmt: skip
    elif share is not None and share > BALANCE[1] and metrics["follow_ups"] == 0:
        out.append({
            "id": "balance",
            "title": f"The candidate did {share}% of the talking with no follow-up questions",
            "evidence": f"{metrics['questions']} questions, no follow-up questions.",
            "default": "Probe long answers with a follow-up instead of moving straight on.",
        })  # fmt: skip
    if metrics["overlaps"] >= 3:
        out.append({
            "id": "overlaps",
            "title": f"Speakers overlapped {metrics['overlaps']} times",
            "evidence": "A line started while the other speaker's line was still going, "
                        "allowing for the pause that ends a line. Sound from one person's "
                        "speakers reaching the other's microphone can also show as overlap.",
            "default": "Leave a pause after each answer before asking the next question.",
        })  # fmt: skip
    explored = set(metrics["explored"])
    unexplored = sorted(
        (r for r in requirements if r["id"] not in explored),
        key=lambda r: (not r["is_mandatory"], -r["weight"]),
    )
    for r in unexplored[:MAX_PER_KIND]:
        share_peers = metrics["peer_share"].get(str(r["id"]))
        peer_note = (
            f" It was explored in {share_peers}% of this job's other interviews."
            if share_peers
            else ""
        )
        out.append({
            "id": f"unexplored:{r['id']}",
            "title": f"Never explored: {r['text']}"
                     + (" (must-have)" if r["is_mandatory"] else ""),
            "evidence": f"No question touched this requirement.{peer_note}",
            "default": f"Plan a question on {r['text']} before the next interview.",
            "requirement_id": r["id"],
        })  # fmt: skip
    for item in metrics["low_signal"][:MAX_PER_KIND]:
        p = pairs[item["pair"]]
        out.append({
            "id": f"low_signal:{item['pair']}",
            "title": f"Little to go on from question {item['pair'] + 1}",
            "evidence": f'"{p["question"][:160]}": {", ".join(item["reasons"])}.',
            "default": "Rephrase it as an open question tied to a requirement, and follow up "
                       "on short answers.",
            "pair": item["pair"],
        })  # fmt: skip
    if metrics["consistency"] is not None and metrics["consistency"] < 50:
        out.append({
            "id": "consistency",
            "title": f"Different ground from this job's other interviews "
                     f"({metrics['consistency']}% overlap)",
            "evidence": f"Compared with {metrics['peers']} other analysed interview(s) for "
                        "the job, by the requirements each explored.",
            "default": "Use the same core questions for every candidate for this job so "
                       "their answers can be compared.",
        })  # fmt: skip
    if (
        metrics["follow_ups"] == 0
        and metrics["questions"] >= 4
        and not any(i["id"] == "balance" for i in out)
    ):
        out.append({
            "id": "follow_ups",
            "title": "No follow-up questions",
            "evidence": f"{metrics['questions']} questions, none following up an answer.",
            "default": "Follow up the most important answers to get past the first reply.",
        })  # fmt: skip
    return out


def check_review(
    raw: dict[str, Any], pairs: list[dict[str, Any]], issue_list: list[dict[str, Any]]
) -> tuple[dict[str, dict[str, Any]], list[dict[str, Any]], str, list[str]]:
    # returns question reviews (keyed by pair index as str), recommendations, the summary
    # and problems. citations outside the question's own lines are removed
    problems: list[str] = []
    questions: dict[str, dict[str, Any]] = {}
    for item in raw.get("questions") or []:
        index = item.get("question")
        if not isinstance(index, int) or not 0 <= index < len(pairs) or str(index) in questions:
            continue
        signal, style = item.get("signal"), item.get("style")
        if signal not in SIGNALS or style not in STYLES:
            continue
        own = set(pairs[index]["question_turn_ids"]) | set(pairs[index]["answer_turn_ids"])

        def turn(m: re.Match, own: set[int] = own) -> str:
            return m.group(0) if int(m.group(1)) in own else ""

        reason = " ".join(_TURN.sub(turn, item.get("reason") or "").split())[:500]
        better = " ".join((item.get("better_question") or "").split())[:300]
        if protected_topics(f"{reason} {better}") or verdict_words(reason):
            problems.append(f"Question {index}: review the question, not the candidate.")
            continue
        if gendered_words(reason):
            problems.append(f'Question {index}: say "the candidate" or "the interviewer".')
            continue
        questions[str(index)] = {
            "signal": signal,
            "style": style,
            "reason": reason,
            "better_question": better if signal != "high" and better.endswith("?") else "",
        }
    missing = [i for i in range(len(pairs)) if str(i) not in questions]
    if missing:
        problems.append(f"Review every question; missing: {', '.join(map(str, missing))}.")

    ids = {i["id"] for i in issue_list}
    written: dict[str, str] = {}
    for item in raw.get("recommendations") or []:
        issue, text = item.get("issue"), " ".join((item.get("text") or "").split())[:500]
        if issue not in ids or issue in written or not text:
            continue
        if protected_topics(text) or verdict_words(text) or gendered_words(text):
            problems.append(
                f"Recommendation for {issue}: advise on the interview only, and say "
                '"the candidate" or "the interviewer".'
            )
            continue
        written[issue] = text
    recommendations = [
        {
            "issue": i["id"],
            "title": i["title"],
            "evidence": i["evidence"],
            "text": written.get(i["id"]) or i["default"],
            "written_by": "ai" if i["id"] in written else "rule",
        }
        for i in issue_list
    ][:MAX_RECOMMENDATIONS]

    summary = " ".join((raw.get("summary") or "").split())[:800]
    if summary and (protected_topics(summary) or verdict_words(summary) or gendered_words(summary)):
        problems.append("The summary must describe the interview process, not judge anyone.")
        summary = ""
    return questions, recommendations, summary, problems


def scores(
    metrics: dict[str, Any], questions: dict[str, dict[str, Any]], pair_count: int
) -> dict[str, float | None]:
    if questions:
        effectiveness = round(mean(SIGNALS[q["signal"]] for q in questions.values()), 1)
    elif pair_count:
        # without the ai's review, the share of questions that gave something to go on
        effectiveness = round(100 * (1 - len(metrics["low_signal"]) / pair_count), 1)
    else:
        effectiveness = None
    share = metrics["candidate_share"]
    if share is None:
        balance = None
    else:
        distance = max(BALANCE[0] - share, share - BALANCE[1], 0)
        balance = float(max(0, 100 - 3 * distance))
    parts = {
        "question_effectiveness": effectiveness,
        "coverage": metrics["coverage"],
        "balance": balance,
        "consistency": metrics["consistency"],
    }
    present = [v for v in parts.values() if v is not None]
    return {**parts, "overall": round(mean(present), 1) if present else None}
