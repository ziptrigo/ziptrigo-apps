"""Every link to the Credits page goes through `credits_url` (issue #347): "Top up" must open the
History tab, where the promo code form is, and a bare `billing:credits-history` would land on the chart.

The scan covers non-test code under `apps/`, `admin/` and `config/`, for the URL name and for the
hard-coded path `/billing/credits/`.
"""

import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
ROOTS = ('apps', 'admin', 'config')
SUFFIXES = {'.py', '.html', '.txt', '.js'}
ALLOWED = {
    # The route that defines the URL name, the page's view (its docstring names the address), the
    # helper that builds every link, and the usage view, which links its own tab by that name.
    'apps/billing/urls.py',
    'apps/billing/services/links.py',
    'apps/billing/views/credits.py',
    'apps/billing/views/usage.py',
}
FORBIDDEN = re.compile(r'credits-history|/billing/credits/')


def _sources():
    for root in ROOTS:
        for path in (REPO / root).rglob('*'):
            relative = path.relative_to(REPO).as_posix()
            if path.suffix not in SUFFIXES or '/tests/' in f'/{relative}' or relative in ALLOWED:
                continue
            if '/node_modules/' in relative or '/.venv/' in relative:
                continue
            yield relative, path.read_text()


def test_nothing_reverses_or_hard_codes_the_credits_page():
    offenders = [name for name, text in _sources() if FORBIDDEN.search(text)]

    assert offenders == [], (
        f'Use apps.billing.services.credits_url / {{% credits_url %}}: {offenders}'
    )


def test_the_guard_sees_the_directories_it_claims_to_scan():
    names = {name for name, _text in _sources()}

    assert any(name.startswith('admin/') for name in names)
    assert any(name.startswith('config/') for name in names)
    assert any(name.startswith('apps/billing/') for name in names)
