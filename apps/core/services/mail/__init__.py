"""Reading inbound email, shared by the products that take mail in (Receipts, To-do).

- `mime`: parsing a raw message with hard limits, SES's headers and the defensive reading of its
  `Authentication-Results`, who a message was delivered to, what its parts are, the stripped copy.
- `addresses`: normalizing addresses and the sender allowlist.
- `text`: an HTML body as text, with links kept as `text (url)`.
- `images`: the size of an image read from its header.

Everything product-specific is a parameter (see `mime`'s module docstring); nothing here imports a
product, Django models or the database.
"""
