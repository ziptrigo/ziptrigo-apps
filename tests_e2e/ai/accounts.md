# Accounts: register, confirm, sign in, reset, account page

## Scope

The `apps/accounts` web UI, all under `/account/` unless noted:

- **Register** (`/account/register/`): validation, the neutral "Check your email" page
  (`/account/created/`), the confirmation email, the two-step confirmation link
  (`/account/confirm-email/<token>/`: GET shows a button, only the POST confirms), the expired-link
  page with its resend form (`/account/email-confirmation-expired/`, posting to
  `/account/resend-confirmation/`).
- **No account enumeration**: registering an address that already has an account answers exactly
  like a new one; the owner is told by email. An *unconfirmed* account is locked down instead and
  its owner gets a "Finish setting up" set-password link.
- **Sign in / sign out** (`/account/login/`, `POST /account/logout/`; a GET of the logout URL only redirects to the login page): error messages, `next=`,
  mixed-case email domains, Google and Microsoft sign-in hidden when not configured.
- **Forgot / reset password** (`/account/forgot-password/`, `/account/reset-password/<token>/`):
  a reset confirms an unconfirmed address and retires its pending confirmation links; a link works
  once, and **any** password change (another link, the account page) retires every link issued
  before it; a link for an address the account has since left is refused.
- **Account page** (`/account/`): profile name (the header menu follows a rename at once), the
  time zone select (`Not set (UTC)`, the browser's detection, the "Use it" hint), theme
  (Appearance, applied without a reload), the promo code form's presence, change password (every
  refusal, Django's validators), the "password changed" email and its forgot-password link, other
  sessions signed out; what persists on the account across a new session and another browser.
- **No self-service deletion**: there is no page or control to delete one's own account; a user is
  deleted only from the admin (`admin.md` owns the admin side), after which they cannot sign in.
- Password rules (6 to 128 characters, at least one digit; Django's too-common / numeric /
  too-similar-to-name-or-email checks on the account page), the escaping of the free-text name in
  emails, and the accessibility and htmx behaviour of every form above.

See "Out of scope" at the end of the file.

## Preconditions

- The AI test server is running at `http://localhost:8765` (`python tests_e2e/ai/serve.py`, see
  `README.md`), ideally freshly restarted. Below, paths like `/account/login/` are relative to it.
- The server runs on a fresh Postgres database and sends email over real SMTP. The mailbox is at
  `/__e2e__/mail/` (newest first; `?to=<address>` filters by the address the site sent it to).
  **Delivery takes a few seconds**: "the mailbox for X has ..." always means "open
  `/__e2e__/mail/?to=<X>` and reload until it arrives, up to a minute". A check that an email did
  **not** arrive (`No emails.`, "still only one", "no new email") passes only after waiting at
  least 30 seconds and reloading. Links in an email are real absolute `http://localhost:8765/...`
  links and clickable there; the HTML body is shown as source under "HTML body (source)". Open the
  mailbox in a separate tab so the page under test keeps its state. For every email below, check
  the subject, the content listed and that each link in it lands where the case says.
- Seeded accounts (password `QaE2e-Passw0rd!` for all): `qa.user@example.com` (confirmed, name
  "QA User", 100 credits), `qa.admin@example.com` (superuser; used only to sign in to `/admin/`
  in ACC-41 and ACC-44), `qa.unconfirmed@example.com` (not confirmed, name "QA Unconfirmed").
- **Welcome credits:** an account registered here and then confirmed gets 1,000 credits (one
  ledger row "A gift for your new account", a bell notification `Welcome! We added 1000 credits to
  your account`), so MAIN's balance is **1000** from ACC-10 on. A confirmation by a reset link
  grants them too (UNC in ACC-40). The seeded accounts were created with the welcome credits
  switched off, so they never get them: `qa.unconfirmed@example.com` still has **0** credits after
  ACC-35 confirms it.
- **Never change the password of `qa.user@example.com` or `qa.admin@example.com`**: other test
  files use them. Password changes and resets in this file use fresh accounts.
  `qa.unconfirmed@example.com` is locked down and given a new password near the end (ACC-34/35);
  nothing else uses it.
- Some cases need a **second, isolated browser context** ("Context B": an incognito/private window,
  a second browser profile, or a new isolated context in your automation tool) so two sessions can
  be signed in at once. "Context A" is the normal one. If you cannot open one, mark those steps
  SKIPPED and say so. ACC-41 and ACC-44 use a fresh Context B to sign in to the admin as
  `qa.admin@example.com` while Context A holds the user's session.
- The time zone detection script sends the browser's zone at most **once per tab**
  (`sessionStorage` key `tzSent`). ACC-42 sets and clears that key from the developer console
  (`sessionStorage.setItem('tzSent', '1')`, `sessionStorage.removeItem('tzSent')`); if your tool
  can't run page JavaScript, mark those steps SKIPPED.
- The cases run **in order**; later ones use accounts and state from earlier ones.

## Test data

At the start of the run pick a short random token `<run>` (6 lowercase letters/digits, e.g.
`k7q2m9`) and use it in every address below, so a rerun against the same database never collides.

| Name | Value |
|---|---|
| MAIN | `qa.acc.<run>.main@example.com`, name `Acc Main <run>` |
| RESEND | `qa.acc.<run>.resend@example.com`, name `Acc Resend <run>` |
| NOBODY | `qa.acc.<run>.nobody@example.com` (never registered) |
| P1 | `AccMain-4821` (MAIN's password at registration) |
| P2 | `AccMain-5932` (MAIN's password after ACC-24) |
| P3 | `AccReset-7310` (MAIN's password after ACC-30) |
| P-RESEND | `AccResend-3307` |
| P-FINISH | `AccFinish-6044` (`qa.unconfirmed@example.com`'s password after ACC-35) |
| P4 | `AccMain-6175` (MAIN's password after ACC-39's reset) |
| P5 | `AccMain-7286` (MAIN's password from the end of ACC-39 on) |
| UNC | `qa.acc.<run>.unc@example.com`, name `Acc <b>Unc</b> <run>` (typed literally, tags included), password P-UNC `AccUnc-4410` |
| MOVED | `qa.acc.<run>.moved@example.com` (UNC's address after ACC-41) |
| TZ | `qa.acc.<run>.tz@example.com`, name `Acc Tz <run>`, password P-TZ `AccTz-2208` |
| DEL | `qa.acc.<run>.del@example.com`, name `Acc Del <run>`, password P-DEL `AccDel-9051` |
| ADMIN | `qa.admin@example.com` / SEED (admin sign-in only; never change it) |
| SEED | `QaE2e-Passw0rd!` (seeded accounts) |
| LONG256 | the letter `N` repeated 256 times |

Error lists on the register, login, forgot-password, resend and reset forms are one red block
**above** the fields, one line per error, written `<Field label>: <message>` for a field error and
just `<message>` for a form-wide one (e.g. `Invalid credentials`). The account page's two forms show
errors in a red box above the fields, *without* the label prefix. **In addition, every field error
is repeated under the field that failed** (small red text with an icon, just below the field or its
wrapper), the field gets a red border and `aria-invalid="true"`, and the message is linked to it with
`aria-describedby`; the first invalid field takes keyboard focus after the swap, and a field's
message disappears as soon as you edit that field. Form-wide errors (`Invalid credentials`) appear
only in the block. Checks below that mention "the red block" mean the block; look under the field
too when the line has a field label.

---

### ACC-01 Login page layout and accessibility

**Purpose:** the sign-in page renders fully, with usable labels and fields.
**Preconditions:** signed out (if the header shows the account icon, sign out via the menu first).

| # | Action | Expected result |
|---|---|---|
| 1 | Go to `/account/login/`. | Tab title `Login - ZipTrigo`. A card with the logo and heading `Sign in`. Header shows a `Login` link (not the account icon). No raw `{{ }}`/`{% %}`, no console errors. |
| 2 | Inspect the form. | Two fields: `Email` (`type="email"`, `name="email"`) and `Password` (`type="password"`, `name="password"`), both `required`; a full-width `Sign in` button. The `Email` label wraps its input and the `Password` label is tied to its input (`for`), so clicking the word `Email` or `Password` focuses that field. The logo is decorative (empty `alt`). |
| 3 | Look below the button. | `No account? Create one` (link to `/account/register/`) and `Forgot your password?` (link to `/account/forgot-password/`). No `or` separator and no `Sign in with Google` button (Google is not configured here). |
| 4 | Type `abc` into Password, then click the eye button inside the field (`aria-label="Show password"`, `aria-controls="password-input"`, `aria-pressed="false"`; it sits next to the label, not inside it, so the field's accessible name is just `Password`). | The password becomes readable (`type="text"`), the icon changes to a crossed eye and `aria-pressed` becomes `true`. Click again: hidden again, plain eye icon, `aria-pressed="false"`. |
| 5 | Note focus and autocomplete. | Record (observation, not a failure): whether any field has focus on load (`autofocus`) and whether the inputs carry `autocomplete` hints (`email`/`username`, `current-password`). |

### ACC-02 Register page layout

**Purpose:** the register page renders fully, without captcha or Google on this server.
**Preconditions:** signed out.

| # | Action | Expected result |
|---|---|---|
| 1 | From the login page click `Create one`. | URL `/account/register/`, tab title `Register`, heading `Create account`. |
| 2 | Inspect the form. | Fields `Name` (text, `name="name"`), `Email` (`type="email"`), `Password` (`type="password"`, `minlength="6"`), all `required`. Under Password the hint `At least 6 characters with 1 digit`; then an optional `Promo code` field (upper-cased as you type, hint `Its credits are added once you confirm your email.`). Button `Create account`. |
| 3 | Look for a captcha, Google and Microsoft. | No Cloudflare Turnstile widget and no `Sign up with Google` or `Sign up with Microsoft` button. |
| 4 | Look below the form. | `Already have an account? Sign in`, linking to `/account/login/`. |

### ACC-03 Register: required fields

**Purpose:** blank and whitespace-only input is refused.
**Preconditions:** on `/account/register/`.

| # | Action | Expected result |
|---|---|---|
| 1 | Click `Create account` with every field empty. | The browser's own "required" validation stops the submit (a tooltip on the first empty field); the URL stays `/account/register/`; no request reaches the server, nothing changes on the page. |
| 2 | Name: three spaces `   `. Email: MAIN. Password: P1. Click `Create account`. | The page does **not** reload (htmx swap; URL unchanged). A red error block above the fields reads `Name: This field is required.`, the same message `This field is required.` appears under the Name field, the field has a red border and keyboard focus. No email for MAIN in the mailbox. |
| 3 | Check the fields after the error. | Email still holds MAIN; Password is empty (passwords are never echoed back). |

### ACC-04 Register: invalid email and weak password

**Purpose:** server-side validation of email and password, and typed values survive a 422.
**Preconditions:** on `/account/register/`.

| # | Action | Expected result |
|---|---|---|
| 1 | Name `Acc Main <run>`, Email `foo@bar` (the browser accepts it), Password `abc12`. | The browser blocks the submit because Password is shorter than 6 (`minlength`). |
| 2 | Change Password to `abcdefg` (7 letters, no digit). Submit. | Red block, two lines: `Email: Enter a valid email address.` and `Password: Password must contain at least one digit.` URL unchanged, no full reload. |
| 3 | Check the fields. | Name still `Acc Main <run>`, Email still `foo@bar`, Password empty. |
| 4 | Email MAIN, Password `abcdefg`. Submit. | Only `Password: Password must contain at least one digit.` |
| 5 | Open `/__e2e__/mail/?to=<MAIN>`. | `No emails.` (nothing was created). |
| 6 | Back on the form, Email MAIN, Password `a1` repeated 65 times (130 characters; paste it). Submit. | `Password: Password must be at most 128 characters long.` (and the same text under the Password field). |

### ACC-05 Register: very long name

**Purpose:** a name over 255 characters is refused with a clear message.
**Preconditions:** on `/account/register/`.

| # | Action | Expected result |
|---|---|---|
| 1 | Name LONG256 (paste or type 256 `N`s; the field has no length cap), Email MAIN, Password P1. Submit. | Red block: `Name: Ensure this value has at most 255 characters (it has 256).` |
| 2 | Check the layout. | The long name in the input doesn't stretch the card or cause horizontal page scroll; the error line wraps inside the card. |
| 3 | Mailbox for MAIN. | Still `No emails.` |

### ACC-06 Register a new account

**Purpose:** a valid signup lands on the neutral "Check your email" page.
**Preconditions:** on `/account/register/`.

| # | Action | Expected result |
|---|---|---|
| 1 | Name `Acc Main <run>`, Email MAIN, Password P1. Click `Create account`. | While the request runs the button may briefly show a spinner and `Creating...` and be disabled. Then the browser goes to `/account/created/`. |
| 2 | Read the page. | Tab title `Check your email - ZipTrigo`, heading `Check your email`. Text exactly: `We've sent you an email with what to do next. If this is a new account, follow the link in it to confirm your address before logging in.` and `A confirmation link expires in 48 hours.` Button `Go to Login`. The wording must not say "account created" or reveal anything about the address. Save this page's full text for comparison in ACC-33. |
| 3 | Header. | Still shows `Login` (signup does not sign you in). |
| 4 | Click `Go to Login`. | Goes to `/account/login/`. |

### ACC-07 Confirmation email

**Purpose:** the signup sends one confirmation email with a working link.
**Preconditions:** ACC-06 done.

| # | Action | Expected result |
|---|---|---|
| 1 | Open `/__e2e__/mail/?to=<MAIN>`. | Exactly one email. Subject `Confirm your ZipTrigo account email`. |
| 2 | Read the body. | Starts `Hi Acc Main <run>,`; says `Please confirm the email address of your ZipTrigo account. Open the link below and press "Confirm my email":`; then a link `http://localhost:8765/account/confirm-email/<token>/`; `This link will expire in 48 hours.`; `If you did not expect this email, please ignore it.`; signed `The ZipTrigo Team`. The HTML body (source) has the same text and the link as an `<a href>` to the same URL. Copy the link (call it CONFIRM-1); ACC-09 opens it. |

### ACC-08 Sign-in is refused before confirmation

**Purpose:** an unconfirmed account cannot sign in, with the documented message.
**Preconditions:** ACC-07; MAIN not yet confirmed.

| # | Action | Expected result |
|---|---|---|
| 1 | On `/account/login/` enter MAIN and P1, click `Sign in`. | No reload; red block: `Please confirm your email address before logging in. Check your inbox for the confirmation link.` Email field keeps MAIN; Password is empty. Header still `Login`. |
| 2 | Look for a way to get a new link from here. | Record (observation): whether the page offers a resend link. (It currently does not; the resend form is only on the expired-link page.) |

### ACC-09 Opening the confirmation link does not confirm by itself

**Purpose:** a GET of the link (what mail scanners do) changes nothing; only the button confirms.
**Preconditions:** ACC-07.

| # | Action | Expected result |
|---|---|---|
| 1 | Open CONFIRM-1 (click it in the mailbox). | Page `Confirm your email` (tab title too), text `Press the button below to confirm your email address.` and a `Confirm my email` button. No "confirmed" message yet. |
| 2 | Without pressing the button, open `/account/login/` in a new tab and sign in with MAIN / P1. | Still refused with `Please confirm your email address before logging in. ...` (the GET confirmed nothing). |

### ACC-10 Confirm the email with the button

**Purpose:** the POST confirms and shows the success page; sign-in then works.
**Preconditions:** the CONFIRM-1 tab from ACC-09.

| # | Action | Expected result |
|---|---|---|
| 1 | Press `Confirm my email`. | Redirected to `/account/email-confirmed/`: heading `Email Confirmed!`, text `Your email address has been successfully confirmed. You can now log in to your account.`, button `Go to Login`. |
| 2 | Refresh the page. | The same success page (no "resubmit form" prompt: it was post/redirect/get). |
| 3 | Click `Go to Login`. | `/account/login/`. |

### ACC-11 Used and invalid confirmation links

**Purpose:** a link already used within its validity still answers success (idempotent by design);
a made-up token lands on the expired page with a resend form.
**Preconditions:** ACC-10.

| # | Action | Expected result |
|---|---|---|
| 1 | Open CONFIRM-1 again and press `Confirm my email`. | `/account/email-confirmed/` again (documented: a link its own token confirmed stays valid until it expires). Not an error page. |
| 2 | Open `/account/confirm-email/not-a-real-token/`. | The same `Confirm your email` page with the button (the GET never reads the token). |
| 3 | Press `Confirm my email`. | Redirected to `/account/email-confirmation-expired/`: tab title and red heading `Link Expired`, text `This email confirmation link has expired or is invalid. Enter your email below to get a new one.`, an `Email` field, a `Resend confirmation email` button and a `Go to Home` button. |
| 4 | Click `Go to Home`. | Goes to `/`. |

### ACC-12 Sign in after confirmation

**Purpose:** a confirmed account signs in and lands on the home page.
**Preconditions:** ACC-10.

| # | Action | Expected result |
|---|---|---|
| 1 | Open `/account/login/`, enter MAIN and P1, click `Sign in`. | Button shows `Signing in...` briefly; then the browser goes to `/` (the home page; there is no `next`). |
| 2 | Header. | The `Login` link is replaced by an account icon button (`aria-label="Account menu"`). |
| 3 | Click the account icon. | A menu opens showing the name `Acc Main <run>`, `Settings` (link to `/account/`) and `Logout`. Press Escape: the menu closes and focus returns to the icon. Click outside after reopening: it closes. |
| 4 | While signed in, open `/account/login/`. | Record (observation): the sign-in form is shown again rather than redirecting a signed-in user. Not a failure. |

### ACC-13 Sign out is POST-only

**Purpose:** logout works from the menu; a plain GET of the logout URL signs nobody out.
**Preconditions:** signed in as MAIN (ACC-12).

| # | Action | Expected result |
|---|---|---|
| 1 | Navigate directly to `/account/logout/` (address bar, a GET). | Redirected to `/account/login/` (no error page). You are **not** signed out: the header shows the account icon and the login form is shown again (as in ACC-12 step 4). |
| 2 | Go to `/account/`. | The account page loads (still signed in). |
| 3 | Open the account menu and click `Logout`. | Redirected to `/` (the home page); header shows `Login`. |
| 4 | Go to `/account/`. | Redirected to `/account/login/?next=/account/`. |

### ACC-14 Wrong password and unknown email look the same

**Purpose:** the login error never says whether the address has an account.
**Preconditions:** signed out.

| # | Action | Expected result |
|---|---|---|
| 1 | On `/account/login/` enter MAIN and `Wrong-0000`. Sign in. | Red block: `Invalid credentials`. Email kept, password cleared, URL unchanged, no full reload. |
| 2 | Enter NOBODY and `Wrong-0000`. Sign in. | Exactly the same: `Invalid credentials`. No other wording difference anywhere on the page. |
| 3 | Enter `foo@bar` and any password. | `Email: Enter a valid email address.` |
| 4 | Press Enter in the Password field (instead of clicking) with MAIN / `Wrong-0000`. | The form submits the same way (`Invalid credentials`). |

### ACC-15 `next=` to a page on this site is honoured

**Purpose:** after sign-in the user goes back to where they were heading.
**Preconditions:** signed out.

| # | Action | Expected result |
|---|---|---|
| 1 | Go to `/receipts/`. | Redirected to `/account/login/?next=/receipts/`. |
| 2 | Sign in with MAIN / P1. | Lands on `/receipts/` (the Receipts dashboard), not `/account/`. |
| 3 | Sign out (menu > `Logout`). Go to `/account/login/?next=/qr/`. Fail once with a wrong password, then sign in with P1. | After the failed attempt the URL still has `?next=/qr/` (the hidden `next` survives the htmx swap); after success you land on `/qr/`. |
| 4 | Sign out. | Header shows `Login`. |

### ACC-16 `next=` to another site is ignored

**Purpose:** no open redirect.
**Preconditions:** signed out.

| # | Action | Expected result |
|---|---|---|
| 1 | Go to `/account/login/?next=https://evil.example.com/` and sign in with MAIN / P1. | Lands on `/` (the home page), never on `evil.example.com`. |
| 2 | Sign out. Go to `/account/login/?next=//evil.example.com/x` and sign in. | Lands on `/`. |
| 3 | Sign out. | Header shows `Login`. |

### ACC-17 Mixed-case email at sign-in

**Purpose:** the domain part of the email is case-insensitive at login; the local part is exact.
**Preconditions:** signed out. Uses `qa.user@example.com` read-only (no password change).

| # | Action | Expected result |
|---|---|---|
| 1 | Sign in with `qa.user@EXAMPLE.COM` and SEED. | Signed in; lands on `/` (the landing page). Opening `/account/` shows the Email field as `qa.user@example.com`. |
| 2 | Sign out. Sign in with `QA.User@example.com` and SEED. | `Invalid credentials` (the part before `@` is matched exactly, by design). |
| 3 | Sign in with `qa.acc.<run>.main@Example.Com` and P1. | Signed in as MAIN. Then sign out. |

### ACC-18 Google and Microsoft sign-in are hidden when not configured

**Purpose:** with no Google or Microsoft client configured, nothing offers them and their URLs fail gracefully.
**Preconditions:** signed out.

| # | Action | Expected result |
|---|---|---|
| 1 | Check `/account/login/` and `/account/register/`. | No `Sign in with Google` / `Sign up with Google` / `Sign in with Microsoft` / `Sign up with Microsoft` button and no `or` divider on either. |
| 2 | Navigate to `/auth/google/login`. | Redirected to `/account/login/` with a red banner at the top: `Google sign-in is not available right now.` |
| 3 | Reload the login page. | The banner is gone (shown once). |
| 4 | Navigate to `/auth/microsoft/login`. | Redirected to `/account/login/` with a red banner at the top: `Microsoft sign-in is not available right now.` |
| 5 | Reload the login page. | The banner is gone (shown once). |

### ACC-19 Account page contents

**Purpose:** the account page shows the right sections for a password account.
**Preconditions:** sign in as MAIN / P1 (Context A).

| # | Action | Expected result |
|---|---|---|
| 1 | Go to `/account/`. | Tab title and heading `Account Settings`. Sections in order: `Profile Information`, `Credits`, `Appearance`, `Change Password`, then a `Back to Home` link. |
| 2 | Profile section. | `Email` shows MAIN in a greyed, disabled field (not editable) with the hint `Email cannot be changed`; `Name` holds `Acc Main <run>`; button `Save Profile`. |
| 3 | Credits section. | `Current balance` then `1000 credits` (the welcome credits); a `Usage History` link to `/billing/credits/`; below it the "Have a promo code?" form. (A balance of exactly 1 reads `1 credit`, 0 and 2+ read `credits`; the seeded `qa.broke@example.com` shows `0 credits`.) |
| 4 | Appearance section. | A `Theme` group with three options, `System` (selected), `Light` and `Dark`, and a hint that System follows the device's setting. No Save button (see ACC-37). |
| 5 | Change Password section. | Fields `Current Password`, `New Password` (hint `6 to 128 characters with at least one digit`), `Confirm New Password`, all `type="password"`, each with its own eye toggle (outside the label; `aria-label="Show password"`, `aria-pressed="false"`); button `Change Password`. No `You signed in with Google` note. |
| 6 | Click each eye toggle. | Each reveals only its own field, flips its own icon and sets its own `aria-pressed="true"`. |
| 7 | Click `Back to Home`. | Goes to `/`. |

### ACC-20 Update the profile name

**Purpose:** the name can be changed and is saved.
**Preconditions:** signed in as MAIN, on `/account/`.

| # | Action | Expected result |
|---|---|---|
| 1 | Change Name to `Acc Main <run> Renamed`, click `Save Profile`. | No full reload; a green message `Profile updated successfully.` above the fields; Name shows the new value. |
| 2 | Open the account menu (before reloading). | Its first line already shows `Acc Main <run> Renamed`: the save's response updates the menu out of band, no reload needed. |
| 3 | Reload `/account/`. | Name field and the account menu both show `Acc Main <run> Renamed`; the green message is gone. |

### ACC-21 Profile name validation

**Purpose:** an empty name is refused and the typed value is kept.
**Preconditions:** on `/account/` as MAIN.

| # | Action | Expected result |
|---|---|---|
| 1 | Clear Name entirely and click `Save Profile`. | The browser blocks it (`required`). |
| 2 | Set Name to three spaces and save. | Red box: `Please fill in your name.` The page isn't reloaded. |
| 3 | Try to type 300 characters into Name. | The field stops accepting input at 255 characters (`maxlength="255"`). |
| 4 | Set Name back to `Acc Main <run>` and save. | `Profile updated successfully.` |

### ACC-22 Change password: wrong current, mismatch, unchanged

**Purpose:** the change-password form's own rules.
**Preconditions:** on `/account/` as MAIN (password P1).

| # | Action | Expected result |
|---|---|---|
| 1 | Current `Wrong-0000`, New P2, Confirm P2. Click `Change Password`. | Red box: `Your current password is incorrect.` No reload. All three fields are empty afterwards. |
| 2 | Current P1, New P2, Confirm `AccMain-9999`. | `Passwords do not match.` |
| 3 | Current P1, New P1, Confirm P1. | `The new password must be different from the current one.` |
| 4 | Mailbox for MAIN. | No `Your ZipTrigo password was changed` email yet. |

### ACC-23 Change password: strength rules

**Purpose:** the new password goes through the project's rule and Django's validators.
**Preconditions:** on `/account/` as MAIN (password P1). Current P1 in every step; New and Confirm
equal to the value given.

| # | Action | Expected result |
|---|---|---|
| 1 | New `password1`. | `This password is too common.` |
| 2 | New `83920571`. | `This password is entirely numeric.` |
| 3 | New `12345678`. | Two lines: `This password is too common.` and `This password is entirely numeric.` |
| 4 | New `abcdefgh`. | `Password must contain at least one digit.` |
| 5 | New `examplecom1`. | `The password is too similar to the email.` |
| 6 | New `abc12` (5 characters). | The browser blocks it (`minlength="6"`). |
| 7 | New `accmain<run>1` (the name is `Acc Main <run>`). | `The password is too similar to the name.` (the name is compared too, not only the email). |
| 8 | New `a1` repeated 65 times (130 characters; paste it), Confirm the same. | `Password must be at most 128 characters long.` Each message above also shows under the New Password field. |
| 9 | After all of these, sign-in still uses P1. | (Checked implicitly in ACC-24: the current password P1 is still accepted.) |

### ACC-24 Change password successfully

**Purpose:** a valid change keeps this session signed in and emails the owner.
**Preconditions:** on `/account/` as MAIN (P1) in Context A.

| # | Action | Expected result |
|---|---|---|
| 1 | In **Context B**, sign in with MAIN / P1 and open `/account/`. | The account page loads in Context B. Leave it open. |
| 2 | In Context A: Current P1, New P2, Confirm P2. Click `Change Password`. | Button shows `Changing...` briefly. Then a green message: `Password changed. We've sent a confirmation to qa.acc.<run>.main@example.com.` The three fields are empty. |
| 3 | In Context A, reload `/account/` and open another page (e.g. `/qr/`). | Still signed in (no redirect to login). |
| 4 | Mailbox for MAIN. | Newest: subject `Your ZipTrigo password was changed`; body `Hi Acc Main <run>,`, `The password for your ZipTrigo account (qa.acc.<run>.main@example.com) was changed on <YYYY-MM-DD> at <HH:MM> UTC.`, and a link `http://localhost:8765/account/forgot-password/` to reset it if it wasn't you. |
| 5 | Click that link in the email. | It opens the `Forgot your password?` page (`/account/forgot-password/`, Email field and `Send reset link`). Don't submit it. |

### ACC-25 Other sessions are signed out; old password stops working

**Purpose:** a password change ends the user's other sessions.
**Preconditions:** ACC-24.

| # | Action | Expected result |
|---|---|---|
| 1 | In Context B, reload `/account/`. | Redirected to `/account/login/?next=/account/` (that session was signed out). |
| 2 | In Context B, sign in with MAIN / P1. | `Invalid credentials`. |
| 3 | In Context B, sign in with MAIN / P2. | Signed in, lands on `/`. Leave Context B signed in for ACC-30. |
| 4 | In Context A, sign out. | Header shows `Login`. |

### ACC-26 Forgot-password page

**Purpose:** the page renders and navigates.
**Preconditions:** signed out in Context A.

| # | Action | Expected result |
|---|---|---|
| 1 | On `/account/login/` click `Forgot your password?`. | `/account/forgot-password/`, tab title `Forgot password`, heading `Forgot your password?`, an `Email` field (`type="email"`, required), a `Cancel` link and a `Send reset link` button. |
| 2 | Click `Cancel`. | Back on `/account/login/`. |

### ACC-27 Forgot password: the same answer for every address

**Purpose:** the response never reveals whether an address has an account; a mixed-case domain
still finds the account.
**Preconditions:** on `/account/forgot-password/`.

| # | Action | Expected result |
|---|---|---|
| 1 | Email `foo@bar`, click `Send reset link`. | Red block: `Email: Enter a valid email address.` The field keeps `foo@bar`. |
| 2 | Email NOBODY, submit. | Green message: `If the account exists, an email will be sent with a password reset link.` The Email field is emptied. URL unchanged. |
| 3 | Email `qa.acc.<run>.main@EXAMPLE.COM`, submit. | Exactly the same green message and empty field; nothing on the page differs from step 2. |
| 4 | Mailbox `?to=<NOBODY>`. | `No emails.` |
| 5 | Mailbox `?to=<MAIN>` (lower case). | Newest: subject `Reset your ZipTrigo account password`. Exactly one such email. |

### ACC-28 Password reset email

**Purpose:** the reset email has the right content and link.
**Preconditions:** ACC-27.

| # | Action | Expected result |
|---|---|---|
| 1 | Read the newest MAIN email. | `Hi Acc Main <run>,`, `You requested to reset your password. Click the link below to set a new password:`, a link `http://localhost:8765/account/reset-password/<token>/`, `This link will expire in 4 hours.`, `If you did not request this, please ignore this email.` Copy the link (RESET-1). |
| 2 | Open RESET-1. | Tab title `Reset password`, heading `Set a new password`, fields `New password` and `Confirm password` (both `type="password"`, `minlength="6"`), button `Save new password`. |

### ACC-29 Reset page validation

**Purpose:** the reset form refuses mismatched and weak passwords.
**Preconditions:** on RESET-1.

| # | Action | Expected result |
|---|---|---|
| 1 | New P3, Confirm `AccReset-0000`. Save. | Red block: `Confirm password: Passwords do not match.` Both fields empty; URL unchanged. |
| 2 | New `abcdefgh`, Confirm `abcdefgh`. | `Password: Password must contain at least one digit.` |
| 3 | New `abc12`. | Blocked by the browser (`minlength`). |
| 4 | Check that the link still works. | The page is still the reset form (not `Link Expired`): failed attempts don't use up the link. |

### ACC-30 Reset the password

**Purpose:** a reset sets the new password, signs out other sessions and retires the old one.
**Preconditions:** on RESET-1; Context B signed in as MAIN from ACC-25.

| # | Action | Expected result |
|---|---|---|
| 1 | New P3, Confirm P3, click `Save new password`. | Redirected to `/account/login/` with a green banner `Your password is set. You can now log in.` |
| 2 | Sign in with MAIN / P2. | `Invalid credentials`. |
| 3 | Sign in with MAIN / P3. | Signed in, lands on `/`. Then sign out. |
| 4 | In Context B, reload `/account/`. | Redirected to `/account/login/?next=/account/` (the reset ended that session). Close Context B. |

### ACC-31 A reset link works only once

**Purpose:** a used or made-up reset link shows the expired page.
**Preconditions:** ACC-30, signed out.

| # | Action | Expected result |
|---|---|---|
| 1 | Open RESET-1 again. | Tab title and red heading `Link Expired`, text `This password reset link has expired or is invalid. Please request a new one.`, button `Request New Link`. No password form. |
| 2 | Click `Request New Link`. | Goes to `/account/forgot-password/`. |
| 3 | Open `/account/reset-password/not-a-real-token/`. | The same `Link Expired` page. |
| 4 | Sign in with MAIN / P3. | Still works (the failed link changed nothing). Sign out. |

### ACC-32 Resend confirmation

**Purpose:** the resend form gives the same answer for every address, respects the 60-second
cooldown, and a new link supersedes the old one.
**Preconditions:** signed out. This case includes a 65-second wait.

| # | Action | Expected result |
|---|---|---|
| 1 | Register RESEND (name `Acc Resend <run>`, password P-RESEND). | `/account/created/`. Mailbox `?to=<RESEND>` has one `Confirm your ZipTrigo account email` (link CONFIRM-R1). |
| 2 | Within 60 seconds of step 1, open `/account/email-confirmation-expired/`, enter RESEND, click `Resend confirmation email`. | Green message starting `If the account exists and is not yet confirmed, we'll send a confirmation email.` and saying to wait a minute after a recent request. Field emptied, no reload. |
| 3 | Mailbox for RESEND. | Still only **one** email (a resend inside the cooldown is skipped, which the message's "wait a minute" covers). |
| 4 | Enter `foo@bar`, submit. | `Email: Enter a valid email address.` |
| 5 | Enter NOBODY, submit. | The same green message as step 2. Mailbox for NOBODY: `No emails.` |
| 6 | Enter MAIN (already confirmed), submit. | The same green message. No new email for MAIN. |
| 7 | Wait 65 seconds after step 1. Enter `qa.acc.<run>.resend@EXAMPLE.COM`, submit. | The same green message. |
| 8 | Mailbox for RESEND. | Now two emails; the newest has a different link (CONFIRM-R2). |
| 9 | Open CONFIRM-R1 and press `Confirm my email`. | `Link Expired` page (`/account/email-confirmation-expired/`): the resend superseded it. |
| 10 | Open CONFIRM-R2 and press `Confirm my email`. | `Email Confirmed!`. |
| 11 | Sign in with RESEND / P-RESEND. | Lands on `/` (the home page). Sign out. |
| 12 | Navigate (GET) to `/account/resend-confirmation/`. | HTTP 405, blank page (the form only lives on the expired-link page). Note as observation. |

### ACC-33 No enumeration: signing up with an address that has an account

**Purpose:** registering an existing confirmed address looks exactly like a new signup; the
existing owner gets a notice and their account is untouched.
**Preconditions:** signed out. Uses `qa.user@example.com` without changing it.

| # | Action | Expected result |
|---|---|---|
| 1 | On `/account/register/`: Name `Someone Else`, Email `qa.user@example.com`, Password `Other-9876`. Submit. | Redirected to `/account/created/`. The page text is **identical** to the one saved in ACC-06 (same heading, sentences, `48 hours`, button). No error such as "already registered". |
| 2 | Mailbox `?to=qa.user@example.com`. | Newest: subject `You already have a ZipTrigo account`. Body: `Hi QA User,` (the account's own name, not `Someone Else`), `Someone tried to create a ZipTrigo account with this email address (qa.user@example.com), but you already have one.`, a sign-in link `http://localhost:8765/account/login/`, a reset link `http://localhost:8765/account/forgot-password/`, and `If it wasn't you, you can ignore this email: nothing about your account was changed.` No `Confirm your ZipTrigo account email` was sent to it. |
| 3 | Sign in with `qa.user@example.com` / `Other-9876`. | `Invalid credentials` (the typed password was not stored). |
| 4 | Sign in with `qa.user@example.com` / SEED. | Signed in; Name on `/account/` is still `QA User`. Sign out. |
| 5 | Register again with Email `QA.User@EXAMPLE.com` (any valid name/password). | The same `/account/created/` page. Mailbox for `qa.user@example.com` gets another `You already have a ZipTrigo account` notice (different case is the same account; no second account was created). |
| 6 | Register MAIN again with password `Other-9876`. | The same `/account/created/` page; MAIN receives a `You already have a ZipTrigo account` notice; MAIN / P3 still signs in, `Other-9876` does not. Sign out. |
| 7 | In MAIN's newest `You already have a ZipTrigo account` email, click the sign-in link, then go back and click the reset link. | The first opens `/account/login/` (the `Sign in` page), the second `/account/forgot-password/`. Don't submit either. The HTML body (source) greets `Hi Acc Main <run>,` and has both links as `<a href>`s. |

### ACC-34 Signing up with an unconfirmed address locks it down

**Purpose:** a signup for an address with an *unconfirmed* account never resends the old
confirmation; it disables the creator's password, retires pending links and mails a set-password
link instead.
**Preconditions:** signed out. Uses `qa.unconfirmed@example.com` (nothing else uses it).

| # | Action | Expected result |
|---|---|---|
| 1 | Sign in with `qa.unconfirmed@example.com` / SEED. | `Please confirm your email address before logging in. Check your inbox for the confirmation link.` (the account exists, unconfirmed). |
| 2 | Open `/account/email-confirmation-expired/`, enter `qa.unconfirmed@example.com`, click `Resend confirmation email`. | Green `If the account exists and is not yet confirmed, ...` message. The mailbox for that address gets `Confirm your ZipTrigo account email` starting `Hi QA Unconfirmed,`. Copy its link (CONFIRM-U) but **do not open it yet**. |
| 3 | Register with Name `Someone Else`, Email `qa.unconfirmed@example.com`, Password `Other-9876`. | `/account/created/`, identical to ACC-06's page. |
| 4 | Mailbox for `qa.unconfirmed@example.com`. | Newest: subject `Finish setting up your ZipTrigo account`. Body starts `Hi there,` (no name), `Someone (maybe you) started creating a ZipTrigo account with this email address (qa.unconfirmed@example.com).`, `To finish setting it up, choose a password here:`, a link `http://localhost:8765/account/reset-password/<token>/` (FINISH-1), `This link will expire in 4 hours.`, `If it wasn't you, you can ignore this email.` There is **no** new `Confirm your ZipTrigo account email`. |
| 5 | Open CONFIRM-U and press `Confirm my email`. | `Link Expired` page: the pending confirmation was invalidated. |
| 6 | Sign in with `qa.unconfirmed@example.com` / SEED. | `Invalid credentials` (the creator's password no longer works at all, so the message is no longer the "please confirm" one). |
| 7 | Sign in with `qa.unconfirmed@example.com` / `Other-9876`. | `Invalid credentials` (nothing typed in step 3 was stored). |

### ACC-35 Finish setting up through the emailed link

**Purpose:** the set-password link both sets a password and confirms the address.
**Preconditions:** ACC-34.

| # | Action | Expected result |
|---|---|---|
| 1 | Open FINISH-1. | The `Set a new password` page with `New password` / `Confirm password` and `Save new password`. Note (copy observation): the page and the success banner speak of a reset, not of finishing a setup. |
| 2 | Enter P-FINISH in both, save. | `/account/login/` with the banner `Your password is set. You can now log in.` |
| 3 | Sign in with `qa.unconfirmed@example.com` / P-FINISH. | Signed in **without** the "please confirm" message (the reset confirmed the email); lands on `/`. On `/account/`, Name shows `QA Unconfirmed`, Email `qa.unconfirmed@example.com` and Credits `0 credits` (a seeded account never gets welcome credits). |
| 4 | Open FINISH-1 again. | `Link Expired` (single use). |
| 5 | Sign out. | Header shows `Login`. |

### ACC-36 htmx and accessibility sweep

**Purpose:** forms behave well with the keyboard and with htmx partial swaps.
**Preconditions:** signed out.

| # | Action | Expected result |
|---|---|---|
| 1 | On `/account/register/`, use only Tab / Shift+Tab. | Focus moves Name -> Email -> Password -> `Create account` -> `Sign in` link, with a visible focus ring on each field. |
| 2 | Submit an invalid form (Name `   `, Email `foo@bar`, Password `abcdefg`). Then press Tab from the top of the card. | The swapped-in form is fully usable: fields keep their typed Name/Email, Tab order is the same, and submitting again works (htmx still bound after the swap; a second submit produces a fresh error list, not two stacked lists, and the under-field messages are replaced, not duplicated). After the first failed submit, keyboard focus is already on the first invalid field (Name), not on the submit button or the page body. |
| 3 | Check the error block. | It sits directly above the first field, inside the card, red text, readable in both light and dark themes. To check the other theme while signed out, emulate the other `prefers-color-scheme` (a visitor without a session follows the system), then reload, which clears the form and its errors, so submit the same invalid form again. Restore the emulation afterwards. Check that the block has `role="alert"` and that each failing input has `aria-invalid="true"` and an `aria-describedby` pointing at its under-field message (the field message itself is not a live region; the block is what is announced). Editing an invalid field removes its message and `aria-invalid`. |
| 4 | On `/account/login/`, sign in with MAIN / `Wrong-0000` twice in a row quickly. | Each submit disables the button while pending (`hx-disabled-elt`), no duplicate error blocks, the eye toggle still works after the swap. |
| 5 | Set the viewport to 375 px wide and visit `/account/login/`, `/account/register/`, `/account/forgot-password/`, `/account/created/`, `/account/email-confirmation-expired/`. | Each card fits the screen with side margins, no horizontal scroll, buttons fully visible. |
| 6 | Sign in as MAIN / P3, visit `/account/` at 375 px. | All three sections fit; the eye buttons stay inside their fields. Sign out and restore the viewport. |

### ACC-37 Theme setting

**Purpose:** the Appearance section saves the theme on the account and applies it everywhere.
**Preconditions:** sign in as MAIN / P3 (Context A) in a browser whose colour scheme is light.

| # | Action | Expected result |
|---|---|---|
| 1 | Go to `/account/`. | The `Appearance` section sits between `Credits` and `Change Password`; `System` is selected; the page is light. |
| 2 | Click `Dark`. | Without a Save click and without a page reload (the page does not flash or jump; scroll position stays), the page turns dark; `Dark` is selected; `<html>` has `data-theme="dark"` and class `dark`. |
| 3 | Use only the keyboard: Tab to the group, use the arrow keys to move to `Light`. | A visible focus ring shows on the focused option and stays on the group after each arrow press; the choice is applied at once, with no reload (the page turns light) when the selection changes. |
| 4 | Open `/`, `/feedback/` and `/billing/credits/`. | All follow the saved theme. |
| 5 | Sign out; the same browser's login page. | It follows the system (light), not the account's last choice. |
| 6 | Sign in again from a different browser profile (another "device"). | The saved theme applies there too. |
| 7 | Visit `/account/` as the no-password account (Context with a Google-created account, if available) or note it as not applicable. | The Appearance section is shown there too. |
| 8 | Restore `System`. | - |

### ACC-38 Time zone select and the "Use it" button

**Purpose:** the account page's time zone select offers canonical zones only and the browser's zone
can be picked with one click.
**Preconditions:** sign in as MAIN / P3 (Context A). The browser reports its own zone (record it as
`<browser zone>`); the first page after sign-in saved it silently if the account had none.

| # | Action | Expected result |
|---|---|---|
| 1 | Open `/account/` and look at the Profile section. | A `Time zone` select between Name and the Save button, with the hint `Used for due dates, reminders and what counts as today.` It shows `<browser zone>` (or `Not set (UTC)` if detection had not run). No "Detected from your browser" line while the select equals the browser's zone. |
| 2 | Open the select's list. | `UTC` first, then `Area/City` names with `_` shown as a space. `Asia/Kolkata`, `Europe/Kyiv` and `America/Argentina/Buenos Aires` are there; their old aliases (`Asia/Calcutta`, `Europe/Kiev`, `America/Buenos Aires`), `Etc/...`, `US/...` and bare names such as `GMT` or `Japan` are **not**. |
| 3 | Pick a zone other than `<browser zone>` (e.g. `Asia/Tokyo`). | Under the select: `Detected from your browser: <browser zone>` followed by **Use it**, which is a real button (reachable with Tab, activated with Enter or Space, underlined; it does not wrap across lines). |
| 4 | Activate **Use it**. | The select goes back to `<browser zone>` and the line disappears. The page did not scroll to the top or change its URL (no `#` was added). |
| 5 | Pick `Asia/Tokyo`, press **Save Profile**. | Green `Profile updated successfully.`; the select still shows `Asia/Tokyo`; the "Detected from your browser" line is back (the form was swapped and the script re-bound). In the console, `document.documentElement.dataset.userTimezone` is now `Asia/Tokyo` **without a reload**. |
| 6 | Reload `/account/`, then restore `<browser zone>` (use **Use it**, **Save Profile**). | The saved zone survived the reload; after the restore the attribute is `<browser zone>` again. |
| 7 | Sign out (menu > `Logout`). | Header shows `Login`. |

### ACC-39 Every password change retires the reset links sent before it

**Purpose:** a reset link is bound to the password the account had when it was sent: using one
link kills the others, and so does a change on the account page.
**Preconditions:** ACC-38; signed out in Context A; MAIN's password is P3.

| # | Action | Expected result |
|---|---|---|
| 1 | On `/account/forgot-password/` submit MAIN. Wait for the email, then submit MAIN again and wait for the second one. | Each submit shows `If the account exists, an email will be sent with a password reset link.` The mailbox for MAIN has two new `Reset your ZipTrigo account password` emails with different links: the older is RESET-A, the newer RESET-B. |
| 2 | Open RESET-A, then RESET-B (don't submit). | Both show the `Set a new password` form: unused links issued under the same password are all valid. |
| 3 | On RESET-B enter P4 in both fields, `Save new password`. | `/account/login/` with the green banner `Your password is set. You can now log in.` |
| 4 | Open RESET-A. | The `Link Expired` page (`This password reset link has expired or is invalid. Please request a new one.`): the password it was issued under is gone. |
| 5 | Sign in with MAIN / P3, then MAIN / P4. | P3: `Invalid credentials`. P4: signed in, lands on `/`. Sign out. |
| 6 | On `/account/forgot-password/` submit MAIN again. | A new reset email arrives (RESET-C). Don't open it yet. |
| 7 | Sign in with MAIN / P4, go to `/account/`, change the password: Current P4, New P5, Confirm P5. | Green `Password changed. We've sent a confirmation to qa.acc.<run>.main@example.com.`; the mailbox for MAIN gets a new `Your ZipTrigo password was changed`. |
| 8 | Sign out. Open RESET-C. | `Link Expired`: a change on the account page retires every reset link sent before it. |
| 9 | Sign in with MAIN / P5. | Signed in. Sign out. |

### ACC-40 A reset confirms an unconfirmed address; names are escaped in emails

**Purpose:** forgot-password works for an account that never confirmed, the reset confirms the
address (granting the welcome credits) and retires the pending confirmation link; the free-text
name is shown as text, never as HTML, in emails and on the page.
**Preconditions:** signed out in Context A.

| # | Action | Expected result |
|---|---|---|
| 1 | Register UNC: Name `Acc <b>Unc</b> <run>` (type the tags literally), Email UNC, Password P-UNC. | `/account/created/`. |
| 2 | Mailbox for UNC. | One `Confirm your ZipTrigo account email`. The text body starts `Hi Acc <b>Unc</b> <run>,` (the tags as plain characters); in the HTML body (source) the greeting is escaped, `Hi Acc &lt;b&gt;Unc&lt;/b&gt; <run>,`: there is no real `<b>` element. Copy its link (CONFIRM-UNC) but **don't open it yet**. |
| 3 | Sign in with UNC / P-UNC. | `Please confirm your email address before logging in. Check your inbox for the confirmation link.` |
| 4 | On `/account/forgot-password/` submit UNC. | The usual green message. The mailbox for UNC gets `Reset your ZipTrigo account password`, greeting `Hi Acc <b>Unc</b> <run>,` in the text body and escaped (`&lt;b&gt;`) in the HTML source. Copy its link (RESET-UNC). |
| 5 | Open RESET-UNC, enter P-UNC in both fields (the reset form, unlike the account page, doesn't refuse the current password), `Save new password`. | `/account/login/` with `Your password is set. You can now log in.` |
| 6 | Sign in with UNC / P-UNC. | Signed in **without** the "please confirm" message; lands on `/`. The account menu's first line reads `Acc <b>Unc</b> <run>` literally (nothing bold). |
| 7 | Open the notification bell; then `/account/`. | The bell has `Welcome! We added 1000 credits to your account` (the reset was a confirmation); the account page shows `1000 credits` and the Name field holds `Acc <b>Unc</b> <run>` as typed. |
| 8 | Sign out. Open CONFIRM-UNC and press `Confirm my email`. | `/account/email-confirmation-expired/` (`Link Expired`): the reset retired the pending confirmation link. |
| 9 | Open RESET-UNC again. | `Link Expired` (single use). |

### ACC-41 A reset link for an address the account has left

**Purpose:** a reset link is bound to the address it was sent to; once the account's address
changes (in the admin, which handles it like an email change: the account is un-confirmed, the
new address is sent a confirmation and the old one a notice), the old link is refused and the
account signs in with the new address only, once that is confirmed.
**Preconditions:** ACC-40 (UNC confirmed, password P-UNC); signed out in Context A; a fresh
Context B. The site has no page to change one's own email (`Email cannot be changed`), so the
address is changed in the admin.

| # | Action | Expected result |
|---|---|---|
| 1 | Context A: on `/account/forgot-password/` submit UNC. | A new `Reset your ZipTrigo account password` arrives for UNC (RESET-UNC2). Don't open it yet. |
| 2 | Context B: open `/admin/` and sign in with ADMIN. Go to `/admin/accounts/user/?q=qa.acc.<run>.unc`, open UNC's row, change `Email` to MOVED (leave "Email confirmed" as it is) and Save. | Saved with a success message. Reopen the row: "Email confirmed" is now **unticked** (a changed address is not confirmed until its owner confirms it). Leave Context B signed in. |
| 3 | Mailboxes (reload up to a minute): `?to=MOVED` and `?to=UNC`. | MOVED: `Confirm your ZipTrigo account email` with a link to `/account/confirm-email/...` (CONFIRM-MOVED; don't open it yet). UNC: `Your ZipTrigo email address was changed`, saying the address was changed from UNC to MOVED on a date and time in UTC, with no link to undo it. |
| 4 | Context A: open RESET-UNC2. | `Link Expired`: the link was for an address the account no longer has. |
| 5 | Context A: sign in with UNC / P-UNC, then with MOVED / P-UNC. | UNC: `Invalid credentials` (no account has that address now). MOVED: `Please confirm your email address before logging in. Check your inbox for the confirmation link.` |
| 6 | Open CONFIRM-MOVED and press `Confirm my email`; then sign in with MOVED / P-UNC. | The email-confirmed page; then signed in, landing on `/`. `/account/` shows Email MOVED and the Name unchanged; no second welcome-credits line in the credits history. Sign out. |
| 7 | On `/account/forgot-password/` submit MOVED. | The mailbox for MOVED gets `Reset your ZipTrigo account password`; the mailbox for UNC gets nothing new (wait 30 s). Open the new link: the `Set a new password` form. Leave it without submitting. |

### ACC-42 Time zone: "Not set", detection and a saved choice that detection never overrides

**Purpose:** a new account without a zone shows `Not set (UTC)`; saving the name alone sets no
zone; the browser's detection fills an empty zone once and updates the open page; a zone the user
chose is never overridden.
**Preconditions:** signed out in Context A, in the same tab as before. `<browser zone>` is the
zone recorded in ACC-38. If `<browser zone>` is `UTC`, the "Detected from your browser" checks are
N/A (the hint only shows when the select differs from the browser's zone).

| # | Action | Expected result |
|---|---|---|
| 1 | Register TZ (Name `Acc Tz <run>`, P-TZ) and confirm it with the link in its `Confirm your ZipTrigo account email` (press `Confirm my email`). | `Email Confirmed!`. |
| 2 | In this tab's developer console run `sessionStorage.setItem('tzSent', '1')` (the tab "has already sent" its zone). Sign in with TZ / P-TZ. | Lands on `/`. In the console `document.documentElement.dataset.userTimezone` is `''` (no zone saved), and no request to `/account/timezone/` was made. |
| 3 | Open `/account/` and look at the Time zone select. | Its first option is `Not set (UTC)` and it is selected; `UTC` follows, then the `Area/City` zones. Under the select: `Detected from your browser: <browser zone>` and **Use it**. |
| 4 | Change Name to `Acc Tz <run> 2` without touching the select; `Save Profile`. Then reload. | `Profile updated successfully.`; the select still shows `Not set (UTC)`, before and after the reload, and `dataset.userTimezone` is still `''`: saving the name set no zone. |
| 5 | Run `sessionStorage.removeItem('tzSent')` and reload `/account/`. Wait two seconds without touching anything. | Without a further reload the select changes by itself from `Not set (UTC)` to `<browser zone>` and the "Detected from your browser" line is hidden; `dataset.userTimezone` is `<browser zone>`; `sessionStorage.getItem('tzSent')` is `'1'`. |
| 6 | Reload `/account/`. | The select shows `<browser zone>`, and `Not set (UTC)` is no longer an option. |
| 7 | Pick `UTC`, `Save Profile`. Then run `sessionStorage.removeItem('tzSent')` and reload. | Saved; after the reload the select still shows `UTC` (detection only fills an empty zone; it never posts once a zone is saved) and the hint offers `Detected from your browser: <browser zone>` with **Use it**. |
| 8 | Set Name back to `Acc Tz <run>`, pick `Asia/Tokyo`, `Save Profile`. | `Profile updated successfully.`; `dataset.userTimezone` is `Asia/Tokyo`. Stay signed in for ACC-43. |

### ACC-43 What the account remembers across a new session and another browser

**Purpose:** the name, time zone and theme are stored on the account, not in the browser.
**Preconditions:** ACC-42 (Context A signed in as TZ, zone `Asia/Tokyo`). Close any Context B
left from ACC-41 and open a fresh one.

| # | Action | Expected result |
|---|---|---|
| 1 | Context A: on `/account/` click `Dark`. | The page turns dark at once (no reload). |
| 2 | Context B (fresh): open `/account/login/`. | It follows the system scheme, not TZ's choice (no session yet). |
| 3 | Context B: sign in with TZ / P-TZ. | `/` renders dark straight away, with no light flash (`<html>` has `data-theme="dark"` and class `dark` in the page source); `dataset.userTimezone` is `Asia/Tokyo`, and no request to `/account/timezone/` was made. |
| 4 | Context B: open `/account/` and the account menu. | Name `Acc Tz <run>` (also the menu's first line), the select on `Asia/Tokyo` (with the "Detected from your browser" hint, unless `<browser zone>` is `Asia/Tokyo`), `Dark` selected, `1000 credits`, the "Have a promo code?" form; the bell has the welcome notification. |
| 5 | Context B: click `System`. Context A: reload `/account/`. | Context A now shows `System` selected and follows the browser's scheme: the setting is the account's, shared by every session. |
| 6 | Context A: sign out and sign in again with TZ / P-TZ; open `/account/`. | Name, `Asia/Tokyo` and `System` are all still there. Sign out in both contexts. |

### ACC-44 There is no self-service deletion; a deleted user can't sign in

**Purpose:** nothing on the site lets users delete their own account; after staff delete one in
the admin, its sessions end, its password and reset requests do nothing, and the address can
register again as a new account (without a second set of welcome credits).
**Preconditions:** signed out in Context A; a fresh Context B. Only the user side is checked here;
`admin.md` covers the admin's delete page in detail.

| # | Action | Expected result |
|---|---|---|
| 1 | Context A: register DEL (Name `Acc Del <run>`, P-DEL), confirm it from the email, sign in. | Signed in; `/account/` shows `1000 credits`. |
| 2 | Look over `/account/` and the account menu. | No "Delete account" / "Close account" button or link anywhere: the page has only `Profile Information`, `Credits`, `Appearance`, `Change Password` and `Back to Home`; the menu only the name, `Settings` and `Logout`. |
| 3 | Navigate to `/account/delete/`. | 404 (this server runs with DEBUG on, so Django's "Page not found" page; the URL patterns it lists under `account/` include no delete route). |
| 4 | Context B: sign in to `/admin/` with ADMIN, open `/admin/accounts/user/?q=qa.acc.<run>.del`, open DEL's row, click `Delete`. | A confirmation page asking whether to delete the user DEL and listing the related items that go with it (its credit account and the welcome credits' ledger line among them); it does not say a permission is missing. |
| 5 | Context B: confirm (`Yes, I’m sure`). | `The user “qa.acc.<run>.del@example.com” was deleted successfully.`; DEL is no longer listed. Sign out of the admin. |
| 6 | Context A: reload `/account/`. | Redirected to `/account/login/?next=/account/`; the header shows `Login` (the deleted user's session ended). |
| 7 | Sign in with DEL / P-DEL. | `Invalid credentials`, exactly as for an address that never had an account. |
| 8 | On `/account/forgot-password/` submit DEL. | The usual green message; after 30 s the mailbox for DEL has no new `Reset your ZipTrigo account password`. |
| 9 | Register DEL again (Name `Acc Del <run>`, P-DEL). | `/account/created/`; the mailbox for DEL gets a new `Confirm your ZipTrigo account email` (not `You already have a ZipTrigo account`: the old account is gone). Confirm it and sign in with DEL / P-DEL. |
| 10 | Open `/account/` and the mailbox for DEL. | `0 credits` and no welcome notification in the bell: the address was used before. The mailbox has `About your ZipTrigo welcome credits`, saying the address (or a variation of it) was already used for a ZipTrigo account and welcome credits are only for new addresses, with a link `http://localhost:8765/billing/credits/`; clicking it opens the credits history page. Sign out. |

## Out of scope

- "Sign in with Google" / "Sign in with Microsoft" flows (real identity providers), and the
  account-page and email variants only those accounts get (the `Password` note with `Set a
  password`, the Google/Microsoft wording of `You already have a ZipTrigo account`). On this server
  they are not configured; ACC-18 checks they fail gracefully.
- The Turnstile captcha and every rate limit (`LOGIN_*`, `CHANGE_PASSWORD_USER`,
  `SIGNUP_EXISTING_EMAIL*`, `ACCOUNT_TIMEZONE_USER`, ...): switched off on this server, unit-tested.
- The JWT API (`/api/auth/...`, `PUT /api/account`), so also the user-facing email change and its
  "Your ZipTrigo email address was changed" notice, which only that endpoint triggers.
- Link expiry by time (48 hours for a confirmation link, 4 hours for a reset link): too long to
  wait for; unit-tested.
- The no-JavaScript fallbacks (the `<noscript>` `Save Theme` button, plain-POST redirects): not
  part of this run.
