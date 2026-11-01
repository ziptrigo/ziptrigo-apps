# Core: site shell

**Scope.** Everything every page shares (`apps/core/templates/core/base.html`) and the landing page
(`core/home.html`): the header (logo, one link per registered product, the notification bell and
account menu for a signed-in user or a Login link), the footer (Feedback, copyright), the "Skip to
content" link, the flash messages, the shared confirmation dialog (`core/partials/confirm_modal.html`),
the product cards on `/`, the "login required" redirects of the product and account pages
(including an htmx request whose session has ended), unknown URLs, the API docs at `/api/docs`,
light/dark theme, times shown in the visitor's zone (`{% local_time %}`), the shared field-error
behaviour of refused forms (`core/js/field_errors.js`), the fonts, focus ring and reduced motion of
`core/css/site.css`, the layout at phone width (375 px), page titles, favicon and logo, and console
errors. Signing in itself is covered in `accounts.md`; here it is only a step. The notification
bell, dropdown and page are in `notifications.md` (they used to be CORE-19).

## Preconditions

- The test server is running at `http://localhost:8765` (`python tests_e2e/ai/serve.py`, fresh
  database), `preflight.md` passed. All URLs below are relative to it.
- Emails: open `/__e2e__/mail/?to=<address>` and reload until the email is there (up to a minute).
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
| 1 | Look at the header's right side. | A bell button (accessible name "Notifications"; the bell itself is tested in `notifications.md`) and a person icon button (tooltip "Account"); no theme button and no **Login** link. |
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
| 1 | Go to `/`. | Same content as CORE-01 (heading, five cards); no personal greeting is expected. |
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
| 5 | Press `Enter` to open it again, then `Tab` past **Logout**. | The menu closes by itself as focus leaves it (focus moves on into the page). |

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
| 1 | Load `/`, `/account/login/` (logged out), `/account/`, `/billing/credits/`, `/feedback/`, `/qr/`, `/transfer/`, `/receipts/`, `/links/`, `/todo/`, and open/close the account menu and the notification dropdown on one of them. | No console **errors**. Acceptable: Tailwind's warning that `cdn.tailwindcss.com` should not be used in production. No failed requests (4xx/5xx) for scripts, styles, images or fonts: htmx (`unpkg.com/htmx.org@2.0.3`), Alpine.js (`cdn.jsdelivr.net`), Font Awesome (`cdnjs.cloudflare.com`), `/static/core/css/site.css`, the self-hosted font `/static/core/fonts/figtree-latin.woff2`, the shared scripts under `/static/core/js/` (`components.js`, `local_time.js`, `field_errors.js`, `tabs.js`, `skeleton.js`, `date_picker.js`), the logo and the favicon all load. |
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

### CORE-19 Notification bell, dropdown and page (moved)

The notification cases moved to [`notifications.md`](notifications.md) (NOTIF-02 to NOTIF-12), which
also covers every product event that creates a notification. Nothing to run here.

### CORE-20 Setup: a fresh account for the shell cases

**Purpose:** a confirmed account of this file's own, so the cases below can change its state.
**Preconditions:** logged out.

| # | Action | Expected result |
|---|---|---|
| 1 | On `/account/register/` enter **Name** `Core Tester`, **Email** `qa.core.<random>@example.com` (record it as `CORE_USER`), **Password** `CoreBell-4821`; **Create account**. | **Check your email**. |
| 2 | Open `/__e2e__/mail/?to=<CORE_USER>` (reload until it arrives), follow the "Confirm your ZipTrigo account email" link and press **Confirm my email**. | **Email Confirmed!**. |
| 3 | Sign in as `CORE_USER` / `CoreBell-4821`. | You land on `/`; the bell shows `1` (the welcome credits; `notifications.md` tests it). |

### CORE-21 The confirmation dialog and flash messages

**Purpose:** the shared `confirmModal` dialog is keyboard friendly and a plain-POST action ends in a
flash message that stays until dismissed.
**Preconditions:** CORE-20, signed in as `CORE_USER`. The dialog is the one every product uses; To-do's
"Regenerate address" is a harmless place to try it.

| # | Action | Expected result |
|---|---|---|
| 1 | Open `/todo/settings/`. Under **Add tasks by email** record the address shown. Press **Regenerate…**. | A dialog over a dimmed page (`role="dialog"`, `aria-modal="true"`, named by its title): title **Regenerate address**, the text "Your old address stops working immediately. Mail already sent to it is dropped, so update any contacts or rules that use it.", buttons **Cancel** and **Regenerate** (red). Keyboard focus is on **Cancel**. |
| 2 | Press `Escape`. | The dialog closes; focus is back on **Regenerate…**. The address is unchanged. |
| 3 | Open it again and click the dimmed backdrop outside the dialog. | It closes. Open it again and press **Cancel**: it closes too. |
| 4 | Open it and press **Regenerate**. | The page reloads on `/todo/settings/` with a green message at the top of the content, "Your new address is ready. The old one no longer works.", with a check icon and an "x" button named "Dismiss"; the message has `role="status"` (an error one would be red with `role="alert"`). The address is different. |
| 5 | Wait 10 seconds. | The message is still there (it is never dismissed automatically). |
| 6 | Press its "x". | The message fades out. Reload: it does not come back (shown once). |
| 7 | Resize to 375 px wide and open the dialog again. | It fits the width with a margin on both sides; both buttons are visible. **Cancel** it; reset the viewport. |

### CORE-22 An htmx action after the session ended goes to a full login page

**Purpose:** a form that htmx posts while the session is gone navigates to the login page (with `next`
set to the page you were on), never renders the login page inside the form (issue #152).
**Preconditions:** CORE-20. Signed in as `CORE_USER` in tab A on `/todo/`; tab B open on the same site.

| # | Action | Expected result |
|---|---|---|
| 1 | In tab B log out. | - |
| 2 | In tab A type `Core session check` into the quick-add box and press **Add task**. | The whole tab navigates to `/account/login/?next=/todo/` (a normal page with one header and one footer); the login page is **not** shown inside the To-do list. |
| 3 | Sign in as `CORE_USER`. | You land on `/todo/` (Today). `Core session check` was not created (search **All**). |

### CORE-23 Times are shown in the visitor's time zone

**Purpose:** `{% local_time %}` renders a UTC fallback that the browser rewrites in its own zone.
**Preconditions:** CORE-20, signed in as `CORE_USER`. Note the browser's time zone
(`Intl.DateTimeFormat().resolvedOptions().timeZone` in the console).

| # | Action | Expected result |
|---|---|---|
| 1 | Open `/billing/credits/` and inspect the date of the welcome line ("A gift for your new account"). | A `<time datetime="<UTC ISO, ending in +00:00>" data-local-time="iso">` element whose text is `YYYY-MM-DD HH:MM` **without** "UTC": the same instant in the browser's zone (in the console, `new Date(el.dateTime)` shows the same local date and time). In a zone other than UTC the hour differs from the `datetime` attribute's. |
| 2 | Open `/notifications/` and hover the relative time of the welcome notification (`just now` or `N minutes ago`). | Its tooltip is the exact local time in the same `YYYY-MM-DD HH:MM` form, without "UTC"; the visible text stays relative. |
| 3 | (If the browser tool can override the time zone, e.g. the DevTools "Sensors" > Location, or CDP `Emulation.setTimezoneOverride`) set `America/Chicago` and reload `/billing/credits/`. | The date and time move to Chicago's wall clock (5 or 6 hours before UTC); the `datetime` attribute is unchanged. Remove the override. |
| 4 | (If the tool can disable JavaScript) disable it and reload `/billing/credits/`. | The same cell reads `YYYY-MM-DD HH:MM UTC` (the server's fallback, marked UTC). Re-enable JavaScript. |

### CORE-24 Field errors of a refused form

**Purpose:** the shared field-error behaviour (`core/js/field_errors.js`, issue #219) on a form with
several fields: the message box above the form, the message under each field, the focus, and what
clears a message.
**Preconditions:** logged out, on `/account/register/` (the browser's own checks pass for every input
below: the name is not empty, only spaces).

| # | Action | Expected result |
|---|---|---|
| 1 | **Name** `Core Errors`, **Email** `qa.core.err.<random>@example.com`, **Password** `abcdefg` (7 letters, no digit); **Create account**. | HTTP 422, the form is replaced in place (URL unchanged). In the form's red block: `Password: Password must contain at least one digit.` Under the password field: `Password must contain at least one digit.` (red, below the field's label and hint), the field is empty again (passwords are not re-rendered), has a red border, `aria-invalid="true"` and an `aria-describedby` whose first id is that message's `<p>` (its id ends in `-error`). Keyboard focus is on **Password**, not on **Name** (which the page autofocuses: an autofocus field the swap rendered does not count as one the person is typing in). |
| 2 | Clear **Name** and type three spaces; type `abcdefg` into **Password** again (a password is never sent back into the form); **Create account**. | The block has two lines, `Name: This field is required.` first, then the password line; both fields carry their own message under them and `aria-invalid`; focus is on **Name** (the first invalid field). |
| 3 | Type one character into **Password**. | Only the password's message (and its red border and `aria-invalid`) goes away; the name's message stays; the red block above is unchanged until the next submission. |
| 4 | Switch to dark mode (OS emulation; logged out the page follows the system) and repeat step 2. | The red block, the messages under the fields and the red borders are readable on the dark background. Switch back. |
| 5 | Leave the page without creating the account. | - |

### CORE-25 Fonts, focus ring and reduced motion

**Purpose:** the design system's shared CSS (`core/css/site.css`, issue #214) is in effect.
**Preconditions:** CORE-20, signed in as `CORE_USER`.

| # | Action | Expected result |
|---|---|---|
| 1 | On `/` run `getComputedStyle(document.body).fontFamily` and `document.fonts.check('16px Figtree')`. | The font list starts with `Figtree`; the check is `true` (the self-hosted font loaded). |
| 2 | Open `/todo/settings/` and run `getComputedStyle(document.querySelector('code')).fontFamily` (the email address). | Starts with `"IBM Plex Mono"` (codes and addresses use the mono font). |
| 3 | On `/` click the **QR Codes** header link with the mouse, go back, then reach it with `Tab`. | After the click no ring stays on the link; with `Tab` a sage ring (a 2 px gap and a sage outline, drawn as a box shadow) surrounds it. In dark mode the gap is the dark background colour. |
| 4 | Open `/feedback/` and click into the textarea. | A soft sage halo around the field and a sage border (fields show it for the mouse too). |
| 5 | Emulate `prefers-reduced-motion: reduce` and reload `/`. | The logo, heading and cards appear at once, without rising in (`getComputedStyle(document.querySelector('h1')).animationDuration` is a tiny value such as `1e-06s` or `0.000001s`, i.e. 0.001 ms). Open and close the account menu: no visible transition. Remove the emulation: reloading `/` shows the rise again. |

## Out of scope

- A branded 404 page: the project has none (CORE-10 records Django's page as an observation).
- The 429 "rate limited" page and partial (`core/429.html`): rate limiting is off on this server.
- The notification bell, dropdown and page: `notifications.md`.
