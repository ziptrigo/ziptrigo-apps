/*
 * The rendered note (`.note-body`), on the note page, the editor's preview and the history.
 * Loaded deferred by notes/layout.html and wired from `htmx.onLoad`:
 *
 *  - Checklists. The server renders the note's checkboxes disabled. In a block that can be edited
 *    (`#note-body[data-checklist-url]`) they are enabled here, and a change posts `index` and
 *    `checked` to that URL; the answer is the block again.
 *  - Checklist boxes get an accessible name from their item's text.
 *  - Every code block (`pre` holding a `code`) gets a Copy button that copies its code.
 */
(function () {
    'use strict';

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
            if (box.dataset.notesReady) return;
            box.dataset.notesReady = '1';
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

    function copyToClipboard(text) {
        if (navigator.clipboard && navigator.clipboard.writeText) {
            return navigator.clipboard.writeText(text);
        }
        return new Promise(function (resolve, reject) {
            const area = document.createElement('textarea');
            area.value = text;
            area.setAttribute('readonly', '');
            area.style.position = 'fixed';
            area.style.opacity = '0';
            document.body.appendChild(area);
            area.select();
            try { document.execCommand('copy'); resolve(); } catch (error) { reject(error); }
            document.body.removeChild(area);
        });
    }

    function addCopyButtons(root) {
        root.querySelectorAll('.note-body pre').forEach(function (pre) {
            const code = pre.querySelector('code');
            if (!code || pre.dataset.copyReady) return;
            pre.dataset.copyReady = '1';
            const button = document.createElement('button');
            button.type = 'button';
            button.className = 'note-copy-code btn';
            button.setAttribute('aria-label', 'Copy this code');
            button.innerHTML = '<i class="far fa-copy" aria-hidden="true"></i><span>Copy</span>';
            let timer = 0;
            button.addEventListener('click', function () {
                const done = function () {
                    button.querySelector('span').textContent = 'Copied';
                    window.clearTimeout(timer);
                    timer = window.setTimeout(function () { button.querySelector('span').textContent = 'Copy'; }, 2000);
                };
                copyToClipboard(code.textContent.replace(/\n$/, '')).then(done, function () {});
            });
            pre.appendChild(button);
        });
    }

    function wire(scope) {
        nameChecklistBoxes(scope);
        addCopyButtons(scope);
        scope.querySelectorAll('[data-checklist-url]').forEach(enableChecklist);
        if (scope.matches && scope.matches('[data-checklist-url]')) enableChecklist(scope);
    }

    htmx.onLoad(function (root) {
        wire(root.querySelectorAll ? root : document);
    });
    // The editor's preview is swapped in as innerHTML of an existing element.
    document.addEventListener('htmx:afterSettle', function (event) {
        if (event.target && event.target.querySelectorAll) wire(event.target);
    });
})();
