import os
import sys
from datetime import UTC, datetime
from http import HTTPStatus
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.table import Table

from admin import PROJECT_ROOT
from admin.utils import EnvironmentAnnotation, logger, set_environment

app = typer.Typer()


def setup_django(db_file: Path):
    """Configure Django settings to use the specified database file."""
    import django

    sys.path.insert(0, str(PROJECT_ROOT.resolve()))
    os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')

    # Override database settings to use the specified file
    from django.conf import settings

    if not settings.configured:
        django.setup()
    else:
        settings.DATABASES['default']['NAME'] = db_file


@app.command(name='users')
def db_users(
    environment: EnvironmentAnnotation,
    file: Annotated[Path, typer.Option(help='Path to the SQLite database file')] = Path(
        'db.sqlite3'
    ),
):
    """List all users from the database in a table format."""
    set_environment(environment.value)

    if not file.exists():
        logger.info(f'Database file not found: {file}')
        raise typer.Exit(1)

    setup_django(file)

    from django.db.models import Count, Q

    from src.qr_code.models import User

    users = User.objects.annotate(
        num_codes=Count('qrcodes', filter=Q(qrcodes__deleted_at__isnull=True))
    ).all()

    if not users:
        logger.warning('No users found in the database.')
        return

    table = Table(title='Users')
    table.add_column('ID', style='cyan', no_wrap=True)
    table.add_column('Username', style='magenta')
    table.add_column('Email', style='green')
    table.add_column('Name')
    table.add_column('Codes')
    table.add_column('Admin', justify='center')

    for user in users:
        table.add_row(
            str(user.id),
            user.username,
            user.email,
            user.name,
            str(user.num_codes),  # noqa
            'x' if user.is_staff else '',
        )

    console = Console()
    console.print(table)


@app.command(name='data')
def db_data(
    email: Annotated[str, typer.Argument(help='User email address.')],
    password: Annotated[str, typer.Argument(help='User password.')],
    qr_codes: Annotated[int, typer.Argument(help='Number of QR codes to generate for the user.')],
    file: Annotated[Path, typer.Option(help='Path to the SQLite database file')] = Path(
        'db.sqlite3'
    ),
):
    """Create or use existing user and generate QR codes via API."""
    import requests
    from faker import Faker

    if not file.exists():
        logger.info(f'Database file not found: {file}')
        raise typer.Exit(1)

    setup_django(file)

    from django.conf import settings

    from src.qr_code.models import User

    # Check if server is running
    base_url = settings.BASE_URL.rstrip('/')
    try:
        response = requests.get(f'{base_url}/', timeout=2)
        response.raise_for_status()
    except requests.exceptions.RequestException:
        logger.error(
            f'Django server is not running at {base_url}. '
            'Please start the server with: python manage.py runserver'
        )
        raise typer.Exit(1)

    logger.info(f'Server is running at {base_url}')

    # Check if user exists
    user = User.objects.filter(email=email).first()

    if user:
        logger.info(f'Using existing user: `{email}` (password not changed)')
        # If we want to update password for existing user:
        # user.set_password(password)
        # user.save(update_fields=['password'])
    else:
        # Create user via signup API
        logger.info(f'Creating new user: {email}')
        signup_data = {'name': email.split('@')[0], 'email': email, 'password': password}
        response = requests.post(f'{base_url}/api/signup', json=signup_data, timeout=10)

        if response.status_code != HTTPStatus.CREATED:
            logger.error(f'Failed to create user: {response.text}')
            raise typer.Exit(1)

        logger.info('User created successfully')

    # Get the user from DB
    user = User.objects.get(email=email)

    # Make sure email is confirmed directly in DB
    if not user.email_confirmed:
        user.email_confirmed = True
        user.email_confirmed_at = datetime.now(UTC)
        user.save(update_fields=['email_confirmed', 'email_confirmed_at'])
        logger.info('Email marked as confirmed')

    # Login to get session
    session = requests.Session()

    # Get CSRF token from cookies by visiting the site first
    session.get(f'{base_url}/', timeout=10)
    csrf_token = session.cookies.get('csrftoken', '')

    login_data = {'email': email, 'password': password}
    headers = {'X-CSRFToken': csrf_token} if csrf_token else {}
    response = session.post(f'{base_url}/api/login', json=login_data, headers=headers, timeout=10)

    if response.status_code != HTTPStatus.OK:
        logger.error(f'Failed to login: {response.status_code}: {response.text}')
        raise typer.Exit(1)

    logger.info('Logged in successfully')

    # Update CSRF token after login
    csrf_token = session.cookies.get('csrftoken', csrf_token)

    # Initialize faker
    fake = Faker()

    # Create QR codes
    logger.info(f'Creating {qr_codes} QR codes...')
    created_count = 0

    for i in range(qr_codes):
        # Generate random name (3-5 words)
        name = fake.text(max_nb_chars=40).rstrip('.')

        # Generate random content (5-20 words)
        content = fake.text(max_nb_chars=120).rstrip('.')

        qr_data = {
            'name': name,
            'data': content,
            'qr_format': 'png',
            'size': 10,
            'error_correction': 'M',
            'border': 4,
            'background_color': '#FFFFFF',
            'foreground_color': '#000000',
            'use_url_shortening': False,
        }

        headers = {'X-CSRFToken': csrf_token} if csrf_token else {}
        response = session.post(
            f'{base_url}/api/qrcodes/', json=qr_data, headers=headers, timeout=10
        )

        if response.status_code == HTTPStatus.CREATED:
            created_count += 1
            logger.info(f'Created QR code {created_count}/{qr_codes}: {name}')
        else:
            logger.error(f'Failed to create QR code: {response.text}')

    logger.info(f'Successfully created {created_count} QR codes for {email}')


if __name__ == '__main__':
    app()
