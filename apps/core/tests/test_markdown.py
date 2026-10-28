"""`apps.core.markdown`: rendering, search text and checklist editing (issue #176)."""

import re
import threading

import pytest
from django.template import Context, Template
from django.utils.safestring import SafeString

from apps.core.markdown import (
    MAX_MARKDOWN_CHARS,
    count_checklist_items,
    normalize_for_search,
    render,
    search_text,
    to_plain_text,
    toggle_checklist_item,
)
from apps.core.markdown.links import LinkKind, classify_href

EXTERNAL = 'target="_blank" rel="noopener noreferrer nofollow ugc"'


def resolver(known: dict[int, str]):
    """A resolver that records its calls and knows `known`."""
    calls: list[list[int]] = []

    def resolve(numbers: list[int]) -> dict[int, str]:
        calls.append(list(numbers))
        return {n: known[n] for n in numbers if n in known}

    resolve.calls = calls  # ty: ignore[unresolved-attribute]
    return resolve


# --- Supported syntax ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ('source', 'html'),
    [
        ('plain text', '<p>plain text</p>'),
        ('*italic* and _italic_', '<p><em>italic</em> and <em>italic</em></p>'),
        ('**bold** and __bold__', '<p><strong>bold</strong> and <strong>bold</strong></p>'),
        ('some `code` here', '<p>some <code>code</code> here</p>'),
        ('> quoted', '<blockquote>\n<p>quoted</p>\n</blockquote>'),
        ('---', '<hr>'),
        ('    indented code', '<pre><code>indented code\n</code></pre>'),
        ('- a\n- b', '<ul>\n<li>a</li>\n<li>b</li>\n</ul>'),
        ('1. a\n2. b', '<ol>\n<li>a</li>\n<li>b</li>\n</ol>'),
        ('3. a\n4. b', '<ol start="3">\n<li>a</li>\n<li>b</li>\n</ol>'),
    ],
)
def test_block_and_inline_syntax(source, html):
    assert render(source).strip() == html


@pytest.mark.parametrize('level', range(1, 7))
def test_headings_have_no_id(level):
    assert render(f'{"#" * level} Title').strip() == f'<h{level}>Title</h{level}>'


def test_seven_hashes_is_not_a_heading():
    assert '<h' not in render('####### Title')


def test_nested_lists():
    html = render('- a\n  - b\n    1. c\n- d')
    assert html.count('<ul>') == 2
    assert '<ol>' in html
    assert '<li>c</li>' in html


def test_single_newline_is_a_line_break():
    assert render('one\ntwo').strip() == '<p>one<br>\ntwo</p>'


def test_blockquote_can_hold_other_blocks():
    html = render('> # Title\n> - item')
    assert '<blockquote>' in html and '<h1>Title</h1>' in html and '<li>item</li>' in html


def test_returns_safe_string_and_empty_input():
    assert isinstance(render('x'), SafeString)
    assert render('') == ''


# --- Fenced code --------------------------------------------------------------------------------


def test_fence_with_language_class():
    assert render('```python\nx = 1 < 2\n```').strip() == (
        '<pre><code class="language-python">x = 1 &lt; 2\n</code></pre>'
    )


def test_fence_language_is_lower_cased_and_filtered():
    assert 'class="language-c++"' in render('```C++\nx\n```')
    assert 'class="language-c"' in render('```c#\nx\n```')
    assert 'class="language-ab"' in render('```a"><b\nx\n```')


def test_fence_language_is_cut_to_twenty_characters():
    assert f'class="language-{"a" * 20}"' in render(f'```{"a" * 40}\nx\n```')


def test_fence_without_usable_language_has_no_class():
    for fence in ('```\nx\n```', '```!!!\nx\n```', '```  \nx\n```'):
        assert render(fence).strip() == '<pre><code>x\n</code></pre>'


def test_fence_uses_the_first_word_of_the_info_string():
    assert 'class="language-js"' in render('```js title="x"\nx\n```')


def test_code_is_not_interpreted():
    html = render('```\n**no** #1234 <b>\n```', reference_resolver=resolver({1234: '/todo/1234/'}))
    assert '**no** #1234 &lt;b&gt;' in html
    assert 'task-ref' not in html


# --- Unsupported syntax stays literal -----------------------------------------------------------


def test_raw_html_is_escaped():
    html = render('<script>alert(1)</script> <b onclick="x">hi</b>')
    assert '<script' not in html and '<b ' not in html
    assert '&lt;script&gt;alert(1)&lt;/script&gt;' in html


def test_html_block_is_escaped():
    html = render('<div>\nhello\n</div>')
    assert '<div' not in html
    assert '&lt;div&gt;' in html


def test_tables_strikethrough_and_footnotes_are_literal():
    html = render('| a | b |\n|---|---|\n| 1 | 2 |')
    assert '<table' not in html and '| a | b |' in html
    assert '<s>' not in render('~~gone~~') and '~~gone~~' in render('~~gone~~')
    assert render('text[^1]').strip() == '<p>text[^1]</p>'
    assert 'footnote' not in render('text[^1]\n\n[^1]: note')


def test_attribute_injection_is_neutralised():
    html = render('[x](https://a.com/"onmouseover="alert(1))')
    assert '" onmouseover=' not in html
    assert re.findall(r'\s(on\w+)=', html) == []


# --- Links --------------------------------------------------------------------------------------


def test_external_links_open_in_a_new_tab():
    assert render('[site](https://example.com/a?b=1)').strip() == (
        f'<p><a href="https://example.com/a?b=1" {EXTERNAL}>site</a></p>'
    )
    assert f'<a href="http://example.com" {EXTERNAL}>x</a>' in render('[x](http://example.com)')


def test_site_relative_links_have_no_target_or_rel():
    assert render('[page](/account/)').strip() == '<p><a href="/account/">page</a></p>'


def test_mailto_links_have_rel_only():
    assert render('[mail](mailto:a@example.com)').strip() == (
        '<p><a href="mailto:a@example.com" rel="nofollow ugc">mail</a></p>'
    )


def test_bare_urls_become_links():
    html = render('see https://example.com/x and www.example.org')
    assert f'<a href="https://example.com/x" {EXTERNAL}>https://example.com/x</a>' in html
    assert f'<a href="http://www.example.org" {EXTERNAL}>www.example.org</a>' in html


@pytest.mark.parametrize(
    'href',
    [
        'javascript:alert(1)',
        'JaVaScRiPt:alert(1)',
        'data:text/html;base64,PHNjcmlwdD4=',
        'vbscript:msgbox(1)',
        '//evil.example.com/x',
        'file:///etc/passwd',
        'ftp://example.com/x',
        'relative/path',
        '#fragment',
    ],
)
def test_other_destinations_render_the_text_without_an_anchor(href):
    html = render(f'[click me]({href}) end')
    assert '<a' not in html
    assert 'href' not in html
    assert html.strip() == '<p>click me end</p>'


def test_backslash_is_not_a_site_relative_destination():
    # markdown-it percent-encodes it, so the browser never reads `/\\host` as `//host`; the
    # classifier refuses the raw form too.
    assert 'href="/%5Cevil.example.com"' in render('[x](/\\evil.example.com)')
    assert classify_href('/\\evil.example.com') is None
    assert classify_href('//evil.example.com') is None
    assert classify_href('/ok') is LinkKind.RELATIVE


def test_unsafe_autolink_is_text():
    html = render('<javascript:alert(1)> <https://example.com>')
    assert 'href="javascript' not in html
    assert f'<a href="https://example.com" {EXTERNAL}>' in html


# --- Images -------------------------------------------------------------------------------------


def test_image_is_a_link_with_its_alt_text():
    assert render('![a cat](https://img.example.com/cat.png)').strip() == (
        f'<p><a href="https://img.example.com/cat.png" {EXTERNAL}>a cat</a></p>'
    )
    assert '<img' not in render('![a cat](https://img.example.com/cat.png)')


def test_image_without_alt_uses_the_url():
    html = render('![](https://img.example.com/cat.png)')
    assert f'{EXTERNAL}>https://img.example.com/cat.png</a>' in html


def test_image_with_an_unsafe_source_is_text_only():
    html = render('![pic](data:image/png;base64,AAAA) ![](javascript:x)')
    assert '<a' not in html and '<img' not in html
    assert 'pic' in html


def test_site_relative_image_is_a_plain_link():
    assert render('![logo](/static/logo.png)').strip() == (
        '<p><a href="/static/logo.png">logo</a></p>'
    )


def test_image_inside_a_link_does_not_nest_anchors():
    html = render('[![alt](https://img.example.com/a.png)](https://example.com)')
    assert html.count('<a ') == 1
    assert 'alt' in html


# --- Task references ----------------------------------------------------------------------------

TASKS = {1234: '/todo/1234/', 2000: '/todo/2000/'}


def test_reference_links_through_the_resolver():
    resolve = resolver(TASKS)
    html = render('Do #1234 first', reference_resolver=resolve)
    assert html.strip() == '<p>Do <a href="/todo/1234/" class="task-ref">#1234</a> first</p>'
    assert resolve.calls == [[1234]]


def test_resolver_is_called_once_with_deduplicated_numbers_in_order():
    resolve = resolver(TASKS)
    html = render(
        '#2000 then #1234, and again #2000 and `#3000` and [x](/todo/1234/)\n\n- #1234',
        reference_resolver=resolve,
    )
    assert resolve.calls == [[2000, 1234]]
    assert html.count('class="task-ref"') == 5


def test_resolver_is_not_called_without_references():
    resolve = resolver(TASKS)
    render('nothing to see, #12 and #999', reference_resolver=resolve)
    assert resolve.calls == []


@pytest.mark.parametrize('source', ['#999', '#1', '#12', '#0', '#99999999999', 'ticket#1234'])
def test_these_are_not_references(source):
    resolve = resolver({n: f'/todo/{n}/' for n in (1, 12, 999, 1234, 99999999999)})
    html = render(source, reference_resolver=resolve)
    assert 'task-ref' not in html
    assert resolve.calls == []


@pytest.mark.parametrize(
    'source',
    [
        '#1234',
        '(#1234)',
        '[#1234]',
        '{#1234}',
        '"#1234"',
        "'#1234'",
        'see,#1234',
        '#1234.',
        '#1234, ok',
        '#1234; ok',
        '#1234: ok',
        '#1234! #1234?',
        'a\n#1234',
        '- #1234',
        '# Heading #1234',
    ],
)
def test_these_are_references(source):
    html = render(source, reference_resolver=resolver(TASKS | {1234: '/todo/1234/'}))
    assert '<a href="/todo/1234/" class="task-ref">#1234</a>' in html


@pytest.mark.parametrize(
    'source', ['#1234abc', '#1234x', '*#1234*', '**#1234**', '#1234-5', '#1234/']
)
def test_neighbouring_characters_prevent_a_reference(source):
    html = render(source, reference_resolver=resolver(TASKS))
    assert 'task-ref' not in html


def test_reference_in_code_span_and_in_link_text_is_text():
    resolve = resolver(TASKS)
    html = render('`#1234` and [about #1234](https://example.com)', reference_resolver=resolve)
    assert 'task-ref' not in html
    assert '<code>#1234</code>' in html
    assert resolve.calls == []


def test_reference_nine_digits_long():
    html = render('#123456789', reference_resolver=resolver({123456789: '/todo/123456789/'}))
    assert 'class="task-ref">#123456789</a>' in html


def test_reference_the_resolver_omits_stays_text():
    resolve = resolver({1234: '/todo/1234/'})
    html = render('#1234 and #1235', reference_resolver=resolve)
    assert resolve.calls == [[1234, 1235]]
    assert html.strip() == '<p><a href="/todo/1234/" class="task-ref">#1234</a> and #1235</p>'


def test_references_are_text_without_a_resolver():
    html = render('#1234 and [x](/todo/1234/)')
    assert 'task-ref' not in html
    assert '#1234' in html
    assert '<a href="/todo/1234/">x</a>' in html


def test_resolver_answers_for_numbers_not_asked_are_ignored():
    def resolve(numbers):
        return {1234: '/todo/1234/', 4321: '/todo/4321/'}

    html = render('#1234', reference_resolver=resolve)
    assert '4321' not in html


def test_resolver_cannot_produce_an_unsafe_link():
    html = render(
        '#1234 #2000',
        reference_resolver=resolver({1234: 'javascript:alert(1)', 2000: '//evil.com'}),
    )
    assert '<a' not in html
    assert 'javascript' not in html


def test_site_relative_task_url_is_a_reference():
    resolve = resolver({1234: '/todo/1234/'})
    html = render(
        '[the task](/todo/1234/) and /todo/1234 [no slash](/todo/1234)', reference_resolver=resolve
    )
    assert resolve.calls == [[1234]]
    assert html.count('<a href="/todo/1234/" class="task-ref">') == 2
    assert '>the task</a>' in html and '>no slash</a>' in html


def test_bare_task_url_keeps_its_text(settings):
    settings.BASE_URL = 'https://app.example.com'
    html = render(
        'https://app.example.com/todo/1234/',
        reference_resolver=resolver({1234: '/todo/1234/'}),
    )
    assert html.strip() == (
        '<p><a href="/todo/1234/" class="task-ref">https://app.example.com/todo/1234/</a></p>'
    )


def test_absolute_task_url_of_this_site_is_a_reference(settings):
    settings.BASE_URL = 'https://app.example.com'
    resolve = resolver({1234: '/todo/1234/'})
    html = render('[go](https://app.example.com/todo/1234/)', reference_resolver=resolve)
    assert resolve.calls == [[1234]]
    assert html.strip() == '<p><a href="/todo/1234/" class="task-ref">go</a></p>'


@pytest.mark.parametrize(
    'url',
    [
        'https://other.example.com/todo/1234/',
        'https://app.example.com/todo/1234/?x=1',
        'https://app.example.com/todo/1234/#c',
        'https://app.example.com/todo/1234/edit',
        'https://app.example.com/other/1234/',
        'https://app.example.com.evil.com/todo/1234/',
    ],
)
def test_other_urls_are_ordinary_links(settings, url):
    settings.BASE_URL = 'https://app.example.com'
    resolve = resolver({1234: '/todo/1234/'})
    html = render(f'[go]({url})', reference_resolver=resolve)
    assert resolve.calls == []
    assert 'task-ref' not in html
    assert f'{EXTERNAL}>go</a>' in html


def test_task_url_the_resolver_omits_stays_an_ordinary_link(settings):
    settings.BASE_URL = 'https://app.example.com'
    html = render('[go](https://app.example.com/todo/1234/)', reference_resolver=resolver({}))
    assert 'task-ref' not in html
    assert f'<a href="https://app.example.com/todo/1234/" {EXTERNAL}>go</a>' in html


def test_task_url_without_a_resolver_is_an_ordinary_link(settings):
    settings.BASE_URL = 'https://app.example.com'
    html = render('[go](https://app.example.com/todo/1234/)')
    assert 'task-ref' not in html
    assert f'{EXTERNAL}>go</a>' in html


# --- Checklists ---------------------------------------------------------------------------------


def test_checkboxes_render_disabled_with_increasing_data_index():
    html = render('- [ ] one\n  - [x] two\n- plain\n- [X] three')
    boxes = re.findall(r'<input[^>]*>', html)
    assert len(boxes) == 3
    assert [re.findall(r'data-index="(\d+)"', box) for box in boxes] == [['0'], ['1'], ['2']]
    assert all('disabled' in box and 'type="checkbox"' in box for box in boxes)
    assert ['checked' in box for box in boxes] == [False, True, True]
    assert html.count('<li class="task-list-item">') == 3


def test_checkboxes_inside_quotes_and_after_other_lists_keep_counting():
    html = render('- [ ] a\n\ntext\n\n> - [ ] b\n\n1. [x] c')
    assert re.findall(r'data-index="(\d+)"', html) == ['0', '1', '2']


def test_data_index_is_the_index_toggle_uses():
    doc = '- [ ] a\n  - [ ] b\n> - [ ] c\n- [ ] d'
    html = render(doc)
    for index, letter in enumerate('abcd'):
        assert re.search(rf'data-index="{index}"[^<]*> {letter}', html)
        lines = toggle_checklist_item(doc, index, True).split('\n')
        assert ['[x]' in line for line in lines].count(True) == 1
        assert f'[x] {letter}' in lines['abcd'.index(letter)]


def test_only_real_checklist_items_get_a_box():
    html = render('- [ ]\n- [ ]x\n- [] y\n- text [ ] more\n```\n- [ ] code\n```')
    assert '<input' not in html


def test_checkbox_text_is_kept():
    html = render('- [ ] buy **milk**')
    assert '> buy <strong>milk</strong></li>' in html


# --- Sanitising ---------------------------------------------------------------------------------


def test_output_only_has_allowed_tags_and_attributes():
    html = render(
        '# h\n\n*a* **b** `c` [d](https://e.com)\n\n> q\n\n- [ ] x\n\n```py\nz\n```\n\n---\n\n1. y'
    )
    tags = set(re.findall(r'<(\w+)', html))
    assert tags <= set('p br strong em code pre h1 ul ol li input blockquote hr a'.split())
    attributes = set(re.findall(r'\s([\w-]+)=', html))
    assert attributes <= {
        'href',
        'target',
        'rel',
        'class',
        'type',
        'checked',
        'disabled',
        'data-index',
        'start',
    }


# --- Length guard -------------------------------------------------------------------------------


def test_document_length_limit():
    assert MAX_MARKDOWN_CHARS == 50_000
    assert render('a' * MAX_MARKDOWN_CHARS)
    with pytest.raises(ValueError, match='Document too long'):
        render('a' * (MAX_MARKDOWN_CHARS + 1))


# --- Template filter ----------------------------------------------------------------------------


def test_markdown_filter():
    html = Template('{% load core_markdown %}{{ text|markdown }}').render(
        Context({'text': '**hi** <b>there</b> #1234'})
    )
    assert html.strip() == '<p><strong>hi</strong> &lt;b&gt;there&lt;/b&gt; #1234</p>'


def test_markdown_filter_accepts_none():
    assert (
        Template('{% load core_markdown %}[{{ text|markdown }}]').render(Context({'text': None}))
        == '[]'
    )


def test_parser_works_from_other_threads():
    results: list[str] = []
    errors: list[BaseException] = []

    def work():
        try:
            for _ in range(20):
                results.append(render('see https://example.com **x**').strip())
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=work) for _ in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert not errors
    assert len(set(results)) == 1


# --- toggle_checklist_item / count_checklist_items ----------------------------------------------

NESTED = """# Plan

- [ ] first
  - [x] nested done
  - [ ] nested open
    1. [ ] deep
- not a task
- [X] last   with  spaces

> - [ ] quoted

```
- [ ] in a fence
```
"""


def test_count_checklist_items():
    assert count_checklist_items(NESTED) == 6
    assert count_checklist_items('nothing here') == 0
    assert count_checklist_items('') == 0


@pytest.mark.parametrize(
    ('index', 'before', 'after'),
    [
        (0, '- [ ] first', '- [x] first'),
        (2, '  - [ ] nested open', '  - [x] nested open'),
        (3, '    1. [ ] deep', '    1. [x] deep'),
        (5, '> - [ ] quoted', '> - [x] quoted'),
    ],
)
def test_toggle_flips_only_the_chosen_item(index, before, after):
    assert toggle_checklist_item(NESTED, index, True) == NESTED.replace(before, after)


def test_toggle_changes_exactly_one_marker_byte_for_byte():
    result = toggle_checklist_item(NESTED, 2, True)
    assert result == NESTED.replace('  - [ ] nested open', '  - [x] nested open')
    assert toggle_checklist_item(result, 2, False) == NESTED


def test_toggle_unchecks_and_normalises_the_capital_x():
    assert toggle_checklist_item(NESTED, 4, False) == NESTED.replace('- [X] last', '- [ ] last')
    assert toggle_checklist_item(NESTED, 1, False) == NESTED.replace('- [x] nested', '- [ ] nested')


def test_toggle_to_the_same_state_is_a_no_op():
    assert toggle_checklist_item(NESTED, 0, False) == NESTED
    assert toggle_checklist_item(NESTED, 1, True) == NESTED


def test_toggle_inside_a_block_quote_and_ignores_code_fences():
    result = toggle_checklist_item(NESTED, 5, True)
    assert '> - [x] quoted' in result
    assert '- [ ] in a fence' in result


def test_toggle_preserves_crlf_and_other_line_endings():
    doc = '- [ ] a\r\n- [ ] b\r\n\r\ntext\r\n'
    assert toggle_checklist_item(doc, 1, True) == '- [ ] a\r\n- [x] b\r\n\r\ntext\r\n'
    assert toggle_checklist_item('x\r- [ ] a\r- [ ] b', 1, True) == 'x\r- [ ] a\r- [x] b'
    assert toggle_checklist_item('- [ ] a\n- [ ] b', 1, True) == '- [ ] a\n- [x] b'


def test_toggle_item_whose_text_starts_on_the_next_line():
    doc = '-\n  [ ] later\n- [ ] now'
    assert count_checklist_items(doc) == 2
    assert toggle_checklist_item(doc, 0, True) == '-\n  [x] later\n- [ ] now'


def test_toggle_without_a_trailing_newline_and_with_unicode():
    assert toggle_checklist_item('- [ ] ação ✓', 0, True) == '- [x] ação ✓'


@pytest.mark.parametrize('index', [-1, 6, 100])
def test_toggle_out_of_range_raises(index):
    with pytest.raises(IndexError):
        toggle_checklist_item(NESTED, index, True)


def test_toggle_on_a_document_without_items_raises():
    with pytest.raises(IndexError):
        toggle_checklist_item('just text', 0, True)


@pytest.mark.parametrize('source', ['- [ ]', '- [ ]x', '- [] a', '-[ ] a', '* text [ ] more'])
def test_not_checklist_items(source):
    assert count_checklist_items(source) == 0


# --- to_plain_text / normalize_for_search / search_text -----------------------------------------


def test_plain_text_drops_markup():
    source = '**Pay** the _rent_, see [the lease](https://example.com/lease) and `code`'
    assert to_plain_text(source) == 'Pay the rent, see the lease and code'


def test_plain_text_keeps_bare_urls_and_references():
    assert to_plain_text('go to https://example.com/x for #1234.') == (
        'go to https://example.com/x for #1234.'
    )


def test_plain_text_of_images_is_their_alt_text():
    assert to_plain_text('a ![cat **photo**](https://img.example.com/c.png) b') == 'a cat photo b'
    assert to_plain_text('![](https://img.example.com/c.png)') == ''


def test_plain_text_drops_checklist_markers_heading_marks_and_fences():
    assert to_plain_text('- [ ] buy milk\n- [x] pay rent') == 'buy milk\npay rent'
    assert to_plain_text('## Title') == 'Title'
    assert to_plain_text('```python\nx = 1\n```') == 'x = 1'


def test_plain_text_separators():
    assert to_plain_text('one\n\ntwo') == 'one\ntwo'
    assert to_plain_text('- a\n  - b\n- c') == 'a\nb\nc'
    assert to_plain_text('# Title\n\nbody') == 'Title body'
    assert to_plain_text('before\n\n```\ncode\n```\n\nafter') == 'before\ncode after'
    assert to_plain_text('> quoted\n\nafter') == 'quoted\nafter'
    assert to_plain_text('a\nb') == 'a\nb'


def test_plain_text_keeps_literal_characters():
    assert to_plain_text('a &amp; b \\*not bold\\* <b>x</b>') == 'a & b *not bold* <b>x</b>'
    assert to_plain_text('') == ''


def test_normalize_for_search_examples():
    assert normalize_for_search('Cão, Ação & Coração!') == 'cao acao coracao'
    assert normalize_for_search('  Hello,\n\tWORLD  ') == 'hello world'
    assert normalize_for_search('Straße') == 'strasse'
    assert normalize_for_search('') == ''
    assert normalize_for_search('!!! ... ???') == ''


def test_normalize_for_search_keeps_digits_and_letters_of_every_script():
    assert normalize_for_search('Привет, МИР! 東京 ١٢٣ Ünï-cøde ﬁne 3.14') == (
        'привет мир 東京 ١٢٣ uni cøde fine 3 14'
    )


def test_normalize_for_search_is_idempotent():
    once = normalize_for_search('Crème brûlée — 50% off!')
    assert once == 'creme brulee 50 off'
    assert normalize_for_search(once) == once


def test_search_text_runs_both_steps():
    assert search_text('**Pay** the RENT') == 'pay the rent'
    assert search_text('- [ ] Café [menu](https://example.com/Menu?x=1) #1234') == 'cafe menu 1234'
    assert search_text('Cão, Ação & Coração!') == 'cao acao coracao'
    assert search_text('') == ''


def test_plain_text_goes_through_normalize_as_the_spec_example():
    assert normalize_for_search(to_plain_text('**Pay** the RENT')) == 'pay the rent'
