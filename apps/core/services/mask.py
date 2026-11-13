"""Partly hiding values that are shown back to a person who may not own them."""

MASK = '•••'


def mask_email(address: str) -> str:
    """`jane@example.com` -> `j•••@example.com`: the first character of the local part, a fixed
    mask (so the length isn't revealed) and the whole domain. For a page that tells someone which
    account a link belongs to without handing the address to whoever holds the link. A value
    without an `@` is masked after its first character."""
    local, at, domain = address.rpartition('@')
    if not at:
        return f'{address[:1]}{MASK}'
    return f'{local[:1]}{MASK}@{domain}'
