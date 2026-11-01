# Short URLs

## Scope

The Short URLs product's web UI (`apps/short_url`):

- the dashboard `/links/` (empty state, list, status tabs, search, tag filter, sort (column
  header, and the "Sort by" bar at phone width), the pager, the row menu, copy button);
- the editor `/links/new/` and `/links/<id>/edit/` (destination, custom alias, title, tags,
  expiry, click limit, password, "count bots", UTM fields, the live "your link will be" line, the
  credit-cost confirmation) and a link's own page `/links/<id>/` (copy, QR code, Edit, Stats,
  Preview, Disable/Enable, Delete);
- the public short links `/s/<code>` (the `302`, case-insensitive codes, the neutral "this link
  isn't available" page for every dead link), the preview page `/s/<code>+` with its report form,
  and the password page `/s/<code>/unlock`;
- the per-link stats page `/links/<id>/stats/` (totals, the paid "detailed analytics", ranges);
- credits: creating and following a link is free, extras cost credits once per link; the cost
  confirmation panel and dialogs, and the "the cost changed" re-ask; prices of 0; the free
  allowance and the hard cap on links;
- access control: logged-out visitors, another user's link ids, an owner whose account staff
  deactivated;
- the admin side of abuse handling: blocked domains, reports and the auto-hold threshold (a link
  "Under review"), a taken-down link and the takedown email to its owner, the settings singleton;
- dark mode, phone width and keyboard use of the pages.

Not covered here: the JWT API (`/api/s/`) and the CLI (`inv shorturl`), which are covered by unit
tests (`apps/short_url/tests/test_api_*.py`, `admin/tests/test_shorturl.py`) and by the Playwright
test `tests_e2e/test_short_url.py`; GeoIP country statistics (no database on this server, so the
country list is always "Unknown"); title fetching from the destination page (needs the internet and
the worker, see below); the background jobs (click rollup and purge).

## Preconditions

- The test server is running at `http://localhost:8765`, freshly restarted (empty database), as
  described in [`README.md`](README.md): Postgres, real SMTP email, rate limiting off. Short URLs
  store nothing in S3.
- **Email** (only the takedown notice, SU-30, and the registration in SU-27): open
  `/__e2e__/mail/?to=<address>` and reload until it arrives, up to a minute. Links in an email are
  absolute (`http://localhost:8765/...`) and clickable there; the HTML body is shown as source.
- **Registering and confirming a fresh account** (SU-27): on `/account/register/` fill **Name**,
  **Email**, **Password**, press **Create account**; you land on **Check your email**. Open
  `/__e2e__/mail/?to=<address>`: an email `Confirm your ZipTrigo account email` with a link to
  `/account/confirm-email/<token>/`. Open it and press **Confirm my email** (**Email Confirmed!**),
  then sign in on `/account/login/`. The account gets 1,000 welcome credits on confirmation.
- **Two sessions.** Cases that need the owner and the admin at once use the main window for one
  and a private window (separate cookies) for the other; "the visitor window" is a private window
  where nobody is signed in. Where a case says "in another tab", it means the same session.
- **Settings changed in the admin are always restored** at the end of the case that changed them;
  if a case is abandoned midway, restore them by hand (`/admin/short_url/shorturlsettings/`:
  prices 1, Free active links 25, Max links 500, Auto hold report threshold 0).
- `BASE_URL` is `http://localhost:8765`, so a short link reads `http://localhost:8765/s/<code>`.
- **Destinations are real internet addresses** (`https://example.com/...`): the site refuses
  `localhost` and private addresses as a destination, on purpose. Following a short link in the
  browser therefore needs the internet; without it the browser shows its own "can't reach this
  site" page for `example.com`, which is still the proof that the redirect happened (read the
  address in the error page, or the `Location` header in the network log). Where a case says
  "follow", either is fine. **Without leaving the page**, a console call on any page of the site
  does the same visit: `fetch('/s/<code>', {redirect: 'manual'}).then(r => [r.type, r.status])`
  gives `['opaqueredirect', 0]` for a redirect (scripts can't read a redirect's `Location`) and
  `['basic', 404]` for the neutral page; `{method: 'HEAD', redirect: 'manual'}` is a HEAD request.
  A GET made this way counts as a click like a navigation does (same `User-Agent`).
- Tasks run inline, so a link created **without a title** makes the server try to fetch the
  destination page's title during the request: it needs the internet, takes up to a few seconds,
  and a failure just leaves the title empty. Cases below give every link a title to avoid that,
  except SU-04, which checks the empty-title behaviour on purpose.
- **Bots are not counted as clicks by default.** The site classes a `User-Agent` that says
  `HeadlessChrome`, `curl`, `python-requests`, `bot` and the like as a bot (and no `User-Agent` at
  all). If the agent's browser identifies itself that way (check `navigator.userAgent`), its visits
  go to "Bot clicks" instead of "Clicks", and the click counts below will not go up; a link with a
  **click limit** counts every visit regardless (SU-11), so that case is unaffected. To make the
  counts predictable in such a browser, tick "Count bots and link previews as clicks" on the links
  you follow (SU-05, SU-14), or use a browser with a normal `User-Agent`.
- Cases run in order; later ones reuse links created earlier.
- `qa.user@example.com` has no links at the start. Prices are the defaults: **1 credit** for a
  custom alias, a password, detailed analytics and each active link beyond the free allowance
  (**25** free active links per user, a hard cap of **500** links).

## Test data

| Name | Value |
|---|---|
| Main user | `qa.user@example.com` / `QaE2e-Passw0rd!` (100 credits) |
| Another user | `qa.broke@example.com` / `QaE2e-Passw0rd!` (0 credits) |
| Admin | `qa.admin@example.com` / `QaE2e-Passw0rd!` |
| Plain link | destination `https://example.com/plain?x=1`, title `QA Plain` |
| Alias link | destination `https://example.com/alias`, alias `QA-Sale`, title `QA Sale`, tags `promo, spring` |
| Limited link | destination `https://example.com/limited`, title `QA Limited`, click limit `2` |
| Expiring link | destination `https://example.com/expiring`, title `QA Expiring`, expires in 2 minutes |
| Protected link | destination `https://example.com/secret`, title `QA Secret`, password `open-sesame` |
| Visitor | a private/incognito browser context where nobody is logged in |

Record these as you go:

- `PLAIN_ID`, `PLAIN_CODE` (7 letters/digits) from SU-04; `SALE_ID` from SU-05;
  `LIMITED_ID`/`LIMITED_CODE`, `PROTECTED_ID`/`PROTECTED_CODE` from SU-12 and SU-13.

A link's id is in its page URL `/links/<id>/`; its code is the last part of the short link.

---

### SU-01 Logged-out visitors are sent to the login page

**Purpose:** every management page needs a login; the public short links do not.
**Preconditions:** not logged in (fresh browser session).

| # | Action | Expected result |
|---|---|---|
| 1 | Go to `http://localhost:8765/links/`. | Redirected to `/account/login/?next=/links/`. |
| 2 | Go to `/links/new/`, then `/links/00000000-0000-0000-0000-000000000000/`, `.../edit/`, `.../stats/` and `.../qr.png`. | Each redirects to the login page with its path in `next`. Nothing about any link is revealed. |
| 3 | Go to `/s/doesnotexist`. | No login needed: the page "This link isn't available" (HTTP 404, no mention of why). |
| 4 | Go to `/links/new/` and sign in as `qa.user@example.com` / `QaE2e-Passw0rd!`. | Lands on `/links/new/` (the `next` target). |

### SU-02 Empty dashboard and navigation

**Purpose:** a user with no links sees a clean empty state.
**Preconditions:** logged in as `qa.user@example.com`.

| # | Action | Expected result |
|---|---|---|
| 1 | Go to `/` and click **Short URLs** in the top navigation (link icon). | Lands on `/links/`. Tab title "Short URLs - ZipTrigo". |
| 2 | Look at the page. | Heading "Short URLs"; a **New link** button; status tabs All, Active, Disabled, Expired, Click limit reached, Taken down (a tab shows its count only when it is above 0, so none has one yet); a search box "Search code, destination, title or tag"; a list header (Short link, Destination, Status, Clicks, Created); an empty state instead of rows: a link icon, **No links yet**, "Shorten a link, track it and share it as a QR code." and a **Create link** button. No tag filter yet. No broken icons, raw `{{ }}`/`{% %}`, or console errors. |
| 3 | Click **Create link** in the empty state. | Goes to `/links/new/`. |

### SU-03 The new link form

**Purpose:** the form offers every field and says what extras cost.
**Preconditions:** logged in as `qa.user@example.com`.

| # | Action | Expected result |
|---|---|---|
| 1 | Open `/links/new/`. | Heading "New link". Fields: Destination (required, `url` input), Custom alias (optional), Title, Tags, Expires (date and time), Click limit, Password, a checkbox "Count bots and link previews as clicks" (unchecked), a collapsed "Campaign tracking (UTM)" section, **Create link** and Cancel. |
| 2 | Read the alias help text. | Says 3-32 letters, digits, hyphens or underscores, that capitals don't matter, and **"An alias costs 1 credit, once."** |
| 3 | Read the password help text. | Says visitors must enter it, and **"A password costs 1 credit, once per link."** |
| 4 | Look at the line "Your link will be ...". | Reads `http://localhost:8765/s/xxxxxxx`. Typing `Spring-Sale` in the alias field updates it live to `http://localhost:8765/s/Spring-Sale`; clearing the field restores `xxxxxxx`. |
| 5 | Expand "Campaign tracking (UTM)". | Five fields: UTM source, UTM medium, UTM campaign, UTM term, UTM content. |
| 6 | Click **Cancel**. | Back on `/links/`. |

### SU-04 Create a plain link (free), without a title

**Purpose:** creating a link costs nothing and gets a random code.
**Preconditions:** logged in as `qa.user@example.com`; note the credit balance first (open
`/billing/credits/`).

| # | Action | Expected result |
|---|---|---|
| 1 | Open `/links/new/`, enter destination `https://example.com/plain?x=1`, leave everything else empty, click **Create link**. | The button disables while it works (it may take a few seconds if the server tries to fetch the page title). No cost panel appears. The browser lands on `/links/<id>/` with a green message "Link created." Record `PLAIN_ID`. |
| 2 | Read the link page. | A heading showing the code (or the fetched title if the server could reach the internet); status pill **Active**; the short link `http://localhost:8765/s/<7 characters>` with a **Copy** button; the destination `https://example.com/plain?x=1`; **Clicks 0**; **Created** with today's date written like `Nov 1, 2026`; a QR code image and "Download QR code"; buttons **Edit**, **Stats**, **Preview**, **Disable**, **Delete**. Record `PLAIN_CODE`. |
| 3 | Open `/billing/credits/`. | The balance is unchanged (100 credits) and there is no new ledger line. |

### SU-05 Create a link with an alias: the cost is confirmed first

**Purpose:** credits are never spent without the user having seen the cost.
**Preconditions:** SU-04 done; balance 100.

| # | Action | Expected result |
|---|---|---|
| 1 | Open `/links/new/`. Enter destination `https://example.com/alias`, alias `QA-Sale`, title `QA Sale`, tags `promo, spring`. Click **Create link**. | Nothing is created yet. An amber panel appears under the form: "This costs **1 credit**, one-off (custom alias 1). Your balance is 100 credits." with a button **Create the link for 1 credit**. |
| 2 | Open `/billing/credits/` in another tab. | Still 100 credits. |
| 3 | Back on the form, click **Create the link for 1 credit**. | "Link created." The link page shows the heading "QA Sale", the short link `http://localhost:8765/s/QA-Sale` (the capitals are kept), tags `promo` and `spring` as chips. Record `SALE_ID`. |
| 4 | Open `/billing/credits/`. | Balance **99**; a ledger line for 1 credit spent, described as a custom alias for `/s/QA-Sale`. |
| 5 | Open `/links/new/` again and try to create another link with alias `qa-sale` (any case), title `Dup`. | The form shows "Alias: That alias is not available." at once (no cost panel) and nothing is charged (balance still 99). It does not say whose alias it is. |

### SU-06 Validation messages

**Purpose:** a refused form explains itself, next to the form, and keeps what was typed.
**Preconditions:** logged in as `qa.user@example.com`; balance 99.

| # | Action | Expected result |
|---|---|---|
| 1 | On `/links/new/`, enter destination `ftp://example.com/file` and submit. | A red line in the form: "Destination: The address must start with http:// or https://." (Your browser may block the submit first because the field is `type=url`; if so, enter `javascript:alert(1)` and expect the same kind of refusal.) The field keeps its value. |
| 2 | Destination `https://127.0.0.1/admin`. | "Destination: That address is not allowed." |
| 3 | Destination `http://localhost:8765/s/abc`. | "Destination: That address is not allowed." (a `localhost` host is refused). |
| 4 | Destination `https://example.com/x y` (with a space). | "Destination: The address cannot contain spaces or control characters." (The browser may refuse this one before the server sees it; that is fine.) |
| 5 | Destination `https://example.com`, alias `ab`. | "Alias: Use 3-32 letters, digits, hyphens or underscores." |
| 6 | Alias `admin`, then alias `help`. | Both: "Alias: That alias is not available." (reserved words). |
| 7 | Alias `bad alias!`. | The shape error from step 5. |
| 8 | Tags `ok, bad tag!`. | `Tags: "bad tag!" is not a valid tag: use up to 32 letters, digits, hyphens or underscores.` |
| 9 | Click limit `0`; then a very large one such as `99999999999`. | `0`: the browser blocks the submit (the field has `min="1"`). The large one reaches the server: "Click limit: Ensure this value is less than or equal to 2000000000." |
| 10 | Set Expires to a time in the past. | "Expires: The expiry must be in the future." |
| 11 | Count the links on `/links/`. | Still 2. None of the above created anything or charged anything. |

### SU-07 Following a link

**Purpose:** the public redirect.
**Preconditions:** SU-04 and SU-05 done.

| # | Action | Expected result |
|---|---|---|
| 1 | In the **visitor window**, open `http://localhost:8765/s/<PLAIN_CODE>`. | The browser is sent to `https://example.com/plain?x=1` (or shows its "can't reach" page for it). No login was needed. |
| 2 | Open `http://localhost:8765/s/qa-sale`, then `/s/QA-SALE` and `/s/QA-Sale/`. | All three go to `https://example.com/alias`: codes are case-insensitive, a trailing slash is fine. |
| 3 | If the tool can show headers (network log or `curl -I`), look at the first response for `/s/QA-Sale`. | Status **302** (never 301), `Location: https://example.com/alias`, `Cache-Control: no-store, private`. |
| 4 | Back in the main window, open `/links/`. | `QA Sale` shows **3** clicks and the plain link **1** (plus however many times step 1 was repeated). If your browser counts as a bot (see Preconditions) the counts stay 0; the stats page's "Bot clicks" shows them instead. |
| 5 | Send a `HEAD` request: in the console of any page of the site run `fetch('/s/<PLAIN_CODE>', {method: 'HEAD', redirect: 'manual'}).then(r => r.type)` three times (or `curl -I`). | Each answers a redirect (`opaqueredirect`; `curl -I` shows the 302), but the plain link's click count on `/links/` does not go up. |

### SU-08 The dashboard

**Purpose:** list, tabs, search, tag filter, sort, row contents, copy.
**Preconditions:** the plain and the alias links exist.

| # | Action | Expected result |
|---|---|---|
| 1 | Open `/links/`. | Two rows, newest first (`QA Sale`, then the plain link). Each row: the path `/s/<code>` with a **Copy** button, the destination (truncated, full text in a tooltip), the title for the alias link, tag chips, an **Active** pill, the click count and the created date, and a "⋮" actions menu. Tabs: All 2, Active 2, the others without a count. A **Tag** dropdown now exists with `promo` and `spring`. |
| 2 | Click **Copy** on a row. | The button reads "Copied" for a moment; pasting somewhere gives the full address `http://localhost:8765/s/<code>`. |
| 3 | Type `sale` in the search box. | After a short pause (no page reload) only `QA Sale` remains; the address bar shows `?q=sale`. Clear it with the × button: both rows return. |
| 4 | Search `example.com/plain`, then `spring`, then `"`. | The plain link; the alias link (matched by tag); for `"` no rows but the empty state **No links found** with "Try other filters or a different search." and no button. The tab counts do not change with the search (they are the user's totals). |
| 5 | Choose tag `promo`. | Only `QA Sale`; the URL has `tag=promo`. Reset the tag to "All tags". |
| 6 | At desktop width click the **Clicks** column header, then again (at phone width use the **Sort by** bar instead, SU-21). | Sorted by clicks descending, then ascending; the arrow flips and the screen reader text says "(sorted ...)". |
| 7 | Click each status tab. | Counts match; Disabled, Expired, Click limit reached and Taken down are empty ("No links found."). The tab you are on keeps your search. Reload the page: the same tab and filters stay (the URL carries them). |
| 8 | Open the "⋮" menu on a row. | Items: Stats, Edit, QR code, Preview, Disable, Delete. Escape closes it and returns focus to the "⋮" button. |

### SU-09 Edit a link: the destination changes, the code does not

**Purpose:** destinations are editable; a code is permanent.
**Preconditions:** `SALE_ID` recorded.

| # | Action | Expected result |
|---|---|---|
| 1 | Open `/links/<SALE_ID>/edit/`. | Heading "Edit link". There is **no alias field**; instead the short link `http://localhost:8765/s/QA-Sale` is shown with "The code can't be changed". Destination, title, tags are filled in. The password section has no "remove" checkbox (no password yet). |
| 2 | Change the destination to `https://example.com/alias-new` and, under "Campaign tracking (UTM)", set UTM source `news` and UTM campaign `spring`. Click **Save changes**. | "Link saved." The link page shows the destination `https://example.com/alias-new?utm_source=news&utm_campaign=spring`. |
| 3 | In the visitor window, open `/s/QA-Sale`. | Goes to `https://example.com/alias-new?utm_source=news&utm_campaign=spring`: the same short link now follows the new destination. |
| 4 | Edit again, change only the title to `QA Sale (spring)` and save. | The destination keeps its UTM parameters; the title changed; nothing was charged. |
| 5 | Edit again, enter a destination `https://example.com/x?utm_source=old` with UTM source `new`. | After saving, the destination has `utm_source=new` (the typed UTM replaces the one in the address). |

### SU-10 Disable and enable; the neutral page

**Purpose:** a dead link never says why.
**Preconditions:** the plain link exists.

| # | Action | Expected result |
|---|---|---|
| 1 | On `/links/<PLAIN_ID>/` click **Disable**. | "Link disabled." The pill reads **Disabled**; **Enable** replaces Disable (no cost mentioned: it is within the free allowance). |
| 2 | In the visitor window, open `/s/<PLAIN_CODE>`. | The page "This link isn't available. It may have expired, been switched off, or been removed." with HTTP status **404** and `Cache-Control: no-store`. |
| 3 | Open `/s/zzzzzzz` (a code that never existed). | **Exactly the same** page and status as step 2: nothing tells a disabled link from an unknown one. |
| 4 | Open `/s/<PLAIN_CODE>+` and `/s/<PLAIN_CODE>/unlock`. | The same neutral page for both. |
| 5 | On `/links/`, open the Disabled tab. | The plain link is listed with a **Disabled** pill. |
| 6 | Click **Enable** on the link page (or in the row menu). | "Link enabled." The link works again (open `/s/<PLAIN_CODE>`). Balance unchanged. |

### SU-11 Expiry and click limit

**Purpose:** a link past its expiry or its click cap stops working.
**Preconditions:** logged in as `qa.user@example.com`.

| # | Action | Expected result |
|---|---|---|
| 1 | Create the limited link: destination `https://example.com/limited`, title `QA Limited`, click limit `2`. Record `LIMITED_ID` and `LIMITED_CODE`. | Created, free. The link page shows "Click limit 2". |
| 2 | In the visitor window follow `/s/<LIMITED_CODE>` twice. | Both redirect. |
| 3 | Follow it a third time. | The neutral "This link isn't available" page (404). |
| 4 | On `/links/`, look at the row and the tabs. | A **Click limit reached** pill; the tab "Click limit reached" counts 1 and lists it; the click count reads 2. |
| 5 | Open the link page and click **Edit**; raise the click limit to `5`, save, and follow the link again. | It redirects again (3 of 5). |
| 6 | Create the expiring link: destination `https://example.com/expiring`, title `QA Expiring`, **Expires** a minute or two ahead **in your own local time** (the field is in the browser's time zone; the link page then shows the same local time). Follow it at once, then again after it has expired. | The first follow redirects; the second shows the neutral page. The dashboard shows an **Expired** pill and the Expired tab counts it. |
| 7 | Edit the expiring link and clear the expiry (empty field), save. | It works again and the pill is **Active**. (An unchanged, already-passed expiry would also have saved.) |

### SU-12 A password-protected link

**Purpose:** the password gate, its cost, and what it hides.
**Preconditions:** logged in as `qa.user@example.com`; note the balance.

| # | Action | Expected result |
|---|---|---|
| 1 | Create a link: destination `https://example.com/secret`, title `QA Secret`, password `open-sesame`. Click **Create link**. | The cost panel appears: "This costs 1 credit, one-off (password 1)". Confirm it. "Link created." The link page shows **Password: Protected**; the list row shows a lock icon before the destination. Balance is one lower. Record `PROTECTED_ID` and `PROTECTED_CODE`. |
| 2 | In the visitor window open `/s/<PROTECTED_CODE>`. | Redirected to `/s/<PROTECTED_CODE>/unlock`: "Password needed", a Password field and **Continue**. The page says nothing about the destination or the title. |
| 3 | Enter `wrong` and submit. | The form re-renders with "Incorrect password." (HTTP 422); still no destination. |
| 4 | Enter `open-sesame` and submit. | Redirected on to `https://example.com/secret`. |
| 5 | Open `/s/<PROTECTED_CODE>` again in the same visitor window. | Goes straight to the destination (remembered for this browser session). In a **new** private window it asks for the password again. |
| 6 | In a window that has not entered the password open `/s/<PROTECTED_CODE>+`. | The preview page says the link is protected by a password and that its destination is shown once it has been entered: no host, no destination, no title. |
| 7 | Back in the main window, edit the link, enter a **new** password `another-pass` and save. | No cost panel (the password was already paid for) and "Link saved." In the window that had unlocked the link, `/s/<PROTECTED_CODE>` asks for the password again (changing it locks everyone out). |
| 8 | Edit again, tick **Remove the password** and save. | The lock icon and "Protected" disappear; `/s/<PROTECTED_CODE>` redirects directly. |
| 9 | Edit again and set a password once more. | Still no cost panel (paid once per link). |

### SU-13 The preview page and reporting a link

**Purpose:** a recipient can inspect a link before following it, and report it.
**Preconditions:** the alias link exists and works.

| # | Action | Expected result |
|---|---|---|
| 1 | In the visitor window open `/s/QA-Sale+`. | "Where does this link go?"; the title; "Goes to **example.com**"; the full destination; "Created <how long ago>. Only continue if you trust where it leads." (for a link made minutes ago "Created 5 minutes ago."; within the first minute "Created just now."); a **Continue** button; a small "Report this link" button. The page is `noindex` (view source or headers: `X-Robots-Tag: noindex`). Nothing is counted as a click. |
| 2 | Click **Continue**. | Follows the short link (a click is counted). |
| 3 | Back on the preview, click **Report this link**. | A form: Reason (choose one), Details (optional), Your email (optional). |
| 4 | Submit without a reason. | The form re-renders with an error on Reason; it stays open. |
| 5 | Choose "Spam", details `QA report`, email `qa.reporter@example.com`, submit. | The form is replaced by "Thank you, your report has been submitted." No login was needed. |
| 6 | Reload the preview and report again straight away. | A form error "You already reported this link recently. Thank you." (the same visitor, the same link, within 10 minutes). |
| 7 | In the main window open `/links/<SALE_ID>/`. | Nothing shows the report or who made it. |

### SU-14 Stats and the detailed analytics

**Purpose:** totals are always shown; the detail is a paid, once-per-link extra.
**Preconditions:** the alias link has clicks (SU-07, SU-09, SU-13); balance noted.

| # | Action | Expected result |
|---|---|---|
| 1 | Open `/links/<SALE_ID>/stats/`. | Heading "Stats", the status pill and the short link. Three cards: **Clicks** (a number, with "People only: bots are left out."), **Unique visitors** ("Counted per day ..."), **Bot clicks**. Below them a panel "Detailed analytics" explaining what it adds, and a button **Turn on for 1 credit**. No chart. |
| 2 | Click **Turn on for 1 credit**. | A confirmation dialog "Turn on detailed analytics?" saying it costs 1 credit once, with a confirm button and a cancel. Cancel closes it without charging. |
| 3 | Open it again and confirm. | The page reloads with "Detailed analytics are on for this link." The balance dropped by 1. |
| 4 | Look at the detailed part. | A range selector (7 days, 30 days, 90 days, All time; 30 days is current), a "Clicks over time" chart with a "Show as a table" disclosure, and four lists: Top referrers (probably "Direct or unknown"), Top countries ("Unknown": no GeoIP database here), Devices, Browsers. The click totals in the heading match the Clicks card for today's clicks. |
| 5 | Click each range. | The URL gets `?range=7` etc., the selected one is marked, and the numbers stay consistent. |
| 6 | Open "Show as a table". | One row per day with its clicks. |
| 7 | Reload the page, and open the stats of another link that has not paid. | The first still shows the detail without asking again; the second shows the offer again. |
| 8 | Edit the alias link and tick "Count bots and link previews as clicks", save, reopen the stats. | The Clicks card now says "Bots included." (if you can send a bot `User-Agent` with `curl -A 'Slackbot-LinkExpanding 1.0' http://localhost:8765/s/QA-Sale`, the Bot clicks card goes up by one and, with this option ticked, so does Clicks). |

### SU-15 Delete a link: the code is never reused

**Purpose:** soft delete with a permanent code.
**Preconditions:** the plain link exists.

| # | Action | Expected result |
|---|---|---|
| 1 | On `/links/<PLAIN_ID>/` click **Delete**. | A dialog "Delete this link?" saying the code will stop working for good and can never be used again, with **Delete** and Cancel. Cancel changes nothing. |
| 2 | Confirm the deletion. | Back on `/links/` with "Link deleted."; the link is gone from every tab and the counts dropped. |
| 3 | Open `/s/<PLAIN_CODE>` in the visitor window. | The neutral unavailable page. |
| 4 | Open `/links/<PLAIN_ID>/` as the owner. | 404. |
| 5 | Create a throwaway link with alias `QA-Temp` (title `Temp`, confirm the 1 credit), delete it as in steps 1-2, then try to create a new link with alias `QA-Temp` (or `qa-temp`). | "Alias: That alias is not available.": a deleted link keeps its code for good. Nothing is charged for the refused attempt. |

### SU-16 Another user's links are not reachable

**Purpose:** ownership is enforced and never leaks (404, not 403).
**Preconditions:** `SALE_ID` recorded; a private window.

| # | Action | Expected result |
|---|---|---|
| 1 | Log in as `qa.broke@example.com` and open `/links/`. | Empty dashboard (the **No links yet** empty state with a **Create link** button). |
| 2 | Open `/links/<SALE_ID>/`, `.../edit/`, `.../stats/` and `.../qr.png`. | Each is a plain 404 page (not "forbidden", not a redirect). |
| 3 | POST-style actions: open `/links/<SALE_ID>/disable/` and `/links/<SALE_ID>/delete/` in the address bar. | 405 (method not allowed) or 404; either way nothing changes. |
| 4 | Log out, log back in as `qa.user@example.com`. | `QA Sale` is still active and unchanged. |

### SU-17 Credits: an alias without credits, and free links without credits

**Purpose:** the free path stays free; paid extras refuse cleanly and offer a top-up.
**Preconditions:** logged in as `qa.broke@example.com` (0 credits).

| # | Action | Expected result |
|---|---|---|
| 1 | Open `/links/new/`, destination `https://example.com/broke`, title `Broke link`, no extras. Submit. | Created for free: "Link created." Balance still 0. |
| 2 | Create another with alias `broke-alias`, title `Broke alias`. | The cost panel says "This costs 1 credit ... Your balance is 0 credits" and offers **Top up your credits** (a link to `/billing/credits/`). Confirming shows "You don't have enough credits for this link." with a Top up link. Nothing is created (the dashboard still lists one link). |
| 3 | On that link's edit page set a password and save. | The cost panel appears; confirming refuses for lack of credits with the Top up link; the link has no password afterwards. |
| 4 | Open the stats page of the link. | Totals only; **Turn on for 1 credit** is offered; confirming gives an error message "You don't have enough credits for detailed analytics. Top up your credits." where **Top up your credits** is a link to `/billing/credits/`. (The same link ends the refusal when enabling a link: "You don't have enough credits to enable this link. Top up your credits.") |

### SU-18 More active links than the free allowance

**Purpose:** the extra link price applies beyond the free allowance, once per link.
**Preconditions:** logged in as `qa.admin@example.com`; change a setting first.

| # | Action | Expected result |
|---|---|---|
| 1 | Go to `/admin/short_url/shorturlsettings/`, open the **Allowances (per user)** tab, set **Free active links** to `1`, keep **Max links** at 500 (or higher), save. | "changed successfully". |
| 2 | As `qa.admin`, create a link (`https://example.com/a1`, title `A1`). | Free (the first active link). |
| 3 | Create a second (`https://example.com/a2`, title `A2`). | The form shows "You have used your free active links, so this one costs 1 credit, once." and the cost panel appears on submit; confirm it. Balance is one lower. |
| 4 | Disable `A2`, then enable it again. | The enable has no cost dialog and charges nothing (the link already paid its way in). |
| 5 | Disable `A1` and enable it. | Also free. |
| 6 | Create a third link `A3` (`https://example.com/a3`). | The cost panel appears again: `A1` still uses the one free active link, `A2` paid its way in and doesn't count. Confirm: 1 more credit is charged, and the ledger agrees with the panel. |
| 7 | Restore **Free active links** to `25` in the admin (same tab). | Saved. |

### SU-19 Admin: blocked domains

**Purpose:** a blocked destination host is refused for new links, and takes existing links offline.
**Preconditions:** logged in as `qa.admin@example.com`; `QA Sale` (owned by `qa.user`) works.

| # | Action | Expected result |
|---|---|---|
| 1 | Open `/admin/short_url/blockeddomain/add/`, enter value `*.blocked-qa.example`, a reason, save. | Listed as "In force". |
| 2 | As `qa.admin`, create a link to `https://www.blocked-qa.example/page`. | Refused with "Destination: That address cannot be used." (it never says why). Nothing is charged. |
| 3 | As `qa.admin`, create a link to `https://blocked-qa.example/` (the bare host). | Also refused: the wildcard covers its own bare host. |
| 4 | Create a link to `https://notblocked-qa.example/`. | Created (a sibling host is not blocked). |
| 5 | In the admin, open `/admin/short_url/shortlink/` and find the link you just made: the list shows its destination, a "Now" column and the pending reports. Add a second blocked entry, value `notblocked-qa.example` (the exact host). | Saved. |
| 6 | In the visitor window follow that link's short URL. | The neutral unavailable page: the block takes existing links offline too. |
| 7 | As `qa.admin`, open that link's page and its dashboard row. | The pill reads **Not working** and the page explains that the destination can't be used and to edit it. |
| 8 | Disable the link, then click **Enable**. | Refused with "That address cannot be used." and nothing is charged; it stays disabled. |
| 9 | Edit the link and point it at `https://example.com/fixed`, save; then click **Enable**. | Saved (a new, unblocked destination is accepted; the pill now reads **Disabled**, since step 8 left it off), and after Enable the link works again. |
| 10 | Delete both blocked-domain entries in the admin. | Gone from the list. |

### SU-20 Admin: reports, takedown and what the owner sees

**Purpose:** staff can take a link down; the owner sees why, never who reported it.
**Preconditions:** SU-13 left a pending report against `QA Sale`; logged in as `qa.admin@example.com`.

| # | Action | Expected result |
|---|---|---|
| 1 | Open `/admin/short_url/linkreport/`. | One report: its first column is the reported link (`QA-Sale`), then reason (the report opens from the reason), source, status Pending. The reporter email and IP are visible to staff only. |
| 2 | Select it and run **Take down the reported link(s)**. | An intermediate page styled like the rest of the admin (a card with the title as its heading): a sentence about what a takedown does, the link listed as `QA-Sale -> https://example.com/...` (code in monospace, with its status), a required **Reason** field (marked `*`), an **Internal note (staff only)** field, an unticked "Notify the owner by email" checkbox, a red **Take down** button and a **Cancel** link (back to the list, nothing changed). |
| 3 | Reason `QA: misleading content`, internal note `reported by qa.reporter`, leave notify unticked, click **Take down**. | Back on the list with a success message; the report is now Actioned. |
| 4 | In the visitor window open `/s/QA-Sale`, `/s/QA-Sale+` and `/s/QA-Sale/unlock`. | All the neutral unavailable page (no hint of a takedown). |
| 5 | Log in as `qa.user@example.com`; open `/links/`. | `QA Sale` has a red **Taken down** pill and the text "Taken down: QA: misleading content". Its row menu has no Edit, Enable or Delete. The Taken down tab counts 1. |
| 6 | Open its link page. | A red notice "This link was taken down: QA: misleading content". No Edit button. Nowhere does "qa.reporter", the internal note or the word "reported" appear. |
| 7 | Try to open `/links/<SALE_ID>/edit/`. | Redirected to the link page with a message that it can no longer be changed. |
| 8 | Check the mailbox `/__e2e__/mail/?to=qa.user@example.com`. | No takedown email (notify was left off). |
| 9 | As admin, try creating a new link with alias `QA-Sale` again. | "That alias is not available": a taken-down link keeps its code. |

### SU-21 Mobile layout

**Purpose:** the pages work on a phone.
**Preconditions:** logged in as `qa.user@example.com`; viewport 375 px wide.

| # | Action | Expected result |
|---|---|---|
| 1 | Open `/links/`. | No horizontal scroll. The column header row is gone, replaced by a one-line **Sort by** bar with **Created** and **Clicks** links (an arrow shows the active one; the screen-reader text says "(sorted ...)"); it appears only while there are links. Tapping **Clicks** re-sorts the list by clicks without a reload. Each row stacks: short link and Copy, the "⋮" menu at the right, the destination below, and a line with the status pill, clicks and date. Tabs wrap or scroll sensibly. |
| 2 | Open `/links/new/`. | A single column; the expiry and click limit fields stack; the buttons are reachable. |
| 3 | Open a link page and a stats page. | Readable; the QR code and details stack; the stats cards stack; the chart fits the width. |
| 4 | Open `/s/<some code>+` and the unavailable page. | Centered, readable, no overflow. |

### SU-22 Create a link with every option at once

**Purpose:** every field of the form is stored as typed and normalized as documented, and two
extras are quoted and charged together.
**Preconditions:** logged in as `qa.user@example.com` in the main window; note the balance on
`/billing/credits/` (call it `B`).

| # | Action | Expected result |
|---|---|---|
| 1 | Open `/links/new/`. Destination `https://example.com/full?a=1`; alias `QA-Full`; title `  QA    Full  ` (extra spaces); tags `Spring Sale, promo, PROMO`; **Expires** tomorrow at 10:30 in your local time; click limit `50`; password `full-pass`; tick **Count bots and link previews as clicks**; expand "Campaign tracking (UTM)" and fill UTM source `news`, UTM medium `email`, UTM campaign `launch`, UTM term `shoes`, UTM content `top`. Click **Create link**. | Nothing is created yet. The amber panel: "This costs **2 credits**, one-off (custom alias 1, password 1). Your balance is `B` credits." and a button **Create the link for 2 credits**. |
| 2 | Click **Create the link for 2 credits**. | "Link created." on `/links/<id>/` (record `FULL_ID`). Heading `QA Full` (whitespace collapsed); pill **Active**; short link `http://localhost:8765/s/QA-Full`; destination `https://example.com/full?a=1&utm_source=news&utm_medium=email&utm_campaign=launch&utm_term=shoes&utm_content=top` (the UTM parameters appended in that order, `a=1` kept); tags `spring-sale` and `promo` (lower-cased, the space turned into a hyphen, the duplicate dropped); **Expires** tomorrow 10:30 in your local time (the same as typed, no "UTC"); **Click limit** 50; **Password** Protected; **Clicks** 0. |
| 3 | Open `/billing/credits/`. | Balance `B - 2`; two ledger lines of 1 credit each: "Short URL: custom alias for /s/QA-Full" and "Short URL: password for /s/QA-Full". |
| 4 | Back on the link page click **Edit**. | Tab title "Edit link - ZipTrigo"; the back link reads `QA-Full`. Expires shows tomorrow 10:30 local; Click limit 50; Tags `spring-sale, promo`; **Count bots** ticked; the password field is empty with the placeholder "Leave empty to keep the current password" and a **Remove the password** checkbox below; its help says "You already paid for this extra on this link, so changing the password is free."; the UTM fields are empty and the section says "Leave a field empty to keep what the address already has; to remove a parameter, edit the destination above." Click **Cancel**: back on the link page, nothing changed. |
| 5 | Open `/links/<FULL_ID>/stats/`. | The **Clicks** card says "Every visit, bots included: the click limit counts them." and the **Bot clicks** card "Crawlers and link previews. Also in the clicks." with a **Change** link to the edit page (a click limit counts bots whatever the checkbox says). |
| 6 | In the visitor window open `/s/QA-Full`, enter `full-pass` on the password page and press **Continue**. | Sent on to the full destination with its UTM parameters. |
| 7 | Main window: reload the link page. | **Clicks** 1, and a **Last click** time in your local time. |

### SU-23 More destination and field refusals

**Purpose:** each rule of the destination and of the other fields has its message.
**Preconditions:** logged in as `qa.user@example.com`; note the link count and the balance. For
each step open `/links/new/`, give the title `Bad` and submit with **Create link**.

| # | Action | Expected result |
|---|---|---|
| 1 | Destination `https://paypal.com@evil.example/`. | "Destination: The address cannot contain a username or password." (also under the Destination field). |
| 2 | Destination `https://2130706433/`, then `https://10.0.0.5/`, then `https://[::1]/`, then `https://intranet/`, then `https://printer.local/`. | Each: "Destination: That address is not allowed." (a number the browser reads as an IP, a private address, IPv6 loopback, a name without a dot, a `.local` name). |
| 3 | Destination `https://-bad-.example/`. | "Destination: Enter a valid web address." |
| 4 | In the console run `document.getElementById('id_destination').value = 'https://example.com/' + 'a'.repeat(2040)` and submit. | "Destination: The address is too long (at most 2,048 characters)." |
| 5 | Destination `https://example.com/ok`; in the console run `const t = document.getElementById('id_title'); t.removeAttribute('maxlength'); t.value = 'x'.repeat(256)` and submit. | "Title: The title is too long (at most 255 characters)." |
| 6 | Title `Bad`, tags `t1, t2, t3, t4, t5, t6, t7, t8, t9, t10, t11, t12, t13, t14, t15, t16, t17, t18, t19, t20, t21`. | "Tags: Use at most 20 tags." |
| 7 | Tags empty, password three spaces. | "Password: Enter a password." |
| 8 | Password empty; in the console run `const a = document.getElementById('id_alias'); a.removeAttribute('maxlength'); a.value = 'a'.repeat(33)` and submit. | "Alias: Use 3-32 letters, digits, hyphens or underscores." |
| 9 | Alias `QA-Full` (taken in SU-22), then `qa-full`. | "Alias: That alias is not available." for both, at once, with no cost panel. |
| 10 | Check `/links/` and `/billing/credits/`. | Same link count and balance as at the start: nothing was created or charged. |

### SU-24 The link page: copy, QR code, preview

**Purpose:** everything on a link's own page works.
**Preconditions:** `FULL_ID` from SU-22; logged in as `qa.user@example.com`.

| # | Action | Expected result |
|---|---|---|
| 1 | Open `/links/<FULL_ID>/`. | Tab title "QA Full - ZipTrigo" (the title, or the code for a link without one). A back link **Short URLs** to `/links/`. |
| 2 | Click **Copy** next to the short link. | The button reads "Copied" (check icon) for a moment; the clipboard holds `http://localhost:8765/s/QA-Full`. |
| 3 | Look at the QR code image. | It loads (alt text "QR code of http://localhost:8765/s/QA-Full"). In the console `fetch('/links/<FULL_ID>/qr.png').then(r => [r.status, r.headers.get('content-type'), r.headers.get('cache-control')])` gives `[200, 'image/png', 'private, max-age=3600']`. A QR reader (optional) decodes it to the short link, not the destination. |
| 4 | Click **Download QR code**. | The browser saves `QA-Full.png` (the code as the file name), a PNG of the same QR code. On `/links/`, the row's "⋮" menu → **QR code** saves the same file. |
| 5 | Click the destination. | It opens in a new tab (the link has `target="_blank"` and `rel="nofollow noopener noreferrer"`). Close that tab. |
| 6 | Click **Preview**. | `/s/QA-Full+` opens in a new tab and, as this session has not entered the password, says the link is protected (no destination, no title). Close it. |
| 7 | Click **Stats**, then the back link `QA-Full` at the top of the stats page. | Stats page, then back on the link page. |

### SU-25 Every unavailable state answers the same, checked from the console

**Purpose:** the redirect never says why a link doesn't work; HEAD is never a click; a click limit
ends a link at the exact count.
**Preconditions:** SU-15 (`PLAIN_CODE` deleted) and SU-20 (`QA-Sale` taken down) done; logged in
as `qa.user@example.com` in the main window; the visitor window is open on
`http://localhost:8765/` (any page of the site will do).

| # | Action | Expected result |
|---|---|---|
| 1 | Main window: create `QA Cap1` (destination `https://example.com/cap1`, title `QA Cap1`, click limit `1`) and `QA Exp1` (`https://example.com/exp1`, title `QA Exp1`, Expires two minutes from now, local time). Record their codes `C1` and `E1`. Then disable `QA Full` (its page, **Disable**). | Both created for free; `QA Full` shows **Disabled**. |
| 2 | Visitor window console: `const probe = async (c, m = 'GET') => { const r = await fetch('/s/' + c, {method: m, redirect: 'manual'}); return [c, m, r.type, r.status, r.headers.get('cache-control')]; }` then `await probe('<C1>', 'HEAD')`. | `['<C1>', 'HEAD', 'opaqueredirect', 0, null]`: a redirect, not counted. |
| 3 | `await probe('<C1>')`, then `await probe('<C1>')` again. | The first is `opaqueredirect` (the one allowed click; a click limit counts every visit, even a bot's). The second is `['<C1>', 'GET', 'basic', 404, 'no-store, private']`. |
| 4 | Main window: open `/links/`. | `QA Cap1`: pill **Click limit reached**, 1 click. |
| 5 | Visitor window, once `QA Exp1` has expired: `await probe('<E1>')`. | `basic`, `404`, `no-store, private`. |
| 6 | `for (const c of ['zzzzzzz', '<PLAIN_CODE>', 'QA-Sale', 'QA-Full']) console.log(await probe(c))`. | Every line is `basic`, `404`, `no-store, private`: unknown, deleted, taken down and disabled look identical. |
| 7 | Open each of `/s/<C1>`, `/s/<E1>`, `/s/zzzzzzz`, `/s/<PLAIN_CODE>`, `/s/QA-Sale`, `/s/QA-Full` in the visitor window, then the same codes with `+` and with `/unlock`. | The same page every time: tab title "Link unavailable - ZipTrigo", an exclamation icon, "This link isn't available" and "It may have expired, been switched off, or been removed." Nothing names the reason or the owner. |
| 8 | `fetch('/s/QA-Full/report').then(r => r.status)` (a GET of the report endpoint). | `405`. |
| 9 | Main window: enable `QA Full` again (its page, **Enable**; free). | "Link enabled." |

### SU-26 Prices: the cost changed re-ask, and free extras

**Purpose:** a price changed between seeing a cost and confirming it is asked again and never
charged unseen; a price of 0 makes an extra free and remembers it.
**Preconditions:** logged in as `qa.admin@example.com`; two tabs of the same session: tab A for
the site, tab B on `/admin/short_url/shorturlsettings/` (the "Prices (credits, one-off, per link)"
tab, which says "A price of 0 makes that extra free."). Note the balance (`B`).

| # | Action | Expected result |
|---|---|---|
| 1 | Tab A: `/links/new/`, destination `https://example.com/cost`, title `Cost`, alias `qa-cost`, **Create link**. | The panel: "This costs **1 credit**, one-off (custom alias 1)...". Leave it showing. |
| 2 | Tab B: set **Custom alias price (credits)** to `2`, save. | The admin's "was changed successfully" message. |
| 3 | Tab A: click **Create the link for 1 credit**. | No link is created and nothing is charged; the panel is replaced: "This costs **2 credits**, one-off (custom alias 2). Your balance is `B` credits." with **Create the link for 2 credits**. |
| 4 | Click **Create the link for 2 credits**. | "Link created."; on `/billing/credits/` the balance is `B - 2` with one line of 2 credits, "Short URL: custom alias for /s/qa-cost". |
| 5 | Tab A: open the new link's **Stats** and click **Turn on for 1 credit**. | The dialog "Turn on detailed analytics?", "This costs 1 credit, once for this link. It won't be charged again.", focus on **Cancel**. Leave it open. |
| 6 | Tab B: set **Detailed analytics price (credits)** to `3`, save. Tab A: click **Turn on for 1 credit** in the dialog. | The stats page reloads with a red message "Detailed analytics cost 3 credits. Confirm it from the page to continue."; nothing charged; the button now reads **Turn on for 3 credits**. |
| 7 | Tab B: set Custom alias, Password and Detailed analytics prices to `0`, save. Tab A: reload the stats page. | The detailed part is shown at once, with no offer and no charge: the range selector (7 days, 30 days, 90 days, All time) and, as nobody clicked this link, "No clicks in this period yet." and "Nothing yet." in each of the four lists. Click **All time**: the heading says "0 clicks, 0 unique visitors in all time (since <today's date>)". |
| 8 | Tab A: open `/links/new/`. | The alias help says "Aliases are free."; the password help says "Passwords are free." |
| 9 | Create a link: `https://example.com/free`, alias `qa-free`, title `Free extras`, password `pw-free`. | No cost panel: "Link created." at once; the balance is unchanged. |
| 10 | Tab B: set all three prices back to `1`, save. Tab A: reload the stats of `qa-cost`; open the stats of `Free extras`; open the edit page of `Free extras`. | `qa-cost` still shows its detailed analytics (opened while free, so kept). `Free extras` offers **Turn on for 1 credit** (its stats were never opened while free). Its password help says "You already paid for this extra on this link, so changing the password is free." (set while free, so recorded as paid). |

### SU-27 A fresh account: the free allowance, the pager and paying to enable

**Purpose:** the 25 free active links, the extra link price in ordinary use, the dashboard pager,
and the enable dialog with its cost.
**Preconditions:** a new account `qa.su.<random>@example.com` (password with a digit), registered
and confirmed as described in Preconditions, signed in in the main window; call it `SU_USER`.
Its balance is 1000.

| # | Action | Expected result |
|---|---|---|
| 1 | Open `/links/` (the empty state), then run in the console: `const t = document.cookie.match(/csrftoken=([^;]+)/)[1]; for (let i = 1; i <= 25; i++) { await fetch('/links/new/', {method: 'POST', headers: {'X-CSRFToken': t, 'Content-Type': 'application/x-www-form-urlencoded'}, body: new URLSearchParams({destination: 'https://example.com/bulk/' + i, title: 'Bulk ' + i})}); }` and reload. | 25 rows, `Bulk 25` first; tabs All 25 and Active 25; no pager (25 per page). The balance is still 1000. |
| 2 | Open `/links/new/`. | Above the buttons, in amber: "You have used your free active links, so this one costs 1 credit, once." |
| 3 | Create `Bulk 26` (`https://example.com/bulk/26`). | The panel: "This costs **1 credit**, one-off (extra active link 1). Your balance is 1000 credits." Confirm it: "Link created."; the ledger line is "Short URL: extra active link for /s/<code>", balance 999. |
| 4 | Open `/links/`. | Below the list "Page 1 of 2" and a **Next** link; `Bulk 1` is not on this page. |
| 5 | Click **Next**. | Without a reload the list shows one row, `Bulk 1`; the address bar reads `/links/?page=2`; "Page 2 of 2" with **Previous** only. Reload: still page 2. Click **Previous**: back to page 1 and `/links/`. |
| 6 | Search `Bulk 1`. | `Bulk 1` and `Bulk 10` to `Bulk 19` (11 rows), one page, no pager. Clear the search. |
| 7 | On page 2 open `Bulk 1`'s "⋮" menu → **Disable**. | "Link disabled."; you stay on page 2 (`?page=2`); the row's pill is **Disabled** and its menu offers **Enable** with no cost (24 free links are active). |
| 8 | Create `Bulk 27` (`https://example.com/bulk/27`). | Created at once, free, and the amber line is not shown on the form (a free slot was available). |
| 9 | On `/links/?page=2` open `Bulk 1`'s menu → **Enable**. | A dialog "Enable this link?": "This link would go beyond your free active links, so enabling it costs 1 credit, once. It won't be charged again for this link." with **Cancel** and **Enable for 1 credit**. **Cancel** closes it; nothing changed. |
| 10 | Open it again and click **Enable for 1 credit**. | "Link enabled."; balance 998. Disable `Bulk 1` and enable it again: no dialog and no charge (it paid its way in). |
| 11 | Disable `Bulk 2` (free); its menu now has a plain **Enable**. Without reloading this tab, in another tab create `Bulk 28` (free). Back in the first tab click `Bulk 2`'s **Enable**. | Not enabled, nothing charged; a red message "Enabling this link costs 1 credits. Confirm it from the menu to continue." (the code says "1 credits": record the grammar as a minor issue). After the reload its menu's **Enable** opens the dialog; confirm it: "Link enabled.", balance 997. |

`SU_USER` now has 28 links, `Bulk 1` to `Bulk 28`. Record the codes of `Bulk 4`, `Bulk 5`,
`Bulk 6` and `Bulk 7` (`B4` ... `B7`) and the ids of `Bulk 4` and `Bulk 6` (`B4_ID`, `B6_ID`).

### SU-28 The hard cap on links

**Purpose:** the cap counts active and disabled links; only deleting makes room.
**Preconditions:** SU-27 done (`SU_USER` has 28 links); `SU_USER` in the main window,
`qa.admin@example.com` in a private window.

| # | Action | Expected result |
|---|---|---|
| 1 | Admin: on `/admin/short_url/shorturlsettings/`, **Allowances (per user)** tab, set **Free active links (per user)** to `100` and **Max links (per user)** to `29`, save. | Saved. |
| 2 | `SU_USER`: create `Bulk 29` (`https://example.com/bulk/29`). | Created, free. |
| 3 | Create `Bulk 30`. | Refused in the form's red box: "You can have at most 29 links. Delete one to make room." No cost panel, nothing created, nothing charged. |
| 4 | Disable `Bulk 3`, then try `Bulk 30` again. | The same refusal: a disabled link still counts. |
| 5 | Delete `Bulk 3` (its page → **Delete** → the dialog's **Delete**), then create `Bulk 30`. | "Link deleted."; then `Bulk 30` is created. |
| 6 | Admin: set Free active links back to `25` and Max links back to `500`, save. | Saved. |

### SU-29 Reports put a link on hold (auto-hold threshold)

**Purpose:** reports from distinct addresses hold a link; a held link is unavailable and frozen
for its owner except Disable; staff release it.
**Preconditions:** SU-27 done; `SU_USER` in the main window, `qa.admin@example.com` in a second
private window, the visitor window signed out.

| # | Action | Expected result |
|---|---|---|
| 1 | Admin: settings page, **Analytics and abuse** tab, set **Auto hold report threshold** to `2`, save. | Saved. |
| 2 | Visitor: open `/s/<B4>+`, click **Report this link**, choose "Phishing or scam", details `QA hold 1`, **Submit report**. | "Thank you, your report has been submitted." The link still works: `fetch('/s/<B4>', {redirect: 'manual'}).then(r => r.type)` is `opaqueredirect` (one report, one address). |
| 3 | Visitor: reload `/s/<B4>+` and send a second report from another address. The server trusts `X-Real-IP` from a loopback peer, as it does from nginx in production: `fetch('/s/<B4>/report', {method: 'POST', headers: {'X-Real-IP': '203.0.113.7', 'Content-Type': 'application/x-www-form-urlencoded'}, body: new URLSearchParams({csrfmiddlewaretoken: document.querySelector('#report-box [name=csrfmiddlewaretoken]').value, reason: 'spam'})}).then(r => r.status)`. | `200`. |
| 4 | Visitor: open `/s/<B4>`, `/s/<B4>+` and `/s/<B4>/unlock`. | The neutral "This link isn't available" page for all three. |
| 5 | `SU_USER`: open `/links/` (All tab), then the **Active** tab. | `Bulk 4` has an amber **Under review** pill and the line "Under review: it can't be changed for now."; it is not in the Active tab. Its "⋮" menu has only **Stats**, **QR code**, **Preview** and **Disable**. |
| 6 | Open `/links/<B4_ID>/`. | An amber notice "This link is under review, so it doesn't work for visitors and can't be changed right now. You can still disable it." Buttons: **Stats**, **Preview**, **Disable**; no **Edit**, no **Delete**. Nothing says who reported it or why. On its stats page the Bot clicks card has no **Change** link. |
| 7 | Open `/links/<B4_ID>/edit/`. | Back on the link page with a red message "This link is under review, so it cannot be changed right now. You can disable it." |
| 8 | Click **Disable**. | "Link disabled."; the pill reads **Disabled**; the notice stays, without "You can still disable it."; there is no **Enable** button. |
| 9 | Admin: `/admin/short_url/linkreport/`. Select one of the two pending reports of `Bulk 4` and run **Dismiss selected report(s)**. | "Dismissed 1 report(s)." `SU_USER`'s link page (reload) still shows the review notice. |
| 10 | Admin: `/admin/short_url/shortlink/`, search `<B4>`: the row shows a held-for-review time and **Pending reports** 1. Select it and run **Release abuse-review hold (dismisses its pending reports)**. | "Released the hold on 1 link(s)."; on the report list both reports of `Bulk 4` are now Dismissed. |
| 11 | `SU_USER`: reload the link page. | No notice; **Edit**, **Enable** and **Delete** are back. Click **Enable**: 25 other free links are active, so the dialog asks for 1 credit; confirm. "Link enabled." and the visitor's `/s/<B4>` works again. |
| 12 | Admin: set **Auto hold report threshold** back to `0`, save. | Saved. |

### SU-30 Takedown with the owner notified by email

**Purpose:** "Notify the owner by email" sends the owner a message with the reason and a working
link; the reason is shown as text everywhere.
**Preconditions:** SU-27 done; `qa.admin@example.com` in a private window, `SU_USER` in the main
window.

| # | Action | Expected result |
|---|---|---|
| 1 | Admin: `/admin/short_url/shortlink/`, search `<B5>`, select it, run **Take down selected link(s)**. Reason `QA <b>removed</b> & co`, internal note `internal QA note`, tick **Notify the owner by email ("one of your links was removed")**, click **Take down**. | "Took down 1 link(s)." |
| 2 | Open `/__e2e__/mail/?to=<SU_USER address>` (reload until it arrives, up to a minute). | One email, subject "One of your short links was removed". Text: "Your short link http://localhost:8765/s/<B5> was removed and no longer works.", "Reason: QA <b>removed</b> & co" and "Details: http://localhost:8765/links/<Bulk 5 id>/". The HTML source shows the reason escaped (`&lt;b&gt;removed&lt;/b&gt; &amp; co`) and a "See the link" anchor to the same page. Neither the internal note, nor any reporter, nor the reason appears in the subject. |
| 3 | Click the Details link (signed in as `SU_USER`). | The link page, pill **Taken down**, red notice "This link was taken down: QA <b>removed</b> & co" with the tags shown literally (not bold). No **Edit** or **Delete**. In a signed-out window the same link goes to the login page and, after signing in as `SU_USER`, on to this page. |
| 4 | Visitor: open `/s/<B5>`. | The neutral unavailable page. |
| 5 | `SU_USER`: `/links/?status=taken_down`. | The Taken down tab counts 1 and lists `Bulk 5` with "Taken down: QA <b>removed</b> & co". |

### SU-31 An owner whose account staff deactivated

**Purpose:** a deactivated owner whose session is still open can't create, change or enable links,
but can disable and delete them; their links keep working; new sign-ins are refused.
**Preconditions:** SU-27 done; `SU_USER` signed in in the main window (keep this session open),
`qa.admin@example.com` in a private window.

| # | Action | Expected result |
|---|---|---|
| 1 | Admin: `/admin/short_url/shortlink/`, search `<B6>`, select it, run **Deactivate the selected link(s)' owner's account**. | A card "Deactivate owner's account(s)": it says the owner can't sign in again or use the API, and can't create, edit or enable links; lists `SU_USER`'s address; a required **Reason**; an unticked "Also take down the owner's other active links"; **Deactivate** and **Cancel**. |
| 2 | Reason `QA deactivation`, click **Deactivate**. | "Deactivated 1 account(s)." |
| 3 | `SU_USER` (same session): open `/links/new/`. | Back on `/links/` with the red message "Your account is not active, so links cannot be created, changed or enabled." |
| 4 | Open `Bulk 6`'s edit page, change the title to `changed`, **Save changes**. | The form's red box shows the same message; reload the link page: the title is unchanged. |
| 5 | **Disable** `Bulk 6`, then **Enable** it. | "Link disabled."; then a red message with the same "Your account is not active ..." text, and it stays disabled. |
| 6 | Visitor: open `/s/<B7>`. | It still redirects (deactivating an account does not take its links down). |
| 7 | `SU_USER`: log out, then try to sign in again. | Refused: "User not active". |
| 8 | Admin: `/admin/accounts/user/`, open `SU_USER`, set **Status** to Active, save. Then sign in as `SU_USER` in the main window and open `/links/new/`. | Signed in; the form opens. Enable `Bulk 6` (confirm the 1-credit dialog if it asks): "Link enabled." |

### SU-32 Reports and the password page, the remaining paths

**Purpose:** reporting works on a protected link without unlocking it, a report on a link that
stopped working meanwhile ends on the neutral page, and the password page of a link without a
password just continues.
**Preconditions:** `PROTECTED_CODE` (SU-12, a password set again in step 9) and `LIMITED_CODE`
(SU-11, click limit 5) from earlier; `qa.user@example.com` in the main window; the visitor window
has not entered the protected link's current password.

| # | Action | Expected result |
|---|---|---|
| 1 | Visitor: open `/s/<PROTECTED_CODE>+`, click **Report this link**. | The button's `aria-expanded` turns `true` and the form opens: **Reason** (a select starting with "Choose a reason", then Phishing or scam, Malware, Spam, Illegal content, Copyright infringement, Other), **Details (optional)**, **Your email (optional)** with "In case we need to follow up. We won't share it with the owner of the link." and **Submit report**. |
| 2 | Choose "Other", details `QA locked report`, submit. | "Thank you, your report has been submitted."; still no destination or title on the page. |
| 3 | Click **Continue**. | The password page `/s/<PROTECTED_CODE>/unlock`: tab title "Password needed - ZipTrigo", heading "Password needed", "This link is protected. Enter the password you were given to continue.", focus already in the Password field. |
| 4 | Visitor: open `/s/<LIMITED_CODE>+` and open the report form. Main window: disable `QA Limited`. Visitor: choose "Spam" and **Submit report**. | The visitor's page reloads by itself and shows the neutral "This link isn't available" page (no report is made). Main window: enable `QA Limited` again. |
| 5 | Visitor: open `/s/<LIMITED_CODE>/unlock` (a link without a password). | No password page: it goes straight on to `https://example.com/limited` (and counts one click). |
| 6 | Admin (`qa.admin@example.com`): `/admin/short_url/linkreport/`. | The report of step 2 is listed against the protected link (reason Other, status Pending, no reporter email). Select it and run **Dismiss selected report(s)**: "Dismissed 1 report(s)." |

### SU-33 Dark mode, keyboard and phone width

**Purpose:** the pages work in the dark theme, without a mouse and on a phone.
**Preconditions:** logged in as `qa.user@example.com`.

| # | Action | Expected result |
|---|---|---|
| 1 | On `/account/`, under "Appearance", choose **Dark**. Open `/links/`, `/links/new/` (submit with an alias to show the cost panel, and with a bad destination to show the red box), a link page, the stats page of `QA Sale` (taken down in SU-20, but its page still shows the detailed analytics paid for in SU-14), and the row menu and the delete dialog. | Dark backgrounds, light text; the pills, tag chips, the amber cost panel, the red messages, the chart bars and the lists' bars are all legible; the QR code keeps its white background. No white boxes or dark-on-dark text. |
| 2 | In the visitor window (dark system scheme), open `/s/<PROTECTED_CODE>/unlock`, `/s/QA-Full+` with the report form open, and `/s/zzzzzzz`. | Readable in dark too. |
| 3 | Choose **System** again on `/account/`. | — |
| 4 | Keyboard only, on `/links/`: `Tab` from the top. | "Skip to content" first; then the tabs, the search box, the Tag select, the sort links, and per row the short link, its **Copy** button and its "⋮" button ("Actions for <code>"), each with a visible focus ring. |
| 5 | Focus a row's "⋮" and press `Enter`. | The menu opens (`aria-expanded="true"`); `Tab` moves through its items; `Escape` closes it and focus is back on "⋮". |
| 6 | Open the menu again and press `Enter` on **Delete**. | The dialog "Delete this link?" opens with focus on **Cancel**; `Tab` reaches **Delete**; `Escape` closes it and nothing is deleted. Note where focus lands: the dialog gives it back to the item that opened it, which sits in the now closed menu, so if focus is lost to the page body, record it as an accessibility issue (not a failure of this plan). |
| 7 | On `/links/new/`, `Tab` through the whole form, then fill destination `https://example.com/kbd`, title `Kbd`, alias `qa-kbd` and press `Enter` in the Destination field. | Every field, the "Campaign tracking (UTM)" summary (`Enter` opens it), the checkbox (`Space` ticks it), **Create link** and **Cancel** are reachable in that order. `Enter` submits: the cost panel appears and `Tab` reaches its **Create the link for 1 credit** button. Do not press it: `Tab` on to **Cancel** and press `Enter` (back on `/links/`, nothing created or charged). |
| 8 | Viewport 375 px wide: open `/links/?page=2` as `SU_USER` (SU-27) or any list, a preview page with the report form open, the password page and the takedown confirmation page of the admin. | No horizontal scroll; the pager, the report form and the password field fit the width; buttons are tappable. Restore the desktop viewport. |

## Out of scope

- **Loops** ("That address already points at one of our short links."): this server's `BASE_URL`
  host is `localhost` (or a `*.localhost` name), which the destination rules refuse as "not
  allowed" before the loop check runs, so the loop message can't be reached here; unit-tested in
  `apps/short_url/tests/test_destinations.py`.
- Title fetching from the destination page (needs the internet; SU-04 only checks that a link
  without a title is still created), GeoIP countries (always "Unknown") and Google Safe Browsing
  (no API key here: every check is skipped).
- Rate limits (`SU_REDIRECT_IP`, `SU_PASSWORD_*`, `SU_REPORT_*`, ...): off on this server, so
  "over the limit a known link still redirects" and the 429 pages are unit-tested only.
- A real bot `User-Agent` from the browser: a page's `fetch` can't set one (SU-14 step 8 uses
  `curl -A` where available).
- The background jobs (click rollup, event purge, the hourly Safe Browsing recheck): the stats
  page reads the raw events after the last rollup, so its numbers are exact without them.
- The JWT API (`/api/s/`) and `inv shorturl`: unit-tested.
