"""Manual actions forwarded by the existing Callable without Google secret access."""

import pytest

from test_callable_google_oauth import wired, pilot, harness, source, assert_counts
from test_rausgegangen_service import prepare_manual, confirmation


@pytest.fixture
def manual_callable(wired):
    return prepare_manual(wired)


def test_callable_manual_flow_and_replays(manual_callable):
    h = manual_callable
    begin = h.invoke(h.manual)["result"]
    assert begin["status"] == "ready_to_copy", begin
    submission = confirmation(h)
    assert h.invoke(submission)["result"]["status"] == "submitted"
    publication = confirmation(h, True, public_url="https://example.org/event")
    assert h.invoke(publication)["result"]["status"] == "published"
    assert h.invoke(submission)["result"]["manual_status"] == "published"
    replay = h.invoke(h.manual)["result"]
    assert replay["package"] == begin["package"] and not replay["package_active"]
    assert replay["recorded_status"] == "in_progress" and replay["manual_status"] == "published"
    assert len(h.feed_calls) == 1 and not h.model_calls
    assert_counts(h, 0, 0, 0)


@pytest.mark.parametrize("stage", ["begin", "submission", "publication"])
@pytest.mark.parametrize("denial", ["auth", "app_check", "access"])
def test_callable_denial_before_storage_and_secrets(manual_callable, stage, denial):
    h = manual_callable
    data = h.manual
    if stage != "begin":
        h.invoke(h.manual)
        if stage == "publication":
            h.invoke(confirmation(h))
        data = confirmation(h, stage == "publication")
    if denial == "auth":
        h.authenticated = False
    elif denial == "app_check":
        h.attested = False
    else:
        h.allowed = False
    h.events.clear()
    h.feed_calls.clear()
    result = h.invoke(data)
    assert "error" in result or result["result"]["status"] in {"forbidden", "unauthenticated"}
    assert "load" not in h.events and "save" not in h.events and not h.feed_calls
    assert_counts(h, 0, 0, 0)
