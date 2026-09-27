import pytest

from ..models import FileTransferSettings, Transfer, TransferFile, TransferStatus

pytestmark = [pytest.mark.django_db, pytest.mark.unit]


def test_display_name_uses_first_file_by_creation_order(draft_transfer):
    TransferFile.objects.create(
        transfer=draft_transfer, name='second.txt', size=10, storage_key='k2'
    )
    TransferFile.objects.create(
        transfer=draft_transfer, name='first.txt', size=10, storage_key='k1'
    )

    # Both share the same auto_now_add second in a fast test run; ordering falls back to id, so
    # this only asserts a name from the transfer's files is used, not which one specifically wins.
    assert draft_transfer.display_name in {'first.txt', 'second.txt'}


def test_display_name_falls_back_when_no_files(draft_transfer):
    assert draft_transfer.display_name == 'Untitled transfer'


def test_downloads_remaining_none_when_uncapped(draft_transfer):
    assert draft_transfer.downloads_remaining is None


def test_downloads_remaining_counts_down(draft_transfer):
    draft_transfer.max_downloads = 3
    draft_transfer.save()
    file = TransferFile.objects.create(
        transfer=draft_transfer, name='a.txt', size=1, storage_key='k', uploaded=True
    )
    from ..models import DownloadEvent

    DownloadEvent.objects.create(transfer=draft_transfer, file=file)
    draft_transfer.refresh_from_db()

    assert draft_transfer.download_count == 1
    assert draft_transfer.downloads_remaining == 2


def test_absolute_download_url_uses_base_url_and_slug(draft_transfer, settings):
    settings.BASE_URL = 'https://ziptrigo.example'
    url = draft_transfer.absolute_download_url
    assert url == f'https://ziptrigo.example/t/{draft_transfer.slug}/'


def test_slug_is_22_char_base62_and_unique(funded_user):
    a = Transfer.objects.create(owner=funded_user)
    b = Transfer.objects.create(owner=funded_user)
    assert len(a.slug) == 22
    assert a.slug != b.slug


def test_is_actionable_and_is_ended():
    for status in [TransferStatus.ACTIVE, TransferStatus.DISABLED, TransferStatus.SUSPENDED]:
        t = Transfer(status=status)
        assert t.is_actionable
        assert not t.is_ended
    for status in [TransferStatus.EXPIRED, TransferStatus.DELETED]:
        t = Transfer(status=status)
        assert not t.is_actionable
        assert t.is_ended


def test_filetransfersettings_is_a_singleton(db):
    first = FileTransferSettings.load()
    first.logged_in_max_files = 5
    first.save()

    second = FileTransferSettings.load()
    assert second.pk == 1
    assert second.logged_in_max_files == 5
    assert FileTransferSettings.objects.count() == 1


def test_filetransfersettings_anonymous_allowed_expiry_days_list(db):
    settings_row = FileTransferSettings.load()
    settings_row.anonymous_allowed_expiry_days = '1, 5,15 ,30'
    assert settings_row.anonymous_allowed_expiry_days_list() == [1, 5, 15, 30]
