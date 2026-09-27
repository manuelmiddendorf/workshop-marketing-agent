"""Lazy, single-request refresh-token grant for trusted Firebase wiring only."""

from collections.abc import Callable
from dataclasses import dataclass
from http.client import HTTPSConnection
import json
import math
import re
from urllib.parse import urlencode

from workshop_marketing_agent.google_local_posts import HttpResponse, HttpTransport

TOKEN_URL = "https://oauth2.googleapis.com/token"
REQUIRED_SCOPE = "https://www.googleapis.com/auth/business.manage"
TOKEN_TIMEOUT = 10.0
POSTS_TIMEOUT = 15.0
MAX_JSON_BYTES = 16_384
MAX_RESPONSE_BYTES = 65_536
_CLIENT_ID = re.compile(r"[0-9]+-[A-Za-z0-9_-]+\.apps\.googleusercontent\.com")


def _object(raw):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("Invalid OAuth data")
            result[key] = value
        return result

    def invalid_constant(_):
        raise ValueError("Invalid OAuth data")

    data = json.loads(raw, object_pairs_hook=unique, parse_constant=invalid_constant)
    if type(data) is not dict:
        raise ValueError("Invalid OAuth data")
    return data


def _text(value, limit):
    return (type(value) is str and 0 < len(value) <= limit
            and all(33 <= ord(c) <= 126 for c in value))


def token_transport(*, method, url, headers, body, timeout) -> HttpResponse:
    """Fixed destination, no redirects/retries; never expose transport exceptions.

    A zero status is a private failure sentinel consumed by the token provider.
    Timeout bounds socket operations, not the complete invocation wall time.
    """
    connection = None
    try:
        if (method != "POST" or url != TOKEN_URL or type(timeout) not in (int, float)
                or not math.isfinite(timeout) or not 0 < timeout <= 60):
            return HttpResponse(0, b"")
        connection = HTTPSConnection("oauth2.googleapis.com", timeout=timeout)
        connection.request("POST", "/token", body=body, headers=headers)
        response = connection.getresponse()
        data = response.read(MAX_RESPONSE_BYTES + 1)
        if len(data) > MAX_RESPONSE_BYTES:
            return HttpResponse(0, b"")
        return HttpResponse(response.status, data)
    except Exception:
        return HttpResponse(0, b"")
    finally:
        if connection is not None:
            try:
                connection.close()
            except Exception:
                pass


@dataclass(frozen=True, slots=True, repr=False)
class RefreshTokenProvider:
    secret_getter: Callable[[], str]
    transport: HttpTransport
    timeout: float

    def __call__(self) -> str:
        """Return a validated token or an empty failure sentinel, never exception data.

        GoogleLocalPosts maps the sentinel to authentication_or_permission before
        dispatch; orchestration records failed/permission_denied. No secret value
        or parsing/transport exception is retained on this callable or in errors.
        """
        try:
            if (type(self.timeout) not in (int, float) or not math.isfinite(self.timeout)
                    or not 0 < self.timeout <= 60):
                return ""
            raw = self.secret_getter()
            if type(raw) is not str or len(raw.encode("utf-8")) > MAX_JSON_BYTES:
                return ""
            credentials = _object(raw)
            if set(credentials) != {"client_id", "client_secret", "refresh_token"}:
                return ""
            if (not _text(credentials["client_id"], 256)
                    or not _CLIENT_ID.fullmatch(credentials["client_id"])
                    or not _text(credentials["client_secret"], 4096)
                    or not _text(credentials["refresh_token"], 4096)):
                return ""
            body = urlencode({"grant_type": "refresh_token", **credentials}).encode("ascii")
            response = self.transport(method="POST", url=TOKEN_URL,
                headers={"Content-Type": "application/x-www-form-urlencoded"},
                body=body, timeout=self.timeout)
            if (type(response.status) is not int or response.status != 200
                    or type(response.body) is not bytes or len(response.body) > MAX_RESPONSE_BYTES):
                return ""
            data = _object(response.body)
            token = data.get("access_token")
            if (not _text(token, 8192) or not re.fullmatch(r"[A-Za-z0-9._~+/-]+=*", token)
                    or type(data.get("token_type")) is not str or data["token_type"].lower() != "bearer"):
                return ""
            # Refresh cannot add scopes to the original grant. If Google reports
            # granted scopes, fail closed when the fixed required scope is absent.
            if "scope" in data and (type(data["scope"]) is not str or REQUIRED_SCOPE not in data["scope"].split()):
                return ""
            return token
        except Exception:
            return ""
