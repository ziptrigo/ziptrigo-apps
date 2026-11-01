# AI-agent browser tests

End-to-end test plans written for an AI agent that drives a real browser (Claude with a browser
tool, or any agent that can navigate, click, type, read a page and take screenshots). They
complement the Playwright suite in `tests_e2e/` (scripted, run by `inv test e2e`): these are
written in plain language, cover far more of the site, and ask the agent to *look* at what it sees
-- layout, wording, broken states -- the way a QA engineer would.

One file per area, plus the preflight that runs before any of them:

| File | Area |
|---|---|
| [`preflight.md`](preflight.md) | **Run first.** The database, S3 (Floci) and SMTP/IMAP checks, and the mailbox page |
| [`core.md`](core.md) | Site shell: landing page, navigation, footer, error pages, theming, mobile layout |
| [`accounts.md`](accounts.md) | Register, email confirmation, login/logout, forgot/reset/change password, account settings, deletion |
| [`billing.md`](billing.md) | Credit balance, the credits history page, promo codes, welcome credits |
| [`notifications.md`](notifications.md) | The header bell, its dropdown, the notifications page, and every event that creates one |
| [`emails.md`](emails.md) | Every email the site sends: delivery, subject, body, escaping and every link in it |
| [`feedback.md`](feedback.md) | The footer's Feedback page |
| [`qr_code.md`](qr_code.md) | QR code dashboard, editor, preview, duplicate, delete, `/go/<code>` short links |
| [`file_transfer.md`](file_transfer.md) | Sending files, dashboard actions, public download page, anonymous sending |
| [`receipts.md`](receipts.md) | Upload, extraction, receipt page, vendors, trash, settings, CSV export |
| [`short_url.md`](short_url.md) | Short links: dashboard, editor and costs, `/s/<code>` redirect and neutral page, password, preview and reports, stats, blocked domains, takedown |
| [`todo.md`](todo.md) | To-do: views, tasks, subtasks, labels, repeats, comments, attachments, reminders, search and filters, settings, credits |
| [`admin.md`](admin.md) | The Django admin: users, tools page, credit adjustment, settings singletons, promo codes, moderation, every product's admin |

## The test server

The plans run on localhost against the **local services the site really uses**, each in its own
docker container, plus a real mailbox:

| Service | What the server uses | Start it |
|---|---|---|
| Postgres (`docker-compose.postgres.yml`) | its own database, `ziptrigo_e2e_ai`, dropped and created again on every start | `docker compose -f docker-compose.postgres.yml up -d` |
| Floci, local AWS (`docker-compose.floci.yml`) | one S3 bucket, `ziptrigo-e2e-ai`, emptied on every start, for every product's files | `docker compose -f docker-compose.floci.yml up -d` |
| SMTP + IMAP (`emails.yaml`) | every email the site sends goes out over SMTP and is read back over IMAP | copy `emails.example.yaml` to `emails.yaml` and fill it in |

Then start the server from the repo root (needs `.env.local`, like the rest of the e2e suite):

```bash
python tests_e2e/ai/serve.py
```

It recreates the database and empties the bucket, migrates, seeds the accounts below, then runs
the **preflight checks** and refuses to start if any fails:

- **Database**: the Postgres e2e database answers, is on localhost, has every migration and the
  seeded accounts, and takes writes;
- **S3**, for file transfer, Receipts and To-do each: the bucket is the e2e one on Floci,
  objects can be written, read and deleted under the product's prefix, the bucket's CORS rule lets
  the site's origin upload from a browser and exposes `ETag`, a presigned PUT works and refuses a
  wrong checksum, and a presigned GET returns the bytes;
- **Email**: `emails.yaml` is complete, SMTP and IMAP sign in, and a probe sent over SMTP is read
  back over IMAP with its link intact.

The same checks run again, live, at **`http://localhost:8765/__e2e__/preflight/`** (`?mail=0`
skips the email round trip), and from a shell with `python tests_e2e/ai/preflight.py`. Every run
starts with [`preflight.md`](preflight.md).

What else the server does:

- it serves `http://localhost:8765` with `tests_e2e.receipts_e2e_settings`; browser uploads and
  downloads go straight to Floci (`http://localhost:4566/ziptrigo-e2e-ai/...`) through presigned
  URLs, as they go to S3 in production;
- a fake AI model for Receipts (`apps/receipts/tests/fakes.py`, `sample_extraction`): every
  processed file reads as vendor **Staples**, amount **42.10 USD**, dated **2026-10-09**, category
  **Office supplies**, document number **R-55102**, payment **Visa ••4242**, confidence 0.95 --
  whatever the file contains; To-do's title parsing is a fake that understands only the titles in
  `tests_e2e/todo_fakes.py`;
- background tasks run inline (a receipt is processed during the upload's `complete` request), and
  the scheduler does not run: a case that needs a scheduled job (metering, reminders, expiry) runs
  it as a **shell step** (below);
- **rate limiting is off** (`RATELIMIT_ENABLE = False`), and the signup captcha is off;
- runserver is threaded, so parallel uploads work.

`python tests_e2e/ai/serve.py --fakes` is the old self-contained mode: a SQLite file
(`db.e2e-ai.sqlite3`), in-memory buckets served by the server itself (`/__e2e__/s3/...`) and an
in-memory mailbox; no docker and no `emails.yaml` needed. It is quicker to start but proves nothing
about S3 or email delivery, so a full run uses the default mode. `--keep-db` keeps the database
and the bucket of the previous run.

### Email

Every email the site sends is a real multipart message sent over the SMTP server of `emails.yaml`,
with the site's own subject, text and HTML bodies, and in its `To:` header the address the site
chose (`qa.user@example.com`). It is **delivered** to the one mailbox of `emails.yaml`
(`imap.address`), because the plans use made-up addresses. Two headers mark it: `X-E2E-Run` (this
server run) and `X-E2E-Original-To`.

**`http://localhost:8765/__e2e__/mail/`** reads that mailbox over IMAP and lists, newest first, the
emails of the current run (`?to=<address>` filters by the address the site sent it to). Delivery
takes a few seconds: when a case says to open the mailbox, **reload until the email is there, up
to a minute**, and only then call it missing; a case that expects *no* email waits 30 seconds
first. Links in the text body are clickable and are the real absolute links (`BASE_URL`); the HTML
body is shown as source under "HTML body (source)" (to check escaping). An IMAP problem is shown on
that page, not hidden.

The mailbox keeps every run's emails; `serve.py --purge-old-mail` deletes the earlier runs' ones
(only messages with an `X-E2E-Run` header, which nothing but the test server writes). Use a mailbox
meant for testing: a full run sends a few hundred emails. If the provider files them as spam, add
its spam folder to `imap.folders`.

### Shell steps

A few cases need something no page can trigger, usually a scheduled job. They are marked
**(shell step)** and give a command such as

```bash
python tests_e2e/ai/manage.py shell -c "from apps.todo.jobs import send_reminders; send_reminders()"
```

`tests_e2e/ai/manage.py [--port N] <manage.py arguments>` runs `manage.py` with the environment
`serve.py` recorded for that port (`tests_e2e/ai/.run/<port>.json`): the same database, bucket and
mailbox, so what the command does shows on the site and its emails in `/__e2e__/mail/`. An agent
without a shell marks those steps BLOCKED.

### Seeded accounts

All share the password `QaE2e-Passw0rd!` (a local test value; see `ACCOUNTS` in `serve.py`).

| Email | What it is |
|---|---|
| `qa.user@example.com` | Ordinary user, email confirmed, 100 credits |
| `qa.admin@example.com` | Superuser (admin access), email confirmed, 100 credits |
| `qa.broke@example.com` | Ordinary user, email confirmed, 0 credits |
| `qa.unconfirmed@example.com` | Email **not** confirmed, 0 credits (cannot log in until confirmed) |

Tests that need a fresh account create one through the register page with a unique address such
as `qa.<area>.<random>@example.com`.

**Welcome credits.** The seeded accounts hold exactly the credits in the table (`serve.py` switches the
welcome credits off while it seeds them). Any account **registered** during a run gets 1,000 welcome
credits, once, as soon as its email is confirmed (a ledger line "A gift for your new account" and a bell
notification), unless Billing settings > Welcome credits is set to 0. So a step that needs an account with
no credits must use `qa.broke@example.com`, not a fresh registration; an address that differs from a
confirmed one only by a `+tag` (or Gmail dots) gets nothing and an email saying why. Accounts created in
the admin's add form get the credits when "Email confirmed" is ticked (`admin.md` switches the setting off
for that reason).

### Several files at once

Cookies are per host name, not per port, so to run files in parallel start one server per file
on its own port **and** host name, e.g. `python tests_e2e/ai/serve.py --port 8775 --host
qr.localhost` (browsers resolve `*.localhost` to the loopback address). Each server has its own
database (`ziptrigo_e2e_ai_8775`), bucket (`ziptrigo-e2e-ai-8775`) and run id, so its mailbox page
shows only its own emails although they all land in the same IMAP mailbox. Shell steps then take
`--port 8775`. A plan that needs a second, signed-out session can use another alias of the same
server (`qr2.localhost:8775`); the bucket's CORS rule allows any origin, so uploads work from
either.

Agents that share one browser should each work in their own tab and never navigate another's; a
browser pane may cap the number of tabs (the in-app pane allows 9), so with many parallel agents
give each its own browser (or a headless Playwright context) instead.

## How to run a test file

1. Restart the server (fresh database and bucket) before a full run, and run
   [`preflight.md`](preflight.md) first: if it fails, stop and report BLOCKED for everything
   rather than test against a broken environment. Files are independent of each other, but the
   test cases inside one file run **in order** and may rely on what earlier cases did.
2. For every test case, follow the steps literally and check **every** expected result. Also
   watch for anything not listed: console errors, broken images, raw template tags (`{{ }}` or
   `{% %}`), `None`/`undefined` shown to users, misaligned or overflowing layout, typos, links that
   go nowhere, missing labels.
   Two things hold for every form unless a case says otherwise: a refused (422) submit shows its
   `Label: message` lines in the form's red message box **and** repeats each field's message under
   that field (red border, `aria-invalid`, keyboard focus on the first invalid field, the message
   removed when the field is edited); and the site's pages have a "Skip to content" link as their
   first Tab stop.
3. Record a result per case: **PASS**, **FAIL** (with what you saw versus what was expected, the
   URL, and a screenshot or the page text), **BLOCKED** (could not run, and why) or **SKIPPED**.
4. A failure does not stop the file: note it and continue unless later cases depend on it.

## Test case format

Each file starts with the area's scope and preconditions, then numbered cases:

```markdown
### QR-03 Create a static QR code

**Purpose:** what the case proves.
**Preconditions:** state it needs (logged in as ..., created in QR-02, ...).

| # | Action | Expected result |
|---|---|---|
| 1 | Go to `/qr/create/`. | The editor opens; the preview is empty. |
| 2 | ... | ... |
```

IDs are `<AREA>-<NN>` (`PRE`, `CORE`, `ACC`, `BILL`, `NOTIF`, `MAIL`, `FB`, `QR`, `FT`, `RCPT`, `SU`,
`TODO`, `ADM`).

## Out of scope here

Anything that needs real third parties (Google and Microsoft sign-in, real AWS, SES, OpenAI,
Cloudflare Turnstile, Google Safe Browsing, MaxMind GeoIP), rate limits (switched off on this
server), inbound email to the `receipts+` / `todo+` addresses (SES drops it in S3; covered by unit
tests, and `manage.py receipts_ingest_eml` / `todo_ingest_eml` exist for local runs), and the JWT
API and the `inv` CLIs (covered by unit tests).

## Keeping the plans current

These plans are only worth running if they describe the site as it is. **Every change that adds,
removes or changes something a user or an admin can see or do updates the plan of its area in the
same pull request** -- the same rule as unit tests. In practice:

- **A new feature** gets new cases in its area's file: the happy path, every refusal the form or
  service can give (with the message, quoted from the code), permissions (another user's object is
  a 404, logged out goes to the login page), every email it sends (in that file *and* a row plus a
  case in [`emails.md`](emails.md)), every notification it creates (in
  [`notifications.md`](notifications.md)), its admin (in [`admin.md`](admin.md)), and phone width,
  dark mode and keyboard access for a new page or widget.
- **A changed feature** updates the cases that describe it: wording, URLs, labels, numbers, the
  order of steps. Search the plans for the old text (`grep -rn "<old wording>" tests_e2e/ai/`); a
  case that would now fail because the site changed on purpose is a stale plan, not a bug.
- **A removed feature** removes or rewrites its cases. Don't renumber the others: IDs are quoted in
  issues and run reports; a removed case's ID is not reused.
- **A new area** (an app, or a cross-cutting feature) gets its own file, with the scope and
  preconditions header, an ID prefix, a row in the table at the top of this file, and, if it stores
  files, a check in `tests_e2e/real_services.py`'s preflight.
- **A new email** goes in the inventory table of `emails.md`; **a new background job** whose effect
  a case checks gets a shell step through `tests_e2e/ai/manage.py`.
- **A new external service** the site depends on (another bucket, a queue, a cache) gets a
  preflight check in `tests_e2e/real_services.py` and a row in the services table above, so a run
  stops before it starts when the service is missing.
- New cases continue the file's numbering (after `ACC-44` comes `ACC-45`), follow the case format
  above, and say in **Preconditions** which earlier cases they rely on.
- Every expected result is checked against the templates, views and services, never written from
  memory: quote messages exactly.

When a run finds a plan that is wrong, fix the plan in the same change that records the finding.
