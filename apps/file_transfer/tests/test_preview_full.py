"""\"View full image / document\" (issue #311): it counts as a download, exactly like the Download
button, and the Download that follows it does not count again."""

import io
import time
from datetime import timedelta

import pytest
from django.test import Client
from django.urls import reverse
from django.utils import timezone
from PIL import Image

from .. import services
from ..models import DownloadEvent, TransferFile, TransferStatus
from ..services import downloads, preview
from ..services import password as password_service
from ..services import storage as storage_service
from ..services.lifecycle import finish_deferred_deletion

pytestmark = [pytest.mark.django_db, pytest.mark.unit]


def _png() -> bytes:
    out = io.BytesIO()
    Image.new('RGB', (40, 30), (10, 120, 200)).save(out, format='PNG')
    return out.getvalue()


PDF = b'%PDF-1.7\n' + b'\0' * 32


def _add(transfer, fake_storage, name, content, *, size=None):
    size = size if size is not None else len(content)
    status = transfer.status
    transfer.status = TransferStatus.DRAFT
    try:
        file = services.add_file(transfer, name, size, storage=fake_storage)
        fake_storage.seed_object(file.storage_key, size, content)
        services.complete_file_upload(file, [{'PartNumber': 1, 'ETag': 'e'}], storage=fake_storage)
    finally:
        transfer.status = status
    file.refresh_from_db()
    return file


def _active(transfer, **overrides):
    transfer.status = TransferStatus.ACTIVE
    for key, value in overrides.items():
        setattr(transfer, key, value)
    transfer.save()
    return transfer


def _full(transfer, file):
    return reverse('t:preview-full', args=[transfer.slug, file.id])


def _download(transfer, file):
    return reverse('t:download-file', args=[transfer.slug, file.id])


class TestViewFullCountsAsADownload:
    def test_it_answers_a_presigned_inline_url_and_records_one_download(
        self, client, draft_transfer, fake_storage
    ):
        file = _add(draft_transfer, fake_storage, 'photo.png', _png())
        _active(draft_transfer)

        response = client.get(_full(draft_transfer, file))

        assert response.status_code == 200
        data = response.json()
        assert data['content_type'] == 'image/png'
        assert data['name'] == 'photo.png'
        assert file.storage_key in data['url']
        assert fake_storage.get_url_calls[-1] == (file.storage_key, 'inline', 'image/png')
        assert response['Cache-Control'] == 'no-store, private'
        event = DownloadEvent.objects.get()
        assert event.file_id == file.pk

    def test_it_notifies_the_sender_like_a_download(
        self, client, draft_transfer, fake_storage, monkeypatch, django_capture_on_commit_callbacks
    ):
        file = _add(draft_transfer, fake_storage, 'photo.png', _png())
        _active(draft_transfer)
        queued = []
        monkeypatch.setattr(
            downloads,
            'send_download_notification',
            type('T', (), {'enqueue': staticmethod(lambda *a: queued.append(a))}),
        )

        with django_capture_on_commit_callbacks(execute=True):
            client.get(_full(draft_transfer, file))

        assert queued == [(str(draft_transfer.id), str(file.id))]

    def test_a_pdf_is_served_inline_as_a_pdf(self, client, draft_transfer, fake_storage):
        file = _add(draft_transfer, fake_storage, 'doc.pdf', PDF)
        _active(draft_transfer)
        assert client.get(_full(draft_transfer, file)).status_code == 200
        assert fake_storage.get_url_calls[-1][1:] == ('inline', 'application/pdf')

    def test_it_counts_against_max_downloads_and_ends_the_transfer_at_the_limit(
        self, client, draft_transfer, fake_storage
    ):
        file = _add(draft_transfer, fake_storage, 'photo.png', _png())
        _active(draft_transfer, max_downloads=1)

        assert client.get(_full(draft_transfer, file)).status_code == 200

        draft_transfer.refresh_from_db()
        assert draft_transfer.status == TransferStatus.EXPIRED
        # The files stay for the grace window: this URL must resolve.
        assert draft_transfer.files_deleted_at is None
        # Everyone else is now refused, the preview of any file included.
        other = Client()
        assert other.get(_full(draft_transfer, file)).status_code == 404
        assert other.get(reverse('t:download', args=[draft_transfer.slug])).status_code == 404

    def test_a_visitor_who_asked_is_not_a_fresh_download_for_another_visitor(
        self, client, draft_transfer, fake_storage
    ):
        file = _add(draft_transfer, fake_storage, 'photo.png', _png())
        _active(draft_transfer)
        client.get(_full(draft_transfer, file))
        Client().get(_full(draft_transfer, file))
        assert DownloadEvent.objects.count() == 2


class TestDownloadAfterViewFullIsOneDownload:
    def test_the_download_that_follows_does_not_count_again(
        self, client, draft_transfer, fake_storage
    ):
        file = _add(draft_transfer, fake_storage, 'photo.png', _png())
        _active(draft_transfer, max_downloads=5)
        client.get(_full(draft_transfer, file))

        response = client.get(_download(draft_transfer, file))

        assert response.status_code == 302
        assert fake_storage.get_url_calls[-1] == (file.storage_key, 'attachment', None)
        assert DownloadEvent.objects.count() == 1
        draft_transfer.refresh_from_db()
        assert draft_transfer.downloads_remaining == 4

    def test_only_one_free_download_the_next_one_counts(self, client, draft_transfer, fake_storage):
        file = _add(draft_transfer, fake_storage, 'photo.png', _png())
        _active(draft_transfer, max_downloads=5)
        client.get(_full(draft_transfer, file))
        client.get(_download(draft_transfer, file))
        client.get(_download(draft_transfer, file))
        assert DownloadEvent.objects.count() == 2

    def test_asking_to_view_full_again_is_free(self, client, draft_transfer, fake_storage):
        file = _add(draft_transfer, fake_storage, 'photo.png', _png())
        _active(draft_transfer, max_downloads=5)
        for _ in range(3):
            assert client.get(_full(draft_transfer, file)).status_code == 200
        assert DownloadEvent.objects.count() == 1
        # And the download after all of that is still the same one download.
        client.get(_download(draft_transfer, file))
        assert DownloadEvent.objects.count() == 1

    def test_the_record_is_per_file(self, client, draft_transfer, fake_storage):
        first = _add(draft_transfer, fake_storage, 'a.png', _png())
        second = _add(draft_transfer, fake_storage, 'b.png', _png())
        _active(draft_transfer)
        client.get(_full(draft_transfer, first))
        client.get(_download(draft_transfer, second))
        assert DownloadEvent.objects.count() == 2

    def test_it_is_per_session(self, client, draft_transfer, fake_storage):
        file = _add(draft_transfer, fake_storage, 'photo.png', _png())
        _active(draft_transfer)
        client.get(_full(draft_transfer, file))
        Client().get(_download(draft_transfer, file))
        assert DownloadEvent.objects.count() == 2

    def test_it_lasts_as_long_as_the_presigned_url(
        self, client, draft_transfer, fake_storage, monkeypatch
    ):
        assert downloads.COUNTED_WINDOW_SECONDS == storage_service.GET_URL_EXPIRES_SECONDS
        file = _add(draft_transfer, fake_storage, 'photo.png', _png())
        _active(draft_transfer)
        client.get(_full(draft_transfer, file))

        now = time.time()
        monkeypatch.setattr(
            downloads.time, 'time', lambda: now + downloads.COUNTED_WINDOW_SECONDS + 1
        )
        client.get(_download(draft_transfer, file))

        assert DownloadEvent.objects.count() == 2

    def test_a_forged_or_damaged_session_value_is_no_free_download(
        self, client, draft_transfer, fake_storage
    ):
        file = _add(draft_transfer, fake_storage, 'photo.png', _png())
        _active(draft_transfer)
        session = client.session
        session[downloads.COUNTED_SESSION_KEY] = 'yes'
        session.save()
        client.get(_download(draft_transfer, file))
        session = client.session
        session[downloads.COUNTED_SESSION_KEY] = {f'{draft_transfer.id}:{file.id}': 'never'}
        session.save()
        client.get(_download(draft_transfer, file))
        assert DownloadEvent.objects.count() == 2

    def test_the_record_is_bounded(self):
        session: dict = {}
        for number in range(80):
            downloads.mark_counted(session, 'transfer', f'file-{number}')
        assert len(session[downloads.COUNTED_SESSION_KEY]) == 50
        assert downloads.is_counted(session, 'transfer', 'file-79')
        assert not downloads.is_counted(session, 'transfer', 'file-0')


class TestTheLastAllowedDownload:
    def test_the_download_after_the_view_that_used_the_last_one_still_works(
        self, client, draft_transfer, fake_storage
    ):
        file = _add(draft_transfer, fake_storage, 'photo.png', _png())
        _active(draft_transfer, max_downloads=1)
        assert client.get(_full(draft_transfer, file)).status_code == 200
        draft_transfer.refresh_from_db()
        assert draft_transfer.status == TransferStatus.EXPIRED

        response = client.get(_download(draft_transfer, file))

        assert response.status_code == 302
        assert fake_storage.get_url_calls[-1] == (file.storage_key, 'attachment', None)
        assert DownloadEvent.objects.count() == 1
        # ...once. The next is a real download, and the limit is reached.
        assert client.get(_download(draft_transfer, file)).status_code == 404
        assert DownloadEvent.objects.count() == 1

    def test_viewing_full_again_after_the_limit_is_still_free_for_that_session(
        self, client, draft_transfer, fake_storage
    ):
        file = _add(draft_transfer, fake_storage, 'photo.png', _png())
        _active(draft_transfer, max_downloads=1)
        client.get(_full(draft_transfer, file))
        assert client.get(_full(draft_transfer, file)).status_code == 200
        assert DownloadEvent.objects.count() == 1

    def test_another_visitor_cannot_use_it(self, client, draft_transfer, fake_storage):
        file = _add(draft_transfer, fake_storage, 'photo.png', _png())
        _active(draft_transfer, max_downloads=1)
        client.get(_full(draft_transfer, file))
        assert Client().get(_download(draft_transfer, file)).status_code == 404

    def test_not_once_the_files_are_deleted(self, client, draft_transfer, fake_storage):
        file = _add(draft_transfer, fake_storage, 'photo.png', _png())
        _active(draft_transfer, max_downloads=1)
        client.get(_full(draft_transfer, file))
        draft_transfer.refresh_from_db()
        finish_deferred_deletion(draft_transfer, notify=False, storage=fake_storage)

        assert client.get(_download(draft_transfer, file)).status_code == 404
        assert client.get(_full(draft_transfer, file)).status_code == 404

    @pytest.mark.parametrize(
        'status',
        [TransferStatus.DISABLED, TransferStatus.SUSPENDED, TransferStatus.DELETED],
    )
    def test_not_when_the_sender_or_the_system_closed_it_meanwhile(
        self, client, draft_transfer, fake_storage, status
    ):
        file = _add(draft_transfer, fake_storage, 'photo.png', _png())
        _active(draft_transfer)
        client.get(_full(draft_transfer, file))
        draft_transfer.status = status
        draft_transfer.save()
        assert client.get(_download(draft_transfer, file)).status_code == 404
        assert client.get(_full(draft_transfer, file)).status_code == 404

    def test_not_when_it_is_held_for_review_or_taken_down(
        self, client, draft_transfer, fake_storage, admin_user
    ):
        from ..services.takedown import take_down_transfer

        file = _add(draft_transfer, fake_storage, 'photo.png', _png())
        _active(draft_transfer)
        client.get(_full(draft_transfer, file))
        draft_transfer.held_for_review_at = timezone.now()
        draft_transfer.save()
        assert client.get(_download(draft_transfer, file)).status_code == 404
        draft_transfer.held_for_review_at = None
        draft_transfer.save()
        take_down_transfer(draft_transfer, by=admin_user, reason='abuse')
        assert client.get(_download(draft_transfer, file)).status_code == 404

    def test_the_password_still_applies(self, client, draft_transfer, fake_storage):
        file = _add(draft_transfer, fake_storage, 'photo.png', _png())
        _active(
            draft_transfer,
            max_downloads=1,
            password_hash=password_service.hash_password('sekret'),
        )
        client.post(reverse('t:unlock', args=[draft_transfer.slug]), {'password': 'sekret'})
        client.get(_full(draft_transfer, file))
        # A password change locks the session out again, counted record or not.
        draft_transfer.password_hash = password_service.hash_password('other')
        draft_transfer.save()
        response = client.get(_download(draft_transfer, file))
        assert response.status_code == 302
        assert reverse('t:download', args=[draft_transfer.slug]) in response['Location']


class TestGates:
    def test_a_password_is_needed_and_a_refusal_is_a_403_not_a_redirect(
        self, client, draft_transfer, fake_storage
    ):
        file = _add(draft_transfer, fake_storage, 'photo.png', _png())
        _active(draft_transfer, password_hash=password_service.hash_password('sekret'))

        response = client.get(_full(draft_transfer, file))

        assert response.status_code == 403
        assert 'Location' not in response
        assert not DownloadEvent.objects.exists()
        client.post(reverse('t:unlock', args=[draft_transfer.slug]), {'password': 'sekret'})
        assert client.get(_full(draft_transfer, file)).status_code == 200

    @pytest.mark.parametrize(
        'overrides',
        [
            {'status': TransferStatus.DISABLED},
            {'status': TransferStatus.EXPIRED},
            {'held_for_review_at': timezone.now()},
            {'expires_at': timezone.now() - timedelta(minutes=1)},
        ],
    )
    def test_an_unavailable_transfer_is_404_and_counts_nothing(
        self, client, draft_transfer, fake_storage, overrides
    ):
        file = _add(draft_transfer, fake_storage, 'photo.png', _png())
        _active(draft_transfer, **overrides)
        assert client.get(_full(draft_transfer, file)).status_code == 404
        assert not DownloadEvent.objects.exists()

    def test_a_file_with_no_preview_is_404(self, client, draft_transfer, fake_storage):
        file = _add(draft_transfer, fake_storage, 'a.bin', b'\0' * 20)
        _active(draft_transfer)
        assert client.get(_full(draft_transfer, file)).status_code == 404
        assert not DownloadEvent.objects.exists()

    def test_rate_limited_like_a_download(self, client, draft_transfer, fake_storage, settings):
        file = _add(draft_transfer, fake_storage, 'photo.png', _png())
        _active(draft_transfer)
        settings.RATELIMIT_ENABLE = True
        settings.RATELIMIT_RULES = {**settings.RATELIMIT_RULES, 'FT_DOWNLOAD_IP': (1, 60)}
        assert client.get(_full(draft_transfer, file)).status_code == 200
        assert client.get(_full(draft_transfer, file)).status_code == 429

    def test_post_is_not_allowed(self, client, draft_transfer, fake_storage):
        file = _add(draft_transfer, fake_storage, 'photo.png', _png())
        _active(draft_transfer)
        assert client.post(_full(draft_transfer, file)).status_code == 405

    def test_a_file_request_is_the_owner_until_it_is_done(
        self, client, make_request, funded_user, fake_storage, anon_uploader
    ):
        from ..services import request_upload
        from ..services import requests as requests_service

        transfer = make_request()
        content = _png()
        file = request_upload.add_file(
            transfer, anon_uploader, 'p.png', len(content), ip='9.9.9.9', storage=fake_storage
        )
        fake_storage.seed_object(file.storage_key, len(content), content)
        request_upload.complete_file(
            transfer,
            file,
            [{'PartNumber': 1, 'ETag': 'e'}],
            anon_uploader,
            ip='9.9.9.9',
            storage=fake_storage,
        )
        url = _full(transfer, file)
        assert client.get(url).status_code == 403
        owner = Client()
        owner.force_login(funded_user)
        assert owner.get(url).status_code == 200
        requests_service.mark_done(transfer, by_name='Jane', storage=fake_storage)
        assert client.get(url).status_code == 200


class TestTextFiles:
    def test_a_sample_that_cut_the_file_offers_the_whole_one(
        self, client, draft_transfer, fake_storage
    ):
        content = b'line\n' * 20_000  # 100 KB
        file = _add(draft_transfer, fake_storage, 'log.txt', content)
        _active(draft_transfer)

        response = client.get(_full(draft_transfer, file))

        assert response.status_code == 200
        assert DownloadEvent.objects.count() == 1
        # Text goes inline as text/plain.
        assert fake_storage.get_url_calls[-1][1:] == ('inline', 'text/plain')

    def test_markdown_is_fetched_as_an_attachment_it_is_never_served_inline(
        self, client, draft_transfer, fake_storage
    ):
        file = _add(draft_transfer, fake_storage, 'notes.md', b'# hi\n' * 20_000)
        _active(draft_transfer)
        assert client.get(_full(draft_transfer, file)).status_code == 200
        assert fake_storage.get_url_calls[-1] == (file.storage_key, 'attachment', None)

    def test_a_file_the_sample_did_not_cut_has_nothing_more_to_show(
        self, client, draft_transfer, fake_storage
    ):
        file = _add(draft_transfer, fake_storage, 'small.txt', b'hello')
        _active(draft_transfer)
        assert client.get(_full(draft_transfer, file)).status_code == 404
        assert not DownloadEvent.objects.exists()

    def test_a_file_too_big_to_show_whole_is_only_a_sample(
        self, client, draft_transfer, fake_storage
    ):
        file = _add(
            draft_transfer,
            fake_storage,
            'huge.txt',
            b'x' * 5000,
            size=preview.FULL_TEXT_MAX_BYTES + 1,
        )
        _active(draft_transfer)
        assert client.get(_full(draft_transfer, file)).status_code == 404
        assert not DownloadEvent.objects.exists()


class TestPageContext:
    def test_the_dialogs_entries_carry_the_view_full_url_only_where_it_applies(
        self, client, draft_transfer, fake_storage
    ):
        image = _add(draft_transfer, fake_storage, 'a.png', _png())
        small = _add(draft_transfer, fake_storage, 'b.txt', b'hi')
        _active(draft_transfer)
        entries = {
            e['id']: e
            for e in client.get(reverse('t:download', args=[draft_transfer.slug])).context[
                'previews'
            ]
        }
        assert entries[str(image.id)]['full_url'] == _full(draft_transfer, image)
        assert entries[str(small.id)]['full_url'] == ''
        assert TransferFile.objects.count() == 2
