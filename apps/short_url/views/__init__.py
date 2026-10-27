from .actions import link_delete, link_disable, link_enable
from .dashboard import dashboard, link_list
from .detail import link_detail, link_qr
from .form import link_create, link_edit
from .redirect import redirect_link

__all__ = [
    'dashboard',
    'link_create',
    'link_delete',
    'link_detail',
    'link_disable',
    'link_edit',
    'link_enable',
    'link_list',
    'link_qr',
    'redirect_link',
]
