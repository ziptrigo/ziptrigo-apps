# Notifications: the bell, its dropdown, the notifications page, and what creates them

**Scope.** The in-app notification system of `apps.core` (issue #169): the bell in the header of
every page for a signed-in user (`core/partials/notification_bell.html`: the badge, its count and
`99+`, the 60-second polling that only runs while the tab is visible, the accessible name), the
dropdown (`POST /notifications/dropdown/`: the latest 10 neither opened nor dismissed, marked read
by opening it, the highlight of the new ones, **Mark all as read**, dismiss, Escape and outside
click, keyboard focus kept after an action), the page `/notifications/` (20 per page, htmx
pagination, read and new rows, **Mark as read**, **Mark all as read**, dismiss, the empty state),
following a notification (`GET /notifications/<id>/open/`: marked read and opened, redirect to its
link), scoping (another user's notification is a 404), the logged-out behaviour of the background
endpoints, escaping, phone width and dark mode; and **every product event that creates a
notification**, each in its own case: welcome credits, a promo code granted at signup, a file
transfer suspended (and the notice removed when it is re-enabled), Receipts storage paused (and
removed on unfreeze), a To-do reminder delivered in-app, To-do storage paused (and removed), a
To-do repeating task whose next occurrence could not be created, and To-do "emailed tasks were not
created". The read-only admin of notifications is in `admin.md` (ADM-32). The emails these events
send are checked here only for arrival, subject and links; `emails.md` has them in full.

## Preconditions

- The test server is running (`python tests_e2e/ai/serve.py`, fresh database), `preflight.md`
  passed. All URLs are relative to `http://localhost:8765`.
- Seeded accounts (password `QaE2e-Passw0rd!`): `qa.user@example.com`, `qa.admin@example.com`
  (superuser), `qa.broke@example.com` (0 credits). None of them has a notification.
- This file registers two fresh accounts (password `NotifBell-4821` for both; pick one random
  suffix `<r>`, e.g. 5 digits, and use it throughout):
  - `NOTIF_A` = `qa.notif.a.<r>@example.com`, name `Notif A`: the bell, dropdown and page cases.
  - `NOTIF_B` = `qa.notif.b.<r>@example.com`, name `Notif B`: the product events.
- Log in on `/account/login/` (**Email**, **Password**, **Sign in**); log out from the account menu
  (person icon, "Account menu") > **Logout**.
- Emails: open `/__e2e__/mail/?to=<address>` and reload until the email is there (up to a minute).
- **(shell step)** rows run a command from the repo root with the wrapper that uses the running
  server's own database, bucket and mail:
  `python tests_e2e/ai/manage.py [--port <port>] <manage.py arguments>` (add `--port` when your
  server is not on 8765). Each command below is one line; replace `<NOTIF_A>` / `<NOTIF_B>` with
  the address. An agent without a shell marks those steps, and the steps that need them,
  **BLOCKED (needs-shell)**. The scheduler does not run on this server, so the metering and
  reminder jobs only run this way.
- The source line under a notification is the product's name (`QR Codes`, `File Transfer`,
  `Receipts`, `Short URLs`, `To-do`, with its icon) or, for anything else, the source label made
  readable (`billing` -> `Billing`, `qa` -> `Qa`, with a generic bell icon), then ` · ` and a
  relative time (`just now`, `2 minutes ago`, one unit) whose tooltip is the exact local time
  (`2026-11-01 15:21` style).

## Reference strings (from the code)

| Where | Text |
|---|---|
| Bell button accessible name | `Notifications` + the badge's sentence, e.g. `Notifications 3 new notifications` (`1 new notification` for one) |
| Badge digits | the count, `99+` above 99 (the digits are `aria-hidden`) |
| Dropdown heading / footer link | **Notifications** / **View all notifications** |
| Dropdown, nothing to show | `No new notifications.` |
| While loading | `Loading…` |
| Header action (dropdown and page, only while something is new) | **Mark all as read** |
| Row buttons | `Dismiss: <title>` (an "x", tooltip "Dismiss"); on the page, new rows only: `Mark as read: <title>` (a check, tooltip "Mark as read") |
| New-row marker | a dot with the screen-reader text `New: ` |
| Page title / heading | `Notifications - ZipTrigo` / **Notifications** |
| Page summary line | `<n> new` or `You're all caught up.` |
| Page, nothing at all | `No notifications yet.` |
| Pagination | `Page <n> of <m>`, links **Previous** / **Next** |

---

### NOTIF-01 Setup: a promo code for the signup case

**Purpose:** a promo code exists for NOTIF-03.
**Preconditions:** logged out.

| # | Action | Expected result |
|---|---|---|
| 1 | Sign in as `qa.admin@example.com`, open `/admin/billing/promocode/add/`. | The add form: **Code**, **Effect**, **Credits**, **Campaign**, **Active**, then the Validity, Eligibility and Origin sections. |
| 2 | Code `NOTIF25`, Effect **Credits**, Credits `25`, Campaign `qa-notif`, **Active** ticked; **Save**. | Back on the list with a success message; `NOTIF25` is listed. |
| 3 | Log out. | - |

### NOTIF-02 Welcome credits create the first notification

**Purpose:** confirming a new account leaves the welcome notification, and the page shows a new one
without marking it read.
**Preconditions:** logged out.

| # | Action | Expected result |
|---|---|---|
| 1 | On `/account/register/` enter **Name** `Notif A`, **Email** `<NOTIF_A>`, **Password** `NotifBell-4821`, no promo code; **Create account**. | **Check your email**. |
| 2 | Open `/__e2e__/mail/?to=<NOTIF_A>`, open the "Confirm your ZipTrigo account email" email's link and press **Confirm my email**. | **Email Confirmed!**. |
| 3 | Sign in as `<NOTIF_A>` / `NotifBell-4821`. On `/` look at the bell, left of the person icon. | An amber badge `1` on the bell. The bell button's accessible name is `Notifications 1 new notification`; it has **no** `aria-label` attribute, has `aria-controls="notification-dropdown"` and `aria-expanded="false"`. |
| 4 | Open `/notifications/`. | Title `Notifications - ZipTrigo`, heading **Notifications**, the line `1 new` and a **Mark all as read** button. One row, highlighted (bold title, the dot with "New:"): the title `Welcome! We added 1000 credits to your account` (a link), the body "Use them in any ZipTrigo app. Your credits history shows where they go.", and `Billing · just now` (hover the time: the exact local time). At the right a check button `Mark as read: Welcome! ...` and an "x" `Dismiss: Welcome! ...`. |
| 5 | Reload the page and look at the bell. | Still `1`: looking at the page marks nothing read. |
| 6 | Click the notification's title. | `/billing/credits/` (heading **Credits History**) with the `+1000` line "A gift for your new account". The badge is gone (hidden at 0). |
| 7 | Back on `/notifications/`. | `You're all caught up.`, no **Mark all as read**; the row is read: lighter text, no dot, no **Mark as read** button (the dismiss "x" is still there). |
| 8 | Click the bell. | The panel opens (`aria-expanded="true"`), briefly `Loading…`, then the heading **Notifications**, `No new notifications.` (an opened notification leaves the dropdown; it stays on the page) and **View all notifications**. |
| 9 | Click outside the panel. | It closes. |

### NOTIF-03 A promo code given at signup is granted, with a notification

**Purpose:** a code typed on the register page is granted at confirmation and announced in the bell
(the user is not on a page that could show a flash).
**Preconditions:** NOTIF-01; logged out.

| # | Action | Expected result |
|---|---|---|
| 1 | Register **Name** `Notif B`, **Email** `<NOTIF_B>`, **Password** `NotifBell-4821`, **Promo code** `notif25` (lower case; it is upper-cased as you type). | **Check your email**. |
| 2 | Confirm it from the email as in NOTIF-02 step 2, and sign in as `<NOTIF_B>`. | The badge reads `2`. |
| 3 | Open `/notifications/`. | `2 new`; two highlighted rows (in either order, both `Billing · just now`): `Your promo code added 25 credits` with the body `Promo code NOTIF25 was applied to your account.`, and `Welcome! We added 1000 credits to your account`. Both link to `/billing/credits/`. |
| 4 | Open `/billing/credits/`. | Balance **1025**; a `+25` line naming `Promo code NOTIF25 (qa-notif)` and the `+1000` welcome line. |
| 5 | On `/notifications/` press **Mark all as read**. | Without a reload: `You're all caught up.`, both rows lose the dot and their **Mark as read** buttons, the badge disappears. |

### NOTIF-04 Signed out: no bell, and the background endpoints never show the login page

**Purpose:** an anonymous visitor gets nothing, the page needs a login, and a session that ends while
a page is open never puts the login page inside the header.
**Preconditions:** logged out, on `/account/login/` (so the page has a CSRF cookie).

| # | Action | Expected result |
|---|---|---|
| 1 | Look at the header. | No bell; a **Login** link. |
| 2 | Go to `/notifications/` and to `/notifications/?page=2`. | Redirected to `/account/login/?next=/notifications/` (and `...next=/notifications/%3Fpage%3D2`). |
| 3 | In the console run `fetch('/notifications/bell/', {headers: {'HX-Request': 'true'}}).then(r => r.status)`. | `204` (an empty answer: htmx swaps nothing). |
| 4 | Run `fetch('/notifications/dropdown/', {method: 'POST', headers: {'HX-Request': 'true', 'X-CSRFToken': getCookie('csrftoken')}}).then(r => r.status)`. | `204`. |
| 5 | Run `fetch('/notifications/bell/').then(r => [r.redirected, r.url])`. | `[true, ".../account/login/?next=/notifications/bell/"]` (a plain request is sent to the login page). |
| 6 | Sign in as `<NOTIF_A>` in tab A (on `/`). Open tab B on the same site and log out there. In tab A, without reloading, click the bell. | The panel opens and keeps `Loading…` (or what it showed before); the header is unchanged: no login form, header or footer appears inside it. |
| 7 | Still in tab A, switch to another tab for a few seconds and back (the badge refreshes when the tab becomes visible). | Nothing visible changes and no error appears. |
| 8 | Reload tab A. | The header shows **Login** (signed out). |

### NOTIF-05 The badge: count, accessible name, hidden at 0

**Purpose:** the badge counts what is new and not dismissed, and says it to screen readers.
**Preconditions:** NOTIF-02. Signed in as `<NOTIF_A>`.

| # | Action | Expected result |
|---|---|---|
| 1 | **(shell step)** `python tests_e2e/ai/manage.py shell -c "from apps.accounts.models import User; from apps.core.services import notifications as n; u = User.objects.get(email='<NOTIF_A>'); n.notify(u, source='qa', title='Second notice', url='/qr/'); n.notify(u, source='qa', title='Escaped <b>bold</b> <script>alert(1)</script>', url='/billing/credits/'); n.notify(u, source='qa', title='Third notice', body='No link on this one.'); n.notify(u, source='qa', title='Long ' + 'x' * 200, url='/todo/')"` | No output, no traceback. |
| 2 | Reload `/`. | The badge reads `4`. The bell's accessible name is `Notifications 4 new notifications` (read the accessibility tree); the `4` itself is `aria-hidden`. |
| 3 | Check the badge element (`#notification-badge`). | It carries `data-count="4"` and is not hidden. No alert dialog has opened anywhere. |

### NOTIF-06 The dropdown

**Purpose:** opening the dropdown lists and reads the latest notifications, highlights the ones that
were new, and keeps the keyboard user's place.
**Preconditions:** NOTIF-05, signed in as `<NOTIF_A>`, on `/`, desktop width.

| # | Action | Expected result |
|---|---|---|
| 1 | Click the bell. | A panel under the bell, right-aligned (about 24 rem wide). Newest first: `Long xxx...` (one line, cut with an ellipsis by the layout), `Third notice` (body `No link on this one.`, at most two lines), `Escaped <b>bold</b> <script>alert(1)</script>` shown **literally** (no bold text, no alert), `Second notice`. All four highlighted (light sage background, bold, the "New:" dot). The welcome notification is not listed (opened). There is **no** **Mark all as read** (opening has already read them all). **View all notifications** at the bottom. |
| 2 | Look at the badge. | It is gone at once (hidden), while the four rows stay highlighted. |
| 3 | Hover `Third notice`'s title. | It is plain text, not a link (no URL); the other rows are links over their whole width (the cursor is a pointer anywhere on the row). |
| 4 | Click the panel heading text **Notifications** (focus drops to the page), then press `Escape`. | The panel closes and keyboard focus is on the bell (a focus ring around it). Escape works wherever the focus is. |
| 5 | Press `Enter` on the focused bell. | The panel opens again: the same four rows, no longer highlighted (they were read by the first opening). |
| 6 | With the keyboard, `Tab` to `Dismiss: Third notice` and press `Enter`. | The row disappears, the other three stay. Focus does **not** fall to the page body: it lands on the control now at that place (the next row's link or dismiss button). Press `Tab` once: focus moves on from there inside the panel. |
| 7 | Click `Second notice`. | `/qr/` (heading **QR codes**). |
| 8 | Open the panel again. | `Second notice` is gone (opened); `Long xxx...` and `Escaped ...` remain. |
| 9 | Click **View all notifications**. | `/notifications/`: the list `Long ...`, `Escaped ...`, `Second notice`, `Welcome! ...` (all read); `Third notice` is absent (dismissed). On the page the long title shows in full, wrapped: 140 characters ending in `…`. |

### NOTIF-07 The page: mark as read, mark all, dismiss, empty state

**Purpose:** the page's actions work in place, keep the badge in step and keep focus.
**Preconditions:** NOTIF-06, signed in as `<NOTIF_A>`, on `/notifications/`.

| # | Action | Expected result |
|---|---|---|
| 1 | **(shell step)** `python tests_e2e/ai/manage.py shell -c "from apps.accounts.models import User; from apps.core.services import notifications as n; u = User.objects.get(email='<NOTIF_A>'); n.notify(u, source='todo', title='Unread one', url='/todo/'); n.notify(u, source='file_transfer', title='Unread two', url='/transfer/')"` then reload the page. | `2 new` and **Mark all as read**; the top rows are `Unread two` (source `File Transfer`, paper-plane icon) and `Unread one` (source `To-do`, check-square icon), both highlighted with **Mark as read** buttons. The badge reads `2`. |
| 2 | Press **Mark as read: Unread two**. | Without a reload: the row stays but loses its highlight, dot and check button; the line reads `1 new`; the badge reads `1`. Focus is on a control in the list (not on `<body>`). |
| 3 | Press **Mark all as read**. | `You're all caught up.`, the button is gone, `Unread one` is read, the badge disappears. |
| 4 | With the keyboard focus `Dismiss: Unread two` and press `Enter`. | The row is removed in place; focus stays inside the list on the control now at that place. |
| 5 | Dismiss every remaining row (`Unread one`, `Long ...`, `Escaped ...`, `Second notice`, `Welcome! ...`). | After the last one the list reads `No notifications yet.` and focus is on the list container itself (no ring is drawn for it after a mouse click). |
| 6 | Open the bell. | `No new notifications.` |
| 7 | Reload `/notifications/`. | Still `No notifications yet.` (dismissed notifications never come back). |

### NOTIF-08 The badge follows new notifications while the tab is visible

**Purpose:** the badge polls every 60 seconds only while the tab is visible, and refreshes when the
tab comes back.
**Preconditions:** NOTIF-07, signed in as `<NOTIF_A>`, on `/`, the network log open.

| # | Action | Expected result |
|---|---|---|
| 1 | Stay on the page for about 2 minutes, watching the network log. | One `GET /notifications/bell/` (200) about every 60 seconds; no other requests. The badge stays hidden. |
| 2 | **(shell step)** `python tests_e2e/ai/manage.py shell -c "from apps.accounts.models import User; from apps.core.services import notifications as n; n.notify(User.objects.get(email='<NOTIF_A>'), source='qa', title='Polled notice', url='/links/')"`. Then wait, without reloading. | Within 60 seconds the badge appears with `1` and pops once (a short scale animation; none with reduced motion). |
| 3 | Open another browser tab and stay there for 2 minutes, then return to this tab. | While the tab was hidden there were **no** `GET /notifications/bell/` requests; one is made right when the tab becomes visible again. |
| 4 | Open the bell, then close it. | `Polled notice` is listed, highlighted; the badge clears. |

### NOTIF-09 Many notifications: 99+, the dropdown's 10, and pagination

**Purpose:** the badge caps at 99+, the dropdown shows the latest 10, and the page pages by 20.
**Preconditions:** NOTIF-08, signed in as `<NOTIF_A>`.

| # | Action | Expected result |
|---|---|---|
| 1 | **(shell step)** `python tests_e2e/ai/manage.py shell -c "from apps.accounts.models import User; from apps.core.services import notifications as n; u = User.objects.get(email='<NOTIF_A>'); [n.notify(u, source='qa', title=f'Bulk notice {i:03d}', url='/') for i in range(1, 106)]"` then reload `/`. | The badge reads `99+`; the bell's accessible name is `Notifications 105 new notifications`. |
| 2 | Open the bell. | Exactly 10 rows, `Bulk notice 105` down to `Bulk notice 096`, all highlighted. This time **Mark all as read** **is** in the panel's header (95 are still new) and the badge reads `95`. The list scrolls inside the panel if it is taller than the panel. |
| 3 | Press **Mark all as read** in the panel. | The panel's list re-renders: the button is gone, the rows are no longer highlighted, the badge disappears. |
| 4 | Close the panel and open `/notifications/`. | `You're all caught up.`; 20 rows, `Bulk notice 105` to `Bulk notice 086`; under the list `Page 1 of 6` and a **Next** link (no **Previous**). |
| 5 | Click **Next**. | The list is replaced in place (no full page load: the header does not flash) by `Bulk notice 085` to `066`; the address bar reads `/notifications/?page=2`; `Page 2 of 6`, **Previous** and **Next**. |
| 6 | Reload. | The same page 2 renders as a full page. Press the browser Back button: the address goes back to `/notifications/`. |
| 7 | Open `/notifications/?page=6`. | `Page 6 of 6`, 6 rows: `Bulk notice 005` to `001` and `Polled notice`; **Previous** only. |
| 8 | Open `/notifications/?page=99` and `/notifications/?page=abc`. | The last page (6 of 6) and the first page (1 of 6) respectively; never an error. |
| 9 | Open `/notifications/list/?page=3` directly. | Redirected to `/notifications/?page=3` (the list fragment alone is only for htmx). |
| 10 | On page 2 dismiss `Bulk notice 085`. | The list stays on page 2 (now starting at `Bulk notice 084`); the summary line and the badge are unchanged. |

### NOTIF-10 Opening a notification

**Purpose:** a notification's link marks it read and opened and goes to its URL; a middle-click
works.
**Preconditions:** NOTIF-09, signed in as `<NOTIF_A>`, on `/notifications/` page 1.

| # | Action | Expected result |
|---|---|---|
| 1 | Read the `href` of `Bulk notice 105`'s title. | `/notifications/<uuid>/open/` (not the target URL itself). Record the uuid as `N_ID`. |
| 2 | Open it in a new tab (middle-click, or Ctrl/Cmd+click). | The new tab lands on `/` (its URL). |
| 3 | Open the bell in the first tab. | `Bulk notice 105` is no longer in the dropdown (opened); `Bulk notice 104` is the first row. On the page it is still listed, read. |
| 4 | Open `/notifications/<N_ID>/open/` again. | Redirected to `/` again (opening twice is fine). |

### NOTIF-11 Another user's notification is a 404

**Purpose:** ids can't be used across accounts, and the answer never confirms the id exists.
**Preconditions:** NOTIF-10 (`N_ID`). Log out and sign in as `qa.user@example.com`.

| # | Action | Expected result |
|---|---|---|
| 1 | Open `/notifications/<N_ID>/open/`. | HTTP 404 (not 403, not a redirect to `/`). |
| 2 | In the console run `fetch('/notifications/<N_ID>/dismiss/', {method: 'POST', headers: {'X-CSRFToken': getCookie('csrftoken')}}).then(r => r.status)` and the same for `/notifications/<N_ID>/read/`. | `404` for both. |
| 3 | Open `/notifications/00000000-0000-0000-0000-000000000000/open/` and `/notifications/not-a-uuid/open/`. | 404 for both. |
| 4 | Open `/notifications/`. | `No notifications yet.` (qa.user has none; nothing of `<NOTIF_A>`'s shows). |
| 5 | Log out, sign in as `<NOTIF_A>`, open `/notifications/`. | `Bulk notice 105` is still listed (step 2 dismissed nothing). |

### NOTIF-12 Phone width and dark mode

**Purpose:** the bell, dropdown and page work on a phone and in dark mode.
**Preconditions:** NOTIF-11, signed in as `<NOTIF_A>`. Add one new notification first:
**(shell step)** `python tests_e2e/ai/manage.py shell -c "from apps.accounts.models import User; from apps.core.services import notifications as n; n.notify(User.objects.get(email='<NOTIF_A>'), source='receipts', title='A notification with a fairly long title to see how it wraps on a phone', body='And a body that is long enough to need more than two lines in the dropdown, so the dropdown clamps it while the page shows it whole.', url='/receipts/')"`.

| # | Action | Expected result |
|---|---|---|
| 1 | Resize to 375 x 812 and reload `/`. | The header is one line: product icons, the bell with its badge `1`, the person icon. No horizontal page scroll. |
| 2 | Tap the bell. | The panel spans the header's width (16 px from each edge), fully inside the viewport; the long title is one line cut with an ellipsis and the body is clamped to two lines; the dismiss "x" buttons are tappable. Take a screenshot. |
| 3 | Tap **View all notifications**. | The page fits the width: titles and bodies wrap, the buttons stay at the right of each row, **Previous**/**Next** and `Page 1 of 6` fit. No horizontal scroll. |
| 4 | Resize back to desktop. On `/account/` choose **Dark** under Appearance. Open the bell. | The panel is dark grey with light text; the read rows, the highlighted new row (if any), the source line and the buttons are all readable; the badge (amber, dark digits) is readable on the dark header. |
| 5 | Open `/notifications/` in dark mode. | The list card, the summary line, **Mark all as read** (if shown), the rows and the pagination are readable; nothing white-on-white. Choose **System** again on `/account/`. |

### NOTIF-13 File transfer: suspended, and the notice removed when it is re-enabled

**Purpose:** a transfer suspended for lack of credits notifies its owner (with an email), and the
notice is deleted when the transfer works again, by the dashboard's **Re-enable** or by a top-up.
**Preconditions:** NOTIF-03 (`<NOTIF_B>` has 1025 credits); signed in as `<NOTIF_B>`. In production
the daily metering job suspends a transfer whose day can't be paid; here the shell step calls the
same `suspend_transfer` that job calls.

| # | Action | Expected result |
|---|---|---|
| 1 | Open `/transfer/send/` and add a file `notif-ft.txt` (100 bytes) with the console snippet of `file_transfer.md` ("Test data", content `'N'.repeat(99) + '\n'`). Recipients `qa.notif.rcpt@example.com`, leave the rest; **Send**. | The sent page with the link `http://localhost:8765/t/<slug>/`. Record the slug. |
| 2 | **(shell step)** `python tests_e2e/ai/manage.py shell -c "from apps.file_transfer.models import Transfer; from apps.file_transfer.services.metering import suspend_transfer; t = Transfer.objects.filter(owner__email='<NOTIF_B>', status='active').latest('created_at'); suspend_transfer(t); print(t.status)"` | Prints `suspended`. |
| 3 | Reload any page and open the bell. | A new notification `Transfer suspended: notif-ft.txt`, source `File Transfer`, body "It ran out of credits and its download link no longer works. Add at least 1 credit (one day of storage for this transfer; a smaller top-up will not resume it) within 7 day(s) to resume it, or its files will be deleted." |
| 4 | Open `/__e2e__/mail/?to=<NOTIF_B>`. | An email `Your transfer "notif-ft.txt" was suspended (out of credits)` saying the link no longer works, "Top up your credits within 7 day(s)", "a balance of at least 1 credit", and `Manage this transfer: http://localhost:8765/transfer/`. Open that link: the transfers dashboard. |
| 5 | Click the notification. | `/transfer/`: the transfer's row says `SUSPENDED` and has a **Re-enable** button. Open `/t/<slug>/` in another tab: the neutral "This transfer is no longer available" page. |
| 6 | Press **Re-enable** on the row. | The row is active again (a **Disable** button instead). Open `/notifications/`: `Transfer suspended: notif-ft.txt` is **gone** (deleted, not just read). `/t/<slug>/` lists the file again. |
| 7 | Repeat step 2 (suspend it again). | Prints `suspended`; the bell shows the suspended notification again (new, badge `1`). |
| 8 | In another session sign in as `qa.admin@example.com`, open `/admin/billing/credittransaction/adjust/`, **User email** `<NOTIF_B>`, **Direction** Add, **Amount** `1`, **Description** `QA notif top-up`, **Apply**. | "Adjusted credits for <NOTIF_B>. New balance: <n>." |
| 9 | As `<NOTIF_B>` reload `/transfer/` and `/notifications/`. | The transfer is active again (the top-up re-enabled it) and its suspended notification is gone; the bell has no badge for it. |

### NOTIF-14 Receipts: storage paused, and the notice removed on unfreeze

**Purpose:** a Receipts account frozen for unpaid storage notifies its owner, and unfreezing deletes
the notice.
**Preconditions:** NOTIF-13; signed in as `<NOTIF_B>`. The shell step calls `freeze_account`, which
the daily storage metering calls when the balance can't pay a day.

| # | Action | Expected result |
|---|---|---|
| 1 | Open `/receipts/` once (the first visit creates the Receipts account). | The Receipts dashboard, empty. |
| 2 | **(shell step)** `python tests_e2e/ai/manage.py shell -c "from apps.receipts.models import ReceiptsAccount; from apps.receipts.services.metering import freeze_account; a = ReceiptsAccount.objects.get(user__email='<NOTIF_B>'); freeze_account(a); print(a.frozen_at)"` | Prints a timestamp. |
| 3 | Reload `/receipts/` and open the bell. | The amber "Storage is paused." banner with **Top up** on the page. In the bell: `Receipts storage is paused`, source `Receipts`, body "Your credit balance couldn't cover your receipts' storage. Nothing was deleted; previews, downloads and processing are paused until you add credits." Clicking it opens `/receipts/`. |
| 4 | Open `/__e2e__/mail/?to=<NOTIF_B>`. | `Your receipts storage is paused`: "Your credit balance couldn't cover the storage for your receipts.", that nothing was deleted, and the links `Add credits: http://localhost:8765/billing/credits/` and `Your receipts: http://localhost:8765/receipts/`. Open both: the credits history and the Receipts dashboard (still with the banner). |
| 5 | As `qa.admin`, add 1 credit to `<NOTIF_B>` with the adjustment tool (NOTIF-13 step 8). | Success message. |
| 6 | As `<NOTIF_B>` reload `/receipts/` and `/notifications/`. | The banner is gone; `Receipts storage is paused` is gone from the page and the bell. |

### NOTIF-15 To-do: a reminder delivered in the app

**Purpose:** a due in-app reminder becomes a notification that opens the task; an email-only one
does not.
**Preconditions:** NOTIF-14; signed in as `<NOTIF_B>`. The scheduler does not run here, so the shell
step makes the reminders due and runs the job (`apps.todo.jobs.send_reminders`).

| # | Action | Expected result |
|---|---|---|
| 1 | On `/todo/` add `Notif call` with **Date** tomorrow and **Time** `09:00` (quick add). Open its page and record its number as `T_CALL`. | **Reminders** lists "5 minutes before (in-app)" (the default). |
| 2 | In the Reminders add row choose **1 hour before**, channel **Email**, **Add**. | "1 hour before (email)" is listed too. |
| 3 | **(shell step)** `python tests_e2e/ai/manage.py shell -c "from datetime import timedelta; from django.utils import timezone; from apps.todo.models import Reminder; print(Reminder.objects.filter(task__user__email='<NOTIF_B>', task__number=<T_CALL>).update(fire_at=timezone.now() - timedelta(minutes=1), sent_at=None)); from apps.todo.jobs import send_reminders; send_reminders()"` | Prints `2`. |
| 4 | Reload any page and open the bell. | **One** new notification: `Notif call`, source `To-do`, body `Due <tomorrow as "2 Nov 2026">, 09:00`. Not two: the email-only reminder made no notification. |
| 5 | Click it. | `/todo/<T_CALL>/`; its **History** lists the in-app delivery. |
| 6 | Open `/__e2e__/mail/?to=<NOTIF_B>`. | One email `Reminder: Notif call` (from the email-only reminder; `todo.md` / `emails.md` check its content). |
| 7 | Run step 3's `send_reminders()` part again: **(shell step)** `python tests_e2e/ai/manage.py shell -c "from apps.todo.jobs import send_reminders; send_reminders()"`. | Nothing new in the bell (a reminder is delivered once). |
| 8 | Trash `Notif call` (its trash button, confirm). | It leaves the lists (it would otherwise count toward NOTIF-17's open-task numbers). |

### NOTIF-16 To-do: storage paused, and the notice removed

**Purpose:** a frozen To-do account (unpaid attachment storage) notifies its owner, and a top-up
removes the notice.
**Preconditions:** NOTIF-15; signed in as `<NOTIF_B>` (the To-do account exists since NOTIF-15).

| # | Action | Expected result |
|---|---|---|
| 1 | **(shell step)** `python tests_e2e/ai/manage.py shell -c "from apps.todo.models import TodoAccount; from apps.todo.services.metering import freeze_account; a = TodoAccount.objects.get(user__email='<NOTIF_B>'); freeze_account(a); print(a.frozen_at)"` | Prints a timestamp. |
| 2 | Reload `/todo/` and open the bell. | The amber banner "**Storage is paused.** Your credits couldn't cover the storage for your attachments. Nothing was deleted; uploads and downloads resume when you add credits." with **Top up**. In the bell: `Your to-do file storage is paused`, source `To-do`, body "Your credit balance couldn't cover the storage for your attachments. Nothing was deleted; uploads and downloads are paused until you add credits." Clicking it opens `/todo/settings/`. |
| 3 | Open `/__e2e__/mail/?to=<NOTIF_B>`. | `Your to-do file storage is paused`: the storage of "the files attached to your tasks (0 bytes, about 1 credit a day)", nothing deleted, and the links `Add credits: http://localhost:8765/billing/credits/` and `Your to-do settings: http://localhost:8765/todo/settings/`; both open the right page. |
| 4 | As `qa.admin`, add 1 credit to `<NOTIF_B>` (NOTIF-13 step 8). | Success message. |
| 5 | As `<NOTIF_B>` reload `/todo/` and `/notifications/`. | The banner is gone, and so is `Your to-do file storage is paused`. |

### NOTIF-17 To-do: the next occurrence of a repeating task could not be created

**Purpose:** a repeating task whose next occurrence (or its subtasks) can't be created because of the
open-task cap says so in the bell, one notice per repeating task, cleared once it works again.
**Preconditions:** NOTIF-16; signed in as `<NOTIF_B>` with no other open To-do task (NOTIF-15 trashed
`Notif call`). Uses a second session as `qa.admin` to change **To-do settings** > **Max open tasks
per user** (default `10000`); step 9 puts it back.

| # | Action | Expected result |
|---|---|---|
| 1 | On `/todo/` type `Water ferns`, set **Date** today, open **Repeat**, choose **Day**, **Done**, **Add task**. Open its page (record the number as `T_W1`) and add the subtask `Mist the leaves` (**Subtask title**, **Add**). | The chip "Every day (fixed schedule)"; one subtask. |
| 2 | Add a plain task `Buy soil` (no date). | Open tasks: `Water ferns`, `Mist the leaves`, `Buy soil` (3). |
| 3 | As `qa.admin`, open `/admin/todo/todosettings/`, set **Max open tasks per user** to `3`, **Save**. | Saved. |
| 4 | As `<NOTIF_B>` complete `Water ferns` (from Today; confirm completing its open subtask too). | It is completed; a new `Water ferns` due tomorrow exists (in **All**) **without** a subtask. The bell shows `The next “Water ferns” has no subtasks`, source `To-do`, body "The next occurrence of this repeating task was created, but its subtasks were not copied because you have reached the open-task limit. Add them yourself once you have freed some space." It links to `/todo/<T_W1>/`. Record the new task's number as `T_W2`. |
| 5 | As `qa.admin` set **Max open tasks per user** to `1`, **Save**. | Saved. |
| 6 | As `<NOTIF_B>` open `/todo/<T_W2>/` and press **Complete**. | The task is completed (completing never fails), but no next `Water ferns` is created. The bell shows `Couldn’t create the next “Water ferns”` with the body "The next occurrence of this repeating task was not created, usually because you have reached the open-task limit. Free some space by completing or deleting tasks, then uncomplete and complete this task again, or set the date manually." linking to `/todo/<T_W2>/`. |
| 7 | Open `/notifications/`. | Only one notice for this repeating task: `Couldn’t create ...` replaced `... has no subtasks` (which is gone). |
| 8 | As `qa.admin` set **Max open tasks per user** back to `10000`, **Save**. | Saved. |
| 9 | As `<NOTIF_B>`, on `/todo/<T_W2>/` press **Uncomplete**, then **Complete**. | A new `Water ferns` is created (due the day after `T_W2`'s date). The `Couldn’t create ...` notice is gone from the bell and the page. |

### NOTIF-18 To-do: emailed tasks not created for lack of credits

**Purpose:** an email to a user's To-do address that can't be paid for creates no task and leaves one
coalesced notice.
**Preconditions:** NOTIF-17. Needs the real-services server (the shell command uploads the message to
the Floci bucket the server reads; in `--fakes` mode the bucket lives inside the server process, so
mark this case **BLOCKED (fakes mode)**). Inbound mail through SES itself is out of scope; the
command runs the real ingest on a local file. Uses `qa.broke@example.com` (0 credits) and, as
`qa.admin`, **To-do settings** > **Free tasks per month** set to `0` for the duration (step 7 puts
it back to `10`).

| # | Action | Expected result |
|---|---|---|
| 1 | Sign in as `qa.broke@example.com`, open `/todo/settings/`. | Section **Add tasks by email** with an address `todo+<code>@<domain>`. Record it as `INBOX`. |
| 2 | As `qa.admin`, open `/admin/todo/todosettings/`, set **Free tasks per month** to `0`, **Save**. | Saved. |
| 3 | **(shell step)** `python -c "open('notif-task.eml', 'w').write('From: Sender <sender@example.com>\nTo: <INBOX>\nSubject: Buy milk\nMessage-ID: <notif-1@example.com>\nContent-Type: text/plain; charset=utf-8\n\nTwo litres.\n')"` then `python tests_e2e/ai/manage.py todo_ingest_eml notif-task.eml --to <INBOX>` | Prints `<INBOX>: rejected_credits (...)` and `Done (todo/inbound/local-<uuid>).` |
| 4 | As `qa.broke` reload `/todo/` and open the bell. | No `Buy milk` task anywhere. The bell: `Emailed tasks were not created: not enough credits`, source `To-do`, body "Tasks sent to your To-do address couldn't be created because you don't have enough credits. Top up, then send them again." (nothing from the email, not even its subject). Clicking it opens `/billing/credits/`. |
| 5 | **(shell step)** Send a second one: the same two commands with `Subject: Buy bread` and `Message-ID: <notif-2@example.com>`. Then reload. | The badge reads `1`, not `2`: the earlier notice was replaced by a fresh one (`/notifications/` lists exactly one `Emailed tasks were not created ...`, new again). |
| 6 | Open `/todo/settings/` > **Recent emails**. | Two rows, `Buy bread` and `Buy milk`, from `sender@example.com`, each "Rejected: not enough credits". |
| 7 | As `qa.admin` set **Free tasks per month** back to `10`, **Save**. Delete `notif-task.eml`. | Saved. |

## Out of scope

- Real inbound email to `todo+` addresses through SES (NOTIF-18 runs the same ingest on a local
  file); the To-do "storage paused" and Receipts "storage paused" *triggers* in their real form (the
  daily metering only freezes when a day's charge reaches a whole credit, which takes gigabytes of
  storage; the shell steps call the very function it calls).
- An email or push channel, per-user preferences and a JWT API for notifications: not built (v1).
- The 90-day retention job (`core.purge_old_notifications`) and the notification admin (`admin.md`).
