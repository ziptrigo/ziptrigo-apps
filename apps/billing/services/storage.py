"""The shared storage price (issue #98): credits per GB stored per day, one number for every
product that charges for stored files (file transfer today, receipts later). The price lives in
`BillingSettings`; `storage_charge` is the daily accrual arithmetic every such product shares.
"""

from decimal import ROUND_FLOOR, Decimal

from ..models import BillingSettings

_BYTES_PER_GB = Decimal(1024**3)


def get_storage_price_per_gb_per_day() -> Decimal:
    """The current storage price, in credits per GB per day (`BillingSettings`)."""
    return BillingSettings.load().storage_price_per_gb_per_day


def storage_charge(
    accrued: Decimal, size_bytes: int, price_per_gb_per_day: Decimal
) -> tuple[int, Decimal]:
    """Add one day's storage of `size_bytes` to `accrued` and split the total into whole credits to
    spend now and the fractional remainder to carry to the next day.

    `accrued` is the fractional remainder carried from earlier days. Rounds down: only the floor
    of the running total is ever spent, so a small amount costs nothing on any single day but still
    adds up to a whole credit eventually. Returns `(whole_credits, new_remainder)`, where
    `whole_credits + new_remainder` is exactly the new running total.
    """
    total = accrued + price_per_gb_per_day * (Decimal(size_bytes) / _BYTES_PER_GB)
    whole_credits = int(total.to_integral_value(rounding=ROUND_FLOOR))
    return whole_credits, total - whole_credits
