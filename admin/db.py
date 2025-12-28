"""Seed database with test data using Typer CLI."""

import os
from decimal import Decimal
from typing import TypedDict

import django
import typer
from faker import Faker
from rich.console import Console
from rich.table import Table

from admin.utils import logger

# Setup Django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

# These imports need to come after Django setup
from django.db.models import Count  # noqa: E402
from src.models import Purchase, Series, User, VideoProduct  # noqa: E402

app = typer.Typer(help='Seed database with test data')
fake = Faker()


@app.command(name='users')
def db_users():
    """List all users from the database in a table format."""
    users = User.objects.annotate(num_purchases=Count('purchases')).all()

    if not users:
        logger.warning('No users found in the database.')
        return

    table = Table(title='Users')
    table.add_column('ID', style='cyan', no_wrap=True)
    table.add_column('Email', style='green')
    table.add_column('Display Name', style='magenta')
    table.add_column('Purchases')
    table.add_column('Verified', justify='center')
    table.add_column('Staff', justify='center')
    table.add_column('Superuser', justify='center')

    for user in users:
        table.add_row(
            str(user.id),
            user.email,
            user.display_name or '',
            str(user.num_purchases),
            'x' if user.email_verified else '',
            'x' if user.is_staff else '',
            'x' if user.is_superuser else '',
        )

    console = Console()
    console.print(table)


@app.command()
def seed_users(count: int = typer.Option(5, help='Number of users to create')):
    """Create test users."""
    logger.info(f'Creating {count} users...')

    # Create verified users
    for i in range(count):
        email = f'user{i+1}@example.com'
        if not User.objects.filter(email=email).exists():
            user = User.objects.create_user(
                email=email, password='password123', display_name=fake.name()
            )
            user.email_verified = True
            user.save()
            logger.info(f'  Created: {user.email}')

    # Create admin user
    admin_email = 'admin@example.com'
    if not User.objects.filter(email=admin_email).exists():
        admin = User.objects.create_superuser(
            email=admin_email, password='admin123', display_name='Admin User'
        )
        logger.info(f'  Created admin: {admin.email}')

    logger.info('[green]✓ Users created successfully![/green]')


@app.command()
def seed_series(count: int = typer.Option(3, help='Number of series to create')):
    """Create test series."""
    logger.info(f'Creating {count} series...')

    series_data = [
        {
            'title_en': 'Spring Arrangements',
            'title_pt': 'Arranjos de Primavera',
            'description_en': 'Beautiful spring flower arrangements for all occasions',
            'description_pt': 'Belos arranjos de flores de primavera para todas as ocasiões',
        },
        {
            'title_en': 'Summer Bouquets',
            'title_pt': 'Buquês de Verão',
            'description_en': 'Vibrant summer bouquets that bring joy and color',
            'description_pt': 'Buquês de verão vibrantes que trazem alegria e cor',
        },
        {
            'title_en': 'Wedding Florals',
            'title_pt': 'Florais de Casamento',
            'description_en': 'Elegant floral arrangements perfect for weddings',
            'description_pt': 'Arranjos florais elegantes perfeitos para casamentos',
        },
        {
            'title_en': 'Autumn Designs',
            'title_pt': 'Designs de Outono',
            'description_en': 'Warm autumn-inspired flower designs',
            'description_pt': 'Designs de flores inspirados no outono quente',
        },
        {
            'title_en': 'Winter Wonderland',
            'title_pt': 'Maravilha de Inverno',
            'description_en': 'Stunning winter flower arrangements',
            'description_pt': 'Arranjos de flores de inverno deslumbrantes',
        },
    ]

    for i in range(min(count, len(series_data))):
        data = series_data[i]
        series, created = Series.objects.get_or_create(title_en=data['title_en'], defaults=data)
        if created:
            logger.info(f'  Created: {series.title_en}')

    logger.info('[green]✓ Series created successfully![/green]')


class VideoTemplate(TypedDict):
    title_en: str
    title_pt: str
    description_en: str
    description_pt: str
    price: Decimal
    duration: int


@app.command()
def seed_videos(per_series: int = typer.Option(4, help='Number of videos per series')):
    """Create test video products."""
    logger.info(f'Creating {per_series} videos per series...')

    series_list = Series.objects.all()
    if not series_list:
        logger.info('[yellow]⚠ No series found. Run seed-series first.[/yellow]')
        return

    video_templates: list[VideoTemplate] = [
        {
            'title_en': 'Introduction to {}',
            'title_pt': 'Introdução a {}',
            'description_en': 'Learn the basics of {} arrangements',
            'description_pt': 'Aprenda o básico dos arranjos de {}',
            'price': Decimal('19.99'),
            'duration': 900,
        },
        {
            'title_en': 'Advanced {} Techniques',
            'title_pt': 'Técnicas Avançadas de {}',
            'description_en': 'Master advanced {} arrangement techniques',
            'description_pt': 'Domine técnicas avançadas de arranjo de {}',
            'price': Decimal('29.99'),
            'duration': 1500,
        },
        {
            'title_en': '{} Color Theory',
            'title_pt': 'Teoria das Cores de {}',
            'description_en': 'Understanding color combinations in {} florals',
            'description_pt': 'Compreendendo combinações de cores em florais de {}',
            'price': Decimal('24.99'),
            'duration': 1200,
        },
        {
            'title_en': 'Professional {} Designs',
            'title_pt': 'Designs Profissionais de {}',
            'description_en': 'Create professional-quality {} arrangements',
            'description_pt': 'Crie arranjos de {} de qualidade profissional',
            'price': Decimal('39.99'),
            'duration': 1800,
        },
    ]

    for series in series_list:
        for i in range(min(per_series, len(video_templates))):
            template = video_templates[i]
            title_en = template['title_en'].format(series.title_en)
            title_pt = template['title_pt'].format(series.title_pt)

            video, created = VideoProduct.objects.get_or_create(
                series=series,
                title_en=title_en,
                defaults={
                    'title_pt': title_pt,
                    'description_en': template['description_en'].format(series.title_en.lower()),
                    'description_pt': template['description_pt'].format(series.title_pt.lower()),
                    'gumlet_video_id_en': f'video_en_{series.id}_{i+1}',
                    'gumlet_video_id_pt': f'video_pt_{series.id}_{i+1}',
                    'price': template['price'],
                    'duration_seconds': template['duration'],
                },
            )
            if created:
                logger.info(f'  Created: {video.title_en}')

    logger.info('[green]✓ Videos created successfully![/green]')


@app.command()
def seed_purchases(
    user_email: str = typer.Option('user1@example.com', help='User email to create purchases for')
):
    """Create test purchases for a user."""
    logger.info(f'Creating purchases for {user_email}...')

    try:
        user = User.objects.get(email=user_email)
    except User.DoesNotExist:
        logger.info(f'[red]✗ User {user_email} not found.[/red]')
        return

    videos = VideoProduct.objects.all()[:5]
    if not videos:
        logger.info('[yellow]⚠ No videos found. Run seed-videos first.[/yellow]')
        return

    for video in videos:
        purchase, created = Purchase.objects.get_or_create(
            user=user, video_product=video, defaults={'amount_paid': video.price}
        )
        if created:
            logger.info(f'  Purchased: {video.title_en}')

    logger.info('[green]✓ Purchases created successfully![/green]')


@app.command()
def seed_all(
    users: int = typer.Option(5, help='Number of users'),
    series_count: int = typer.Option(3, help='Number of series'),
    videos_per_series: int = typer.Option(4, help='Videos per series'),
):
    """Seed all data (users, series, videos, purchases)."""
    logger.info('🌱 Seeding all data...\n')

    seed_users(users)
    seed_series(series_count)
    seed_videos(videos_per_series)
    seed_purchases('user1@example.com')

    logger.info('[bold green]✅ All data seeded successfully![/bold green]')
    logger.info('\nTest credentials:')
    logger.info('  Admin: admin@example.com / admin123')
    logger.info('  User1: user1@example.com / password123')

@app.command()
def clear(confirm: bool = typer.Option(False, '--yes', '-y', help='Skip confirmation')):
    """Clear all seeded data from the database."""
    if not confirm:
        confirmed = typer.confirm(
            '⚠️  This will delete all users, series, videos, and purchases. Continue?'
        )
        if not confirmed:
            logger.info('Cancelled.')
            raise typer.Abort()

    logger.info('Clearing database...')

    Purchase.objects.all().delete()
    logger.info('  Deleted purchases')

    VideoProduct.objects.all().delete()
    logger.info('  Deleted videos')

    Series.objects.all().delete()
    logger.info('  Deleted series')

    User.objects.all().delete()
    logger.info('  Deleted users')

    logger.info('[green]✓ Database cleared successfully![/green]')


if __name__ == '__main__':
    app()
