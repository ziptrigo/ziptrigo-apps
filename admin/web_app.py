from enum import StrEnum


class WebApp(StrEnum):
    """
    Django web apps in this project.

    The value is the app's directory name, relative to the repo root. It is *not* the uv
    dependency-group name -- those are declared separately in ``admin.pip.App`` because
    ``pyproject.toml`` groups can't contain a dash.
    """

    QR_CODE = 'qr_code'
    USERS = 'user-service'
