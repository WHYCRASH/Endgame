# models/

## STATUS: TODO

This directory is reserved for declarative Open WebUI custom model definitions.
Open WebUI exposes `POST /api/v1/models/sync` (admin-only) which makes the
instance match a JSON list exactly — same shape as `GET /api/v1/models/export`
returns. So in principle this directory should hold one JSON file per model,
or a single `models.json`, and the function-sync container would reconcile them.

## Why it's not wired up yet

The user's LLM access is via:
1. Anthropic API (native integration in Open WebUI)
2. OpenRouter (as the OpenAI-compatible endpoint at OPENAI_API_BASE_URL)

For (1), models come from Anthropic directly — no custom model entry needed.

For (2), the user is currently relying on someone else's OpenRouter
translation pipe/filter to handle model routing. Until that's refactored,
the model definitions that would live here are coupled to a filter that
isn't yet in this repo. Defining them here in isolation would either:
  - duplicate what the filter does, or
  - conflict with what the filter does.

So this is parked until the OpenRouter filter refactor lands in `functions/`.
Once it does:
  - Each custom model = one .json file here (id, name, base_model, params, meta)
  - function-sync gains a `sync_models()` step that calls `/api/v1/models/sync`
  - `models/README.md` gets the file format documented

Reference: https://docs.openwebui.com/reference/api-endpoints/#programmatic-model-management-export-import-sync
