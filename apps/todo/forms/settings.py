from django import forms


class SettingsForm(forms.Form):
    default_due_time = forms.TimeField(
        label='Default due time',
        error_messages={'required': 'Enter a time.', 'invalid': 'Enter a valid time.'},
    )
