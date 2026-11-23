"""Every link to the Credits page goes through `credits_url` (issue #347): "Top up" must open the
History tab, where the promo code form is, and a bare `billing:credits-history` would land on the chart."""

import re
from pathlib import Path

APPS = Path(__file__).resolve().parents[2]
ALLOWED = {
    # The page itself, its URL and the helper.
    'billing/urls.py',
    'billing/services/links.py',
    'billing/views/credits.py',
    'billing/views/usage.py',
}


def _sources():
    for path in APPS.rglob('*'):
        relative = path.relative_to(APPS).as_posix()
        if path.suffix not in {'.py', '.html', '.txt'} or '/tests/' in f'/{relative}':
            continue
        if relative in ALLOWED:
            continue
        yield relative, path.read_text()


def test_nothing_reverses_the_credits_page_by_hand():
    offenders = [name for name, text in _sources() if re.search(r'credits-history', text)]

    assert offenders == [], (
        f'Use apps.billing.services.credits_url / {{% credits_url %}}: {offenders}'
    )
