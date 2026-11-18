"""A fake GitHub REST API on 127.0.0.1 (stdlib http.server in a thread).

GET answers come from `routes` (404 for anything else), the pull request endpoints
keep their state, and `drop` closes the next N connections without an answer (a
transport error).
"""

import json
import re
import threading
from pathlib import Path
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit


# real GitHub answers (captured once), because githubkit validates every field of a
# typed response
API = Path(__file__).parent / "api"


def fixture(name, **fields):
    """A captured GitHub answer from tests/fakes/api/<name>.json, fields replaced.

    `fields` replace top-level keys of the captured answer; everything else stays as
    GitHub sent it, so the SDK's validation passes.
    """
    return {**json.loads((API / f"{name}.json").read_text()), **fields}


def pull(number, ref):
    """A pull request answer with the given `number` and head branch `ref`.

    Built from the captured pull request, with the html_url pointing at the number.
    """
    data = fixture(
        "pull", number=number, html_url=f"https://example.test/pull/{number}"
    )
    data["head"] = {**data["head"], "ref": ref}
    return data


class FakeGitHub:
    """The fake server and its record of what the scripts did to it.

    `created`, `closed` and `requests` are what a test asserts on; `routes` and `drop`
    are what a test sets up before running a script.
    """

    def __init__(self):
        """Start the server on a free port in a daemon thread; `url` is its address."""
        self.routes = {}  # path -> JSON answer of a GET
        self.prs = []  # open pull requests (full answers, see pull())
        self.created = []  # (head, title) of every pull request opened
        self.closed = []  # numbers of the pull requests closed
        self.requests = []  # (method, path) of every request seen
        self.drop = 0  # close this many next connections without answering
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), self._handler())
        self.url = f"http://127.0.0.1:{self.server.server_port}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def stop(self):
        """Shut the server down and release its port."""
        self.server.shutdown()
        self.server.server_close()

    def answer(self, method, path, body):
        """The (status, JSON payload) answer for one request.

        `body` is the parsed request body (None when empty). The pull request
        endpoints keep state in `prs`, `created` and `closed`; other GETs come from
        `routes`; anything unknown is a 404, like GitHub's own.
        """
        # listing pull requests answers with the open ones
        if method == "GET" and path.endswith("/pulls"):
            return 200, self.prs

        # any other GET is a captured route, or a 404
        if method == "GET":
            return (
                (200, self.routes[path])
                if path in self.routes
                else (404, {"message": "Not Found"})
            )

        # opening a pull request: record it and keep it open
        if method == "POST" and path.endswith("/pulls"):
            number = len(self.prs) + len(self.created) + 100
            self.created.append((body["head"], body["title"]))
            self.prs.append(pull(number, body["head"]))
            return 201, self.prs[-1]

        # a comment on a pull request needs no state
        if method == "POST" and path.endswith("/comments"):
            return 201, fixture("comment")

        # closing a pull request: record it and drop it from the open list
        if method == "PATCH" and (m := re.search(r"/pulls/(\d+)$", path)):
            self.closed.append(int(m.group(1)))
            # the pull request being closed, then the open list without it
            closed = next(p for p in self.prs if p["number"] == int(m.group(1)))
            # keep every other pull request open
            self.prs = [p for p in self.prs if p is not closed]
            return 200, closed

        # deleting a branch succeeds without an answer body
        if method == "DELETE":
            return 204, None
        return 404, {"message": "Not Found"}

    def _handler(self):
        """The request handler class, bound to this fake (a closure over `fake`)."""
        fake = self

        class Handler(BaseHTTPRequestHandler):
            """One handler for every HTTP method; it only calls `fake.answer`."""

            def log_message(self, *args):
                """Keep the server quiet: pytest output must stay readable."""

            def handle_any(self):
                """Record the request, maybe drop it, else answer with `fake.answer`."""
                length = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(length) or "null")
                path = urlsplit(self.path).path
                fake.requests.append((self.command, path))

                # a dropped connection is a transport error for the retry tests
                if fake.drop > 0:
                    fake.drop -= 1
                    self.connection.close()
                    return
                status, payload = fake.answer(self.command, path, body)

                # every list fits on page 1, so a later page is empty
                if (
                    isinstance(payload, list)
                    and parse_qs(urlsplit(self.path).query).get("page", ["1"])[0] != "1"
                ):
                    payload = []  # every list fits on page 1

                data = json.dumps(payload).encode() if payload is not None else b""
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            do_GET = do_POST = do_PATCH = do_DELETE = handle_any

        return Handler
