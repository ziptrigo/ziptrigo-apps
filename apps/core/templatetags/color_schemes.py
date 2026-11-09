"""Template access to the colour schemes (`apps.core.color_schemes`)."""

from django import template

from apps.core import color_schemes

register = template.Library()


@register.simple_tag(takes_context=True)
def active_color_scheme(context) -> color_schemes.ColorScheme:
    """The scheme the page renders in: the signed-in user's, the default for a visitor."""
    return color_schemes.scheme_for(context.get('user'))


@register.simple_tag(name='color_schemes')
def color_scheme_list() -> list[color_schemes.ColorScheme]:
    """Every scheme, in the order the picker shows them."""
    return color_schemes.all_schemes()
