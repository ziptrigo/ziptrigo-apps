from django import forms

from ..services import validate_description


class FeedbackForm(forms.Form):
    """The feedback page's form (issue #82). The length cap and the blank check live in
    `services.validate_description`, so the form and `submit_feedback` enforce the same rules."""

    description = forms.CharField(
        label='Your feedback',
        strip=False,
        error_messages={'required': 'Please write your feedback.'},
        widget=forms.Textarea(attrs={'rows': 9}),
    )

    def clean_description(self) -> str:
        return validate_description(self.cleaned_data['description'])
