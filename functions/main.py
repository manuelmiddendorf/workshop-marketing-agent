"""Second-generation Callable entrypoint; discovery reads no parameter values."""

from firebase_functions import https_fn
from firebase_functions.params import SecretParam, StringParam

from pilot_adapter import FUNCTION_TIMEOUT, REGION, build_service, handle_callable, initialize_admin

_app = initialize_admin()
OPENAI_API_KEY = SecretParam("PILOT_OPENAI_API_KEY")
MODEL = StringParam("PILOT_MODEL", description="Explicit OpenAI model for the pilot; no fallback")


@https_fn.on_call(region=REGION, timeout_sec=FUNCTION_TIMEOUT,
                 secrets=[OPENAI_API_KEY], enforce_app_check=True)
def pilot_teacher_service(request: https_fn.CallableRequest) -> dict:
    return handle_callable(request, service_factory=lambda context: build_service(
        context, app=_app, model=MODEL.value, api_key=lambda: OPENAI_API_KEY.value,
    ))
