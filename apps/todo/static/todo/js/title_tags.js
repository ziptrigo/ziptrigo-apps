/*
 * `@label` and `#project` pills under the quick-add title (loaded deferred by todo/layout.html).
 *
 * A container with `data-title-tags` holds the title input (`data-parse-input`) and an empty
 * `data-tag-pills` box. While the user types, every tag of the title -- `@` or `#` at the start of a
 * word, then at least one character -- is matched against the form's own labels (the `labels`
 * checkboxes, `data-tag-name` and `data-tag-color`) and projects (the options of the `project`
 * select, `data-tag-color`). Each match shows as a pill, at most 3 per tag; nothing is fetched, so the
 * pills follow every key. A click on a pill confirms it:
 *
 *  - a label: its checkbox is ticked;
 *  - a project: the Project select chooses it;
 *
 * and the tag is taken out of the title (its trailing punctuation stays, the white space is tidied
 * the way the server's `title_tags` does it). The title's `input` event is fired, so the date chip
 * (title_parse.js) reads the new title again.
 *
 * Matching ignores case, accents, spaces, `-` and `_`: a name starting with the typed text matches
 * (an exact name first), then a name with a later word starting with it. `#` followed by digits only
 * is a task reference (`#1234`), never a project. A pill that is not clicked changes nothing: the
 * server still reads a tag that names a project or label exactly when the task is created
 * (apps/todo/services/title_tags.py).
 *
 * Also here: the Labels button of a task form says how many labels are ticked ("2 selected") as the
 * boxes change (`data-labels-summary`), which a confirmed pill relies on.
 */
(function () {
    'use strict';

    var PER_TAG = 3;
    var MAX_PILLS = 6;
    var TAG = /(^|\s)([#@])(\S+)/g;
    var TRAILING = /[.,;:!?)\]}"']+$/;

    function key(text) {
        return text.normalize('NFKD').replace(/[̀-ͯ]/g, '')
            .replace(/[\s_-]+/g, '').toLowerCase();
    }

    function wordKeys(name) {
        return name.normalize('NFKD').replace(/[̀-ͯ]/g, '')
            .toLowerCase().split(/[\s_-]+/).filter(Boolean);
    }

    // What the form offers: `{kind, id, name, color, dark, control}`.
    function options(form) {
        var items = { '@': [], '#': [] };
        form.querySelectorAll('input[type="checkbox"][name="labels"][data-tag-name]').forEach(function (box) {
            items['@'].push({
                kind: 'label', id: box.value, name: box.dataset.tagName,
                color: box.dataset.tagColor || '', dark: box.dataset.tagDark || '', control: box,
            });
        });
        var select = form.querySelector('select[name="project"]');
        if (select) {
            Array.prototype.forEach.call(select.options, function (option) {
                if (!option.value) return;
                items['#'].push({
                    kind: 'project', id: option.value, name: option.textContent.trim(),
                    color: option.dataset.tagColor || '', control: select,
                });
            });
        }
        return items;
    }

    // The items `typed` names, best first: an exact name, a name that starts with it, a name with a
    // later word that starts with it.
    function matches(items, typed) {
        var wanted = key(typed);
        if (!wanted) return [];
        var ranked = [];
        items.forEach(function (item, index) {
            var whole = key(item.name);
            var rank = whole === wanted ? 0
                : whole.indexOf(wanted) === 0 ? 1
                : wordKeys(item.name).some(function (word) { return word.indexOf(wanted) === 0; }) ? 2
                : -1;
            if (rank >= 0) ranked.push({ item: item, rank: rank, index: index });
        });
        ranked.sort(function (a, b) { return a.rank - b.rank || a.index - b.index; });
        return ranked.slice(0, PER_TAG).map(function (entry) { return entry.item; });
    }

    // The tags of `title`: `{sign, typed, start, end}`, `end` being where the name stops (trailing
    // punctuation is not part of it).
    function tags(title) {
        var found = [];
        var match;
        TAG.lastIndex = 0;
        while ((match = TAG.exec(title)) !== null) {
            var sign = match[2];
            var typed = match[3].replace(TRAILING, '');
            if (!typed || (sign === '#' && /^\d+$/.test(typed))) continue;
            var start = match.index + match[1].length;
            found.push({ sign: sign, typed: typed, start: start, end: start + 1 + typed.length });
        }
        return found;
    }

    function tidy(text) {
        return text.replace(/\s+/g, ' ').replace(/\s+([.,;:!?])/g, '$1').trim();
    }

    function pill(tag, item) {
        var button = document.createElement('button');
        button.type = 'button';
        button.dataset.tagPill = item.kind;
        button.dataset.tagId = item.id;
        button.className = 'btn inline-flex items-center gap-2 rounded-full border border-sage-300 dark:border-gray-600 '
            + 'bg-sage-50 dark:bg-gray-700 px-3 py-1 text-sm text-gray-800 dark:text-gray-100 '
            + 'hover:bg-sage-100 dark:hover:bg-gray-600';
        var mark = document.createElement(item.kind === 'label' ? 'i' : 'span');
        mark.setAttribute('aria-hidden', 'true');
        if (item.kind === 'label') {
            mark.className = 'fas fa-tag label-chip';
            mark.style.color = item.color;
            if (item.dark) mark.style.setProperty('--chip-dark', item.dark);
        } else {
            mark.className = 'h-2.5 w-2.5 flex-shrink-0 rounded-full';
            mark.style.backgroundColor = item.color;
        }
        var text = document.createElement('span');
        text.textContent = item.name;
        var check = document.createElement('i');
        check.className = 'fas fa-check text-green-700 dark:text-green-400';
        check.setAttribute('aria-hidden', 'true');
        button.append(mark, text, check);
        button.setAttribute('aria-label', item.kind === 'label'
            ? 'Add the label ' + item.name
            : 'Put the task in the project ' + item.name);
        button.title = (item.kind === 'label' ? 'Add the label ' : 'Put the task in the project ') + item.name;
        button.dataset.tagText = tag.sign + tag.typed;
        return button;
    }

    function setup(box) {
        if (box.dataset.tagsWired) return;
        var input = box.querySelector('[data-parse-input]');
        var pills = box.querySelector('[data-tag-pills]');
        var form = box.closest('form');
        if (!input || !pills || !form) return;
        box.dataset.tagsWired = '1';
        var status = box.querySelector('[data-tag-status]');
        var shown = 0;
        var found = [];  // the pills' items, in the order of the buttons

        function render() {
            var items = options(form);
            pills.replaceChildren();
            found = [];
            tags(input.value).forEach(function (tag) {
                matches(items[tag.sign], tag.typed).forEach(function (item) {
                    if (found.length >= MAX_PILLS) return;
                    found.push({ tag: tag, item: item });
                    pills.appendChild(pill(tag, item));
                });
            });
            pills.hidden = found.length === 0;
            if (status && found.length !== shown) {
                status.textContent = found.length
                    ? found.length + (found.length === 1 ? ' suggestion' : ' suggestions') + ' below the title.'
                    : '';
            }
            shown = found.length;
        }

        function confirm(entry) {
            var item = entry.item;
            var tag = entry.tag;
            // The title may have changed since the pill was drawn: the tag must still be where it was.
            if (input.value.slice(tag.start, tag.end) !== tag.sign + tag.typed) {
                render();
                return;
            }
            if (item.kind === 'label') {
                item.control.checked = true;
            } else {
                item.control.value = item.id;
            }
            item.control.dispatchEvent(new Event('change', { bubbles: true }));
            var before = input.value.slice(0, tag.start);
            var after = input.value.slice(tag.end);
            var head = tidy(before);
            var caret;
            if (after.trim()) {
                input.value = tidy(before + ' ' + after);
                caret = Math.min(head.length + (head ? 1 : 0), input.value.length);
            } else {
                // The tag ended the title: leave a space, so the next word (another tag) starts one.
                input.value = head ? head + ' ' : '';
                caret = input.value.length;
            }
            input.focus();
            input.setSelectionRange(caret, caret);
            input.dispatchEvent(new Event('input', { bubbles: true }));
            // After the re-render the `input` event caused, which has nothing new to say.
            if (status) status.textContent = item.kind === 'label'
                ? 'Added the label ' + item.name + '.'
                : 'Project set to ' + item.name + '.';
        }

        input.addEventListener('input', render);
        pills.addEventListener('click', function (event) {
            var button = event.target.closest('[data-tag-pill]');
            if (!button) return;
            var index = Array.prototype.indexOf.call(pills.children, button);
            if (index >= 0 && found[index]) confirm(found[index]);
        });
        render();
        box.dataset.tagsReady = '1';  // wired: the browser tests wait for it before typing
    }

    // The Labels button of a task form counts the ticked boxes.
    document.addEventListener('change', function (event) {
        var target = event.target;
        if (!target || target.name !== 'labels' || target.type !== 'checkbox') return;
        var form = target.closest('form');
        var summary = form && form.querySelector('[data-labels-summary]');
        if (!summary) return;
        var count = form.querySelectorAll('input[type="checkbox"][name="labels"]:checked').length;
        summary.textContent = count ? count + ' selected' : 'Add labels';
    });

    htmx.onLoad(function (root) {
        var scope = root.querySelectorAll ? root : document;
        scope.querySelectorAll('[data-title-tags]').forEach(setup);
        if (root.matches && root.matches('[data-title-tags]')) setup(root);
    });
})();
