"""The colour theme a user picked on the account page (issue #166).

It is stored on `User.theme` rather than in the browser, so it follows the account across devices.
`core/base.html` renders it on every page for a signed-in user; a visitor without a session
follows the system.
"""

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
    user.save(update_fields=['theme'])
