"""Acceso a los datos de Sherpas: MCP (fuente principal) con caché en memoria y respaldo local `cases/`."""

from __future__ import annotations

import json
import logging
import re
from contextlib import AsyncExitStack
from pathlib import Path
from typing import Any, Literal, Protocol

logger = logging.getLogger("sherpy.data")

DEFAULT_CASES_DIR = Path(__file__).resolve().parents[2] / "cases"
PARTS = ("financial-health", "investment-analysis", "legal-values", "persona", "signals")
MARKDOWN_PARTS = {"persona"}


class CaseNotFound(ValueError):
    pass


class Backend(Protocol):
    async def list_cases(self) -> list[dict[str, Any]]: ...
    async def get_case(self, case_id: str, part: str | None = None, section: str | None = None) -> Any: ...


def _md_sections(text: str) -> dict[str, str]:
    sections: dict[str, str] = {}
    for chunk in re.split(r"(?m)^(?=## )", text):
        if chunk.startswith("## "):
            title = chunk.splitlines()[0][3:].strip()
            sections[title] = chunk.strip()
    return sections


def _match_section(keys: list[str], section: str) -> str:
    if section in keys:
        return section
    low = section.strip().lower()
    hits = [k for k in keys if k.lower() == low] or [k for k in keys if k.lower().startswith(low)]
    if len(hits) == 1:
        return hits[0]
    raise KeyError(f"Sección '{section}' no encontrada. Disponibles: {keys}")


def _approx_tokens(value: Any) -> int:
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
    return max(len(text) // 4, 1)


class LocalBackend:
    """Lee `cases/<caseId>/<part>.json|.md` (misma forma que el MCP)."""

    def __init__(self, cases_dir: Path = DEFAULT_CASES_DIR) -> None:
        self.cases_dir = cases_dir

    def _case_dir(self, case_id: str) -> Path:
        d = self.cases_dir / case_id
        if not d.is_dir():
            raise CaseNotFound(f"Caso '{case_id}' no existe.")
        return d

    def _load_part(self, case_id: str, part: str) -> Any:
        if part not in PARTS:
            raise KeyError(f"Parte '{part}' no válida. Opciones: {list(PARTS)}")
        d = self._case_dir(case_id)
        if part in MARKDOWN_PARTS:
            return (d / f"{part}.md").read_text()
        path = d / f"{part}.json"
        if not path.exists() and part == "legal-values":
            path = next(self.cases_dir.glob("*/legal-values.json"))
        return json.loads(path.read_text())

    async def list_cases(self) -> list[dict[str, Any]]:
        out = []
        for d in sorted(p for p in self.cases_dir.iterdir() if p.is_dir()):
            fh = json.loads((d / "financial-health.json").read_text())
            case = {k: v for k, v in fh["case"].items() if k != "slug"}
            out.append({"id": d.name, **case, "score": fh["score"]["published_score"],
                        "band": fh["score"]["band"], "parts": list(PARTS)})
        return out

    async def get_case(self, case_id: str, part: str | None = None, section: str | None = None) -> Any:
        if part is None:
            header = next((c for c in await self.list_cases() if c["id"] == case_id), None)
            if header is None:
                raise CaseNotFound(f"Caso '{case_id}' no existe.")
            parts: dict[str, Any] = {}
            for p in PARTS:
                data = self._load_part(case_id, p)
                secs = _md_sections(data) if isinstance(data, str) else data
                parts[p] = {"format": "markdown" if p in MARKDOWN_PARTS else "json",
                            "approx_tokens": _approx_tokens(data),
                            "sections": {k: _approx_tokens(v) for k, v in secs.items()}}
            return {**header, "parts": parts}
        data = self._load_part(case_id, part)
        if section is None:
            return data
        if isinstance(data, str):
            secs = _md_sections(data)
            return secs[_match_section(list(secs), section)]
        return data[_match_section(list(data), section)]


class MCPBackend:
    """Cliente directo al MCP de Sherpas (streamable HTTP + Bearer). Abrir con `async with`."""

    def __init__(self, url: str, token: str) -> None:
        self.url = url
        self.token = token
        self._stack: AsyncExitStack | None = None
        self._session: Any = None

    async def __aenter__(self) -> MCPBackend:
        from mcp import ClientSession
        from mcp.client.streamable_http import streamable_http_client
        from mcp.shared._httpx_utils import create_mcp_http_client

        stack = AsyncExitStack()
        try:
            http = await stack.enter_async_context(
                create_mcp_http_client(headers={"Authorization": f"Bearer {self.token}"}))
            read, write = await stack.enter_async_context(streamable_http_client(self.url, http_client=http))
            session = await stack.enter_async_context(ClientSession(read, write))
            await session.initialize()
        except BaseException:
            await stack.aclose()
            raise
        self._stack, self._session = stack, session
        return self

    async def __aexit__(self, *exc: object) -> None:
        if self._stack:
            await self._stack.aclose()
        self._stack = self._session = None

    async def _call(self, name: str, args: dict[str, Any]) -> Any:
        if self._session is None:
            raise RuntimeError("Sesión MCP no abierta.")
        r = await self._session.call_tool(name, args)
        if getattr(r, "isError", False) or getattr(r, "is_error", False):
            text = r.content[0].text if r.content else "error"
            raise KeyError(f"MCP {name}: {text}")
        text = r.content[0].text
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return text  # una parte Markdown entera llega como texto plano

    async def list_cases(self) -> list[dict[str, Any]]:
        return await self._call("list_cases", {})

    async def get_case(self, case_id: str, part: str | None = None, section: str | None = None) -> Any:
        args: dict[str, Any] = {"caseId": case_id}
        if part:
            args["part"] = part
        if section:
            args["section"] = section
        payload = await self._call("get_case", args)
        if part and isinstance(payload, dict) and "value" in payload:
            return payload["value"]
        return payload


Source = Literal["auto", "local", "mcp"]


class CaseSource:
    """Fachada usada por el agente y la API, con caché por (caseId, part, section).

    En cada lectura se elige la fuente:
    - "local": copia de `cases/` (sin red, milisegundos). Si no tiene el dato, pasa al MCP.
    - "mcp": API oficial de Sherpas, pidiendo justo la sección. Si falla, pasa a local.
    - "auto": caché → local si lo tiene → MCP.
    """

    def __init__(self, primary: Backend | None = None, fallback: Backend | None = None) -> None:
        self.fallback: Backend = fallback or LocalBackend()
        self.primary = primary
        self._cache: dict[tuple[str, str | None, str | None], tuple[Any, str]] = {}
        self._cases: list[dict[str, Any]] | None = None

    @property
    def origin(self) -> str:
        return "mcp" if self.primary else "local"

    async def _try(self, backend: Backend | None, name: str, method: str, *args: Any) -> tuple[bool, Any]:
        if backend is None:
            return False, None
        try:
            return True, await getattr(backend, method)(*args)
        except CaseNotFound:
            raise
        except KeyError:
            if name == "mcp":
                raise
            return False, None  # local no tiene esa sección/parte
        except Exception as e:  # red, auth, sesión caída…
            logger.warning("%s falló en %s%s (%s)", name, method, args, e)
            return False, None

    async def _read(self, prefer: Source, method: str, *args: Any) -> tuple[Any, str]:
        order: list[tuple[str, Backend | None]] = (
            [("mcp", self.primary), ("local", self.fallback)] if prefer == "mcp"
            else [("local", self.fallback), ("mcp", self.primary)]
        )
        last_key_error: KeyError | None = None
        for name, backend in order:
            try:
                ok, data = await self._try(backend, name, method, *args)
            except KeyError as e:
                last_key_error = e
                continue
            if ok:
                return data, name
        if last_key_error:
            raise last_key_error
        raise KeyError(f"Ninguna fuente pudo servir {method}{args}.")

    async def list_cases(self) -> list[dict[str, Any]]:
        if self._cases is None:
            self._cases, _ = await self._read("local", "list_cases")
        return self._cases

    async def get_case_with_origin(self, case_id: str, part: str | None = None, section: str | None = None,
                                   prefer: Source = "auto") -> tuple[Any, str]:
        """Devuelve (datos, origen) con origen "cache", "local" o "mcp"."""
        key = (case_id, part, section)
        if key in self._cache and prefer != "mcp":
            return self._cache[key][0], "cache"
        if key in self._cache and self._cache[key][1] == "mcp":
            return self._cache[key][0], "cache"
        data, origin = await self._read(prefer, "get_case", case_id, part, section)
        self._cache[key] = (data, origin)
        return data, origin

    async def get_case(self, case_id: str, part: str | None = None, section: str | None = None,
                       prefer: Source = "auto") -> Any:
        return (await self.get_case_with_origin(case_id, part, section, prefer))[0]

    async def financial_health(self, case_id: str, *sections: str) -> dict[str, Any]:
        """Compone un dict parcial de financial-health con solo las secciones pedidas (local primero: rápido)."""
        return {s: await self.get_case(case_id, "financial-health", s) for s in sections}

    async def legal(self, case_id: str) -> dict[str, Any]:
        return await self.get_case(case_id, "legal-values")
