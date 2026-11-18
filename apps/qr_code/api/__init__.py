from . import share  # noqa: F401  (registers the share-link endpoints on the router)
from .qrcode import router

__all__ = ['router']
