"""Free slots, the creation price, and what restoring costs."""

from datetime import timedelta

import pytest
from django.core.exceptions import ValidationError
from django.utils import timezone

from apps.billing.models import CreditTransaction
from apps.billing.services import InsufficientCreditsError, add_credits, get_balance

from ..models import Note
from ..services import (
    CostChangedError,
    archive,
    create_note,
    duplicate,
    free_slots_used,
    quote_create,
    quote_restore,
    restore,
    trash,
)
from .factories import make_note

pytestmark = pytest.mark.django_db


@pytest.fixture
def two_slots(config):
    config.free_notes = 2
    config.save()
    return config


class TestCreationBilling:
    def test_free_slots_then_the_price(self, funded_user, two_slots):
        user = funded_user
        before = get_balance(user)
        a = create_note(user, title='A')
        b = create_note(user, title='B')
        assert a.is_free and b.is_free and get_balance(user) == before
        assert quote_create(user) == 3
        c = create_note(user, title='C')
        assert not c.is_free
        assert c.charged_credits == 3
        assert get_balance(user) == before - 3
        # Paid for one renewal period from now.
        assert c.paid_through is not None
        delta = c.paid_through - timezone.now()
        assert timedelta(days=364, hours=23) < delta <= timedelta(days=365)

    def test_the_ledger_line(self, funded_user, two_slots):
        for title in ('A', 'B'):
            create_note(funded_user, title=title)
        create_note(funded_user, title='A very useful note')
        line = CreditTransaction.objects.filter(user=funded_user, source='notes').get()
        assert line.amount == -3
        assert line.description == 'Notes: "A very useful note"'

    def test_trashing_a_free_note_frees_its_slot_for_the_next(self, funded_user, two_slots):
        user = funded_user
        a = create_note(user, title='A')
        create_note(user, title='B')
        assert free_slots_used(user) == 2 and quote_create(user) == 3
        trash(a)
        assert free_slots_used(user) == 1 and quote_create(user) == 0
        assert create_note(user, title='C').is_free

    def test_archived_notes_still_hold_their_slot(self, funded_user, two_slots):
        a = create_note(funded_user, title='A')
        create_note(funded_user, title='B')
        archive(a)
        assert quote_create(funded_user) == 3

    def test_a_paid_note_never_becomes_free_later(self, funded_user, two_slots):
        user = funded_user
        a = create_note(user, title='A')
        create_note(user, title='B')
        paid = create_note(user, title='C')
        trash(a)
        assert not Note.objects.get(pk=paid.pk).is_free
        assert quote_create(user) == 0  # a slot is open again for the next one
        assert create_note(user, title='D').is_free

    def test_a_price_of_zero_makes_it_free_to_create_but_not_a_free_note(
        self, funded_user, two_slots
    ):
        two_slots.creation_price = 0
        two_slots.save()
        for title in ('A', 'B'):
            create_note(funded_user, title=title)
        note = create_note(funded_user, title='C')
        assert not note.is_free and note.charged_credits == 0 and note.paid_through is not None
        assert CreditTransaction.objects.filter(user=funded_user, source='notes').count() == 0

    def test_insufficient_credits_rolls_everything_back(self, user, two_slots):
        for title in ('A', 'B'):
            create_note(user, title=title)
        add_credits(user, 2, description='Not enough', source='test')
        with pytest.raises(InsufficientCreditsError):
            create_note(user, title='C')
        assert Note.objects.filter(user=user).count() == 2
        assert get_balance(user) == 2

    def test_cost_changed(self, funded_user, two_slots):
        for title in ('A', 'B'):
            create_note(funded_user, title=title)
        with pytest.raises(CostChangedError) as exc:
            create_note(funded_user, title='C', expected_cost=0)
        assert exc.value.quote == 3
        assert Note.objects.filter(user=funded_user).count() == 2
        assert create_note(funded_user, title='C', expected_cost=3).charged_credits == 3

    def test_slots_are_per_user(self, funded_user, other_user, two_slots):
        for title in ('A', 'B'):
            create_note(funded_user, title=title)
        assert quote_create(other_user) == 0

    def test_duplicate_is_a_normal_new_note(self, funded_user, two_slots):
        note = create_note(funded_user, title='A')
        assert duplicate(note, funded_user).is_free  # slot 2
        paid_copy = duplicate(note, funded_user, expected_cost=3)
        assert not paid_copy.is_free and paid_copy.charged_credits == 3
        with pytest.raises(CostChangedError):
            duplicate(note, funded_user, expected_cost=0)


class TestRestoreCosts:
    def test_a_free_note_with_a_slot_open_restores_free(self, funded_user, two_slots):
        note = create_note(funded_user, title='A')
        trash(note)
        assert quote_restore(note) == 0
        assert restore(note).is_free

    def test_a_free_note_into_full_slots_is_charged_and_becomes_paid(self, funded_user, two_slots):
        user = funded_user
        first = create_note(user, title='A')
        trash(first)
        create_note(user, title='B')
        create_note(user, title='C')  # the slots are full again
        before = get_balance(user)
        assert quote_restore(first) == 3
        restored = restore(first, expected_cost=3)
        assert not restored.is_trashed and not restored.is_free
        assert restored.paid_through is not None and restored.charged_credits == 3
        assert get_balance(user) == before - 3
        line = CreditTransaction.objects.filter(user=user, source='notes').latest('created_at')
        assert line.description == 'Notes: "A"'

    def test_cost_changed_on_restore(self, funded_user, two_slots):
        user = funded_user
        first = create_note(user, title='A')
        trash(first)
        create_note(user, title='B')
        create_note(user, title='C')
        with pytest.raises(CostChangedError) as exc:
            restore(first, expected_cost=0)
        assert exc.value.quote == 3
        assert Note.objects.get(pk=first.pk).is_trashed

    def test_insufficient_credits_leaves_it_in_the_trash(self, user, two_slots):
        first = create_note(user, title='A')
        trash(first)
        create_note(user, title='B')
        create_note(user, title='C')
        with pytest.raises(InsufficientCreditsError):
            restore(first)
        again = Note.objects.get(pk=first.pk)
        assert again.is_trashed and again.is_free

    def test_a_paid_note_that_passed_its_date_in_the_trash_is_renewed_on_restore(
        self, funded_user, two_slots
    ):
        past = timezone.now() - timedelta(days=2)
        note = make_note(
            funded_user,
            is_free=False,
            paid_through=past,
            locked_at=past,
            deleted_at=timezone.now() - timedelta(days=1),
        )
        before = get_balance(funded_user)
        assert quote_restore(note) == 5
        restored = restore(note, expected_cost=5)
        assert restored.locked_at is None
        assert restored.paid_through > timezone.now() + timedelta(days=364)
        assert get_balance(funded_user) == before - 5
        line = CreditTransaction.objects.filter(user=funded_user, source='notes').get()
        assert line.description == 'Notes renewal: "%s"' % note.title

    def test_a_paid_note_still_paid_through_restores_free(self, funded_user, two_slots):
        future = timezone.now() + timedelta(days=100)
        note = make_note(funded_user, is_free=False, paid_through=future, deleted_at=timezone.now())
        assert quote_restore(note) == 0
        assert restore(note).paid_through == future

    def test_the_cap_applies_to_a_restore(self, funded_user, two_slots):
        two_slots.max_notes_per_user = 1
        two_slots.save()
        gone = make_note(funded_user, deleted_at=timezone.now())
        make_note(funded_user)
        with pytest.raises(ValidationError) as exc:
            restore(gone)
        assert exc.value.messages == ['You have too many notes. Delete some first.']
