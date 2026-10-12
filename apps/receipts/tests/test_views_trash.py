"""The Trash page and its restore / delete-forever actions (`views/trash.py`)."""

from datetime import date, timedelta
from decimal import Decimal

import pytest
from django.contrib.messages import get_messages
from django.urls import reverse
from django.utils import timezone

from ..models import Receipt, ReceiptStatus
from ..services.accounts import get_account
from .factories import create_receipt

pytestmark = [pytest.mark.django_db, pytest.mark.unit]


@pytest.fixture
def trashed(make_receipt):
    return make_receipt(
        status=ReceiptStatus.PROCESSED,
        vendor='Staples',
        amount=Decimal('12.00'),
        currency='USD',
        receipt_date=date(2026, 10, 1),
        deleted_at=timezone.now() - timedelta(days=3),
    )


def flashed(response) -> list[str]:
    return [str(m) for m in get_messages(response.wsgi_request)]


def test_lists_the_trash_with_how_long_is_left(web, trashed, make_receipt):
    make_receipt(vendor='Live one', status=ReceiptStatus.PROCESSED)

    response = web.get(reverse('receipts:trash'))
    content = response.content.decode()

    assert response.status_code == 200
    assert 'Staples' in content and 'Live one' not in content
    assert '2026-10-01' in content and 'USD 12.00' in content
    assert 'deleted in 27 days' in content
    assert 'deleted forever 30 days after being trashed' in content
    assert reverse('receipts:restore', args=[trashed.pk]) in content
    assert reverse('receipts:delete', args=[trashed.pk]) in content


def test_the_most_recently_trashed_comes_first(web, make_receipt):
    old = make_receipt(vendor='Older', deleted_at=timezone.now() - timedelta(days=5))
    new = make_receipt(vendor='Newer', deleted_at=timezone.now() - timedelta(days=1))

    content = web.get(reverse('receipts:trash')).content.decode()

    assert content.index(str(new.pk)) < content.index(str(old.pk))


def test_a_receipt_about_to_be_purged_says_so(web, make_receipt):
    make_receipt(vendor='Almost', deleted_at=timezone.now() - timedelta(days=30, hours=1))

    assert 'deleted soon' in web.get(reverse('receipts:trash')).content.decode()


def test_an_empty_trash(web):
    assert 'The trash is empty.' in web.get(reverse('receipts:trash')).content.decode()


def test_only_the_owners_trash_is_listed(web, other_user, fake_storage):
    create_receipt(other_user, fake_storage, vendor='Theirs', deleted_at=timezone.now())

    assert 'Theirs' not in web.get(reverse('receipts:trash')).content.decode()


def test_it_paginates(web, make_receipt):
    for index in range(27):
        make_receipt(vendor=f'V{index}', deleted_at=timezone.now())

    first = web.get(reverse('receipts:trash')).content.decode()
    second = web.get(reverse('receipts:trash'), {'page': 2}).content.decode()

    assert 'Page 1 of 2' in first and 'Page 2 of 2' in second


def test_deleting_forever_asks_for_confirmation_in_a_modal(web, trashed):
    content = web.get(reverse('receipts:trash')).content.decode()

    assert 'role="dialog"' in content and 'aria-modal="true"' in content
    assert f'data-url="{reverse("receipts:delete", args=[trashed.pk])}"' in content
    assert "This can't be undone" in content


class TestRestore:
    def test_restores_and_goes_back_to_the_trash(self, web, trashed):
        response = web.post(reverse('receipts:restore', args=[trashed.pk]))

        assert response.status_code == 302 and response['Location'] == reverse('receipts:trash')
        trashed.refresh_from_db()
        assert trashed.deleted_at is None
        assert flashed(response) == ['Restored Staples.']

    def test_from_the_receipts_own_page_it_goes_back_there(self, web, trashed):
        response = web.post(reverse('receipts:restore', args=[trashed.pk]), {'next': 'detail'})

        assert response['Location'] == reverse('receipts:detail', args=[trashed.pk])

    def test_over_htmx_it_redirects_with_a_header(self, web, trashed):
        response = web.post(reverse('receipts:restore', args=[trashed.pk]), HTTP_HX_REQUEST='true')

        assert response['HX-Redirect'] == reverse('receipts:trash')

    def test_a_receipt_that_is_not_in_the_trash_is_a_404(self, web, make_receipt):
        live = make_receipt(status=ReceiptStatus.PROCESSED)

        assert web.post(reverse('receipts:restore', args=[live.pk])).status_code == 404

    def test_it_needs_post(self, web, trashed):
        assert web.get(reverse('receipts:restore', args=[trashed.pk])).status_code == 405


class TestDeleteForever:
    def test_deletes_the_row_and_its_files(self, web, trashed, fake_storage):
        key = trashed.file_key

        response = web.post(reverse('receipts:delete', args=[trashed.pk]))

        assert response['Location'] == reverse('receipts:trash')
        assert not Receipt.objects.filter(pk=trashed.pk).exists()
        assert key in fake_storage.deleted and key not in fake_storage.objects
        assert flashed(response) == ['Deleted Staples forever.']

    def test_gives_the_storage_back(self, web, trashed, funded_user):
        before = get_account(funded_user).stored_bytes
        assert before > 0

        web.post(reverse('receipts:delete', args=[trashed.pk]))

        assert get_account(funded_user).stored_bytes == 0

    def test_a_live_receipt_cannot_be_deleted_this_way(self, web, make_receipt):
        live = make_receipt(status=ReceiptStatus.PROCESSED)

        assert web.post(reverse('receipts:delete', args=[live.pk])).status_code == 404
        assert Receipt.objects.filter(pk=live.pk).exists()

    def test_a_receipt_being_processed_is_refused_with_a_message(self, web, make_receipt):
        busy = make_receipt(status=ReceiptStatus.PROCESSING, deleted_at=timezone.now())

        response = web.post(reverse('receipts:delete', args=[busy.pk]))

        assert response['Location'] == reverse('receipts:trash')
        assert 'being processed' in flashed(response)[0]
        assert Receipt.objects.filter(pk=busy.pk).exists()

    def test_a_storage_error_is_a_message_not_a_500(self, web, trashed, fake_storage):
        fake_storage.failing_deletes.add(trashed.file_key)

        response = web.post(reverse('receipts:delete', args=[trashed.pk]))

        assert response.status_code == 302
        assert "couldn't delete it just now" in flashed(response)[0]
        assert Receipt.objects.filter(pk=trashed.pk).exists()

    def test_it_needs_post(self, web, trashed):
        assert web.get(reverse('receipts:delete', args=[trashed.pk])).status_code == 405
