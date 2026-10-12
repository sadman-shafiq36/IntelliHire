# deterministic checks on a draft. returns the cleaned draft plus a list of problems
# phrased as instructions, which go back to the drafter on the next attempt

import re
from typing import Any

# coded or exclusionary wording, matched as whole words
BIASED_TERMS = [
    "young",
    "youthful",
    "energetic",
    "digital native",
    "recent graduate",
    "rockstar",
    "rock star",
    "ninja",
    "guru",
    "native speaker",
    "native english",
    "he",
    "she",
    "his",
    "her",
    "manpower",
    "salesman",
    "chairman",
    "able-bodied",
    "unmarried",
    "married",
]
_BIASED_RE = re.compile(r"\b(" + "|".join(re.escape(t) for t in BIASED_TERMS) + r")\b", re.I)
_AGE_RE = re.compile(r"\b(aged?|age)\s*(between|under|over|below|above)?\s*\d{2}\b", re.I)


def review_draft(
    draft: dict[str, Any],
    profile: dict[str, Any],
    chunk_ids: set[str],
    allow_extra_sections: bool = False,
) -> tuple[dict[str, Any], list[str]]:
    problems: list[str] = []
    # extra sections carry no sources, so they're only for things the interviewer asked
    # for in chat. otherwise company facts could skip the citation check through them
    if draft.get("additional_sections") and not allow_extra_sections:
        problems.append(
            "Remove additional_sections. Put working arrangements and benefits under "
            "what_we_offer with their sources."
        )
    draft = _drop_unknown_sources(draft, chunk_ids, problems)

    requirements = {r["id"]: r for r in profile["requirements"]}
    required_ids = {i for line in draft["required"] for i in line["requirement_ids"]}
    nice_ids = {i for line in draft["nice_to_have"] for i in line["requirement_ids"]}

    for req_id, requirement in requirements.items():
        if requirement["is_mandatory"] and req_id not in required_ids:
            problems.append(
                f'Must-have requirement {req_id} ("{requirement["text"]}") is missing from '
                '"required".'
            )
        elif req_id not in required_ids | nice_ids:
            problems.append(f'Requirement {req_id} ("{requirement["text"]}") is not covered.')

    unknown_reqs = (required_ids | nice_ids) - requirements.keys()
    if unknown_reqs:
        problems.append(f"Requirement ids {sorted(unknown_reqs)} do not exist in the profile.")

    for line in draft["what_we_offer"]:
        if not line["source_ids"]:
            problems.append(
                f'Benefit "{line["text"]}" has no supporting policy passage. Remove it or cite one.'
            )

    if chunk_ids and not draft["equal_opportunity"]["source_ids"]:
        problems.append("The equal opportunity statement must cite the policy it comes from.")

    for term in sorted(_biased_terms(draft)):
        problems.append(f'Remove the biased or coded wording "{term}".')

    if not draft["responsibilities"]:
        problems.append("Add the main responsibilities of the role.")

    return draft, problems


def _drop_unknown_sources(
    draft: dict[str, Any], chunk_ids: set[str], problems: list[str]
) -> dict[str, Any]:
    # a cited id that wasn't retrieved is made up, strip it so it never reaches the ui
    invented: set[str] = set()

    def clean(line: dict[str, Any]) -> dict[str, Any]:
        kept = [s for s in line["source_ids"] if s in chunk_ids]
        invented.update(s for s in line["source_ids"] if s not in chunk_ids)
        return {**line, "source_ids": kept}

    cleaned = {
        **draft,
        "what_we_offer": [clean(line) for line in draft["what_we_offer"]],
        "about_company": clean(draft["about_company"]),
        "equal_opportunity": clean(draft["equal_opportunity"]),
    }
    if invented:
        problems.append(
            f"Source ids {sorted(invented)} were not among the passages provided. "
            "Cite only the ids given."
        )
    return cleaned


def _biased_terms(draft: dict[str, Any]) -> set[str]:
    return biased_terms_in(_all_text(draft))


def biased_terms_in(texts: list[str]) -> set[str]:
    found: set[str] = set()
    for text in texts:
        found.update(m.group(1).lower() for m in _BIASED_RE.finditer(text))
        found.update(m.group(0).lower() for m in _AGE_RE.finditer(text))
    return found


def _all_text(draft: dict[str, Any]) -> list[str]:
    texts = [draft["summary"], *draft["responsibilities"]]
    texts += [line["text"] for line in draft["required"] + draft["nice_to_have"]]
    texts += [line["text"] for line in draft["what_we_offer"]]
    texts += [draft["about_company"]["text"], draft["equal_opportunity"]["text"]]
    for section in draft.get("additional_sections") or []:
        texts += [section["heading"], section["body"]]
    return texts
