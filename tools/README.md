# tools/

Open WebUI Tools live here. Same file format as `functions/` — each file is
the Python source the workspace editor would save. The function-sync
container (runs at `podman compose up`) reconciles them into Open WebUI
via per-tool /create /update /delete (there's no /sync endpoint for tools,
so we diff manually).

## Subdirectories

- `openwebui_function_author/` — the bootstrap authoring tool. Once loaded
  into Open WebUI, the LLM can call it from chat to write/update/list/delete
  more functions and tools via the API. This tool bootstraps itself.

## File format

```python
"""
title: My Tool
author: dAutist
version: 0.1.0
required_open_webui_version: 0.5.0
type: tool
description: one-line description
"""

from typing import Optional


class Tools:
    class Valves(BaseModel):
        api_key: str = ""

    def do_something(self, arg: str) -> str:
        """Do the thing."""
        return f"did the thing with {arg}"
```

## Sync semantics

- Deletions in this directory propagate to Open WebUI.
- Each file's lowercased stem (sans `.py`) becomes the tool id.
- Subdirectories are NOT recursively walked — only top-level .py files
  and `openwebui_function_author/main.py` (which lives in a subdir).
  Wait, actually the sync script walks top-level only. To support the
  authoring tool being in a subdirectory, see sync_functions.py — it
  includes special handling. Actually no, it doesn't. To bootstrap the
  authoring tool, copy it to `tools/openwebui_function_author.py` instead
  of using a subdirectory, OR put the file at `tools/openwebui_function_author/main.py`
  and add a recursive walk. See notes below.

## Bootstrapping the authoring tool

The authoring tool needs to be installed in Open WebUI before the LLM can
use it. The function-sync container handles this on first `up`:

1. Drop `openwebui_function_author/main.py` somewhere the sync script can
   find it. Currently the sync script only walks top-level `tools/*.py`.
   To install the authoring tool, either:
   - Move it to `tools/openwebui_function_author.py` (flat layout), OR
   - Extend `sync_functions.py` to walk `tools/*/main.py` too.

   The flat layout is simpler. Use that.

2. `podman compose up -d` — function-sync runs, installs the tool.

3. In Open WebUI, the tool appears in Workspace > Tools. Enable it on a
   model in the model editor. Then chat with that model and ask it to
   write a function — it will call `write_function`.

## Why the authoring tool uses /create and /update, not /sync

`/sync` is destructive: it deletes functions not in the payload. The
LLM calling /sync would wipe anything not in the request. So the
authoring tool uses the safe upsert pattern: try /update, on 404 fall
back to /create. It never deletes.
