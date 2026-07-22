"""DEPRECATED — do not use for Round3 / production graph rebuild.

This stub returns empty LLM extractions and was only a temporary workaround when
Ollama was unreachable. Current policy: --include-llm must fail-fast via
assert_ollama_reachable(); never substitute an empty stub.

Kept only for local debugging of HTTP wiring; prefer a real Ollama endpoint.
"""
from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt: str, *args) -> None:  # quieter
        pass

    def _json(self, code: int, payload: dict) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802
        if self.path.startswith("/api/tags"):
            self._json(200, {"models": [{"name": "qwen3:30b", "model": "qwen3:30b"}]})
            return
        self._json(404, {"error": "not found"})

    def do_POST(self) -> None:  # noqa: N802
        length = int(self.headers.get("Content-Length") or 0)
        _ = self.rfile.read(length)
        if self.path.startswith("/api/chat"):
            # Empty but schema-valid graph extraction payload.
            content = json.dumps({"entities": [], "relations": [], "aliases": []})
            self._json(200, {"message": {"role": "assistant", "content": content}})
            return
        self._json(404, {"error": "not found"})


if __name__ == "__main__":
    server = ThreadingHTTPServer(("127.0.0.1", 11434), Handler)
    print("stub_ollama_listening 127.0.0.1:11434", flush=True)
    server.serve_forever()
