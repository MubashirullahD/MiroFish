"""Install the isolated study browser inside this checkout."""

import os
from pathlib import Path
import subprocess

root = Path(__file__).resolve().parents[1]
subprocess.run(
    ['uv', 'run', 'playwright', 'install', 'chromium'], cwd=root / 'backend',
    env={**os.environ, 'PLAYWRIGHT_BROWSERS_PATH': str(root / '.local' / 'browsers')}, check=True,
)
