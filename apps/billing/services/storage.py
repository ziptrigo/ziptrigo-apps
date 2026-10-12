"""The shared storage price (issue #98): credits per GB stored per day, one number for every
product that charges for stored files (file transfer today, receipts later). The price lives in
`BillingSettings`; `storage_charge` is the daily accrual arithmetic every such product shares.
"""

from decimal import ROUND_FLOOR, ROUND_HALF_EVEN, Decimal

from ..models import BillingSettings

_BYTES_PER_GB = Decimal(1024**3)
#: Precision of the carried remainder, matching `Transfer.accrued`
#: (`DecimalField(decimal_places=6)`).
_REMAINDER_PRECISION = Decimal('0.000001')


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
    adds up to a whole credit eventually. Returns `(whole_credits, new_remainder)`.

    The remainder is quantized to 6 decimal places (half-even), so callers must store it in a field
    with at least that many decimal places (`Transfer.accrued` has exactly 6) and what they hold in
    memory is what the database will read back. Quantizing happens after the split, so
    `whole_credits + new_remainder` equals the new running total to within that precision.
    """
    total = accrued + price_per_gb_per_day * (Decimal(size_bytes) / _BYTES_PER_GB)
    whole_credits = int(total.to_integral_value(rounding=ROUND_FLOOR))
    return whole_credits, (total - whole_credits).quantize(
        _REMAINDER_PRECISION, rounding=ROUND_HALF_EVEN
    )
