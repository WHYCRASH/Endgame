# Endgame

Self-hosted [Open WebUI](https://github.com/open-webui/open-webui) stack with local embeddings + reranking, Qdrant vector storage, MetaMCP for tool aggregation, and git-managed functions/tools with bootstrap sync.

## What's in here

| Service | Purpose |
|---|---|
| `open-webui` | Chat UI + RAG orchestrator. Built from `WHYCRASH/open-webui-Custom` (a slimmed fork: torch + sentence-transformers removed, since axum-embed handles embeddings). Talks to Anthropic + OpenRouter for LLMs, axum-embed for embeddings/rerank, Qdrant for vectors. |
| `axum-embed` | Rust (axum) server exposing [fastembed-rs](https://github.com/Anush008/fastembed-rs) over OpenAI-compatible `/v1/embeddings` and Cohere-style `/v1/rerank`. |
| `qdrant` | Vector database. 384-dim collections (matches `intfloat/multilingual-e5-small`). |
| `metamcp` | MCP aggregator (Umbrella-IT-Group fork). Exposes a single OpenAPI endpoint that Open WebUI consumes as a tool server. |
| `function-sync` | One-shot Python container. Reads `functions/*.py` and `tools/*.py` from this repo, reconciles them into Open WebUI via the API, then exits. Runs on every `podman compose up`. |

No local LLM is shipped. Bring your own Anthropic + OpenRouter API keys.

## Models

| Role | Model | Dimensions | Source |
|---|---|---|---|
| Embedding | `intfloat/multilingual-e5-small` | 384 | fastembed-rs native |
| Reranking | `BAAI/bge-reranker-base` | n/a (cross-encoder) | fastembed-rs native |

Both are downloaded from HuggingFace on first boot of `axum-embed` (cached in the `axum-cache` volume). First start takes ~1-2 minutes; subsequent starts are instant.

## Repo layout

```
Endgame/
├── axum-embed/                    # Rust translation server (subproject)
│   ├── Cargo.toml
│   ├── src/main.rs
│   └── Dockerfile
├── functions/                     # git-managed Open WebUI function source
│   └── README.md                  #   (filters, pipes, pipelines)
├── tools/                         # git-managed Open WebUI tool source
│   ├── README.md
│   └── openwebui_function_author/ # the authoring tool (LLM-callable)
│       └── main.py
├── models/                        # TODO — parked until OpenRouter filter refactor
├── sync/                          # one-shot bootstrap sync container
│   ├── sync_functions.py
│   ├── requirements.txt
│   └── Dockerfile
├── open-webui-Custom/             # git submodule — your slimmed Open WebUI fork
├── metamcp/                       # git submodule — Umbrella-IT-Group/metamcp (umbrella branch)
├── docker-compose.yml
├── .env.example
├── .gitmodules
├── docs/
│   ├── architecture.md
│   ├── functions-and-tools.md     # how the sync + authoring flow works
│   └── CI_NOTES.md
└── README.md
```

## Quickstart

```bash
# 1. Clone with submodules (open-webui-Custom + metamcp)
git clone --recurse-submodules https://github.com/WHYCRASH/Endgame.git
cd Endgame

# 2. Copy and fill in env
cp .env.example .env
# Edit .env — at minimum set:
#   ANTHROPIC_API_KEY         (your Anthropic API key)
#   OPENAI_API_KEY            (your OpenRouter key — Open WebUI treats OpenRouter as the OpenAI endpoint)
#   WEBUI_SECRET_KEY          (run: openssl rand -hex 32)
#   OPENWEBUI_ADMIN_API_KEY   (generated in Open WebUI after first boot — see below)
#   METAMCP_API_KEY           (any random string)
#   METAMCP_POSTGRES_PASSWORD (any random string)

# 3. Boot
podman compose up -d

# 4. Wait for first-boot model downloads (axum-embed: ~2 min, open-webui: ~3-5 min build on first run)
podman compose logs -f axum-embed
# wait for: "axum-embed listening"
```

Open WebUI will be at `http://localhost:3000`.

### Generating OPENWEBUI_ADMIN_API_KEY

The function-sync container and the authoring tool both need an admin API key.

1. Wait for `open-webui` to be healthy: `podman compose ps`.
2. Open `http://localhost:3000`, create your admin account on first visit.
3. Settings (top-right) > Account > API Keys. Generate one. It starts with `sk-`.
4. Paste it into `.env` as `OPENWEBUI_ADMIN_API_KEY`.
5. `podman compose up -d` again — function-sync will now run successfully.

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

## Verifying function-sync

```bash
podman compose logs function-sync
# Should print:
#   == functions ==
#     local: 0 file(s)
#     synced: 0 function(s) on the instance
#   == tools ==
#     local: 1 file(s)
#     - openwebui_function_author            OpenWebUI Function Author
#     remote: 0 tool(s) on the instance
#     NEW  openwebui_function_author
#   done.
```

After that, the authoring tool appears in Open WebUI > Workspace > Tools. Enable it on a model and ask the model to write a function — it will call `write_function`.

## Architecture

```
              ┌────────────────────────────────────────────┐
              │              open-webui                    │
              │   (chat UI + RAG pipeline + filters)       │
              └────────────────────────────────────────────┘
                │       │       │       │       │
       LLMs ────┘       │       │       │       └──── MCP tools
  (Anthropic API,         │       │           (via MetaMCP)
   OpenRouter)            │       │
                           │       │
              embeddings ──┘       └── rerank
                  │                       │
                  ▼                       ▼
              ┌────────────────────────────────────────────┐
              │              axum-embed                    │
              │   /v1/embeddings  /v1/rerank  /v1/models   │
              │       (fastembed-rs, ONNX CPU)             │
              └────────────────────────────────────────────┘
                  │
                  ▼
              ┌────────────────────────────────────────────┐
              │                 qdrant                     │
              │         (vector store, 384-dim)            │
              └────────────────────────────────────────────┘

              ┌────────────────────────────────────────────┐
              │              function-sync                 │
              │   (one-shot, reads functions/ + tools/)    │
              │   pushes to Open WebUI via API, then exits │
              └────────────────────────────────────────────┘
                                  │
                                  ▼
              ┌────────────────────────────────────────────┐
              │              open-webui                    │
              │   /api/v1/functions/sync  (declarative)    │
              │   /api/v1/tools/{create,update,delete}     │
              └────────────────────────────────────────────┘
```

Open WebUI generates embeddings by calling axum-embed, then writes the resulting vectors into Qdrant itself. axum-embed is stateless — it only computes vectors and rerank scores; it does not store anything.

The function-sync container is also stateless — it reads the repo's `functions/` and `tools/` directories and reconciles them into Open WebUI. The instance is the source of truth at runtime; the repo is the source of truth at deploy time.

## Updating submodules

```bash
# Bump open-webui-Custom (your fork) to its latest main:
git submodule update --remote open-webui-Custom
git add open-webui-Custom
git commit -m 'bump open-webui-Custom submodule'

# Same for metamcp (tracks the umbrella branch):
git submodule update --remote metamcp
git add metamcp
git commit -m 'bump metamcp submodule'
```

## Notes

- **Podman, not Docker.** Tested with `podman compose` (podman 5.x). The compose file is Compose-spec compliant so `docker compose` works too if you'd rather.
- **e5-small prefixes**: Open WebUI prepends `query: ` / `passage: ` via `RAG_EMBEDDING_QUERY_PREFIX` / `RAG_EMBEDDING_CONTENT_PREFIX`. Do not also add them in axum-embed, or you'll get double prefixes.
- **384-dim**: Wherever vectors land in Qdrant, the collection must be 384-dim. Open WebUI handles this automatically when `RAG_EMBEDDING_MODEL` matches the model axum-embed serves.
- **Hybrid search**: `ENABLE_RAG_HYBRID_SEARCH=true` is required for the reranker to actually run. `RAG_TOP_K=20` (vector candidates) > `RAG_TOP_K_RERANKER=4` (final, post-rerank).
- **No local LLM**: chat completions come from Anthropic (native integration) and OpenRouter (OpenAI-compatible).
- **Pipeline filters**: now part of this repo. Drop them in `functions/` and they sync on next `podman compose up`. See `docs/functions-and-tools.md` for the full workflow.
- **Models dir**: parked at TODO. See `models/README.md`.

## License

MIT for the code in this repo. Upstream licenses apply to open-webui, fastembed-rs, Qdrant, and MetaMCP.
