# Core: site shell

**Scope.** Everything every page shares (`apps/core/templates/core/base.html`) and the landing page
(`core/home.html`): the header (logo, one link per registered product, Login link or
account menu), the footer (Feedback, copyright), the product cards on `/`, the "login
required" redirects of the product and account pages, unknown URLs, the API docs at `/api/docs`,
light/dark theme, the layout at phone width (375 px), page titles, favicon and logo, and console
errors. Signing in itself is covered in `accounts.md`; here it is only a step.

## Preconditions

- The test server is running at `http://localhost:8765` (`python tests_e2e/ai/serve.py`, fresh
  database). All URLs below are relative to it.
- Seeded accounts (password `QaE2e-Passw0rd!` for all): `qa.user@example.com` (name "QA User",
  100 credits), `qa.admin@example.com` (superuser, "QA Admin").
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

---

### CORE-01 Landing page, logged out

**Purpose:** the landing page renders its content, logo and product cards for an anonymous visitor.
**Preconditions:** logged out; desktop width (1280 px or wider).

| # | Action | Expected result |
|---|---|---|
| 1 | Go to `/`. | HTTP 200. The browser tab title is exactly `ZipTrigo`. The tab shows the ZipTrigo favicon (`/static/core/images/logo_32x32.png`, declared with `<link rel="icon">`), not a generic globe. |
| 2 | Look at the main content. | A centred 128 x 128 logo image (alt text `ZipTrigo logo`) that loads (not a broken-image icon); under it the heading **Welcome to ZipTrigo** (H1) and the line "A set of small tools under one account." |
| 3 | Look at the product cards. | Exactly three cards, in the order of the reference table, each with its icon, title and description exactly as listed. At this width they sit in a two-column grid (the third card alone on the second row, left column). |
| 4 | Hover each card. | The border colour changes and a shadow appears (hover state); the cursor is a pointer. |
| 5 | Check the page for leftovers. | No raw template syntax (`{{`, `{%`), no `None`, no empty cards, no text overlapping. |

### CORE-02 Header and footer, logged out

**Purpose:** the shared header and footer show the anonymous variant.
**Preconditions:** logged out, on `/`.

| # | Action | Expected result |
|---|---|---|
| 1 | Look at the header (top bar). | Left: a small logo plus the text **ZipTrigo**, then the links **QR Codes**, **File Transfer**, **Receipts**, each with its icon. Right: a **Login** link (no theme button: the theme is chosen on the account page). There is no person icon / account menu. |
| 2 | Look for a credit balance in the header. | None is shown (the base template has no balance in the header; the balance is on the account page, see CORE-08). |
| 3 | Look at the footer. | A **Feedback** link in the middle (no FAQ link: there is no FAQ page); on the right, in small grey text, `© <current year> Joao Coelho. Licensed under MIT.` (the year is the current one, e.g. `© 2026`). The footer sits at the bottom of the window even though the page content is short (no gap below it). |
| 4 | Click **ZipTrigo** in the header. | Stays on / reloads `/`. |
| 5 | Click **Login**. | Goes to `/account/login/` (title `Login - ZipTrigo`, heading **Sign in**, fields **Email** and **Password**, button **Sign in**, links "Create one" and "Forgot your password?"). The header and footer are the same as on `/`. |

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
| 1 | Look at the header's right side. | A person icon button (tooltip "Account"); no theme button and no **Login** link. |
| 2 | Click the person icon. | A dropdown opens under it, right-aligned and fully inside the window: first line **QA User** (bold, not a link), then **Settings** (cog icon) and **Logout** (sign-out icon). |
| 3 | Click anywhere outside the dropdown. | It closes. |
| 4 | Open it again and press `Escape`. | It closes and keyboard focus returns to the person icon (a focus ring is visible on it). |
| 5 | Open it and click **Settings**. | Goes to `/account/` (**Account Settings**). |
| 6 | Hover the name line in the dropdown. | A tooltip with the full name (`QA User`); the text is selectable, not clickable. |
| 7 | Go to `/account/logout/` by typing it in the address bar (a GET). | HTTP 405 (method not allowed; a blank or plain error page). You are **still logged in**: going back to `/` still shows the person icon. |

### CORE-07 Logged-in landing page and product navigation

**Purpose:** the landing page and header links work for a signed-in user.
**Preconditions:** logged in as `qa.user@example.com`.

| # | Action | Expected result |
|---|---|---|
| 1 | Go to `/`. | Same content as CORE-01 (heading, three cards); no personal greeting is expected. |
| 2 | Click each card in turn (use Back between them). | **QR Codes** opens `/qr/` (heading **QR codes**), **File Transfer** opens `/transfer/` (heading **Your transfers**), **Receipts** opens `/receipts/` (heading **Receipts**). No login page in between. |
| 3 | On each product page, click the header links **QR Codes**, **File Transfer**, **Receipts** and **ZipTrigo**. | Each goes to its page directly; the header (logo, links, toggle, person icon) and footer look the same on every page. |

### CORE-08 Where the credit balance is shown

**Purpose:** the balance from `apps.billing` reaches the pages that show it, and the header shows
none.
**Preconditions:** logged in as `qa.user@example.com` (100 credits on a fresh database).

| # | Action | Expected result |
|---|---|---|
| 1 | Look at the header on `/`. | No credit balance in the header (by design of `core/base.html`; if the product owner expects one, note it). |
| 2 | Go to `/account/`. | Section **Credits**: "Current balance" with **100 credits** and a **Usage History** button. |
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
| 2 | Scroll the operation list. | Groups of endpoints including `/api/auth/login`, `/api/billing/...`, `/api/qr/...`, `/api/ft/...` and `/api/receipts/...`. There is **no** `/api/auth/signup` (removed in issue #117). |
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
| 4 | Choose **Dark**. | The page reloads by itself (no Save button) into dark: `<html>` has `data-theme="dark"` and class `dark`; dark grey background, light text; header and footer dark grey. **Dark** is selected after the reload. |
| 5 | In dark mode, go to `/`, `/feedback/` and `/billing/credits/`. | Every page is dark too (the choice is the account's, so it applies to all pages); all text and input fields are readable (no dark text on a dark background, no white boxes with white text). |
| 6 | Choose **Light** on `/account/`. | Reloads into light: `<html>` has `data-theme="light"` and no `dark` class. |
| 7 | With **Light** saved, emulate a dark OS colour scheme and reload. | The page stays light: an explicit choice wins over the system. |
| 8 | Choose **System** (OS emulation still dark). | Reloads into dark (`data-theme="system"`, class `dark` added by the boot script). While in **System**, switch the emulated scheme back to light **without reloading**: the page follows it live. |
| 9 | Sign out and sign in again (or open a fresh browser profile and sign in); open `/account/` and save **Dark**, then sign out. | After signing out the page follows the system again (light), and the choice is waiting on the account after signing back in (it is stored on the account, not in the browser). |
| 10 | Look for a flash of the wrong theme on reload while in dark mode. | No visible white flash before dark styles apply (the server renders the `dark` class on `<html>` before any CSS loads). Tailwind's Play CDN may cause a brief unstyled flash; note it if noticeable. |

### CORE-13 Keyboard navigation of the header

**Purpose:** the header is usable with the keyboard alone.
**Preconditions:** logged in as `qa.user@example.com`, on `/`, focus at the top of the page.

| # | Action | Expected result |
|---|---|---|
| 1 | Press `Tab` repeatedly. | Focus moves in order: **ZipTrigo**, **QR Codes**, **File Transfer**, **Receipts**, the account icon, then into the page (the product cards), then the footer links. Every focused element shows a visible focus indicator. |
| 2 | With the account icon focused press `Enter`. | The menu opens; its `aria-expanded` becomes `true`. |
| 3 | Press `Tab`. | Focus moves to **Settings**, then **Logout**. |
| 4 | Press `Escape`. | The menu closes, `aria-expanded` is `false`, focus is back on the account icon. |

### CORE-14 Phone width (375 px): landing page and header, logged out

**Purpose:** the shell works on a phone-sized viewport.
**Preconditions:** logged out; resize the viewport to 375 x 812 (mobile preset), then reload `/`.

| # | Action | Expected result |
|---|---|---|
| 1 | Look at the whole page, then try to scroll horizontally. | No horizontal scrolling: the page is not wider than the viewport (`document.documentElement.scrollWidth` <= 375). Nothing is cut off at the right edge. |
| 2 | Look at the header. | One line: the logo and **ZipTrigo**, the four product links as icons only (QR Codes, File Transfer, Receipts, Short URLs; each name is its tooltip and accessible name), and **Login** at the right. Nothing overlaps, wraps or is clipped. Take a screenshot. (The product names appear next to the icons from 768 px wide.) |
| 3 | Look at the product cards. | One column, full width with side padding; text wraps inside the cards. |
| 4 | Look at the footer. | **FAQ**, **Feedback** and the copyright line fit within the width (the copyright is `whitespace-nowrap`, so check it isn't pushed off-screen). |
| 5 | Tap **Login**. | The login form fits the width; the inputs are full width. |

> Note for the reviewer: the header has no mobile menu, and the footer copyright never wraps, so
> overflow at 375 px is plausible. Record exactly what you see, with a screenshot, and the measured
> `scrollWidth`.

### CORE-15 Phone width (375 px), logged in

**Purpose:** the account menu and product pages work on a phone.
**Preconditions:** viewport 375 x 812; logged in as `qa.user@example.com`.

| # | Action | Expected result |
|---|---|---|
| 1 | Look at the header. | Still one line: the four product icons, the notification bell and the person icon (no icon pushed onto a second line). It stays one line at 320 px too, where **ZipTrigo** next to the logo is hidden. |
| 2 | On `/`, tap the person icon. | The dropdown opens fully inside the viewport (nothing cut off at the right or left edge); **QA User**, **Settings**, **Logout** are readable and tappable. |
| 3 | Tap outside it. | It closes. |
| 4 | Open `/qr/`, `/transfer/`, `/receipts/`, `/account/`, `/links/`, `/billing/credits/` and `/feedback/` in turn. | Each page has no horizontal page scroll (a wide table may scroll inside its own box, e.g. the credits table, but the page itself must not). Headings and primary buttons are visible without zooming. |
| 5 | Reset the viewport to desktop. | - |

### CORE-16 Page titles

**Purpose:** every page has a sensible, distinct browser-tab title.
**Preconditions:** logged in as `qa.user@example.com` (log out for the login/register rows).

| # | Action | Expected result |
|---|---|---|
| 1 | Visit each page and read the tab title. | Titles at the time of writing: `/` `ZipTrigo`; `/qr/` `QR codes - ZipTrigo`; `/transfer/` `File Transfer - ZipTrigo`; `/transfer/send/` `Send files - ZipTrigo`; `/receipts/` `Receipts - ZipTrigo`; `/receipts/upload/` `Upload receipts - ZipTrigo`; `/account/` `Account Settings - ZipTrigo`; `/billing/credits/` `Credits History - ZipTrigo`; `/feedback/` `Send feedback - ZipTrigo`; `/account/login/` `Login - ZipTrigo`; `/account/register/` `Register - ZipTrigo`; `/api/docs` `ZipTrigo API`. None is empty, `None` or a raw template tag. |
| 2 | Compare them. | Record as a UX observation (not a failure) that the account, billing, feedback, login and register titles lack the ` - ZipTrigo` suffix the product pages use, and that `/qr/` is just "Dashboard". |

### CORE-17 Console errors and broken assets

**Purpose:** the main pages load without JavaScript errors or missing files.
**Preconditions:** open the browser console / network log before each page load. Logged in as
`qa.user@example.com` except for the login page.

| # | Action | Expected result |
|---|---|---|
| 1 | Load `/`, `/account/login/` (logged out), `/account/`, `/billing/credits/`, `/feedback/`, `/qr/`, `/transfer/`, `/receipts/`, and open/close the account menu on one of them. | No console **errors**. Acceptable: Tailwind's warning that `cdn.tailwindcss.com` should not be used in production. No failed requests (4xx/5xx) for scripts, styles, images or fonts: htmx (`unpkg.com/htmx.org@2.0.3`), Alpine.js, Font Awesome, the logo and the favicon all load. |
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
