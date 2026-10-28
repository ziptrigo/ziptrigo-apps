"""Schema for `POST /api/todo/parse`."""

from ninja import Schema
from pydantic import ConfigDict


class ParseSchema(Schema):
    """Body for `POST /parse`: the title to read a date, time and repeat out of."""

    model_config = ConfigDict(extra='forbid')

    title: str
