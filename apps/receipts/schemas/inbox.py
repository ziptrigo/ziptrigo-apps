"""Schemas for the inbox endpoints (`/api/receipts/inbox`)."""

from datetime import datetime

from ninja import Schema


class ForwardingConfirmationSchema(Schema):
    """The latest Gmail forwarding confirmation (of the last 7 days) the user has to act on: type
    `code` into Gmail, or open `url` (empty unless it is an https link to Google's mail
    settings)."""

    received_at: datetime
    code: str
    url: str


class InboxSchema(Schema):
    address: str
    sender_allowlist: list[str]
    default_currency: str
    forwarding_confirmation: ForwardingConfirmationSchema | None


class InboxUpdateSchema(Schema):
    """Body for `PATCH /inbox`; only the fields present are applied. `sender_allowlist` is a list
    of addresses and `*@domain` wildcards (an empty list accepts anyone); `default_currency` is an
    ISO 4217 code."""

    sender_allowlist: list[str] | None = None
    default_currency: str | None = None
