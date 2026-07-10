# Multi-stage build. Uses Rust's official slim image; fastembed-rs pulls
# in ort (ONNX Runtime) which has prebuilt static binaries for x86_64-linux,
# so we do NOT need system ONNX libs. We do need pkg-config + libssl-dev
# for the hf-hub reqwest client.

ARG RUST_VERSION=1.97

# ---------------------------------------------------------------------------
# Build stage
# ---------------------------------------------------------------------------
FROM rust:${RUST_VERSION}-slim-bookworm AS builder

RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        pkg-config \
        libssl-dev \
        ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Cache deps separately from source for faster incremental builds.
COPY Cargo.toml ./
RUN mkdir src && echo 'fn main() {}' > src/main.rs
# Dummy-binary cache bug: the first build above produces an empty `axum-embed`
# stub binary from `fn main() {}`. Because the build cache mount is shared
# between the two RUN steps, cargo's incremental fingerprint will see the
# stub's mtime as newer than the real source on the second build and skip
# relinking, shipping the dummy stub as the final binary. The second build
# deletes the stub first to force a relink from the real source.
RUN --mount=type=cache,target=/app/target \
    --mount=type=cache,target=/usr/local/cargo/registry \
    cargo build --release || true

# Now copy the real source and build for real.
COPY src ./src
RUN --mount=type=cache,target=/app/target \
    --mount=type=cache,target=/usr/local/cargo/registry \
    rm -f target/release/axum-embed && \
    cargo build --release && \
    cp ./target/release/axum-embed /usr/local/bin/axum-embed

# ---------------------------------------------------------------------------
# Runtime stage
# ---------------------------------------------------------------------------
FROM debian:bookworm-slim AS runtime

RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        ca-certificates \
        libssl3 \
    && rm -rf /var/lib/apt/lists/*

RUN addgroup --system --gid 10001 app && \
    adduser  --system --uid 10001 --gid 10001 --home /nonexistent \
             --shell /usr/sbin/nologin app

COPY --from=builder /usr/local/bin/axum-embed /usr/local/bin/axum-embed

USER app

ENV LISTEN_ADDR=0.0.0.0:8080 \
    FASTEMBED_CACHE_DIR=/data/.fastembed_cache \
    RUST_LOG=info

EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=3s --start-period=120s --retries=3 \
    CMD ["/bin/sh", "-c", "exec 3<>/dev/tcp/127.0.0.1/8080; printf 'GET /health HTTP/1.0\\r\\n\\r\\n' >&3; cat <&3 | head -1 | grep -q 200 || exit 1"]

ENTRYPOINT ["axum-embed"]
