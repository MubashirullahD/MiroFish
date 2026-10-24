"""Prepare a local-only subscription configuration without overwriting settings."""
from pathlib import Path
import os
import secrets

ROOT = Path(__file__).resolve().parents[1]
target = ROOT / '.env'
if target.exists():
    print('Existing .env preserved. See LOCAL_SETUP.md for local subscription settings.')
else:
    values = {
        'FLASK_HOST': '127.0.0.1', 'FLASK_PORT': '5001', 'FLASK_DEBUG': 'false',
        'SECRET_KEY': secrets.token_urlsafe(48),
        'LLM_API_KEY': secrets.token_urlsafe(48),
        'LLM_BASE_URL': 'http://127.0.0.1:5001/api/chatgpt/v1',
        'LLM_MODEL_NAME': 'chatgpt-plan', 'MEMORY_BACKEND': 'local',
        'CHATGPT_MAX_REQUESTS': '100', 'CHATGPT_MAX_CONCURRENT': '2',
        'OASIS_DEFAULT_MAX_ROUNDS': '3', 'DEFAULT_LOCALE': 'en',
        'TIKTOKEN_CACHE_DIR': str(ROOT / '.local' / 'tiktoken-cache'),
    }
    descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, 'w', encoding='utf-8') as handle:
        handle.write('# Local MiroFish: keys below authenticate only the loopback adapter.\n')
        handle.writelines(f'{key}={value}\n' for key, value in values.items())
    print('Created local subscription configuration. Credentials are excluded from Git.')
