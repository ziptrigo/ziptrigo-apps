# Admin

## Scope

The custom Django admin site at `/admin/` (`apps/core/admin_site.py`, themed with django-jazzmin):

- access control (logged out, an ordinary user, a staff user without permissions, a superuser) and
  the admin login page;
- the dashboard, the jazzmin top menu ("Site", "Admin Tools"), the user menu and the light/dark
  theme toggle (`core/js/admin_theme_toggle.js`);
- the Admin Tools page (`/admin/tools/`): test email, masked environment variables;
- users (`/admin/accounts/user/`): list, search, filters, the email-based create form, the change
  form, setting a password, deactivating, deleting;
- billing: the read-only ledger and credit accounts, the credit adjustment tool
  (`/admin/billing/credittransaction/adjust/`);
- the four singleton settings pages (Core, Billing, File transfer, Receipts);
- file transfer moderation: transfers (read-only plus the take down / release hold / block sender
  actions), abuse reports (dismiss), the block list (CRUD with validation);
- feedback triage, the read-only Receipts admins, and the other read-only admins (scheduled jobs,
  email verifications, rate limit counters, download events).

Not covered here: the site pages themselves (see the other files; this file only uses them to
create data and to check the effect of an admin action).

## Preconditions

- The test server is running at `http://localhost:8765` (see [README](README.md)), ideally freshly
  restarted. Rate limiting is off; every email lands in the mailbox at `/__e2e__/mail/`.
- **Two browser sessions.** The admin and the site share one login cookie, so logging in on the
  site logs the admin out. Use:
  - **Session A**: the main window, logged in to the admin as `qa.admin@example.com` from ADM-04 on;
  - **Session B**: a second, isolated session (a private/incognito window or a separate browser
    profile/context) for everything done as another user.

  If your browser tool has only one session, log out and back in instead each time the session
  changes, and note it in the results.

  A second session can also be another host name of the same server (`<name>2.localhost`, see
  the README): cookies are per host name, and the fake S3 answers CORS for any origin.
- A small local text file to upload (any content), and a small JPEG, PNG or PDF for the Receipts
  upload in ADM-28.
- **Switch the welcome credits off first.** Ticking "Email confirmed" on a new, non-staff account
  (ADM-10) gives it the 1,000 welcome credits, which would break every balance in ADM-14 to ADM-16
  and ADM-21. Before ADM-10 open `/admin/billing/billingsettings/1/change/`, set **Welcome credits**
  to `0` and Save; ADM-18 step 5b puts it back to `1000` (do not skip that step: the other files
  expect new accounts to get the credits).

## Test data

| Name | Value |
|---|---|
| Password of every account below | `QaE2e-Passw0rd!` (the seeded test password; never change a seeded account's password) |
| `<rand>` | 5 random digits chosen once at the start of the run, e.g. `48213` |
| SENDER | `qa.adm.<rand>@example.com`, name `ADM Sender`, created in ADM-10 |
| USERB | `qa.adm.<rand>.b@example.com`, name `ADM User B`, created in ADM-10 (deleted in ADM-30) |
| Transfer T1 | a transfer by SENDER whose first file is named `adm-takedown-<rand>.txt` (ADM-21) |
| Transfer T2 | a transfer by SENDER whose first file is named `adm-hold-<rand>.txt` (ADM-21) |
| Recipient for transfers | `qa.adm.recipient@example.com` |

Never block `127.0.0.1`, `::1` or an `@example.com` wildcard on the block list: every account and
browser in this run would be locked out of sending.

---

### ADM-01 Logged-out visitor is sent to the admin login page

**Purpose:** the admin is not reachable without a session, and the login page looks right.
**Preconditions:** Session A, not logged in (clear cookies for `localhost:8765` if needed).

| # | Action | Expected result |
|---|---|---|
| 1 | Go to `/admin/`. | Redirected to `/admin/login/?next=/admin/`. |
| 2 | Look at the page. | Jazzmin login box with the ZipTrigo logo, the text "ZipTrigo Admin", an "Email" field, a "Password" field (masked) and a "Log in" button. Tab title starts "Log in". No raw template tags, broken image or console error. |
| 3 | Go to `/admin/tools/`. | Redirected to `/admin/login/?next=/admin/tools/`. |
| 4 | Go to `/admin/billing/credittransaction/adjust/`. | Redirected to the admin login page with `next=/admin/billing/credittransaction/adjust/`. |
| 5 | Go to `/admin/accounts/user/`. | Redirected to the admin login page; no user data is shown. |

### ADM-02 Wrong credentials and non-staff accounts are refused at the admin login

**Purpose:** only staff accounts can sign in to the admin, without revealing which part was wrong.
**Preconditions:** Session A on `/admin/login/`, not logged in.

| # | Action | Expected result |
|---|---|---|
| 1 | Enter `qa.admin@example.com` and the password `wrong-password`, click "Log in". | The login page again, with the error "Please enter the correct email and password for a staff account. Note that both fields may be case-sensitive." The email stays filled in; the password is cleared. |
| 2 | Enter `qa.user@example.com` and the correct password `QaE2e-Passw0rd!`, click "Log in". | Refused with the **same** error as step 1 (an ordinary user is not staff). No session is started: going to `/admin/` still redirects to the login page. |
| 3 | Go to `/` (the site). | The header shows you as logged out (the admin login attempt did not log `qa.user` in to the site). |

### ADM-03 An ordinary user logged in on the site cannot open the admin

**Purpose:** a site session of a non-staff user does not grant admin access.
**Preconditions:** Session B.

| # | Action | Expected result |
|---|---|---|
| 1 | In Session B go to `/account/login/` and log in as `qa.user@example.com` / `QaE2e-Passw0rd!`. | Logged in to the site (lands on the home page). |
| 2 | Go to `/admin/`. | Redirected to `/admin/login/?next=/admin/`, which shows "You are authenticated as qa.user@example.com, but are not authorized to access this page. Would you like to login to a different account?" above the login form. |
| 3 | Go to `/admin/tools/` and `/admin/billing/credittransaction/adjust/`. | Both redirect to the admin login page with the same message; neither page's content is shown. |
| 4 | Go to `/account/`. | Still logged in to the site as `qa.user` (being refused by the admin did not log you out). |
| 5 | Log out of the site (header menu). | Logged out. |

### ADM-04 Superuser logs in and the dashboard lists every app

**Purpose:** a superuser gets in and sees every registered model.
**Preconditions:** Session A on `/admin/login/`.

| # | Action | Expected result |
|---|---|---|
| 1 | Log in as `qa.admin@example.com` / `QaE2e-Passw0rd!`. | Lands on `/admin/`. Tab title "Dashboard \| ZipTrigo Admin". The navbar shows "Site" and "Admin Tools" links and a user icon menu; the left sidebar shows "ZipTrigo" and `qa.admin@example.com`. |
| 2 | Read the main area. | Groups and models, each with links: **Accounts**: Users (Add, Change). **Billing**: Billing settings (Change), Credit accounts (View), Credit transactions (View), Promo codes (Add, Change), Promo redemptions (View), Welcome credits (View). **Core**: Core settings (Change), Email verifications (View), Notifications (View), Rate limit counters (View), Scheduled jobs (View). **Feedback**: Feedback (Change). **File transfer**: Abuse reports (View), Blocked senders (Add, Change), Download events (View), File transfer settings (Change), Transfers (View). **QR codes**: QR Codes (Add, Change). **Receipts**: Inbound emails (View), Inbound messages (View), Receipts (View), Receipts accounts (View), Receipts settings (Change), Vendors (View). **Short URLs**: Blocked domains (Add, Change), Daily link stats (View), Link reports (View), Short links (View), Short URL settings (Change). **To-do**: Attachments, Comments, Inbound emails, Inbound messages, Labels, Recurrences, Reminder deliveries, Reminders, Saved filters, Tasks, To-do accounts (all View) and To-do settings (Change). |
| 3 | Check the group headings' wording. | Each heading is readable ("File transfer", "QR codes", "Short URLs", "To-do"). Record as a UX issue any heading that shows a raw app label such as "File_Transfer" or "Qr_Code". |
| 4 | Check the "Core settings", "Billing settings", "File transfer settings", "Receipts settings", "Short URL settings" and "To-do settings" rows. | Each offers only "Change", never "Add", even on a fresh server where the row does not exist yet (opening the page creates it): the pages are singletons (see ADM-17 to ADM-20). |
| 5 | Find the "Admin Tools" box below the models. | Shown to a superuser only: heading "Admin Tools", a button "Open Admin Tools" and the help text "Access utilities such as sending test emails or viewing masked environment variables." |
| 6 | Check the sidebar. | The same apps and models as the main area, expanded; clicking "Users" opens `/admin/accounts/user/`. Go back to `/admin/`. |

### ADM-05 Top menu, user menu and the navbar search

**Purpose:** the jazzmin navigation goes where it says.
**Preconditions:** Session A logged in as `qa.admin` (ADM-04).

| # | Action | Expected result |
|---|---|---|
| 1 | Click "Site" in the navbar. | Opens `/` (the site's landing page) in the same tab, with `qa.admin` logged in on the site too. Go back to `/admin/`. |
| 2 | Click "Admin Tools" in the navbar. | Opens `/admin/tools/` (page heading "Admin Tools"). Go back. |
| 3 | Click "Open Admin Tools" on the dashboard. | Also opens `/admin/tools/`. Go back. |
| 4 | Open the user icon menu (top right). | It shows "Account", "Change password", "Log out" and "See Profile". |
| 5 | Click "See Profile". | Opens the change form of `qa.admin@example.com` (`/admin/accounts/user/<id>/change/`). Go back. |
| 6 | Open the menu again and click "Change password". | Opens `/admin/password_change/` with old/new password fields. **Do not submit it.** Go back to `/admin/`. |
| 7 | In the navbar search box (placeholder "Search Users..."), type `qa.broke` and press Enter. | Opens the Users list filtered to `qa.broke@example.com` only. |

### ADM-06 Light/dark theme toggle

**Purpose:** the admin has a working light/dark switch that is remembered.
**Preconditions:** Session A on `/admin/`.

| # | Action | Expected result |
|---|---|---|
| 1 | Look at the navbar, just left of the user icon menu. | A toggle link with a sun icon (accessible name "Dark mode", `aria-pressed="false"`, tooltip "Light mode (click to switch to dark)"). The page is light. |
| 2 | Click the toggle. | The admin switches to a dark theme (dark backgrounds, light text everywhere: navbar, sidebar, content, tables; `<html data-bs-theme="dark">`); the icon becomes a moon with tooltip "Dark mode (click to switch to light)" and `aria-pressed="true"`. |
| 3 | Reload the page, then open `/admin/accounts/user/`. | Both stay dark; text is readable (no dark-on-dark), links and buttons visible. |
| 4 | Click the toggle again and reload. | Back to light, and it stays light after the reload. |

Note: if there is no toggle at all in step 1, record FAIL (the script looks for the Bootstrap 5
`#jazzy-navbar ul.navbar-nav.ms-auto` of jazzmin 3) and skip steps 2-4. The choice is stored in
`localStorage['jazzmin-theme-mode']`, which jazzmin's own early script reads, so there is no flash of
the other theme on reload.

### ADM-07 Admin Tools: send a test email

**Purpose:** the test email form sends through the site's email backend.
**Preconditions:** Session A on `/admin/tools/`.

| # | Action | Expected result |
|---|---|---|
| 1 | Look at the page. | Heading "Admin Tools". A section "Email Testing" ("Send a test email to any address.") with a "Recipient email:" field pre-filled with `qa.admin@example.com`, the help text "Email address to send the test email to." and a "Send test email" button. A section "Show Environment" with "Environment: local" and a "Show" button. |
| 2 | Replace the recipient with `qa.adm.tools.<rand>@example.com` and click "Send test email". | A success message "Test email sent to qa.adm.tools.<rand>@example.com via 1 backend(s) (0 failure(s))." The message appears **once** (record FAIL if it is shown twice). The field keeps the address. |
| 3 | In a new tab open `/__e2e__/mail/?to=qa.adm.tools.<rand>@example.com`. | Exactly one email, subject "Test email from the admin panel", body "This is a test email." / "Sent from the Django admin panel." and a "Sent:" line with the current UTC time. |
| 4 | Back on the tools page, enter `qa@nodot` and click "Send test email". | Error "Please correct the errors below." and, at the field, "Enter a valid email address." No new email in the mailbox. (If the browser's own validation blocks the submit first, that is fine; note it.) |
| 5 | Clear the field and click "Send test email". | The browser asks to fill in the field, or the page shows "This field is required."; nothing is sent. |

### ADM-08 Admin Tools: masked environment variables

**Purpose:** the environment view needs a confirmation, masks secrets and can be hidden.
**Preconditions:** Session A on `/admin/tools/`.

| # | Action | Expected result |
|---|---|---|
| 1 | Click "Show" in "Show Environment". | A modal "Confirm" / "Are you sure?" with "Cancel" and "Yes, continue". Nothing is loaded yet. |
| 2 | Click "Cancel". | The modal closes; no table appears. |
| 3 | Click "Show" again, then "Yes, continue". | The page reloads with the message "Environment variables loaded.", a table "Environment Variables" (columns Name, Value) sorted by name, and the button now reads "Hide". |
| 4 | Find `SECRET_KEY`, `JWT_SECRET`, `FILE_TRANSFER_AWS_SECRET_ACCESS_KEY` and any `OPENAI_API_KEY` / `*_PASSWORD` / `*_TOKEN` rows. | Each value is masked: only a few characters at each end are visible and the middle is asterisks (a value of 4 characters or fewer is all asterisks). |
| 5 | Find `ENVIRONMENT`, `BASE_URL`, `AWS_REGION`, `AWS_SES_SENDER`, `DJANGO_SETTINGS_MODULE`. | Shown in clear: `local`, `http://localhost:8765`, a region, a sender address, `tests_e2e.receipts_e2e_settings`. |
| 6 | Scan the whole table for credentials shown in clear: a URL with `user:password@` in it (e.g. `DATABASE_URL`, `CACHE_URL`), an access key id (`*_ACCESS_KEY_ID`), anything that looks like a key or password. | None is shown in clear. **FAIL** for each key whose value exposes a credential; in the report name the key only, never copy the value. |
| 7 | Click "Hide". | The table disappears without a page load (no confirmation), and the button reads "Show" again. |

### ADM-09 Users list: search and filters

**Purpose:** staff can find accounts.
**Preconditions:** Session A.

| # | Action | Expected result |
|---|---|---|
| 1 | Go to `/admin/accounts/user/`. | Columns Email, Name, Email confirmed, Is staff, Is active, Status, sorted by email. The four seeded accounts are listed (other accounts created by other test files may be there too). An "Add user" button. Filters on the right: status (Active / Inactive / Deleted), email confirmed, is staff, is active. |
| 2 | Check the seeded rows. | `qa.admin@example.com`: email confirmed and staff ticked; `qa.unconfirmed@example.com`: email confirmed not ticked; all four Status "Active". Boolean columns show icons, not "True"/"False". |
| 3 | Search `QA Broke`. | Only `qa.broke@example.com` (search covers the name). |
| 4 | Clear the search; filter "email confirmed: No". | `qa.unconfirmed@example.com` is listed; `qa.user` and `qa.admin` are not. |
| 5 | Clear it; filter "is staff: Yes". | `qa.admin@example.com` is listed; the ordinary seeded users are not. |
| 6 | Open the action menu ("- Select an option -"). | It offers "Delete selected users". Do not run it. |

### ADM-10 Create users with the email-based form

**Purpose:** the add form creates an account by email with a hashed password and refuses a
duplicate address in any letter case.
**Preconditions:** Session A.

| # | Action | Expected result |
|---|---|---|
| 1 | Click "Add user" (`/admin/accounts/user/add/`). | Fields: Email (required), Name, "Password-based authentication" (Enabled / Disabled, Enabled selected), Password with the validator hints, Password confirmation. No username field. The intro reads "First, enter an email address and a password. Then you'll be able to edit more of the user's options." (record FAIL if it still says "username"). |
| 2 | Enter Email `QA.User@EXAMPLE.COM`, Name `Dup`, password `QaE2e-Passw0rd!` twice; click "Save". | "Please correct the error below." and at Email: "A user with this email already exists." No account is created. |
| 3 | Enter Email `qa.adm.<rand>@example.com`, Name `ADM Sender`, password `QaE2e-Passw0rd!` and confirmation `Different-Passw0rd!`; Save. | An error at the confirmation: "The two password fields didn't match." (or Django's equivalent wording). Not created. |
| 4 | Enter the same email and name, password `12345678` twice; Save. | Refused with a validator error, e.g. "This password is entirely numeric." |
| 5 | Enter Email `qa.adm.<rand>@example.com`, Name `ADM Sender`, password `QaE2e-Passw0rd!` twice; Save. | The change form of the new user opens with "The user “qa.adm.<rand>@example.com” was added successfully. You may edit it again below." Status "Active", Email confirmed unticked, Is active ticked, staff/superuser unticked. |
| 6 | Tick "Email confirmed" and click "Save". | Back on the Users list with a success message; SENDER is listed with Email confirmed ticked. |
| 7 | Repeat steps 5-6 for USERB: `qa.adm.<rand>.b@example.com`, Name `ADM User B`. | USERB is created and confirmed. |
| 8 | Search the list for `qa.adm.<rand>`. | Exactly SENDER and USERB. |

### ADM-11 User change form: password only through the hashed field

**Purpose:** the change form never shows or accepts a raw password, and email edits keep the
case-insensitive uniqueness rule.
**Preconditions:** USERB from ADM-10. Session A.

| # | Action | Expected result |
|---|---|---|
| 1 | Open USERB's change form. | Sections "Authentication", "Personal info", "Status", "Permissions", "Important dates". Password is read-only text such as "algorithm: pbkdf2_sha256 iterations: … salt: abc****** hash: def******", followed by a "Reset password" link and "Raw passwords are not stored, so there is no way to see the user’s password." There is no password input. "Google sub", "Email confirmed at", "Created at", "Updated at" are read-only. |
| 2 | Change Email to `QA.Broke@Example.com` and click "Save and continue editing". | "Please correct the error below." with "A user with this email already exists." at Email. Nothing saved. |
| 3 | Reload the form, change Name to `ADM User B2`, click "Save and continue editing". | Saved ("… was changed successfully. You may edit it again below."); Name shows `ADM User B2`; Email unchanged. |
| 4 | Click "Reset password". | `/admin/accounts/user/<id>/password/` titled "Change password" with "Enter a new password for the user qa.adm.<rand>.b@example.com." and two password fields; button "Change password". |
| 5 | Enter `QaE2e-Passw0rd!` and `QaE2e-Passw0rd!x`; submit. | A mismatch error; not changed. |
| 6 | Enter `QaE2e-Passw0rd!` in both and submit. | Back on the change form with "Password changed successfully." (or similar). The hash text changed (new salt). |

### ADM-12 Staff user without superuser rights

**Purpose:** staff status alone shows nothing; the Admin Tools page is superuser-only; the
singletons stay superuser-only even with model permissions.
**Preconditions:** USERB. Session A and Session B.

| # | Action | Expected result |
|---|---|---|
| 1 | Session A: on USERB's change form tick "Is staff" (leave "Superuser status" unticked); Save. | Saved. |
| 2 | Session B: go to `/admin/login/`, log in as USERB. | Logged in to the admin. The dashboard lists no models (Django's "You don’t have permission to view or edit anything." would be expected; jazzmin shows an empty dashboard instead, so record its absence as a UX issue only) and has **no** "Admin Tools" box. The navbar has "Site" but **no** "Admin Tools" link. |
| 3 | Session B: go to `/admin/tools/`. | HTTP 403 page rendered inside the admin that says only "You do not have permission to access this page. Admin Tools are for superusers."; none of the tools (email form, environment table) is shown. |
| 4 | Session B: go to `/admin/billing/credittransaction/adjust/`. | 403 with "You do not have permission to adjust credits."; the form is shown but cannot apply anything. |
| 5 | Session B: go to `/admin/core/coresettings/`. | Refused (403 or a redirect to the dashboard); the settings form is not shown. |
| 6 | Session A: on USERB's change form, in "User permissions" add "Accounts \| user \| Can add user" and "Core \| Core settings \| Can change Core settings"; Save. | Saved. |
| 7 | Session B: reload `/admin/`. | "Users" now appears. "Core settings" still does **not** appear (singletons are superuser-only). |
| 8 | Session B: look at the navbar and the dashboard. | There is still **no** "Admin Tools" link or box (the link needs a permission that exists nowhere, so only a superuser has it; having "Can add user" does not show it). **FAIL** if either appears. |
| 9 | Session B: go to `/admin/core/coresettings/1/change/`. | Refused (403); the form is not shown. |
| 10 | Session B: log out (user menu → "Log out"). Session A: on USERB's form untick "Is staff", remove both permissions; Save. | USERB is an ordinary user again. |

### ADM-13 Deactivate and reactivate a user

**Purpose:** setting Status or "Is active" in the admin blocks login on the site.
**Preconditions:** USERB (not staff). Session A and Session B.

| # | Action | Expected result |
|---|---|---|
| 1 | Session A: USERB's change form, set Status to "Inactive"; Save. | Saved; the Users list shows Status "Inactive" for USERB. |
| 2 | Session B: `/account/login/`, log in as USERB. | Refused with "User not active". |
| 3 | Session A: set Status back to "Active" and untick "Is active"; Save. | Saved; "Is active" shows unticked in the list. |
| 4 | Session B: log in as USERB again. | Refused with "Invalid credentials" (an inactive account is refused before the status check). |
| 5 | Session A: filter the Users list "is active: No". | USERB is listed. |
| 6 | Session A: tick "Is active" again; Save. Session B: log in as USERB. | Login succeeds. Log out again in Session B. **Do not open the File transfer pages as USERB** (that would create rows that stop USERB being deleted in ADM-30; Receipts, Short URLs and To-do rows go with a deleted user). |

### ADM-14 Ledger and credit accounts are read-only

**Purpose:** credits can't be edited by hand.
**Preconditions:** Session A.

| # | Action | Expected result |
|---|---|---|
| 1 | Go to `/admin/billing/credittransaction/`. | Columns ID, User, Amount, Type, Source, Description, Created at; a date drill-down above the list; filters type (Purchase, Spend, Adjustment, Refund, Promo code, Welcome credits), source, created at; a search box. The seed rows (`e2e seed`, source `e2e`, 100 each) are listed. There is **no** "Add" button, and an "Adjust credits" button (coins icon) instead. No action menu with "Delete selected". |
| 2 | Click one seed row. | A read-only view (tab title starts "View credit transaction"): User, Amount ("Credits added (positive) or spent (negative)."), Type, Description, Source, ID, Created at. No Save and no Delete button. Note as a UX issue if the page heading is a raw string such as "<uuid> purchase 100 @ 2026-…". |
| 3 | Go to `/admin/billing/credittransaction/add/`, then `/admin/billing/credittransaction/1/delete/`. | Both answer 403 Forbidden. |
| 4 | Go to `/admin/billing/creditaccount/`. | Columns User, Balance, Updated at, with `qa.user@example.com` 100 (or whatever other tests left) and a search box; no "Add" button and no actions. |
| 5 | Open a credit account. | Read-only view of User, Balance, Updated at; no Save/Delete. `/admin/billing/creditaccount/add/` answers 403. |

### ADM-15 Adjust credits: add

**Purpose:** the adjustment tool adds credits, records an "Adjustment" ledger row and the user sees
the new balance.
**Preconditions:** SENDER and USERB (ADM-10), both with no credits yet. Session A and Session B.

| # | Action | Expected result |
|---|---|---|
| 1 | On the ledger list click "Adjust credits". | `/admin/billing/credittransaction/adjust/`: heading "Adjust credits", a box "Credits Adjustment" with "Manually adjust a user's credits. This will always create a ledger entry of type adjustment." Fields "User email:" (help "Email address of the user to adjust."), "Direction:" (Add / Spend, Add selected), "Amount:", "Description:" (help "Optional description for the ledger entry."), and an "Apply" button. |
| 2 | Enter SENDER's email, Direction "Add", Amount `50`, Description `ADM top-up <rand>`; click "Apply". | Message "Adjusted credits for qa.adm.<rand>@example.com. New balance: 50." The form is emptied. |
| 3 | Enter USERB's email, Add, `5`, leave Description empty; Apply. | "Adjusted credits for qa.adm.<rand>.b@example.com. New balance: 5." |
| 4 | Go to the ledger list and search `qa.adm.<rand>`. | Two new rows: SENDER +50 "ADM top-up <rand>" and USERB +5 "Admin adjustment" (the default description), both Type "Adjustment", Source empty. Filtering type "Adjustment" keeps them. |
| 5 | Go to `/admin/billing/creditaccount/` and search `qa.adm.<rand>`. | SENDER balance 50, USERB balance 5. |
| 6 | Session B: log in on the site as SENDER and open `/billing/credits/`. | "Current balance: 50 credits", and the history lists the +50 adjustment with its description. Stay logged in as SENDER in Session B. |

### ADM-16 Adjust credits: spend and refusals

**Purpose:** a spend adjustment can't take a balance below zero; bad input is refused.
**Preconditions:** SENDER has 50 credits (ADM-15). Session A on the adjust page.

| # | Action | Expected result |
|---|---|---|
| 1 | SENDER, Direction "Spend", Amount `60`; Apply. | Error "Insufficient credits for this spend adjustment." Balance unchanged (still 50 on the credit accounts list); no ledger row added. |
| 2 | SENDER, Spend, `10`, Description `ADM correction`; Apply. | "Adjusted credits for qa.adm.<rand>@example.com. New balance: 40." The ledger shows a row of **-10**, Type "Adjustment", "ADM correction". |
| 3 | SENDER, Add, Amount `0`; Apply. | "Please correct the errors below." and at Amount "Ensure this value is greater than or equal to 1." (the browser may block it first; note that). Nothing changes. |
| 4 | Email `nobody.<rand>@example.com`, Add, `5`; Apply. | Error "No user found with email: nobody.<rand>@example.com". |
| 5 | Email `qa.adm.<rand>@EXAMPLE.COM` (domain in capitals), Add, `1`; Apply. | Expected: the account is found (email domains are case-insensitive, and the rest of the site normalises them) and the balance becomes 41. Record **FAIL** if it answers "No user found with email: …"; then continue with SENDER at 40. |
| 6 | Email `not-an-email`; Apply. | "Enter a valid email address." (or the browser's own validation). |
| 7 | Session B (SENDER): reload `/billing/credits/`. | The balance matches the last success message (40, or 41 if step 5 passed), and the history lists the adjustments. |

### ADM-17 Core settings singleton

**Purpose:** the singleton page goes straight to its one row, enforces bounds and can't be deleted.
**Preconditions:** Session A.

| # | Action | Expected result |
|---|---|---|
| 1 | Go to `/admin/core/coresettings/`. | Redirected to `/admin/core/coresettings/1/change/`, a form "Core settings" with "Verification code length" (6), "Verification validity (minutes)" (30), "Verification max attempts" (5), "Verification resend cooldown (seconds)" (60), each with its help text. Buttons "Save" and "Save and continue editing"; **no** "Delete" button and no "Save and add another". |
| 2 | Set code length `5`; Save. | "Please correct the errors below." and "Ensure this value is greater than or equal to 6." |
| 3 | Set code length `11`, validity `0`, max attempts `0`; Save. | Errors "Ensure this value is less than or equal to 10." (code length), "Ensure this value is greater than or equal to 1." (validity and max attempts). Nothing saved. |
| 4 | Set max attempts `11`; Save. | "Ensure this value is less than or equal to 10." |
| 5 | Set code length `8`, validity `30`, max attempts `5`, cooldown `60`; "Save and continue editing". | "The Core settings “Core settings” was changed successfully. You may edit it again below."; code length shows 8. |
| 6 | Set code length back to `6`; Save. | Saved; you land on the dashboard or the same form (either is fine) with a success message. Reopen: 6. |
| 7 | Go to `/admin/core/coresettings/1/delete/` and `/admin/core/coresettings/add/`. | Both answer 403 Forbidden. |

### ADM-18 Billing settings singleton

**Purpose:** the shared storage price is editable and can't be negative.
**Preconditions:** Session A.

| # | Action | Expected result |
|---|---|---|
| 1 | Click "Billing settings" in the sidebar. | Redirected to `/admin/billing/billingsettings/1/change/` with "Storage price (credits per GB per day)" = `0.1000` (an input styled like the others, `min` 0) and its help text naming file transfer and receipts, and "Welcome credits" = `0` (you switched it off in the preconditions; the default is 1000) with the help text "Credits added to a genuinely new account once its email is confirmed. 0 turns it off: accounts created while it is off never get them." No Delete button. |
| 2 | Enter `-1`; Save. | "Ensure this value is greater than or equal to 0." |
| 3 | Enter `abc`; Save. | "Enter a number." (or the browser blocks it). |
| 4 | Enter `0.25`; "Save and continue editing". | Saved with the success message; the field shows `0.2500`. |
| 5 | Enter `0.1000`; Save. | Saved with the message "Billing settings saved." (not "The Billing settings “Billing settings” was changed successfully.") and you stay on the settings form; reopening shows `0.1000` again. |
| 5b | Set Welcome credits to `1000001`; Save. | "Ensure this value is less than or equal to 1000000." (the input also has `max="1000000"`). Set it back to `1000` and Save: this **restores** the setting switched off in the preconditions, and it must stay at 1000 for the other files. |
| 6 | `/admin/billing/billingsettings/1/delete/`. | 403 Forbidden. |

### ADM-19 File transfer settings singleton

**Purpose:** the file transfer settings page loads, saves and validates.
**Preconditions:** Session A.

| # | Action | Expected result |
|---|---|---|
| 1 | Go to `/admin/file_transfer/filetransfersettings/`. | Redirected to `.../filetransfersettings/1/change/`. Fields include "Max file size (bytes)" (5368709120), Logged in max files (100), "Max total size (bytes)", Logged in max recipients (20), "Anonymous enabled" (unticked), the anonymous limits, "Anonymous allowed expiry days" (`1,5,15,30`), "Suspension grace days" (7), "Auto hold report threshold" (0, help text explains 0 turns it off). No storage price field (it is on Billing settings) and no Delete button. |
| 2 | Set "Logged in max recipients" to `-1`; Save. | An error such as "Ensure this value is greater than or equal to 0." Nothing saved. |
| 3 | Set "Anonymous allowed expiry days" to `1,abc`; Save. | Expected: refused with an error at that field. If it is **saved**, record FAIL (an unparseable value is accepted) and immediately set it back to `1,5,15,30` and save. |
| 4 | Make sure every field has its original value (step 1) and click Save. | Saved with "The File transfer settings “File transfer settings” was changed successfully." |

### ADM-20 Receipts settings singleton

**Purpose:** the Receipts settings page behaves like the other singletons.
**Preconditions:** Session A.

| # | Action | Expected result |
|---|---|---|
| 1 | Go to `/admin/receipts/receiptssettings/`. | Redirected to `.../receiptssettings/1/change/` with "Price per file (credits)" (1), "Max file size (bytes)" (15728640), "Allowed types" (`application/pdf,image/jpeg,image/png,image/webp,image/heic`), "Min inline image px" (150), "Max files per user per day" (100), "Max inbound rows per day" (500), "Trash days" (30), "Max pdf pages to ai" (5), "Max ai attempts" (3), each with help text. No Delete button. |
| 2 | Go back to `/admin/`. | "Receipts settings" offers only "Change" (it never offers "Add"). |
| 3 | Set "Max pdf pages to ai" to `21`, "Max file size (bytes)" to `0`, "Max ai attempts" to `0`; Save. | Errors "Ensure this value is less than or equal to 20.", and "Ensure this value is greater than or equal to 1." for the other two. Nothing saved. |
| 4 | Restore 5 / 15728640 / 3, set "Trash days" to `31`; "Save and continue editing". | Saved with the success message; Trash days shows 31. |
| 5 | Set "Trash days" back to `30`; Save. | Saved. |
| 6 | Go to `/admin/receipts/receiptssettings/add/` and `.../1/delete/`. | Both 403 Forbidden. |

### ADM-21 Moderation set-up: SENDER sends two transfers

**Purpose:** create the transfers the moderation cases act on. (`file_transfer.md` covers the send
flow in depth; here it is only set-up.)
**Preconditions:** Session B logged in as SENDER (ADM-15) with at least 40 credits; File transfer
settings' auto-hold threshold is 0 (ADM-19).

| # | Action | Expected result |
|---|---|---|
| 1 | Session B: go to `/transfer/send/`. | "Send files" page. |
| 2 | "Choose files", pick the local text file renamed (or saved) as `adm-takedown-<rand>.txt`; wait until it shows as uploaded. Enter `qa.adm.recipient@example.com` in Recipients; click "Send". | "Your transfer is on its way" with the download link `http://localhost:8765/t/<slug>/`. Write down this link as **T1's link**. |
| 3 | Repeat steps 1-2 with a file named `adm-hold-<rand>.txt`. | Note **T2's link**. |
| 4 | Open `/transfer/`. | Both transfers listed, status ACTIVE. |
| 5 | Open T1's link and T2's link. | Each shows its download page with the file and a "Report this transfer" link. |

### ADM-22 Abuse report list and Dismiss

**Purpose:** a report from the download page shows up in the admin and can be dismissed.
**Preconditions:** T1 (ADM-21). Session B and Session A.

| # | Action | Expected result |
|---|---|---|
| 1 | Session B: on T1's download page click "Report this transfer", choose Reason "Phishing or spam", Details `ADM report <rand>`, "Your email (optional)" `qa.adm.reporter@example.com`; click "Submit report". | "Thank you, your report has been submitted." |
| 2 | Session A: go to `/admin/file_transfer/abusereport/`. | The report is listed: Transfer (T1's id, as a link), Reason "Phishing or spam", Status "Pending", Reporter email `qa.adm.reporter@example.com`, Reporter ip `127.0.0.1` (or `::1`), Created at. No "Add" button. Filters status / reason / created at, a date drill-down. |
| 3 | Open the report. | Read-only view (title starts "View abuse report") with all fields, including Details `ADM report <rand>`; no Save or Delete. |
| 4 | Back on the list, click the Transfer link. | Opens T1's read-only transfer view. Go back. |
| 5 | Open the action menu. | "Dismiss selected report(s)", "Take down the reported transfer(s)", "Block the reported transfer(s)'s sender(s)" (and no "Delete selected"). |
| 6 | Click "Go" with an action chosen but no row ticked. | Django's "Items must be selected in order to perform actions on them. No items have been changed." |
| 7 | Tick the report, choose "Dismiss selected report(s)", click "Go". | "Dismissed 1 report(s)." Status is now "Dismissed"; the view shows Reviewed by `qa.admin@example.com`, a Reviewed at time and Resolution note "Dismissed via admin.". |
| 8 | Tick it again and dismiss again. | "Dismissed 0 report(s)." (already dismissed; nothing changes). Note as a UX issue that this is shown as a success. |
| 9 | Filter status "Dismissed", then "Pending". | It shows under Dismissed only. |
| 10 | Session B: open T1's link. | Still available (dismissing changes nothing for the transfer). |

### ADM-23 Transfers admin: read-only view and Take down

**Purpose:** transfers can't be edited, and the takedown action asks for a reason, ends the
transfer and tells the sender only what it should.
**Preconditions:** T1 active (ADM-22). Session A and Session B.

| # | Action | Expected result |
|---|---|---|
| 1 | Session A: go to `/admin/file_transfer/transfer/`. | Columns Id, Owner, Status, Held for review at, Size bytes, Credits charged, Created at, Expires at; filters status (Draft … Taken down) and created at; no "Add" button. |
| 2 | Search T1's slug (the last part of T1's link). | Only T1 (owner SENDER, Status "Active"). |
| 3 | Open T1. | Read-only view titled "Transfer <id> (active)" with all fields and two inlines, "Transfer files" (`adm-takedown-<rand>.txt`, uploaded) and "Transfer recipients" (`qa.adm.recipient@example.com`); no Save/Delete buttons. `/admin/file_transfer/transfer/add/` answers 403. |
| 4 | Back on the list, tick T1, choose "Take down selected transfer(s)", click "Go". | A confirmation page "Take down transfer(s)" explaining files will be deleted immediately and the link will stop working, listing "<T1 id> -- adm-takedown-<rand>.txt (Active)". Fields: "Reason:" (help text says it is always shown to the sender and never reveals the reporter), "Internal note (staff only):" ("Never shown to the sender, on the dashboard or in any email."), a checkbox "Notify sender by email ("your transfer was removed")" **unticked** by default, and a "Take down" button. |
| 5 | Leave Reason empty and click "Take down". | The same page with "This field is required." at Reason; T1 is not taken down. |
| 6 | Reason `Spam content (ADM <rand>)`, Internal note `ADM internal <rand>`, tick "Notify sender", click "Take down". | Back on the transfer list with "Took down 1 transfer(s)." T1's Status is "Taken down". |
| 7 | Open T1. | Taken down by `qa.admin@example.com`, Taken down at set, Takedown reason `Spam content (ADM <rand>)`, Takedown internal note `ADM internal <rand>`, Takedown notify ticked, Files deleted at set. |
| 8 | Open `/__e2e__/mail/?to=qa.adm.<rand>@example.com`. | An email "Your transfer "adm-takedown-<rand>.txt" was removed" whose text says it "was removed and its files are no longer available." and "Reason: Spam content (ADM <rand>)". It does **not** contain `ADM internal <rand>` or the reporter's address. |
| 9 | Session B: open T1's link. | "This transfer is no longer available" (the same neutral page as any unavailable transfer; no mention of a takedown or the reason). |
| 10 | Session B: open `/transfer/`. | T1 shows status TAKEN DOWN and "This transfer was taken down: Spam content (ADM <rand>)". The internal note appears nowhere. |
| 11 | Session A: the abuse report list. | T1's report stays "Dismissed" (it was already reviewed); no new report rows. |
| 12 | Session A: open T1 and click **History** (top right of the change view). | One entry by `qa.admin@example.com` reading "Taken down: Spam content (ADM <rand>)": the reason the sender sees, never the internal note. (Bulk actions and two-step pages leave an entry per object like this; Django alone would log only change-form saves.) |

### ADM-24 Auto-hold and "Release abuse-review hold"

**Purpose:** with a threshold set, a report holds a transfer, and staff can release the hold.
**Preconditions:** T2 active (ADM-21). Session A and Session B.

| # | Action | Expected result |
|---|---|---|
| 1 | Session A: File transfer settings, set "Auto hold report threshold" to `1`; Save. | Saved. |
| 2 | Session B: on T2's download page "Report this transfer", Reason "Malware", Details `ADM hold <rand>`; "Submit report". | "Thank you, your report has been submitted." |
| 3 | Session B: reload T2's link. | "This transfer is no longer available". |
| 4 | Session B: `/transfer/`. | T2 still shows ACTIVE with "This transfer is on hold pending review and is temporarily unavailable."; its actions except disabling are not offered. |
| 5 | Session A: transfers list. | T2 has a "Held for review at" time; status still "Active". |
| 6 | Tick T2, choose "Release abuse-review hold (dismisses its pending reports)", "Go". | "Released the hold on 1 transfer(s)." (no confirmation page). "Held for review at" is empty again, and T2's **History** has an entry "Released the abuse-review hold." |
| 7 | Abuse report list. | T2's report is "Dismissed", Reviewed by `qa.admin@example.com`, Resolution note "Hold released via admin.". |
| 8 | Session B: reload T2's link. | The download page works again. |
| 9 | Session A: run "Release abuse-review hold" on T2 again. | "Released the hold on 0 transfer(s)." Nothing changes. |
| 10 | Session A: set "Auto hold report threshold" back to `0`; Save. | Saved. |

### ADM-25 "Block sender" action on a transfer

**Purpose:** blocking a transfer's sender stops new sends but leaves existing transfers running.
**Preconditions:** T2 active and not held (ADM-24). Session A and Session B (SENDER).

| # | Action | Expected result |
|---|---|---|
| 1 | Session A: transfers list, tick T2, choose "Block sender of selected transfer(s)", "Go". | A page "Block sender(s)": "Block the sender of the following transfer(s) from sending any further transfers." listing "<T2 id> -- adm-hold-<rand>.txt (qa.adm.<rand>@example.com)". Checkboxes "Block sender email" (**ticked**), "Block sender IP" (**unticked**, help text about shared/CGNAT IPs), a "Reason:" box, "Also take down this sender's other active transfers" (unticked), "Also deactivate the sender's account (owned transfers only, no effect on anonymous ones)" (unticked), and a "Block sender" button. |
| 2 | Leave the defaults, Reason `ADM block <rand>`, click "Block sender". | Back on the list with "Added 1 block-list entry/entries." |
| 3 | Go to `/admin/file_transfer/blockedsender/`. | A row Kind "Email", Value `qa.adm.<rand>@example.com`, Reason `ADM block <rand>`, Created by `qa.admin@example.com`, Expires at empty. No IP row. |
| 4 | Session B (SENDER): go to `/transfer/send/`. | HTTP 403 page "Sending isn't available" with "Sorry, you can't send transfers at this time." (no reason or mention of a block list). |
| 5 | Session B: open T2's link. | Still available: existing transfers keep running. |
| 6 | Session B: `/account/` (or any site page). | SENDER is still logged in (the account was not deactivated). |
| 7 | Session A: run "Block sender of selected transfer(s)" on T2 again with the defaults. | "Added 0 block-list entry/entries." (already blocked); still exactly one row for SENDER in the block list. |

### ADM-26 Block list CRUD and validation

**Purpose:** the block list form validates values and refuses an active duplicate.
**Preconditions:** SENDER's email block from ADM-25. Session A.

| # | Action | Expected result |
|---|---|---|
| 1 | Block list → "Add blocked sender". | Fields Kind (Email / IP / CIDR), Value (help text about exact emails, `*@domain` wildcards and IPs/CIDRs), Reason, Expires at (date and time, "Blank means this block never expires."); Created by and Created at are read-only/empty. |
| 2 | Kind "Email", Value `example.com`; Save. | "Enter a valid email address, or a "*@domain" wildcard." |
| 3 | Kind "IP / CIDR", Value `999.1.1.1`; Save. | "Enter a valid IP address or CIDR range." |
| 4 | Kind "Email", Value `QA.ADM.<rand>@EXAMPLE.COM` (SENDER in capitals); Save. | "This value is already actively blocked -- edit that entry instead of adding a duplicate." |
| 5 | Kind "Email", Value `*@blocked-domain-<rand>.test`, Reason `ADM wildcard`; Save. | Saved; listed with Created by `qa.admin@example.com`. |
| 6 | Add Kind "IP / CIDR", Value `203.0.113.0/24`, Reason `ADM range`; Save. | Saved. |
| 7 | Filter Kind "IP / CIDR"; search `blocked-domain`. | The filter shows only the range; the search shows only the wildcard. |
| 8 | Open the `203.0.113.0/24` row, set Expires at to yesterday; Save. Then add another IP / CIDR entry `203.0.113.0/24`. | Both saves succeed: an expired entry does not count as a duplicate. |
| 9 | Delete the two `203.0.113.0/24` rows and the wildcard row (open each → "Delete" → confirm). | Each delete shows a success message; the rows are gone. |
| 10 | Delete SENDER's email block the same way. | Gone; the block list has none of this run's rows left. |
| 11 | Session B (SENDER): reload `/transfer/send/`. | The "Send files" page is back (no 403). |

### ADM-27 Feedback triage

**Purpose:** feedback from the site is listed and only its status can change.
**Preconditions:** Session B logged in as SENDER. Session A.

| # | Action | Expected result |
|---|---|---|
| 1 | Session B: open `/feedback/` (footer "Feedback"), type `ADM feedback <rand>: line one` then a new line `line two <b>bold?</b>` in "Your feedback"; click "Send feedback". | The thanks page. |
| 2 | Session A: go to `/admin/feedback/feedback/`. | A row with Created at, Submitted by `qa.adm.<rand>@example.com`, Status "New", Feedback preview `ADM feedback <rand>: line one line two <b>bold?</b>` (on one line, the tag shown as text, not rendered bold). No "Add" button. Filters status (New / In process / Closed) and created at; date drill-down. |
| 3 | Search `ADM feedback <rand>`, then search SENDER's email. | Both find the row. |
| 4 | Open it. | Id, Created at, Updated at, Created by, Feedback (read-only, keeping the line break, `<b>bold?</b>` shown literally), and an editable Status select. `/admin/feedback/feedback/add/` answers 403. |
| 5 | Set Status "In process"; Save. | Saved; the list shows "In process". |
| 6 | Tick the row, action "Mark selected as Closed", "Go". | "Marked 1 feedback as Closed."; status "Closed"; Updated at has moved forward. |
| 7 | Action "Mark selected as New" on it. | "Marked 1 feedback as New." |
| 8 | Open the action menu. | Also offers "Mark selected as In process" and Django's "Delete selected feedback". Do not delete. |

### ADM-28 Receipts admins are read-only

**Purpose:** support can look at receipts, vendors, accounts and inbound mail, but not edit them.
**Preconditions:** Session B logged in as SENDER (has credits). Session A.

| # | Action | Expected result |
|---|---|---|
| 1 | Session B: open `/receipts/upload/`, upload the small JPEG/PNG/PDF, wait until it is processed. | The receipt reads vendor Staples, 42.10 (the fake AI). |
| 2 | Session A: go to `/admin/receipts/receipt/`. | The receipt is listed: User SENDER, Vendor "Staples", Vendor extracted "Staples", Amount 42.10, Currency USD, Receipt date "Oct. 9, 2026", Status "Processed", Source "Upload". Filters status, failure reason, source, created at. No "Add" button and no "Delete selected" action. |
| 3 | Search SENDER's email; open the receipt. | Read-only view of all its fields; no Save/Delete. `/admin/receipts/receipt/add/` answers 403. |
| 4 | Go to `/admin/receipts/vendor/`, search SENDER's email. | Vendor "Staples", user SENDER, Vetted shown as an icon (not vetted), Receipts 1. Open it: read-only, with a "Vendor aliases" inline (name, key, source). |
| 5 | Go to `/admin/receipts/receiptsaccount/`, search SENDER's email. | SENDER's account with Stored bytes > 0 and Frozen at empty. Open it: read-only, with an "Inbox codes" inline showing one active 8-character code. |
| 6 | Go to `/admin/receipts/inboundemail/`. | Loads (probably empty: no inbound email on this server); no "Add" button. |
| 7 | Go to `/admin/receipts/inboundmessage/`. | Loads with filters status (Pending / Done / Failed) and created at; no "Add" button. If rows exist, the action menu offers "Retry selected failed messages"; running it on a non-failed row says "Reset 0 failed message(s) to pending.". If the list is empty (no action menu is shown), record that check as SKIPPED. |

### ADM-29 Other read-only admins

**Purpose:** the support-only lists load and offer no editing.
**Preconditions:** Session A.

| # | Action | Expected result |
|---|---|---|
| 1 | Go to `/admin/core/scheduledjob/`. | (On every list in this case: Django renders no column headers for an empty list, so check the columns only where rows exist.) Loads with columns Name, Interval seconds, Last started at, Last finished at, Last success at, Run count, Locked until (the list may be empty, "0 Scheduled jobs", since this server runs no scheduler). No "Add" button, no actions. `/admin/core/scheduledjob/add/` answers 403. |
| 2 | Go to `/admin/core/emailverification/`. | Rows for confirmations sent during this run may be listed (Email, Purpose, Attempts, Confirmed at, Expires at, Created at); filter by purpose. Opening one shows no code or token hash and no Save/Delete. |
| 3 | Go to `/admin/core/ratelimitcounter/`. | Loads (likely empty: rate limiting is off); no "Add" button. |
| 4 | Go to `/admin/file_transfer/downloadevent/`. | Loads; if rows exist (other tests' downloads), each opens read-only. No "Add" button. |
| 5 | Go to `/admin/qr_code/qrcode/`. | Loads with columns Id, Name, Content, Qr format, Created by, Scan count, Created at, Deleted at, and an "Add" button (QR codes are editable here). Do not add or change anything. |

### ADM-30 Delete a user who has credit history

**Purpose:** a superuser can delete an account even though its ledger rows are read-only.
**Preconditions:** USERB (has a +5 adjustment, never opened File transfer). Session A.

| # | Action | Expected result |
|---|---|---|
| 1 | Open USERB's change form and click "Delete". | A confirmation page asking whether to delete the user `qa.adm.<rand>.b@example.com`, listing the related objects that go with it (its credit account and the credit transaction). It does **not** say your account lacks permission to delete any of them. |
| 2 | Confirm ("Yes, I’m sure"). | Back on the Users list with "The user “qa.adm.<rand>.b@example.com” was deleted successfully." USERB is no longer listed. |
| 3 | Search the ledger for `qa.adm.<rand>.b`. | No rows (deleted with the user). The credit accounts list has no USERB row either. |
| 4 | Open SENDER's change form, click "Delete" and read the page, then leave it through the breadcrumbs (Django shows no "No, take me back" link on this page). | SENDER has sent transfers (ADM-21), so the page says the user can't be deleted because your account doesn't have permission to delete related objects (the transfers and their files and recipients; file transfer's admins stay read-only), and offers no confirm button. Nothing is deleted. It must **not** name "vendor", "receipt" or "Receipts account": Receipts, Short URLs and To-do rows are deleted with their user. |

### ADM-31 Recent actions and logging out

**Purpose:** the dashboard records changes made in the admin, and logging out ends the admin
session.
**Preconditions:** Session A.

| # | Action | Expected result |
|---|---|---|
| 1 | Go to `/admin/`. | "Recent actions" lists this run's changes (e.g. the users added and changed, the deleted USERB, the settings changes, the block list entries), newest first, with links for objects that still exist. |
| 2 | Open the user icon menu and click "Log out". | A "Logged out" page (jazzmin styling) with a link to log in again. |
| 3 | Go to `/admin/`. | Redirected to `/admin/login/?next=/admin/`. |
| 4 | Go to `/` (the site). | Logged out on the site too (the admin and the site share the session). |
| 5 | Session B: log out of the site. | Logged out. |

### ADM-32 Sections added since the first plan: notifications, promo codes, welcome credits, Short URLs, To-do

**Purpose:** the newer admin sections load, are read-only where they should be and show readable names.
**Preconditions:** Session A as `qa.admin`. A new account that confirmed its email (BILL-12 in `billing.md`) makes
the welcome credit rows meaningful; without it some lists are empty.

| # | Action | Expected result |
|---|---|---|
| 1 | `/admin/core/notification/`. | Read-only list (filters by source, read state, dismissed, date; search by the user's email or name and by title); no "Add" button; opening a row shows its fields without Save. |
| 2 | `/admin/billing/welcomecredit/`. | Read-only list: **User** (the email, not an id), status, reason, credits, created, decided; filters status and reason. Opening a granted row shows "Ledger line" as a link reading like `+1000 Welcome credits, 2026-11-01 12:00 UTC` to the ledger row, not a raw id. |
| 3 | `/admin/billing/promocode/`, then **Add promo code**. | A list with usage columns and a state filter (live, scheduled, expired, fully redeemed, inactive); the add form leaves **Code** blank to generate one. A **Create many** tool exists at `.../promocode/bulk/`. Do not create codes unless a case needs one. |
| 4 | `/admin/short_url/shortlink/`, `/admin/short_url/linkreport/`, `/admin/short_url/blockeddomain/`. | Short links and link reports are read-only lists (no "Add"); the report list's first column is the reported link's code and the report opens from its **Reason**; blocked domains are plain CRUD. Their takedown / block / deactivate actions show the card-style confirmation pages described in SU-19 and SU-20. |
| 5 | `/admin/short_url/shorturlsettings/`, `/admin/todo/todosettings/`. | Straight to the one form (no list, no Add, no Delete). Short URL settings has an **Allowances (per user)** tab. |
| 6 | `/admin/todo/task/`. | Read-only task list (search by title, number or the user's email; filters priority, source, completed, trash, unpaid). The **Unpaid** column shows "Unpaid" only for an owed task (nothing, not a red cross, for the normal state). No Add. |
| 7 | `/admin/todo/reminderdelivery/`. | The list and the dashboard call it "Reminder deliveries" (plural), read-only. |
| 8 | On a billing or Short URL settings form, save without changing anything. | Billing settings says "Billing settings saved."; the others use Django's "... was changed successfully." wording. |
