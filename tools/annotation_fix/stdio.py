"""Run one correction over a pipe: request JSON on stdin, response on stdout.

This is what the Scalabel server actually uses. Spawning a short-lived process
per import beats a long-running HTTP service here: nothing to start by hand,
nothing to keep alive, no port to collide, and no way to end up with a stale
daemon running old code. The process exists only for the duration of one
project creation.

Request (stdin), matching the HTTP service's body::

    {"document": <frames or full doc>, "image_root": "...", "clamp": true,
     "connect": true, "tolerance": 15.0, "cache_dir": "..."}

Response (stdout)::

    {"ok": true, "document": <corrected>, "report": {...}}
    {"ok": false, "error": "..."}

Anything the correction code prints to stdout would corrupt that response, so
stdout is kept exclusively for the JSON payload and diagnostics go to stderr.
"""

from __future__ import annotations

import json
import sys
from typing import Any, Dict

from .core import Options, process_document


def run(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Apply the requested corrections to one payload."""
    document = payload.get("document")
    if document is None:
        return {"ok": False, "error": "request is missing 'document'"}

    options = Options(
        clamp=bool(payload.get("clamp", True)),
        connect=bool(payload.get("connect", True)),
        threshold=int(payload.get("threshold", 10)),
        tolerance=float(payload.get("tolerance", 15.0)),
        min_angle=float(payload.get("min_angle", 0.0)),
        inset=float(payload.get("inset", 1.5)),
        flag_distance=float(payload.get("flag_distance", 50.0)),
        image_root=str(payload.get("image_root") or ""),
        cache_dir=payload.get("cache_dir") or None,
    )

    corrected, report = process_document(document, options)
    return {"ok": True, "document": corrected, "report": report.to_dict()}


def main() -> int:
    """Read one request from stdin, write one response to stdout."""
    try:
        payload = json.load(sys.stdin)
    except json.JSONDecodeError as exc:
        json.dump({"ok": False, "error": f"invalid request JSON: {exc}"}, sys.stdout)
        return 1

    try:
        response = run(payload)
    except Exception as exc:  # noqa: BLE001 - reported to the caller instead
        json.dump(
            {"ok": False, "error": f"{type(exc).__name__}: {exc}"}, sys.stdout
        )
        return 1

    json.dump(response, sys.stdout)
    return 0 if response.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
