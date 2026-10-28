from typing import Any

from django import forms

from ..models import User
from ..services.timezones import set_timezone, timezone_choices
from ..validators import UNKNOWN_TIMEZONE_MESSAGE, is_valid_timezone


class ProfileForm(forms.ModelForm):
    """The profile section of the account page: the name and the time zone. The email can't be
    changed here.

    The zone is not a model-form field (it is not in `Meta.fields`, so it is never assigned to the
    instance nor saved with the row): it is saved through `set_timezone`, so `timezone_changed` is
    sent when (and only when) it changes, and so no other write of this form can put a stale
    snapshot of the column back.
    """

    # A text field validated by hand (below) rather than a `ChoiceField`: the account page's select
    # lists the usual zones, but a valid one outside that list (a legacy name set through the API)
    # must not make an unrelated save of the name fail.
    timezone = forms.CharField(required=False, max_length=64, widget=forms.Select)

    class Meta:
        model = User
        fields = ['name']

    def __init__(self, *args: Any, **kwargs: Any):
        super().__init__(*args, **kwargs)
        self.fields['name'].required = True
        self.fields['name'].error_messages['required'] = 'Please fill in your name.'
        # What is stored now: what the select shows, and what `set_timezone` compares with.
        self._stored_timezone = self.instance.timezone
        self.initial['timezone'] = self._stored_timezone
        # The zone the user chose, or `None` when the post didn't decide anything about it.
        self._chosen_timezone: str | None = None

    @property
    def rendered_timezone(self) -> str:
        """The zone the form was built against, sent back as the hidden `timezone_rendered`.

        A posted `timezone` equal to it means the user didn't touch the select, whatever the
        account holds by then: the browser's detection can fill an empty zone between rendering the
        page and saving it, and an untouched "Not set" must not clear what it saved."""
        return self._stored_timezone

    def clean_timezone(self) -> str:
        field = self.add_prefix('timezone')
        if field not in self.data:
            # A post without the field (a client that only sends the name) leaves the zone alone,
            # rather than reading the missing value as "clear it".
            return self._stored_timezone
        name = self.cleaned_data['timezone'].strip()
        rendered = self.data.get(self.add_prefix('timezone_rendered'))
        if rendered is not None and name == rendered.strip():
            # Unchanged from what the page offered: not a decision, so never a write (this also
            # keeps a stale saved name, which isn't valid any more, from failing the save).
            return self._stored_timezone
        if not is_valid_timezone(name):
            raise forms.ValidationError(UNKNOWN_TIMEZONE_MESSAGE)
        self._chosen_timezone = name
        return name

    @property
    def timezone_options(self) -> list[tuple[str, str]]:
        """The select's `(value, label)` options: `UTC` first, then the other zones. A user with no
        zone yet gets a leading "not set" entry, so saving the name alone doesn't set one; a valid
        zone outside the usual list (kept as saved, not offered to others) is added after `UTC`,
        and a saved name that is no longer a zone at all is still offered, marked, so the select
        shows what is stored instead of silently falling to its first option."""
        options = timezone_choices()
        current = self._stored_timezone
        if not current:
            options.insert(0, ('', 'Not set (UTC)'))
        elif not is_valid_timezone(current):
            options.insert(1, (current, f'{current} (no longer recognised)'))
        elif current not in dict(options):
            options.insert(1, (current, current.replace('_', ' ')))
        return options

    def save(self, commit: bool = True) -> User:
        user = super().save(commit=False)
        if not commit:
            return user
        # Only these columns: `timezone` is written by `set_timezone` alone, so a change made since
        # this request loaded the user (the browser's detection, another tab) isn't reverted by a
        # snapshot of the whole row.
        user.save(update_fields=['name', 'updated_at'])
        if self._chosen_timezone is not None:
            set_timezone(user, self._chosen_timezone)
        return user
