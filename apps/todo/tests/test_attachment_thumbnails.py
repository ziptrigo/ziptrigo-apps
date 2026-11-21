"""Thumbnails of image attachments (issue #344): shown under a file's row on the task page and under
a comment's files, only for a file whose *bytes* were sniffed as an image; their `src` is
`todo:attachment-thumbnail`, which redirects to a short-lived inline URL and never leaves a flash
message."""

import pytest
from django.contrib.messages import get_messages
from django.urls import reverse
from django.utils import timezone

from apps.core.services.storage import GET_URL_EXPIRES_SECONDS

from ..models import Attachment, TodoAccount
from ..models.attachment import THUMBNAIL_MAX_BYTES
from ..services import add_comment, trash_task
from ..views.attachments import THUMBNAIL_CACHE_SECONDS
from .factories import make_attachment, make_task

pytestmark = [
    pytest.mark.django_db,
    pytest.mark.unit,
    pytest.mark.usefixtures('ratelimit_clock'),
]

PDF_BYTES = b'%PDF-1.4\n' + b'\x00' * 30
MARKER = 'data-attachment-thumbnail'


@pytest.fixture
def task(funded_user):
    return make_task(funded_user)


def page(web, task) -> str:
    return web.get(reverse('todo:task', args=[task.number])).content.decode()


def thumb_url(attachment) -> str:
    return reverse('todo:attachment-thumbnail', args=[attachment.pk])


class TestRendering:
    def test_an_image_attachment_has_a_thumbnail(self, web, task, fake_storage):
        attachment = make_attachment(task, fake_storage)

        html = page(web, task)

        assert html.count(MARKER) == 1
        assert f'src="{thumb_url(attachment)}"' in html
        # Not a tab stop and hidden from assistive tech: "Open" is the keyboard path.
        assert 'tabindex="-1" aria-hidden="true"' in html
        assert 'alt=""' in html
        assert 'loading="lazy"' in html
        # Clicking it opens the image, like the "Open" link.
        assert (
            f'href="{reverse("todo:attachment-download", args=[attachment.pk])}?inline=1"' in html
        )

    def test_an_image_over_the_size_cap_has_none(self, web, task, fake_storage):
        make_attachment(task, fake_storage, size_bytes=THUMBNAIL_MAX_BYTES + 1)

        assert MARKER not in page(web, task)

    def test_an_image_exactly_at_the_cap_has_one(self, web, task, fake_storage):
        make_attachment(task, fake_storage, size_bytes=THUMBNAIL_MAX_BYTES)

        assert page(web, task).count(MARKER) == 1

    def test_a_pdf_has_none(self, web, task, fake_storage):
        make_attachment(
            task, fake_storage, data=PDF_BYTES, filename='a.pdf', inline_type='application/pdf'
        )

        assert MARKER not in page(web, task)

    def test_a_png_named_file_that_is_not_an_image_has_none(self, web, task, fake_storage):
        make_attachment(task, fake_storage, data=b'MZ' + b'\x00' * 30, declared_type='image/png')

        assert MARKER not in page(web, task)

    def test_an_unknown_type_has_none(self, web, task, fake_storage):
        make_attachment(task, fake_storage, data=b'hello', filename='notes.txt')

        assert MARKER not in page(web, task)

    def test_a_pending_upload_is_not_listed_so_has_none(self, web, task, fake_storage):
        make_attachment(task, fake_storage, uploaded=False)

        assert MARKER not in page(web, task)

    def test_none_while_storage_is_paused(self, web, task, fake_storage, funded_user):
        make_attachment(task, fake_storage)
        TodoAccount.objects.filter(pk=funded_user.pk).update(frozen_at=timezone.now())

        assert MARKER not in page(web, task)

    def test_a_comments_image_file_has_one(self, web, task, fake_storage, funded_user):
        comment = add_comment(task, funded_user, 'see photo')
        image = make_attachment(task, fake_storage, for_comment=True, comment=comment)
        pdf = make_attachment(
            task,
            fake_storage,
            data=PDF_BYTES,
            filename='a.pdf',
            inline_type='application/pdf',
            for_comment=True,
            comment=comment,
        )

        html = page(web, task)

        assert html.count(MARKER) == 1
        assert f'src="{thumb_url(image)}"' in html
        assert thumb_url(pdf) not in html

    def test_the_task_list_does_not_show_a_comments_file_twice(
        self, web, task, fake_storage, funded_user
    ):
        comment = add_comment(task, funded_user, 'see photo')
        make_attachment(task, fake_storage, for_comment=True, comment=comment)

        assert page(web, task).count(MARKER) == 1

    def test_a_trashed_task_still_shows_them(self, web, task, fake_storage):
        make_attachment(task, fake_storage)
        trash_task(task)

        assert MARKER in page(web, task)

    def test_the_htmx_list_partial_has_them(self, web, task, fake_storage):
        make_attachment(task, fake_storage)

        response = web.get(
            reverse('todo:attachment-list', args=[task.number]), HTTP_HX_REQUEST='true'
        )

        assert MARKER in response.content.decode()


class TestEndpoint:
    def test_redirects_to_a_short_lived_inline_url(self, web, task, fake_storage):
        attachment = make_attachment(task, fake_storage)

        response = web.get(thumb_url(attachment))

        assert response.status_code == 302
        assert response['Location'].startswith('https://fake-s3.test/')
        assert 'disposition=inline' in response['Location']
        assert fake_storage.get_urls[0]['content_type'] == 'image/png'
        # Cached for less than the presigned URL lives (15 minutes).
        assert response['Cache-Control'] == f'private, max-age={THUMBNAIL_CACHE_SECONDS}'
        # The object itself is signed to be cached for the URL's lifetime.
        assert (
            fake_storage.get_urls[0]['cache_control']
            == f'private, max-age={GET_URL_EXPIRES_SECONDS}'
        )

    def test_the_redirect_is_cached_for_less_than_the_url_lives(self):
        assert 0 < THUMBNAIL_CACHE_SECONDS < GET_URL_EXPIRES_SECONDS

    def test_an_image_over_the_cap_is_a_404_and_signs_nothing(self, web, task, fake_storage):
        big = make_attachment(task, fake_storage, size_bytes=THUMBNAIL_MAX_BYTES + 1)

        assert web.get(thumb_url(big)).status_code == 404
        assert fake_storage.get_urls == []

    def test_an_image_exactly_at_the_cap_is_served(self, web, task, fake_storage):
        exact = make_attachment(task, fake_storage, size_bytes=THUMBNAIL_MAX_BYTES)

        assert web.get(thumb_url(exact)).status_code == 302

    def test_a_non_image_is_a_404_and_signs_nothing(self, web, task, fake_storage):
        pdf = make_attachment(
            task, fake_storage, data=PDF_BYTES, filename='a.pdf', inline_type='application/pdf'
        )
        other = make_attachment(task, fake_storage, data=b'hello', filename='b.png')

        assert web.get(thumb_url(pdf)).status_code == 404
        assert web.get(thumb_url(other)).status_code == 404
        assert fake_storage.get_urls == []

    def test_a_pending_upload_is_a_404(self, web, task, fake_storage):
        pending = make_attachment(task, fake_storage, uploaded=False, inline_type='image/png')

        assert web.get(thumb_url(pending)).status_code == 404

    def test_another_users_attachment_is_a_404(self, web, other_user, fake_storage):
        theirs = make_attachment(make_task(other_user), fake_storage)

        assert web.get(thumb_url(theirs)).status_code == 404
        assert fake_storage.get_urls == []

    def test_frozen_storage_is_a_plain_404_without_a_message(
        self, web, task, fake_storage, funded_user
    ):
        attachment = make_attachment(task, fake_storage)
        TodoAccount.objects.filter(pk=funded_user.pk).update(frozen_at=timezone.now())

        response = web.get(thumb_url(attachment))

        assert response.status_code == 404
        assert fake_storage.get_urls == []
        assert list(get_messages(response.wsgi_request)) == []

    def test_a_trashed_tasks_image_is_served(self, web, task, fake_storage):
        attachment = make_attachment(task, fake_storage)
        trash_task(task)

        assert web.get(thumb_url(attachment)).status_code == 302

    def test_over_the_limit_is_a_json_429_with_no_message(self, web, task, fake_storage, settings):
        settings.RATELIMIT_ENABLE = True
        settings.RATELIMIT_RULES = {**settings.RATELIMIT_RULES, 'TODO_THUMBNAIL_USER': (1, 60)}
        attachment = make_attachment(task, fake_storage)

        assert web.get(thumb_url(attachment)).status_code == 302
        response = web.get(thumb_url(attachment))

        assert response.status_code == 429
        assert 'Retry-After' in response
        assert list(get_messages(response.wsgi_request)) == []

    def test_is_a_separate_budget_from_downloads(self, web, task, fake_storage, settings):
        settings.RATELIMIT_ENABLE = True
        settings.RATELIMIT_RULES = {**settings.RATELIMIT_RULES, 'TODO_THUMBNAIL_USER': (1, 60)}
        attachment = make_attachment(task, fake_storage)

        assert web.get(thumb_url(attachment)).status_code == 302
        download = reverse('todo:attachment-download', args=[attachment.pk])
        assert web.get(download).status_code == 302

    def test_requires_login_and_get(self, client, web, task, fake_storage):
        attachment = make_attachment(task, fake_storage)

        assert client.get(thumb_url(attachment)).status_code == 302
        assert web.post(thumb_url(attachment)).status_code == 405


def test_has_thumbnail_needs_an_image_within_the_cap():
    assert Attachment(inline_type='image/png', uploaded=True, size_bytes=1).has_thumbnail
    assert not Attachment(
        inline_type='image/png', uploaded=True, size_bytes=THUMBNAIL_MAX_BYTES + 1
    ).has_thumbnail
    assert not Attachment(inline_type='application/pdf', uploaded=True, size_bytes=1).has_thumbnail


def test_is_image_follows_the_sniffed_type_only():
    for inline_type in ('image/jpeg', 'image/png', 'image/gif', 'image/webp'):
        assert Attachment(inline_type=inline_type, uploaded=True).is_image
    assert not Attachment(inline_type='application/pdf', uploaded=True).is_image
    assert not Attachment(inline_type='', uploaded=True, declared_type='image/png').is_image
    assert not Attachment(inline_type='image/png', uploaded=False).is_image
