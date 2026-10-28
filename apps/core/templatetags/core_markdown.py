"""`{{ text|markdown }}`: Markdown rendered by `apps.core.markdown`.

    {% load core_markdown %}
    {{ comment.body|markdown }}

For text without task references: it renders with no resolver, so `#1234` stays text. The TODO app
renders through its own tag that passes one.

The filter never raises: a template that renders a field must not fail with a 500 because the
value is too long. A text over `MAX_MARKDOWN_CHARS` is shown unrendered instead, escaped, in one
`<p>` with its line breaks kept. (Callers are still expected to cap their fields.)
"""

import logging

from django import template
from django.template.defaultfilters import linebreaksbr
from django.utils.html import format_html
from django.utils.safestring import SafeString

from apps.core.markdown import render

logger = logging.getLogger(__name__)

register = template.Library()


@register.filter(name='markdown')
def markdown_filter(text: object) -> SafeString:
    source = '' if text is None else str(text)
    try:
        return render(source)
    except ValueError:
        logger.warning('Markdown filter: document of %d characters shown unrendered', len(source))
        return format_html('<p>{}</p>', linebreaksbr(source, autoescape=True))
