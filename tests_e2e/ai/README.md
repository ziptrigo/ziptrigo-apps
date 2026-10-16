# AI-agent browser tests

End-to-end test plans written for an AI agent that drives a real browser (Claude with a browser
tool, or any agent that can navigate, click, type, read a page and take screenshots). They
complement the Playwright suite in `tests_e2e/` (scripted, run by `inv test e2e`): these are
written in plain language, cover far more of the site, and ask the agent to *look* at what it sees
-- layout, wording, broken states -- the way a QA engineer would.

One file per area:

| File | Area |
|---|---|
| [`core.md`](core.md) | Site shell: landing page, navigation, footer, error pages, theming, mobile layout |
| [`accounts.md`](accounts.md) | Register, email confirmation, login/logout, forgot/reset password, account page |
| [`billing.md`](billing.md) | Credit balance and the credits history page |
| [`feedback.md`](feedback.md) | The footer's Feedback page |
| [`qr_code.md`](qr_code.md) | QR code dashboard, editor, preview, duplicate, delete, `/go/<code>` short links |
| [`file_transfer.md`](file_transfer.md) | Sending files, dashboard actions, public download page, anonymous sending |
| [`receipts.md`](receipts.md) | Upload, extraction, receipt page, vendors, trash, settings, CSV export |
| [`admin.md`](admin.md) | The Django admin: tools page, credit adjustment, settings singletons, moderation |

## The test server

Start it from the repo root (needs `.env.local`, like the rest of the e2e suite):

```bash
python tests_e2e/ai/serve.py
```

It serves `http://localhost:8765` with `tests_e2e.receipts_e2e_settings`:

- its own SQLite database, `db.e2e-ai.sqlite3`, recreated on every start (pass `--keep-db` to
  keep it), so every run starts from the same state;
- in-memory S3 for Receipts **and** File transfer, whose presigned URLs point back at the server
  itself (`/__e2e__/s3/...`, `/__e2e__/ft-s3/...`), so real browser uploads and downloads work;
- a fake AI model for Receipts (`apps/receipts/tests/fakes.py`, `sample_extraction`): every
  processed file reads as vendor **Staples**, amount **42.10 USD**, dated **2026-10-09**, category
  **Office supplies**, document number **R-55102**, payment **Visa ••4242**, confidence 0.95 --
  whatever the file contains;
- background tasks run inline (a receipt is processed during the upload's `complete` request);
- **rate limiting is off** (`RATELIMIT_ENABLE = False`), and the signup captcha is off;
- runserver is threaded and SQLite takes the write lock up front (`transaction_mode: IMMEDIATE`),
  so parallel uploads wait for each other rather than failing with "database is locked";
- every email the site sends is kept in memory and listed, newest first, at
  **`http://localhost:8765/__e2e__/mail/`** (`?to=<address>` filters by recipient). Links in the
  email text are clickable; an email's HTML body is shown as source under "HTML body (source)"
  (to check escaping). The mailbox is in memory: restarting the server empties it.

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

### Several files at once

Cookies are per host name, not per port, so to run files in parallel start one server per file
on its own port **and** host name, e.g. `python tests_e2e/ai/serve.py --port 8775 --host
qr.localhost` (browsers resolve `*.localhost` to the loopback address). Each server has its own
database and mailbox. A plan that needs a second, signed-out session can use another alias of the
same server (`qr2.localhost:8775`); the fake buckets answer CORS for any origin, so uploads work
from either.

Agents that share one browser should each work in their own tab and never navigate another's; a
browser pane may cap the number of tabs (the in-app pane allows 9), so with many parallel agents
give each its own browser (or a headless Playwright context) instead.

## How to run a test file

1. Restart the server (fresh database) before a full run. Files are independent of each other,
   but the test cases inside one file run **in order** and may rely on what earlier cases did.
2. For every test case, follow the steps literally and check **every** expected result. Also
   watch for anything not listed: console errors, broken images, raw template tags (`{{ }}` or
   `{% %}`), `None`/`undefined` shown to users, misaligned or overflowing layout, typos, links that
   go nowhere, missing labels.
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

IDs are `<AREA>-<NN>` (`CORE`, `ACC`, `BILL`, `FB`, `QR`, `FT`, `RCPT`, `ADM`).

## Out of scope here

Anything that needs real third parties (Google sign-in, real S3/SES/OpenAI, Cloudflare
Turnstile), rate limits (switched off on this server), inbound receipt email (SES drops mail in
S3; covered by unit tests and `manage.py receipts_ingest_eml`), and the JWT API/CLIs (covered by
unit tests).
