"""The admin's "Promo codes" section (issue #70): create a code (typed, or generated when the
code is left blank), a bulk tool for N single-use codes under one campaign (CSV out), a duplicate
action, usage columns and a state filter on the list, a read-only inline of a code's redemptions
and a read-only list of every redemption. Rules the admin enforces, from the design on #70:

- Once a code has been redeemed or reserved (`claimed_count > 0`) its code string and credits are
  read-only: the ledger and the redemptions already say what it paid.
- A code with redemptions can't be deleted, only deactivated (`has_delete_permission`; the FK is
  `PROTECT` as the backstop). Never-used ones can.
- Redemption rows are never added, edited or deleted by hand (`services.promo` writes them).
"""

import csv
from typing import Any
from urllib.parse import urlencode

from django import forms
from django.contrib import admin, messages
from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.db import transaction
from django.db.models import Count, F, Max, Q, QuerySet, Sum
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect, render
from django.urls import path, reverse
from django.utils import timezone
from django.utils.html import format_html

from apps.accounts.http import AuthenticatedHttpRequest

from .models import PromoCode, PromoRedemption, PromoRedemptionStatus
from .models.promo import CAMPAIGN_MAX_LENGTH, MAX_CREDITS, MIN_CREDITS
from .services.promo import generate_code, normalize_code

#: The most codes one bulk request creates; run it again for more.
BULK_MAX = 1000


def _parse_emails(text: str) -> list[str]:
    """One address per line (commas and spaces work too), lower-cased, deduplicated, each a valid
    address."""
    seen: dict[str, None] = {}
    for raw in text.replace(',', '\n').split():
        email = raw.strip().lower()
        if not email:
            continue
        validate_email(email)
        seen.setdefault(email, None)
    return list(seen)


class PromoCodeAdminForm(forms.ModelForm):
    """The add/change form: a blank code is generated; `allowed_emails` is a textarea rather than
    raw JSON; the dates must be in order."""

    allowed_emails = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={'rows': 3}),
        help_text='Only these addresses may redeem, one per line; empty means anyone. Everyone '
        'else is told "Invalid code".',
    )

    class Meta:
        model = PromoCode
        fields = [
            'code',
            'effect',
            'credits',
            'campaign',
            'active',
            'starts_at',
            'ends_at',
            'max_redemptions',
            'new_accounts_within_days',
            'allowed_emails',
            'notes',
        ]

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        if 'code' in self.fields:
            self.fields['code'].required = False
        instance = kwargs.get('instance')
        if instance is not None and 'allowed_emails' in self.fields and not self.is_bound:
            self.initial['allowed_emails'] = '\n'.join(instance.allowed_emails or [])

    def clean_code(self) -> str:
        raw = (self.cleaned_data.get('code') or '').strip()
        if not raw:
            return generate_code()
        try:
            return normalize_code(raw)
        except Exception:
            raise ValidationError('Letters, digits and hyphens only, 4 to 32 characters.') from None

    def clean_allowed_emails(self) -> list[str]:
        return _parse_emails(self.cleaned_data.get('allowed_emails') or '')

    def clean(self) -> dict[str, Any]:
        data = super().clean()
        starts, ends = data.get('starts_at'), data.get('ends_at')
        if starts and ends and ends <= starts:
            self.add_error('ends_at', 'The end must come after the start.')
        return data


class BulkCreateForm(forms.Form):
    """The "Create many" tool: N single-use codes sharing every setting but the code string."""

    count = forms.IntegerField(min_value=1, max_value=BULK_MAX, initial=10)
    prefix = forms.CharField(
        required=False,
        max_length=20,
        help_text='Optional; a code looks like PREFIX-7K3M9QX2WD. Letters, digits and hyphens.',
    )
    credits = forms.IntegerField(min_value=MIN_CREDITS, max_value=MAX_CREDITS)
    campaign = forms.CharField(
        max_length=CAMPAIGN_MAX_LENGTH,
        help_text='Required for a batch: it is how the codes are found and exported later.',
    )
    starts_at = forms.DateTimeField(required=False)
    ends_at = forms.DateTimeField(required=False)
    new_accounts_within_days = forms.IntegerField(required=False, min_value=1)
    notes = forms.CharField(required=False, widget=forms.Textarea(attrs={'rows': 3}))

    def clean_prefix(self) -> str:
        prefix = (self.cleaned_data.get('prefix') or '').strip().upper()
        if prefix:
            try:
                generate_code(prefix)
            except ValidationError as exc:
                raise ValidationError(exc.messages) from None
        return prefix

    def clean(self) -> dict[str, Any]:
        data = super().clean()
        starts, ends = data.get('starts_at'), data.get('ends_at')
        if starts and ends and ends <= starts:
            self.add_error('ends_at', 'The end must come after the start.')
        return data


class StateFilter(admin.SimpleListFilter):
    """Whether a code can be redeemed right now, and if not, why."""

    title = 'state'
    parameter_name = 'state'

    def lookups(self, request, model_admin):
        return [
            ('live', 'Redeemable now'),
            ('scheduled', 'Scheduled (not started)'),
            ('expired', 'Expired'),
            ('exhausted', 'Fully redeemed'),
            ('inactive', 'Inactive'),
        ]

    def queryset(self, request, queryset: QuerySet) -> QuerySet | None:
        now = timezone.now()
        exhausted = Q(max_redemptions__isnull=False, claimed_count__gte=F('max_redemptions'))
        match self.value():
            case 'live':
                return (
                    queryset.filter(active=True)
                    .exclude(starts_at__gt=now)
                    .exclude(ends_at__lte=now)
                    .exclude(exhausted)
                )
            case 'scheduled':
                return queryset.filter(active=True, starts_at__gt=now)
            case 'expired':
                return queryset.filter(ends_at__lte=now)
            case 'exhausted':
                return queryset.filter(exhausted)
            case 'inactive':
                return queryset.filter(active=False)
        return None


class PromoRedemptionInline(admin.TabularInline):
    model = PromoRedemption
    extra = 0
    can_delete = False
    fields = ['user', 'status', 'source', 'credits', 'created_at', 'granted_at', 'ledger']
    readonly_fields = fields
    ordering = ['-created_at']
    show_change_link = False

    def has_add_permission(self, request, obj=None) -> bool:
        return False

    def has_change_permission(self, request, obj=None) -> bool:
        return False

    @admin.display(description='Ledger')
    def ledger(self, obj: PromoRedemption) -> str:
        if obj.transaction_id is None:
            return '-'
        url = reverse('custom_admin:billing_credittransaction_change', args=[obj.transaction_id])
        return format_html('<a href="{}">#{}</a>', url, obj.transaction_id)


class PromoCodeAdmin(admin.ModelAdmin):
    form = PromoCodeAdminForm
    inlines = [PromoRedemptionInline]
    list_display = [
        'code',
        'campaign',
        'credits',
        'usage',
        'credits_granted',
        'active',
        'starts_at',
        'ends_at',
        'last_redeemed_at',
        'created_at',
    ]
    list_filter = [StateFilter, 'active', 'campaign', 'created_at']
    search_fields = ['code', 'campaign', 'notes', 'allowed_emails']
    ordering = ['-created_at']
    date_hierarchy = 'created_at'
    actions = ['duplicate_action', 'export_csv_action']
    fieldsets = [
        (None, {'fields': ['code', 'effect', 'credits', 'campaign', 'active']}),
        ('Validity', {'fields': ['starts_at', 'ends_at', 'max_redemptions']}),
        ('Eligibility', {'fields': ['new_accounts_within_days', 'allowed_emails']}),
        (
            'Origin',
            {'fields': ['notes', 'created_by', 'created_at', 'updated_at', 'claimed_count']},
        ),
    ]
    readonly_fields = ['created_by', 'created_at', 'updated_at', 'claimed_count']

    def get_queryset(self, request: HttpRequest) -> QuerySet:
        granted = Q(redemptions__status=PromoRedemptionStatus.GRANTED)
        return (
            super()
            .get_queryset(request)
            .annotate(
                granted_count=Count('redemptions', filter=granted),
                credits_granted=Sum('redemptions__credits', filter=granted),
                last_redeemed_at=Max('redemptions__granted_at'),
            )
        )

    def get_readonly_fields(self, request: HttpRequest, obj: PromoCode | None = None) -> list[str]:
        fields = list(super().get_readonly_fields(request, obj))
        if obj is not None and obj.is_claimed:
            # Redeemed or reserved: what it pays is on record. Dates, cap, eligibility and notes
            # stay editable.
            fields += ['code', 'effect', 'credits']
        return fields

    def has_delete_permission(self, request: HttpRequest, obj: PromoCode | None = None) -> bool:
        allowed = super().has_delete_permission(request, obj)
        if allowed and obj is not None and obj.redemptions.exists():
            return False
        return allowed

    def save_model(self, request: AuthenticatedHttpRequest, obj: PromoCode, form, change) -> None:
        if not change:
            obj.created_by = request.user  # ty: ignore[invalid-assignment]
        super().save_model(request, obj, form, change)

    # -- list columns --

    @admin.display(description='Used / max')
    def usage(self, obj) -> str:
        pending = obj.claimed_count - obj.granted_count
        text = f'{obj.granted_count}'
        if pending:
            text += f' (+{pending} pending)'
        if obj.max_redemptions is not None:
            text += f' / {obj.max_redemptions}'
        return text

    @admin.display(description='Credits granted', ordering='credits_granted')
    def credits_granted(self, obj) -> int:
        return obj.credits_granted or 0

    @admin.display(description='Last redeemed', ordering='last_redeemed_at')
    def last_redeemed_at(self, obj):
        return obj.last_redeemed_at

    # -- actions --

    @admin.action(description='Duplicate selected code(s) with a new code string')
    def duplicate_action(self, request: AuthenticatedHttpRequest, queryset: QuerySet) -> None:
        if not self.has_add_permission(request):
            self.message_user(request, 'You cannot add promo codes.', messages.ERROR)
            return
        created = []
        with transaction.atomic():
            for source in queryset:
                copy = PromoCode(
                    code=generate_code(),
                    effect=source.effect,
                    credits=source.credits,
                    campaign=source.campaign,
                    notes=source.notes,
                    active=source.active,
                    starts_at=source.starts_at,
                    ends_at=source.ends_at,
                    max_redemptions=source.max_redemptions,
                    new_accounts_within_days=source.new_accounts_within_days,
                    allowed_emails=list(source.allowed_emails or []),
                    created_by=request.user,
                )
                copy.save()
                created.append(copy.code)
        self.message_user(request, f'Created {len(created)} code(s): {", ".join(created)}.')

    @admin.action(description='Export selected code(s) as CSV')
    def export_csv_action(self, request: HttpRequest, queryset: QuerySet) -> HttpResponse:
        return _csv_response(queryset, 'promo_codes.csv')

    # -- the bulk tool and the campaign export --

    def get_urls(self):
        custom = [
            path(
                'bulk/',
                self.admin_site.admin_view(self.bulk_create_view),
                name='billing_promocode_bulk',
            ),
            path(
                'export/',
                self.admin_site.admin_view(self.export_view),
                name='billing_promocode_export',
            ),
        ]
        return custom + super().get_urls()

    def bulk_create_view(self, request: AuthenticatedHttpRequest) -> HttpResponse:
        """Create `count` single-use codes under one campaign, then show them on the list filtered
        by that campaign, with a link to download the batch as CSV."""
        if not self.has_add_permission(request):
            messages.error(request, 'You cannot add promo codes.')
            return redirect('custom_admin:billing_promocode_changelist')
        form = BulkCreateForm(request.POST or None)
        if request.method == 'POST' and form.is_valid():
            data = form.cleaned_data
            codes = []
            with transaction.atomic():
                for _ in range(data['count']):
                    codes.append(
                        PromoCode(
                            code=generate_code(data['prefix']),
                            credits=data['credits'],
                            campaign=data['campaign'],
                            notes=data['notes'],
                            starts_at=data['starts_at'],
                            ends_at=data['ends_at'],
                            max_redemptions=1,
                            new_accounts_within_days=data['new_accounts_within_days'],
                            created_by=request.user,
                        )
                    )
                PromoCode.objects.bulk_create(codes)
            query = urlencode({'campaign': data['campaign']})
            export_url = reverse('custom_admin:billing_promocode_export')
            messages.success(
                request,
                format_html(
                    'Created {} code(s) in campaign "{}". <a href="{}?{}">Download them as CSV</a>.',
                    len(codes),
                    data['campaign'],
                    export_url,
                    query,
                ),
            )
            changelist = reverse('custom_admin:billing_promocode_changelist')
            return redirect(f'{changelist}?{query}')
        context = {
            **self.admin_site.each_context(request),
            'title': 'Create many promo codes',
            'opts': self.model._meta,
            'form': form,
            'bulk_max': BULK_MAX,
        }
        return render(request, 'admin/billing/promocode/bulk_create.html', context)

    def export_view(self, request: HttpRequest) -> HttpResponse:
        """`?campaign=<name>`: every code of that campaign as CSV."""
        if not self.has_view_permission(request):
            messages.error(request, 'You cannot view promo codes.')
            return redirect('custom_admin:index')
        campaign = request.GET.get('campaign', '')
        if not campaign:
            messages.error(request, 'Choose a campaign to export.')
            return redirect('custom_admin:billing_promocode_changelist')
        queryset = self.get_queryset(request).filter(campaign=campaign).order_by('created_at')
        return _csv_response(queryset, f'promo_codes_{campaign}.csv')


def _csv_response(queryset: QuerySet, filename: str) -> HttpResponse:
    response = HttpResponse(content_type='text/csv; charset=utf-8')
    safe = ''.join(c if c.isalnum() or c in '-_.' else '_' for c in filename)
    response['Content-Disposition'] = f'attachment; filename="{safe}"'
    writer = csv.writer(response)
    writer.writerow(
        [
            'code',
            'credits',
            'campaign',
            'active',
            'starts_at',
            'ends_at',
            'max_redemptions',
            'claimed',
        ]
    )
    for code in queryset:
        writer.writerow(
            [
                code.code,
                code.credits,
                code.campaign,
                code.active,
                code.starts_at.isoformat() if code.starts_at else '',
                code.ends_at.isoformat() if code.ends_at else '',
                code.max_redemptions if code.max_redemptions is not None else '',
                code.claimed_count,
            ]
        )
    return response


class PromoRedemptionAdmin(admin.ModelAdmin):
    """Read-only: what did this user redeem, who redeemed this code."""

    list_display = ['code', 'user', 'status', 'source', 'credits', 'created_at', 'granted_at']
    list_filter = ['status', 'source', 'code__campaign', 'created_at']
    search_fields = ['user__email', 'user__name', 'code__code', 'code__campaign']
    readonly_fields = [
        'code',
        'user',
        'status',
        'source',
        'credits',
        'transaction',
        'created_at',
        'granted_at',
        'released_at',
    ]
    date_hierarchy = 'created_at'
    list_select_related = ['code', 'user']

    def has_add_permission(self, request: HttpRequest) -> bool:
        return False

    def has_change_permission(self, request: HttpRequest, obj=None) -> bool:
        return False

    def has_delete_permission(self, request: HttpRequest, obj=None) -> bool:
        return False
