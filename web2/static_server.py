import os
import mimetypes
import http.server
import urllib.parse
import http.client
from http import HTTPStatus

DIST_DIR = "/app/dist"
SCRAPING_DIR = "/data/scrapingImages"

API_UPSTREAM_HOST = "rag-service"
API_UPSTREAM_PORT = 10605

ARTICLEIMG_UPSTREAM_HOST = "192.168.10.206"
ARTICLEIMG_UPSTREAM_PORT = 8080
ARTICLEIMG_UPSTREAM_PREFIX = "/zsltStaticData"

API_PREFIX = "/api/"
SCRAPING_PREFIX = "/scraping/"
ARTICLEIMG_PREFIX = "/articleImg/"


def _send_file(handler, rel_path, root_dir):
    full = os.path.abspath(os.path.join(root_dir, rel_path.lstrip("/")))
    root_abs = os.path.abspath(root_dir)
    if not (full == root_abs or full.startswith(root_abs + os.sep)):
        handler.send_error(HTTPStatus.FORBIDDEN, "Forbidden")
        return

    if not os.path.isfile(full):
        handler.send_error(HTTPStatus.NOT_FOUND, "Not Found")
        return

    ctype, _ = mimetypes.guess_type(full)
    if not ctype:
        ctype = "application/octet-stream"

    handler.send_response(HTTPStatus.OK)
    handler.send_header("Content-Type", ctype)
    fs = os.stat(full)
    handler.send_header("Content-Length", str(fs.st_size))
    handler.end_headers()

    with open(full, "rb") as f:
        while True:
            chunk = f.read(16 * 1024)
            if not chunk:
                break
            handler.wfile.write(chunk)
            handler.wfile.flush()


def _proxy(handler, method, upstream_host, upstream_port, upstream_path):
    length = handler.headers.get("Content-Length")
    body = handler.rfile.read(int(length)) if length and length.isdigit() else None

    parsed = urllib.parse.urlsplit(handler.path)
    forward_path = upstream_path
    if parsed.query:
        forward_path = forward_path + "?" + parsed.query

    conn = http.client.HTTPConnection(upstream_host, upstream_port, timeout=300)
    try:
        headers = {k: v for k, v in handler.headers.items()}
        headers["Host"] = f"{upstream_host}:{upstream_port}"

        conn.request(method, forward_path, body=body, headers=headers)
        resp = conn.getresponse()

        handler.send_response(resp.status, resp.reason)
        for k, v in resp.getheaders():
            if k.lower() in ("transfer-encoding", "connection"):
                continue
            handler.send_header(k, v)
        handler.end_headers()

        while True:
            chunk = resp.read(16 * 1024)
            if not chunk:
                break
            handler.wfile.write(chunk)
            handler.wfile.flush()
    finally:
        conn.close()


class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        self._handle("GET")

    def do_POST(self):
        self._handle("POST")

    def do_PUT(self):
        self._handle("PUT")

    def do_DELETE(self):
        self._handle("DELETE")

    def _handle(self, method):
        parsed = urllib.parse.urlsplit(self.path)
        path = parsed.path

        if path.startswith("/api/") or path == "/api":
            upstream_path = path[len("/api") :]  # '/query' 等
            if not upstream_path.startswith("/"):
                upstream_path = "/" + upstream_path
            _proxy(self, method, API_UPSTREAM_HOST, API_UPSTREAM_PORT, upstream_path)
            return

        if path.startswith(SCRAPING_PREFIX):
            rel = path[len(SCRAPING_PREFIX) :]
            _send_file(self, rel, SCRAPING_DIR)
            return

        if path.startswith(ARTICLEIMG_PREFIX):
            rel = path[len(ARTICLEIMG_PREFIX) :]
            upstream_path = ARTICLEIMG_UPSTREAM_PREFIX + ("/" + rel if rel else "")
            _proxy(
                self,
                method,
                ARTICLEIMG_UPSTREAM_HOST,
                ARTICLEIMG_UPSTREAM_PORT,
                upstream_path,
            )
            return

        # SPA 静态资源与回退
        if path in ("", "/"):
            rel = "index.html"
        else:
            rel = path.lstrip("/")

        file_path = os.path.join(DIST_DIR, rel)
        if not os.path.isfile(file_path):
            rel = "index.html"

        _send_file(self, rel, DIST_DIR)

    def log_message(self, format, *args):
        # 降噪：避免刷屏
        return


def main():
    os.makedirs(DIST_DIR, exist_ok=True)
    httpd = http.server.ThreadingHTTPServer(("", 80), Handler)
    httpd.serve_forever()


if __name__ == "__main__":
    main()

