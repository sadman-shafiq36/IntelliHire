from typing import Any

SECTION_FOR = {
    "what_we_offer": "What we offer",
    "about_company": "About us",
    "equal_opportunity": "Equal opportunity",
}


def render_markdown(draft: dict[str, Any], profile: dict[str, Any]) -> str:
    facts = [profile.get("department"), profile.get("location"), profile.get("employment_type")]
    lines = [f"# {profile['title']}", ""]
    if any(facts):
        lines += [" · ".join(f for f in facts if f), ""]

    lines += ["## About the role", "", draft["summary"].strip(), ""]
    lines += _bullets("What you'll do", draft["responsibilities"])
    lines += _bullets("What you'll need", [line["text"] for line in draft["required"]])
    lines += _bullets("Nice to have", [line["text"] for line in draft["nice_to_have"]])
    lines += _bullets("What we offer", [line["text"] for line in draft["what_we_offer"]])
    for section in draft.get("additional_sections") or []:
        if section["heading"].strip() and section["body"].strip():
            lines += [f"## {section['heading'].strip()}", "", section["body"].strip(), ""]
    if draft["about_company"]["text"].strip():
        lines += ["## About us", "", draft["about_company"]["text"].strip(), ""]
    if draft["equal_opportunity"]["text"].strip():
        lines += ["## Equal opportunity", "", draft["equal_opportunity"]["text"].strip(), ""]
    return "\n".join(lines).strip() + "\n"


def _bullets(heading: str, items: list[str]) -> list[str]:
    items = [i.strip() for i in items if i.strip()]
    if not items:
        return []
    return [f"## {heading}", "", *[f"- {item}" for item in items], ""]


def collect_citations(draft: dict[str, Any], chunks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    # one entry per cited passage, with the sections that used it
    by_id = {c["chunk_id"]: c for c in chunks}
    used_for: dict[str, list[str]] = {}

    def note(source_ids: list[str], section: str) -> None:
        for source_id in source_ids:
            sections = used_for.setdefault(source_id, [])
            if section not in sections:
                sections.append(section)

    for line in draft["what_we_offer"]:
        note(line["source_ids"], SECTION_FOR["what_we_offer"])
    note(draft["about_company"]["source_ids"], SECTION_FOR["about_company"])
    note(draft["equal_opportunity"]["source_ids"], SECTION_FOR["equal_opportunity"])

    return [
        {
            "chunk_id": chunk_id,
            "policy_id": by_id[chunk_id]["policy_id"],
            "policy_title": by_id[chunk_id]["policy_title"],
            "excerpt": by_id[chunk_id]["text"],
            "used_for": sections,
        }
        for chunk_id, sections in used_for.items()
        if chunk_id in by_id
    ]
