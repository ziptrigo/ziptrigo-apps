"""`#project` and `@label` in a new task's title.

Typing `Call Bob #Website @home` files the task in the project "Website" and gives it the label
"home", and the title becomes `Call Bob`. The rules:

- A tag starts a word: the start of the title or after white space (`bob@example.com` is no label).
  It runs to the next white space, without trailing punctuation (`#Website.` is `Website`).
- It names an existing project or label of the user's, compared ignoring case and spaces, `-` and
  `_` (`#website-relaunch` and `#WebsiteRelaunch` are "Website relaunch"). Nothing is ever
  created: a tag that names nothing (or an archived project, or two projects alike) stays in the
  title as typed.
- `#` followed by digits only is a task reference (`#1234`), never a project.
- The first `#` that names a project wins; every `@` that names a label is added.
- A tag that was used is taken out of the title; a title that would then be empty is kept whole.

Applied when a task is created from the quick-add box, `POST /api/todo/tasks` and an emailed
subject. A project chosen explicitly (the form's Project select, the API's `project`) wins, and
`#` is then left alone; a subtask is in its parent's project, so only its `@` labels are read.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from apps.todo.models import Label, Project

if TYPE_CHECKING:
    from apps.accounts.models import User

#: A tag: `#` or `@` at the start or after white space, then everything up to the next white space.
_TAG = re.compile(r'(?:(?<=\s)|^)([#@])(\S+)')
#: Punctuation that ends a sentence or a bracket, not part of the name.
_TRAILING = '.,;:!?)]}"\''


@dataclass(frozen=True)
class TitleTags:
    """What a title's tags name: the title without them, the project and the labels."""

    title: str
    project: Project | None = None
    labels: list[Label] = field(default_factory=list)


def _key(name: str) -> str:
    return re.sub(r'[\s_-]+', '', name).casefold()


def _match(candidates: dict[str, list], token: str):
    """The one candidate the token names, preferring an exact name (case ignored) over a looser
    match; `None` for none or two alike."""
    found = candidates.get(_key(token), [])
    if len(found) == 1:
        return found[0]
    exact = [item for item in found if item.name.casefold() == token.casefold()]
    return exact[0] if len(exact) == 1 else None


def _index(items) -> dict[str, list]:
    index: dict[str, list] = {}
    for item in items:
        index.setdefault(_key(item.name), []).append(item)
    return index


def read(user: User, title: str, *, projects: bool = True, labels: bool = True) -> TitleTags:
    """The tags of `title` that name the user's projects (`projects`) and labels (`labels`). Two
    queries at most, none for a title without a tag."""
    tags = [m for m in _TAG.finditer(title)]
    if not tags:
        return TitleTags(title=title)
    wants_project = projects and any(m.group(1) == '#' for m in tags)
    wants_labels = labels and any(m.group(1) == '@' for m in tags)
    project_index = (
        _index(Project.objects.filter(user=user, archived_at__isnull=True)) if wants_project else {}
    )
    label_index = _index(Label.objects.filter(user=user)) if wants_labels else {}

    project: Project | None = None
    found_labels: list[Label] = []
    used: list[tuple[int, int]] = []
    for m in tags:
        sign, raw = m.group(1), m.group(2)
        name = raw.rstrip(_TRAILING)
        if not name:
            continue
        if sign == '#':
            if project is not None or not wants_project or name.isdigit():
                continue
            if (hit := _match(project_index, name)) is not None:
                project = hit
                used.append((m.start(), m.start(2) + len(name)))
        elif wants_labels and (hit := _match(label_index, name)) is not None:
            if hit not in found_labels:
                found_labels.append(hit)
            used.append((m.start(), m.start(2) + len(name)))
    if not used:
        return TitleTags(title=title)
    return TitleTags(title=_without(title, used), project=project, labels=found_labels)


def _without(title: str, spans: list[tuple[int, int]]) -> str:
    """`title` with the spans taken out and its white space tidied; the title itself when nothing
    would be left."""
    parts, last = [], 0
    for start, end in spans:
        parts.append(title[last:start])
        last = end
    parts.append(title[last:])
    text = ' '.join(''.join(parts).split())
    text = re.sub(r'\s+([.,;:!?])', r'\1', text)
    return text or title


def keep_in(original: str, cleaned: str) -> str:
    """`cleaned` (a title the date reader shortened) with any tag of `original` it dropped put back
    at the end, so accepting a date never loses `#project` or `@label`."""
    present = {m.group(0) for m in _TAG.finditer(cleaned)}
    missing = [m.group(0) for m in _TAG.finditer(original) if m.group(0) not in present]
    return ' '.join([cleaned, *missing]) if missing else cleaned
