import json
import os
import time
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

from cryptography.hazmat.primitives.asymmetric import rsa
from flask import Flask
import jwt
import pytest

from app import chatgpt_auth as auth


@pytest.fixture
def local_app(tmp_path, monkeypatch):
    monkeypatch.setattr(auth.Config, 'CHATGPT_AUTH_DIR', str(tmp_path / 'auth'))
    auth._pending.clear()
    app = Flask(__name__)
    app.register_blueprint(auth.chatgpt_auth_bp)
    app.config['TESTING'] = True
    yield app
    auth._pending.clear()


def begin(client):
    response = client.get('/api/chatgpt/login', base_url='http://127.0.0.1:5001')
    assert response.status_code == 302
    return parse_qs(urlsplit(response.location).query)


def token_fixture(monkeypatch, nonce):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    monkeypatch.setattr(auth._jwks, 'get_signing_key_from_jwt', lambda _: SimpleNamespace(key=key.public_key()))
    encoded = jwt.encode({'iss': auth.ISSUER, 'aud': 'oaiapp_test', 'sub': 'user1',
                          'email': 'person@example.test', 'iat': int(time.time()),
                          'exp': int(time.time()) + 3600, 'nonce': nonce}, key, algorithm='RS256')
    return {'access_token': 'secret-access', 'refresh_token': 'secret-refresh',
            'id_token': encoded, 'token_type': 'Bearer', 'expires_in': 3600, 'scope': auth.SCOPES}


def test_registration_pkce_host_persistence_and_no_partner_secret(local_app):
    client = local_app.test_client()
    first = begin(client)
    second = begin(client)
    assert first['client_id'] == ['dynamic_agent_client']
    assert first['agent_name_hint'] == ['MiroFish Local']
    assert first['ext_agent_host_id'] == second['ext_agent_host_id']
    assert first['code_challenge_method'] == ['S256']
    assert first['state'] != second['state']
    assert first['resource'] == ['https://api.openai.com/v1']
    assert auth.PLAN_SCOPE in first['scope'][0].split()
    assert 'client_secret' not in first


def test_callback_needs_same_browser_state_and_is_one_use(local_app, monkeypatch):
    client = local_app.test_client()
    params = begin(client)
    tokens = token_fixture(monkeypatch, params['nonce'][0])
    calls = []
    monkeypatch.setattr(auth, '_token_request', lambda data: calls.append(data) or tokens)
    path = '/auth/callback?state=' + params['state'][0] + '&code=one-use-code&client_id=oaiapp_test'
    outsider = local_app.test_client()
    assert outsider.get(path, base_url='http://127.0.0.1:5001').status_code == 400
    assert not calls
    result = client.get(path, base_url='http://127.0.0.1:5001')
    assert result.status_code == 200
    assert result.headers['Cache-Control'] == 'no-store'
    assert b'secret-access' not in result.data
    assert calls[0]['client_id'] == 'oaiapp_test'
    assert calls[0]['redirect_uri'] == params['redirect_uri'][0]
    path_on_disk = auth._directory() / 'account.json'
    assert path_on_disk.stat().st_mode & 0o777 == 0o600
    assert auth._read('account.json')['subject'] == 'user1'
    assert client.get(path, base_url='http://127.0.0.1:5001').status_code == 400
    assert len(calls) == 1


@pytest.mark.parametrize('problem', ['nonce', 'permission'])
def test_bad_identity_or_declined_plan_never_replaces_credentials(local_app, monkeypatch, problem):
    client = local_app.test_client()
    params = begin(client)
    tokens = token_fixture(monkeypatch, 'wrong' if problem == 'nonce' else params['nonce'][0])
    if problem == 'permission':
        tokens['scope'] = 'openid email'
    monkeypatch.setattr(auth, '_token_request', lambda _: tokens)
    response = client.get('/auth/callback', query_string={'state': params['state'][0], 'code': 'code', 'client_id': 'oaiapp_test'},
                          base_url='http://127.0.0.1:5001')
    assert response.status_code in {401, 403}
    assert not auth._read('account.json')


@pytest.mark.parametrize('base_url,origin,address', [
    ('http://127.0.0.1:5001', 'https://evil.example', '127.0.0.1'),
    ('http://rebound.example:5001', None, '127.0.0.1'),
    ('http://127.0.0.1:5001', None, '192.168.1.2'),
])
def test_foreign_origin_rebinding_and_remote_clients_rejected(local_app, base_url, origin, address):
    headers = {'Origin': origin} if origin else {}
    response = local_app.test_client().post('/api/chatgpt/logout', base_url=base_url, headers=headers,
                                            environ_overrides={'REMOTE_ADDR': address})
    assert response.status_code == 403


def test_refresh_rotates_all_credentials_and_preserves_scope(local_app, monkeypatch):
    auth._write('account.json', {'client_id': 'oaiapp_test', 'subject': 'user1',
                               'access_token': 'expired', 'refresh_token': 'old-refresh',
                               'scopes': [auth.PLAN_SCOPE], 'expires_at': 0, 'selected_model': 'account-model'})
    calls = []
    monkeypatch.setattr(auth, '_token_request', lambda data: calls.append(data) or {
        'access_token': 'renewed', 'refresh_token': 'new-refresh', 'expires_in': 3600, 'token_type': 'Bearer'})
    assert auth.get_access_token() == 'renewed'
    assert auth.get_access_token() == 'renewed'
    assert len(calls) == 1
    assert calls[0]['client_id'] == 'oaiapp_test'
    assert 'scope' not in calls[0]
    account = auth._read('account.json')
    assert account['refresh_token'] == 'new-refresh'
    assert account['selected_model'] == 'account-model'
    assert account['expires_at'] > time.time()


def test_status_never_returns_credentials(local_app, monkeypatch):
    from app import subscription_proxy
    monkeypatch.setattr(subscription_proxy, 'get_proxy_usage', lambda: {'requests_used': 0, 'max_requests': 100})
    auth._write('account.json', {'access_token': 'private-token', 'refresh_token': 'private-refresh',
                               'expires_at': time.time()+3600, 'scopes': [auth.PLAN_SCOPE]})
    response = local_app.test_client().get('/api/chatgpt/status', base_url='http://127.0.0.1:5001')
    assert response.json['connected']
    assert response.json['plan_enabled']
    assert b'private-token' not in response.data
    assert b'private-refresh' not in response.data


def test_sign_out_preserves_registration_and_subsequent_sign_in_reuses_client(local_app):
    auth._write('account.json', {'client_id': 'oaiapp_existing', 'subject': 'user1', 'access_token': 'private'})
    auth._write('registration.json', {'client_id': 'oaiapp_existing', 'subject': 'user1'})
    client = local_app.test_client()
    assert client.post('/api/chatgpt/logout', base_url='http://127.0.0.1:5001').status_code == 200
    assert not auth._read('account.json')
    params = begin(client)
    assert params['client_id'] == ['oaiapp_existing']
    assert 'id_token_hint' not in params
    assert 'agent_name_hint' not in params


def test_stale_callback_does_not_consume_new_browser_transaction(local_app, monkeypatch):
    client = local_app.test_client()
    first, second = begin(client), begin(client)
    tokens = token_fixture(monkeypatch, second['nonce'][0])
    monkeypatch.setattr(auth, '_token_request', lambda _: tokens)
    base = 'http://127.0.0.1:5001'
    stale = client.get('/auth/callback', query_string={'state': first['state'][0], 'code': 'old', 'client_id': 'oaiapp_test'}, base_url=base)
    assert stale.status_code == 400
    fresh = client.get('/auth/callback', query_string={'state': second['state'][0], 'code': 'new', 'client_id': 'oaiapp_test'}, base_url=base)
    assert fresh.status_code == 200


def test_temporary_auth_outage_preserves_connection_and_retries_later(local_app, monkeypatch):
    account = {'client_id': 'oaiapp_test', 'subject': 'user1', 'access_token': 'old',
               'refresh_token': 'still-valid', 'scopes': [auth.PLAN_SCOPE], 'expires_at': 0}
    auth._write('account.json', account)
    monkeypatch.setattr(auth.httpx, 'post', lambda *_, **__: auth.httpx.Response(503, json={'error': 'temporary'}))
    with pytest.raises(auth.SubscriptionAuthError) as captured:
        auth.get_access_token()
    assert captured.value.status_code == 503
    assert auth._read('account.json') == account


def test_revoked_refresh_token_stops_further_refresh_attempts(local_app, monkeypatch):
    auth._write('account.json', {'client_id': 'oaiapp_test', 'subject': 'user1', 'access_token': 'old',
                                'refresh_token': 'revoked', 'scopes': [auth.PLAN_SCOPE], 'expires_at': 0})
    calls = []
    monkeypatch.setattr(auth.httpx, 'post', lambda *_, **__: calls.append(1) or auth.httpx.Response(400, json={'error': 'invalid_grant'}))
    for _ in range(2):
        with pytest.raises(auth.SubscriptionAuthError):
            auth.get_access_token()
    assert calls == [1]
    assert auth._read('account.json')['client_id'] == 'oaiapp_test'
    assert not auth._read('account.json')['refresh_token']


def test_local_workspace_guards_existing_apis_against_foreign_websites(local_app):
    from app import create_app
    class LocalConfig(auth.Config):
        MEMORY_BACKEND = 'local'
    client = create_app(LocalConfig).test_client()
    response = client.get('/api/graph/project/list', base_url='http://127.0.0.1:5001', headers={'Origin': 'https://foreign.example'})
    assert response.status_code == 403
    assert 'Access-Control-Allow-Origin' not in response.headers
    assert client.get('/health', base_url='http://127.0.0.1:5001').status_code == 200


def test_connection_test_rejects_an_empty_completed_reply(local_app, monkeypatch):
    monkeypatch.setattr(auth, '_adapter_configured', lambda: True)
    monkeypatch.setattr(auth, 'get_selected_model', lambda: 'model-test')
    monkeypatch.setattr(auth.httpx, 'post', lambda *_, **__: auth.httpx.Response(
        200, json={'choices': [{'message': {'content': ''}}]}))
    response = local_app.test_client().post('/api/chatgpt/test', base_url='http://127.0.0.1:5001', json={})
    assert response.status_code == 502
    assert response.json['success'] is False
    assert response.json['code'] == 'empty_model_response'
