"""The intermediate confirmation form of the admin's takedown action on QR codes: a reason and an
internal note, on a page of its own (a copy of Notes' `TakedownForm`: a takedown never emails)."""

from django import forms


class TakedownForm(forms.Form):
    reason = forms.CharField(
        label='Reason',
        widget=forms.Textarea(attrs={'rows': 3, 'class': 'form-control'}),
        help_text='Recorded on the QR code and always shown to its owner on the dashboard and in the '
        'share dialog. It never says who reported the QR code: keep it about the QR code, not the '
        'reporter. Use the internal note for anything staff-only.',
    )
    internal_note = forms.CharField(
        label='Internal note (staff only)',
        required=False,
        widget=forms.Textarea(attrs={'rows': 2, 'class': 'form-control'}),
        help_text='Never shown to the owner, on the page or in any email.',
    )
