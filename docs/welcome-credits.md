# Welcome credits (issue #160)

Every genuinely new account gets free credits once its email is confirmed: no code to type, no
form. This is the admin-facing note; `CLAUDE.md` > "Welcome credits" has the design.

## What a user sees

- Sign up (or "Sign up with Google/Microsoft"), confirm the email, and the balance goes up by the
  welcome amount, with a **Welcome credits** line in the credits history and a bell notification.
- If the address is not new to us, nothing is credited and **one email** says why. The account
  works normally.

## Settings

`/admin/billing/billingsettings/` > **Welcome credits** (superusers). Default **1000**.

| Value | Effect |
|---|---|
| a number, 1 to 1,000,000 | New accounts get that many credits once their email is confirmed. |
| `0` | Off. Accounts created while it is off are never offered credits, even if it is turned on later. A row already waiting for a confirmation stays waiting. |

Changing the amount applies to everyone who has not been credited yet, whenever they confirm.
Accounts that existed before this shipped, and staff and superusers, are never offered credits.

**Who got what** is under `/admin/billing/welcomecredit/` (read-only): status `pending`
(waiting for the email confirmation), `granted` or `refused` (reason "Address already used"). The
credit itself is an ordinary ledger line, type "Welcome credits".

## What counts as "new"

The address on the account must be the first one, ever, to have been confirmed on any account, or
a variation of it:

- `email+anything@x.com` is `email@x.com`, for every provider;
- for Gmail only, dots do not matter (`first.last@gmail.com` is `firstlast@gmail.com`), and
  `googlemail.com` is `gmail.com`;
- letter case does not matter.

It is remembered after the account is deleted, and after the account moves to another address, so
neither frees the address up. Only **confirmed** addresses count: registering with an address you
do not own claims nothing, so it cannot get the real owner refused later. When two accounts with
variants of one address are both waiting, the first to confirm gets the credits.

What is stored is a keyed hash of the canonical address, not the address (`accounts.KnownEmail`).

## Caveats

- **Do not rotate `SECRET_KEY` casually.** The hashes are keyed with it; after a rotation every
  remembered address looks new again. Re-fingerprint first if you must.
- Addresses that an *existing* account has already moved away from before this shipped are not
  remembered (the information is gone); the backfill covers confirmed accounts as they are.
- Someone with several separate mailboxes (not aliases of one) can still get the credits once per
  mailbox. Making that harder (phone, card) is a different feature.
- It is on by default. To ship it switched off, set the value to 0 in the admin before the first
  user signs up (or after the deploy, before announcing it): rows are only created while it is above 0.

## Running it by hand

`inv users create local me@example.com --confirm` creates a confirmed account and grants the
credits right away (when the setting is above 0 and the address is new). `inv users confirm local
me@example.com` confirms an existing one and settles it.

The daily job `billing.settle_welcome_credits` retries anything whose task was lost (a pending
row of a confirmed user, a refusal whose email did not go out); it needs no attention.

## Earlier alternative: a promo code

Before this existed, the same credits could be given with a `WELCOME1000` promo code that users
typed at signup (`/admin/billing/promocode/add/`). It still works for other campaigns, but it is
manual and cannot tell a new address from an old one, which is why this is separate.
