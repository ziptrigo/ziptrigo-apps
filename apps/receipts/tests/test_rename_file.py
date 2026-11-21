"""Renaming a receipt's file from its page (`receipts:rename-file`): the name is edited without its
extension, which is kept; the new name is the download name and the one exports use."""

import pytest
from django.contrib.messages import get_messages
from django.core.exceptions import ValidationError
from django.test import Client
from django.urls import reverse

from apps.accounts.tests.factories import UserFactory

from ..services import rename_file, trash

pytestmark = [pytest.mark.django_db, pytest.mark.unit]

HX = {'HTTP_HX_REQUEST': 'true'}


@pytest.fixture
def receipt(make_receipt):
    return make_receipt(original_filename='IMG_4410.jpg')


def _url(receipt):
    return reverse('receipts:rename-file', args=[receipt.pk])


class TestService:
    def test_keeps_the_extension(self, receipt):
        rename_file(receipt, 'Lunch with Sam')

        receipt.refresh_from_db()
        assert receipt.original_filename == 'Lunch with Sam.jpg'

    def test_an_empty_name_is_refused(self, receipt):
        with pytest.raises(ValidationError, match='Give the file a name'):
            rename_file(receipt, ' ')

    def test_a_receipt_in_the_trash_is_refused(self, receipt):
        trash(receipt)

        with pytest.raises(ValidationError, match='Restore the receipt'):
            rename_file(receipt, 'x')

    def test_an_email_body_has_no_name_to_change(self, make_receipt):
        body = make_receipt(original_filename='')

        with pytest.raises(ValidationError, match='no file name'):
            rename_file(body, 'x')


class TestView:
    def test_renames_and_redirects(self, web, receipt):
        response = web.post(_url(receipt), {'filename': 'Lunch'}, **HX)

        assert response['HX-Redirect'] == reverse('receipts:detail', args=[receipt.pk])
        receipt.refresh_from_db()
        assert receipt.original_filename == 'Lunch.jpg'
        assert 'Renamed to Lunch.jpg.' in [str(m) for m in get_messages(response.wsgi_request)]

    def test_a_refusal_goes_under_the_name(self, web, receipt):
        response = web.post(_url(receipt), {'filename': ''}, **HX)

        assert response.status_code == 422
        assert response['HX-Retarget'] == '#receipt-file-msg'

    def test_the_page_offers_rename_with_the_stem(self, web, receipt):
        html = web.get(reverse('receipts:detail', args=[receipt.pk])).content.decode()

        assert 'Rename IMG_4410.jpg' in html
        assert 'value="IMG_4410"' in html

    def test_another_users_receipt_is_a_404(self, receipt):
        other = Client()
        other.force_login(UserFactory())

        assert other.post(_url(receipt), {'filename': 'x'}).status_code == 404
