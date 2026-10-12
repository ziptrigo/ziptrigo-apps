"""Shared helper for the `pk=1` singleton settings models (`CoreSettings`, `BillingSettings`,
`FileTransferSettings`)."""

from typing import TypeVar

from django.db import IntegrityError, models

_ModelT = TypeVar('_ModelT', bound=models.Model)


def load_singleton(model_cls: type[_ModelT]) -> _ModelT:
    """Return the `pk=1` row of `model_cls`, creating it with defaults on first use.

    `get_or_create` already handles a concurrent insert in a transaction-per-statement setup, but
    on Postgres two workers hitting an empty table at the same instant can both attempt the insert
    and one raises `IntegrityError` (its own `get_or_create` fallback can fail inside an outer
    transaction). Whoever lost the race just re-reads the row the winner created.
    """
    manager = model_cls._default_manager
    try:
        obj, _ = manager.get_or_create(pk=1)
    except IntegrityError:
        obj = manager.get(pk=1)
    return obj
