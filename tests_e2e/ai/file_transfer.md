# File transfer

## Scope

The `apps/file_transfer` web UI, against the real local S3 (Floci) and real email:

- the logged-in send page `/transfer/send/` (file picker, folders, remove, progress, multipart
  uploads of several parts with per-part checksums, resuming an interrupted upload after a reload
  with `?resume=`, the options form and its validation, the credit minimum) and the sent page
  `/transfer/sent/<id>/`;
- every email the app sends, and the links in them: to recipients, the sender's copy, "was
  downloaded", "expires tomorrow", "files deleted", "suspended (out of credits)", and the anonymous
  confirmation and sender copy;
- the dashboard `/transfer/` (Active/Ended filters, the row, the download log, and the actions:
  copy link, resend and its 10-minute cooldown, add recipients, extend/shorten the expiry, max
  downloads, password, disable, re-enable, delete now, the "Resume it" link for an unfinished
  upload);
- the public download page `/t/<slug>/` (file list, per-file download with the right name and
  bytes, password gate, "Download all (.zip)" built by streaming from S3, the neutral "no longer
  available" page, "Report this transfer");
- the daily jobs' visible effects (metering, suspension and its in-app notification, re-enabling
  on a top-up, the suspension grace period, the expiry reminder, natural expiry, the deferred file
  deletion after the last allowed download), triggered with shell steps because the scheduler does
  not run on the test server;
- anonymous sending `/transfer/send/anon/` (off by default, switched on in the admin), its email
  confirmation by code and by link, resending the code, the anonymous limits, the anonymous sent
  page, the one-time manage link, and claim on login (including the zero-balance deferral);
- access control (logged out, another user's transfer), phone width and dark mode.

Out of scope here (see the end of the file): rate limits, the admin moderation tools (takedown,
block list, auto-hold: `admin.md`), and the `/api/ft/` JWT API and `admin/filetransfer.py` CLI.

## Preconditions

- The AI test server is running at `http://localhost:8765` with a fresh database (see
  [`README.md`](README.md)). All URLs below are relative to it.
- Seeded accounts (password `QaE2e-Passw0rd!` for all):
  - `qa.user@example.com` -- the main sender, 100 credits;
  - `qa.broke@example.com` -- "another user", 0 credits;
  - `qa.admin@example.com` -- superuser, for the admin settings and the credit adjustment tool
    (`/admin/billing/credittransaction/adjust/`: User email, Direction Add/Spend, Amount, Apply).
  - FT-42 registers a fresh account, `qa.ft.<rand>@example.com`, for the billing cases.
- **S3 is Floci**, a real S3 emulator: the browser PUTs each part of a file straight to a presigned
  URL on the bucket (`http://localhost:4566/...?partNumber=N&uploadId=...`, with an
  `x-amz-checksum-sha256` header), and a download is a 302 from `/t/...` to a short-lived (15
  minutes) presigned GET URL on the same host, which serves the file as an attachment under its
  original name. The bucket is emptied on every server start. Background tasks run inline, so the
  "download all" zip is built during the request that asks for it.
- **Mailbox:** `/__e2e__/mail/` lists the emails the site sent in this run, newest first;
  `?to=<address>` filters by the address the site sent it to (case-insensitive). Each entry shows
  **To**, **Subject**, **Sent** and the text body, whose links are real, absolute
  (`http://localhost:8765/...`) and clickable; an HTML body is shown as source. Email goes over real
  SMTP, so an email takes a few seconds to arrive: **reload until it arrives, up to a minute**.
  Where a step says no email is sent, wait a full minute and reload before concluding so.
- **Shell steps** (marked "(shell step)") stand in for the daily jobs, which nothing in the UI or
  the admin can trigger. Run them from the repo root with the wrapper, which uses the running
  server's own database, bucket and mail:
  `python tests_e2e/ai/manage.py shell -c "..."` (add `--port <port>` before `shell` when your
  server is not on 8765). Without a shell, mark those steps (and what depends on them)
  **BLOCKED (needs-setup)**.

### Times

The pages show times with the browser's own time zone (`<time>` elements rewritten in the
browser, e.g. "Nov 30, 2026, 3:51 PM" or, in the dashboard's "sent" line and the download log,
`2026-11-30 15:51`), without a "UTC" suffix. Emails always write UTC (`2026-11-30 15:51 UTC`).
A "Set a date" expiry means the **end of that day in the account's time zone** (Account page >
time zone; the browser sets it on the first visit after sign-in), so on pages it reads as 11:59 PM
(23:59) of that day, and in an email it is that same instant converted to UTC -- exactly `23:59
UTC` only when the account's zone is UTC. Note the zone when you start.

### Browser windows

Use two independent browser contexts with separate cookies:

- **Main window** -- logged in (as `qa.user` unless a case says otherwise).
- **Visitor window** -- a private/incognito context (or a second browser profile, or another host
  alias of the same server such as `127.0.0.1:8765`) where nobody is logged in. Used for the public
  download page and anonymous sending. If the tool only has one context, log out of the main window
  before a visitor step and log back in afterwards; cookies must not carry over (clear them if
  possible). Some anonymous cases need a **clean** visitor context: close it and open a new private
  one, or clear its cookies.

Log out through the account menu in the header (logout is a POST form there, not a link).

### Values to record

Write these down as you go; later cases use them:

| Name | Where it comes from |
|---|---|
| `TRANSFER_A_ID`, `SLUG_A` | FT-07: the sent page URL `/transfer/sent/<TRANSFER_A_ID>/` and the link `http://localhost:8765/t/<SLUG_A>/` |
| `SLUG_B`, `URL_B` | FT-09; `URL_B` is the presigned URL of B's one download (FT-24) |
| `SLUG_C`, `URL_C` | FT-10; `URL_C` in FT-26 |
| `ANON_SLUG_1`, `ANON_MANAGE_URL` | FT-33 |
| Time A was sent | FT-07 (for the resend cooldown in FT-37) |
| `SLUG_BIG` | FT-39 |
| `SLUG_E` | FT-41 |
| `FT_USER`, `SLUG_M` | FT-42 (the fresh account and its transfer M); `URL_M` in FT-45 |
| `SLUG_M2` | FT-46 |

A slug is 22 letters/digits.

## Test data

Test files are created in the page with JavaScript, so nothing has to exist on disk. If the
browser tool can attach real files to an `<input type="file">`, that is fine too: use files with
the same names, sizes and content.

**Add files through the "Choose files" input** (`#file-input`; for the anonymous page it is the
same id). Run in the page's console / JS tool, adjusting the list:

```js
((specs) => {
  const dt = new DataTransfer();
  for (const s of specs) {
    dt.items.add(new File([s.content], s.name, { type: 'text/plain', lastModified: s.lastModified ?? Date.now() }));
  }
  const input = document.getElementById('file-input');
  input.files = dt.files;
  input.dispatchEvent(new Event('change', { bubbles: true }));
})([
  { name: 'qa-report.txt', content: 'R'.repeat(2999) + '\n' },   // 3000 bytes
]);
```

Files used in this plan:

| Name | Content (bytes) | Used in |
|---|---|---|
| `qa-report.txt` | `'R'.repeat(2999) + '\n'` (3,000; SHA-256 `d47d8e2f87eb95aff0cf89b622f864252a78bbc73b4e9d2f3f3c54113620d5e6`) | Transfer A |
| `qa-notes.txt` | `'N'.repeat(1499) + '\n'` (1,500) | Transfer A |
| `qa-remove-me.txt` | `'X'.repeat(99) + '\n'` (100) | FT-04, removed |
| `empty.txt` | `''` (0) | FT-05, refused |
| `qa-limit.txt` | `'L'.repeat(499) + '\n'` (500) | Transfer B |
| `qa-paused.txt` | `'P'.repeat(1199) + '\n'` (1,200), `lastModified: 1700000000000` | Transfer C |
| `qa-broke.txt` | `'B'.repeat(99) + '\n'` (100) | FT-11 |
| `qa-anon.txt` | `'A'.repeat(799) + '\n'` (800) | Anonymous transfer 1 |
| `qa-anon-link.txt` | `'K'.repeat(899) + '\n'` (900) | Anonymous transfer 2 |
| `qa-net.txt`, `qa-net2.txt` | `'W'.repeat(2999) + '\n'` (3,000), `lastModified: 1700000000002`; `'V'.repeat(799) + '\n'` (800), `lastModified: 1700000000003` | FT-38 |
| `qa-big.bin` | 70 MiB built by the snippet below (73,400,320; two parts) | Transfer BIG |
| `qa-dup.txt` (twice) | `'1'.repeat(9) + '\n'` (10), then `'2'.repeat(19) + '\n'` (20) | Transfer BIG |
| `relatório café.txt` | `'Olá\n'` (5 bytes in UTF-8) | Transfer BIG |
| `qa-expiring.txt` | `'E'.repeat(299) + '\n'` (300) | Transfer E |
| `qa-meter.txt` | `'M'.repeat(2999) + '\n'` (3,000) | Transfer M |
| `qa-meter-2.txt` | `'D'.repeat(399) + '\n'` (400) | Transfer M2 |
| `qa-anon-3.txt`, `qa-anon-4.txt`, `qa-anon-5.txt` | `'3'.repeat(99) + '\n'` (100) and likewise with `4`, `5` | FT-47 to FT-49 |

**The large file.** The part size is 64 MiB (`PART_SIZE_BYTES`, 67,108,864 bytes), so a 70 MiB file
is uploaded in two parts (64 MiB + 6 MiB). Build it in the page (byte `i` is `i % 251`; it takes a
few seconds) and add it through the file input:

```js
(() => {
  const size = 70 * 1024 * 1024;
  const bytes = new Uint8Array(size);
  for (let i = 0; i < size; i++) bytes[i] = i % 251;
  const dt = new DataTransfer();
  dt.items.add(new File([bytes], 'qa-big.bin', { type: 'application/octet-stream', lastModified: 1700000000001 }));
  const input = document.getElementById('file-input');
  input.files = dt.files;
  input.dispatchEvent(new Event('change', { bubbles: true }));
})();
```

Its SHA-256 is `24f1c89f87a1ed78b333d3560219a9faa42c74da56497d5eb9275dbcb0eed34a`.

**Drop a folder** on the drop zone (`#drop-zone`), simulating what the browser reports for a
dragged folder `qa-folder/` holding `qa-folder-a.txt` and `inner/qa-folder-b.txt` (the same
technique as `tests_e2e/test_drop_zone.py`):

```js
(() => {
  const fileEntry = (name) => ({ isFile: true, isDirectory: false,
    file: (resolve) => resolve(new File([name + '\n'], name, { type: 'text/plain' })) });
  const dirEntry = (name, children) => ({ isFile: false, isDirectory: true, name,
    createReader: () => { let i = 0; return { readEntries: (resolve) => resolve(i < children.length ? [children[i++]] : []) }; } });
  const folder = dirEntry('qa-folder', [fileEntry('qa-folder-a.txt'), dirEntry('inner', [fileEntry('qa-folder-b.txt')])]);
  const dt = { types: ['Files'], files: [], items: [{ kind: 'file', webkitGetAsEntry: () => folder }] };
  const drop = new Event('drop', { bubbles: true, cancelable: true });
  Object.defineProperty(drop, 'dataTransfer', { value: dt });
  document.getElementById('drop-zone').dispatchEvent(drop);
})();
```

**Break the upload of one part.** To simulate a dropped connection for the bucket's part PUTs
(all parts, or only part 2), run in the page before adding the file, and restore with
`window.fetch = window.__ftFetch;` (a reload restores it too):

```js
window.__ftFetch = window.__ftFetch || window.fetch;
window.__ftBlock = /partNumber=2(&|$)/;   // only part 2; use /partNumber=/ to break every part
window.fetch = (u, o) => (o && o.method === 'PUT' && window.__ftBlock.test(String(u)))
  ? Promise.reject(new TypeError('Failed to fetch')) : window.__ftFetch(u, o);
```

**Downloads count.** Every request to `/t/<slug>/files/<id>/` or `/t/<slug>/zip/` counts one
download against the transfer's max downloads (and emails the sender a "was downloaded" notice
when that is on). It answers a 302 to a presigned URL on the bucket (`http://localhost:4566/...`),
valid 15 minutes; opening or fetching **that** URL again does not count. To check what a URL
serves, define this helper in the page (again after every navigation) and call it with a `/t/...`
link (counts a download) or with a presigned URL from the network log (doesn't):

```js
window.ftFetch = async (url) => {
  const r = await fetch(url);
  const buf = await r.arrayBuffer();
  const hash = await crypto.subtle.digest('SHA-256', buf);
  return { status: r.status, url: r.url, bytes: buf.byteLength,
           sha256: Array.from(new Uint8Array(hash), (b) => b.toString(16).padStart(2, '0')).join(''),
           text: buf.byteLength <= 4096 ? new TextDecoder().decode(buf) : null };
};
```

`r.url` is the presigned URL the download ended on (record it when a case asks for `URL_...`).
The file's *name* comes from the `Content-Disposition` the bucket answers, which a script can't
read: check it on a real click, where the browser saves the file under that name.

**Recipients:** `qa.recipient1@example.com`, `qa.recipient2@example.com`,
`qa.recipient3@example.com`, `qa.broke.recipient@example.com`, `qa.anon.recipient@example.com`,
`qa.anon.recipient2@example.com`, `qa.anon.recipient3@example.com`; the anonymous sender
`qa.anon.sender@example.com`.

---

## Logged-in sending

### FT-01 Logged-out visitors are sent to the login page

**Purpose:** every logged-in File transfer page requires a session.
**Preconditions:** visitor window (nobody logged in).

| # | Action | Expected result |
|---|---|---|
| 1 | Go to `/transfer/`. | Redirected to `/account/login/?next=/transfer/`; the login page is shown. |
| 2 | Go to `/transfer/send/`. | Redirected to `/account/login/?next=/transfer/send/`. |
| 3 | Go to `/transfer/transfers/?filter=ended`. | Redirected to the login page (`next=/transfer/transfers/...`). |
| 4 | In the main window, log in as `qa.user@example.com` via `/account/login/?next=/transfer/`. | After logging in you land on `/transfer/` ("Your transfers"). |

### FT-02 Send page layout

**Purpose:** the send page renders every control with the right labels and defaults.
**Preconditions:** main window, logged in as `qa.user`.

| # | Action | Expected result |
|---|---|---|
| 1 | On `/transfer/`, click **New transfer**. | `/transfer/send/` opens. Title "Send files - ZipTrigo", heading **Send files**. |
| 2 | Read the line under the heading. | "Your balance: 100 credits. Sending needs at least 1." -- no "Buy credits" link (the balance is enough). |
| 3 | Look at the **Files** card. | A dashed drop zone with a cloud icon, "Drag & drop files or folders here, or", and two buttons **Choose files** and **Add folder**. The file list below it is empty. |
| 4 | Look at the options form. | Fields in order: **Recipients** (textarea, help text "One email address per line (or separated by commas)."), **Message** (textarea), **Expiry** (select), **Max downloads** (number), **Password (optional)** (password input with an eye button), and a checkbox "Email me when a file is downloaded" that is **checked**. |
| 5 | Open the **Expiry** select. | Options: "1 day", "5 days", "15 days", "30 days", "Set a date", "No expiration"; "No expiration" is selected. No date picker is visible. |
| 6 | Choose "Set a date". | A date button reading "Select a date" with a calendar icon appears under the select. Click it: a calendar for the current month opens with "Previous month"/"Next month" arrows, weekday headers, days before today greyed out and disabled, and **Clear** / **Today** at the bottom. Press Escape to close it, then set Expiry back to "No expiration" (the date button hides). |
| 7 | Type `abc` in the password field, click the eye button, then click it again. | The password becomes readable (`abc`, eye-slash icon, button label "Hide password"), then hidden again ("Show password"). Clear the field. |
| 8 | Look at the bottom of the form. | A **Send** button that is **disabled** (dimmed, not-allowed cursor) and the hint "Add at least one file that has finished uploading." |

### FT-03 Upload files: progress and completion

**Purpose:** files go straight to the bucket (Floci) in parts and the row reflects progress and completion.
**Preconditions:** FT-02, still on `/transfer/send/`.

| # | Action | Expected result |
|---|---|---|
| 1 | Add `qa-report.txt` (3,000 bytes) through the file input (see Test data). | A row appears: "qa-report.txt (2.9 KB)", a progress bar, then a green check (tooltip "Done") and a red trash icon. No alert is shown. |
| 2 | Look at the address bar. | The URL is now `/transfer/send/?resume=<uuid>` (added without a page reload). |
| 3 | Look at the **Send** button. | It is now enabled, and the hint "Add at least one file that has finished uploading." under it has disappeared (it reappears if every file is removed). |
| 4 | Add `qa-notes.txt` (1,500 bytes) the same way. | A second row "qa-notes.txt (1.5 KB)" appears below the first and reaches the green check. |
| 5 | Check the network log (or console). | For each file: one `POST .../files/` (201, answering `part_size_bytes` 67108864 and `part_count` 1), one `POST .../files/<id>/parts/`, one `PUT` to the presigned part URL on the bucket (`http://localhost:4566/...?partNumber=1&uploadId=...`, with an `x-amz-checksum-sha256` request header; 200 with an `ETag`) and one `POST .../files/<id>/complete/` (200). No console errors (in particular no CORS error). |

### FT-04 Remove a file; the list survives a reload

**Purpose:** a file can be removed from the draft, and the draft's files come back after a reload.
**Preconditions:** FT-03.

| # | Action | Expected result |
|---|---|---|
| 1 | Add `qa-remove-me.txt` (100 bytes). | Row "qa-remove-me.txt (100 bytes)" reaches the green check (sizes below 1 KB are shown in bytes, from 1 KB with one decimal, like the download page). |
| 2 | Click its trash icon (accessible name "Remove qa-remove-me.txt"). | The row disappears; the other two rows stay; **Send** stays enabled. |
| 3 | Reload the page (the URL still has `?resume=`). | The page lists exactly `qa-report.txt (2.9 KB)` and `qa-notes.txt (1.5 KB)`, both with green checks; `qa-remove-me.txt` is gone; **Send** is enabled. |

### FT-05 Add a folder; an empty file is refused

**Purpose:** a dropped folder is walked recursively and added flat; empty files are rejected.
**Preconditions:** FT-04, on the same draft.

| # | Action | Expected result |
|---|---|---|
| 1 | Run the "Drop a folder" snippet. | Two rows appear, "qa-folder-a.txt" and "qa-folder-b.txt" (names only, no folder path), both reaching the green check. |
| 2 | If the browser tool can pick a real folder, click **Add folder** and choose a folder with one small file. | A native folder picker opens; the folder's file is added as a row. (Skip this step if the tool cannot pick folders.) |
| 3 | Remove every row added in this case with its trash icon. | Only `qa-report.txt` and `qa-notes.txt` remain. |
| 4 | Add `empty.txt` (0 bytes) through the file input. | No browser alert. A red line with `role="alert"` appears above the file list: "Could not add empty.txt: File is empty." No row is added. Adding another file (or the next selection) clears the line. |

### FT-06 Options form validation

**Purpose:** invalid options are reported in the red box above the files without losing the files.
**Preconditions:** FT-05 (two uploaded files on the draft).

| # | Action | Expected result |
|---|---|---|
| 1 | Leave **Recipients** empty and click **Send**. | The browser's own "fill out this field" bubble appears on Recipients; no request is sent. |
| 2 | Enter three spaces in Recipients and click **Send**. | A red box appears above the Files card with "Recipients: This field is required." |
| 3 | Enter `,` and click **Send**. | The red box now reads "Add at least one recipient." (it replaces the previous message, it doesn't add to it). |
| 4 | Enter `qa.recipient1@example.com, not-an-email` and click **Send**. | Red box: `Recipients: "not-an-email" is not a valid email address.` |
| 5 | Fill Recipients with 21 addresses: run `document.getElementById('id_recipients').value = Array.from({length: 21}, (_, i) => 'qa.many' + (i + 1) + '@example.com').join('\n')`, then click **Send**. | Red box: "A transfer can have at most 20 recipients." |
| 6 | Set Recipients to `qa.recipient1@example.com`, choose Expiry "Set a date" without picking a date, click **Send**. | Red box: "Expiry date: Provide a date for a custom expiry." |
| 7 | Check the page and the mailbox (`/__e2e__/mail/?to=qa.recipient1@example.com`). | The two file rows are still listed with green checks; the mailbox shows "No emails." (after waiting a minute) -- nothing was sent. |
| 8 | Set Expiry back to "No expiration", type `0` in **Max downloads** and click **Send**. | The browser's own bubble refuses a value below 1 (the input has `min="1"`). |
| 9 | Run `document.getElementById('send-form').noValidate = true` and click **Send** again. | Red box: "Max downloads: Ensure this value is greater than or equal to 1."; the same message under the Max downloads field. Clear Max downloads. |

### FT-07 Send transfer A

**Purpose:** a valid send activates the transfer and shows the link.
**Preconditions:** FT-06.

| # | Action | Expected result |
|---|---|---|
| 1 | Set Recipients to three lines: `qa.recipient1@example.com`, `qa.recipient2@example.com`, `QA.RECIPIENT1@example.com`. | -- |
| 2 | Message: `Hello from QA.` + new line + `Second line.`; Expiry "30 days"; Max downloads `10`; Password empty; leave "Email me when a file is downloaded" checked. Click **Send**. | The page navigates to `/transfer/sent/<uuid>/`. Record the uuid as `TRANSFER_A_ID` and the current time. |
| 3 | Look at the sent page. | Title "Transfer sent - ZipTrigo", a check icon, heading **Your transfer is on its way**, text "We emailed the recipients and sent you a copy. Here's the link too:", a read-only input holding `http://localhost:8765/t/<slug>/`, a **Copy** button and a link **Back to your transfers**. Record the slug as `SLUG_A`. |
| 4 | Click **Copy**. | The link is copied (if the tool can read the clipboard, it holds the same URL) and the button label changes to **Copied** for about two seconds, then back to **Copy**. |
| 5 | Go back to `/transfer/send/`. | A fresh, empty send page (no file rows, no `?resume=`); Transfer A's files are not listed. |

### FT-08 Emails for transfer A

**Purpose:** each distinct recipient and the sender get the right email.
**Preconditions:** FT-07.

| # | Action | Expected result |
|---|---|---|
| 1 | Open `/__e2e__/mail/?to=qa.recipient1@example.com`. | Exactly **one** email (the duplicate `QA.RECIPIENT1@...` line was merged). Subject: `qa-report.txt and 1 more file were sent to you via ZipTrigo` (the first file and a count of the others; a single-file transfer reads `<name> was sent to you via ZipTrigo`). |
| 2 | Read its body. | "You have received 2 files:" followed by the lines "- qa-report.txt" and "- qa-notes.txt" (every uploaded file is named, up to 20, then "- and N more"; one file reads "You have received a file: <name>."), a blank line, "Hello from QA." / "Second line.", then "Download: http://localhost:8765/t/<SLUG_A>/" and "This link expires on YYYY-MM-DD HH:MM UTC." exactly 30 days from the send, in UTC. No HTML body. |
| 3 | Open `/__e2e__/mail/?to=qa.recipient2@example.com`. | One email with the same subject, file list and download link. |
| 4 | Open `/__e2e__/mail/?to=qa.user@example.com`. | Newest email subject: `Your transfer "qa-report.txt" is on its way`. Body: `Your transfer "qa-report.txt" was sent to: qa.recipient1@example.com, qa.recipient2@example.com.`, then "Download link: http://localhost:8765/t/<SLUG_A>/" and "Manage this transfer: http://localhost:8765/transfer/". |
| 5 | Click the download link in the recipient email. | The public download page for `qa-report.txt` opens (it is fine that the main window is logged in; downloads are tested in FT-20). Don't click Download here. |
| 6 | In the sender's copy (step 4), click the "Download link", then the "Manage this transfer" link. | The first opens the same download page; the second opens `/transfer/` (the dashboard) in the logged-in main window. Opened in the visitor window (logged out), the manage link goes to `/account/login/?next=/transfer/`. |

### FT-09 Send transfer B: password, custom date, one download

**Purpose:** the custom expiry date, password and max downloads options are saved.
**Preconditions:** main window as `qa.user`.

| # | Action | Expected result |
|---|---|---|
| 1 | Go to `/transfer/send/` and add `qa-limit.txt` (500 bytes). | Row "qa-limit.txt (500 bytes)" reaches the green check. |
| 2 | Recipients `qa.recipient1@example.com`; Expiry "Set a date"; click the date button, click **Next month**, then click day `15`. | The date button now shows the 15th of next month (in the browser's date format) and the calendar closes. |
| 3 | Max downloads `1`; Password `Open-Sesame-42`; uncheck nothing. Click **Send**. | The sent page opens. Record the slug as `SLUG_B`. |
| 4 | Open `/__e2e__/mail/?to=qa.recipient1@example.com`. | The newest email's subject is `qa-limit.txt was sent to you via ZipTrigo`, and its body says "This link expires on <date> <time> UTC." where that is the end of the 15th of next month in the account's zone, in UTC (see "Times": `<15th> 23:59 UTC` for a UTC account). There is no message paragraph (none was entered), and nothing in the email mentions the password. |

### FT-10 Paused upload: resume after a reload, and send transfer C

**Purpose:** a file whose upload never finished shows as Paused after a reload, sending with it
asks for confirmation, and re-selecting the same file resumes it.
**Preconditions:** main window as `qa.user`.

| # | Action | Expected result |
|---|---|---|
| 1 | Go to `/transfer/send/`. Register a file without uploading its bytes: `fetch('/transfer/send/' + document.getElementById('send-form').getAttribute('hx-post').split('/')[3] + '/files/', {method: 'POST', headers: {'Content-Type': 'application/json', 'X-CSRFToken': getCookie('csrftoken')}, body: JSON.stringify({name: 'qa-paused.txt', size: 1200, client_last_modified: 1700000000000})}).then(r => r.status)` | Returns `201`. |
| 2 | Note the draft id (the 4th path segment of the form's `hx-post`, as above) and open `/transfer/send/?resume=<draft id>`. | One row "qa-paused.txt (1.2 KB)" with the status text **Paused** (no check); hovering the row shows "Select the same file again to resume uploading it." **Send** is disabled. |
| 3 | Add `qa-report.txt` again (3,000 bytes) so the transfer has a finished file. Then set Recipients `qa.recipient2@example.com` and click **Send**. | A browser confirm dialog: "Some files haven't finished uploading and will be left out of this transfer. Send anyway?" Click **Cancel**: nothing is sent, you stay on the page. |
| 4 | Remove the `qa-report.txt` row. Add `qa-paused.txt` through the file input with **exactly** the content and `lastModified: 1700000000000` from Test data. | The existing Paused row is reused (no second row): it shows progress and then the green check. **Send** is enabled. |
| 5 | Click **Send**. | No confirm dialog this time; the sent page opens. Record the slug as `SLUG_C`. |

### FT-11 Not enough credits; resuming an unfinished upload from the dashboard

**Purpose:** a sender below the 1-credit minimum can upload but not send, and the dashboard
offers to resume the unfinished draft.
**Preconditions:** visitor window, log in there as `qa.broke@example.com`.

| # | Action | Expected result |
|---|---|---|
| 1 | Go to `/transfer/send/`. | "Your balance: 0 credits. Sending needs at least 1. Buy credits." -- **Buy credits** links to `/billing/credits/`. |
| 2 | Add `qa-broke.txt`; Recipients `qa.broke.recipient@example.com`; click **Send**. | The upload succeeds (green check). The red box shows "You need at least 1 credit to send a transfer." You stay on the send page. |
| 3 | Open `/__e2e__/mail/?to=qa.broke.recipient@example.com`. | "No emails." |
| 4 | Go to `/transfer/`. | "Your transfers" with the empty state **No transfers yet**, "Send a file to anyone with a link that expires." and a **Send files** button (to `/transfer/send/`), and a banner above the tabs: "You have an unfinished upload." with a **Resume it** link. |
| 5 | Click **Resume it**. | `/transfer/send/?resume=<uuid>` opens with the `qa-broke.txt` row already listed with a green check and **Send** enabled. |

### FT-12 Another user's transfer is not reachable

**Purpose:** dashboard actions and pages on someone else's transfer answer 404 and change nothing.
**Preconditions:** visitor window still logged in as `qa.broke`; `TRANSFER_A_ID` from FT-07.

| # | Action | Expected result |
|---|---|---|
| 1 | Go to `/transfer/sent/<TRANSFER_A_ID>/`. | A 404 "Page not found" response; no link or recipient of Transfer A is shown. |
| 2 | On `/transfer/`, run: `(async () => { const t = document.cookie.match(/csrftoken=([^;]+)/)[1]; const id = '<TRANSFER_A_ID>'; const out = {}; for (const p of ['disable', 'reenable', 'delete', 'settings', 'recipients']) { const body = new URLSearchParams({expiry_choice: 'none', recipients: 'x@example.com'}); out[p] = (await fetch('/transfer/' + id + '/' + p + '/', {method: 'POST', headers: {'X-CSRFToken': t, 'HX-Request': 'true'}, body})).status; } return out; })()` | Every status is `404`. |
| 3 | Go to `/transfer/send/?resume=<TRANSFER_A_ID>`. | qa.broke's own send page (its `qa-broke.txt` draft or an empty one); nothing of Transfer A appears. |
| 4 | Log out of the visitor window. In the main window (qa.user) open `/transfer/`. | Transfer A is still **ACTIVE** with its 2 recipients and 10 max downloads. |

## Dashboard

### FT-13 Transfer list and Active/Ended filters

**Purpose:** the dashboard lists sent transfers with their details and filters them.
**Preconditions:** main window as `qa.user`; FT-07, FT-09, FT-10 done.

| # | Action | Expected result |
|---|---|---|
| 1 | Go to `/transfer/`. | Heading **Your transfers**, a **New transfer** button, tabs **Active** (highlighted) and **Ended**. No "You have an unfinished upload." banner. Three rows, newest first: `qa-paused.txt` (C), `qa-limit.txt` (B), `qa-report.txt` (A). No draft or "Untitled transfer" rows. |
| 2 | Read row A. | "2 files · sent YYYY-MM-DD HH:MM · ACTIVE" (local time; status rendered uppercase), then "Expires: <30 days ahead, local time> · Downloads: 0 / 10 (10 left) · Credits charged: 0". Buttons: **Copy link**, **Disable**, **Manage**, **Delete**. Page title "File Transfer - ZipTrigo". |
| 3 | Read row B. | "1 file · sent ... · ACTIVE", "Expires: <15th of next month>, 11:59 PM (local) · Downloads: 0 / 1 (1 left) · Credits charged: 0". |
| 4 | Read row C. | "1 file · ... · ACTIVE", "Expires: Never · Downloads: 0 · Credits charged: 0" (no "/ max" part). |
| 5 | Click **Ended**. | The list swaps without a full reload to the empty state **No ended transfers** with "Transfers that expire or are deleted appear here." (no button); the URL becomes `/transfer/?filter=ended`; the **Ended** tab is now the highlighted one (`aria-selected="true"`). |
| 6 | Click **Active**. | The three rows come back, URL `/transfer/?filter=active`, **Active** highlighted. |
| 7 | Open `/transfer/?filter=ended` directly, then `/transfer/?filter=active`. | Same results as steps 5 and 6, with the matching tab highlighted. |

### FT-14 Copy link on a row

**Purpose:** the row's Copy link button copies the public link.
**Preconditions:** FT-13.

| # | Action | Expected result |
|---|---|---|
| 1 | Click **Copy link** on row A. | The download link `http://localhost:8765/t/<SLUG_A>/` is copied to the clipboard (check with `navigator.clipboard.readText()` if allowed, or paste into the Recipients box of the send page) and the button label reads **Copied** for about two seconds before going back to **Copy link**. |
| 2 | Check the console. | No errors. |

### FT-15 Manage panel contents

**Purpose:** the Manage panel shows the current settings, recipients and actions.
**Preconditions:** FT-13.

| # | Action | Expected result |
|---|---|---|
| 1 | Click **Manage** on row A. | A panel opens below the row with two columns. Click again closes it; open it again. |
| 2 | Read the left column. | "Expiry & limits"; **Expiry** select with "Set a date" selected and a date button showing A's expiry date; "Max downloads (blank = uncapped)" holding `10`; "Password (none; leave blank to keep it unchanged)" (empty input); a "Remove password" checkbox; **Save**. |
| 3 | Read the right column. | "Recipients": `qa.recipient1@example.com` and `qa.recipient2@example.com`, each with a **Resend** link; "Add recipients" textarea with placeholder `one@example.com` and an **Add** button. No "Download log" section (nothing downloaded yet). |

### FT-16 Resend is refused during the cooldown

**Purpose:** a recipient can't be re-emailed within 10 minutes of the last email.
**Preconditions:** FT-15, less than 10 minutes since FT-07 (otherwise see FT-37).

| # | Action | Expected result |
|---|---|---|
| 1 | In row A's Manage panel click **Resend** next to `qa.recipient1@example.com`. | Row A re-renders (the panel closes) with a red message at its top: "This recipient was emailed recently; you can resend in N minutes." with N between 1 and 10. |
| 2 | Open `/__e2e__/mail/?to=qa.recipient1@example.com`. | Still no second `qa-report.txt and 1 more file were sent to you via ZipTrigo` email (one for A; the other one in the mailbox is B's `qa-limit.txt was sent to you via ZipTrigo`). |

### FT-17 Add recipients

**Purpose:** new recipients are validated, capped, de-duplicated against existing ones and emailed.
**Preconditions:** FT-16.

| # | Action | Expected result |
|---|---|---|
| 1 | Open Manage on row A, leave "Add recipients" empty and click **Add**. | Row A shows the red message "This field is required." |
| 2 | Open Manage, enter `bad@` and click **Add**. | Red message: `"bad@" is not a valid email address.` |
| 3 | Open Manage, enter `qa.recipient2@example.com, qa.recipient3@example.com` and click **Add**. | Row A re-renders without an error. Open Manage: the recipients are qa.recipient1, qa.recipient2 and qa.recipient3 (no duplicate of recipient2). |
| 4 | Open `/__e2e__/mail/?to=qa.recipient3@example.com` and `?to=qa.recipient2@example.com`. | recipient3 has one email `qa-report.txt and 1 more file were sent to you via ZipTrigo` with the A link; recipient2 still has only its original one. |
| 5 | Open Manage, fill its "Add recipients" textarea with 18 new addresses (`document.querySelector('#transfer-row-<TRANSFER_A_ID> textarea[name=recipients]').value = Array.from({length: 18}, (_, i) => 'qa.extra' + (i + 1) + '@example.com').join('\n')`) and click **Add**. | Red message: "A transfer can have at most 20 recipients." (3 + 18 = 21). Manage still lists 3 recipients and no `qa.extra*` address got an email. |

### FT-18 Change expiry and max downloads

**Purpose:** the settings form updates the transfer and reports invalid input.
**Preconditions:** FT-17.

| # | Action | Expected result |
|---|---|---|
| 1 | Open Manage on row A; set Expiry "15 days" and Max downloads `5`; click **Save**. | Row A re-renders: "Expires: <now + 15 days, local time> · Downloads: 0 / 5 (5 left)". |
| 2 | Open Manage; set Expiry "Set a date", open the date button, click **Clear**, click **Save**. | Red message "Provide a date for a custom expiry."; the row's expiry is unchanged. |
| 3 | Open Manage; set Expiry "No expiration"; Save. | "Expires: Never". |
| 4 | Open Manage; set Expiry "Set a date" and pick the 20th of next month; Save. | "Expires: <20th of next month>, 11:59 PM" (local). |
| 5 | Open Manage; clear Max downloads (blank) and Save; then set it back to `5` and Save. | First "Downloads: 0" with no cap shown, then "Downloads: 0 / 5 (5 left)". |
| 6 | Open Manage; set Expiry "1 day"; Save. | "Expires: <exactly 24 hours from now>" (shortening works the same way as extending). |
| 7 | Open Manage; set Expiry "30 days"; Save. Then set it back to "Set a date", the 20th of next month, and Save (FT-20 expects that date). | First "Expires: <30 days from now>", then the 20th of next month, 11:59 PM again. |
| 8 | Open Manage; type `0` in Max downloads and click **Save**; if the browser's bubble blocks it, run `document.querySelector('#transfer-row-<TRANSFER_A_ID> form[hx-post$="/settings/"]').noValidate = true` and Save again. | Row A re-renders with the red message "Ensure this value is greater than or equal to 1."; the row still reads "Downloads: 0 / 5 (5 left)" and the expiry is unchanged. |

### FT-19 Set and remove the download password

**Purpose:** a password can be added and removed from the dashboard and gates the public page.
**Preconditions:** FT-18; visitor window logged out.

| # | Action | Expected result |
|---|---|---|
| 1 | Main window: open Manage on row A, type `Row-Pass-7` in the password field, Save. Open Manage again. | The label reads "Password (set; leave blank to keep it unchanged)". |
| 2 | Visitor window: open `/t/<SLUG_A>/`. | The file list is visible with a **Locked** badge on each file instead of Download, no "Download all (.zip)" button, and the text "This transfer is password protected. Enter the password to download." with a password input and **Unlock**. Don't unlock. |
| 3 | Main window: open Manage, leave the password blank, change Max downloads to `6`, Save; open Manage again. | Still "Password (set; ...)" (blank kept it), "Downloads: 0 / 6 (6 left)". Set Max downloads back to `5` and Save. |
| 4 | Main window: open Manage, tick **Remove password**, Save; open Manage. | "Password (none; leave blank to keep it unchanged)". |
| 5 | Visitor window: reload `/t/<SLUG_A>/`. | Each file has a **Download** button and there is a **Download all (.zip)** button; no password form. |

## Public download page

### FT-20 Download page and a single-file download

**Purpose:** the public page shows the transfer and serves a file, and the sender is notified.
**Preconditions:** FT-19; visitor window logged out.

| # | Action | Expected result |
|---|---|---|
| 1 | Visitor window: `/t/<SLUG_A>/`. | Title "qa-report.txt - ZipTrigo". Heading **qa-report.txt**, the message on two lines ("Hello from QA." / "Second line."), "Available until <20th of next month>, 11:59 PM" (local time). |
| 2 | Read the file list. | Sorted by name: `qa-notes.txt` "1.5 KB" and `qa-report.txt` "2.9 KB", each with **Download**. Below: **Download all (.zip)** and, under a divider, a small "Report this transfer" link. |
| 3 | Click **Download** for `qa-report.txt`. | The browser saves a file named `qa-report.txt` (3,000 bytes, 2,999 `R` characters and a newline). In the network log the request went to `/t/<SLUG_A>/files/<id>/` and was redirected (302) to a presigned URL on `http://localhost:4566/` (the bucket). Run `ftFetch('<that presigned URL>')` (see Test data; it doesn't count a download): status 200, `bytes` 3000, `sha256` `d47d8e2f...20d5e6` as in Test data. |
| 4 | Open `/__e2e__/mail/?to=qa.user@example.com`. | Newest email subject `"qa-report.txt" was downloaded`, body `Someone just downloaded "qa-report.txt" from your transfer "qa-report.txt".` and "Manage this transfer: http://localhost:8765/transfer/". Click that link in the main window: the dashboard opens. |

### FT-21 Download all as a zip

**Purpose:** "Download all" builds the zip, then serves it as one download.
**Preconditions:** FT-20.

| # | Action | Expected result |
|---|---|---|
| 1 | Visitor window: click **Download all (.zip)**. | The button is replaced by "Preparing the zip..." with a spinner, which polls every 2 s and turns into a **Download all (.zip)** link. (On this server the zip is built during the first request, so the link may appear at once.) No "Could not prepare the zip." message. |
| 2 | Click the **Download all (.zip)** link. | A file `all.zip` downloads (from a presigned URL on the bucket). If you can inspect it (`unzip -l`, `python3 -m zipfile -l`): it contains exactly `qa-notes.txt` (1,500 bytes) and `qa-report.txt` (3,000 bytes), stored uncompressed, with their original content and today's date. Otherwise run FT-40 step 6's name-check snippet on the presigned zip URL from the network log, with the names `qa-notes.txt` and `qa-report.txt`: a little over 4,500 bytes and both names `true`. |
| 3 | Open `/__e2e__/mail/?to=qa.user@example.com`. | Newest email subject `"all files" was downloaded`. |

### FT-22 Download log on the dashboard

**Purpose:** downloads are counted and listed on the sender's dashboard.
**Preconditions:** FT-21 (exactly two downloads of A so far).

| # | Action | Expected result |
|---|---|---|
| 1 | Main window: reload `/transfer/`. | Row A: "Downloads: 2 / 5 (3 left)". |
| 2 | Open Manage on row A. | A **Download log** section lists two entries, newest first: "All files (zip)" then "qa-report.txt", each with a `YYYY-MM-DD HH:MM` time and the IP (`127.0.0.1` or `::1`). |

### FT-23 Password gate

**Purpose:** a protected transfer refuses a wrong password, accepts the right one, and the direct
file URL is gated too.
**Preconditions:** FT-09 (`SLUG_B`, password `Open-Sesame-42`); visitor window logged out.

| # | Action | Expected result |
|---|---|---|
| 1 | Visitor window: `/t/<SLUG_B>/`. | Heading **qa-limit.txt**, "Available until <15th of next month>, 11:59 PM" (local), the file `qa-limit.txt` "500 bytes" with **Locked**, the password prompt and **Unlock**. No "Download all" (single file). |
| 2 | Enter `wrong-password` and click **Unlock**. | The page re-renders (URL `/t/<SLUG_B>/unlock/`) with "Incorrect password." under the input; files still **Locked**. |
| 3 | Enter `Open-Sesame-42` and click **Unlock**. | Redirected to `/t/<SLUG_B>/`; the file now has a **Download** button; no password form. |
| 4 | Copy the Download link's `href` (do **not** click it). Open that href in a third clean context (or clear the visitor window's cookies and open it there). | Redirected to `/t/<SLUG_B>/` with the file **Locked** -- the direct URL doesn't bypass the password, and nothing downloads. If you cleared the visitor window's cookies, unlock B again with `Open-Sesame-42` before FT-24. |
| 5 | In that clean (locked) context, click **Report this transfer**. | The report form opens without asking for the password (reporting works for anyone with the link). Don't submit it. |

### FT-24 Max downloads reached: the neutral page

**Purpose:** the download that reaches the limit works, and the link is unavailable afterwards.
**Preconditions:** FT-23 (B unlocked in the visitor window, 0 downloads so far).

| # | Action | Expected result |
|---|---|---|
| 1 | Visitor window: on `/t/<SLUG_B>/` (unlocked), click **Download**. | `qa-limit.txt` downloads (500 bytes). Record the presigned URL it was redirected to (network log) as `URL_B`. |
| 2 | Define `ftFetch` (Test data) and run `ftFetch('<URL_B>')`. | 200, 500 bytes: the files of a transfer that just reached its limit stay in the bucket for a while (deferred deletion, so this very download can finish); FT-41 checks that they go later. |
| 3 | Reload `/t/<SLUG_B>/`. | Title "Transfer unavailable - ZipTrigo": heading **This transfer is no longer available** and "The link may have expired, reached its download limit, or been removed." It does not say which. |
| 4 | Main window: `/transfer/`, then the **Ended** tab. | B is no longer under Active. Under Ended: "1 file · sent ... · EXPIRED", "Ended: <date and time>" (not "Expires:"), "Downloads: 1 / 1 (0 left)", and **no buttons at all** (no Copy link, Disable, Manage or Delete: an ended transfer has no working link). |
| 5 | Open `/__e2e__/mail/?to=qa.user@example.com`. | An email `"qa-limit.txt" was downloaded`. |

### FT-25 Disable and re-enable

**Purpose:** disabling makes the link unavailable; re-enabling restores it.
**Preconditions:** main window on `/transfer/` (Active tab); visitor window logged out.

| # | Action | Expected result |
|---|---|---|
| 1 | Main window: click **Disable** on row A. | Row A re-renders in place: status **DISABLED**, a **Re-enable** button instead of Disable, **Manage** and **Delete** still there. Reloading keeps A in the Active tab. |
| 2 | Visitor window: open `/t/<SLUG_A>/`. | The same neutral "This transfer is no longer available" page as in FT-24. |
| 3 | Main window: click **Re-enable** on row A. | Row A shows **ACTIVE** again with **Disable**; no error. |
| 4 | Visitor window: reload `/t/<SLUG_A>/`. | The download page with both files is back. |

### FT-26 Delete now

**Purpose:** delete now asks for confirmation, ends the transfer and removes the link.
**Preconditions:** main window on `/transfer/`; visitor window logged out; `SLUG_C` from FT-10.

| # | Action | Expected result |
|---|---|---|
| 1 | Visitor window: open `/t/<SLUG_C>/`, define `ftFetch` and run `ftFetch(document.querySelector('a[href*="/files/"]').href)` (one download of C). | 200, 1,200 bytes, `text` is 1,199 `P`s and a newline (the resumed upload of FT-10 stored the right bytes). Record the returned `url` as `URL_C`. |
| 2 | Main window: click **Delete** on row C (`qa-paused.txt`). | A browser confirm dialog: "Delete this transfer now? Its files will be removed immediately." Click **Cancel**: nothing changes. |
| 3 | Click **Delete** again and confirm with **OK**. | Row C re-renders in place with status **DELETED**, "Ended: <date and time>" and no buttons. |
| 4 | Reload `/transfer/`; open **Ended**. | C is gone from Active and listed under Ended as **DELETED** next to B (**EXPIRED**). |
| 5 | Visitor window: open `/t/<SLUG_C>/`. | The neutral "This transfer is no longer available" page. |
| 6 | Open `/__e2e__/mail/?to=qa.user@example.com`. | No email about C's files being deleted (`Files for "qa-paused.txt" have been deleted` must **not** appear: delete-now doesn't notify). There is a `"qa-paused.txt" was downloaded` from step 1. |
| 7 | Open `URL_C` in a new tab (it is still within its 15 minutes). | The bucket answers an S3 error (`NoSuchKey`, HTTP 404) instead of the file: delete now really removed the object. (If more than 15 minutes passed the URL itself has expired -- `AccessDenied`/403 -- which proves nothing; note it.) |

### FT-27 Unknown slug and the neutral page

**Purpose:** every unavailable case looks identical and answers 404.
**Preconditions:** visitor window; FT-24 and FT-26 done.

| # | Action | Expected result |
|---|---|---|
| 1 | Open `/t/AAAAAAAAAAAAAAAAAAAAAA/`. | The neutral "This transfer is no longer available" page. |
| 2 | In the console run `Promise.all(['/t/AAAAAAAAAAAAAAAAAAAAAA/', '/t/<SLUG_B>/', '/t/<SLUG_C>/'].map(u => fetch(u).then(r => r.status)))`. | `[404, 404, 404]`. |
| 3 | Compare the three pages visually (unknown, expired B, deleted C). | Identical heading and text; nothing reveals why. |

### FT-28 Report this transfer: validation and success

**Purpose:** anyone with the link can report a transfer; bad input is refused.
**Preconditions:** visitor window logged out; A is active and unlocked (FT-25).

| # | Action | Expected result |
|---|---|---|
| 1 | On `/t/<SLUG_A>/` click **Report this transfer**. | A form expands: **Reason** select (Malware, Copyright infringement, Illegal content, Phishing or spam, Other; "Malware" preselected), **Details (optional)** textarea, **Your email (optional)** with "In case we need to follow up. We won't share it with the sender.", and **Submit report**. No password is asked for. |
| 2 | Type `not-an-email` in Your email and click **Submit report**. | The browser's own email-format bubble blocks the submit. |
| 3 | Run `document.querySelector('#report-box form').noValidate = true`, then click **Submit report** again. | The form re-renders with "Enter a valid email address." under Your email. |
| 4 | Clear the email. Run `document.querySelector('#report-box select[name=reason]').add(new Option('Bogus', 'bogus', true, true))`, then **Submit report**. | "Select a valid choice. bogus is not one of the available choices." under Reason. |
| 5 | Reload the page, open the form, choose "Phishing or spam", Details `QA test report, please ignore.`, email `qa.reporter@example.com`, **Submit report**. | The form is replaced by a green "Thank you, your report has been submitted." |
| 6 | Reload `/t/<SLUG_A>/`. | The download page still works (a report alone doesn't hide a transfer). |
| 7 | Main window: reload `/transfer/`. | Row A shows no hold / takedown message and nothing about the reporter. |

### FT-29 A repeat report within 10 minutes is refused

**Purpose:** the same visitor can't report the same transfer twice in a row.
**Preconditions:** FT-28 step 5 less than 10 minutes ago.

| # | Action | Expected result |
|---|---|---|
| 1 | Visitor window: on `/t/<SLUG_A>/` open the report form, choose "Other", **Submit report**. | A red box at the top of the form: "You already reported this transfer recently. Thank you." No green confirmation. |

## Anonymous sending

### FT-30 Anonymous sending is off by default

**Purpose:** with the default settings the anonymous page is unavailable, and a logged-in visitor
is sent to the normal page.
**Preconditions:** fresh-database default settings; visitor window logged out.

| # | Action | Expected result |
|---|---|---|
| 1 | Visitor window: open `/transfer/send/anon/`. | Title "Send files - ZipTrigo", heading **Sending without an account isn't available right now**, and "Log in to send files." where **Log in** links to `/account/login/?next=/transfer/send/`. `fetch('/transfer/send/anon/').then(r => r.status)` returns `404`. |
| 2 | Main window (logged in as qa.user): open `/transfer/send/anon/`. | Redirected to `/transfer/send/` (the normal send page). |

### FT-31 Switch anonymous sending on in the admin

**Purpose:** the superuser can enable anonymous sending.
**Preconditions:** main window.

| # | Action | Expected result |
|---|---|---|
| 1 | Main window: log out, then log in as `qa.admin@example.com` (site login, or `/admin/login/`). Open `/admin/file_transfer/filetransfersettings/`. | Redirected straight to the change form `/admin/file_transfer/filetransfersettings/1/change/` ("File transfer settings"). |
| 2 | Check the anonymous fields. | **Anonymous enabled** is unchecked; anonymous max files `20`, max recipients `10`, max transfers per IP per day `5`, and allowed expiry days `1,5,15,30`. |
| 3 | Tick **Anonymous enabled** and click **Save**. | A success message saying the file transfer settings were changed successfully; reopening the page shows the box ticked. |

### FT-32 Anonymous send page and validation

**Purpose:** the anonymous page has its own limits and fields and validates like the logged-in one.
**Preconditions:** FT-31; visitor window logged out.

| # | Action | Expected result |
|---|---|---|
| 1 | Visitor window: open `/transfer/send/anon/`. | Heading **Send files**; "Sending without an account is free, within limits: up to 20 files, 5.0 GB total, 10 recipients. Log in for higher limits and a dashboard." (**Log in** -> `/account/login/?next=/transfer/send/`). The same drop zone, **Choose files** and **Add folder** buttons. |
| 2 | Read the form. | **Your email** (help "We'll email you a code to confirm before anything is sent."), **Recipients** with the same help text, **Message**, **Expiry** with only "1 day", "5 days", "15 days", "30 days" (no "Set a date", no "No expiration"), **Max downloads**, "Password (optional, cannot be changed later)". There is **no** "Email me when a file is downloaded" checkbox. **Send** is disabled. |
| 3 | Add `qa-anon.txt` (800 bytes). | Row "qa-anon.txt (800 bytes)" reaches the green check; **Send** is enabled; the URL does **not** gain `?resume=`. |
| 4 | Reload the page. | The same draft comes back: the `qa-anon.txt` row with a green check. |
| 5 | Your email `qa.anon.sender@example.com`; Recipients: 11 addresses (`Array.from({length: 11}, (_, i) => 'qa.anon' + (i + 1) + '@example.com').join('\n')`); **Send**. | Red box: "A transfer can have at most 10 recipients." |
| 6 | Recipients `qa.anon.recipient@example.com, oops`; **Send**. | Red box: `Recipients: "oops" is not a valid email address.` |
| 7 | Change Your email to `not-an-email` and click **Send**. | The browser's email-format bubble blocks the submit. Set it back to `qa.anon.sender@example.com`. |

### FT-33 Anonymous confirmation by code

**Purpose:** nothing is sent until the sender's email is confirmed; the code works, a wrong code
and an early resend are refused.
**Preconditions:** FT-32 (visitor window, `qa-anon.txt` uploaded).

| # | Action | Expected result |
|---|---|---|
| 1 | Recipients `qa.anon.recipient@example.com`, Expiry "5 days", click **Send**. | The page reloads to `/transfer/send/anon/` (title "Confirm your transfer - ZipTrigo") showing **Confirm your email**: "We've sent a code and a confirmation link to qa.anon.sender@example.com. Enter the code below, or click the link in the email -- either confirms the transfer." A box with "Enter the 6-digit code we emailed to qa.anon.sender@example.com", a code input, **Confirm** and **Resend code**. |
| 2 | Open `/__e2e__/mail/?to=qa.anon.recipient@example.com`. | "No emails." (nothing sent before confirmation). |
| 3 | Open `/__e2e__/mail/?to=qa.anon.sender@example.com`. | Subject "Confirm your ZipTrigo file transfer". Body: "Someone (hopefully you) is sending files through ZipTrigo.", "Enter this code on the page you were sending from: NNNNNN" (6 digits), "Or click this link to confirm instead: http://localhost:8765/transfer/send/anon/<uuid>/confirm/link/<token>/" and "This code expires in 30 minutes. ...". The HTML body (source) has the code in `<strong>` and the link as an `<a href>` to the same URL. Note the code. |
| 4 | Back on the confirm page click **Resend code** right away. | A red message in the box: "Please wait Ns before requesting another code." (N up to 60). The mailbox still has only one confirmation email. |
| 5 | Enter a wrong 6-digit code (e.g. `000000`, or `111111` if that is the real one) and click **Confirm**. | Under the input: "Incorrect code (4 attempt(s) remaining)." Still on the confirm page. |
| 6 | Enter the real code and click **Confirm**. | Navigates to `/transfer/sent/anon/<uuid>/`: heading **Your transfer is on its way**, "We emailed the recipients and sent you a copy, with a link to manage this transfer. Here's the download link too:", the link `http://localhost:8765/t/<slug>/` with **Copy** (the label turns into **Copied** for two seconds after a click), and "Create an account and this transfer -- and any others sent from qa.anon.sender@example.com -- will show up in your dashboard next time you log in." (**Create an account** -> `/account/register/`). Record the slug as `ANON_SLUG_1`. |
| 7 | Mailbox `?to=qa.anon.recipient@example.com`. | One email `qa-anon.txt was sent to you via ZipTrigo` with "This link expires on ..." about 5 days ahead. |
| 8 | Mailbox `?to=qa.anon.sender@example.com`. | Newest email `Your transfer "qa-anon.txt" is on its way`: sent to `qa.anon.recipient@example.com`, the download link, and "Manage this transfer (disable it, or see how many times it was downloaded) -- keep this link, it's the only way to manage this transfer: http://localhost:8765/t/<ANON_SLUG_1>/manage/<token>/". Record that URL as `ANON_MANAGE_URL`. |
| 9 | Visitor window: open `/transfer/send/anon/` again. | Redirected to the same `/transfer/sent/anon/<uuid>/` page (this session's transfer is active; a new anonymous send isn't offered until it is disabled or ends -- note this as a UX observation). |
| 10 | Main window (logged in, a different session): open the visitor's `/transfer/sent/anon/<uuid>/` URL. | 404 -- only the sending (or confirming) browser may see this page. |

### FT-34 Anonymous manage link

**Purpose:** the emailed manage link shows the transfer and can disable it; a wrong token can't.
**Preconditions:** FT-33.

| # | Action | Expected result |
|---|---|---|
| 1 | Visitor window: open `ANON_MANAGE_URL` with the last character of the token changed. | The neutral "This transfer is no longer available" page. |
| 2 | Open the real `ANON_MANAGE_URL`. | Title "Manage your transfer - ZipTrigo", heading **qa-anon.txt**, "Status: ACTIVE", "Downloaded 0 times." (no stray space before the full stop), "Expires: <5 days ahead, local time>.", and a red **Disable this transfer** button. |
| 3 | Click **Disable this transfer**. | A confirmation dialog opens (the shared modal, not a browser dialog): title "Disable this transfer?", text "The download link stops working for everyone, and it can't be turned back on.", buttons **Cancel** and **Disable**. Press **Cancel** (or Escape): nothing changes and the status stays ACTIVE. Click **Disable this transfer** again and **Disable**. Back on the same manage URL: "Status: DISABLED", and "This transfer is disabled -- the link no longer works." instead of the button. |
| 4 | Open `/t/<ANON_SLUG_1>/`. | The neutral "This transfer is no longer available" page. |
| 5 | Open `/transfer/send/anon/`. | A fresh, empty anonymous send page (the disabled transfer no longer holds the session). |

### FT-35 Anonymous confirmation by link, from another browser

**Purpose:** the emailed link only shows a button on GET, confirms on POST, works from a different
browser session, and a bad token is refused.
**Preconditions:** FT-34 (visitor window on an empty anonymous send page); main window logged in
(as qa.admin after FT-31 -- that's fine, it just needs to be a different session).

| # | Action | Expected result |
|---|---|---|
| 1 | Visitor window: add `qa-anon-link.txt`; Your email `qa.user@example.com`; Recipients `qa.anon.recipient2@example.com`; Expiry "1 day"; **Send**. | The **Confirm your email** page for `qa.user@example.com`. |
| 2 | Open `/__e2e__/mail/?to=qa.user@example.com` and copy the link from the newest "Confirm your ZipTrigo file transfer" email. | -- |
| 3 | Main window: open the link with its token's last character changed. | Title "Confirm your transfer - ZipTrigo", heading **Confirm your transfer**, "Press the button below to confirm this transfer was sent by you. Nothing is sent to your recipients until you do." and a **Confirm this transfer** button. (A GET never checks or uses the token.) |
| 4 | Click **Confirm this transfer**. | The page shows a red box "This confirmation link is invalid or has expired." and no button. Mailbox `?to=qa.anon.recipient2@example.com` is still empty. |
| 5 | Main window: open the real link. | The same **Confirm your transfer** page with the button. Mailbox `?to=qa.anon.recipient2@example.com` is still empty (opening the link alone confirms nothing). |
| 6 | Click **Confirm this transfer**. | Redirected to `/transfer/sent/anon/<uuid>/` ("Your transfer is on its way") in the main window. |
| 7 | Mailbox `?to=qa.anon.recipient2@example.com`. | One email `qa-anon-link.txt was sent to you via ZipTrigo`. |
| 8 | Visitor window: reload `/transfer/send/anon/`. | Redirected to the same anonymous sent page (the confirm-code page is gone). |
| 9 | Main window: open the real confirmation link again. | Redirected straight to the sent page (already confirmed); no second confirmation, no second recipient email. |

### FT-36 Logged-in visitors and claim on login

**Purpose:** a logged-in user never gets the free anonymous page, and an anonymous transfer sent
from a confirmed account's address joins that account's dashboard at login.
**Preconditions:** FT-35 (anonymous transfer from `qa.user@example.com`).

| # | Action | Expected result |
|---|---|---|
| 1 | Main window: log out, then log in as `qa.user@example.com`. | Logged in. |
| 2 | Open `/transfer/send/anon/`. | Redirected to `/transfer/send/` (anonymous sending is on, but not for a logged-in user). |
| 3 | Open `/transfer/`. | A new row `qa-anon-link.txt`: "1 file · sent ... · ACTIVE", "Expires: <1 day ahead, local time> · Downloads: 0 · Credits charged: 0", with Copy link / Disable / Manage / Delete. |
| 4 | Open Manage on that row. | Recipients: `qa.anon.recipient2@example.com` with Resend. |

### FT-37 Resend after the cooldown

**Purpose:** a resend works once 10 minutes have passed, and a second one is refused again.
**Preconditions:** main window as qa.user; at least **10 minutes** since Transfer A was sent
(FT-07) -- wait if needed (compare with the time in row A's "sent" line).

| # | Action | Expected result |
|---|---|---|
| 1 | On `/transfer/` open Manage on row A and click **Resend** next to `qa.recipient1@example.com`. | Row A re-renders with a green status line at its top (`role="status"`): "Sent the email to qa.recipient1@example.com again." and no error. |
| 2 | Mailbox `?to=qa.recipient1@example.com`. | A new email `qa-report.txt and 1 more file were sent to you via ZipTrigo` on top, with the same A link. |
| 3 | Open Manage again and click **Resend** for `qa.recipient1@example.com` once more. | Red message: "This recipient was emailed recently; you can resend in 10 minutes." No further email. |

### FT-38 A network error during an upload is shown on the file's row

**Purpose:** a dropped connection no longer opens a blocking alert; the row says what happened and
offers to resume.
**Preconditions:** the main window as `qa.user`, on a fresh `/transfer/send/`. Run the "Break the
upload of one part" snippet (Test data) with `window.__ftBlock = /partNumber=/` so every part PUT
to the bucket fails (mark the case BLOCKED if the tool cannot run JavaScript or block requests).

| # | Action | Expected result |
|---|---|---|
| 1 | Add `qa-net.txt` (3,000 bytes, with the `lastModified` from Test data) through the file input. | No browser dialog at any point. The row's status text becomes **Paused** (not a percentage) and under the row a red line with `role="alert"` reads "Upload interrupted by a network error. Select the same file again to resume." followed by an underlined button **Choose the file**. The row wraps so the line sits on its own line below the name and bar. |
| 2 | Click **Choose the file**. | The file picker opens (the same one as **Choose files**). Cancel it. |
| 3 | Restore the network: `window.fetch = window.__ftFetch;`. Add `qa-net.txt` again with the same size and `lastModified`. | The existing row is reused (no second row): the red line disappears, the progress runs and the row ends with the green check. |
| 4 | Break the network again, add a different file `qa-net2.txt` (800 bytes), then restore it. | Its row shows the same interrupted line; the first row is unaffected. Re-adding `qa-net2.txt` finishes it. |
| 5 | Remove both rows with their trash icons. | The list is empty and **Send** is disabled. |

## Large files on the real bucket

### FT-39 A 70 MiB file in two parts, interrupted after part 1 and resumed after a reload

**Purpose:** a multipart upload of several parts goes to the real bucket with a checksum per part;
after a reload, re-selecting the file uploads only the part that is missing, and the checksum of
the part already there round-trips into the completion.
**Preconditions:** main window as `qa.user`; open `/transfer/send/` without `?resume=` (an empty
draft). The browser must be able to hold a 70 MiB file in memory (mark BLOCKED otherwise).

| # | Action | Expected result |
|---|---|---|
| 1 | Run the "Break the upload of one part" snippet as it is (part 2 only), then "The large file" snippet. | A row "qa-big.bin (70.0 MB)" at 0% while the page hashes both parts (a few seconds). Network: `POST .../files/` 201 with `part_size_bytes` 67108864 and `part_count` 2; one `POST .../files/<id>/parts/` whose body lists parts 1 and 2 with a `checksum_sha256` each, answering two URLs; a `PUT` of 67,108,864 bytes to the bucket's `...partNumber=1...` URL, 200; the row reaches **50%**. Then the row turns **Paused** with the red line "Upload interrupted by a network error. Select the same file again to resume." and **Choose the file**. The URL now has `?resume=<draft id>`; **Send** is disabled. |
| 2 | Reload the page. | The URL keeps `?resume=`. One row "qa-big.bin (70.0 MB)" with **Paused** (tooltip "Select the same file again to resume uploading it."); **Send** disabled with its hint. The reload also dropped the fetch override. |
| 3 | Run "The large file" snippet again (same name, size, content and `lastModified`). | No second row. Network: `POST .../files/<id>/resume/` 200 with `restarted: false`, `part_count: 2` and `uploaded_parts` holding exactly part 1 (`size` 67108864, an `etag`, a non-empty `checksum_sha256`); the row starts at 50%; exactly **one** `PUT`, to `...partNumber=2...` (6,291,456 bytes), 200; then `POST .../complete/` 200 whose body lists both parts with their ETags and checksums. The row ends with the green check; **Send** is enabled. No console or CORS errors. |
| 4 | Add three files in one run of the add snippet: `qa-dup.txt` (10 bytes), `qa-dup.txt` (20 bytes) and `relatório café.txt` (`'Olá\n'`), see Test data. | Three more rows, all reaching the green check: "qa-dup.txt (10 bytes)", "qa-dup.txt (20 bytes)" (two files of the same name are allowed) and "relatório café.txt (5 bytes)", the name shown with its accents. |
| 5 | Recipients `qa.recipient3@example.com`; Expiry "No expiration"; **uncheck** "Email me when a file is downloaded"; **Send**. | The sent page. Record the slug as `SLUG_BIG`. |
| 6 | Mailbox `?to=qa.recipient3@example.com`. | Newest: `qa-big.bin and 3 more files were sent to you via ZipTrigo`; body "You have received 4 files:" with "- qa-big.bin", "- qa-dup.txt", "- qa-dup.txt", "- relatório café.txt" (`qa-big.bin` first, the other three in the order the server received them; accents intact), then "Download: http://localhost:8765/t/<SLUG_BIG>/" and **no** "This link expires" line. |

### FT-40 Download the large transfer: bytes, names, and a zip of several parts

**Purpose:** a file assembled from two parts downloads byte for byte, a non-ASCII name survives
the presigned URL, and "Download all" streams every file from the bucket into a zip that is
itself written in parts.
**Preconditions:** FT-39; visitor window logged out.

| # | Action | Expected result |
|---|---|---|
| 1 | Visitor window: `/t/<SLUG_BIG>/`. | Heading **qa-big.bin**, "No expiration", the files sorted by name: `qa-big.bin` "70.0 MB", `qa-dup.txt` "10 bytes" and `qa-dup.txt` "20 bytes" (either order), `relatório café.txt` "5 bytes", each with **Download**, and **Download all (.zip)**. |
| 2 | Define `ftFetch` and run `ftFetch(<the href of qa-big.bin's Download link>)` (counts one download). | `status` 200, `url` a presigned URL on `http://localhost:4566/`, `bytes` 73400320 and `sha256` `24f1c89f87a1ed78b333d3560219a9faa42c74da56497d5eb9275dbcb0eed34a`: the two parts were joined in order. |
| 3 | Click **Download** for `relatório café.txt`. | The browser saves a file named exactly `relatório café.txt` (accents and space kept: not `relat_rio`, not percent-encoded), containing `Olá` and a newline (5 bytes). |
| 4 | Open `/t/<SLUG_BIG>/zip/` directly (before asking for the zip). | Redirected back to `/t/<SLUG_BIG>/`; nothing downloads (no zip yet, so no download is counted). |
| 5 | Click **Download all (.zip)**. | "Preparing the zip..." with a spinner (the request builds a ~70 MiB zip from the bucket: a few seconds), then a **Download all (.zip)** link. No "Could not prepare the zip." |
| 6 | Click the link. | A file named `all.zip` downloads, a little over 73,400,355 bytes (the four files stored uncompressed, 73,400,320 + 10 + 20 + 5, plus headers; more than one 64 MiB part, so the zip was written to the bucket in two parts). If you can inspect it (`unzip -l`, `python3 -m zipfile -l`): exactly the entries `qa-big.bin` (SHA-256 as in step 2), `qa-dup.txt`, `qa-dup (1).txt` (the second file of the same name, renamed; one holds the 10-byte content and the other the 20-byte one) and `relatório café.txt`, dated today. Otherwise run `(async (url) => { const b = new Uint8Array(await (await fetch(url)).arrayBuffer()); const tail = new TextDecoder().decode(b.slice(b.length - 4096)); return { bytes: b.length, names: ['qa-big.bin', 'qa-dup.txt', 'qa-dup (1).txt', 'relatório café.txt'].map((n) => [n, tail.includes(n)]) }; })('<the presigned zip URL from the network log>')`: the size as above and all four names `true`. |
| 7 | Main window: `/transfer/`; open **Manage** on the `qa-big.bin` row. | "4 files · sent ... · ACTIVE", "Expires: Never · Downloads: 3 · Credits charged: 0". Download log, newest first: "All files (zip)", "relatório café.txt", "qa-big.bin". |
| 8 | Mailbox `?to=qa.user@example.com` (wait a minute). | No `"qa-big.bin" was downloaded`, `"relatório café.txt" was downloaded` or `"all files" was downloaded` email: notifications were switched off for this transfer. |

## Scheduled jobs (shell steps)

### FT-41 Expiry reminder, natural expiry, and deleting the files after the last download

**Purpose:** the five-minute expiry job sends the "expires tomorrow" reminder once, ends an expired
transfer with the "files deleted" email, and removes the objects of a transfer that reached its
download limit once that download's URL has had time to expire.
**Preconditions:** main window as `qa.user`; FT-24 (B ended by its one download, `URL_B`); a shell.

| # | Action | Expected result |
|---|---|---|
| 1 | Main window: send transfer E: `qa-expiring.txt`, Recipients `qa.recipient2@example.com`, Expiry "5 days". | The sent page. Record the slug as `SLUG_E`. On `/transfer/` row E reads "Expires: <5 days from now>". |
| 2 | (shell step) Make E due for its reminder (sent 4 days ago, expiring in 12 hours) and B's deferred deletion due (ended 16 minutes ago), then run the job: `python tests_e2e/ai/manage.py shell -c "from datetime import timedelta; from django.utils import timezone; from apps.file_transfer.models import Transfer; from apps.file_transfer.jobs import expire_transfers; now = timezone.now(); print(Transfer.objects.filter(slug='<SLUG_E>').update(completed_at=now - timedelta(days=4), expires_at=now + timedelta(hours=12))); print(Transfer.objects.filter(slug='<SLUG_B>').update(ended_at=now - timedelta(minutes=16))); expire_transfers()"` | Prints `1` and `1`, no traceback. |
| 3 | Mailbox `?to=qa.user@example.com`. | Two new emails. `Your transfer "qa-expiring.txt" expires tomorrow`, body `Your transfer "qa-expiring.txt" expires tomorrow (YYYY-MM-DD HH:MM UTC).` (about 12 hours from now, in UTC) and "Manage this transfer: http://localhost:8765/transfer/". And `Files for "qa-limit.txt" have been deleted`, body `The files for your transfer "qa-limit.txt" have been deleted and are no longer available.` (no link). |
| 4 | Click the reminder's "Manage this transfer" link. | `/transfer/`: row E is still **ACTIVE**, "Expires: <about 12 hours from now>". |
| 5 | Open `URL_B` in a new tab. | An S3 `NoSuchKey` error (404) instead of `qa-limit.txt`: the deferred deletion removed the object. (`AccessDenied`/403 means the URL itself expired: inconclusive, note it.) |
| 6 | (shell step) Run the job again after moving E's expiry into the past: `python tests_e2e/ai/manage.py shell -c "from datetime import timedelta; from django.utils import timezone; from apps.file_transfer.models import Transfer; from apps.file_transfer.jobs import expire_transfers; print(Transfer.objects.filter(slug='<SLUG_E>').update(expires_at=timezone.now() - timedelta(minutes=1))); expire_transfers()"` | Prints `1`. |
| 7 | Mailbox `?to=qa.user@example.com` (wait a minute). | One new email, `Files for "qa-expiring.txt" have been deleted` (same body shape as step 3). No second "expires tomorrow" for E and no second `Files for "qa-limit.txt" ...`: each is sent once. |
| 8 | Main window: `/transfer/`, then **Ended**. | E is no longer under Active. Under Ended: `qa-expiring.txt` "**EXPIRED**", "Ended: <just now>", no buttons. |
| 9 | Visitor window: `/t/<SLUG_E>/`. | The neutral "This transfer is no longer available" page (404). |

## Credits: metering and suspension

### FT-42 Set-up: a fresh account with a transfer

**Purpose:** an account whose balance the billing cases can change without touching the seeded
ones.
**Preconditions:** main window; log out of `qa.user`.

| # | Action | Expected result |
|---|---|---|
| 1 | Main window: register at `/account/register/` with Name `QA FT <rand>`, Email `qa.ft.<rand>@example.com` (record as `FT_USER`), Password `FtMeter-4821`. | "Check your email". |
| 2 | Mailbox `?to=<FT_USER>`: open the `Confirm your ZipTrigo account email` link and press **Confirm my email**. | "Email Confirmed!". |
| 3 | Log in as `FT_USER`; open `/billing/credits/`. | "Current balance: 1000 credits" (the welcome credits, line "A gift for your new account"). |
| 4 | Send transfer M from `/transfer/send/`: `qa-meter.txt`, Recipients `qa.recipient1@example.com`, Expiry "No expiration", notifications left on. | The sent page. Record the slug as `SLUG_M`. On `/transfer/` row M: "ACTIVE ... Expires: Never · Downloads: 0 · Credits charged: 0". |

### FT-43 Daily metering charges whole credits (shell step)

**Purpose:** the daily job charges a transfer's storage once per 24 hours, in whole credits, with a
ledger line, and a second run the same day charges nothing.
**Preconditions:** FT-42 (main window as `FT_USER`); a shell.

| # | Action | Expected result |
|---|---|---|
| 1 | (shell step) Make M's day due with one whole credit accrued (3,000 bytes at the default 0.1 credit per GB per day would take millions of days to reach one), then run the job: `python tests_e2e/ai/manage.py shell -c "from datetime import timedelta; from decimal import Decimal; from django.utils import timezone; from apps.file_transfer.models import Transfer; from apps.file_transfer.jobs import meter_transfers; print(Transfer.objects.filter(slug='<SLUG_M>').update(last_billed_at=timezone.now() - timedelta(hours=25), accrued=Decimal('1'))); meter_transfers()"` | Prints `1`, no traceback. |
| 2 | Reload `/transfer/`. | Row M: still **ACTIVE**, "Credits charged: 1". |
| 3 | Open `/billing/credits/`. | "Current balance: 999 credits"; the history's newest line is `Transfer "qa-meter.txt", day 1`, -1. |
| 4 | (shell step) Run the job again without changing anything: `python tests_e2e/ai/manage.py shell -c "from apps.file_transfer.jobs import meter_transfers; meter_transfers()"`. Reload both pages. | Nothing changes: "Credits charged: 1", balance 999, no new ledger line (the next day isn't due for 24 hours). |

### FT-44 Out of credits: suspension, its email and notification, and a top-up re-enables it

**Purpose:** a transfer its owner can't pay for is suspended (link off, email, in-app notice), its
manual re-enable is refused below a day's cost, and a top-up re-enables it at once and removes the
notice.
**Preconditions:** FT-43. Visitor window: log in there as `qa.admin@example.com` (the public page
works the same for a signed-in visitor); a shell.

| # | Action | Expected result |
|---|---|---|
| 1 | Visitor window: `/admin/billing/credittransaction/adjust/`, User email `<FT_USER>`, Direction "Spend", Amount `999`; **Apply**. | "Adjusted credits for <FT_USER>. New balance: 0." |
| 2 | (shell step) The same command as FT-43 step 1. | Prints `1`, no traceback. |
| 3 | Main window: reload `/transfer/`. | Row M: status **SUSPENDED**, buttons **Copy link**, **Re-enable**, **Manage**, **Delete**; "Credits charged: 1". |
| 4 | Visitor window: `/t/<SLUG_M>/`. | The neutral "This transfer is no longer available" page. |
| 5 | Mailbox `?to=<FT_USER>`. | Subject `Your transfer "qa-meter.txt" was suspended (out of credits)`. Body: `Your transfer "qa-meter.txt" ran out of credits and has been suspended: the download link is no longer working.`, then `Top up your credits within 7 day(s) to automatically resume it, or its files will be deleted. You need a balance of at least 1 credit (one day of storage for this transfer); a smaller top-up will not resume it.`, then "Manage this transfer: http://localhost:8765/transfer/". Click that link: the dashboard opens with M suspended. |
| 6 | Main window: look at the header bell, then open `/notifications/`. | The bell counts a new notification. The page lists `Transfer suspended: qa-meter.txt` with the body "It ran out of credits and its download link no longer works. Add at least 1 credit (one day of storage for this transfer; a smaller top-up will not resume it) within 7 day(s) to resume it, or its files will be deleted." Clicking it opens `/transfer/`. |
| 7 | On `/transfer/` click **Re-enable** on row M. | The row re-renders with the red message "You need at least 1 credit to re-enable this transfer (one day of storage)."; still **SUSPENDED**. |
| 8 | Visitor window: adjust credits for `<FT_USER>`, Direction "Add", Amount `1`; **Apply**. | "Adjusted credits for <FT_USER>. New balance: 1." |
| 9 | Main window: reload `/transfer/`, then `/notifications/`. | Row M is **ACTIVE** with **Disable** (the top-up itself re-enabled it; no click needed). `Transfer suspended: qa-meter.txt` is no longer listed (the notice is removed when the transfer resumes). |
| 10 | Visitor window: reload `/t/<SLUG_M>/`. | The download page with `qa-meter.txt` is back. |

### FT-45 The suspension grace period deletes the files (shell step)

**Purpose:** a suspended transfer not topped up within the grace period is deleted with its
objects, and the owner is told.
**Preconditions:** FT-44 (M active, `FT_USER` holds 1 credit); visitor window (still as
`qa.admin`); a shell.

| # | Action | Expected result |
|---|---|---|
| 1 | Visitor window: on `/t/<SLUG_M>/` define `ftFetch` and run `ftFetch(document.querySelector('a[href*="/files/"]').href)` (one download). | 200, 3,000 bytes. Record the returned `url` as `URL_M`. |
| 2 | (shell step) Charge two credits (more than the 1 held, so M is suspended again), then age the suspension past the 7-day grace period and run the grace-period step: `python tests_e2e/ai/manage.py shell -c "from datetime import timedelta; from decimal import Decimal; from django.utils import timezone; from apps.file_transfer.models import Transfer; from apps.file_transfer.jobs import meter_transfers; from apps.file_transfer.services.metering import delete_files_past_grace_period; qs = Transfer.objects.filter(slug='<SLUG_M>'); qs.update(last_billed_at=timezone.now() - timedelta(hours=25), accrued=Decimal('2')); meter_transfers(); print(qs.get().status); qs.update(suspended_at=timezone.now() - timedelta(days=8)); print(delete_files_past_grace_period())"` | Prints `suspended`, then `1`. |
| 3 | Mailbox `?to=<FT_USER>`. | A `"qa-meter.txt" was downloaded` (step 1), a second `Your transfer "qa-meter.txt" was suspended (out of credits)`, and `Files for "qa-meter.txt" have been deleted` with the body `The files for your transfer "qa-meter.txt" have been deleted and are no longer available.` |
| 4 | Main window: `/transfer/`, then **Ended**; then `/billing/credits/`. | M is gone from Active; under Ended: **DELETED**, "Ended: <just now>", "Credits charged: 1", no buttons. Balance still 1 (the refused 2-credit charge spent nothing). |
| 5 | `/notifications/`. | A new `Transfer suspended: qa-meter.txt` from step 2 is listed and stays (a deleted transfer's notice is kept as a record). |
| 6 | Visitor window: open `URL_M` in a new tab, then `/t/<SLUG_M>/`. | `URL_M`: an S3 `NoSuchKey` error (404) -- the object is gone (403 `AccessDenied` if 15 minutes passed: inconclusive). `/t/<SLUG_M>/`: the neutral page. |

### FT-46 Re-enabling a disabled transfer needs a day of credits; a top-up doesn't do it for you

**Purpose:** re-enabling something the owner disabled is refused below a day's cost, and unlike a
suspension, a top-up leaves it disabled until the owner re-enables it.
**Preconditions:** FT-45 (`FT_USER` holds 1 credit); visitor window as `qa.admin`.

| # | Action | Expected result |
|---|---|---|
| 1 | Main window: send transfer M2: `qa-meter-2.txt`, Recipients `qa.recipient2@example.com`. | The sent page. Record `SLUG_M2`. |
| 2 | On `/transfer/` click **Disable** on row M2. | **DISABLED**, with **Re-enable**. |
| 3 | Visitor window: adjust credits for `<FT_USER>`, "Spend", `1`; **Apply**. | "... New balance: 0." |
| 4 | Main window: click **Re-enable** on M2. | Red message in the row: "You need at least 1 credit to re-enable this transfer (one day of storage)."; still **DISABLED**. |
| 5 | Open `/transfer/send/`. | "Your balance: 0 credits. Sending needs at least 1. Buy credits." |
| 6 | Visitor window: adjust credits for `<FT_USER>`, "Add", `1`; **Apply**. | "... New balance: 1." |
| 7 | Main window: reload `/transfer/`. | M2 is still **DISABLED** (a top-up re-enables only suspended transfers). Click **Re-enable**: **ACTIVE** with **Disable**, no error; `/t/<SLUG_M2>/` works again in the visitor window. |
| 8 | Log out of both windows. Main window: log in as `qa.user@example.com`. | Logged in. |

## Anonymous sending: more

### FT-47 Resending the code; superseded code and link; too many wrong codes

**Purpose:** "Resend code" sends a new code and link that replace the old ones, and the code box
stops accepting guesses after five wrong ones.
**Preconditions:** anonymous sending on (FT-31); a **clean** visitor context (the old one holds an
active anonymous transfer and would be redirected to its sent page).

| # | Action | Expected result |
|---|---|---|
| 1 | Clean visitor context: `/transfer/send/anon/`; add `qa-anon-3.txt`; Your email `qa.anon.sender@example.com`; Recipients `qa.anon.recipient3@example.com`; Expiry "15 days"; **Send**. | **Confirm your email** for `qa.anon.sender@example.com`. |
| 2 | Mailbox `?to=qa.anon.sender@example.com`. | A new "Confirm your ZipTrigo file transfer". Note its code as `CODE1` and its link as `LINK1`. |
| 3 | Wait until 60 seconds have passed since step 1, then click **Resend code**. | The box re-renders with no red message. (Nothing on the page says a new code was sent: note it as a UX observation.) A second confirmation email arrives with a different code (`CODE2`) and link (`LINK2`). |
| 4 | Enter `CODE1` and click **Confirm**. | "Incorrect code (4 attempt(s) remaining)." under the input: the resend replaced it. |
| 5 | Main window: open `LINK1` and click **Confirm this transfer**. | "This confirmation link is invalid or has expired." and no button. Mailbox `?to=qa.anon.recipient3@example.com` stays empty (wait a minute). |
| 6 | Visitor: enter four more wrong codes (e.g. `000001` to `000004`, none of them `CODE2`), one at a time. | The first three answer "Incorrect code (3 attempt(s) remaining).", "(2 ...)", "(1 attempt(s) remaining)." The fourth (the fifth wrong code in all) must say clearly that the code can no longer be used and a new one is needed. Record **FAIL** if the field shows an empty error (a red border with no text) or nothing at all: the "too many attempts" error has no message today (`apps/file_transfer/views/anonymous.py`, `form.add_error('code', str(exc))`). |
| 7 | Enter `CODE2`. | Refused the same way: after five wrong guesses even the right code no longer confirms. |
| 8 | Wait 60 seconds after step 3, click **Resend code**; enter the new code (`CODE3`) from the newest email. | Navigates to the anonymous sent page. Mailbox `?to=qa.anon.recipient3@example.com`: one `qa-anon-3.txt was sent to you via ZipTrigo`, expiring about 15 days ahead. |

### FT-48 The anonymous limits come from the admin

**Purpose:** the anonymous page reads its file cap and expiry choices from the settings, and the
per-connection daily cap refuses a send before any code is emailed.
**Preconditions:** FT-47; main window: log out and log in as `qa.admin@example.com`; a **clean**
visitor context.

| # | Action | Expected result |
|---|---|---|
| 1 | Main window: `/admin/file_transfer/filetransfersettings/`; set **Anonymous max files** `2` and **Anonymous allowed expiry days** `2,7`; **Save**. | The success message; the values are kept. |
| 2 | Clean visitor context: `/transfer/send/anon/`. | "Sending without an account is free, within limits: up to 2 files, 5.0 GB total, 10 recipients. ..." The **Expiry** select offers only "2 days" and "7 days". |
| 3 | Add `qa-anon-4.txt`; when it has its green check, add `qa-anon-5.txt`; when that one has finished, add `qa-anon-3.txt`. | Two rows with green checks. No third row: a red line above the list, "Could not add qa-anon-3.txt: A transfer can have at most 2 files." |
| 4 | Your email `qa.anon.sender@example.com`, Recipients `qa.anon.recipient3@example.com`, Expiry "7 days". Don't send yet. Main window: set **Anonymous max transfers per ip per day** to `1`; **Save**. | Saved. |
| 5 | Visitor: **Send**. | Red box: "Daily limit reached for anonymous transfers from this connection. Try again tomorrow, or sign in to send without a limit." The page stays on the form (no **Confirm your email**), and no new confirmation email reaches `qa.anon.sender@example.com` (wait a minute): the confirmed anonymous transfers of FT-34 and FT-47 came from this same connection. (Counted per client IP and per browser cookie: if your visitor contexts reach the server over different loopback addresses, `::1` and `127.0.0.1`, the count may differ -- note it.) |
| 6 | Main window: put the settings back: max transfers per ip per day `5`, max files `20`, allowed expiry days `1,5,15,30`; **Save**. | Saved. |
| 7 | Visitor: reload; Expiry "5 days"; **Send**. | The expiry list is 1/5/15/30 days again, and the send goes through to **Confirm your email** (leave it unconfirmed). |

### FT-49 Claim on login waits until the account has a credit

**Purpose:** an anonymous transfer sent from a confirmed account's address is not claimed while that
account's balance is below 1 credit (it would be suspended at once), and is claimed at the first
login after a top-up.
**Preconditions:** FT-48 (main window as `qa.admin`); `qa.broke@example.com` still at 0 credits; a
**clean** visitor context.

| # | Action | Expected result |
|---|---|---|
| 1 | Clean visitor context: anonymous send of `qa-anon-5.txt`, Your email `qa.broke@example.com`, Recipients `qa.anon.recipient3@example.com`, Expiry "1 day"; confirm with the code from `/__e2e__/mail/?to=qa.broke@example.com`. | The anonymous sent page. |
| 2 | In the same visitor window log in as `qa.broke@example.com` and open `/transfer/`. | No `qa-anon-5.txt` row (only FT-11's "You have an unfinished upload." banner and the empty state). |
| 3 | Main window: adjust credits for `qa.broke@example.com`, "Add", `1`. | "... New balance: 1." |
| 4 | Visitor: log out, log in again as `qa.broke@example.com`, open `/transfer/`. | A `qa-anon-5.txt` row: "1 file · ... · ACTIVE", "Credits charged: 0", with Copy link / Disable / Manage / Delete; **Manage** lists `qa.anon.recipient3@example.com`. Log out. |

### FT-50 "Create an account" from the anonymous sent page claims the earlier transfers

**Purpose:** what the anonymous sent page promises happens: an account created with the sender's
address picks up its anonymous transfers at login, a disabled one included, which can then be
re-enabled from the dashboard.
**Preconditions:** FT-34 (`ANON_SLUG_1` disabled), FT-47 (`qa-anon-3.txt` active), FT-48 (a pending,
unconfirmed one), all from `qa.anon.sender@example.com`; visitor window logged out.

| # | Action | Expected result |
|---|---|---|
| 1 | Visitor window: `/account/register/` (where the sent page's **Create an account** link goes): Name `QA Anon Sender`, Email `qa.anon.sender@example.com`, Password `AnonSender-4821`. | "Check your email". |
| 2 | Mailbox `?to=qa.anon.sender@example.com`: open the newest `Confirm your ZipTrigo account email` link, press **Confirm my email**. | "Email Confirmed!". |
| 3 | Log in as `qa.anon.sender@example.com`; open `/transfer/`. | Two rows: `qa-anon-3.txt` **ACTIVE** and `qa-anon.txt` **DISABLED** with **Re-enable**, **Manage** and **Delete**. The FT-48 transfer is not listed (never confirmed). |
| 4 | Click **Re-enable** on `qa-anon.txt`. | **ACTIVE** (the account's 1,000 welcome credits cover a day). `/t/<ANON_SLUG_1>/` shows the download page again. |
| 5 | Open `ANON_MANAGE_URL`. | The neutral "This transfer is no longer available" page: the one-time manage link only serves a transfer nobody owns. Log out. |

## Layout and access

### FT-51 Phone width, dark mode and keyboard

**Purpose:** the File transfer pages work at phone width and in dark mode, and the dashboard can be
driven from the keyboard.
**Preconditions:** main window as `qa.user` (log in again if needed); visitor window logged out.

| # | Action | Expected result |
|---|---|---|
| 1 | Set the viewport to 375 x 812. Open `/transfer/send/` and add `qa-report.txt`. | No horizontal scrolling. The drop zone and its two buttons fit; the row (name, bar, status, trash) fits, the name truncating rather than pushing the trash icon off screen; the Expiry / Max downloads and Password / checkbox pairs fit side by side or wrap without overflow. |
| 2 | Open `/transfer/` and click **Manage** on row A. | No horizontal scrolling; each row's buttons wrap onto new lines; the Manage panel is one column (expiry form above recipients and download log); long addresses truncate. |
| 3 | Visitor window at 375 wide: `/t/<SLUG_A>/` and `/t/<SLUG_BIG>/`; then `/t/AAAAAAAAAAAAAAAAAAAAAA/`. | File names truncate and every **Download** button stays visible; **Report this transfer** is reachable and its form fits. The neutral page is centred and fits. |
| 4 | Main window: on the account page's **Appearance** choose **Dark**. Open `/transfer/send/`, `/transfer/` (Manage open on row A, and the Ended tab), a sent page (`/transfer/sent/<TRANSFER_A_ID>/`). Visitor window (emulate `prefers-color-scheme: dark`): `/t/<SLUG_A>/` with the report form open, `/transfer/send/anon/` (or the anonymous sent page it redirects to). | Dark backgrounds, readable text and borders everywhere; no white card left over; the red error box, the green report confirmation and the status lines stay legible; the date picker's calendar is dark too. |
| 5 | Main window, `/transfer/` at desktop width: press Tab from the top of the page. | The first stop is "Skip to content". Tab reaches **New transfer**, the **Active**/**Ended** tabs (Enter switches the list), then each row's **Copy link**, **Disable**, **Manage**, **Delete**, each with a visible focus ring. |
| 6 | Focus **Manage** on row A and press Enter. | The panel opens. Tab moves through Expiry, the date button (when "Set a date"), Max downloads, Password, Remove password, **Save**, each **Resend**, the Add recipients box and **Add**. Enter on **Manage** again closes the panel. |
| 7 | On `/transfer/send/`, Tab to **Choose files** and press Enter; Tab to the password field's eye button and press Space. | Enter opens the file picker (cancel it). Space shows the password and the button's name becomes "Hide password". |
| 8 | Reset: Appearance back to **System**, viewport back to normal. | -- |

## Out of scope

- **Rate limits** (`FT_UPLOAD_USER`, `FT_DOWNLOAD_IP`, `FT_UNLOCK_*`, `FT_REPORT_*`,
  `FT_RECIPIENT_EMAIL_USER`, the anonymous ones): switched off on this server. Unit tests cover
  them.
- **Admin moderation** (takedown, the block list and its "Sending isn't available" page, the
  auto-hold and its release): `admin.md` (ADM-21 to ADM-26), which drives them from the admin.
- **Upload expiry**: an incomplete multipart upload the bucket aborted (after a day) and the
  `resume` endpoint restarting it (`restarted: true`), and `cleanup_drafts` deleting a draft idle
  for 24 hours. Both need a day or the job, and the visible result is only "the upload starts
  again from 0%"; unit tests cover them.
- **`purge_download_ips`** (nulls a download's IP after 90 days): nothing visible in a run.
- **Files of several GB**, the per-file and total size limits (5 GB / 20 GB): impractical in a
  browser test; the 70 MiB file of FT-39 exercises the multipart path the same way.
- **The JWT API `/api/ft/` and the `inv filetransfer` CLI**: unit tests.
