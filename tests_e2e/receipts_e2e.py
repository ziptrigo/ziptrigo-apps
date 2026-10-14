"""The app entry (`INSTALLED_APPS`) that installs the fakes of `receipts_e2e_settings.py`."""

from django.apps import AppConfig


class E2EConfig(AppConfig):
    name = 'tests_e2e.receipts_e2e'
    label = 'receipts_e2e'

    def ready(self) -> None:
        from apps.file_transfer.services import storage as file_transfer_storage
        from apps.file_transfer.tests.fakes import FakeS3Storage
        from apps.receipts.services import ai, storage
        from apps.receipts.tests.fakes import FakeAIClient

        from .receipts_fakes import E2EStorage

        storage._storage = E2EStorage()
        ai._client = FakeAIClient()
        # File transfer's send page talks to S3 when a file is added (a multipart upload); the
        # drop zone test only needs that call to succeed, so it gets the unit tests' in-memory fake.
        file_transfer_storage._storage = FakeS3Storage()
