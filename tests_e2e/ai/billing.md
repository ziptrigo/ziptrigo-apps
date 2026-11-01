# Billing: credits, promo codes and welcome credits

**Scope.** The user-facing side of `apps.billing`: the balance shown on the account page and in the
products; the credits history page at `/billing/credits/` (`billing/credits_history.html`: columns,
colours, every transaction type's label, the description rules, the empty state, pagination at 25
rows per page, escaping), its login requirement and the links to it; how the ledger reacts to things
that change the balance (a Receipts upload, charged `ReceiptsSettings.price_per_file`, **1 credit**
by default, when the receipt is processed; the refund of a file that could not be read; a receipt
parked "Waiting for credits" and resumed by a top-up; manual adjustments made in the admin);
**promo codes** from the user's side (the "Have a promo code?" form on the account page, the credits
history page and the redeem link page `/billing/redeem/<code>/`, signed in and signed out, the
login page's note and the code forwarded to the register page, every refusal, the flash, the ledger
line, the register page's optional **Promo code** field with its reservation, its grant on
confirmation and the bell notification, several codes on one account, a reservation that is
released); and the **welcome credits** of a new account (granted once on confirmation with a
notification, refused with an email for a `+tag` or Gmail-dots variant of an address already
confirmed, first to confirm wins, switched off by setting them to 0). There is no way to buy
credits on the site: credits come from the admin adjustment tool, promo codes and the welcome
credits. Promo codes and settings are **set up** in the admin as steps of these cases; the admin
pages themselves (list columns, filters, bulk tool, CSV, read-only rules) are tested in `admin.md`.

## Preconditions

- The test server is running at `http://localhost:8765` on a **fresh database** (the expected
  balances below assume it). All URLs are relative to it.
- Seeded accounts (password `QaE2e-Passw0rd!`):
  - `qa.user@example.com`: 100 credits, one ledger row `+100`, type `purchase`, description
    `e2e seed`.
  - `qa.admin@example.com`: superuser, same 100-credit seed row.
  - `qa.broke@example.com`: 0 credits, no ledger rows at all.

  These three hold exactly those credits because `serve.py` switches the welcome credits off while
  it seeds them. **An account registered during the run gets 1,000 welcome credits** the moment
  its email is confirmed (one ledger row, see BILL-12), so a case that needs an account with 0
  credits must use `qa.broke`.
- Log in on `/account/login/` (**Email**, **Password**, **Sign in**); log out from the account menu
  (person icon, top right) > **Logout**. A superuser logged in on the site can open `/admin/`
  directly (same session).
- Times on the history page are shown in the **browser's** time zone, formatted `YYYY-MM-DD HH:MM`
  (`{% local_time ... 'iso' %}`; the `<time datetime="...">` attribute holds the UTC value).
- Background tasks run inline, so a receipt is charged during its upload's final request, and a
  welcome grant or a promo reservation is settled during the confirming request.
- **Email** goes over real SMTP and is read back at **`/__e2e__/mail/`** (`?to=<address>` filters by
  the address the site sent it to). Delivery takes a few seconds: "open `/__e2e__/mail/?to=...`"
  always means reload until it arrives, up to a minute. Links in an email are absolute
  (`BASE_URL`) and clickable there; the HTML body is shown as source. In the `?to=` value write a
  `+` as `%2B` (`?to=qa.bill.123456%2Btwo@example.com`): a bare `+` in a query string means a space.
- **Registering and confirming a fresh account** (used by many cases): on `/account/register/` fill
  **Name**, **Email**, **Password** (and **Promo code** when the case says so), press **Create
  account**; you land on **Check your email**. Open `/__e2e__/mail/?to=<address>`: an email
  `Confirm your ZipTrigo account email` with a link to `/account/confirm-email/<token>/`. Open it:
  **Confirm your email** with a **Confirm my email** button; press it: **Email Confirmed!** with
  **Go to Login**. Then sign in on `/account/login/`. Use `qa.bill.<something>.<random>@example.com`
  addresses (one `<random>` per run, e.g. 6 digits) and passwords with a digit.
- **Session A / Session B.** Some cases need two signed-in users at once: Session A is the main
  window; Session B is a private/incognito window (or a separate browser profile), whose cookies
  are separate. Sign Session B out at the end of the case.
- The promo form is a single-field form with its own message line (`#promo-msg`, `role="alert"`,
  red text under the field): it does **not** repeat the message under the field with a red border
  like the other forms (CLAUDE.md, "Field errors", not covered on purpose). The register page's
  promo field does follow the general rule.
- **Promo code setup** (BILL-14) is done in the admin as `qa.admin`. Codes are matched ignoring case
  and stored upper-case; every code in this file is fixed (`QA-BILL-...`), which is fine on a fresh
  database.
- Bell notifications: the bell in the header (button named "Notifications"); clicking it opens a
  dropdown of the latest notifications; clicking one opens its link.

## Test data

Create these local files before starting (the fake AI reads any real image as a Staples receipt):

- `receipt-1.png`: any small valid PNG (or a JPG / one-page PDF; under 15 MB).
- `receipt-2.png`: another small valid PNG (a different file, so it isn't flagged a duplicate).
- `broken.png`: the 8-byte PNG signature followed by junk, so it passes the type check but cannot
  be read, e.g. `printf '\x89PNG\r\n\x1a\nthis is not really an image' > broken.png`.

---

### BILL-01 The history page needs a login

**Purpose:** `/billing/credits/` is `@login_required`.
**Preconditions:** logged out.

| # | Action | Expected result |
|---|---|---|
| 1 | Go to `/billing/credits/`. | Redirected to `/account/login/?next=/billing/credits/`. No balance or table is shown. |
| 2 | Go to `/billing/credits` (no trailing slash). | Redirected to `/billing/credits/`, then to the login page as above. |
| 3 | Sign in as `qa.user@example.com` on that page. | You land on `/billing/credits/`. |

### BILL-02 History page with the seeded purchase

**Purpose:** the page shows the balance and the ledger with the right columns and formatting.
**Preconditions:** logged in as `qa.user@example.com` (from BILL-01), on `/billing/credits/`.

| # | Action | Expected result |
|---|---|---|
| 1 | Look at the page. | Tab title `Credits History - ZipTrigo`. A card with a coins icon, heading **Credits History** and "Current balance: **100** credits". At the top right of the card (wrapping under the heading on a narrow window) a **Back to Account** link with a left arrow. Under the heading a "Have a promo code?" form with a `CODE` field and a **Redeem** button (see BILL-11). |
| 2 | Look at the table header. | Three columns, in this order: **Date**, **Change**, **Description**. |
| 3 | Look at the rows. | Exactly one row (a transaction whose description only repeats its type label shows an empty Description cell, not the label twice): Date = today's date and a time in the browser's time zone (`YYYY-MM-DD HH:MM`, close to when the server was started; hover/inspect: the `<time>` element's `datetime` attribute is the UTC instant); Change = **+100** in green followed by a small grey pill `Purchase`; Description = `e2e seed`. |
| 4 | Look below the table. | No pagination bar ("Page 1 of 1" is **not** shown when there is only one page). |
| 5 | Note the transaction type label. | A grey pill with the display label, `Purchase`. |
| 6 | Click **Back to Account**. | Goes to `/account/`: section **Credits**, "Current balance" **100 credits**, and a **Usage History** button. |
| 7 | Click **Usage History**. | Back on `/billing/credits/`. |

### BILL-03 Empty state for a user with no transactions

**Purpose:** a user with no ledger rows sees an empty state, and the zero balance is consistent
everywhere.
**Preconditions:** log out, then log in as `qa.broke@example.com`.

| # | Action | Expected result |
|---|---|---|
| 1 | Go to `/billing/credits/`. | "Current balance: **0** credits". The table has its three column headings and one full-width row reading "No credit transactions yet." (grey, centred). No pagination bar. |
| 2 | Go to `/account/`. | **Credits** section shows **0 credits**. |
| 3 | Go to `/transfer/send/`. | "Your balance: 0 credits. Sending needs at least 1." followed by a **Buy credits** link. |
| 4 | Click **Buy credits**. | Goes to `/billing/credits/`. Observation to record: the page offers no way to buy credits (only the history), so the link's wording promises something the destination doesn't offer. |
| 5 | Go to `/receipts/upload/`. | Top right: "Balance: **0 credits**" linking to `/billing/credits/`; the drop zone says "1 credit each". |

### BILL-04 Other users' transactions are never shown

**Purpose:** the history is per user.
**Preconditions:** logged in as `qa.broke@example.com`.

| # | Action | Expected result |
|---|---|---|
| 1 | Go to `/billing/credits/?page=2` and `/billing/credits/?user=qa.user@example.com`. | Still only your own (empty) history; the query parameters don't reveal anyone else's rows. `?page=2` on a one-page history shows the last (only) page, not an error. |
| 2 | Go to `/billing/credits/?page=abc`. | Page 1, no error. |

### BILL-05 A receipt upload spends 1 credit

**Purpose:** spending credits in a product appears in the ledger and lowers the balance everywhere.
**Preconditions:** log out, log in as `qa.user@example.com` (balance 100).

| # | Action | Expected result |
|---|---|---|
| 1 | Go to `/receipts/upload/`. | "Balance: **100 credits**". |
| 2 | Click **Choose files** and pick `receipt-1.png`. | A row appears for the file; its status goes through "Preparing…", "Uploading…", "Finishing…" and ends with "✓ Uploaded. View receipt". |
| 3 | Look at the balance at the top of the upload page without reloading. | It may still say 100 (it is rendered when the page loads). Reload the page: now "Balance: **99 credits**". |
| 4 | Click the balance link. | `/billing/credits/`: "Current balance: **99** credits". Two rows, newest first: **-1** in red with a `Spend` pill, description `Receipt processing: receipt-1.png`; then the `+100 Purchase` `e2e seed` row. |
| 5 | Go to `/account/`. | **99 credits**. |
| 6 | Go to `/receipts/`. | The receipt is listed as processed (vendor **Staples**, amount 42.10). |

### BILL-06 A file that can't be read is refunded

**Purpose:** a charge for a file the pipeline could not read is refunded and both rows show.
**Preconditions:** logged in as `qa.user@example.com` (balance 99 after BILL-05). Needs
`broken.png`; mark BLOCKED if you can't create it.

| # | Action | Expected result |
|---|---|---|
| 1 | On `/receipts/upload/`, choose `broken.png`. | The upload itself succeeds ("✓ Uploaded. View receipt"). |
| 2 | Click **View receipt**. | The receipt is **Failed** with the explanation "We couldn't read this file." and "Try a sharper photo or scan and upload it again.", not a server error. |
| 3 | Go to `/billing/credits/`. | Balance still **99**. Two new rows on top, newest first: **+1** green `Refund` pill "Receipt could not be processed: broken.png", and under it **-1** red `Spend` pill "Receipt processing: broken.png". |
| 4 | If instead the receipt shows **Trying again shortly** (a transient failure, not a refund). | Record it with a screenshot: the document-reading child process failed to start on this machine, which is an environment problem worth reporting separately. |

### BILL-07 Admin adjustments appear in the user's history, escaped

**Purpose:** manual adjustments show as `adjustment` rows, negative ones in red, and a description
containing HTML is shown as text.
**Preconditions:** log out, log in as `qa.admin@example.com`.

| # | Action | Expected result |
|---|---|---|
| 1 | Go to `/admin/billing/credittransaction/` and click **Adjust credits** (top right). | `/admin/billing/credittransaction/adjust/`: heading **Adjust credits**, fields **User email**, **Direction** (Add / Spend), **Amount**, **Description**, button **Apply**. |
| 2 | Fill User email `qa.user@example.com`, Direction **Add**, Amount `5`, Description `<b>Goodwill</b> & "thanks"`, press **Apply**. | A success message "Adjusted credits for qa.user@example.com. New balance: 104." (99 + 5, if BILL-06 left it at 99) and the form is cleared. |
| 3 | Fill `qa.user@example.com`, **Spend**, `3`, Description empty, **Apply**. | "Adjusted credits for qa.user@example.com. New balance: 101." |
| 4 | Fill `qa.user@example.com`, **Spend**, `1000`, **Apply**. | Error message "Insufficient credits for this spend adjustment."; the balance is unchanged. |
| 5 | Log out, log in as `qa.user@example.com`, go to `/billing/credits/`. | Balance **101**. Newest first: **-3** red `Adjustment` pill "Admin adjustment" (the default description); **+5** green `Adjustment` pill with the description shown literally as `<b>Goodwill</b> & "thanks"` (the tags are visible text, nothing is bold); then the BILL-06 and BILL-05 rows. No row for the refused 1000 spend. |

### BILL-08 A top-up resumes a receipt waiting for credits

**Purpose:** with 0 credits a receipt waits instead of being charged; adding credits processes it
and the ledger shows the top-up and the charge.
**Preconditions:** log out, log in as `qa.broke@example.com` (balance 0, no rows).

| # | Action | Expected result |
|---|---|---|
| 1 | On `/receipts/upload/`, choose `receipt-2.png`. | "✓ Uploaded. View receipt" (the upload is allowed with 0 credits). |
| 2 | Click **View receipt**. | A panel **Waiting for credits**: "We'll read this receipt as soon as you add credits." with a **Top up** link pointing to `/billing/credits/`; no extracted fields yet. |
| 3 | Go to `/billing/credits/`. | Still "Current balance: **0** credits" and "No credit transactions yet." (nothing was charged). |
| 4 | Log out, log in as `qa.admin@example.com`, and on `/admin/billing/credittransaction/adjust/` add `2` credits to `qa.broke@example.com` with description `top-up test`. | "Adjusted credits for qa.broke@example.com. New balance: 2." (the message is computed right after the add; the charge may already have happened, so `1` is also acceptable here). |
| 5 | Log out, log in as `qa.broke@example.com`, go to `/billing/credits/`. | "Current balance: **1** credit" (singular). Newest first: **-1** red `Spend` pill "Receipt processing: receipt-2.png", then **+2** green `Adjustment` pill "top-up test". |
| 6 | Open `/receipts/`. | The receipt is no longer waiting: it is processed (vendor **Staples**). |

### BILL-09 Pagination at 25 rows

**Purpose:** the history paginates 25 rows per page with Previous/Next links.
**Preconditions:** logged in as `qa.admin@example.com` (1 seed row on a fresh database). This case
is repetitive: it needs 25 more rows.

| # | Action | Expected result |
|---|---|---|
| 1 | On `/admin/billing/credittransaction/adjust/`, add `1` credit to `qa.admin@example.com` with description `page test 1`; repeat with `page test 2` ... `page test 25` (the form clears after each success). | 25 success messages in turn; the last says "New balance: 125." |
| 2 | Go to `/billing/credits/`. | Balance **125**. 25 rows, the newest (`page test 25`) on top and `page test 1` last. Under the table "Page 1 of 2" and a **Next** link only (no Previous). |
| 3 | Click **Next**. | URL `?page=2`: one row, the `+100 Purchase` `e2e seed` row; "Page 2 of 2" and a **Previous** link only. |
| 4 | Click **Previous**. | Back to page 1. |
| 5 | Go to `/billing/credits/?page=999`, then `?page=0`, then `?page=-1`, then `?page=abc`. | The three out-of-range numbers show the last page (`Page 2 of 2`, Django's `get_page`); `abc` shows page 1. Never an error page. |
| 6 | Log out, log in as `qa.user@example.com`, open `/billing/credits/`. | None of the `page test` rows appear (they belong to `qa.admin`). |

### BILL-10 Layout of the history page

**Purpose:** the page looks right in both themes and at phone width.
**Preconditions:** logged in as `qa.admin@example.com` (26 rows after BILL-09).

| # | Action | Expected result |
|---|---|---|
| 1 | At desktop width, look at the table. | Columns aligned; the Date and Change columns don't wrap; long descriptions wrap inside the Description column. Green/red amounts are readable. |
| 2 | Switch to dark mode: choose **Dark** under Appearance on `/account/`, then return to this page. | Card, table borders and text are readable on the dark background; green and red amounts remain readable. Switch back to **System** afterwards. |
| 3 | Resize to 375 x 812. | The page has no horizontal scroll and the table has no scrolling box of its own: the header row is gone and each transaction is a two-line card (the change with its pill on the left and the date on the right; the full description on the second line, wrapping, never clipped). The heading, balance and **Back to Account** link are visible (they may wrap). The promo form and the pagination bar fit. |
| 4 | Reset the viewport to desktop. | - |

### BILL-11 Promo code form on the history page

**Purpose:** the "Have a promo code?" form announces its refusals and the redeem link explains
itself to a signed-out visitor. (Redeeming valid codes, and every other refusal, is covered from
BILL-13 on, once codes exist.)
**Preconditions:** logged in as `qa.user@example.com`, on `/billing/credits/`.

| # | Action | Expected result |
|---|---|---|
| 1 | Press **Redeem** with the field empty. | The browser's own "required" bubble stops the submit. |
| 2 | Type `nope-nope` (it shows upper case) and press **Redeem**. | No reload; a red line `Invalid code.` under the field (it sits in `#promo-msg`, a `role="alert"` live region); the field keeps `NOPE-NOPE`. The balance is unchanged. |
| 3 | Log out and open `/billing/redeem/SOME-CODE/`. | Redirected to `/account/login/?next=/billing/redeem/SOME-CODE/&promo=SOME-CODE`. Above the sign-in form a ticket-icon note: "Sign in to redeem your promo code. We'll check it and take you to the redeem page once you are in." The note does **not** repeat or judge the code. |
| 4 | Open `/account/login/` with no `promo` parameter. | No such note. |
| 5 | Sign in as `qa.user@example.com` on that login page. | You land on `/billing/redeem/SOME-CODE/`: the form with `SOME-CODE` prefilled (nothing is redeemed by opening the page). |

### BILL-12 Welcome credits of a new account

**Purpose:** a genuinely new, confirmed account is given 1,000 credits once, and says so.
**Preconditions:** none. Register and confirm a fresh account (see Preconditions) as `qa.bill.<random>@example.com`, password `BillWelcome-4821` (remember `<random>`: BILL-28 and BILL-30 reuse it).

| # | Action | Expected result |
|---|---|---|
| 1 | Sign in as the new account and open `/billing/credits/`. | "Current balance: **1000** credits". One row: **+1000** in green with a grey `Welcome credits` pill and the description **A gift for your new account** (the label is not repeated). |
| 2 | Click the bell in the header. | A notification "Welcome! We added 1000 credits to your account" with the body "Use them in any ZipTrigo app. Your credits history shows where they go." Clicking it opens `/billing/credits/`. |
| 3 | Open `/account/`. | The Credits section reads **1000 credits**. |
| 4 | Log out. Register a second account with the same address written with a `+tag` (`qa.bill.<random>+two@example.com`) and confirm it (the confirmation email goes to the `+two` address). | Confirming works as usual (**Email Confirmed!**). |
| 5 | Sign in as the `+two` account and open `/billing/credits/`. | It got **no** welcome credits: "Current balance: **0** credits", "No credit transactions yet.", and the bell has no welcome notification. |
| 6 | Open `/__e2e__/mail/?to=qa.bill.<random>%2Btwo@example.com` (reload until it arrives). | Besides the confirmation email, one email `About your ZipTrigo welcome credits` saying why (its content is checked in BILL-28). Log out. |

---

## Promo codes

Cases BILL-13 to BILL-27 run in order. Names used below:

- **P**: the promo account registered in BILL-13, `qa.bill.promo.<random>@example.com`, password
  `BillPromo-4821`. It starts with the 1,000 welcome credits; its expected balance is given after each
  case (1,000 -> 1,050 -> 1,070 -> 1,100 -> 1,115 -> 1,125 -> 1,130).
- `qa.user@example.com`: its balance depends on the earlier cases (101 if BILL-05 to BILL-07 ran as
  written), so its steps say "goes up by N" and give the number for that case.

### BILL-13 Setup: a fresh account for the promo cases

**Purpose:** P exists, is confirmed and holds its welcome credits.
**Preconditions:** logged out.

| # | Action | Expected result |
|---|---|---|
| 1 | Register `qa.bill.promo.<random>@example.com` (Name `Promo Tester`, password `BillPromo-4821`, Promo code empty) and confirm it (see Preconditions). | **Check your email**, then the confirmation email arrives, then **Email Confirmed!**. |
| 2 | Sign in as P and open `/billing/credits/`. | "Current balance: **1000** credits"; one `+1000` `Welcome credits` row. Log out. |

### BILL-14 Setup: create the promo codes in the admin

**Purpose:** the codes the following cases redeem. (This is setup; `admin.md` tests the admin pages.)
**Preconditions:** BILL-13. Log in as `qa.admin@example.com`.

| # | Action | Expected result |
|---|---|---|
| 1 | Go to `/admin/billing/promocode/add/`. | The add form with sections for the code (**Code**, **Effect**, **Credits**, **Campaign**, **Active**, ticked by default), **Validity** (**Starts at**, **Ends at**, each a date and a time box; **Max redemptions**), **Eligibility** (**New accounts within days**, **Allowed emails**) and **Origin** (**Notes**). |
| 2 | Create each code below with **Save** (one add form per code; leave every field not listed at its default: Effect `Credits`, Active ticked, the rest empty). Type the first one's code in lower case. | After each save a success message and the list. The list shows every code **upper-cased** (`qa-bill-50` is stored as `QA-BILL-50`). |
| 3 | Look at the list. | 14 codes, each with its credits; the **Used / max** column reads `0` (or `0 / 1` for the two with a cap). |

| Code | Credits | Other fields |
|---|---|---|
| `qa-bill-50` | 50 | Campaign `Spring <i>launch</i> & co` |
| `QA-BILL-ACCT` | 20 | - |
| `QA-BILL-LINK` | 30 | Campaign `Link test` |
| `QA-BILL-OFF` | 5 | **Active** unticked |
| `QA-BILL-SOON` | 5 | Starts at `2030-01-01` `00:00:00` |
| `QA-BILL-OLD` | 5 | Ends at `2020-01-01` `00:00:00` |
| `QA-BILL-ONE` | 15 | Max redemptions `1` |
| `QA-BILL-NEW` | 5 | New accounts within days `0` (no existing account is "new" enough, so the form always refuses it; see BILL-25 for signup) |
| `QA-BILL-MINE` | 25 | Allowed emails `QA.User@Example.com` (mixed case on purpose) |
| `QA-BILL-LATE` | 10 | - |
| `QA-BILL-SIGNUP` | 40 | Campaign `Signup` |
| `QA-BILL-NEW2` | 5 | New accounts within days `0` |
| `QA-BILL-HONOR` | 35 | - |
| `QA-BILL-CAP` | 5 | Max redemptions `1` |

Log out of `qa.admin` at the end.

### BILL-15 Redeem a code on the credits history page

**Purpose:** a valid code adds its credits at once, with a flash, a ledger line naming the code and
its campaign (escaped), and the new balance everywhere; entry ignores case and surrounding spaces.
**Preconditions:** BILL-14. Signed in as P (balance 1,000), on `/billing/credits/`.

| # | Action | Expected result |
|---|---|---|
| 1 | Type `  qa-bill-50  ` (lower case, spaces around it) in the **Have a promo code?** field. | The field shows it upper-cased (`QA-BILL-50`, CSS only). |
| 2 | Press **Redeem**. | The button shows a spinner and is disabled while the request runs. The page reloads at `/billing/credits/` with a green flash (check-circle icon, `role="status"`, a **Dismiss** ×): `Added 50 credits with promo code QA-BILL-50.` |
| 3 | Look at the balance and the table. | "Current balance: **1050** credits". A new top row: **+50** green with a grey `Promo code` pill and the description shown literally as `Promo code QA-BILL-50 (Spring <i>launch</i> & co)` (the `<i>` tags visible as text, nothing in italics); below it the `+1000 Welcome credits` row. The form's field is empty again. |
| 4 | Press **Dismiss** on the flash, then reload. | The flash goes and doesn't come back. |
| 5 | Open `/account/`. | Credits section: **1050 credits**. |

### BILL-16 The same code twice is refused

**Purpose:** a code is once per user, whatever the case it is typed in.
**Preconditions:** BILL-15. Signed in as P, on `/billing/credits/`.

| # | Action | Expected result |
|---|---|---|
| 1 | Type `Qa-Bill-50` and press **Redeem**. | No reload: the form re-renders in place with `You already redeemed this code.` in red under the field; the field keeps what was typed. No flash. |
| 2 | Reload the page. | Balance still **1050**; still only one `Promo code QA-BILL-50 ...` row. |

### BILL-17 Redeem on the account page

**Purpose:** the account page's form works the same, returns to the account page, and shows a
refusal in place; Enter submits it.
**Preconditions:** BILL-16. Signed in as P.

| # | Action | Expected result |
|---|---|---|
| 1 | Open `/account/`. In the Credits section, below **Usage History**, the **Have a promo code?** form. Click into the field (or Tab to it), type `QA-BILL-OFF` and press **Enter**. | The form submits without a mouse. It re-renders in place with `Invalid code.` (an inactive code reads exactly like an unknown one); you are still on `/account/`; the balance is still **1050 credits**. |
| 2 | Clear the field, type `qa-bill-acct`, press **Redeem**. | The page reloads at **`/account/`** (not the history page) with the flash `Added 20 credits with promo code QA-BILL-ACCT.`; the Credits section reads **1070 credits**. |
| 3 | Open `/billing/credits/`. | Top row **+20** `Promo code`, description `Promo code QA-BILL-ACCT` (no campaign, so no parentheses). |

### BILL-18 The redeem link while signed in

**Purpose:** `/billing/redeem/<code>/` shows the form prefilled and only redeems when the user presses
Redeem; odd codes in the URL are shown escaped and cut to 32 characters; the POST endpoint can't be
opened directly.
**Preconditions:** BILL-17. Signed in as P (balance 1,070).

| # | Action | Expected result |
|---|---|---|
| 1 | Open `/billing/redeem/qa-bill-link/`. | Tab title `Redeem a promo code - ZipTrigo`. A centred card with a ticket icon, heading **Redeem a promo code**, "Current balance: **1070** credits", the **Have a promo code?** form with `QA-BILL-LINK` prefilled (upper-cased in the value), and a **Credits history** link with a left arrow below it. |
| 2 | Open `/billing/credits/` in the same tab. | Balance still **1070**: opening the link redeemed nothing. Go back to `/billing/redeem/qa-bill-link/`. |
| 3 | Press **Redeem**. | Lands on **`/billing/credits/`** with the flash `Added 30 credits with promo code QA-BILL-LINK.`; balance **1100**; top row **+30** `Promo code` `Promo code QA-BILL-LINK (Link test)`. |
| 4 | Open `/billing/redeem/QA-BILL-LINK/` again and press **Redeem**. | Stays on the redeem page; `You already redeemed this code.` under the field. |
| 5 | Click **Credits history**. | `/billing/credits/`. |
| 6 | Open `/billing/redeem/%3Cb%3Ehi%3Cb%3E/`. | The page renders; the field holds the literal text `<B>HI<B>` (escaped, not markup); nothing on the page is bold or broken. Press **Redeem**: `Invalid code.` |
| 7 | Open `/billing/redeem/AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA/` (40 `A`s). | The field holds exactly 32 `A`s. |
| 8 | Open `/billing/redeem/` (no code). | HTTP 405 (an empty page; the endpoint only accepts the form's POST). Nothing is redeemed. |
| 9 | Click **Credits history** (or open `/billing/credits/`). | Balance still **1100**. |

### BILL-19 Every refusal on the form

**Purpose:** each reason a code can't be redeemed has its message, and none of them changes anything.
**Preconditions:** BILL-18. Signed in as P (balance 1,100), on `/billing/credits/`. For every step:
type the code, press **Redeem**; the form re-renders in place (no reload, no flash), the message is
the red line under the field (`#promo-msg`, announced by a screen reader as an alert), the field
keeps what was typed, and the balance stays **1100**.

| # | Action | Expected result |
|---|---|---|
| 1 | Type three spaces, press **Redeem**. | (The browser's "required" bubble does not stop spaces.) `Enter a code.` |
| 2 | `AB` | `Invalid code.` (too short to be a code: nothing is looked up). |
| 3 | `NOPE-NOPE` | `Invalid code.` |
| 4 | `QA-BILL-OFF` | `Invalid code.` (inactive). |
| 5 | `QA-BILL-SOON` | `This code is not active yet.` |
| 6 | `QA-BILL-OLD` | `This code has expired.` |
| 7 | `QA-BILL-NEW` | `This code is only for new accounts.` |
| 8 | `QA-BILL-MINE` (bound to `qa.user@example.com`, not P) | `Invalid code.`: exactly the unknown-code answer, nothing says the code exists or whose it is. |
| 9 | Try typing a 40-character code. | The field stops at 32 characters (`maxlength`). |
| 10 | Reload `/billing/credits/`. | Balance **1100**; none of these codes left a row. |

### BILL-20 A code bound to an address, and a code with a cap

**Purpose:** a code bound to an address works for that account (the address matched ignoring case);
a capped code stops at its cap for everyone else.
**Preconditions:** BILL-19. Signed in as P.

| # | Action | Expected result |
|---|---|---|
| 1 | As P, redeem `QA-BILL-ONE` on `/billing/credits/`. | Flash `Added 15 credits with promo code QA-BILL-ONE.`; balance **1115**. |
| 2 | Log out; sign in as `qa.user@example.com`; open `/billing/credits/` and note the balance B. | - |
| 3 | Redeem `qa-bill-mine`. | Flash `Added 25 credits with promo code QA-BILL-MINE.` (the admin typed the address as `QA.User@Example.com`); balance B + 25; a `+25` `Promo code` row `Promo code QA-BILL-MINE`. |
| 4 | Redeem `QA-BILL-ONE`. | `This code has been fully redeemed.` (P took its one use); balance unchanged. |
| 5 | Log out. | - |

### BILL-21 An unconfirmed email can't redeem

**Purpose:** a signed-in account whose email is no longer confirmed is refused until it is confirmed
again; confirming again grants no second welcome credits.
**Preconditions:** BILL-20. Session A signed in as `qa.admin@example.com`; Session B (private window)
signed in as P on `/billing/credits/` (balance 1,115).

| # | Action | Expected result |
|---|---|---|
| 1 | Session A: `/admin/accounts/user/`, search P's address, open it; in **Status** untick **Email confirmed**; **Save**. | Saved. |
| 2 | Session B: redeem `QA-BILL-LATE`. | `Confirm your email address first.`; balance **1115** (the session outlives the change, the form does not). |
| 3 | Session A: open P again, tick **Email confirmed**, **Save**. | Saved. |
| 4 | Session B: redeem `QA-BILL-LATE` again. | Flash `Added 10 credits with promo code QA-BILL-LATE.`; balance **1125**. |
| 5 | Session B: look at the table. | Still exactly **one** `+1000 Welcome credits` row: confirming again gave nothing more. Sign Session B out and close it; log Session A out. |

### BILL-22 Several codes on one account

**Purpose:** different codes add up on one account, each its own ledger row, newest first.
**Preconditions:** BILL-21. Sign in as P, open `/billing/credits/`.

| # | Action | Expected result |
|---|---|---|
| 1 | Read the table. | "Current balance: **1125** credits" and six rows, newest first: `+10` `Promo code QA-BILL-LATE`, `+15` `Promo code QA-BILL-ONE`, `+30` `Promo code QA-BILL-LINK (Link test)`, `+20` `Promo code QA-BILL-ACCT`, `+50` `Promo code QA-BILL-50 (Spring <i>launch</i> & co)`, `+1000` `Welcome credits` `A gift for your new account`. Every promo row has the `Promo code` pill. 1000 + 50 + 20 + 30 + 15 + 10 = 1125. |
| 2 | Log out. | - |

### BILL-23 A redeem link opened signed out, carried through sign-up

**Purpose:** a visitor without an account follows a redeem link, creates an account from the login
page with the code prefilled, and gets the code's credits (and the welcome credits) once the email
is confirmed, with a bell notification for each.
**Preconditions:** BILL-14. Logged out. S1 = `qa.bill.signup.<random>@example.com`.

| # | Action | Expected result |
|---|---|---|
| 1 | Open `/billing/redeem/qa-bill-signup/`. | Redirected to `/account/login/?next=/billing/redeem/qa-bill-signup/&promo=qa-bill-signup` (the `next` value URL-encoded; the code as typed, not upper-cased) with the note "Sign in to redeem your promo code. We'll check it and take you to the redeem page once you are in." |
| 2 | Click **Create one** (under the form, "No account?"). | `/account/register/?promo=qa-bill-signup`; the optional **Promo code** field is prefilled (shown `QA-BILL-SIGNUP`) with the hint `Its credits are added once you confirm your email.` |
| 3 | Fill Name `Signup Tester`, Email S1, Password `BillSignup-4821`, keep the code, press **Create account**. | **Check your email** (nothing is granted yet). |
| 4 | Open `/__e2e__/mail/?to=<S1>` (reload until it arrives). | The confirmation email. Do **not** open its link yet. |
| 5 | Try to sign in as S1. | Refused (the email isn't confirmed); no credits involved. |
| 6 | Open the confirmation link, press **Confirm my email**. | **Email Confirmed!** |
| 7 | Sign in as S1. | Signed in (lands on the home page). The bell shows a badge **2**. |
| 8 | Click the bell. | Two notifications: "Your promo code added 40 credits" ("Promo code QA-BILL-SIGNUP was applied to your account.") and "Welcome! We added 1000 credits to your account". Click the promo one: `/billing/credits/`. |
| 9 | Read the history. | "Current balance: **1040** credits"; two rows: `+40` `Promo code` `Promo code QA-BILL-SIGNUP (Signup)` and `+1000` `Welcome credits` (either order, they land in the same second). |
| 10 | Redeem `QA-BILL-SIGNUP` on the form. | `You already redeemed this code.` Balance unchanged. |
| 11 | Log out. | - |

### BILL-24 Promo codes refused on the register page

**Purpose:** the register page judges a typed code (validity, dates, email binding, cap) and refuses
the whole sign-up with its message, creating nothing; a valid code with an address that already has
an account changes nothing and reveals nothing.
**Preconditions:** BILL-20 (`QA-BILL-ONE` is used up). Logged out, on `/account/register/`. For
steps 1 to 6 use Name `Refuse Tester`, Email `qa.bill.refuse.<random>@example.com` and Password
`BillRefuse-4821` (retype the password each time: it is emptied after a refusal).

| # | Action | Expected result |
|---|---|---|
| 1 | Promo code `NOPE-NOPE`, **Create account**. | Stays on the page (422). The red box at the top reads `Promo code: Invalid code.`; the same message under the Promo code field, which gets a red border, `aria-invalid="true"` and the focus. Name, Email and the code keep what was typed. |
| 2 | Edit the code field. | Its message under the field disappears. |
| 3 | `QA-BILL-OFF` | `Promo code: Invalid code.` |
| 4 | `QA-BILL-SOON` / `QA-BILL-OLD` (one submit each). | `Promo code: This code is not active yet.` / `Promo code: This code has expired.` |
| 5 | `QA-BILL-MINE` (bound to qa.user, not this address). | `Promo code: Invalid code.` |
| 6 | `QA-BILL-ONE` | `Promo code: This code has been fully redeemed.` |
| 7 | Open `/__e2e__/mail/?to=qa.bill.refuse.<random>@example.com` (wait a minute). | No email: no account was created by any of these. |
| 8 | Sign in as `qa.admin` in Session B, note the **Used / max** of `QA-BILL-LINK` on `/admin/billing/promocode/` (`1`). In Session A register with Name `Existing`, Email `qa.user@example.com`, any valid password, Promo code `QA-BILL-LINK`. | The same **Check your email** page as a new account (nothing says the address is taken). |
| 9 | Session B: reload the promo code list. | `QA-BILL-LINK` still reads `1`: nothing was reserved for the existing address. Sign Session B out. |

### BILL-25 Signup codes: new-accounts-only, and honoured once reserved

**Purpose:** a "new accounts only" code is accepted at sign-up (the account is about to be created);
a code reserved at sign-up is honoured on confirmation even if staff deactivated it in between.
**Preconditions:** BILL-14. Logged out. S2 = `qa.bill.new.<random>@example.com`, S3 =
`qa.bill.honor.<random>@example.com`.

| # | Action | Expected result |
|---|---|---|
| 1 | Register S2 (password `BillNew-4821`) with Promo code `QA-BILL-NEW2`. | **Check your email**: the code is accepted although the form would refuse it (BILL-19 step 7). |
| 2 | Confirm S2, sign in, open `/billing/credits/`. | Balance **1005**: `+5` `Promo code QA-BILL-NEW2` and `+1000` `Welcome credits`; the bell has "Your promo code added 5 credits". Log out. |
| 3 | Register S3 (password `BillHonor-4821`) with Promo code `QA-BILL-HONOR`. Do not confirm yet. | **Check your email**. |
| 4 | As `qa.admin` (Session B): `/admin/billing/promocode/`: `QA-BILL-HONOR` reads `0 (+1 pending)`. Open it: **Code** and **Credits** are now read-only (it has been claimed). Untick **Active**, **Save**. | Saved; the code is inactive. Sign Session B out. |
| 5 | Session A: confirm S3, sign in, open `/billing/credits/`. | Balance **1035**: `+35` `Promo code QA-BILL-HONOR` is granted anyway (judged at sign-up), plus the welcome `+1000`; the bell has "Your promo code added 35 credits". |
| 6 | Redeem `QA-BILL-HONOR` on the form. | `Invalid code.` (it is inactive now; nothing changes). Log out. |

### BILL-26 A reservation holds a use until it is released

**Purpose:** an unconfirmed sign-up's reservation counts against a code's cap; when it is released
(the daily job does this after 30 days) the use comes back and the unconfirmed account gets no
promo credits when it confirms later.
**Preconditions:** BILL-25 (every earlier sign-up with a code is confirmed). Logged out. S4 =
`qa.bill.cap.<random>@example.com`, S5 = `qa.bill.cap2.<random>@example.com`.

| # | Action | Expected result |
|---|---|---|
| 1 | Register S4 (password `BillCap-4821`) with Promo code `QA-BILL-CAP` (max 1). Do not confirm. | **Check your email**. |
| 2 | Sign in as P; redeem `QA-BILL-CAP`. | `This code has been fully redeemed.` (S4's reservation holds its one use); P's balance **1125**. Log out. |
| 3 | Register S5 with Promo code `QA-BILL-CAP`. | 422: `Promo code: This code has been fully redeemed.`; no account created. |
| 4 | (shell step) Release the reservation as the daily job would after 30 days: `python tests_e2e/ai/manage.py shell -c "from datetime import timedelta; from apps.billing.services.promo import release_stale_reservations; print(release_stale_reservations(max_age=timedelta(0)))"` | Prints `1`. |
| 5 | Sign in as P; redeem `QA-BILL-CAP`. | Flash `Added 5 credits with promo code QA-BILL-CAP.`; balance **1130**. Log out. |
| 6 | Confirm S4 (its confirmation link from the mailbox), sign in, open `/billing/credits/`. | Balance **1000**: only the welcome credits; no `Promo code` row and no promo notification in the bell (only the welcome one). |
| 7 | Redeem `QA-BILL-CAP` on the form. | `This code has been fully redeemed.` (P has the use now). Log out. |

### BILL-27 Redeem page: phone width, dark mode, keyboard

**Purpose:** the redeem link page and the promo form are usable at phone width, in dark mode and
from the keyboard.
**Preconditions:** signed in as P.

| # | Action | Expected result |
|---|---|---|
| 1 | Open `/billing/redeem/QA-BILL-TEST/` at desktop width. | The card is centred and narrow (`max-w-md`). The balance reads "Current balance: 1130 credits". |
| 2 | Resize to 375 x 812. | No horizontal scroll; the field and **Redeem** button sit side by side within the card (the field shrinks), the heading and the **Credits history** link fit. Press **Redeem**: `Invalid code.` wraps inside the card. |
| 3 | Reset to desktop. Choose **Dark** under Appearance on `/account/`, reopen the redeem page. | Card, field (dark background, light text), button, the red refusal line and the link are readable. Switch back to **System**. |
| 4 | Reload the redeem page and press Tab from the top. | The first stop is "Skip to content"; continuing reaches the header, then the code field, then **Redeem**, then **Credits history**, each with a visible focus ring. With focus on the field, Enter submits. |
| 5 | Log out, sign in as `qa.broke@example.com` (balance 1 after BILL-08) and open `/billing/redeem/QA-BILL-TEST/`. | The balance reads `1 credit` here, as on the history page. |

---

## Welcome credits

### BILL-28 The refusal email: content, escaping and its link

**Purpose:** the "no welcome credits" email says why without naming the other account, escapes the
user's name, and its link works.
**Preconditions:** BILL-12 (the `<random>` it used). Logged out.

| # | Action | Expected result |
|---|---|---|
| 1 | Register `qa.bill.<random>+three@example.com` with Name `<b>Bill</b> & "Co"` (password `BillWelcome-4821`) and confirm it. | **Email Confirmed!** |
| 2 | Open `/__e2e__/mail/?to=qa.bill.<random>%2Bthree@example.com` (reload until it arrives). | Two emails: the confirmation and `About your ZipTrigo welcome credits`. |
| 3 | Read the welcome email's text. | It starts `Hi <b>Bill</b> & "Co",` (literal), then explains that new accounts get free welcome credits but this one didn't because the address, or a variation of it (a +tag or, for Gmail, different dots), was already used for a ZipTrigo account before; that nothing else is affected; and that you can add credits or redeem a promo code at a link to `<BASE_URL>/billing/credits/`; signed "The ZipTrigo Team". It does **not** name the other account or its address. |
| 4 | Read the HTML body (source). | The greeting is escaped (`Hi &lt;b&gt;Bill&lt;/b&gt; &amp; &quot;Co&quot;,` or equivalent entities), never a real `<b>` tag; the link is an `<a href>` to the same credits URL. |
| 5 | Click the link while logged out. | The login page with `?next=/billing/credits/`; sign in as the `+three` account: `/billing/credits/`, "Current balance: **0** credits", "No credit transactions yet." |
| 6 | Click the bell. | No welcome notification: the dropdown reads "No new notifications." |
| 7 | Reload the mailbox after a minute. | Still exactly one welcome-credits email for this address (it is sent once). Log out. |

### BILL-29 The first variant to confirm gets the credits

**Purpose:** "already used" means already *confirmed*: of two unconfirmed sign-ups that are variants
of one address, the one confirmed first gets the credits, whichever registered first.
**Preconditions:** logged out. A = `qa.bill.first.<random>@example.com`, A+ =
`qa.bill.first.<random>+shop@example.com`.

| # | Action | Expected result |
|---|---|---|
| 1 | Register A, then register A+ (password `BillFirst-4821` for both). Confirm neither yet. | **Check your email** twice. |
| 2 | Confirm **A+** first (its email in `/__e2e__/mail/?to=<A+>`, the `+` written `%2B`), sign in as A+. | Balance **1000**, `+1000 Welcome credits`, the welcome notification. Log out. |
| 3 | Confirm A, sign in as A. | Balance **0**, no ledger row, no welcome notification; `/__e2e__/mail/?to=<A>` has `About your ZipTrigo welcome credits`. Log out. |

### BILL-30 Gmail dots and googlemail.com; dots elsewhere are different addresses

**Purpose:** for Gmail, dots in the local part and `googlemail.com` are the same mailbox; for any other
domain they are different addresses.
**Preconditions:** logged out. G = `qa.bill.dots.<random>@gmail.com`, G2 = `qabilldots<random>@googlemail.com`,
D = `q.a.bill.<random>@example.com` (BILL-12's address with an extra dot). These go to the local mail
server like every other address; if no email ever arrives for the `gmail.com`/`googlemail.com`
addresses within a minute, mark steps 1 to 3 BLOCKED (the mail server didn't accept that domain) and
still run step 4.

| # | Action | Expected result |
|---|---|---|
| 1 | Register and confirm G (password `BillDots-4821`), sign in. | Balance **1000** (`+1000 Welcome credits`). Log out. |
| 2 | Register and confirm G2, sign in. | Balance **0**, no ledger row, no welcome notification. Log out. |
| 3 | Open `/__e2e__/mail/?to=<G2>`. | `About your ZipTrigo welcome credits`. |
| 4 | Register and confirm D, sign in. | Balance **1000**: outside Gmail a dot makes a different address, so D is new. No welcome-credits email for D. Log out. |

### BILL-31 Setting the welcome credits to 0 turns them off, and it isn't retroactive

**Purpose:** with **Welcome credits** at 0 a new account gets nothing (no row, no notification, no
email); turning them back on does not give it anything later.
**Preconditions:** logged out. O = `qa.bill.off.<random>@example.com`. Run this case after every other
case that registers an account, and put the setting back at the end.

| # | Action | Expected result |
|---|---|---|
| 1 | As `qa.admin`: `/admin/billing/billingsettings/`. | The Billing settings form, **Welcome credits** `1000`. |
| 2 | Set **Welcome credits** to `0`, **Save**. | `Billing settings saved.` Log out. |
| 3 | Register and confirm O (password `BillOff-4821`), sign in, open `/billing/credits/`. | Balance **0**, "No credit transactions yet.", no welcome notification. `/__e2e__/mail/?to=<O>` has only the confirmation email (no refusal email: nothing was refused). Log out. |
| 4 | As `qa.admin`: set **Welcome credits** back to `1000`, **Save**. | `Billing settings saved.` Log out. |
| 5 | Sign in as O again, open `/billing/credits/`. | Still **0**: an account created while the setting was 0 never gets them. Log out. |

## Credits history: types and descriptions

### BILL-32 Every transaction type's label, and the description rules

**Purpose:** each of the six transaction types shows its own label, and a description that only
repeats the label is not shown twice.
**Preconditions:** BILL-05 to BILL-07 and BILL-22 ran.

| # | Action | Expected result |
|---|---|---|
| 1 | As `qa.admin` on `/admin/billing/credittransaction/adjust/`, add `1` credit to `qa.user@example.com` with Description `Adjustment`. | "Adjusted credits for qa.user@example.com. New balance: ...". Log out. |
| 2 | Sign in as `qa.user`, open `/billing/credits/`. | The new top row: `+1` with the `Adjustment` pill and an **empty** Description cell (the label isn't repeated). Further down the pills `Promo code` (BILL-20), `Adjustment`, `Refund`, `Spend` and `Purchase`, each next to its amount (green `+` for credits added, red `-` for spent). |
| 3 | Sign in as P, open `/billing/credits/`. | The `Welcome credits` pill on the `+1000` row (description `A gift for your new account`) and `Promo code` pills. Together with step 2 all six labels appear: Purchase, Spend, Adjustment, Refund, Promo code, Welcome credits; never a raw value such as `promo` or `welcome`. Log out. |

## Out of scope

- The redeem form without JavaScript (a refusal then becomes an error flash on the page the form
  came from): the browser always has htmx here.
- The rate limits on redeeming (`PROMO_REDEEM_USER`, `PROMO_REDEEM_IP`): rate limiting is off on this
  server.
- Welcome credits for accounts created through Google/Microsoft sign-in (out of scope here) and for
  staff accounts (`admin.md`).
- The daily `billing.settle_welcome_credits` safety net and the 30-day wait before a reservation is
  released (BILL-26 runs the release by hand); both are unit-tested.
- The JWT `/api/billing/` endpoints (unit-tested).
