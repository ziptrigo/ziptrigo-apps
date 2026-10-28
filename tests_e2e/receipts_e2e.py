"""The app entry (`INSTALLED_APPS`) that installs the fakes of `receipts_e2e_settings.py`."""

from django.apps import AppConfig


class E2EConfig(AppConfig):
    name = 'tests_e2e.receipts_e2e'
    label = 'receipts_e2e'

    def ready(self) -> None:
        from apps.file_transfer.services import storage as file_transfer_storage
        from apps.receipts.services import ai, storage
        from apps.receipts.tests.fakes import FakeAIClient

        from .receipts_fakes import E2EStorage
        from .site_fakes import FileTransferE2EStorage, install_mailbox
        from .todo_fakes import install_title_parser

        storage._storage = E2EStorage()
        ai._client = FakeAIClient()
        # File transfer's send page talks to S3 when a file is added (a multipart upload): an
        # in-memory bucket whose presigned URLs point back at this server (`site_fakes`).
        file_transfer_storage._storage = FileTransferE2EStorage()
        install_mailbox()
        # To-do reads dates out of a title with the AI: a fake that understands one title.
        install_title_parser()
