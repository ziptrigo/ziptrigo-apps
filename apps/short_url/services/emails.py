"""The emails Short URLs sends. Today one: "a link of yours was removed", sent to the owner only
when staff opt in on the takedown confirmation page.

A `django.tasks` task (call `.enqueue(...)`, never `.call(...)`, outside a test) taking a plain
id and re-reading the row. It passes an explicit autoescaped `html_body`, because the link's
destination is whatever the owner typed and the reason is free text (without one,
`SesEmailBackend` wraps the text in an unescaped `<pre>`), and puts nothing of either in the
subject. It goes only to a *confirmed* address of an active account: changing the address through
the API un-confirms the account but leaves sessions valid, and this mail quotes the owner's
link.
"""

from uuid import UUID

from django.conf import settings
from django.tasks import task
from django.urls import reverse
from django.utils.html import format_html

from apps.accounts.models import User
from apps.core.services.email import send_email

from ..models import LinkStatus, ShortLink


@task
def send_link_taken_down_email(link_id: str) -> None:
    """ "A short link of yours was removed" -- to the owner, with the reason staff gave (the same
    text the link's page shows). Skipped if the link is no longer taken down, or the owner has no
    confirmed address or is no longer active."""
    try:
        link = ShortLink.objects.select_related('owner').get(pk=UUID(link_id))
    except ShortLink.DoesNotExist, ValueError:
        return
    owner = link.owner
    if link.status != LinkStatus.TAKEN_DOWN:
        return
    if not owner.email or not owner.email_confirmed or owner.status != User.STATUS_ACTIVE:
        return

    short = f'{settings.BASE_URL.rstrip("/")}{settings.SHORT_URL_PATH}{link.code}'
    page = f'{settings.BASE_URL.rstrip("/")}{reverse("short_url:detail", args=[link.pk])}'
    lines = [
        f'Your short link {short} was removed and no longer works.',
        '',
    ]
    if link.takedown_reason:
        lines += [f'Reason: {link.takedown_reason}', '']
    lines += [f'Details: {page}']

    html_parts = [format_html('<p>Your short link {} was removed and no longer works.</p>', short)]
    if link.takedown_reason:
        html_parts.append(format_html('<p>Reason: {}</p>', link.takedown_reason))
    html_parts.append(format_html('<p><a href="{}">See the link</a></p>', page))
    send_email(
        to=owner.email,
        subject='One of your short links was removed',
        text_body='\n'.join(lines),
        html_body=''.join(str(part) for part in html_parts),
    )
