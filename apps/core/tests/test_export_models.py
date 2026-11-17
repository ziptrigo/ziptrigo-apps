"""The columns every product's export shares (`apps.core.models.exports`, issues #315 and #326):
the bases are abstract (no table of their own) and every product's models carry all of them, so
the shared lifecycle (`services/exports.py`) works on either."""

import pytest
from django.apps import apps
from django.db.models import NOT_PROVIDED

from apps.receipts.models import ReceiptExport, ReceiptExportItem, ReceiptExportPart
from apps.todo.models import TodoExport, TodoExportItem, TodoExportPart

from ..models.exports import BaseExport, BaseExportItem, BaseExportPart, ExportStatus

pytestmark = [pytest.mark.unit]

PAIRS = [
    (BaseExport, ReceiptExport),
    (BaseExport, TodoExport),
    (BaseExportPart, ReceiptExportPart),
    (BaseExportPart, TodoExportPart),
    (BaseExportItem, ReceiptExportItem),
    (BaseExportItem, TodoExportItem),
]


@pytest.mark.parametrize('base', [BaseExport, BaseExportPart, BaseExportItem])
def test_the_bases_are_abstract_and_have_no_table(base):
    assert base._meta.abstract
    assert base.__name__ not in {model.__name__ for model in apps.get_models()}


@pytest.mark.parametrize(('base', 'model'), PAIRS)
def test_every_product_model_has_every_shared_column(base, model):
    shared = {field.name for field in base._meta.get_fields()}
    own = {field.name for field in model._meta.get_fields()}
    assert shared <= own


@pytest.mark.parametrize('model', [ReceiptExport, TodoExport])
def test_every_new_column_has_a_database_default(model):
    for name in (
        'status',
        'file_count',
        'total_bytes',
        'part_count',
        'price_per_file',
        'charged_credits',
        'refunded_credits',
        'skipped_count',
        'attempts',
        'last_error',
        'filters',
    ):
        assert model._meta.get_field(name).db_default is not NOT_PROVIDED, name


@pytest.mark.parametrize('model', [ReceiptExport, TodoExport])
def test_one_building_export_per_user_is_a_database_constraint(model):
    names = {constraint.name for constraint in model._meta.constraints}
    assert names == {f'{model._meta.app_label}_export_one_building_per_user'}


def test_the_status_is_one_enum_for_both_products():
    from apps.receipts.models import ExportStatus as ReceiptsStatus
    from apps.todo.models import ExportStatus as TodoStatus

    assert ReceiptsStatus is ExportStatus and TodoStatus is ExportStatus
    assert [value for value, _label in ExportStatus.choices] == [
        'building',
        'ready',
        'failed',
        'expired',
        'deleted',
    ]


@pytest.mark.parametrize('model', [ReceiptExport, TodoExport])
def test_the_status_helpers(model):
    export = model(status=ExportStatus.BUILDING)
    assert export.is_building and not export.is_ready
    export.status = ExportStatus.READY
    assert export.is_ready and not export.is_building
