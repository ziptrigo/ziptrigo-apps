# Notes

## Scope

The Notes product's web UI (`apps/notes`, issue #260, foundation: PR 2 of 6, renewals: PR 3): Markdown notes with a
live-preview editor, tags, versions, free slots, creation billing and yearly renewals, archive and Trash:

- the sidebar (a **New note** button, then Notes, Archived, Trash, Tags; a row of scrolling links on phones);
- the lists **Notes** `/notes/` (manual drag-and-drop order, the sort bar, the tag filter, 50 per page),
  **Archived** `/notes/archived/` and **Trash** `/notes/trash/` (days left, Restore, Delete forever);
- the note page `/notes/<id>/`: title, tag chips with the "Edit tags" popover, the rendered content (coloured code
  blocks, tables, checklist boxes, links), the buttons Edit, History, Duplicate, Download, Copy, Archive, Move to trash,
  and the banners for the Trash and a locked note;
- the editor `/notes/new/` and `/notes/<id>/edit/`: the live preview (side by side from 1024 px wide, Write and Preview
  tabs below), Save, Ctrl+S / Cmd+S, the unsaved-changes warning, the cost confirmation of a note that is not free and
  the **conflict panel** of an edit that lost a race;
- history `/notes/<id>/history/` (who and when, view, restore) and the one-version page;
- tags `/notes/tags/` (add, edit in place, colour, description tooltip, reorder, delete);
- billing: the first 5 notes a user has outside the Trash are free for as long as they live; every other note costs 1
  credit to create and 5 credits every 365 days after that: the daily `notes.renew` job charges the renewal silently,
  **locks** a note whose renewal the balance can't cover (read-only, nothing deleted), unlocks it when credits arrive
  (oldest first) and warns 7 days ahead; the note page says "Free" or "Renews <date>" (NOTES-16 to NOTES-19);
- access control: logged-out visitors and another user's notes, tags and versions.

Not covered here (later sub-issues of #260): share links and the public `/n/<code>` pages (so a locked note's links
stopping is checked there), reports and takedown, search and saved filters, the JWT API and the `inv notes` CLI. The
renewal cases use shell steps: the scheduler does not run on the test server, so the daily job is run by hand. The two
emails and three notices they cause are checked in full in `emails.md` (MAIL-24, MAIL-25) and `notifications.md`
(NOTIF-27 to NOTIF-29).

## Preconditions

- The test server is running at `http://localhost:8765`, freshly restarted, as described in [`README.md`](README.md).
  Rate limiting is off.
- **Prices** are the defaults: **5 free notes**, creation price **1 credit**. `qa.user@example.com` has 100 credits,
  `qa.broke@example.com` has none. Cases that need "the free slots are full" fill them (NOTES-12) or lower **Free notes**
  in the admin (`/admin/notes/notessettings/`, as `qa.admin@example.com`, in another browser context).
- Dates and times follow the account's format (a seeded account is `mm/dd/yyyy` + 12-hour); a time on a page is shown
  in the browser's own zone.
- **Shell steps** run `python tests_e2e/ai/manage.py [--port <port>] shell -c "..."` from the repo root (see the README).
  Without a shell, mark them **BLOCKED (needs-setup)**.
- Cases run in order; later ones reuse notes created earlier. A note's id is the UUID in its URL.

## Test data

| Name | Value |
|---|---|
| Main user | `qa.user@example.com` / `QaE2e-Passw0rd!` (100 credits) |
| Broke user | `qa.broke@example.com` / `QaE2e-Passw0rd!` (0 credits) |
| Admin | `qa.admin@example.com` / `QaE2e-Passw0rd!` |
| Visitor | a private/incognito browser context where nobody is logged in |

`CONTENT_A` (type it into the editor with real line breaks):

````
# Shopping

```python
def total(prices):
    return sum(prices)
```

| item | qty |
|:-----|----:|
| eggs | 12  |
| milk | 2   |

- [ ] buy eggs
- [ ] buy milk
````

Record as you go: `N_A` (the id of `Shopping list`), `N_B`, `N_C`, ... the notes created below.

---

### NOTES-01 Access control and the empty states

**Purpose:** every page needs a login and a new account starts empty.
**Preconditions:** a brand-new account `qa.notes.<random>@example.com` (register, confirm; it gets welcome credits) is
signed in in Context A; Visitor has no session.

| # | Action | Expected result |
|---|---|---|
| 1 | Visitor: open `/notes/`, `/notes/new/`, `/notes/archived/`, `/notes/trash/`, `/notes/tags/`. | Each redirects to `/account/login/?next=...`. |
| 2 | Context A: open `/`. | The header has a **Notes** link (icon `fa-note-sticky`) and the home page a **Notes** card, "Markdown notes with code blocks, tags and share links.", opening `/notes/`. |
| 3 | Open `/notes/`. | Tab title `Notes - ZipTrigo`; heading **Notes**; the sidebar has a primary **New note** button and the entries Notes (current, highlighted, `aria-current="page"`), Archived, Trash, Tags. The list shows the empty state **No notes yet** / "Write your first note." with a **New note** button. The sort bar says **Sort by** Manual (active), Updated, Created, Title. |
| 4 | Open `/notes/archived/` and `/notes/trash/`. | **Nothing archived** with a hint; **The Trash is empty**. The Trash page says "Notes are deleted for good 30 days after being trashed. Credits spent on them are not refunded." The Archived sort bar has **Updated, Created, Title** but no **Manual**. |
| 5 | Open `/notes/tags/`. | Heading **Tags**; the list says "No tags yet. Add your first below." and a **New tag** form. |

### NOTES-02 Create a note: the editor and its live preview

**Purpose:** the editor previews Markdown as you type (colours and tables included), and saving creates a free note.
**Preconditions:** NOTES-01, Context A.

| # | Action | Expected result |
|---|---|---|
| 1 | Click **New note**. | `/notes/new/`, heading **New note**; a **Title** input; a monospace **Content (Markdown)** textarea on the left and a **Preview** pane on the right (from 1024 px wide); a hint line listing the Markdown; **Cancel** and **Save**. The preview says "Nothing to preview yet." |
| 2 | Type `Shopping list` as the title and type `CONTENT_A` into the content. | About half a second after you stop typing the preview shows a heading **Shopping**, a code block with **coloured** Python (`def` and `return` in one colour, the rest in another; the block is light gray on a light theme), a table with the header cells `item` and `qty` (the `qty` column right-aligned) and two unticked checkboxes. Nothing flickers while typing. |
| 3 | Append a line `<script>alert(1)</script>` to the content. | The preview shows it as plain text (raw HTML is never rendered); no script runs. |
| 4 | Remove that line and press **Ctrl+S** (**Cmd+S** on a Mac) with the focus in the textarea. | The note is saved exactly as if **Save** was clicked (see step 5); the browser's own "save page" dialog does not open. |
| 5 | (If step 4 did not save, click **Save**.) | The page goes to the note page `/notes/<id>/`; a flash message **Note created.**; no "leave page" warning appeared. Record `N_A`. |
| 6 | Look at the note page. | Tab title `Shopping list - Notes - ZipTrigo`; heading **Shopping list**; "Updated <time>" (in your zone); **No tags** and an **Edit tags** button; the buttons **Edit**, **History**, **Duplicate**, **Download**, **Copy**, **Archive**, **Move to trash**; the content as in the preview, with the code coloured, the table (a thin border, header cells bold) and two enabled checkboxes whose accessible names are `buy eggs` and `buy milk`. Hovering the code block shows a **Copy** button on it. |
| 7 | Narrow the window below 1024 px and open `/notes/new/`. | The editor shows two tabs, **Write** and **Preview**, instead of two columns; **Preview** shows the rendered text, **Write** the textarea. |
| 8 | On `/notes/new/`, type `x` into the title and then click the **Notes** sidebar link. | The browser asks whether to leave the page (unsaved changes). Cancel the question, clear the title (the form is as it opened) and click **Notes**: no question. |
| 9 | On `/notes/new/`, click **Save** with an empty title. | Nothing is created. The red box above the form says `Title: Give the note a title.` and the same message shows under the Title input (red border; keyboard focus on it). |
| 10 | Paste 201 characters into the Title and click **Save**. | `Title: Keep the title to 200 characters or fewer.` Nothing is created. (The input's own limit may also stop the typing at 200.) |
| 11 | Put 50,001 characters into the content (the textarea may stop at 50,000: build the text with a script `document.querySelector('#note-content').value = 'x'.repeat(50001)` and click Save). | `Content: Keep the note to 50,000 characters or fewer.` Nothing is created. |

### NOTES-03 The note page: ticking boxes, copy, download

**Purpose:** checklist boxes save on the rendered note; Copy and Download give the raw Markdown.
**Preconditions:** NOTES-02; Context A on `N_A`.

| # | Action | Expected result |
|---|---|---|
| 1 | Tick **buy eggs**. | The box stays ticked without a page reload (and the page keeps its scroll position); no message. |
| 2 | Reload the page. | **buy eggs** is ticked, **buy milk** is not. |
| 3 | Open **History** (`/notes/<id>/history/`). | Version 1 only, marked **Current**, "by You": ticking the box changed the note but, being the same editor within ten minutes, did not add a version. |
| 4 | Back on the note, click **Copy**. | The button reads **Copied** for two seconds. Paste into the title box of `/notes/new/` or any text field: the note's Markdown (starting `# Shopping`, with `- [x] buy eggs`). |
| 5 | Click the **Copy** button on the code block. | It reads **Copied**; pasting gives only the Python code (`def total(prices):` ...). |
| 6 | Click **Download**. | A file `shopping-list.md` downloads, containing the Markdown. Opening `/notes/<id>/download/` directly shows the same as an attachment. |
| 7 | Edit the page's HTML in devtools so a checkbox's `data-index` is `9`, tick it. | A red message `Item: That checklist item no longer exists.` appears above the tags; nothing changes. |

### NOTES-04 Edit a note, and the conflict panel

**Purpose:** a save of an out-of-date note is refused and the person merges by hand.
**Preconditions:** NOTES-02; Context A on `N_A`; a second tab B (same account) for the race.

| # | Action | Expected result |
|---|---|---|
| 1 | Tab A: click **Edit**. Tab B: open `/notes/<N_A>/edit/` too. | Both editors open with the current text; the preview fills in. |
| 2 | Tab B: change the content to `from tab B` and **Save**. | Saved: flash **Saved.** on the note page. |
| 3 | Tab A: change the content to `from tab A`, change the title to `Shopping list (A)` and click **Save**. | The page stays; the editor is replaced by itself with a yellow panel: **This note was changed by someone else while you were editing.**, "Your text is kept below...", a block **Current version (title: Shopping list)** with `from tab B`, a **Copy current version** button, and the buttons **Save mine anyway** and **Discard my changes**; the normal Save / Cancel buttons are hidden. The textarea still holds `from tab A` and the title `Shopping list (A)`; the preview shows `from tab A`. |
| 4 | Click **Copy current version** and paste somewhere. | `from tab B` (the stored text). |
| 5 | Click **Discard my changes**. | The editor reloads with the stored text `from tab B`; no "leave page" question (the discard was deliberate). |
| 6 | Make the conflict again (tab B saves `again from B`, tab A edits and saves). Click **Save mine anyway**. | Saved: the note page shows tab A's text and title `Shopping list (A)`. |
| 7 | Open **History**. | Versions: 1 ("by You", the first text), 2 (tab B's save, "by You"), 3 (tab A's text) and the numbers keep counting; newest first, the newest marked **Current**. (Saves by the same person within ten minutes overwrite the newest version, so fewer rows than saves is expected.) |
| 8 | Click **Edit**, change nothing, and click **Save**. | **Saved.** and the note page; no new version, "Updated" unchanged. |
| 9 | In tab A's editor, change something; in tab B press **Move to trash** on the same note; then **Save** in tab A. | `This note is in the Trash.` in the red box; nothing is saved. Restore the note from the Trash (NOTES-10 step 4) before going on. |

### NOTES-05 History: view and restore a version

**Purpose:** the owner can read an earlier version and restore it; restoring keeps what it replaced.
**Preconditions:** NOTES-04. To get versions that do not coalesce, use a shell step first (an editor's saves within ten minutes
overwrite the newest version): **(shell step)** `python tests_e2e/ai/manage.py shell -c "from datetime import UTC, datetime; from apps.notes.models import NoteVersion; NoteVersion.objects.all().update(updated_at=datetime(2020, 1, 1, tzinfo=UTC))"`.

| # | Action | Expected result |
|---|---|---|
| 1 | On `N_A` click **Edit**, set the content to `version four text`, **Save**. | Saved. |
| 2 | Open **History**. | A new top row **Version N**, **Current**, "by You"; the one below it has a **View** and a **Restore** button; the **Current** row has **View** only. |
| 3 | Click **View** on an older row. | `/notes/<id>/history/<version id>/`: that version's title and rendered content, "Version N, <time> by You", a link **Back to the history** and a **Restore this version** button (none when it is the current one). |
| 4 | Click **Restore this version**, then **Restore** in the dialog (`Restore version N?`, "The note's title and content become what they were in version N. The current version stays in the history."). | The note page with the older text and title; a flash **Restored version N.** |
| 5 | Open **History**. | One more row than before, **Current**; the text that was replaced is still listed. |
| 6 | Open another user's note's history URL (use a note id of `qa.admin`'s, NOTES-14). | A 404 page. |

### NOTES-06 Tags

**Purpose:** Notes' own tags: managed like To-do's labels, but a separate set.
**Preconditions:** Context A; the user has a To-do label named `Home` (TODO-xx or create one at `/todo/labels/`).

| # | Action | Expected result |
|---|---|---|
| 1 | `/notes/tags/`: add the tag `Work` with colour `#336699` and description `Office things`. | The list shows a row with the chip **Work** (blue), "0 notes" and the description in muted text; the form is cleared of errors. |
| 2 | Add `Home`. | It is added even though a To-do label `Home` exists (separate sets). |
| 3 | Add `work`. | Red `Name: You already have a tag with this name.` (also under the Name field). Nothing is added. |
| 4 | Add a tag with an empty name; then one named with 51 characters; then one with colour `red` (edit the input's value in devtools). | `Name: Name is required.`; `Name: Name is too long (at most 50 characters).`; `Colour: Use a colour like #8fa89e.` |
| 5 | Click the pencil on `Home`, change the name to `House` and the colour, Save. Then click the pencil and Escape. | The row updates; after the Escape the inputs go back to the saved text. |
| 6 | Hover the `Work` chip anywhere. | The tooltip shows `Office things`. |
| 7 | Tab to the drag handle of `House` and press Up. | `House moved to position 1 of 2.` (screen-reader text); the order is kept after a reload. |
| 8 | On `N_A`'s page click **Edit tags**, tick `Work` and `House`. | The chips appear next to the button as each box is ticked (no reload, the popover stays open); untick `House`: its chip goes. Press Escape or click outside: the popover closes. |
| 9 | Open `/notes/` and look at `N_A`'s row. | The chip **Work** is on the row. A **Tag** row above the list offers **All**, **House**, **Work**; clicking **Work** shows only tagged notes and the URL gets `?tag=<id>`. |
| 10 | On `/notes/tags/` the `Work` row says "1 note"; click its trash icon and confirm the dialog (`Delete tag`, "Are you sure you want to delete Work? 1 note uses it; they keep their other tags."). | The page reloads with **Deleted the tag Work. Its notes keep their other tags.**; `N_A` is intact without the chip. |
| 11 | In dark mode, look at a chip with a dark colour such as `#0000ff` (add the tag `Dark blue`). | The chip text is lightened until it is readable on the dark card. |

### NOTES-07 The list: manual order, sorts, the tag filter and paging

**Purpose:** the list is in the owner's drag-and-drop order, with other sorts and a filter available.
**Preconditions:** Context A with three more notes created from `/notes/new/`: `Alpha`, `Bravo`, `Charlie` (in that order).
A tag `Work` on `Bravo` only. Notes are free (5 free slots: `Shopping list` + 3 = 4 used).

| # | Action | Expected result |
|---|---|---|
| 1 | Open `/notes/`. | Newest first: Charlie, Bravo, Alpha, Shopping list. Each row has a **drag handle** (six dots), the title (a link), an excerpt of the text without Markdown marks (e.g. `Shopping def total(prices): return sum(prices) item qty ...`), tag chips and `Updated <time>`. The excerpt is one line and ends in `…` when long. |
| 2 | Drag Alpha above Charlie with the mouse. | The row moves with an animation; a reload keeps the order Alpha, Charlie, Bravo, Shopping list. |
| 3 | Tab to Bravo's handle (a role=button named `Reorder Bravo: use the up and down arrow keys`) and press Down. | It moves one place (to the bottom); the focus stays on the handle; `Bravo moved to position 4 of 4.` is announced and one request saves the move. Press Down again: `Bravo is already last.` and no request. |
| 4 | Click **Title** in the sort bar. | A to Z; the active sort shows an arrow icon (screen reader: `, sorted ascending`) and its tooltip says **Click to sort descending**; there are **no drag handles**; the URL is `/notes/?sort=title`. |
| 5 | Click **Title** again. | Z to A; `?sort=title&dir=desc`; the tooltip now says **Click to sort ascending**. |
| 6 | Click **Updated**, then **Created**, then **Manual**. | Updated: newest edit first (`N_A` was edited last); Created: newest first; Manual: your drag order again, with the handles, and the URL has no `sort`. |
| 7 | Click **Work** in the tag row. | Only Bravo. No drag handles (a filtered list cannot be reordered). The sort links keep `tag=`. |
| 8 | Open `/notes/?tag=junk` and `/notes/?tag=<another user's tag id>`. | The full list (an unknown tag is ignored). |
| 9 | (shell step) create 51 more notes: `python tests_e2e/ai/manage.py shell -c "from apps.accounts.models import User; from apps.notes.services import create_note; u=User.objects.get(email='qa.user@example.com'); [create_note(u, title=f'Bulk {n:02}') for n in range(51)]"` with the creation cost confirmed by the shell (the user has the credits; use `expected_cost=None`). Reload `/notes/`. | `Page 1 of 2` with 50 rows; **Next** goes to `?page=2` (via htmx, the URL updates) with the rest; **Previous** goes back. |
| 10 | Open `/notes/list/?view=trash` in the address bar. | A redirect to the full page `/notes/trash/` (the partial is only for htmx). |

### NOTES-08 Free slots and what a note costs

**Purpose:** a note created while the user has fewer than 5 notes outside the Trash is free; otherwise it costs 1 credit,
confirmed first. Trashing a free note frees its slot; a paid note never becomes free.
**Preconditions:** a **fresh** account `qa.notes.<random>@example.com` (1,000 welcome credits), Context A. Clean state: no notes.

| # | Action | Expected result |
|---|---|---|
| 1 | Create five notes `Free 1` ... `Free 5` (each: title only, **Save**). | Each saves at once, no cost question; the balance (the credits history page `/billing/credits/`) is unchanged: no ledger line from Notes. |
| 2 | Click **New note**, type `Paid 6`, click **Save**. | Nothing is created yet. A yellow panel appears above the buttons: "Your free notes are all in use, so this one costs **1 credit** to create, then 5 credits every 365 days. Your balance is <n> credits." and a button **Create the note for 1 credit**. |
| 3 | Click **Create the note for 1 credit**. | The note is created (**Note created.**); the balance is down by 1; the ledger has `Notes: "Paid 6"` (-1, source Notes). |
| 4 | Open **Duplicate** on `Free 1`. | A dialog `Duplicate this note?` "Your free notes are all in use, so the copy costs 1 credit." with **Duplicate for 1 credit** and **Cancel**. Confirm: a new note `Free 1 (copy)` opens (flash **Note duplicated.**), no tags; balance down by 1. |
| 5 | Move `Free 1` to the Trash (its page, **Move to trash**). | Flash **Note moved to the Trash.**; the note page now shows a red banner **This note is in the Trash.** with "It is deleted for good in 30 days. Restore it to edit it again." |
| 6 | Create `After trash`. | Free again: no cost question (the trashed free note freed its slot). Then create another: it asks for 1 credit again (the slots are full). |
| 7 | Open the Trash: `Free 1`'s row. | Its **Restore** button opens a dialog `Restore this note?` "Your free notes are all in use, so restoring this one makes it a paid note: it costs 1 credit." (**Restore for 1 credit**). Cancel. |
| 8 | Trash `After trash` (a free note: that frees a slot). Open the Trash: `Free 1`'s **Restore** now has no dialog; click it. | `Free 1` is restored free: flash **Note restored.**, redirected to its page, no ledger line. |
| 9 | Trash `Paid 6` and restore it. | Restored without a charge (it is paid through a year from its creation). |
| 10 | Admin (another context): set **Free notes** to `0` in `/admin/notes/notessettings/`. In Context A, open **New note**, type a title and click **Save**: the cost panel quotes 1 credit (do not confirm yet). In the admin set **Creation price** to `2`. In Context A click **Create the note for 1 credit**. | Nothing is charged or created: the panel is replaced by a new one quoting **2 credits** (the cost changed since the person saw it). Restore the settings (Free notes 5, Creation price 1). |
| 11 | As `qa.broke@example.com` (0 credits, with five notes made through a shell step) create a sixth: confirm the cost. | A red `You don't have enough credits for this note.` with a **Top up your credits** link to `/billing/credits/`; no note is created; no ledger line. The cost panel (before confirming) also shows the **Top up your credits** link next to the button. |
| 12 | As admin set **Max notes per user** to `3`; then as a user with 3 notes outside the Trash click **Save** on a new note. | `You have too many notes. Delete some first.` with no cost question; restore the setting. |

### NOTES-09 Archive and unarchive

**Purpose:** archived notes leave the main list and have their own view; nothing is lost.
**Preconditions:** Context A with notes from NOTES-07.

| # | Action | Expected result |
|---|---|---|
| 1 | On a row's menu (three dots) of `Alpha` choose **Archive**. | The page reloads on `/notes/`; **Note archived.**; Alpha is not in the list. |
| 2 | Open `/notes/archived/`. | Alpha is there (no drag handles); the sort bar has no **Manual**. The note page shows an **Archived** pill and the button **Unarchive**. |
| 3 | Edit the archived note and save. | Works (an archived note is editable). It stays in Archived. |
| 4 | Open the row menu on the Archived page: **Unarchive**. | **Note moved back to your notes.**; Alpha is on `/notes/` again in its place by position. |
| 5 | The sidebar: with `Alpha` archived, the **Archived** entry is highlighted on its page and **Notes** is not. | Right. |
| 6 | Archived notes still hold their free slot. | Archive five free notes: **New note** still asks for the cost (NOTES-08 step 2). |

### NOTES-10 Trash, restore and delete forever

**Purpose:** deleting moves a note to the Trash for 30 days; restoring brings it back; deleting forever ends it.
**Preconditions:** Context A.

| # | Action | Expected result |
|---|---|---|
| 1 | Row menu of `Charlie` > **Move to trash**. | **Note moved to the Trash.** on `/notes/`; Charlie is gone from the list. |
| 2 | Open `/notes/trash/`. | Charlie's row: "deleted in 30 days" (muted), a **Restore** button and a trash icon button. No drag handle, no sort bar, no tag row. |
| 3 | Open Charlie's page. | Red banner **This note is in the Trash.**; the buttons **Download**, **Copy**, **Restore**, **Delete forever** only (no Edit, History is also hidden, no tags popover). Its checkboxes (if any) are disabled. |
| 4 | Click **Restore**. | **Note restored.**; the note page without the banner. |
| 5 | Trash it again; on the Trash page click the trash icon. | A dialog `Delete forever`, "Are you sure you want to delete Charlie forever? This can't be undone." |
| 6 | Confirm. | **Note deleted for good.** and back on `/notes/trash/`, Charlie gone; opening its old URL is a 404. |
| 7 | Open the URL of a note that is **not** in the Trash with `/delete/` appended through a form POST (devtools). | A 404 (only a trashed note can be deleted forever). |
| 8 | (shell step) make a note look 31 days in the Trash and run the job: `python tests_e2e/ai/manage.py shell -c "from datetime import timedelta; from django.utils import timezone; from apps.notes.models import Note; from apps.notes.jobs import purge_trash; n=Note.objects.filter(title='Bravo').first(); Note.objects.filter(pk=n.pk).update(deleted_at=timezone.now()-timedelta(days=31)); purge_trash()"`. Reload `/notes/trash/`. | Bravo is gone. A note trashed 29 days ago would have stayed. |
| 9 | `/admin/core/scheduledjob/` as admin. | A job `notes.purge_trash` exists (once the scheduler has run it; its page describes it as deleting notes in the Trash longer than the Trash period). |

### NOTES-11 Duplicate

**Purpose:** a copy of a note: same content, no tags, no history, a normal new note.
**Preconditions:** Context A; a note with a tag and two versions.

| # | Action | Expected result |
|---|---|---|
| 1 | On a note with a tag, click **Duplicate** (a free slot is open, so no dialog). | The new note opens (**Note duplicated.**) titled `<title> (copy)`, same content, **No tags**, **History** shows only version 1. |
| 2 | Duplicate a note with a 200-character title. | The copy's title is cut so it still ends in ` (copy)` and is 200 characters. |
| 3 | Open a trashed note's URL and POST `/duplicate/` (devtools). | A 404. |

### NOTES-12 The cost confirmation is not bypassable

**Purpose:** credits are only spent on the amount the person saw.
**Preconditions:** a user whose free slots are full (NOTES-08 step 6), 1,000 credits.

| # | Action | Expected result |
|---|---|---|
| 1 | Open a note's page and, in devtools, change the `confirm_cost` value of the Duplicate dialog's hidden input to `0` (the dialog posts `confirm_cost`); confirm. | A red flash `Duplicating this note costs 1 credit. Confirm it from the Duplicate button to continue.`; no copy; balance unchanged. |
| 2 | Same for the Restore dialog of a trashed free note with the slots full. | `Restoring this note costs 1 credit. Confirm it from the Restore button to continue.` |
| 3 | In the editor, POST the form with `confirm_cost=7` (edit the panel button's value). | The panel is shown again quoting 1 credit; nothing is created. |

### NOTES-13 Phone width and the dark theme

**Purpose:** the pages work on a phone and in dark mode.
**Preconditions:** Context A; the window 375 px wide, then the theme set to Dark on `/account/`.

| # | Action | Expected result |
|---|---|---|
| 1 | Open `/notes/`. | The sidebar is a row of links at the top (it scrolls sideways; **New note** is full width above it); rows wrap; nothing is clipped; the row menu opens inside the screen. |
| 2 | Open a note with a wide table and a long code line. | The table and the code block scroll sideways inside the page; the page itself does not. |
| 3 | Dark mode: list, note page, editor, history and tags. | Cards are dark, the code block is dark with its own colours (not white), links are readable, tag chips readable, no white flash. |
| 4 | High contrast and another color scheme (e.g. Ember) on `/account/`, then `/notes/`. | The primary buttons follow the scheme; text stays readable. |

### NOTES-14 Another user's notes

**Purpose:** a note, tag or version that is not yours is a 404, never a 403.
**Preconditions:** MAIN has `N_A`; `qa.admin@example.com` (Context B) has a note `Admin note` and a tag.

| # | Action | Expected result |
|---|---|---|
| 1 | Context A: open `/notes/<id of Admin note>/`, `/edit/`, `/history/`, `/download/`. | Each is a 404 page. |
| 2 | Context A: in devtools POST to `/notes/<id of Admin note>/archive/`, `/trash/`, `/duplicate/`, `/tags/` with the CSRF token. | Each is a 404. |
| 3 | Context A: POST to `/notes/tags/<admin's tag id>/edit/`. | A 404. |
| 4 | Context A: ticking a tag that is not yours in the tag popover (add its id to a checkbox's value in devtools). | A red `Tags: Choose your own tags.`; nothing changes. |
| 5 | Context A: POST `/notes/reorder/` with `ids[]=<id of Admin note>`. | 204 and nothing changes for the admin's note. |

### NOTES-15 No JavaScript

**Purpose:** the pages keep working without JavaScript where they can.
**Preconditions:** JavaScript disabled in the browser.

| # | Action | Expected result |
|---|---|---|
| 1 | Open `/notes/new/`, fill the title and content, **Save**. | A normal POST; the note page with **Note created.**. The preview is empty (it needs JavaScript). |
| 2 | Save with an empty title. | The whole editor comes back (status 422) with what you typed and the red message `Title: Give the note a title.`. |
| 3 | On a note page: **Archive**, **Move to trash**, **Restore**. | Each is a plain form: a flash message and a redirect. Duplicate when a cost applies shows `Duplicating this note costs ... Confirm it from the Duplicate button to continue.` (the confirm dialog needs JavaScript). |
| 4 | The tag popover. | Its box list works with a **Save tags** button (the checkboxes do not save on their own). |

### NOTES-16 A paid note renews silently, once per period

**Purpose:** the daily job charges each paid note whose renewal date has passed, one period at a time, with a ledger
line and nothing else; free notes, notes in the Trash and notes not yet due are untouched.
**Preconditions:** a **fresh** account `NOTES_USER` = `qa.notes.renew.<random>@example.com` (1,000 welcome credits),
Context A, with `Free 1` ... `Free 5` (free) and three paid notes `Paid A`, `Paid B`, `Paid C` made as in NOTES-08
(each cost 1 credit). Record the balance as `B0` (the credits history `/billing/credits/`). `<DUE>` below is the shell
step `python tests_e2e/ai/manage.py shell -c "from datetime import timedelta; from django.utils import timezone; from apps.notes.models import Note; Note.objects.filter(user__email='<NOTES_USER>', title='<TITLE>').update(paid_through=timezone.now()-timedelta(hours=1))"`
and `<RENEW>` is `python tests_e2e/ai/manage.py shell -c "from apps.notes.jobs import renew; renew()"`.

| # | Action | Expected result |
|---|---|---|
| 1 | Open `Paid A` and `Free 1`. | Under the title, after "Updated ...", `Paid A` says **Renews <mm/dd/yyyy>** (a year from its creation); `Free 1` says **Free**. |
| 2 | Run `<RENEW>` now. | No traceback. Nothing is due: the balance is still `B0`, no ledger line from Notes since the creations. |
| 3 | Run `<DUE>` for `Paid A`, then `<RENEW>`. Reload `/billing/credits/`. | A new line `Notes renewal: "Paid A"` for **-5** (source Notes); the balance is `B0 - 5`. No notification in the bell and no email in `/__e2e__/mail/?to=<NOTES_USER>` (a successful renewal is silent). |
| 4 | Open `Paid A`. | **Renews <date>** is now one year after the old date (about a year from today minus one hour), the note is not locked and is editable. |
| 5 | Run `<RENEW>` again. | No second charge (the note is paid for a year). |
| 6 | Archive `Paid B`, run `<DUE>` for it and `<RENEW>`. | The ledger has `Notes renewal: "Paid B"` -5: archived notes renew like active ones; it is still archived. |
| 7 | Move `Paid C` to the Trash, run `<DUE>` for it and `<RENEW>`. | No charge, no lock, no notification: a note in the Trash on its renewal date is skipped. Open it: the page says **Renewal was due <date>** and the red Trash banner; the Trash row's button is **Restore for 5 credits**. |
| 8 | Click **Restore for 5 credits** and confirm the dialog ("Its renewal fell due while it was in the Trash, so restoring it costs 5 credits."). | **Note restored.**; the ledger has `Notes renewal: "Paid C"` -5; the page says **Renews <a year from now>** and is not locked. |
| 9 | Admin (Context B): `/admin/notes/notessettings/`, **Renewal price (credits)** `0`, save. Run `<DUE>` for `Paid A` and `<RENEW>`. | No new ledger line; `Paid A`'s **Renews** date moved on by a year. Restore the price to 5. |
| 10 | Admin: **Renewal days** `30`, save. Run `<DUE>` for `Paid A` and `<RENEW>`. | -5 and **Renews** about 30 days from the old date. Restore 365. |
| 11 | Set `Paid A`'s date two years back with `Note.objects.filter(...).update(paid_through=timezone.now()-timedelta(days=800))` and run `<RENEW>` twice. | Each run charges one period (-5 each) and moves the date on by a year only; after the second the date is still not in the future (the third run would charge again). |

### NOTES-17 A renewal the balance can't cover locks the note

**Purpose:** when the renewal can't be paid the job locks the note: read-only, nothing deleted, the owner told in the
app and by one email; trashing, deleting, copying and downloading still work.
**Preconditions:** a **fresh** account `LOCK_USER` = `qa.notes.lock.<random>@example.com` (1,000 welcome credits),
Context A, with `Free 1` ... `Free 5` and three paid notes `Lock A`, `Lock B`, `Lock C` (content `- [ ] one`). Admin
(Context B): `/admin/billing/credittransaction/adjust/`, User email `LOCK_USER`, Direction **Spend**, Amount = the
balance minus 7 (leave **7** credits). Use the `<DUE>` / `<RENEW>` steps of NOTES-16 with `LOCK_USER`.

| # | Action | Expected result |
|---|---|---|
| 1 | Run `<DUE>` for `Lock A`, `Lock B` and `Lock C` (set `Lock A` two days back, `Lock B` one day back, `Lock C` one hour back), then `<RENEW>`. | No traceback. With 7 credits: `Lock A` (the oldest) is renewed for 5 (the ledger line `Notes renewal: "Lock A"`), the other two are locked; the balance is 2. |
| 2 | Open `Lock B`. | An amber banner **This note is locked until its renewal is paid.** "You can still read, copy, download and delete it. Nothing was deleted; it unlocks when you add credits." with a **Top up** button to `/billing/credits/`; under the title **Renewal unpaid since <date>**. No **Edit**, **Archive** or **Edit tags** controls; the checkbox `one` is disabled; **Download**, **Copy**, **Duplicate** and **Move to trash** are there. |
| 3 | Open `/notes/<id of Lock B>/edit/` directly and press Save after a change. | A 422 with `This note is locked until its renewal is paid.`; nothing changes. (`/notes/<id>/archive/`, `/tags/` via a form POST give the same message.) |
| 4 | `/notes/`. | The rows of `Lock B` and `Lock C` have an amber lock icon (tooltip "Locked until its renewal is paid"); `Lock A` does not. |
| 5 | The bell. | Two new notifications `Your note "Lock B" is locked` and `Your note "Lock C" is locked`, body "Its yearly renewal of 5 credits could not be paid. Top up to unlock it."; clicking one opens that note's page. |
| 6 | `/__e2e__/mail/?to=<LOCK_USER>` (reload until it arrives). | **One** email, subject `Some of your notes are locked`, naming both `Lock B` and `Lock C` (see MAIL-24). |
| 7 | Run `<RENEW>` again. | Nothing changes: no new charge, no new notification, no new email (a locked note isn't renewed again). |
| 8 | Move `Lock C` to the Trash, then restore it (the button names the cost, "Restore for 5 credits"; the balance is only 2). | The dialog opens; confirming shows the red `You don't have enough credits to restore this note.` with a **Top up your credits** link; the note stays in the Trash and locked. |
| 9 | Delete `Lock C` forever from the Trash. | **Note deleted for good.** (a locked note can be deleted). |

### NOTES-18 Topping up unlocks automatically, oldest first

**Purpose:** a top-up unlocks the user's locked notes without any click, oldest lock first, stopping at the first the
balance can't cover; the locked days are not billed; the notices go.
**Preconditions:** NOTES-17 done up to step 7 but with `Lock C` still locked (re-lock it if you deleted it: make a
new paid note, run `<DUE>` and `<RENEW>`); `LOCK_USER` has 2 credits; Admin in Context B. Make sure `Lock B` was locked
before `Lock C` (`Note.objects.filter(title='Lock B').update(locked_at=...)` a day earlier if needed).

| # | Action | Expected result |
|---|---|---|
| 1 | Admin: **Adjust credits**, User email `LOCK_USER`, Direction **Add**, Amount `3` (balance 5), **Apply**. | "Adjusted credits ...". The background task runs inline on this server. |
| 2 | Context A: reload `/notes/`. | `Lock B` (the older lock) is unlocked: no lock icon; `Lock C` is still locked. The balance is 0 and the ledger has `Notes renewal: "Lock B"` -5. |
| 3 | The bell / `/notifications/`. | `Your note "Lock B" is locked` is **gone** (deleted, not just read); `Your note "Lock C" is locked` is still there. |
| 4 | Open `Lock B`. | No banner, **Edit** is back, **Renews <a year from today>** (a new period starting now, not from the old date); the checkbox works. |
| 5 | Admin: add `10` credits. | `Lock C` is unlocked too (ledger -5), its notice is gone, the balance is 5. |
| 6 | Admin: add `1` credit (a user with no locked note). | Nothing happens to any note; no error. |
| 7 | (A trashed locked note is left alone.) Lock a note again (`<DUE>`, `<RENEW>` with a balance under 5), move it to the Trash, then add 20 credits. | The note stays in the Trash and locked (restoring it pays the renewal: NOTES-16 step 8); the balance is unchanged by the top-up. |

### NOTES-19 The daily job's snapshot rule and the Trash restore of a locked note

**Purpose:** one run does not lock a note and unlock it again at once; restoring a locked note from the Trash pays its
renewal and clears its notice.
**Preconditions:** `LOCK_USER` from NOTES-18 with the balance at 5 and no locked notes (adjust with **Spend** or **Add**);
two paid notes `Snap A`, `Snap B` (create them, 1 credit each; balance 3: add credits to get back to 5).

| # | Action | Expected result |
|---|---|---|
| 1 | Run `<DUE>` for both `Snap A` (two days back) and `Snap B` (one hour back), then `<RENEW>`. | The balance covers one renewal: `Snap A`, the older due date, is renewed (-5, balance 0) and `Snap B` is locked. It is **not** unlocked by the same run (the job never offers a resume to a note it locked itself). |
| 2 | Run `<RENEW>` again twice. | Nothing changes: no new charge, notification or email (the note was already locked before those runs, and a locked note is not renewed again; it is only unlocked when the owner can pay). |
| 3 | Move the locked `Snap B` to the Trash. Add 5 credits in the admin. Open the Trash page. | The note is still locked (a top-up leaves a trashed note alone); its row says **Restore for 5 credits** (the renewal it skipped). |
| 4 | Restore it (confirm). | **Note restored.**; the note is unlocked, **Renews <a year from now>**, the ledger has `Notes renewal: ...` -5, and its `Your note ... is locked` notification is gone. |

### NOTES-20 The renewal warning, seven days ahead

**Purpose:** a user whose balance won't cover the notes renewing in the next 7 days gets one notice and one email per
window; none when the balance covers them.
**Preconditions:** a **fresh** account `WARN_USER` = `qa.notes.warn.<random>@example.com`, five free notes and two paid
notes `Soon A`, `Soon B`; then in the admin **Spend** credits so that exactly 7 remain. `<DUE+n>` is the shell step with
`paid_through=timezone.now()+timedelta(days=n)` in place of the `-hours=1` of `<DUE>`.

| # | Action | Expected result |
|---|---|---|
| 1 | Run `<DUE+3>` for `Soon A` and `<DUE+5>` for `Soon B`; run `<RENEW>`. | Neither is renewed or locked (not due). The bell has `Notes renewal: not enough credits` with the body "2 notes renew in the next 7 days for 10 credits and your balance is 7." linking to `/billing/credits/`; one email `Your notes renew soon` (MAIL-25). |
| 2 | Run `<RENEW>` again. | No second notice and no second email (once per 7-day window). |
| 3 | Admin: add `3` credits (balance 10) and `Spend` back to 7; shell: `NotesAccount.objects.filter(user__email='<WARN_USER>').update(renewal_warned_at=timezone.now()-timedelta(days=8))` and run `<RENEW>`. | A new warning (a new window): the bell still has **one** such entry (the old one was replaced), now new again; one more email. |
| 4 | Admin: add `3` credits (balance 10, exactly the cost), reset `renewal_warned_at` as in step 3 and run `<RENEW>`. | No warning (the balance covers the renewals). |
| 5 | Move `Soon B` to the Trash, spend back to 7, reset the stamp, run `<DUE+3>` for `Soon A` and `<RENEW>`. | No warning (one renewal = 5 credits, balance 7; trashed and free notes don't count). |

## Out of scope

- Share links and the public pages (including a locked note's links showing the neutral page), reports and takedown, search and saved filters, the JWT API and the CLI (later sub-issues).
- The scheduler actually running `notes.purge_trash` and `notes.renew` on this server (NOTES-10 step 8 and NOTES-16 to NOTES-20 run the jobs from a shell).
