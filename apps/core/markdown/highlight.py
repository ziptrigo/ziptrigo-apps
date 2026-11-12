"""Pygments colouring of fenced code in the `extended` profile.

Pygments' lexers are hand-written backtracking regular expressions (several have had ReDoS
advisories), and a fence's language tag is the author's choice, on a page that anonymous visitors
can open and reload. So only an allow-list of common languages is coloured (`HIGHLIGHT_LANGUAGES`:
fence tag -> Pygments alias), each resolved to its lexer class once at import; every other tag,
real Pygments language or not, renders as `basic` does. The work is bounded as well: a fence over
`MAX_HIGHLIGHT_CHARS` stays plain, and a document spends at most
`MAX_HIGHLIGHT_CHARS_PER_DOCUMENT` characters of colouring (tracked in the render `env`); a fence
past the budget stays plain too.
"""

from collections.abc import MutableMapping
from typing import Any

import pygments
from pygments.formatters.html import HtmlFormatter
from pygments.lexer import Lexer
from pygments.lexers import find_lexer_class_by_name

#: Pygments' class prefix; `build_highlight_css` writes the matching stylesheet.
HIGHLIGHT_PREFIX = 'hl-'
#: A fenced block longer than this (characters) is never coloured.
MAX_HIGHLIGHT_CHARS = 20_000
#: The most characters of colouring one document gets, over all its fences.
MAX_HIGHLIGHT_CHARS_PER_DOCUMENT = 40_000
#: `env` key holding the characters coloured so far in this render.
HIGHLIGHTED_CHARS = 'highlighted_chars'

#: Fence tag (lower-case, as `renderer._language` leaves it) -> Pygments lexer alias.
HIGHLIGHT_LANGUAGES: dict[str, str] = {
    'python': 'python',
    'py': 'python',
    'typescript': 'typescript',
    'ts': 'typescript',
    'javascript': 'javascript',
    'js': 'javascript',
    'jsx': 'jsx',
    'tsx': 'tsx',
    'sh': 'bash',
    'bash': 'bash',
    'shell': 'bash',
    'zsh': 'bash',
    'console': 'console',
    'powershell': 'powershell',
    'ps1': 'powershell',
    'json': 'json',
    'yaml': 'yaml',
    'yml': 'yaml',
    'toml': 'toml',
    'ini': 'ini',
    'xml': 'xml',
    'html': 'html',
    'css': 'css',
    'scss': 'scss',
    'sql': 'sql',
    'go': 'go',
    'rust': 'rust',
    'rs': 'rust',
    'java': 'java',
    'kotlin': 'kotlin',
    'kt': 'kotlin',
    'c': 'c',
    'cpp': 'cpp',
    'c++': 'cpp',
    'csharp': 'csharp',
    'cs': 'csharp',
    'ruby': 'ruby',
    'rb': 'ruby',
    'php': 'php',
    'swift': 'swift',
    'dart': 'dart',
    'lua': 'lua',
    'r': 'r',
    'perl': 'perl',
    'scala': 'scala',
    'haskell': 'haskell',
    'elixir': 'elixir',
    'erlang': 'erlang',
    'clojure': 'clojure',
    'dockerfile': 'docker',
    'makefile': 'make',
    'nginx': 'nginx',
    'diff': 'diff',
    'markdown': 'markdown',
    'md': 'markdown',
    'graphql': 'graphql',
    'hcl': 'hcl',
    'terraform': 'terraform',
    'tf': 'terraform',
    'text': 'text',
    'plaintext': 'text',
}

#: Fence tag -> lexer class, resolved once. A lexer is instantiated per call (they carry options).
_LEXERS: dict[str, type[Lexer]] = {
    tag: find_lexer_class_by_name(alias) for tag, alias in HIGHLIGHT_LANGUAGES.items()
}
_FORMATTER = HtmlFormatter(nowrap=True, classprefix=HIGHLIGHT_PREFIX)


def highlighted(code: str, language: str, env: MutableMapping[str, Any]) -> str | None:
    """`code` as Pygments spans (no wrapper), or None when it should stay plain: a tag outside the
    allow-list, too long, the document's budget spent, or any Pygments error."""
    lexer_class = _LEXERS.get(language)
    if lexer_class is None or len(code) > MAX_HIGHLIGHT_CHARS:
        return None
    spent = env.get(HIGHLIGHTED_CHARS, 0)
    if spent + len(code) > MAX_HIGHLIGHT_CHARS_PER_DOCUMENT:
        return None
    try:
        body = pygments.highlight(code, lexer_class(), _FORMATTER)
    except Exception:
        return None
    env[HIGHLIGHTED_CHARS] = spent + len(code)
    return body
