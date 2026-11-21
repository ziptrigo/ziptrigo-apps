/*
 * The To-do pages' scripts, loaded deferred by todo/layout.html (after SortableJS and core's
 * `initSortableList` on the pages that reorder). Everything is wired from `htmx.onLoad`, so it
 * also covers whatever htmx swaps in:
 *
 *  - Checklists. The server renders the description's checkboxes disabled. On a task that can be
 *    edited (the description block has `data-checklist-url`) they are enabled here, and a change
 *    posts `index` and `checked` to that URL; the answer is the description block again.
 *  - The task page's details save themselves (`form[data-autosave]`): every change of a named
 *    control (the date and time fields' hidden inputs, the priority select, an accepted AI chip)
 *    submits the form, a few at once making one request; the labels and repeat popovers save when
 *    they close (`popover-closed`; changes inside `[data-save-on-close]` wait for it). A form whose
 *    values equal those last sent sends nothing, and a time typed before any date waits for the
 *    date (the server refuses a time alone). A save swaps in only the parts of the panel it
 *    touched; `#details-save-status` says "Saving...", "Saved" or "Not saved". A change not sent
 *    yet when the page is left is sent with `sendBeacon`.
 *  - Text typed into a field that a swap replaces, while the request was in flight, is kept.
 *  - Clearing a task's date asks first when it has reminders (they are removed with the date); saying
 *    no puts the date back.
 *  - Drag and drop. A list with `data-sortable` is made sortable with core's `initSortableList`
 *    (core/js/sortable_list.js: SortableJS by its `.drag-handle`, arrow keys, announcements);
 *    the new order of the rows' `data-task-id`s is posted to `data-reorder-url` as `ids[]`, with
 *    `scope` and `parent` when the list has them (`data-scope`, `data-parent`). The handle is
 *    focusable too: the up and down arrow keys move its row and post the order the same way, so
 *    ordering does not need a mouse. A list with
 *    `data-after-reorder-url` fetches that URL into `data-after-reorder-target` once the order is
 *    saved (the filters page refreshes the sidebar's copy of the list).
 *  - On Today a row is dragged only inside its own state group (`data-group` on the rows: in_progress,
 *    todo, muted), whose order the server keeps; changing a task's state re-renders the list.
 *  - Empty lists. A row action removes the row without re-rendering its list, so a list marked
 *    `data-empty-list` shows its `<template data-empty-state>` when the last row has left (or hides
 *    the section marked `data-vanish-when-empty`) and takes it away when a row is back.
 *  - A click anywhere on a row (`.task-row`) opens the task, as the title link does, unless it lands
 *    on something interactive (a link, button, field, menu, the drag handle) or ends a text selection.
 *  - The tab title follows an inline rename (`#task-panel[data-document-title]`).
 *  - `[data-scroll-hint]` menus (a phone's horizontal scrollers) say whether there is more to
 *    scroll to (`data-more-right`, which the layout's CSS turns into a fade).
 *  - Checklist boxes get an accessible name from their item's text.
 *
 * The close dialog for a parent with open subtasks (`close_dialog.js`), the state menus and the Postpone
 * menu need no code here: they are Alpine in the templates.
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

    function postOrder(list, items, info) {
        // A row dragged in from another section takes its subtasks with it and changes both counts:
        // the page is reloaded once the move is saved, instead of patching all of that here.
        const reloadAfter = !!(info && info.fromOtherList);
        const body = new URLSearchParams();
        list.querySelectorAll(':scope > [data-task-id]').forEach(function (row) {
            body.append('ids[]', row.dataset.taskId);
        });
        if (list.dataset.scope) body.set('scope', list.dataset.scope);
        if (list.dataset.parent) body.set('parent', list.dataset.parent);
        // A project's page: which project, and which of its lists (a section, or '' for none).
        if (list.dataset.project) {
            body.set('project', list.dataset.project);
            body.set('section', list.dataset.section || '');
        }
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
            if (reloadAfter) { window.location.reload(); return; }
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
        if (typeof window.initSortableList !== 'function') return;
        window.initSortableList(list, {
            // A project's sections are reordered by their own handle (`data-handle`).
            handle: list.dataset.handle || '.drag-handle',
            item: '[data-task-id]',
            // Lists that share a `data-sort-group` (the sections of a project) take each other's rows;
            // a subtask row stays in its parent's section.
            group: list.dataset.sortGroup || undefined,
            canLeave: function (item) { return !item.dataset.subtask; },
            label: function (handle) { return handle.dataset.name || 'Item'; },
            onReorder: postOrder,
            // Today is sorted by state group (In progress, To do, On hold) and its manual order only
            // exists inside a group: a row is never dragged past a row of another group. (A parent's
            // subtasks are one list whatever their states.)
            canMove: list.dataset.scope === 'today'
                ? function (item, other) { return item.dataset.group === other.dataset.group; }
                : undefined,
        });
    }

    function rowsOf(list) {
        return Array.from(list.children).filter(function (child) { return child.hasAttribute('data-task-id'); });
    }

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

    // A click on a row's empty space opens the task, like its title link. Anything interactive keeps
    // its own behaviour: links, buttons, fields, the drag handle, and whatever sits inside an Alpine
    // menu (the state and Postpone menus), so closing a menu never opens the task.
    const INTERACTIVE = 'a, button, input, select, textarea, label, summary, [role="button"], ' +
        '[role="menu"], [role="menuitem"], [role="menuitemradio"], [role="listbox"], [x-data], .drag-handle';
    // A press that lands while a menu is open only closes it (the menu's own outside-click runs before
    // the click does, so ask at press time).
    let menuWasOpen = false;
    document.addEventListener('pointerdown', function () {
        menuWasOpen = !!document.querySelector('.task-row [aria-expanded="true"]');
    }, true);
    document.addEventListener('click', function (event) {
        if (event.defaultPrevented || event.button !== 0) return;
        if (menuWasOpen) { menuWasOpen = false; return; }
        const target = event.target;
        if (!(target instanceof Element)) return;
        const row = target.closest('.task-row');
        if (!row) return;
        const hit = target.closest(INTERACTIVE);
        if (hit && row.contains(hit)) return;
        const selection = window.getSelection ? window.getSelection() : null;
        if (selection && !selection.isCollapsed && row.contains(selection.anchorNode)) return;
        const link = row.querySelector('a.task-title');
        if (!link) return;
        // Ctrl/Cmd-click opens a new tab like the link does, by asking for it outright (a synthetic click
        // on the link could be treated as a popup); anything else is a plain visit.
        if (event.ctrlKey || event.metaKey) window.open(link.href, '_blank', 'noopener');
        else link.click();
    });

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

    // -- The task page's details save themselves ------------------------------------------------------
    // `form[data-autosave]`. What is sent is compared with what was last sent, so a control set to
    // the value it already had sends nothing.
    const lastSent = new WeakMap();
    const DETAILS_FIELDS = ['due_date', 'due_time', 'rrule', 'recurrence_mode', 'labels'];

    function formState(form) {
        const params = new URLSearchParams(new FormData(form));
        params.delete('csrfmiddlewaretoken');
        return params.toString();
    }
    function fieldsOf(state, name) {
        return new URLSearchParams(state).getAll(name).join(',');
    }
    function changedFields(before, after) {
        return DETAILS_FIELDS.filter(function (name) {
            return before === null || fieldsOf(before, name) !== fieldsOf(after, name);
        });
    }
    function saveStatus(form, text) {
        const status = form && form.querySelector('#details-save-status');
        if (status) status.textContent = text;
    }
    function reminderCount(form) {
        const holder = form.querySelector('[data-reminder-count]');
        return holder ? parseInt(holder.dataset.reminderCount, 10) || 0 : 0;
    }
    // Why the form can't be saved as it stands, or ''. A time needs a date: one typed on a task
    // with no date waits for the date (the server refuses a time alone).
    function waitingReason(state, before) {
        const noDate = !fieldsOf(state, 'due_date') && !fieldsOf(before || '', 'due_date');
        return fieldsOf(state, 'due_time') && noDate ? 'Choose a date to save the time.' : '';
    }
    // The values the page was rendered with (or that a save swapped in) are the saved ones. Read
    // a tick later, once Alpine has bound the repeat control's hidden inputs, and never while a
    // change is waiting to be sent: its values are not saved yet.
    function rememberState(form) {
        setTimeout(function () {
            if (form.isConnected && !form._autosaveTimer) lastSent.set(form, formState(form));
        }, 0);
    }
    function scheduleSave(form) {
        clearTimeout(form._autosaveTimer);
        // A tick later: an accepted chip sets the date, the time and the repeat one after the
        // other (one request for all three), and Alpine refreshes the repeat's hidden inputs on a
        // later microtask.
        form._autosaveTimer = setTimeout(function () {
            form._autosaveTimer = null;
            if (!form.isConnected) return;
            const state = formState(form);
            const before = lastSent.has(form) ? lastSent.get(form) : null;
            if (state === before) return;
            const waiting = waitingReason(state, before);
            if (waiting) {
                saveStatus(form, waiting);
                return;
            }
            form.requestSubmit();
        }, 0);
    }

    document.addEventListener('change', function (event) {
        const target = event.target;
        if (!(target instanceof Element) || !target.name) return;
        const form = target.closest('form[data-autosave]');
        if (!form || target.closest('[data-save-on-close], [data-parse-input]')) return;
        scheduleSave(form);
    });
    // The labels and repeat popovers save when they close (task_form_fields.html).
    document.addEventListener('popover-closed', function (event) {
        const target = event.target;
        const form = target instanceof Element ? target.closest('form[data-autosave]') : null;
        if (form) scheduleSave(form);
    });
    // Typing anything takes an earlier "Saved" away: it would sit next to a field showing a typo.
    document.addEventListener('input', function (event) {
        const target = event.target;
        const form = target instanceof Element ? target.closest('form[data-autosave]') : null;
        if (form) saveStatus(form, '');
    });

    // A save swaps nothing itself (`hx-swap="none"`): the answer is the whole panel, and only the
    // parts the change touched are taken from it (`hx-select-oob`), so an editor or a menu open
    // elsewhere on the page stays as it is. The form, and its status line, stay in place.
    document.addEventListener('htmx:beforeRequest', function (event) {
        const form = event.detail.elt;
        if (!(form instanceof HTMLFormElement) || !form.matches('form[data-autosave]')) return;
        const state = formState(form);
        const changed = changedFields(lastSent.has(form) ? lastSent.get(form) : null, state);
        const parts = ['task-msg'];
        if (changed.length) parts.push('task-details-fields');
        const touchesDate = changed.includes('due_date') || changed.includes('due_time');
        if (changed.some(function (name) { return name !== 'labels'; })) {
            parts.push('task-repeat-line');
        }
        if (touchesDate) parts.push('task-reminders');
        form.setAttribute('hx-select-oob', parts.join(','));
        lastSent.set(form, state);
        saveStatus(form, 'Saving…');
    });
    document.addEventListener('htmx:afterRequest', function (event) {
        const form = event.detail.elt;
        if (!(form instanceof HTMLFormElement) || !form.matches('form[data-autosave]')) return;
        // A refusal's message is in the panel's slot (and under the field); the next change sends
        // everything again.
        saveStatus(form, event.detail.successful ? 'Saved' : 'Not saved');
    });

    // Leaving the page with a change not sent yet (a popover still open, a change made just
    // before a click on a link) sends it on the way out. Not a change that needs a question first
    // (clearing the date of a task with reminders), nor one the server would refuse (a time with
    // no date).
    window.addEventListener('pagehide', function () {
        document.querySelectorAll('form[data-autosave]').forEach(function (form) {
            const state = formState(form);
            const before = lastSent.has(form) ? lastSent.get(form) : null;
            if (before === null || state === before || waitingReason(state, before)) return;
            const clearsDate = !fieldsOf(state, 'due_date') && fieldsOf(before, 'due_date');
            if (clearsDate && reminderCount(form)) return;
            clearTimeout(form._autosaveTimer);
            if (navigator.sendBeacon) navigator.sendBeacon(form.action, new FormData(form));
        });
    });

    // Clearing the date of a task removes its reminders (they are due relative to the date): ask
    // first. htmx raises `htmx:confirm` before every request; cancelling it and calling
    // `issueRequest(true)` sends it. Saying no puts the saved date (the one last sent: a hidden
    // input has no separate default value, setting it rewrites its attribute) back in the field.
    document.addEventListener('htmx:confirm', function (event) {
        const form = event.target;
        if (!(form instanceof HTMLFormElement) || !form.matches('form[data-autosave]')) return;
        const count = reminderCount(form);
        const date = form.querySelector('input[name="due_date"]');
        const saved = lastSent.has(form) ? fieldsOf(lastSent.get(form), 'due_date') : '';
        if (!count || !date || date.value || !saved) return;
        event.preventDefault();
        const noun = count === 1 ? 'reminder' : 'reminders';
        if (window.confirm('Clearing the date removes its ' + count + ' ' + noun + '. Continue?')) {
            event.detail.issueRequest(true);
        } else {
            date.value = saved;
            date.dispatchEvent(new CustomEvent('field-set', { detail: { value: saved } }));
        }
    });

    // -- Typing while a part of the panel is swapped ---------------------------------------------
    // A save (or another action of the task page) swaps parts of the panel in. Someone who went on
    // typing in a field that is replaced would see their text turn back into the saved value: keep
    // it. Only a field whose text changed since its request started is put back, focused, with its
    // selection, and only while the field that replaces it is shown.
    const typingAtRequest = new WeakMap();
    const typingToRestore = new WeakMap();
    function isTextField(element) {
        return element instanceof HTMLTextAreaElement
            || (element instanceof HTMLInputElement && ['text', 'search'].includes(element.type));
    }
    document.addEventListener('htmx:beforeRequest', function (event) {
        const active = document.activeElement;
        if (event.detail.xhr && isTextField(active) && active.id) {
            typingAtRequest.set(event.detail.xhr, { element: active, value: active.value });
        }
    });
    document.addEventListener('htmx:beforeSwap', function (event) {
        const xhr = event.detail.xhr;
        const active = document.activeElement;
        if (!xhr || !isTextField(active) || !active.id) return;
        const before = typingAtRequest.get(xhr);
        if (before && before.element === active && before.value === active.value) return;
        typingToRestore.set(xhr, {
            element: active,
            value: active.value,
            start: active.selectionStart,
            end: active.selectionEnd,
        });
    });
    document.addEventListener('htmx:afterSettle', function (event) {
        const typing = event.detail.xhr ? typingToRestore.get(event.detail.xhr) : null;
        if (!typing) return;
        typingToRestore.delete(event.detail.xhr);
        if (typing.element.isConnected) return; // not replaced: nothing was lost
        const field = document.getElementById(typing.element.id);
        if (!isTextField(field) || field.value === typing.value) return;
        if (!field.getClientRects().length) return; // replaced by a hidden one (an editor closed)
        field.value = typing.value;
        field.dispatchEvent(new Event('input', { bubbles: true }));
        field.focus();
        try {
            field.setSelectionRange(typing.start, typing.end);
        } catch (e) { /* not every field has a selection */ }
    });

    // A renamed attachment's list is swapped in place of the form that was focused, and htmx then puts
    // the focus back on the element with the focused one's id: the new, hidden name box. Remember which
    // row was saved (before the swap, while the form is still on the page) and, once the list has
    // settled, focus that row's Rename button instead.
    let renamedAttachment = null;
    document.addEventListener('htmx:beforeSwap', function (event) {
        // `detail.elt` is the swap's target here; the element that sent the request is the config's.
        const form = event.detail.requestConfig && event.detail.requestConfig.elt;
        const id = form && form.getAttribute && form.getAttribute('data-rename-attachment');
        renamedAttachment = id && event.detail.xhr && event.detail.xhr.status === 200 ? id : null;
    });
    document.addEventListener('htmx:afterSettle', function (event) {
        if (!renamedAttachment || !event.detail.target || event.detail.target.id !== 'attachment-list') return;
        const id = renamedAttachment;
        renamedAttachment = null;
        const button = document.querySelector('[data-rename-button="' + CSS.escape(id) + '"]');
        if (button) button.focus();
    });

    htmx.onLoad(function (root) {
        const scope = root.querySelectorAll ? root : document;
        nameChecklistBoxes(scope);
        wireScrollHints(scope);
        scope.querySelectorAll('[data-checklist-url]').forEach(enableChecklist);
        if (root.matches && root.matches('[data-checklist-url]')) enableChecklist(root);
        scope.querySelectorAll('[data-sortable]').forEach(makeSortable);
        if (root.matches && root.matches('[data-sortable]')) makeSortable(root);
        // A page, a re-rendered panel, or the fields a save swapped in: what they show is saved.
        const saved = new Set(scope.querySelectorAll('form[data-autosave]'));
        const form = root.closest && root.closest('form[data-autosave]');
        if (form) saved.add(form);
        saved.forEach(rememberState);
    });
})();
