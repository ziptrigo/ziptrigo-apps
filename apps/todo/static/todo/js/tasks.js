/*
 * The To-do pages' scripts, loaded deferred by todo/layout.html (after SortableJS on the pages that
 * reorder). Everything is wired from `htmx.onLoad`, so it also covers whatever htmx swaps in:
 *
 *  - Checklists. The server renders the description's checkboxes disabled. On a task that can be
 *    edited (the description block has `data-checklist-url`) they are enabled here, and a change
 *    posts `index` and `checked` to that URL; the answer is the description block again.
 *  - Clearing a task's date asks first when it has reminders (they are removed with the date).
 *  - Drag and drop. A list with `data-sortable` is sorted with SortableJS by its `.drag-handle`;
 *    the new order of the rows' `data-task-id`s is posted to `data-reorder-url` as `ids[]`, with
 *    `scope` and `parent` when the list has them (`data-scope`, `data-parent`). The handle is
 *    focusable too: the up and down arrow keys move its row and post the order the same way (an
 *    announcement goes to a polite live region), so ordering does not need a mouse. A list with
 *    `data-after-reorder-url` fetches that URL into `data-after-reorder-target` once the order is
 *    saved (the filters page refreshes the sidebar's copy of the list).
 *  - Empty lists. A row action removes the row without re-rendering its list, so a list marked
 *    `data-empty-list` shows its `<template data-empty-state>` when the last row has left (or hides
 *    the section marked `data-vanish-when-empty`) and takes it away when a row is back.
 *  - The tab title follows an inline rename (`#task-panel[data-document-title]`).
 *  - `[data-scroll-hint]` menus (a phone's horizontal scrollers) say whether there is more to
 *    scroll to (`data-more-right`, which the layout's CSS turns into a fade).
 *  - Checklist boxes get an accessible name from their item's text.
 *
 * The completion confirmation for a parent with open subtasks and the Postpone menu need no code
 * here: they use the shared `confirmModal` component and Alpine in the templates.
 */
(function () {
    'use strict';

    // Closing an editor with Cancel or Escape throws away what was typed: the form goes back to the
    // saved text, and the refusal an earlier save left in `slot` (a selector) goes with it. Called
    // from the templates' Alpine handlers.
    window.todoCancelEdit = function (form, slot) {
        if (form && form.reset) form.reset();
        const message = slot ? document.querySelector(slot) : null;
        if (message) message.replaceChildren();
    };

    // The accessible name of a rendered checklist box: the text of its own item (not of a nested list).
    function nameChecklistBoxes(root) {
        root.querySelectorAll('input.task-list-item-checkbox').forEach(function (box) {
            if (box.hasAttribute('aria-label')) return;
            const item = box.closest('li');
            if (!item) return;
            const copy = item.cloneNode(true);
            copy.querySelectorAll('ul, ol, input').forEach(function (node) { node.remove(); });
            const text = copy.textContent.replace(/\s+/g, ' ').trim();
            box.setAttribute('aria-label', text.length > 120 ? text.slice(0, 117) + '...' : (text || 'Checklist item'));
        });
    }

    function enableChecklist(section) {
        const url = section.dataset.checklistUrl;
        if (!url) return;
        section.querySelectorAll('input.task-list-item-checkbox').forEach(function (box) {
            if (box.dataset.todoReady) return;
            box.dataset.todoReady = '1';
            box.disabled = false;
            box.addEventListener('change', function () {
                box.disabled = true;
                // A refusal swaps its message elsewhere and leaves this box in place; a failed request
                // (rejected promise) must not leave the box disabled, nor surface as an unhandled rejection.
                const enable = function () { box.disabled = false; };
                htmx.ajax('POST', url, {
                    target: '#' + section.id,
                    swap: 'outerHTML',
                    values: { index: box.dataset.index, checked: box.checked ? 'true' : 'false' },
                }).then(enable, enable);
            });
        });
    }

    function showProblem(list, html) {
        const slot = document.querySelector(list.dataset.msg || '#todo-msg');
        if (slot) slot.innerHTML = html;
    }

    function postOrder(list) {
        const body = new URLSearchParams();
        list.querySelectorAll(':scope > [data-task-id]').forEach(function (row) {
            body.append('ids[]', row.dataset.taskId);
        });
        if (list.dataset.scope) body.set('scope', list.dataset.scope);
        if (list.dataset.parent) body.set('parent', list.dataset.parent);
        // `HX-Request` makes the views answer a refusal with their message partial.
        fetch(list.dataset.reorderUrl, {
            method: 'POST',
            headers: {
                'X-CSRFToken': getCookie('csrftoken') || '',
                'HX-Request': 'true',
                'Content-Type': 'application/x-www-form-urlencoded',
            },
            body: body,
            credentials: 'same-origin',
        }).then(function (response) {
            if (!response.ok) return response.text().then(function (html) { showProblem(list, html); });
            showProblem(list, '');
            const url = list.dataset.afterReorderUrl;
            const target = list.dataset.afterReorderTarget;
            if (url && target && document.querySelector(target)) {
                htmx.ajax('GET', url, { target: target, swap: 'outerHTML' }).catch(function () {});
            }
        }).catch(function () {
            showProblem(list, '<p class="text-red-600 dark:text-red-400" role="alert">Could not save the new order.</p>');
        });
    }

    function makeSortable(list) {
        if (typeof Sortable === 'undefined' || Sortable.get(list)) return;
        Sortable.create(list, {
            handle: '.drag-handle',
            draggable: '[data-task-id]',
            animation: 150,
            onEnd: function (event) {
                if (event.oldIndex !== event.newIndex) postOrder(list);
            },
        });
    }

    // Keyboard ordering: the up and down arrow keys on a row's handle move the row and save the order
    // (one request for a burst of key presses). The move is announced in a polite live region.
    let live = null;
    function announce(text) {
        if (!live) {
            live = document.createElement('div');
            live.className = 'sr-only';
            live.setAttribute('aria-live', 'polite');
            document.body.appendChild(live);
        }
        live.textContent = '';
        window.setTimeout(function () { live.textContent = text; }, 30);
    }
    const saveTimers = new WeakMap();
    function rowsOf(list) {
        return Array.from(list.children).filter(function (child) { return child.hasAttribute('data-task-id'); });
    }
    document.addEventListener('keydown', function (event) {
        if (event.key !== 'ArrowUp' && event.key !== 'ArrowDown') return;
        const handle = event.target instanceof Element ? event.target.closest('.drag-handle') : null;
        const row = handle && handle.closest('[data-task-id]');
        const list = row && row.parentElement;
        if (!list || !list.hasAttribute('data-sortable')) return;
        event.preventDefault();
        const rows = rowsOf(list);
        const from = rows.indexOf(row);
        const to = from + (event.key === 'ArrowUp' ? -1 : 1);
        if (to < 0 || to >= rows.length) return;
        if (event.key === 'ArrowUp') list.insertBefore(row, rows[to]);
        else list.insertBefore(row, rows[to].nextElementSibling);
        handle.focus();
        const name = handle.dataset.name || 'Item';
        announce(name + ' moved to position ' + (to + 1) + ' of ' + rows.length + '.');
        window.clearTimeout(saveTimers.get(list));
        saveTimers.set(list, window.setTimeout(function () { postOrder(list); }, 400));
    });

    // Lists whose last row has left (an action answered with an empty body) or got one back.
    function refreshEmpty() {
        document.querySelectorAll('ul[data-empty-list]').forEach(function (list) {
            const hasRows = rowsOf(list).length > 0;
            const placeholder = list.querySelector(':scope > [data-empty]');
            const template = list.querySelector(':scope > template[data-empty-state]');
            const section = list.closest('[data-vanish-when-empty]');
            if (hasRows) {
                if (placeholder) placeholder.remove();
                if (section) section.hidden = false;
            } else if (section) {
                section.hidden = true;
            } else if (template && !placeholder) {
                list.insertBefore(template.content.cloneNode(true), template);
            }
        });
    }
    // A timer, not requestAnimationFrame: a tab in the background never runs those.
    let emptyTimer = 0;
    new MutationObserver(function () {
        if (emptyTimer) return;
        emptyTimer = window.setTimeout(function () {
            emptyTimer = 0;
            refreshEmpty();
        }, 30);
    }).observe(document.body, { childList: true, subtree: true });

    // The tab title follows an inline rename of the task page.
    document.addEventListener('htmx:afterSettle', function (event) {
        const panel = document.getElementById('task-panel');
        if (panel && panel.dataset.documentTitle) document.title = panel.dataset.documentTitle;
    });

    // Phone menus: tell the layout's CSS whether more items are hidden to the right.
    function updateScrollHint(list) {
        const more = list.scrollWidth - list.clientWidth - list.scrollLeft > 4;
        list.setAttribute('data-more-right', more ? 'true' : 'false');
    }
    function wireScrollHints(scope) {
        scope.querySelectorAll('[data-scroll-hint]').forEach(function (list) {
            if (!list.dataset.hintReady) {
                list.dataset.hintReady = '1';
                list.addEventListener('scroll', function () { updateScrollHint(list); }, { passive: true });
            }
            updateScrollHint(list);
        });
    }
    window.addEventListener('resize', function () { wireScrollHints(document); });

    // Clearing the date of a task removes its reminders (they are due relative to the date): ask first.
    // Completing a task: mark the row done the moment the circle is clicked (the check draws, the title strikes
    // through; core/css/site.css `.done`), while the request runs and the row's 200 ms swap delay lets it show.
    // If the request fails the row is not done after all.
    document.addEventListener('click', function (event) {
        var circle = event.target instanceof Element ? event.target.closest('[data-complete]') : null;
        if (circle) {
            var row = circle.closest('.task-row');
            if (row) row.classList.add('done');
        }
    });
    document.addEventListener('htmx:afterRequest', function (event) {
        if (event.detail.successful) return;
        var circle = event.target instanceof Element ? event.target.closest('[data-complete]') : null;
        var row = circle && circle.closest('.task-row');
        if (row) row.classList.remove('done');
    });

    // The details form carries the number of reminders in `data-reminder-count`. htmx raises
    // `htmx:confirm` before every request; cancelling it and calling `issueRequest(true)` sends it.
    document.addEventListener('htmx:confirm', function (event) {
        const form = event.target;
        if (!form || !form.matches || !form.matches('form[data-reminder-count]')) return;
        const count = parseInt(form.dataset.reminderCount, 10);
        const date = form.querySelector('input[name="due_date"]');
        if (!count || !date || date.value) return;
        event.preventDefault();
        const noun = count === 1 ? 'reminder' : 'reminders';
        if (window.confirm('Clearing the date removes its ' + count + ' ' + noun + '. Continue?')) {
            event.detail.issueRequest(true);
        }
    });

    htmx.onLoad(function (root) {
        const scope = root.querySelectorAll ? root : document;
        nameChecklistBoxes(scope);
        wireScrollHints(scope);
        scope.querySelectorAll('[data-checklist-url]').forEach(enableChecklist);
        if (root.matches && root.matches('[data-checklist-url]')) enableChecklist(root);
        scope.querySelectorAll('[data-sortable]').forEach(makeSortable);
        if (root.matches && root.matches('[data-sortable]')) makeSortable(root);
    });
})();
