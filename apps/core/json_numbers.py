"""Reading a whole number out of a JSON request body.

`int(body['size'])` is not safe on JSON: `1e400` parses as infinity, and `int()` of that raises
`OverflowError` (and `ValueError` for NaN), which a `except TypeError, ValueError` lets through as a
500. Every view that takes a size, a timestamp or a part number from a JSON body reads it with
`whole_number` instead.
"""


def whole_number(raw: object) -> int | None:
    """`raw` as an `int` if it is a whole number, else `None`.

    Accepted: an int, a float with no fractional part (`1000.0`; JavaScript has one number type), a
    string of digits with an optional sign. Not accepted: a boolean, a fraction, infinity or NaN,
    `None`, a list or an object, or any other string. Range checks (a size must be positive, a part
    number at least 1) are the caller's.
    """
    if isinstance(raw, bool):
        return None
    if isinstance(raw, float):
        if not raw.is_integer():  # also False for infinity and NaN
            return None
        return int(raw)
    if isinstance(raw, int):
        return raw
    if isinstance(raw, str):
        try:
            return int(raw)
        except ValueError:
            return None
    return None
