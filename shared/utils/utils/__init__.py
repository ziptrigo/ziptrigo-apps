from pathlib import Path

PROJECT_ROOT = Path(__file__).parents[1]
PROJECT_NAME = PROJECT_ROOT.name.replace('-', '_')
SOURCE_DIR = PROJECT_ROOT / 'src' / PROJECT_NAME

import os
from pathlib import Path

# Provide a root path similar to what was in common/admin
PROJECT_ROOT = Path(__file__).resolve().parent
