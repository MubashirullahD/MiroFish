"""Local ChatGPT plan authorization using the documented public OSS flow.

Credentials belong to this installation. They never enter the frontend or reuse
Codex's credential cache. See developers.openai.com/siwc/token-sharing-open-source.
"""
from __future__ import annotations

import base64
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import secrets
import tempfile
import threading
import time
from urllib.parse import urlencode, urlsplit
import uuid

import httpx
import jwt
from flask import Blueprint, jsonify, make_response, redirect, request

from .config import Config

ISSUER = 'https://auth.openai.com'
AUTHORIZE_URL = ISSUER + '/api/accounts/authorize'
TOKEN_URL = ISSUER + '/api/accounts/oauth/token'
RESOURCE = 'https://api.openai.com/v1'
SCOPES = 'openid profile email offline_access resource.invoke chatgpt.tokens.use.direct'
PLAN_SCOPE = 'chatgpt.tokens.use.direct'
USAGE_URL = 'https://chatgpt.com/#settings/Usage'
chatgpt_auth_bp = Blueprint('chatgpt_auth', __name__)
_lock = threading.RLock()
_pending: dict[str, dict] = {}
_jwks = jwt.PyJWKClient(ISSUER + '/.well-known/jwks.json', timeout=20)


class SubscriptionAuthError(RuntimeError):
    def __init__(self, message: str, code: str = 'chatgpt_auth_required', status_code: int = 401):
        super().__init__(message)
        self.code = code
        self.status_code = status_code


def _directory() -> Path:
    directory = Path(Config.CHATGPT_AUTH_DIR)
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    directory.chmod(0o700)
    return directory


def _read(name: str) -> dict:
    path = _directory() / name
    try:
        value = json.loads(path.read_text(encoding='utf-8'))
        if not isinstance(value, dict):
            raise ValueError
        return value
    except FileNotFoundError:
        return {}
    except (ValueError, OSError) as exc:
        raise SubscriptionAuthError('Local ChatGPT settings could not be read. Reconnect your account.') from exc


def _write(name: str, data: dict) -> None:
    directory = _directory()
    fd, temporary = tempfile.mkstemp(prefix='.account-', dir=directory)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as handle:
            json.dump(data, handle)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, directory / name)
    finally:
        Path(temporary).unlink(missing_ok=True)


def _host_id() -> str:
    host = _read('host.json')
    if not host.get('ext_agent_host_id'):
        host = {'ext_agent_host_id': 'urn:uuid:' + str(uuid.uuid4())}
        _write('host.json', host)
    return host['ext_agent_host_id']


def require_local_request() -> None:
    """Block non-loopback callers, DNS rebinding and foreign web origins."""
    try:
        if not ipaddress.ip_address(request.remote_addr or '').is_loopback:
            raise ValueError
    except ValueError:
        raise SubscriptionAuthError('This connection is available only on this computer.', 'local_only', 403)
    if urlsplit(request.host_url).hostname not in {'127.0.0.1', 'localhost', '::1'}:
        raise SubscriptionAuthError('Use the local MiroFish address.', 'local_only', 403)
    origin = request.headers.get('Origin')
    allowed = {'http://127.0.0.1:3000', 'http://localhost:3000',
               f'http://127.0.0.1:{os.environ.get("FLASK_PORT", "5001")}',
               f'http://localhost:{os.environ.get("FLASK_PORT", "5001")}'}
    if origin and origin not in allowed:
        raise SubscriptionAuthError('Open MiroFish locally to manage this connection.', 'invalid_origin', 403)


@chatgpt_auth_bp.before_request
def _local_guard():
    require_local_request()


@chatgpt_auth_bp.errorhandler(SubscriptionAuthError)
def _auth_error(error):
    return jsonify(success=False, error=str(error), code=error.code, manage_usage_url=USAGE_URL), error.status_code


def _token_request(data: dict) -> dict:
    try:
        response = httpx.post(TOKEN_URL, data=data, timeout=30, follow_redirects=False, trust_env=False)
        if response.status_code != 200:
            try:
                failure = response.json()
                code = failure.get('error') if isinstance(failure, dict) else None
            except ValueError:
                code = None
            if code == 'invalid_grant':
                raise SubscriptionAuthError('The ChatGPT authorization expired or was revoked. Please reconnect.', 'oauth_invalid_grant', 401)
            if response.status_code in {429, 500, 502, 503, 504}:
                raise SubscriptionAuthError('ChatGPT authorization is temporarily unavailable. Try again later.', 'authorization_unavailable', 503)
            raise SubscriptionAuthError('ChatGPT authorization could not be completed. Please reconnect.', 'authorization_failed', 401)
        result = response.json()
        if not isinstance(result, dict):
            raise ValueError
        return result
    except (httpx.HTTPError, ValueError) as exc:
        raise SubscriptionAuthError('Could not reach ChatGPT authorization. Try again.', 'authorization_unavailable', 503) from exc


def _verify_identity(encoded: str, client_id: str, nonce: str | None = None) -> dict:
    try:
        signing_key = _jwks.get_signing_key_from_jwt(encoded)
        claims = jwt.decode(encoded, signing_key.key, algorithms=['RS256'],
                            audience=client_id, issuer=ISSUER, leeway=5,
                            options={'require': ['sub', 'exp', 'iat']})
        if not isinstance(claims.get('sub'), str) or not claims['sub']:
            raise ValueError
        if nonce is not None and not secrets.compare_digest(str(claims.get('nonce', '')), nonce):
            raise ValueError
        return claims
    except (jwt.PyJWTError, ValueError, OSError) as exc:
        raise SubscriptionAuthError('ChatGPT identity could not be verified. Please reconnect.', 'invalid_identity', 401) from exc


def _new_credentials(tokens: dict, client_id: str, identity: dict, previous: dict | None = None) -> dict:
    previous = previous or {}
    scopes = tokens.get('scope', ' '.join(previous.get('scopes', []))).split()
    access_token = tokens.get('access_token')
    refresh_token = tokens.get('refresh_token', previous.get('refresh_token'))
    try:
        lifetime = int(tokens.get('expires_in', 0))
    except (TypeError, ValueError):
        lifetime = 0
    if not isinstance(access_token, str) or not access_token or lifetime <= 0:
        raise SubscriptionAuthError('ChatGPT did not return usable plan credentials. Reconnect.', 'missing_credentials', 401)
    if tokens.get('token_type', '').lower() != 'bearer':
        raise SubscriptionAuthError('ChatGPT returned an unsupported credential type.', 'invalid_credentials', 401)
    if PLAN_SCOPE not in scopes:
        raise SubscriptionAuthError('ChatGPT plan usage was not enabled. Reconnect and allow plan usage.', 'plan_permission_required', 403)
    return {
        'client_id': client_id, 'subject': identity['sub'], 'email': identity.get('email'),
        'access_token': access_token, 'refresh_token': refresh_token,
        'id_token': tokens.get('id_token', previous.get('id_token')),
        'scopes': scopes, 'expires_at': time.time() + lifetime,
        'session_id': previous.get('session_id') or secrets.token_urlsafe(24),
        'selected_model': previous.get('selected_model') if previous.get('subject') == identity['sub'] and previous.get('client_id') == client_id else None,
    }


def get_access_token() -> str:
    """Return an app-owned plan token, refreshing rotating credentials atomically."""
    with _lock:
        account = _read('account.json')
        if not account.get('access_token') or PLAN_SCOPE not in account.get('scopes', []):
            raise SubscriptionAuthError('Connect your ChatGPT account in MiroFish first.')
        if account.get('expires_at', 0) > time.time() + 60:
            return account['access_token']
        if not account.get('refresh_token'):
            raise SubscriptionAuthError('Your ChatGPT connection expired. Please reconnect.')
        try:
            tokens = _token_request({'grant_type': 'refresh_token', 'client_id': account['client_id'],
                                     'refresh_token': account['refresh_token'], 'resource': RESOURCE})
        except SubscriptionAuthError as exc:
            if exc.code == 'oauth_invalid_grant':
                account.update(access_token=None, refresh_token=None, expires_at=0)
                _write('account.json', account)
            raise
        identity = {'sub': account['subject'], 'email': account.get('email')}
        if tokens.get('id_token'):
            identity = _verify_identity(tokens['id_token'], account['client_id'])
            if identity['sub'] != account['subject']:
                raise SubscriptionAuthError('The refreshed ChatGPT account did not match. Reconnect.', 'account_mismatch', 401)
        renewed = _new_credentials(tokens, account['client_id'], identity, account)
        _write('account.json', renewed)
        return renewed['access_token']


def get_selected_model(requested_model: str | None = None) -> str:
    if requested_model and requested_model != 'chatgpt-plan':
        return requested_model
    selected = _read('account.json').get('selected_model')
    if not selected:
        raise SubscriptionAuthError('Choose a model from your ChatGPT account in MiroFish first.', 'model_not_selected', 400)
    return selected


def get_account_cache_scope() -> str:
    """Opaque cache partition, stable through refresh and changed on new login."""
    account = _read('account.json')
    if not account.get('access_token') or not account.get('subject') or not account.get('client_id'):
        raise SubscriptionAuthError('Connect your ChatGPT account in MiroFish first.')
    identity = '\0'.join(str(account.get(key, '')) for key in ('client_id', 'subject', 'session_id'))
    return hashlib.sha256(identity.encode()).hexdigest()


def _adapter_configured() -> bool:
    address = urlsplit(Config.LLM_BASE_URL)
    return (Config.LLM_MODEL_NAME == 'chatgpt-plan' and address.scheme == 'http'
            and address.hostname in {'127.0.0.1', 'localhost', '::1'}
            and address.path.rstrip('/') == '/api/chatgpt/v1')


def list_account_models() -> list[dict]:
    token = get_access_token()
    try:
        response = httpx.get(RESOURCE + '/models', headers={'Authorization': 'Bearer ' + token}, timeout=30,
                             follow_redirects=False, trust_env=False)
        if response.status_code != 200:
            raise SubscriptionAuthError('Your account model catalog is unavailable. Check your ChatGPT connection.', 'models_unavailable', response.status_code if response.status_code in {401, 403, 429} else 503)
        data = response.json()
        if not isinstance(data, dict):
            raise ValueError
        models = data.get('models', data.get('data', []))
        if not isinstance(models, list):
            raise ValueError
        return [{'id': item.get('slug', item.get('id')), 'display_name': item.get('display_name', item.get('slug', item.get('id')))}
                for item in models if isinstance(item, dict) and item.get('visibility', 'list') == 'list'
                and isinstance(item.get('slug', item.get('id')), str)]
    except (httpx.HTTPError, ValueError, TypeError) as exc:
        raise SubscriptionAuthError('Could not load your ChatGPT model catalog. Try again.', 'models_unavailable', 503) from exc


@chatgpt_auth_bp.get('/api/chatgpt/login')
def login():
    with _lock:
        previous = (_read('account.json') or _read('registration.json')) if request.args.get('new') != '1' else {}
        verifier = secrets.token_urlsafe(64)
        state, nonce, browser = (secrets.token_urlsafe(32) for _ in range(3))
        callback = f'http://127.0.0.1:{int(os.environ.get("FLASK_PORT", "5001"))}/auth/callback'
        for key, transaction in list(_pending.items()):
            if transaction['expires_at'] < time.time():
                del _pending[key]
        if len(_pending) >= 10:
            raise SubscriptionAuthError('A sign-in is already pending. Finish it or try again shortly.', 'login_pending', 429)
        _pending[browser] = {'state': state, 'nonce': nonce, 'verifier': verifier,
                             'callback': callback, 'expires_at': time.time() + 600,
                             'client_id': previous.get('client_id'), 'subject': previous.get('subject')}
        params = {'client_id': previous.get('client_id') or 'dynamic_agent_client',
                  'ext_agent_host_id': _host_id(), 'response_type': 'code', 'redirect_uri': callback,
                  'scope': SCOPES, 'resource': RESOURCE, 'state': state, 'nonce': nonce,
                  'code_challenge_method': 'S256',
                  'code_challenge': base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip('=')}
        if previous.get('client_id'):
            if previous.get('id_token'):
                params['id_token_hint'] = previous['id_token']
        else:
            params['agent_name_hint'] = 'MiroFish Local'
        response = redirect(AUTHORIZE_URL + '?' + urlencode(params))
        response.set_cookie('mirofish_oauth', browser, max_age=600, httponly=True, samesite='Lax', path='/auth')
        response.headers['Cache-Control'] = 'no-store'
        response.headers['Referrer-Policy'] = 'no-referrer'
        return response


@chatgpt_auth_bp.get('/auth/callback')
def callback():
    with _lock:
        browser = request.cookies.get('mirofish_oauth', '')
        transaction = _pending.get(browser)
        if not transaction or transaction['expires_at'] <= time.time() or not secrets.compare_digest(
                transaction['state'], request.args.get('state', '')):
            raise SubscriptionAuthError('This sign-in expired or could not be verified. Start again in MiroFish.', 'invalid_state', 400)
        _pending.pop(browser)
        if request.args.get('error'):
            raise SubscriptionAuthError('ChatGPT sign-in was not completed. Return to MiroFish to reconnect.', 'authorization_declined', 400)
        code = request.args.get('code')
        supplied_id = request.args.get('client_id')
        client_id = transaction['client_id'] or supplied_id
        if not code or not client_id or client_id == 'dynamic_agent_client' or (
                transaction['client_id'] and supplied_id and supplied_id != client_id):
            raise SubscriptionAuthError('ChatGPT registration was incomplete. Please reconnect.', 'invalid_registration', 400)
        tokens = _token_request({'grant_type': 'authorization_code', 'client_id': client_id,
                                 'code': code, 'code_verifier': transaction['verifier'],
                                 'redirect_uri': transaction['callback'], 'resource': RESOURCE})
        identity = _verify_identity(tokens.get('id_token', ''), client_id, transaction['nonce'])
        if transaction['subject'] and transaction['subject'] != identity['sub']:
            raise SubscriptionAuthError('This connection belongs to a different account. Use Switch account in MiroFish.', 'account_mismatch', 401)
        # Retain the non-credential registration when local tokens are signed out.
        _write('registration.json', {'client_id': client_id, 'subject': identity['sub'], 'email': identity.get('email')})
        credentials = _new_credentials(tokens, client_id, identity, _read('account.json'))
        credentials['session_id'] = secrets.token_urlsafe(24)
        _write('account.json', credentials)
        from .subscription_proxy import clear_proxy_context
        clear_proxy_context()
    response = make_response('<!doctype html><html lang="en"><meta name="viewport" content="width=device-width">'
                             '<title>MiroFish connected</title><body style="font:18px system-ui;padding:48px;max-width:560px">'
                             '<h1>ChatGPT connected</h1><p>Return to MiroFish, choose a model and test the connection.</p>'
                             '<p>You can close this tab.</p></body></html>')
    response.delete_cookie('mirofish_oauth', path='/auth')
    response.headers['Cache-Control'] = 'no-store'
    response.headers['Referrer-Policy'] = 'no-referrer'
    return response


@chatgpt_auth_bp.get('/api/chatgpt/status')
def status():
    from .subscription_proxy import get_proxy_usage
    account = _read('account.json')
    connected = bool(account.get('access_token') and (account.get('expires_at', 0) > time.time() or account.get('refresh_token')))
    return jsonify(success=True, connected=connected, email=account.get('email'),
                   client_id=account.get('client_id'),
                   plan_enabled=connected and PLAN_SCOPE in account.get('scopes', []) and _adapter_configured(),
                   adapter_configured=_adapter_configured(),
                   model=account.get('selected_model'), memory_backend=Config.MEMORY_BACKEND,
                   usage=get_proxy_usage(), manage_usage_url=USAGE_URL)


@chatgpt_auth_bp.get('/api/chatgpt/models')
def models():
    return jsonify(success=True, models=list_account_models(), selected_model=_read('account.json').get('selected_model'))


@chatgpt_auth_bp.post('/api/chatgpt/model')
def select_model():
    model = (request.get_json(silent=True) or {}).get('model')
    if model not in {item['id'] for item in list_account_models()}:
        raise SubscriptionAuthError('Choose a model from your account catalog.', 'invalid_model', 400)
    with _lock:
        account = _read('account.json')
        account['selected_model'] = model
        _write('account.json', account)
    return jsonify(success=True, model=model)


@chatgpt_auth_bp.post('/api/chatgpt/logout')
def logout():
    with _lock:
        (_directory() / 'account.json').unlink(missing_ok=True)
        _pending.clear()
        from .subscription_proxy import clear_proxy_context
        clear_proxy_context()
    return jsonify(success=True, message='Local credentials removed. Disconnect MiroFish in ChatGPT settings to revoke access.', manage_usage_url=USAGE_URL)


@chatgpt_auth_bp.post('/api/chatgpt/usage/reset')
def reset_budget():
    from .subscription_proxy import reset_proxy_usage, SubscriptionProxyError
    try:
        reset_proxy_usage()
    except SubscriptionProxyError as exc:
        raise SubscriptionAuthError(str(exc), exc.code, exc.status) from exc
    return jsonify(success=True)


@chatgpt_auth_bp.post('/api/chatgpt/test')
def test_connection():
    if not _adapter_configured():
        raise SubscriptionAuthError('Enable the local subscription settings in .env before testing. See LOCAL_SETUP.md.', 'adapter_not_configured', 400)
    get_selected_model()
    try:
        response = httpx.post(f'http://127.0.0.1:{int(os.environ.get("FLASK_PORT", "5001"))}/api/chatgpt/v1/chat/completions',
                              headers={'Authorization': 'Bearer ' + Config.LLM_API_KEY},
                              json={'model': 'chatgpt-plan', 'messages': [{'role': 'user', 'content': 'Reply with exactly: MiroFish connected'}]},
                              timeout=250, trust_env=False, follow_redirects=False)
        if response.status_code != 200:
            data = response.json().get('error', {})
            raise SubscriptionAuthError(data.get('message', 'The connection test failed.'),
                                         data.get('code', 'test_failed'), response.status_code)
        message = response.json()['choices'][0]['message']['content']
        if not isinstance(message, str) or not message.strip():
            raise SubscriptionAuthError('ChatGPT completed without a text reply. The connection has not passed its test.', 'empty_model_response', 502)
        return jsonify(success=True, message=message)
    except (httpx.HTTPError, ValueError, KeyError, IndexError) as exc:
        raise SubscriptionAuthError('The model connection test did not complete. Check the local service and retry.', 'test_failed', 503) from exc
