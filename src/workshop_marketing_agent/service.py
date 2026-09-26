"""Framework-independent teacher actions. Trust is supplied by the server, never JSON."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Annotated, Literal
from urllib.parse import urlsplit

from pydantic import Field, TypeAdapter, model_validator

from . import application as app
from .campaign import CampaignMetadata, Reference
from .campaign_state import (
    CampaignRepository, CampaignState, FrozenState, ServiceRequestBinding,
    dump_campaign, restore_campaign,
)
from .feed import FeedResponse, load_public_workshop
from .firestore_repository import CampaignStorageError, CommitOutcomeUnknown
from .generation import DraftClient, generate_pilot_drafts
from .revision import ChannelId, _aware_utc, _fingerprint_payload
from .service_views import campaign_view, diagnostic_views, result_view


class VerifiedPrincipal(FrozenState):
    """Construct only from verified server authentication context, not request data."""

    subject: Annotated[str, Field(min_length=1, max_length=256)]

    @property
    def actor_reference(self) -> str:
        return "teacher-" + hashlib.sha256(self.subject.encode()).hexdigest()


class Request(FrozenState):
    workshop_reference: Reference
    campaign_reference: Reference


class GetCampaign(Request):
    action: Literal["get_campaign"]


class CreateCampaign(Request):
    action: Literal["create_campaign"]
    request_id: Reference


class Mutation(CreateCampaign):
    expected_revision: Annotated[int, Field(ge=0)]
    round_reference: Reference


class StartRound(Mutation):
    action: Literal["start_round"]
    purpose: Annotated[str, Field(min_length=1, max_length=500)]
    selected_channels: tuple[ChannelId, ...]

    @model_validator(mode="after")
    def selection(self):
        if not self.purpose.strip() or len(set(self.selected_channels)) != len(self.selected_channels):
            raise ValueError("Invalid round selection")
        return self


class GenerateDrafts(Mutation):
    action: Literal["generate_drafts"]
    channels: Annotated[tuple[ChannelId, ...], Field(min_length=1, max_length=2)]

    @model_validator(mode="after")
    def unique_channels(self):
        if len(set(self.channels)) != len(self.channels):
            raise ValueError("Duplicate channels")
        return self


class ChannelMutation(Mutation):
    channel: ChannelId


class VersionMutation(ChannelMutation):
    version_reference: Reference


class DirectRevision(VersionMutation):
    action: Literal["direct_revision"]
    title: Annotated[str, Field(min_length=1, max_length=500)]
    text: Annotated[str, Field(min_length=1, max_length=10_000)]
    selected_image_reference: Annotated[str, Field(max_length=2048)] | None


class AIRevision(VersionMutation):
    action: Literal["ai_revision"]
    instruction: Annotated[str, Field(min_length=1, max_length=500)]
    selected_image_reference: Annotated[str, Field(max_length=2048)] | None


class BindLink(VersionMutation):
    action: Literal["bind_link"]
    variant_reference: Reference


class SelectVersion(VersionMutation):
    action: Literal["select_version"]


class ApproveVersion(VersionMutation):
    action: Literal["approve_version"]


class PrepareSubmission(VersionMutation):
    action: Literal["prepare_submission"]
    approval_reference: Reference


class SetChannelEnabled(ChannelMutation):
    action: Literal["set_channel_enabled"]
    enabled: bool


TeacherRequest = Annotated[
    GetCampaign | CreateCampaign | StartRound | GenerateDrafts | DirectRevision | AIRevision
    | BindLink | SelectVersion | ApproveVersion | PrepareSubmission | SetChannelEnabled,
    Field(discriminator="action"),
]
REQUEST_ADAPTER = TypeAdapter(TeacherRequest)


@dataclass(frozen=True)
class ServiceDependencies:
    repository: CampaignRepository
    check_access: Callable[[VerifiedPrincipal, str], bool]
    clock: Callable[[], datetime]
    endpoint_for: Callable[[str], str]
    # Same bounded HTTP signature as the public importer. Only trusted server code supplies it.
    feed_loader: Callable[[str, float], FeedResponse]
    client: DraftClient
    model: str
    model_timeout: float
    feed_timeout: float
    workshops: frozenset[str]
    channels: frozenset[ChannelId]

    def __post_init__(self):
        for timeout in (self.model_timeout, self.feed_timeout):
            if type(timeout) not in (int, float) or not 0 < timeout <= 120:
                raise ValueError("Timeouts must be positive and bounded by 120 seconds")
        if not isinstance(self.model, str) or not self.model.strip() or self.client is None:
            raise ValueError("Explicit model configuration is required")
        if (not self.workshops or not self.channels
                or not self.channels <= {"google_business", "rausgegangen"}):
            raise ValueError("Explicit pilot allowlists are required")
        for reference in self.workshops:
            TypeAdapter(Reference).validate_python(reference)


_MESSAGES = {
    "ok": "Aktion gespeichert.", "replayed": "Gespeichertes Ergebnis erneut angezeigt.",
    "unauthenticated": "Bitte anmelden.", "forbidden": "Kein bestätigter Zugriff auf diesen Workshop.",
    "not_found": "Kampagne nicht gefunden.", "invalid_request": "Bitte Angaben und Auswahl prüfen.",
    "revision_conflict": "Der Stand wurde geändert. Bitte neu laden und prüfen.",
    "request_conflict": "Diese Anfrage-ID wurde bereits anders verwendet.",
    "outdated": "Die Workshopdaten haben sich geändert. Bitte erneut prüfen.",
    "review_required": "Bitte die markierten Angaben prüfen.",
    "provider_unavailable": "Daten oder Textdienst derzeit nicht verfügbar.",
    "storage_unavailable": "Speicherung derzeit nicht verfügbar.",
    "internal_error": "Die Aktion konnte nicht abgeschlossen werden.",
    "unknown_commit_outcome": "Speicherergebnis unklar. Bitte neu laden; nicht automatisch wiederholen.",
    "partial_completion": "Nur ein Teil wurde gespeichert. Bitte prüfen; keine automatische Fortsetzung.",
}


def _response(status: str, **fields) -> dict:
    return {"status": status, "message": _MESSAGES[status], **fields}


def _command_id(request_id: str, slot: str = "action") -> str:
    return "service-" + _fingerprint_payload({"request_id": request_id, "slot": slot})


def _binding(request, principal) -> ServiceRequestBinding:
    # Revision is a CAS precondition, not intent; server time/evidence never come from the browser.
    intent = request.model_dump(mode="json", exclude={"expected_revision"})
    return ServiceRequestBinding(request_id=request.request_id, actor_reference=principal.actor_reference,
        intent_fingerprint=_fingerprint_payload({"schema": "teacher-intent.v1", "actor": principal.actor_reference,
                                                "request": intent}))


class PilotService:
    def __init__(self, dependencies: ServiceDependencies):
        self.dependencies = dependencies

    def handle(self, request_data: dict, *, principal: VerifiedPrincipal | None) -> dict:
        """Return JSON-compatible allowlisted fields; never log or expose caught exception text."""
        if not isinstance(principal, VerifiedPrincipal):
            return _response("unauthenticated")
        try:
            principal = VerifiedPrincipal.model_validate_json(principal.model_dump_json(warnings="error"))
            if type(request_data) is not dict:
                raise ValueError("Expected request object")
            request = REQUEST_ADAPTER.validate_json(json.dumps(request_data, allow_nan=False))
        except (ValueError, TypeError):
            return _response("invalid_request")
        if request.workshop_reference not in self.dependencies.workshops:
            return _response("forbidden")
        try:
            allowed = self.dependencies.check_access(principal, request.workshop_reference)
        except Exception:
            allowed = False
        if allowed is not True:
            return _response("forbidden")
        requested_channels = (getattr(request, "channels", ()) or getattr(request, "selected_channels", ())
                              or ((request.channel,) if isinstance(request, ChannelMutation) else ()))
        if any(channel not in self.dependencies.channels for channel in requested_channels):
            return _response("invalid_request")
        try:
            return self._authorized(request, principal)
        except CommitOutcomeUnknown:
            return _response("unknown_commit_outcome", refresh_required=True)
        except CampaignStorageError:
            return _response("storage_unavailable", refresh_required=True)
        except Exception:
            return _response("internal_error")

    def _load(self, request):
        state = self.dependencies.repository.load(request.campaign_reference)
        if state is not None:
            state = restore_campaign(dump_campaign(state))
            if (state.campaign_reference != request.campaign_reference
                    or state.workshop_reference != request.workshop_reference):
                return None, _response("not_found")
        return state, None

    def _now(self):
        return _aware_utc(self.dependencies.clock(), field="server time")

    def _authorized(self, request, principal):
        state, error = self._load(request)
        if error:
            return error
        if isinstance(request, GetCampaign):
            return (_response("not_found") if state is None else
                    _response("ok", campaign=campaign_view(state, self.dependencies.channels)))
        binding = _binding(request, principal)
        if state is not None:
            prior = [r for r in state.receipts if r.service_request is not None
                     and r.service_request.request_id == request.request_id]
            creation = state.creation_request
            if ((creation is not None and creation.request_id == request.request_id and creation != binding)
                    or any(r.service_request != binding for r in prior)):
                return _response("request_conflict")
            if prior:
                complete = not isinstance(request, GenerateDrafts) or len(prior) == len(request.channels)
                return _response("replayed" if complete else "partial_completion", historical=True,
                    automatic_resume=False, results=[result_view(r.result) for r in prior],
                    pending_channels=([c for c in request.channels if c not in {r.result.channel for r in prior}]
                                      if isinstance(request, GenerateDrafts) else []),
                    campaign=campaign_view(state, self.dependencies.channels))
            if isinstance(request, CreateCampaign) and not isinstance(request, Mutation):
                if creation == binding:
                    return _response("replayed", historical=True, campaign=campaign_view(state, self.dependencies.channels))
                return _response("revision_conflict", refresh_required=True, revision=state.revision)
        if isinstance(request, CreateCampaign) and not isinstance(request, Mutation):
            initial = app.new_campaign(campaign_reference=request.campaign_reference,
                workshop_reference=request.workshop_reference, actor_reference=principal.actor_reference, now=self._now())
            initial = initial.model_copy(update={"creation_request": binding})
            if not self.dependencies.repository.compare_and_save(initial, expected_revision=None):
                return self._conflict(request)
            return _response("ok", campaign=campaign_view(initial, self.dependencies.channels))
        if state is None:
            return _response("not_found")
        if state.revision != request.expected_revision:
            return _response("revision_conflict", refresh_required=True, revision=state.revision)
        if isinstance(request, GenerateDrafts):
            return self._generate(request, state, binding)
        evidence = None
        now = self._now()
        if isinstance(request, (DirectRevision, AIRevision, BindLink, ApproveVersion, PrepareSubmission)):
            evidence, now, error = self._evidence(request)
            if error:
                return error
        fields = request.model_dump(exclude={"action", "request_id", "variant_reference"})
        fields.update(command_id=_command_id(request.request_id), actor_reference=principal.actor_reference, now=now)
        classes = {"start_round": app.StartRound, "direct_revision": app.DirectRevision,
            "ai_revision": app.AIRevision, "bind_link": app.BindLink, "select_version": app.SelectVersion,
            "approve_version": app.ApproveVersion, "prepare_submission": app.PrepareSubmission,
            "set_channel_enabled": app.SetChannelEnabled}
        if isinstance(request, AIRevision):
            fields.update(model=self.dependencies.model, timeout=float(self.dependencies.model_timeout))
        if isinstance(request, BindLink):
            fields["campaign"] = CampaignMetadata(workshop=request.workshop_reference,
                campaign=request.campaign_reference, round=request.round_reference, channel=request.channel,
                variant=request.variant_reference)
        outcome = app.execute_command(self.dependencies.repository, classes[request.action](**fields),
            evidence=evidence, client=self.dependencies.client, service_request=binding)
        return self._outcome(request, outcome)

    def _evidence(self, request):
        endpoint = self.dependencies.endpoint_for(request.workshop_reference)
        # Never forward a request-controlled destination or credentials to the loader.
        url = urlsplit(endpoint)
        if (url.scheme != "https" or not url.hostname or url.username is not None or url.password is not None
                or url.fragment or "\\" in endpoint or any(c.isspace() or ord(c) < 32 for c in endpoint)):
            raise ValueError("Invalid configured endpoint")
        url.port
        try:
            response = self.dependencies.feed_loader(endpoint, float(self.dependencies.feed_timeout))
        except Exception:
            return None, None, _response("provider_unavailable")
        # Observations can be newer than request start. Validate only after fetching.
        now = self._now()
        evidence = load_public_workshop(endpoint=endpoint, workshop_id=request.workshop_reference,
            timeout=float(self.dependencies.feed_timeout), now=now, http_get=lambda *_: response)
        if evidence.validation is None:
            return None, now, _response("provider_unavailable", diagnostics=diagnostic_views(evidence.diagnostics))
        return evidence, now, None

    def _conflict(self, request):
        state, error = self._load(request)
        if error:
            return error
        return _response("revision_conflict", refresh_required=True,
                         revision=state.revision if state is not None else None)

    def _outcome(self, request, outcome):
        if outcome.status == "conflict":
            return self._conflict(request)
        if outcome.status == "rejected":
            return _response("invalid_request")
        result = result_view(outcome.result)
        status = result["status"] if result["status"] in ("outdated", "review_required", "provider_unavailable") else "ok"
        return _response("replayed" if outcome.status == "replayed" else status,
                         historical=outcome.status == "replayed", results=[result])

    def _generate(self, request, state, binding):
        round_ = next((r for r in state.rounds if r.reference == request.round_reference), None)
        if round_ is None:
            return _response("invalid_request")
        selected = {c.channel: c for c in round_.channels if c.channel in request.channels}
        if any(not c.enabled or c.versions for c in selected.values()):
            return _response("invalid_request")
        evidence, now, error = self._evidence(request)
        if error:
            return error
        generated = generate_pilot_drafts(evidence, marketing_round_purpose=round_.purpose, now=now,
            model=self.dependencies.model, timeout=self.dependencies.model_timeout, client=self.dependencies.client)
        if not generated.has_drafts:
            diagnostics = diagnostic_views(generated.diagnostics)
            provider = any(d["code"] == "provider" for d in diagnostics)
            return _response("provider_unavailable" if provider else "review_required", diagnostics=diagnostics)
        results = []
        revision = state.revision
        # One bounded existing model call yields both drafts; commit requested channels independently.
        for channel in request.channels:
            command = app.AttachDraft(campaign_reference=request.campaign_reference,
                workshop_reference=request.workshop_reference, round_reference=request.round_reference,
                expected_revision=revision, command_id=_command_id(request.request_id, channel),
                actor_reference=binding.actor_reference, now=self._now(), channel=channel,
                content=generated.google if channel == "google_business" else generated.rausgegangen,
                facts=generated.facts, metadata=generated.metadata,
                selected_image_reference=(evidence.validation.workshop.image.reference
                                          if evidence.validation.workshop.image is not None else None))
            try:
                outcome = app.execute_command(self.dependencies.repository, command, evidence=evidence,
                                              service_request=binding)
            except CommitOutcomeUnknown:
                return _response("unknown_commit_outcome", results=results, refresh_required=True,
                                 automatic_resume=False, uncertain_channel=channel)
            except CampaignStorageError:
                return _response("partial_completion" if results else "storage_unavailable",
                                 results=results, refresh_required=True, automatic_resume=False,
                                 failure="storage_unavailable", pending_channels=list(request.channels[len(results):]))
            if outcome.status not in ("committed", "replayed"):
                return _response("partial_completion" if results else "revision_conflict", results=results,
                                 refresh_required=True, automatic_resume=False, failure="revision_conflict",
                                 pending_channels=list(request.channels[len(results):]))
            results.append(result_view(outcome.result))
            revision = outcome.result.revision
        status = "review_required" if any(r["status"] == "review_required" for r in results) else "ok"
        return _response(status, results=results, historical=False)
