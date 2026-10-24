"""Study isolation, durable execution, controls, and evidence validation."""

from contextlib import contextmanager
from copy import deepcopy
from io import BytesIO
import json
import socket
import time

from PIL import Image
import pytest

from app import create_app
from app.api import product_testing as api
from app.services import product_studies as studies
from app.services.study_browser import BrowserStudyError, public_address, public_origin
from app.services.study_store import StudyStore
from app.utils.study_images import image_data_url, inspect_image, validate_image_url
from app import subscription_proxy as proxy
from app import chatgpt_auth


def png(color='white'):
    output = BytesIO()
    Image.new('RGB', (640, 480), color).save(output, format='PNG')
    return output.getvalue()


def spec(kind='first_impression', people=2):
    return {'name': 'Independent study', 'kind': kind, 'researcher_context': 'HIDDEN_PRODUCT_BRIEF_SECRET',
            'hypothesis': 'HIDDEN_PREFERRED_VARIANT_A', 'personas': deepcopy(studies.DEFAULT_PERSONAS[:people]),
            'variants': [{'name': 'HIDDEN_VARIANT_NAME_A', 'url': ''}, *([{'name': 'HIDDEN_VARIANT_NAME_B', 'url': ''}] if kind == 'comparison' else [])],
            'viewport': {'width': 640, 'height': 480}, 'max_requests': 16}


def actor_answer():
    return {'understanding': 'I see experiences on a map.', 'audience': 'People looking for outings.',
            'first_action': 'Open a visible photo pin.', 'uncertainties': ['Whether the details explain visiting conditions.'],
            'screen_evidence': [{'region': 'center', 'observation': 'There are photo pins.'}]}


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    app = create_app()
    app.config.update(TESTING=True, PRODUCT_TESTING_DIR=str(tmp_path), LLM_API_KEY='local-key',
                      CHATGPT_MAX_REQUESTS=100, CHATGPT_MAX_CONCURRENT=2)
    identity = {'owner': 'account-one', 'model': 'chosen-model'}
    monkeypatch.setattr(api, 'get_account_cache_scope', lambda: identity['owner'])
    monkeypatch.setattr(api, 'get_study_owner_scope', lambda: identity['owner'])
    monkeypatch.setattr(studies, 'get_account_cache_scope', lambda: identity['owner'])
    monkeypatch.setattr(studies, 'get_study_owner_scope', lambda: identity['owner'])
    monkeypatch.setattr(studies, 'get_selected_model', lambda: identity['model'])
    calls = []
    def model(messages, selected):
        calls.append(deepcopy(messages))
        if 'Evaluate independent' in messages[0]['content']:
            context = json.loads(messages[1]['content'][0]['text'])
            run = context['sessions'][0]
            quote = 'I see experiences on a map.' if 'understanding' in run['response'] else 'goal_observed'
            return json.dumps({'summary': 'A hypothesis to investigate.', 'findings': [{'title': 'Comprehension',
                'description': 'Check understanding with people.', 'severity': 'low',
                'evidence': [{'run_id': run['run_id'], 'quote': quote}]}], 'next_step': 'Observe real participants.'})
        return json.dumps(actor_answer())
    with app.app_context():
        store, runner = api.store_and_runner()
        runner.model_call = model
        app.extensions['product_vision_verified'] = {('account-one', 'chosen-model')}
    return app, store, runner, calls, identity


def prepare(workspace, kind='first_impression', people=2, identical=False):
    app, store, runner, calls, identity = workspace
    data = store.create(studies.validate_spec(spec(kind, people)), identity['owner'])
    assets = [store.save_asset(png('white')), store.save_asset(png('white' if identical else 'blue'))]
    def attach(current):
        for index, variant in enumerate(current['spec']['variants']):
            variant['asset'] = assets[index]
    return store.update(data['id'], attach)


def run_and_wait(workspace, data):
    app, store, runner, *_ = workspace
    with app.app_context():
        runner.start(data['id'])
    runner.threads[data['id']].join(timeout=8)
    assert not runner.threads[data['id']].is_alive()
    return store.get(data['id'])


def test_actor_inputs_exclude_researcher_context_labels_and_other_sessions(workspace):
    data = prepare(workspace, 'comparison')
    result = run_and_wait(workspace, data)
    assert result['status'] == 'completed', result['error']
    calls = workspace[3]
    assert len(calls) == 5  # Four fresh participants plus one evaluation.
    for messages in calls[:4]:
        assert len(messages) == 2
        payload = json.dumps(messages)
        for secret in ('HIDDEN_PRODUCT_BRIEF_SECRET', 'HIDDEN_VARIANT_NAME', 'HIDDEN_PREFERRED_VARIANT', 'run_'):
            assert secret not in payload
        assert 'I see experiences on a map.' not in payload
    evaluator = json.loads(calls[-1][1]['content'][0]['text'])
    assert evaluator['researcher_context'] == 'HIDDEN_PRODUCT_BRIEF_SECRET'
    assert 'HIDDEN_VARIANT_NAME' not in json.dumps(evaluator)
    assert result['report']['findings'][0]['evidence'][0]['quote'] == 'I see experiences on a map.'
    assert StudyStore(workspace[1].root).get(data['id'])['runs'] == result['runs']


def test_identical_screens_are_reported_as_control_not_a_design_winner(workspace):
    data = prepare(workspace, 'comparison', identical=True)
    result = run_and_wait(workspace, data)
    assert result['status'] == 'completed'
    assert result['report']['control'] == 'identical_screens'
    assert result['report']['findings'] == []
    assert len(workspace[3]) == 4
    assert 'model variation' in result['report']['summary']


def test_uncited_report_is_rejected_and_resume_does_not_repeat_saved_participants(workspace):
    app, store, runner, calls, _ = workspace
    data = prepare(workspace)
    original = runner.model_call
    def model(messages, selected):
        raw = original(messages, selected)
        if 'Evaluate independent' in messages[0]['content']:
            report = json.loads(raw)
            report['findings'][0]['evidence'][0]['quote'] = 'This never appeared in a session.'
            return json.dumps(report)
        return raw
    runner.model_call = model
    result = run_and_wait(workspace, data)
    assert result['status'] == 'failed' and result['report'] is None
    assert all(r['status'] == 'completed' for r in result['runs'])
    assert 'quote' in result['error']
    runner.model_call = original
    result = run_and_wait(workspace, data)
    assert result['status'] == 'completed'
    assert len(calls) == 4  # Two actors, rejected report, resumed report.
    assert result['requests_used'] == 4


def test_account_or_model_changes_stop_before_further_dispatch(workspace):
    app, store, runner, calls, identity = workspace
    data = prepare(workspace)
    original = runner.model_call
    def model(messages, selected):
        identity['model'] = 'different-model'
        return original(messages, selected)
    runner.model_call = model
    result = run_and_wait(workspace, data)
    assert result['status'] == 'failed'
    assert len(calls) == 1 and result['report'] is None
    assert 'changed' in result['error']


def test_cancellation_preserves_state_and_prevents_report_or_next_participant(workspace):
    app, store, runner, calls, _ = workspace
    data = prepare(workspace)
    original = runner.model_call
    def model(messages, selected):
        store.update(data['id'], lambda d: d.update(status='cancelled'))
        return original(messages, selected)
    runner.model_call = model
    result = run_and_wait(workspace, data)
    assert result['status'] == 'cancelled' and result['report'] is None
    assert result['requests_used'] == 1 and len(calls) == 1


def test_same_account_can_resume_after_a_new_signin_but_not_continue_old_session(workspace, monkeypatch):
    app, store, runner, calls, identity = workspace
    data = prepare(workspace)
    connection = {'id': 'first-login'}
    monkeypatch.setattr(studies, 'get_account_cache_scope', lambda: connection['id'])
    original = runner.model_call
    def changed(messages, selected):
        connection['id'] = 'second-login'
        return original(messages, selected)
    runner.model_call = changed
    assert run_and_wait(workspace, data)['status'] == 'failed'
    runner.model_call = original
    result = run_and_wait(workspace, data)
    assert result['status'] == 'completed'
    assert result['owner'] == 'account-one'
    assert result['connection_scope'] == 'second-login'


def test_incomplete_capture_fails_before_spending_a_model_request(workspace, monkeypatch):
    app, store, runner, calls, identity = workspace
    value = spec()
    value['variants'][0]['url'] = 'https://example.com/'
    data = store.create(studies.validate_spec(value), identity['owner'])
    def incomplete(*args):
        raise BrowserStudyError('The page still shows a loading state.')
    monkeypatch.setattr(studies, 'capture_public', incomplete)
    result = run_and_wait(workspace, data)
    assert result['status'] == 'failed' and 'loading state' in result['error']
    assert result['requests_used'] == 0 and calls == []


def test_request_cap_is_checked_before_starting_or_spending(workspace):
    app, store, runner, calls, _ = workspace
    data = prepare(workspace, 'comparison')
    store.update(data['id'], lambda d: d['spec'].update(max_requests=3))
    with app.app_context(), pytest.raises(studies.StudyError, match='request cap'):
        runner.start(data['id'])
    assert calls == [] and store.get(data['id'])['requests_used'] == 0


def test_api_ownership_freezing_and_asset_reference_boundaries(workspace):
    app, store, _, _, identity = workspace
    client = app.test_client()
    created = client.post('/api/product-testing/studies', json=spec()).json['data']
    sid = created['id']
    assert 'owner' not in created and 'worker_pid' not in created
    response = client.post(f'/api/product-testing/studies/{sid}/variants/v0/image', data={'image': (BytesIO(png()), 'shot.png')})
    assert response.status_code == 200
    asset = response.json['data']['spec']['variants'][0]['asset']
    assert client.get(f'/api/product-testing/studies/{sid}/assets/{asset["id"]}').data == png()
    other = store.save_asset(png('red'))
    assert client.get(f'/api/product-testing/studies/{sid}/assets/{other["id"]}').status_code == 404
    store.update(sid, lambda d: d.update(status='failed'))
    assert client.post(f'/api/product-testing/studies/{sid}/variants/v0/image', data={'image': (BytesIO(png()), 'shot.png')}).status_code == 400
    identity['owner'] = 'account-two'
    assert client.get(f'/api/product-testing/studies/{sid}').status_code == 404
    assert client.get(f'/api/product-testing/studies/{sid}/export').status_code == 404
    assert client.get('/api/product-testing/studies').json['data'] == []


@pytest.mark.parametrize('url', ['http://example.com', 'https://user:secret@example.com', 'https://example.com:444', 'file:///tmp/test'])
def test_public_capture_rejects_unapproved_url_forms(url):
    with pytest.raises(BrowserStudyError):
        public_origin(url)


@pytest.mark.parametrize('address', ['127.0.0.1', '10.0.0.1', '169.254.169.254', '::1', 'fd00::1'])
def test_public_capture_rejects_private_or_metadata_resolution(monkeypatch, address):
    monkeypatch.setattr(socket, 'getaddrinfo', lambda *a, **k: [(2, 1, 6, '', (address, 443))])
    with pytest.raises(BrowserStudyError, match='public internet'):
        public_address('https://example.com')


def test_raster_inputs_are_verified_and_remote_images_never_forwarded(workspace):
    app = workspace[0]
    url = image_data_url(png())
    assert inspect_image(png())['width'] == 640
    assert validate_image_url(url) == url
    with app.app_context():
        payload = proxy.chat_to_responses({'model': 'chosen-model', 'messages': [{'role': 'user', 'content': [
            {'type': 'text', 'text': 'Read this screen.'}, {'type': 'image_url', 'image_url': {'url': url, 'detail': 'high'}}]}]})
        assert payload['input'][0]['content'][1] == {'type': 'input_image', 'image_url': url, 'detail': 'high'}
        with pytest.raises(proxy.SubscriptionProxyError):
            proxy.chat_to_responses({'model': 'chosen-model', 'messages': [{'role': 'assistant', 'content': [{'type': 'image_url', 'image_url': {'url': url}}]}]})
    for value in ('https://example.com/shot.png', 'data:image/svg+xml;base64,abcd', url.replace('image/png', 'image/jpeg'), 'data:image/png;base64,AAAA'):
        with pytest.raises(ValueError):
            validate_image_url(value)


def test_saved_study_owner_survives_same_account_signin_but_is_not_shared(monkeypatch):
    account = {'access_token': 'example-token', 'subject': 'user-one', 'client_id': 'app-client', 'session_id': 'login-one'}
    monkeypatch.setattr(chatgpt_auth, '_read', lambda name: dict(account))
    owner = chatgpt_auth.get_study_owner_scope()
    session = chatgpt_auth.get_account_cache_scope()
    account['session_id'] = 'login-two'
    assert chatgpt_auth.get_study_owner_scope() == owner
    assert chatgpt_auth.get_account_cache_scope() != session
    account['subject'] = 'user-two'
    assert chatgpt_auth.get_study_owner_scope() != owner
    account['access_token'] = None
    with pytest.raises(chatgpt_auth.SubscriptionAuthError):
        chatgpt_auth.get_study_owner_scope()


@pytest.mark.parametrize('reachable', [True, False])
def test_browser_trace_checks_observed_success_instead_of_model_claim(workspace, monkeypatch, reachable):
    app, store, runner, _, identity = workspace
    value = spec('browser_task', 1)
    value.update(task='Open the experience details.', success_text='Experience details', max_steps=1)
    value['variants'][0]['url'] = 'https://example.com/'
    data = store.create(studies.validate_spec(value), identity['owner'])
    class Browser:
        acted = False
        def observe(self):
            return {'url': 'https://example.com/details' if self.acted else 'https://example.com/',
                    'text': 'Experience details' if self.acted and reachable else 'Login required', 'links': [], 'blocked_requests': []}
        def screenshot(self): return png()
        def act(self, action, observation):
            self.acted = True
            return 'continue' if reachable else 'access_restriction'
    @contextmanager
    def browser(*args): yield Browser()
    monkeypatch.setattr(studies, 'public_browser', browser)
    original = runner.model_call
    def model(messages, selected):
        if 'Evaluate independent' in messages[0]['content']:
            return json.dumps({'summary': 'Review the evidence.', 'findings': [], 'next_step': 'Use real participants.'})
        return json.dumps({'action': 'click', 'x': 20, 'y': 20, 'reason': 'Open the experience.', 'observation': 'A visible card.'})
    runner.model_call = model
    result = run_and_wait(workspace, data)
    assert result['status'] == 'completed', result['error']
    run = result['runs'][0]
    assert run['response']['success_observed'] is reachable
    assert run['response']['outcome'] == ('goal_observed' if reachable else 'access_restriction')
    if not reachable:
        assert 'not evidence of a product defect' in run['response']['outcome_reason']
    assert len(run['steps']) == 2
    assert run['steps'][-1]['screenshot']['sha256']
