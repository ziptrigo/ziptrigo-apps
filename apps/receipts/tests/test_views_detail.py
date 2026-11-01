"""A receipt's page and the actions on it (`views/detail.py`), including the email body view."""

from datetime import date, timedelta
from decimal import Decimal

import pytest
from django.contrib.messages import get_messages
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from apps.billing.services import get_balance, spend_credits

from ..models import Receipt, ReceiptsSettings, ReceiptStatus
from ..services.accounts import get_account
from ..views.detail import BODY_CSP
from . import samples
from .factories import create_receipt

pytestmark = [pytest.mark.django_db, pytest.mark.unit]

HX = {'HTTP_HX_REQUEST': 'true'}


@pytest.fixture
def processed(make_receipt):
    return make_receipt(
        status=ReceiptStatus.PROCESSED,
        vendor='Staples',
        amount=Decimal('42.10'),
        currency='USD',
        receipt_date=date(2026, 10, 9),
        document_type='receipt',
        document_number='R-55102',
        payment_method='Visa ••4242',
        notes='toner',
        original_filename='staples.jpg',
    )


def url(name, receipt):
    return reverse(f'receipts:{name}', args=[receipt.pk])


def flashed(response) -> list[str]:
    return [str(m) for m in get_messages(response.wsgi_request)]


class TestAccess:
    @pytest.mark.parametrize(
        ('method', 'name'),
        [
            ('get', 'detail'),
            ('post', 'detail'),
            ('get', 'body'),
            ('get', 'download'),
            ('post', 'reprocess'),
            ('post', 'trash-receipt'),
            ('post', 'dismiss-duplicate'),
        ],
    )
    def test_anonymous_users_are_sent_to_login(self, client, processed, method, name):
        response = getattr(client, method)(url(name, processed))

        assert response.status_code == 302
        assert response['Location'].startswith(reverse('accounts:login'))

    @pytest.mark.parametrize(
        ('method', 'name'),
        [
            ('get', 'detail'),
            ('post', 'detail'),
            ('get', 'body'),
            ('get', 'download'),
            ('post', 'reprocess'),
            ('post', 'trash-receipt'),
            ('post', 'restore'),
            ('post', 'delete'),
            ('post', 'dismiss-duplicate'),
        ],
    )
    def test_another_users_receipt_is_a_404_never_a_403(
        self, web, other_user, fake_storage, method, name
    ):
        theirs = create_receipt(
            other_user,
            fake_storage,
            status=ReceiptStatus.PROCESSED,
            vendor='Theirs',
            deleted_at=timezone.now(),
            data=samples.HTML_BODY,
        )

        response = getattr(web, method)(url(name, theirs))

        assert response.status_code == 404
        assert Receipt.objects.filter(pk=theirs.pk, deleted_at__isnull=False).exists()

    def test_an_unfinished_upload_is_a_404(self, web, make_receipt):
        receipt = make_receipt(uploaded=False)

        assert web.get(url('detail', receipt)).status_code == 404

    def test_an_unknown_id_is_a_404(self, web):
        assert (
            web.get(
                reverse('receipts:detail', args=['00000000-0000-0000-0000-000000000000'])
            ).status_code
            == 404
        )


class TestPage:
    def test_a_processed_receipt_shows_the_preview_the_form_and_the_details(self, web, processed):
        response = web.get(url('detail', processed))
        content = response.content.decode()

        assert response.status_code == 200
        assert '<title>Staples - ZipTrigo</title>' in content
        assert 'Processed' in content
        assert 'value="Staples"' in content and 'value="2026-10-09"' in content
        assert 'value="42.10"' in content
        assert '<option value="USD" selected>' in content
        assert 'name="document_type" value="receipt" checked' in content
        assert 'value="R-55102"' in content and 'value="Visa ••4242"' in content
        assert 'Notes (yours)' in content and '>toner</textarea>' in content
        assert 'Added ' in content and '(receipt dated Oct 9)' in content
        assert 'Source: uploaded' in content and 'staples.jpg' in content
        assert 'Download original' in content and url('download', processed) in content
        assert 'Re-run extraction (1 credit)' in content
        assert 'Move to trash' in content
        assert 'frame-src' not in response.get('Content-Security-Policy', '')

    def test_re_run_asks_in_the_confirmation_modal_naming_the_cost(self, web, processed):
        content = web.get(url('detail', processed)).content.decode()

        assert 'hx-confirm' not in content  # not the browser's native dialog
        assert 'x-data="confirmModal"' in content and 'id="confirm-modal-title"' in content
        assert f'data-url="{url("reprocess", processed)}"' in content
        assert 'data-title="Run the extraction again?"' in content
        assert 'This costs 1 credit.' in content
        assert 'anything you edited and your notes are kept' in content
        assert 'data-label="Re-run extraction (1 credit)"' in content
        assert 'data-tone="primary"' in content

    def test_a_free_re_run_says_so(self, web, processed, receipts_settings):
        ReceiptsSettings.objects.filter(pk=1).update(price_per_file=0)

        content = web.get(url('detail', processed)).content.decode()

        assert 'This is free.' in content and 'data-label="Re-run extraction"' in content

    def test_a_failed_receipt_re_runs_through_the_same_modal(self, web, make_receipt):
        receipt = make_receipt(status=ReceiptStatus.FAILED, failure_reason='not_a_receipt')

        content = web.get(url('detail', receipt)).content.decode()

        assert 'hx-confirm' not in content and 'x-data="confirmModal"' in content
        assert f'data-url="{url("reprocess", receipt)}"' in content
        assert 'This costs 1 credit.' in content

    def test_an_image_is_previewed_through_a_presigned_inline_url(
        self, web, processed, fake_storage
    ):
        content = web.get(url('detail', processed)).content.decode()

        assert (
            f'src="https://fake-s3.test/{processed.file_key}?get&amp;disposition=inline"' in content
        )
        assert fake_storage.get_urls[-1]['disposition'] == 'inline'

    def test_a_pdf_is_previewed_in_a_frame(self, web, make_receipt):
        receipt = make_receipt(
            status=ReceiptStatus.PROCESSED, vendor='Delta', data=samples.text_pdf_bytes()
        )

        content = web.get(url('detail', receipt)).content.decode()

        assert '<iframe src="https://fake-s3.test/' in content
        assert 'sandbox' not in content.split('<iframe')[1].split('>')[0]

    def test_a_heic_original_is_previewed_through_its_jpeg(self, web, make_receipt, fake_storage):
        receipt = make_receipt(
            status=ReceiptStatus.PROCESSED,
            vendor='Phone',
            data=samples.heic_bytes(),
            preview_key='receipts/users/x/previews/p.jpg',
        )

        web.get(url('detail', receipt))

        assert fake_storage.get_urls[-1]['key'] == 'receipts/users/x/previews/p.jpg'
        assert fake_storage.get_urls[-1]['content_type'] == 'image/jpeg'

    def test_a_heic_without_its_preview_yet_says_so(self, web, make_receipt):
        receipt = make_receipt(
            status=ReceiptStatus.NEEDS_REVIEW, data=samples.heic_bytes(), original_filename='a.heic'
        )

        content = web.get(url('detail', receipt)).content.decode()

        assert 'preview is not ready yet' in content

    def test_an_email_body_is_shown_in_a_sandboxed_frame_with_no_permissions(
        self, web, make_receipt
    ):
        receipt = make_receipt(
            status=ReceiptStatus.PROCESSED,
            vendor='Shop',
            data=samples.HTML_BODY,
            original_filename='',
            source='email',
        )

        response = web.get(url('detail', receipt))
        content = response.content.decode()

        frame = content.split('<iframe sandbox')[1].split('>')[0]
        assert f'src="{url("body", receipt)}"' in frame
        # A bare `sandbox` attribute: not one of the allow-* tokens (scripts, forms, popups,
        # same-origin, top-navigation...) is present.
        assert '<iframe sandbox src=' in content
        assert 'allow-' not in content.split('<iframe sandbox')[1].split('</iframe>')[0]
        assert response['Content-Security-Policy'] == "frame-src 'self'"

    def test_a_missing_required_field_is_marked_with_an_amber_border_and_a_hint(
        self, web, make_receipt
    ):
        receipt = make_receipt(
            status=ReceiptStatus.NEEDS_REVIEW, vendor='Staples', receipt_date=date(2026, 10, 9)
        )

        content = web.get(url('detail', receipt)).content.decode()

        assert "couldn't read the amount" in content
        assert 'id="amount-hint"' in content
        assert 'border-amber-600' in content
        assert "couldn't read the vendor" not in content
        assert content.count('aria-hidden="true">*</span>') == 3

    def test_nothing_is_flagged_while_the_receipt_is_still_being_read(self, web, make_receipt):
        receipt = make_receipt(status=ReceiptStatus.RECEIVED)

        content = web.get(url('detail', receipt)).content.decode()

        assert "couldn't read" not in content

    def test_an_email_receipt_names_the_sender_and_subject(self, web, funded_user, make_receipt):
        from .factories import create_inbound_email

        email = create_inbound_email(
            funded_user, from_address='orders@staples.com', subject='Your <b>Staples</b> receipt'
        )
        receipt = make_receipt(
            status=ReceiptStatus.PROCESSED, vendor='Staples', source='email', inbound_email=email
        )

        content = web.get(url('detail', receipt)).content.decode()

        assert 'Source: email from' in content and 'orders@staples.com' in content
        assert '&ldquo;Your &lt;b&gt;Staples&lt;/b&gt; receipt&rdquo;' in content

    def test_everything_the_sender_controls_is_escaped(self, web, make_receipt):
        receipt = make_receipt(
            status=ReceiptStatus.PROCESSED,
            vendor='<script>alert(1)</script>',
            notes='</textarea><script>alert(2)</script>',
            original_filename='"><img src=x onerror=alert(3)>',
        )

        content = web.get(url('detail', receipt)).content.decode()

        assert '<script>alert' not in content and '<img src=x' not in content

    def test_the_trash_banner_and_a_read_only_form(self, web, processed):
        Receipt.objects.filter(pk=processed.pk).update(deleted_at=timezone.now())

        content = web.get(url('detail', processed)).content.decode()

        assert 'in the Trash' in content and url('restore', processed) in content
        assert '<fieldset disabled' in content
        assert 'Move to trash' not in content and 'Re-run extraction' not in content

    def test_a_frozen_account_gets_no_preview_and_no_download(
        self, web, processed, freeze, fake_storage
    ):
        content = web.get(url('detail', processed)).content.decode()

        assert 'Previews and downloads are paused' in content
        assert 'Download original' not in content
        assert fake_storage.get_urls == []
        assert 'Storage is paused' in content


class TestStates:
    def test_a_failed_receipt_shows_why_and_what_to_do_instead_of_the_form(self, web, make_receipt):
        receipt = make_receipt(
            status=ReceiptStatus.FAILED,
            failure_reason='multiple_receipts',
            original_filename='scan.pdf',
        )

        content = web.get(url('detail', receipt)).content.decode()

        assert (
            'more than one receipt' in content and 'Upload each receipt as its own file' in content
        )
        assert 'id="receipt-form"' not in content
        assert 'Re-run extraction' in content and 'Move to trash' in content
        assert 'Download original' in content

    @pytest.mark.parametrize('reason', ['unsupported_type', 'too_large'])
    def test_some_failures_cannot_be_re_run(self, web, make_receipt, reason):
        receipt = make_receipt(status=ReceiptStatus.FAILED, failure_reason=reason)

        content = web.get(url('detail', receipt)).content.decode()

        assert 'Re-run extraction' not in content and 'Move to trash' in content

    def test_a_refund_is_mentioned(self, web, make_receipt):
        receipt = make_receipt(
            status=ReceiptStatus.FAILED,
            failure_reason='extraction_error',
            refunded_at=timezone.now(),
        )

        assert (
            'credits for this attempt were returned'
            in web.get(url('detail', receipt)).content.decode()
        )

    @pytest.mark.parametrize('status', [ReceiptStatus.RECEIVED, ReceiptStatus.PROCESSING])
    def test_a_receipt_being_read_polls_until_it_settles(self, web, make_receipt, status):
        receipt = make_receipt(status=status)

        content = web.get(url('detail', receipt)).content.decode()
        capped = web.get(url('detail', receipt), {'polls': 40}).content.decode()

        assert 'Reading your receipt' in content
        assert 'hx-trigger="every 3s"' in content and f'{url("detail", receipt)}?polls=1' in content
        assert 'hx-select="#receipt-panel"' in content
        assert 'id="receipt-form"' not in content
        assert 'every 3s' not in capped and 'taking longer than usual' in capped

    def test_a_scheduled_retry_says_so_and_does_not_poll(self, web, make_receipt):
        receipt = make_receipt(
            status=ReceiptStatus.RECEIVED,
            attempts=1,
            next_attempt_at=timezone.now() + timedelta(minutes=30),
        )

        content = web.get(url('detail', receipt)).content.decode()

        assert 'Trying again shortly' in content and 'Reading your receipt' not in content
        assert 'every 3s' not in content

    def test_waiting_for_credits_does_not_poll_and_offers_a_top_up(self, web, make_receipt):
        receipt = make_receipt(status=ReceiptStatus.WAITING_FOR_CREDITS)

        content = web.get(url('detail', receipt)).content.decode()

        assert 'Waiting for credits' in content and 'every 3s' not in content
        assert reverse('billing:credits-history') in content

    def test_a_possible_duplicate_banner_links_to_the_original(self, web, processed, make_receipt):
        dup = make_receipt(
            status=ReceiptStatus.PROCESSED,
            vendor='Staples',
            amount=Decimal('42.10'),
            currency='USD',
            receipt_date=date(2026, 10, 9),
            possible_duplicate_of=processed,
        )

        content = web.get(url('detail', dup)).content.decode()

        assert 'Possible duplicate' in content and 'Staples, 2026-10-09, USD 42.10' in content
        assert f'href="{url("detail", processed)}"' in content
        assert 'Not a duplicate' in content and url('dismiss-duplicate', dup) in content

    def test_a_trashed_duplicate_keeps_the_banner_without_the_button(
        self, web, processed, make_receipt
    ):
        dup = make_receipt(
            status=ReceiptStatus.PROCESSED,
            possible_duplicate_of=processed,
            deleted_at=timezone.now(),
        )

        content = web.get(url('detail', dup)).content.decode()

        assert 'Possible duplicate' in content
        assert url('dismiss-duplicate', dup) not in content

    def test_a_dismissed_duplicate_shows_no_banner(self, web, processed, make_receipt):
        dup = make_receipt(
            status=ReceiptStatus.PROCESSED,
            possible_duplicate_of=processed,
            duplicate_dismissed=True,
        )

        assert 'Possible duplicate' not in web.get(url('detail', dup)).content.decode()


class TestSave:
    def post(self, web, receipt, **fields):
        data = {
            'vendor': receipt.vendor,
            'receipt_date': receipt.receipt_date.isoformat() if receipt.receipt_date else '',
            'amount': '' if receipt.amount is None else str(receipt.amount),
            'currency': receipt.currency,
            'document_type': receipt.document_type,
            'document_number': receipt.document_number,
            'payment_method': receipt.payment_method,
            'category': receipt.category_id or '',
            'notes': receipt.notes,
        }
        data.update(fields)
        return web.post(url('detail', receipt), data, **fields.pop('headers', {}))

    def test_saving_redirects_back_with_a_flash_and_updates_the_receipt(self, web, processed):
        response = self.post(web, processed, vendor='Staples Inc', notes='new notes')

        assert response.status_code == 302 and response['Location'] == url('detail', processed)
        assert flashed(response) == ['Saved.']
        processed.refresh_from_db()
        assert processed.vendor == 'Staples Inc' and processed.notes == 'new notes'
        assert processed.user_edited_fields == ['vendor']  # notes are the user's own, untracked

    def test_over_htmx_it_answers_with_an_hx_redirect(self, web, processed):
        response = web.post(
            url('detail', processed),
            {'vendor': 'Staples Inc', 'receipt_date': '2026-10-09', 'amount': '42.10'},
            **HX,
        )

        assert response.status_code == 200
        assert response['HX-Redirect'] == url('detail', processed)

    def test_a_decimal_comma_amount_is_saved_as_a_decimal(self, web, processed):
        self.post(web, processed, amount='12,50')

        processed.refresh_from_db()
        assert processed.amount == Decimal('12.50')

    def test_filling_the_missing_fields_moves_needs_review_to_processed(self, web, make_receipt):
        receipt = make_receipt(status=ReceiptStatus.NEEDS_REVIEW, vendor='Staples')

        self.post(web, receipt, receipt_date='2026-10-09', amount='42.10')

        receipt.refresh_from_db()
        assert receipt.status == ReceiptStatus.PROCESSED
        assert set(receipt.user_edited_fields) == {'receipt_date', 'amount'}

    def test_a_category_can_be_set_and_cleared(self, web, processed, funded_user):
        get_account(funded_user)
        travel = funded_user.receipt_categories.get(name='Travel')

        self.post(web, processed, category=str(travel.pk))
        processed.refresh_from_db()
        assert processed.category_id == travel.pk

        self.post(web, processed, category='')
        processed.refresh_from_db()
        assert processed.category_id is None

    def test_another_users_category_is_refused(self, web, processed, other_user):
        get_account(other_user)
        theirs = other_user.receipt_categories.first()

        response = web.post(
            url('detail', processed),
            {'vendor': 'Staples', 'category': str(theirs.pk)},
            **HX,
        )

        assert response.status_code == 422
        assert 'Unknown category' in response.content.decode()
        processed.refresh_from_db()
        assert processed.category_id is None

    @pytest.mark.parametrize(
        ('fields', 'message'),
        [
            ({'receipt_date': '09/10/2026'}, 'Receipt date: Enter a valid date.'),
            ({'amount': 'forty'}, 'Amount: Enter the amount as a number.'),
            ({'amount': '-5'}, 'Amount: The amount cannot be negative.'),
            ({'currency': 'ZZZ'}, 'Currency: Enter a 3-letter currency code'),
            ({'document_type': 'memo'}, 'Type: Choose receipt or invoice.'),
            ({'notes': 'x' * 5001}, 'Notes: Notes can be at most 5000 characters.'),
            ({'vendor': 'v' * 256}, 'Vendor: Vendor can be at most 255 characters.'),
        ],
    )
    def test_invalid_input_is_a_422_for_the_message_slot(self, web, processed, fields, message):
        response = web.post(url('detail', processed), {'vendor': 'Staples', **fields}, **HX)

        assert response.status_code == 422
        assert response['HX-Retarget'] == '#receipt-form-msg'
        assert response['HX-Reswap'] == 'innerHTML'
        assert message in response.content.decode()
        processed.refresh_from_db()
        assert processed.vendor == 'Staples' and processed.notes == 'toner'

    def test_several_errors_are_listed_one_per_line(self, web, processed):
        response = web.post(url('detail', processed), {'amount': 'x', 'receipt_date': 'nope'}, **HX)

        body = response.content.decode()
        assert response.status_code == 422
        assert body.count('<br>') == 2 and 'Amount' in body and 'Receipt date' in body

    def test_a_refused_save_without_htmx_flashes_the_errors_and_goes_back(self, web, processed):
        response = web.post(url('detail', processed), {'amount': 'x'})

        assert response.status_code == 302 and response['Location'] == url('detail', processed)
        assert any('Amount' in m for m in flashed(response))

    def test_a_receipt_still_being_read_cannot_be_edited(self, web, make_receipt):
        receipt = make_receipt(status=ReceiptStatus.PROCESSING)

        response = web.post(url('detail', receipt), {'vendor': 'Mine'}, **HX)

        assert response.status_code == 422 and 'still being processed' in response.content.decode()

    def test_a_trashed_receipt_cannot_be_edited(self, web, processed):
        Receipt.objects.filter(pk=processed.pk).update(deleted_at=timezone.now())

        response = web.post(url('detail', processed), {'vendor': 'Mine'}, **HX)

        assert response.status_code == 422 and 'Restore this receipt' in response.content.decode()

    def test_a_long_card_number_is_masked(self, web, processed):
        self.post(web, processed, payment_method='Visa 4111 1111 1111 1234')

        processed.refresh_from_db()
        assert '4111' not in processed.payment_method and '1234' in processed.payment_method


class TestActions:
    def test_re_run_charges_again_and_goes_back_to_the_page(self, web, funded_user, make_receipt):
        receipt = make_receipt(status=ReceiptStatus.NEEDS_REVIEW, vendor='Staples')
        before = ReceiptsSettings.load().price_per_file

        response = web.post(url('reprocess', receipt), **HX)

        assert response.status_code == 200 and response['HX-Redirect'] == url('detail', receipt)
        receipt.refresh_from_db()
        assert receipt.status == ReceiptStatus.RECEIVED
        assert flashed(response) and 'Reading the receipt again' in flashed(response)[0]
        assert before == 1

    def test_re_run_without_enough_credits_offers_a_top_up(self, web, funded_user, make_receipt):
        receipt = make_receipt(status=ReceiptStatus.FAILED, failure_reason='not_a_receipt')
        spend_credits(funded_user, get_balance(funded_user), description='x', source='test')

        response = web.post(url('reprocess', receipt), **HX)

        assert response.status_code == 422
        assert response['HX-Retarget'] == '#receipt-form-msg'
        body = response.content.decode()
        assert 'enough credits' in body and reverse('billing:credits-history') in body
        receipt.refresh_from_db()
        assert receipt.status == ReceiptStatus.FAILED

    def test_re_run_while_frozen_is_refused_gracefully(self, web, make_receipt, freeze):
        receipt = make_receipt(status=ReceiptStatus.NEEDS_REVIEW)

        response = web.post(url('reprocess', receipt), **HX)

        assert response.status_code == 422
        assert 'paused until you add credits' in response.content.decode()

    @pytest.mark.parametrize(
        ('status', 'reason', 'message'),
        [
            (ReceiptStatus.PROCESSING, '', 'already being processed'),
            (ReceiptStatus.FAILED, 'unsupported_type', 'cannot be processed again'),
        ],
    )
    def test_re_run_that_cannot_happen_says_why(self, web, make_receipt, status, reason, message):
        receipt = make_receipt(status=status, failure_reason=reason)

        response = web.post(url('reprocess', receipt), **HX)

        assert response.status_code == 422 and message in response.content.decode()

    def test_re_run_from_the_modal_is_a_plain_post_that_redirects_back(self, web, make_receipt):
        receipt = make_receipt(status=ReceiptStatus.NEEDS_REVIEW, vendor='Staples')

        response = web.post(url('reprocess', receipt))

        assert response.status_code == 302 and response['Location'] == url('detail', receipt)
        receipt.refresh_from_db()
        assert receipt.status == ReceiptStatus.RECEIVED
        assert any('Reading the receipt again' in m for m in flashed(response))

    def test_re_run_is_csrf_protected(self, funded_user, make_receipt):
        receipt = make_receipt(status=ReceiptStatus.NEEDS_REVIEW)
        client = Client(enforce_csrf_checks=True)
        client.force_login(funded_user)

        for extra in ({}, HX):
            response = client.post(url('reprocess', receipt), **extra)

            assert response.status_code == 403
        receipt.refresh_from_db()
        assert receipt.status == ReceiptStatus.NEEDS_REVIEW  # nothing was queued or charged

    def test_re_run_without_htmx_flashes_the_refusal(self, web, make_receipt):
        receipt = make_receipt(status=ReceiptStatus.PROCESSING)

        response = web.post(url('reprocess', receipt))

        assert response.status_code == 302
        assert any('already being processed' in m for m in flashed(response))

    def test_re_run_is_rate_limited(self, web, make_receipt, settings):
        settings.RATELIMIT_ENABLE = True
        settings.RATELIMIT_RULES = {
            **settings.RATELIMIT_RULES,
            'RECEIPTS_REPROCESS_USER': (1, 3600),
        }
        receipt = make_receipt(status=ReceiptStatus.NEEDS_REVIEW)

        assert web.post(url('reprocess', receipt), **HX).status_code == 200
        limited = web.post(url('reprocess', receipt), **HX)

        assert limited.status_code == 429
        assert limited['HX-Retarget'] == '#receipt-form-msg'

    def test_move_to_trash(self, web, processed):
        response = web.post(url('trash-receipt', processed), **HX)

        assert response['HX-Redirect'] == reverse('receipts:dashboard')
        processed.refresh_from_db()
        assert processed.deleted_at is not None
        assert 'Moved to the trash' in flashed(response)[0] and '30 days' in flashed(response)[0]

    def test_trash_needs_post(self, web, processed):
        assert web.get(url('trash-receipt', processed)).status_code == 405

    def test_not_a_duplicate_clears_the_banner_slot(self, web, processed, make_receipt):
        dup = make_receipt(status=ReceiptStatus.PROCESSED, possible_duplicate_of=processed)

        response = web.post(url('dismiss-duplicate', dup), **HX)

        assert response.status_code == 200
        assert response.content.decode().strip() == '<div id="duplicate-banner">\n    \n</div>'
        dup.refresh_from_db()
        assert dup.possible_duplicate_of_id is None and dup.duplicate_dismissed is True

    def test_not_a_duplicate_without_htmx_goes_back_to_the_page(self, web, processed, make_receipt):
        dup = make_receipt(status=ReceiptStatus.PROCESSED, possible_duplicate_of=processed)

        response = web.post(url('dismiss-duplicate', dup))

        assert response.status_code == 302 and response['Location'] == url('detail', dup)

    def test_download_redirects_to_a_short_lived_attachment_url(self, web, processed, fake_storage):
        response = web.get(url('download', processed))

        assert response.status_code == 302
        assert (
            response['Location']
            == f'https://fake-s3.test/{processed.file_key}?get&disposition=attachment'
        )
        assert response['Cache-Control'] == 'private, no-store'
        assert fake_storage.get_urls[-1]['filename'] == 'staples.jpg'

    def test_download_while_frozen_goes_back_with_a_message(self, web, processed, freeze):
        response = web.get(url('download', processed))

        assert response.status_code == 302 and response['Location'] == url('detail', processed)
        assert 'paused' in flashed(response)[0]

    def test_download_of_a_receipt_with_no_file_goes_back_with_a_message(self, web, make_receipt):
        receipt = make_receipt(
            status=ReceiptStatus.FAILED, failure_reason='unsupported_type', file_key=''
        )

        response = web.get(url('download', receipt))

        assert response.status_code == 302 and 'no file' in flashed(response)[0]


class TestBodyView:
    @pytest.fixture
    def body(self, make_receipt):
        return make_receipt(
            status=ReceiptStatus.PROCESSED,
            vendor='Shop',
            data=samples.HTML_BODY,
            original_filename='',
            source='email',
        )

    def test_the_html_is_served_with_the_locked_down_headers(self, web, body):
        response = web.get(url('body', body))

        assert response.status_code == 200
        assert response['Content-Type'] == 'text/html; charset=utf-8'
        assert response['Content-Security-Policy'].startswith(
            "default-src 'none'; img-src data:; style-src 'unsafe-inline'"
        )
        assert response['Content-Security-Policy'] == BODY_CSP
        assert "frame-ancestors 'self'" in BODY_CSP and "base-uri 'none'" in BODY_CSP
        # Opaque-origin even when the body URL is opened top-level, outside the iframe.
        assert response['Content-Security-Policy'].split('; ')[-1] == 'sandbox'
        assert response['X-Content-Type-Options'] == 'nosniff'
        assert response['Referrer-Policy'] == 'no-referrer'
        assert response['Cache-Control'] == 'private, no-store'
        assert response.content == samples.HTML_BODY

    def test_only_our_own_pages_may_frame_it(self, web, body):
        response = web.get(url('body', body))

        assert response['X-Frame-Options'] == 'SAMEORIGIN'
        assert "frame-ancestors 'self'" in response['Content-Security-Policy']

    def test_a_plain_text_body_is_escaped_into_a_pre(self, web, make_receipt):
        receipt = make_receipt(
            status=ReceiptStatus.PROCESSED,
            data=b'Total <b>42.10</b> & thanks\nSecond line',
            original_filename='',
            content_type='text/plain',
        )

        response = web.get(url('body', receipt))
        content = response.content.decode()

        assert response['Content-Type'] == 'text/html; charset=utf-8'
        assert response['Content-Security-Policy'] == BODY_CSP
        assert '<pre' in content
        assert 'Total &lt;b&gt;42.10&lt;/b&gt; &amp; thanks\nSecond line' in content
        assert '<b>' not in content

    def test_a_receipt_that_is_not_a_body_is_a_404(self, web, processed):
        assert web.get(url('body', processed)).status_code == 404

    def test_a_frozen_account_gets_a_notice_with_the_same_headers(self, web, body, freeze):
        response = web.get(url('body', body))

        assert response.status_code == 402
        assert 'paused' in response.content.decode()
        assert response['Content-Security-Policy'] == BODY_CSP
        assert response['X-Content-Type-Options'] == 'nosniff'
        assert b'<h1>' not in response.content

    def test_a_missing_object_is_a_404_notice(self, web, body, fake_storage):
        fake_storage.objects.pop(body.file_key)

        response = web.get(url('body', body))

        assert response.status_code == 404 and 'could not be loaded' in response.content.decode()
        assert response['X-Content-Type-Options'] == 'nosniff'

    def test_a_huge_body_is_a_413_notice(self, web, body, monkeypatch):
        monkeypatch.setattr('apps.receipts.services.files.MAX_BODY_BYTES', 10)

        response = web.get(url('body', body))

        assert response.status_code == 413 and 'too large' in response.content.decode()

    def test_it_is_a_get_only_view(self, web, body):
        assert web.post(url('body', body)).status_code == 405
