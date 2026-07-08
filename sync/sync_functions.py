#!/usr/bin/env python3
"""Bootstrap sync: reconcile local functions/ + tools/ into Open WebUI.

Reads .py files from $FUNCTIONS_DIR and $TOOLS_DIR, parses each file's
Open WebUI frontmatter docstring, and reconciles them into a running
Open WebUI instance via its REST API.

Functions: declarative `POST /api/v1/functions/sync` — Open WebUI creates,
updates, and deletes to match exactly what we send.
Tools: manual diff. There is no `/sync` endpoint for tools; we export the
current set, diff against local files, and call /create /id/{id}/update
/id/{id}/delete as needed. Deletions in the repo propagate.

Layout:
  functions/<id>.py                       flat, id = lowercased stem
  tools/<id>.py                           flat, id = lowercased stem
  tools/<dir>/main.py                     subdir layout, id = lowercased dir name
  tools/<dir>/__init__.py                 subdir layout, id = lowercased dir name

Auth: requires an admin API key (functions/sync and tools/create are
admin-only). Pass via $OPENWEBUI_API_KEY.
"""

from __future__ import annotations

import os
import re
import sys
import time
from pathlib import Path
from typing import Any

import requests
import yaml


OPENWEBUI_URL = os.environ.get("OPENWEBUI_URL", "http://open-webui:8080").rstrip("/")
OPENWEBUI_API_KEY = os.environ.get("OPENWEBUI_API_KEY")
FUNCTIONS_DIR = Path(os.environ.get("FUNCTIONS_DIR", "/functions"))
TOOLS_DIR = Path(os.environ.get("TOOLS_DIR", "/tools"))

# Files that are documentation, not tool/function source.
IGNORE = {"README.md", "README.txt", "README.rst", ".gitkeep", "__pycache__"}


def _headers() -> dict[str, str]:
    if not OPENWEBUI_API_KEY:
        sys.exit("OPENWEBUI_API_KEY is not set")
    return {
        "Authorization": f"Bearer {OPENWEBUI_API_KEY}",
        "Content-Type": "application/json",
    }


def _wait_for_open_webui(timeout: int = 300) -> None:
    """Poll /health until Open WebUI is up. Belt-and-braces on top of the
    compose `depends_on: condition: service_healthy`."""
    deadline = time.time() + timeout
    last_err: str | None = None
    while time.time() < deadline:
        try:
            r = requests.get(f"{OPENWEBUI_URL}/health", timeout=5)
            if r.ok:
                return
            last_err = f"{r.status_code} {r.text[:200]}"
        except Exception as e:  # noqa: BLE001
            last_err = str(e)
        time.sleep(2)
    sys.exit(f"Open WebUI not reachable at {OPENWEBUI_URL}/health within {timeout}s: {last_err}")


# ---------------------------------------------------------------------------
# Frontmatter parser
# ---------------------------------------------------------------------------
# Open WebUI function/tool files start with a triple-quoted docstring whose
# body is YAML-ish (key: value lines). We parse it the same way Open WebUI
# does: pull out the first `"""..."""` block, parse the lines as YAML.

_FRONTMATTER_RE = re.compile(r'^\s*"""(.*?)"""', re.DOTALL)


def parse_frontmatter(content: str) -> dict[str, Any]:
    """Return the parsed frontmatter dict, or {} if none."""
    m = _FRONTMATTER_RE.match(content)
    if not m:
        return {}
    raw = m.group(1)
    try:
        parsed = yaml.safe_load(raw)
        if not isinstance(parsed, dict):
            return {}
        return parsed
    except yaml.YAMLError:
        return {}


def _load_one(path: Path, function_id: str) -> dict[str, Any] | None:
    """Read a single .py file and return its sync record, or None if skipped."""
    if not function_id.isidentifier():
        print(f"  WARN: '{path}' -> id '{function_id}' is not a valid Python identifier; skipping", file=sys.stderr)
        return None
    content = path.read_text(encoding="utf-8")
    fm = parse_frontmatter(content)
    name = fm.get("title") or function_id
    description = fm.get("description") or ""
    return {
        "id": function_id,
        "name": str(name),
        "content": content,
        "meta": {"description": description, "manifest": fm},
        "frontmatter": fm,
    }


def load_py_files(directory: Path) -> list[dict[str, Any]]:
    """Walk `directory` for Open WebUI function/tool source files.

    Recognized layouts (in priority order):
      - <dir>/<id>.py           flat file, id = lowercased stem
      - <dir>/<id>/main.py      subdir, id = lowercased subdir name
      - <dir>/<id>/__init__.py  subdir, id = lowercased subdir name

    If both <id>.py and <id>/main.py exist, the flat file wins.
    """
    out: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    if not directory.exists():
        return out

    # Pass 1: top-level .py files.
    for path in sorted(directory.iterdir()):
        if path.name in IGNORE:
            continue
        if path.is_file() and path.suffix == ".py":
            fid = path.stem.lower()
            if fid in seen_ids:
                continue
            rec = _load_one(path, fid)
            if rec:
                out.append(rec)
                seen_ids.add(fid)

    # Pass 2: subdirectory main.py / __init__.py.
    for sub in sorted(directory.iterdir()):
        if not sub.is_dir():
            continue
        if sub.name in IGNORE or sub.name.startswith("."):
            continue
        fid = sub.name.lower()
        if fid in seen_ids:
            continue
        for candidate in (sub / "main.py", sub / "__init__.py"):
            if candidate.is_file():
                rec = _load_one(candidate, fid)
                if rec:
                    out.append(rec)
                    seen_ids.add(fid)
                break

    return out


# ---------------------------------------------------------------------------
# Functions: declarative sync via POST /api/v1/functions/sync
# ---------------------------------------------------------------------------

def sync_functions() -> None:
    print("== functions ==")
    local = load_py_files(FUNCTIONS_DIR)
    print(f"  local: {len(local)} file(s)")
    for f in local:
        print(f"    - {f['id']:40s} {f['name']}")

    payload = {"functions": local}
    r = requests.post(
        f"{OPENWEBUI_URL}/api/v1/functions/sync",
        headers=_headers(),
        json=payload,
        timeout=120,
    )
    if not r.ok:
        print(f"  ERR  /functions/sync {r.status_code}: {r.text[:500]}", file=sys.stderr)
        sys.exit(1)
    after = r.json()
    print(f"  synced: {len(after)} function(s) on the instance")


# ---------------------------------------------------------------------------
# Tools: manual diff (no /sync endpoint)
# ---------------------------------------------------------------------------

def sync_tools() -> None:
    print("== tools ==")
    local = load_py_files(TOOLS_DIR)
    local_by_id = {t["id"]: t for t in local}
    print(f"  local: {len(local)} file(s)")
    for t in local:
        print(f"    - {t['id']:40s} {t['name']}")

    r = requests.get(
        f"{OPENWEBUI_URL}/api/v1/tools/export",
        headers=_headers(),
        timeout=60,
    )
    if not r.ok:
        print(f"  ERR  /tools/export {r.status_code}: {r.text[:500]}", file=sys.stderr)
        sys.exit(1)
    remote = r.json()
    if isinstance(remote, dict) and "data" in remote:
        remote = remote["data"]
    remote_by_id = {t["id"]: t for t in remote}
    print(f"  remote: {len(remote_by_id)} tool(s) on the instance")

    # 1. Create + update
    for tid, t in local_by_id.items():
        form = {
            "id": tid,
            "name": t["name"],
            "content": t["content"],
            "meta": t["meta"],
            "access_grants": [],
        }
        if tid in remote_by_id:
            if remote_by_id[tid].get("content") != t["content"]:
                rr = requests.post(
                    f"{OPENWEBUI_URL}/api/v1/tools/id/{tid}/update",
                    headers=_headers(),
                    json=form,
                    timeout=60,
                )
                if rr.ok:
                    print(f"  UPD  {tid}")
                else:
                    print(f"  ERR  update {tid} {rr.status_code}: {rr.text[:300]}", file=sys.stderr)
            else:
                print(f"  ok   {tid} (unchanged)")
        else:
            rr = requests.post(
                f"{OPENWEBUI_URL}/api/v1/tools/create",
                headers=_headers(),
                json=form,
                timeout=60,
            )
            if rr.ok:
                print(f"  NEW  {tid}")
            else:
                print(f"  ERR  create {tid} {rr.status_code}: {rr.text[:300]}", file=sys.stderr)

    # 2. Delete (deletions propagate)
    for tid in remote_by_id:
        if tid not in local_by_id:
            rr = requests.delete(
                f"{OPENWEBUI_URL}/api/v1/tools/id/{tid}/delete",
                headers=_headers(),
                timeout=60,
            )
            if rr.ok:
                print(f"  DEL  {tid}")
            else:
                print(f"  ERR  delete {tid} {rr.status_code}: {rr.text[:300]}", file=sys.stderr)


def main() -> int:
    if not OPENWEBUI_API_KEY:
        print("OPENWEBUI_API_KEY is not set", file=sys.stderr)
        return 2
    _wait_for_open_webui()
    sync_functions()
    sync_tools()
    print("done.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
