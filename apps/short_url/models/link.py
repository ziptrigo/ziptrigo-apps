import uuid
from datetime import datetime
from typing import TYPE_CHECKING, ClassVar, cast

from django.conf import settings
from django.core.exceptions import ObjectDoesNotExist
from django.db import models

if TYPE_CHECKING:
    from django.db.models import Manager

    from apps.accounts.models import User


class LinkStatus(models.TextChoices):
    """What the owner (or staff) did to a link. Whether a link works *right now* also depends on
    its expiry, click cap and review hold: see `services.links.is_available`."""

    ACTIVE = 'active', 'Active'
    DISABLED = 'disabled', 'Disabled'
    #: Terminal: removed by staff. The owner sees why, never who reported it.
    TAKEN_DOWN = 'taken_down', 'Taken down'
    #: Terminal: the owner's soft delete. The row stays so the code is never issued again.
    DELETED = 'deleted', 'Deleted'


class RetiredCode(models.Model):
    """A code that may never be issued again, kept after the link that held it is gone.

    A link's own row keeps its code reserved (a soft delete), but deleting the *owner* removes
    their rows (`ShortLink.owner` cascades), which would free every code and alias for anyone to
    take over, while printed and shared links still point at them. A `pre_delete(User)` receiver
    (`apps.py`) therefore copies the owner's `code_key`s here first, and
    `services.codes.is_available` refuses any key found here.
    """

    objects: ClassVar['Manager']

    code_key = cast(str, models.CharField(max_length=32, unique=True))
    retired_at = cast(datetime, models.DateTimeField(auto_now_add=True))

    def __str__(self) -> str:
        return self.code_key


class ShortLink(models.Model):
    """One short link: `<BASE_URL>/s/<code>` redirects to `destination` (issue #162).

    The code is permanent. A deleted link keeps its row (and so its `code_key`), so an old printed
    link can never be taken over by someone else's new one; the alias can't be changed after
    creation for the same reason.
    """

    objects: ClassVar['Manager']
    DoesNotExist: ClassVar[type[ObjectDoesNotExist]]

    # A foreign key's auto `_id` companion attribute, which `ty` can't see.
    owner_id: uuid.UUID
    # Set by the dashboard view on the links it lists (not stored): the credits enabling this
    # disabled link would cost, so its menu can ask first.
    enable_cost: int

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    owner = cast(
        'User',
        models.ForeignKey(
            settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='short_links'
        ),
    )

    code = cast(
        str,
        models.CharField(
            max_length=32,
            help_text="The code as shown in the link (a random one, or the owner's alias).",
        ),
    )
    #: `code`, lower-cased: what the redirect looks up. Unique across random codes and aliases,
    #: so `/s/Sale` and `/s/sale` can't both exist, while a visitor may type either.
    code_key = cast(str, models.CharField(max_length=32, unique=True, editable=False))
    is_alias = cast(bool, models.BooleanField(default=False))

    destination = cast(str, models.CharField(max_length=2048))
    title = cast(str, models.CharField(max_length=255, blank=True))
    #: Lower-cased, at most 20 (`services.links.normalize_tags`).
    tags = cast('list[str]', models.JSONField(default=list, blank=True))

    status = cast(
        str,
        models.CharField(max_length=12, choices=LinkStatus.choices, default=LinkStatus.ACTIVE),
    )
    expires_at = cast('datetime | None', models.DateTimeField(null=True, blank=True))
    max_clicks = cast('int | None', models.PositiveIntegerField(null=True, blank=True))
    #: A Django password hash, or null for a link without one. Visitors are asked for it on
    #: `/s/<code>/unlock` first (`views.password`); setting one is the paid `password` extra.
    password_hash = cast(
        'str | None', models.CharField(max_length=255, null=True, blank=True, editable=False)
    )
    #: Whether the headline counts, the click cap and the statistics leave out clicks flagged as
    #: bots (crawlers, link previews). The events are recorded either way; see `services.clicks`.
    filter_bots = cast(bool, models.BooleanField(default=True))

    click_count = cast(int, models.PositiveBigIntegerField(default=0))
    bot_click_count = cast(int, models.PositiveBigIntegerField(default=0))
    last_clicked_at = cast('datetime | None', models.DateTimeField(null=True, blank=True))

    # When each one-off extra was paid for (null: not yet). Turning an extra off and on again, or
    # re-enabling a link, never charges twice.
    alias_paid_at = cast('datetime | None', models.DateTimeField(null=True, blank=True))
    password_paid_at = cast('datetime | None', models.DateTimeField(null=True, blank=True))
    analytics_paid_at = cast('datetime | None', models.DateTimeField(null=True, blank=True))
    extra_link_paid_at = cast('datetime | None', models.DateTimeField(null=True, blank=True))

    #: Abuse review (a later phase): a held link is unavailable and frozen for its owner.
    held_for_review_at = cast('datetime | None', models.DateTimeField(null=True, blank=True))
    taken_down_at = cast('datetime | None', models.DateTimeField(null=True, blank=True))
    taken_down_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='+',
    )
    #: Shown to the owner, never who reported the link.
    takedown_reason = cast(str, models.TextField(blank=True))
    #: Staff only; never shown to the owner.
    takedown_internal_note = cast(str, models.TextField(blank=True))
    deleted_at = cast('datetime | None', models.DateTimeField(null=True, blank=True))

    created_at = cast(datetime, models.DateTimeField(auto_now_add=True))
    updated_at = cast(datetime, models.DateTimeField(auto_now=True))

    class Meta:
        indexes = [
            models.Index(fields=['owner', 'status', '-created_at'], name='short_link_owner_idx'),
        ]
        ordering = ['-created_at']

    def __str__(self) -> str:
        return self.code
