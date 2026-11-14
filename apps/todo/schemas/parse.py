"""Schema for `POST /api/todo/parse`."""

from ninja import Schema
from pydantic import ConfigDict


class ParseSchema(Schema):
    """Body for `POST /parse`: the title to read a date, time and repeat out of."""

    model_config = ConfigDict(extra='forbid')

    title: str


class ParseAnswerSchema(Schema):
    """The answer of `POST /parse`, in one of three shapes (only the fields of that shape are sent):

    - a title read: `available` and `has_date` true or false, and for a title with a date in it
      `clean_title` (the title without those words), `due_date` (`YYYY-MM-DD` or `null`), `due_time`
      (`HH:MM` or `null`), `rrule` and `rrule_text` (the repeat, empty for none), `matched_text`
      (the words read), `label` (the interpretation in words) and `relative` (true when the time is
      relative to now, "in 2 hours": it is right for the moment of the answer, so a client that
      shows it before applying it should move it on by the time that passes); a title with no date has
      `has_date` false and the same fields, with the title as it is;
    - `retry` true, with `available` true and `has_date` false: the call failed or the server is
      busy with another, try again in a minute;
    - `available` false alone: the date reader is not configured on this site.
    """

    available: bool
    has_date: bool | None = None
    retry: bool | None = None
    clean_title: str | None = None
    due_date: str | None = None
    due_time: str | None = None
    rrule: str | None = None
    rrule_text: str | None = None
    matched_text: str | None = None
    label: str | None = None
    relative: bool | None = None
