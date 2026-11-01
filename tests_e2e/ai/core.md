# Core: site shell

**Scope.** Everything every page shares (`apps/core/templates/core/base.html`) and the landing page
(`core/home.html`): the header (logo, one link per registered product, the notification bell and
account menu for a signed-in user or a Login link), the footer (Feedback, copyright), the "Skip to
content" link, the product cards on `/`, the "login required" redirects of the product and
account pages, unknown URLs, the API docs at `/api/docs`, light/dark theme, the in-app
notification bell and page, the layout at phone width (375 px), page titles, favicon and logo,
and console errors. Signing in itself is covered in `accounts.md`; here it is only a step.

## Preconditions

- The test server is running at `http://localhost:8765` (`python tests_e2e/ai/serve.py`, fresh
  database). All URLs below are relative to it.
- Seeded accounts (password `QaE2e-Passw0rd!` for all): `qa.user@example.com` (name "QA User",
  100 credits), `qa.admin@example.com` (superuser, "QA Admin"). Neither has any notification (the
  seeded accounts were created with the welcome credits off).
- Start logged out, in a fresh browser profile or after clearing cookies and `localStorage` for
  `localhost:8765` (the theme is stored on the account, not in the browser, since issue #166).
- To log in during a case: on `/account/login/` fill **Email** and **Password** and press
  **Sign in**. To log out: open the account menu (the person icon at the top right, accessible
  name "Account menu") and press **Logout**.

## Reference data

The products are registered in `INSTALLED_APPS` order, so the header and the landing page list
them in this order:

| Product | Header link / card title | URL | Card description | Icon |
|---|---|---|---|---|
| QR codes | `QR Codes` | `/qr/` | "Generate QR codes for URLs and text, with trackable short links." | `fa-qrcode` |
| File transfer | `File Transfer` | `/transfer/` | "Send large files to anyone with a link that expires." | `fa-paper-plane` |
| Receipts | `Receipts` | `/receipts/` | "Email or snap your receipts and invoices; we file and categorize them." | `fa-receipt` |
| Short URLs | `Short URLs` | `/links/` | "Shorten a link, track it, and share it as a QR code." | `fa-link` |
| To-do | `To-do` | `/todo/` | "Tasks with due dates, labels and reminders, repeating when you need them." | `fa-check-square` |

---

### CORE-01 Landing page, logged out

**Purpose:** the landing page renders its content, logo and product cards for an anonymous visitor.
**Preconditions:** logged out; desktop width (1280 px or wider).

| # | Action | Expected result |
|---|---|---|
| 1 | Go to `/`. | HTTP 200. The browser tab title is exactly `ZipTrigo`. The tab shows the ZipTrigo favicon (`/static/core/images/logo_32x32.png`, declared with `<link rel="icon">`), not a generic globe. |
| 2 | Look at the main content. | A centred 128 x 128 logo image (alt text `ZipTrigo logo`) that loads (not a broken-image icon); under it the heading **Welcome to ZipTrigo** (H1) and the line "A set of small tools under one account." The logo, heading, line and cards rise into place one after the other once (no motion with reduced-motion on). |
| 3 | Look at the product cards. | Exactly five cards, in the order of the reference table, each with its icon, title and description exactly as listed. At this width they sit in a two-column grid (the fifth card alone on the third row, left column). |
| 4 | Hover each card. | The card lifts slightly, the border colour changes and a shadow appears (hover state); the cursor is a pointer. |
| 5 | Check the page for leftovers. | No raw template syntax (`{{`, `{%`), no `None`, no empty cards, no text overlapping. |

### CORE-02 Header and footer, logged out

**Purpose:** the shared header and footer show the anonymous variant.
**Preconditions:** logged out, on `/`.

| # | Action | Expected result |
|---|---|---|
| 1 | Look at the header (top bar). | Left: a small logo plus the text **ZipTrigo**, then the links **QR Codes**, **File Transfer**, **Receipts**, **Short URLs**, **To-do**, each with its icon (from 768 px wide the names show next to the icons; each link also has the name as its accessible name and tooltip). Right: a **Login** link (no theme button: the theme is chosen on the account page). There is no bell and no person icon / account menu. |
| 2 | Look for a credit balance in the header. | None is shown (the base template has no balance in the header; the balance is on the account page, see CORE-08). |
| 3 | Look at the footer. | A **Feedback** link in the middle (no FAQ link: there is no FAQ page); on the right, in small grey text, `© <current year> Joao Coelho. Licensed under MIT.` (the year is the current one, e.g. `© 2026`). The footer sits at the bottom of the window even though the page content is short (no gap below it). |
| 4 | Click **ZipTrigo** in the header. | Stays on / reloads `/`. |
| 5 | Click **Login**. | Goes to `/account/login/` (title `Login - ZipTrigo`, heading **Sign in**, fields **Email** and **Password**, button **Sign in**, links "Create one" and "Forgot your password?"). The header and footer are the same as on `/`. |
| 6 | Go back to `/`, reload and press `Tab` once. | A **Skip to content** link appears at the top left (the first focusable element); pressing `Enter` moves focus to the main content (`#main`) and the URL gets `#main`. Tab away: the link disappears again. |

### CORE-03 Product links require login and keep `next`

**Purpose:** the product entry points redirect an anonymous visitor to the login page with a `next`
parameter, and signing in returns them to the product.
**Preconditions:** logged out.

| # | Action | Expected result |
|---|---|---|
| 1 | On `/`, click the **QR Codes** card. | The URL becomes `/account/login/?next=/qr/`. The login page is shown (no error, no flash of the QR page). |
| 2 | Sign in as `qa.user@example.com`. | You land on `/qr/` (heading **QR codes**, title `QR codes - ZipTrigo`), not on the account page. The header now shows the person icon instead of **Login**. |
| 3 | Log out (account menu > **Logout**). | You are on `/`; the header shows **Login** again. |
| 4 | Click the **File Transfer** header link. | URL `/account/login/?next=/transfer/`. |
| 5 | Sign in as `qa.user@example.com`. | You land on `/transfer/` (heading **Your transfers**, title `File Transfer - ZipTrigo`). |
| 6 | Log out, then click the **Receipts** card. | URL `/account/login/?next=/receipts/`. |
| 7 | Sign in as `qa.user@example.com`. | You land on `/receipts/` (heading **Receipts**, title `Receipts - ZipTrigo`). |
| 8 | Log out. | Back on `/`, logged out. |

### CORE-04 Other login-required pages

**Purpose:** every account-only page redirects with `next`, including deep pages.
**Preconditions:** logged out.

| # | Action | Expected result |
|---|---|---|
| 1 | Type each URL directly into the address bar: `/account/`, `/billing/credits/`, `/feedback/`, `/feedback/thanks/`, `/receipts/upload/`, `/transfer/send/`, `/qr/create/`. | Each ends on `/account/login/?next=<that path>` (the `next` value is the exact path typed, URL-encoded where needed). No page content of the protected page is shown. |
| 2 | From `/account/login/?next=/billing/credits/`, sign in as `qa.user@example.com`. | You land on `/billing/credits/` (heading **Credits History**). |
| 3 | Log out. Go to `/feedback` (no trailing slash). | Redirected first to `/feedback/` and then to `/account/login/?next=/feedback/`. |

### CORE-05 Login without `next`, and `next` pointing off-site

**Purpose:** the default landing page after login, and the open-redirect guard.
**Preconditions:** logged out.

| # | Action | Expected result |
|---|---|---|
| 1 | Go to `/account/login/` (no query string) and sign in as `qa.user@example.com`. | You land on `/`, the landing page (**Welcome to ZipTrigo**, as in CORE-01). The header shows the person icon, not **Login**. |
| 2 | Log out. Go to `/account/login/?next=https://evil.example.com/` and sign in as `qa.user@example.com`. | You land on `/`, **not** on `evil.example.com` (the browser never leaves `localhost:8765`). |
| 3 | Log out. Go to `/account/login/?next=//evil.example.com/qr/` and sign in. | You land on `/`, still on `localhost:8765`. |
| 4 | Log out. | Back on `/`. |

### CORE-06 Logged-in header and account menu

**Purpose:** the authenticated header variant and its dropdown behave.
**Preconditions:** logged in as `qa.user@example.com`, on `/`.

| # | Action | Expected result |
|---|---|---|
| 1 | Look at the header's right side. | A bell button (accessible name "Notifications", see CORE-19) and a person icon button (tooltip "Account"); no theme button and no **Login** link. |
| 2 | Click the person icon. | A dropdown opens under it, right-aligned and fully inside the window: first line **QA User** (bold, not a link), then **Settings** (cog icon) and **Logout** (sign-out icon). |
| 3 | Click anywhere outside the dropdown. | It closes. |
| 4 | Open it again and press `Escape`. | It closes and keyboard focus returns to the person icon (a focus ring is visible on it). |
| 5 | Open it and click **Settings**. | Goes to `/account/` (**Account Settings**). |
| 6 | Hover the name line in the dropdown. | A tooltip with the full name (`QA User`); the text is selectable, not clickable. |
| 7 | Go to `/account/logout/` by typing it in the address bar (a GET). | Redirected to `/account/login/` (no error page). You are **still logged in**: going back to `/` still shows the person icon. |

### CORE-07 Logged-in landing page and product navigation

**Purpose:** the landing page and header links work for a signed-in user.
**Preconditions:** logged in as `qa.user@example.com`.

| # | Action | Expected result |
|---|---|---|
| 1 | Go to `/`. | Same content as CORE-01 (heading, three cards); no personal greeting is expected. |
| 2 | Click each card in turn (use Back between them). | **QR Codes** opens `/qr/` (heading **QR codes**), **File Transfer** opens `/transfer/` (heading **Your transfers**), **Receipts** opens `/receipts/` (heading **Receipts**), **Short URLs** opens `/links/` (heading **Short URLs**), **To-do** opens `/todo/` (heading **Today**). No login page in between. |
| 3 | On each product page, click the header links **QR Codes**, **File Transfer**, **Receipts**, **Short URLs**, **To-do** and **ZipTrigo**. | Each goes to its page directly (`/qr/`, `/transfer/`, `/receipts/`, `/links/`, `/todo/`, `/`); the header (logo, links, bell, person icon) and footer look the same on every page. The link of the product you are in is highlighted (a light sage background) and carries `aria-current="true"`; the others do not. |

### CORE-08 Where the credit balance is shown

**Purpose:** the balance from `apps.billing` reaches the pages that show it, and the header shows
none.
**Preconditions:** logged in as `qa.user@example.com` (100 credits on a fresh database).

| # | Action | Expected result |
|---|---|---|
| 1 | Look at the header on `/`. | No credit balance in the header (by design of `core/base.html`; if the product owner expects one, note it). |
| 2 | Go to `/account/`. | Section **Credits**: "Current balance" with **100 credits**, a **Usage History** button and the "Have a promo code?" form. |
| 3 | Click **Usage History**. | Goes to `/billing/credits/`: "Current balance: **100** credits". |
| 4 | Go to `/receipts/upload/`. | Top right: "Balance: **100 credits**" as a link to `/billing/credits/`. |
| 5 | Go to `/transfer/send/`. | "Your balance: 100 credits. Sending needs at least 1." with no "Buy credits" link (shown only below the minimum). |

### CORE-09 Footer links

**Purpose:** the footer's links go where they should and mark the current page.
**Preconditions:** logged in as `qa.user@example.com`, on `/`.

| # | Action | Expected result |
|---|---|---|
| 1 | Click **Feedback** in the footer. | Goes to `/feedback/` (title `Send feedback - ZipTrigo`, heading **Send feedback**). |
| 2 | Inspect the **Feedback** footer link on `/feedback/` (read the page's accessibility tree). | It carries `aria-current="page"`. On `/` and on `/account/` it does **not**. |
| 3 | Look for an **FAQ** link in the footer. | None (there is no FAQ page, and no placeholder `href="#"` link). |
| 4 | Log out, then click **Feedback** in the footer. | Redirected to `/account/login/?next=/feedback/`. |

### CORE-10 Unknown URLs

**Purpose:** unknown paths answer 404 without breaking anything.
**Preconditions:** none (run logged out, then repeat step 1 logged in as `qa.user@example.com`).

| # | Action | Expected result |
|---|---|---|
| 1 | Go to `/this-page-does-not-exist/`. | HTTP 404. This server runs with `DEBUG = True`, so Django's yellow technical page "Page not found (404)" listing the URL patterns is expected here (in production it would be Django's plain "Not Found" page, since the project has no `404.html`). Note: no branded 404 page exists; record as an observation, not a failure. |
| 2 | Go to `/qr/does-not-exist/` and `/billing/nope/`. | 404 for both, logged out and logged in; never a 500 / "Server Error". |
| 3 | Go to `/go/zzzzzzzz` (a short link that doesn't exist). | 404, never a 429 or 500. |
| 4 | Go to `/t/not-a-real-slug/`. | HTTP 404 with the site's own neutral page: tab title `Transfer unavailable - ZipTrigo`, heading **This transfer is no longer available**, normal header and footer. Never a 500. |

### CORE-11 API docs

**Purpose:** the Django Ninja docs page loads.
**Preconditions:** logged out.

| # | Action | Expected result |
|---|---|---|
| 1 | Go to `/api/docs`. | HTTP 200, tab title `ZipTrigo API`. The Swagger UI renders (it loads `swagger-ui-dist` from `cdn.jsdelivr.net`, so it needs internet access): title **ZipTrigo API**, version **1.0.0**, description "API for every ZipTrigo app. Authenticate with a JWT from `/api/auth/login`." and an **Authorize** button. |
| 2 | Scroll the operation list. | Groups of endpoints including `/api/auth/login`, `/api/billing/...`, `/api/qr/...`, `/api/ft/...`, `/api/receipts/...`, `/api/s/...` and `/api/todo/...`. There is **no** `/api/auth/signup` (removed in issue #117). |
| 3 | Expand `POST /api/auth/login`. | The request body schema shows `email` and `password`. (Do not execute requests here; the API is out of scope.) |
| 4 | Open `/api/openapi.json`. | A JSON document whose `info.title` is `ZipTrigo API`. |

### CORE-12 Theme follows the account setting

**Purpose:** the theme is chosen on the account page (issue #166), is stored on the account, and
dark mode is readable. There is no toggle in the header.
**Preconditions:** logged out, the OS/browser colour scheme set to **light**, on `/`.

| # | Action | Expected result |
|---|---|---|
| 1 | Look at the header and the page. | No theme button anywhere in the header. The page is light (white background, dark text) and `<html>` has `data-theme="system"` and no `dark` class. |
| 2 | (If the browser tool can emulate `prefers-color-scheme: dark`) switch the emulated scheme to dark while logged out and reload. | The page renders dark with no choice made: a visitor without a session follows the system. Switch the emulation back to light. |
| 3 | Sign in as `qa.user@example.com` and open `/account/`. | The **Appearance** section has three options, **System**, **Light**, **Dark**, with **System** selected. |
| 4 | Choose **Dark**. | With no Save button and no reload (scroll position and focus stay on the option), the page turns dark at once: `<html>` has `data-theme="dark"` and class `dark`; dark grey background, light text; header and footer dark grey. **Dark** stays selected. |
| 5 | In dark mode, go to `/`, `/feedback/` and `/billing/credits/`. | Every page is dark too (the choice is the account's, so it applies to all pages); all text and input fields are readable (no dark text on a dark background, no white boxes with white text). |
| 6 | Choose **Light** on `/account/`. | The page turns light at once, without a reload: `<html>` has `data-theme="light"` and no `dark` class. |
| 7 | With **Light** saved, emulate a dark OS colour scheme and reload. | The page stays light: an explicit choice wins over the system. |
| 8 | Choose **System** (OS emulation still dark). | The page turns dark at once (`data-theme="system"`, class `dark` added by the script). While in **System**, switch the emulated scheme back to light **without reloading**: the page follows it live. |
| 9 | Sign out and sign in again (or open a fresh browser profile and sign in); open `/account/` and save **Dark**, then sign out. | After signing out the page follows the system again (light), and the choice is waiting on the account after signing back in (it is stored on the account, not in the browser). |
| 10 | Look for a flash of the wrong theme on reload while in dark mode. | No visible white flash before dark styles apply (the server renders the `dark` class on `<html>` before any CSS loads). Tailwind's Play CDN may cause a brief unstyled flash; note it if noticeable. |

### CORE-13 Keyboard navigation of the header

**Purpose:** the header is usable with the keyboard alone.
**Preconditions:** logged in as `qa.user@example.com`, on `/`, focus at the top of the page.

| # | Action | Expected result |
|---|---|---|
| 1 | Press `Tab` repeatedly. | Focus moves in order: the **Skip to content** link (it appears while focused), **ZipTrigo**, **QR Codes**, **File Transfer**, **Receipts**, **Short URLs**, **To-do**, the notification bell, the account icon, then into the page (the product cards), then the footer links. Every focused element shows a visible focus indicator. |
| 2 | With the account icon focused press `Enter`. | The menu opens; its `aria-expanded` becomes `true`. |
| 3 | Press `Tab`. | Focus moves to **Settings**, then **Logout**. |
| 4 | Press `Escape`. | The menu closes, `aria-expanded` is `false`, focus is back on the account icon. |

### CORE-14 Phone width (375 px): landing page and header, logged out

**Purpose:** the shell works on a phone-sized viewport.
**Preconditions:** logged out; resize the viewport to 375 x 812 (mobile preset), then reload `/`.

| # | Action | Expected result |
|---|---|---|
| 1 | Look at the whole page, then try to scroll horizontally. | No horizontal scrolling: the page is not wider than the viewport (`document.documentElement.scrollWidth` <= 375). Nothing is cut off at the right edge. |
| 2 | Look at the header. | One line: the logo and **ZipTrigo**, the five product links as icons only (QR Codes, File Transfer, Receipts, Short URLs, To-do; each name is its tooltip and accessible name), and **Login** at the right. Nothing overlaps, wraps or is clipped. Take a screenshot. (The product names appear next to the icons from 768 px wide.) |
| 3 | Look at the product cards. | One column, full width with side padding; text wraps inside the cards. |
| 4 | Look at the footer. | **Feedback** and the copyright line fit within the width (stacked on a phone; check the copyright isn't pushed off-screen). |
| 5 | Tap **Login**. | The login form fits the width; the inputs are full width. |

> Note for the reviewer: the header has no mobile menu (the product names are hidden below 768 px
> and the **ZipTrigo** text below 360 px), so overflow at 320-375 px is the thing to look for.
> Record exactly what you see, with a screenshot, and the measured `scrollWidth`.

### CORE-15 Phone width (375 px), logged in

**Purpose:** the account menu and product pages work on a phone.
**Preconditions:** viewport 375 x 812; logged in as `qa.user@example.com`.

| # | Action | Expected result |
|---|---|---|
| 1 | Look at the header. | Still one line: the five product icons, the notification bell and the person icon (no icon pushed onto a second line). It stays one line at 320 px too, where **ZipTrigo** next to the logo is hidden. |
| 2 | On `/`, tap the person icon. | The dropdown opens fully inside the viewport (nothing cut off at the right or left edge); **QA User**, **Settings**, **Logout** are readable and tappable. |
| 3 | Tap outside it. | It closes. |
| 4 | Open `/qr/`, `/transfer/`, `/receipts/`, `/account/`, `/links/`, `/todo/`, `/billing/credits/` and `/feedback/` in turn. | Each page has no horizontal page scroll (a wide table may scroll inside its own box, but the page itself must not; the credits history becomes cards with no scrolling box). Headings and primary buttons are visible without zooming. |
| 5 | Reset the viewport to desktop. | - |

### CORE-16 Page titles

**Purpose:** every page has a sensible, distinct browser-tab title.
**Preconditions:** logged in as `qa.user@example.com` (log out for the login/register rows).

| # | Action | Expected result |
|---|---|---|
| 1 | Visit each page and read the tab title. | Titles at the time of writing: `/` `ZipTrigo`; `/qr/` `QR codes - ZipTrigo`; `/transfer/` `File Transfer - ZipTrigo`; `/transfer/send/` `Send files - ZipTrigo`; `/receipts/` `Receipts - ZipTrigo`; `/receipts/upload/` `Upload receipts - ZipTrigo`; `/links/` `Short URLs - ZipTrigo`; `/todo/` `Today - To-do - ZipTrigo`; `/todo/settings/` `Settings - To-do - ZipTrigo`; `/account/` `Account Settings - ZipTrigo`; `/billing/credits/` `Credits History - ZipTrigo`; `/notifications/` `Notifications - ZipTrigo`; `/feedback/` `Send feedback - ZipTrigo`; `/account/login/` `Login - ZipTrigo`; `/account/register/` `Register - ZipTrigo`; `/api/docs` `ZipTrigo API`. None is empty, `None` or a raw template tag. |
| 2 | Compare them. | Every page of the site ends in ` - ZipTrigo` (the API docs, `ZipTrigo API`, and `/`, plain `ZipTrigo`, are the exceptions) and To-do pages carry their section (`... - To-do - ZipTrigo`). A page without the suffix is a regression. |

### CORE-17 Console errors and broken assets

**Purpose:** the main pages load without JavaScript errors or missing files.
**Preconditions:** open the browser console / network log before each page load. Logged in as
`qa.user@example.com` except for the login page.

| # | Action | Expected result |
|---|---|---|
| 1 | Load `/`, `/account/login/` (logged out), `/account/`, `/billing/credits/`, `/feedback/`, `/qr/`, `/transfer/`, `/receipts/`, `/links/`, `/todo/`, and open/close the account menu and the notification dropdown on one of them. | No console **errors**. Acceptable: Tailwind's warning that `cdn.tailwindcss.com` should not be used in production. No failed requests (4xx/5xx) for scripts, styles, images or fonts: htmx (`unpkg.com/htmx.org@2.0.3`), Alpine.js, Font Awesome, the logo and the favicon all load. |
| 2 | Check that Font Awesome icons render. | Icons in the header links, the cards and the account menu show as glyphs, not empty squares. |
| 3 | Check images. | No broken-image icons anywhere (logo in the header and on the landing page). |

### CORE-18 Logout ends access

**Purpose:** after logging out, protected pages need a login again.
**Preconditions:** logged in as `qa.user@example.com`, on `/billing/credits/`.

| # | Action | Expected result |
|---|---|---|
| 1 | Open the account menu and press **Logout**. | You land on `/` with **Login** in the header. |
| 2 | Press the browser Back button, then reload. | After the reload, you are redirected to `/account/login/?next=/billing/credits/` (a cached copy may flash before the reload; after reloading no balance or history is shown). |
| 3 | Go to `/receipts/`. | Redirected to the login page with `next=/receipts/`. |

### CORE-19 Notification bell, dropdown and page

**Purpose:** the in-app notifications reach the bell, and the dropdown and list keep the keyboard
user's place.
**Preconditions:** register and confirm a fresh account (`qa.core.<random>@example.com`, password
`CoreBell-4821`; confirming it gives the welcome credits and so one notification). Then add two more
with a shell on the machine running the server (`E2E_DB_NAME` as in RCPT-44; without a shell mark
the steps that need `Second notice` and `Third notice` **BLOCKED (needs-setup)**):

```bash
ENVIRONMENT=local DJANGO_SETTINGS_MODULE=tests_e2e.receipts_e2e_settings E2E_DB_NAME=db.e2e-ai.sqlite3 \
  python manage.py shell -c "from apps.accounts.models import User; from apps.core.services import notifications as n; u = User.objects.get(email='<address>'); n.notify(u, source='qa', title='Second notice', url='/qr/'); n.notify(u, source='qa', title='Third notice')"
```

Sign in as that account.

| # | Action | Expected result |
|---|---|---|
| 1 | On `/`, look at the bell (left of the person icon). | A bell with a count badge: `3` (`1` if you skipped the shell step; `99+` above 99). Its accessible name is "Notifications" plus the badge's sentence (e.g. "3 new notifications"); the button has no `aria-label`. |
| 2 | Click the bell. | A panel opens under it (spanning the header on a phone) with the heading **Notifications**, newest first: `Third notice`, `Second notice`, `Welcome! We added 1000 credits to your account` (with the body "Use them in any ZipTrigo app. Your credits history shows where they go."), and a **View all notifications** link at the bottom (no **Mark all as read**: opening the panel has already marked these read). The badge clears at once and the rows are highlighted (bold, a dot with the screen-reader text "New:"). |
| 3 | Click the panel's heading text `Notifications` (not a link; focus drops to the page), then press `Escape`. | The panel closes and focus returns to the bell. Escape works wherever the focus is, not only while it is inside the bell's wrapper. |
| 4 | Reopen the panel and click the "x" **Dismiss: Third notice** at the right of the first row. | The row disappears and the other two stay. Keyboard focus does **not** fall to the page body: it lands on the control now at the same place (the next row's dismiss button, or the last one). Press `Tab` once: focus moves on from there. |
| 5 | Click `Second notice` (the whole row is a link). | You are on `/qr/`. Reopen the panel: `Second notice` is gone (opened); the welcome notification is still listed, not highlighted (read). |
| 6 | Click **View all notifications**. | `/notifications/` (title `Notifications - ZipTrigo`): "You're all caught up." above a list with `Second notice` and the welcome notification (read ones are lighter, no dot, no "Mark as read" button). `Third notice` is absent (dismissed). |
| 7 | With the keyboard focus the **Dismiss** button of the first row and press `Enter`. | The row is removed in place and focus stays inside the list (on the control now at that place), not on `<body>`. |
| 8 | Dismiss the last row. | The list reads "No notifications yet."; focus goes to the list container itself (no ring is drawn for it on a mouse click). |
| 9 | Open the bell's panel. | "No new notifications." and **View all notifications**. |
