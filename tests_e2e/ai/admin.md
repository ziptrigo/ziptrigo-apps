# Admin

## Scope

The custom Django admin site at `/admin/` (`apps/core/admin_site.py`, themed with django-jazzmin):

- access control (logged out, an ordinary user, a staff user without permissions, a staff user
  with specific permissions -- `file_transfer.takedown_transfer`, `file_transfer.add_blockedsender`,
  `short_url.takedown_link`, `short_url.add_blockeddomain`, `accounts.change_user`,
  `billing.add_credittransaction` --, a superuser made in the admin) and the admin login page;
- the dashboard, the jazzmin top menu ("Site", "Admin Tools"), the user menu and the light/dark
  theme toggle (`core/js/admin_theme_toggle.js`);
- the Admin Tools page (`/admin/tools/`): test email, masked environment variables;
- users (`/admin/accounts/user/`): list, search, filters, the email-based create form and its
  case-insensitive uniqueness check, the change form (time zone, read-only theme and social ids),
  setting a password, staff and superuser rights, deactivating, confirming an email by ticking the
  box (which grants the welcome credits, refuses them for a `+tag` variant and leaves a staff
  account's pending), deleting a user with credit history, and deleting one with Receipts, Short
  URLs and To-do data (what goes with it, and what is remembered: retired short codes and the
  address for the welcome credits);
- billing: the read-only ledger and credit accounts, the credit adjustment tool
  (`/admin/billing/credittransaction/adjust/`), welcome credits, promo codes (add with a generated
  code, validation, read-only fields once claimed, delete only while unredeemed, Duplicate, Export
  CSV, Create many with a prefix and the campaign CSV, the state filter, the redemptions inline and
  list);
- the six singleton settings pages (Core, Billing, File transfer, Receipts, Short URL, To-do) with
  their validation;
- file transfer moderation: transfers (read-only plus the take down / release hold / block sender
  actions), abuse reports (dismiss, take down, block sender with "also take down" and "also
  deactivate"), the block list (CRUD with validation);
- Short URLs moderation: link reports (dismiss, which releases a hold), auto-hold and "Release
  abuse-review hold", take down with the owner's email, "Block the destination domain",
  "Deactivate the owner's account", the blocked domains list;
- the History entries the admin actions write, feedback triage, notifications, scheduled jobs
  after a scheduler run, the read-only Receipts and To-do admins with their filters and search,
  both apps' inbound message "retry" action, and the other read-only admins (email verifications,
  rate limit counters, download events, daily link stats);
- dark mode on the admin's own custom pages.

Not covered here: the site pages themselves (see the other files; this file only uses them to
create data and to check the effect of an admin action).

## Preconditions

- The test server is running at `http://localhost:8765` (see [README](README.md)), ideally freshly
  restarted. Rate limiting is off. Email is really sent (SMTP) and read back: open
  `/__e2e__/mail/?to=<address>` and **reload until the email arrives (up to a minute)**; links in
  it are absolute (`http://localhost:8765/...`) and clickable.
- **Shell steps.** A few cases (ADM-46, ADM-47, ADM-49) run a command next to the server with
  `python tests_e2e/ai/manage.py [--port <port>] <manage.py arguments>` from the repo root, which
  runs `manage.py` with the running server's exact environment (same database, bucket and mail).
  Write any `.eml` file they need to a temporary folder of your own and pass its absolute path.
- **Two browser sessions.** The admin and the site share one login cookie, so logging in on the
  site logs the admin out. Use:
  - **Session A**: the main window, logged in to the admin as `qa.admin@example.com` from ADM-04 on;
  - **Session B**: a second, isolated session (a private/incognito window or a separate browser
    profile/context) for everything done as another user.

  If your browser tool has only one session, log out and back in instead each time the session
  changes, and note it in the results.

  A second session can also be another host name of the same server (`<name>2.localhost`, see
  the README): cookies are per host name.
- A small local text file to upload (any content), and a small JPEG, PNG or PDF for the Receipts
  uploads in ADM-28 and ADM-45.
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
| USERC | `qa.adm.<rand>.c@example.com`, name `ADM User C`, created in ADM-33 with the welcome credits on (deleted in ADM-50, registered again there) |
| USERC2 | `qa.adm.<rand>.c+tag@example.com`, name `ADM User C tag`, created in ADM-33 (a `+tag` variant of USERC) |
| STAFF | `qa.adm.<rand>.staff@example.com`, name `ADM Staff`, created in ADM-33, staff from then on |
| SUOWNER | `qa.adm.<rand>.su@example.com`, name `ADM Link Owner`, created in ADM-39; owns the short links L1-L5 |
| Transfers T3, T4 | by SENDER, files `adm-report-<rand>.txt` and `adm-block-<rand>.txt` (ADM-38) |

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
| 2 | Replace the recipient with `qa.adm.tools.<rand>@example.com` and click "Send test email". | A success message "Test email sent to qa.adm.tools.<rand>@example.com via N backend(s) (0 failure(s))." with N of 1 or more (the number of configured email backends). The message appears **once** (record FAIL if it is shown twice). The field keeps the address. An error "All email backends failed (…)" is a FAIL (the SMTP delivery is broken). |
| 3 | In a new tab open `/__e2e__/mail/?to=qa.adm.tools.<rand>@example.com` (reload until it arrives, up to a minute). | Exactly one email, subject "Test email from the admin panel", text "This is a test email." / "Sent from the Django admin panel." and a "Sent:" line with the current time, `YYYY-MM-DD HH:MM:SS UTC`. It contains no link. |
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
| 4 | Find `SECRET_KEY`, `JWT_SECRET`, `FILE_TRANSFER_AWS_SECRET_ACCESS_KEY`, `FILE_TRANSFER_AWS_ACCESS_KEY_ID` and any `OPENAI_API_KEY` / `*_PASSWORD` / `*_TOKEN` / other `AWS_*` rows that are set. | Each value is masked with a fixed `********`: a value of 16 characters or more shows only its **last 4** characters after the asterisks (`********ab12`), a shorter one is `********` alone. Nothing of the start of a secret is visible. |
| 5 | Find `ENVIRONMENT`, `BASE_URL`, `AWS_REGION`, `AWS_SES_SENDER`, `DJANGO_SETTINGS_MODULE`, `E2E_S3_ENDPOINT_URL` and a duration such as `PASSWORD_RESET_TOKEN_TTL_HOURS` (if set). | Shown in clear: `local`, `http://localhost:8765`, a region, a sender address, `tests_e2e.receipts_e2e_settings`, `http://localhost:4566`, a number (names ending in `_HOURS`, `_SECONDS`, `_DAYS`, `_TTL` are not treated as secrets). |
| 5b | Find `E2E_DATABASE_URL` (the test server's Postgres URL) and any `DATABASE_URL` / `CACHE_URL`. | The URL is shown with its password replaced: `postgres://postgres:********@127.0.0.1:5432/ziptrigo_e2e_ai…` (user name, host, port and database visible; the password never). A URL without a password is shown unchanged. |
| 6 | Scan the whole table for credentials shown in clear: a URL with `user:password@` in it, an access key id, anything that looks like a key or password. | None is shown in clear. **FAIL** for each key whose value exposes a credential; in the report name the key only, never copy the value. |
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
| 1 | Open USERB's change form. | Sections "Authentication" (Email, Password, Google sub, Microsoft id), "Personal info" (Name, Timezone, Theme), "Status" (Status, Email confirmed, Email confirmed at), "Permissions", "Important dates". Password is read-only text such as "algorithm: pbkdf2_sha256 iterations: … salt: abc****** hash: def******", followed by a "Reset password" link and "Raw passwords are not stored, so there is no way to see the user’s password." There is no password input. "Google sub", "Microsoft id", "Theme" ("System"), "Email confirmed at", "Created at", "Updated at" are read-only; "Timezone" is an editable text box (empty). |
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
| 5 | Email `QA.ADM.<rand>@EXAMPLE.COM` (all in capitals), Add, `1`; Apply. | The account is found (the lookup ignores letter case, like signup): "Adjusted credits for qa.adm.<rand>@example.com. New balance: 41." (the stored address, in lower case). Record **FAIL** if it answers "No user found with email: …"; then continue with SENDER at 40. |
| 6 | Email `not-an-email`; Apply. | "Enter a valid email address." (or the browser's own validation). |
| 7 | Session B (SENDER): reload `/billing/credits/`. | The balance matches the last success message (41, or 40 if step 5 failed), and the history lists the adjustments. |

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
| 3 | Set "Anonymous allowed expiry days" to `1,abc`; Save. Then `0,5`; Save. | Both refused with "Enter whole numbers of days of at least 1, separated by commas (e.g. 1,5,15,30)." at that field; nothing saved. If either is **saved**, record FAIL and immediately set it back to `1,5,15,30` and save. |
| 4 | Make sure every field has its original value (step 1) and click Save. | Saved with "The File transfer settings “File transfer settings” was changed successfully." |

### ADM-20 Receipts settings singleton

**Purpose:** the Receipts settings page behaves like the other singletons.
**Preconditions:** Session A.

| # | Action | Expected result |
|---|---|---|
| 1 | Go to `/admin/receipts/receiptssettings/`. | Redirected to `.../receiptssettings/1/change/` with "Price per file (credits)" (1), "Max file size (bytes)" (15728640), "Allowed types" (`application/pdf,image/jpeg,image/png,image/webp,image/heic`), "Min inline image px" (150), "Max files per user per day" (100), "Max inbound rows per day" (500), "Trash days" (30), "Max pdf pages to ai" (5), "Max ai attempts" (3), each with help text. No Delete button. |
| 2 | Go back to `/admin/`. | "Receipts settings" offers only "Change" (it never offers "Add"). |
| 3 | Set "Max pdf pages to ai" to `21`, "Max file size (bytes)" to `0`, "Max ai attempts" to `0`; Save. | Errors "Ensure this value is less than or equal to 20.", and "Ensure this value is greater than or equal to 1." for the other two. Nothing saved. |
| 3b | Restore 5 / 15728640 / 3; set "Allowed types" to `application/pdf,image/jpg`; Save. | Refused at Allowed types: "Unsupported type(s): image/jpg. Use a comma-separated list of: application/pdf, image/heic, image/jpeg, image/png, image/webp." Then empty the field and Save: refused too (the list may not be empty). Put back `application/pdf,image/jpeg,image/png,image/webp,image/heic`. |
| 4 | Set "Trash days" to `31`; "Save and continue editing". | Saved with the success message; Trash days shows 31. |
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
| 8 | Open `/__e2e__/mail/?to=qa.adm.<rand>@example.com` (reload until it arrives, up to a minute). | An email "Your transfer "adm-takedown-<rand>.txt" was removed" whose text says it "was removed and its files are no longer available." and "Reason: Spam content (ADM <rand>)". It has no link. It does **not** contain `ADM internal <rand>` or the reporter's address. |
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
| 9 | Run "Mark selected as New" on the row again. | "Marked 0 feedback as New." (it already was; nothing changes, Updated at stays). |
| 10 | Open the row and click **History**. | Newest first: "Marked as New.", "Marked as Closed." (by `qa.admin@example.com`, from the bulk actions) and a "Changed Status." entry from the form save in step 5. Step 9 left no entry. |

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
| 7 | Go to `/admin/receipts/inboundmessage/`. | Loads with filters status (Pending / Done / Failed) and created at; no "Add" button. Probably empty here (no inbound email yet); ADM-46 fills it and runs the "retry" action. |

### ADM-29 Other read-only admins

**Purpose:** the support-only lists load and offer no editing.
**Preconditions:** Session A.

| # | Action | Expected result |
|---|---|---|
| 1 | Go to `/admin/core/scheduledjob/`. | (On every list in this case: Django renders no column headers for an empty list, so check the columns only where rows exist.) Loads; probably empty ("0 Scheduled jobs") since this server runs no scheduler (ADM-49 runs one and checks the rows). No "Add" button, no actions. `/admin/core/scheduledjob/add/` answers 403. |
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
**Preconditions:** ADM-31 logged Session A out: log in again at `/admin/login/` as `qa.admin`. ADM-33 creates
the welcome credit rows; before it some lists are empty.

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

### ADM-33 Welcome credits when staff tick "Email confirmed"

**Purpose:** confirming an address by hand in the admin is a confirmation like any other: a new account gets the
welcome credits (ledger line, welcome credit row, in-app notification), a `+tag` variant of an address already
confirmed is refused with one email, and a staff account is never given them.
**Preconditions:** Session A logged in to the admin as `qa.admin` (ADM-32). ADM-18 step 5b put Welcome credits back
to `1000`.

| # | Action | Expected result |
|---|---|---|
| 1 | Open `/admin/billing/billingsettings/1/change/`. | "Welcome credits" is `1000`. If it is not, set it to `1000` and Save before going on. |
| 2 | "Add user": Email USERC (`qa.adm.<rand>.c@example.com`), Name `ADM User C`, password `QaE2e-Passw0rd!` twice; Save. | USERC's change form, "Email confirmed" unticked. |
| 3 | Open `/admin/billing/welcomecredit/` and search USERC's email. | One row: User `qa.adm.<rand>.c@example.com` (the email, not an id), Status "Pending", Reason empty ("-"), Credits 0, Decided at empty. No "Add" button. |
| 4 | Back on USERC's change form tick "Email confirmed"; Save. | Saved with a success message. |
| 5 | `/admin/billing/creditaccount/`, search USERC. | Balance **1000**. |
| 6 | Ledger (`/admin/billing/credittransaction/`), search USERC. | One row: Amount 1000, Type "Welcome credits", Source `billing`, Description "A gift for your new account". |
| 7 | Welcome credits list again; filter Status "Granted"; open USERC's row. | Status "Granted", Credits 1000, Decided at set, Refusal emailed at empty. "Ledger line" is a link reading like `+1000 Welcome credits, 2026-11-01 14:05 UTC`; clicking it opens that ledger row's read-only view. Nothing on the page is editable. |
| 8 | `/admin/core/notification/`, search USERC's email. | One row: User USERC, Source `billing`, Title "Welcome! We added 1000 credits to your account", Read at / Opened at / Dismissed at empty. Filters on the right: source, read state (New (unread) / Read), dismissed (Not dismissed / Dismissed), created at. Filter "read state: New (unread)": still listed; "Read": not. |
| 9 | Open the notification. | Read-only: Id, User, Source, Title, Body "Use them in any ZipTrigo app. Your credits history shows where they go.", Url `/billing/credits/`, the dates. No Save or Delete; `/admin/core/notification/add/` answers 403. Search by title (`Welcome!`) and by USERC's name (`ADM User C`) both find it. |
| 10 | Session B: log in on the site as USERC. Look at the header bell, then open it. | The bell shows **1**; the dropdown lists "Welcome! We added 1000 credits to your account". `/billing/credits/` shows a balance of 1000 credits and the line "A gift for your new account". Stay logged in as USERC in Session B. |
| 11 | Session A: reload the notification list with "read state: Read". | USERC's notification is now listed there (opening the dropdown marked it read); Read at is set. |
| 12 | Add user USERC2 (`qa.adm.<rand>.c+tag@example.com`, Name `ADM User C tag`, the same password); on its change form tick "Email confirmed"; Save. | Saved. The credit accounts list shows USERC2 with 0 (or no row), and the ledger has no row for it. |
| 13 | Welcome credits list, search `c+tag`. | USERC2's row: Status "Refused", Reason "Address already used", Credits 0, Decided at set, Refusal emailed at set. Filter Reason "Address already used" lists it. |
| 14 | Open `/__e2e__/mail/?to=qa.adm.<rand>.c+tag@example.com` (reload until it arrives, up to a minute). | Exactly one email, subject "About your ZipTrigo welcome credits", starting "Hi ADM User C tag,". It says new accounts get free welcome credits but this one did not, because the address or a variation of it (a +tag, or Gmail dots) was already used for a ZipTrigo account; that nothing else is affected; and gives the link `http://localhost:8765/billing/credits/`, signed "The ZipTrigo Team". It does not name the other account. The HTML body has the same text with the link as an `<a href>`. |
| 15 | Click the link in the email. | Opens `/billing/credits/` on the site (the credits history of whoever is signed in in that browser, or the login page with `next=/billing/credits/` when nobody is). |
| 16 | Untick "Email confirmed" on USERC2, Save; tick it again, Save. | Saved both times. The welcome credit row stays "Refused" and **no second email** arrives for USERC2 (wait a minute and reload the mailbox). |
| 17 | Add user STAFF (`qa.adm.<rand>.staff@example.com`, Name `ADM Staff`, the same password). On its change form tick **both** "Is staff" and "Email confirmed"; Save. | Saved. |
| 18 | Welcome credits list, search STAFF. | STAFF's row stays "Pending" with Credits 0 (staff and superusers are not given the welcome credits); the ledger has no row for STAFF, and no notification was created for it. |

### ADM-34 User form: time zone and the read-only fields

**Purpose:** the time zone is validated and reaches the account page; theme and social ids can't be edited.
**Preconditions:** USERC (ADM-33) logged in on the site in Session B. Session A.

| # | Action | Expected result |
|---|---|---|
| 1 | Session A: open USERC's change form. | "Timezone" holds the zone the browser reported when USERC signed in in ADM-33 (the site detects it once), or is empty. "Theme" shows "System" as read-only text; "Google sub" and "Microsoft id" are read-only (empty, shown as "-"). |
| 2 | Set Timezone to `Mars/Olympus`; "Save and continue editing". | "Please correct the error below." and at Timezone "Unknown time zone." Nothing saved. |
| 3 | Set Timezone to `Asia/Tokyo`; "Save and continue editing". | "… was changed successfully. You may edit it again below."; the field shows `Asia/Tokyo`. |
| 4 | Click **History**. | The latest entry by `qa.admin@example.com` says "Changed Timezone." (the ADM-33 save shows "Changed Email confirmed."). |
| 5 | Session B (USERC): open `/account/`. | The time zone select shows "Asia/Tokyo" selected (a zone saved by staff is never replaced by the browser's), and `<html>` carries `data-user-timezone="Asia/Tokyo"`. |
| 6 | Session B: on `/account/` change the theme to Dark under "Appearance". Session A: reload USERC's change form. | Theme now reads "Dark", still read-only text (no select). Session B: set the theme back to System. |

### ADM-35 A superuser made in the admin

**Purpose:** "Superuser status" set on the change form gives the full admin, including the superuser-only parts, and
taking it away removes them again.
**Preconditions:** STAFF (ADM-33, staff, no permissions). Session A, and Session B (log USERC out there first).

| # | Action | Expected result |
|---|---|---|
| 1 | Session B: log out of the site. Go to `/admin/login/` and log in as STAFF. | The admin opens; the dashboard has no models and no "Admin Tools" box; no "Admin Tools" link in the navbar (as USERB in ADM-12). |
| 2 | Session A: on STAFF's change form tick "Superuser status" (help text "Designates that this user has all permissions without explicitly assigning them."); Save. | Saved; the Users list filtered "is staff: Yes" shows STAFF. |
| 3 | Session B: reload `/admin/`. | Every app and model of ADM-04 step 2 is listed, the "Admin Tools" box and the navbar's "Admin Tools" link are there, and "Core settings" is listed. `/admin/tools/` opens the tools (not the 403 text) and `/admin/core/coresettings/` opens the form. |
| 4 | Session A: untick "Superuser status" on STAFF (keep "Is staff"); Save. Session B: reload `/admin/`. | Back to an empty dashboard without "Admin Tools"; `/admin/tools/` is the 403 page "You do not have permission to access this page. Admin Tools are for superusers.". |
| 5 | Session B: log out of the admin (user menu "Log out"). | The "Logged out" page. |

### ADM-36 Promo codes: add, generated code, validation

**Purpose:** the add form generates a code when left blank, normalises a typed one and refuses bad values.
**Preconditions:** Session A.

| # | Action | Expected result |
|---|---|---|
| 1 | Go to `/admin/billing/promocode/`. | The list (Code, Campaign, Credits, Used / max, Credits granted, Active, Starts at, Ends at, Last redeemed, Created at), a date drill-down, filters state, active, campaign, created at, and buttons "Create many" and "Add promo code". |
| 2 | Click "Add promo code". | Sections: (no title) Code, Effect (one choice, credits), Credits, Campaign, Active (ticked); "Validity" Starts at, Ends at, Max redemptions; "Eligibility" New accounts within days, Allowed emails (a textarea, help "Only these addresses may redeem, one per line; empty means anyone. Everyone else is told "Invalid code"."); "Origin" Notes and the read-only Created by / Created at / Updated at / Claimed count. Code's help text ends "Leave empty to generate one." |
| 3 | Code `ab`, Credits `25`; Save. | "Please correct the error below." and at Code "Letters, digits and hyphens only, 4 to 32 characters." |
| 4 | Code empty, Credits `0`; Save. Then Credits `1000001`; Save. | "Ensure this value is greater than or equal to 1." then "Ensure this value is less than or equal to 1000000." |
| 5 | Credits `25`, Starts at today 12:00, Ends at today 10:00; Save. | At Ends at: "The end must come after the start." |
| 6 | Clear both dates; Allowed emails `not-an-email`; Save. | Refused at Allowed emails with "Enter a valid email address." |
| 7 | Clear Allowed emails, leave Code **empty**, Credits `5`, Campaign `ADM gen <rand>`; Save. | Saved ("The promo code “<code>” was added successfully."). The list shows a code of 10 characters from capitals and digits without `0`, `O`, `1` or `I`, Campaign `ADM gen <rand>`, Used / max `0`, Credits granted 0, Active ticked. Created by (on its page) is `qa.admin@example.com`. This is **code G**. |
| 8 | Add another: Code `adm-<rand>` (lower case), Credits `25`, Campaign `ADM campaign <rand>`; Save. | Saved and stored as `ADM-<rand>` (upper case). This is **code A**. |
| 9 | Add another with Code `ADM-<rand>` again (Credits `1`). | Refused at Code: "Promo code with this Code already exists." |
| 10 | Open code A. | Code, Effect and Credits are still editable (nothing claimed yet); there is a "Delete" button; the "Promo redemptions" inline is empty. |

### ADM-37 A redeemed promo code: what can still change

**Purpose:** once redeemed, a code's code string, effect and credits are fixed, it can't be deleted, and the redemption
shows in the inline and the redemptions list.
**Preconditions:** code A (ADM-36). USERC (confirmed, ADM-33). Session A and Session B.

| # | Action | Expected result |
|---|---|---|
| 1 | Session B: log in on the site as USERC, open `/billing/credits/`, type `adm-<rand>` in "Have a promo code?" and click "Redeem". | Back on the page with "Added 25 credits with promo code ADM-<rand>."; the balance is 1025 and the history has "Promo code ADM-<rand> (ADM campaign <rand>)". |
| 2 | Redeem `ADM-<rand>` again. | Refused under the field: "You already redeemed this code." |
| 3 | Session A: reload the promo code list. | Code A: Used / max `1`, Credits granted 25, Last redeemed set. |
| 4 | Open code A. | Code, Effect and Credits are now shown as read-only text; Campaign, Active, the dates, Max redemptions, the eligibility fields and Notes stay editable. Claimed count reads 1. There is **no** "Delete" button. |
| 5 | Look at the "Promo redemptions" inline. | One read-only row: User USERC, Status "Granted", Source "Redeem form", Credits 25, Created at, Granted at, Ledger `#<id>` as a link; no "Add another" link and no delete box. The Ledger link opens the ledger row (+25, Type "Promo code"). |
| 6 | Change Notes to `ADM note`; Save. | Saved; the code string and credits are unchanged. |
| 7 | Go to `/admin/billing/promocode/<code A id>/delete/`. | 403 Forbidden. Code G (never redeemed) offers "Delete" on its page; do not delete it yet. |
| 8 | Go to `/admin/billing/promoredemption/`. | Read-only list: Code `ADM-<rand>`, User USERC, Status "Granted", Source "Redeem form", Credits 25, Created at, Granted at; filters status (Pending / Granted / Released), source, campaign (`code__campaign`), created at; a date drill-down; no "Add" button, no action menu. Search `ADM-<rand>` and USERC's email both find it; filtering campaign `ADM campaign <rand>` keeps it. Opening it shows every field read-only, Transaction included. |

### ADM-38 File transfer: take down from the report list, block a sender with the extras

**Purpose:** the abuse report list's own "Take down" and "Block sender" actions work, and the block form's two
opt-in extras take down the sender's other transfers and deactivate the account.
**Preconditions:** SENDER (ADM-10) is not blocked (ADM-26), is active and has credits; T2 is active. Session A, and
Session B (log out of the site first).

| # | Action | Expected result |
|---|---|---|
| 1 | Session B: log in on the site as SENDER and send two transfers as in ADM-21 steps 1-2: **T3** with a file `adm-report-<rand>.txt`, **T4** with `adm-block-<rand>.txt`, both to `qa.adm.recipient@example.com`. | Both "Your transfer is on its way"; note T3's and T4's links. `/transfer/` lists T2, T3, T4 as ACTIVE. |
| 2 | Session B: report T3 from its download page (Reason "Copyright infringement", Details `ADM T3 <rand>`) and T4 (Reason "Other", Details `ADM T4 <rand>`). | "Thank you, your report has been submitted." for each. |
| 3 | Session A: `/admin/file_transfer/abusereport/`, filter Status "Pending". | The two new reports. Search T3's slug: only T3's report. |
| 4 | Tick T3's report, action "Take down the reported transfer(s)", "Go". | The "Take down transfer(s)" page listing "<T3 id> -- adm-report-<rand>.txt (Active)", with the fields of ADM-23 step 4. |
| 5 | Reason `ADM copyright <rand>`, leave notify unticked, click "Take down". | Back on the **report** list with "Took down 1 transfer(s)."; T3's report is now "Actioned". No email is sent to SENDER for it (check the mailbox after a minute: no "Your transfer "adm-report-<rand>.txt" was removed"). |
| 6 | Tick T3's report again and take it down again with any reason. | "Took down 0 transfer(s)." (it had already ended); nothing changes. |
| 7 | Tick T4's report, action "Block the reported transfer(s)'s sender(s)", "Go". | The "Block sender(s)" page listing "<T4 id> -- adm-block-<rand>.txt (qa.adm.<rand>@example.com)" (no IP: a logged-in sender's transfers record none), with the checkboxes of ADM-25 step 1. |
| 8 | Keep "Block sender email" ticked, Reason `ADM block extras <rand>`, tick **"Also take down this sender's other active transfers"** and **"Also deactivate the sender's account (owned transfers only, no effect on anonymous ones)"**; click "Block sender". | Back on the report list with "Added 1 block-list entry/entries. Took down 2 active transfer(s). Deactivated 1 account(s)." (T2 and T4 were SENDER's active transfers). |
| 9 | Transfers list, search `qa.adm.<rand>@example.com`. | T1, T2, T3, T4 all "Taken down". T2's and T4's Takedown reason is `ADM block extras <rand>`; Takedown notify unticked. |
| 10 | Open T4 → History; open the new block-list entry (`/admin/file_transfer/blockedsender/`) → History; open SENDER's user change form → History. | T4: "Taken down: sender blocked."; the block entry (Kind "Email", Value SENDER's address, Reason `ADM block extras <rand>`, Created by `qa.admin@example.com`): an "Added" entry "Blocked the sender of <T4 id>."; SENDER: "Deactivated: sender blocked.". SENDER's Status is "Inactive". |
| 11 | Session B (still signed in as SENDER): open `/transfer/send/`. Then log out and try to log in as SENDER. | The 403 "Sending isn't available" page; the new login is refused with "User not active". |
| 12 | Session A: on SENDER's change form set Status back to "Active"; Save. Delete the block-list entry from step 8. | SENDER is active again and the block list has none of this run's rows. Session B: SENDER can log in again; log out. |

### ADM-39 Short URLs set-up: a link owner with five links

**Purpose:** create the links the Short URLs moderation cases act on. (`short_url.md` covers the link pages in depth.)
**Preconditions:** Session A and Session B (logged out).

| # | Action | Expected result |
|---|---|---|
| 1 | Session A: add user SUOWNER (`qa.adm.<rand>.su@example.com`, Name `ADM Link Owner`, the usual password) and tick "Email confirmed" on its change form. | Created and confirmed (it gets the 1,000 welcome credits; that is fine). |
| 2 | Session B: log in on the site as SUOWNER. Open `/links/new/` and create, one after the other, links with these destinations and titles, each with **Create link**: L1 `https://example.com/adm-l1-<rand>` `ADM L1`; L2 `https://example.com/adm-l2-<rand>` `ADM L2`; L3 `https://example.com/adm-l3-<rand>` `ADM L3`; L4 `https://example.com/adm-l4-<rand>` `ADM L4`; L5 `https://adm-block-<rand>.example/page` `ADM L5`. | Each lands on its link page with "Link created." and no cost panel (free links). Note each link's id (`/links/<id>/`) and code (the last part of `http://localhost:8765/s/<code>`): L1-L5. |
| 3 | Session A: go to `/admin/short_url/shortlink/` and search `ADM L` (the title). | Five rows: Code, Owner SUOWNER, Destination, Status "Active", Now `active`, Held for review at empty, Pending reports 0, Click count, Is alias ✗, Created at. Filters status, held for review at (Empty / Not empty), is alias, created at; a date drill-down; no "Add" button. |
| 4 | Open L1. | A read-only view with every field and "Short link" `http://localhost:8765/s/<L1 code>`; no Save or Delete. `/admin/short_url/shortlink/add/` answers 403. |
| 5 | Search by SUOWNER's email, then by `adm-l3` (the destination). | The five links, then L3 only. |

### ADM-40 Short URLs: dismiss a report, and auto-hold released by dismissing

**Purpose:** a report shows in the admin and can be dismissed; with a threshold a report holds the link, and
dismissing its last pending report releases the hold.
**Preconditions:** L1, L2 (ADM-39). Session A (also used as the reporting visitor) and Session B (SUOWNER).

| # | Action | Expected result |
|---|---|---|
| 1 | Session A: open `/s/<L1 code>+`, click "Report this link", choose Reason "Spam", Details `ADM L1 <rand>`, "Your email (optional)" `qa.adm.reporter@example.com`, click "Submit report". | "Thank you, your report has been submitted." |
| 2 | Submit another report on L1 at once (reload the preview, report again). | Refused on the form with "You already reported this link recently. Thank you." (a repeat from the same address within 10 minutes is not recorded twice). |
| 3 | `/admin/short_url/linkreport/`. | One row: Link `<L1 code>` (a link to L1's admin page), Reason "Spam" (the row opens from it), Source "Visitor", Status "Pending", Reporter email, Reporter ip `127.0.0.1` (or `::1`), Created at. Filters status, source, reason, created at; no "Add" button. Opening it shows every field read-only. |
| 4 | Open the action menu. | "Dismiss selected report(s)", "Take down the reported link(s)", "Block the reported link(s)' destination domain", "Deactivate the reported link(s)' owner's account"; no "Delete selected". |
| 5 | Tick it, "Dismiss selected report(s)", "Go". | "Dismissed 1 report(s)."; Status "Dismissed"; the report shows Reviewed by `qa.admin@example.com`, Reviewed at and Resolution note "Dismissed via admin.". Its **History** has "Dismissed.". |
| 6 | Dismiss it again. | "Dismissed 0 report(s)." |
| 7 | Open `/s/<L1 code>+`. | Still the "Where does this link go?" preview (dismissing changes nothing for the link). |
| 8 | Open `/admin/short_url/shorturlsettings/`. | Straight to the form, in three sections (tabs): "Prices (credits, one-off, per link)" ("A price of 0 makes that extra free.": Custom alias price, Password price, Detailed analytics price, Extra link price, each 1), "Allowances (per user)" (Free active links (per user) 25, Max links (per user) 500) and "Analytics and abuse" (Click event retention days 90, Auto hold report threshold 0). No Delete button. |
| 9 | Set Max links (per user) `0` and Click event retention days `0`; Save. Then Custom alias price `-1`; Save. | "Ensure this value is greater than or equal to 1." at both; then "Ensure this value is greater than or equal to 0.". Nothing saved. |
| 10 | Restore 500 / 90 / 1 and set **Auto hold report threshold** to `1`; Save. | Saved with "The Short URL settings “Short URL settings” was changed successfully." (or Django's equivalent). |
| 11 | Session A: report L2 from `/s/<L2 code>+` (Reason "Phishing or scam"). | The thank-you message. Reload `/s/<L2 code>+`: "This link isn't available" (it is held). |
| 12 | Session B (SUOWNER): open L2's link page and `/links/`. | An amber "Under review" pill; the page says "This link is under review, so it doesn't work for visitors and can't be changed right now. You can still disable it."; the row says "Under review: it can't be changed for now.". |
| 13 | Session A: Short links list, filter "held for review at: Not empty". | Only L2: Held for review at set, Status still "Active", Now `held`, Pending reports 1. |
| 14 | Link reports list: tick L2's report, "Dismiss selected report(s)". | "Dismissed 1 report(s)." It was L2's last pending report, so the hold is released: L2's Held for review at is empty again and `/s/<L2 code>+` shows the preview again. |

### ADM-41 Short URLs: "Release abuse-review hold"

**Purpose:** staff release a held link from the link list; its pending reports are dismissed with it.
**Preconditions:** threshold 1 (ADM-40), L3. Session A and Session B.

| # | Action | Expected result |
|---|---|---|
| 1 | Session A: report L3 from `/s/<L3 code>+` (Reason "Malware"). | Thank-you message; L3 is now held (`/s/<L3 code>+` shows "This link isn't available"). |
| 2 | Short links list: tick L3, action "Release abuse-review hold (dismisses its pending reports)", "Go". | "Released the hold on 1 link(s)." (no confirmation page). Held for review at empty; Pending reports 0. L3's **History**: "Released the abuse-review hold.". |
| 3 | Link reports list. | L3's report is "Dismissed" with Resolution note "Hold released via admin.". |
| 4 | Session B: reload L3's page. | Active again, no review notice; `/s/<L3 code>+` shows the preview. |
| 5 | Run the release action on L3 again. | "Released the hold on 0 link(s)." |
| 6 | Short URL settings: set Auto hold report threshold back to `0`; Save. | Saved. |

### ADM-42 Short URLs: take down a link and email the owner

**Purpose:** the takedown page asks for a reason, the owner is emailed only when staff tick the box, and the email
carries the reason but never the internal note.
**Preconditions:** L4 (ADM-39). Session A and Session B (SUOWNER).

| # | Action | Expected result |
|---|---|---|
| 1 | Short links list: tick L4, action "Take down selected link(s)", "Go". | A card titled "Take down link(s)": "You are about to take down the following link(s). Each stops working for good (its code is never reissued) and its owner sees the reason below on the link's page.", then `<L4 code> → https://example.com/adm-l4-<rand> (Active)`; fields **Reason** (marked `*`, help text says it is always shown to the owner and never says who reported the link), **Internal note (staff only)** ("Never shown to the owner, on the page or in any email."), an **unticked** checkbox "Notify the owner by email ("one of your links was removed")", a red **Take down** button and a **Cancel** link. |
| 2 | Click Cancel. | Back on the list (the page's own URL); L4 still Active. |
| 3 | Run the action again, leave Reason empty, click Take down. | The page again with "This field is required." under Reason; nothing taken down. |
| 4 | Reason `ADM misleading <rand>`, Internal note `ADM internal SU <rand>`, **tick** Notify; Take down. | "Took down 1 link(s)."; L4's Status "Taken down", Now `taken_down`. Its **History**: "Taken down: ADM misleading <rand>" (never the internal note). |
| 5 | Open `/__e2e__/mail/?to=qa.adm.<rand>.su@example.com` (reload until it arrives, up to a minute). | One email, subject "One of your short links was removed", text "Your short link http://localhost:8765/s/<L4 code> was removed and no longer works.", "Reason: ADM misleading <rand>" and "Details: http://localhost:8765/links/<L4 id>/". It does not contain `ADM internal SU <rand>`, the reporter or the word "reported". The HTML body has the same, with a "See the link" `<a href>` to the details page. |
| 6 | Session B (SUOWNER): click the Details link in the email. | L4's link page with the red notice "This link was taken down: ADM misleading <rand>" and no Edit button. The internal note appears nowhere. |
| 7 | Session A: `/s/<L4 code>` and `/s/<L4 code>+`. | Both "This link isn't available". |
| 8 | Take L4 down again (any reason). | "Took down 0 link(s)." (a taken-down link is left alone); no second email. |

### ADM-43 Short URLs: block the destination domain

**Purpose:** the "block the destination domain" action adds a block-list entry that takes the link offline and
refuses new links there.
**Preconditions:** L5 (`https://adm-block-<rand>.example/page`, ADM-39). Session A and Session B (SUOWNER).

| # | Action | Expected result |
|---|---|---|
| 1 | Short links list: tick L5, action "Block the selected link(s)' destination domain", "Go". | A card "Block destination domain(s)": "Block the destination host of the following link(s). New links to it are refused, and links that already point there stop working at once …", `<L5 code> → https://adm-block-<rand>.example/page`; a **ticked** checkbox "Also block its subdomains" (help: on blocks "*.host", off exactly that host), a Reason box, a red **Block** button and **Cancel**. |
| 2 | Reason `ADM block domain <rand>`, keep the box ticked; Block. | "Added 1 block-list entry/entries. Links pointing there stop working at once." |
| 3 | `/admin/short_url/blockeddomain/`. | A row Value `*.adm-block-<rand>.example`, Reason `ADM block domain <rand>`, Created by `qa.admin@example.com`, Expires at empty, In force ✓. Its **History**: an "Added" entry "Blocked the destination of <L5 code>.". |
| 4 | `/s/<L5 code>+`. Session B: L5's link page. | "This link isn't available". The owner's page shows the **Not working** pill and says the destination can't be used. Short links list: L5's Status still "Active" and its Now column reads `blocked` (the column consults the block list, so it says why the link doesn't work). |
| 5 | Session B: create a link to `https://www.adm-block-<rand>.example/x`. | Refused: "Destination: That address cannot be used." |
| 6 | Session A: run the block action on L5 again. | "Added 0 block-list entry/entries. …" (already blocked); still one row. |
| 7 | Delete the `*.adm-block-<rand>.example` row (open it → Delete → confirm). Run the action on L5 again with "Also block its subdomains" **unticked**. | The new row's Value is exactly `adm-block-<rand>.example`. |
| 8 | Blocked domains → Add: Value `*.com`; Save. Then `not a host`; Save. Then `*.10.0.0.1`; Save. Then `ADM-BLOCK-<rand>.example` (the exact host in capitals); Save. | "A wildcard needs a domain of at least two parts ("*.example.com")."; "Enter a host name ("example.com") or "*.example.com"."; "An IP address can only be blocked exactly."; "This value is already actively blocked -- edit that entry instead of adding a duplicate.". |
| 9 | Delete the exact-host row. | `/s/<L5 code>+` shows the preview again; the blocked domains list has none of this run's rows. |

### ADM-44 Short URLs: deactivate the owner's account

**Purpose:** "Deactivate the owner's account" skips the moderator's own account, can take the owner's other links
down (with a reason), and the owner is shut out.
**Preconditions:** SUOWNER with L1, L2, L3, L5 active (L4 taken down). Session A, Session B (SUOWNER signed in).

| # | Action | Expected result |
|---|---|---|
| 1 | Session A (signed in on the site as `qa.admin` too): open `/links/new/` and create a link to `https://example.com/adm-admin-<rand>`, title `ADM admin link`. | "Link created." |
| 2 | Short links list: search `ADM`, tick L1 and `ADM admin link`, action "Deactivate the selected link(s)' owner's account", "Go". | A card "Deactivate owner's account(s)": "Deactivate the following account(s): the owner can't sign in again or use the API, and can't create, edit or enable links.", listing SUOWNER's email; then "Will not be deactivated:" with "qa.admin@example.com (it is your own account)". A Reason box, an unticked "Also take down the owner's other active links" (help says the owner is not emailed), a red **Deactivate** button and **Cancel**. |
| 3 | Tick "Also take down …", leave Reason empty; Deactivate. | The page again with "Give a reason: it is shown to the owner on each link." under Reason; nothing changed. |
| 4 | Reason `ADM deactivate <rand>`, keep the box ticked; Deactivate. | "Deactivated 1 account(s). Skipped: 1 not deactivated (staff or your own account). Took down 4 link(s)." (L1, L2, L3, L5). `ADM admin link` stays Active. |
| 5 | Users list: SUOWNER. | Status "Inactive". Its **History**: "Deactivated: link owner.". L1's History: "Taken down: owner deactivated.". |
| 6 | Session B (SUOWNER's session is still open): open `/links/new/`. Then open L1's page. | `/links/new/` sends you back to `/links/` with the error "Your account is not active, so links cannot be created, changed or enabled." (an open session outlives the deactivation, but the link pages refuse it). L1's page shows "This link was taken down: ADM deactivate <rand>". No takedown email arrives for these four links. |
| 7 | Session B: log out and log in again as SUOWNER. | Refused with "User not active". |
| 8 | Session A: run the action on L1 alone, without the take-down box. | The page lists SUOWNER "(already inactive)"; after Deactivate: "Deactivated 0 account(s). Skipped: 1 already inactive." |
| 9 | Delete `ADM admin link` from its link page on the site (Session A). | "Link deleted." |

### ADM-45 USERC's data in Receipts, Short URLs and To-do

**Purpose:** set-up for ADM-46, ADM-47 and ADM-50: USERC uses three products. (The product files cover these pages;
here only the data matters.)
**Preconditions:** USERC (1,025 credits) signed in on the site in Session B. **Do not open the File transfer pages as
USERC**: file transfer rows would stop USERC being deleted in ADM-50.

| # | Action | Expected result |
|---|---|---|
| 1 | Open `/receipts/upload/` and upload the small JPEG/PNG/PDF; wait until it is processed. | Read as Staples, 42.10 (the fake AI); 1 credit spent. |
| 2 | Open `/receipts/settings/`. | Note the address shown (`receipts+<code>@…`, call it **RADDR**). |
| 3 | Open `/links/new/`: destination `https://example.com/adm-c-<rand>`, Alias `adm-c-<rand>`, Create link, confirm the cost panel (1 credit). | "Link created."; the short link is `http://localhost:8765/s/adm-c-<rand>`. |
| 4 | In To-do (`/todo/`): on Labels add a label `adm-label`; quick-add a task `ADM task alpha` due **tomorrow** (no time) with the label and a weekly repeat (the "Repeat" button, Week); on its page add a comment `ADM comment`, then edit it to `ADM comment edited`; attach the small text file; search `ADM` on `/todo/search/` and "Save as filter" with the name `ADM filter`. (`todo.md` TODO-03, -08, -17, -19, -23 show how.) | The task exists as #1000 with the label, a repeat chip, a default reminder (5 minutes before, in-app), one edited comment and one attachment; the filter is in the sidebar. |
| 5 | Open `/todo/settings/`. | Note the address under "Add tasks by email" (`todo+<code>@…`, call it **TADDR**). |

### ADM-46 Receipts: inbound emails and the "retry" action

**Purpose:** the Receipts inbound admins show what email intake did, and "Retry selected failed messages" puts a failed
message back through the ingest.
**Preconditions:** RADDR (ADM-45). Shell steps (see Preconditions at the top). Session A.

| # | Action | Expected result |
|---|---|---|
| 1 | (shell step) Write `ok.eml` with these lines (blank line before the body): `From: Billing <billing@vendor.example>`, `To: <RADDR>`, `Subject: ADM receipt <rand>`, `Content-Type: text/plain; charset=utf-8`, blank, `Thank you for your order. Total 42.10 USD, paid by card.` Run `python tests_e2e/ai/manage.py receipts_ingest_eml <path>/ok.eml --to <RADDR>`. | Prints `<RADDR>: accepted, 1 receipt(s)` and `Done (receipts/inbound/local-<uuid>).` |
| 2 | (shell step) Write `big.eml`: a `From:` line, a `Subject:` line of 70,000 `x` characters, a blank line and `hi`. Run `python tests_e2e/ai/manage.py receipts_ingest_eml <path>/big.eml`. | Exits with an error: "Ingest failed after 1 attempt(s): A header is over 65536 bytes. (the object is at receipts/failed/local-<uuid>)". |
| 3 | `/admin/receipts/inboundmessage/`. | Two rows: S3 key `receipts/inbound/local-…`, Status "Done", Attempts 1, Last error empty, Processed at set; and one "Failed", Attempts 1, Last error "A header is over 65536 bytes.". Filter Status "Failed" keeps only that one; search `65536` finds it. No "Add" button. |
| 4 | Open the Done row. | Read-only, with an "Inbound emails" inline: User USERC, Recipient RADDR, From address `billing@vendor.example`, Subject `ADM receipt <rand>`, Status "Accepted". |
| 5 | `/admin/receipts/inboundemail/`. | The same email: Recipient, User, From address, Subject, Status "Accepted", Note, Created at; filters status, created at; search by subject finds it. `/admin/receipts/receipt/` lists a new receipt of USERC with Source "Email". |
| 6 | Inbound messages: tick the **Done** row, action "Retry selected failed messages", "Go". | "Reset 0 failed message(s) to pending." Nothing changes. |
| 7 | Tick the **Failed** row and retry it. | "Reset 1 failed message(s) to pending." The ingest runs again at once (tasks are inline): reload, and the row is "Failed" again with Attempts **1** (a fresh count, not 2) and the same Last error; there is still exactly one failed row. |

### ADM-47 To-do admins

**Purpose:** every To-do admin is read-only and shows USERC's data; the To-do settings singleton validates; To-do's
inbound messages can be retried.
**Preconditions:** ADM-45. Shell steps. Session A.

| # | Action | Expected result |
|---|---|---|
| 1 | `/admin/todo/task/`, search USERC's email. | `ADM task alpha`: User, Number 1000, Due date (the one picked in ADM-45; dates are the user's own, now in Asia/Tokyo), Priority, Recurrence (set), Charged credits 0, Unpaid "-", Source "Web", Created at. Filters priority, source, completed (Completed / Open), trash (In the trash / Not in the trash), unpaid at (Empty / Not empty), created at. Filter "completed: Open" and "trash: Not in the trash" keep it; "trash: In the trash" doesn't. Opening it: no Save/Delete; `/admin/todo/task/add/` answers 403. |
| 2 | `/admin/todo/recurrence/`. | USERC's rule: Rrule `FREQ=WEEKLY…`, Mode, Anchor date = the task's due date, Occurrences created 1, Ended at empty; filters mode and ended at. |
| 3 | `/admin/todo/label/`, `/admin/todo/comment/`. | Label `adm-label` (Color, Tasks 1). Comment: Task `#1000`, User USERC, Edited at set, Revision count 1, Comment `ADM comment edited`; filter "edited at: Not empty" keeps it. Opening the comment shows a read-only "Comment revisions" inline with the old body `ADM comment`. |
| 4 | `/admin/todo/attachment/`, `/admin/todo/reminder/`, `/admin/todo/reminderdelivery/`, `/admin/todo/savedfilter/`. | The text file (Task, Size bytes, Uploaded ✓, Inline type empty, User); the reminder (Task `#1000`, User, Minutes before 5, Channel in-app, Fire at, Sent at empty, Skipped ✗); reminder deliveries probably empty; the saved filter `ADM filter` (User, Position, Search text `ADM`; opening it shows the stored Params). None has "Add" or a delete action. |
| 5 | `/admin/todo/todoaccount/`, open USERC's. | Next task number 1001, Created this month 1, Task creation blocked ✗, Stored bytes > 0, Storage frozen ✗, Default due time 09:00; an "Inbox codes" inline with one active code (the one in TADDR). |
| 6 | (shell step) Write `task.eml`: `From: <USERC's email>`, `To: <TADDR>`, `Subject: Fwd: ADM emailed task`, `Content-Type: text/plain; charset=utf-8`, blank, `Details of the emailed task.` Run `python tests_e2e/ai/manage.py todo_ingest_eml <path>/task.eml --to <TADDR>`. Then the `big.eml` of ADM-46 with `todo_ingest_eml`. | `<TADDR>: accepted, task #1001` and `Done (todo/inbound/local-…)`; the second fails with "A header is over 65536 bytes." |
| 7 | `/admin/todo/inboundemail/` and the Tasks list. | The email: Status "Accepted", From address USERC's; the new task `ADM emailed task` (the `Fwd:` removed), Number 1001, Source "Email". |
| 8 | `/admin/todo/inboundmessage/`: retry the Done row, then the Failed row. | "Reset 0 failed message(s) to pending.", then "Reset 1 failed message(s) to pending." and the row is Failed again with Attempts 1. The Done row's page has an "Inbound emails" inline with a Task link. |
| 9 | `/admin/todo/todosettings/`. | Straight to the form: Free tasks per month 10, Task price (credits) 10, Trash days 30, Max open tasks per user 10000, Max labels per user 200, Max attachment bytes 52428800, Max attachments per task 20, Max inbound rows per day 500, each with help text; no Delete. |
| 10 | Set Trash days `0` and Task price `-1`; Save. | "Ensure this value is greater than or equal to 1." and "Ensure this value is greater than or equal to 0.". Restore 30 / 10; Save: "The To-do settings “To-do settings” was changed successfully." (or Django's equivalent). `/admin/todo/todosettings/1/delete/` answers 403. |

### ADM-48 Promo codes: Duplicate, Export CSV, Create many and the state filter

**Purpose:** the bulk tools and the list filter.
**Preconditions:** codes A (redeemed) and G (ADM-36/37). Session A.

| # | Action | Expected result |
|---|---|---|
| 1 | Promo code list: tick code G, action "Duplicate selected code(s) with a new code string", "Go". | "Created 1 code(s): <new code>." The copy has Credits 5, Campaign `ADM gen <rand>`, Used / max `0`, Created by `qa.admin`. |
| 2 | Delete the copy (its page → Delete → confirm). | Deleted (never redeemed). |
| 3 | Tick A and G, action "Export selected code(s) as CSV", "Go". | A file `promo_codes.csv` downloads: header `code,credits,campaign,active,starts_at,ends_at,max_redemptions,claimed`, one row per code (A: `ADM-<rand>,25,ADM campaign <rand>,True,,,,1`). |
| 4 | Click "Create many". | `/admin/billing/promocode/bulk/`, heading "Create many promo codes", a "Batch" box explaining up to 1000 single-use codes per run, fields Count (10), Prefix, Credits, Campaign, Starts at, Ends at, New accounts within days, Notes, buttons "Create codes" and "Cancel". |
| 5 | Count `1001`, Credits `5`, Campaign empty, Prefix `a b`; Create codes. | Errors: "Ensure this value is less than or equal to 1000.", "This field is required." at Campaign, and "A prefix may only have letters, digits and hyphens." at Prefix. Nothing created. |
| 6 | Count `3`, Prefix `adm`, Credits `5`, Campaign `ADM-BULK-<rand>`; Create codes. | Redirected to the list filtered `?campaign=ADM-BULK-<rand>` with "Created 3 code(s) in campaign "ADM-BULK-<rand>". Download them as CSV." The list shows three codes `ADM-` + 10 characters, Used / max `0 / 1` (single-use). An "Export campaign CSV" button appears. |
| 7 | Click "Download them as CSV" (and the "Export campaign CSV" button). | Both download `promo_codes_ADM-BULK-<rand>.csv` with the three codes, max_redemptions 1, claimed 0. `/admin/billing/promocode/export/` without `?campaign=` redirects to the list with "Choose a campaign to export.". |
| 8 | Edit the three bulk codes: the first **Starts at** tomorrow, the second **Ends at** yesterday, the third Active unticked. Edit code A: Max redemptions `1`. | All saved. |
| 9 | Filter **state**: "Redeemable now", "Scheduled (not started)", "Expired", "Fully redeemed", "Inactive" (clear the campaign filter first). | Scheduled: the first bulk code; Expired: the second; Inactive: the third; Fully redeemed: code A (`1 / 1`); Redeemable now: code G and none of the others. |
| 10 | Session B (USERC): redeem the first bulk code on `/billing/credits/`, then the second. | "This code is not active yet." and "This code has expired." |

### ADM-49 Scheduled jobs after a scheduler run

**Purpose:** the scheduled jobs admin shows each job's state once a scheduler has run.
**Preconditions:** shell steps. Session A.

| # | Action | Expected result |
|---|---|---|
| 1 | (shell step) Run the scheduler for about 30 seconds and stop it: `python tests_e2e/ai/manage.py run_scheduler --tick-seconds 1 & PID=$!; sleep 30; kill -INT $PID; wait $PID`. | Prints "Scheduler running, tick=1.0s" and, at the end, "Scheduler stopped". |
| 2 | `/admin/core/scheduledjob/`. | One row per job, e.g. `core.purge_old_email_verifications` (Interval seconds 86400), `core.clear_expired_sessions`, `billing.settle_welcome_credits`, `file_transfer.expire_transfers` (300), `receipts.poll_inbound` (60), `short_url.rollup_clicks`, `todo.send_reminders` (60) and the rest of the apps' jobs; each with Last started at, Last finished at, Last success at set, Run count 1 or more, Locked until empty or in the past (a job that was still running when the scheduler was stopped keeps its lease until it expires: that is expected). No "Add" button, no actions. |
| 3 | Open a job. | Every field read-only, including Last error; no Save or Delete. A job whose Last success at is empty or whose Last error is not empty is a **FAIL** (name the job and the first line of the error). |

### ADM-50 Delete a user who has Receipts, Short URLs and To-do data

**Purpose:** deleting a user removes their rows in every product (the read-only admins don't block it), while a short
code is never reissued and the address stays known for the welcome credits.
**Preconditions:** USERC with the data of ADM-33 to ADM-48 (no file transfer). Session A, Session B.

| # | Action | Expected result |
|---|---|---|
| 1 | Session B: log out of the site. Session A: open USERC's change form and click "Delete". | The confirmation page asks to delete `qa.adm.<rand>.c@example.com` and lists the related objects that go with it: the credit account and credit transactions, the welcome credit, the promo redemption, the notification(s), the Receipts account (and its inbox code), the receipt(s), the vendor and its aliases, the inbound email, the short link, the To-do account (and inbox code), the tasks, label, comment and its revision, attachment, reminder, recurrence, saved filter and the To-do inbound email. It does **not** say your account lacks permission to delete any of them, and has a "Yes, I’m sure" button. |
| 2 | Confirm. | "The user “qa.adm.<rand>.c@example.com” was deleted successfully." |
| 3 | Search USERC's email in the ledger, credit accounts, welcome credits, notifications, promo redemptions, receipts, receipts accounts, short links, tasks and To-do accounts. | No rows anywhere. |
| 4 | Promo code list: code A. | Still claimed: Claimed count 1 on its page, and it stays "Fully redeemed" (its use stays spent). Credits granted now reads 0 and Used / max reads `0 (+1 pending) / 1`: record this as a UX issue (the deleted user's granted redemption is counted as pending). |
| 5 | Session B: open `/s/adm-c-<rand>` and `/s/adm-c-<rand>+`. | "This link isn't available" (404). |
| 6 | Session A (signed in on the site as `qa.admin`): open `/links/new/`, destination `https://example.com/reuse`, Alias `adm-c-<rand>`, Create link. | Refused with "Alias: That alias is not available." (a deleted user's codes are retired); nothing is charged. |
| 7 | Session B: register USERC's address again on `/account/register/` (Name `ADM User C again`, password `QaE2e-Passw0rd!`), open the confirmation email in `/__e2e__/mail/?to=qa.adm.<rand>.c@example.com` (reload until it arrives), click its link and press "Confirm my email". | The account is confirmed. |
| 8 | Reload the mailbox for that address (up to a minute). | An email "About your ZipTrigo welcome credits" arrives: the address was remembered even though the account that first confirmed it was deleted. Session A: the welcome credits list shows the new account "Refused", "Address already used", and its balance is 0. |

### ADM-51 Staff with specific permissions

**Purpose:** each moderation action appears only with the permission it needs, the "also take down" checkbox needs
its own, and the credit tool needs `billing.add_credittransaction`.
**Preconditions:** STAFF (staff, not superuser, no permissions; ADM-35). Transfers T1-T4, their reports and the short
links of ADM-39 exist. Session A, and Session B logged in to the admin as STAFF.

| # | Action | Expected result |
|---|---|---|
| 1 | Session A: in STAFF's "User permissions" add "File transfer \| transfer \| Can view transfer" and "File transfer \| abuse report \| Can view abuse report"; Save. Session B: open `/admin/file_transfer/transfer/` and `/admin/file_transfer/abusereport/`. | Both lists load, but with **no** action menu or row checkboxes (no action STAFF may run). Neither page offers Add. |
| 2 | Session A: add "File transfer \| blocked sender \| Can add blocked sender"; Save. Session B: reload both lists. | The transfer list's actions offer only "Block sender of selected transfer(s)", the report list's only "Block the reported transfer(s)'s sender(s)". "Blocked senders" appears on the dashboard. |
| 3 | Session B: tick T1, "Block sender of selected transfer(s)", "Go"; untick "Block sender email", tick "Also take down this sender's other active transfers"; click "Block sender". | The page again with "You don't have permission to take down transfers." at that checkbox; nothing is added. Leave the page through the breadcrumbs. |
| 4 | Session A: add "File transfer \| transfer \| Can take down a transfer, release an abuse-review hold, or dismiss a report (issue #59)"; Save. Session B: reload. | The transfer list now also offers "Take down selected transfer(s)" and "Release abuse-review hold (dismisses its pending reports)"; the report list "Dismiss selected report(s)" and "Take down the reported transfer(s)". Do not run them. |
| 5 | Session A: add "Short URLs \| short link \| Can view short link" and "Short URLs \| link report \| Can view link report"; Save. Session B: open the short links and link reports lists. | Both load with no action menu. |
| 6 | Session A: add "Short URLs \| short link \| Can take down, hold and release short links, and review their reports"; Save. Session B: reload. | Short links: "Take down selected link(s)" and "Release abuse-review hold (dismisses its pending reports)" only; link reports: "Dismiss selected report(s)" and "Take down the reported link(s)". No block or deactivate action. |
| 7 | Session A: add "Short URLs \| blocked domain \| Can add blocked domain"; Save. Session B: reload. | The block-domain actions appear on both lists; "Blocked domains" appears on the dashboard with Add. Still no deactivate action. |
| 8 | Session A: add "Accounts \| user \| Can change user"; Save. Session B: reload. | "Deactivate the selected link(s)' owner's account" (and the reports' twin) appears; "Users" appears on the dashboard and a user's change form can be opened and saved. |
| 9 | Session A: add "Billing \| credit transaction \| Can view credit transaction"; Save. Session B: open the ledger, then `/admin/billing/credittransaction/adjust/`. | The ledger lists rows but has **no** "Adjust credits" button; the adjust page is the 403 with "You do not have permission to adjust credits.". |
| 10 | Session A: add "Billing \| credit transaction \| Can add credit transaction"; Save. Session B: reload the ledger and click "Adjust credits"; add `1` credit to STAFF's own email with Description `ADM staff test`. | The button is there; "Adjusted credits for qa.adm.<rand>.staff@example.com. New balance: 1." The ledger still has no "Add" button. |
| 11 | Session B: `/admin/tools/`, `/admin/core/coresettings/`, `/admin/billing/promocode/bulk/`. | Tools: the 403 text for non-superusers; Core settings refused; the bulk page redirects to the promo code list with "You cannot add promo codes." (or is refused outright: STAFF has no promo permission). No "Admin Tools" link anywhere. |
| 12 | Session A: remove every permission from STAFF and untick "Is staff"; Save. Session B: log out. | STAFF is an ordinary account again. |

### ADM-52 Dark mode and phone width on the admin's own pages

**Purpose:** the pages this project adds to the admin are readable in the dark theme and on a phone.
**Preconditions:** Session A. A transfer and a short link to act on (any of this run's).

| # | Action | Expected result |
|---|---|---|
| 1 | Turn the dark theme on (ADM-06). Open `/admin/tools/` and click "Show". | The page and the "Confirm" / "Are you sure?" modal are readable: no light text on a white box, no dark text on a dark background (the modal's box is drawn with an inline white background: record unreadable text as a FAIL). Cancel. |
| 2 | Open the adjust credits page, the promo code "Create many" page, a transfer's "Take down" and "Block sender" confirmation pages and a short link's "Take down", "Block" and "Deactivate" pages (choose the action, click "Go", read, then leave without submitting). | All readable in dark: labels, help texts (the file transfer pages use an inline grey), inputs and buttons; the Short URL cards use the theme's card colours. |
| 3 | Turn the dark theme off. Set the viewport to 375 px wide; open `/admin/`, `/admin/tools/` and the adjust credits page. | The sidebar collapses behind a menu button; the content fits without horizontal page scroll. The adjust page lays its three fields in a fixed grid (`1fr 200px 160px`): record any horizontal scroll or clipped field as a UX issue. Reset the viewport. |

### ADM-53 History and Recent actions for the admin actions

**Purpose:** the History of the objects the actions changed reads well, and the dashboard's Recent actions list them.
**Preconditions:** ADM-33 to ADM-51. Session A.

| # | Action | Expected result |
|---|---|---|
| 1 | Go to `/admin/`. | "Recent actions" lists this run's latest changes, newest first: the users added and changed, the deleted USERC, promo codes, settings, block-list entries added by the actions, links and transfers taken down; objects that still exist are links. |
| 2 | Open SENDER's change form → History. | Entries for the form saves ("Changed Email confirmed.", "Changed Status.", …) and "Deactivated: sender blocked." from ADM-38, each with date and `qa.admin@example.com`. |
| 3 | Open L2's and L3's admin pages → History. | L3: "Released the abuse-review hold." and "Taken down: owner deactivated."; L2: only "Taken down: owner deactivated." (its hold was released by dismissing the report, which logs on the report, not the link). |
| 4 | Log out of the admin (user menu). | The "Logged out" page; `/admin/` redirects to the login page. |

## Out of scope

- The JWT API endpoints the admin uses or mirrors (`POST /api/billing/users/{id}/credits` and its `billing.add_credittransaction`
  check) are covered by unit tests, not here.
- Safe Browsing's automated reports (Source "Safe Browsing" on link reports, `reputation_cleared_destination`) need a real
  `SAFE_BROWSING_API_KEY`; GeoIP countries need the GeoLite2 database. Neither is available on this server.
- Inbound email through SES is replaced by the `receipts_ingest_eml` / `todo_ingest_eml` shell steps above; how SES itself
  renders headers is not tested.
- Google/Microsoft sign-in, so the read-only "Google sub" and "Microsoft id" fields are only checked empty.
- A promo code reserved at signup ("+N pending" while the account is unconfirmed) is in `billing.md`/`accounts.md` (the
  register page's promo field), not repeated here.
