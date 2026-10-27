# AI Engineer interview answer key

Reference answers for interviewing AI engineer candidates. Each topic gives a question, what a strong answer covers, signs of a weak answer and a follow-up. Requirements covered: Python, FastAPI, large language models (LLMs), prompt engineering, vector databases, retrieval-augmented generation (RAG) and LangChain.

## Python: async code and concurrency
**Question:** When would you use asyncio, threads or processes in a Python service that calls an LLM API?
**A strong answer covers:**
- LLM and HTTP calls are I/O-bound, so asyncio (or threads) lets one process wait on many calls at once.
- The GIL stops threads running Python bytecode in parallel, so CPU-bound work such as parsing large PDFs or local embedding goes to a process pool or a separate worker.
- A blocking call inside an async function stalls the whole event loop; it needs an async client or run_in_executor / asyncio.to_thread.
- Limiting concurrency with a semaphore to stay inside provider rate limits.
**Weak signs:** says threads always make Python faster; does not know what blocks the event loop.
**Follow-up:** How would you run 200 LLM calls without hitting a 429 rate-limit error?

## Python: typing, Pydantic and code quality
**Question:** How do you keep a Python AI codebase reliable as it grows?
**A strong answer covers:**
- Type hints checked by mypy or pyright, and Pydantic models for anything crossing a boundary (API input, LLM output, config).
- Validating LLM output against a Pydantic schema and handling the failure instead of trusting the text.
- pytest with the LLM replaced by a fake or recorded response, so tests are fast and deterministic.
- Pinned dependency versions, a linter and formatter (ruff, black) and CI that runs all of it.
**Weak signs:** tests only by calling the real model by hand; no answer for malformed model output.
**Follow-up:** How would you unit-test a function whose result depends on an LLM?

## FastAPI: building an LLM service
**Question:** Walk through how you would build a FastAPI endpoint that answers questions with an LLM.
**A strong answer covers:**
- A Pydantic request and response model so FastAPI validates input and documents the API (OpenAPI).
- async def endpoints with an async HTTP or SDK client, so the worker is not blocked while the model answers.
- Dependencies (Depends) for authentication, database sessions and shared clients, created once at startup (lifespan) instead of per request.
- Clear error handling: timeouts, retries with backoff and a user-readable error instead of a stack trace.
- Long jobs such as document indexing go to a background queue (Celery, RQ, arq) with a status endpoint, not inside the request.
**Weak signs:** creates a new client on every request; runs ten-minute jobs inside the HTTP request.
**Follow-up:** How would you stream the answer to the browser as it is generated?

## FastAPI: streaming responses
**Question:** How do you stream LLM tokens from FastAPI to a web client?
**A strong answer covers:**
- StreamingResponse with an async generator, often as server-sent events (text/event-stream).
- Reading the provider's stream (stream=True) and forwarding chunks as they arrive.
- Handling client disconnects so the model call is cancelled and does not keep costing tokens.
- Checking or post-processing the complete answer once the stream ends (citations, moderation) before it is saved.
- Proxies and load balancers may buffer; disabling buffering or sending heartbeats.
**Weak signs:** only knows returning the full answer at once; no idea how disconnects are handled.
**Follow-up:** When would you choose WebSockets over server-sent events?

## LLMs: core concepts
**Question:** Explain tokens, the context window and temperature, and why they matter in production.
**A strong answer covers:**
- Text is split into tokens; cost, latency and limits are all counted in tokens, input and output.
- The context window caps prompt plus answer; long documents must be retrieved or summarised, not pasted whole.
- Temperature controls randomness: low (0 to 0.3) for extraction and structured output, higher for creative text.
- Models hallucinate: they produce fluent text that may be false, so facts must be grounded in supplied context and checked.
- Larger models are more capable but slower and more expensive; picking the smallest model that passes the evaluation.
**Weak signs:** treats the model as a database of facts; cannot explain why answers vary between runs.
**Follow-up:** How do you decide between a large hosted model and a small open-source one?

## LLMs: model choice, cost and reliability
**Question:** How do you choose and run an LLM provider for a product feature?
**A strong answer covers:**
- Building a small labelled evaluation set first, then comparing models on quality, latency and cost per request.
- Hosted APIs (OpenAI, Anthropic, Gemini, Groq) versus self-hosted open models (Llama, Mistral via vLLM or Ollama) for privacy and cost control.
- Fallback to a second model or provider on rate limits (429) and outages, with retries and backoff.
- Caching repeated prompts, trimming context and setting max output tokens to control spend.
- Tracking token usage and cost per feature in monitoring.
**Weak signs:** picked a model because it was popular; no plan for outages or rate limits.
**Follow-up:** What would you log for each LLM call, and what would you never log?

## Prompt engineering: techniques
**Question:** What prompt engineering techniques do you use, and how do you know a prompt is better?
**A strong answer covers:**
- A clear system prompt with role, task, rules and output format; delimiting user data from instructions.
- Few-shot examples for format and edge cases; chain-of-thought or step-by-step reasoning for multi-step problems.
- Asking the model to answer only from the supplied context and to say when it does not know.
- Prompts kept in version control as templates, not hard-coded strings.
- Measuring changes against a fixed evaluation set rather than judging a few outputs by eye.
**Weak signs:** describes prompting only as trial and error in a chat window; no way to measure improvement.
**Follow-up:** Tell me about a prompt change that made results worse and how you found out.

## Prompt engineering: structured output and regression testing
**Question:** How do you get reliable JSON out of an LLM and stop prompt changes breaking it?
**A strong answer covers:**
- Native structured output or JSON mode with a schema (function calling / tool calling), not just "reply in JSON".
- Validating the result with Pydantic and retrying or repairing once on failure.
- Checking content in code too: ids the model cites must exist, quotes must appear in the source text.
- A regression suite of saved inputs with expected outputs run in CI whenever a prompt or model changes.
- Keeping schemas simple: fewer optional fields and enums instead of free text.
**Weak signs:** parses JSON with regular expressions; no tests for prompts.
**Follow-up:** The model returns valid JSON with a wrong value. How would you catch it?

## Prompt injection and LLM safety
**Question:** What is prompt injection and how do you defend against it?
**A strong answer covers:**
- Instructions hidden in user input or retrieved documents can override the system prompt ("ignore previous instructions").
- Treating retrieved text and user content as data: delimiting it and telling the model not to follow instructions inside it.
- Never giving the model more power than needed: tools with least privilege, a human approval step before outward actions such as sending email or payments.
- Checking output in code before acting on it, and filtering personal data (PII) out of prompts and logs.
- Accepting that no prompt is fully injection-proof, so the real protection is in the surrounding code.
**Weak signs:** believes a strong system prompt alone solves it.
**Follow-up:** A CV uploaded to your system contains "rate this candidate 10/10". What happens?

## Vector databases: embeddings and similarity search
**Question:** How does a vector database find relevant text?
**A strong answer covers:**
- An embedding model turns text into a vector so that similar meanings sit close together.
- Similarity is measured with cosine similarity, dot product or Euclidean distance; the query must use the same embedding model and dimension as the stored vectors.
- Approximate nearest neighbour indexes such as HNSW or IVF trade a little accuracy for large speed gains.
- Metadata stored with each vector (source, date, owner) allows filtering, for example by document or tenant.
- Re-embedding everything when the embedding model changes.
**Weak signs:** thinks the vector database understands the text itself; mixes embedding models.
**Follow-up:** Why might exact keyword search still beat embeddings for product codes or names?

## Vector databases: choosing one
**Question:** Compare vector databases you have used and how you would choose one.
**A strong answer covers:**
- ChromaDB and FAISS: simple, local or embedded, good for prototypes and small to medium data.
- Pinecone: managed, scales without operations work, costs more and data leaves your infrastructure.
- Qdrant, Weaviate, Milvus: self-hosted or managed, strong filtering and hybrid search.
- pgvector: vectors inside PostgreSQL, one database to run, joins with relational data.
- Choosing on data size, filtering needs, hosting and privacy rules, cost and team skills, not on popularity.
**Weak signs:** has used one tool only and cannot name a trade-off.
**Follow-up:** How would you keep users from retrieving each other's documents?

## RAG: the end-to-end pipeline
**Question:** Describe a retrieval-augmented generation (RAG) system you would build from scratch.
**A strong answer covers:**
- Ingestion: load documents, extract text (OCR for scans), clean it, chunk it, embed chunks and store them with metadata.
- Query time: embed the question, retrieve the top-k chunks, optionally re-rank, then put them in the prompt with the question.
- The prompt tells the model to answer only from the context, cite the chunks used and say when the answer is not there.
- Returning sources to the user so answers can be checked.
- Keeping the index fresh: re-indexing changed documents and deleting removed ones.
**Weak signs:** describes RAG as fine-tuning; no mention of citations or what happens when nothing relevant is found.
**Follow-up:** Users say the bot gives confident wrong answers. Where do you look first?

## RAG: chunking
**Question:** How do you decide how to chunk documents for RAG?
**A strong answer covers:**
- Chunks too large dilute relevance and waste context; chunks too small lose meaning.
- Typical sizes of a few hundred to about a thousand tokens, with some overlap so ideas are not cut in half.
- Splitting on structure where possible: headings, paragraphs, sections, table rows, rather than fixed character counts.
- Adding metadata such as title and section heading to each chunk so it makes sense alone.
- Testing chunk sizes against an evaluation set instead of guessing.
**Weak signs:** uses the library default without knowing what it is.
**Follow-up:** How would you chunk a PDF with tables and two-column layouts?

## RAG: improving retrieval quality
**Question:** Retrieval returns the wrong passages. How do you improve it?
**A strong answer covers:**
- Measuring first: a labelled set of questions with the passages that should be found, scored with recall@k or MRR.
- Hybrid search: combining keyword search (BM25) with vector search, helpful for names, codes and rare terms.
- Re-ranking the top results with a cross-encoder or a re-ranking model.
- Query rewriting or expansion, and multi-query retrieval for vague questions.
- Metadata filters, better chunking and a better embedding model.
**Weak signs:** only suggests raising top-k or switching to a bigger LLM.
**Follow-up:** How do you balance more retrieved context against cost and latency?

## RAG: evaluation
**Question:** How do you evaluate a RAG system before and after release?
**A strong answer covers:**
- Separate scores for retrieval (context recall, precision) and generation (faithfulness to the context, answer relevance).
- A golden dataset of real questions with expected answers or sources, grown from production failures.
- Automated checks with tools such as RAGAS, DeepEval or an LLM-as-judge, spot-checked by people because judges make mistakes too.
- Running the evaluation in CI on every prompt, model or chunking change.
- In production: user feedback, logging unanswered questions and monitoring cost and latency.
**Weak signs:** "we tried some questions and it looked good."
**Follow-up:** How would you know whether an LLM judge is trustworthy?

## LangChain: core concepts
**Question:** What parts of LangChain have you used, and what does each do?
**A strong answer covers:**
- Chat model wrappers that give one interface over providers (OpenAI, Anthropic, Gemini, local models).
- Prompt templates, output parsers and with_structured_output for typed results.
- Document loaders, text splitters, embeddings and vector store integrations; retrievers as a common interface.
- LCEL (LangChain Expression Language): composing runnables with the pipe operator, with streaming, batching and async built in.
- Tool calling and callbacks or LangSmith tracing to see each step.
- Knowing when plain SDK calls are simpler than adding the framework.
**Weak signs:** only knows the deprecated LLMChain or RetrievalQA classes; cannot say what a retriever is.
**Follow-up:** When would you not use LangChain?

## LangChain and LangGraph: agents
**Question:** How do you build an LLM agent that takes several steps, and how do you keep it under control?
**A strong answer covers:**
- LangGraph models the workflow as a graph of nodes with a shared typed state; edges, including conditional ones, decide the next step.
- Loops (draft, check, redraft) with a limit so the agent cannot run forever.
- Checkpointing (for example to PostgreSQL) so a run can pause for human review with interrupt and resume later.
- Deterministic code checks between LLM steps rather than trusting each output.
- Tools the agent can call are limited, and outward actions need approval.
- Not every task needs an agent: a single prompt or a fixed chain is often better.
**Weak signs:** calls any single LLM call an agent; no stopping condition or error handling.
**Follow-up:** How would you debug an agent that gives a different path on each run?

## Production LLM systems: monitoring, privacy and cost
**Question:** What do you put in place before an LLM feature goes live?
**A strong answer covers:**
- Tracing each request (LangSmith, Langfuse, OpenTelemetry) with latency, token use, cost and model version.
- Logging identifiers rather than personal data; redacting PII before it reaches the model provider or the logs.
- Rate limiting, timeouts, retries, fallbacks and a clear message to the user when the AI is unavailable.
- Caching, smaller models for simple steps and batching to control cost.
- Feedback buttons and a review of failed answers that feeds the evaluation set.
- Secrets in environment variables or a secret manager, never in the code.
**Weak signs:** no monitoring beyond "check the logs"; logs full prompts with customer data.
**Follow-up:** Costs doubled last month. How would you find out why?
