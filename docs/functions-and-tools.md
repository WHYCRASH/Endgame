# Functions and tools

This doc explains how the git-managed functions and tools in this repo get into Open WebUI, and how the LLM can author new ones from chat.

## Two-way flow

```
              git (this repo)                 Open WebUI instance
              ───────────────                 ────────────────────
              functions/*.py
              tools/*/main.py      ───────►   POST /api/v1/functions/sync
                                   function-   POST /api/v1/tools/{create,update,delete}
                                   sync        (declarative; deletions propagate)

                                                Open WebUI instance
                                                ────────────────────
              functions/*.py                    Workspace > Functions
              tools/*/main.py      ◄───────    (LLM calls write_function via
                                   export      openwebui_function_author tool,
                                   manually    then user pastes source into
                                               the repo and commits)
```

Left-to-right is automated (function-sync container). Right-to-left is manual (export from the UI or copy from chat, paste into a file, commit).

## File format

Every file - whether it's destined for `functions/` or `tools/` - is the same Python source that Open WebUI's workspace editor saves. It starts with a triple-quoted docstring whose body is parsed as YAML for the frontmatter.

### Function example

`functions/my_filter.py`:

```python
"""
title: My Filter
author: dAutist
version: 0.1.0
required_open_web_ui_version: 0.5.0
type: filter
description: one-line description shown in the workspace
"""

from typing import Optional
from pydantic import BaseModel


class Filter:
    class Valves(BaseModel):
        api_key: str = ""

    def inlet(self, body, __user__):
        # Modify body here before it goes to the LLM
        return body
```

### Tool example

`tools/my_tool/main.py` (subdir layout) or `tools/my_tool.py` (flat layout):

```python
"""
title: My Tool
author: dAutist
version: 0.1.0
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

## Frontmatter fields

| Field | Required | Notes |
|---|---|---|
| `title` | yes | Human-readable name shown in the workspace. |
| `type` | yes | `filter`, `pipe`, `pipeline`, or `tool`. Open WebUI auto-detects from the class definition if you omit it, but explicit is better. |
| `description` | recommended | One-liner shown in the workspace list. |
| `version` | optional | For your own tracking. |
| `author` | optional | For your own tracking. |
| `required_open_web_ui_version` | optional | Skipped if not present. |

Other frontmatter fields Open WebUI recognises (e.g. `requirements`, `permissions`) also pass through; the sync script doesn't strip anything.

## ID derivation

The function or tool id is derived from the file path:
- `functions/my_filter.py` -> id `my_filter`
- `tools/my_tool.py` -> id `my_tool`
- `tools/my_tool/main.py` -> id `my_tool` (subdir layout, takes the directory name)
- `tools/my_tool/__init__.py` -> id `my_tool` (subdir layout, takes the directory name)

The id must be a valid Python identifier: lowercase, alphanumeric + underscore. The sync script lowercases it for you.

If both `tools/foo.py` and `tools/foo/main.py` exist, the flat file wins.

## Sync semantics

### Functions

`POST /api/v1/functions/sync` is declarative: Open WebUI makes its instance match the payload exactly. That means:
- New file in `functions/` -> function is created.
- Edited file -> function is updated.
- Deleted file -> function is deleted from the instance.
- File renamed -> old id deleted, new id created (treated as delete + create).

This is what you want for a git-managed workflow: `git rm functions/foo.py && podman compose up -d` removes the function from the instance.

### Tools

There is no `/sync` endpoint for tools. The sync script does the same thing manually:
1. `GET /api/v1/tools/export` to get the current set.
2. For each local file: if the id exists in the export and the content changed, `POST /api/v1/tools/id/{id}/update`. If the id doesn't exist, `POST /api/v1/tools/create`.
3. For each tool in the export that has no corresponding local file: `DELETE /api/v1/tools/id/{id}/delete`.

Deletions propagate, same as functions.

Tools that come from MCP/OpenAPI servers (the `server:mcp:...` and `server:...` ids) are NOT in the `/tools/export` response, so the sync script will never accidentally delete them.

## The authoring tool

`tools/openwebui_function_author/main.py` is a tool the LLM can call from chat. It exposes:

- `write_function(function_id, name, description, function_type, python_source, is_global=False)` - upsert a function.
- `write_tool(tool_id, name, description, python_source)` - upsert a tool.
- `list_functions()` - return all functions on the instance.
- `list_tools()` - return all local tools on the instance.
- `delete_function(function_id)` - delete a function.
- `delete_tool(tool_id)` - delete a tool.

It uses `POST /api/v1/functions/id/{id}/update` (falling back to `/create` on 404) instead of `/sync` because `/sync` is destructive - the LLM calling `/sync` with a partial payload would wipe anything not in the request.

### Setting it up

1. First `podman compose up -d` installs the tool via function-sync.
2. In Open WebUI: Workspace > Tools > find `openwebui_function_author` > enable.
3. In a model editor (Workspace > Models > edit a model): attach the tool.
4. Chat with that model: "write a filter that..." - the model calls `write_function`.
5. The function is live on the instance immediately.
6. To make it permanent: copy the source from the chat into `functions/<id>.py`, commit, done. Next `up` reconciles it.

### Why both write_function AND write_tool

Because Open WebUI distinguishes functions (filters/pipes/pipelines that hook the chat pipeline) from tools (Python classes with methods the LLM calls). The LLM might be asked to write either. The authoring tool exposes both APIs.

## Auth

All endpoints used by function-sync and the authoring tool are admin-only:
- `POST /api/v1/functions/sync`
- `POST /api/v1/functions/create`
- `POST /api/v1/functions/id/{id}/update`
- `DELETE /api/v1/functions/id/{id}/delete`
- `POST /api/v1/tools/create` (admin OR `workspace.tools` permission)
- `POST /api/v1/tools/id/{id}/update` (admin OR `workspace.tools`)
- `DELETE /api/v1/tools/id/{id}/delete` (admin OR write access)
- `GET /api/v1/functions/export` (admin)
- `GET /api/v1/tools/export` (admin OR `workspace.tools_export`)

So the API key in `OPENWEBUI_ADMIN_API_KEY` must be an admin's. Generate it in Open WebUI: Settings > Account > API Keys.

## What's NOT synced

- Models (custom model definitions) - parked at `models/README.md` until the OpenRouter filter refactor lands.
- Prompts (workspace prompts) - no API endpoint for declarative sync in current Open WebUI.
- Notes, skills, knowledge bases - same; no `/sync` endpoint.
- MCP/OpenAPI tool server connections - those are config (the `TOOL_SERVER_CONNECTIONS` env var), not workspace items.

## Common operations

### Add a new function

```bash
$EDITOR functions/my_filter.py
# write the frontmatter + class
git add functions/my_filter.py
git commit -m 'add my_filter'
podman compose up -d  # function-sync runs, installs it
podman compose logs function-sync | tail
```

### Edit a function

Same as add - just edit the file and `podman compose up -d`. function-sync detects the content change and calls `/update`.

### Delete a function

```bash
git rm functions/my_filter.py
git commit -m 'remove my_filter'
podman compose up -d  # function-sync calls /sync without it; Open WebUI deletes it
```

### Have the LLM write a function, then commit it

1. Chat with a model that has `openwebui_function_author` enabled.
2. Ask: "write a filter that..."
3. The model calls `write_function` and reports success.
4. The function is live. Test it.
5. When you're happy: copy the source from the chat into `functions/<id>.py`.
6. `git add functions/<id>.py && git commit && podman compose up -d`.

The repo is now the source of truth for that function.

### Re-sync without rebooting the whole stack

```bash
podman compose up -d --build function-sync
# or, to force a re-run:
podman compose run --rm function-sync
```

## Troubleshooting

### function-sync exits non-zero

Check `podman compose logs function-sync`:
- `OPENWEBUI_API_KEY is not set` -> you forgot to set `OPENWEBUI_ADMIN_API_KEY` in `.env`.
- `Open WebUI not reachable at...` -> open-webui isn't healthy yet. Check `podman compose ps`.
- `ERR /functions/sync 401` -> the API key isn't an admin's. Generate one in Settings > Account > API Keys.
- `ERR /functions/sync 400: Error loading function` -> the Python source has a syntax error or the frontmatter is malformed. Fix the file, commit, re-run.
- `WARN: ... is not a valid Python identifier` -> the filename has dashes or other non-identifier chars. Rename to underscores.

### Authoring tool doesn't appear in Open WebUI

Check that function-sync ran successfully. Then in Open WebUI: Workspace > Tools. If it's not there, the sync script's subdir walk didn't find it - check `podman compose logs function-sync` for the `local:` line under `== tools ==`.

### Authoring tool is there but the model won't call it

Make sure the tool is enabled on the specific model you're chatting with. In Workspace > Models > edit the model > Tools tab > toggle `openwebui_function_author`.

Some models need explicit prompting: "Use the openwebui_function_author tool to write a function that..."
