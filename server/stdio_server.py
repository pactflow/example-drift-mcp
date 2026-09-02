#!/usr/bin/env python3
"""stdio-transport variant of the Drift MCP plugin example server.

Reuses every tool/resource/prompt definition and the `dispatch` logic from `server.py`; only the
transport differs — this variant reads newline-delimited JSON-RPC requests from stdin and writes
newline-delimited JSON-RPC responses to stdout, instead of listening over HTTP.

Modes (identical semantics to server.py):
  (default)   modern era (2026-07-28): stateless, answers server/discover
  --legacy    legacy era (2025-06-18): initialize handshake
  --drift     deliberately diverge from notes.mcp.yaml to demonstrate detection

It must never write anything to stdout that is not an MCP message: all logging goes to stderr.

Standard library only.
"""
import argparse
import json
import sys
import uuid
from pathlib import Path

# Reuse the tool/resource/prompt definitions and dispatch logic verbatim rather than
# duplicating them; only the transport in this file differs from server.py.
sys.path.insert(0, str(Path(__file__).resolve().parent))
import server as notes  # noqa: E402  (import after sys.path manipulation)

MODERN_VERSION = notes.MODERN_VERSION
LEGACY_VERSION = notes.LEGACY_VERSION


def log(message):
    """Writes a diagnostic line to stderr. Never write diagnostics to stdout."""
    print(f"[mcp-stdio] {message}", file=sys.stderr, flush=True)


def send(message):
    """Writes one newline-delimited JSON-RPC message to stdout and flushes it."""
    sys.stdout.write(json.dumps(message) + "\n")
    sys.stdout.flush()


def send_result(request_id, result):
    send({"jsonrpc": "2.0", "id": request_id, "result": result})


def send_error(request_id, error):
    send({"jsonrpc": "2.0", "id": request_id, "error": error})


def handle_modern(method, request_id, params):
    if method == "server/discover":
        notes.reset_store()
        send_result(
            request_id,
            {
                "supportedVersions": [MODERN_VERSION],
                "capabilities": {"tools": {}, "resources": {}, "prompts": {}},
                "instructions": "Notes server example (stdio)",
                "ttlMs": 60000,
                "cacheScope": "public",
            },
        )
        return

    result, error = notes.dispatch(method, params)
    if error:
        send_error(request_id, error)
    else:
        send_result(request_id, result)


def handle_legacy(method, request_id, params):
    if method == "server/discover":
        # There is no HTTP status code over stdio to carry the "fall back to initialize"
        # signal, so this responds with a JSON-RPC error instead. The error must NOT be one
        # era.rs::is_recognised_modern_error treats as a modern-but-needs-retry signal (that
        # set includes code -32601 and a handful of header-mismatch messages) — those resolve
        # to EraProbe::RetryModern, which negotiate_era treats as a modern server, not a
        # legacy one. An unrecognised code/message falls through to EraProbe::Legacy, which is
        # what we want here.
        send_error(
            request_id,
            {"code": -32000, "message": "server/discover is not implemented by this server"},
        )
        return

    if method == "initialize":
        notes.reset_store()
        send_result(
            request_id,
            {
                "protocolVersion": LEGACY_VERSION,
                "capabilities": {"tools": {}, "resources": {}, "prompts": {}},
                "serverInfo": {"name": "notes", "version": "1.0.0"},
            },
        )
        return

    result, error = notes.dispatch(method, params)
    if error:
        send_error(request_id, error)
    else:
        send_result(request_id, result)


def main():
    parser = argparse.ArgumentParser(description="stdio-transport notes MCP server")
    parser.add_argument("--legacy", action="store_true", help="use the initialize handshake era")
    parser.add_argument("--drift", action="store_true", help="diverge from the definition")
    args = parser.parse_args()

    # server.py's tool/resource/dispatch functions read this module-level global.
    notes.ARGS = args

    era = "legacy" if args.legacy else "modern"
    drift = " (drift enabled)" if args.drift else ""
    log(f"stdio notes MCP server starting [{era}]{drift}")

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            message = json.loads(line)
        except json.JSONDecodeError:
            log(f"failed to parse line as JSON: {line!r}")
            continue

        method = message.get("method")
        request_id = message.get("id")
        params = message.get("params") or {}

        if request_id is None:
            # Notifications (e.g. notifications/initialized) are fire-and-forget.
            log(f"received notification: {method}")
            continue

        if args.legacy:
            handle_legacy(method, request_id, params)
        else:
            handle_modern(method, request_id, params)

    log("stdin closed; exiting")


if __name__ == "__main__":
    main()
