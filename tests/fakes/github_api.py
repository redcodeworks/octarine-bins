"""A fake GitHub REST API on 127.0.0.1 (stdlib http.server in a thread): GET answers come from `routes` (404 for anything else), the pull request endpoints keep their state, and `drop` closes the next N connections without an answer (a transport error)."""
import json
import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit


class FakeGitHub:
    def __init__(self):
        self.routes = {}      # path -> JSON answer of a GET
        self.prs = []         # open pull requests: {"number", "head": {"ref"}}
        self.created = []     # (head, title) of every pull request opened
        self.closed = []      # numbers of the pull requests closed
        self.requests = []    # (method, path) of every request seen
        self.drop = 0         # close this many next connections without answering
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), self._handler())
        self.url = f"http://127.0.0.1:{self.server.server_port}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def stop(self):
        self.server.shutdown()
        self.server.server_close()

    def answer(self, method, path, body):
        """(status, JSON) for one request."""
        if method == "GET" and path.endswith("/pulls"):
            return 200, self.prs
        if method == "GET":
            return (200, self.routes[path]) if path in self.routes else (404, {"message": "Not Found"})
        if method == "POST" and path.endswith("/pulls"):
            number = len(self.prs) + len(self.created) + 100
            self.created.append((body["head"], body["title"]))
            self.prs.append({"number": number, "head": {"ref": body["head"]}})
            return 201, {"number": number, "html_url": f"https://example.test/pull/{number}"}
        if method == "POST" and path.endswith("/comments"):
            return 201, {}
        if method == "PATCH" and (m := re.search(r"/pulls/(\d+)$", path)):
            self.closed.append(int(m.group(1)))
            self.prs = [p for p in self.prs if p["number"] != int(m.group(1))]
            return 200, {}
        if method == "DELETE":
            return 204, None
        return 404, {"message": "Not Found"}

    def _handler(self):
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def handle_any(self):
                length = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(length) or "null")
                path = urlsplit(self.path).path
                fake.requests.append((self.command, path))
                if fake.drop > 0:
                    fake.drop -= 1
                    self.connection.close()
                    return
                status, payload = fake.answer(self.command, path, body)
                data = json.dumps(payload).encode() if payload is not None else b""
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            do_GET = do_POST = do_PATCH = do_DELETE = handle_any

        return Handler
