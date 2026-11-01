# Preflight: the test environment works

**Run this file first, before any other plan.** It proves that the services every other file
depends on are there and behave like the real ones: the Postgres database, S3 on Floci, and email
over SMTP read back over IMAP. If a case here fails, record it and report every other file as
**BLOCKED** ("preflight failed: <check>"): their results would say more about the environment than
about the site.

`serve.py` runs the same checks before it serves and refuses to start when one fails, so a server
that is up has passed them once. These cases check them again from the browser, right before the
run, and exercise the two pages the other plans rely on (`/__e2e__/mail/` and the bucket).

**Scope:** `/__e2e__/preflight/`, `/__e2e__/mail/`, one browser upload and download through Floci,
and one email sent by the site end to end.

**Preconditions:**

- Postgres and Floci running in docker (`docker compose -f docker-compose.postgres.yml up -d`,
  `docker compose -f docker-compose.floci.yml up -d`), `tests_e2e/ai/emails.yaml` filled in, and the
  server started with `python tests_e2e/ai/serve.py` (default mode, not `--fakes`). Its output ends
  with `E2E site: http://localhost:8765` and the mailbox and preflight URLs.
- `<BASE>` below is the server's URL (`http://localhost:8765`, or the `--port`/`--host` it was
  started with).

### PRE-01 Every preflight check passes

**Purpose:** the database, the three products' storage and the mail round trip work right now.
**Preconditions:** none.

| # | Action | Expected result |
|---|---|---|
| 1 | Open `<BASE>/__e2e__/preflight/`. It takes up to a couple of minutes (it sends an email and waits for it over IMAP). | The heading is `E2E preflight: PASS`. `Mode:` is `real services (Postgres, Floci, SMTP/IMAP)`. If it says `fakes (in-memory)`, the server was started with `--fakes`: stop, the run needs the default mode. |
| 2 | Read the **Database** rows. | Four rows, all **PASS**: `Connect` names PostgreSQL, a database `ziptrigo_e2e_ai...` on `127.0.0.1`/`localhost`; `Migrations` says all applied; `Write` works; `Seeded accounts` counts at least 4 `qa.*` accounts. |
| 3 | Read the **S3** rows. | For each of **File transfer**, **Receipts** and **To-do**: `bucket` (the `ziptrigo-e2e-ai...` bucket at `http://localhost:4566`), `read/write` (under `transfers/`, `receipts/`, `todo/` respectively) and `browser upload` (CORS, presigned PUT with the checksum enforced, presigned GET), all **PASS**. |
| 4 | Read the **Email** rows. | `emails.yaml` (names the SMTP and IMAP hosts and the delivery address, never a password), `SMTP sign-in`, `IMAP sign-in` and `Send and receive` (sent and read back in N s), all **PASS**. |
| 5 | Open `<BASE>/__e2e__/preflight/?mail=0`. | Same page without the Email rows; it answers in a few seconds. |

### PRE-02 The mailbox page lists the run's emails

**Purpose:** the page every other plan reads email on works, and shows only this run's mail.
**Preconditions:** PRE-01 (it sent a probe to `preflight@example.com`).

| # | Action | Expected result |
|---|---|---|
| 1 | Open `<BASE>/__e2e__/mail/`. | Heading `E2E mailbox (N)` with N >= 1, and a line saying the emails were sent over SMTP and read back over IMAP from the `emails.yaml` address, with the run id. No IMAP error. |
| 2 | Open `<BASE>/__e2e__/mail/?to=preflight@example.com`. | At least one email, subject `ZipTrigo e2e preflight <token>`, `To: preflight@example.com`, a `From:` line, a `Sent:` time, a text body with a clickable `<BASE>/__e2e__/preflight/?probe=...` link, and an "HTML body (source)" section showing the `<a href=...>` markup as text. |
| 3 | Open `<BASE>/__e2e__/mail/?to=nobody@example.com`. | `E2E mailbox (0)` and `No emails.` |
| 4 | Open `<BASE>/__e2e__/mail/?to=PREFLIGHT@Example.com`. | The same emails as step 2: the filter ignores case. |

### PRE-03 An email the site sends arrives, with working links

**Purpose:** the site's own sending path (not just the probe) delivers over SMTP, with absolute
links that work. It registers its own account, so the seeded accounts stay as the other plans
expect them (their credits, their empty products).
**Preconditions:** signed out. Pick an address `qa.pre.<random>@example.com` (PRE below).

| # | Action | Expected result |
|---|---|---|
| 1 | Go to `<BASE>/account/register/`, fill Name `QA Preflight`, Email PRE, Password `QaE2e-Passw0rd!`, and submit. | The "Check your email" page. |
| 2 | Open `<BASE>/__e2e__/mail/?to=PRE`; reload until the email is there (up to a minute). | One email, subject `Confirm your ZipTrigo account email`; its text says to open the link and press **Confirm my email**; the link starts with `<BASE>/account/confirm-email/`. |
| 3 | Click the link, then press **Confirm my email**. | The email-confirmed page. |
| 4 | Sign in as PRE / `QaE2e-Passw0rd!`. | Signed in; the header shows QA Preflight and 1,000 credits (welcome credits) appear shortly (the bell may show the welcome notification). |

### PRE-04 A file goes to Floci and comes back

**Purpose:** a browser upload and download go through the real S3 protocol on Floci, not through
the server.
**Preconditions:** PRE-03 (signed in as PRE). Make a small image file, e.g. a 200 x 200 px PNG named
`preflight.png`.

| # | Action | Expected result |
|---|---|---|
| 1 | Open `<BASE>/receipts/upload/`. | The upload page. |
| 2 | Choose `preflight.png`. If the browser tool shows network requests, watch them. | The row reaches **Uploaded**. The file's PUT went to `http://localhost:4566/ziptrigo-e2e-ai.../receipts/users/...` (not to `<BASE>`), answered 200, with no CORS error in the console. |
| 3 | Go to the receipts dashboard and open the new receipt. | It reads as Staples, USD 42.10 (the fake AI). The preview image shows; its `src` is a presigned URL on `http://localhost:4566/`. |
| 4 | Click **Download original**. | `preflight.png` downloads (a redirect to a presigned URL on `localhost:4566`) and is the same image. |
| 5 | Sign out. | The login page. |

## Out of scope

The checks of `serve.py` that run before the server is up (Docker containers reachable, the
database recreated, the bucket emptied and its CORS rule set) can only be seen in its output; an
agent that started the server should confirm that output ended with `0 failed` and the `E2E site:`
line.
