//! axum-embed: OpenAI-compatible /v1/embeddings + Cohere-style /v1/rerank
//! backed by fastembed-rs.
//!
//! Embedding model: intfloat/multilingual-e5-small (384-dim)
//! Rerank model:    BAAI/bge-reranker-base
//!
//! Both are loaded once at startup and held behind an Arc. fastembed-rs is
//! synchronous (no tokio), so all inference calls are wrapped in
//! tokio::task::spawn_blocking to avoid blocking the async runtime.
//!
//! Open WebUI config (see ../.env.example):
//!   RAG_EMBEDDING_ENGINE=openai
//!   RAG_OPENAI_API_BASE_URL=http://axum-embed:8080/v1
//!   RAG_RERANKING_ENGINE=external
//!   RAG_EXTERNAL_RERANKER_URL=http://axum-embed:8080/v1/rerank
//!
//! Prefix handling: e5-small wants "query:" / "passage:" prefixes, but
//! Open WebUI prepends them via RAG_EMBEDDING_QUERY_PREFIX and
//! RAG_EMBEDDING_CONTENT_PREFIX before calling us. So this server stays
//! prefix-agnostic — it embeds whatever text it receives, verbatim.

use std::net::SocketAddr;
use std::sync::Arc;

use anyhow::{Context, Result};
use axum::{
    extract::State,
    http::StatusCode,
    response::{IntoResponse, Json},
    routing::{get, post},
    Router,
};
use fastembed::{
    EmbeddingModel, InitOptions, RerankInitOptions, RerankerModel, TextEmbedding, TextRerank,
};
use serde::{Deserialize, Serialize};
use tower_http::{cors::CorsLayer, trace::TraceLayer};
use tracing_subscriber::EnvFilter;

// ============================================================================
// App state
// ============================================================================

#[derive(Clone)]
struct AppState {
    /// TextEmbedding::embed takes &mut self in fastembed 5.x, so we need a
    /// mutex for exclusive access.
    embed: Arc<tokio::sync::Mutex<TextEmbedding>>,
    /// TextRerank::rerank also takes &mut self.
    rerank: Arc<tokio::sync::Mutex<TextRerank>>,
    embed_model_code: String,
    rerank_model_code: String,
}

// ============================================================================
// OpenAI-compatible /v1/embeddings
// ============================================================================

#[derive(Debug, Deserialize)]
struct EmbeddingsRequest {
    /// Ignored — we serve exactly one model. Accepted for OpenAI API parity.
    #[allow(dead_code)]
    model: Option<String>,
    /// OpenAI allows a string or an array of strings. We normalize to Vec.
    input: EmbeddingsInput,
    /// Ignored — fastembed picks its own batch size.
    #[allow(dead_code)]
    encoding_format: Option<String>,
    /// Ignored.
    #[allow(dead_code)]
    dimensions: Option<u32>,
    /// Ignored.
    #[allow(dead_code)]
    user: Option<String>,
}

#[derive(Debug, Deserialize)]
#[serde(untagged)]
enum EmbeddingsInput {
    Single(String),
    Many(Vec<String>),
}

impl From<EmbeddingsInput> for Vec<String> {
    fn from(value: EmbeddingsInput) -> Self {
        match value {
            EmbeddingsInput::Single(s) => vec![s],
            EmbeddingsInput::Many(v) => v,
        }
    }
}

#[derive(Debug, Serialize)]
struct EmbeddingsResponse {
    object: &'static str,
    data: Vec<EmbeddingObject>,
    model: String,
    usage: Usage,
}

#[derive(Debug, Serialize)]
struct EmbeddingObject {
    object: &'static str,
    index: usize,
    embedding: Vec<f32>,
}

#[derive(Debug, Serialize)]
struct Usage {
    prompt_tokens: u32,
    total_tokens: u32,
}

async fn embeddings_handler(
    State(state): State<AppState>,
    Json(req): Json<EmbeddingsRequest>,
) -> Result<Json<EmbeddingsResponse>, AppError> {
    let inputs: Vec<String> = req.input.into();
    if inputs.is_empty() {
        return Err(AppError::BadRequest("input must not be empty".into()));
    }

    let embed = state.embed.clone();
    let model_code = state.embed_model_code.clone();

    // fastembed-rs is synchronous; offload to a blocking thread.
    let result = tokio::task::spawn_blocking(move || {
        let mut guard = embed.blocking_lock();
        guard.embed(inputs.clone(), None)
    })
    .await
    .map_err(|e| AppError::Internal(format!("join error: {e}")))?;

    let embeddings = result.map_err(|e| AppError::Internal(format!("embed failed: {e}")))?;

    // Rough token accounting. fastembed doesn't expose token counts directly,
    // so we estimate from the input strings. This is only used by Open WebUI
    // for telemetry; correctness is not load-bearing.
    let prompt_tokens: u32 = embeddings
        .iter()
        .map(|v| v.len() as u32 / 4)
        .sum::<u32>()
        .max(1);

    let data = embeddings
        .into_iter()
        .enumerate()
        .map(|(index, embedding)| EmbeddingObject {
            object: "embedding",
            index,
            embedding,
        })
        .collect();

    Ok(Json(EmbeddingsResponse {
        object: "list",
        data,
        model: model_code,
        usage: Usage {
            prompt_tokens,
            total_tokens: prompt_tokens,
        },
    }))
}

// ============================================================================
// Cohere/Jina-style /v1/rerank
// ============================================================================
//
// Open WebUI calls this with a body shaped like:
//   { "model": "...", "query": "...", "documents": ["...", ...], "top_n": 4 }
// and expects:
//   { "results": [{ "index": N, "relevance_score": F }, ...] }
// sorted descending by score, length == min(top_n, documents.len()).

#[derive(Debug, Deserialize)]
struct RerankRequest {
    #[allow(dead_code)]
    model: Option<String>,
    query: String,
    documents: Vec<String>,
    top_n: Option<usize>,
    /// Ignored — we always return scores in [0,1] via sigmoid (matches what
    /// bge-reranker-base produces when the cross-encoder's sigmoid head is
    /// applied; Open WebUI uses these against RAG_RELEVANCE_THRESHOLD).
    #[allow(dead_code)]
    return_documents: Option<bool>,
}

#[derive(Debug, Serialize)]
struct RerankResponse {
    results: Vec<RerankResultJson>,
}

#[derive(Debug, Serialize)]
struct RerankResultJson {
    index: usize,
    relevance_score: f32,
    // Open WebUI does not require the document text back; we omit it to keep
    // responses small. If a future consumer wants it, flip the flag below.
    document: Option<String>,
}

async fn rerank_handler(
    State(state): State<AppState>,
    Json(req): Json<RerankRequest>,
) -> Result<Json<RerankResponse>, AppError> {
    if req.documents.is_empty() {
        return Err(AppError::BadRequest("documents must not be empty".into()));
    }
    if req.query.is_empty() {
        return Err(AppError::BadRequest("query must not be empty".into()));
    }

    let rerank = state.rerank.clone();
    let query = req.query.clone();
    let docs = req.documents.clone();
    let return_documents = req.return_documents.unwrap_or(false);

    let result = tokio::task::spawn_blocking(move || {
        // fastembed's rerank takes &mut self.
        let mut guard = rerank.blocking_lock();
        // Pass query as String and docs as &[String] so S=String (String:
        // AsRef<str>). Mixing &str query with &[String] docs infers S=&str
        // and fails because &[String] != AsRef<[&str]>.
        guard.rerank(query, &docs, return_documents, None)
    })
    .await
    .map_err(|e| AppError::Internal(format!("join error: {e}")))?;

    let mut ranked = result.map_err(|e| AppError::Internal(format!("rerank failed: {e}")))?;

    if let Some(top_n) = req.top_n {
        ranked.truncate(top_n);
    }

    let results = ranked
        .into_iter()
        .map(|r| RerankResultJson {
            index: r.index,
            // bge-reranker-base emits raw logits via fastembed. Apply sigmoid
            // to map to [0,1] so RAG_RELEVANCE_THRESHOLD behaves sensibly.
            // (This mirrors Open WebUI's
            // SENTENCE_TRANSFORMERS_CROSS_ENCODER_SIGMOID_ACTIVATION_FUNCTION
            // for local rerankers.)
            relevance_score: sigmoid(r.score),
            document: r.document,
        })
        .collect();

    Ok(Json(RerankResponse { results }))
}

fn sigmoid(x: f32) -> f32 {
    1.0 / (1.0 + (-x).exp())
}

// ============================================================================
// /v1/models — minimal OpenAI-compatible model list
// ============================================================================
// Open WebUI calls this on the configured embedding base URL to probe what's
// available. If it returns nothing useful, embedding setup in the admin UI
// can look broken.

#[derive(Debug, Serialize)]
struct ModelsResponse {
    object: &'static str,
    data: Vec<ModelObject>,
}

#[derive(Debug, Serialize)]
struct ModelObject {
    id: String,
    object: &'static str,
    owned_by: &'static str,
}

async fn models_handler(State(state): State<AppState>) -> Json<ModelsResponse> {
    Json(ModelsResponse {
        object: "list",
        data: vec![
            ModelObject {
                id: state.embed_model_code.clone(),
                object: "model",
                owned_by: "intfloat",
            },
            ModelObject {
                id: state.rerank_model_code.clone(),
                object: "model",
                owned_by: "BAAI",
            },
        ],
    })
}

// ============================================================================
// /health
// ============================================================================

async fn health_handler() -> &'static str {
    "ok"
}

// ============================================================================
// Error handling
// ============================================================================

enum AppError {
    BadRequest(String),
    Internal(String),
}

impl IntoResponse for AppError {
    fn into_response(self) -> axum::response::Response {
        let (status, msg) = match self {
            AppError::BadRequest(m) => (StatusCode::BAD_REQUEST, m),
            AppError::Internal(m) => (StatusCode::INTERNAL_SERVER_ERROR, m),
        };
        (
            status,
            Json(serde_json::json!({
                "error": { "message": msg, "type": "invalid_request_error" }
            })),
        )
            .into_response()
    }
}

// ============================================================================
// Bootstrap
// ============================================================================

#[tokio::main]
async fn main() -> Result<()> {
    tracing_subscriber::fmt()
        .with_env_filter(
            EnvFilter::try_from_default_env().unwrap_or_else(|_| EnvFilter::new("info")),
        )
        .init();

    let embed_model_code = std::env::var("EMBED_MODEL")
        .unwrap_or_else(|_| "intfloat/multilingual-e5-small".to_string());
    let rerank_model_code = std::env::var("RERANK_MODEL")
        .unwrap_or_else(|_| "BAAI/bge-reranker-base".to_string());
    let cache_dir = std::env::var("FASTEMBED_CACHE_DIR")
        .unwrap_or_else(|_| "./.fastembed_cache".to_string());
    let listen_addr: SocketAddr = std::env::var("LISTEN_ADDR")
        .unwrap_or_else(|_| "0.0.0.0:8080".to_string())
        .parse()
        .context("LISTEN_ADDR is not a valid socket address")?;

    // Resolve model codes to fastembed enum variants. The `models` module is
    // private in fastembed-rs; the public re-export is at the crate root as
    // `fastembed::EmbeddingModel`. Use that, not the deep path.
    let embed_model = EmbeddingModel::MultilingualE5Small;
    let rerank_model = RerankerModel::BGERerankerBase;

    // Sanity-check that the env-provided code strings actually match what
    // we're loading — surfaces config typos early instead of silently serving
    // a different model.
    //
    // fastembed 5.17.2 API notes:
    //   - TextEmbedding::get_model_info returns Result<&ModelInfo, _>
    //     (not Option), so unwrap_or_else takes |e|.
    //   - TextRerank::get_model_info returns RerankerModelInfo directly
    //     (panics if not found), not Option/Result, so no .map() needed.
    let actual_embed_code =
        TextEmbedding::get_model_info(&embed_model)
            .map(|i| i.model_code.clone())
            .unwrap_or_else(|_| embed_model_code.clone());
    if actual_embed_code != embed_model_code {
        tracing::warn!(
            "EMBED_MODEL='{embed_model_code}' does not match the loaded variant's model_code '{actual_embed_code}'. Using the variant anyway."
        );
    }
    let actual_rerank_code = TextRerank::get_model_info(&rerank_model).model_code.clone();
    if actual_rerank_code != rerank_model_code {
        tracing::warn!(
            "RERANK_MODEL='{rerank_model_code}' does not match the loaded variant's model_code '{actual_rerank_code}'. Using the variant anyway."
        );
    }

    tracing::info!(
        "loading embedding model '{actual_embed_code}' (cache: {cache_dir})"
    );
    let embed = TextEmbedding::try_new(
        InitOptions::new(embed_model).with_cache_dir(cache_dir.clone().into()),
    )
    .context("failed to load embedding model — first run downloads weights from HuggingFace; check network + cache dir permissions")?;

    tracing::info!("loading rerank model '{actual_rerank_code}'");
    let rerank = TextRerank::try_new(
        RerankInitOptions::new(rerank_model).with_cache_dir(cache_dir.into()),
    )
    .context("failed to load rerank model")?;

    let state = AppState {
        embed: Arc::new(tokio::sync::Mutex::new(embed)),
        rerank: Arc::new(tokio::sync::Mutex::new(rerank)),
        embed_model_code: actual_embed_code,
        rerank_model_code: actual_rerank_code,
    };

    let app = Router::new()
        .route("/health", get(health_handler))
        .route("/v1/models", get(models_handler))
        .route("/v1/embeddings", post(embeddings_handler))
        .route("/v1/rerank", post(rerank_handler))
        .with_state(state)
        .layer(TraceLayer::new_for_http())
        .layer(CorsLayer::permissive());

    let listener = tokio::net::TcpListener::bind(listen_addr).await?;
    tracing::info!(addr = %listener.local_addr()?, "axum-embed listening");
    axum::serve(listener, app).await?;
    Ok(())
}
