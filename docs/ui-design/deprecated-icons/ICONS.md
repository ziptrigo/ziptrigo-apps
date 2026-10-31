# Icons before the refresh (issue #214)

Every Font Awesome 6.5.1 glyph the product pages used before the frontend refresh, per app, taken from the
code as it was just before part 1 (`9d9c295`), so a later change can go back to one or stay consistent with it.
The glyph set itself is unchanged by the refresh: the product icons and the state icons stay solid (`fas`), while
the header chrome (bell, account) moved to the outlined style (`far`). The few other changes are listed at the end.

## Image files

| File | Where it is | Notes |
|---|---|---|
| `core/logo_32x32.png`, `core/logo_128x128.png` | copied here; the originals stay in `apps/core/static/core/images/` | the shared logo (header, landing page) |
| `accounts/logo_*.png` | copied here; originals in `apps/accounts/static/accounts/images/` | still used by the auth pages (`logo_full.png` is 122 KB) |
| `qr_code/logo_32x32.png`, `qr_code/logo_128x128.png` | copied here; originals in `apps/qr_code/static/qr_code/images/` | the QR code app logo |
| `apps/qr_code/static/qr_code/images/logo_full.png` | **not copied** (5.4 MB); it stays where it is | |
| `apps/core/static/core/images/theme-*.png` | **not copied**; unchanged | the system / light / dark previews on the account page |

Nothing was deleted: the logos are still referenced by templates, so the copies here are a reference, not the
live files.

## Product icons (`ProductApp.icon`)

`fas fa-paper-plane`, `fas fa-qrcode`, `fas fa-receipt`, `fas fa-link`, `fas fa-check-square`

## Glyphs per app

### Core (shell, notifications)

| Glyph | Style | Uses |
|---|---|---|
| `fa-bell` | `fas` | 2 |
| `fa-sun` | `fas` | 2 |
| `fa-calendar` | `far` | 1 |
| `fa-check` | `fas` | 1 |
| `fa-check-double` | `fas` | 1 |
| `fa-chevron-left` | `fas` | 1 |
| `fa-chevron-right` | `fas` | 1 |
| `fa-cog` | `fas` | 1 |
| `fa-hourglass-half` | `fas` | 1 |
| `fa-lock` | `fas` | 1 |
| `fa-moon` | `fas` | 1 |
| `fa-qrcode` | `fas` | 1 |
| `fa-sign-out-alt` | `fas` | 1 |
| `fa-times` | `fas` | 1 |
| `fa-user` | `fas` | 1 |
| `fa-user-circle` | `fas` | 1 |

### Accounts

| Glyph | Style | Uses |
|---|---|---|
| `fa-spinner` | `fas` | 7 |
| `fa-arrow-left` | `fas` | 1 |
| `fa-user-cog` | `fas` | 1 |

### Billing

| Glyph | Style | Uses |
|---|---|---|
| `fa-arrow-left` | `fas` | 2 |
| `fa-coins` | `fas` | 2 |
| `fa-file-csv` | `fas` | 1 |
| `fa-layer-group` | `fas` | 1 |
| `fa-spinner` | `fas` | 1 |
| `fa-ticket-alt` | `fas` | 1 |

### Feedback

| Glyph | Style | Uses |
|---|---|---|
| `fa-check` | `fas` | 1 |
| `fa-comment-dots` | `fas` | 1 |
| `fa-spinner` | `fas` | 1 |

### QR codes

| Glyph | Style | Uses |
|---|---|---|
| `fa-clone` | `fas` | 1 |
| `fa-download` | `fas` | 1 |
| `fa-edit` | `fas` | 1 |
| `fa-ellipsis-v` | `fas` | 1 |
| `fa-plus` | `fas` | 1 |
| `fa-qrcode` | `fas` | 1 |
| `fa-question-circle` | `fas` | 1 |
| `fa-times` | `fas` | 1 |
| `fa-trash` | `fas` | 1 |

### File transfer

| Glyph | Style | Uses |
|---|---|---|
| `fa-paper-plane` | `fas` | 4 |
| `fa-check-circle` | `fas` | 2 |
| `fa-circle-exclamation` | `fas` | 2 |
| `fa-cloud-upload-alt` | `fas` | 2 |
| `fa-copy` | `fas` | 2 |
| `fa-file` | `fas` | 2 |
| `fa-file-zipper` | `fas` | 2 |
| `fa-folder-open` | `fas` | 2 |
| `fa-ban` | `fas` | 1 |
| `fa-circle-pause` | `fas` | 1 |
| `fa-cog` | `fas` | 1 |
| `fa-download` | `fas` | 1 |
| `fa-flag` | `fas` | 1 |
| `fa-link` | `fas` | 1 |
| `fa-lock` | `fas` | 1 |
| `fa-spinner` | `fas` | 1 |
| `fa-trash` | `fas` | 1 |

### Receipts

| Glyph | Style | Uses |
|---|---|---|
| `fa-trash` | `fas` | 9 |
| `fa-circle-check` | `fas` | 6 |
| `fa-spinner` | `fas` | 5 |
| `fa-times` | `fas` | 5 |
| `fa-triangle-exclamation` | `fas` | 5 |
| `fa-arrow-left` | `fas` | 4 |
| `fa-check` | `fas` | 4 |
| `fa-code-merge` | `fas` | 4 |
| `fa-circle-info` | `fas` | 3 |
| `fa-circle-xmark` | `fas` | 3 |
| `fa-pen` | `fas` | 3 |
| `fa-turn-up` | `fas` | 3 |
| `fa-arrow-right` | `fas` | 2 |
| `fa-arrow-up-from-bracket` | `fas` | 2 |
| `fa-chevron-left` | `fas` | 2 |
| `fa-chevron-right` | `fas` | 2 |
| `fa-coins` | `fas` | 2 |
| `fa-download` | `fas` | 2 |
| `fa-envelope` | `fas` | 2 |
| `fa-file` | `fas` | 2 |
| `fa-magnifying-glass` | `fas` | 2 |
| `fa-plus` | `fas` | 2 |
| `fa-rotate` | `fas` | 2 |
| `fa-rotate-left` | `fas` | 2 |
| `fa-seedling` | `fas` | 2 |
| `fa-arrow-up-right-from-square` | `fas` | 1 |
| `fa-arrows-rotate` | `fas` | 1 |
| `fa-camera` | `fas` | 1 |
| `fa-chevron-down` | `fas` | 1 |
| `fa-chevron-up` | `fas` | 1 |
| `fa-clock-rotate-left` | `fas` | 1 |
| `fa-cloud-arrow-up` | `fas` | 1 |
| `fa-ellipsis-vertical` | `fas` | 1 |
| `fa-eye` | `fas` | 1 |
| `fa-inbox` | `fas` | 1 |
| `fa-list` | `fas` | 1 |
| `fa-pause` | `fas` | 1 |
| `fa-plug` | `fas` | 1 |
| `fa-receipt` | `fas` | 1 |
| `fa-store` | `fas` | 1 |
| `fa-xmark` | `fas` | 1 |

### Short URLs

| Glyph | Style | Uses |
|---|---|---|
| `fa-ban` | `fas` | 4 |
| `fa-lock` | `fas` | 4 |
| `fa-play` | `fas` | 4 |
| `fa-arrow-left` | `fas` | 3 |
| `fa-chart-simple` | `fas` | 3 |
| `fa-flag` | `fas` | 3 |
| `fa-pause` | `fas` | 3 |
| `fa-trash` | `fas` | 3 |
| `fa-arrow-right` | `fas` | 2 |
| `fa-coins` | `fas` | 2 |
| `fa-eye` | `fas` | 2 |
| `fa-link` | `fas` | 2 |
| `fa-pen` | `fas` | 2 |
| `fa-check` | `fas` | 1 |
| `fa-chevron-left` | `fas` | 1 |
| `fa-chevron-right` | `fas` | 1 |
| `fa-circle-exclamation` | `fas` | 1 |
| `fa-clock` | `fas` | 1 |
| `fa-download` | `fas` | 1 |
| `fa-ellipsis-v` | `fas` | 1 |
| `fa-hand` | `fas` | 1 |
| `fa-magnifying-glass` | `fas` | 1 |
| `fa-plus` | `fas` | 1 |
| `fa-qrcode` | `fas` | 1 |
| `fa-times` | `fas` | 1 |
| `fa-unlock` | `fas` | 1 |

### To-do

| Glyph | Style | Uses |
|---|---|---|
| `fa-trash` | `fas` | 12 |
| `fa-check` | `fas` | 10 |
| `fa-pen` | `fas` | 6 |
| `fa-plus` | `fas` | 4 |
| `fa-rotate-left` | `fas` | 4 |
| `fa-times` | `fas` | 4 |
| `fa-grip-vertical` | `fas` | 3 |
| `fa-paperclip` | `fas` | 3 |
| `fa-repeat` | `fas` | 3 |
| `fa-bell` | `far` | 2 |
| `fa-calendar` | `far` | 2 |
| `fa-comment` | `far` | 2 |
| `fa-arrow-left` | `fas` | 2 |
| `fa-chevron-left` | `fas` | 2 |
| `fa-chevron-right` | `fas` | 2 |
| `fa-magnifying-glass` | `fas` | 2 |
| `fa-spinner` | `fas` | 2 |
| `fa-tag` | `fas` | 2 |
| `fa-triangle-exclamation` | `fas` | 2 |
| `fa-xmark` | `fas` | 2 |
| `fa-bookmark` | `far` | 1 |
| `fa-calendar-plus` | `far` | 1 |
| `fa-arrow-` | `fas` | 1 |
| `fa-arrow-right` | `fas` | 1 |
| `fa-arrows-rotate` | `fas` | 1 |
| `fa-calendar-check` | `fas` | 1 |
| `fa-calendar-day` | `fas` | 1 |
| `fa-calendar-days` | `fas` | 1 |
| `fa-check-square` | `fas` | 1 |
| `fa-circle-check` | `fas` | 1 |
| `fa-circle-question` | `fas` | 1 |
| `fa-cloud-arrow-up` | `fas` | 1 |
| `fa-coins` | `fas` | 1 |
| `fa-ellipsis` | `fas` | 1 |
| `fa-envelope` | `fas` | 1 |
| `fa-filter` | `fas` | 1 |
| `fa-flag` | `fas` | 1 |
| `fa-gear` | `fas` | 1 |
| `fa-list` | `fas` | 1 |
| `fa-list-check` | `fas` | 1 |
| `fa-rotate` | `fas` | 1 |

## What the refresh changed

| Where | Before | After |
|---|---|---|
| Header bell | `fas fa-bell` | `far fa-bell` |
| Header account button | `fas fa-user-circle` | `far fa-user-circle` |
| To-do task circle (done) | `fas fa-check` in a bordered circle | an inline SVG circle with a drawn tick (`.check-svg`) |
| To-do priority and repeat, due date | bare coloured icon and text | the same icon inside a tinted pill |
| Empty states | text only | a `fas` icon in a round tint above the sentence |

The notification source icons (`core/templatetags/notification_tags.py`) and the product icons did not change.
