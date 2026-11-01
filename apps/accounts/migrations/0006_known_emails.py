"""`KnownEmail` (issue #160): the keyed hashes of every address ever confirmed on an account,
backfilled from the accounts that exist now.

The backfill covers confirmed accounts only (an unconfirmed one never proved its address) and
cannot recover an address an existing account has already moved away from. Oldest account first,
so when two accounts share a canonical address the older one is its holder. The canonical form and
the hash are written out here, a frozen copy of `services.known_emails`, so the keys this writes
keep their meaning whatever that module becomes.
"""

import hashlib
import hmac

from django.conf import settings
from django.db import migrations, models

GMAIL_DOMAINS = frozenset({'gmail.com', 'googlemail.com'})
BATCH = 500


def canonical_email(email):
    email = email.strip().lower()
    local, at, domain = email.rpartition('@')
    if not at or not local:
        return email
    base = local.split('+', 1)[0] or local
    if domain in GMAIL_DOMAINS:
        base = base.replace('.', '') or base
        domain = 'gmail.com'
    return f'{base}@{domain}'


def fingerprint(email):
    key = settings.SECRET_KEY.encode()
    message = b'known-email:' + canonical_email(email).encode()
    return hmac.new(key, message, hashlib.sha256).hexdigest()


def backfill(apps, schema_editor):
    User = apps.get_model('accounts', 'User')
    KnownEmail = apps.get_model('accounts', 'KnownEmail')
    users = (
        User.objects.filter(email_confirmed=True)
        .order_by('created_at', 'pk')
        .values_list('pk', 'email')
    )
    batch = []
    for pk, email in users.iterator(chunk_size=BATCH):
        batch.append(KnownEmail(fingerprint=fingerprint(email), first_user_id=pk))
        if len(batch) >= BATCH:
            KnownEmail.objects.bulk_create(batch, ignore_conflicts=True)
            batch = []
    if batch:
        KnownEmail.objects.bulk_create(batch, ignore_conflicts=True)


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0005_user_timezone'),
    ]

    operations = [
        migrations.CreateModel(
            name='KnownEmail',
            fields=[
                ('fingerprint', models.CharField(max_length=64, primary_key=True, serialize=False)),
                ('first_user_id', models.UUIDField()),
                ('created_at', models.DateTimeField(auto_now_add=True)),
            ],
            options={
                'verbose_name': 'Known email',
                'verbose_name_plural': 'Known emails',
            },
        ),
        migrations.RunPython(backfill, migrations.RunPython.noop),
    ]
