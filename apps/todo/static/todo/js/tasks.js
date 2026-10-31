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
 *    `scope` and `parent` when the list has them (`data-scope`, `data-parent`).
 *
 * The completion confirmation for a parent with open subtasks and the Postpone menu need no code
 * here: they use the shared `confirmModal` component and Alpine in the templates.
 */
(function () {
    'use strict';

    function enableChecklist(section) {
        const url = section.dataset.checklistUrl;
        if (!url) return;
        section.querySelectorAll('input.task-list-item-checkbox').forEach(function (box) {
            if (box.dataset.todoReady) return;
            box.dataset.todoReady = '1';
            box.disabled = false;
            box.addEventListener('change', function () {
                box.disabled = true;
                htmx.ajax('POST', url, {
                    target: '#' + section.id,
                    swap: 'outerHTML',
                    values: { index: box.dataset.index, checked: box.checked ? 'true' : 'false' },
                }).then(function () {
                    // A refusal swaps its message elsewhere and leaves this box in place.
                    box.disabled = false;
                });
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
            if (response.ok) return showProblem(list, '');
            return response.text().then(function (html) { showProblem(list, html); });
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
        scope.querySelectorAll('[data-checklist-url]').forEach(enableChecklist);
        if (root.matches && root.matches('[data-checklist-url]')) enableChecklist(root);
        scope.querySelectorAll('[data-sortable]').forEach(makeSortable);
        if (root.matches && root.matches('[data-sortable]')) makeSortable(root);
    });
})();
