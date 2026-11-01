# Billing: credit balance and history

**Scope.** The user-facing side of `apps.billing`: the balance shown on the account page and in the
products, the credits history page at `/billing/credits/` (`billing/credits_history.html`: columns,
colours, transaction types, the empty state, pagination at 25 rows per page), its login
requirement and the links to it, and how the ledger reacts to things that change the balance:
a Receipts upload (charged `ReceiptsSettings.price_per_file`, **1 credit** by default, when the
receipt is processed), the refund of a file that could not be read, a receipt parked "Waiting for
credits" and resumed by a top-up, and manual adjustments made in the admin. There is no way to buy
credits on the site; the admin adjustment tool is the only way to add them here (the tool itself is
covered in depth in `admin.md`).

## Preconditions

- The test server is running at `http://localhost:8765` on a **fresh database** (the expected
  balances below assume it). All URLs are relative to it.
- Seeded accounts (password `QaE2e-Passw0rd!`):
  - `qa.user@example.com`: 100 credits, one ledger row `+100`, type `purchase`, description
    `e2e seed`.
  - `qa.admin@example.com`: superuser, same 100-credit seed row.
  - `qa.broke@example.com`: 0 credits, no ledger rows at all.
- Log in on `/account/login/` (**Email**, **Password**, **Sign in**); log out from the account menu
  (person icon, top right) > **Logout**. A superuser logged in on the site can open `/admin/`
  directly (same session).
- Times on the history page are in UTC (`TIME_ZONE = 'UTC'`), formatted `YYYY-MM-DD HH:MM`.
- Background tasks run inline, so a receipt is charged during its upload's final request.

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
| 1 | Look at the page. | Tab title `Credits History - ZipTrigo`. A card with a coins icon, heading **Credits History** and "Current balance: **100** credits". At the top right of the card a **Back to Account** link with a left arrow. |
| 2 | Look at the table header. | Three columns, in this order: **Date**, **Change**, **Description**. |
| 3 | Look at the rows. | Exactly one row: Date = today's date and a time in UTC (`YYYY-MM-DD HH:MM`, close to when the server was started); Change = **+100** in green followed by a small grey pill `Purchase`; Description = `e2e seed`. |
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
| 3 | Go to `/billing/credits/`. | Balance still **99**. Two new rows on top, newest first: **+1** green `(refund)` "Receipt could not be processed: broken.png", and under it **-1** red `(spend)` "Receipt processing: broken.png". |
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
| 5 | Log out, log in as `qa.user@example.com`, go to `/billing/credits/`. | Balance **101**. Newest first: **-3** red `(adjustment)` "Admin adjustment" (the default description); **+5** green `(adjustment)` with the description shown literally as `<b>Goodwill</b> & "thanks"` (the tags are visible text, nothing is bold); then the BILL-06 and BILL-05 rows. No row for the refused 1000 spend. |

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
| 5 | Log out, log in as `qa.broke@example.com`, go to `/billing/credits/`. | Balance **1**. Newest first: **-1** red `(spend)` "Receipt processing: receipt-2.png", then **+2** green `(adjustment)` "top-up test". |
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
| 3 | Resize to 375 x 812. | The page itself has no horizontal scroll; if the table is wider than the card, it scrolls horizontally **inside** its box. The heading, balance and **Back to Account** link are visible (they may wrap). The pagination bar fits. |
| 4 | Reset the viewport to desktop. | - |
