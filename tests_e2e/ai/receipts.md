# Receipts

## Scope

The Receipts web UI under `/receipts/`: first visit, the upload page (direct-to-S3 uploads, client
and server refusals, credit charge per file), the dashboard (tabs, search, category and date
filters, sorting, pagination, URL state, CSV export), a receipt's page (preview, editing,
validation, status changes, the vendor combobox, vetting, renaming, re-running, download, trash),
the Trash, the Vendors page (tabs, search, bulk vetting, merging, deleting, the dashboard's vendor
filter), the settings page (address, accepted senders, default currency, categories), waiting for
credits and a paused (frozen) account, and access control.

Out of scope: inbound email (the "Recent emails" list stays empty here), the JWT API and the CLI,
HEIC files, rate limits (off on this server), and real AI extraction.

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
  - The receipt files live in an in-memory bucket served by the site itself at
    `/__e2e__/s3/<key>`; presigned URLs (previews, downloads, the upload PUT) point there.
- `ReceiptsSettings` defaults apply: **1 credit per file** (`price_per_file`), max file size
  **15 MB**, trash kept **30 days**, 100 files per user per 24 h.
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
window.rcptPick = (specs) => {
  const input = document.getElementById('file-input');
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
// Paste the base64 strings from the table above:
window.PNG_A = '...'; window.PNG_B = '...'; window.PDF_A = '...';
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
| 5 | Click **Open full size**. | Opens the image in a new tab from `/__e2e__/s3/receipts/users/…?disposition=inline`; it displays (not downloaded). Close the tab. |

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
| 1 | On R1, in the console run `const r = await fetch(location.pathname + 'download/'); [r.status, r.redirected, r.url, r.headers.get('content-disposition'), (await r.arrayBuffer()).byteLength]`. | `[200, true, "<…>/__e2e__/s3/receipts/users/<…>/files/<R1_ID>?disposition=attachment&filename=staples-1.png", 'attachment; filename="staples-1.png"', 280]`. |
| 2 | Click **Download original**. | The browser downloads `staples-1.png` (280 bytes). The page itself does not change. |
| 3 | Open `/receipts/<R3_ID>/`. | The preview is an iframe titled "Preview of Staples" showing the PDF page with "STAPLES 42.10" in the browser's PDF viewer. (The fake bucket serves the inline PDF with `X-Frame-Options: SAMEORIGIN`, so the same-origin frame is allowed. A headless browser without a PDF viewer shows an empty frame and downloads the file instead: a tool limitation, not a failure.) |

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

### RCPT-44 Paused storage (frozen account) banner — needs setup

**Purpose:** a frozen account shows the "Storage is paused" banner everywhere, blocks uploads,
previews, downloads and re-runs, and a top-up lifts it.
**Preconditions:** needs a shell on the machine running the server (the daily storage billing that
freezes an account does not run on this server). If you have no shell, mark **BLOCKED (needs-setup)**.
From the repo root run:

```bash
ENVIRONMENT=local DJANGO_SETTINGS_MODULE=tests_e2e.receipts_e2e_settings E2E_DB_NAME=db.e2e-ai.sqlite3 \
  python manage.py shell -c "from django.utils import timezone; from apps.receipts.models import ReceiptsAccount; print(ReceiptsAccount.objects.filter(user__email='qa.broke@example.com').update(frozen_at=timezone.now()))"
```

It prints `1`. (`E2E_DB_NAME` must be the running server's database file: `db.e2e-ai.sqlite3` for the
default server, the file of your own server when several run in parallel; an absolute path works.)
Stay signed in as `qa.broke@example.com`.

| # | Action | Expected result |
|---|---|---|
| 1 | Open `/receipts/`. | An amber banner at the top (role alert): "**Storage is paused.** Your balance couldn't cover storage. Your receipts are safe; previews, downloads and processing resume when you add credits." with a **Top up** button to `/billing/credits/`. The list still works. |
| 2 | Open `/receipts/upload/`, `/receipts/settings/`, `/receipts/vendors/`, `/receipts/trash/`. | The same banner on each. On the upload page **Choose files** and **Take photo** are disabled, and "Uploads are paused until you add credits." is shown; the "not enough credits" notice is not. |
| 3 | Open `/receipts/<B1_ID>/`. | Preview: "Previews and downloads are paused until you add credits." with a **Top up** link; no **Download original**. |
| 4 | Open `/receipts/<B1_ID>/download/` directly. | Back on the receipt page with the red flash "Your receipts are paused until you add credits." |
| 5 | Click **Re-run extraction (1 credit)** and confirm. | Red flash "Your receipts are paused until you add credits."; balance unchanged (4). |
| 6 | As `qa.admin`, add 1 credit to `qa.broke@example.com` with the adjustment tool (RCPT-43 step 2). | Success message. |
| 7 | As `qa.broke`, reload `/receipts/`. | The banner is gone; the receipt page shows its preview and **Download original** again. |

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
