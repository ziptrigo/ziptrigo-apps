from enum import StrEnum

from . import PROJECT_ROOT

APPS_DIR = PROJECT_ROOT / 'apps'

# Django apps in this project, discovered from `apps/` so adding an app needs no change here. The
# member value is the app's directory (and package) name under `apps/`, e.g. `qr_code`.
DjangoApp = StrEnum(
    'DjangoApp',
    {
        path.name.upper(): path.name
        for path in sorted(APPS_DIR.iterdir())
        if (path / 'apps.py').is_file()
    },
)
