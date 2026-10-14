"""Rate limiting on file_transfer endpoints (issue #53): anonymous upload/confirm, logged-in
uploads (generous, per user), the public download page and password attempts (per IP and per
transfer), and the anonymous manage link.
"""

import json
from datetime import timedelta
from types import SimpleNamespace

import pytest
from django.urls import reverse
from django.utils import timezone

from ..models import Transfer, TransferStatus
from ..services import password as password_service

pytestmark = [
    pytest.mark.django_db,
    pytest.mark.unit,
    # Every test here makes two or more requests against a limit of 1-2 per fixed window; see the
    # fixture's docstring for why a real clock makes that flaky.
    pytest.mark.usefixtures('ratelimit_clock'),
]


def _post_json(client, url, payload):
    return client.post(url, data=json.dumps(payload), content_type='application/json')


def _enable(settings, rule: str, limit: int = 1, window: int = 60):
    settings.RATELIMIT_ENABLE = True
    settings.RATELIMIT_RULES = {**settings.RATELIMIT_RULES, rule: (limit, window)}


def _active(transfer, **overrides):
    transfer.status = TransferStatus.ACTIVE
    for key, value in overrides.items():
        setattr(transfer, key, value)
    transfer.save()
    return transfer


class TestAnonymousUploadRateLimit:
    def test_add_file_429_after_ip_limit(self, client, anon_enabled, fake_storage, settings):
        _enable(settings, 'FT_ANON_UPLOAD_IP', limit=1)
        client.get(reverse('file_transfer:anon-send'))
        transfer = Transfer.objects.get(owner__isnull=True, status=TransferStatus.DRAFT)
        url = reverse('file_transfer:anon-send-add-file', args=[transfer.id])

        _post_json(client, url, {'name': 'a.bin', 'size': 10})
        response = _post_json(client, url, {'name': 'b.bin', 'size': 10})

        assert response.status_code == 429
        assert response.json()['error']
        assert 'Retry-After' in response

    def test_part_urls_429_after_ip_limit(self, client, anon_enabled, fake_storage, settings):
        client.get(reverse('file_transfer:anon-send'))
        transfer = Transfer.objects.get(owner__isnull=True, status=TransferStatus.DRAFT)
        add_url = reverse('file_transfer:anon-send-add-file', args=[transfer.id])
        added = _post_json(client, add_url, {'name': 'a.bin', 'size': 10}).json()

        # Enabled *after* creating the draft/file (which themselves go through the same shared
        # bucket) so the limit budget is spent entirely on the `part_urls` calls below.
        _enable(settings, 'FT_ANON_UPLOAD_IP', limit=1)
        parts_url = reverse(
            'file_transfer:anon-send-part-urls', args=[transfer.id, added['file_id']]
        )
        payload = {'parts': [{'part_number': 1, 'checksum_sha256': 'x'}]}

        _post_json(client, parts_url, payload)
        response = _post_json(client, parts_url, payload)

        assert response.status_code == 429


class TestAnonymousConfirmRateLimit:
    def _drafted_transfer_with_uploaded_file(self, client, fake_storage):
        client.get(reverse('file_transfer:anon-send'))
        transfer = Transfer.objects.get(owner__isnull=True, status=TransferStatus.DRAFT)
        from ..models import TransferFile

        add_url = reverse('file_transfer:anon-send-add-file', args=[transfer.id])
        added = _post_json(client, add_url, {'name': 'report.pdf', 'size': 10}).json()
        file = TransferFile.objects.get(id=added['file_id'])
        fake_storage.seed_object(file.storage_key, 10)
        _post_json(
            client,
            reverse('file_transfer:anon-send-complete-file', args=[transfer.id, file.id]),
            {'parts': [{'PartNumber': 1, 'ETag': 'e1'}]},
        )
        return transfer

    def test_start_confirmation_429_after_ip_limit(
        self, client, anon_enabled, fake_storage, settings, monkeypatch
    ):
        sent = []
        monkeypatch.setattr(
            'apps.core.services.email_verification.send_email',
            lambda **kwargs: sent.append(kwargs) or (1, 0),
        )
        transfer = self._drafted_transfer_with_uploaded_file(client, fake_storage)
        _enable(settings, 'FT_ANON_CONFIRM_START_IP', limit=0)

        response = client.post(
            reverse('file_transfer:anon-send-confirm', args=[transfer.id]),
            data={
                'sender_email': 'sender@example.com',
                'recipients': 'recipient@example.com',
                'expiry_choice': '1',
            },
        )

        assert response.status_code == 429
        transfer.refresh_from_db()
        assert transfer.status == TransferStatus.DRAFT  # never started
        assert not sent

    def test_resend_confirmation_429_after_ip_limit(
        self, client, anon_enabled, fake_storage, settings, monkeypatch
    ):
        monkeypatch.setattr(
            'apps.core.services.email_verification.send_email',
            lambda **kwargs: (1, 0),
        )
        transfer = self._drafted_transfer_with_uploaded_file(client, fake_storage)
        client.post(
            reverse('file_transfer:anon-send-confirm', args=[transfer.id]),
            data={
                'sender_email': 'sender@example.com',
                'recipients': 'recipient@example.com',
                'expiry_choice': '1',
            },
        )
        _enable(settings, 'FT_ANON_CONFIRM_RESEND_IP', limit=0)

        response = client.post(
            reverse('file_transfer:anon-send-confirm-resend', args=[transfer.id])
        )

        assert response.status_code == 429
        assert b'Too many requests' in response.content

    def test_confirm_code_429_after_ip_limit(
        self, client, anon_enabled, fake_storage, settings, monkeypatch
    ):
        monkeypatch.setattr(
            'apps.core.services.email_verification.send_email',
            lambda **kwargs: (1, 0),
        )
        transfer = self._drafted_transfer_with_uploaded_file(client, fake_storage)
        client.post(
            reverse('file_transfer:anon-send-confirm', args=[transfer.id]),
            data={
                'sender_email': 'sender@example.com',
                'recipients': 'recipient@example.com',
                'expiry_choice': '1',
            },
        )
        _enable(settings, 'FT_ANON_CONFIRM_CODE_IP', limit=0)

        response = client.post(
            reverse('file_transfer:anon-send-confirm-code', args=[transfer.id]),
            data={'code': '000000'},
        )

        assert response.status_code == 429
        assert b'Too many attempts' in response.content
        transfer.refresh_from_db()
        assert transfer.status == TransferStatus.PENDING_CONFIRMATION  # code never even checked

    def test_confirm_link_429_after_ip_limit(
        self, client, anon_enabled, fake_storage, settings, monkeypatch
    ):
        import re

        sent = []
        monkeypatch.setattr(
            'apps.core.services.email_verification.send_email',
            lambda **kwargs: sent.append(kwargs) or (1, 0),
        )
        transfer = self._drafted_transfer_with_uploaded_file(client, fake_storage)
        client.post(
            reverse('file_transfer:anon-send-confirm', args=[transfer.id]),
            data={
                'sender_email': 'sender@example.com',
                'recipients': 'recipient@example.com',
                'expiry_choice': '1',
            },
        )
        match = re.search(r'(/transfer/send/anon/\S+/confirm/link/\S+/)', sent[-1]['text_body'])
        assert match
        link_url = match.group(1)

        _enable(settings, 'FT_ANON_CONFIRM_LINK_IP', limit=0)
        response = client.get(link_url)

        assert response.status_code == 429
        transfer.refresh_from_db()
        assert transfer.status == TransferStatus.PENDING_CONFIRMATION


class TestLoggedInUploadRateLimit:
    def test_add_file_429_after_user_limit(self, client, draft_transfer, fake_storage, settings):
        _enable(settings, 'FT_UPLOAD_USER', limit=1)
        client.force_login(draft_transfer.owner)
        url = reverse('file_transfer:send-add-file', args=[draft_transfer.id])

        _post_json(client, url, {'name': 'a.bin', 'size': 10})
        response = _post_json(client, url, {'name': 'b.bin', 'size': 10})

        assert response.status_code == 429
        assert response.json()['error']

    def test_different_users_have_independent_budgets(
        self, client, draft_transfer, fake_storage, settings
    ):
        from apps.accounts.tests.factories import UserFactory

        from ..models import Transfer as TransferModel

        _enable(settings, 'FT_UPLOAD_USER', limit=1)
        other_user = UserFactory()
        other_transfer = TransferModel.objects.create(owner=other_user, status=TransferStatus.DRAFT)

        client.force_login(draft_transfer.owner)
        _post_json(
            client,
            reverse('file_transfer:send-add-file', args=[draft_transfer.id]),
            {'name': 'a.bin', 'size': 10},
        )
        client.logout()

        client.force_login(other_user)
        response = _post_json(
            client,
            reverse('file_transfer:send-add-file', args=[other_transfer.id]),
            {'name': 'b.bin', 'size': 10},
        )

        assert response.status_code == 201


class TestDownloadPageRateLimit:
    def test_429_after_ip_limit(self, client, draft_transfer, uploaded_file, settings):
        _active(draft_transfer)
        _enable(settings, 'FT_DOWNLOAD_IP', limit=1)

        client.get(reverse('t:download', args=[draft_transfer.slug]))
        response = client.get(reverse('t:download', args=[draft_transfer.slug]))

        assert response.status_code == 429
        assert 'Retry-After' in response


class TestUnlockRateLimit:
    def test_429_after_ip_limit(self, client, draft_transfer, settings):
        _active(draft_transfer, password_hash=password_service.hash_password('sekret'))
        _enable(settings, 'FT_UNLOCK_IP', limit=1)

        url = reverse('t:unlock', args=[draft_transfer.slug])
        client.post(url, {'password': 'wrong'})
        response = client.post(url, {'password': 'wrong'})

        assert response.status_code == 429
        assert b'Too many attempts' in response.content

    def test_429_after_per_transfer_limit_even_from_different_ips(
        self, client, draft_transfer, settings
    ):
        """`FT_UNLOCK_TRANSFER` (issue #53 code review: looser, counts only wrong passwords) --
        brute-forcing one transfer's password from many different IPs still trips it."""
        _active(draft_transfer, password_hash=password_service.hash_password('sekret'))
        _enable(settings, 'FT_UNLOCK_TRANSFER', limit=1)
        settings.RATELIMIT_RULES = {**settings.RATELIMIT_RULES, 'FT_UNLOCK_IP': (1000, 60)}

        url = reverse('t:unlock', args=[draft_transfer.slug])
        client.post(url, {'password': 'wrong'}, REMOTE_ADDR='10.0.0.1')
        response = client.post(url, {'password': 'wrong'}, REMOTE_ADDR='10.0.0.2')

        assert response.status_code == 429

    def test_correct_password_still_works_under_the_limit(self, client, draft_transfer, settings):
        _active(draft_transfer, password_hash=password_service.hash_password('sekret'))
        _enable(settings, 'FT_UNLOCK_IP', limit=5)

        response = client.post(
            reverse('t:unlock', args=[draft_transfer.slug]), {'password': 'sekret'}
        )

        assert response.status_code == 302

    def test_per_transfer_ceiling_ignores_correct_password_attempts(
        self, client, draft_transfer, settings
    ):
        """Issue #53 code review: `FT_UNLOCK_TRANSFER` must count only *wrong* passwords -- a
        third party who merely knows (or guesses) a transfer's slug must not be able to lock the
        real recipient out of a transfer whose password *they* actually have, just by submitting
        the correct one repeatedly (this also proves entering the right password on the first try
        doesn't itself burn the budget)."""
        _active(draft_transfer, password_hash=password_service.hash_password('sekret'))
        _enable(settings, 'FT_UNLOCK_TRANSFER', limit=1)
        settings.RATELIMIT_RULES = {**settings.RATELIMIT_RULES, 'FT_UNLOCK_IP': (1000, 60)}

        url = reverse('t:unlock', args=[draft_transfer.slug])
        for _ in range(3):
            response = client.post(url, {'password': 'sekret'}, REMOTE_ADDR='10.0.0.1')
            assert response.status_code == 302

    def test_per_transfer_ceiling_still_blocks_after_enough_wrong_passwords(
        self, client, draft_transfer, settings
    ):
        _active(draft_transfer, password_hash=password_service.hash_password('sekret'))
        _enable(settings, 'FT_UNLOCK_TRANSFER', limit=1)
        settings.RATELIMIT_RULES = {**settings.RATELIMIT_RULES, 'FT_UNLOCK_IP': (1000, 60)}

        url = reverse('t:unlock', args=[draft_transfer.slug])
        client.post(url, {'password': 'wrong'}, REMOTE_ADDR='10.0.0.1')

        # The one wrong attempt used up the budget; even the *correct* password is now refused
        # until the window resets (the accepted trade-off of a looser, shared-across-IPs ceiling
        # -- see `apps.accounts.services.login_throttle`'s module docstring for the same idea
        # applied to login).
        response = client.post(url, {'password': 'sekret'}, REMOTE_ADDR='10.0.0.1')
        assert response.status_code == 429


class TestManageLinkRateLimit:
    def test_manage_page_429_after_ip_limit(self, client, settings):
        transfer = Transfer.objects.create(
            owner=None, sender_email='sender@example.com', status=TransferStatus.ACTIVE
        )
        _enable(settings, 'FT_MANAGE_IP', limit=1)
        url = f'/t/{transfer.slug}/manage/{transfer.manage_token}/'

        client.get(url)
        response = client.get(url)

        assert response.status_code == 429


class TestRecipientEmailRateLimit:
    """Resend and add-recipients email the sender's message to a third party, so they share one
    per-user budget (`FT_RECIPIENT_EMAIL_USER`) across the dashboard and `/api/ft/`."""

    @pytest.fixture
    def active(self, draft_transfer):
        from ..models import TransferRecipient

        _active(draft_transfer)
        recipient = TransferRecipient.objects.create(transfer=draft_transfer, email='a@example.com')
        return draft_transfer, recipient

    @pytest.fixture(autouse=True)
    def no_cooldown(self, monkeypatch):
        """The per-recipient resend cooldown has its own tests below; here it would get in the
        way of resending the same recipient to exercise the per-user budget."""
        from ..services import actions

        monkeypatch.setattr(actions, 'RESEND_COOLDOWN', timedelta(0))

    @pytest.fixture
    def queued(self, monkeypatch):
        from ..services import actions

        calls: list[tuple] = []
        fake_task = SimpleNamespace(enqueue=lambda *args, **kwargs: calls.append(args))
        monkeypatch.setattr(actions, 'send_transfer_notification', fake_task)
        return calls

    def test_web_resend_429_after_user_limit_and_no_email_queued(
        self, client, active, queued, settings
    ):
        transfer, recipient = active
        _enable(settings, 'FT_RECIPIENT_EMAIL_USER', limit=2)
        client.force_login(transfer.owner)
        url = reverse('file_transfer:resend-recipient', args=[transfer.id, recipient.id])
        hx = {'HTTP_HX_REQUEST': 'true'}

        assert client.post(url, **hx).status_code == 200
        assert client.post(url, **hx).status_code == 200
        response = client.post(url, **hx)

        assert response.status_code == 429
        assert 'Retry-After' in response
        assert 'a@example.com' in response.content.decode()  # the row is kept, not replaced
        assert len(queued) == 2

    def test_web_non_htmx_gets_the_429_page(self, client, active, queued, settings):
        transfer, recipient = active
        _enable(settings, 'FT_RECIPIENT_EMAIL_USER', limit=1)
        client.force_login(transfer.owner)
        url = reverse('file_transfer:resend-recipient', args=[transfer.id, recipient.id])

        client.post(url)
        response = client.post(url)

        assert response.status_code == 429
        assert len(queued) == 1

    def test_api_resend_429_after_user_limit_and_no_email_queued(
        self, client, active, queued, settings, funded_user
    ):
        from apps.accounts.tokens import CustomAccessToken

        transfer, recipient = active
        _enable(settings, 'FT_RECIPIENT_EMAIL_USER', limit=2)
        headers = {'HTTP_AUTHORIZATION': f'Bearer {CustomAccessToken.for_user(funded_user)}'}
        url = f'/api/ft/transfers/{transfer.id}/recipients/{recipient.id}/resend'

        assert client.post(url, **headers).status_code == 200
        assert client.post(url, **headers).status_code == 200
        response = client.post(url, **headers)

        assert response.status_code == 429
        assert 'Retry-After' in response
        assert len(queued) == 2

    def test_add_recipients_shares_the_budget_across_web_and_api(
        self, client, active, queued, settings, funded_user
    ):
        from apps.accounts.tokens import CustomAccessToken

        transfer, recipient = active
        _enable(settings, 'FT_RECIPIENT_EMAIL_USER', limit=2)
        client.force_login(transfer.owner)
        hx = {'HTTP_HX_REQUEST': 'true'}

        # One resend and one add on the web spend the whole budget ...
        client.post(
            reverse('file_transfer:resend-recipient', args=[transfer.id, recipient.id]), **hx
        )
        web_add = client.post(
            reverse('file_transfer:add-recipients', args=[transfer.id]),
            data={'recipients': 'b@example.com'},
            **hx,
        )
        assert web_add.status_code == 200
        assert len(queued) == 2

        # ... so adding through the API, or on the web, is now refused with nothing queued.
        headers = {'HTTP_AUTHORIZATION': f'Bearer {CustomAccessToken.for_user(funded_user)}'}
        api_add = client.post(
            f'/api/ft/transfers/{transfer.id}/recipients',
            data=json.dumps({'recipients': ['c@example.com']}),
            content_type='application/json',
            **headers,
        )
        web_add_again = client.post(
            reverse('file_transfer:add-recipients', args=[transfer.id]),
            data={'recipients': 'd@example.com'},
            **hx,
        )
        assert api_add.status_code == 429
        assert web_add_again.status_code == 429
        assert len(queued) == 2
        assert not transfer.recipients.filter(email__in=['c@example.com', 'd@example.com']).exists()

    def test_invalid_add_does_not_spend_the_budget(self, client, active, queued, settings):
        transfer, recipient = active
        _enable(settings, 'FT_RECIPIENT_EMAIL_USER', limit=1)
        client.force_login(transfer.owner)
        hx = {'HTTP_HX_REQUEST': 'true'}

        invalid = client.post(
            reverse('file_transfer:add-recipients', args=[transfer.id]),
            data={'recipients': 'not-an-email'},
            **hx,
        )
        assert invalid.status_code == 422

        resend = client.post(
            reverse('file_transfer:resend-recipient', args=[transfer.id, recipient.id]), **hx
        )
        assert resend.status_code == 200

    def test_add_recipients_is_charged_per_new_address(self, client, active, queued, settings):
        transfer, _recipient = active
        _enable(settings, 'FT_RECIPIENT_EMAIL_USER', limit=3)
        client.force_login(transfer.owner)
        hx = {'HTTP_HX_REQUEST': 'true'}
        url = reverse('file_transfer:add-recipients', args=[transfer.id])

        # Two new addresses (the existing one is skipped and costs nothing) spend 2 of 3 ...
        first = client.post(
            url, data={'recipients': 'a@example.com, b@example.com, c@example.com'}, **hx
        )
        assert first.status_code == 200
        assert len(queued) == 2

        # ... so two more don't fit: refused as a whole, with nothing created or queued.
        second = client.post(url, data={'recipients': 'd@example.com, e@example.com'}, **hx)
        assert second.status_code == 429
        assert len(queued) == 2
        assert not transfer.recipients.filter(email__in=['d@example.com', 'e@example.com']).exists()

    def test_web_429_says_how_long_to_wait(self, client, active, queued, settings):
        transfer, recipient = active
        _enable(settings, 'FT_RECIPIENT_EMAIL_USER', limit=1, window=3600)
        client.force_login(transfer.owner)
        url = reverse('file_transfer:resend-recipient', args=[transfer.id, recipient.id])
        hx = {'HTTP_HX_REQUEST': 'true'}

        client.post(url, **hx)
        response = client.post(url, **hx)

        assert response.status_code == 429
        # 2,600 s left of the hourly window: the message and the header say the same thing.
        assert response['Retry-After'] == '2600'
        assert 'Too many requests. Please try again in 44 minutes.' in response.content.decode()

    def test_api_429_says_how_long_to_wait(self, client, active, queued, settings, funded_user):
        from apps.accounts.tokens import CustomAccessToken

        transfer, recipient = active
        _enable(settings, 'FT_RECIPIENT_EMAIL_USER', limit=1, window=3600)
        headers = {'HTTP_AUTHORIZATION': f'Bearer {CustomAccessToken.for_user(funded_user)}'}
        url = f'/api/ft/transfers/{transfer.id}/recipients/{recipient.id}/resend'

        client.post(url, **headers)
        response = client.post(url, **headers)

        assert response.status_code == 429
        assert response['Retry-After'] == '2600'
        assert response.json()['detail'] == 'Too many requests. Please try again in 44 minutes.'

    def test_wait_shrinks_with_the_clock_and_ends_with_the_window(
        self, client, active, queued, settings, ratelimit_clock
    ):
        transfer, recipient = active
        _enable(settings, 'FT_RECIPIENT_EMAIL_USER', limit=1, window=3600)
        client.force_login(transfer.owner)
        url = reverse('file_transfer:resend-recipient', args=[transfer.id, recipient.id])
        hx = {'HTTP_HX_REQUEST': 'true'}

        client.post(url, **hx)
        ratelimit_clock.advance(600)
        response = client.post(url, **hx)
        assert response['Retry-After'] == '2000'
        assert 'try again in 34 minutes.' in response.content.decode()

        ratelimit_clock.advance(2000)  # the window is over: a fresh budget
        assert client.post(url, **hx).status_code == 200
        assert len(queued) == 2

    def test_api_refusals_do_not_spend_the_budget(
        self, client, active, queued, settings, funded_user
    ):
        """An invalid address, an over-cap list or a held transfer is turned away before the
        budget is charged -- the same as the web form -- so the one hit left is still there."""
        from apps.accounts.tokens import CustomAccessToken

        transfer, recipient = active
        _enable(settings, 'FT_RECIPIENT_EMAIL_USER', limit=1)
        headers = {'HTTP_AUTHORIZATION': f'Bearer {CustomAccessToken.for_user(funded_user)}'}
        add_url = f'/api/ft/transfers/{transfer.id}/recipients'

        def add(recipients):
            return client.post(
                add_url,
                data=json.dumps({'recipients': recipients}),
                content_type='application/json',
                **headers,
            )

        assert add(['not-an-email']).status_code == 400
        assert add([f'u{i}@example.com' for i in range(21)]).status_code == 400
        transfer.held_for_review_at = timezone.now()
        transfer.save()
        assert add(['b@example.com']).status_code == 409

        transfer.held_for_review_at = None
        transfer.save()
        assert add(['b@example.com']).status_code == 200  # the budget was still whole
        assert add(['c@example.com']).status_code == 429
        assert len(queued) == 1

    def test_held_transfer_is_refused_with_409_even_when_the_budget_is_spent(
        self, client, active, queued, settings, funded_user
    ):
        """Pins the order: the service's refusals (409 held) come before the budget (429)."""
        from apps.accounts.tokens import CustomAccessToken

        transfer, recipient = active
        _enable(settings, 'FT_RECIPIENT_EMAIL_USER', limit=1)
        headers = {'HTTP_AUTHORIZATION': f'Bearer {CustomAccessToken.for_user(funded_user)}'}
        resend_url = f'/api/ft/transfers/{transfer.id}/recipients/{recipient.id}/resend'
        assert client.post(resend_url, **headers).status_code == 200  # spends the only hit
        assert client.post(resend_url, **headers).status_code == 429

        transfer.held_for_review_at = timezone.now()
        transfer.save()

        assert client.post(resend_url, **headers).status_code == 409
        assert (
            client.post(
                f'/api/ft/transfers/{transfer.id}/recipients',
                data=json.dumps({'recipients': ['b@example.com']}),
                content_type='application/json',
                **headers,
            ).status_code
            == 409
        )
        assert len(queued) == 1

    def test_web_held_transfer_is_refused_with_422_even_when_the_budget_is_spent(
        self, client, active, queued, settings
    ):
        transfer, recipient = active
        _enable(settings, 'FT_RECIPIENT_EMAIL_USER', limit=1)
        client.force_login(transfer.owner)
        url = reverse('file_transfer:resend-recipient', args=[transfer.id, recipient.id])
        hx = {'HTTP_HX_REQUEST': 'true'}
        assert client.post(url, **hx).status_code == 200
        assert client.post(url, **hx).status_code == 429

        transfer.held_for_review_at = timezone.now()
        transfer.save()

        assert client.post(url, **hx).status_code == 422
        assert len(queued) == 1


class TestRecipientResendCooldown:
    """The same recipient can't be emailed again until `RESEND_COOLDOWN` has passed, whichever
    surface asks, and a refused resend spends none of the per-user budget."""

    @pytest.fixture
    def active(self, draft_transfer):
        from ..models import TransferRecipient

        _active(draft_transfer)
        recipient = TransferRecipient.objects.create(
            transfer=draft_transfer, email='a@example.com', last_sent_at=timezone.now()
        )
        return draft_transfer, recipient

    @pytest.fixture
    def queued(self, monkeypatch):
        from ..services import actions

        calls: list[tuple] = []
        fake_task = SimpleNamespace(enqueue=lambda *args, **kwargs: calls.append(args))
        monkeypatch.setattr(actions, 'send_transfer_notification', fake_task)
        return calls

    def _age(self, recipient):
        from ..services.actions import RESEND_COOLDOWN

        recipient.last_sent_at = timezone.now() - RESEND_COOLDOWN - timedelta(seconds=1)
        recipient.save()

    def test_web_resend_refused_then_allowed_and_budget_untouched(
        self, client, active, queued, settings
    ):
        transfer, recipient = active
        _enable(settings, 'FT_RECIPIENT_EMAIL_USER', limit=1)
        client.force_login(transfer.owner)
        url = reverse('file_transfer:resend-recipient', args=[transfer.id, recipient.id])
        hx = {'HTTP_HX_REQUEST': 'true'}

        for _ in range(3):
            refused = client.post(url, **hx)
            assert refused.status_code == 422
            assert 'emailed recently' in refused.content.decode()
        assert queued == []

        self._age(recipient)
        assert client.post(url, **hx).status_code == 200  # the single hit was still there
        assert len(queued) == 1
        assert client.post(url, **hx).status_code == 422  # cooldown again, not a 429

    def test_api_resend_refused_then_allowed_and_budget_untouched(
        self, client, active, queued, settings, funded_user
    ):
        from apps.accounts.tokens import CustomAccessToken

        transfer, recipient = active
        _enable(settings, 'FT_RECIPIENT_EMAIL_USER', limit=1)
        headers = {'HTTP_AUTHORIZATION': f'Bearer {CustomAccessToken.for_user(funded_user)}'}
        url = f'/api/ft/transfers/{transfer.id}/recipients/{recipient.id}/resend'

        for _ in range(3):
            refused = client.post(url, **headers)
            assert refused.status_code == 400
            assert 'emailed recently' in refused.json()['detail']
        assert queued == []

        self._age(recipient)
        assert client.post(url, **headers).status_code == 200
        assert len(queued) == 1
