# Short URLs

## Scope

The Short URLs product's web UI (`apps/short_url`):

- the dashboard `/links/` (empty state, list, status tabs, search, tag filter, sort, the row menu,
  copy button);
- the editor `/links/new/` and `/links/<id>/edit/` (destination, custom alias, title, tags,
  expiry, click limit, password, "count bots", UTM fields, the live "your link will be" line, the
  credit-cost confirmation) and a link's own page `/links/<id>/` (copy, QR code, Edit, Stats,
  Preview, Disable/Enable, Delete);
- the public short links `/s/<code>` (the `302`, case-insensitive codes, the neutral "this link
  isn't available" page for every dead link), the preview page `/s/<code>+` with its report form,
  and the password page `/s/<code>/unlock`;
- the per-link stats page `/links/<id>/stats/` (totals, the paid "detailed analytics", ranges);
- credits: creating and following a link is free, extras cost credits once per link;
- access control: logged-out visitors, another user's link ids;
- the admin side of abuse handling: blocked domains, a taken-down link, the settings singleton.

Not covered here: the JWT API (`/api/s/`) and the CLI (`inv shorturl`), which are covered by unit
tests (`apps/short_url/tests/test_api_*.py`, `admin/tests/test_shorturl.py`) and by the Playwright
test `tests_e2e/test_short_url.py`; GeoIP country statistics (no database on this server, so the
country list is always "Unknown"); title fetching from the destination page (needs the internet and
the worker, see below); the background jobs (click rollup and purge).

## Preconditions

- The test server is running at `http://localhost:8765`, freshly restarted (empty database), as
  described in [`README.md`](README.md). Rate limiting is off.
- `BASE_URL` is `http://localhost:8765`, so a short link reads `http://localhost:8765/s/<code>`.
- **Destinations are real internet addresses** (`https://example.com/...`): the site refuses
  `localhost` and private addresses as a destination, on purpose. Following a short link in the
  browser therefore needs the internet; without it the browser shows its own "can't reach this
  site" page for `example.com`, which is still the proof that the redirect happened (read the
  address in the error page, or the `Location` header in the network log). Where a case says
  "follow", either is fine.
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
| 2 | Look at the page. | Heading "Short URLs"; a **New link** button; status tabs All, Active, Disabled, Expired, Click limit reached, Taken down, each with a count of 0; a search box "Search code, destination, title or tag"; a list header (Short link, Destination, Status, Clicks, Created); one row "No links yet. Create your first one." with a link. No tag filter yet. No broken icons, raw `{{ }}`/`{% %}`, or console errors. |
| 3 | Click **Create your first one.** | Goes to `/links/new/`. |

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
| 2 | Read the link page. | A heading showing the code (or the fetched title if the server could reach the internet); status pill **Active**; the short link `http://localhost:8765/s/<7 characters>` with a **Copy** button; the destination `https://example.com/plain?x=1`; **Clicks 0**; Created today; a QR code image and "Download QR code"; buttons **Edit**, **Stats**, **Preview**, **Disable**, **Delete**. Record `PLAIN_CODE`. |
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
| 8 | Tags `ok, bad tag!`. | "Tags: ..." names the invalid tag and the allowed characters. |
| 9 | Click limit `0`; then a very large one such as `99999999999`. | The browser or the server refuses: a number between 1 and 2,000,000,000. |
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
| 5 | Open `/s/<PLAIN_CODE>` with a `HEAD` request if the tool allows it (`curl -I`). | Same 302, but the click count does not go up. |

### SU-08 The dashboard

**Purpose:** list, tabs, search, tag filter, sort, row contents, copy.
**Preconditions:** the plain and the alias links exist.

| # | Action | Expected result |
|---|---|---|
| 1 | Open `/links/`. | Two rows, newest first (`QA Sale`, then the plain link). Each row: the path `/s/<code>` with a **Copy** button, the destination (truncated, full text in a tooltip), the title for the alias link, tag chips, an **Active** pill, the click count and the created date, and a "⋮" actions menu. Tabs: All 2, Active 2, others 0. A **Tag** dropdown now exists with `promo` and `spring`. |
| 2 | Click **Copy** on a row. | The button reads "Copied" for a moment; pasting somewhere gives the full address `http://localhost:8765/s/<code>`. |
| 3 | Type `sale` in the search box. | After a short pause (no page reload) only `QA Sale` remains; the address bar shows `?q=sale`. Clear it with the × button: both rows return. |
| 4 | Search `example.com/plain`, then `spring`, then `"`. | The plain link; the alias link (matched by tag); no results ("No links found.") for `"`. |
| 5 | Choose tag `promo`. | Only `QA Sale`; the URL has `tag=promo`. Reset the tag to "All tags". |
| 6 | Click the **Clicks** column header, then again. | Sorted by clicks descending, then ascending; the arrow flips and the screen reader text says "(sorted ...)". |
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
| 6 | Create the expiring link: destination `https://example.com/expiring`, title `QA Expiring`, **Expires** a minute or two ahead. Follow it at once, then again after it has expired. | The first follow redirects; the second shows the neutral page. The dashboard shows an **Expired** pill and the Expired tab counts it. |
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
| 1 | In the visitor window open `/s/QA-Sale+`. | "Where does this link go?"; the title; "Goes to **example.com**"; the full destination; "Created ... ago. Only continue if you trust where it leads."; a **Continue** button; a small "Report this link" button. The page is `noindex` (view source or headers: `X-Robots-Tag: noindex`). Nothing is counted as a click. |
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
| 1 | Log in as `qa.broke@example.com` and open `/links/`. | Empty dashboard ("No links yet."). |
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
| 4 | Open the stats page of the link. | Totals only; **Turn on for 1 credit** is offered; confirming gives an error message "You don't have enough credits for detailed analytics. Top up first." |

### SU-18 More active links than the free allowance

**Purpose:** the extra link price applies beyond the free allowance, once per link.
**Preconditions:** logged in as `qa.admin@example.com`; change a setting first.

| # | Action | Expected result |
|---|---|---|
| 1 | Go to `/admin/short_url/shorturlsettings/` and set **Free active links** to `1`, keep **Max links** at 500 (or higher), save. | "changed successfully". |
| 2 | As `qa.admin`, create a link (`https://example.com/a1`, title `A1`). | Free (the first active link). |
| 3 | Create a second (`https://example.com/a2`, title `A2`). | The form shows "You have used your free active links, so this one costs 1 credit, once." and the cost panel appears on submit; confirm it. Balance is one lower. |
| 4 | Disable `A2`, then enable it again. | The enable has no cost dialog and charges nothing (the link already paid its way in). |
| 5 | Disable `A1` and enable it. | Also free. |
| 6 | Create a third link `A3` (`https://example.com/a3`). | The cost panel appears again: `A1` still uses the one free active link, `A2` paid its way in and doesn't count. Confirm: 1 more credit is charged, and the ledger agrees with the panel. |
| 7 | Restore **Free active links** to `25` in the admin. | Saved. |

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
| 9 | Edit the link and point it at `https://example.com/fixed`, save. | Saved (a new, unblocked destination is accepted) and the link works again. |
| 10 | Delete both blocked-domain entries in the admin. | Gone from the list. |

### SU-20 Admin: reports, takedown and what the owner sees

**Purpose:** staff can take a link down; the owner sees why, never who reported it.
**Preconditions:** SU-13 left a pending report against `QA Sale`; logged in as `qa.admin@example.com`.

| # | Action | Expected result |
|---|---|---|
| 1 | Open `/admin/short_url/linkreport/`. | One report: link `QA-Sale`, reason Spam, status Pending. The reporter email and IP are visible to staff only. |
| 2 | Select it and run **Take down the reported link(s)**. | An intermediate page "take down" listing the link, with a **Reason** box, an **Internal note (staff only)** box and an unticked "Notify the owner by email" checkbox. |
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
| 1 | Open `/links/`. | No horizontal scroll. Each row stacks: short link and Copy, the "⋮" menu at the right, the destination below, and a line with the status pill, clicks and date. Tabs wrap or scroll sensibly. |
| 2 | Open `/links/new/`. | A single column; the expiry and click limit fields stack; the buttons are reachable. |
| 3 | Open a link page and a stats page. | Readable; the QR code and details stack; the stats cards stack; the chart fits the width. |
| 4 | Open `/s/<some code>+` and the unavailable page. | Centered, readable, no overflow. |
