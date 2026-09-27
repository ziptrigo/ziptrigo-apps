from pathlib import Path

ADMIN_DIR = Path(__file__).parent
PROJECT_ROOT = Path(__file__).parents[1]

APP_NAME = 'ziptrigo-apps'  # PROJECT_ROOT.name and same as in `pyproject.toml` > project > name

PROJECT_NAME = APP_NAME.replace('-', '_')
