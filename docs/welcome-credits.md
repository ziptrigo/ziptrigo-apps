# Welcome credits with promo codes (issue #160)

Issue #160 asks for 1,000 credits to be added automatically to every genuinely new account, with
three safeguards (an email that never existed, `+` aliases treated as one address, an email
explaining why when nothing was credited). It also says that if the existing promo codes (#70) can
do it, no code change is needed.

**Verdict: partly.** A promo code can give 1,000 credits to a new account once its email is
confirmed, today, with no code change. It is not automatic, and it enforces none of the three
safeguards. Use it as a manual stand-in; the automatic version needs new code (see "What would
need building").

## What a promo code can do for this

- Grants a fixed number of credits (1 to 1,000,000) as a `Promo code` line in the credits history.
- A new user can enter it in the optional field on the register page, or arrive through a link
  that prefills it. The credits are **reserved at signup and granted when the email is confirmed**
  (`email_confirmed` signal), with an in-app notification, "Your promo code added N credits".
- Once per user per code. Optional cap across all users (`max_redemptions`), optional dates, an
  optional "only accounts created within N days" window and an optional list of allowed emails.
- Reservations for accounts that never confirm are released after 30 days.

## Creating the code

Needs a staff user with the `billing.add_promocode` permission (superusers have it).

1. Open `/admin/billing/promocode/add/`.
2. Fill in:

   | Field | Value |
   |---|---|
   | Code | `WELCOME1000` (4-32 letters, digits, hyphens; matched ignoring case) |
   | Effect | Credits |
   | Credits | `1000` |
   | Campaign | `Welcome credits` (shown in the ledger as `Promo code WELCOME1000 (Welcome credits)`) |
   | Active | on |
   | Starts at / Ends at | optional; set an end to stop the offer |
   | Max redemptions | optional budget, e.g. `500` (empty is unlimited) |
   | New accounts within days | `7` is sensible: someone who signs up without the code can still redeem it on the credits page within a week, an old account cannot |
   | Allowed emails | leave empty (anyone) |

3. Save. The code can be changed later except its code, effect and credits once somebody has
   claimed it.

For single-use codes (one per invited person) use **Create many** at
`/admin/billing/promocode/bulk/` instead: up to 1,000 codes sharing one credits value and campaign,
with an optional prefix, exportable as CSV.

## Handing it to users

- At signup: the user types `WELCOME1000` in the register page's promo field.
- By link: `https://app.ziptrigo.com/billing/redeem/WELCOME1000/`. Without a session this sends
  the visitor to sign-in with the code kept, and "Create one" forwards it to the register page
  (`/account/register/?promo=WELCOME1000`). Link to the register page directly with `?promo=` to
  skip the detour.
- Afterwards: a signed-in, confirmed user can redeem it on the credits page ("Have a promo code?").
- "Sign up with Google/Microsoft" skips the register form, so those users cannot enter the code at
  signup (and a `?promo=` prefill is lost). They redeem it on the credits page afterwards, which
  is why the "new accounts within days" window matters.

## Where it falls short of the issue

| Issue asks | With a promo code |
|---|---|
| Credited automatically on account creation | No. The user has to type the code or follow a link. |
| Only for an email that never existed, even after email changes | No. The only memory is "once per user per code"; a deleted account's email, or an address a previous user moved away from, can redeem again on a new account. |
| `email+a@g.com`, `email+b@g.com` and `email@g.com` are one person | No. Addresses are compared as typed (lower-cased); each alias is a new user. |
| Email the user when the credits were not given | No. A refusal appears on the register or redeem page ("Invalid code.", "This code has expired.", "You already redeemed this code.", "This code has been fully redeemed."). Nothing is emailed. Some refusals are silent: if the cap is reached between the register form's check and the account being created, the user sees the normal "Check your email" page, is never credited, and is told nothing. |

The first is a product choice; the other three are the real risk, since a public welcome code is
farmable with `+` aliases and throwaway addresses. Mitigations that exist today: the confirmed
email is required before any grant, the register page has rate limits and, when Turnstile is
configured (`TURNSTILE_SITE_KEY` and `TURNSTILE_SECRET_KEY`), a captcha, and `max_redemptions`
plus an end date cap the total spend. They reduce the abuse, they do not prevent it. One
side effect of a cap: signups that never confirm still hold a use for up to 30 days, so a public
capped code can be used up by throwaway signups and lock real users out meanwhile.

## What would need building for the automatic version

1. **A welcome grant on confirmation.** A receiver on `accounts.signals.email_confirmed` in
   `billing` (the one `grant_reservations` already uses) that redeems a configured welcome code
   without a typed code, for example a `BillingSettings` field naming the code or a flag on
   `PromoCode`. Two traps: that signal is **not sent for Google or Microsoft signups** (those
   accounts are created already confirmed), so either send it from those two create paths or add a
   new `account_created` signal; and it **can fire more than once per user** (a confirmed new
   address after an email change, a reset that confirms the account). Reusing
   `PromoCode`/`PromoRedemption` makes repeats harmless, since a partial unique constraint allows
   one pending or granted redemption per code and user; anything built differently needs its own
   once-per-user guard.
2. **Remembered addresses.** A table of canonical emails that have ever had an account, written at
   signup and never deleted with the user, so deleting an account or changing an email does not free
   the address. Fill it from existing users and from email changes. Store an HMAC of the
   canonical address keyed with `SECRET_KEY` (as the rate limiter and Short URLs visitor hash do),
   not the address itself: that answers "has this ever existed" without keeping personal data
   after an account is deleted.
3. **Canonicalisation.** One function mapping `local+anything@domain` to `local@domain` used for
   that table. Since it only refuses a grant and never merges accounts, strip `+tag` for every
   domain and dots only for `gmail.com` / `googlemail.com`. The existing email-case rules in `accounts` are
   the model to follow.
4. **The "not credited" email.** Sent when the check in 2 or 3 refuses, or the cap is reached, to the
   confirmed address, naming the reason and nothing sender-controlled. In-app notifications already
   exist (`core.services.notifications`); an email task would follow `accounts` and `feedback`.

Items 2 and 3 are the substance. Do them before making the grant automatic; otherwise the
automatic version is strictly easier to farm than the manual code.
