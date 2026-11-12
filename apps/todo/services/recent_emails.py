"""The user's latest inbound emails (issue #186): what the Settings page lists under "Add tasks by
email" and `GET /api/todo/inbox` returns as `recent_emails`."""

from apps.accounts.models import User

from ..models import InboundEmail, InboundEmailStatus

#: How many of the latest emails are listed.
RECENT_EMAILS = 20

#: The words for a rejection (the Settings page's "outcome").
OUTCOMES = {
    InboundEmailStatus.REJECTED_SENDER: 'sender not allowed',
    InboundEmailStatus.REJECTED_SPAM: 'flagged as spam',
    InboundEmailStatus.REJECTED_VIRUS: 'flagged as a virus',
    InboundEmailStatus.REJECTED_CREDITS: 'not enough credits',
    InboundEmailStatus.REJECTED_TOO_LARGE: 'too large',
    InboundEmailStatus.REJECTED_TASK_LIMIT: 'too many open tasks',
    InboundEmailStatus.NO_CONTENT: 'nothing to add',
}


def recent_emails(user: User) -> list[InboundEmail]:
    """The user's latest `RECENT_EMAILS` inbound emails, newest first, each with `accepted` and
    `outcome` (the words for a rejection) for the template. One query: the task is joined."""
    emails = list(
        InboundEmail.objects.filter(user=user)
        .select_related('task')
        .order_by('-created_at')[:RECENT_EMAILS]
    )
    for email in emails:
        email.accepted = email.status == InboundEmailStatus.ACCEPTED
        email.outcome = OUTCOMES.get(email.status, '')
    return emails
