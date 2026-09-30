"""Small diagnostic trace helper for verbose calculation runs."""
from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Any


class DiagnosticTrace:
    def __init__(self, enabled: bool = False, prefix: str = "PCM TRACE"):
        self.enabled = bool(enabled)
        self.prefix = prefix
        self.events: list[dict[str, Any]] = []
        self.logger = logging.getLogger("costoptima.diagnostics")

    def add(self, step: str, message: str, **details: Any) -> None:
        event = {
            "time": datetime.utcnow().isoformat(timespec="seconds") + "Z",
            "step": step,
            "message": message,
            "details": _json_safe(details),
        }
        self.events.append(event)
        if self.enabled:
            line = self.format_event(event)
            print(line, flush=True)
            self.logger.info(line)

    def section(self, message: str, **details: Any) -> None:
        self.add("SECTION", message, **details)

    def warning(self, message: str, **details: Any) -> None:
        self.add("WARNING", message, **details)

    def error(self, message: str, **details: Any) -> None:
        self.add("ERROR", message, **details)

    def format_event(self, event: dict[str, Any]) -> str:
        details = event.get("details") or {}
        suffix = ""
        if details:
            text = json.dumps(details, ensure_ascii=False, default=str)
            if len(text) > 1800:
                text = text[:1800] + "...<truncated>"
            suffix = f" | {text}"
        return f"[{self.prefix}] {event['step']}: {event['message']}{suffix}"

    def summary_lines(self, limit: int = 500) -> list[str]:
        lines = [self.format_event(event) for event in self.events]
        if len(lines) > limit:
            return lines[:limit] + [f"[{self.prefix}] SUMMARY: {len(lines) - limit} additional trace lines omitted from API logs; see console output for full trace."]
        return lines


def _json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(k): _json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(v) for v in value]
    if hasattr(value, "isoformat"):
        try:
            return value.isoformat()
        except Exception:
            pass
    if hasattr(value, "item"):
        try:
            return _json_safe(value.item())
        except Exception:
            pass
    return value
