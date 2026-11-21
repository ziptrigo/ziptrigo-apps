/*
 * `@label` and `#project` pills under a task title (loaded deferred by todo/layout.html).
 *
 * A container with `data-title-tags` holds the title input (`data-parse-input` or `data-tag-input`)
 * and an empty `data-tag-pills` box. While the user types, every tag of the title -- `@` or `#` at the
 * start of a word, then at least one character -- is matched against what the form offers, and each
 * match shows as a pill, at most 3 per tag; nothing is fetched, so the pills follow every key. What a
 * form offers, and what a click on a pill does:
 *
 *  - a label checkbox (`input[name=labels]` with `data-tag-name`, `data-tag-color`, `data-tag-dark`;
 *    the quick-add box): the box is ticked and the tag is taken out of the title;
 *  - a Project select option (`data-tag-color`; the quick-add box): the select chooses it and the tag
 *    is taken out of the title;
 *  - a plain option (`[data-tag-option]` with the same `data-tag-*`; the subtask form, which has no
 *    label controls): the tag is completed in the title as the label's own name (`@website-relaunch`),
 *    which the server reads when the task is created. A tag that already names an option exactly is
 *    left alone (nothing to suggest).
 *
 * Taking a tag out keeps its trailing punctuation and tidies the white space the way the server's
 * `title_tags` does; a tag at the end leaves a space, so the next tag starts a word. The title's
 * `input` event is fired after a click, so the date chip (title_parse.js) reads the new title again.
 *
 * Matching ignores case, accents, spaces, `-` and `_`: an exact name first, then a name starting with
 * the typed text, then a name with a later word starting with it; a label already ticked, or the
 * project already chosen, comes after the others and says "added". `#` followed by digits only is a
 * task reference (`#1234`), never a project. A pill that is not clicked changes nothing: the server
 * still reads a tag that names a project or label exactly when the task is created
 * (apps/todo/services/title_tags.py).
 *
 * Escape in the title, or the row's "Hide suggestions" button, hides the pills of the tags typed so
 * far: those exact tags are not offered again (typing more of one asks again). A screen reader hears
 * how many suggestions there are once the typing pauses, not at every key.
 *
 * Also here: the Labels button of a task form says how many labels are ticked ("2 selected") as the
 * boxes change (`data-labels-summary`), which a confirmed pill relies on.
 */
(function () {
    'use strict';

    var PER_TAG = 3;
    var MAX_PILLS = 6;
    var ANNOUNCE_PAUSE_MS = 600;
    var TAG = /(^|\s)([#@])(\S+)/g;
    var TRAILING = /[.,;:!?)\]}"']+$/;

    function fold(text) {
        return text.normalize('NFKD').replace(/\p{M}/gu, '').toLowerCase();
    }

    function key(text) {
        return fold(text).replace(/[\s_-]+/g, '');
    }

    function wordKeys(name) {
        return fold(name).split(/[\s_-]+/).filter(Boolean);
    }

    // The tag that names `name` in a title: the sign and the name with its white space turned into `-`.
    function tagText(sign, name) {
        return sign + name.trim().replace(/\s+/g, '-');
    }

    function entryOf(kind, element, control) {
        return {
            kind: kind,
            id: element.value || '',
            name: (element.dataset.tagName || element.textContent).trim(),
            color: element.dataset.tagColor || '',
            dark: element.dataset.tagDark || '',
            control: control,
        };
    }

    // What the form offers: `{'@': [...], '#': [...]}` of `{kind, id, name, color, dark, control}`;
    // `control` is null for a plain option (the tag is completed in the text instead).
    function options(form) {
        var items = { '@': [], '#': [] };
        form.querySelectorAll('input[type="checkbox"][name="labels"][data-tag-name]').forEach(function (box) {
            items['@'].push(entryOf('label', box, box));
        });
        form.querySelectorAll('[data-tag-option][data-tag-name]').forEach(function (option) {
            // A name the server would cut short (it drops a tag's trailing punctuation) can't be typed.
            if (TRAILING.test(option.dataset.tagName)) return;
            items['@'].push(entryOf('label', option, null));
        });
        var select = form.querySelector('select[name="project"]');
        if (select) {
            Array.prototype.forEach.call(select.options, function (option) {
                if (option.value) items['#'].push(entryOf('project', option, select));
            });
        }
        return items;
    }

    function isApplied(candidate) {
        if (!candidate.control) return false;
        return candidate.kind === 'label'
            ? candidate.control.checked
            : candidate.control.value === candidate.id;
    }

    // The items `typed` names, best first: an exact name, a name that starts with it, a name with a
    // later word that starts with it; the ones already applied last.
    function matches(items, typed) {
        var wanted = key(typed);
        if (!wanted) return [];
        var ranked = [];
        items.forEach(function (candidate, index) {
            var whole = key(candidate.name);
            var rank = whole === wanted ? 0
                : whole.indexOf(wanted) === 0 ? 1
                : wordKeys(candidate.name).some(function (word) { return word.indexOf(wanted) === 0; }) ? 2
                : -1;
            if (rank < 0) return;
            // A plain option the tag already names exactly needs no suggestion: the server reads it.
            if (!candidate.control && rank === 0) return;
            ranked.push({ item: candidate, applied: isApplied(candidate), rank: rank, index: index });
        });
        ranked.sort(function (a, b) {
            return (a.applied - b.applied) || a.rank - b.rank || a.index - b.index;
        });
        return ranked.slice(0, PER_TAG);
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

    function describe(candidate) {
        return candidate.kind === 'label'
            ? 'Add the label ' + candidate.name
            : 'Put the task in the project ' + candidate.name;
    }

    function pill(candidate, applied) {
        var button = document.createElement('button');
        button.type = 'button';
        button.dataset.tagPill = candidate.kind;
        button.className = 'btn inline-flex items-center gap-2 rounded-full border border-sage-300 dark:border-gray-600 '
            + 'bg-sage-50 dark:bg-gray-700 px-3 py-1 text-sm text-gray-800 dark:text-gray-100 '
            + 'hover:bg-sage-100 dark:hover:bg-gray-600';
        var mark = document.createElement(candidate.kind === 'label' ? 'i' : 'span');
        mark.setAttribute('aria-hidden', 'true');
        if (candidate.kind === 'label') {
            mark.className = 'fas fa-tag label-chip';
            mark.style.color = candidate.color;
            if (candidate.dark) mark.style.setProperty('--chip-dark', candidate.dark);
        } else {
            mark.className = 'h-2.5 w-2.5 flex-shrink-0 rounded-full';
            mark.style.backgroundColor = candidate.color;
        }
        var text = document.createElement('span');
        text.textContent = candidate.name;
        button.append(mark, text);
        if (applied) {
            var note = document.createElement('span');
            note.className = 'text-xs text-gray-500 dark:text-gray-400';
            note.textContent = 'added';
            button.append(note);
        }
        var check = document.createElement('i');
        check.className = 'fas fa-check text-green-700 dark:text-green-400';
        check.setAttribute('aria-hidden', 'true');
        button.append(check);
        var label = describe(candidate) + (applied ? ' (already added)' : '');
        button.setAttribute('aria-label', label);
        button.title = label;
        return button;
    }

    function hideButton() {
        var button = document.createElement('button');
        button.type = 'button';
        button.dataset.tagHide = '1';
        button.className = 'btn rounded-lg p-1 text-gray-500 dark:text-gray-300 hover:bg-sage-100 dark:hover:bg-gray-600';
        button.setAttribute('aria-label', 'Hide suggestions');
        button.title = 'Hide suggestions (Esc)';
        var icon = document.createElement('i');
        icon.className = 'fas fa-xmark';
        icon.setAttribute('aria-hidden', 'true');
        button.append(icon);
        return button;
    }

    function setup(box) {
        if (box.dataset.tagsWired) return;
        var input = box.querySelector('[data-parse-input], [data-tag-input]');
        var pills = box.querySelector('[data-tag-pills]');
        var form = box.closest('form');
        if (!input || !pills || !form) return;
        box.dataset.tagsWired = '1';
        var status = box.querySelector('[data-tag-status]');
        var announced = 0;      // the number of suggestions a screen reader was last told about
        var announceTimer = null;
        var found = [];         // the pills' entries, in the order of the buttons
        var dismissed = Object.create(null);  // tags (sign and text) the user hid: not offered again

        function say(text) {
            clearTimeout(announceTimer);
            if (status) status.textContent = text;
        }

        // Told once the typing pauses, and only when the number changed: a live region rewritten at
        // every key interrupts the typing.
        function announceLater() {
            clearTimeout(announceTimer);
            announceTimer = setTimeout(function () {
                var count = found.length;
                if (count === announced) return;
                announced = count;
                if (status) {
                    status.textContent = count
                        ? count + (count === 1 ? ' suggestion' : ' suggestions') + ' below the title.'
                        : '';
                }
            }, ANNOUNCE_PAUSE_MS);
        }

        function render() {
            var items = options(form);
            pills.replaceChildren();
            found = [];
            tags(input.value).forEach(function (tag) {
                if (dismissed[tag.sign + tag.typed]) return;
                matches(items[tag.sign], tag.typed).forEach(function (match) {
                    if (found.length >= MAX_PILLS) return;
                    found.push({ tag: tag, item: match.item });
                    pills.appendChild(pill(match.item, match.applied));
                });
            });
            if (found.length) pills.appendChild(hideButton());
            pills.hidden = found.length === 0;
        }

        function hide() {
            tags(input.value).forEach(function (tag) { dismissed[tag.sign + tag.typed] = true; });
            render();
            announced = 0;
            say('Suggestions hidden.');
        }

        // Writes `value` into the title with the caret at `caret`, and tells the other scripts.
        function writeTitle(value, caret) {
            input.value = value;
            input.focus();
            input.setSelectionRange(caret, caret);
            input.dispatchEvent(new Event('input', { bubbles: true }));
        }

        function applyPill(entry) {
            var candidate = entry.item;
            var tag = entry.tag;
            // The title may have changed since the pill was drawn: the tag must still be where it was.
            if (input.value.slice(tag.start, tag.end) !== tag.sign + tag.typed) {
                render();
                return;
            }
            var before = input.value.slice(0, tag.start);
            var after = input.value.slice(tag.end);
            if (!candidate.control) {
                // No control to set: the tag is completed in the title for the server to read.
                var completed = tagText(tag.sign, candidate.name);
                var rest = after.trim() ? after : ' ';
                writeTitle(before + completed + rest, before.length + completed.length + 1);
                announced = 0;
                say('Completed the label ' + candidate.name + '.');
                return;
            }
            if (candidate.kind === 'label') {
                candidate.control.checked = true;
            } else {
                candidate.control.value = candidate.id;
            }
            candidate.control.dispatchEvent(new Event('change', { bubbles: true }));
            var head = tidy(before);
            if (after.trim()) {
                var value = tidy(before + ' ' + after);
                writeTitle(value, Math.min(head.length + (head ? 1 : 0), value.length));
            } else {
                var title = head ? head + ' ' : '';
                writeTitle(title, title.length);
            }
            // After the re-render the `input` event caused, so nothing overwrites it.
            announced = 0;
            say(candidate.kind === 'label'
                ? 'Added the label ' + candidate.name + '.'
                : 'Project set to ' + candidate.name + '.');
        }

        input.addEventListener('input', function () {
            render();
            announceLater();
        });
        input.addEventListener('keydown', function (event) {
            if (event.key !== 'Escape' || pills.hidden) return;
            event.preventDefault();
            hide();
        });
        pills.addEventListener('click', function (event) {
            if (event.target.closest('[data-tag-hide]')) {
                hide();
                input.focus();
                return;
            }
            var button = event.target.closest('[data-tag-pill]');
            if (!button) return;
            var index = Array.prototype.indexOf.call(pills.children, button);
            if (index >= 0 && found[index]) applyPill(found[index]);
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
