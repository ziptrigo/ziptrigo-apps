import pytest
from django.urls import reverse

from ..models import InboundMessage, InboundMessageStatus, ReceiptsSettings, ReceiptStatus
from ..services.accounts import get_account
from .factories import create_inbound_email, create_receipt, create_vendor, make_user

pytestmark = [pytest.mark.django_db, pytest.mark.unit]


def url(name, *args):
    return reverse(f'custom_admin:{name}', args=args)


class TestSettings:
    def test_changelist_goes_straight_to_the_singleton_form(self, client, admin_user):
        client.force_login(admin_user)

        response = client.get(url('receipts_receiptssettings_changelist'))

        assert response.status_code == 302
        assert client.get(response['Location']).status_code == 200

    def test_a_superuser_can_edit_it(self, client, admin_user):
        client.force_login(admin_user)
        row = ReceiptsSettings.load()

        response = client.post(
            url('receipts_receiptssettings_change', row.pk),
            {
                'price_per_file': 3,
                'max_file_bytes': 1000,
                'allowed_types': 'application/pdf',
                'min_inline_image_px': 100,
                'max_files_per_user_per_day': 7,
                'max_inbound_rows_per_day': 50,
                'trash_days': 10,
                'max_pdf_pages_to_ai': 2,
                'max_ai_attempts': 4,
            },
        )

        assert response.status_code == 302
        row.refresh_from_db()
        assert (row.price_per_file, row.trash_days, row.max_ai_attempts) == (3, 10, 4)
        assert row.max_inbound_rows_per_day == 50

    def test_forbidden_for_non_superuser_staff(self, client, regular_user):
        regular_user.is_staff = True
        regular_user.save()
        client.force_login(regular_user)

        row = ReceiptsSettings.load()

        assert client.get(url('receipts_receiptssettings_change', row.pk)).status_code == 403
        assert client.post(url('receipts_receiptssettings_change', row.pk), {}).status_code == 403


class TestReadOnlyModels:
    def test_receipt_list_and_filters(self, client, admin_user, funded_user, fake_storage):
        create_receipt(
            funded_user, fake_storage, status=ReceiptStatus.FAILED, failure_reason='unreadable'
        )
        client.force_login(admin_user)

        response = client.get(url('receipts_receipt_changelist'), {'status': 'failed'})

        assert response.status_code == 200
        assert b'unreadable' in response.content

    def test_receipt_page_shows_the_vendor_link_and_the_name_as_read(
        self, client, admin_user, funded_user, fake_storage
    ):
        acme = create_vendor(funded_user, 'ACME', vetted=True)
        receipt = create_receipt(
            funded_user,
            fake_storage,
            vendor='ACME',
            vendor_ref=acme,
            vendor_extracted='ACME Industries, LLC',
        )
        client.force_login(admin_user)

        page = client.get(url('receipts_receipt_change', receipt.pk)).content.decode()
        listing = client.get(url('receipts_receipt_changelist'), {'q': 'Industries, LLC'})

        assert 'ACME Industries, LLC' in page and 'ACME' in page
        assert str(receipt.pk) in listing.content.decode()  # searchable by the name as read

    def test_receipt_page_is_read_only(self, client, admin_user, funded_user, fake_storage):
        receipt = create_receipt(funded_user, fake_storage)
        client.force_login(admin_user)

        response = client.get(url('receipts_receipt_change', receipt.pk))

        assert response.status_code == 200
        assert 'name="_save"' not in response.content.decode()

    def test_receipt_cannot_be_added_changed_or_deleted(
        self, client, admin_user, funded_user, fake_storage
    ):
        receipt = create_receipt(funded_user, fake_storage)
        client.force_login(admin_user)

        assert client.get(url('receipts_receipt_add')).status_code == 403
        assert (
            client.post(url('receipts_receipt_delete', receipt.pk), {'post': 'yes'}).status_code
            == 403
        )
        assert (
            client.post(url('receipts_receipt_change', receipt.pk), {'vendor': 'x'}).status_code
            == 403
        )

    def test_account_page_shows_codes_and_frozen_state(self, client, admin_user, funded_user):
        account = get_account(funded_user)
        client.force_login(admin_user)

        response = client.get(url('receipts_receiptsaccount_change', account.pk))

        assert response.status_code == 200
        assert 'name="_save"' not in response.content.decode()
        assert b'stored_bytes' in response.content or b'Stored bytes' in response.content
        assert client.get(url('receipts_receiptsaccount_changelist')).status_code == 200

    def test_inbound_email_list(self, client, admin_user, funded_user):
        create_inbound_email(funded_user, status='rejected_spam')
        client.force_login(admin_user)

        response = client.get(url('receipts_inboundemail_changelist'), {'status': 'rejected_spam'})

        assert response.status_code == 200
        assert b'orders@staples.com' in response.content


class TestVendors:
    @pytest.fixture
    def vendors(self, funded_user, fake_storage):
        acme = create_vendor(funded_user, 'ACME', vetted=True, aliases=('ACME Industries, LLC',))
        create_vendor(funded_user, 'Blue Bottle')
        create_receipt(funded_user, fake_storage, vendor='ACME', vendor_ref=acme)
        return acme

    def test_the_list_shows_vetted_state_and_receipt_counts(self, client, admin_user, vendors):
        client.force_login(admin_user)

        response = client.get(url('receipts_vendor_changelist'))

        content = response.content.decode()
        assert response.status_code == 200
        assert 'ACME' in content and 'Blue Bottle' in content and 'testuser@example.com' in content

    def test_it_filters_by_vetted_and_searches_alias_names(self, client, admin_user, vendors):
        client.force_login(admin_user)

        unvetted = client.get(url('receipts_vendor_changelist'), {'vetted_at__isempty': '1'})
        by_alias = client.get(url('receipts_vendor_changelist'), {'q': 'Industries, LLC'})

        assert (
            b'Blue Bottle' in unvetted.content
            and b'ACME' not in unvetted.content.split(b'<tbody>')[1]
        )
        assert (
            b'ACME' in by_alias.content
            and b'Blue Bottle' not in by_alias.content.split(b'<tbody>')[1]
        )

    def test_the_page_lists_the_aliases_and_is_read_only(self, client, admin_user, vendors):
        client.force_login(admin_user)

        response = client.get(url('receipts_vendor_change', vendors.pk))

        content = response.content.decode()
        assert response.status_code == 200
        assert 'ACME Industries, LLC' in content and 'acme industries' in content
        assert 'name="_save"' not in content

    def test_it_cannot_be_added_changed_or_deleted(self, client, admin_user, vendors):
        client.force_login(admin_user)

        assert client.get(url('receipts_vendor_add')).status_code == 403
        assert (
            client.post(url('receipts_vendor_change', vendors.pk), {'name': 'x'}).status_code == 403
        )
        assert (
            client.post(url('receipts_vendor_delete', vendors.pk), {'post': 'yes'}).status_code
            == 403
        )


class TestInboundMessageRetry:
    @pytest.fixture(autouse=True)
    def queued(self, monkeypatch) -> list[int]:
        """Stands in for the `ingest_message` task: with the test queue backend it would run the
        ingest inline (and fail, there being no object), so record what was queued instead."""
        calls: list[int] = []

        class Recorder:
            def enqueue(self, message_id):
                calls.append(message_id)

        monkeypatch.setattr('apps.receipts.services.inbound.ingest_message', Recorder())
        return calls

    def make(self, status, **fields):
        return InboundMessage.objects.create(
            s3_key=f'receipts/inbound/{status}', status=status, **fields
        )

    def retry(self, client, *messages):
        return client.post(
            url('receipts_inboundmessage_changelist'),
            {
                'action': 'retry_action',
                '_selected_action': [m.pk for m in messages],
            },
            follow=True,
        )

    def test_a_superuser_resets_failed_messages_to_pending(
        self, client, admin_user, queued, django_capture_on_commit_callbacks
    ):
        failed = self.make(InboundMessageStatus.FAILED, attempts=3, last_error='boom')
        done = self.make(InboundMessageStatus.DONE, attempts=1)
        client.force_login(admin_user)

        with django_capture_on_commit_callbacks(execute=True):
            response = self.retry(client, failed, done)

        assert response.status_code == 200
        failed.refresh_from_db()
        done.refresh_from_db()
        assert failed.status == InboundMessageStatus.PENDING
        assert failed.attempts == 0 and failed.last_error == ''
        assert done.status == InboundMessageStatus.DONE  # only failed ones are reset
        assert b'Reset 1 failed message' in response.content
        assert queued == [failed.pk]  # and the ingest was queued again

    def test_the_list_shows_status_attempts_and_error(self, client, admin_user):
        self.make(InboundMessageStatus.FAILED, attempts=3, last_error='Could not parse MIME')
        client.force_login(admin_user)

        response = client.get(url('receipts_inboundmessage_changelist'), {'status': 'failed'})

        assert response.status_code == 200
        assert b'Could not parse MIME' in response.content

    def test_staff_without_the_change_permission_cannot_retry(self, client):
        from django.contrib.auth.models import Permission

        staff = make_user(is_staff=True)
        # `user_permissions` is a many-to-many manager `ty` can't see through the field type.
        staff.user_permissions.add(  # ty: ignore[unresolved-attribute]
            Permission.objects.get(codename='view_inboundmessage')
        )
        failed = self.make(InboundMessageStatus.FAILED)
        client.force_login(staff)

        self.retry(client, failed)

        failed.refresh_from_db()
        assert failed.status == InboundMessageStatus.FAILED

    def test_staff_with_the_change_permission_can_retry(self, client):
        from django.contrib.auth.models import Permission

        staff = make_user(is_staff=True)
        staff.user_permissions.add(  # ty: ignore[unresolved-attribute]
            Permission.objects.get(codename='view_inboundmessage'),
            Permission.objects.get(codename='change_inboundmessage'),
        )
        failed = self.make(InboundMessageStatus.FAILED)
        client.force_login(staff)

        self.retry(client, failed)

        failed.refresh_from_db()
        assert failed.status == InboundMessageStatus.PENDING
