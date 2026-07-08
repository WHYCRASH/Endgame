"""
title: OpenWebUI Function Author
author: Endgame
version: 0.1.0
required_open_webui_version: 0.5.0
type: tool
description: Write, update, list, and delete Open WebUI functions and tools via the API. Useful for iteratively building filters/pipes/tools during a chat.
"""

from typing import Optional, List, Dict, Any
import json
import requests


class Tools:
    class Valves(BaseModel):
        openwebui_url: str = "http://open-webui:8080"
        openwebui_api_key: str = ""

    def _headers(self) -> Dict[str, str]:
        return {
            "Authorization": f"Bearer {self.valves.openwebui_api_key}",
            "Content-Type": "application/json",
        }

    def _url(self, path: str) -> str:
        return f"{self.valves.openwebui_url.rstrip('/')}{path}"

    def write_function(
        self,
        function_id: str,
        name: str,
        description: str,
        function_type: str,
        python_source: str,
        is_global: bool = False,
        __event_emitter__: Optional[Any] = None,
    ) -> str:
        """
        Write or update an Open WebUI Function. Creates if the id doesn't exist,
        updates if it does. Returns a status string.

        Args:
            function_id: Python identifier, lowercase alphanumeric + underscore. E.g. 'anthropic_filter'.
            name: Human-readable name shown in the workspace.
            description: One-line description.
            function_type: One of 'filter', 'pipe', 'pipeline'.
            python_source: Complete Python source. MUST include a triple-quoted
                           frontmatter docstring at the top with at least:
                           title, type, description. MUST be syntactically valid.
            is_global: If True, set the function as global after writing.
        """
        if __event_emitter__:
            await __event_emitter__(
                {"type": "status", "data": {"description": f"Writing function {function_id}..."}}
            )

        fid = function_id.lower()
        if not fid.isidentifier():
            return f"ERROR: id '{fid}' is not a valid Python identifier"

        # Ensure the frontmatter docstring is present. If the source already
        # starts with a triple-quote, trust the model. Otherwise inject one.
        src = python_source.lstrip()
        if not src.startswith('"""'):
            fm = (
                f'"""\n'
                f'title: {name}\n'
                f'version: 0.1.0\n'
                f'type: {function_type}\n'
                f'description: {description}\n'
                f'"""\n\n'
            )
            src = fm + src

        form = {
            "id": fid,
            "name": name,
            "content": src,
            "meta": {"description": description},
            "access_grants": [],
        }

        # Try update first; on 404 fall back to create.
        r = requests.post(
            self._url(f"/api/v1/functions/id/{fid}/update"),
            headers=self._headers(),
            json=form,
            timeout=60,
        )
        if r.status_code == 404 or (r.ok and r.json() is None):
            r = requests.post(
                self._url("/api/v1/functions/create"),
                headers=self._headers(),
                json=form,
                timeout=60,
            )

        if not r.ok:
            return f"ERROR: {r.status_code} {r.text[:500]}"

        if is_global:
            requests.post(
                self._url(f"/api/v1/functions/id/{fid}/toggle/global"),
                headers=self._headers(),
                timeout=30,
            )

        return f"OK: function '{fid}' saved. is_global={is_global}"

    def write_tool(
        self,
        tool_id: str,
        name: str,
        description: str,
        python_source: str,
        __event_emitter__: Optional[Any] = None,
    ) -> str:
        """
        Write or update an Open WebUI Tool. Creates if the id doesn't exist,
        updates if it does. Returns a status string.

        Args:
            tool_id: Python identifier, lowercase alphanumeric + underscore.
            name: Human-readable name.
            description: One-line description.
            python_source: Complete Python source. MUST include a triple-quoted
                           frontmatter docstring at the top with at least:
                           title, type: tool, description. MUST be syntactically
                           valid. Must define a class Tools with methods the
                           LLM can call.
        """
        if __event_emitter__:
            await __event_emitter__(
                {"type": "status", "data": {"description": f"Writing tool {tool_id}..."}}
            )

        tid = tool_id.lower()
        if not tid.isidentifier():
            return f"ERROR: id '{tid}' is not a valid Python identifier"

        src = python_source.lstrip()
        if not src.startswith('"""'):
            fm = (
                f'"""\n'
                f'title: {name}\n'
                f'version: 0.1.0\n'
                f'type: tool\n'
                f'description: {description}\n'
                f'"""\n\n'
            )
            src = fm + src

        form = {
            "id": tid,
            "name": name,
            "content": src,
            "meta": {"description": description},
            "access_grants": [],
        }

        r = requests.post(
            self._url(f"/api/v1/tools/id/{tid}/update"),
            headers=self._headers(),
            json=form,
            timeout=60,
        )
        if r.status_code == 404 or (r.ok and r.json() is None):
            r = requests.post(
                self._url("/api/v1/tools/create"),
                headers=self._headers(),
                json=form,
                timeout=60,
            )

        if not r.ok:
            return f"ERROR: {r.status_code} {r.text[:500]}"
        return f"OK: tool '{tid}' saved."

    def list_functions(self) -> str:
        """List all functions on the Open WebUI instance."""
        r = requests.get(
            self._url("/api/v1/functions/export"),
            headers=self._headers(),
            timeout=60,
        )
        if not r.ok:
            return f"ERROR: {r.status_code} {r.text[:300]}"
        out = []
        for f in r.json():
            out.append(f"  - {f['id']:40s} {f.get('name', '')}  [{f.get('type', '?')}]")
        return f"Functions ({len(out)}):\n" + "\n".join(out)

    def list_tools(self) -> str:
        """List all local tools on the Open WebUI instance (excludes MCP/OpenAPI tool servers)."""
        r = requests.get(
            self._url("/api/v1/tools/export"),
            headers=self._headers(),
            timeout=60,
        )
        if not r.ok:
            return f"ERROR: {r.status_code} {r.text[:300]}"
        out = []
        for t in r.json():
            out.append(f"  - {t['id']:40s} {t.get('name', '')}")
        return f"Tools ({len(out)}):\n" + "\n".join(out)

    def delete_function(self, function_id: str) -> str:
        """Delete a function by id."""
        r = requests.delete(
            self._url(f"/api/v1/functions/id/{function_id.lower()}/delete"),
            headers=self._headers(),
            timeout=60,
        )
        return "OK" if r.ok else f"ERROR: {r.status_code} {r.text[:300]}"

    def delete_tool(self, tool_id: str) -> str:
        """Delete a tool by id."""
        r = requests.delete(
            self._url(f"/api/v1/tools/id/{tool_id.lower()}/delete"),
            headers=self._headers(),
            timeout=60,
        )
        return "OK" if r.ok else f"ERROR: {r.status_code} {r.text[:300]}"
