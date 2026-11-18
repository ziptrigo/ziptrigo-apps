"""Limits of QR code sharing (issue #5). The app has no settings singleton and these don't justify
one, so they are constants."""

#: How many share links of one QR code may be active (not revoked, not expired) at once.
MAX_ACTIVE_LINKS_PER_CODE = 10
#: The longest label the owner can give a link (their own reminder of who it is for).
LINK_LABEL_MAX = 100
