/*
 * The Notes pages' list scripts, loaded deferred by notes/layout.html (after SortableJS and core's
 * `initSortableList` on the pages that reorder). Wired from `htmx.onLoad`, so it also covers what
 * htmx swaps in:
 *
 *  - Drag and drop. A list with `data-sortable` (the notes in manual order, the tags) is made
 *    sortable with core's `initSortableList` (core/js/sortable_list.js: SortableJS by its
 *    `.drag-handle`, arrow keys on the focused handle, announcements); the new order of the rows'
 *    `data-sort-id`s is posted to `data-reorder-url` as `ids[]`. A refusal's message goes to
 *    `data-msg`. A list may also name `data-after-reorder-url` and `data-after-reorder-target`: once
 *    the order is saved that URL is fetched into the target (the filters page refreshes the sidebar).
 *  - `notesCancelEdit(form, slot)`: Cancel / Escape on an inline editor throws away what was typed.
 */
(function () {
    'use strict';

    window.notesCancelEdit = function (form, slot) {
        if (form && form.reset) form.reset();
        const message = slot ? document.querySelector(slot) : null;
        if (message) message.replaceChildren();
    };

    function showProblem(list, html) {
        const slot = document.querySelector(list.dataset.msg || '#notes-msg');
        if (slot) slot.innerHTML = html;
    }

    function postOrder(list) {
        const body = new URLSearchParams();
        list.querySelectorAll(':scope > [data-sort-id]').forEach(function (row) {
            body.append('ids[]', row.dataset.sortId);
        });
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
            // The saved filters list refreshes the sidebar's copy of the order once it is saved.
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
            handle: '.drag-handle',
            item: '[data-sort-id]',
            label: function (handle) { return handle.dataset.name || 'Item'; },
            onReorder: postOrder,
        });
    }

    htmx.onLoad(function (root) {
        const scope = root.querySelectorAll ? root : document;
        scope.querySelectorAll('[data-sortable]').forEach(makeSortable);
        if (root.matches && root.matches('[data-sortable]')) makeSortable(root);
    });
})();
