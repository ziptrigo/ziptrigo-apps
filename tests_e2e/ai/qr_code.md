# QR codes

## Scope

The QR code product's web UI (`apps/qr_code`):

- the dashboard `/qr/` (empty state, list, thumbnails from `/qr/image/<id>/`, the enlarged preview
  modal and the delete modal with their keyboard focus handling, search, sort by name, the per-row
  actions menu);
- the editor `/qr/create/` (name, Text / URL content, type, format, "Track QR Code" with the
  server-issued short URL, live preview, validation) and `/qr/edit/<id>/` (rename only);
- duplicate (`/qr/duplicate/<id>/`) and delete (confirmation modal);
- the public short links `/go/<code>` and `/go/<code>/`, their scan count, and the neutral
  "This QR code isn't available" page for an unknown or deleted code;
- the image `/qr/image/<id>/` and its download (`?download=1`);
- sharing a QR code (issue #5): the dashboard row's **Share** item and the editor's **Share** button, the share dialog (create, copy,
  label, expiry, edit, revoke), the "Shared" icon, and the public pages `/q/<code>` and `/q/<code>/image` with the neutral unavailable
  page for every link that doesn't work (QR-32 to QR-37);
- sending a share link by email: the **Send by email** form in the dialog, the people a link was sent to, **Resend**, the 10-minute
  cooldown, the cap of 20 people, and every refusal (QR-38 to QR-40; the email itself is MAIL-31 in `emails.md`);
- reporting a shared QR code (the collapsed **Report this QR code** form on the public page) and a takedown by staff as the owner
  and the visitor see it, including that a tracked code's `/go/` short link keeps working (QR-41 and QR-42; the admin side is ADM-69
  in `admin.md`);
- the share links in the JWT API (`/api/qr/{id}/links`) and the `inv qrcode links|share|unshare` commands (QR-43);
- the "too long for a QR code" refusal, including the advice to turn on tracking for a long URL;
- access control: logged-out visitors, another user's QR code ids;
- that creating and sharing QR codes costs no credits;
- phone width, dark mode and keyboard use of the dashboard and the editor.

The editor offers exactly two content types, **Text** and **URL** (a URL can be tracked); both are
covered. Not in the web UI, so out of scope here: size, border, error correction and colours (only
the JWT API at `/api/qr/` sets them; covered by unit tests). The web UI offers the PNG through
**Download** in each row's menu (SVG and PDF formats chosen at creation are not offered); QR-23
checks it.

## Preconditions

- The test server is running at `http://localhost:8765`, freshly restarted (empty database), as
  described in [`README.md`](README.md). Rate limiting is off. This file needs neither S3 nor
  email except for QR-38 to QR-40, which send the share email: run those in the default real-services mode (the emails are read at
  `/__e2e__/mail/`, see `emails.md` "Preconditions"). QR images are rendered on demand from the database.
- The server's `BASE_URL` is `http://localhost:8765`, so every short URL reads
  `http://localhost:8765/go/<code>` (8 letters/digits, case-sensitive).
- Cases run in order; later ones reuse QR codes created earlier.
- The editor's red error box lists `Label: message` lines (e.g. `Name: This field is required.`).
  Each message is also repeated in small red text under the field that failed, with a red border,
  `aria-invalid="true"` and the focus moved to the first invalid field; it disappears when the
  field is edited. A line without a field label (a general error) is only in the box.
- `qa.user@example.com` has no QR codes at the start. Nothing in this file creates QR codes for
  any other account before QR-21.

## Test data

| Name | Value |
|---|---|
| Main user | `qa.user@example.com` / `QaE2e-Passw0rd!` (100 credits) |
| Another user | `qa.broke@example.com` / `QaE2e-Passw0rd!` (0 credits) |
| Admin (scan counts only) | `qa.admin@example.com` / `QaE2e-Passw0rd!` |
| Text QR | name `QA Text`, content `Hello from QA` |
| Tracked URL QR | name `QA Tracked`, content `http://localhost:8765/?from=qr` (a page on the test server, so following the short link needs no internet) |
| Plain URL QR | name `QA Plain URL`, content `https://example.com/path?x=1&y=2`, format SVG |
| Unicode QR | name `Café ☕ QR 👋`, content `Grüße aus Lisboa — 你好 👋 مرحبا` |
| Injection QR | name `<img src=x onerror=alert('name')>`, content `<script>alert('content')</script>` |
| Too long for a QR code | `'한'.repeat(1000)` (1,000 Hangul syllables = 3,000 UTF-8 bytes: within the 1,000-character limit but over a QR code's capacity) |

Record these values as you go; later cases refer to them:

- `TEXT_ID`: the UUID of `QA Text` (QR-06);
- `SHORT_URL` / `CODE`: the short URL of `QA Tracked` and its 8-character code (QR-07, QR-08);
- `TRACKED_ID`, `PLAIN_ID`: the UUIDs of `QA Tracked` and `QA Plain URL`.

A QR code's UUID is in its row's Edit link: open the row's actions menu (the vertical "⋮" button,
labelled "Actions for <name>") and read the `href` of **Edit**, `/qr/edit/<uuid>/`.

---

### QR-01 Logged-out visitors are sent to the login page

**Purpose:** every QR page needs a login, and the login comes back to the page asked for.
**Preconditions:** not logged in (fresh browser session).

| # | Action | Expected result |
|---|---|---|
| 1 | Go to `http://localhost:8765/qr/`. | Redirected to `/account/login/?next=/qr/`. The login form shows "Email", "Password" and a **Sign in** button. |
| 2 | Go to `/qr/create/`. | Redirected to `/account/login/?next=/qr/create/`. |
| 3 | Go to `/qr/image/00000000-0000-0000-0000-000000000000/` and `/qr/edit/00000000-0000-0000-0000-000000000000/`. | Both redirect to the login page with the path in `next` (no 404 and no image is revealed before login). |
| 4 | Go to `/qr/create/` again, then sign in as `qa.user@example.com` / `QaE2e-Passw0rd!`. | After signing in the browser lands on `/qr/create/` (the `next` target), not on `/account/`. The heading reads "Create QR code". |

### QR-02 Empty dashboard and navigation

**Purpose:** a user with no QR codes sees a clean empty state.
**Preconditions:** logged in as `qa.user@example.com` (QR-01).

| # | Action | Expected result |
|---|---|---|
| 1 | Go to `/` and click **QR Codes** in the top navigation (qrcode icon). | Lands on `/qr/`. Browser tab title "QR codes - ZipTrigo". |
| 2 | Look at the page. | Heading "QR codes"; a **New QR code** button (plus icon) at the top right; a search box with placeholder "Search by name" and no clear (×) button visible; a list header "Sort by" with **Newest** (marked current) and **Name** links, and no checkboxes; below it an empty state: a QR icon, **No QR codes yet**, "Your first code takes a minute." and a **Create QR code** button (to `/qr/create/`). The search box has a label for screen readers ("Search QR codes by name"). No broken images, no raw `{{ }}`/`{% %}`, no console errors. |
| 3 | Click **New QR code**. | Goes to `/qr/create/`. |
| 4 | Go back to `/qr/` and click the empty state's **Create QR code** button. | Also goes to `/qr/create/`. |

### QR-03 Editor layout in create mode

**Purpose:** the create form offers exactly the fields the web UI supports.
**Preconditions:** on `/qr/create/`.

| # | Action | Expected result |
|---|---|---|
| 1 | Look at the page. | Tab title "Create QR Code - ZipTrigo"; heading "Create QR code". Fields, top to bottom: **Name** (empty text input); **Text / URL to encode** (empty textarea, 4 rows) with the hint "Up to 1000 characters. Can be any text; short URL is only meaningful when the content is a URL."; **Type** select (options `Text`, `URL`; `Text` selected); **Format** select (options `PNG`, `SVG`, `PDF`; `PNG` selected); a **Track QR Code** checkbox with a question-mark icon. |
| 2 | Hover the question-mark icon next to "Track QR Code". | Tooltip: "This tracks the QR Code usage and gives you analytical data about its usage." |
| 3 | Check the state of **Track QR Code**. | It is unchecked and **disabled** (Type is `Text`). No "Short URL" field is visible. |
| 4 | Look below the options. | A **Preview** button and, next to it, a dashed box showing the ZipTrigo QR logo placeholder (`/static/qr_code/images/logo_128x128.png`), not a QR code. At the bottom right: **Cancel** and **Save**. |
| 5 | Look for size, border, error-correction or colour controls. | There are none (not offered by the web editor). |
| 6 | Click **Cancel**. | Back on `/qr/` with nothing created (the "No QR codes yet" empty state). |

### QR-04 Preview validation messages

**Purpose:** the Preview button reports server-side validation errors in the message box above the form.
**Preconditions:** on `/qr/create/`.

| # | Action | Expected result |
|---|---|---|
| 1 | Leave everything empty and click **Preview**. | A red message appears above the form: "Name: This field is required." and on the next line "Text / URL: Please provide the text or URL to encode.". The two messages are repeated under the Name field and under the Text / URL field, and keyboard focus is on Name. The preview box still shows the logo placeholder. |
| 2 | Type two spaces in Name and two spaces in Text / URL, click **Preview**. | The same two messages (whitespace counts as empty). |
| 3 | Name `Check`, Type `URL`, Text / URL `example.com`, click **Preview**. | Only "Text / URL: Please enter a valid URL, e.g. https://example.com." (a URL needs a scheme). |
| 4 | Replace the content with `javascript:alert(1)`, click **Preview**. | Same message: "Text / URL: Please enter a valid URL, e.g. https://example.com.". No alert dialog. |
| 5 | Replace the content with `https://example.com/a b` (with a space), click **Preview**. | Same "Please enter a valid URL" message. |
| 6 | Replace the content with `https://example.com`, click **Preview**. | The red message disappears and the preview box now shows a black-on-white QR code. |

### QR-05 Live preview renders without saving

**Purpose:** Preview returns an inline PNG (`data:` URI), follows the content, and saves nothing.
**Preconditions:** on `/qr/create/` (reload it to start clean).

| # | Action | Expected result |
|---|---|---|
| 1 | Name `Preview only`, Type `Text`, Text / URL `Hello from QA`, click **Preview**. | A QR code appears in the preview box. Inspect the image (`#qrcode-preview-img`): its `src` starts with `data:image/png;base64,`, not a `/qr/...` or `/media/...` URL. |
| 2 | Change the content to `Hello from QA, version 2` and click **Preview** again. | The image changes (a visibly different, denser pattern; the `src` value differs). |
| 3 | Set Format to `PDF` and click **Preview**. | Still an image preview (PNG `data:` URI); no PDF viewer, no download, no error. |
| 4 | Point your phone or a QR decoder at the preview, if available (optional). | It decodes to `Hello from QA, version 2`. |
| 5 | Click **Cancel**. | `/qr/` still shows the "No QR codes yet" empty state (preview saved nothing). |

### QR-06 Create a static Text QR code

**Purpose:** the basic create flow, the browser-side required fields, and the thumbnail.
**Preconditions:** on `/qr/create/`, no QR codes yet.

| # | Action | Expected result |
|---|---|---|
| 1 | Leave Name empty, type `Hello from QA` in Text / URL, click **Save**. | The browser blocks the submit with its native "Please fill out this field." bubble on Name; no request is sent and nothing is created. |
| 2 | Name `QA Text`, Text / URL `Hello from QA`, Type `Text`, Format `PNG`, click **Save**. | The Save button is briefly disabled, then the browser goes to `/qr/`. |
| 3 | Look at the dashboard. | One row: a 48×48 thumbnail, the name `QA Text` and, below it in small grey text, `Hello from QA`. The "No QR codes yet" empty state is gone. |
| 4 | Check the thumbnail image. | Its `src` is `/qr/image/<uuid>/` and it has loaded (`naturalWidth > 0`, a QR pattern is visible, not a broken-image icon). Its `alt` text is `QA Text`. Record the UUID as `TEXT_ID`. |
| 5 | Open `/qr/image/<TEXT_ID>/` directly in a new tab. | A PNG QR code is shown (response `Content-Type: image/png`). |

### QR-07 "Track QR Code" checkbox and the server-issued short URL

**Purpose:** tracking is only for URLs, the short code comes from the server, and it is stable within the session.
**Preconditions:** logged in as `qa.user`; go to `/qr/create/`.

| # | Action | Expected result |
|---|---|---|
| 1 | With Type `Text`, try to tick **Track QR Code**. | It can't be ticked (disabled). |
| 2 | Change Type to `URL`. | **Track QR Code** becomes enabled (still unchecked). |
| 3 | Tick **Track QR Code**. | A read-only **Short URL** field appears below the options, filled with `http://localhost:8765/go/` followed by 8 letters/digits (mixed case). Record it as `SHORT_URL`, and its last 8 characters as `CODE`. The field can't be edited. |
| 4 | Untick, then tick **Track QR Code** again. | The field hides, then reappears with **the same** `SHORT_URL`. |
| 5 | Change Type back to `Text`. | The checkbox is unticked and disabled again and the Short URL field is hidden. |
| 6 | Reload the page, set Type `URL` and tick **Track QR Code**. | The Short URL is still `SHORT_URL` (the server keeps the issued code for this session until a QR code is saved). |

### QR-08 Create a tracked URL QR code

**Purpose:** a tracked code saves the issued short code and encodes the `/go/` link.
**Preconditions:** QR-07 just finished: on `/qr/create/`, Type `URL`, Track ticked, Short URL = `SHORT_URL`.

| # | Action | Expected result |
|---|---|---|
| 1 | Name `QA Tracked`, Text / URL `http://localhost:8765/?from=qr`, click **Preview**. | A QR code appears, no error. (Optional: decoding it gives `SHORT_URL`, not the destination URL.) |
| 2 | Click **Save**. | Goes to `/qr/`. Two rows now, newest first: `QA Tracked` above `QA Text`. |
| 3 | Read the `QA Tracked` row. | The grey line under the name is `SHORT_URL` exactly (the dashboard shows the encoded short link, not the destination). The thumbnail loads. Record its UUID as `TRACKED_ID`. |
| 4 | Go to `/qr/create/`, set Type `URL` and tick **Track QR Code**. | The Short URL shown now is a **different** code from `CODE` (the previous one was consumed by the save). Click **Cancel**. |

### QR-09 Create a plain (untracked) URL QR code in SVG format

**Purpose:** an untracked URL encodes the URL itself; the format choice is accepted.
**Preconditions:** logged in as `qa.user`.

| # | Action | Expected result |
|---|---|---|
| 1 | Go to `/qr/create/`. Name `QA Plain URL`, Type `URL`, Track unticked, Text / URL `https://example.com/path?x=1&y=2`, Format `SVG`. Click **Save**. | Goes to `/qr/`; three rows, `QA Plain URL` on top. |
| 2 | Read its row. | The grey line is `https://example.com/path?x=1&y=2` (the `&` shown as `&`, not `&amp;`). Thumbnail loads. Record its UUID as `PLAIN_ID`. |
| 3 | Open `/qr/image/<PLAIN_ID>/`. | A PNG image (the on-page image is always PNG, whatever the chosen format). |

### QR-10 Enlarged preview modal on the dashboard

**Purpose:** clicking a thumbnail opens a larger preview that can be closed three ways.
**Preconditions:** on `/qr/` with the three codes above.

| # | Action | Expected result |
|---|---|---|
| 1 | Hover the `QA Text` thumbnail. | It scales up slightly. The thumbnail is a button named "Preview QA Text" (the image inside has no alt text of its own). |
| 2 | Click it. | A dark overlay opens with a large QR code image on a white background, and a "×" close button (labelled "Close QR code preview") at the top right. The image is `/qr/image/<TEXT_ID>/`. |
| 3 | Click **×**. | The overlay closes, focus returns to the thumbnail you opened it from, and the hidden image has no `src` (check `document.getElementById('qr-modal-image').hasAttribute('src')` is `false`). |
| 4 | Open it again and press **Escape**. | It closes. |
| 5 | Open it again and click the dark area outside the image panel. | It closes. Clicking on the dark panel itself (around the image) does not close it. |

### QR-11 Follow the short link

**Purpose:** `/go/<code>` and `/go/<code>/` redirect to the destination; unknown codes are a plain 404.
**Preconditions:** `CODE` from QR-07/QR-08.

| # | Action | Expected result |
|---|---|---|
| 1 | Open `SHORT_URL` (`http://localhost:8765/go/<CODE>`) in the browser. | Redirected (302) to `http://localhost:8765/?from=qr`: the ZipTrigo home page loads. |
| 2 | Open `http://localhost:8765/go/<CODE>/` (trailing slash). | Same redirect to `/?from=qr`. |
| 3 | Open `/go/<CODE>` with the case of one letter flipped (e.g. `a` ↔ `A`). | HTTP 404 with a small standalone page (no site header, navigation or footer): the ZipTrigo logo, the heading "This QR code isn't available", the line "It may have been removed by its owner." and a link **Go to ZipTrigo** (to `/`). Tab title "QR code unavailable - ZipTrigo". Codes are case-sensitive. |
| 4 | Open `/go/doesnotexist`, then click **Go to ZipTrigo**. | The same 404 page, word for word; the link goes to the home page. |
| 5 | Open `/go/`. | A 404 (Django's own "Page not found", not the QR page above); no redirect. |
| 6 | Log out (account icon → **Logout**), open `SHORT_URL` again, then log back in as `qa.user`. | Logged out, the short link still redirects to `/?from=qr` (short links are public). |

So far `QA Tracked` has been followed **3** times successfully (steps 1, 2 and 6).

### QR-12 Scan count

**Purpose:** each redirect is counted; failed lookups are not.
**Preconditions:** QR-11 done (3 successful redirects).

| # | Action | Expected result |
|---|---|---|
| 1 | As `qa.user`, look at the `QA Tracked` row on `/qr/` and its Edit page. | Note whether a scan count is shown anywhere. (At the time of writing the web UI shows **no** scan count; the count is only visible in the admin. Report this as an observation, not a failure.) |
| 2 | Log out and sign in as `qa.admin@example.com`. Go to `/admin/qr_code/qrcode/`. Search for `CODE`. | One row, name `QA Tracked`, with **Scan count** = `3`. |
| 3 | In another tab open `SHORT_URL` once more, then reload the admin list. | **Scan count** = `4`. |
| 4 | Open the row. | "Last scanned at" is a moment ago; "Use url shortening" is ticked; "Original url" is `http://localhost:8765/?from=qr`; "Short code" is `CODE`. |
| 5 | Log out and sign back in as `qa.user@example.com`. | Back on the site as `qa.user`. |

### QR-13 Edit (rename) a QR code

**Purpose:** edit mode only changes the name; content, type and short link stay.
**Preconditions:** logged in as `qa.user`, on `/qr/`.

| # | Action | Expected result |
|---|---|---|
| 1 | Click the "⋮" button on the `QA Tracked` row. | A menu opens with **Edit**, **Duplicate**, **Share**, **Download** and **Delete** (red). Clicking elsewhere on the page closes it; clicking "⋮" on another row closes this one and opens that one. |
| 2 | Click **Edit**. | `/qr/edit/<TRACKED_ID>/`. Tab title "Edit QR Code - ZipTrigo", heading "Edit QR code". |
| 3 | Look at the form. | **Name** = `QA Tracked` (editable); **Type** shown read-only as `URL` (grey box, not a select); **Text / URL (read-only)** is a disabled grey textarea showing `SHORT_URL`; no Format select, no Track checkbox, no Short URL field, no Preview button. "QR Code Preview:" label with the saved QR image (`/qr/image/<TRACKED_ID>/`). **Cancel** and **Save**. |
| 4 | Clear the Name and click **Save**. | Native "Please fill out this field." bubble; nothing is sent. |
| 5 | In the browser console run `document.querySelector('input[name=name]').removeAttribute('required')`, keep Name empty and click **Save**. | Red message above the form: "Name: This field is required."; the page stays on the editor. |
| 6 | Set Name to `QA Tracked (renamed)` and click **Save**. | Goes to `/qr/`. The row now reads `QA Tracked (renamed)` with the same `SHORT_URL` and the same thumbnail. The list order is unchanged (still sorted by creation date). |
| 7 | Open `SHORT_URL`. | Still redirects to `/?from=qr`. |
| 8 | Open the Edit page of `QA Tracked (renamed)` again and click **Cancel**. | Back on `/qr/` with nothing changed. |

### QR-14 Duplicate a tracked QR code

**Purpose:** duplicate pre-fills the create form from the source, with the destination URL and a new short code.
**Preconditions:** on `/qr/`.

| # | Action | Expected result |
|---|---|---|
| 1 | Open the `QA Tracked (renamed)` menu and click **Duplicate**. | `/qr/duplicate/<TRACKED_ID>/`, heading "Create QR code" (create mode, not edit). |
| 2 | Look at the pre-filled form. | Name `QA Tracked (renamed)`; Text / URL `http://localhost:8765/?from=qr` (the original destination, **not** `SHORT_URL`); Type `URL`; Format `PNG`; **Track QR Code** ticked and enabled; the Short URL field is visible with a code **different from** `CODE`. |
| 3 | Change Name to `QA Tracked copy` and click **Save**. | Goes to `/qr/`; four rows, `QA Tracked copy` on top, its grey line being the new short URL. `QA Tracked (renamed)` is unchanged. |
| 4 | Open the new short URL. | Redirects to `/?from=qr`. `SHORT_URL` still works too. |

### QR-15 Duplicate a Text QR code

**Purpose:** a Text duplicate keeps its type and cannot be tracked.
**Preconditions:** on `/qr/`.

| # | Action | Expected result |
|---|---|---|
| 1 | Open the `QA Text` menu and click **Duplicate**. | Create-mode editor with Name `QA Text`, Text / URL `Hello from QA`, Type `Text`, Format `PNG`, **Track QR Code** unticked and disabled, no Short URL field. |
| 2 | Click **Preview**. | A QR code appears (same pattern as the `QA Text` thumbnail). |
| 3 | Click **Cancel**. | Back on `/qr/`, still four rows (duplicate creates nothing until Save). |

### QR-16 Search and sort

**Purpose:** the dashboard filters by name (case-insensitive) and sorts by name.
**Preconditions:** four codes: `QA Tracked copy`, `QA Plain URL`, `QA Tracked (renamed)`, `QA Text` (newest first).

| # | Action | Expected result |
|---|---|---|
| 1 | Check the default order. | Newest first: `QA Tracked copy`, `QA Plain URL`, `QA Tracked (renamed)`, `QA Text`. |
| 2 | Type `tracked` in "Search by name". | A clear (×) button appears at the right of the box as you type. |
| 3 | Press **Enter**. | URL becomes `/qr/?q=tracked`; only the two "QA Tracked…" rows are listed; the box keeps `tracked`. |
| 4 | Click the × button. | Back to `/qr/` with all four rows and an empty box. |
| 5 | Search `zzz`. | The empty state reads **No QR codes found** with "Try another name." (a magnifier icon, no button). |
| 6 | Search `plain`, then click the **Name** sort link. | URL is `/qr/?sort=name&q=plain`; still only `QA Plain URL`; **Name** is now the current sort. |
| 7 | Clear the search, then click **Name**. | Alphabetical: `QA Plain URL`, `QA Text`, then the two `QA Tracked…` codes. Their order between themselves follows the database's collation: on the Postgres test database (an `en_US` locale ignores punctuation and spaces at first) `QA Tracked copy` may come before `QA Tracked (renamed)`; either order is a PASS. |
| 8 | Click **Newest**. | Back to newest first, as in step 1. |

### QR-17 Search terms with special characters survive sorting

**Purpose:** the search term is carried into the sort link intact.
**Preconditions:** on `/qr/`.

| # | Action | Expected result |
|---|---|---|
| 1 | Create a Text QR code named `R&D Lab` with content `R&D`. | Saved; five rows. |
| 2 | Search `R&D`. | URL `/qr/?q=R%26D`; only `R&D Lab` is listed. |
| 3 | Click the **Name** sort link. | Still only `R&D Lab`, and the search box still says `R&D` (the link should be `?sort=name&q=R%26D`). If other codes appear too (every name containing "r"), or the box shows only `R`, mark FAIL: the term is not URL-encoded in the sort link (it becomes `?sort=name&q=R&D`). |

### QR-18 Too long, and too big for a QR code

**Purpose:** the 1,000-character limit and the "doesn't fit" check, in the browser and on the server.
**Preconditions:** on `/qr/create/`, Name `Too long`, Type `Text`.

| # | Action | Expected result |
|---|---|---|
| 1 | Type (or paste) 1,001 `a` characters into Text / URL. | The textarea stops at 1,000 characters (`document.querySelector('textarea[name=url]').value.length` is `1000`). |
| 2 | Click **Preview**. | A QR code is rendered (1,000 ASCII characters fit). |
| 3 | In the console run `const t = document.querySelector('textarea[name=url]'); t.removeAttribute('maxlength'); t.value = 'a'.repeat(1001);` then click **Preview**. | Red message: "Text / URL: Ensure this value has at most 1000 characters (it has 1001)." |
| 4 | Click **Save** (the textarea still holds 1,001 characters). | The same message; still on `/qr/create/`; no row is created. |
| 5 | Reload. Name `Too big`, Type `Text`, then in the console set `document.querySelector('textarea[name=url]').value = '한'.repeat(1000)` and click **Preview**. | Red message: "Text / URL: That is too long for a QR code. Please shorten it." (For a URL it adds: or turn on "Track QR Code".) |
| 6 | Click **Save**. | Same message; nothing is created. |
| 7 | Replace the content with `'😀'.repeat(500)` (console) and click **Preview**. | A (dense) QR code renders: 500 emoji fit. |
| 8 | Click **Cancel**, check `/qr/`. | Still five rows (`Too long`/`Too big` were never saved). |

### QR-19 Unicode and emoji

**Purpose:** non-ASCII names and content are stored and displayed exactly.
**Preconditions:** on `/qr/create/`.

| # | Action | Expected result |
|---|---|---|
| 1 | Name `Café ☕ QR 👋`, Type `Text`, Text / URL `Grüße aus Lisboa — 你好 👋 مرحبا`. Click **Preview**. | A QR code renders, no error. |
| 2 | Click **Save**. | On `/qr/`, a new top row named `Café ☕ QR 👋` with the content line `Grüße aus Lisboa — 你好 👋 مرحبا`, every character intact (no `?`, mojibake or HTML entities). The thumbnail loads. |
| 3 | Open the row's Edit page. | Name and the read-only content show the same characters. Click **Cancel**. |
| 4 | Search `café` (lowercase). | The row is found. Clear the search. |

### QR-20 HTML in names and content is shown as text

**Purpose:** user text is escaped everywhere it is displayed.
**Preconditions:** on `/qr/create/`.

| # | Action | Expected result |
|---|---|---|
| 1 | Name `<img src=x onerror=alert('name')>`, Type `Text`, Text / URL `<script>alert('content')</script>`. Click **Save**. | Goes to `/qr/`. No alert dialog appears at any point. |
| 2 | Read the new row. | The name and content are displayed literally, angle brackets included; no broken image icon from the name. |
| 3 | Open its "⋮" menu → **Delete**. | The confirmation text reads "Are you sure you want to delete <img src=x onerror=alert('name')>?" literally, no alert. Click **Cancel**. |
| 4 | Open its Edit page. | The Name field and read-only content show the literal text. Click **Cancel**. |

### QR-21 Delete with confirmation

**Purpose:** delete asks first, can be cancelled, and then removes the code and its image.
**Preconditions:** on `/qr/`; `PLAIN_ID` from QR-09.

| # | Action | Expected result |
|---|---|---|
| 1 | Open the `QA Plain URL` menu and click **Delete**. | The menu closes and a modal opens (a dialog named "Delete QR Code", the title is a level-2 heading), text "Are you sure you want to delete QA Plain URL?" (name emphasised: brighter, medium weight), buttons **Cancel** and **Delete** (red). |
| 2 | Click **Cancel**. | The modal closes; the row is still there. |
| 3 | Open the modal again and press **Escape**. | It closes; the row is still there. |
| 4 | Open it again and click the dark backdrop. | It closes; the row is still there. |
| 5 | Open it again and click **Delete**. | The page reloads; `QA Plain URL` is gone; the other rows remain. No alert ("Failed to delete QR code…") appears. |
| 6 | Check `/qr/image/<PLAIN_ID>/` without the browser cache: in the console run `fetch('/qr/image/<PLAIN_ID>/', {cache: 'no-store'}).then(r => r.status)`. | `404`. (Opening the URL in the address bar right after the delete can still show the image: the dashboard loaded it with `Cache-Control: private, max-age=300`, so the browser serves its cached copy for up to 5 minutes. That is expected.) |
| 7 | Open `/qr/edit/<PLAIN_ID>/` and `/qr/duplicate/<PLAIN_ID>/`. | 404 for both, like the image (a deleted code should not be editable). If the editor renders instead (with a broken preview image), mark FAIL. |
| 8 | In the console run `fetch('/qr/delete/<PLAIN_ID>/', {method: 'POST', headers: {'X-CSRFToken': document.cookie.match(/csrftoken=([^;]+)/)[1]}}).then(r => r.status)`. | `404` (already deleted). |
| 9 | Open `/qr/delete/<TEXT_ID>/` with a plain GET (address bar). | 405 (delete is POST-only); `QA Text` is still on the dashboard afterwards. |

### QR-22 Short link of a deleted QR code

**Purpose:** what a scanner sees once a tracked code is deleted.
**Preconditions:** on `/qr/`; `QA Tracked copy` exists (QR-14). Note its short URL first.

| # | Action | Expected result |
|---|---|---|
| 1 | Delete `QA Tracked copy` through the menu and the modal. | It disappears from the list. |
| 2 | Open its short URL while logged in. | The same standalone 404 page as an unknown code (QR-11 step 3: "This QR code isn't available", "It may have been removed by its owner."); not redirected to `/?from=qr`, and nothing distinguishes it from a code that never existed. |
| 3 | Log out and open the same short URL. | The same 404 page, with no login redirect. |
| 4 | Open `SHORT_URL` (`QA Tracked (renamed)`, not deleted). | Still redirects to `/?from=qr`. |
| 5 | Sign back in as `qa.user`. | — |

### QR-23 What the user can download

**Purpose:** document the image the UI offers (a **Download** item in each row's menu).
**Preconditions:** logged in as `qa.user`. (`QA Plain URL` was deleted in QR-21, so this case
makes its own SVG code.)

| # | Action | Expected result |
|---|---|---|
| 1 | Create a URL QR code named `QA SVG`, content `https://example.com/svg`, Format `SVG`, Track unticked. | Saved; it is the top row on `/qr/`. |
| 2 | Open the "⋮" menu of the `QA SVG` row and look at it; also look in the preview modal and on the Edit page. | The menu has **Edit**, **Duplicate**, **Share** (share icon), **Download** (download icon) and **Delete** (red). The preview modal and the Edit page have no download control. |
| 3 | Click **Download** on the `QA Text` row. | The browser saves `qa-text.png` (the name slugified); the request was `/qr/image/<TEXT_ID>/?download=1` with `Content-Disposition: attachment; filename="qa-text.png"`. Open the file: a PNG QR code. |
| 4 | Click **Download** on the `QA SVG` row. | It saves `qa-svg.png`, a PNG too: the SVG format chosen at creation can't be obtained from the web UI (observation, not a failure). |
| 5 | Click **Download** on the `Café ☕ QR 👋` row (QR-19). | It saves `cafe-qr.png` (accents and emoji dropped from the file name). |
| 6 | Open the `QA Text` enlarged preview, right-click the image and choose "Open image in new tab" (or open `/qr/image/<TEXT_ID>/`). | A crisp PNG with a white quiet zone; the response has `Content-Type: image/png` and `Cache-Control: private, max-age=300`, and no `Content-Disposition` (shown, not downloaded). |
| 7 | Decode it with a QR reader (optional). | `Hello from QA`. |

### QR-24 Another user's QR codes are invisible

**Purpose:** ids belonging to someone else are 404 on every endpoint, never 403.
**Preconditions:** `TEXT_ID` and `TRACKED_ID` from earlier cases.

| # | Action | Expected result |
|---|---|---|
| 1 | Log out; sign in as `qa.broke@example.com`. Go to `/qr/`. | The "No QR codes yet" empty state (none of `qa.user`'s codes are listed). |
| 2 | Open `/qr/edit/<TEXT_ID>/`. | 404 page; no name or content of `QA Text` appears. |
| 3 | Open `/qr/duplicate/<TEXT_ID>/`. | 404; the editor is not pre-filled with `qa.user`'s data. |
| 4 | Open `/qr/image/<TEXT_ID>/`. | 404; no image. |
| 5 | In the console run `fetch('/qr/delete/<TEXT_ID>/', {method: 'POST', headers: {'X-CSRFToken': document.cookie.match(/csrftoken=([^;]+)/)[1]}}).then(r => r.status)`. | `404`. |
| 6 | In the console run `fetch('/qr/edit/<TRACKED_ID>/submit/', {method: 'POST', headers: {'X-CSRFToken': document.cookie.match(/csrftoken=([^;]+)/)[1], 'Content-Type': 'application/x-www-form-urlencoded'}, body: 'name=hijacked'}).then(r => r.status)`. | `404`. |
| 7 | Open `/qr/edit/not-a-uuid/`. | 404 (no server error). |

### QR-25 Creating QR codes costs no credits

**Purpose:** QR codes are free, even with a zero balance.
**Preconditions:** logged in as `qa.broke@example.com` (0 credits), from QR-24.

| # | Action | Expected result |
|---|---|---|
| 1 | Go to `/account/`. | The balance reads "0 credits". |
| 2 | Create a Text QR code named `Broke QR` with content `free`. | Saved; `/qr/` lists only `Broke QR`. No "insufficient credits" message. |
| 3 | Create a tracked URL QR code named `Broke tracked` with content `http://localhost:8765/`. | Saved; its short URL redirects to the home page. |
| 4 | Go to `/account/` and `/billing/credits/`. | Still "0 credits" / "Current balance: 0 credits"; no QR-related transaction. |
| 5 | Log out; sign in as `qa.user@example.com`. Go to `/account/`. | "100 credits", unchanged by everything in this file. |
| 6 | Go to `/qr/`. | `qa.user`'s codes are all still there (`QA Text` included: QR-24's delete attempt did nothing) and `qa.broke`'s are not listed. `QA Tracked (renamed)` still has its original name. |

### QR-26 Mobile layout

**Purpose:** the dashboard and editor work at phone width.
**Preconditions:** logged in as `qa.user`; resize the viewport to 375×812.

| # | Action | Expected result |
|---|---|---|
| 1 | Open `/qr/`. | No horizontal scrolling. Heading and **New QR code** fit; long content lines (e.g. the Unicode row) are truncated with an ellipsis instead of overflowing. |
| 2 | Open the "⋮" menu of the bottom row. | The menu opens fully visible (above the button if there's no room below) and its items are tappable. |
| 3 | Open `/qr/create/`. | Fields stack vertically; Type, Format and Track are on separate lines; Preview, the preview box, Cancel and Save are all reachable without horizontal scrolling. |
| 4 | Fill Name `Mobile`, content `mobile test`, tap **Preview**, then **Cancel**. | The preview renders in its box; nothing is created. |
| 5 | Restore the desktop viewport. | — |

### QR-27 Dialogs keep the keyboard inside

**Purpose:** both dashboard dialogs (the enlarged preview and the delete confirmation) are modal
for the keyboard too: Tab and Shift+Tab never reach the page behind them.
**Preconditions:** logged in as `qa.user`, on `/qr/` with at least one code (e.g. `QA Text`).

| # | Action | Expected result |
|---|---|---|
| 1 | Click the `QA Text` thumbnail to open the preview. | Focus is on the "×" close button. |
| 2 | Press `Tab`, then `Shift+Tab`. | Focus never leaves the dialog (it only holds the close button, so it stays on it). The page behind (search box, row menus) is not reachable. |
| 3 | Press `Escape`. | The dialog closes and focus is back on the thumbnail. |
| 4 | Open the "⋮" menu of `QA Text`, click **Delete**. | The delete dialog opens. |
| 5 | Press `Tab` repeatedly (4-5 times), then `Shift+Tab` repeatedly. | Focus cycles between **Cancel** and **Delete** only, in both directions, wrapping from the last to the first and back. It never lands on the search box, the sort links or a row. |
| 6 | Press `Escape`. | The dialog closes; the row is still there; keyboard focus is not lost to `<body>` (it returns to where you opened the dialog from, or at worst the page). |

### QR-28 A long URL that only fits once it is tracked

**Purpose:** the "too long for a QR code" refusal of a URL suggests tracking, and tracking really
fixes it (the code then holds the short link).
**Preconditions:** logged in as `qa.user`, on `/qr/create/`.

| # | Action | Expected result |
|---|---|---|
| 1 | Name `Long URL`, Type `URL`, Track unticked. In the console run `document.querySelector('textarea[name=url]').value = 'http://localhost:8765/?q=' + '한'.repeat(900)` (925 characters, about 2,700 bytes) and click **Preview**. | Red message: `Text / URL: That is too long for a QR code. Please shorten it or turn on "Track QR Code".` The same message under the Text / URL field. The preview box keeps the logo placeholder. |
| 2 | Click **Save**. | The same message; nothing is created. |
| 3 | Tick **Track QR Code** (a Short URL appears, as in QR-07) and click **Preview**. | The message goes and a QR code renders: it encodes the short link, which always fits. |
| 4 | Click **Save**. | Goes to `/qr/`; a new top row `Long URL` whose grey line is its `http://localhost:8765/go/<code>` short URL. |
| 5 | Open that short URL. | Redirected to the home page; the address bar holds `/?q=` followed by the 900 Hangul syllables (shown decoded or percent-encoded, both are fine). |
| 6 | Open the row's **Duplicate**. | The editor is pre-filled with the long original URL (not the short link), Track ticked, a new Short URL. Click **Cancel**: nothing is created. |

### QR-29 Duplicate keeps the format; an emoji-only name downloads as `qr-code.png`

**Purpose:** the duplicate form copies every option the editor has; the download name has a fallback.
**Preconditions:** logged in as `qa.user`; `QA SVG` exists (QR-23).

| # | Action | Expected result |
|---|---|---|
| 1 | Open the `QA SVG` menu → **Duplicate**. | Create-mode editor: Name `QA SVG`, Text / URL `https://example.com/svg`, Type `URL`, **Format `SVG`** (not reset to PNG), Track unticked and enabled, no Short URL field. |
| 2 | Set Format `PDF`, Name `👋👋`, click **Save**. | Saved; a new top row named `👋👋` with the content `https://example.com/svg`; its thumbnail loads (always a PNG). |
| 3 | Click **Download** on the `👋👋` row. | The file is saved as `qr-code.png` (a name with no letters or digits falls back to that). |
| 4 | Delete the `👋👋` row through the menu and the modal. | It disappears; `QA SVG` stays. |

### QR-30 Dark mode

**Purpose:** the QR pages are readable with the dark theme, and the public 404 page follows the
visitor's system setting.
**Preconditions:** logged in as `qa.user`.

| # | Action | Expected result |
|---|---|---|
| 1 | On `/account/`, under "Appearance", choose **Dark**. | The page turns dark at once (no reload). |
| 2 | Open `/qr/`, open a row's "⋮" menu, the enlarged preview and the delete dialog (cancel it). | Dark backgrounds with light text everywhere; the menu, the search box and the delete dialog are dark too; the QR thumbnails and the enlarged image keep their white background (a QR code must stay dark-on-light to scan). No white flash on load, no text that is dark on dark. |
| 3 | Open `/qr/create/`, choose Type `URL`, tick Track, click **Preview** with empty fields, then fill Name and content and click **Preview** again. | Inputs, selects, the read-only Short URL field and the red error lines are all legible on the dark background. |
| 4 | Open `/qr/edit/<TEXT_ID>/`. | The read-only Type box and textarea are a dimmer grey than the editable Name field, still legible. |
| 5 | With the browser (or the OS) set to prefer a dark colour scheme, open `/go/doesnotexist` (no site theme applies: the page is standalone). | Dark background, light heading, readable grey line, a visible link colour. Switch the scheme back to light and reload: a white page with dark text. |
| 6 | Back on `/account/`, choose **System** again. | The theme is back to what it was. |

### QR-31 Keyboard only

**Purpose:** everything on the dashboard and in the editor can be done without a mouse.
**Preconditions:** logged in as `qa.user`, on `/qr/` with several codes; use only the keyboard.

| # | Action | Expected result |
|---|---|---|
| 1 | Press `Tab` from the top of the page. | The first stop is "Skip to content"; then the header, the **New QR code** button, the search box, the **Newest**/**Name** sort links, and for each row its thumbnail button ("Preview <name>") and its "⋮" button ("Actions for <name>"). Every focused control shows a visible focus ring. |
| 2 | Focus a row's "⋮" button and press `Enter`. | Its menu opens. `Tab` moves through **Edit**, **Duplicate**, **Share**, **Download** and **Delete** in that order. |
| 3 | Press `Escape` while the menu is open. | Record whether it closes. (The dashboard's script closes the menu on an outside click only; if `Escape` leaves it open, record it as an accessibility observation, not a failure.) |
| 4 | Open the menu again, `Tab` to **Delete** and press `Enter`. | The delete dialog opens with focus on **Cancel** (QR-27 covers the focus trap). Press `Escape`: it closes and focus returns to that row's "⋮" button. |
| 5 | Focus a thumbnail button and press `Enter`. | The enlarged preview opens with focus on "×"; `Enter` on "×" closes it and focus returns to the thumbnail. |
| 6 | In the search box type `text` and press `Enter`. | The list is filtered (`/qr/?q=text`). |
| 7 | Go to `/qr/create/` and `Tab` through the form. | Name, Text / URL, Type, Format, Track QR Code (skipped while disabled), **Preview**, **Cancel**, **Save**, in that order. With Type `URL` selected by the arrow keys, Track becomes reachable and `Space` ticks it. |
| 8 | Fill Name and content, `Tab` to **Preview** and press `Enter`, then `Tab` to **Save** and press `Enter`. | The preview renders, then the code is saved and the browser goes to `/qr/`. Delete this code afterwards (menu → **Delete** → **Delete**). |

### QR-32 Share a QR code with a link

**Purpose:** the owner makes a share link from the dashboard and copies it (issue #5).
**Preconditions:** logged in as `qa.user`, on `/qr/` with `QA Text` (`TEXT_ID`) and `QA Tracked` (`TRACKED_ID`).

| # | Action | Expected result |
|---|---|---|
| 1 | Open the "⋮" menu of the `QA Text` row and click **Share**. | The menu closes and a dialog opens over the page, titled `Share "QA Text"`, with the focus inside it. It says "Anyone who has a link can see this QR code and download it, without signing in. The link always shows the code as it is now. 0 of 10 active links used.", has **Label (optional)** ("Only you see it."), **Expires on (optional)**, a **Create link** button and "This QR code has no links yet.". |
| 2 | Type `for the printer` in **Label** and click **Create link**. | The list shows one link: an **Active** pill, the label, an address `http://localhost:8765/q/<22 letters, digits, - or _>` (`LINK_URL`), **Copy link**, "Created <time>", **Edit** and **Revoke** (red). The counter reads "1 of 10 active links used". |
| 3 | Click **Copy link**. | The button reads **Copied** for about two seconds, then **Copy link** again; pasting elsewhere gives exactly `LINK_URL`. |
| 4 | Click **Close** (or press `Escape`). | The dialog closes (the focus goes back to the control that opened it when that is still on screen). The `QA Text` row now has a small link icon next to its name (tooltip "Shared"); the other rows have none. |
| 5 | Open `/qr/edit/<TEXT_ID>/`. | A **Share** button sits next to the heading; it opens the same dialog with the same link. `Escape` closes it. |
| 6 | Open `/qr/create/` and `/qr/duplicate/<TEXT_ID>/`. | No **Share** button there (nothing to share yet). |
| 7 | Reload `/qr/` and open `/qr/share/<TEXT_ID>/` directly in the address bar. | The browser goes to `/qr/?share=<TEXT_ID>` and the share dialog opens by itself. `/qr/?share=nonsense` and `/qr/?share=<another user's id>` open nothing. |
| 8 | In the dialog, create 9 more links (labels `l2` to `l10`), then try an 11th. | The counter reaches "10 of 10 active links used"; the 11th is refused with the red line "A QR code can have at most 10 active links." and no link is added. |
| 9 | As `qa.broke` (0 credits), share one of their own codes. | It works: sharing costs no credits (QR-25 and the Costs page say the same). |

### QR-33 The public page

**Purpose:** anyone with the link sees the code, with no account (issue #5).
**Preconditions:** QR-32 done (`LINK_URL` for `QA Text`); a second browser profile or a private window that is **not** signed in.

| # | Action | Expected result |
|---|---|---|
| 1 | In the private window open `LINK_URL`. | No login. The heading `QA Text`, the QR code image (white background), a **Download PNG** button, and the section **This QR code contains** with `Hello from QA` and a **Copy** button. Nothing about the owner (no name, no email), the label `for the printer`, the scan count or other links. The tab title is `QA Text - ZipTrigo`. |
| 2 | Look at the response headers (developer tools) of the page and of `LINK_URL/image`. | Both: `Cache-Control: no-store, private`, `Referrer-Policy: strict-origin`, `X-Robots-Tag: noindex, nofollow`; the page has `<meta name="robots" content="noindex">`. The image is `image/png` with `X-Content-Type-Options: nosniff`. |
| 3 | Click **Download PNG**. | The browser saves `qa-text.png`; it is a PNG that scans to `Hello from QA`. |
| 4 | Click **Copy**. | The button reads **Copied**; pasting gives `Hello from QA`. |
| 5 | Open `LINK_URL` with a trailing slash, then `LINK_URL/image?download=1`. | Both work; the second downloads the PNG as an attachment. |
| 6 | Create a link for `QA Tracked` (QR-32 step 1 to 2) and open it as the visitor. | The heading, the image and **This QR code leads to** `http://localhost:8765/?from=qr` as plain text (not a link) with the note "Shown as text, not a link: check where it goes before you open it." The page does not show the `/go/<code>` short link. |
| 7 | Scan (or decode) the downloaded `QA Tracked` image and open the short URL it encodes; then, as `qa.admin`, look at the code in `/admin/qr_code/qrcode/`. | The short link redirects to the home page and the code's **Scan count** went up by one: a scan of a shared copy counts like any other. |
| 8 | Create a link for `QA Plain URL` (an SVG code) and open it. | It shows a PNG image like the dashboard does. |
| 9 | Rename `QA Text` to `QA Text (renamed)` as the owner (`/qr/edit/<TEXT_ID>/`) and reload `LINK_URL` as the visitor. | The visitor's page shows the new name: a link shows the code as it is now. Rename it back. |
| 10 | Open the `Injection QR` code's link (create the code first if it is gone). | The name and content show as text; no alert dialog appears. |
| 11 | Signed in as `qa.user`, open `LINK_URL` in the normal window. | The same public page (no nav of the QR app, no edit controls). |
| 12 | In the shell, deactivate the owner (`python tests_e2e/ai/manage.py shell -c "from apps.accounts.models import User as U; u=U.objects.get(email='qa.user@example.com'); u.deactivate()"`, use the seeded address) and reload `LINK_URL` and `LINK_URL/image` as the visitor; then set `status='ACTIVE'` and `is_active=True` again with `U.objects.filter(...).update(...)`. | While the owner is deactivated both show the neutral unavailable page (404); after reactivating they work again. |

### QR-34 Expiry and revoking

**Purpose:** a link can be given an end date and killed at any time (issue #5).
**Preconditions:** QR-32 done, the dialog open for `QA Text`.

| # | Action | Expected result |
|---|---|---|
| 1 | Create a link with **Expires on** set to yesterday (type the date). | The red line `Expires on: Choose a date that has not passed.` (also under the field); no link is added. |
| 2 | Type `tomorrow` in the field and create. | The red line `Expires on: Enter a date.`. |
| 3 | Create a link with **Expires on** = a date 10 days ahead. | The new link says "Expires <date>" (in your date format). The visitor's page for it shows "Expires <date and time>". |
| 4 | Click **Edit** on a link, change the label to `printer 2`, clear the date, **Save link**. | The list shows `printer 2` and no expiry. |
| 5 | Click **Edit** on a link and type or paste 150 characters into **Label**. | The browser stops at 100 characters (`maxlength`); the server's own limit, `Label: Keep the label to 100 characters or fewer.`, is unit-tested. |
| 5b | Paste a label containing a zero-width space or a right-to-left override (e.g. `\u200b` before `Ann`) into **Label** and save. | The saved label has those characters removed (only visible text remains). |
| 6 | Click **Revoke** on `LINK_URL`'s link. | A confirmation: "Revoke this link?" / "Anyone who has it can no longer see the QR code through it. This can't be undone: you can make a new link." with **Cancel** and **Revoke**. `Escape` closes only the confirmation, not the share dialog. |
| 7 | Click **Revoke** and confirm (also try it from the editor's **Share** button: you return to the editor with the dialog open). Revoking works even right after 60 link changes in a minute (it is never rate limited). | The page reloads (the dashboard, or the editor when you started there) with the share dialog open again and a green "Link revoked."; the link is greyed with a **Revoked** pill, no **Copy link**, **Edit** or **Revoke**. If it was the only active link, the row's link icon is gone. |
| 8 | As the visitor reload `LINK_URL` and `LINK_URL/image`. | Both show the neutral page **This QR code isn't available** ("It may have been removed by its owner.", a link "Go to ZipTrigo") with HTTP 404 (QR-35). |
| 9 | Reload the dialog. | The revoked link has no **Edit** or **Revoke** button and no **Copy link** (a replayed edit POST answers `This link was revoked and can no longer be changed.`, a replayed revoke is harmless). |
| 10 | In the shell (`python tests_e2e/ai/manage.py shell -c "from datetime import timedelta; from django.utils import timezone; from apps.qr_code.models import QRShareLink as L; L.objects.filter(label='l2').update(expires_at=timezone.now()-timedelta(minutes=1))"`) expire the link labelled `l2`. | The dialog (reload) greys it with an **Expired** pill; the visitor gets the neutral page; **Edit** is still offered, and extending the date makes it active again. |

### QR-35 Every dead link looks the same

**Purpose:** a visitor can't tell why a link doesn't work (issue #5).
**Preconditions:** QR-32 to QR-34 done; the visitor window.

| # | Action | Expected result |
|---|---|---|
| 1 | Open `/q/doesnotexist`, a revoked link, an expired link, and `/q/` followed by 200 letters. | The same page for all: **This QR code isn't available**, status 404, the same headers as QR-33 step 2. View source: the four pages are byte for byte identical. |
| 2 | As the owner, delete `QA Text` (QR-21 steps), then open its still-active link and its `/image`. | The same neutral page and 404: deleting a code stops every link of it. |
| 3 | Compare with `/go/doesnotexist`. | The same page again. |
| 4 | The words "revoked", "expired", "deleted" and "taken down" appear nowhere in any of those pages. | As described. |

### QR-36 Access control for sharing

**Purpose:** only the owner shares a code (issue #5).
**Preconditions:** `qa.user` has `QA Tracked` (`TRACKED_ID`) with a link `L_ID`; the other user is `qa.broke`.

| # | Action | Expected result |
|---|---|---|
| 1 | Logged out, open `/qr/share/<TRACKED_ID>/` and POST to `/qr/share/<TRACKED_ID>/new/`. | Redirected to the login page (`next` = the path). The public pages need no login (QR-33). |
| 2 | As `qa.broke`, open `/qr/share/<TRACKED_ID>/`, `/qr/?share=<TRACKED_ID>`, and replay the create, edit and revoke POSTs for `qa.user`'s code and link. | The share URLs answer 404; `/qr/?share=...` opens nothing. No link is created, changed or revoked. |
| 3 | As `qa.user`, replay the edit POST of a link of `QA Tracked` under the URL of `QA Text`. | 404. |
| 4 | Look at `/qr/` as `qa.broke`. | No link icons: another user's links are invisible. |

### QR-37 Sharing: mobile, dark mode and keyboard

**Purpose:** the share dialog and the public page work at phone width, in dark mode and with the keyboard (issue #5).
**Preconditions:** a QR code with an active link and a revoked one.

| # | Action | Expected result |
|---|---|---|
| 1 | At 375 px wide open the share dialog and the public page. | The dialog fits with margins and scrolls inside itself when long; the address wraps instead of overflowing; the buttons wrap. The public page's image scales down; no horizontal scroll. |
| 2 | Choose **Dark** on `/account/` and look at both again. | Dark backgrounds with light text; the dialog, its fields, the pills and the confirmation are legible; the QR image keeps its own colours on white. |
| 3 | With the keyboard only: focus a row's "⋮", `Enter`, `Tab` to **Share**, `Enter`. | The dialog opens with focus on its first control (**Close**); `Tab` walks through its fields and buttons; `Escape` closes it. |
| 4 | Inside the dialog `Tab` to **Revoke**, `Enter`, then `Tab`/`Shift+Tab`. | The confirmation takes the focus (on **Cancel**); `Escape` closes only it. |
| 5 | Make an error (a past date) and look at the field. | The message is repeated under **Expires on** with a red border and `aria-invalid="true"`; the focus moves to the field. |

### QR-38 Send a share link by email

**Purpose:** the owner emails a link to people from the share dialog (issue #5).
**Preconditions:** QR-32 done (`QA Text` has an active link `LINK_URL` labelled `for the printer`); `qa.user` has a confirmed email; real-services mode so the emails are delivered (`emails.md` "Reading mail"). Use addresses `a.<R>@example.com` and `b.<R>@example.com` (`R` a random suffix).

| # | Action | Expected result |
|---|---|---|
| 1 | Open the share dialog of `QA Text`. | The active link has **Edit**, **Revoke** and **Send by email**; no "Emailed to" list yet. A revoked or expired link has no **Send by email**. |
| 2 | Click **Send by email**. | A form opens under the link: **Email addresses** ("One per line, or separated by commas. A link can be emailed to at most 20 people."), **Message (optional)** and **Send email**. |
| 3 | Type `A.<R>@Example.com, b.<R>@example.com` in the addresses and `Here is the door code` in the message; click **Send email**. | The dialog is redrawn with the green line "Emailed the link to 2 people." and an **Emailed to** list under the link: both addresses (the first with its domain lower-cased: `A.<R>@example.com`), each "sent <time>" and a **Resend** button. The form is closed again. |
| 4 | `/__e2e__/mail/?to=a.<R>@example.com` (reload until it arrives). | One email: subject exactly `A QR code was shared with you`; body `<owner's name or "Someone"> shared the QR code "QA Text" with you.`, the message, `LINK_URL`. MAIL-31 checks the rest. Same for `b.<R>@`. |
| 5 | Open the link from the email in a private window. | The public page of QR-33. |
| 6 | Open the form again and type `a.<R>@example.com` once more (any case) and send. | Refused: the red line `Email addresses: "a.<R>@example.com" was emailed recently; you can resend in N minutes.` (N is 10 or 9), also under the field; nobody is added or emailed. |
| 7 | Open the form and send an empty box. | `Email addresses: Enter at least one email address.` |
| 8 | Send `not-an-address`, then `ok.<R>@example.com, nope`. | `Email addresses: "not-an-address" is not a valid email address.` and `Email addresses: "nope" is not a valid email address.`; the first request added nobody, and nor did the second (ok.<R> was not emailed). |
| 9 | Type 501 characters in **Message**. | The browser stops at 500 (`maxlength`); the server's own limit, `Message: Keep the message to 500 characters or fewer.`, is unit-tested. |
| 10 | Send `c.<R>@example.com` with a message of three lines separated by blank lines, containing `<b>x</b>`. | Sent. In the mailbox the text body keeps the line breaks (at most one blank line between paragraphs) and the HTML source shows `&lt;b&gt;x&lt;/b&gt;`. |

### QR-39 Resend, the cooldown and the cap

**Purpose:** a person can be emailed again, no sooner than every 10 minutes, and a link has at most 20 people (issue #5).
**Preconditions:** QR-38 done.

| # | Action | Expected result |
|---|---|---|
| 1 | Click **Resend** next to `b.<R>@example.com` right away. | The red line `This person was emailed recently; you can resend in N minutes.` (N is 10 or 9) in the dialog's message slot; no new email. |
| 2 | Shell step: make that person's last send old: `python tests_e2e/ai/manage.py shell -c "from datetime import timedelta; from django.utils import timezone; from apps.qr_code.models import QRShareRecipient as R; R.objects.filter(email__startswith='b.').update(last_sent_at=timezone.now()-timedelta(minutes=11))"`; click **Resend** again. | The green line `Sent the email to b.<R>@example.com again.`; a second email (same subject, the link, **no message**: a resend does not carry the first message) arrives; the "sent <time>" of that person is updated. |
| 3 | Double-click **Resend** quickly on another person whose cooldown has passed (repeat step 2 for them). | One email only (the button disables while the request runs; a second request would be refused with the cooldown message). |
| 4 | Send one address list with 18 more addresses (`p1.<R>@` to `p18.<R>@example.com`) in one go: 21 people in all with the 3 earlier ones. | Refused: `Email addresses: A link can be emailed to at most 20 people.`; nobody added or emailed. |
| 5 | Send 17 addresses (`p1.<R>@` to `p17.<R>@example.com`). | Sent: "Emailed the link to 17 people." The list has 20 people. |
| 6 | Send one more new address. | Refused with the same cap message. Typing an address already on the link (after its cooldown) is still allowed: it is a resend. |
| 7 | Create a second link for `QA Text` and send it to `a.<R>@example.com`. | Allowed: the 20 people are per link; the second link has its own list. |

### QR-40 Sending: who may and when it can't

**Purpose:** only an owner with a confirmed email can send, only a working link is sent, and other users' links can't be reached (issue #5).
**Preconditions:** QR-38 done; shell access.

| # | Action | Expected result |
|---|---|---|
| 1 | Shell step: un-confirm the owner (`python tests_e2e/ai/manage.py shell -c "from apps.accounts.models import User as U; U.objects.filter(email='qa.user@example.com').update(email_confirmed=False)"`) and reopen the dialog. | Under the active links there is no **Send by email** button or **Resend**; the text `Confirm your email address to send a QR code by email.` is shown once above the list of links (not once per link), with a link "Resend the confirmation email" that opens `/account/resend-confirmation/`. A replayed send POST answers `Confirm your email address to send a QR code by email.` (422) and nothing is emailed. Copying and creating links still work. |
| 2 | Re-confirm the owner (`email_confirmed=True`). | The form is back. |
| 3 | Revoke the link and replay the send POST (`POST /qr/share/<TEXT_ID>/<link id>/email/` with `emails=x@example.com`, using the page's CSRF token). | `This link no longer works.` (422); nothing is emailed. The revoked link shows its "Emailed to" list without **Resend** buttons. |
| 4 | Send to someone, then immediately revoke the link (or expire it with QR-34 step 10) before the email is delivered; or, in the shell, delete the person's row before it goes out. | No email arrives for a link that no longer works or a person who was removed: the queued email checks again when it runs. (Hard to time in real mode: mark **BLOCKED: timing** if the email was already delivered.) |
| 4b | Budget: in the shell lower the rule is not possible, so send single emails until the 429, then with fewer hits left than addresses (e.g. 29 used, 5 addresses typed) submit once. | The 429 line `Too many requests. Please try again in N minutes.` appears and **no hit was spent** and nobody was emailed (a later single send still fits the remaining hit). (Hard to reach by hand: mark **BLOCKED: budget** if it takes too long; unit-tested.) |
| 5 | As `qa.broke`, replay `POST /qr/share/<TEXT_ID>/<link id>/email/` and `.../recipients/<person id>/resend/` for `qa.user`'s link. | 404 for both; nobody is emailed. |
| 6 | Logged out, replay the same POSTs. | Redirected to the login page; nothing is sent. |
| 7 | As the owner, replay the resend POST with a person id that belongs to another of the owner's links. | 404. |
| 8 | Delete `QA Text` (QR-21) and open the admin's QR share recipients (ADM-68 step 9). | The people of its links are gone with it. |

### QR-41 Report a shared QR code

**Purpose:** anyone with the link can report the QR code, without an account (issue #5).
**Preconditions:** QR-32 and QR-33 done (`QA Tracked` has an active link `TRACKED_LINK`); the visitor window (not signed in); `qa.user` is the owner in the normal window.

| # | Action | Expected result |
|---|---|---|
| 1 | Visitor: open `TRACKED_LINK`, scroll to the bottom and click **Report this QR code**. | A small flag button, collapsed by default; clicking it opens a form: **Reason** (Choose a reason, Phishing or scam, Malware, Spam, Illegal content, Copyright infringement, Other), **Details (optional)**, **Your email (optional)** ("In case we need to follow up. We won't share it with the owner of the QR code.") and **Submit report**. |
| 2 | Click **Submit report** with no reason. | The browser's "Please select an item in the list" (the field is required) and nothing is sent. (With JavaScript off, or by posting without `reason`, the server answers 422 and the form comes back with the red line `This field is required.` under **Reason**; unit-tested.) |
| 3 | Choose **Phishing or scam**, details `Asks for bank details`, click **Submit report**. | The form is replaced, with no reload, by a green **Thank you, your report has been submitted.** In the shell the report exists (`python tests_e2e/ai/manage.py shell -c "from apps.qr_code.models import QRShareReport as R; print(list(R.objects.values('reason', 'status', 'reporter_ip', 'reporter_email')))"`): `phishing`, `pending`, the visitor's address (`127.0.0.1` here), an empty email. |
| 4 | Reload `TRACKED_LINK`, open the form again and submit another report right away. | A red `You already reported this QR code recently. Thank you.`; no second report (a repeat from the same address within 10 minutes). After 10 minutes, or from another address, it works. |
| 5 | Type `not-an-email` in **Your email** and submit with a reason. | A red error under the email field (`Enter a valid email address.`); nothing is stored. A valid address is stored with the report. |
| 6 | Paste 2,001 characters into **Details**. | The browser stops at 2,000 characters (`maxlength`); the server's own limit (`Ensure this value has at most 2000 characters ...`) is unit-tested. |
| 7 | With the form open, the owner revokes a second link of the code made for this step (QR-34 step 7), and the visitor, on that link's page, then submits. | The page reloads by itself into **This QR code isn't available** (404, QR-35); no report is stored. |
| 8 | In devtools replay the report POST of a made-up code (`POST /q/doesnotexist/report`) and of a revoked link. | The same neutral 404 page; nothing is stored. A GET of `/q/<code>/report` is a 405. |
| 9 | Look at the page's report `<form>` (view source). | It has `action="/q/<code>/report"`, a CSRF token and works without JavaScript: a plain POST answers the whole public page with the thank-you box open, with the headers of QR-33 step 2. |
| 10 | The owner opens the dashboard and the share dialog. | Nothing about the report appears: the owner is never told who reported, or that anyone did. |
| 11 | (Only with rate limiting on, which this server has off: otherwise skip, unit-tested.) Submit 6 reports from one address within an hour. | The sixth is `Too many requests. Please wait a moment and try again.` (429) and nothing is stored. |

### QR-42 A takedown, as the owner and the visitor see it

**Purpose:** staff take a QR code down; its links die, the owner is told why and can still use the code, and a tracked code's short link keeps working (issue #5).
**Preconditions:** QR-32, QR-33 and QR-41 done: `QA Text` has an active link `LINK_URL` and `QA Tracked` (`TRACKED_ID`) has one (`TRACKED_LINK`, reported in QR-41); `qa.admin` takes `QA Tracked` down in the admin (ADM-69 step 4) with the reason `Pretends to be a bank` and the internal note `ticket 77`; the visitor window; `/__e2e__/mail/` open.

| # | Action | Expected result |
|---|---|---|
| 1 | Visitor: reload `TRACKED_LINK` and `TRACKED_LINK/image`. | **This QR code isn't available** and 404, byte for byte the page of QR-35 (the words "taken down" appear nowhere). `LINK_URL` (of `QA Text`) still works. |
| 2 | Owner: open `/qr/`. | The `QA Tracked` row shows, under its content, a red line with a ban icon: "Taken down. Pretends to be a bank Its share links no longer work." The link icon next to its name is gone. The internal note `ticket 77`, the admin's name and any reporter appear nowhere. |
| 3 | Owner: open the "⋮" menu of that row, click **Share**. | The dialog opens with a red box **This QR code was taken down.** with the reason `Pretends to be a bank` and "Its links no longer work and it can't be shared or emailed. You can still edit, download or delete it, and revoke the links below." There is **no** create form, and the link under it has a red **Not working** pill, its address as text, **Revoke** only (no **Copy link**, **Edit**, **Send by email**, no **Resend** next to people it was emailed to). |
| 4 | Click **Revoke** and confirm. | It works (a green "Link revoked."); the link becomes **Revoked**. Revoking is never refused, whatever the code's state. |
| 5 | In devtools replay `POST /qr/share/<TRACKED_ID>/new/`, `.../<link id>/edit/` (`label=x`) and `.../<link id>/email/` (`emails=x@example.com`). | Each answers 422 with the red line `This QR code was taken down and cannot be shared.`; no link is made or changed and nothing is emailed (`/__e2e__/mail/?to=x@example.com` has nothing). |
| 6 | Owner: open `/qr/edit/<TRACKED_ID>/`, rename it to `QA Tracked 2`, save; open **Download**. | Everything still works: the code can be viewed, renamed, downloaded and deleted, and it stays taken down (the red line stays). |
| 7 | Take the short URL of `QA Tracked` (its `http://localhost:8765/go/<code>`) and open it in the visitor window. | It still redirects to the destination: **a takedown does not stop `/go/<code>`** (it is printed on physical codes). The code's scan count in `/admin/qr_code/qrcode/` goes up by one. |
| 8 | Owner: open the "⋮" menu of the `QA Tracked` row; then open `/qr/duplicate/<TRACKED_ID>/` directly. | The menu has **Edit**, **Share**, **Download** and **Delete** but **no Duplicate** (every other row still has it). The direct URL redirects to `/qr/` with the red flash `This QR code was taken down and cannot be copied.` and nothing is created. |
| 9 | `/__e2e__/mail/?to=qa.user@example.com` and the bell. | The owner got no email and no notification about the takedown. |
| 10 | Owner: delete `QA Tracked 2` (QR-21). | It works; a taken-down code can be deleted. |
| 11 | As `qa.broke`, open `/qr/share/<TRACKED_ID>/` (QA User's taken-down code). | 404, as for any other user's code. |

### QR-43 The share links over the API and the `inv qrcode` CLI

**Purpose:** the command line over the same share links (issue #5, PR 4).
**Preconditions:** QR-32 and QR-38 done; a shell in the repo root; `QRCODE_PASSWORD='QaE2e-Passw0rd!'` exported; the test server of the README (`API_BASE_URL=http://localhost:8765`); `inv qrcode <command> local ...` with `local` the environment; `QA_ID` the id of `QA Text` (`inv qrcode list local` shows the first 8 characters, `/qr/edit/<id>/` the whole id); real-services mode for step 3.

| # | Action | Expected result |
|---|---|---|
| 1 | `inv qrcode links local <QA_ID>` before logging in; then `inv qrcode login local qa.user@example.com`; then `links` again. | The first exits 1 (`Not authenticated on local. Please login first.`); after the login a table (id, url, label, state, expires, emailed to) lists the links of QR-32 and QR-38, the one with the people showing their addresses. A label with `[bold]` in it is printed as typed. |
| 2 | `inv qrcode share local <QA_ID>`, then `--label "for Sam" --expires 2026-12-31`, `--expires "2026-12-31 18:00"` and `--expires soon`. | `Made a share link: <url>` and `Link ID: <id>`; a date means the end of that day in your time zone, a date-time that moment; `soon` is a usage error (exit 2) before any request. Open a printed url as the visitor: the public page of QR-33. |
| 3 | `inv qrcode share local <QA_ID> --to c.<R>@example.com --to d.<R>@example.com --message "Door code inside"`; then `--message hi` without `--to`. | The link is printed, then `Emailed the link to c.<R>@example.com, d.<R>@example.com.`; both emails arrive at `/__e2e__/mail/` (subject `A QR code was shared with you`). `--message` alone is a usage error (exit 2). |
| 4 | Make links until the cap: `inv qrcode share local <QA_ID>` repeatedly. | At the tenth active link (counting those of the earlier cases) it exits 1 with `400: A QR code can have at most 10 active links.` and nothing is made. |
| 5 | `inv qrcode unshare local <QA_ID> <link id>` (answer `n`, then `y`), then `links` again, then `unshare ... --yes` on the same link. | `n` exits 1 `Cancelled; nothing was changed.` and keeps the link; `y` says `Revoked the link.` and the link is `revoked` in the table, and its url is the neutral page (QR-35) for the visitor; revoking again works (idempotent). |
| 6 | With the access token of `qa.user`, `curl -H "Authorization: Bearer <token>" $API_BASE_URL/api/qr/<QA_ID>` (get the token from `~/.qrcode_token.local`). | The QR code JSON now has `has_active_link` (true while a link works), `taken_down` false and `takedown_reason` null. After ADM-69's takedown of a code, its JSON has `taken_down` true and the reason, never the internal note; `share` on it exits 1 with `409: This QR code was taken down and cannot be shared.`, while `links` and `unshare` still work. |
| 7 | Run `inv qrcode links local <id of a code of qa.broke>` and `inv qrcode unshare local <QA_ID> <a link id of another code> --yes`. | Both exit 1 with `404:` (another user's code, or a link that isn't that code's): nothing is changed. |

## Out of scope

- Size, border, error correction and colours: the editor has no controls for them (JWT API only,
  unit-tested in `apps/qr_code/tests/test_render_options.py`).
- The SVG and PDF files the format choice produces: the site only ever serves a PNG
  (`/qr/image/<id>/`); the stored file is not reachable from the web UI.
- The `/go/<code>` per-IP rate limit (`QR_REDIRECT_IP`): rate limiting is off on this server.
- The JWT API `/api/qr/` and `inv qrcode`, except the share links (QR-43): unit-tested.
- The per-user budget of emailed links (`QR_SHARE_EMAIL_USER`, 30 an hour): rate limiting is off on this server, so it is
  unit-tested (`apps/qr_code/tests/test_share_emails.py`).
- The per-IP and per-code budgets of reports (`QR_SHARE_REPORT_IP`, 5 an hour, and `QR_SHARE_REPORT_CODE`, 20 an hour): rate
  limiting is off on this server, so they are unit-tested (`apps/qr_code/tests/test_share_reports.py`).
