"""Exact read-only manual handoff with offline evidence and unchanged snapshots."""
from dataclasses import replace
import pytest
from workshop_marketing_agent.campaign_state import dump_campaign
from workshop_marketing_agent.service import PilotService
from test_service import PRINCIPAL, call
from test_service_publication import pilot, harness, source, publish


def handoff(h, **changes):
    data={k:v for k,v in h.intent.items() if k != 'request_id'}
    return h.service.handle(data | {'action':'get_google_handoff'} | changes, principal=PRINCIPAL)


def test_exact_stored_projection_and_no_writes(pilot):
    h=pilot
    h.service=PilotService(replace(h.dependencies, google=None))
    before=dump_campaign(h.repo.load('c-1'))
    stored=h.repo.load('c-1').rounds[0].channels[0].submissions[-1].package
    result=handoff(h)
    assert result['status']=='ready_for_manual_publication', result
    assert result['package']==dict(summary=stored.payload.summary,event_title=stored.payload.event.title,
        schedule=stored.payload.event.schedule.model_dump(mode='json'),time_zone=stored.version.workshop_facts.time_zone,
        booking_url=stored.payload.callToAction.url,image_reference=stored.version.selected_image_reference)
    assert dump_campaign(h.repo.load('c-1'))==before
    assert handoff(h)==result
    assert len(h.feed_calls)==2 and not h.google.calls


@pytest.mark.parametrize('key',['channel','request_id','summary','actor_reference','payload','fingerprint','location','provider_id'])
def test_extra_fields_rejected(pilot,key):
    assert handoff(pilot,**{key:'untrusted'})['status']=='invalid_request'
    assert not pilot.feed_calls


@pytest.mark.parametrize('key',['version_reference','approval_reference','submission_reference','round_reference'])
def test_associations(pilot,key):
    assert handoff(pilot,**{key:'wrong'})['status']=='review_required'


def test_unselected_and_disabled(pilot):
    h=pilot
    call(h,'set_channel_enabled',channel='google_business',enabled=False)
    assert handoff(h,expected_revision=h.repo.load('c-1').revision)['status']=='review_required'


@pytest.mark.parametrize('change',['source','image','status','copy'])
def test_changed_evidence(pilot,change):
    h=pilot
    if change=='source': h.data['source_version']='new-source'
    elif change=='image': h.data['image']['marketing_permission']=None
    elif change=='status': h.data['provenance']['active']=False
    else: h.data['content']['public_description_html']='Changed original copy'
    result=handoff(h)
    assert result['status'] in ('outdated','review_required','provider_unavailable'),result
    assert 'package' not in result and not h.google.calls


def test_unknown_attempt_blocks_handoff(pilot):
    h=pilot;h.google.error=TimeoutError()
    assert publish(h)['status']=='outcome_unknown'
    before=dump_campaign(h.repo.load('c-1'))
    assert handoff(h,expected_revision=h.repo.load('c-1').revision)['status']=='reconciliation_required'
    assert dump_campaign(h.repo.load('c-1'))==before


def test_disabled_api_actions_fail_before_repository_and_provider(pilot):
    h=pilot;h.service=PilotService(replace(h.dependencies,google=None));h.events.clear()
    before=dump_campaign(h.repo.load('c-1'))
    for request in (h.intent,dict(h.intent,action='reconcile_google_publication')):
        if request['action']=='reconcile_google_publication':
            request={k:v for k,v in request.items() if k not in ('version_reference','approval_reference','submission_reference')}
            request['attempt_reference']='some-attempt'
        result=h.service.handle(request,principal=PRINCIPAL)
        assert result['status']=='google_api_unavailable'
    assert dump_campaign(h.repo.load('c-1'))==before
    assert not h.google.calls and not h.feed_calls


def test_authorization_before_handoff(pilot):
    h=pilot;h.service=PilotService(replace(h.dependencies,check_access=lambda *_:False))
    assert handoff(h)['status']=='forbidden'
    assert not h.feed_calls


def test_unselected_version_and_changed_link_require_review(pilot):
    h=pilot
    selected=h.repo.load('c-1').rounds[0].channels[0].versions[0].reference
    call(h,'select_version',channel='google_business',version_reference=selected)
    assert handoff(h,expected_revision=h.repo.load('c-1').revision)['status']=='review_required'


def test_changed_stored_selection_during_fetch_is_not_returned(pilot):
    h=pilot
    original=h.dependencies.feed_loader
    def feed(*args):
        response=original(*args)
        call(h,'set_channel_enabled',channel='google_business',enabled=False)
        return response
    h.service=PilotService(replace(h.dependencies,feed_loader=feed))
    result=handoff(h)
    assert result['status']=='review_required' and 'package' not in result


def test_explicit_text_only_approval_has_no_invented_image(pilot):
    h=pilot
    version=h.repo.load('c-1').rounds[0].channels[0].versions[-1].version
    result=call(h,'direct_revision',channel='google_business',version_reference=h.intent['version_reference'],
        title=version.content.title,text=version.content.body,selected_image_reference=None)
    reference=result['results'][0]['version_reference']
    approval=call(h,'approve_version',channel='google_business',version_reference=reference)['results'][0]['approval_reference']
    submission=call(h,'prepare_submission',channel='google_business',version_reference=reference,approval_reference=approval)['results'][0]['preparation_reference']
    result=handoff(h,expected_revision=h.repo.load('c-1').revision,version_reference=reference,approval_reference=approval,submission_reference=submission)
    assert result['status']=='ready_for_manual_publication',result
    assert result['package']['image_reference'] is None
