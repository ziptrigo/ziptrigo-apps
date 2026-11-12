"""Template helpers for the Notes pages: the rendered Markdown, the tag chip and who made a
version.

Load with `{% load notes_ui %}`.
"""

import colorsys
import logging
import re
from collections.abc import Sequence

from django import template
from django.template.defaultfilters import linebreaksbr
from django.utils.html import format_html
from django.utils.safestring import SafeString

from apps.core.markdown import render

from ..models import EditorKind, Note, NoteVersion, Tag

logger = logging.getLogger(__name__)

register = template.Library()


def render_text(text: str) -> SafeString:
    """`text` as sanitised HTML in the `extended` profile (tables, coloured code). Notes never
    resolve task references. A text over the Markdown size guard (the services cap at it) is shown
    unrendered, escaped, rather than failing the page."""
    try:
        return render(text, profile='extended')
    except ValueError:
        logger.warning('Note text of %d characters shown unrendered', len(text))
        return format_html('<p>{}</p>', linebreaksbr(text, autoescape=True))


@register.simple_tag
def render_note(note: Note) -> SafeString:
    """The note's content as HTML."""
    return render_text(note.content)


@register.simple_tag
def render_markdown(text: str) -> SafeString:
    """Any Notes text (a preview, a version) as HTML."""
    return render_text(text)


#: The card the chips sit on in dark mode (Tailwind `gray-800`), and the contrast WCAG asks of text.
_DARK_CARD = (0x28, 0x2E, 0x2C)
_MIN_CONTRAST = 4.5
_HEX_COLOR = re.compile(r'#[0-9a-fA-F]{6}')


def _channel(value: float) -> float:
    value /= 255
    return value / 12.92 if value <= 0.03928 else ((value + 0.055) / 1.055) ** 2.4


def _luminance(rgb: Sequence[float]) -> float:
    red, green, blue = (_channel(part) for part in rgb)
    return 0.2126 * red + 0.7152 * green + 0.0722 * blue


def _contrast(first: Sequence[float], second: Sequence[float]) -> float:
    lighter, darker = sorted((_luminance(first), _luminance(second)), reverse=True)
    return (lighter + 0.05) / (darker + 0.05)


def dark_mode_color(color: str) -> str:
    """`color` (`#rrggbb`) lightened, keeping its hue, until it reads on the dark card with the
    chip's own 20% tint on it (contrast of at least 4.5:1); a colour that already does is returned
    as it is. (To-do's label chip, with the same card and threshold.)"""
    if not _HEX_COLOR.fullmatch(color):  # the model validates it; a stray value is left alone
        return color
    red, green, blue = (int(color[index : index + 2], 16) for index in (1, 3, 5))
    background = tuple(
        0.8 * card + 0.2 * part for card, part in zip(_DARK_CARD, (red, green, blue))
    )
    hue, lightness, saturation = colorsys.rgb_to_hls(red / 255, green / 255, blue / 255)
    candidate = (red, green, blue)
    while _contrast(candidate, background) < _MIN_CONTRAST and lightness < 1:
        lightness = min(lightness + 0.02, 1)
        candidate = tuple(
            round(part * 255) for part in colorsys.hls_to_rgb(hue, lightness, saturation)
        )
    return '#{:02x}{:02x}{:02x}'.format(*candidate)


@register.simple_tag
def tag_chip(tag: Tag) -> SafeString:
    """The tag as a chip: its colour at 20% as the background and as the text. In dark mode the
    text is lightened where the raw colour would be unreadable on the dark card: `--chip-dark`,
    which the layout's CSS applies under `.dark`. The description is the native tooltip."""
    return format_html(
        '<span class="tag-chip inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium"'
        ' style="background-color: {}33; color: {}; --chip-dark: {}"{}>{}</span>',
        tag.color,
        tag.color,
        dark_mode_color(tag.color),
        format_html(' title="{}"', tag.description) if tag.description else '',
        tag.name,
    )


@register.simple_tag
def version_author(version: NoteVersion) -> str:
    """Who made a version, for the owner's history: `You`, the editor's account name for another
    signed-in user, `Someone` for anyone else (and for an account that has since been deleted),
    plus `via a shared link` when it was made through one."""
    if version.editor_kind == EditorKind.OWNER:
        who = 'You'
    elif version.editor_kind == EditorKind.USER and version.editor is not None:
        who = version.editor.name or 'Someone'
    else:
        who = 'Someone'
    if getattr(version, 'link_id', None):
        who += ' via a shared link'
    return who
