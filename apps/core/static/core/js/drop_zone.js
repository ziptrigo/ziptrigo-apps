/*
 * Drag & drop target for files *and* folders, shared by the templates `file_transfer/send.html`,
 * `file_transfer/anon_send.html` and `receipts/upload.html`.
 * Loaded as a classic script before the page script, so `initDropZone` is a global.
 */

/**
 * Make `zoneEl` a drag & drop target for files *and* folders, calling `onFiles(File[])` with
 * everything dropped. A dropped folder is walked recursively (`webkitGetAsEntry`) into its
 * files, flat, the same as the "Add folder" picker's `webkitdirectory` input gives.
 *
 * `options.onEmpty` runs when a drop yields no files at all (an empty folder, or one that could
 * not be read); by default it alerts the user.
 */
function initDropZone(zoneEl, onFiles, options) {
    const onEmpty = (options && options.onEmpty) || function () {
        alert('No files found in what you dropped (an empty folder, or one that could not be read).');
    };
    const activeClasses = ['border-brand-primary', 'bg-gray-50', 'dark:bg-gray-700'];
    let depth = 0;

    function hasFiles(e) {
        return !!e.dataTransfer && Array.from(e.dataTransfer.types || []).indexOf('Files') !== -1;
    }

    function setActive(on) {
        activeClasses.forEach(function (c) { zoneEl.classList.toggle(c, on); });
    }

    function readAllEntries(reader) {
        // `readEntries` returns at most ~100 entries per call; keep going until it's empty.
        return new Promise(function (resolve, reject) {
            const all = [];
            (function next() {
                reader.readEntries(function (batch) {
                    if (!batch.length) return resolve(all);
                    all.push.apply(all, batch);
                    next();
                }, reject);
            })();
        });
    }

    async function collect(entry, out) {
        if (entry.isFile) {
            const file = await new Promise(function (resolve, reject) { entry.file(resolve, reject); });
            out.push(file);
        } else if (entry.isDirectory) {
            const children = await readAllEntries(entry.createReader());
            for (const child of children) await collect(child, out);
        }
    }

    zoneEl.addEventListener('dragenter', function (e) {
        // Dragging selected text or a link over the zone isn't something it can accept.
        if (!hasFiles(e)) return;
        e.preventDefault();
        depth++;
        setActive(true);
    });
    zoneEl.addEventListener('dragover', function (e) {
        e.preventDefault();
        if (e.dataTransfer) e.dataTransfer.dropEffect = 'copy';
    });
    zoneEl.addEventListener('dragleave', function () {
        depth = Math.max(0, depth - 1);
        if (depth === 0) setActive(false);
    });
    zoneEl.addEventListener('drop', async function (e) {
        e.preventDefault();
        depth = 0;
        setActive(false);
        const dt = e.dataTransfer;
        if (!dt) return;
        const out = [];
        // `webkitGetAsEntry` must be called synchronously, before the first `await`: the
        // DataTransferItemList is cleared once the event handler yields.
        const entries = Array.from(dt.items || [])
            .filter(function (item) { return item.kind === 'file'; })
            .map(function (item) { return item.webkitGetAsEntry ? item.webkitGetAsEntry() : null; });
        if (entries.length && entries.every(Boolean)) {
            for (const entry of entries) {
                try { await collect(entry, out); } catch (err) { console.warn('Could not read', err); }
            }
        } else {
            out.push.apply(out, Array.from(dt.files));
        }
        if (out.length) {
            onFiles(out);
        } else {
            onEmpty();
        }
    });

    // A file dropped just outside the zone would make the browser navigate to it and lose the page.
    ['dragover', 'drop'].forEach(function (type) {
        window.addEventListener(type, function (e) {
            if (hasFiles(e)) e.preventDefault();
        });
    });
}
