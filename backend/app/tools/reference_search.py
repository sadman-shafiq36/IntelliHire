# retrieval over the interview reference material the hiring team uploaded. the
# co-pilot's reference answers may only cite passages found here

from app.rag.policy_store import PolicyChunk, get_reference_store
from app.tools.policy_search import search_many


def search_references(
    queries: list[str], k_per_query: int = 3, limit: int = 6
) -> list[PolicyChunk]:
    return search_many(queries, k_per_query, limit, store=get_reference_store())
