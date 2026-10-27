"""Pydantic/ninja schemas for the Short URLs JWT API (`/api/s/`)."""

from .link import (
    ChargeSchema,
    CostsSchema,
    LinkCreateSchema,
    LinkDetailSchema,
    LinkListSchema,
    LinkSchema,
    LinkUpdateSchema,
    PreviewSchema,
    QuoteSchema,
)
from .stats import (
    BucketSchema,
    StatsDetailSchema,
    StatsSchema,
    TopRowSchema,
    TopTableSchema,
    TotalsSchema,
)

__all__ = [
    'BucketSchema',
    'ChargeSchema',
    'CostsSchema',
    'LinkCreateSchema',
    'LinkDetailSchema',
    'LinkListSchema',
    'LinkSchema',
    'LinkUpdateSchema',
    'PreviewSchema',
    'QuoteSchema',
    'StatsDetailSchema',
    'StatsSchema',
    'TopRowSchema',
    'TopTableSchema',
    'TotalsSchema',
]
