# Endgame

Self-hosted [Open WebUI](https://github.com/open-webui/open-webui) stack with local embeddings + reranking, Qdrant vector storage, and MetaMCP for tool aggregation.

## What's in here

| Service | Purpose |
|---|---|
| `open-webui` | Chat UI + RAG orchestrator. Talks to Anthropic + OpenRouter for LLMs, axum-embed for embeddings/rerank, Qdrant for vectors. |
| `axum-embed` | Rust (axum) server exposing [fastembed-rs](https://github.com/Anush008/fastembed-rs) over OpenAI-compatible `/v1/embeddings` and Cohere-style `/v1/rerank`. |
| `qdrant` | Vector database. 384-dim collections (matches `intfloat/multilingual-e5-small`). |
| `metamcp` | MCP aggregator (Umbrella-IT-Group fork). Exposes a single OpenAPI endpoint that Open WebUI consumes as a tool server. |

No local LLM is shipped. Bring your own Anthropic + OpenRouter API keys.

## Models

| Role | Model | Dimensions | Source |
|---|---|---|---|
| Embedding | `intfloat/multilingual-e5-small` | 384 | fastembed-rs native |
| Reranking | `BAAI/bge-reranker-base` | n/a (cross-encoder) | fastembed-rs native |

Both are downloaded from HuggingFace on first boot of `axum-embed` (cached in `./volumes/axum-cache`). First start takes ~1-2 minutes; subsequent starts are instant.

## Quickstart

```bash
# 1. Clone with the metamcp submodule
git clone --recurse-submodules https://github.com/WHYCRASH/Endgame.git
cd Endgame

# 2. Copy and fill in env
cp .env.example .env
# edit .env — at minimum set ANTHROPIC_API_KEY and OPENAI_API_KEY

# 3. Boot
docker compose up -d
```

Open WebUI will be at `http://localhost:3000`.

## Verifying axum-embed

```bash
# health
curl http://localhost:8081/health

# list models (used by Open WebUI's model probe)
curl http://localhost:8081/v1/models

# embed something
curl -X POST http://localhost:8081/v1/embeddings \
  -H 'content-type: application/json' \
  -d '{"model":"intfloat/multilingual-e5-small","input":"passage: hello world"}'

# rerank something
curl -X POST http://localhost:8081/v1/rerank \
  -H 'content-type: application/json' \
  -d '{"model":"BAAI/bge-reranker-base","query":"what is rust","documents":["a language","a fungus","iron oxide"]}'
```

## Architecture

```
                ┌──────────────────────────────┐
                │         open-webui           │
                │   (chat UI + RAG pipeline)  │
                └──────────────────────────────┘
                  │       │       │       │
         LLMs ────┘       │       │       └──── MCP tools
  (Anthropic API,         │       │           (via MetaMCP)
   OpenRouter)            │       │
                           │       │
              embeddings ──┘       └── rerank
                  │                       │
                  ▼                       ▼
                ┌──────────────────────────────┐
                │          axum-embed          │
                │  /v1/embeddings  /v1/rerank  │
                │   (fastembed-rs, ONNX CPU)   │
                └──────────────────────────────┘
                  │
                  ▼
                ┌──────────────────────────────┐
                │           qdrant             │
                │   (vector store, 384-dim)    │
                └──────────────────────────────┘
```

Open WebUI generates embeddings by calling axum-embed, then writes the resulting vectors into Qdrant itself. axum-embed is stateless — it only computes vectors and rerank scores; it does not store anything.

## Repo layout

```
Endgame/
├── axum-embed/        # Rust translation server (this repo)
├── metamcp/           # git submodule -> Umbrella-IT-Group/metamcp (umbrella branch)
├── docker-compose.yml
├── .env.example
├── .gitmodules
└── README.md
```

## Updating the metamcp submodule

```bash
git submodule update --remote metamcp
git add metamcp
git commit -m 'bump metamcp submodule'
```

## Notes

- **e5-small prefixes**: Open WebUI is configured to prepend `query: ` / `passage: ` via `RAG_EMBEDDING_QUERY_PREFIX` / `RAG_EMBEDDING_CONTENT_PREFIX`. Do not also add them in axum-embed, or you'll get double prefixes.
- **384-dim**: Wherever vectors land in Qdrant, the collection must be 384-dim. Open WebUI handles this automatically when `RAG_EMBEDDING_MODEL` matches the model axum-embed serves.
- **Hybrid search**: `ENABLE_RAG_HYBRID_SEARCH=true` is required for the reranker to actually run. `RAG_TOP_K=20` (vector candidates) > `RAG_TOP_K_RERANKER=4` (final, post-rerank).
- **No local LLM**: chat completions come from Anthropic (native integration) and OpenRouter (OpenAI-compatible).
- **Pipeline filters**: not part of this repo. If migrating from an existing Open WebUI instance, export your Anthropic / OpenRouter filter functions from the old one and re-import.

## License

MIT for the code in this repo. Upstream licenses apply to open-webui, fastembed-rs, Qdrant, and MetaMCP.
