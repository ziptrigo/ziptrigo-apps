"""Refuse to run production with missing or placeholder secrets. Django-free, like `environment`."""

from collections.abc import Mapping

# Values that mean "not really set": the defaults baked into `settings.py`, the placeholders in
# `.env.example`, and the fallbacks in `docker-compose.yml`.
_PLACEHOLDER_PREFIXES = ('django-insecure', 'change-me', '<')


def insecure_secrets(secrets: Mapping[str, str | None]) -> list[str]:
    """Return the names of the secrets in ``secrets`` that are unset or placeholders."""
    return [
        name
        for name, value in secrets.items()
        if not value or value.strip().lower().startswith(_PLACEHOLDER_PREFIXES)
    ]
