from django import forms

from ..models import User


class ProfileForm(forms.ModelForm):
    """The profile section of the account page. The email can't be changed here."""

    class Meta:
        model = User
        fields = ['name']

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['name'].required = True
        self.fields['name'].error_messages['required'] = 'Please fill in your name.'
