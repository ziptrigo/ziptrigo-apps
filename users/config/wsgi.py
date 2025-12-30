"""WSGI config for user.

It exposes the WSGI callable as a module-level variable named ``application``.

For more information on this file, see
https://docs.djangoproject.com/en/6.0/howto/deployment/wsgi/
"""

import os

from django.core.wsgi import get_wsgi_application
from whitenoise import WhiteNoise

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')

application = get_wsgi_application()

# Wrap with WhiteNoise for static file serving in production
if not os.environ.get('DEBUG', 'False').lower() in ['true', '1']:
    from pathlib import Path
    project_root = Path(__file__).resolve().parent.parent
    static_root = project_root / 'staticfiles'
    application = WhiteNoise(
        application,
        root=str(static_root),
        index_file=True,
        mimetypes={
            '.webp': 'image/webp',
        },
    )
