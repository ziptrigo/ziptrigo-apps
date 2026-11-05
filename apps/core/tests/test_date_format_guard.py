"""Neither a template nor Python code can bring back a hard-coded date or time format (issue #245).

Every date and time a page shows goes through `apps.core.datetime_format` (`|fdate`, `|ftime`,
`|fdatetime`, `{% local_time %}`) so it follows the viewer's choice. Django's `|date` and `|time`
are for machine values only: the ISO `Y-m-d` / `H:i` that go into an input's `value`, an
attribute, or a date or time field include. Anything else fails here.

The second check does the same for non-test Python: a `strftime` / f-string format that writes a
month or weekday name, a 12-hour clock, a locale-dependent directive or an hours-and-minutes time
fails unless the (file, format) pair is in `PYTHON_ALLOW_LIST` with a reason.
"""

import ast
import re
from pathlib import Path

import pytest

APPS = Path(__file__).resolve().parents[2]
TEMPLATE_SUFFIXES = {'.html', '.txt'}

#: `|date` / `|time` with their format argument (or none).
FILTER = re.compile(r"\|(date|time)(?![A-Za-z_])(?::(['\"])(.*?)\2)?")

#: The only formats a machine value uses.
ISO_FORMATS = {'date': 'Y-m-d', 'time': 'H:i'}

#: A line that feeds a machine value to an input or a field component.
VALUE_CONTEXTS = (
    'core/components/date_picker.html',
    'core/components/time_field.html',
    ' value="',
    ' min="',
    ' max="',
    ' data-',
)

#: (path relative to `apps/`, line text) pairs that are allowed anyway. Empty on purpose: add to it
#: only for a machine value the contexts above don't recognise, with a reason.
ALLOW_LIST: set[tuple[str, str]] = set()


def violations_in(text: str) -> list[str]:
    """The lines of a template that format a date or time for display with `|date` / `|time`."""
    found = []
    for number, line in enumerate(text.splitlines(), start=1):
        for match in FILTER.finditer(line):
            kind, _quote, argument = match.groups()
            iso = argument == ISO_FORMATS[kind]
            if iso and any(context in line for context in VALUE_CONTEXTS):
                continue
            found.append(f'line {number}: {line.strip()}')
    return found


def templates():
    for path in sorted(APPS.rglob('*')):
        if path.suffix not in TEMPLATE_SUFFIXES or 'templates' not in path.parts:
            continue
        yield path


def test_no_template_formats_a_date_or_time_with_the_django_filters():
    problems = []
    for path in templates():
        relative = str(path.relative_to(APPS))
        for problem in violations_in(path.read_text()):
            if (relative, problem.split(': ', 1)[1]) not in ALLOW_LIST:
                problems.append(f'{relative} {problem}')
    assert not problems, (
        'Use |fdate, |ftime, |fdatetime or {% local_time %} (apps.core.datetime_format) for a '
        'date or time a person reads:\n' + '\n'.join(problems)
    )


@pytest.mark.parametrize(
    'source',
    [
        "{{ task.created_at|date:'M j' }}",
        "{{ task.due_date|date:'Y-m-d' }}",  # ISO, but shown to a person
        "{{ task.due_time|time:'H:i' }}",
        '{{ task.due_date|date }}',
        '{{ moment|time }}',
        '{{ moment|date:"j M Y, H:i" }}',
    ],
)
def test_the_guard_catches_a_display_format(source):
    assert violations_in(source)


@pytest.mark.parametrize(
    'source',
    [
        """<input value="{{ task.due_date|date:'Y-m-d' }}">""",
        """{% include 'core/components/date_picker.html' with value=d|date:'Y-m-d' %}""",
        """{% include 'core/components/time_field.html' with value=t|time:'H:i' %}""",
        """<div data-day="{{ day|date:'Y-m-d' }}">""",
        '{{ task.due_date|fdate }}',
    ],
)
def test_the_guard_allows_machine_values_and_the_site_filters(source):
    assert not violations_in(source)


def test_the_guard_found_templates():
    assert len(list(templates())) > 50


# -- Python code ---------------------------------------------------------------------------------

#: A directive or shape that writes a date or time for a person: month or weekday names (`%b %B
#: %a %A`), the 12-hour clock (`%I %p`), the locale's own forms (`%c %x %X`), a clock time
#: (`%H:%M`), a numeric day-month date (`%d/%m`, `%m/%d`).
PYTHON_DISPLAY = re.compile(r'%[bBaAIpcxX]|%H:%M|%d[/.]%m|%m[/.]%d')

#: Files that are not display code: the formatting module itself (tests and migrations are
#: skipped by `python_files`).
PYTHON_SKIPPED = {'apps/core/datetime_format.py'}

#: (path relative to the repo, format text) -> why this one may stay. Machine values (ISO text
#: that goes into an input, a JSON field, a model prompt), the admin and `__str__` only.
PYTHON_ALLOW_LIST: dict[tuple[str, str], str] = {
    ('apps/core/admin_site.py', '%Y-%m-%d %H:%M:%S %Z'): 'admin tools page: staff, always ISO',
    ('apps/core/forms/widgets.py', '%H:%M'): "the time field widget's hidden HH:MM value",
    ('apps/todo/api/parse.py', '%H:%M'): "the parse endpoint's machine `due_time` (ISO HH:MM)",
    ('apps/todo/views/parse.py', '%H:%M'): "the parse endpoint's machine `due_time` (ISO HH:MM)",
    ('apps/todo/services/parsing.py', '%H:%M'): 'the AI prompt: times as 24-hour machine text',
    ('apps/short_url/models/click.py', '%Y-%m-%d %H:%M'): '`__str__` of a model (admin/debug)',
    ('apps/short_url/views/form.py', '%Y-%m-%dT%H:%MZ'): 'ISO UTC value for the date/time pair',
    ('apps/billing/admin.py', '%Y-%m-%d %H:%M UTC'): 'admin ledger line, always ISO UTC',
}


def python_files():
    root = APPS.parent
    for path in sorted(APPS.rglob('*.py')):
        relative = path.relative_to(root)
        if 'tests' in relative.parts or 'migrations' in relative.parts:
            continue
        if path.name.startswith('test_') or path.name == 'conftest.py':
            continue
        if str(relative) in PYTHON_SKIPPED:
            continue
        yield path


def display_formats_in(source: str) -> list[tuple[int, str]]:
    """`(line, text)` of every string literal that holds a display date/time directive.
    Docstrings and bare string statements are documentation, not formats."""
    tree = ast.parse(source)
    documentation = {
        id(node.value)
        for node in ast.walk(tree)
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant)
    }
    found = []
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and id(node) not in documentation
            and PYTHON_DISPLAY.search(node.value)
        ):
            found.append((node.lineno, node.value))
    return found


def _python_findings() -> set[tuple[str, str]]:
    root = APPS.parent
    return {
        (str(path.relative_to(root)), text)
        for path in python_files()
        for _line, text in display_formats_in(path.read_text())
    }


def test_no_python_code_formats_a_date_or_time_for_display():
    root = APPS.parent
    problems = []
    for path in python_files():
        relative = str(path.relative_to(root))
        for line, text in display_formats_in(path.read_text()):
            if (relative, text) not in PYTHON_ALLOW_LIST:
                problems.append(f'{relative}:{line} {text!r}')
    assert not problems, (
        'Use apps.core.datetime_format (format_date, format_time, format_datetime) for a date or '
        'time a person reads, or allow-list a machine value in PYTHON_ALLOW_LIST with a reason:\n'
        + '\n'.join(problems)
    )


def test_the_python_allow_list_has_no_stale_entries():
    assert set(PYTHON_ALLOW_LIST) <= _python_findings()


@pytest.mark.parametrize(
    'source',
    [
        "x.strftime('%b %d, %Y')",
        "x.strftime('%A %I:%M %p')",
        "f'{moment:%H:%M}'",
        "x.strftime('%d/%m/%Y')",
        "x.strftime('%c')",
    ],
)
def test_the_python_guard_catches_a_display_format(source):
    assert display_formats_in(source)


@pytest.mark.parametrize(
    'source',
    [
        "x.strftime('%Y-%m-%d')",
        "x.strftime('%Y-%m')",
        '"""Docstring about %b and %H:%M."""',
        'def f():\n    """%b in a docstring"""\n    return 1',
    ],
)
def test_the_python_guard_allows_machine_values_and_documentation(source):
    assert not display_formats_in(source)


def test_the_python_guard_found_files():
    assert len(list(python_files())) > 200
