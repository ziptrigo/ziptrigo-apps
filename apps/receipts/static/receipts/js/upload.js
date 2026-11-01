/*
 * The Receipts upload page (issue #98). Each file goes through three steps:
 *
 *   1. POST /receipts/upload/start/        -> a presigned PUT URL (and the headers its signature
 *                                              binds), for the file's size and SHA-256
 *   2. PUT the file to that URL            -> straight to S3, never through Django
 *   3. POST /receipts/upload/<id>/complete/ -> the server checks the object and queues reading it
 *
 * The SHA-256 (base64) is computed here, before step 1, because it is signed into the URL: S3
 * refuses a body that differs from it. The browser sets Content-Length itself (it is a signed
 * header and must match the declared size); we send the headers the server returned, and nothing
 * else -- in particular the body is a type-less Blob so no Content-Type is sent, which would
 * otherwise need to be allowed by the bucket's CORS rules.
 *
 * The drop zone (files and folders) is `initDropZone` from core's `core/js/drop_zone.js`, loaded
 * before this script.
 */
(function () {
    'use strict';

    const root = document.getElementById('upload-root');
    if (!root) return;

    const startUrl = root.dataset.startUrl;
    const completeUrlTemplate = root.dataset.completeUrl;
    const maxBytes = parseInt(root.dataset.maxBytes, 10);
    const frozen = root.dataset.frozen === 'true';

    const dropZone = document.getElementById('drop-zone');
    const fileInput = document.getElementById('file-input');
    const cameraInput = document.getElementById('camera-input');
    const pickFilesBtn = document.getElementById('pick-files-btn');
    const takePhotoBtn = document.getElementById('take-photo-btn');
    const listEl = document.getElementById('upload-list');

    const MAX_PARALLEL = 3;
    const MAX_RATE_LIMIT_RETRIES = 3;
    // What `ReceiptsSettings.allowed_types` allows, from the page (the server checks again).
    function listOf(value) { return (value || '').split(',').filter(Boolean); }
    const SUPPORTED_EXTENSIONS = listOf(root.dataset.extensions);
    const SUPPORTED_TYPES = listOf(root.dataset.types);
    const TYPES_TEXT = root.dataset.typesText;

    const pending = [];
    let active = 0;

    function csrfToken() {
        const match = document.cookie.match(/(?:^|; )csrftoken=([^;]*)/);
        return match ? decodeURIComponent(match[1]) : '';
    }

    function formatSize(bytes) {
        const KB = 1024;
        const MB = KB * 1024;
        if (bytes >= MB) return (bytes / MB).toFixed(1).replace(/\.0$/, '') + ' MB';
        return Math.max(Math.round(bytes / KB), bytes ? 1 : 0).toLocaleString() + ' KB';
    }

    function isSupported(file) {
        const name = file.name.toLowerCase();
        if (SUPPORTED_EXTENSIONS.some(function (ext) { return name.endsWith(ext); })) return true;
        return SUPPORTED_TYPES.indexOf((file.type || '').toLowerCase()) !== -1;
    }

    async function sha256Base64(file) {
        const buffer = await file.arrayBuffer();
        const digest = await crypto.subtle.digest('SHA-256', buffer);
        let binary = '';
        new Uint8Array(digest).forEach(function (byte) { binary += String.fromCharCode(byte); });
        return btoa(binary);
    }

    function sleep(ms) {
        return new Promise(function (resolve) { setTimeout(resolve, ms); });
    }

    // -- Rows ------------------------------------------------------------------------------

    function el(tag, className, text) {
        const node = document.createElement(tag);
        if (className) node.className = className;
        if (text !== undefined) node.textContent = text;
        return node;
    }

    function createRow(file) {
        const li = el('li', 'rounded-xl border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-800 px-4 py-3');
        const top = el('div', 'flex items-center gap-3');
        const name = el('span', 'min-w-0 flex-1 truncate text-sm font-medium text-gray-900 dark:text-gray-100', file.name);
        const size = el('span', 'text-xs text-gray-500 dark:text-gray-400 whitespace-nowrap', formatSize(file.size));
        const cancel = el('button', 'btn p-1 rounded-full text-gray-400 hover:text-gray-600 dark:hover:text-gray-300 transition-colors');
        cancel.type = 'button';
        cancel.setAttribute('aria-label', 'Cancel upload of ' + file.name);
        const cancelIcon = el('i', 'fas fa-times');
        cancelIcon.setAttribute('aria-hidden', 'true');
        cancel.appendChild(cancelIcon);
        top.append(name, size, cancel);

        const bar = el('div', 'mt-2 h-2 rounded-full bg-gray-200 dark:bg-gray-700 overflow-hidden');
        bar.setAttribute('role', 'progressbar');
        bar.setAttribute('aria-valuemin', '0');
        bar.setAttribute('aria-valuemax', '100');
        bar.setAttribute('aria-valuenow', '0');
        bar.setAttribute('aria-label', 'Upload progress of ' + file.name);
        const fill = el('div', 'h-full bg-brand-primary transition-all duration-150 ease-out');
        fill.style.width = '0%';
        bar.appendChild(fill);

        const status = el('p', 'mt-1 text-xs text-gray-500 dark:text-gray-400', 'Waiting…');
        li.append(top, bar, status);
        listEl.appendChild(li);
        return { li: li, name: name, cancel: cancel, bar: bar, fill: fill, status: status };
    }

    function setProgress(item, percent) {
        item.row.fill.style.width = percent + '%';
        item.row.bar.setAttribute('aria-valuenow', String(Math.round(percent)));
    }

    function setStatus(item, text) {
        item.row.status.className = 'mt-1 text-xs text-gray-500 dark:text-gray-400';
        item.row.status.textContent = text;
    }

    function showError(item, message, topUpUrl) {
        item.done = true;
        item.row.bar.classList.add('hidden');
        item.row.cancel.classList.add('hidden');
        item.row.status.className = 'mt-1 text-xs text-red-600 dark:text-red-400';
        item.row.status.textContent = '✕ ' + message;
        if (topUpUrl) {
            const link = el('a', 'ml-2 underline', 'Top up');
            link.href = topUpUrl;
            item.row.status.appendChild(link);
        }
    }

    function showUploaded(item, detailUrl) {
        item.done = true;
        setProgress(item, 100);
        item.row.cancel.classList.add('hidden');
        item.row.status.className = 'mt-1 text-xs text-green-700 dark:text-green-400';
        item.row.status.textContent = '✓ Uploaded. ';
        const link = el('a', 'underline', 'View receipt');
        link.href = detailUrl;
        item.row.status.appendChild(link);
    }

    // -- Network ---------------------------------------------------------------------------

    async function postJson(url, body) {
        const response = await fetch(url, {
            method: 'POST',
            credentials: 'same-origin',
            headers: { 'Content-Type': 'application/json', 'X-CSRFToken': csrfToken() },
            body: JSON.stringify(body || {}),
        });
        let data = {};
        try { data = await response.json(); } catch (_) { /* not JSON */ }
        return { response: response, data: data };
    }

    // POSTs, waiting out a 429 (a burst of files) a few times before giving up.
    async function postWithRetry(item, url, body) {
        for (let attempt = 0; ; attempt++) {
            const result = await postJson(url, body);
            if (result.response.status !== 429 || attempt >= MAX_RATE_LIMIT_RETRIES) return result;
            if (item.cancelled) return result;
            const wait = Math.min(parseInt(result.response.headers.get('Retry-After') || '5', 10), 30);
            setStatus(item, 'Waiting a moment…');
            await sleep(wait * 1000);
        }
    }

    function putFile(item, ticket) {
        return new Promise(function (resolve, reject) {
            const xhr = new XMLHttpRequest();
            item.xhr = xhr;
            xhr.open('PUT', ticket.url);
            Object.keys(ticket.headers || {}).forEach(function (name) {
                // Content-Length is signed but is the browser's to set.
                if (name.toLowerCase() !== 'content-length') xhr.setRequestHeader(name, ticket.headers[name]);
            });
            xhr.upload.addEventListener('progress', function (event) {
                if (event.lengthComputable) setProgress(item, (event.loaded / event.total) * 100);
            });
            xhr.addEventListener('load', function () {
                if (xhr.status >= 200 && xhr.status < 300) resolve();
                else reject(new Error('The upload was refused (' + xhr.status + '). Try again.'));
            });
            xhr.addEventListener('error', function () {
                reject(new Error('The upload failed. Check your connection and try again.'));
            });
            xhr.addEventListener('abort', function () { reject(new Error('cancelled')); });
            // A type-less Blob: no Content-Type header (see the note at the top).
            xhr.send(item.file.slice(0, item.file.size));
        });
    }

    async function uploadOne(item) {
        try {
            setStatus(item, 'Preparing…');
            const checksum = await sha256Base64(item.file);
            if (item.cancelled) return;

            const started = await postWithRetry(item, startUrl, {
                name: item.file.name,
                size: item.file.size,
                content_type: item.file.type,
                checksum_sha256: checksum,
            });
            if (item.cancelled) return;
            if (!started.response.ok) {
                const fallback = started.response.status === 429
                    ? 'Too many uploads at once. Try again in a minute.'
                    : 'The upload could not be started.';
                showError(item, started.data.error || fallback, started.data.top_up_url);
                return;
            }
            const ticket = started.data;
            item.receiptId = ticket.receipt_id;

            setStatus(item, 'Uploading…');
            await putFile(item, ticket);
            if (item.cancelled) return;

            // The file is in the bucket: from here `complete` verifies, queues and bills it, and
            // a cancel could no longer stop that, so the button goes away.
            item.row.cancel.classList.add('hidden');
            setStatus(item, 'Finishing…');
            const completed = await postWithRetry(item, completeUrlTemplate.replace(
                '00000000-0000-0000-0000-000000000000', ticket.receipt_id), {});
            if (!completed.response.ok) {
                const fallback = completed.response.status === 429
                    ? 'Too many uploads at once. Try again in a minute.'
                    : 'The file could not be checked. Try again.';
                showError(item, completed.data.error || fallback);
                return;
            }
            showUploaded(item, completed.data.url);
        } catch (error) {
            if (!item.cancelled) showError(item, error.message || 'The upload failed.');
        } finally {
            item.xhr = null;
        }
    }

    function pump() {
        while (active < MAX_PARALLEL && pending.length) {
            const item = pending.shift();
            if (item.cancelled) continue;
            active++;
            uploadOne(item).finally(function () {
                active--;
                pump();
            });
        }
    }

    // -- Adding files ----------------------------------------------------------------------

    function addFiles(files) {
        Array.from(files).forEach(function (file) {
            const item = { file: file, row: createRow(file), done: false, cancelled: false, xhr: null };
            item.row.cancel.addEventListener('click', function () {
                item.cancelled = true;
                if (item.xhr) item.xhr.abort();
                item.row.li.remove();
            });
            if (file.size === 0) {
                showError(item, 'This file is empty.');
            } else if (!isSupported(file)) {
                showError(item, 'Unsupported file type. Use ' + TYPES_TEXT + '.');
            } else if (file.size > maxBytes) {
                showError(item, 'This file is larger than ' + formatSize(maxBytes) + '.');
            } else {
                pending.push(item);
            }
        });
        pump();
    }

    function bindPicker(button, input) {
        button.addEventListener('click', function () { input.click(); });
        input.addEventListener('change', function () {
            if (input.files && input.files.length) addFiles(input.files);
            input.value = '';
        });
    }

    if (!frozen) {
        bindPicker(pickFilesBtn, fileInput);
        bindPicker(takePhotoBtn, cameraInput);
        // Core's `drop_zone.js`; an empty drop is ignored here (no alert), as it always was.
        initDropZone(dropZone, addFiles, { onEmpty: function () {} });
    }
})();
