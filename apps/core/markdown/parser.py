"""The one Markdown parser behind rendering, plain text and checklist editing.

CommonMark with the `tasklists` plugin, bare-URL linkify (only `http://`, `https://`, `www.` and
e-mail addresses: scheme-less `manage.py` or `README.md` stay text) and `breaks`, without tables,
strikethrough or raw HTML (`html=False`: HTML in the source is escaped, never rendered). Sharing it
is what keeps `render`, `to_plain_text` and `toggle_checklist_item` agreeing on what the text means.
"""

import threading

from linkify_it import LinkifyIt
from linkify_it.main import Match
from markdown_it import MarkdownIt
from mdit_py_plugins.tasklists import tasklists_plugin

from .references import install_references
from .renderer import install_renderer
from .tasklist import install_checkbox_numbering

#: The prefix of a `www.` link, which linkify-it only knows as one kind of its "fuzzy" links.
_WWW = 'www.'


class _WwwNormalizer:
    """linkify-it wants a bound method to normalise a custom schema's match."""

    def normalize(self, match: Match) -> None:
        match.url = f'http://{match.url}'


def _configure_linkify(linkify: LinkifyIt) -> None:
    """Link only what names its scheme or starts with `www.`.

    linkify-it's `fuzzy_link` also links any `name.tld` with a known top-level domain, which turns
    developer file names into links (`manage.py` is Paraguay's, `README.md` Moldova's, and `.sh`,
    `.rs`, `.io` are all countries). It cannot tell those from `www.`-less hosts, and `www.` is not
    a separate option, so `fuzzy_link` goes off and `www.` is added back as a schema of its own:
    after `www.` at least one more label and a top-level one, as for any host.
    """
    linkify.set({'fuzzy_link': False})
    pattern = (
        '^(?:(?:'
        + linkify.re['src_domain']
        + r')\.)+'
        + linkify.re['src_domain_root']
        + linkify.re['src_port']
        + linkify.re['src_host_terminator']
        + linkify.re['src_path']
    )
    linkify.add(_WWW, {'validate': pattern, 'normalize': _WwwNormalizer().normalize})


class _Parser(MarkdownIt):
    def validateLink(self, url: str) -> bool:
        # markdown-it refuses `javascript:`, `data:` ... as link destinations, which leaves
        # `[text](javascript:x)` as literal source. Accept everything here instead and let the
        # renderer decide (`links.classify_href`): such a link renders as its text, no anchor.
        return True


def build_parser() -> MarkdownIt:
    md = _Parser(
        'commonmark', {'html': False, 'linkify': True, 'typographer': False, 'breaks': True}
    )
    # The commonmark preset leaves the `linkify` rules off even with the option set.
    md.enable('linkify')
    # `linkify-it-py` is installed (a declared dependency), so `md.linkify` is set.
    assert md.linkify is not None
    _configure_linkify(md.linkify)
    # Boxes render disabled: toggling goes through the caller's own endpoint.
    md.use(tasklists_plugin, enabled=False, label=False)
    install_references(md)
    install_checkbox_numbering(md)
    install_renderer(md)
    return md


_local = threading.local()


def get_parser() -> MarkdownIt:
    """This thread's parser. `linkify-it-py` keeps match state on the instance, so not shared."""
    md = getattr(_local, 'md', None)
    if md is None:
        md = _local.md = build_parser()
    return md
