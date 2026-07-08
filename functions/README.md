# functions/

Open WebUI Functions live here. Each file is one function, written as the
same Python source the Open WebUI workspace editor saves. The function-sync
container (runs at `podman compose up`) parses each file's frontmatter
docstring and pushes them all to Open WebUI via `POST /api/v1/functions/sync`.

## File format

```python
"""
title: My Filter
author: dAutist
version: 0.1.0
required_open_webui_version: 0.5.0
type: filter
escription: one-line description shown in the workspace
"""

class Filter:
    class Valves(BaseModel):
        api_key: str = ""

    def inlet(self, body, __user__):
        return body
```

## Sync semantics

- Deletions in this directory propagate to Open WebUI: if you `git rm` a
  file here and bring the stack back up, the corresponding function is
  removed from the instance.
- Edits to file content update the function in place.
- New files create new functions.
- The function id is the lowercased filename (sans `.py`). It must be a
  valid Python identifier: lowercase, alphanumeric + underscore.

## Editing workflow

1. Edit a file in this directory.
2. `podman compose up -d --build function-sync` (or just `up -d` — the
   one-shot service runs on every `up`).
3. Check the sync container logs: `podman compose logs function-sync`.

## Migrating from an existing Open WebUI instance

1. In the old instance: Admin > Functions > export each as Python.
2. Drop them in this directory.
3. `git add functions/ && git commit`.
4. `podman compose up -d`.

## Anthropic / OpenRouter filters

These two are typically the ones you migrate first (your existing filters
from the previous instance). Once the openrouter filter refactor is
finished, the new version lands here too.
