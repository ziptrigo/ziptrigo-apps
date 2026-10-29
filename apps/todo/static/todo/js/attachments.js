/*
 * The task page's attachment uploads (issue #184), the twin of Receipts' upload.js. The upload
 * itself (hash, start, PUT, complete, cancel) is `todo/js/uploader.js`, shared with the comment
 * forms (`comment_files.js`); this wires it to the drop zone and the list of attached files.
 *
 * Rows for the uploads in flight live in #attachment-uploads; when a file lands its row goes and
 * the list of attached files (#attachment-list) is fetched again.
 *
 * The drop zone (files and folders) is `initDropZone` from core's `core/js/drop_zone.js`, loaded
 * before this script, and `uploader.js` is loaded before both.
 */
(function () {
    'use strict';

    const root = document.getElementById('attachments');
    const dropZone = document.getElementById('attachment-drop-zone');
    if (!root || !dropZone) return;

    const NIL_UUID = '00000000-0000-0000-0000-000000000000';
    const completeUrlTemplate = root.dataset.completeUrl;
    const cancelUrlTemplate = root.dataset.cancelUrl;
    const listUrl = root.dataset.listUrl;
    const frozen = root.dataset.frozen === 'true';

    const fileInput = document.getElementById('attachment-file-input');
    const pickButton = document.getElementById('attachment-pick-btn');

    const uploader = TodoUploader.create({
        startUrl: root.dataset.startUrl,
        completeUrl: function (id) { return completeUrlTemplate.replace(NIL_UUID, id); },
        cancelUrl: function (id) { return cancelUrlTemplate.replace(NIL_UUID, id); },
        listEl: document.getElementById('attachment-uploads'),
        maxBytes: parseInt(root.dataset.maxBytes, 10),
        // The file landed: its progress row goes and the list of attached files is fetched again.
        onUploaded: function (item) {
            item.row.li.remove();
            if (window.htmx) {
                window.htmx.ajax('GET', listUrl, { target: '#attachment-list', swap: 'innerHTML' });
            }
        },
    });

    if (!frozen) {
        pickButton.addEventListener('click', function () { fileInput.click(); });
        fileInput.addEventListener('change', function () {
            if (fileInput.files && fileInput.files.length) uploader.addFiles(fileInput.files);
            fileInput.value = '';
        });
        // Core's `drop_zone.js`; an empty drop is ignored here (no alert), as on the Receipts page.
        initDropZone(dropZone, uploader.addFiles, { onEmpty: function () {} });
    }
})();
