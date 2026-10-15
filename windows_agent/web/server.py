"""Loopback-only HTTP server; no extra web framework or model process."""
import argparse
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import secrets
from urllib.parse import parse_qs, urlsplit
import webbrowser

from .session import Session

STATIC = Path(__file__).with_name("static")
FILES = {"/": ("index.html", "text/html; charset=utf-8"),
         "/app.js": ("app.js", "text/javascript; charset=utf-8"),
         "/style.css": ("style.css", "text/css; charset=utf-8")}


def load_messages():
    from windows_agent import context
    pairs = context.system_profile.load(context.HISTORY).get("exchanges", [])[-6:]
    return [{"task": str(p.get("user", ""))[:12000], "answer": str(p.get("assistant", ""))[:12000],
             "status": "saved"} for p in pairs if isinstance(p, dict)]


def local_models():
    try:
        from ollama import Client
        result = Client(host="http://127.0.0.1:11434", timeout=3).list()
        return {"online": True, "models": [item.model for item in result.models if item.model], "error": ""}
    except Exception:
        return {"online": False, "models": [], "error": "Ollama is unavailable. Start Ollama locally, then refresh models."}


class LocalServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, port, session=None, default_model="qwen3.5:4b"):
        super().__init__(("127.0.0.1", port), Handler)
        self.session = session if session is not None else Session(initial_messages=load_messages())
        self.token = secrets.token_urlsafe(32)
        self.default_model = default_model
        actual_port = self.server_address[1]
        self.allowed_hosts = {f"127.0.0.1:{actual_port}", f"localhost:{actual_port}"}
        self.allowed_origins = {"http://" + host for host in self.allowed_hosts}


class Handler(BaseHTTPRequestHandler):
    def setup(self):
        super().setup()
        self.connection.settimeout(5)

    def log_message(self, *args):
        pass

    def reply(self, status, data, mime="application/json; charset=utf-8"):
        body = data if isinstance(data, bytes) else json.dumps(data, ensure_ascii=False, default=str).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'")
        self.end_headers()
        self.wfile.write(body)

    def allowed(self, authenticated=False):
        if self.headers.get("Host", "") not in self.server.allowed_hosts:
            self.reply(403, {"error": "Use the local server address."})
            return False
        origin = self.headers.get("Origin")
        if origin is not None and origin not in self.server.allowed_origins:
            self.reply(403, {"error": "This request must come from the local interface."})
            return False
        if authenticated and not secrets.compare_digest(self.headers.get("X-Agent-Token", ""), self.server.token):
            self.reply(403, {"error": "Refresh the page to reconnect to the local agent."})
            return False
        return True

    def do_GET(self):
        route = urlsplit(self.path)
        if not self.allowed(authenticated=route.path.startswith("/api/") and route.path != "/api/bootstrap"):
            return
        if route.path in FILES:
            filename, mime = FILES[route.path]
            self.reply(200, (STATIC / filename).read_bytes(), mime)
        elif route.path == "/api/bootstrap":
            self.reply(200, {"token": self.server.token, "default_model": self.server.default_model,
                             "desktop_mode": hasattr(self.server, 'desktop_size_callback')})
        elif route.path == "/api/state":
            query = parse_qs(route.query)
            try:
                cursor = max(0, int(query.get("cursor", ["0"])[0]))
            except ValueError:
                self.reply(400, {"error": "Invalid activity cursor."})
                return
            self.reply(200, self.server.session.snapshot(cursor, query.get("job", [""])[0]))
        elif route.path == "/api/models":
            self.reply(200, local_models())
        else:
            self.reply(404, {"error": "Not found."})

    def do_POST(self):
        if not self.allowed(authenticated=True):
            return
        if self.headers.get("Content-Type", "").split(";")[0].strip() != "application/json":
            self.reply(415, {"error": "Send JSON."})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 < length <= 65536:
                raise ValueError("Request is too large or empty.")
            data = json.loads(self.rfile.read(length))
            if not isinstance(data, dict):
                raise ValueError("Expected an object.")
            route = urlsplit(self.path).path
            session = self.server.session
            if route == "/api/tasks":
                result = {"id": session.start(data)}
            elif route == "/api/stop":
                session.stop(data.get("job"))
                result = {"ok": True}
            elif route == "/api/approval":
                session.decide(data.get("job"), data.get("approval"), data.get("approved"))
                result = {"ok": True}
            elif route == "/api/new-chat":
                session.reset()
                result = {"ok": True}
            elif route == '/api/desktop-window' and hasattr(self.server, 'desktop_size_callback'):
                width, height = data.get('width'), data.get('height')
                if type(width) is not int or type(height) is not int or not (960 <= width <= 3840 and 640 <= height <= 2160):
                    raise ValueError('Invalid desktop window dimensions')
                self.server.desktop_size_callback(width, height)
                result = {'ok': True}
            else:
                self.reply(404, {"error": "Not found."})
                return
            self.reply(200, result)
        except RuntimeError as exc:
            self.reply(409, {"error": str(exc)})
        except (ValueError, TypeError) as exc:
            self.reply(400, {"error": str(exc)})
        except Exception:
            self.reply(500, {"error": "Local server error. See the terminal for diagnostics."})


def main():
    parser = argparse.ArgumentParser(description="Windows-Agent local browser interface")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--model", default="qwen3.5:4b")
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error("Port must be between 1 and 65535.")
    try:
        server = LocalServer(args.port, default_model=args.model)
    except OSError as exc:
        parser.exit(1, f"Could not start the local interface: {exc}\n")
    url = f"http://127.0.0.1:{server.server_address[1]}"
    print(f"Windows-Agent: {url}\nKeep this terminal open. Ctrl+C stops the server; Ctrl+Alt+F12 stops a task.", flush=True)
    if not args.no_browser:
        webbrowser.open(url)
    try:
        server.serve_forever(poll_interval=0.2)
    except KeyboardInterrupt:
        pass
    finally:
        server.session.close()
        server.server_close()


if __name__ == "__main__":
    main()
