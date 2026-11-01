# Emails

## Scope

Every email the site sends, checked end to end: what triggers it, who gets it, its exact subject,
what the text body and the HTML body say, and where every link in it lands. Other plans check
their area's emails briefly; this file is the thorough, cross-cutting one. It also checks the rules
every email follows:

- **Recipient.** Account notices go only to a *confirmed* address (password changed, feedback
  receipt, receipts, short link and to-do notices, welcome refusal); a confirmation goes to the
  address being confirmed; nothing is sent for an address that has no account (forgot password,
  resend confirmation).
- **Subject.** A fixed text, or the owner's own words (a file transfer's file name, a to-do
  title). Never text a stranger wrote (a feedback message, an inbound email's subject, a takedown
  reason, a typed name).
- **Escaping.** Free text in an HTML body (a name, a file name, a feedback message, an email
  subject, a reason, a task title) is shown as text: the source has `&lt;b&gt;`, never a raw
  `<b>` or `<script>`. A text-only email has **no** HTML part at all.
- **Links.** Absolute, on the server's own `BASE_URL` (written `<BASE>` below, e.g.
  `http://localhost:8765`), and each one opens the right page.

### Inventory

Subjects are quoted from the code; `<...>` is filled in at send time.

| # | Email (subject) | Trigger | Recipient | Case |
|---|---|---|---|---|
| 1 | `Confirm your ZipTrigo account email` | Register; "Resend confirmation email"; an email change (`PUT /api/account`) | The address being confirmed | MAIL-02, MAIL-03, MAIL-18 |
| 2 | `You already have a ZipTrigo account` | Register with the address of a **confirmed** account | That account's address | MAIL-04 |
| 3 | `Finish setting up your ZipTrigo account` | Register with the address of an **unconfirmed** account | That address | MAIL-08 |
| 4 | `Reset your ZipTrigo account password` | Forgot password | The account's address (none for an unknown one) | MAIL-05 |
| 5 | `Your ZipTrigo password was changed` | Change password on the account page | The account's address, if confirmed | MAIL-07, MAIL-18 (not sent) |
| 6 | `Your ZipTrigo email address was changed` | Email change through `PUT /api/account` | The address being left, if it was confirmed | MAIL-18 |
| 7 | `About your ZipTrigo welcome credits` | Confirming an address seen before (a `+tag` of a confirmed one) | That account | MAIL-09 |
| 8 | `Thanks for your feedback` | Footer "Feedback" form | The submitter, if confirmed | MAIL-10 |
| 9 | `New feedback submitted` | Same | Every active superuser | MAIL-10 |
| 10 | `Test email from the admin panel` | Admin > Tools > "Send test email" | The address typed | MAIL-11 |
| 11 | `<file> was sent to you via ZipTrigo` / `<file> and N more file(s) were sent to you via ZipTrigo` | Send a transfer; "Add recipients" | Each recipient | MAIL-12, MAIL-17 |
| 12 | `Your transfer "<file>" is on its way` | Send a transfer (logged in) | The sender | MAIL-12 |
| 13 | `"<file>" was downloaded` / `"all files" was downloaded` | A download, with "Email me when a file is downloaded" on | The sender | MAIL-13 |
| 14 | `Your transfer "<file>" expires tomorrow` | `expire_transfers` job (shell step) | The sender | MAIL-14 |
| 15 | `Files for "<file>" have been deleted` | Expiry, or the end of a suspension's grace period (shell steps) | The sender | MAIL-14, MAIL-15 |
| 16 | `Your transfer "<file>" was suspended (out of credits)` | Metering suspends a transfer (shell step) | The sender | MAIL-15 |
| 17 | `Your transfer "<file>" was removed` | Admin takedown with "Notify sender" ticked | The sender | MAIL-16 |
| 18 | `Confirm your ZipTrigo file transfer` | Anonymous send | The anonymous sender | MAIL-17 |
| 19 | `Your transfer "<file>" is on its way` (with a manage link) | Anonymous send confirmed | The anonymous sender | MAIL-17 |
| 20 | `N receipts need your attention` / `1 receipt needs your attention` | A Receipts inbound email whose receipts fail or need review (shell step) | The owner, if confirmed | MAIL-19 |
| 21 | `Your receipts storage is paused` | Receipts storage can't be paid (shell step) | The owner, if confirmed | MAIL-20 |
| 22 | `Your to-do file storage is paused` | To-do attachment storage can't be paid (shell step) | The owner, if confirmed | MAIL-20 |
| 23 | `One of your short links was removed` | Admin takedown of a short link with "Notify the owner" ticked | The owner, if confirmed | MAIL-21 |
| 24 | `Reminder: <task title>` | A to-do reminder with channel Email or Both comes due (shell step) | The owner, if confirmed | MAIL-22 |

Out of scope (see the end of the file): the Google / Microsoft wording of email 2, delivery
failures, and rate-limited sends.

## Preconditions

- The AI test server is running with a fresh database (see [`README.md`](README.md)), in the
  default **real-services** mode: email goes over SMTP and is read back over IMAP. All URLs below
  are relative to it; `<BASE>` is its URL.
- **Reading mail.** Open `/__e2e__/mail/?to=<address>` and **reload until the email arrives, up to
  a minute** (delivery takes a few seconds). The page is headed "E2E mailbox (N)" and lists the
  emails sent to that address in this run, newest first; each shows **To**, **From**, **Subject**,
  **Sent**, the text body (links are clickable) and, when the email has an HTML part, a collapsed
  "HTML body (source)" showing that HTML as source text. "No emails." means none. When a step says
  an email must **not** arrive, wait a full minute and reload before deciding.
- **Shell steps** (marked "(shell step)") run `manage.py` against the running server's database,
  bucket and mail with `python tests_e2e/ai/manage.py [--port <port>] <arguments>` from the repo
  root (add `--port` when the server is not on 8765). An email a shell step sends shows up in
  `/__e2e__/mail/` like any other. In `--fakes` mode the mailbox lives inside the server process,
  so emails sent from a shell step do **not** appear: run this file in real mode. Without shell
  access, mark the shell cases **BLOCKED: needs shell**.
- Seeded accounts (password `QaE2e-Passw0rd!`): `qa.admin@example.com` (superuser) is used for the
  admin steps.

### Browser windows

- **Visitor window**: logged out. Used for registering, confirming and resetting, and for the
  public download page.
- **Main window**: logged in as `MAIL_USER` (from MAIL-06 on).
- **Admin window**: logged in as `qa.admin@example.com` (from MAIL-10 on).

They need separate cookies (separate browser contexts, or different host aliases of the same
server such as `localhost:8765` and `mail2.localhost:8765`). With only one context, log out and in
as each step says; nothing here relies on two sessions being open at once, except where stated.

### Values to record

| Name | Value |
|---|---|
| `R` | A random 6-character suffix (lower-case letters and digits) chosen once, e.g. `k3x9p2` |
| `MAIL_USER` | `qa.mail.<R>@example.com` |
| `MAIL_NAME` | `QA <b>Mail</b> & "Co"` (typed exactly; the HTML checks look for it) |
| `MAIL_NAME_HTML` | how `MAIL_NAME` must appear in HTML source: `QA &lt;b&gt;Mail&lt;/b&gt; &amp; &quot;Co&quot;` |
| `P1`, `P2`, `P3` | `QaMail-Passw0rd1`, `QaMail-Reset-7302`, `Zt-Change-5814` (MAIL_USER's passwords in turn) |
| `PENDING` | `qa.mail.pending.<R>@example.com` |
| `TAGGED` | `qa.mail.<R>+tag@example.com` |
| `CHG`, `CHG_NEW` | `qa.mail.chg.<R>@example.com`, `qa.mail.chgnew.<R>@example.com` |
| `RCPT1`, `RCPT2` | `qa.mail.rcpt1.<R>@example.com`, `qa.mail.rcpt2.<R>@example.com` |
| `ANON`, `ANON_RCPT` | `qa.mail.anon.<R>@example.com`, `qa.mail.anonrcpt.<R>@example.com` |
| `CONFIRM_1`, `CONFIRM_2`, `SLUG_M`, ... | Links and ids recorded by the cases below |

### Test files

File transfer and the Receipts shell step need files; create them in the page (no disk needed) with
the JavaScript of `file_transfer.md` > "Test data", e.g. on `/transfer/send/`:

```js
((specs) => {
  const dt = new DataTransfer();
  for (const s of specs) dt.items.add(new File([s.content], s.name, { type: 'text/plain' }));
  const input = document.getElementById('file-input');
  input.files = dt.files;
  input.dispatchEvent(new Event('change', { bubbles: true }));
})([{ name: 'qa-<b>mail</b>.txt', content: 'M'.repeat(999) + '\n' }]);
```

---

## Accounts

### MAIL-01 The mailbox works and starts empty

**Purpose:** the mail reader answers and filters by recipient before anything is sent.
**Preconditions:** fresh server; `R` chosen.

| # | Action | Expected result |
|---|---|---|
| 1 | Open `/__e2e__/mail/?to=<MAIL_USER>`. | "E2E mailbox (0)" and "No emails."; a line says the mail is read back over IMAP for this run. No error page (an "IMAP error" heading is a **BLOCKED** for the whole file: the server's mail settings are wrong). |
| 2 | Open `/__e2e__/mail/` (no filter). | The page loads; it may list the server's own preflight probe or nothing. Note the count. |

### MAIL-02 Signup confirmation email

**Purpose:** registering sends one confirmation email, the name in it is escaped in HTML, and the
link lands on the confirm page without confirming anything.
**Preconditions:** Visitor window (logged out).

| # | Action | Expected result |
|---|---|---|
| 1 | Open `/account/register/`. Name `MAIL_NAME`, Email `MAIL_USER`, Password `P1`, Promo code empty. Click **Create account**. | Lands on `/account/created/`: heading **Check your email**, "We've sent you an email with what to do next. ..." and "A confirmation link expires in 48 hours." |
| 2 | Open `/__e2e__/mail/?to=<MAIL_USER>` (reload until it arrives). | Exactly **one** email. Subject exactly `Confirm your ZipTrigo account email` (nothing typed in the form is in it). |
| 3 | Read the text body. | `Hi QA <b>Mail</b> & "Co",` (the name as typed, plain text), "Please confirm the email address of your ZipTrigo account. Open the link below and press "Confirm my email":", then a link `<BASE>/account/confirm-email/<token>/`, "This link will expire in 48 hours.", "If you did not expect this email, please ignore it." and the signature "-- The ZipTrigo Team". Record the link as `CONFIRM_1`. |
| 4 | Open "HTML body (source)". | It starts `Hi MAIL_NAME_HTML,` -- i.e. `Hi QA &lt;b&gt;Mail&lt;/b&gt; &amp; &quot;Co&quot;,`. A raw `<b>Mail</b>` in the source is a **FAIL**. It has `<strong>Confirm my email</strong>` and an `<a href="CONFIRM_1">` whose text is the same URL. No `None`, `{{` or `{%`. |
| 5 | Open `CONFIRM_1` (click it in the mailbox). | `<BASE>/account/confirm-email/<token>/`: title "Confirm your email - ZipTrigo", heading **Confirm your email**, "Press the button below to confirm your email address." and a **Confirm my email** button. Do **not** press it. |
| 6 | Open `/account/login/` and try to log in as `MAIL_USER` / `P1`. | Refused: "Please confirm your email address before logging in. Check your inbox for the confirmation link." (opening the link alone confirmed nothing). |

### MAIL-03 Resend replaces the old link; confirming

**Purpose:** "Resend confirmation email" sends a new email whose link supersedes the first; only the
POST of the button confirms, and a confirmed link still works when pressed again.
**Preconditions:** MAIL-02; Visitor window. At least **60 seconds** have passed since MAIL-02 step 1
(the resend cooldown: a resend sooner is silently skipped).

| # | Action | Expected result |
|---|---|---|
| 1 | Open `/account/email-confirmation-expired/`. Enter `qa.mail.nobody.<R>@example.com` in Email and click **Resend confirmation email**. | The green text "If the account exists and is not yet confirmed, we'll send a confirmation email. We send at most one a minute: ...". After a minute `/__e2e__/mail/?to=qa.mail.nobody.<R>@example.com` still shows "No emails." |
| 2 | Enter `MAIL_USER` and click **Resend confirmation email**. | The same green text. |
| 3 | `/__e2e__/mail/?to=<MAIL_USER>` (reload until it arrives). | Two emails, both `Confirm your ZipTrigo account email`. The newest has a different link: record it as `CONFIRM_2` (`CONFIRM_2` != `CONFIRM_1`). |
| 4 | Open `CONFIRM_1` and press **Confirm my email**. | Redirected to `/account/email-confirmation-expired/`: heading **Link Expired**, "This email confirmation link has expired or is invalid. Enter your email below to get a new one." (the resend superseded it). |
| 5 | Open `CONFIRM_2` and press **Confirm my email**. | Redirected to `/account/email-confirmed/`: heading **Email Confirmed!**, "Your email address has been successfully confirmed. You can now log in to your account." and a **Go to Login** button linking to `/account/login/`. |
| 6 | Open `CONFIRM_2` again and press **Confirm my email**. | **Email Confirmed!** again (a link that confirmed itself stays valid within its 48 hours; not "Link Expired"). |
| 7 | Back on `/account/email-confirmation-expired/`, resend for `MAIL_USER` (wait 60 s after step 2 first). | The green text; after a minute no third email arrives (the account is confirmed). |

### MAIL-04 "You already have an account"

**Purpose:** registering a confirmed address again creates nothing, answers like a new signup, and
emails the owner using only the account's own data.
**Preconditions:** MAIL-03 (MAIL_USER confirmed); Visitor window.

| # | Action | Expected result |
|---|---|---|
| 1 | `/account/register/`: Name `Someone <i>Else</i>`, Email `MAIL_USER`, Password `Other-Passw0rd9`, **Create account**. | `/account/created/` with **Check your email** -- the same page as a new signup (nothing says the address is taken). |
| 2 | `/__e2e__/mail/?to=<MAIL_USER>` (reload until it arrives). | A new email, subject exactly `You already have a ZipTrigo account`. |
| 3 | Read the text body. | `Hi QA <b>Mail</b> & "Co",` -- the **account's** name, not `Someone <i>Else</i>` (the typed name appears nowhere in the email). "Someone tried to create a ZipTrigo account with this email address (`MAIL_USER`), but you already have one.", "If it was you, there is nothing to set up again. You can sign in here:" with `<BASE>/account/login/`, "If you forgot your password, you can reset it here:" with `<BASE>/account/forgot-password/`, "If it wasn't you, you can ignore this email: nothing about your account was changed." and the signature. Nothing about Google or Microsoft. |
| 4 | HTML source. | `MAIL_NAME_HTML` in the greeting (no raw `<b>`); the two links as `<a href>`s with the same URLs; no `Else`. |
| 5 | Click the sign-in link, then the reset link. | `/account/login/` (the login form) and `/account/forgot-password/` (heading **Forgot your password?**). |
| 6 | Log in as `MAIL_USER` / `Other-Passw0rd9`. Then log in as `MAIL_USER` / `P1`. | The first is refused ("Invalid credentials"); the second works (nothing was changed). Log out again. |

### MAIL-05 Password reset email; a link works once

**Purpose:** forgot-password emails a working, single-use link; an unknown address gets nothing;
using one link kills every earlier one; a reset sends no "password changed" email.
**Preconditions:** MAIL-04; Visitor window (logged out).

| # | Action | Expected result |
|---|---|---|
| 1 | `/account/forgot-password/`: Email `qa.mail.nobody.<R>@example.com`, **Send reset link**. | "If the account exists, an email will be sent with a password reset link." After a minute `/__e2e__/mail/?to=qa.mail.nobody.<R>@example.com` still has "No emails." |
| 2 | Request a reset for `MAIL_USER`, then a second one. | The same message each time. |
| 3 | `/__e2e__/mail/?to=<MAIL_USER>` (reload until both arrive). | Two new emails, subject exactly `Reset your ZipTrigo account password`. Record the older one's link as `RESET_1`, the newer as `RESET_2`. |
| 4 | Read the newest text body. | `Hi QA <b>Mail</b> & "Co",`, "You requested to reset your password. Click the link below to set a new password:", the link `<BASE>/account/reset-password/<token>/`, "This link will expire in 4 hours.", "If you did not request this, please ignore this email." and the signature. |
| 5 | HTML source. | `Hi MAIL_NAME_HTML,` and an `<a href>` with the reset link; no raw `<b>`. |
| 6 | Open `RESET_2`. | Title "Reset password - ZipTrigo", heading **Set a new password**, fields **New password** and **Confirm password**, button **Save new password**. |
| 7 | Enter `P2` twice and click **Save new password**. | Redirected to `/account/login/` with "Your password is set. You can now log in." |
| 8 | Open `RESET_2` again, then `RESET_1`. | Both show heading **Link Expired**, "This password reset link has expired or is invalid. Please request a new one." and **Request New Link** (-> `/account/forgot-password/`). |
| 9 | Wait a minute and check `/__e2e__/mail/?to=<MAIL_USER>`. | No `Your ZipTrigo password was changed` email (a reset through the emailed link doesn't send one). |

### MAIL-06 Logging in: welcome credits come without an email

**Purpose:** the welcome credits of a new account are announced in the app, not by email.
**Preconditions:** MAIL-05. Main window.

| # | Action | Expected result |
|---|---|---|
| 1 | Main window: log in as `MAIL_USER` / `P2`. | Lands on the home page, logged in. The header bell has a new notification "Welcome! We added 1000 credits to your account". |
| 2 | `/__e2e__/mail/?to=<MAIL_USER>`. | Still only the 2 confirmation, 1 "already have an account" and 2 reset emails: no email about credits. |

### MAIL-07 "Your password was changed"

**Purpose:** changing the password on the account page emails the owner, with a working reset link.
**Preconditions:** MAIL-06; Main window.

| # | Action | Expected result |
|---|---|---|
| 1 | `/account/`: Current Password `P2`, New Password and Confirm New Password `P3`, **Change Password**. | "Your password has been changed."; still logged in. |
| 2 | `/__e2e__/mail/?to=<MAIL_USER>` (reload until it arrives). | One new email, subject exactly `Your ZipTrigo password was changed`. |
| 3 | Text body. | `Hi QA <b>Mail</b> & "Co",`, "The password for your ZipTrigo account (`MAIL_USER`) was changed on YYYY-MM-DD at HH:MM UTC." with today's UTC date and a time within a minute or two of now (UTC, not your local time), "If you made this change, you can ignore this email.", "If you didn't, someone else may have access to your account. Reset your password now:" and `<BASE>/account/forgot-password/`, then the signature. No password anywhere. |
| 4 | HTML source. | `MAIL_NAME_HTML`, the same sentences, an `<a href>` to the forgot-password URL. |
| 5 | Click the reset link. | `/account/forgot-password/` (**Forgot your password?**). |

### MAIL-08 "Finish setting up": an unconfirmed address registered again

**Purpose:** a second signup for an unconfirmed address locks the account down, kills the first
confirmation link and emails a set-password link that carries nothing the creator typed.
**Preconditions:** Visitor window (logged out).

| # | Action | Expected result |
|---|---|---|
| 1 | `/account/register/`: Name `Pending <b>Creator</b>`, Email `PENDING`, Password `QaPending-0001`, **Create account**. | **Check your email**. |
| 2 | `/__e2e__/mail/?to=<PENDING>` (reload until it arrives). | One `Confirm your ZipTrigo account email`, greeting `Hi Pending <b>Creator</b>,`. Record its link as `PENDING_CONFIRM`. Do not open it. |
| 3 | `/account/register/` again: Name `Second Visitor`, Email `PENDING`, Password `QaPending-0002`, **Create account**. | **Check your email** (the same answer). |
| 4 | `/__e2e__/mail/?to=<PENDING>` (reload until it arrives). | A new email, subject exactly `Finish setting up your ZipTrigo account`. No second confirmation email. |
| 5 | Text body. | `Hi there,` (no name at all), "Someone (maybe you) started creating a ZipTrigo account with this email address (`PENDING`).", "To finish setting it up, choose a password here:", a link `<BASE>/account/reset-password/<token>/`, "This link will expire in 4 hours.", "If it wasn't you, you can ignore this email." and the signature. Neither `Pending`, `Creator` nor `Second Visitor` appears in the text or the HTML. Record the link as `SETUP_LINK`. |
| 6 | Open `PENDING_CONFIRM` and press **Confirm my email**. | **Link Expired** (the lockdown retired it). |
| 7 | Log in as `PENDING` / `QaPending-0001`. | Refused with "Invalid credentials" (the creator's password no longer works). |
| 8 | Open `SETUP_LINK`. | **Set a new password**. Enter `QaPending-0003` twice, **Save new password**. Redirected to the login page with "Your password is set. You can now log in." |
| 9 | Log in as `PENDING` / `QaPending-0003`. | Logged in (the reset through the emailed link confirmed the address); the bell shows "Welcome! We added 1000 credits to your account". Log out. |

### MAIL-09 Welcome credits refused for a `+tag` of a known address

**Purpose:** the refusal email goes once, says why, and links to the credits page.
**Preconditions:** MAIL-03 (MAIL_USER is confirmed); Visitor window.

| # | Action | Expected result |
|---|---|---|
| 1 | Register Name `QA Tagged`, Email `TAGGED`, Password `QaTagged-0001`. Open `/__e2e__/mail/?to=<TAGGED>`, open the confirmation link and press **Confirm my email**. | **Email Confirmed!** |
| 2 | `/__e2e__/mail/?to=<TAGGED>` (reload until it arrives). | Exactly one new email, subject exactly `About your ZipTrigo welcome credits`. |
| 3 | Text body. | `Hi QA Tagged,`, "Welcome to ZipTrigo! New accounts get free welcome credits, but yours did not get them: the email address on this account, or a variation of it (for example with a +tag or, for Gmail, different dots), was already used for a ZipTrigo account before, ...", "Nothing else is affected. Your account works normally, and you can add credits or redeem a promo code any time: `<BASE>/billing/credits/`" and the signature. It does not name the other account or its address. |
| 4 | HTML source. | The same text; an `<a href>` with the credits URL as its text. |
| 5 | Click the credits link (logged out). | The login page with `next=/billing/credits/`. Log in as `TAGGED` / `QaTagged-0001`: `/billing/credits/` with balance **0** and no welcome line; no welcome notification in the bell. Log out. |
| 6 | Wait a minute and reload the mailbox. | Still exactly one `About your ZipTrigo welcome credits` (sent once). |

## Feedback and admin

### MAIL-10 Feedback receipt and the superuser notice

**Purpose:** both feedback emails quote the message escaped, keep it out of the subject, and the
admin link works.
**Preconditions:** MAIL-07; Main window (MAIL_USER). Admin window logged in as `qa.admin@example.com`.

| # | Action | Expected result |
|---|---|---|
| 1 | Main window: `/feedback/`. Under the text box: "A copy will be emailed to `MAIL_USER`." Type two lines: `MAIL-10 <script>alert(1)</script> & <b>bold</b>` and `second line`. Click **Send feedback**. | `/feedback/thanks/`: **Thanks for your feedback!** and "We've received your message and emailed a copy to `MAIL_USER`. Every message is read." |
| 2 | `/__e2e__/mail/?to=<MAIL_USER>` (reload until it arrives). | Subject exactly `Thanks for your feedback`. |
| 3 | Text body. | `Hi QA <b>Mail</b> & "Co",`, "Thanks for taking the time to send us your feedback. Every message is read.", "Here's a copy of what you sent on YYYY-MM-DD at HH:MM UTC:", then the two lines quoted with `> ` (`> MAIL-10 <script>alert(1)</script> & <b>bold</b>` and `> second line`), then "The ZipTrigo team". |
| 4 | HTML source. | `MAIL_NAME_HTML`; the message inside a `<blockquote>` as `MAIL-10 &lt;script&gt;alert(1)&lt;/script&gt; &amp; &lt;b&gt;bold&lt;/b&gt;` and `second line`. Any raw `<script>` or `<b>bold` is a **FAIL**. |
| 5 | `/__e2e__/mail/?to=qa.admin@example.com` (reload until it arrives). | Subject exactly `New feedback submitted` (no feedback text in it). |
| 6 | Text body. | `From: QA <b>Mail</b> & "Co" (MAIL_USER)`, `Submitted: YYYY-MM-DD HH:MM UTC`, `Status: New`, the quoted message as in step 3, and `Review it in the admin: <BASE>/admin/feedback/feedback/<uuid>/change/`. |
| 7 | HTML source. | `<strong>From:</strong> MAIL_NAME_HTML (MAIL_USER)`, the escaped message in a `<blockquote>`, and `<a href="<BASE>/admin/feedback/feedback/<uuid>/change/">Review it in the admin</a>`. |
| 8 | Admin window: open the admin link. | The feedback's change page in the admin, showing the message as typed (escaped) and Status "New". |
| 9 | Open `/__e2e__/mail/` and look at every `New feedback submitted` email from this step. | Each is addressed to a superuser (`qa.admin@example.com`); none to `MAIL_USER`. |

### MAIL-11 Admin test email

**Purpose:** the admin's test email arrives and is text-only.
**Preconditions:** Admin window.

| # | Action | Expected result |
|---|---|---|
| 1 | `/admin/tools/`. Under **Email Testing**, set the recipient to `not-an-email` and click **Send test email**. | "Please correct the errors below." and a field error; nothing sent. (If the browser's own email check blocks the submit first, that is fine too.) |
| 2 | Set the recipient to `MAIL_USER`, **Send test email**. | A success message "Test email sent to `MAIL_USER` via N backend(s) (0 failure(s))." with N at least 1. |
| 3 | `/__e2e__/mail/?to=<MAIL_USER>` (reload until it arrives). | Subject exactly `Test email from the admin panel`. Text: "This is a test email.", "Sent from the Django admin panel." and "Sent: YYYY-MM-DD HH:MM:SS UTC" (now, in UTC). No "HTML body (source)" section (text only). |

## File transfer

### MAIL-12 A transfer: recipient email, sender copy, added recipient

**Purpose:** the send emails name the file as the sender wrote it, carry the sender's message, and
their links work; these emails are text-only, so nothing in them can render as HTML.
**Preconditions:** MAIL-07; Main window (MAIL_USER, 1,000+ credits).

| # | Action | Expected result |
|---|---|---|
| 1 | Main window: `/transfer/send/`. Add the file `qa-<b>mail</b>.txt` (1,000 bytes; see "Test files"). | Its row reaches the green check; **Send** is enabled. |
| 2 | Recipients `RCPT1`; Message `Hi <b>there</b> & <script>x</script>`; Expiry "30 days"; leave "Email me when a file is downloaded" checked; **Send**. | The sent page **Your transfer is on its way** with the link `<BASE>/t/<slug>/`. Record the slug as `SLUG_M`. |
| 3 | `/__e2e__/mail/?to=<RCPT1>` (reload until it arrives). | Exactly one email, subject exactly `qa-<b>mail</b>.txt was sent to you via ZipTrigo`. |
| 4 | Its text body. | "You have received a file: qa-<b>mail</b>.txt.", a blank line, `Hi <b>there</b> & <script>x</script>` (the message as typed), then `Download: <BASE>/t/<SLUG_M>/` and "This link expires on YYYY-MM-DD HH:MM UTC." about 30 days ahead. |
| 5 | Look for an HTML part. | There is **no** "HTML body (source)" section: the email is text-only. (If one is shown, the message and file name in it must be escaped -- a raw `<script>` or `<b>` there is a **FAIL**, since the sender's text would render in the recipient's mail client.) |
| 6 | Click the download link (in the Visitor window). | The public download page for `qa-<b>mail</b>.txt`: the file name shown as text (no bold), the message as text, no alert. Don't download yet. |
| 7 | `/__e2e__/mail/?to=<MAIL_USER>` (reload until it arrives). | A new email, subject exactly `Your transfer "qa-<b>mail</b>.txt" is on its way`. Text: `Your transfer "qa-<b>mail</b>.txt" was sent to: RCPT1.`, `Download link: <BASE>/t/<SLUG_M>/`, `Manage this transfer: <BASE>/transfer/`. Text-only. |
| 8 | Click both links (Main window). | The download page of `SLUG_M`, and the transfer dashboard `/transfer/` with the `qa-<b>mail</b>.txt` row. |
| 9 | On the dashboard open **Manage** on that row, enter `RCPT2` in "Add recipients", click **Add**. | The row re-renders without an error; Manage lists `RCPT1` and `RCPT2`. |
| 10 | `/__e2e__/mail/?to=<RCPT2>` (reload until it arrives), and `?to=<RCPT1>`. | `RCPT2` has one email identical in subject and body to step 3-4 (same download link). `RCPT1` still has only its one. No second sender copy for `MAIL_USER`. |

### MAIL-13 Download notifications

**Purpose:** each download, and "download all", tells the sender.
**Preconditions:** MAIL-12; Visitor window.

| # | Action | Expected result |
|---|---|---|
| 1 | Visitor window: `/t/<SLUG_M>/`, click **Download** on the file. | The file downloads as `qa-<b>mail</b>.txt` (or the browser's sanitised form of that name), 1,000 bytes. |
| 2 | `/__e2e__/mail/?to=<MAIL_USER>` (reload until it arrives). | Subject exactly `"qa-<b>mail</b>.txt" was downloaded`. Text: `Someone just downloaded "qa-<b>mail</b>.txt" from your transfer "qa-<b>mail</b>.txt".` and `Manage this transfer: <BASE>/transfer/`. Text-only. The link opens the dashboard (Main window). |
| 3 | Visitor window: click **Download all (.zip)** and then the download link when it appears. | A zip downloads. |
| 4 | Mailbox for `MAIL_USER`. | A new email `"all files" was downloaded`, body `Someone just downloaded "all files" from your transfer "qa-<b>mail</b>.txt".` |

### MAIL-14 "Expires tomorrow", then expiry deletes the files (shell steps)

**Purpose:** the expiry reminder goes once; expiring sends "files deleted".
**Preconditions:** MAIL-13; shell access.

| # | Action | Expected result |
|---|---|---|
| 1 | (shell step) Make the transfer expire in 12 hours, as if it had been sent two days ago, and run the expiry job: `python tests_e2e/ai/manage.py shell -c "from datetime import timedelta; from django.utils import timezone; from apps.file_transfer.models import Transfer; from apps.file_transfer.jobs import expire_transfers; now = timezone.now(); Transfer.objects.filter(slug='<SLUG_M>').update(expires_at=now + timedelta(hours=12), completed_at=now - timedelta(days=2), expiry_notified_at=None); expire_transfers()"` | The command ends without a traceback. |
| 2 | `/__e2e__/mail/?to=<MAIL_USER>` (reload until it arrives). | Subject exactly `Your transfer "qa-<b>mail</b>.txt" expires tomorrow`. Text: `Your transfer "qa-<b>mail</b>.txt" expires tomorrow (YYYY-MM-DD HH:MM UTC).` (12 hours from now, UTC) and `Manage this transfer: <BASE>/transfer/` (opens the dashboard). Text-only. |
| 3 | (shell step) Run the job again: `python tests_e2e/ai/manage.py shell -c "from apps.file_transfer.jobs import expire_transfers; expire_transfers()"`. Wait a minute. | No second "expires tomorrow" email. |
| 4 | (shell step) Move the expiry into the past and run the job: `python tests_e2e/ai/manage.py shell -c "from datetime import timedelta; from django.utils import timezone; from apps.file_transfer.models import Transfer; from apps.file_transfer.jobs import expire_transfers; Transfer.objects.filter(slug='<SLUG_M>').update(expires_at=timezone.now() - timedelta(minutes=1)); expire_transfers()"` | No traceback. |
| 5 | Mailbox for `MAIL_USER`. | Subject exactly `Files for "qa-<b>mail</b>.txt" have been deleted`; text `The files for your transfer "qa-<b>mail</b>.txt" have been deleted and are no longer available.` and nothing else (no link). Text-only. |
| 6 | Visitor window: `/t/<SLUG_M>/`. | The neutral "This transfer is no longer available" page. The recipients got no email about it (`?to=<RCPT1>` still has one email). |

### MAIL-15 Suspension, then the end of the grace period (shell steps)

**Purpose:** the suspension email states the deadline and the credits needed, and the grace-period
deletion emails "files deleted".
**Preconditions:** MAIL-12's flow; shell access; Main window.

| # | Action | Expected result |
|---|---|---|
| 1 | Main window: send another transfer with the file `qa-mail-suspend.txt` (`'S'.repeat(499) + '\n'`) to `RCPT1`, Expiry "30 days". Record its slug as `SLUG_S`. | The sent page. |
| 2 | (shell step) Suspend it as metering does when the balance can't pay a day: `python tests_e2e/ai/manage.py shell -c "from apps.file_transfer.models import Transfer; from apps.file_transfer.services.metering import suspend_transfer; suspend_transfer(Transfer.objects.get(slug='<SLUG_S>'))"` | No traceback. |
| 3 | `/__e2e__/mail/?to=<MAIL_USER>` (reload until it arrives). | Subject exactly `Your transfer "qa-mail-suspend.txt" was suspended (out of credits)`. Text: `Your transfer "qa-mail-suspend.txt" ran out of credits and has been suspended: the download link is no longer working.`, "Top up your credits within 7 day(s) to automatically resume it, or its files will be deleted. You need a balance of at least 1 credit (one day of storage for this transfer); a smaller top-up will not resume it." and `Manage this transfer: <BASE>/transfer/`. Text-only. |
| 4 | Main window: reload any page. | The bell has a "suspended" notification for this transfer; the dashboard row says SUSPENDED. |
| 5 | (shell step) Put the suspension 8 days in the past and run the grace-period deletion: `python tests_e2e/ai/manage.py shell -c "from datetime import timedelta; from django.utils import timezone; from apps.file_transfer.models import Transfer; from apps.file_transfer.services.metering import delete_files_past_grace_period; Transfer.objects.filter(slug='<SLUG_S>').update(suspended_at=timezone.now() - timedelta(days=8)); print(delete_files_past_grace_period())"` | Prints `1`. |
| 6 | Mailbox for `MAIL_USER`. | Subject exactly `Files for "qa-mail-suspend.txt" have been deleted`, the same one-sentence body as MAIL-14 step 5. |

### MAIL-16 Admin takedown with "Notify sender"

**Purpose:** the takedown email uses the removal wording and quotes the reason, but never the
internal note.
**Preconditions:** Main window (MAIL_USER); Admin window.

| # | Action | Expected result |
|---|---|---|
| 1 | Main window: send a transfer with the file `qa-mail-takedown.txt` (`'T'.repeat(299) + '\n'`) to `RCPT1`. Record the slug as `SLUG_T`. | The sent page. |
| 2 | Admin window: `/admin/file_transfer/transfer/`, tick the `qa-mail-takedown.txt` row, action **Take down selected transfer(s)**, **Go**. Reason `Malware <b>found</b> & removed`, Internal note `MAIL internal <R>`, tick **Notify sender by email ("your transfer was removed")**, click **Take down**. | Back on the list with "Took down 1 transfer(s)." |
| 3 | `/__e2e__/mail/?to=<MAIL_USER>` (reload until it arrives). | Subject exactly `Your transfer "qa-mail-takedown.txt" was removed` (the reason is not in it). Text: `Your transfer "qa-mail-takedown.txt" was removed and its files are no longer available.`, a blank line, `Reason: Malware <b>found</b> & removed`. `MAIL internal <R>` appears **nowhere** in the email. No reporter, no link. Text-only. |

### MAIL-17 Anonymous sending: the code email and the sender copy with its manage link

**Purpose:** the anonymous flow's two sender emails, and the recipient email only after confirming.
**Preconditions:** Admin window; Visitor window (logged out).

| # | Action | Expected result |
|---|---|---|
| 1 | Admin window: `/admin/file_transfer/filetransfersettings/`, tick **Anonymous enabled**, **Save**. | Saved. |
| 2 | Visitor window: `/transfer/send/anon/`. Add `qa-anon-<b>x</b>.txt` (`'A'.repeat(799) + '\n'`). Your email `ANON`, Recipients `ANON_RCPT`, Expiry "5 days", **Send**. | **Confirm your email** with a code box. |
| 3 | `/__e2e__/mail/?to=<ANON_RCPT>`. | "No emails." (nothing goes to recipients before confirmation). |
| 4 | `/__e2e__/mail/?to=<ANON>` (reload until it arrives). | Subject exactly `Confirm your ZipTrigo file transfer`. Text: "Someone (hopefully you) is sending files through ZipTrigo.", "Enter this code on the page you were sending from: NNNNNN" (6 digits), "Or click this link to confirm instead: `<BASE>/transfer/send/anon/<uuid>/confirm/link/<token>/`" and "This code expires in 30 minutes. If this wasn't you, just ignore this email -- nothing will be sent." HTML source: the same with the code in `<strong>` and the link as an `<a href>`; it contains no file name or recipient. |
| 5 | Open the link from the email. | Title "Confirm your transfer - ZipTrigo", heading **Confirm your transfer** and a **Confirm this transfer** button (opening it confirms nothing). Don't press it. |
| 5b | Back on the Visitor window's **Confirm your email** page, enter the code and click **Confirm**. | The anonymous sent page **Your transfer is on its way**. |
| 6 | Mailbox for `ANON_RCPT`. | One email `qa-anon-<b>x</b>.txt was sent to you via ZipTrigo`, "You have received a file: qa-anon-<b>x</b>.txt.", the download link and "This link expires on ..." about 5 days ahead. Text-only. |
| 7 | Mailbox for `ANON`. | Newest subject exactly `Your transfer "qa-anon-<b>x</b>.txt" is on its way`: `... was sent to: ANON_RCPT.`, `Download link: <BASE>/t/<slug>/` and "Manage this transfer (disable it, or see how many times it was downloaded) -- keep this link, it's the only way to manage this transfer: `<BASE>/t/<slug>/manage/<token>/`". Text-only. |
| 8 | Open the manage link (Visitor window). | **Manage your transfer** for `qa-anon-<b>x</b>.txt` (the name as text), "Status: ACTIVE", "Downloaded 0 times." Open the download link: the download page of that transfer. |
| 9 | Admin window: untick **Anonymous enabled** and **Save** (restore the default). | Saved. |

## Account email change (API used as a tool)

### MAIL-18 Email change: the notice to the old address, and the new address's confirmation

**Purpose:** changing the address emails a notice to the old one and a confirmation to the new one,
and a later account notice (password changed) is not sent while the new address is unconfirmed.
**Preconditions:** the user-facing email change exists only in the JSON API (`PUT /api/account`); it
is used here only as a tool to trigger the emails (the API itself is out of scope). Visitor window.

| # | Action | Expected result |
|---|---|---|
| 1 | Register Name `QA <b>Chg</b>`, Email `CHG`, Password `QaChg-Passw0rd1`; confirm it through its email (`/__e2e__/mail/?to=<CHG>`, open the link, **Confirm my email**). Log in as `CHG`. | Logged in. |
| 2 | In the browser console of a page on the site, run: `const t = await fetch('/api/auth/login', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({email: '<CHG>', password: 'QaChg-Passw0rd1'})}).then(r => r.json()); const r = await fetch('/api/account', {method: 'PUT', headers: {'Content-Type': 'application/json', Authorization: 'Bearer ' + t.access_token}, body: JSON.stringify({email: '<CHG_NEW>', current_password: 'QaChg-Passw0rd1'})}); [r.status, await r.json()]` (or do the same in the Swagger UI at `/api/docs`, as `feedback.md` FB-15 does). | `[200, {... "message": "Profile updated. Check your inbox to confirm your new email address." ...}]`. |
| 3 | `/__e2e__/mail/?to=<CHG>` (reload until it arrives). | Subject exactly `Your ZipTrigo email address was changed`. Text: `Hi QA <b>Chg</b>,`, "The email address on your ZipTrigo account was changed from `CHG` to `CHG_NEW` on YYYY-MM-DD at HH:MM UTC.", "If you made this change, you can ignore this email.", "If you didn't, someone else may have access to your account." and the signature; no link. HTML source: `Hi QA &lt;b&gt;Chg&lt;/b&gt;,` (no raw `<b>`). |
| 4 | `/__e2e__/mail/?to=<CHG_NEW>` (reload until it arrives). | One `Confirm your ZipTrigo account email` greeting `Hi QA <b>Chg</b>,`. Record its link as `CHG_CONFIRM`; don't open it yet. |
| 5 | Reload `/account/` (the session is still valid). Change the password: Current `QaChg-Passw0rd1`, New and Confirm `Zt-Chg-Other-77`, **Change Password**. | "Your password has been changed." |
| 6 | Wait a minute; check `?to=<CHG_NEW>` and `?to=<CHG>`. | **No** `Your ZipTrigo password was changed` email to either (the account's address is unconfirmed now, and the old one is no longer the account's). |
| 7 | Open `CHG_CONFIRM`, press **Confirm my email**. | **Email Confirmed!** Log out and log in as `CHG_NEW` / `Zt-Chg-Other-77`: logged in. Log out. |

## Receipts, short links and to-do

### MAIL-19 Receipts: "N receipts need your attention" (shell step)

**Purpose:** an inbound email whose receipts fail or need review produces one email to the owner;
the sender's file names and subject are escaped in HTML and kept out of the email's subject.
**Preconditions:** MAIL-07; shell access; Main window (MAIL_USER). Inbound email through SES is out of
scope, so the email is fed in with the `receipts_ingest_eml` command, and the fake AI is told for
these two files to read no vendor, and to say "not a receipt".

| # | Action | Expected result |
|---|---|---|
| 1 | Main window: open `/receipts/` once (creates the Receipts account and its inbox address). | The Receipts dashboard. |
| 2 | (shell step) Build an email with two PNG attachments, from a stranger, and ingest it: `python tests_e2e/ai/manage.py shell -c "from email.message import EmailMessage; from io import BytesIO; from pathlib import Path; from PIL import Image; from django.core.management import call_command; from apps.accounts.models import User; from apps.receipts.services import ai; from apps.receipts.services.accounts import inbox_address_for; from apps.receipts.tests.fakes import sample_extraction; user = User.objects.get(email='<MAIL_USER>'); to = inbox_address_for(user); msg = EmailMessage(); msg['From'] = 'billing@vendor.example'; msg['To'] = to; msg['Subject'] = 'Invoice <b>October</b> & co'; msg.set_content('Two receipts attached.'); b1 = BytesIO(); Image.new('RGB', (400, 400), 'white').save(b1, 'PNG'); b2 = BytesIO(); Image.new('RGB', (401, 400), 'white').save(b2, 'PNG'); msg.add_attachment(b1.getvalue(), maintype='image', subtype='png', filename='qa-<b>first</b>.png'); msg.add_attachment(b2.getvalue(), maintype='image', subtype='png', filename='qa-second.png'); path = Path('qa-mail-attention.eml'); path.write_bytes(bytes(msg)); ai.get_client().queue.extend([sample_extraction(vendor=None), sample_extraction(is_receipt=False)]); call_command('receipts_ingest_eml', str(path), '--to', to); path.unlink()"` | It prints `receipts+<code>@ziptrigo.com: accepted, 2 receipt(s)` and `Done (receipts/inbound/local-<uuid>).` |
| 3 | `/__e2e__/mail/?to=<MAIL_USER>` (reload until it arrives). | Exactly one new email, subject exactly `2 receipts need your attention` (nothing from the inbound email's subject or file names). |
| 4 | Text body. | `From the email "Invoice <b>October</b> & co" (received Mon DD, HH:MM UTC):` (today, in UTC), then two lines: one `  - <file> couldn't read the vendor -> Review: <BASE>/receipts/<uuid>/` and one `  - <file> doesn't look like a receipt or an invoice` (no Review link in the text for a failed one), the two file names being `qa-<b>first</b>.png` and `qa-second.png`; then `Manage receipts: <BASE>/receipts/`. |
| 5 | HTML source. | `From the email &quot;Invoice &lt;b&gt;October&lt;/b&gt; &amp; co&quot; ...`, a `<ul>` with one `<li>` per receipt (`qa-&lt;b&gt;first&lt;/b&gt;.png` escaped, the apostrophe of "couldn't" as `&#x27;`), each ending in `<a href="<BASE>/receipts/<uuid>/">Review</a>`, and `<a href="<BASE>/receipts/">Manage receipts</a>`. A raw `<b>` anywhere is a **FAIL**. |
| 6 | Main window: click each Review link, then Manage receipts. | Each opens that receipt's page: one "Needs review" with the vendor empty, one "Failed" with the not-a-receipt reason; the file names shown as text. Manage receipts opens the dashboard. |
| 7 | Wait a minute. | No second attention email for this inbound email. |

### MAIL-20 Receipts and To-do: "storage is paused" (shell steps)

**Purpose:** freezing either product's storage emails the owner once, with working links, and the
pages show the paused state; a top-up resumes without an email.
**Preconditions:** MAIL-19; shell access; Main window; Admin window.

| # | Action | Expected result |
|---|---|---|
| 1 | Main window: open `/todo/` once (creates the To-do account). | The Today page. |
| 2 | (shell step) Freeze both storages as the daily metering does when the balance can't pay: `python tests_e2e/ai/manage.py shell -c "from apps.receipts.models import ReceiptsAccount; from apps.receipts.services.metering import freeze_account as freeze_receipts; from apps.todo.models import TodoAccount; from apps.todo.services.metering import freeze_account as freeze_todo; freeze_receipts(ReceiptsAccount.objects.get(user__email='<MAIL_USER>')); freeze_todo(TodoAccount.objects.get(user__email='<MAIL_USER>'))"` | No traceback. |
| 3 | `/__e2e__/mail/?to=<MAIL_USER>` (reload until both arrive). | Two new emails: `Your receipts storage is paused` and `Your to-do file storage is paused` (exact subjects). |
| 4 | The Receipts one. | Text: "Your credit balance couldn't cover the storage for your receipts.", "Your receipts are safe, nothing was deleted. Until you add credits, previews, downloads and processing are paused.", `Add credits: <BASE>/billing/credits/` and `Your receipts: <BASE>/receipts/`. HTML: the same sentences and two links "Add credits" and "Your receipts" with those URLs. |
| 5 | The To-do one. | Text: "Your credit balance couldn't cover the storage for the files attached to your tasks (0 bytes, about 1 credit a day).", "Your files are safe, nothing was deleted. Until you add credits, uploading and downloading attachments is paused. It resumes by itself when your balance covers a day of storage.", `Add credits: <BASE>/billing/credits/`, `Your to-do settings: <BASE>/todo/settings/` and the signature. HTML: the same, with links "Add credits" and "Your to-do settings". |
| 6 | Click every link (Main window). | `/billing/credits/`, `/receipts/` (with the "Storage is paused." banner) and `/todo/settings/` (with "Storage is paused until you add credits." and a Top up link). The bell has "Receipts storage is paused" and "Your to-do file storage is paused". |
| 7 | (shell step) Run step 2 again. Wait a minute. | No new emails (an account already frozen is not frozen again). |
| 8 | Admin window: `/admin/billing/credittransaction/adjust/`, User email `MAIL_USER`, Direction Add, Amount `1`, **Apply**. | The adjustment is recorded. |
| 9 | Main window: reload `/receipts/` and `/todo/settings/`; check the mailbox after a minute. | Both paused notices are gone; no email about resuming. |

### MAIL-21 Short link takedown with "Notify the owner"

**Purpose:** the owner is told which link was removed and why, the reason is escaped in HTML and not
in the subject, and the link to the link's page works.
**Preconditions:** Main window (MAIL_USER); Admin window.

| # | Action | Expected result |
|---|---|---|
| 1 | Main window: `/links/new/`, Destination `https://example.com/mail-<R>`, nothing else, **Create link**. | "Link created." on `/links/<id>/`. Record the id as `LINK_ID` and the code as `LINK_CODE`. |
| 2 | Admin window: `/admin/short_url/shortlink/`, tick that link, action **Take down selected link(s)**, **Go**. Reason `Phishing <b>page</b> & more`, Internal note `MAIL link note <R>`, tick **Notify the owner by email ("one of your links was removed")**, **Take down**. | "Took down 1 link(s)." |
| 3 | `/__e2e__/mail/?to=<MAIL_USER>` (reload until it arrives). | Subject exactly `One of your short links was removed`. |
| 4 | Text body. | `Your short link <BASE>/s/<LINK_CODE> was removed and no longer works.`, a blank line, `Reason: Phishing <b>page</b> & more`, a blank line, `Details: <BASE>/links/<LINK_ID>/`. `MAIL link note <R>` appears nowhere. |
| 5 | HTML source. | `<p>Your short link <BASE>/s/<LINK_CODE> was removed and no longer works.</p>`, `<p>Reason: Phishing &lt;b&gt;page&lt;/b&gt; &amp; more</p>` and `<a href="<BASE>/links/<LINK_ID>/">See the link</a>`. |
| 6 | Main window: click the Details link. | `/links/<LINK_ID>/` with "This link was taken down: Phishing <b>page</b> & more" (the reason as text). The short link `<BASE>/s/<LINK_CODE>` itself opens the neutral unavailable page. |

### MAIL-22 To-do reminder email (shell step)

**Purpose:** an email reminder is delivered once with the task's title and due time; the title and
description are escaped (the description rendered as Markdown) and both links work.
**Preconditions:** MAIL-20; shell access; Main window. The scheduler does not run on the test server,
so the delivery job is run by hand.

| # | Action | Expected result |
|---|---|---|
| 1 | Main window: on `/todo/` quick-add the title `Call <b>the</b> bank & co`, Date today, Time at least an hour from now in your local time (if that is past midnight use tomorrow), **Add task**. Record its number as `T_MAIL`. | The task is listed with its title as text (no bold). |
| 2 | Open `/todo/<T_MAIL>/`. Edit the description to `**Bring** the papers <script>alert(1)</script>`, **Save**. In **Reminders** choose **At time of task**, channel **Email**, **Add**. | "At time of task (email)" is listed with "Fires" at the due time; the default "5 minutes before (in-app)" is there too. |
| 3 | (shell step) Make the email reminder due a minute ago and run the delivery job: `python tests_e2e/ai/manage.py shell -c "from datetime import timedelta; from django.utils import timezone; from apps.todo.models import Reminder; from apps.todo.jobs import send_reminders; Reminder.objects.filter(task__user__email='<MAIL_USER>', task__number=<T_MAIL>, minutes_before=0).update(fire_at=timezone.now() - timedelta(minutes=1), sent_at=None); send_reminders()"` | No traceback. |
| 4 | `/__e2e__/mail/?to=<MAIL_USER>` (reload until it arrives). | Subject exactly `Reminder: Call <b>the</b> bank & co` (the owner's own title). |
| 5 | Text body. | `Reminder: Call <b>the</b> bank & co`, then `Due D Mon YYYY, HH:MM (<zone>)` (the date and time you set, e.g. `Due 1 Nov 2026, 19:30 (Europe/Lisbon)`; `<zone>` is the zone saved on your account, `UTC` if none), the description as typed (`**Bring** the papers <script>alert(1)</script>`), `Open the task: <BASE>/todo/<T_MAIL>/`, and "You set this reminder on "Call <b>the</b> bank & co". Change or remove it on the task page (`<BASE>/todo/<T_MAIL>/`); your default for new tasks is in To-do settings (`<BASE>/todo/settings/`)." |
| 6 | HTML source. | The title as `Call &lt;b&gt;the&lt;/b&gt; bank &amp; co` (twice); the description rendered: `<strong>Bring</strong>` as a real tag and `&lt;script&gt;alert(1)&lt;/script&gt;` escaped; `<a href="<BASE>/todo/<T_MAIL>/">Open the task</a>` and a "To-do settings" link to `<BASE>/todo/settings/`. A raw `<script>` or `<b>the</b>` is a **FAIL**. |
| 7 | Click "Open the task", then the settings link. | `/todo/<T_MAIL>/`, whose **History** lists the delivery with "Email"; then `/todo/settings/`. |
| 8 | (shell step) Run `python tests_e2e/ai/manage.py shell -c "from apps.todo.jobs import send_reminders; send_reminders()"` again; wait a minute. | No second reminder email (a reminder is delivered once). |

## Cross-checks

### MAIL-23 Every email of the run follows the rules

**Purpose:** a final sweep over everything this file made the site send.
**Preconditions:** MAIL-01 to MAIL-22 have run.

| # | Action | Expected result |
|---|---|---|
| 1 | Open `/__e2e__/mail/` (no filter) and go through every email from this file's addresses. | Every link in every text and HTML body starts with `<BASE>` (no `http://localhost:8000`, no `example.com` link that the site made, no relative `/...` link). Every link opened during this file landed on the page described. |
| 2 | Same list. | No email contains `None`, `undefined`, `{{`, `{%` or an empty greeting such as `Hi ,`. Each has a non-empty subject and text body. |
| 3 | Same list, subjects only. | No subject contains text a stranger chose (a feedback message, `Someone <i>Else</i>`, `Invoice <b>October</b>`, a takedown reason); the only free text in subjects is a transfer's file name and a to-do title, both the owner's own. |
| 4 | Same list, HTML parts. | Wherever free text appears in an HTML part it is escaped (`&lt;`, `&amp;`); the emails without an HTML part are exactly the file transfer ones and the admin test email. |

## Out of scope

- The Google and Microsoft wording of `You already have a ZipTrigo account` (an account created by
  "Sign in with Google/Microsoft" with no password): third-party sign-in is not available on the
  test server. Unit tests cover the wording.
- Delivery failures (every backend failing, a queue failure) and the retries they cause: they need a
  broken mail server. The per-address email budgets (`SIGNUP_EXISTING_EMAIL`, the email
  verification daily cap) need rate limiting, which is off on this server.
- The "files deleted" email for a transfer ended at its download limit (sent by the deferred-deletion
  sweep after the presigned URL lifetime): the same template as MAIL-14 step 5, covered by unit tests.
- Inbound email through SES and the To-do email intake (they send no email of their own; To-do's
  credit refusal is an in-app notice). MAIL-19 feeds Receipts an email through
  `receipts_ingest_eml` instead.
- The JWT API and the CLIs as products (MAIL-18 only uses `PUT /api/account` to trigger emails).
