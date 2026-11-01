# UI design guidelines

How ZipTrigo's web pages look and behave, written down so that every product (QR codes, file
transfer, whatever comes next) and every shared page (accounts, billing) feels like one site.

The source of truth is the **QR code product**, the most finished UI in the repo:

- `apps/qr_code/templates/qr_code/dashboard.html`: list page, search, row menu, modals
- `apps/qr_code/templates/qr_code/qrcode_editor.html`: form page, fields, preview, actions
- `apps/qr_code/templates/qr_code/partials/`: HTMX partials (errors, preview, short URL)
- `apps/core/templates/core/base.html`: the shell every page extends (header, footer, theme, Tailwind config)

This document is the **target**: every new page and component follows it (the
[checklist](#16-checklist-for-a-new-page) at the end is what a UI pull request is reviewed against).
Where existing templates still differ,
[Known deviations](#15-known-deviations-in-the-current-code) says which variant to follow and what is
queued to change (issue #214, the refresh).

## Reference pages

`reference/` has static HTML pages that render the design with the site's own Tailwind config.
Open them straight from disk; they need network access for the CDNs, same as the site.

| Page | What it shows |
|---|---|
| [`reference/index.html`](reference/index.html) | Entry point, links to the rest |
| [`reference/foundations.html`](reference/foundations.html) | Colour, dark-mode pairs, type, spacing, radius, elevation, icons, motion |
| [`reference/components.html`](reference/components.html) | Every component, live, with an **HTML** disclosure showing the markup to copy |
| [`reference/list-page.html`](reference/list-page.html) | Full list page (the QR dashboard pattern) |
| [`reference/form-page.html`](reference/form-page.html) | Full create/edit page (the QR editor pattern), including the error state |

The theme button in each reference page's header cycles system → light → dark. (The site itself has
no such button: its theme is chosen under Appearance on the account page, issue #166.) Check both
themes before you ship a page.

`reference/assets/reference.js` holds the Tailwind config (a copy of the one in `base.html`), the
theme toggle and the demo behaviour. If the palette in `base.html` changes, update it there too.

---

## 1. Principles

1. **Quiet surfaces, sage for meaning.** Pages are Tailwind grays. Sage marks the brand (logo,
   headings on the landing page), the primary action and focus. If everything is sage, nothing
   stands out.
2. **One primary action per view.** Each page, toolbar and modal has at most one sage button.
   Everything else is secondary, subtle or a link.
3. **Every colour has a dark twin.** Dark mode is class-based (`<html class="dark">`). Any `bg-*`,
   `text-*`, `border-*`, `divide-*` or `hover:*` colour needs a `dark:` counterpart.
4. **Server-rendered, HTMX-enhanced.** Pages are Django templates. Interactivity is htmx first,
   then a little vanilla JS or Alpine. Forms post form-encoded data to session views and swap
   partials back, and they still work as plain redirects without htmx.
5. **Utility classes, plus one small site stylesheet.** Styling is Tailwind classes in templates.
   `core/css/site.css` holds only what utilities cannot say: the web fonts, the motion tokens and
   keyframes, the htmx hooks. The admin theme is `core/css/jazzmin_custom.css`.
6. **Sentence case, plain words.** "Generate QR code", "Save", "No QR codes found."
7. **Motion answers an action.** Things move because someone did something (pressed, opened,
   completed, copied) and the movement shows what changed. Nothing animates on its own, except
   the landing page's one reveal. Every motion is off under `prefers-reduced-motion`
   ([9. Motion](#9-motion)).

## 2. Stack

| Concern | What | Loaded by |
|---|---|---|
| Styling | Tailwind CSS (Play CDN, pinned to 3.4.17) with a `sage` scale, `brand-*` aliases, sage-tinted `gray` and the font stacks | `core/base.html` |
| Site CSS | `core/css/site.css`: `@font-face`, motion tokens, keyframes, htmx hooks, reduced motion | `core/base.html` |
| Interactivity | htmx 2.0.3; 422 responses are swapped (`htmx-config` meta tag) | `core/base.html` |
| Small client state | Alpine.js 3 (available; the qr_code pages use vanilla JS) | `core/base.html` |
| Icons | Font Awesome 6.5.1 (`fas fa-*`) | `core/base.html` |
| Font | **Figtree** (text) and **IBM Plex Mono** (codes, addresses), self-hosted under `core/static/core/fonts/`, latin subset, `font-display: swap`; `font-sans` / `font-mono` in the Tailwind config, with the system stack as fallback | `core/css/site.css` |

Templates and static files are always namespaced: `apps/<app>/templates/<app>/…`,
`apps/<app>/static/<app>/…`. Partials go in `templates/<app>/partials/`.

## 3. Colour

### Brand: sage

From the logos. The full scale is configured in `base.html`, and `brand-primary` / `brand-dark` are
aliases for `sage-300` / `sage-800`.

| Token | Hex | Used for |
|---|---|---|
| `sage-50` | `#f4f7f6` | Tinted card background (landing product cards) |
| `sage-100` | `#d4e0da` | Soft Mint: light dividers (`border-sage-100`) |
| `sage-200` | `#b5c7be` | Light Sage: card and section borders |
| **`sage-300`** = `brand-primary` | **`#8fa89e`** | **Sage Green: primary buttons, focus rings, checkboxes, nav hover, dark-mode brand text** |
| `sage-400` | `#728e84` | Product icons on cards and page headers |
| `sage-500` | `#5a736a` | Not used yet |
| `sage-600` | `#475a53` | Brand-coloured text on light backgrounds (see contrast) |
| `sage-700` | `#3b4a47` | Dark Slate: brand headings (light), card borders (dark) |
| `sage-800` = `brand-dark` | `#2c3432` | Deep Charcoal: tinted card background (dark) |
| `sage-900` | `#1e2422` | Not used yet |

### Neutrals: sage-tinted `gray`

All structure (page background, surfaces, borders, body text) is `gray`, not sage. The `gray` scale
is **overridden in the Tailwind config** with neutrals tinted a few degrees toward sage (Tailwind's own
gray leans blue and fights the green brand, most of all in dark mode), so every `gray-*` class in the
templates uses it; don't reach for `slate`, `zinc` or `neutral`.

| Step | Hex | Step | Hex |
|---|---|---|---|
| `gray-50` | `#f6f8f7` | `gray-600` | `#4f5956` |
| `gray-100` | `#eef1f0` | `gray-700` | `#3c4441` |
| `gray-200` | `#dfe4e2` | `gray-800` | `#282e2c` |
| `gray-300` | `#c6cecb` | `gray-900` | `#1a1f1d` |
| `gray-400` | `#98a39f` | `gray-950` | `#111514` |
| `gray-500` | `#66716d` | | |

| Role | Light | Dark |
|---|---|---|
| Page background | `bg-white` | `dark:bg-gray-900` |
| Header / footer | `bg-gray-100` | `dark:bg-gray-800` |
| Raised surface (list card, menu, modal) | `bg-white` | `dark:bg-gray-800` |
| Recessed surface (list header, read-only field) | `bg-gray-50` / `bg-gray-100` | `dark:bg-gray-900` |
| Row hover | `hover:bg-gray-50` | `dark:hover:bg-gray-700` |
| Menu item / icon button hover | `hover:bg-gray-100` / `hover:bg-gray-200` | `dark:hover:bg-gray-700` / `dark:hover:bg-gray-600` |
| Primary text | `text-gray-900` | `dark:text-gray-100` |
| Body text | `text-gray-700` | `dark:text-gray-300` |
| Muted text (help, meta, empty state) | `text-gray-500` | `dark:text-gray-400` |
| Placeholder / decorative icon | `text-gray-400` | `text-gray-400` |
| Field border | `border-gray-300` | `dark:border-gray-600` |
| Divider / menu border | `divide-gray-200`, `border-gray-200` | `dark:divide-gray-700`, `dark:border-gray-700` |

### Semantic

| Meaning | Light | Dark | Where |
|---|---|---|---|
| Danger / error | `text-red-600`, `bg-red-600 hover:bg-red-700` | `dark:text-red-400` | Delete actions, validation errors |

Status colours (first used by the Receipts status pills, [10.13](#1013-status-pills)) use Tailwind's
`green`, `amber`, `blue` and `red`, always with a `dark:` twin:

| Meaning | Dot / icon and border | Label text | Where |
|---|---|---|---|
| Success | `text-green-600` / `dark:text-green-400` | `text-green-700` / `dark:text-green-400` | Processed |
| Needs attention | `text-amber-600` / `dark:text-amber-400` | `text-amber-700` / `dark:text-amber-400` | Needs review, "storage paused" banner, duplicate banner |
| In progress, info | `text-blue-600` / `dark:text-blue-400` | `text-blue-600` / `dark:text-blue-400` | Received, Processing |
| Error | `text-red-600` / `dark:text-red-400` | `text-red-600` / `dark:text-red-400` | Failed |
| Neutral, waiting | `text-gray-500` / `dark:text-gray-400` | `text-gray-600` / `dark:text-gray-400` | Waiting for credits |

Steps are `-600` on light and `-400` on dark, with one exception: label **text** in green and amber
is `-700` on light, because `-600` on white is only 3.3:1 (green) and 3.2:1 (amber), below the 4.5:1
needed for small text (`blue-600` is 5.2:1, `red-600` 4.8:1). On dark, `green-400`, `amber-400` and
`blue-400` are 7.9, 8.3 and 5.4:1 on `gray-800`.

### Contrast rules

WCAG 2.x ratios for the pairs that come up:

| Foreground on background | Ratio | Verdict |
|---|---|---|
| `gray-900` on `sage-300` (primary button) | 6.57 | ✅ Text on sage buttons is **always `text-gray-900`** |
| `white` on `sage-300` | 2.54 | ❌ Never white text on sage |
| `sage-300` on `white` | 2.54 | ❌ Not for text on light backgrounds; OK for focus rings, borders, icons, checkboxes |
| `sage-600` on `white` | 7.35 | ✅ Use for brand-coloured text on light |
| `sage-700` on `white` | 9.31 | ✅ Brand headings (light) |
| `sage-300` on `gray-800` / `gray-900` | 5.45 / 6.57 | ✅ Brand text in dark mode |
| `gray-500` on `white` | 5.06 | ✅ Muted text, minimum for small text |
| `gray-400` on `gray-800` / `gray-900` | 5.32 / 6.42 | ✅ Muted text in dark mode, the minimum |
| `red-600` on `white` | 4.83 | ✅ |
| `red-400` on `gray-800` | 5.00 | ✅ |

## 4. Dark mode

- `tailwind.config.darkMode = 'class'`. The early-boot script in `base.html` adds `dark` to
  `<html>` before CSS loads to avoid a flash.
- The user picks system / light / dark under **Appearance** on the account page; it is stored on
  `User.theme` (so it follows the account across devices), and `base.html` renders it on `<html>`
  (`data-theme`, plus the `dark` class for "dark"). "System" follows `prefers-color-scheme` live,
  and so does a visitor without a session. Saving a new choice applies it at once, without a
  reload: the server answers with an `HX-Trigger: themeChanged` event and `base.html` toggles the
  class (Tailwind's CDN generates the `dark:` styles regardless of the class).
- **Rule:** write the light class and its `dark:` twin next to each other, using the table
  above: `bg-white dark:bg-gray-800`, `text-gray-500 dark:text-gray-400`,
  `border-gray-300 dark:border-gray-600`.
- Dark mode steps one gray level lighter per level of elevation: page `gray-900` → surface
  `gray-800` → hover `gray-700` → pressed/hover-on-hover `gray-600`. Recessed areas go back
  down to `gray-900`.

## 5. Typography

**Figtree** for all text (`font-sans`, the default on every page) and **IBM Plex Mono** for codes,
short links and inbox addresses (`font-mono`). One family keeps headings and body related; weight
and size carry the hierarchy. Use `tabular-nums` wherever digits line up (amounts, counts, times,
task numbers). Body line-height is 1.6 (set on `body` in `site.css`) and running text stays under
about 70 characters wide. Sizes come from Tailwind's scale; don't use arbitrary values.

| Role | Classes | Example |
|---|---|---|
| Landing hero | `text-4xl font-bold tracking-tight text-sage-700 dark:text-sage-300` | "Welcome to ZipTrigo" |
| Page title (h1) | `text-3xl font-semibold tracking-tight text-gray-900 dark:text-gray-100` | "QR codes", "Create QR code" |
| Section title (h2) | `text-xl font-semibold` (+ `pb-2 border-b border-sage-200 dark:border-sage-700` for a ruled section) | "Your transfers" |
| Modal title (a `p`, see 10.9) / card title (h3) | `text-lg font-semibold text-gray-900 dark:text-gray-100` | "Delete QR code" |
| Nav link | `text-lg` | Header |
| Body | default (`text-base`) | Paragraphs |
| Field label | `text-sm font-medium` | "Name" |
| Button | `text-sm font-medium` | "Save" |
| List item title | `text-sm font-medium` | QR code name |
| Secondary / meta | `text-xs text-gray-500 dark:text-gray-400` | QR content under the name, field help |
| Message / error | `text-sm` (`text-red-600` for errors) | Form messages |

Truncate single-line list content with `truncate` inside a `min-w-0` flex child.

## 6. Layout and spacing

### Page skeleton

Every page extends `core/base.html` and fills `{% block content %}`. The shell gives a sticky-footer
column (`min-h-screen flex flex-col`) with `<main class="flex-grow flex flex-col">`.

```html
<div class="container mx-auto px-4 py-8 max-w-4xl">
  <h1 class="text-3xl font-bold mb-8 text-gray-900 dark:text-gray-100">QR codes</h1>
  …
</div>
```

| Page kind | Max width | Title margin |
|---|---|---|
| List / dashboard | `max-w-4xl` | `mb-8` |
| Form (create / edit) | `max-w-3xl` | `mb-6` |
| Two-column detail (preview left, form right; stacked on phones) | `max-w-5xl` | `mb-6` |
| Sidebar + content (To-do: a `w-48` menu beside the page) | `max-w-6xl` (the content column is as wide as a list page, about `max-w-4xl`) | `mb-6` |
| Product intro page | `max-w-3xl`, `py-10` | `mb-6` |
| Auth card (login, register) | `max-w-md`, centred with `flex-grow flex items-center justify-center p-6` | `mb-6` |
| Modal panel | `max-w-md w-full mx-4` | `mb-2` |

### Vertical rhythm

- Between page-level blocks (title → toolbar → list): `mb-8`.
- Between form fields: each field wrapper is `mb-4`.
- Label → control: `mb-1` on the label. Control → help text: `mt-1` on the help.
- Before the form's action row: `mt-6`.
- Horizontal gaps: `gap-4` between toolbar items and fields in a row, `gap-3` between buttons.

### Responsive

Mobile-first; `sm:` (640px) is the one breakpoint that matters today.

- Field rows stack on mobile and go side by side at `sm`: `flex flex-col sm:flex-row sm:items-end gap-4`.
- List rows get more padding at `sm`: `px-4 sm:px-6`; the list card's corners only round at `sm`
  (`sm:rounded-md`) so it goes edge to edge on phones.
- List item content can split into columns at `md`: `md:grid md:grid-cols-2 md:gap-4`.

## 7. Shape, elevation and layering

The radius grows with the element, so the hierarchy reads from the corners alone: a pill is round,
a control is 8 px, a container 12 px, an overlay 16 px.

| Role | Class | Used on |
|---|---|---|
| Text-like elements | `rounded-full` | Pills, chips, tags, count bubbles, icon-only buttons, avatars, the task circle |
| Controls | `rounded-lg` | Buttons, text inputs, selects, textareas, menu items, nav items |
| Small things | `rounded-md` | Thumbnails, inline code, links and text buttons (the shape of their focus ring) |
| Containers | `rounded-xl` | List cards, panels, banners, dropdown menus, product cards, the auth card, dashed drop zones |
| Overlays | `rounded-2xl` | Modals, the lightbox |
| Shadow | `shadow-sm` | Containers (always with a `border border-gray-200 dark:border-gray-700`), the selected segment |
| | `shadow-md` | Product card on hover |
| | `shadow-lg` | Dropdown menus |
| | `shadow-2xl` | Modals |
| Z-index | `z-40` | Image preview overlay |
| | `z-50` | Dropdown menus, confirmation modals |

Buttons carry no shadow. A container's border does the separating, which is what holds it apart
from the page in dark mode too.

**How the radius is chosen** (it is decided by what the element *is*, not by the class it had): an `<input>`,
`<textarea>`, `<select>`, or a button or link with padding is a control (`rounded-lg`); a bordered, shadowed or tinted
`<div>`, `<ul>`, `<form>` or `<section>` is a container (`rounded-xl`); a fixed overlay panel is an overlay
(`rounded-2xl`); `<img>`, `<code>`, fixed-size boxes and padding-less links are small (`rounded-md`); anything with
`rounded-full` stays a pill. The refresh applied this by script; new markup follows the same rule by hand.

## 8. Icons

- Font Awesome 6: `<i class="fas fa-…"></i>` for state and product icons (solid says "this is a state"), `far` (outlined)
  for chrome such as the header's bell and account button. What the pages used before the refresh is kept in
  [`deprecated-icons/ICONS.md`](deprecated-icons/ICONS.md), with copies of the logo images beside it.
- Icons next to text take a right margin: `mr-1` in nav/links, `mr-2` in menu items and buttons.
- Product icons (registered on `ProductApp.icon`): `fas fa-qrcode`, `fas fa-paper-plane`. On a
  product page header or card: `text-2xl text-sage-400`.
- Icon-only controls need `aria-label` (and usually `title`).
- Help hints: `fas fa-question-circle text-gray-500 dark:text-gray-400 cursor-help` with a
  `title` tooltip, placed after the label text.
- Common glyphs: edit `fa-edit`, duplicate `fa-clone`, delete `fa-trash`, row menu
  `fa-ellipsis-v`, clear `fa-times`, account `fa-user-circle`, busy `fa-spinner fa-spin`.

## 9. Motion

The rule: **motion answers something the person did, shows what changed, and is over before they
notice it.** No entrance animations on dashboards, no hover lift on every card. The only motion that
starts by itself is the landing page's reveal (logo, headline, line and product cards rise in order
over about half a second, once per visit).

Five durations and two easings do all of it (CSS variables in `core/css/site.css`):

| Token | Value | Used for |
|---|---|---|
| `--d-press` | 90 ms | A button pressing to 98% (`.btn:active`) |
| `--d-hover` | 150 ms | Hover colour, border and shadow changes (`.btn`, `transition-colors`) |
| `--d-enter` | 200 ms | Something appearing: a menu, a flash message, a row, the tab indicator |
| `--d-exit` | 140 ms | Something leaving. Always faster than it arrived |
| `--d-modal` | 220 ms | A modal's backdrop and panel |
| `--d-moment` | 480 ms | The landing reveal only |
| `--ease-out` | `cubic-bezier(.2,.8,.2,1)` | Everything that arrives |
| `--ease-in` | `cubic-bezier(.4,0,1,1)` | Everything that leaves |

Patterns (live in [`reference/foundations.html`](reference/foundations.html) and the mock review on
issue #214):

- **Buttons**: `class="btn ..."` gives the colour transition and a 98% press. Hover darkens one step
  (primary: `sage-300` to `sage-400`); never `hover:opacity-90`, which washes the label out.
- **Focus** shows for the keyboard only (`:focus-visible`, a 2 px sage ring in `site.css`). A mouse
  click leaves no ring behind.
- **Modal**: backdrop fades in 220 ms with a light blur; the panel rises 8 px and scales from 97%;
  it leaves in 140 ms (`confirm_modal.html`).
- **Menus and dropdowns** (account menu, bell): scale from 95% at their corner and fade, 200 ms in,
  150 ms out (Alpine `x-transition`).
- **Flash messages** slide down 6 px and fade in (`animate-flash-in`), have a dismiss button and
  are never auto-dismissed, so an error is not missed.
- **Copy** buttons: the check that replaces the copy glyph pops (`data-copy-icon`, `copyText`).
  The bell badge pops when its count goes up.
- **htmx**: `htmx-added` and `htmx-swapping` are opt-in. A row that should slide in carries
  `row-enter`; one that should fade out carries `row-leave` with `hx-swap="outerHTML swap:200ms"`.
  A list that is slow to refresh shows skeleton rows ([10.20](#1020-loading-a-list)).
  Never animate a whole list on search or paging.
- **Tabs**: the underline slides to the chosen tab ([10.15](#1015-tabs)). **Completing a task**: the tick
  draws, the title strikes, the row fades out ([10.21](#1021-completing-a-task)).
- **Drop zones** tint sage and grow 1% while a file is dragged over them (`core/js/drop_zone.js`).
- **Theme change** cross-fades colours over 260 ms (`html.theming`, added by `base.html`).
- **Landing page**: `animate-rise` with an `animation-delay` per element is the one orchestrated moment.

**Reduced motion is not optional.** `site.css` sets every duration and delay to zero under
`prefers-reduced-motion: reduce` and stops the shimmer. New motion must use the tokens (or the
`animate-*` / `.btn` / `.row-*` classes that do) so it is switched off too; never a literal
`duration-` that bypasses them for something that moves. Hover-only effects go under
`@media (hover: hover)` so a tap on a phone doesn't leave one stuck (the Tailwind config sets
`future.hoverOnlyWhenSupported`, so every `hover:` utility already does).

---

## 10. Components

Live versions and copyable markup: [`reference/components.html`](reference/components.html).

### 10.1 Buttons

One recipe in two sizes. All buttons share `btn inline-flex items-center text-sm font-medium rounded-lg`
(`btn` is in `core/css/site.css`: the colour transition and the 98% press), `px-4 py-2` by default and
`px-3 py-1.5` (or `py-1`) inside a table row or a card. There are no `focus:ring-*` classes on a button:
`site.css` draws the keyboard focus ring for every button and link.

| Variant | When | Classes (beyond the shared base) |
|---|---|---|
| **Primary** | The one main action: Save, Generate QR code, Preview | `text-gray-900 bg-brand-primary hover:bg-sage-400` |
| **Secondary** | Cancel / back on a page | `border border-gray-300 dark:border-gray-600 text-gray-700 dark:text-gray-100 bg-white dark:bg-gray-800 hover:bg-gray-50 dark:hover:bg-gray-700` |
| **Subtle** | Cancel inside a modal, small row actions | `text-gray-700 dark:text-gray-300 bg-gray-100 dark:bg-gray-700 hover:bg-gray-200 dark:hover:bg-gray-600` |
| **Danger** | Confirming a destructive action | `text-white bg-red-600 hover:bg-red-700` |
| **Icon** | Row menu trigger, other icon-only actions | `btn p-2 rounded-full hover:bg-gray-200 dark:hover:bg-gray-600 transition-colors`, icon `text-gray-500 dark:text-gray-400`, plus `aria-label` |

Rules:

- Hover darkens one step (primary: `sage-300` to `sage-400`). Never `hover:opacity-90`, which washes the label out.
- Button groups right-align (`flex items-center justify-end gap-3`) with the **primary last**
  (rightmost): `[Cancel] [Save]`, `[Cancel] [Delete]`.
- Use `<a>` for navigation (Cancel → dashboard, Generate → create page), `<button>` for actions.
- Busy state: put `hx-disabled-elt="find button[type='submit']"` on htmx forms (or
  `hx-disabled-elt="this"` on an htmx button). For a visible spinner, use the `group` /
  `group-disabled:` swap from `accounts/partials/login_form.html`:
  ```html
  <button type="submit" class="group … disabled:opacity-50">
    <span class="group-disabled:hidden">Save</span>
    <span class="hidden group-disabled:flex items-center"><i class="fas fa-spinner fa-spin mr-2"></i>Saving…</span>
  </button>
  ```
- Full-width primary buttons (`w-full justify-center`) are only for single-column auth cards.

### 10.2 Form fields

```html
<div class="mb-4">
  <label for="name" class="block mb-1 text-sm font-medium">Name</label>
  <input id="name" type="text" name="name"
         class="w-full rounded-lg border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-800 px-3 py-2">
  <p class="mt-1 text-xs text-gray-500 dark:text-gray-400">Help text.</p>
</div>
```

- **Text input, textarea, select** share the control classes above. Textareas set `rows`; selects
  that hold short enums get a fixed width (`w-40`) instead of `w-full`. **No focus classes:** `site.css`
  gives every field a sage border and a soft 3 px halo on focus (no offset, so fields in a row don't
  jump), for the mouse as well as the keyboard.
- **Read-only value** (the type after creation) is a `<div>`, not a disabled input: control classes
  with `bg-gray-100 dark:bg-gray-900 text-gray-500 dark:text-gray-400` instead of the white
  background.
- **Disabled control** (content in edit mode): the same muted background plus
  `cursor-not-allowed`, and say so in the label: "Text / URL (read-only)".
- **Generated read-only input** (short URL): `readonly`, muted background,
  `text-gray-600 dark:text-gray-300 cursor-default`.
- **Checkbox**: `h-4 w-4 rounded border-gray-300 accent-sage-500` (a sage tick; `site.css` gives it a focus outline),
  inside `<label class="inline-flex items-center gap-2">` with the text in `<span class="text-sm">`.
  A dependent checkbox is `disabled` until its condition holds (Track needs type = URL).
- **Conditional fields** (short URL) start `hidden` and are revealed by JS or an htmx swap. Don't
  reserve space for them.
- Use `maxlength` / `required` for native validation, and mirror the limit in help text
  ("Up to 1000 characters.").

### 10.3 Form actions

Last block in the form: `mt-6 flex items-center justify-end gap-3` with Secondary Cancel (a link back
to the list) then Primary Save (`type="submit"`).

### 10.4 Messages and validation errors

- Each form has one message slot above the fields: `<div id="<form>-msg" class="mb-4 text-sm"></div>`,
  targeted by the form (`hx-target="#<form>-msg"`).
- On invalid input the view returns an errors partial with **status 422**, which `base.html`'s htmx
  config swaps in. The partial (`qr_code/partials/form_errors.html`) is `text-red-600`, one line per
  error as `Label: message`, non-field errors first.
- Use `HX-Retarget` when an error from a secondary request (Preview) belongs in the main slot, and
  `hx-swap-oob` to clear the slot after a later success (`qr_code/partials/preview_image.html`).
- Client-side checks (`form.checkValidity()`) write to the same slot with the same style.
- **Each field's error also goes under the field that failed**, so a person fixing a long form sees what is
  wrong where they are looking. The errors partial adds `{% field_errors <form, ErrorLines or dict> %}` (for a
  form with no message slot, also `fallback=True`, which lists the messages in a `<noscript>`), which only renders
  hidden data, one `<li data-field-error="<input name>">` per message. `core/js/field_errors.js` (loaded by
  `base.html`) then puts a
  `<p id="…-error" class="mt-1.5 text-xs text-red-600 dark:text-red-400"><i class="fas fa-circle-exclamation mr-1"></i>message</p>`
  under the control (after its wrapping label, or the `.relative` box or flex row it sits in), sets `aria-invalid="true"`
  on the control and puts the message's id first in its `aria-describedby`, ahead of any help text. `site.css` draws the
  red border and halo from `aria-invalid`, so templates add no `border-red-*` class. The message is **not** a live region (the
  slot is what is announced); the first invalid field takes the focus unless the person is already typing in one of the
  form's fields, so a person using a screen reader hears the message once, through the focus move (the label, then the
  message as the field's description), rather than twice; the message goes away, with its attributes, as soon as that
  field is edited, and all of them when the form is submitted again (the form or its submit button; a poll or a live
  check from a control inside the form leaves them alone). The slot keeps every line, `Label: message`, and is the only
  place for an error that belongs to no field or whose field has no control on the page.
  - In a view: `form_error_lines(form)` / `validation_lines(exc)` (per product, over `apps.core.field_errors`) return the
    slot's lines *with* the per-field data, so passing them to `message_response` is enough; a hand-built error is a
    dict, `{'recipients': ['Add at least one address.']}`. Input names, not labels, are the keys.
  - Under the field the message has no `Label:` prefix (the label is right above it), so it must stand on its own and
    say how to fix it (`The expiry must be in the future.`, not `Invalid.`).
  - A field whose control is not the named input (a date picker's hidden value, a mirror of a `datetime-local`) is found
    through the first visible control in the same box, or mark any element `data-error-for="<name>"`. A widget made
    of buttons marks its wrapper `data-field-wrap`, so a click inside it counts as editing the field (the date picker
    has it).
  - The data is looked for in the form it sits in, else in the form that sent the request. A slot outside any form that
    the request did not come from (a row re-rendered by a request one of its forms sent) names where to look:
    `{% field_errors lines scope="<id of the form or card>" %}`.
  - Layout: the message goes after the control's label, or its `.relative` box, or the flex/grid row it sits in. A
    `<form>` that is *itself* a flex row or a grid has no block to climb to, so the message would become one more cell:
    wrap its fields in a block (`<div>`) inside the form.
  - A single-field form that already shows its own message with the same attributes (the rename-vendor dialog, the password
    pages of a transfer and a short link, the promo-code form) doesn't need it.
- Don't use `alert()`; see [Known deviations](#15-known-deviations-in-the-current-code).

### 10.5 Search input

A GET form (`?q=`) that fills the toolbar: magnifier on the left, clear (`fa-times`) on the right,
shown only when there's a value.

- Input: `block w-full pl-10 pr-10 py-2 border border-gray-300 rounded-lg leading-5 bg-white placeholder-gray-500 focus:placeholder-gray-400 sm:text-sm dark:bg-gray-700 dark:border-gray-600 dark:text-white`
- Icons: `absolute inset-y-0 left-0 pl-3 flex items-center pointer-events-none`, `h-5 w-5 text-gray-400`.
- Clear button: `absolute inset-y-0 right-0 pr-3 … text-gray-400 hover:text-gray-600 dark:hover:text-gray-300`,
  `aria-label="Clear search"`. Clearing navigates to the unfiltered URL.
- Placeholder names the field searched: "Search by name".

### 10.6 Toolbar

`mb-8 flex items-center justify-between gap-4`: search (`flex-1`) on the left, the page's primary
action on the right.

### 10.7 List

The dashboard's "table" is a stacked list in a card, not a `<table>`:

| Part | Classes |
|---|---|
| Card | `bg-white dark:bg-gray-800 border border-gray-200 dark:border-gray-700 shadow-sm overflow-visible sm:rounded-xl` (`overflow-visible` so row menus aren't clipped) |
| List | `<ul class="divide-y divide-gray-200 dark:divide-gray-700">` |
| Header row | `bg-gray-50 dark:bg-gray-900 px-4 py-3 flex items-center justify-between sm:px-6`: select-all checkbox, sort links |
| Sort link | `group flex items-center text-sm font-medium text-gray-500 hover:text-gray-700 dark:text-gray-400 dark:hover:text-gray-200` + chevron `ml-1 h-4 w-4` |
| Row | `px-4 py-4 flex items-center sm:px-6 hover:bg-gray-50 dark:hover:bg-gray-700 transition duration-150 ease-in-out` |
| Row: checkbox | `mr-4`, same checkbox classes as forms |
| Row: thumbnail | a button (opens a preview) wrapping `h-12 w-12 rounded-md object-cover bg-gray-100` with the hover-grow |
| Row: text | `min-w-0 flex-1 px-4`; title `text-sm font-medium truncate`, meta `mt-2 text-xs text-gray-500 dark:text-gray-400 truncate` |
| Row: actions | `relative ml-4 flex-shrink-0` wrapping an icon button + dropdown menu |
| Empty state | the shared partial, [10.19](#1019-empty-states) |

A row's title is `font-medium` in the primary text colour (`text-gray-900 dark:text-gray-100`) with `hover:underline`, not
sage: brand-coloured text on white is 2.5:1.

### 10.8 Dropdown menu (row actions)

- Menu: `hidden absolute top-full mt-2 right-0 w-48 bg-white dark:bg-gray-800 rounded-xl p-1 shadow-lg z-50 border border-gray-200 dark:border-gray-700 origin-top-right`.
  It scales from 95% at its corner and fades, 200 ms in and 150 ms out (Alpine `x-transition`, the classes in `core/base.html`'s account menu).
- Item (`<a>` for navigation, `<button class="w-full text-left">` for actions):
  `block px-3 py-2 text-sm text-gray-700 dark:text-gray-200 hover:bg-gray-100 dark:hover:bg-gray-700 transition-colors rounded-lg`, leading icon `mr-2`.
- Destructive item goes last: `text-red-600 dark:text-red-400`, and it opens a confirmation
  modal rather than acting straight away.
- Behaviour: one menu open at a time; the trigger toggles; clicking outside or pressing Esc
  closes; if the menu would overflow the viewport bottom, flip it above the trigger.

### 10.9 Confirmation modal

```
overlay  fixed inset-0 z-50 hidden bg-black/50 dark:bg-black/70 backdrop-blur-[2px] flex items-center justify-center, fades in 220 ms, out 140 ms
panel    bg-white dark:bg-gray-800 rounded-2xl shadow-2xl max-w-md w-full mx-4, rises 8 px and scales from 97% (Alpine x-transition)
body     p-6
title    p   text-lg font-semibold text-gray-900 dark:text-gray-100 mb-2   (not a heading: the dialog is named by aria-labelledby)
text     p   text-sm text-gray-600 dark:text-gray-400 mb-6   (item name in font-medium text-gray-900 dark:text-gray-100)
actions  flex justify-end gap-3   [Subtle: Cancel] [Danger: Delete]
```

Close on Cancel, Esc and a backdrop click. The copy names the object: "Are you sure you want to
delete **Summer menu**?"

### 10.10 Image preview (lightbox)

For looking at an image bigger (QR thumbnails). Overlay `fixed inset-0 z-40 bg-black/60 dark:bg-black/70`;
panel `bg-gray-900 dark:bg-black p-24 rounded-lg relative`; close `×` at `absolute top-4 right-4 text-gray-300 hover:text-white text-xl`;
image `max-h-[70vh] max-w-[70vw] object-contain bg-white rounded`. The QR sits on white so it
scans in dark mode too. Same close rules as the modal.

### 10.11 Preview well

A dashed box that holds a generated preview in place, with a placeholder (the product logo)
until there is one: `flex items-center justify-center border border-dashed border-gray-300 dark:border-gray-700 rounded-md p-2 min-h-[96px] min-w-[96px]`,
image `h-24 w-24 object-contain`. The Preview button sits to its left
(`flex flex-col sm:flex-row sm:items-center sm:justify-between gap-4`).

### 10.12 Product card (landing page)

From `core/home.html`, for anything that links into a product:
`lift animate-rise block p-6 rounded-xl border border-sage-200 dark:border-sage-700 bg-sage-50 dark:bg-sage-800 hover:border-sage-300 hover:shadow-md transition` (`lift` raises it 1 px on hover, `animate-rise` is the landing reveal),
icon `text-2xl text-sage-400`, title `text-xl font-semibold`, text `text-gray-700 dark:text-gray-300`.
This is the one place sage is used as a surface.

### 10.13 Pills

One shape for every status, chip, tag, count and badge: a small `rounded-full` label with a **tinted
fill**, the darker shade of the same colour as text, and an icon when colour alone wouldn't say it.
Rendered by one shared tag, `apps/core/templatetags/core_ui.py`, so the products stop keeping their own
colour tables:

```django
{% load core_ui %}
{% pill 'green' 'Processed' icon='fas fa-check' %}
<span class="{% pill_class 'amber' %}">3</span>   {# a count or tag you write yourself #}
```

From Python, `pill_html(tone, label, icon=, title=, sr_only=)`. The product helpers
(`status_pill`, `vendor_chip`, `link_status_pill`, `due_badge`, `priority_flag`, `repeat_chip`) are thin wrappers
that choose the tone and icon.

| Tone | Light | Dark | Means |
|---|---|---|---|
| `green` | `bg-green-50 text-green-700` | `bg-green-400/10 text-green-300` | Done, healthy, today |
| `amber` | `bg-amber-50 text-amber-700` | `bg-amber-400/10 text-amber-300` | Needs attention |
| `blue` | `bg-blue-50 text-blue-700` | `bg-blue-400/10 text-blue-300` | In progress |
| `red` | `bg-red-50 text-red-700` | `bg-red-400/10 text-red-300` | Failed, overdue, priority 1 |
| `sky` | `bg-sky-50 text-sky-700` | `bg-sky-400/10 text-sky-300` | Priority 3 |
| `gray` | `bg-gray-100 text-gray-600` | `bg-gray-700 text-gray-300` | Neutral, waiting, counts, tags |

The shared shape is `inline-flex items-center whitespace-nowrap rounded-full px-2 py-0.5 text-xs font-medium`;
an icon is `text-[10px]` with `gap-1.5`. Contrast: the light pairs are at least 4.5:1 (green 4.8, amber 4.8, red 5.9, sky 5.6,
blue 6.2, gray 6.4); the dark ones are at least 6.2:1 on a `gray-800` card (blue 6.5, red 6.3, sky 6.9, gray 6.3, amber 7.8,
green 7.9) and 7.7:1 or better on the page background. A user's own label colour (To-do's `label_chip`) is the one exception: it tints with
the colour they chose.

| Status (Receipts) | Tone | Icon |
|---|---|---|
| Processed | green | `fa-check` |
| Needs review | amber | `fa-triangle-exclamation` |
| Received | blue | `fa-inbox` |
| Processing | blue | `fa-spinner fa-spin` |
| Waiting for credits | gray | `fa-coins` |
| Failed | red | `fa-circle-xmark` |

Colours and contrast: [Semantic](#semantic). A new status goes in the product's helper table, never as pill
markup written by hand.

### 10.14 Banners

A full-width notice above a page's content (Receipts: "Storage is paused", "Possible duplicate").
`rounded-xl border border-amber-600/30 dark:border-amber-400/30 bg-amber-50 dark:bg-amber-400/5 px-4 py-3`,
the icon in the status colour, body text `text-sm text-gray-700 dark:text-gray-300` with the lead
words in `font-medium text-gray-900 dark:text-gray-100`, and the action on the right as a
**Secondary** button (a banner never adds a second primary button), stacked under the text on
phones (`flex flex-col gap-3 sm:flex-row sm:items-center`). `role="alert"` for something that
blocks work, none for the rest.

### 10.15 Tabs

Two kinds, for two jobs.

**Underline tabs** filter a list, with counts: an underlined row (`border-b border-gray-200
dark:border-gray-700`, the `<nav>` carries `data-tabs`), each tab `border-b-2 px-3 py-2 text-sm font-medium`;
the current one has `border-brand-primary text-gray-900 dark:text-gray-100` and `aria-current="page"`, the others
`border-transparent text-gray-500 dark:text-gray-400` plus a hover. The count is a [pill](#1013-pills) (amber for
something that needs the user). The underline **slides** to the tab you pick (`core/js/tabs.js` remembers where it
sat before htmx swaps the bar and moves a fresh indicator from there; without JS the static underline stays).
Tabs and filters re-fetch the list with htmx and the server sends `HX-Push-Url`, so the address bar always holds
the full page's URL.

**Segmented control** chooses one of a few values (Active / Ended, 7 / 30 / 90 days): a `segmented inline-flex
rounded-lg bg-gray-100 dark:bg-gray-800 p-0.5` track; each option `btn rounded-md px-3 py-1.5 text-sm font-medium`.
The selected option is `aria-selected="true"` (a tablist) or `aria-current="true"` (links) and is styled from that
attribute (`.segmented` in `site.css`: raised, white or `gray-700`), not from classes, so `tabs.js` can move it after
an htmx click that swaps only the list below.

Underline tabs don't use `btn` (a press scale on a tab looks wrong; their hover already transitions).

### 10.16 Vendor chips

The same [pill](#1013-pills) (10.13), for the state of a vendor: **Vetted** (green,
`fa-circle-check`: the user confirmed it) and **New vendor** (neutral gray, `fa-seedling`: only what
the AI read, so it reads as a state rather than a problem; "New" in a list row). Rendered by
`{% vendor_chip vendor.vetted %}` (`receipts/templatetags/receipts_ui.py`). On a receipt's page the
chip sits inside the vendor field (`pointer-events-none absolute inset-y-0 right-2`), and the
field is an ARIA combobox: `role="combobox"` on the input, a `role="listbox"` of `role="option"`
entries that the arrow keys move through (`aria-activedescendant`), Enter picks, Escape closes.

### 10.17 Notification bell and list

The bell is the one icon in the header that carries state (`core/partials/notification_bell.html`,
signed-in users only, between the theme toggle and the account menu). The dropdown follows 10.8 and
the page follows the list (10.7); both render each notification with `partials/notification_item.html`.

- **Bell**: `fas fa-bell` in an icon button (`p-1 rounded text-2xl leading-none hover:text-sage-300`)
  with a count badge, `absolute -right-1 -top-1 rounded-full bg-amber-400 text-gray-900 text-xs
  font-semibold` (amber is "needs attention", 10.13's table; `gray-900` on `amber-400` is high
  contrast in both themes). Capped at `99+`, hidden at 0, and spoken in full (`sr-only` "N new
  notifications"), since the button's own label can't change.
- **Dropdown panel**: 10.8's menu surface, `sm:w-96` hanging under the bell from `sm`, and on a phone
  spanning the header (`inset-x-4`, so the header is `relative`). Header row (title, "Mark all as
  read" as a small sage link), a `max-h-96` scrolling list, and a "View all notifications" link as its
  footer. Opens and closes on click, Esc and an outside click, like the account menu.
- **Row** (list rows, 10.7): source icon, title (`text-sm`, `truncate` in the dropdown), optional body
  (`text-sm text-gray-600 dark:text-gray-400`, two lines in the dropdown), then source name and
  relative time as meta (`text-xs text-gray-500 dark:text-gray-400`). The title is a **stretched
  link** (`after:absolute after:inset-0`) so the whole row opens the notification, with the dismiss
  (`fa-times`) and "Mark as read" (`fa-check`) icon buttons above it (`relative z-10`). A notification
  without a URL is plain text.
- **New and read**: a new notification has a small `bg-sage-400` dot (with "New:" for screen readers),
  `font-semibold text-gray-900 dark:text-gray-100` and a tinted background (`bg-sage-50
  dark:bg-gray-700/40`); a read one is `font-medium text-gray-700 dark:text-gray-300` with none of
  that. Colour is never the only signal: the dot and the weight carry it too.
- **Page**: the list page pattern with `max-w-4xl`, a toolbar row ("5 new" or "You're all caught up."
  on the left, the **Secondary** "Mark all as read" button on the right) and the pagination links of the
  Trash page. Empty state: "No notifications yet."

### 10.18 Site header

Same surface as the page (`bg-white dark:bg-gray-900`) with a bottom border
(`border-b border-gray-200 dark:border-gray-800`), not a grey band with a shadow. The wordmark is
`text-base font-semibold`; each product is a link button (`btn rounded-lg px-2.5 py-1.5 text-sm
font-medium`) with its icon, and **the product the visitor is in is marked** (`aria-current="true"` and a
`bg-sage-100 dark:bg-sage-800` fill; `request.resolver_match.app_name` equals the product's app
label). Below `md` only the icons show. On the right, the bell and the account button are outlined
icons (`far fa-bell`, `far fa-user-circle`) in round icon buttons; the account menu is a
`rounded-xl` panel with `rounded-lg` items. Outlined (`far`) icons are for chrome, solid (`fas`)
for state (pills, banners, flash messages).

### 10.19 Empty states

An empty list is an invitation to act, not an error: one shared partial, `core/partials/empty_state.html`, with an
icon in a round sage tint, a title that says what is missing (`No receipts yet`), one line of help, and, for a list
the user hasn't started, a primary button that starts it (`Upload receipts`). After a search or filter that matched
nothing it has no button: `No receipts found` and "Try other filters or a different search." Put it in the list's own
wrapper (`<li>` or the card).

### 10.20 Loading a list

A list the user refreshes (search, a tab, a page) shows three shimmering skeleton rows if the answer takes longer than
250 ms (`core/js/skeleton.js`; mark the container `data-list`; the rows are `<template id="skeleton-rows">` in
`base.html`). A fast answer never shows them, polling never does, and an answer that isn't swapped in brings the old
list back. Don't add spinners to lists.

### 10.21 Completing a task

The To-do circle is an inline SVG whose tick draws itself in 220 ms the moment it is clicked (`data-complete`, the
`.done` class `tasks.js` puts on the row), the title strikes through left to right, and the row then leaves with a
140 ms fade (`row-leave` with `hx-swap="outerHTML swap:200ms"`). A task that was already completed when the page rendered
is drawn complete (`.is-done`) with no animation. If the request fails the row is not done after all.

---

## 11. Page templates

### List page (`reference/list-page.html`)

```
container mx-auto px-4 py-8 max-w-4xl
├─ h1 (mb-8)
├─ toolbar (mb-8): search form · primary action
├─ list card: header row · rows · empty state
└─ modals (outside the container): image preview, delete confirmation
```

### Form page (`reference/form-page.html`)

```
container mx-auto px-4 py-8 max-w-3xl
├─ h1 (mb-6): "Create …" / "Edit …"
├─ message slot (mb-4)
└─ form (hx-post, hx-target=message slot, hx-disabled-elt)
   ├─ fields (mb-4 each), option row (sm:flex-row)
   ├─ preview row (mb-6)
   └─ actions (mt-6, right-aligned): Cancel · Save
```

One template serves create and edit (`{% if object %}`): the title verb changes, fields fixed after
creation become read-only, and create-only controls (Preview, Type/Format, Track) are left out.

## 12. Interaction patterns

- **Forms:** htmx post, form-encoded, to a session-authenticated view (never `/api/`). Success →
  `hx_redirect()` (or the updated partial); invalid → 422 partial into the message slot. See
  `apps/core/htmx.py` and CLAUDE.md, "HTMX form views".
- **Server-issued values** (short codes) are fetched with `htmx.ajax` and swapped into a read-only
  input; the user can't type them.
- **Destructive actions** always go through the confirmation modal, then POST with the CSRF
  header, then reload or swap.
- **Overlays** (menus, modals) close on Esc and on outside click; only one is open at a time.
- **Theme** changes are saved on the account (`User.theme`), apply at once and to every page.

## 13. Content and copy

- **Sentence case** for titles, labels, buttons and menu items: "Create QR code", "Track QR code",
  "Select all". Product names are the exception ("QR Codes", "File Transfer", as registered on
  `ProductApp`).
- **Buttons are verbs**: Save, Preview, Delete, Generate QR code. Don't use OK, Yes or Submit.
- **Page titles** (`{% block title %}`): `<Page> - ZipTrigo`, e.g. "Dashboard - ZipTrigo".
- **Empty states**: "No <things> found." for a search that matched nothing; "No <things> yet." for
  a new account.
- **Errors**: `Field label: what's wrong`, one per line. Say how to fix it, not only that it failed.
- **Help text** gives limits and consequences: "Up to 1000 characters. Short URL is only
  meaningful when the content is a URL."
- **Confirmations** name the object and the verb matches the button: "Delete QR code" / "Are you
  sure you want to delete X?" / [Delete].

## 14. Accessibility

- Every control has a label. Pair `<label for>` with an `id`, or wrap the control in the
  `<label>`.
- Icon-only buttons have `aria-label` (see the row menu, clear search, modal close).
- Keep a visible keyboard focus on every control. `site.css` draws the sage ring for
  `:focus-visible` on buttons, links, selects and summaries; fields use a sage border and a soft
  halo. Never remove an outline without something in its place.
- Respect `prefers-reduced-motion` ([9. Motion](#9-motion)): use the motion tokens and classes.
- Follow the [contrast rules](#contrast-rules). Muted text doesn't go lighter than `gray-500` on
  white or `gray-400` on `gray-800`.
- Images have `alt`: the object's name for thumbnails, empty `alt=""` for decorative logos
  next to text.
- Modals should carry `role="dialog" aria-modal="true" aria-labelledby="<title id>"` and move
  focus into the panel when opened.
- Don't rely on colour alone. Destructive items also have the trash icon and the word "Delete".

## 15. Known deviations in the current code

The refresh (#214) brought every product page onto this document. What is left:

| Where | Current | Guideline |
|---|---|---|
| Casing | "Delete QR Code", "Track QR Code", "QR Code Preview:" | Sentence case: "Delete QR code", … |
| Editor labels | `<label>` without `for`, so not tied to its control | Add `for`/`id`, or wrap the control |
| Search / sort icons | Inline Heroicons SVGs | Font Awesome (`fa-magnifying-glass`, `fa-chevron-down`), like the rest |
| Delete failure | `alert(…)` | Inline error in the modal or the page's message slot |
| Modals | Some have no `role="dialog"` / focus handling | See [Accessibility](#14-accessibility) |

**Follow the guideline column in new work.** Admin pages (jazzmin) have their own theme and are out of scope.

## 16. Checklist for a new page

- [ ] Extends `core/base.html`, sets `{% block title %}<Page> - ZipTrigo{% endblock %}`.
- [ ] Container and max width from [Layout](#6-layout-and-spacing); h1 uses the page-title classes.
- [ ] At most one primary (sage) button per view; buttons right-aligned, primary last.
- [ ] Radius by role (pill `rounded-full`, control `rounded-lg`, container `rounded-xl`, overlay `rounded-2xl`); containers have a border and `shadow-sm`.
- [ ] Buttons are the one recipe (`btn`, `rounded-lg`, `hover:bg-sage-400` for primary); no `focus:ring-*` classes on controls.
- [ ] Statuses, counts and tags are the shared pill (`{% pill %}` / `core_ui`), never hand-written colours.
- [ ] An empty list uses the empty-state partial; a refreshing list is `data-list`.
- [ ] Every colour class has its `dark:` twin; checked in light **and** dark.
- [ ] Text is the default `font-sans` (Figtree); codes and addresses are `font-mono`; numbers that line up are `tabular-nums`.
- [ ] Motion uses the tokens and classes of [9. Motion](#9-motion) (`btn`, `animate-*`, `row-enter`); nothing moves on its own; checked with reduced motion on.
- [ ] Forms: labels tied to controls, one message slot, 422 error partial, `hx-disabled-elt`.
- [ ] The 422 partial carries each field's error (`{% field_errors %}`, [10.4](#104-messages-and-validation-errors)), so it shows under the field with `aria-invalid` and `aria-describedby`; the message says how to fix it.
- [ ] Destructive actions go through a confirmation modal.
- [ ] Icon-only controls have `aria-label`; focus rings intact.
- [ ] Sentence-case copy; empty state written.
- [ ] Checked at phone width (stacked rows, edge-to-edge list).
