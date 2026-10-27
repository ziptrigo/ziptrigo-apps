"""The preview page's "report this link" form. No login, and deliberately no password: reporting
must work for anyone who merely has the link, not just someone who could unlock it.
"""

from django import forms

from ..models import MAX_DETAILS_LENGTH, ReportReason

_INPUT = (
    'w-full rounded border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-800 '
    'px-3 py-2 focus:outline-none focus:ring-2 focus:ring-brand-primary'
)


class ReportForm(forms.Form):
    reason = forms.ChoiceField(
        choices=[('', 'Choose a reason'), *ReportReason.choices],
        label='Reason',
        widget=forms.Select(attrs={'class': _INPUT}),
    )
    details = forms.CharField(
        label='Details (optional)',
        required=False,
        max_length=MAX_DETAILS_LENGTH,
        widget=forms.Textarea(attrs={'rows': 3, 'class': _INPUT}),
    )
    reporter_email = forms.EmailField(
        label='Your email (optional)',
        required=False,
        help_text="In case we need to follow up. We won't share it with the owner of the link.",
        widget=forms.EmailInput(attrs={'class': _INPUT, 'autocomplete': 'email'}),
    )
