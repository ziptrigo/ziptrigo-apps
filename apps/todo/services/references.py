"""Task references in Markdown: which `#1234` and task links go where.

`apps.core.markdown` finds the numbers a text mentions and asks a resolver for their URLs; this is
the To-do app's: only the user's own tasks that are not in the Trash link anywhere (anything else
stays text). The comments sub-issue renders through the same resolver.
"""

import re

from django.urls import reverse

from apps.accounts.models import User
from apps.core.markdown import ReferenceResolver

from ..models import Task

#: What a task page's path looks like (`todo:task`), for links written as URLs.
TASK_URL_PATH = re.compile(r'^/todo/(?P<number>[0-9]{1,9})/?$')


def resolver_for(user: User) -> ReferenceResolver:
    """A resolver mapping task numbers to `reverse('todo:task', args=[number])` for the user's
    non-trashed tasks among them (one query per call)."""

    def resolve(numbers: list[int]) -> dict[int, str]:
        found = Task.objects.filter(
            user=user, number__in=numbers, deleted_at__isnull=True
        ).values_list('number', flat=True)
        return {number: reverse('todo:task', args=[number]) for number in found}

    return resolve
