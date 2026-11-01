# QR codes

## Scope

The QR code product's web UI (`apps/qr_code`):

- the dashboard `/qr/` (empty state, list, thumbnails from `/qr/image/<id>/`, the enlarged preview
  modal, search, sort by name, the per-row actions menu);
- the editor `/qr/create/` (name, Text / URL content, type, format, "Track QR Code" with the
  server-issued short URL, live preview, validation) and `/qr/edit/<id>/` (rename only);
- duplicate (`/qr/duplicate/<id>/`) and delete (confirmation modal);
- the public short links `/go/<code>` and `/go/<code>/`, and their scan count;
- access control: logged-out visitors, another user's QR code ids;
- that creating QR codes costs no credits.

Not in the web UI, so out of scope here: size, border, error correction and colours (only the JWT
API at `/api/qr/` sets them; covered by unit tests). There is no "Download" button anywhere in the
QR UI; QR-23 checks what is offered instead.

## Preconditions

- The test server is running at `http://localhost:8765`, freshly restarted (empty database), as
  described in [`README.md`](README.md). Rate limiting is off.
- The server's `BASE_URL` is `http://localhost:8765`, so every short URL reads
  `http://localhost:8765/go/<code>` (8 letters/digits, case-sensitive).
- Cases run in order; later ones reuse QR codes created earlier.
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
labelled "QR code actions") and read the `href` of **Edit**, `/qr/edit/<uuid>/`.

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
| 1 | Go to `/` and click **QR Codes** in the top navigation (qrcode icon). | Lands on `/qr/`. Browser tab title "Dashboard - ZipTrigo". |
| 2 | Look at the page. | Heading "QR codes"; a **New QR code** button (plus icon) at the top right; a search box with placeholder "Search by name" and no clear (×) button visible; a list header "Sort by" with **Newest** (marked current) and **Name** links, and no checkboxes; one row reading "No QR codes found.". No broken images, no raw `{{ }}`/`{% %}`, no console errors. |
| 3 | Click **New QR code**. | Goes to `/qr/create/`. |

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
| 6 | Click **Cancel**. | Back on `/qr/` with nothing created ("No QR codes found."). |

### QR-04 Preview validation messages

**Purpose:** the Preview button reports server-side validation errors in the message box above the form.
**Preconditions:** on `/qr/create/`.

| # | Action | Expected result |
|---|---|---|
| 1 | Leave everything empty and click **Preview**. | A red message appears above the form: "Name: This field is required." and on the next line "Text / URL: Please provide the text or URL to encode.". The preview box still shows the logo placeholder. |
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
| 5 | Click **Cancel**. | `/qr/` still says "No QR codes found." (preview saved nothing). |

### QR-06 Create a static Text QR code

**Purpose:** the basic create flow, the browser-side required fields, and the thumbnail.
**Preconditions:** on `/qr/create/`, no QR codes yet.

| # | Action | Expected result |
|---|---|---|
| 1 | Leave Name empty, type `Hello from QA` in Text / URL, click **Save**. | The browser blocks the submit with its native "Please fill out this field." bubble on Name; no request is sent and nothing is created. |
| 2 | Name `QA Text`, Text / URL `Hello from QA`, Type `Text`, Format `PNG`, click **Save**. | The Save button is briefly disabled, then the browser goes to `/qr/`. |
| 3 | Look at the dashboard. | One row: a 48×48 thumbnail, the name `QA Text` and, below it in small grey text, `Hello from QA`. "No QR codes found." is gone. |
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
| 1 | Hover the `QA Text` thumbnail. | It scales up slightly. |
| 2 | Click it. | A dark overlay opens with a large QR code image on a white background, and a "×" close button (labelled "Close QR code preview") at the top right. The image is `/qr/image/<TEXT_ID>/`. |
| 3 | Click **×**. | The overlay closes. |
| 4 | Open it again and press **Escape**. | It closes. |
| 5 | Open it again and click the dark area outside the image panel. | It closes. Clicking on the dark panel itself (around the image) does not close it. |

### QR-11 Follow the short link

**Purpose:** `/go/<code>` and `/go/<code>/` redirect to the destination; unknown codes are a plain 404.
**Preconditions:** `CODE` from QR-07/QR-08.

| # | Action | Expected result |
|---|---|---|
| 1 | Open `SHORT_URL` (`http://localhost:8765/go/<CODE>`) in the browser. | Redirected (302) to `http://localhost:8765/?from=qr`: the ZipTrigo home page loads. |
| 2 | Open `http://localhost:8765/go/<CODE>/` (trailing slash). | Same redirect to `/?from=qr`. |
| 3 | Open `/go/<CODE>` with the case of one letter flipped (e.g. `a` ↔ `A`). | 404 page in the site layout: "This QR code isn't available" (codes are case-sensitive). |
| 4 | Open `/go/doesnotexist`. | The same 404 page, "This QR code isn't available". |
| 5 | Open `/go/`. | 404 (Django "Page not found", since the server runs with `DEBUG=True`); no redirect. |
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
| 1 | Click the "⋮" button on the `QA Tracked` row. | A menu opens with **Edit**, **Duplicate** and **Delete** (red). Clicking elsewhere on the page closes it; clicking "⋮" on another row closes this one and opens that one. |
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
| 5 | Search `zzz`. | "No QR codes found." |
| 6 | Search `plain`, then click the **Name** sort link. | URL is `/qr/?sort=name&q=plain`; still only `QA Plain URL`; **Name** is now the current sort. |
| 7 | Clear the search, then click **Name**. | Alphabetical: `QA Plain URL`, `QA Text`, `QA Tracked (renamed)`, `QA Tracked copy`. |
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
| 1 | Open the `QA Plain URL` menu and click **Delete**. | The menu closes and a modal opens: title "Delete QR Code", text "Are you sure you want to delete QA Plain URL?" (name emphasised: brighter, medium weight), buttons **Cancel** and **Delete** (red). |
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
| 2 | Open its short URL while logged in. | 404 page in the site layout, "This QR code isn't available"; not redirected to `/?from=qr`. |
| 3 | Log out and open the same short URL. | The same 404 page, with no login redirect. |
| 4 | Open `SHORT_URL` (`QA Tracked (renamed)`, not deleted). | Still redirects to `/?from=qr`. |
| 5 | Sign back in as `qa.user`. | — |

### QR-23 What the user can download

**Purpose:** document the image the UI offers (there is no Download button).
**Preconditions:** logged in as `qa.user`.

| # | Action | Expected result |
|---|---|---|
| 1 | Look for a "Download" button or link on `/qr/`, in the row menus, in the preview modal and on the Edit page. | None exists (only Edit, Duplicate, Delete). Report as an observation: the SVG/PDF format chosen at creation can't be obtained from the web UI. |
| 2 | Open the `QA Text` enlarged preview, right-click the image and choose "Open image in new tab" (or open `/qr/image/<TEXT_ID>/`). | A crisp PNG with a white quiet zone; the response has `Content-Type: image/png` and `Cache-Control: private, max-age=300`. |
| 3 | Decode it with a QR reader (optional). | `Hello from QA`. |

### QR-24 Another user's QR codes are invisible

**Purpose:** ids belonging to someone else are 404 on every endpoint, never 403.
**Preconditions:** `TEXT_ID` and `TRACKED_ID` from earlier cases.

| # | Action | Expected result |
|---|---|---|
| 1 | Log out; sign in as `qa.broke@example.com`. Go to `/qr/`. | "No QR codes found." (none of `qa.user`'s codes are listed). |
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
