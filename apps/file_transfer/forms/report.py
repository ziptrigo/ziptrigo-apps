"""The public download page's "report this transfer" form (issue #59). No login required, and
deliberately doesn't ask for the transfer's password -- reporting must work for anyone who merely
has the link, not just someone who could actually unlock it.
"""

from django import forms

from ..models import MAX_DETAILS_LENGTH, AbuseReportReason


class AbuseReportForm(forms.Form):
    reason = forms.ChoiceField(choices=AbuseReportReason.choices, label='Reason')
    details = forms.CharField(
        label='Details (optional)',
        required=False,
        max_length=MAX_DETAILS_LENGTH,
        widget=forms.Textarea(attrs={'rows': 3}),
    )
    reporter_email = forms.EmailField(
        label='Your email (optional)',
        required=False,
        help_text="In case we need to follow up. We won't share it with the sender.",
    )
