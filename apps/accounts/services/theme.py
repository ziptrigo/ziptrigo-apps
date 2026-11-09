"""The colour theme a user picked on the account page (issue #166).

It is stored on `User.theme` rather than in the browser, so it follows the account across devices.
`core/base.html` renders it on every page for a signed-in user; a visitor without a session
follows the system.

The colour scheme (`User.color_scheme`, one of `apps.core.color_schemes`) is chosen next to it and
is independent of it: every scheme has a light and a dark side.
"""

from apps.core import color_schemes

from ..models import User

THEMES = frozenset(value for value, _label in User.THEME_CHOICES)


def set_theme(user: User, theme: str) -> None:
    """Save `theme` (`system`, `light` or `dark`) as `user`'s theme.

    The caller has validated it (`apps.accounts.forms.ThemeForm`); anything else is a bug and
    raises `ValueError` rather than being stored, since a bad value would only be caught at render
    time.
    """
    if theme not in THEMES:
        raise ValueError(f'Unknown theme: {theme!r}')
    user.theme = theme
    user.save(update_fields=['theme', 'updated_at'])


def set_color_scheme(user: User, scheme: str) -> None:
    """Save `scheme` (an id of `apps.core.color_schemes`) as `user`'s colour scheme.

    Like `set_theme`, an unknown value is a bug in the caller and raises `ValueError`.
    """
    if scheme not in color_schemes.SCHEMES:
        raise ValueError(f'Unknown color scheme: {scheme!r}')
    user.color_scheme = scheme
    user.save(update_fields=['color_scheme', 'updated_at'])
