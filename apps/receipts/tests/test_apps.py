"""What `ReceiptsConfig.ready()` wires up, and the app's system checks."""

import weakref

import pytest
from django.apps import apps
from django.conf import settings
from django.db.models.signals import pre_delete

from apps.billing.signals import credits_added
from apps.core.products import get_products

from ..checks import check_receipts_configuration

pytestmark = [pytest.mark.unit]


def _dispatch_uids(signal) -> set:
    return {key[0] for key, *_ in signal.receivers}


def test_the_app_is_installed_with_the_expected_label():
    config = apps.get_app_config('receipts')

    assert config.name == 'apps.receipts'
    assert config.label == 'receipts'


def test_both_receivers_hold_a_strong_reference():
    """`weak=False`: a weak reference to a local closure is collected as soon as `ready()` returns,
    silently turning the receiver into a dead entry that never fires."""
    for signal, uid in (
        (credits_added, 'receipts.resume_on_credits_added'),
        (pre_delete, 'receipts.delete_storage_on_user_delete'),
    ):
        [(_, receiver, *_)] = [entry for entry in signal.receivers if entry[0][0] == uid]
        assert not isinstance(receiver, weakref.ref)


def test_file_transfers_receiver_is_still_connected_alongside():
    assert 'file_transfer.reenable_on_credits_added' in _dispatch_uids(credits_added)


def test_the_receiver_survives_garbage_collection():
    import gc

    gc.collect()

    assert 'receipts.resume_on_credits_added' in _dispatch_uids(credits_added)
    assert 'receipts.delete_storage_on_user_delete' in _dispatch_uids(pre_delete)


def test_the_product_is_registered_for_the_nav_and_the_landing_page():
    [product] = [p for p in get_products() if p.label == 'receipts']

    assert product.url_name == 'receipts:dashboard'
    assert product.icon == 'fas fa-receipt'


def test_the_receipts_queue_is_configured():
    assert 'receipts' in settings.TASKS['default']['QUEUES']
    assert 'default' in settings.TASKS['default']['QUEUES']


def test_the_rate_limit_rules_exist():
    for name in (
        'RECEIPTS_UPLOAD_USER',
        'RECEIPTS_REPROCESS_USER',
        'RECEIPTS_EXPORT_USER',
        'RECEIPTS_REGENERATE_USER',
    ):
        limit, window = settings.RATELIMIT_RULES[name]
        assert limit > 0 and window > 0


class TestChecks:
    def test_all_empty_is_fine(self, settings):
        settings.OPENAI_API_KEY = ''
        settings.OPENAI_RECEIPTS_MODEL = ''
        settings.RECEIPTS_S3_BUCKET = ''

        assert check_receipts_configuration() == []

    def test_everything_set_is_fine(self, settings):
        settings.OPENAI_API_KEY = 'sk'
        settings.OPENAI_RECEIPTS_MODEL = 'gpt'
        settings.RECEIPTS_S3_BUCKET = 'b'
        settings.RECEIPTS_AWS_ACCESS_KEY_ID = 'k'
        settings.RECEIPTS_AWS_SECRET_ACCESS_KEY = 's'

        assert check_receipts_configuration() == []

    @pytest.mark.parametrize(('key', 'model'), [('sk', ''), ('', 'gpt')])
    def test_half_configured_ai_warns(self, settings, key, model):
        settings.OPENAI_API_KEY = key
        settings.OPENAI_RECEIPTS_MODEL = model

        [warning] = [w for w in check_receipts_configuration() if w.id == 'receipts.W001']

        assert 'OPENAI_API_KEY' in warning.msg

    def test_a_bucket_without_credentials_warns(self, settings):
        settings.OPENAI_API_KEY = ''
        settings.OPENAI_RECEIPTS_MODEL = ''
        settings.RECEIPTS_S3_BUCKET = 'b'
        settings.RECEIPTS_AWS_ACCESS_KEY_ID = ''
        settings.RECEIPTS_AWS_SECRET_ACCESS_KEY = ''

        ids = [w.id for w in check_receipts_configuration()]

        assert ids == ['receipts.W002']
