"""The "Download files" dialog (issue #315): how to organize the folders, and the cost the user
confirmed."""

from django import forms

from ..models import ExportLayout


class ExportForm(forms.Form):
    layout = forms.ChoiceField(
        choices=ExportLayout.choices,
        error_messages={
            'required': 'Choose how to organize the files.',
            'invalid_choice': 'Choose how to organize the files.',
        },
    )
    #: The total the dialog showed. Required for the web (credits are never spent unseen): a
    #: post without it is refused and the dialog shows the cost again.
    confirm_cost = forms.IntegerField(
        min_value=0,
        required=False,
        error_messages={'invalid': 'Confirm the cost.', 'min_value': 'Confirm the cost.'},
    )
