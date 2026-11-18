"""Limits of QR code sharing (issue #5). The app has no settings singleton and these don't justify
one, so they are constants."""

from datetime import timedelta

#: How many share links of one QR code may be active (not revoked, not expired) at once.
MAX_ACTIVE_LINKS_PER_CODE = 10
#: The longest label the owner can give a link (their own reminder of who it is for).
LINK_LABEL_MAX = 100
#: How many people one share link may be emailed to (the people it was sent to, resends included).
MAX_RECIPIENTS_PER_LINK = 20
#: The longest message the owner can add to the email (after cleaning).
SHARE_MESSAGE_MAX = 500
#: How soon the same person may be emailed the same link again (a resend, or an address typed again).
RESEND_COOLDOWN = timedelta(minutes=10)
#: The longest "details" a report of a shared QR code can have.
MAX_DETAILS_LENGTH = 2000
