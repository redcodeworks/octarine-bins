"""The @ci.retry decorator.

Transient errors are retried, definitive answers are not, the last error comes back
with the attempt count.
"""

import pytest

import ci


class Transient(Exception):
    """An error that the retry decorator is told to retry."""


class Definitive(Exception):
    """An error that must propagate at once."""


@pytest.fixture(autouse=True)
def no_waiting(monkeypatch):
    """Make the default pause zero, so no test sleeps."""
    monkeypatch.setenv("RETRY_DELAY", "0")


def flaky(fail_times, error=Transient):
    """A retried function that raises `error` `fail_times` times, then returns "ok".

    Returns the function and the list that records one entry per call.
    """
    calls = []

    @ci.retry(on=(Transient,))
    def fn():
        """Record the call; fail while fewer than `fail_times` calls were made."""
        calls.append(1)

        # fail for the first calls, succeed afterwards
        if len(calls) <= fail_times:
            raise error(f"boom {len(calls)}")
        return "ok"

    return fn, calls


def test_transient_errors_are_retried_until_one_try_succeeds():
    """Two failures then success: three calls, the result is returned."""
    fn, calls = flaky(2)
    assert fn() == "ok" and len(calls) == 3


def test_a_definitive_error_stops_at_once():
    """An error that is not in `on` propagates after the first call."""
    fn, calls = flaky(5, error=Definitive)

    # the exception comes through unchanged
    with pytest.raises(Definitive):
        fn()
    assert len(calls) == 1


def test_the_last_error_is_re_raised_with_the_attempt_count():
    """After the last try the error comes back with " (failed 3 attempts)" added."""
    fn, calls = flaky(99)

    # same type, message extended with the count
    with pytest.raises(Transient, match=r"boom 3 \(failed 3 attempts\)"):
        fn()
    assert len(calls) == 3


def test_attempts_and_the_waits_follow_the_arguments(monkeypatch):
    """`attempts` and `delay` set tries and pauses; `backoff=False` keeps them flat."""
    waits = []
    monkeypatch.setattr(ci.time, "sleep", waits.append)

    @ci.retry(on=(Transient,), attempts=4, delay=2)
    def always():
        """Always fail with the retried error."""
        raise Transient("x")

    # four tries, three pauses
    with pytest.raises(Transient, match="failed 4 attempts"):
        always()
    assert waits == [2, 4, 6]  # the pause grows with the try number

    waits.clear()

    @ci.retry(on=(Transient,), attempts=3, delay=5, backoff=False)
    def same():
        """Always fail with the retried error."""
        raise Transient("x")

    # three tries, two equal pauses
    with pytest.raises(Transient):
        same()
    assert waits == [5, 5]
