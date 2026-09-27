"""Provider observations retain only exact, representable editable fields."""

import copy
from dataclasses import asdict
import json

import pytest

from workshop_marketing_agent.google_local_posts import HttpResponse, observed_payload
from test_google_local_posts import Boundary, POST, payload


def test_exact_content_and_output_only_fields(payload):
    data = payload.provider_payload() | {"name": POST, "state": "LIVE", "searchUrl": "https://example.org/post",
        "createTime": "ignored", "updateTime": "ignored", "private": "discarded"}
    data["event"]["recurringInstanceTime"] = "ignored"
    data["media"][0]["googleUrl"] = "https://example.org/other"
    result = Boundary(data).client().get_post(POST)
    assert result.observed_payload == payload
    assert result.observed_payload.summary == "Mal-Yoga – Zeit für dich.\nOriginaltext bleibt erhalten."
    assert "discarded" not in str(asdict(result))
    assert payload.summary not in repr(result)


@pytest.mark.parametrize("path,value", [
    (("languageCode",), "de"), (("topicType",), "STANDARD"), (("summary",), 7), (("summary",), ""),
    (("event", "title"), None), (("event", "schedule", "startDate", "year"), 0),
    (("event", "schedule", "startDate", "month"), 13), (("event", "schedule", "startDate", "day"), 40),
    (("event", "schedule", "startTime", "hours"), 25), (("event", "schedule", "startTime", "minutes"), True),
    (("event", "schedule", "startTime", "seconds"), 1), (("event", "schedule", "startTime", "nanos"), 1),
    (("callToAction", "actionType"), "LEARN_MORE"), (("callToAction", "url"), "javascript:private"),
    (("media",), None), (("media",), {}), (("media",), [{"sourceUrl": "not a URL"}]),
    (("media",), [{"googleUrl": "https://example.org/media"}]),
    (("media",), [{"sourceUrl": "https://example.org/media", "dataRef": {}}]),
    (("event", "recurrenceInfo"), {}), (("scheduledTime",), "2026-10-01T10:00:00Z"),
    (("offer",), {}), (("alertType",), "COVID_19"),
])
def test_unrepresentable_content_is_unresolved(payload, path, value):
    data = payload.provider_payload()
    target = data
    for part in path[:-1]:
        target = target[part]
    target[path[-1]] = value
    result = Boundary(data | {"name": POST, "state": "LIVE"}).client().get_post(POST)
    assert result.observed_payload is None


@pytest.mark.parametrize("path", [
    ("languageCode",), ("topicType",), ("summary",), ("event",), ("callToAction",),
    ("event", "title"), ("event", "schedule", "startDate"), ("event", "schedule", "endTime"),
    ("event", "schedule", "startTime", "seconds"), ("event", "schedule", "endTime", "nanos"),
    ("callToAction", "actionType"), ("callToAction", "url"),
])
def test_missing_fields_are_not_filled(payload, path):
    data = payload.provider_payload()
    target = data
    for part in path[:-1]:
        target = target[part]
    del target[path[-1]]
    assert observed_payload(data) is None


def test_absent_empty_present_media_remain_distinct(payload):
    present = payload.provider_payload()
    empty = copy.deepcopy(present) | {"media": []}
    absent = copy.deepcopy(present)
    del absent["media"]
    values = [observed_payload(d).provider_payload() for d in (present, empty, absent)]
    assert values[0]["media"] and values[1]["media"] == [] and "media" not in values[2]


def test_duplicate_json_fields_are_ambiguous(payload):
    fake = Boundary()
    raw = json.dumps(payload.provider_payload() | {"name": POST, "state": "LIVE"})
    fake.response = HttpResponse(200, (raw[:-1] + ',"summary":"different"}').encode())
    result = fake.client().get_post(POST)
    assert result.outcome == "unavailable" and result.observed_payload is None
    assert len(fake.requests) == fake.tokens == 1
