"""The category endpoints (`/api/receipts/categories`): the user's own list, which the AI picks
from. Each one calls the service the settings page uses."""

from django.shortcuts import get_object_or_404

from .. import services
from ..models import ReceiptCategory
from ..schemas import CategoryNameSchema, CategoryOrderSchema, CategorySchema
from .common import mapped_errors
from .router import router


def _owned(request, category_id: int) -> ReceiptCategory:
    """The caller's category, or a 404."""
    return get_object_or_404(ReceiptCategory, pk=category_id, user=request.auth)


def _counted(user, category_id: int) -> ReceiptCategory:
    """The category again, with its `receipt_count`."""
    return next(c for c in services.list_categories(user) if c.pk == category_id)


@router.get('/categories', response=list[CategorySchema])
def list_categories(request):
    """The caller's categories in order (`receipt_count` includes receipts in the Trash)."""
    services.get_account(request.auth)  # seeds the defaults on first use
    return services.list_categories(request.auth)


@router.post('/categories', response={201: CategorySchema})
def create_category(request, payload: CategoryNameSchema):
    with mapped_errors():
        category = services.create_category(request.auth, payload.name)
    return 201, _counted(request.auth, category.pk)


# Registered before the `{category_id}` routes, which use the `int` path converter and so never
# read `reorder` as an id either way.
@router.post('/categories/reorder', response=list[CategorySchema])
def reorder_categories(request, payload: CategoryOrderSchema):
    """Put the categories in the order given; ones left out follow, in their current order."""
    with mapped_errors():
        services.reorder_categories(request.auth, payload.ids)
    return services.list_categories(request.auth)


@router.patch('/categories/{int:category_id}', response=CategorySchema)
def rename_category(request, category_id: int, payload: CategoryNameSchema):
    category = _owned(request, category_id)
    with mapped_errors():
        services.rename_category(category, payload.name)
    return _counted(request.auth, category.pk)


@router.delete('/categories/{int:category_id}', response={204: None})
def delete_category(request, category_id: int):
    """Delete a category; its receipts become uncategorized."""
    services.delete_category(_owned(request, category_id))
    return 204, None
