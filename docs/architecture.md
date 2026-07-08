# Endgame architecture

## Components

### open-webui
- Built from `./open-webui-Custom` (the WHYCRASH fork, slimmed).
- Internal port: 8080 (exposed on host :3000).
- Volume: `open-webui-data` -> `/app/backend/data` (sqlite, uploads, cache).
- The fork's Dockerfile + requirements.txt have torch + sentence-transformers removed in the non-CUDA path; embeddings + reranking are delegated to axum-embed.
- Configured entirely via `.env`. Most relevant vars:
  - `ANTHROPIC_API_KEY` (native Anthropic integration)
  - `OPENAI_API_KEY` + `OPENAI_API_BASE_URL=https://openrouter.ai/api/v1` (OpenRouter as OpenAI-compatible)
  - `RAG_EMBEDDING_ENGINE=openai` + `RAG_OPENAI_API_BASE_URL=http://axum-embed:8080/v1`
  - `RAG_RERANKING_ENGINE=external` + `RAG_EXTERNAL_RERANKER_URL=http://axum-embed:8080/v1/rerank`
  - `VECTOR_DB=qdrant` + `QDRANT_URI=http://qdrant:6333`
  - `ENABLE_RAG_HYBRID_SEARCH=true` (required for reranker to run)
  - `TOOL_SERVER_CONNECTIONS` JSON (MetaMCP as tool server)
- `depends_on` axum-embed + qdrant with health checks.

### axum-embed
- Built from `./axum-embed`.
- Internal port: 8080 (exposed on host :8081).
- Volume: `axum-cache` -> `/data/.fastembed_cache` (ONNX weights).
- Endpoints:
  - `GET /health` - readiness probe.
  - `GET /v1/models` - OpenAI-compatible model list (returns both the embedding and rerank model codes so Open WebUI's probe succeeds).
  - `POST /v1/embeddings` - OpenAI-compatible. Input is `string | string[]`. Embedding model is `intfloat/multilingual-e5-small` (384-dim) loaded once at startup.
  - `POST /v1/rerank` - Cohere/Jina-style. Input is `{query, documents, top_n?}`. Rerank model is `BAAI/bge-reranker-base`.
- Synchronous fastembed-rs calls are wrapped in `tokio::task::spawn_blocking` to keep the async runtime responsive.
- `TextRerank::rerank` requires `&mut self`, so the reranker is held behind a `tokio::sync::Mutex`. The embedding model is `Arc<TextEmbedding>` (its `embed` takes `&self`).
- Sigmoid is applied to rerank scores before returning - bge-reranker-base emits raw logits; Open WebUI expects [0,1] for `RAG_RELEVANCE_THRESHOLD` to make sense.

### qdrant
- Image: `qdrant/qdrant:latest`.
- Ports: 6333 (REST), 6334 (gRPC, optional).
- Volume: `qdrant-data` -> `/qdrant/storage`.
- Open WebUI creates collections itself; the 384-dim schema is implied by `RAG_EMBEDDING_MODEL=intfloat/multilingual-e5-small`.

### metamcp
- Built from the `./metamcp` git submodule (Umbrella-IT-Group/metamcp, `umbrella` branch).
- Internal port: 12008 (exposed on host :12008).
- Uses its own bundled `metamcp-postgres` service for state.
- Exposed to Open WebUI via `TOOL_SERVER_CONNECTIONS` as an OpenAPI tool server.

### function-sync
- Built from `./sync`.
- One-shot container: reads `functions/` and `tools/` from the repo (mounted read-only), reconciles them into Open WebUI, exits 0.
- Functions: `POST /api/v1/functions/sync` (declarative - creates/updates/deletes to match).
- Tools: manual diff against `GET /api/v1/tools/export`, then per-tool `/create` `/id/{id}/update` `/id/{id}/delete`. Deletions in the repo propagate.
- Auth: `OPENWEBUI_ADMIN_API_KEY` (admin-only endpoints).
- Runs after open-webui is healthy (compose `depends_on: condition: service_healthy`).

### open-webui-Custom (submodule)
- Your fork of open-webui, branch `main`.
- `backend/requirements.txt`: transformers, sentence-transformers, accelerate, einops, colbert-ai commented out.
- `Dockerfile`: in the non-CUDA path, the `pip3 install torch` step and the sentence-transformers pre-download steps are removed. faster-whisper + tiktoken still pre-download (they don't depend on torch).
- To re-enable local embeddings: uncomment those lines in both files. The Dockerfile's CUDA branch is untouched.

## Data flow

### RAG flow
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

### Function/tool sync flow
1. `podman compose up` starts all services. function-sync waits for open-webui to be healthy.
2. function-sync reads `functions/*.py` and `tools/**/*.py` from the repo (read-only bind mounts).
3. Each file's frontmatter docstring (the `"""..."""` at the top) is parsed for `title`, `type`, `description`.
4. Functions are pushed via `POST /api/v1/functions/sync` (declarative - instance matches the payload exactly, including deletions).
5. Tools are diffed against `GET /api/v1/tools/export`. New tools -> `/create`. Changed tools -> `/id/{id}/update`. Tools in the instance but not in the repo -> `/id/{id}/delete`.
6. function-sync exits 0.

### Authoring flow (LLM-callable)
1. The `openwebui_function_author` tool is installed by function-sync on first `up`.
2. User enables the tool on a model in Open WebUI's model editor.
3. User chats: "write a filter that strips PII from the user message before sending to the LLM."
4. The model calls `write_function(function_id='pii_filter', name='PII Filter', description='...', function_type='filter', python_source='...')`.
5. The tool calls `POST /api/v1/functions/id/pii_filter/update` (or `/create` on 404).
6. The function is now live on the instance. The model reports success.
7. To make the change permanent in git, the user copies the source from the chat into `functions/pii_filter.py`, commits, and the next `podman compose up` reconciles it.

## Why axum-embed is stateless

Open WebUI itself owns the vector storage relationship - it generates embeddings via axum-embed and writes them to Qdrant. axum-embed does not know about Qdrant. This keeps the embedding server simple and lets you swap it out (or run multiple replicas behind a load balancer) without touching vector data.

## Filter migration

Existing Anthropic and OpenRouter pipeline filters from a prior Open WebUI instance are now part of this repo. To migrate:
1. In the old instance: Admin > Functions > export each filter as Python.
2. Drop them in `functions/`.
3. `git add functions/ && git commit`.
4. `podman compose up -d` - function-sync installs them.

See `docs/functions-and-tools.md` for the full file format and sync semantics.
