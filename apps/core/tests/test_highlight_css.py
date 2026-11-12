"""The `extended` Markdown profile's stylesheet is generated; the committed file must match."""

from pathlib import Path

from django.contrib.staticfiles import finders

from apps.core.markdown import highlight_css


def test_the_committed_stylesheet_is_what_the_generator_writes():
    path = finders.find(highlight_css.CSS_PATH)
    assert path is not None
    assert Path(path).read_text() == highlight_css.render_css(), (
        'core/css/highlight.css is out of date: run `./manage.py build_highlight_css`.'
    )


def test_the_stylesheet_scopes_both_styles_and_styles_tables():
    css = highlight_css.render_css()
    assert 'pre.hl .hl-k' in css
    assert '.dark pre.hl .hl-k' in css
    assert '.md-table' in css and 'overflow-x: auto' in css
