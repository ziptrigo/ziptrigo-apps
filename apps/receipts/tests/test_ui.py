"""The status pill, the other template helpers, and the full-page checks that cut across views."""

import pytest
from django.template import Context, Template
from django.urls import reverse

from ..models import FailureReason, ReceiptStatus
from ..templatetags import receipts_ui

pytestmark = [pytest.mark.django_db, pytest.mark.unit]


def render(template: str, **context) -> str:
    return Template('{% load receipts_ui %}' + template).render(Context(context))


class TestStatusPill:
    @pytest.mark.parametrize(
        ('status', 'label', 'colour'),
        [
            (ReceiptStatus.PROCESSED, 'Processed', 'green'),
            (ReceiptStatus.NEEDS_REVIEW, 'Needs review', 'amber'),
            (ReceiptStatus.RECEIVED, 'Received', 'blue'),
            (ReceiptStatus.PROCESSING, 'Processing', 'blue'),
            (ReceiptStatus.WAITING_FOR_CREDITS, 'Waiting for credits', 'gray'),
            (ReceiptStatus.FAILED, 'Failed', 'red'),
        ],
    )
    def test_each_status_has_its_label_and_colour_with_a_dark_twin(self, status, label, colour):
        html = render('{% status_pill status %}', status=status.value)

        assert label in html
        # one tinted pill (apps.core.templatetags.core_ui): a fill and text with a dark twin each
        assert 'rounded-full' in html
        if colour == 'gray':
            assert (
                'bg-gray-100' in html
                and 'dark:bg-gray-700' in html
                and 'dark:text-gray-300' in html
            )
        else:
            assert f'bg-{colour}-50' in html and f'dark:bg-{colour}-400/10' in html
            assert f'text-{colour}-700' in html and f'dark:text-{colour}-300' in html
        assert 'aria-hidden="true"' in html  # the icon is decoration; the label is the meaning

    def test_light_text_steps_follow_the_contrast_rules(self):
        # -700 text on a -50 fill is 4.5:1 or better for every coloured tone, which 12px text needs
        # (docs/ui-design/README.md, "Pills").
        assert 'text-green-700' in render('{% status_pill "processed" %}')
        assert 'text-amber-700' in render('{% status_pill "needs_review" %}')
        assert 'text-blue-700' in render('{% status_pill "processing" %}')
        assert 'text-red-700' in render('{% status_pill "failed" %}')

    def test_an_unknown_status_still_renders(self):
        html = render('{% status_pill "mystery_state" %}')

        assert 'Mystery state' in html

    def test_the_label_is_escaped(self):
        assert '<script>' not in render('{% status_pill "<script>" %}')

    def test_only_a_receipt_in_progress_spins(self):
        assert 'fa-spin' in render('{% status_pill "processing" %}')
        assert 'fa-spin' not in render('{% status_pill "processed" %}')


class TestVendorChip:
    def test_a_vetted_vendor(self):
        html = render('{% vendor_chip True %}')

        assert 'Vetted' in html and 'text-green-700' in html and 'dark:text-green-300' in html
        assert 'fa-circle-check' in html and 'aria-hidden="true"' in html

    def test_a_new_vendor_is_a_neutral_gray_not_a_warning(self):
        html = render('{% vendor_chip False %}')

        assert 'New vendor' in html and 'text-gray-600' in html and 'dark:text-gray-300' in html
        assert 'amber' not in html and 'red' not in html

    def test_the_short_form_for_the_vendors_page(self):
        assert '>New</span>' in render('{% vendor_chip False True %}')
        assert 'Vetted' in render('{% vendor_chip True True %}')


class TestHelpers:
    @pytest.mark.parametrize('reason', list(FailureReason))
    def test_every_failure_reason_has_an_explanation_and_a_next_step(self, reason):
        assert receipts_ui.failure_reason_text(reason).endswith('.')
        assert receipts_ui.failure_next_step(reason).endswith('.')

    def test_an_unknown_reason_has_a_fallback(self):
        assert receipts_ui.failure_reason_text('') == "We couldn't process this receipt."
        assert receipts_ui.failure_next_step('') == 'Run the extraction again in a moment.'

    @pytest.mark.parametrize(
        ('size', 'text'),
        [
            (0, '0 KB'),
            (1, '1 KB'),
            (340 * 1024, '340 KB'),
            (int(2.1 * 1024**2), '2.1 MB'),
            (15 * 1024**2, '15 MB'),
        ],
    )
    def test_human_size(self, size, text):
        assert receipts_ui.human_size(size) == text

    def test_missing_fields(self, make_receipt):
        receipt = make_receipt(status=ReceiptStatus.NEEDS_REVIEW, vendor='Staples')

        assert receipts_ui.missing_fields(receipt) == 'amount, date'


class TestEveryPage:
    """Cross-cutting checks that need a rendered page."""

    @pytest.mark.parametrize('name', ['dashboard', 'upload', 'trash', 'settings', 'vendors'])
    def test_pages_follow_the_design_checklist(self, web, name):
        content = web.get(reverse(f'receipts:{name}')).content.decode()

        assert '</title>' in content and ' - ZipTrigo</title>' in content
        assert content.count('<h1') == 1
        assert 'alert(' not in content and 'window.confirm' not in content

    @pytest.mark.parametrize('name', ['dashboard', 'upload', 'trash', 'settings', 'vendors'])
    def test_pages_load_the_shared_script_as_a_classic_script(self, web, name):
        content = web.get(reverse(f'receipts:{name}')).content.decode()

        # Not `defer`: its `alpine:init` listener has to exist before Alpine starts.
        assert '<script src="/static/receipts/js/common.js"></script>' in content

    def test_the_csrf_cookie_is_set_for_pages_that_post_with_script(self, web):
        for name in ('dashboard', 'upload', 'trash', 'settings', 'vendors'):
            assert 'csrftoken' in web.get(reverse(f'receipts:{name}')).cookies
