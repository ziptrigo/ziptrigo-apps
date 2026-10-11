"""Template source checks that apply to every app."""

from pathlib import Path

import pytest
from django.conf import settings

pytestmark = pytest.mark.unit


def test_no_template_comment_spans_lines():
    """`{# ... #}` is single-line only: a multi-line one is rendered into the page as text.

    Use `{% comment %}...{% endcomment %}` for anything longer than one line.
    """
    offenders = [
        f'{path.relative_to(settings.PROJECT_ROOT)}:{lineno}'
        for path in sorted(Path(settings.PROJECT_ROOT, 'apps').glob('*/templates/**/*.html'))
        for lineno, line in enumerate(path.read_text().splitlines(), start=1)
        if '{#' in line and '#}' not in line[line.index('{#') :]
    ]

    assert not offenders, offenders
