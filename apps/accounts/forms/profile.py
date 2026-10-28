from typing import Any

from django import forms

from ..models import User
from ..services.timezones import set_timezone, timezone_choices
from ..validators import UNKNOWN_TIMEZONE_MESSAGE, is_valid_timezone


class ProfileForm(forms.ModelForm):
    """The profile section of the account page: the name and the time zone. The email can't be
    changed here.

    The zone is saved through `set_timezone`, not by the model form's own save, so
    `timezone_changed` is sent when (and only when) it changes.
    """

    # A text field validated by hand (below) rather than a `ChoiceField`: the account page's select
    # lists the usual zones, but a valid one outside that list (a legacy name set through the API)
    # must not make an unrelated save of the name fail.
    timezone = forms.CharField(required=False, max_length=64, widget=forms.Select)

    class Meta:
        model = User
        fields = ['name', 'timezone']

    def __init__(self, *args: Any, **kwargs: Any):
        super().__init__(*args, **kwargs)
        self.fields['name'].required = True
        self.fields['name'].error_messages['required'] = 'Please fill in your name.'
        # What is stored now; the model form assigns the posted zone to the instance during
        # validation, and `set_timezone` has to see the change against this.
        self._stored_timezone = self.instance.timezone

    def clean_timezone(self) -> str:
        if self.add_prefix('timezone') not in self.data:
            # A post without the field (a client that only sends the name) leaves the zone alone,
            # rather than reading the missing value as "clear it".
            return self._stored_timezone
        name = self.cleaned_data['timezone'].strip()
        if not is_valid_timezone(name):
            raise forms.ValidationError(UNKNOWN_TIMEZONE_MESSAGE)
        return name

    @property
    def timezone_options(self) -> list[tuple[str, str]]:
        """The select's `(value, label)` options: `UTC` first, then the other zones. A user with no
        zone yet gets a leading "not set" entry, so saving the name alone doesn't set one; a valid
        zone outside the usual list (kept as saved, not offered to others) is added after `UTC`."""
        options = timezone_choices()
        current = self._stored_timezone
        if not current:
            options.insert(0, ('', 'Not set (UTC)'))
        elif is_valid_timezone(current) and current not in dict(options):
            options.insert(1, (current, current.replace('_', ' ')))
        return options

    def save(self, commit: bool = True) -> User:
        user = super().save(commit=False)
        if not commit:
            return user
        new = self.cleaned_data['timezone']
        # Back to the stored value, so the row update below doesn't write the zone behind
        # `set_timezone`'s back (and its signal).
        user.timezone = self._stored_timezone
        user.save()
        set_timezone(user, new)
        return user
