import sys
from pathlib import Path

# Provide a root path
PROJECT_ROOT = Path(__file__).resolve().parent.parent

# The shared packages are not installed into the environment during local development
# (`uv sync --no-install-project`), so make them importable the same way `config/settings.py`
# does in each service.
for _shared_package in ('utils', 'auth_client'):
    _path = str(PROJECT_ROOT / 'shared' / _shared_package)
    if _path not in sys.path:
        sys.path.insert(0, _path)
