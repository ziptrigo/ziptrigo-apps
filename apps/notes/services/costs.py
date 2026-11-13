"""What Notes cost, for the Costs page (`apps.core.costs`)."""

from django.db.models import Count, Min

from apps.accounts.models import User
from apps.core.costs import CostSection, charge, credits_text, free, plural
from apps.core.datetime_format import format_date

from ..models import Note, NotesSettings


def cost_section(user: User) -> CostSection:
    config = NotesSettings.load()
    allowance = config.free_notes
    every = f'every {plural(config.renewal_days, "day")}'
    lines = []
    if allowance:
        lines.append(
            free(
                f'Your first {plural(allowance, "note")}',
                'Free for as long as they live. Trashing one frees its place.',
            )
        )
    lines += [
        charge(
            f'Each note beyond {allowance:,}' if allowance else 'Create a note',
            config.creation_price,
            'to create',
            'You confirm the cost first. Duplicating a note, or restoring one from the Trash '
            'when your free notes are all in use, counts as a new note.',
        ),
        charge(
            'Renewal of a paid note',
            config.renewal_price,
            every,
            'Taken automatically on the renewal date.',
        ),
        free('Tags, history, sharing, search and saved filters'),
    ]

    live = Note.objects.filter(user=user, deleted_at__isnull=True)
    usage = []
    if allowance:
        used = live.filter(is_free=True).count()
        usage.append(f'You have {min(used, allowance):,} of {allowance:,} free notes.')
    if config.renewal_price > 0:
        renewing = live.filter(is_free=False, locked_at__isnull=True).aggregate(
            count=Count('pk'), next_renewal=Min('paid_through')
        )
        count = renewing['count']
        if count:
            verb = 'renews' if count == 1 else 'renew'
            usage.append(
                f'{plural(count, "paid note")} {verb} {every}; the next renewal is on '
                f'{format_date(renewing["next_renewal"], "full")} for '
                f'{credits_text(config.renewal_price)}.'
            )
    locked = live.filter(locked_at__isnull=False).count()
    if locked:
        verb = 'is' if locked == 1 else 'are'
        usage.append(f'{plural(locked, "note")} {verb} locked until the renewal is paid.')

    return CostSection(
        lines=lines,
        usage=usage,
        footnote="Out of credits: a note whose renewal can't be paid is locked (read-only) until "
        'you top up.',
    )
