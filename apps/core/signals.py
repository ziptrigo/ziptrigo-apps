"""Signals `core` sends for the apps above it to react to, since `core` can't import them."""

from django.dispatch import Signal

#: A user answered a push notification with one of its action buttons (issue #239). Sent once
#: per answer, after the `PushAction` row is committed, with `send_robust` (a failing receiver is
#: logged). Arguments: `user_id`, `push_id`, `kind` (what the caller of `send_push` said the
#: question is about; a receiver checks it is its own), `action` (the button's id) and
#: `push_action` (the row).
push_action = Signal()
