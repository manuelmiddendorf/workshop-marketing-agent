"""Firebase integration only; request data never supplies authority or configuration."""

from datetime import UTC, datetime
import re
from threading import Lock

import firebase_admin
from firebase_admin import auth, firestore

from workshop_marketing_agent.feed import _http_get
from workshop_marketing_agent.firestore_repository import FirestoreCampaignRepository
from workshop_marketing_agent.generation import OpenAIDraftClient
from workshop_marketing_agent.service import PilotService, ServiceDependencies, VerifiedPrincipal, _response

PROJECT = "middendorf-yoga"
DATABASE = "(default)"
COLLECTION = "pilotMarketingCampaigns"
REGION = "europe-west1"
FUNCTION_TIMEOUT = 300
FEED_TIMEOUT = 10.0
MODEL_TIMEOUT = 60.0
AUTH_TIMEOUT = 10
WORKSHOP = "malws-copy"
CHANNELS = frozenset({"google_business", "rausgegangen"})
ENDPOINT = (
    "https://europe-west1-middendorf-yoga.cloudfunctions.net/WSGetPublicWorkshops"
    "?format=workshop-data.v1&id=malws-copy"
)
_app_lock = Lock()


def initialize_admin():
    """Construct the SDK's lazy credential holder, never resolve credentials here.

    Callable token verification needs the default app before our handler runs.
    Explicit options avoid reading Firebase configuration files during discovery.
    """
    with _app_lock:
        try:
            app = firebase_admin.get_app()
        except ValueError:
            app = firebase_admin.initialize_app(options={"projectId": PROJECT, "httpTimeout": AUTH_TIMEOUT})
        if app.project_id != PROJECT or app.options.get("httpTimeout") != AUTH_TIMEOUT:
            raise ValueError("Unexpected Firebase application configuration")
        return app


def normalized_email(value):
    """Match existing trim/lower normalization, without alias or Unicode rewriting."""
    if not isinstance(value, str):
        return None
    email = value.strip().lower()
    if (len(email.encode("utf-8")) > 254
            or not re.fullmatch(r"[^\s/@\x00-\x1f\x7f]+@[^\s/@\x00-\x1f\x7f]+", email)):
        return None
    return email


def teacher_has_access(context, workshop, *, get_user, get_user_by_email, read_role):
    """Recheck authoritative identity and role on every call, including replays.

    UID and email lookups must identify the same enabled, non-tenant account.
    Lookup exceptions or malformed records never establish access.
    """
    try:
        if workshop != WORKSHOP or not isinstance(context.uid, str) or not context.uid:
            return False
        token = context.token
        if type(token) is not dict or type(token.get("firebase")) is not dict:
            return False
        if "tenant" in token["firebase"]:
            return False
        email = normalized_email(token.get("email"))
        if email is None:
            return False
        for account in (get_user(context.uid), get_user_by_email(email)):
            if (account.uid != context.uid or account.disabled is not False
                    or account.tenant_id is not None or normalized_email(account.email) != email):
                return False
            # Callable verification does not check revocation. Compare current Auth state.
            auth_time = token.get("auth_time")
            valid_after = account.tokens_valid_after_timestamp
            if (type(auth_time) not in (int, float) or not 0 <= auth_time < float("inf")
                    or type(valid_after) is not int or valid_after < 0
                    or auth_time * 1000 < valid_after):
                return False
        role = read_role(email)
        return (type(role) is dict and role.get("type") in ("MSN", "SBN")
                and ("uid" not in role or role["uid"] == context.uid))
    except Exception:
        return False


def build_service(context, *, app, model, api_key):
    """Trusted wiring; secrets and clients are evaluated only during an invocation."""
    database = firestore.client(app=app, database_id=DATABASE)

    def read_role(email):
        snapshot = database.collection("Users").document(email).get(retry=None, timeout=AUTH_TIMEOUT)
        return snapshot.to_dict() if snapshot.exists is True else None

    def check_access(principal, workshop):
        return principal.subject == context.uid and teacher_has_access(
            context, workshop, get_user=lambda uid: auth.get_user(uid, app=app),
            get_user_by_email=lambda email: auth.get_user_by_email(email, app=app), read_role=read_role,
        )

    # Laziness keeps denied and read-only calls away from secret access/model construction.
    class DraftClient:
        def generate(self, request):
            key = api_key()
            if not isinstance(key, str) or not key.strip():
                raise ValueError("Missing model secret")
            return OpenAIDraftClient(api_key=key).generate(request)

    return PilotService(ServiceDependencies(
        repository=FirestoreCampaignRepository(database, collection_name=COLLECTION),
        check_access=check_access, clock=lambda: datetime.now(UTC),
        endpoint_for=lambda _: ENDPOINT, feed_loader=_http_get, client=DraftClient(),
        model=model, model_timeout=MODEL_TIMEOUT, feed_timeout=FEED_TIMEOUT,
        workshops=frozenset({WORKSHOP}), channels=CHANNELS,
    ))


def handle_callable(request, *, service_factory):
    """Consume SDK-verified context only; preserve the service's sanitized result."""
    try:
        if request.auth is None:
            return _response("unauthenticated")
        principal = VerifiedPrincipal(subject=request.auth.uid)
        service = service_factory(request.auth)
        return service.handle(request.data, principal=principal)
    except Exception:
        # Do not log provider text or turn an uncertain mutation into an automatic retry.
        return _response("internal_error")
