"""Deployed manual-only factory never constructs a Google credential/provider boundary."""
from types import SimpleNamespace as NS
import pytest
import pilot_adapter as adapter
from workshop_marketing_agent.generation import ModelCallResult
from workshop_marketing_agent.campaign_state import dump_campaign
from test_callable_google_oauth import wired, pilot, harness, source, assert_counts
from test_service import request


@pytest.fixture
def manual_only(wired, monkeypatch):
    h=wired
    def forbidden(*a,**k): pytest.fail('Google boundary used in manual deployment')
    monkeypatch.setattr(adapter,'GoogleLocalPosts',forbidden)
    monkeypatch.setattr(adapter,'RefreshTokenProvider',forbidden)
    def factory(ctx,**kwargs):
        service=adapter.build_service(ctx,**kwargs,google_oauth=forbidden,token_http=forbidden,posts_http=forbidden)
        assert service.dependencies.google is None
        return service
    monkeypatch.setattr(h.main,'build_service',factory)
    return h


def test_callable_handoff_and_crafted_automatic_actions(manual_only):
    h=manual_only
    before=dump_campaign(h.repo.load('c-1'))
    data={k:v for k,v in h.intent.items() if k!='request_id'}|{'action':'get_google_handoff'}
    assert h.invoke(data)['result']['status']=='ready_for_manual_publication'
    assert h.invoke()['result']['status']=='google_api_unavailable'
    assert h.invoke(request(h,'reconcile_google_publication',attempt_reference='unknown'))['result']['status']=='google_api_unavailable'
    assert dump_campaign(h.repo.load('c-1'))==before
    assert_counts(h,0,0,0)


@pytest.mark.parametrize('denial',['auth','app_check','access'])
def test_manual_handoff_authentication(manual_only,denial):
    h=manual_only
    if denial=='auth':h.authenticated=False
    elif denial=='app_check':h.attested=False
    else:h.allowed=False
    data={k:v for k,v in h.intent.items() if k!='request_id'}|{'action':'get_google_handoff'}
    result=h.invoke(data)
    assert 'error' in result or result['result']['status'] in ('forbidden','unauthenticated')
    assert_counts(h,0,0,0)


def test_manual_factory_generates_and_revises_with_fake_openai(manual_only,monkeypatch):
    h=manual_only;calls=[]
    monkeypatch.setattr(h.main,'OPENAI_API_KEY',NS(value='synthetic-openai-key'))
    output=h.wording
    class Client:
        def __init__(self,**kwargs): assert kwargs=={'api_key':'synthetic-openai-key'}
        def generate(self,req):
            calls.append(req)
            return ModelCallResult('completed',output=output)
    monkeypatch.setattr(adapter,'OpenAIDraftClient',Client)
    assert h.invoke(request(h,'start_round',round_reference='r-2',purpose='Erinnerung',selected_channels=['google_business']))['result']['status']=='ok'
    result=h.invoke(request(h,'generate_drafts',round_reference='r-2',channels=['google_business']))['result']
    assert result['status']=='ok',result
    version=result['results'][0]['version_reference']
    output={'title':h.wording['google_title'],'text':h.wording['google_body']}
    result=h.invoke(request(h,'ai_revision',round_reference='r-2',channel='google_business',version_reference=version,instruction='Klarer formulieren',selected_image_reference=h.data['image']['reference']))['result']
    assert result['status']=='ok',result
    assert len(calls)==2
    assert_counts(h,0,0,0)
