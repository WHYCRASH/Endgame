# Endgame architecture

## Components

### open-webui
- Image: `ghcr.io/open-webui/open-webui:main`
- Internal port: 8080 (exposed on host :3000)
- Volume: `open-webui-data` → `/app/backend/data` (sqlite, uploads, cache)
- Configured entirely via `.env`. Most relevant vars:
  - `ANTHROPIC_API_KEY` (native Anthropic integration in Open WebUI)
  - `OPENAI_API_KEY` + `OPENAI_API_BASE_URL=https://openrouter.ai/api/v1` (OpenRouter as OpenAI-compatible)
  - `RAG_EMBEDDING_ENGINE=openai` + `RAG_OPENAI_API_BASE_URL=http://axum-embed:8080/v1` (embeddings)
  - `RAG_RERANKING_ENGINE=external` + `RAG_EXTERNAL_RERANKER_URL=http://axum-embed:8080/v1/rerank` (rerank)
  - `VECTOR_DB=qdrant` + `QDRANT_URI=http://qdrant:6333`
  - `ENABLE_RAG_HYBRID_SEARCH=true` (required for reranker to run)
  - `TOOL_SERVER_CONNECTIONS` JSON (MetaMCP as tool server)
- `depends_on` axum-embed + qdrant with health checks.

### axum-embed
- Built from `./axum-embed`.
- Internal port: 8080 (exposed on host :8081).
- Volume: `axum-cache` → `/data/.fastembed_cache` (ONNX weights).
- Endpoints:
  - `GET /health` — readiness probe.
  - `GET /v1/models` — OpenAI-compatible model list (returns both the embedding and rerank model codes so Open WebUI's probe succeeds).
  - `POST /v1/embeddings` — OpenAI-compatible. Input is `string | string[]`. Embedding model is `intfloat/multilingual-e5-small` (384-dim) loaded once at startup.
  - `POST /v1/rerank` — Cohere/Jina-style. Input is `{query, documents, top_n?}`. Rerank model is `BAAI/bge-reranker-base`.
- Synchronous fastembed-rs calls are wrapped in `tokio::task::spawn_blocking` to keep the async runtime responsive.
- `TextRerank::rerank` requires `&mut self`, so the reranker is held behind a `tokio::sync::Mutex`. The embedding model is `Arc<TextEmbedding>` (its `embed` takes `&self`).
- Sigmoid is applied to rerank scores before returning — bge-reranker-base emits raw logits; Open WebUI expects [0,1] for `RAG_RELEVANCE_THRESHOLD` to make sense.

### qdrant
- Image: `qdrant/qdrant:latest`.
- Ports: 6333 (REST), 6334 (gRPC, optional).
- Volume: `qdrant-data` → `/qdrant/storage`.
- Open WebUI creates collections itself; the 384-dim schema is implied by `RAG_EMBEDDING_MODEL=intfloat/multilingual-e5-small`.

### metamcp
- Built from the `./metamcp` git submodule (Umbrella-IT-Group/metamcp, `umbrella` branch).
- Internal port: 12008 (exposed on host :12008).
- Uses its own bundled `metamcp-postgres` service for state.
- Exposed to Open WebUI via `TOOL_SERVER_CONNECTIONS` as an OpenAPI tool server.

## Data flow

1. User chats in Open WebUI.
2. If RAG is triggered (file upload, knowledge base, web search):
   a. Open WebUI sends the query text (already prefixed with `query: ` by `RAG_EMBEDDING_QUERY_PREFIX`) to `axum-embed:8080/v1/embeddings`.
   b. axum-embed runs `intfloat/multilingual-e5-small` on the text and returns a 384-dim vector.
   c. Open WebUI queries Qdrant for the top-`RAG_TOP_K` (20) similar vectors.
   d. Open WebUI sends the query + the 20 retrieved chunks to `axum-embed:8080/v1/rerank`.
   e. axum-embed runs `BAAI/bge-reranker-base`, applies sigmoid, returns sorted scores.
   f. Open WebUI keeps the top-`RAG_TOP_K_RERANKER` (4) chunks above `RAG_RELEVANCE_THRESHOLD` and injects them into the LLM prompt.
3. The LLM call goes to Anthropic (native) or OpenRouter (as OpenAI-compatible), per the model selected in the chat UI.
4. If the model invokes a tool, Open WebUI calls MetaMCP via the configured `TOOL_SERVER_CONNECTIONS` URL, which routes to whichever MCP server is registered under that namespace.

## Why axum-embed is stateless

Open WebUI itself owns the vector storage relationship — it generates embeddings via axum-embed and writes them to Qdrant. axum-embed does not know about Qdrant. This keeps the embedding server simple and lets you swap it out (or run multiple replicas behind a load balancer) without touching vector data.

## Filter migration (TODO)

Existing Anthropic and OpenRouter pipeline filters from a prior Open WebUI instance are not in this repo. To migrate:
1. In the old instance: Admin → Functions → export each filter as a Python file.
2. In this stack: Admin → Functions → import.
3. Re-attach filters to the appropriate model connection in Admin → Settings → Connections.

This will be expanded once the user decides how they want to handle the OpenRouter filter refactor.
