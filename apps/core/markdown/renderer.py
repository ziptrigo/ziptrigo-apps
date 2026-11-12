"""The HTML renderer rules of `apps.core.markdown`: what each token becomes.

Installed on the shared parser by `parser.get_parser`. They only overwrite what the spec changes:
links (and what counts as one), images (rendered as links), a fence's language class, task
references and checklist boxes. `render.render` sanitises whatever they write.
"""

import re
from collections.abc import Sequence

import pygments
from markdown_it import MarkdownIt
from markdown_it.common.utils import escapeHtml
from markdown_it.renderer import RendererHTML
from markdown_it.token import Token
from markdown_it.utils import EnvType, OptionsDict
from pygments.formatters.html import HtmlFormatter
from pygments.lexers import get_lexer_by_name

from .inline_text import inline_text
from .links import LinkKind, classify_href

#: `env` key holding `{number: url}` from the caller's reference resolver.
TASK_URLS = 'task_urls'
#: `env` key holding the stack of open links (`True`: the link was written as an anchor). Links
#: cannot nest, so it is at most one deep; its length is how an image knows it sits inside a link.
_OPEN_LINKS = 'open_links'

_LANGUAGE_CHARS = re.compile(r'[^a-z0-9+\-]')
_MAX_LANGUAGE_LENGTH = 20
#: A fenced block longer than this (characters) is never coloured (the `extended` profile).
MAX_HIGHLIGHT_CHARS = 20_000
#: Pygments' class prefix; `build_highlight_css` writes the matching stylesheet.
HIGHLIGHT_PREFIX = 'hl-'
_ALIGNMENT = re.compile(r'text-align:\s*(left|center|right)')


def _task_url(number: object, env: EnvType) -> str | None:
    """The URL the resolver gave `number`, if there is one a link may point at."""
    url = env.get(TASK_URLS, {}).get(number)
    if isinstance(url, str) and classify_href(url) is not None:
        return url
    return None


def _anchor_open(href: str, number: int | None, env: EnvType) -> str | None:
    """The opening `<a>` tag for a destination, or None when it must not be an anchor."""
    if number is not None and (url := _task_url(number, env)) is not None:
        return f'<a href="{escapeHtml(url)}" class="task-ref">'
    kind = classify_href(href)
    target = escapeHtml(href)
    if kind is LinkKind.EXTERNAL:
        return f'<a href="{target}" target="_blank" rel="noopener noreferrer nofollow ugc">'
    if kind is LinkKind.MAIL:
        return f'<a href="{target}" rel="nofollow ugc">'
    if kind in (LinkKind.RELATIVE, LinkKind.SAME_SITE):
        return f'<a href="{target}">'
    return None


def _href(token: Token, name: str) -> str:
    value = token.attrGet(name)
    return value if isinstance(value, str) else ''


def _open_links(env: EnvType) -> list[bool]:
    return env.setdefault(_OPEN_LINKS, [])


def link_open(
    self: RendererHTML, tokens: Sequence[Token], idx: int, options: OptionsDict, env: EnvType
) -> str:
    token = tokens[idx]
    opening = _anchor_open(_href(token, 'href'), token.meta.get('task_number'), env)
    _open_links(env).append(opening is not None)
    return opening or ''


def link_close(
    self: RendererHTML, tokens: Sequence[Token], idx: int, options: OptionsDict, env: EnvType
) -> str:
    stack = _open_links(env)
    return '</a>' if stack and stack.pop() else ''


def image(
    self: RendererHTML, tokens: Sequence[Token], idx: int, options: OptionsDict, env: EnvType
) -> str:
    """Images are never rendered: a link with the alt text (the URL when there is none)."""
    token = tokens[idx]
    src = _href(token, 'src')
    label = escapeHtml(inline_text(token.children or []) or src)
    opening = None if _open_links(env) else _anchor_open(src, None, env)
    return f'{opening}{label}</a>' if opening else label


def _language(token: Token) -> str:
    """The usable language tag of a fence: what is left of the first word of the info string after
    lower-casing it and dropping everything but `a-z`, `0-9`, `-` and `+`, cut to 20 characters."""
    words = token.info.split(maxsplit=1)
    return _LANGUAGE_CHARS.sub('', words[0].lower())[:_MAX_LANGUAGE_LENGTH] if words else ''


def fence(
    self: RendererHTML, tokens: Sequence[Token], idx: int, options: OptionsDict, env: EnvType
) -> str:
    """A fenced block; its language tag becomes `class="language-<tag>"` when it has a usable one."""
    token = tokens[idx]
    language = _language(token)
    css = f' class="language-{language}"' if language else ''
    return f'<pre><code{css}>{escapeHtml(token.content)}</code></pre>\n'


def task_reference(
    self: RendererHTML, tokens: Sequence[Token], idx: int, options: OptionsDict, env: EnvType
) -> str:
    token = tokens[idx]
    url = _task_url(token.meta['number'], env)
    if url is None:
        return escapeHtml(token.content)
    return f'<a href="{escapeHtml(url)}" class="task-ref">{escapeHtml(token.content)}</a>'


def task_checkbox(
    self: RendererHTML, tokens: Sequence[Token], idx: int, options: OptionsDict, env: EnvType
) -> str:
    meta = tokens[idx].meta
    checked = ' checked' if meta['checked'] else ''
    return (
        f'<input class="task-list-item-checkbox" type="checkbox"{checked} disabled '
        f'data-index="{int(meta["index"])}">'
    )


def install_renderer(md: MarkdownIt) -> None:
    for rule in (link_open, link_close, image, fence, task_reference, task_checkbox):
        md.add_render_rule(rule.__name__, rule)


def highlighted(code: str, language: str) -> str | None:
    """`code` as Pygments spans (no wrapper), or None when it should stay plain: no language, too
    long, a language Pygments doesn't know, or any Pygments error."""
    if not language or len(code) > MAX_HIGHLIGHT_CHARS:
        return None
    try:
        lexer = get_lexer_by_name(language)
        return pygments.highlight(
            code, lexer, HtmlFormatter(nowrap=True, classprefix=HIGHLIGHT_PREFIX)
        )
    except Exception:
        return None


def fence_extended(
    self: RendererHTML, tokens: Sequence[Token], idx: int, options: OptionsDict, env: EnvType
) -> str:
    """`fence` with Pygments colouring when the block has a language Pygments knows."""
    token = tokens[idx]
    language = _language(token)
    body = highlighted(token.content, language)
    if body is None:
        return fence(self, tokens, idx, options, env)
    return f'<pre class="hl"><code class="language-{language}">{body}</code></pre>\n'


def _cell_open(tag: str, token: Token) -> str:
    match = _ALIGNMENT.search(str(token.attrGet('style') or ''))
    css = f' class="text-{match.group(1)}"' if match else ''
    return f'<{tag}{css}>'


def th_open(
    self: RendererHTML, tokens: Sequence[Token], idx: int, options: OptionsDict, env: EnvType
) -> str:
    return _cell_open('th', tokens[idx])


def td_open(
    self: RendererHTML, tokens: Sequence[Token], idx: int, options: OptionsDict, env: EnvType
) -> str:
    return _cell_open('td', tokens[idx])


def table_open(
    self: RendererHTML, tokens: Sequence[Token], idx: int, options: OptionsDict, env: EnvType
) -> str:
    return '<div class="md-table"><table>\n'


def table_close(
    self: RendererHTML, tokens: Sequence[Token], idx: int, options: OptionsDict, env: EnvType
) -> str:
    return '</table></div>\n'


def install_extended_renderer(md: MarkdownIt) -> None:
    md.add_render_rule('fence', fence_extended)
    for rule in (th_open, td_open, table_open, table_close):
        md.add_render_rule(rule.__name__, rule)
