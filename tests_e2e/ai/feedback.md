# Feedback: the footer's Feedback page

**Scope.** `apps.feedback`: the login-only page at `/feedback/` reached from the footer's
**Feedback** link, its htmx form (validation: empty, whitespace-only, over 5,000 characters; the
live character counter), the one-shot thank-you page at `/feedback/thanks/`, the two emails each
submission sends (a receipt to the submitter, only when their address is confirmed, and a
notification to every active superuser), escaping of HTML in the text, and the staff side in the
admin (`/admin/feedback/feedback/`: list, detail, status change, bulk status actions). Rate
limiting (`FEEDBACK_USER`, 10 per hour) is switched off on this server and is not tested.

## Preconditions

- The test server is running at `http://localhost:8765` on a fresh database. All URLs are relative
  to it.
- Seeded accounts (password `QaE2e-Passw0rd!`): `qa.user@example.com` (name "QA User", confirmed),
  `qa.admin@example.com` (name "QA Admin", superuser, confirmed; the **only** superuser, so the only
  recipient of notifications). `qa.unconfirmed@example.com` exists but cannot log in (see FB-15).
- Log in on `/account/login/` (**Email**, **Password**, **Sign in**); log out from the account menu
  (person icon, top right) > **Logout**.
- Emails: `/__e2e__/mail/` lists every email sent, newest first, showing **To**, **Subject**,
  **Sent** and the plain-text body (`?to=<address>` filters). Each email's HTML body is shown as
  source under a collapsed **HTML body (source)** section.
- Timestamps in emails are UTC (`YYYY-MM-DD` and `HH:MM`).

## Reference strings (from the code)

| Where | Exact text |
|---|---|
| Page title / heading | `Send feedback` / **Send feedback** |
| Intro | "Found a bug, have an idea, or want to tell us what you think? Every message is read." |
| Field label / placeholder | `Your feedback` / `Tell us what's on your mind…` |
| Under the field (confirmed users only) | `A copy will be emailed to <email>.` |
| Counter | `<n> / 5,000` (red when over 5,000) |
| Button / while sending | `Send feedback` / `Sending...` |
| Empty or whitespace error | `Please write your feedback.` |
| Too long error | `Please keep your feedback to 5,000 characters.` |
| Thanks page title / heading | `Thanks for your feedback` / **Thanks for your feedback!** |
| Thanks text, confirmed | "We've received your message and emailed a copy to **<email>**. Every message is read." |
| Thanks text, unconfirmed | "We've received your message. Every message is read." |
| Thanks buttons | `Send more feedback`, `Back to home` |
| Receipt email subject | `Thanks for your feedback` |
| Notification email subject | `New feedback submitted` |

---

### FB-01 The page needs a login

**Purpose:** `/feedback/` and `/feedback/thanks/` are login-only, and the footer link leads there.
**Preconditions:** logged out, on `/`.

| # | Action | Expected result |
|---|---|---|
| 1 | Click **Feedback** in the footer. | Redirected to `/account/login/?next=/feedback/`. |
| 2 | Go to `/feedback/thanks/`. | Redirected to `/account/login/?next=/feedback/thanks/`. |
| 3 | Go back to `/account/login/?next=/feedback/` and sign in as `qa.user@example.com`. | You land on `/feedback/` with the form. |

### FB-02 Form layout and the live counter

**Purpose:** the page shows the right texts and the counter follows what is typed.
**Preconditions:** logged in as `qa.user@example.com`, on `/feedback/`.

| # | Action | Expected result |
|---|---|---|
| 1 | Look at the page. | Tab title `Send feedback`. A card with a speech-bubble icon, heading **Send feedback**, the intro sentence, the label **Your feedback**, an empty 9-row textarea with the placeholder, and a full-width **Send feedback** button. |
| 2 | Look under the textarea. | Left: "A copy will be emailed to qa.user@example.com." Right: `0 / 5,000`. |
| 3 | Look at the footer. | The **Feedback** link carries `aria-current="page"` (check the accessibility tree). |
| 4 | Type `Hello`. | The counter reads `5 / 5,000` after each keystroke. |
| 5 | Clear the field and type `Olá 😀 日本` (8 characters: O, l, á, space, 😀, space, 日, 本). | The counter reads `8 / 5,000` (an emoji counts as one character, as the server counts it). |
| 6 | Clear the field. | `0 / 5,000`. |

### FB-03 Empty submission is blocked in the browser

**Purpose:** the textarea is `required`, so nothing is sent for an empty form.
**Preconditions:** logged in as `qa.user@example.com`, on `/feedback/`, field empty. Note the count
of emails at `/__e2e__/mail/` (heading "E2E mailbox (N)").

| # | Action | Expected result |
|---|---|---|
| 1 | Press **Send feedback**. | The browser's own validation bubble ("Please fill out this field." or the browser's equivalent) appears on the textarea; the page does not change; no request is sent. |
| 2 | Check `/__e2e__/mail/`. | The email count is unchanged. |

### FB-04 Whitespace-only feedback is rejected by the server

**Purpose:** spaces and line breaks alone pass the browser's `required` check but not the server's.
**Preconditions:** logged in as `qa.user@example.com`, on `/feedback/`.

| # | Action | Expected result |
|---|---|---|
| 1 | Type three spaces, press `Enter` twice, type two more spaces. | The counter shows `7 / 5,000`. |
| 2 | Press **Send feedback**. | The button briefly shows a spinner and `Sending...`. The form is replaced in place (no page reload, URL still `/feedback/`): a red message **Please write your feedback.** appears between the label and the textarea, and the textarea has a red border. The response status is 422. |
| 3 | Check `/__e2e__/mail/`. | No new email. |
| 4 | Type `x` in the field and look at the counter. | The counter still works after the form was swapped (it counts the whitespace kept in the field plus `x`). |
| 5 | Clear the field. | - |

### FB-05 Over 5,000 characters is rejected, the text is kept

**Purpose:** the length cap, and that a rejected text is not lost.
**Preconditions:** logged in as `qa.user@example.com`, on `/feedback/`. Typing 5,001 characters by
hand is impractical: paste a prepared string, or set it with JavaScript and fire an `input` event so
the counter updates:
`const t = document.querySelector('textarea[name=description]'); t.value = 'A'.repeat(4990) + 'END-MARKER'.padEnd(11, '!'); t.dispatchEvent(new Event('input'));`
(4,990 + 11 = 5,001 characters).

| # | Action | Expected result |
|---|---|---|
| 1 | Put the 5,001-character text in the field. | The counter reads `5,001 / 5,000` in red. There is no `maxlength`, so the browser accepts it. |
| 2 | Press **Send feedback**. | 422, form swapped in place: the red message **Please keep your feedback to 5,000 characters.**; the textarea still contains the whole text (it ends with `END-MARKER!`); the counter again reads `5,001 / 5,000` in red. |
| 3 | Delete one character (the last `!`). | Counter `5,000 / 5,000`, no longer red. |
| 4 | Press **Send feedback**. | Accepted: the browser goes to `/feedback/thanks/` (exactly 5,000 characters is allowed). |
| 5 | Check `/__e2e__/mail/`. | Two new emails (receipt to `qa.user@example.com`, notification to `qa.admin@example.com`); the receipt quotes the long text. |

### FB-06 Successful submission and the thank-you page

**Purpose:** the happy path, with multi-line Unicode text.
**Preconditions:** logged in as `qa.user@example.com`. Go to `/feedback/`.

Text to type (three lines, keep the blank line between the first two):

```
FB-06 Olá! The upload page is great. 😀

Second paragraph: naïve café, 日本語 text.
```

| # | Action | Expected result |
|---|---|---|
| 1 | Type the text and press **Send feedback**. | The button shows `Sending...` and is disabled while the request runs (a second click does nothing), then the browser navigates to `/feedback/thanks/`. |
| 2 | Look at the page. | Tab title `Thanks for your feedback`. A round check-mark icon, heading **Thanks for your feedback!**, the text "We've received your message and emailed a copy to **qa.user@example.com**. Every message is read." and two buttons: **Send more feedback** (outlined) and **Back to home** (filled). |
| 3 | Click **Send more feedback**. | `/feedback/` with an **empty** form (counter `0 / 5,000`). |
| 4 | Press Back to return to the thanks page, or submit `FB-06 second message` and then click **Back to home**. | **Back to home** goes to `/`. |

### FB-07 The thank-you page is one-shot

**Purpose:** `/feedback/thanks/` can only be seen right after a submission, so a refresh never
resends and a direct visit shows the form.
**Preconditions:** logged in as `qa.user@example.com`.

| # | Action | Expected result |
|---|---|---|
| 1 | Submit `FB-07 one-shot check` on `/feedback/`. | You are on `/feedback/thanks/`. |
| 2 | Reload the page (F5). | Redirected to `/feedback/` showing the empty form. No "resubmit form?" browser prompt. |
| 3 | Check `/__e2e__/mail/`. | Only one pair of emails for "FB-07 one-shot check" (the reload sent nothing). |
| 4 | Type `/feedback/thanks/` into the address bar. | Redirected to `/feedback/`. |
| 5 | Press the browser Back button a few times. | You never see a duplicate submission; any thank-you page shown from history redirects to the form on reload. |

### FB-08 The receipt email

**Purpose:** the submitter's copy has the right subject, greeting and quoted text.
**Preconditions:** FB-06 done.

| # | Action | Expected result |
|---|---|---|
| 1 | Open `/__e2e__/mail/?to=qa.user@example.com` and find the email whose body contains `FB-06 Olá!`. | **To** `qa.user@example.com`, **Subject** exactly `Thanks for your feedback` (no feedback text in the subject). |
| 2 | Read the body. | In order: `Hi QA User,`; "Thanks for taking the time to send us your feedback. Every message is read."; "Here's a copy of what you sent on <today, YYYY-MM-DD> at <HH:MM> UTC:"; the text quoted line by line with `> ` (`> FB-06 Olá! The upload page is great. 😀`, a line `> ` for the blank line, `> Second paragraph: naïve café, 日本語 text.`); then `The ZipTrigo team`. Accents, emoji and CJK characters are intact (no `?` or mojibake). The date and time match when you submitted (UTC). |

### FB-09 The superuser notification email

**Purpose:** every active superuser is told, with the sender, the text and an admin link.
**Preconditions:** FB-06 done.

| # | Action | Expected result |
|---|---|---|
| 1 | Open `/__e2e__/mail/?to=qa.admin@example.com` and find the email containing `FB-06 Olá!`. | **Subject** exactly `New feedback submitted`. |
| 2 | Read the body. | `From: QA User (qa.user@example.com)`; `Submitted: <YYYY-MM-DD HH:MM> UTC`; `Status: New`; the quoted text as in FB-08; then `Review it in the admin: http://localhost:8765/admin/feedback/feedback/<uuid>/change/` (a clickable link). |
| 3 | Check who else received it: open `/__e2e__/mail/` and look at every `New feedback submitted` email. | Each is addressed to `qa.admin@example.com` only (one per submission); none to `qa.user`, `qa.broke` or `qa.unconfirmed`. |
| 4 | Click the admin link while logged in as `qa.user@example.com`. | The admin login page with "You are authenticated as qa.user@example.com, but are not authorized to access this page. Would you like to login to a different account?"; no feedback content is shown. |

### FB-10 HTML in feedback is shown as text

**Purpose:** markup typed into feedback never renders or runs, on the site, in the emails or in the
admin.
**Preconditions:** logged in as `qa.user@example.com`, on `/feedback/`.

Text to submit:

```
FB-10 <script>alert('xss')</script> <b>bold?</b> <img src=x onerror="alert(1)"> & "quotes" 'single'
```

| # | Action | Expected result |
|---|---|---|
| 1 | Submit the text. | The thanks page appears; no alert dialog opens at any point. |
| 2 | Open `/__e2e__/mail/?to=qa.user@example.com`, find the newest `Thanks for your feedback`. | The quoted line shows the text exactly as typed, tags visible as text (`<script>`, `<b>`, `<img ...>`), nothing bold, no broken-image icon, no alert. |
| 3 | Same check on the newest `New feedback submitted` to `qa.admin@example.com`. | Same: literal text. |
| 4 | Expand **HTML body (source)** on these two emails. | The text appears escaped (`&lt;script&gt;`, `&lt;b&gt;`, `&amp;`, `&quot;`, `&#x27;`), inside a `<blockquote>`. |
| 5 | The admin rendering is checked in FB-12. | - |

### FB-11 A superuser's own feedback

**Purpose:** a superuser submitting gets both the receipt and the notification, with their own name.
**Preconditions:** log out, log in as `qa.admin@example.com`.

| # | Action | Expected result |
|---|---|---|
| 1 | On `/feedback/`, check the line under the field. | "A copy will be emailed to qa.admin@example.com." |
| 2 | Submit `FB-11 from the admin`. | Thanks page naming **qa.admin@example.com**. |
| 3 | Open `/__e2e__/mail/?to=qa.admin@example.com`. | Two new emails: `Thanks for your feedback` starting `Hi QA Admin,`, and `New feedback submitted` with `From: QA Admin (qa.admin@example.com)`. |

### FB-12 Admin: list and detail

**Purpose:** staff can find and read feedback; it is read-only except the status.
**Preconditions:** logged in as `qa.admin@example.com`; FB-05, FB-06, FB-07, FB-10 and FB-11 done.

| # | Action | Expected result |
|---|---|---|
| 1 | Go to `/admin/feedback/feedback/`. | Tab title `Select feedback to change | ZipTrigo Admin`. A list with columns **Created at**, **Submitted by**, **Status**, **Feedback**, newest first; a date drill-down above the list; filters for **status** (New / In process / Closed) and **created at**; a search box. There is **no** "Add feedback" button. Every row's status is **New**. |
| 2 | Look at the **Feedback** column. | One line per entry: the FB-06 row shows its text with the line breaks collapsed into single spaces; the 5,000-character FB-05 row is cut to about 80 characters ending in `…`. The FB-10 row shows the tags as literal text (no bold, no image, no alert). **Submitted by** shows the email (`qa.user@example.com`, `qa.admin@example.com`). |
| 3 | Search for `naïve`. | Only the FB-06 entry is listed. Clear the search; search for `qa.admin@example.com`: only the FB-11 entry. Clear it. |
| 4 | Open the FB-06 entry. | Read-only fields **Id** (a UUID matching the email link from FB-09), **Created at**, **Updated at**, **Created by**, **Feedback** (the full text with its line breaks and blank line preserved, Unicode intact); **Status** is the only editable field, a select with **New**, **In process**, **Closed**, currently **New**. |
| 5 | Open the FB-10 entry. | The **Feedback** field shows the markup as text; no alert, no bold. |

### FB-13 Admin: change the status

**Purpose:** a single status change and the bulk actions.
**Preconditions:** logged in as `qa.admin@example.com`, on the FB-06 entry's change page. Note its
**Updated at** value.

| # | Action | Expected result |
|---|---|---|
| 1 | Set **Status** to **In process** and press **Save**. | Back on the list with a success message like `The feedback “Feedback <uuid> (in_process)” was changed successfully.` (the object name shows the raw value `in_process`; record as a cosmetic observation). The FB-06 row's status is **In process**. |
| 2 | Open it again. | **Status** is **In process**; **Updated at** is later than the value you noted; **Created at** unchanged. |
| 3 | Choose **In process** in the **status** filter dropdown and press **Search** (the Jazzmin filters apply only on Search). | Only the FB-06 row (URL `?status__exact=in_process`). Clear the filter. |
| 4 | Tick the FB-07 and FB-11 rows, choose the action **Mark selected as Closed** and press the action button (**Go** / **Run**). | Success message `Marked 2 feedback as Closed.`; both rows show **Closed**. |
| 5 | Tick the FB-11 row, run **Mark selected as New**. | `Marked 1 feedback as New.`; it shows **New** again. |
| 6 | Run **Mark selected as In process** on the FB-10 row. | `Marked 1 feedback as In process.` |
| 7 | Open the actions menu. | It also offers Django's **Delete selected feedback** (deletion is not disabled for feedback). Do not run it. |

### FB-14 A logged-out session while the form is open

**Purpose:** what happens when the session ends between loading the form and sending it.
**Preconditions:** logged in as `qa.user@example.com`. Open `/feedback/` in tab A and the site in tab
B.

| # | Action | Expected result |
|---|---|---|
| 1 | In tab A type `FB-14 after logout`. In tab B log out. | - |
| 2 | In tab A press **Send feedback**. | Expected: you end up on the login page as a normal full page (URL `/account/login/?next=/feedback/`), or a clear message that you were signed out. Nothing is saved: after logging in again, the admin list (as `qa.admin`) has no "FB-14" entry. **Watch for:** the login page rendered *inside* the feedback card (a second header and footer nested in the page) while the URL stays `/feedback/`; htmx follows the login redirect and swaps whatever comes back into the form. If that happens, record **FAIL** with a screenshot. |
| 3 | Log in again as `qa.user@example.com`. | - |

### FB-15 Wording for an unconfirmed email address (needs setup)

**Purpose:** a logged-in user whose address isn't confirmed is not promised a copy and gets no
receipt email, while superusers are still notified.
**Preconditions:** the seeded `qa.unconfirmed@example.com` **cannot** log in (a password login needs
a confirmed email), so this case needs a session whose account became unconfirmed *after* login.
The only way on this site is an email change through the JSON API (`PUT /api/account`), which
un-confirms the account but keeps the browser session valid. Setup, using the Swagger UI at
`/api/docs` as a tool (if any step is not possible with your browser tool, mark the case
**BLOCKED: needs setup**):

1. Register a fresh account on `/account/register/` (**Email** `qa.fb.<random>@example.com`,
   **Name** `FB Tester`, **Password** `QaE2e-Passw0rd!`), open the confirmation email at
   `/__e2e__/mail/?to=<that address>`, follow its link and press **Confirm my email**.
2. Log in on the site as that account (tab A). Check `/feedback/` shows "A copy will be emailed to
   <address>."
3. In tab B open `/api/docs`, expand `POST /api/auth/login`, **Try it out** with
   `{"email": "<address>", "password": "QaE2e-Passw0rd!"}`, **Execute**, copy `access_token`.
4. Press **Authorize**, paste the token into the `JWTAuth` bearer field, **Authorize**, close.
5. Expand `PUT /api/account`, **Try it out** with
   `{"email": "qa.fb.<random>.new@example.com", "current_password": "QaE2e-Passw0rd!"}`,
   **Execute**: expect 200.

| # | Action | Expected result |
|---|---|---|
| 1 | In tab A reload `/feedback/`. | Still logged in (the form is shown). The line "A copy will be emailed to ..." is **absent** (the left side under the field is empty); the counter `0 / 5,000` is still on the right. |
| 2 | Submit `FB-15 unconfirmed`. | `/feedback/thanks/` reads "We've received your message. Every message is read." with **no** email address and no "emailed a copy". |
| 3 | Open `/__e2e__/mail/`. | **No** `Thanks for your feedback` email to either the old or the new address for this submission (the confirmation email for the new address and the "email changed" notice to the old one from setup step 5 are expected). A `New feedback submitted` email to `qa.admin@example.com` **is** there, with `From: FB Tester (qa.fb.<random>.new@example.com)`. |

### FB-16 Layout: dark mode and phone width

**Purpose:** the form and thank-you page look right in dark mode and at 375 px.
**Preconditions:** logged in as `qa.user@example.com`.

| # | Action | Expected result |
|---|---|---|
| 1 | Switch to dark mode (choose **Dark** under Appearance on `/account/`) and open `/feedback/`. | The card, label, textarea (dark background, light text, readable placeholder), counter and button are readable. Trigger the whitespace error (FB-04 steps 1-2): the red error text and red border are readable on dark. |
| 2 | Submit `FB-16 dark mode` and look at the thanks page. | Readable in dark mode; the check-mark circle is visible. Switch back to **System** under Appearance. |
| 3 | Resize to 375 x 812 and open `/feedback/`. | No horizontal page scroll; the card fills the width with padding; the textarea and button are full width; the "A copy will be emailed to qa.user@example.com." line and the counter fit (the address may wrap; the counter stays on one line). |
| 4 | Submit `FB-16 phone` and look at the thanks page. | The heading, text and both buttons fit; the buttons wrap onto two lines if needed rather than overflowing. |
| 5 | Reset the viewport to desktop. | - |
