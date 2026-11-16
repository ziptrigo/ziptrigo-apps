"""Showing a service `ValidationError` on a form, shared by the send and request forms."""

from django import forms
from django.core.exceptions import ValidationError

#: The service's field names that a form calls something else.
_FORM_FIELD = {'max_total_size_bytes': 'max_total_gb'}


def add_service_errors(form: forms.Form, exc: ValidationError) -> None:
    """Show a service `ValidationError` on the form: under its field when it names one, as a form
    error otherwise."""
    if hasattr(exc, 'error_dict'):
        for field, errors in exc.error_dict.items():
            target = _FORM_FIELD.get(field, field)
            for error in errors:
                form.add_error(target if target in form.fields else None, error.messages[0])
    else:
        form.add_error(None, exc.messages[0])
