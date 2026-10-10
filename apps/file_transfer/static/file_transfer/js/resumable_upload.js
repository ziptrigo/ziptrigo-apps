/**
 * Resumable multipart-upload driver for the file_transfer send pages (spec section 2: direct-to-S3
 * multipart uploads with per-part SHA-256 checksums; issue #55 phase 3: resumable after a page
 * reload). Shared by the logged-in send page (`send.html`) and the anonymous one
 * (`anon_send.html`): both render an identical file-list/submit-button shape and talk to the same
 * JSON endpoint shape (`apps.file_transfer.views.uploads` / `views.anonymous`), just under a
 * different URL prefix -- see each template's own `<script>` block for how it wires this up.
 *
 * How resuming works: the server already knows a draft's files (`TransferFile` rows) across a
 * reload -- what it can't do on its own is get the *bytes* back, since browsers never let a page
 * reopen a `File` a user picked in an earlier load. So each template renders the draft's existing
 * files into the page (`existingFiles`, `{id, name, size, uploaded, client_last_modified}`) for
 * `handleFileSelection` to match a re-selected file against by name + size (+ `lastModified` when
 * the row has one) -- a match resumes that row (asking the server which parts of its multipart
 * upload S3 already has, via `.../resume/`, then PUTting only the rest) instead of adding a
 * duplicate new file.
 */
function getCookie(name) {
    const value = `; ${document.cookie}`;
    const parts = value.split(`; ${name}=`);
    if (parts.length === 2) return parts.pop().split(';').shift();
}

// How many parts' presigned PUT URLs to request at once, rather than every part of the whole file
// up front: a presigned URL is only valid for `PUT_URL_EXPIRES_SECONDS` (1h,
// `services.storage.PUT_URL_EXPIRES_SECONDS`), so on a slow connection a large file's later parts
// could easily still be waiting when their URL expires if every URL were requested at time zero.
const PART_URL_BATCH_SIZE = 8;

function initFileTransferUpload(options) {
    const base = options.base;
    const fileListEl = options.fileListEl;
    const submitBtn = options.submitBtn;
    const csrftoken = options.csrftoken;
    const onFirstFileAdded = options.onFirstFileAdded;
    // file_id -> { name, size, uploaded, progress, clientLastModified, resumable }
    const files = new Map();
    // Existing, not-yet-uploaded files from a previous page load, available to be matched against
    // a re-selected `File` (see `findResumeMatch`). `matched` guards against matching the same row
    // twice within one file-picker batch, and is reset if the resume attempt it triggered fails,
    // so the same file can be re-selected and tried again.
    const resumeCandidates = [];

    function hasPausedFiles() {
        return Array.from(files.values()).some(function (f) { return f.resumable && !f.uploaded; });
    }

    function updateSubmitState() {
        const anyUploaded = Array.from(files.values()).some(function (f) { return f.uploaded; });
        submitBtn.disabled = !anyUploaded;
    }

    // Base64-encode a SHA-256 digest of `blob`, for the S3 per-part integrity check (spec section
    // 11): the multipart upload was created with `ChecksumAlgorithm='SHA256'`
    // (`services.storage.S3Storage.create_multipart_upload`), so S3 requires every part to carry
    // its own checksum. Computed here, client-side, *before* asking Django for that part's
    // presigned URL -- the checksum has to be known before the URL is signed, since it becomes
    // part of the signature (`ChecksumSHA256` in `services.uploads.presign_parts`).
    async function sha256Base64(blob) {
        const buffer = await blob.arrayBuffer();
        const digest = await crypto.subtle.digest('SHA-256', buffer);
        let binary = '';
        new Uint8Array(digest).forEach(function (byte) { binary += String.fromCharCode(byte); });
        return btoa(binary);
    }

    function renderRow(fileId, entry) {
        let li = document.getElementById('file-row-' + fileId);
        if (!li) {
            li = document.createElement('li');
            li.id = 'file-row-' + fileId;
            li.className = 'flex items-center justify-between text-sm gap-2';
            fileListEl.appendChild(li);
        }
        // Built with DOM APIs (not `innerHTML`) so a file name is never parsed as markup -- it
        // comes straight from the browser's own File objects, but a folder upload can carry names
        // the user didn't type themselves.
        li.textContent = '';
        const pct = entry.progress || 0;
        li.title = entry.resumable && !entry.uploaded
            ? 'Select the same file again to resume uploading it.'
            : '';

        const nameSpan = document.createElement('span');
        nameSpan.className = 'truncate flex-1';
        nameSpan.textContent = entry.name + ' (' + Math.round(entry.size / 1024 / 1024) + ' MB)';

        const barOuter = document.createElement('span');
        barOuter.className = 'w-32 bg-gray-200 dark:bg-gray-700 rounded h-2 overflow-hidden';
        const barInner = document.createElement('span');
        barInner.className = 'block h-2 bg-brand-primary';
        barInner.style.width = pct + '%';
        barOuter.appendChild(barInner);

        const statusSpan = document.createElement('span');
        statusSpan.className = 'w-20 text-right';
        statusSpan.textContent = entry.uploaded ? 'Done' : (entry.resumable ? 'Paused' : pct + '%');

        const removeBtn = document.createElement('button');
        removeBtn.type = 'button';
        removeBtn.setAttribute('data-remove', fileId);
        removeBtn.className = 'text-red-500 hover:underline';
        removeBtn.textContent = 'Remove';

        li.append(nameSpan, barOuter, statusSpan, removeBtn);
    }

    function findResumeMatch(file) {
        for (const candidate of resumeCandidates) {
            if (candidate.matched) continue;
            if (candidate.name !== file.name || candidate.size !== file.size) continue;
            if (
                candidate.clientLastModified != null
                && candidate.clientLastModified !== file.lastModified
            ) {
                continue;
            }
            candidate.matched = true;
            return candidate;
        }
        return null;
    }

    // Shared by a from-scratch upload and a resumed one: PUTs every part not already accounted
    // for in `alreadyUploaded` (part number -> {etag, checksum}), in order, then completes the
    // file. Requests presigned URLs (and PUTs them) a batch at a time rather than all up front --
    // see `PART_URL_BATCH_SIZE`. Returns whether the file finished uploading successfully.
    async function uploadParts(fileId, fileName, partCount, requestParts, blobs, alreadyUploaded) {
        const finalParts = new Array(partCount);
        let completedCount = alreadyUploaded.size;
        alreadyUploaded.forEach(function (part, partNumber) {
            finalParts[partNumber - 1] = {
                PartNumber: partNumber,
                ETag: part.etag,
                ChecksumSHA256: part.checksum,
            };
        });

        for (let batchStart = 0; batchStart < requestParts.length; batchStart += PART_URL_BATCH_SIZE) {
            const batch = requestParts.slice(batchStart, batchStart + PART_URL_BATCH_SIZE);
            const urlsResp = await fetch(base + '/files/' + fileId + '/parts/', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json', 'X-CSRFToken': csrftoken },
                body: JSON.stringify({ parts: batch }),
            });
            if (!urlsResp.ok) {
                const err = await urlsResp.json().catch(function () { return {}; });
                alert('Could not upload ' + fileName + ': ' + (err.error || urlsResp.status));
                return false;
            }
            const urlsData = await urlsResp.json();

            for (const requestPart of batch) {
                const partNumber = requestPart.part_number;
                const blob = blobs[partNumber - 1];
                const url = urlsData.urls[String(partNumber)];
                const putResp = await fetch(url, {
                    method: 'PUT',
                    headers: { 'x-amz-checksum-sha256': requestPart.checksum_sha256 },
                    body: blob,
                });
                if (!putResp.ok) {
                    alert(
                        'Uploading part ' + partNumber + ' of ' + fileName + ' failed: '
                        + putResp.status
                    );
                    return false;
                }
                finalParts[partNumber - 1] = {
                    PartNumber: partNumber,
                    ETag: putResp.headers.get('ETag'),
                    ChecksumSHA256: requestPart.checksum_sha256,
                };
                completedCount += 1;
                const entry = files.get(fileId);
                entry.progress = Math.round((completedCount / partCount) * 100);
                renderRow(fileId, entry);
            }
        }

        const completeResp = await fetch(base + '/files/' + fileId + '/complete/', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json', 'X-CSRFToken': csrftoken },
            body: JSON.stringify({ parts: finalParts }),
        });
        if (completeResp.ok) {
            const entry = files.get(fileId);
            entry.uploaded = true;
            entry.resumable = false;
            entry.progress = 100;
            renderRow(fileId, entry);
            updateSubmitState();
            return true;
        }
        const err = await completeResp.json().catch(function () { return {}; });
        alert('Upload of ' + fileName + ' failed: ' + (err.error || completeResp.status));
        return false;
    }

    async function uploadFile(file) {
        const isFirstFile = files.size === 0;
        const addResp = await fetch(base + '/files/', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json', 'X-CSRFToken': csrftoken },
            body: JSON.stringify({
                name: file.name,
                size: file.size,
                client_last_modified: file.lastModified,
            }),
        });
        if (!addResp.ok) {
            const err = await addResp.json().catch(function () { return {}; });
            alert('Could not add ' + file.name + ': ' + (err.error || addResp.status));
            return;
        }
        const added = await addResp.json();
        const fileId = added.file_id;
        const partSize = added.part_size_bytes;
        const partCount = added.part_count;

        files.set(fileId, {
            name: file.name,
            size: file.size,
            uploaded: false,
            progress: 0,
            clientLastModified: file.lastModified,
            resumable: false,
        });
        renderRow(fileId, files.get(fileId));
        if (isFirstFile && typeof onFirstFileAdded === 'function') onFirstFileAdded();

        // Slice every part and hash it up front (`Blob.slice` is a cheap, lazy view -- it doesn't
        // copy the underlying bytes), so each request for a presigned URL can carry that part's
        // checksum (see `sha256Base64` above).
        const blobs = [];
        const requestParts = [];
        for (let i = 0; i < partCount; i++) {
            const partNumber = i + 1;
            const start = i * partSize;
            const end = Math.min(start + partSize, file.size);
            const blob = file.slice(start, end);
            blobs[partNumber - 1] = blob;
            requestParts.push({
                part_number: partNumber,
                checksum_sha256: await sha256Base64(blob),
            });
        }

        await uploadParts(fileId, file.name, partCount, requestParts, blobs, new Map());
    }

    async function resumeFile(fileId, file, candidate) {
        const entry = files.get(fileId);
        const resumeResp = await fetch(base + '/files/' + fileId + '/resume/', {
            method: 'POST',
            headers: { 'X-CSRFToken': csrftoken },
        });
        if (!resumeResp.ok) {
            const err = await resumeResp.json().catch(function () { return {}; });
            alert('Could not resume ' + file.name + ': ' + (err.error || resumeResp.status));
            if (candidate) candidate.matched = false;
            return;
        }
        const resumeData = await resumeResp.json();
        const partSize = resumeData.part_size_bytes;
        const partCount = resumeData.part_count;
        // Empty when the upload had expired and the server restarted it from scratch
        // (`resumeData.restarted`) -- handled the same way as a resume with nothing done yet.
        // Each already-uploaded part's checksum (`checksum_sha256`) must be carried through to the
        // eventual `.../complete/` call: S3 requires it for every part once the upload was created
        // with a checksum algorithm, including ones this resume isn't re-uploading (issue #55
        // phase 3 review).
        const alreadyUploaded = new Map();
        resumeData.uploaded_parts.forEach(function (part) {
            alreadyUploaded.set(part.part_number, { etag: part.etag, checksum: part.checksum_sha256 });
        });

        const blobs = [];
        const requestParts = [];
        for (let i = 0; i < partCount; i++) {
            const partNumber = i + 1;
            if (alreadyUploaded.has(partNumber)) continue;
            const start = i * partSize;
            const end = Math.min(start + partSize, file.size);
            const blob = file.slice(start, end);
            blobs[partNumber - 1] = blob;
            requestParts.push({
                part_number: partNumber,
                checksum_sha256: await sha256Base64(blob),
            });
        }

        entry.uploaded = false;
        entry.resumable = false;
        entry.progress = Math.round((alreadyUploaded.size / partCount) * 100);
        renderRow(fileId, entry);

        const ok = await uploadParts(fileId, file.name, partCount, requestParts, blobs, alreadyUploaded);
        if (!ok && candidate) candidate.matched = false;
    }

    function handleFileSelection(fileList) {
        Array.from(fileList).forEach(function (file) {
            const candidate = findResumeMatch(file);
            if (candidate) {
                resumeFile(candidate.fileId, file, candidate);
            } else {
                uploadFile(file);
            }
        });
    }

    // Hydrate the file list from the draft's existing files (spec: resumable across a reload).
    (options.existingFiles || []).forEach(function (existing) {
        files.set(existing.id, {
            name: existing.name,
            size: existing.size,
            uploaded: existing.uploaded,
            progress: existing.uploaded ? 100 : 0,
            clientLastModified: existing.client_last_modified,
            resumable: !existing.uploaded,
        });
        renderRow(existing.id, files.get(existing.id));
        if (!existing.uploaded) {
            resumeCandidates.push({
                fileId: existing.id,
                name: existing.name,
                size: existing.size,
                clientLastModified: existing.client_last_modified,
                matched: false,
            });
        }
    });
    updateSubmitState();

    fileListEl.addEventListener('click', async function (e) {
        const btn = e.target.closest('button[data-remove]');
        if (!btn) return;
        const fileId = btn.getAttribute('data-remove');
        await fetch(base + '/files/' + fileId + '/remove/', {
            method: 'POST',
            headers: { 'X-CSRFToken': csrftoken },
        });
        files.delete(fileId);
        const row = document.getElementById('file-row-' + fileId);
        if (row) row.remove();
        updateSubmitState();
    });

    // Both send pages' options form posts through htmx (`#send-form`, `hx-post`). A paused
    // (not-yet-finished) file is silently left out of the transfer at send time
    // (`services.send.finalize_send`/`start_confirmation` only count `uploaded=True` files), so
    // ask for confirmation before letting the request through if any row is still paused --
    // `htmx:confirm` is htmx's own extensibility point for exactly this (see its docs), fired
    // before every request that element makes.
    const formEl = document.getElementById('send-form');
    if (formEl) {
        formEl.addEventListener('htmx:confirm', function (e) {
            if (!hasPausedFiles()) return;
            e.preventDefault();
            const proceed = window.confirm(
                "Some files haven't finished uploading and will be left out of this transfer. "
                + 'Send anyway?'
            );
            if (proceed) e.detail.issueRequest(true);
        });
    }

    return { handleFileSelection: handleFileSelection, hasPausedFiles: hasPausedFiles };
}

/**
 * Make `zoneEl` a drag & drop target for files *and* folders, calling `onFiles(File[])` with
 * everything dropped. A dropped folder is walked recursively (`webkitGetAsEntry`) into its
 * files, flat, the same as the "Add folder" picker's `webkitdirectory` input gives.
 */
function initDropZone(zoneEl, onFiles) {
    const activeClasses = ['border-brand-primary', 'bg-gray-50', 'dark:bg-gray-700'];
    let depth = 0;

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
        if (out.length) onFiles(out);
    });

    // A file dropped just outside the zone would make the browser navigate to it and lose the page.
    ['dragover', 'drop'].forEach(function (type) {
        window.addEventListener(type, function (e) {
            if (e.dataTransfer && Array.from(e.dataTransfer.types || []).indexOf('Files') !== -1) {
                e.preventDefault();
            }
        });
    });
}
