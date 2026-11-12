/*
 * The Notes pages' list scripts, loaded deferred by notes/layout.html (after SortableJS and core's
 * `initSortableList` on the pages that reorder). Wired from `htmx.onLoad`, so it also covers what
 * htmx swaps in:
 *
 *  - Drag and drop. A list with `data-sortable` (the notes in manual order, the tags) is made
 *    sortable with core's `initSortableList` (core/js/sortable_list.js: SortableJS by its
 *    `.drag-handle`, arrow keys on the focused handle, announcements); the new order of the rows'
 *    `data-sort-id`s is posted to `data-reorder-url` as `ids[]`. A refusal's message goes to
 *    `data-msg`. A list may also name `data-sync-order-with`, a selector: once the order is saved
 *    the items of that container that carry a `data-filter-id` are put in the same order, in
 *    place, by id (the filters page and the sidebar's copy of the saved filters). Nothing is fetched,
 *    so a drag spends no search budget and the counts the sidebar shows stay as they are.
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

    // Put the items of `container` that have a `data-filter-id` in the order of `ids`, ahead of any
    // item without one ("and N more", "No saved filters"). Ids the container doesn't have are skipped.
    function syncOrder(container, ids) {
        const items = new Map();
        container.querySelectorAll('[data-filter-id]').forEach(function (item) {
            items.set(item.dataset.filterId, item);
        });
        if (!items.size) return;
        const parent = items.values().next().value.parentElement;
        const tail = Array.from(parent.children).find(function (child) {
            return !child.hasAttribute('data-filter-id');
        }) || null;
        ids.forEach(function (id) {
            const item = items.get(id);
            if (item) parent.insertBefore(item, tail);
        });
    }

    function postOrder(list) {
        const body = new URLSearchParams();
        const ids = [];
        list.querySelectorAll(':scope > [data-sort-id]').forEach(function (row) {
            body.append('ids[]', row.dataset.sortId);
            ids.push(row.dataset.sortId);
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
            // The saved filters list reorders the sidebar's copy in place once the order is saved.
            const target = list.dataset.syncOrderWith ? document.querySelector(list.dataset.syncOrderWith) : null;
            if (target) syncOrder(target, ids);
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
