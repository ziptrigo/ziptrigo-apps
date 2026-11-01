"""The app entry (`INSTALLED_APPS`) that installs the fakes of `receipts_e2e_settings.py`."""

from django.apps import AppConfig


class E2EConfig(AppConfig):
    name = 'tests_e2e.receipts_e2e'
    label = 'receipts_e2e'

    def ready(self) -> None:
        from django.conf import settings

        from apps.receipts.services import ai
        from apps.receipts.tests.fakes import FakeAIClient

        from .todo_fakes import install_title_parser

        ai._client = FakeAIClient()
        # To-do reads dates out of a title with the AI: a fake that understands a few titles.
        install_title_parser()
        if settings.E2E_SERVICES == 'real':
            # Postgres, Floci and SMTP/IMAP (`real_services.py`); the storages build themselves
            # from the settings, which point every product at the e2e bucket.
            from .real_services import install_smtp_backend

            install_smtp_backend()
            return

        from apps.file_transfer.services import storage as file_transfer_storage
        from apps.receipts.services import storage
        from apps.todo.services import storage as todo_storage

        from .receipts_fakes import E2EStorage
        from .site_fakes import FileTransferE2EStorage, TodoE2EStorage, install_mailbox

        storage._storage = E2EStorage()
        # File transfer's send page talks to S3 when a file is added (a multipart upload): an
        # in-memory bucket whose presigned URLs point back at this server (`site_fakes`).
        file_transfer_storage._storage = FileTransferE2EStorage()
        # To-do attachments and comment files: the same, a bucket at `/__e2e__/todo-s3/`.
        todo_storage._storage = TodoE2EStorage()
        install_mailbox()
