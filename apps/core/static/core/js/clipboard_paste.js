/*
 * Paste an image from the clipboard as a way to upload it (issue #313), shared by the file transfer
 * send pages and file request page, the Receipts upload page and the To-do task page (the task's
 * attachments and the comment forms). Loaded as a classic script before the page script, so
 * `initClipboardPaste` is a global, like `initDropZone` (`core/js/drop_zone.js`).
 *
 *     initClipboardPaste(onFiles, { within, enabled, hint, keep })
 *
 * `onFiles(File[])` is the very callback the drop zone and the file picker use, so a pasted image goes
 * through exactly the same checks and the same upload as a picked one.
 *
 * What a paste does:
 *   - Only an IMAGE is taken (a `file` item of the clipboard whose type is `image/*`). With none, the
 *     paste is ignored entirely: `preventDefault` is not called, so text pasted into a field still
 *     works. Text, HTML, the path of a file as text, and a non-image file are all "no image".
 *   - In a text field (input, textarea, contenteditable) the text wins: if the clipboard holds text as
 *     well as an image (copying cells from a spreadsheet does), the paste is left to the field. An
 *     image alone is taken even there (the field could not use it).
 *   - The file is named after the local time of the paste, `YYYY-MM-DD-HH-mm-SS.<ext>`, with `-2`,
 *     `-3` for the next ones in the same second. The browser gives a re-encoded copy (a screenshot
 *     is always a PNG), so the original format is not known: a PNG is saved as WebP at 85% quality
 *     (a browser that cannot write WebP keeps the PNG); a JPEG, WebP or GIF is kept as it is (so a
 *     GIF keeps its animation). `options.keep` changes that list (Receipts cannot read a GIF).
 *   - No permission is involved: the `paste` event hands the clipboard to the page because the user
 *     pressed the keys. (Reading it on demand, `navigator.clipboard.read()`, is what asks.)
 *
 * Where it goes. One document-level listener serves every registration:
 *   - `within` (an element): a paste whose target is inside it goes there (the innermost one wins).
 *     The To-do comment forms use it. A registration whose element has left the page is dropped.
 *   - no `within`: the page-level target, for a paste anywhere else (the drop zone's page).
 *   - `enabled()` (optional) is asked at paste time; false means "not now" (the paste is ignored).
 *   - `hint` (an element, `hidden` by default): shown here (not on a device with no keyboard-like
 *     pointer, where there is no Ctrl+V), and a `[data-paste-key]` inside it reads "⌘V" on Apple.
 */
(function () {
    'use strict';

    const registrations = [];
    const stampCounts = {};
    let listening = false;
    let statusEl = null;

    // What is kept as it is when `options.keep` is not given, and the extension each gets.
    const DEFAULT_KEEP = ['image/jpeg', 'image/webp', 'image/gif'];
    const EXTENSIONS = { 'image/jpeg': 'jpg', 'image/webp': 'webp', 'image/gif': 'gif', 'image/png': 'png' };
    const WEBP_QUALITY = 0.85;

    function isApple() {
        const platform = (navigator.userAgentData && navigator.userAgentData.platform) || navigator.platform || '';
        return /Mac|iPhone|iPad|iPod/i.test(platform);
    }

    function touchOnly() {
        return !!window.matchMedia && !window.matchMedia('(any-pointer: fine)').matches;
    }

    function pad(n) { return String(n).padStart(2, '0'); }

    // `2026-11-15-21-03-07`, then `...-07-2`, `...-07-3` for more in the same second.
    function uniqueStamp(date) {
        const base = date.getFullYear() + '-' + pad(date.getMonth() + 1) + '-' + pad(date.getDate()) + '-'
            + pad(date.getHours()) + '-' + pad(date.getMinutes()) + '-' + pad(date.getSeconds());
        stampCounts[base] = (stampCounts[base] || 0) + 1;
        return stampCounts[base] > 1 ? base + '-' + stampCounts[base] : base;
    }

    function isEditable(node) {
        if (!node || node.nodeType !== 1) return false;
        if (node.isContentEditable) return true;
        const tag = node.tagName;
        if (tag === 'TEXTAREA') return true;
        if (tag !== 'INPUT') return false;
        return ['button', 'checkbox', 'radio', 'file', 'submit', 'reset', 'image', 'range', 'color']
            .indexOf((node.type || 'text').toLowerCase()) === -1;
    }

    // The image files of a clipboard. `getAsFile` is synchronous, and has to run before the event
    // handler yields: the clipboard's items are gone afterwards.
    function imageFiles(data) {
        const out = [];
        Array.from(data.items || []).forEach(function (item) {
            if (item.kind !== 'file' || !(item.type || '').toLowerCase().startsWith('image/')) return;
            const file = item.getAsFile();
            if (file) out.push(file);
        });
        if (!out.length) {
            Array.from(data.files || []).forEach(function (file) {
                if ((file.type || '').toLowerCase().startsWith('image/')) out.push(file);
            });
        }
        return out;
    }

    function hasText(data) {
        try { return (data.getData('text/plain') || '').trim() !== ''; } catch (_) { return false; }
    }

    // The image re-encoded as WebP at 85%, or null when the browser cannot (Safari answers a PNG).
    async function toWebp(file) {
        if (!window.createImageBitmap) return null;
        const bitmap = await createImageBitmap(file);
        try {
            const canvas = document.createElement('canvas');
            canvas.width = bitmap.width;
            canvas.height = bitmap.height;
            canvas.getContext('2d').drawImage(bitmap, 0, 0);
            const blob = await new Promise(function (resolve) { canvas.toBlob(resolve, 'image/webp', WEBP_QUALITY); });
            return blob && blob.type === 'image/webp' ? blob : null;
        } finally {
            if (bitmap.close) bitmap.close();
        }
    }

    function extensionOf(type) {
        if (EXTENSIONS[type]) return EXTENSIONS[type];
        const sub = (type.split('/')[1] || 'img').split('+')[0].replace(/[^a-z0-9]/g, '');
        return sub || 'img';
    }

    // One pasted image -> the File to upload, named `stamp.<ext>`.
    async function prepare(file, stamp, keep) {
        const type = (file.type || '').toLowerCase();
        const now = Date.now();
        if (keep.indexOf(type) === -1) {
            try {
                const blob = await toWebp(file);
                if (blob) return new File([blob], stamp + '.webp', { type: 'image/webp', lastModified: now });
            } catch (_) { /* cannot be decoded here: keep the original below */ }
        }
        return new File([file], stamp + '.' + extensionOf(type), { type: type, lastModified: now });
    }

    function announce(message) {
        if (!statusEl) {
            statusEl = document.createElement('div');
            statusEl.className = 'sr-only';
            statusEl.setAttribute('role', 'status');
            statusEl.setAttribute('aria-live', 'polite');
            document.body.appendChild(statusEl);
        }
        // Cleared first so the same sentence twice is still read.
        statusEl.textContent = '';
        setTimeout(function () { statusEl.textContent = message; }, 50);
    }

    // The registration a paste with this target goes to: the innermost `within` that holds it, else
    // the page-level one. Registrations whose element left the page are dropped on the way.
    function choose(target) {
        for (let i = registrations.length - 1; i >= 0; i--) {
            if (registrations[i].within && !registrations[i].within.isConnected) registrations.splice(i, 1);
        }
        const open = registrations.filter(function (r) { return !r.enabled || r.enabled(); });
        const inside = open.filter(function (r) { return r.within && target && r.within.contains(target); });
        if (inside.length) {
            return inside.reduce(function (best, r) { return best.within.contains(r.within) ? r : best; });
        }
        return open.find(function (r) { return !r.within; }) || null;
    }

    function onPaste(event) {
        const data = event.clipboardData;
        if (event.defaultPrevented || !data) return;
        const images = imageFiles(data);
        if (!images.length) return;
        if (isEditable(event.target) && hasText(data)) return;
        const registration = choose(event.target);
        if (!registration) return;
        event.preventDefault();
        const when = new Date();
        const jobs = images.map(function (file) { return prepare(file, uniqueStamp(when), registration.keep); });
        Promise.all(jobs).then(function (files) {
            registration.onFiles(files);
            announce(files.length === 1
                ? 'Pasted ' + files[0].name + ' from the clipboard.'
                : 'Pasted ' + files.length + ' images from the clipboard.');
        });
    }

    window.initClipboardPaste = function (onFiles, options) {
        const opts = options || {};
        const registration = {
            onFiles: onFiles,
            within: opts.within || null,
            enabled: opts.enabled || null,
            keep: opts.keep || DEFAULT_KEEP,
        };
        registrations.push(registration);
        if (!listening) {
            listening = true;
            document.addEventListener('paste', onPaste);
        }
        if (opts.hint && !touchOnly()) {
            opts.hint.classList.remove('hidden');
            if (isApple()) {
                opts.hint.querySelectorAll('[data-paste-key]').forEach(function (key) { key.textContent = '⌘V'; });
            }
        }
        return {
            remove: function () {
                const at = registrations.indexOf(registration);
                if (at !== -1) registrations.splice(at, 1);
            },
        };
    };
})();
