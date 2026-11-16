from django import forms

from ..limits import PROJECT_NAME_MAX


class ProjectForm(forms.Form):
    """Adding or editing a project. It only gathers the fields; the rules (name length and
    uniqueness, colour, description length, the cap) are `services.projects`' own."""

    name = forms.CharField(
        label='Name',
        max_length=PROJECT_NAME_MAX,
        error_messages={'required': 'Name is required.'},
    )
    color = forms.CharField(label='Colour', required=False, max_length=7)
    # No `max_length`: a browser submits `\r\n`, which the field would count twice.
    description = forms.CharField(label='Description', required=False, strip=False)


class MoveForm(forms.Form):
    """Moving a task: a project id, or nothing for "No project" (`services.projects.move_task`
    says whether it is one of the user's)."""

    project = forms.CharField(label='Project', required=False, max_length=36)
