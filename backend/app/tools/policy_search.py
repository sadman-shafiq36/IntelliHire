# retrieval over company policy chunks. search_company_policy is what graph nodes
# call; policy_search_tool wraps it for agents that pick their own tools.
# the same search runs over interview reference material when given that store

from langchain_core.tools import tool

from app.config import settings
from app.rag.policy_store import PolicyChunk, PolicyStore, get_policy_store
from app.services import ai


def search_company_policy(
    query: str, k: int = 4, min_score: float | None = None, store: PolicyStore | None = None
) -> list[PolicyChunk]:
    query = query.strip()
    if not query:
        return []
    threshold = settings.policy_min_score if min_score is None else min_score
    embedding = ai.embed_texts([query], for_query=True)[0]
    hits = (store or get_policy_store()).query(embedding, k)
    return [hit for hit in hits if hit.score >= threshold]


def search_many(
    queries: list[str],
    k_per_query: int = 3,
    limit: int = 8,
    store: PolicyStore | None = None,
) -> list[PolicyChunk]:
    # one embed call for all queries, best score wins when a chunk comes back twice
    queries = [q.strip() for q in queries if q.strip()]
    if not queries:
        return []
    embeddings = ai.embed_texts(queries, for_query=True)
    store = store or get_policy_store()
    best: dict[str, PolicyChunk] = {}
    for embedding in embeddings:
        for hit in store.query(embedding, k_per_query):
            if hit.score < settings.policy_min_score:
                continue
            if hit.chunk_id not in best or hit.score > best[hit.chunk_id].score:
                best[hit.chunk_id] = hit
    ranked = sorted(best.values(), key=lambda h: h.score, reverse=True)
    return ranked[:limit]


@tool
def policy_search_tool(query: str) -> str:
    """Search the company's hiring and HR policy documents.

    Use this for benefits, leave, working hours, remote work, equal opportunity
    wording or any rule a job description has to follow. Returns matching
    passages, each tagged with its source id.
    """
    hits = search_company_policy(query)
    if not hits:
        return "No relevant company policy found."
    return "\n\n".join(f"[{hit.chunk_id}] ({hit.policy_title})\n{hit.text}" for hit in hits)
