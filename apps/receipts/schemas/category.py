"""Schemas for the category endpoints (`/api/receipts/categories`)."""

from ninja import Schema


class CategorySchema(Schema):
    id: int
    name: str
    sort_order: int
    receipt_count: int


class CategoryNameSchema(Schema):
    """Body for creating or renaming a category."""

    name: str


class CategoryOrderSchema(Schema):
    """Body for `POST /categories/reorder`: category ids in the order wanted. Categories left out
    keep their relative order after the listed ones."""

    ids: list[int]
