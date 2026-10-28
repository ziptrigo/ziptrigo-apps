"""`{{ text|markdown }}`: Markdown rendered by `apps.core.markdown`.

    {% load core_markdown %}
    {{ comment.body|markdown }}

For text without task references: it renders with no resolver, so `#1234` stays text. The TODO app
renders through its own tag that passes one.
"""

from django import template
from django.utils.safestring import SafeString

from apps.core.markdown import render

register = template.Library()


@register.filter(name='markdown')
def markdown_filter(text: object) -> SafeString:
    return render('' if text is None else str(text))
