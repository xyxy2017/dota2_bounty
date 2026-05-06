from __future__ import annotations

import json
from pathlib import Path
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qsl, urlparse

from app.supervisor import HttpError, Supervisor


def run_server(supervisor: Supervisor, host: str, port: int) -> None:
    static_files = _build_static_file_map(supervisor.settings.ui_dir)

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            self._handle("GET")

        def do_POST(self) -> None:  # noqa: N802
            self._handle("POST")

        def do_PATCH(self) -> None:  # noqa: N802
            self._handle("PATCH")

        def log_message(self, format: str, *args) -> None:  # noqa: A003
            return

        def _handle(self, method: str) -> None:
            parsed = urlparse(self.path)
            if method == "GET":
                maybe_static = self._try_static(parsed.path)
                if maybe_static:
                    return
            query = dict(parse_qsl(parsed.query))
            payload = self._read_json_body()
            try:
                status, body = supervisor.handle_request(method, parsed.path, query, payload)
            except HttpError as exc:
                self._write_json(exc.status_code, {"detail": exc.detail})
                return
            self._write_json(status, body)

        def _read_json_body(self) -> dict | None:
            length = int(self.headers.get("Content-Length", "0"))
            if length <= 0:
                return None
            raw = self.rfile.read(length)
            if not raw:
                return None
            return json.loads(raw.decode("utf-8"))

        def _write_json(self, status: int, body: dict) -> None:
            encoded = json.dumps(body, ensure_ascii=True).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

        def _try_static(self, path: str) -> bool:
            target = static_files.get(path)
            if not target:
                return False
            data = target.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", _guess_content_type(target))
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return True

    httpd = ThreadingHTTPServer((host, port), Handler)
    supervisor.startup()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:  # pragma: no cover - manual stop
        pass
    finally:
        httpd.server_close()
        supervisor.shutdown()


def _build_static_file_map(ui_dir: Path) -> dict[str, Path]:
    return {
        "/": ui_dir / "index.html",
        "/index.html": ui_dir / "index.html",
        "/debug/roster/live": ui_dir / "debug_roster_live.html",
        "/debug/ocr": ui_dir / "debug_ocr.html",
        "/debug/ocr/live": ui_dir / "debug_ocr_live.html",
        "/app.js": ui_dir / "app.js",
        "/styles.css": ui_dir / "styles.css",
    }


def _guess_content_type(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix == ".html":
        return "text/html; charset=utf-8"
    if suffix == ".js":
        return "application/javascript; charset=utf-8"
    if suffix == ".css":
        return "text/css; charset=utf-8"
    return "application/octet-stream"
