/*
 * Files on a comment (the comment forms of the task page, `comment_files_field.html`). The upload
 * engine is `todo/js/uploader.js`, loaded before this; each file is uploaded as soon as it is picked,
 * as a draft for a comment (`for_comment`), and a finished one becomes a chip holding a hidden
 * `attachment_ids` input, so submitting the form saves it with the comment. Removing a chip asks the
 * server to delete the draft (`discard`; one that is never saved is also swept after a day). The
 * form's submit button is disabled while a file is still uploading, and a submit that gets through
 * anyway (Ctrl+Enter) is stopped.
 *
 * The comments section is swapped by htmx after every action, so the forms are wired from
 * `htmx:load` (which also fires for the first render) as well as on DOMContentLoaded.
 */
(function () {
    'use strict';

    const NIL_UUID = '00000000-0000-0000-0000-000000000000';

    function chip(file, hiddenValue, onRemove) {
        const li = TodoUploader.el('li', 'flex items-center gap-2 text-sm');
        const icon = TodoUploader.el('i', 'fas fa-paperclip w-4 text-center text-sage-400');
        icon.setAttribute('aria-hidden', 'true');
        const name = TodoUploader.el('span', 'min-w-0 flex-1 truncate text-gray-900 dark:text-gray-100', file.filename);
        const size = TodoUploader.el('span', 'text-xs text-gray-500 dark:text-gray-400 whitespace-nowrap', TodoUploader.formatSize(file.size_bytes));
        const remove = TodoUploader.el('button', 'text-xs text-red-600 dark:text-red-400 hover:underline focus:outline-none focus:ring-2 focus:ring-red-500 rounded', 'Remove');
        remove.type = 'button';
        remove.setAttribute('aria-label', 'Remove ' + file.filename);
        const input = document.createElement('input');
        input.type = 'hidden';
        input.name = 'attachment_ids';
        input.value = hiddenValue;
        remove.addEventListener('click', function () {
            li.remove();
            onRemove();
        });
        li.append(icon, name, size, remove, input);
        return li;
    }

    function init(box) {
        if (box.dataset.filesReady === '1') return;
        box.dataset.filesReady = '1';

        const form = box.closest('form');
        const pick = box.querySelector('[data-files-pick]');
        const input = box.querySelector('[data-files-input]');
        const attached = box.querySelector('[data-files-attached]');
        const uploads = box.querySelector('[data-files-uploads]');
        const submit = form ? form.querySelector('button[type="submit"]') : null;
        const completeUrl = box.dataset.completeUrl;
        const cancelUrl = box.dataset.cancelUrl;
        const discardUrl = box.dataset.discardUrl;

        const uploader = TodoUploader.create({
            startUrl: box.dataset.startUrl,
            startExtra: { for_comment: true },
            completeUrl: function (id) { return completeUrl.replace(NIL_UUID, id); },
            cancelUrl: function (id) { return cancelUrl.replace(NIL_UUID, id); },
            listEl: uploads,
            maxBytes: parseInt(box.dataset.maxBytes, 10),
            onUploaded: function (item, data) {
                item.row.li.remove();
                attached.appendChild(chip(data, data.attachment_id, function () {
                    TodoUploader.postJson(discardUrl.replace(NIL_UUID, data.attachment_id), {})
                        .catch(function () { /* swept after a day */ });
                }));
            },
            onChange: function () {
                if (submit) submit.disabled = uploader.busy();
            },
        });

        if (box.dataset.frozen !== 'true') {
            pick.addEventListener('click', function () { input.click(); });
            input.addEventListener('change', function () {
                if (input.files && input.files.length) uploader.addFiles(input.files);
                input.value = '';
            });
        }
        if (form) {
            // Capture phase on the form itself runs before htmx's own submit listener.
            form.addEventListener('submit', function (event) {
                if (uploader.busy()) {
                    event.preventDefault();
                    event.stopImmediatePropagation();
                }
            }, true);
        }
    }

    function initAll(root) {
        if (!window.TodoUploader || !root || !root.querySelectorAll) return;
        if (root.matches && root.matches('[data-comment-files]')) init(root);
        root.querySelectorAll('[data-comment-files]').forEach(init);
    }

    document.addEventListener('htmx:load', function (event) { initAll(event.target); });
    document.addEventListener('DOMContentLoaded', function () { initAll(document); });
    if (document.readyState !== 'loading') initAll(document);
})();
