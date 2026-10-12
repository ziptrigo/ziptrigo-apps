"""Inbound email bookkeeping.

The S3 poller, the `ingest_message` task and the MIME parsing arrive in a later release; for now
this holds the one operation the admin already needs, so there is a single place to extend.
"""

from ..models import InboundMessage, InboundMessageStatus


def reset_message_for_retry(message: InboundMessage) -> bool:
    """Put a `failed` message back to `pending` with a fresh attempt count, for the admin's
    "retry" action. Returns whether anything changed.

    Hook: once the poller exists it re-enqueues `pending` rows older than 10 minutes, which picks
    these up on its own; if immediate retries are wanted, enqueue the ingest task here.
    """
    changed = InboundMessage.objects.filter(
        pk=message.pk, status=InboundMessageStatus.FAILED
    ).update(status=InboundMessageStatus.PENDING, attempts=0, last_error='', processed_at=None)
    # `created_at` is deliberately left alone: it is what makes a reset row count as "older than
    # 10 minutes" to the poller.
    return bool(changed)
