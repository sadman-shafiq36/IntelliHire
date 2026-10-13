# deterministic parts of screening: redaction, evidence verification, the must-have
# filter and the fulfilment score

import re
from typing import Any

POINTS = {"met": 1.0, "partially_met": 0.5, "unmet": 0.0}
MIN_QUOTE_CHARS = 3
# a quote may pick items out of a list ("Docker, AWS") or skip words ("Built ... in Django")
_QUOTE_PARTS = re.compile(r"\s*(?:\.\.\.|…|[;,])\s*")

_NOT_WORD = re.compile(r"[^a-z0-9+#.]+")


def redact(text: str, identity: dict[str, Any]) -> str:
    # removes the person's name, email and phone so the matcher judges the work only
    out = text
    email = (identity.get("email") or "").strip()
    if email:
        out = re.sub(re.escape(email), "[email]", out, flags=re.I)
    out = re.sub(r"[\w.+-]+@[\w-]+\.[\w.-]+", "[email]", out)
    phone = re.sub(r"\D", "", identity.get("phone") or "")
    if len(phone) >= 6:
        out = re.sub(
            r"\+?[\d][\d\s().-]{5,}\d",
            lambda m: "[phone]" if phone[-6:] in re.sub(r"\D", "", m.group(0)) else m.group(0),
            out,
        )
    for part in (identity.get("full_name") or "").split():
        if len(part) >= 2:
            out = re.sub(rf"\b{re.escape(part)}\b", "[name]", out, flags=re.I)
    return out


# a dot that isn't inside a word ends a sentence; "node.js" keeps its dot
_LOOSE_DOT = re.compile(r"(?<![a-z0-9])\.|\.(?![a-z0-9])")


def normalise(text: str) -> str:
    out = _NOT_WORD.sub(" ", text.lower())
    return " ".join(_LOOSE_DOT.sub(" ", out).split())


def quote_in_text(quote: str, text: str) -> bool:
    # every part must appear in the cv as whole words, so "go" can't match inside "google"
    parts = [normalise(p) for p in _QUOTE_PARTS.split(quote)]
    parts = [p for p in parts if p]
    if not parts or sum(len(p) for p in parts) < MIN_QUOTE_CHARS:
        return False
    haystack = f" {normalise(text)} "
    return all(f" {p} " in haystack for p in parts)


def verify(
    matches: list[dict[str, Any]], requirements: list[dict[str, Any]], text: str
) -> tuple[list[dict[str, Any]], list[str]]:
    # returns the matches with evidence_verified set, plus problems phrased as fixes
    problems: list[str] = []
    wanted = {r["id"]: r for r in requirements}
    seen: dict[int, dict[str, Any]] = {}
    for m in matches:
        rid = m.get("requirement_id")
        if rid not in wanted:
            problems.append(f"Requirement id {rid} does not exist; use only the ids given.")
            continue
        if rid in seen:
            problems.append(f"Requirement {rid} was answered twice; answer it once.")
            continue
        checked = {**m, "confidence": max(0.0, min(1.0, float(m.get("confidence") or 0)))}
        quote = (m.get("evidence_quote") or "").strip()
        if m["status"] in ("met", "partially_met"):
            checked["evidence_verified"] = quote_in_text(quote, text)
            if not checked["evidence_verified"]:
                problems.append(
                    f'Requirement {rid} ("{wanted[rid]["text"]}"): the evidence_quote is not '
                    "copied word for word from the CV. Quote the CV exactly, or mark it unmet."
                )
        else:
            checked["evidence_quote"] = ""
            checked["evidence_verified"] = False
        seen[rid] = checked
    for rid, req in wanted.items():
        if rid not in seen:
            problems.append(f'Requirement {rid} ("{req["text"]}") has no answer.')
    return [seen[rid] for rid in wanted if rid in seen], problems


def settle(
    matches: list[dict[str, Any]], requirements: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    # after the last attempt: anything still without verified evidence counts as unmet,
    # and missing answers become unmet, so nothing unproven raises a score
    by_id = {m["requirement_id"]: m for m in matches}
    settled = []
    for req in requirements:
        m = by_id.get(req["id"])
        if m is None:
            m = {
                "requirement_id": req["id"],
                "status": "unmet",
                "evidence_quote": "",
                "reasoning": "The screening did not return an answer for this requirement.",
                "confidence": 0.0,
                "evidence_verified": False,
            }
        elif m["status"] != "unmet" and not m.get("evidence_verified"):
            m = {
                **m,
                "status": "unmet",
                "reasoning": "The quoted evidence could not be found in the CV, so it was not "
                "counted. " + (m.get("reasoning") or ""),
                "confidence": 0.0,
            }
        settled.append(m)
    return settled


def mandatory_gaps(matches: list[dict[str, Any]], requirements: list[dict[str, Any]]) -> list[str]:
    # a must-have that is unmet excludes the candidate; partially met does not
    status = {m["requirement_id"]: m["status"] for m in matches}
    return [r["text"] for r in requirements if r["is_mandatory"] and status.get(r["id"]) == "unmet"]


def fulfilment(matches: list[dict[str, Any]], requirements: list[dict[str, Any]]) -> float:
    status = {m["requirement_id"]: m["status"] for m in matches}
    total = sum(r["weight"] for r in requirements) or 1.0
    got = sum(r["weight"] * POINTS[status.get(r["id"], "unmet")] for r in requirements)
    return round(100.0 * got / total, 1)
