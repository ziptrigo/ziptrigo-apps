"""Anonymous-sender emails (spec sections 2 and 8): the pre-confirmation code+link email, and the
post-confirmation sender copy. Both go through `apps.core.services.email`/`email_verification`
like every other email in this app (`services.emails`); split into their own module since they're
keyed on `sender_email` rather than `owner`.
"""

from django.conf import settings
from django.tasks import task
from django.urls import reverse

from apps.core.services.email import send_email
from apps.core.services.email_verification import EmailVerificationContext

from ..models import Transfer
from .naming import transfer_display_name

#: `core.services.email_verification` scopes "only the latest verification is valid" and the
#: resend cooldown to `(email, purpose)` -- this is file_transfer's own namespace within that,
#: distinct from e.g. `accounts.email_confirmation`.
PURPOSE = 'file_transfer.anonymous_send'


def build_confirmation_email(transfer: Transfer):
    """A `core.services.email_verification.start`-compatible `build_email` callback for `transfer`:
    a code to type on the send page, and a link to the same effect (spec section 2 step 4)."""

    def _build(context: EmailVerificationContext) -> tuple[str, str, str]:
        confirm_url = (
            f'{settings.BASE_URL}'
            f'{reverse("file_transfer:anon-send-confirm-link", args=[transfer.id, context.token])}'
        )
        subject = 'Confirm your ZipTrigo file transfer'
        text_body = (
            'Someone (hopefully you) is sending files through ZipTrigo.\n\n'
            f'Enter this code on the page you were sending from: {context.code}\n\n'
            f'Or click this link to confirm instead: {confirm_url}\n\n'
            f"This code expires in {context.validity_minutes} minutes. If this wasn't you, "
            'just ignore this email -- nothing will be sent.'
        )
        html_body = (
            '<p>Someone (hopefully you) is sending files through ZipTrigo.</p>'
            f'<p>Enter this code on the page you were sending from: <strong>{context.code}</strong></p>'
            f'<p>Or click this link to confirm instead: <a href="{confirm_url}">{confirm_url}</a></p>'
            f"<p>This code expires in {context.validity_minutes} minutes. If this wasn't you, "
            'just ignore this email -- nothing will be sent.</p>'
        )
        return subject, text_body, html_body

    return _build


def _manage_url(transfer: Transfer) -> str:
    base = settings.BASE_URL.rstrip('/')
    path = reverse('t:manage', args=[transfer.slug, transfer.manage_token])
    return f'{base}{path}'


@task
def send_anonymous_sender_copy(transfer_id: str) -> None:
    """Copy/confirmation to the anonymous sender, on activation (spec section 8) -- like
    `services.emails.send_sender_copy`, but keyed on `sender_email` (there's no `owner`) and
    including the one-time manage link, since an anonymous sender has no dashboard to fall back
    on afterwards (spec section 6)."""
    try:
        transfer = Transfer.objects.get(id=transfer_id)
    except Transfer.DoesNotExist:
        return
    if not transfer.sender_email:
        return

    name = transfer_display_name(transfer)
    recipients = ', '.join(r.email for r in transfer.recipients.all())
    body = (
        f'Your transfer "{name}" was sent to: {recipients}.\n\n'
        f'Download link: {transfer.absolute_download_url}\n\n'
        'Manage this transfer (disable it, or see how many times it was downloaded) -- keep '
        f"this link, it's the only way to manage this transfer: {_manage_url(transfer)}"
    )
    send_email(
        to=transfer.sender_email,
        subject=f'Your transfer "{name}" is on its way',
        text_body=body,
    )
