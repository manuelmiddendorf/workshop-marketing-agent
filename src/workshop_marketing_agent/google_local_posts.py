"""Single-request Google Local Posts transport; no campaign orchestration."""

from dataclasses import dataclass, field
from http.client import HTTPSConnection
from ipaddress import ip_address
import json
import math
import re
from typing import Callable, Literal, Protocol
from urllib.parse import urlencode, urlsplit

from pydantic import HttpUrl

from .models import _booking_url
from .submission import GoogleSubmissionPayload

API_ROOT = "https://mybusiness.googleapis.com"
_MAX_RESPONSE_BYTES = 1_048_576
_LOCATION = re.compile(r"accounts/[A-Za-z0-9_-]+/locations/[A-Za-z0-9_-]+")
_POST_ID = re.compile(r"[A-Za-z0-9_-]+")
Operation = Literal["create", "update", "get"]
ProviderState = Literal["LIVE", "RECURRING", "PROCESSING", "SCHEDULED", "REJECTED"]
Outcome = Literal[
    "visible", "pending", "rejected", "unresolved", "invalid_input",
    "authentication_or_permission", "provider_rejection", "unavailable", "ambiguous_write",
]


@dataclass(frozen=True, slots=True)
class PostResult:
    operation: Operation
    outcome: Outcome
    resource_name: str | None = None
    state: ProviderState | None = None
    search_url: str | None = None


@dataclass(frozen=True, slots=True)
class HttpResponse:
    status: int
    body: bytes = field(repr=False)


class HttpTransport(Protocol):
    def __call__(
        self, *, method: str, url: str, headers: dict[str, str],
        body: bytes | None, timeout: float,
    ) -> HttpResponse:
        """Make at most one request, without redirects or automatic retries.

        Exceptions provide no evidence that a write was not dispatched.
        Implementations must not log credentials, bodies or response details.
        """
        ...


def https_transport(
    *, method: str, url: str, headers: dict[str, str], body: bytes | None, timeout: float,
) -> HttpResponse:
    """Standard-library implementation: one HTTPS request, bounded response size.

    The timeout bounds socket operations, not total elapsed wall-clock time.
    HTTPSConnection neither follows redirects nor automatically retries requests.
    """
    parsed = urlsplit(url)
    if parsed.scheme != "https" or parsed.netloc != "mybusiness.googleapis.com":
        raise ValueError("Invalid API host")
    connection = HTTPSConnection("mybusiness.googleapis.com", timeout=timeout)
    try:
        path = parsed.path + ("?" + parsed.query if parsed.query else "")
        connection.request(method, path, body=body, headers=headers)
        response = connection.getresponse()
        data = response.read(_MAX_RESPONSE_BYTES + 1)
        if len(data) > _MAX_RESPONSE_BYTES:
            raise ValueError("Response exceeds size limit")
        return HttpResponse(response.status, data)
    finally:
        connection.close()


@dataclass(frozen=True, slots=True, repr=False)
class GoogleLocalPosts:
    """Server-configured boundary; callers retain responsibility for authorization."""

    location: str
    token_provider: Callable[[], str]
    transport: HttpTransport
    timeout: float

    def create_event(self, payload: GoogleSubmissionPayload) -> PostResult:
        return self._request("create", None, payload)

    def update_event(self, post_name: str, payload: GoogleSubmissionPayload) -> PostResult:
        return self._request("update", post_name, payload)

    def get_post(self, post_name: str) -> PostResult:
        return self._request("get", post_name, None)

    def _valid_post(self, name: object) -> bool:
        prefix = self.location + "/localPosts/"
        return (
            isinstance(name, str) and name.startswith(prefix)
            and _POST_ID.fullmatch(name[len(prefix):]) is not None
        )

    def _request(
        self, operation: Operation, post_name: str | None,
        payload: GoogleSubmissionPayload | None,
    ) -> PostResult:
        # No untrusted exception is returned or chained into a public exception.
        try:
            if not isinstance(self.location, str) or not _LOCATION.fullmatch(self.location):
                return PostResult(operation, "invalid_input")
            if type(self.timeout) not in (int, float) or not math.isfinite(self.timeout) or not 0 < self.timeout <= 60:
                return PostResult(operation, "invalid_input")
            if operation != "create" and not self._valid_post(post_name):
                return PostResult(operation, "invalid_input")
            body = None
            url = API_ROOT + "/v4/" + (
                self.location + "/localPosts" if operation == "create" else post_name
            )
            if operation != "get":
                if type(payload) is not GoogleSubmissionPayload:
                    return PostResult(operation, "invalid_input")
                # Revalidate unchecked model_construct/model_copy inputs as well.
                validated = GoogleSubmissionPayload.model_validate_json(payload.model_dump_json(warnings="error"))
                if operation == "update" and not validated.media:
                    return PostResult(operation, "invalid_input")
                provider_payload = validated.provider_payload()
                body = json.dumps(provider_payload, ensure_ascii=False, allow_nan=False).encode("utf-8")
                if operation == "update":
                    url += "?" + urlencode({"updateMask": ",".join(sorted(provider_payload))})
        except Exception:
            return PostResult(operation, "invalid_input")
        try:
            token = self.token_provider()
            if not isinstance(token, str) or not re.fullmatch(r"[A-Za-z0-9._~+/-]+=*", token):
                return PostResult(operation, "authentication_or_permission")
        except Exception:
            return PostResult(operation, "authentication_or_permission")
        uncertain: Outcome = "unavailable" if operation == "get" else "ambiguous_write"
        try:
            response = self.transport(
                method={"create": "POST", "update": "PATCH", "get": "GET"}[operation],
                url=url, headers={"Authorization": "Bearer " + token, "Content-Type": "application/json"},
                body=body, timeout=self.timeout,
            )
            if type(response.status) is not int:
                return PostResult(operation, uncertain)
            if response.status in (401, 403):
                return PostResult(operation, "authentication_or_permission")
            if response.status in (400, 404, 422):
                return PostResult(operation, "provider_rejection")
            if not 200 <= response.status < 300:
                # Includes redirects, rate limiting and 5xx; no proof of no write.
                return PostResult(operation, uncertain)
            if not isinstance(response.body, bytes) or len(response.body) > _MAX_RESPONSE_BYTES:
                return PostResult(operation, uncertain)
            data = json.loads(response.body)
            if not isinstance(data, dict) or not self._valid_post(data.get("name")):
                return PostResult(operation, uncertain)
            if operation != "create" and data["name"] != post_name:
                return PostResult(operation, uncertain)
            search_url = data.get("searchUrl")
            if search_url is not None:
                if not isinstance(search_url, str):
                    return PostResult(operation, uncertain)
                _booking_url(search_url)
                host = HttpUrl(search_url).host.lower().rstrip(".").strip("[]")
                if host.endswith((".localhost", ".local", ".internal")):
                    return PostResult(operation, uncertain)
                try:
                    address = ip_address(host)
                except ValueError:
                    pass
                else:
                    if not address.is_global:
                        return PostResult(operation, uncertain)
            state = data.get("state")
            outcomes: dict[str, Outcome] = {
                "LIVE": "visible", "RECURRING": "visible", "PROCESSING": "pending",
                "SCHEDULED": "pending", "REJECTED": "rejected",
            }
            if not isinstance(state, str) or state not in outcomes:
                return PostResult(operation, "unresolved", data["name"], search_url=search_url)
            return PostResult(operation, outcomes[state], data["name"], state, search_url)
        except Exception:
            return PostResult(operation, uncertain)
