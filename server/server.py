#!/usr/bin/env python3
"""Minimal MCP server for the Drift MCP plugin example.

Modes:
  (default)   modern era (2026-07-28): stateless, answers server/discover
  --legacy    legacy era (2025-06-18): initialize handshake + Mcp-Session-Id
  --drift     deliberately diverge from notes.mcp.yaml to demonstrate detection

Standard library only.
"""
import argparse
import json
import uuid
from http.server import BaseHTTPRequestHandler, HTTPServer

MODERN_VERSION = "2026-07-28"
LEGACY_VERSION = "2025-06-18"

ARGS = None
SESSIONS = set()

# Seed state. The store is reset at the start of every verification run (see reset_store)
# so the example is re-runnable against a long-lived server process.
SEED_NOTES = {
    "1": {"title": "Welcome", "body": "Your first note."},
    "disposable": {"title": "Disposable", "body": "Exists to be deleted."},
}
NOTES = dict(SEED_NOTES)
NEXT_ID = [2]


def reset_store():
    """Restores the seed notes.

    Called when a client negotiates a protocol era, which happens exactly once per
    verification run. Without this, a destructive test would poison later runs against
    the same server process.
    """
    NOTES.clear()
    NOTES.update({k: dict(v) for k, v in SEED_NOTES.items()})
    NEXT_ID[0] = 2


def tools():
    read_note = {
        "name": "read_note",
        "title": "Read Note",
        "description": "Read the contents of a note by its ID.",
        "annotations": {
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
        },
        "inputSchema": {
            "type": "object",
            "properties": {
                "id": {
                    "type": "string",
                    "description": "The unique identifier of the note to read.",
                }
            },
            "required": ["id"],
        },
    }
    create_note = {
        "name": "create_note",
        "title": "Create Note",
        "description": "Create a new note with the given title and body.",
        "annotations": {
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": False,
        },
        "inputSchema": {
            "type": "object",
            "properties": {
                "title": {"type": "string"},
                "body": {"type": "string"},
            },
            "required": ["title", "body"],
        },
        "outputSchema": {
            "type": "object",
            "properties": {
                "title": {"type": "string"},
                "body": {"type": "string"},
            },
            "required": ["title", "body"],
        },
    }
    delete_note = {
        "name": "delete_note",
        "title": "Delete Note",
        "description": "Permanently delete a note by its ID. This action cannot be undone.",
        "annotations": {
            "readOnlyHint": False,
            "destructiveHint": True,
            "idempotentHint": True,
        },
        "inputSchema": {
            "type": "object",
            "properties": {"id": {"type": "string"}},
            "required": ["id"],
        },
    }

    if ARGS.drift:
        # 1. read_note is no longer read-only   -> annotation drift (Fail)
        read_note["annotations"]["readOnlyHint"] = False
        # 2. create_note narrows its contract   -> newly required argument (Fail)
        #    plus an undeclared property        -> (Warn)
        create_note["inputSchema"]["properties"]["author"] = {"type": "string"}
        create_note["inputSchema"]["required"] = ["title", "body", "author"]
        # 3. delete_note disappears entirely    -> missing tool (Fail)
        return [read_note, create_note]

    return [read_note, create_note, delete_note]


def resources():
    return [
        {
            "name": "notes_index",
            "uri": "notes://index",
            "description": "A list of all note IDs and their titles.",
            "mimeType": "application/json",
        }
    ]


def resource_templates():
    return [
        {
            "name": "note_by_id",
            "uriTemplate": "notes://notes/{id}",
            "description": "The full content of a single note, addressed by its ID.",
            "mimeType": "text/plain",
        }
    ]


def prompts():
    return [
        {
            "name": "summarise_note",
            "title": "Summarise Note",
            "description": "Ask the assistant to produce a concise summary of a specific note.",
            "arguments": [
                {
                    "name": "id",
                    "description": "The ID of the note to summarise.",
                    "required": True,
                }
            ],
        }
    ]


def text_result(text, is_error=False):
    result = {"content": [{"type": "text", "text": text}]}
    if is_error:
        result["isError"] = True
    return result, None


def call_tool(params):
    name = params.get("name")
    args = params.get("arguments") or {}

    if name == "read_note":
        note = NOTES.get(args.get("id"))
        if note is None:
            return text_result("note not found", is_error=True)
        return text_result(note["body"])

    if name == "create_note":
        if "title" not in args or "body" not in args:
            return None, {
                "code": -32602,
                "message": "Invalid params: title and body are required",
            }
        note_id = str(NEXT_ID[0])
        NEXT_ID[0] += 1
        NOTES[note_id] = {"title": args["title"], "body": args["body"]}
        result = {
            "content": [{"type": "text", "text": f"Created note {note_id}"}],
            "structuredContent": {"title": args["title"], "body": args["body"]},
        }
        if ARGS.drift:
            # Violate the declared outputSchema: body must be a string.
            result["structuredContent"]["body"] = 42
        return result, None

    if name == "delete_note":
        if NOTES.pop(args.get("id"), None) is None:
            return text_result("note not found", is_error=True)
        return text_result("deleted")

    return None, {"code": -32602, "message": f"Unknown tool: {name}"}


def read_resource(params):
    uri = params.get("uri", "")
    if uri == "notes://index":
        index = [{"id": k, "title": v["title"]} for k, v in sorted(NOTES.items())]
        return {
            "contents": [
                {"uri": uri, "mimeType": "application/json", "text": json.dumps(index)}
            ]
        }, None
    if uri.startswith("notes://notes/"):
        note = NOTES.get(uri.rsplit("/", 1)[-1])
        if note is None:
            return None, {"code": -32602, "message": f"Unknown resource: {uri}"}
        return {
            "contents": [{"uri": uri, "mimeType": "text/plain", "text": note["body"]}]
        }, None
    return None, {"code": -32602, "message": f"Unknown resource: {uri}"}


def get_prompt(params):
    if params.get("name") != "summarise_note":
        return None, {"code": -32602, "message": "Unknown prompt"}
    note_id = (params.get("arguments") or {}).get("id", "1")
    note = NOTES.get(note_id, {"body": ""})
    return {
        "description": "Summarise a note",
        "messages": [
            {
                "role": "user",
                "content": {"type": "text", "text": f"Summarise: {note['body']}"},
            }
        ],
    }, None


def dispatch(method, params=None):
    """Returns (result, error) for a method; error is set for unknown methods."""
    params = params or {}
    if method == "tools/call":
        return call_tool(params)
    if method == "resources/read":
        return read_resource(params)
    if method == "prompts/get":
        return get_prompt(params)
    if method == "tools/list":
        return {"tools": tools(), "ttlMs": 60000, "cacheScope": "public"}, None
    if method == "resources/list":
        return {"resources": resources(), "ttlMs": 60000, "cacheScope": "public"}, None
    if method == "resources/templates/list":
        return (
            {
                "resourceTemplates": resource_templates(),
                "ttlMs": 60000,
                "cacheScope": "public",
            },
            None,
        )
    if method == "prompts/list":
        return {"prompts": prompts(), "ttlMs": 60000, "cacheScope": "public"}, None
    return None, {"code": -32601, "message": "Method not found"}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        print("[mcp] " + (fmt % args))

    def _send(self, status, payload, extra_headers=None):
        body = b"" if payload is None else json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        for key, value in (extra_headers or {}).items():
            self.send_header(key, value)
        self.end_headers()
        if body:
            self.wfile.write(body)

    def _result(self, request_id, result, extra_headers=None):
        self._send(200, {"jsonrpc": "2.0", "id": request_id, "result": result}, extra_headers)

    def _error(self, status, request_id, error):
        self._send(status, {"jsonrpc": "2.0", "id": request_id, "error": error})

    def do_GET(self):
        # This revision removed the standalone GET stream.
        self._send(405, None)

    def do_DELETE(self):
        self._send(405, None)

    def do_POST(self):
        if self.path.rstrip("/") != "/mcp":
            self._send(404, None)
            return

        length = int(self.headers.get("Content-Length", "0"))
        try:
            message = json.loads(self.rfile.read(length) or b"{}")
        except json.JSONDecodeError:
            self._error(400, None, {"code": -32700, "message": "Parse error"})
            return

        method = message.get("method")
        request_id = message.get("id")

        # Notifications are fire-and-forget.
        if request_id is None:
            self._send(202, None)
            return

        if ARGS.legacy:
            self._handle_legacy(method, request_id, message.get("params"))
        else:
            self._handle_modern(method, request_id, message.get("params"))

    def _handle_modern(self, method, request_id, params=None):
        if method == "server/discover":
            reset_store()
            self._result(
                request_id,
                {
                    "supportedVersions": [MODERN_VERSION],
                    "capabilities": {"tools": {}, "resources": {}, "prompts": {}},
                    "instructions": "Notes server example",
                    "ttlMs": 60000,
                    "cacheScope": "public",
                },
            )
            return

        if self.headers.get("MCP-Protocol-Version") != MODERN_VERSION:
            self._error(
                400,
                request_id,
                {
                    "code": -32600,
                    "message": "HeaderMismatch: MCP-Protocol-Version",
                    "data": {"supported": [MODERN_VERSION]},
                },
            )
            return

        result, error = dispatch(method, params)
        if error:
            self._error(404, request_id, error)
        else:
            self._result(request_id, result)

    def _handle_legacy(self, method, request_id, params=None):
        # A legacy server does not implement server/discover; it returns a bare 400
        # so the client falls back to the initialize handshake.
        if method == "server/discover":
            self._send(400, None)
            return

        if method == "initialize":
            reset_store()
            session_id = uuid.uuid4().hex
            SESSIONS.add(session_id)
            self._result(
                request_id,
                {
                    "protocolVersion": LEGACY_VERSION,
                    "capabilities": {"tools": {}, "resources": {}, "prompts": {}},
                    "serverInfo": {"name": "notes", "version": "1.0.0"},
                },
                extra_headers={"Mcp-Session-Id": session_id},
            )
            return

        if self.headers.get("Mcp-Session-Id") not in SESSIONS:
            self._error(400, request_id, {"code": -32600, "message": "Missing or unknown session"})
            return

        result, error = dispatch(method, params)
        if error:
            self._error(404, request_id, error)
        else:
            self._result(request_id, result)


def main():
    global ARGS
    parser = argparse.ArgumentParser(description="Minimal MCP server for the Drift example")
    parser.add_argument("--legacy", action="store_true", help="use the initialize handshake era")
    parser.add_argument("--drift", action="store_true", help="diverge from the definition")
    parser.add_argument("--port", type=int, default=8080)
    ARGS = parser.parse_args()

    era = "legacy" if ARGS.legacy else "modern"
    drift = " (drift enabled)" if ARGS.drift else ""
    print(f"Notes MCP server listening on http://127.0.0.1:{ARGS.port}/mcp [{era}]{drift}")
    HTTPServer(("127.0.0.1", ARGS.port), Handler).serve_forever()


if __name__ == "__main__":
    main()
