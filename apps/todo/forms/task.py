from typing import Any

from django import forms

from apps.accounts.models import User

from ..models import Label, Priority
from ..services.limits import DESCRIPTION_MAX, TITLE_MAX
from ..services.tasks import POSTPONE_PRESETS

#: The parts of the task page that save on their own (`part` in the POST): which fields each one
#: sends. Without a `part` the form is the whole task form (the quick-add box).
PARTS = {
    'title': ('title',),
    'description': ('description',),
    'details': ('priority', 'due_date', 'due_time', 'labels'),
}


class TaskForm(forms.Form):
    """The quick-add box and the task page's editable parts. It only gathers and parses the fields;
    every rule is `services.tasks`' own, so the web and the API can't drift apart."""

    title = forms.CharField(
        label='Title',
        max_length=TITLE_MAX,
        error_messages={'required': 'Title is required.'},
    )
    description = forms.CharField(
        label='Description', required=False, strip=False, max_length=DESCRIPTION_MAX
    )
    priority = forms.TypedChoiceField(
        label='Priority',
        required=False,
        choices=Priority.choices,
        coerce=int,
        empty_value=Priority.NONE,
    )
    due_date = forms.DateField(label='Date', required=False)
    due_time = forms.TimeField(label='Time', required=False)
    labels = forms.ModelMultipleChoiceField(
        label='Labels', required=False, queryset=Label.objects.none()
    )

    def __init__(self, *args, user: User, part: str = '', **kwargs):
        super().__init__(*args, **kwargs)
        self.user = user
        self.part = part
        self.fields['labels'].queryset = Label.objects.filter(user=user)
        if part:
            keep = PARTS.get(part, ())
            for name in list(self.fields):
                if name not in keep:
                    del self.fields[name]

    def clean(self):
        cleaned = super().clean()
        if cleaned.get('due_time') is not None and cleaned.get('due_date') is None:
            if 'due_date' not in self.fields or 'due_date' in self.errors:
                pass
            elif self.part:
                # Editing a task: clearing the date clears the time with it (the service's rule),
                # so a time input still holding its value is not an error.
                cleaned['due_time'] = None
            else:
                self.add_error('due_time', 'A time needs a date.')
        return cleaned

    def changes(self) -> dict[str, Any]:
        """The cleaned values of the fields this form has, as `update_task` / `create_task` take
        them (the labels as a list)."""
        values = {
            name: self.cleaned_data[name] for name in self.fields if name in self.cleaned_data
        }
        if 'labels' in values:
            values['labels'] = list(values['labels'])
        return values


class SubtaskForm(forms.Form):
    """Adding a subtask: the title only."""

    title = forms.CharField(
        label='Title', max_length=TITLE_MAX, error_messages={'required': 'Title is required.'}
    )


class PostponeForm(forms.Form):
    preset = forms.ChoiceField(
        label='When',
        choices=[(preset, preset) for preset in POSTPONE_PRESETS],
        error_messages={'invalid_choice': 'Choose when to postpone the task to.'},
    )
    date = forms.DateField(label='Date', required=False)


class ChecklistForm(forms.Form):
    index = forms.IntegerField(label='Item', min_value=0, max_value=100_000)
    checked = forms.BooleanField(label='Checked', required=False)
