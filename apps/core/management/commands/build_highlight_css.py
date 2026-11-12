"""Write the `extended` Markdown profile's stylesheet (syntax colours, tables)."""

from pathlib import Path

from django.core.management.base import BaseCommand

from apps.core.markdown import highlight_css

STATIC = Path(__file__).resolve().parents[2] / 'static'


class Command(BaseCommand):
    help = 'Write core/css/highlight.css from Pygments (apps/core/markdown/highlight_css.py).'

    def handle(self, *args, **options):
        target = STATIC / highlight_css.CSS_PATH
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(highlight_css.render_css())
        self.stdout.write(f'Wrote {target.relative_to(STATIC)}')
