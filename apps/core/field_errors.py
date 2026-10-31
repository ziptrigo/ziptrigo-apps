"""Per-field validation errors for the 422 responses (issue #219).

A refused form already puts one line per error, `Label: message`, in the form's message slot. This
module also keeps *which field* each error belongs to, so the page can show the message under that
field as well (`core/js/field_errors.js`). The slot is unchanged; the field messages are an addition
the slot's partial carries as hidden data (`{% field_errors source %}`,
`core/partials/field_errors.html`).

Three ways to hand the data over, all ending in the same `{html input name: [messages]}` dict:

- a Django form: `{% field_errors form %}` in the errors partial (`from_form`);
- the lines a view builds for its slot: `form_lines(form)` / `validation_lines(exc, labels=...)`
  return an `ErrorLines`, a list of the slot's lines that also carries `field_errors`, so the view
  passes it to its partial as before and the partial calls `{% field_errors messages_list %}`;
- a dict, for a view that builds its own errors (`{'recipients': ['Add at least one address.']}`).

An error that belongs to no field (`__all__`), or to a field the page has no control for, is only in
the slot: the script ignores a name it cannot find.
"""

from collections.abc import Mapping
from typing import Any

from django.core.exceptions import NON_FIELD_ERRORS, ValidationError
from django.forms import BaseForm
from django.utils.functional import Promise

#: `{html input name: [message, ...]}`, in the order the form reports them.
FieldErrors = dict[str, list[str]]


class ErrorLines(list[str]):
    """The lines of a message slot (`Label: message`, non-field errors first) that also remember
    which field each error belongs to. Behaves as the plain list the slot partials loop over."""

    field_errors: FieldErrors

    def __init__(self, lines=(), field_errors: FieldErrors | None = None):
        super().__init__(lines)
        self.field_errors = field_errors or {}


def _label(name: str, labels: Mapping[str, str] | None) -> str:
    if labels and name in labels:
        return labels[name]
    return name.replace('_', ' ').capitalize()


def _texts(messages: Any) -> list[str]:
    """The messages of one entry as plain strings: a string or lazy translation is one message
    (never iterated character by character), a `ValidationError` its messages, anything else is
    walked (a list, an `ErrorList`)."""
    if isinstance(messages, (str, Promise)):
        return [str(messages)]
    if isinstance(messages, ValidationError):
        return [str(message) for message in messages.messages]
    return [text for message in messages for text in _texts(message)]


def from_form(form: BaseForm) -> FieldErrors:
    """The errors of a bound form's fields by the name of their input (so a prefixed form is
    found under its prefixed name). Non-field errors are left out."""
    errors: FieldErrors = {}
    for name, messages in form.errors.items():
        if name == NON_FIELD_ERRORS:
            continue
        html_name = form[name].html_name if name in form.fields else name
        errors[html_name] = [str(message) for message in messages]
    return errors


def form_lines(form: BaseForm) -> ErrorLines:
    """`Label: message` per error of a form, non-field errors first (the design system's error
    format), with each field's messages kept by input name."""
    lines = [str(error) for error in form.non_field_errors()]
    for name, messages in form.errors.items():
        if name == NON_FIELD_ERRORS:
            continue
        field = form.fields.get(name)
        label = field.label if field is not None and field.label else _label(name, None)
        lines += [f'{label}: {message}' for message in messages]
    return ErrorLines(lines, from_form(form))


def validation_lines(
    exc: ValidationError,
    labels: Mapping[str, str] | None = None,
    names: Mapping[str, str] | None = None,
) -> ErrorLines:
    """The lines to show for a service's `ValidationError`: `Label: message` for a field's,
    plain for one that belongs to no field, which come first (as in `form_lines`). `labels` maps a
    service field to its label; `names` maps it to the name of the input when they differ
    (default: the same)."""
    if not hasattr(exc, 'error_dict'):
        return ErrorLines(exc.messages)
    general: list[str] = []
    fielded: list[str] = []
    field_errors: FieldErrors = {}
    for field, messages in exc.message_dict.items():
        for message in messages:
            if field == NON_FIELD_ERRORS:
                general.append(message)
            else:
                fielded.append(f'{_label(field, labels)}: {message}')
                field_errors.setdefault((names or {}).get(field, field), []).append(message)
    return ErrorLines(general + fielded, field_errors)


def message_lines(exc: ValidationError, names: Mapping[str, str] | None = None) -> ErrorLines:
    """`exc.messages` as they are (no labels: for a view that always showed them bare), with the
    errors of a field-keyed `ValidationError` kept by input name as well."""
    field_errors: FieldErrors = {}
    if hasattr(exc, 'error_dict'):
        for field, messages in exc.message_dict.items():
            if field != NON_FIELD_ERRORS:
                field_errors.setdefault((names or {}).get(field, field), []).extend(messages)
    return ErrorLines(exc.messages, field_errors)


def field_errors_of(source: Any) -> FieldErrors:
    """The per-field errors of whatever a view gave its errors partial: a form, `ErrorLines`, a
    dict, or nothing. Anything else (a plain list of strings, a string) has none."""
    if isinstance(source, BaseForm):
        return from_form(source) if source.is_bound else {}
    if isinstance(source, ErrorLines):
        return source.field_errors
    if isinstance(source, Mapping):
        return {str(name): _texts(messages) for name, messages in source.items()}
    return {}
