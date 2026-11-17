"""`{% app_actions %}`: the per-app actions block of the account page's "Apps" section."""

import logging
from typing import Any

from django import template
from django.template import Context
from django.template.loader import render_to_string
from django.utils.safestring import SafeString, mark_safe

from apps.core.products import products_for

register = template.Library()
logger = logging.getLogger(__name__)


def _blocks(context: Context) -> tuple[list[dict[str, Any]], list[str]]:
    """The products' blocks, and the scripts they need (each once, in the order first asked)."""
    request = context.get('request')
    user = getattr(request, 'user', None)
    if request is None or user is None or not getattr(user, 'is_authenticated', False):
        return [], []
    blocks: list[dict[str, Any]] = []
    scripts: list[str] = []
    # Every product, switched-off ones too: an app hidden from the menu still has the user's data.
    for product, _visible in products_for(user):
        if product.actions is None:
            continue
        try:
            actions = product.actions(request)
            if actions is None:
                continue
            html: SafeString | None = mark_safe(
                render_to_string(actions.template, actions.context, request=request)
            )
        except Exception:
            logger.exception('Actions of %s failed', product.label)
            html = None
        else:
            scripts.extend(path for path in actions.scripts if path not in scripts)
        blocks.append({'product': product, 'html': html})
    return blocks, scripts


@register.simple_tag(takes_context=True)
def app_actions(context: Context) -> SafeString:
    """The "App actions" subsection, or nothing when no product has actions to show."""
    blocks, scripts = _blocks(context)
    if not blocks:
        return mark_safe('')
    return mark_safe(
        render_to_string(
            'core/partials/app_actions.html',
            {'blocks': blocks, 'scripts': scripts},
            request=context.get('request'),
        )
    )
