"""The forms of a shared event. They gather the fields; `services.events` holds the rules."""

from django import forms

from ..limits import EVENT_TITLE_MAX

_TITLE_ERRORS = {
    'required': 'Give the event a title.',
    'max_length': f'Keep the title to {EVENT_TITLE_MAX} characters or fewer.',
}


class EventCreateForm(forms.Form):
    """What the grid's "Share as an event" posts: a title and the two instants of the selected
    range (ISO 8601 with an offset, written by the script)."""

    title = forms.CharField(max_length=EVENT_TITLE_MAX, error_messages=_TITLE_ERRORS)
    start = forms.DateTimeField(
        label='Start', error_messages={'required': 'Select a time range first.'}
    )
    end = forms.DateTimeField(
        label='End', error_messages={'required': 'Select a time range first.'}
    )


class EventEditForm(forms.Form):
    """The owner's edit form: the title, and the time as a date with a start and an end on the
    clock of the event's home location (an end at or before the start is the next day)."""

    title = forms.CharField(max_length=EVENT_TITLE_MAX, error_messages=_TITLE_ERRORS)
    date = forms.DateField(error_messages={'required': 'Choose the date.'})
    start_time = forms.TimeField(
        label='Start', error_messages={'required': 'Choose when it starts.'}
    )
    end_time = forms.TimeField(label='End', error_messages={'required': 'Choose when it ends.'})
