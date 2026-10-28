"""The one Markdown parser behind rendering, plain text and checklist editing.

CommonMark with the `tasklists` plugin, bare-URL linkify and `breaks`, without tables,
strikethrough or raw HTML (`html=False`: HTML in the source is escaped, never rendered). Sharing it
is what keeps `render`, `to_plain_text` and `toggle_checklist_item` agreeing on what the text means.
"""

import threading

from markdown_it import MarkdownIt
from mdit_py_plugins.tasklists import tasklists_plugin

from .references import install_references
from .renderer import install_renderer
from .tasklist import install_checkbox_numbering


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
