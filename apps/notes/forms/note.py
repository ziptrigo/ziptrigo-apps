from django import forms


class NoteForm(forms.Form):
    """The editor: title, content and the version being edited. The length and emptiness rules are
    the service's (`services.clean_title` / `clean_content`), so the form only reads the fields. The
    title is optional on the form of an edit through a share link (a later sub-issue)."""

    title = forms.CharField(label='Title', required=False, strip=False)
    content = forms.CharField(label='Content', required=False, strip=False)
    base_version = forms.IntegerField(
        label='Version',
        required=False,
        min_value=1,
        max_value=2_000_000_000,
        error_messages={'invalid': 'Reload the editor: its version could not be read.'},
    )
    #: The credits the person confirmed on the cost panel (`create`): compared with the real cost.
    confirm_cost = forms.CharField(required=False)


class ChecklistForm(forms.Form):
    index = forms.IntegerField(label='Item', min_value=0, max_value=100_000)
    checked = forms.BooleanField(label='Checked', required=False)


class PreviewForm(forms.Form):
    content = forms.CharField(label='Content', required=False, strip=False)


class TagsForm(forms.Form):
    """The tag popover: the ids ticked (`tags`, repeated). Read from the request by the view."""
