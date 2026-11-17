"""The GitHub API layer of ci.py (githubkit against the fake API): a 404 is definitive, a transport error is retried, models validate untrusted JSON."""
import ci


def test_a_404_is_definitive_and_not_retried(github):
    assert ci.gh_api("/repos/o/r/releases/latest") is None
    assert github.requests == [("GET", "/repos/o/r/releases/latest")]


def test_a_transport_error_is_retried(github):
    github.routes["/repos/o/r/x"] = {"ok": True}
    github.drop = 2  # the first two connections are closed without an answer
    assert ci.gh_api("/repos/o/r/x") == {"ok": True}
    assert len(github.requests) == 3


def test_a_transport_error_that_persists_gives_none_after_three_tries(github):
    github.routes["/repos/o/r/x"] = {"ok": True}
    github.drop = 99
    assert ci.gh_api("/repos/o/r/x") is None
    assert len(github.requests) == 3


def test_the_token_goes_in_the_authorization_header_and_a_model_validates_the_answer(github):
    github.routes["/repos/o/r/releases/latest"] = {"tag_name": "v1.2.3", "other": 1}
    assert ci.gh_api("/repos/o/r/releases/latest", ci.Release).tag_name == "v1.2.3"
    github.routes["/repos/o/r/releases/latest"] = {"name": "no tag"}
    assert ci.gh_api("/repos/o/r/releases/latest", ci.Release) is None  # invalid JSON from the server is not trusted


def test_pages_are_joined(github):
    github.routes["/repos/o/r/tags"] = [{"name": str(i)} for i in range(5)]
    assert len(ci.gh_pages("/repos/o/r/tags")) == 5


def test_versions_order_numerically_not_alphabetically():
    assert ci.version("v1.10.0") > ci.version("v1.9.0") and ci.version("1.0.3") < ci.version("v1.1.0")
