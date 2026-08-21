"""
Shared utilities for Ziptrigo services.

Importable both from Django settings (``utils.environment``, ``utils.settings.base``) and from
application code. Nothing here may import Django at module level -- ``utils.environment`` in
particular is loaded from ``settings.py`` before Django is configured.
"""
