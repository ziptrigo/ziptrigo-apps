"""A template can't bring back a hard-coded date or time format (issue #245).

Every date and time a page shows goes through `apps.core.datetime_format` (`|fdate`, `|ftime`,
`|fdatetime`, `{% local_time %}`) so it follows the viewer's choice. Django's `|date` and `|time`
are for machine values only: the ISO `Y-m-d` / `H:i` that go into an input's `value`, an
attribute, or a date or time field include. Anything else fails here.
"""

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
