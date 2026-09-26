# UI design guidelines

How ZipTrigo's web pages look and behave, written down so that every product (QR codes, file
transfer, whatever comes next) and every shared page (accounts, billing) feels like one site.

The source of truth is the **QR code product**, the most finished UI in the repo:

- `apps/qr_code/templates/qr_code/dashboard.html`: list page, search, row menu, modals
- `apps/qr_code/templates/qr_code/qrcode_editor.html`: form page, fields, preview, actions
- `apps/qr_code/templates/qr_code/partials/`: HTMX partials (errors, preview, short URL)
- `apps/core/templates/core/base.html`: the shell every page extends (header, footer, theme, Tailwind config)

This document describes those pages; it doesn't redesign them. Where the current code is
inconsistent with itself, [Known deviations](#15-known-deviations-in-the-current-code) says which
variant to follow.

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

The theme button in each page's header cycles system → light → dark, like the site's. Check both
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
5. **Utility classes only.** No site stylesheet. Styling is Tailwind classes in templates. The only
   custom CSS is the admin theme (`core/css/jazzmin_custom.css`).
6. **Sentence case, plain words.** "Generate QR code", "Save", "No QR codes found."

## 2. Stack

| Concern | What | Loaded by |
|---|---|---|
| Styling | Tailwind CSS (Play CDN) with a `sage` scale and `brand-*` aliases | `core/base.html` |
| Interactivity | htmx 2.0.3; 422 responses are swapped (`htmx-config` meta tag) | `core/base.html` |
| Small client state | Alpine.js 3 (available; the qr_code pages use vanilla JS) | `core/base.html` |
| Icons | Font Awesome 6.5.1 (`fas fa-*`) | `core/base.html` |
| Font | Tailwind's default system sans stack; no web fonts | n/a |

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

### Neutrals: Tailwind `gray`

All structure (page background, surfaces, borders, body text) is Tailwind's `gray`, not sage.

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

There are no success, warning or info colours yet. When one is needed, use Tailwind's `green`,
`amber` or `blue` at the same steps (`-600` text light, `-400` text dark) and add it here.

### Contrast rules

WCAG 2.x ratios for the pairs that come up:

| Foreground on background | Ratio | Verdict |
|---|---|---|
| `gray-900` on `sage-300` (primary button) | 6.98 | ✅ Text on sage buttons is **always `text-gray-900`** |
| `white` on `sage-300` | 2.54 | ❌ Never white text on sage |
| `sage-300` on `white` | 2.54 | ❌ Not for text on light backgrounds; OK for focus rings, borders, icons, checkboxes |
| `sage-600` on `white` | 7.35 | ✅ Use for brand-coloured text on light |
| `sage-700` on `white` | 9.31 | ✅ Brand headings (light) |
| `sage-300` on `gray-800` / `gray-900` | 5.78 / 6.98 | ✅ Brand text in dark mode |
| `gray-500` on `white` | 4.83 | ✅ Muted text, minimum for small text |
| `red-600` on `white` | 4.83 | ✅ |
| `red-400` on `gray-800` | 5.31 | ✅ |

## 4. Dark mode

- `tailwind.config.darkMode = 'class'`. The early-boot script in `base.html` adds `dark` to
  `<html>` before CSS loads to avoid a flash.
- The user picks system / light / dark with the header toggle, stored in `localStorage.theme`.
  "System" follows `prefers-color-scheme` live.
- **Rule:** write the light class and its `dark:` twin next to each other, using the table
  above: `bg-white dark:bg-gray-800`, `text-gray-500 dark:text-gray-400`,
  `border-gray-300 dark:border-gray-600`.
- Dark mode steps one gray level lighter per level of elevation: page `gray-900` → surface
  `gray-800` → hover `gray-700` → pressed/hover-on-hover `gray-600`. Recessed areas go back
  down to `gray-900`.

## 5. Typography

System sans (Tailwind default). Sizes come from Tailwind's scale; don't use arbitrary values.

| Role | Classes | Example |
|---|---|---|
| Landing hero | `text-4xl font-bold text-sage-700 dark:text-sage-300` | "Welcome to ZipTrigo" |
| Page title (h1) | `text-3xl font-bold text-gray-900 dark:text-gray-100` | "QR codes", "Create QR code" |
| Section title (h2) | `text-xl font-semibold` (+ `pb-2 border-b border-sage-200 dark:border-sage-700` for a ruled section) | "Your transfers" |
| Modal / card title (h3) | `text-lg font-semibold text-gray-900 dark:text-gray-100` | "Delete QR code" |
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

| | Class | Used on |
|---|---|---|
| Radius | `rounded` | Text inputs, selects, textareas, thumbnails |
| | `rounded-md` | Buttons, list card, dropdown menu and its items, search input, preview well |
| | `rounded-lg` | Modals, landing product cards, auth card |
| | `rounded-full` | Icon-only buttons |
| Shadow | `shadow-sm` | Buttons |
| | `shadow` | Header, list card, auth card |
| | `shadow-md` | Product card on hover |
| | `shadow-lg` | Dropdown menus |
| | `shadow-xl` | Modals |
| Z-index | `z-40` | Image preview overlay |
| | `z-50` | Dropdown menus, confirmation modals |

## 8. Icons

- Font Awesome 6 solid: `<i class="fas fa-…"></i>`.
- Icons next to text take a right margin: `mr-1` in nav/links, `mr-2` in menu items and buttons.
- Product icons (registered on `ProductApp.icon`): `fas fa-qrcode`, `fas fa-paper-plane`. On a
  product page header or card: `text-2xl text-sage-400`.
- Icon-only controls need `aria-label` (and usually `title`).
- Help hints: `fas fa-question-circle text-gray-500 dark:text-gray-400 cursor-help` with a
  `title` tooltip, placed after the label text.
- Common glyphs: edit `fa-edit`, duplicate `fa-clone`, delete `fa-trash`, row menu
  `fa-ellipsis-v`, clear `fa-times`, account `fa-user-circle`, busy `fa-spinner fa-spin`.

## 9. Motion

Short and functional: **150 ms, ease-out**.

- Hover colour changes: `transition-colors` (buttons, menu items, icon buttons).
- List row hover: `transition duration-150 ease-in-out`.
- Thumbnails grow on hover: `transform transition-transform duration-150 ease-out group-hover:scale-110`.
- Modals fade in and scale from 95%: overlay `transition-opacity duration-150 ease-out`, panel
  `transform transition-transform duration-150 ease-out scale-95`; the JS removes `opacity-0` /
  `scale-95` on the next frame to open, and adds them back and hides after 150 ms to close.

---

## 10. Components

Live versions and copyable markup: [`reference/components.html`](reference/components.html).

### 10.1 Buttons

All buttons share `inline-flex items-center px-4 py-2 text-sm font-medium rounded-md` plus a
`focus:outline-none focus:ring-2 focus:ring-offset-2` ring.

| Variant | When | Classes (beyond the shared base) |
|---|---|---|
| **Primary** | The one main action: Save, Generate QR code, Preview | `border border-transparent shadow-sm text-gray-900 bg-brand-primary hover:opacity-90 focus:ring-brand-primary` |
| **Secondary** | Cancel / back on a page | `border border-gray-300 dark:border-gray-600 shadow-sm text-gray-700 dark:text-gray-100 bg-white dark:bg-gray-800 hover:bg-gray-50 dark:hover:bg-gray-700 focus:ring-brand-primary` |
| **Subtle** | Cancel inside a modal | `text-gray-700 dark:text-gray-300 bg-gray-100 dark:bg-gray-700 hover:bg-gray-200 dark:hover:bg-gray-600 focus:ring-gray-500 transition-colors` |
| **Danger** | Confirming a destructive action | `text-white bg-red-600 hover:bg-red-700 focus:ring-red-500 transition-colors` |
| **Icon** | Row menu trigger, other icon-only actions | `p-2 rounded-full hover:bg-gray-200 dark:hover:bg-gray-600 focus:outline-none focus:ring-2 focus:ring-brand-primary transition-colors`, icon `text-gray-500 dark:text-gray-400`, plus `aria-label` |

Rules:

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
         class="w-full rounded border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-800 px-3 py-2 focus:outline-none focus:ring-2 focus:ring-brand-primary">
  <p class="mt-1 text-xs text-gray-500 dark:text-gray-400">Help text.</p>
</div>
```

- **Text input, textarea, select** share the control classes above. Textareas set `rows`; selects
  that hold short enums get a fixed width (`w-40`) instead of `w-full`.
- **Read-only value** (the type after creation) is a `<div>`, not a disabled input: control classes
  with `bg-gray-100 dark:bg-gray-900 text-gray-500 dark:text-gray-400` instead of the white
  background.
- **Disabled control** (content in edit mode): the same muted background plus
  `cursor-not-allowed`, and say so in the label: "Text / URL (read-only)".
- **Generated read-only input** (short URL): `readonly`, muted background,
  `text-gray-600 dark:text-gray-300 cursor-default`.
- **Checkbox**: `h-4 w-4 rounded border-gray-300 text-brand-primary focus:ring-brand-primary`,
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
- Don't use `alert()`; see [Known deviations](#15-known-deviations-in-the-current-code).

### 10.5 Search input

A GET form (`?q=`) that fills the toolbar: magnifier on the left, clear (`fa-times`) on the right,
shown only when there's a value.

- Input: `block w-full pl-10 pr-10 py-2 border border-gray-300 rounded-md leading-5 bg-white placeholder-gray-500 focus:outline-none focus:placeholder-gray-400 focus:ring-1 focus:ring-brand-primary focus:border-brand-primary sm:text-sm dark:bg-gray-700 dark:border-gray-600 dark:text-white`
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
| Card | `bg-white dark:bg-gray-800 shadow overflow-visible sm:rounded-md` (`overflow-visible` so row menus aren't clipped) |
| List | `<ul class="divide-y divide-gray-200 dark:divide-gray-700">` |
| Header row | `bg-gray-50 dark:bg-gray-900 px-4 py-3 flex items-center justify-between sm:px-6`: select-all checkbox, sort links |
| Sort link | `group flex items-center text-sm font-medium text-gray-500 hover:text-gray-700 dark:text-gray-400 dark:hover:text-gray-200` + chevron `ml-1 h-4 w-4` |
| Row | `px-4 py-4 flex items-center sm:px-6 hover:bg-gray-50 dark:hover:bg-gray-700 transition duration-150 ease-in-out` |
| Row: checkbox | `mr-4`, same checkbox classes as forms |
| Row: thumbnail | a button (opens a preview) wrapping `h-12 w-12 rounded object-cover bg-gray-100` with the hover-grow |
| Row: text | `min-w-0 flex-1 px-4`; title `text-sm font-medium truncate`, meta `mt-2 text-xs text-gray-500 dark:text-gray-400 truncate` |
| Row: actions | `relative ml-4 flex-shrink-0` wrapping an icon button + dropdown menu |
| Empty state | `<li class="px-4 py-12 text-center sm:px-6">` + `text-sm text-gray-500 dark:text-gray-400` "No QR codes found." |

Row title colour: see [Known deviations](#15-known-deviations-in-the-current-code).

### 10.8 Dropdown menu (row actions)

- Menu: `hidden absolute top-full mt-2 right-0 w-48 bg-white dark:bg-gray-800 rounded-md shadow-lg z-50 border border-gray-200 dark:border-gray-700 origin-top-right`.
- Item (`<a>` for navigation, `<button class="w-full text-left">` for actions):
  `block px-4 py-2 text-sm text-gray-700 dark:text-gray-200 hover:bg-gray-100 dark:hover:bg-gray-700 transition-colors rounded-md`, leading icon `mr-2`.
- Destructive item goes last: `text-red-600 dark:text-red-400`, and it opens a confirmation
  modal rather than acting straight away.
- Behaviour: one menu open at a time; the trigger toggles; clicking outside or pressing Esc
  closes; if the menu would overflow the viewport bottom, flip it above the trigger.

### 10.9 Confirmation modal

```
overlay  fixed inset-0 z-50 hidden bg-black/50 dark:bg-black/70 flex items-center justify-center transition-opacity duration-150 ease-out
panel    bg-white dark:bg-gray-800 rounded-lg shadow-xl max-w-md w-full mx-4 transform transition-transform duration-150 ease-out scale-95
body     p-6
title    h3  text-lg font-semibold text-gray-900 dark:text-gray-100 mb-2
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
`block p-6 rounded-lg border border-sage-200 dark:border-sage-700 bg-sage-50 dark:bg-sage-800 hover:border-sage-300 hover:shadow-md transition`,
icon `text-2xl text-sage-400`, title `text-xl font-semibold`, text `text-gray-700 dark:text-gray-300`.
This is the one place sage is used as a surface.

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
- **Theme** changes persist in `localStorage.theme` and apply to every page.

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
- Keep the `focus:ring-*` classes. Never `focus:outline-none` without a ring.
- Follow the [contrast rules](#contrast-rules). Muted text doesn't go lighter than `gray-500` on
  white or `gray-400` on `gray-800`.
- Images have `alt`: the object's name for thumbnails, empty `alt=""` for decorative logos
  next to text.
- Modals should carry `role="dialog" aria-modal="true" aria-labelledby="<title id>"` and move
  focus into the panel when opened.
- Don't rely on colour alone. Destructive items also have the trash icon and the word "Delete".

## 15. Known deviations in the current code

The qr_code pages are the reference, but a few details disagree with the rest of the design or
with the rules above. **Follow the guideline column in new work.** The existing templates are left
as they are (no code changes in this document).

| Where | Current | Guideline |
|---|---|---|
| Dashboard row title | `text-brand-primary` on white: 2.54:1, too low for `text-sm` | `text-sage-600 dark:text-sage-300` |
| Login links | `text-sage-300 underline` on white (2.54:1) | `text-sage-600 dark:text-sage-300 underline` |
| Casing | "Delete QR Code", "Track QR Code", "QR Code Preview:" | Sentence case: "Delete QR code", … |
| Editor labels | `<label>` without `for`, so not tied to its control | Add `for`/`id`, or wrap the control |
| Search / sort icons | Inline Heroicons SVGs | Font Awesome (`fa-magnifying-glass`, `fa-chevron-down`), like the rest |
| Search focus | `focus:ring-1` | `focus:ring-2`, like the other fields (keep `focus:border-brand-primary`) |
| Delete failure | `alert(…)` | Inline error in the modal or the page's message slot |
| Modals | No `role="dialog"` / focus handling | See [Accessibility](#14-accessibility) |
| Accent class name | qr_code uses `brand-primary`, accounts uses `sage-300` (same colour) | `brand-primary` for action accents (primary button, focus ring, checkbox), `sage-*` elsewhere |
| Theme toggle | Reloads the page to apply | Toggling the `dark` class works without a reload (the reference pages do this). Not a guideline change, just noted |

## 16. Checklist for a new page

- [ ] Extends `core/base.html`, sets `{% block title %}<Page> - ZipTrigo{% endblock %}`.
- [ ] Container and max width from [Layout](#6-layout-and-spacing); h1 uses the page-title classes.
- [ ] At most one primary (sage) button per view; buttons right-aligned, primary last.
- [ ] Every colour class has its `dark:` twin; checked in light **and** dark.
- [ ] Forms: labels tied to controls, one message slot, 422 error partial, `hx-disabled-elt`.
- [ ] Destructive actions go through a confirmation modal.
- [ ] Icon-only controls have `aria-label`; focus rings intact.
- [ ] Sentence-case copy; empty state written.
- [ ] Checked at phone width (stacked rows, edge-to-edge list).
