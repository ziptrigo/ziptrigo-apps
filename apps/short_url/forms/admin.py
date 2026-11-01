"""The intermediate confirmation forms of the admin's abuse actions on short links: take down,
block the destination domain, deactivate the owner's account. Each action asks once more on its
own page before it changes anything, the shape of `CreditTransactionAdmin`'s credit adjustment.
"""

from django import forms


class TakedownForm(forms.Form):
    """Take down: a reason (recorded either way, and *always* shown to the owner, see its help
    text), a staff-only internal note, and an opt-in notification, off by default so a takedown
    never emails an owner unless staff choose to."""

    reason = forms.CharField(
        label='Reason',
        widget=forms.Textarea(attrs={'rows': 3, 'class': 'form-control'}),
        help_text="Recorded on the link and always shown to its owner (on the link's page, and in "
        'the email if you notify them below). It never says who reported the link: keep it '
        'about the link, not the reporter. Use the internal note for anything staff-only.',
    )
    internal_note = forms.CharField(
        label='Internal note (staff only)',
        required=False,
        widget=forms.Textarea(attrs={'rows': 2, 'class': 'form-control'}),
        help_text='Never shown to the owner, on the page or in any email.',
    )
    notify_owner = forms.BooleanField(
        label='Notify the owner by email ("one of your links was removed")',
        required=False,
        initial=False,
        widget=forms.CheckboxInput(attrs={'class': 'form-check-input'}),
    )


class BlockDomainForm(forms.Form):
    """Block the destination host of the selected links."""

    include_subdomains = forms.BooleanField(
        label='Also block its subdomains',
        required=False,
        initial=True,
        widget=forms.CheckboxInput(attrs={'class': 'form-check-input'}),
        help_text='On: blocks "*.host", the host and every subdomain of it. Off: blocks exactly '
        'that host. Links already pointing there stop working at once.',
    )
    reason = forms.CharField(
        label='Reason',
        required=False,
        widget=forms.Textarea(attrs={'rows': 2, 'class': 'form-control'}),
    )


class DeactivateOwnerForm(forms.Form):
    """Deactivate the owner's account: it can no longer sign in or use the API."""

    reason = forms.CharField(
        label='Reason',
        required=False,
        widget=forms.Textarea(attrs={'rows': 2, 'class': 'form-control'}),
        help_text="Kept on the account (and shown to the owner as the link's takedown reason if "
        'you also take their links down below).',
    )
    take_down_links = forms.BooleanField(
        label="Also take down the owner's other active links",
        required=False,
        initial=False,
        widget=forms.CheckboxInput(attrs={'class': 'form-check-input'}),
        help_text='Deactivating an account does not stop its links redirecting: off by default, '
        'so anything the owner already has live keeps working unless you opt in here. The '
        'owner is not emailed.',
    )

    def clean(self):
        cleaned = super().clean() or {}
        if cleaned.get('take_down_links') and not cleaned.get('reason', '').strip():
            self.add_error('reason', 'Give a reason: it is shown to the owner on each link.')
        return cleaned
