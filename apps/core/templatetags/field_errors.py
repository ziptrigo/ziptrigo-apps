"""`{% field_errors source %}`: hand a refused form's per-field errors to the page (issue #219).

Put it in a 422 errors partial, next to the message slot's lines:

    {% load field_errors %}
    <div class="text-red-600" role="alert">{% for line in messages_list %}{{ line }}<br>{% endfor %}</div>
    {% field_errors messages_list %}

`source` is a form, the `ErrorLines` a view built (`apps.core.field_errors`) or a dict of input
name to messages. It renders hidden data only (nothing the visitor sees or a screen reader reads);
`core/js/field_errors.js` shows each message under its field. The fields are looked for in the form the
data sits in, else the form that sent the request; `scope` is the id of the element (a form, or a card with
several) to look in when neither is it -- a row re-rendered by a request one of its forms sent.
A form with no message slot passes `fallback=True`: with JavaScript off the messages are listed in a
`<noscript>` instead, so they are not lost.
"""

from django import template

from apps.core.field_errors import field_errors_of

register = template.Library()


@register.inclusion_tag('core/partials/field_errors.html')
def field_errors(source, scope: str = '', fallback: bool = False) -> dict:
    return {'field_errors': field_errors_of(source), 'scope': scope, 'fallback': fallback}
