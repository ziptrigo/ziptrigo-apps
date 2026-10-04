"""Tailwind classes for form widgets.

Django renders bare `<input>`/`<textarea>`/`<select>` elements, with no visible border in light
mode and a stark white fill in dark mode. `StyledFormMixin` gives every text-like widget the same
classes the account profile form uses (`accounts/partials/profile_form.html`).
"""

from django import forms

WIDGET_CLASSES = (
    'w-full rounded border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-800 '
    'px-3 py-2 focus:outline-none focus:ring-2 focus:ring-sage-300'
)
CHECKBOX_CLASSES = 'rounded border-gray-300 dark:border-gray-600 text-sage-500 focus:ring-sage-300'


class StyledFormMixin:
    """Add the site's input styling to every widget; list it before `forms.Form`."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():  # ty: ignore[unresolved-attribute]
            widget = field.widget
            if isinstance(widget, forms.CheckboxInput):
                classes = CHECKBOX_CLASSES
            elif isinstance(widget, forms.HiddenInput):
                continue
            else:
                classes = WIDGET_CLASSES
            widget.attrs['class'] = f'{widget.attrs.get("class", "")} {classes}'.strip()
