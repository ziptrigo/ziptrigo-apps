from django import forms

from apps.core.products import get_products


class AppsForm(forms.Form):
    """The "Apps" section of the account page (issue #246): which product apps the user sees, and
    in what order.

    `order` is every app's label in the order the tiles are in (a repeated hidden input, so the
    browser sends the DOM order) and `enabled` the labels whose checkbox is ticked. Both are
    checked against the registered products, read when the form is built.
    """

    order = forms.MultipleChoiceField(
        required=False,
        choices=(),
        error_messages={'invalid_choice': 'Unknown app: %(value)s.'},
    )
    enabled = forms.MultipleChoiceField(
        required=False,
        choices=(),
        error_messages={'invalid_choice': 'Unknown app: %(value)s.'},
    )

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        choices = [(product.label, product.name) for product in get_products()]
        self.fields['order'].choices = choices
        self.fields['enabled'].choices = choices
