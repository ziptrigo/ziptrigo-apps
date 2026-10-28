from django import forms
from django.core.exceptions import ValidationError

from ..services.comments import clean_comment_body


class CommentForm(forms.Form):
    """Adding or editing a comment: the text only. No `max_length` of its own (a browser submits
    `\\r\\n`, which the field would count twice); the cap and the "Write a comment." message are the
    service's, called below."""

    body = forms.CharField(label='Comment', required=False, strip=False)

    def clean_body(self) -> str:
        try:
            return clean_comment_body(self.cleaned_data['body'])
        except ValidationError as exc:
            raise forms.ValidationError(exc.message_dict['body']) from None
