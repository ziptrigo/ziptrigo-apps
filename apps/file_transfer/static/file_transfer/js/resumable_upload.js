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
    // twice within one file-picker batch.
    const resumeCandidates = [];

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
            return candidate.fileId;
        }
        return null;
    }

    // Shared by a from-scratch upload and a resumed one: PUTs every part not already accounted
    // for in `alreadyUploaded` (part number -> ETag), in order, then completes the file.
    async function uploadParts(fileId, fileName, partCount, requestParts, blobs, alreadyUploaded) {
        const finalParts = new Array(partCount);
        alreadyUploaded.forEach(function (etag, partNumber) {
            finalParts[partNumber - 1] = { PartNumber: partNumber, ETag: etag };
        });

        if (requestParts.length) {
            const urlsResp = await fetch(base + '/files/' + fileId + '/parts/', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json', 'X-CSRFToken': csrftoken },
                body: JSON.stringify({ parts: requestParts }),
            });
            if (!urlsResp.ok) {
                const err = await urlsResp.json().catch(function () { return {}; });
                alert('Could not upload ' + fileName + ': ' + (err.error || urlsResp.status));
                return;
            }
            const urlsData = await urlsResp.json();

            for (const requestPart of requestParts) {
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
                    return;
                }
                finalParts[partNumber - 1] = {
                    PartNumber: partNumber,
                    ETag: putResp.headers.get('ETag'),
                    ChecksumSHA256: requestPart.checksum_sha256,
                };
                const entry = files.get(fileId);
                entry.progress = Math.round((partNumber / partCount) * 100);
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
        } else {
            const err = await completeResp.json().catch(function () { return {}; });
            alert('Upload of ' + fileName + ' failed: ' + (err.error || completeResp.status));
        }
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

        // Slice every part and hash it up front, so each request for a presigned URL can carry
        // that part's checksum (see `sha256Base64` above).
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

    async function resumeFile(fileId, file) {
        const entry = files.get(fileId);
        const resumeResp = await fetch(base + '/files/' + fileId + '/resume/', {
            method: 'POST',
            headers: { 'X-CSRFToken': csrftoken },
        });
        if (!resumeResp.ok) {
            const err = await resumeResp.json().catch(function () { return {}; });
            alert('Could not resume ' + file.name + ': ' + (err.error || resumeResp.status));
            return;
        }
        const resumeData = await resumeResp.json();
        const partSize = resumeData.part_size_bytes;
        const partCount = resumeData.part_count;
        // Empty when the upload had expired and the server restarted it from scratch
        // (`resumeData.restarted`) -- handled the same way as a resume with nothing done yet.
        const alreadyUploaded = new Map();
        resumeData.uploaded_parts.forEach(function (part) {
            alreadyUploaded.set(part.part_number, part.etag);
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
        entry.progress = Math.round(((partCount - requestParts.length) / partCount) * 100);
        renderRow(fileId, entry);

        await uploadParts(fileId, file.name, partCount, requestParts, blobs, alreadyUploaded);
    }

    function handleFileSelection(fileList) {
        Array.from(fileList).forEach(function (file) {
            const matchId = findResumeMatch(file);
            if (matchId) {
                resumeFile(matchId, file);
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

    return { handleFileSelection: handleFileSelection };
}
