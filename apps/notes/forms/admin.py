"""The intermediate confirmation form of the admin's takedown action on notes: a reason and an
internal note, on a page of its own (the shape of Short URLs' `TakedownForm`, without the
"notify the owner" box: a takedown never emails)."""

from django import forms


class TakedownForm(forms.Form):
    reason = forms.CharField(
        label='Reason',
        widget=forms.Textarea(attrs={'rows': 3, 'class': 'form-control'}),
        help_text="Recorded on the note and always shown to its owner on the note's page. It never "
        'says who reported the note: keep it about the note, not the reporter. Use the internal '
        'note for anything staff-only.',
    )
    internal_note = forms.CharField(
        label='Internal note (staff only)',
        required=False,
        widget=forms.Textarea(attrs={'rows': 2, 'class': 'form-control'}),
        help_text='Never shown to the owner, on the page or in any email.',
    )
