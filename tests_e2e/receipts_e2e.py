"""The app entry (`INSTALLED_APPS`) that installs the fakes of `receipts_e2e_settings.py`."""

from django.apps import AppConfig


class E2EConfig(AppConfig):
    name = 'tests_e2e.receipts_e2e'
    label = 'receipts_e2e'

    def ready(self) -> None:
        from apps.receipts.services import ai, storage
        from apps.receipts.tests.fakes import FakeAIClient

        from .receipts_fakes import E2EStorage

        storage._storage = E2EStorage()
        ai._client = FakeAIClient()
