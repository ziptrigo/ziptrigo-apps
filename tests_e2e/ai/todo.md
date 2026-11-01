# To-do

## Scope

The To-do product's web UI (`apps/todo`, issue #161 and its sub-issues): tasks (#178), repeating tasks (#179),
comments and task references (#180), search and saved filters (#181), reminders (#182), AI date parsing (#183),
attachments and storage billing (#184), the "Add tasks by email" settings (#186), and the polish of the E2E run
(#224):

- the sidebar (a search box, then Today with a count, Upcoming, All, Completed, Labels, Trash, Settings, then
  a **Filters** section; a top bar of scrolling rows on phones), the "Tasks are blocked" and "Storage is paused"
  banners;
- **Today** `/todo/` (the quick-add box with the free-tasks line, the Overdue section with "Reschedule overdue to
  today", today's tasks, drag-and-drop and keyboard order), **Upcoming** `/todo/upcoming/` (14 days, "Nothing due"),
  **All** `/todo/all/` (sorts and their direction, 50 per page), **Completed**, **Trash**;
- the task page `/todo/<number>/`: inline title edit, the Markdown description (checklists, `#1234` references,
  links), Complete, Postpone, Move to trash, Restore, Delete forever, then the details (the "Or type a date" box, due
  date, time, priority, labels, repeat), reminders, subtasks (add, complete, reorder), "Referenced by", then,
  outside the panel, **Attachments** and **Comments** (with their own files);
- repeating tasks (every rule the Repeat popover builds, fixed schedule and from completion, stop repeating, an
  owed next occurrence that blocks creation until a top-up settles it, the open-task cap's notice);
- comments (add, edit with a visible history, delete and restore, Markdown, files) and "Referenced by";
- attachments on a task and on a comment (uploaded straight to the local S3, Floci; Open in the browser only for a
  sniffed image or PDF; the size and per-task limits; trashed, completed and paused-storage states);
- reminders (presets, custom, edit, remove, the defaults from Settings, delivery in-app and by email, the history
  page `/todo/reminders/history/`);
- the AI date chip (with the fake parser, see Preconditions);
- **Search** `/todo/search/` (glob, regular expressions, the filter bar, number search, snippets) and **Filters**
  `/todo/filters/` (saved filters in the sidebar with counts, rename, reorder, delete, update, a deleted label);
- **Labels** `/todo/labels/` (add, edit, colour, description tooltip, reorder, delete) and **Settings**
  `/todo/settings/` (default due time, default reminder and channel, what tasks cost, the storage line, the email
  address with Copy and Regenerate, accepted senders, recent emails);
- credits: the first 10 tasks of a calendar month are free, then 10 credits each; subtasks and occurrences count;
- access control: logged-out visitors, another user's task numbers, attachments and filters.

Not covered here: inbound email itself (SES drops mail in S3 under `todo/inbound/`; the "Recent emails" list
therefore stays empty here, and the parser is unit-tested), the JWT API and the `inv todo` CLI (unit tests), the
daily trash purge and the real daily storage metering (unit tests; TODO-31 freezes the storage with a shell step
instead), a real AI model.

## Preconditions

- The test server is running at `http://localhost:8765`, freshly restarted (fresh database, empty bucket), as
  described in [`README.md`](README.md). Rate limiting is off. Tasks (emails, settling, the reminder email) run
  inline.
- **Time zone.** A seeded account has no time zone saved, but the site saves the **browser's** zone the first time
  a signed-in page loads (the account page shows it under the profile). So "today" is the browser's date. Cases
  below say "today", "tomorrow" and "yesterday"; compute the dates from the heading of **Today**'s list ("Today
  Wednesday 28 Oct"). Date inputs are `type=date`: type the date the way the browser expects (`10/28/2026` in an
  English browser).
- Prices are the defaults: **10 free tasks per calendar month**, then **10 credits** per task. Subtasks and every
  occurrence of a repeating task count. `qa.user@example.com` has 100 credits, `qa.broke@example.com` has none; an
  account registered during the run gets 1,000 welcome credits once confirmed.
- `qa.user@example.com` creates far more than 10 tasks in this file (TODO-22 alone adds 51). **Before TODO-02**, as
  `qa.admin@example.com`, add **3,000** credits to `qa.user@example.com` with the credit adjustment tool, so no later
  case runs out; the cases that check balances use fresh accounts or `qa.broke@example.com`.
- Cases run in order; later ones reuse tasks created earlier. Task numbers start at **1000** per user: the first
  task created by `qa.user@example.com` is `#1000`.
- **Email** is real SMTP read back over IMAP: open `/__e2e__/mail/?to=<address>` and reload until the message
  arrives (up to a minute). Links in it are absolute (`http://localhost:8765/...`) and clickable; the HTML body is
  shown as source.
- **Files** go to Floci (a local S3 at `http://localhost:4566`): an upload is a `PUT` from the browser to a
  presigned `localhost:4566` URL, and Download / Open redirect to one. Check what the user sees (the file
  downloads with its name and bytes, the image or PDF shows), not the URL.
- **The AI date parser is a fake** (`tests_e2e/todo_fakes.py`). It understands exactly two titles:
  `pay rent every month on the 1st at 9am` (the next 1st on or after today, 09:00, repeating monthly on the 1st,
  cleaned title `pay rent`) and `call the bank tomorrow` (tomorrow, no time, cleaned title `call the bank`). Every
  other text has no date.
- **Shell steps.** A step marked "(shell step)" runs a command from the repo root on the machine running the
  server, through the wrapper that uses the running server's exact environment (database, bucket, mail):
  `python tests_e2e/ai/manage.py [--port <port>] shell -c "..."` (pass `--port` when the server is not on 8765).
  Emails it sends show in `/__e2e__/mail/`. Without a shell, mark those steps **BLOCKED (needs-setup)**. The
  scheduler does not run on this server, so the reminder delivery job is always a shell step.
- **Admin tools** used here: the credit adjustment tool `/admin/billing/credittransaction/adjust/` (User email,
  Direction Add / Spend, Amount, Description, **Apply**; success message "Adjusted credits for <email>. New
  balance: <n>.") and To-do settings `/admin/todo/todosettings/`, both as `qa.admin@example.com`. Use another
  browser context (or `<name>2.localhost`, see README) for the admin so the user's session stays signed in.

## Test data

| Name | Value |
|---|---|
| Main user | `qa.user@example.com` / `QaE2e-Passw0rd!` (100 credits) |
| Broke user | `qa.broke@example.com` / `QaE2e-Passw0rd!` (0 credits) |
| Admin | `qa.admin@example.com` / `QaE2e-Passw0rd!` |
| Visitor | a private/incognito browser context where nobody is logged in |
| Files user (`T2`) | `qa.todo.<random>@example.com` / `TodoQa-4821!`, registered in TODO-27 (1,000 welcome credits) |

Record as you go: `T_REPORT` (the number of `Write report`), `T_CALL`, `T_LATE`, `T_FUTURE`,
`T_PARENT`, the subtasks' numbers, and from TODO-27 on `T2` (the files user's address), `T2_TASK` and the ids
noted in the cases.

### Files

Files are built in the page with JavaScript, so nothing has to exist on disk (a browser tool that can attach real
files to an `<input type="file">` may use files with the same names, sizes and bytes instead).

| Name | What it is | Content |
|---|---|---|
| `photo.png` | 120x160 light-gray PNG, 280 bytes (`PNG_A`) | base64 `PNG_A` below |
| `scan.pdf` | one-page PDF, 588 bytes (`PDF_A`) | base64 `PDF_A` below |
| `notes.txt` | plain text, 12 bytes | `hello world\n` |
| `fake.png` | text pretending to be a PNG, 15 bytes, type `image/png` | `this is no png\n` |
| `empty.txt` | 0 bytes | (nothing) |
| `big.bin` | 2 MiB of zero bytes (used once the limit is lowered to 1 MiB) | `size: 2 * 1024 * 1024` |

`PNG_A` = `iVBORw0KGgoAAAANSUhEUgAAAHgAAACgCAIAAABIaz/HAAAA30lEQVR42u3QMQEAAAwCIPtXtYQttgcikHIiCkSLRrRo0YgWjWjRohEtGtGiRSNaNKJFi0a0aESLFo1o0YgWLRrRohEtWjSiRSNatGhEi0a0aNGIFo1o0aIRLRrRokUjWjSiRYtGtGhEixaNaNGIFi0a0aIRLVo0okUjWrRoRItGtGjRiBaNaNGiES0a0aJFI1o0okWLRrRoRIsWjWjRiBYtGtGiES1aNKJFI1q0aESLRrRo0YgWjWjRohEtGtGiRSNaNKJFi0a0aESLFo1o0YgWLRrRohEtWjSiRSP6zwBnGZcT9CKujQAAAABJRU5ErkJggg==`

`PDF_A` = `JVBERi0xLjQKMSAwIG9iago8PCAvVHlwZSAvQ2F0YWxvZyAvUGFnZXMgMiAwIFIgPj4KZW5kb2JqCjIgMCBvYmoKPDwgL1R5cGUgL1BhZ2VzIC9LaWRzIFszIDAgUl0gL0NvdW50IDEgPj4KZW5kb2JqCjMgMCBvYmoKPDwgL1R5cGUgL1BhZ2UgL1BhcmVudCAyIDAgUiAvTWVkaWFCb3ggWzAgMCAyMDAgMjAwXSAvQ29udGVudHMgNCAwIFIgL1Jlc291cmNlcyA8PCAvRm9udCA8PCAvRjEgNSAwIFIgPj4gPj4gPj4KZW5kb2JqCjQgMCBvYmoKPDwgL0xlbmd0aCA0NCA+PgpzdHJlYW0KQlQgL0YxIDEyIFRmIDIwIDEwMCBUZCAoU1RBUExFUyA0Mi4xMCkgVGogRVQKZW5kc3RyZWFtCmVuZG9iago1IDAgb2JqCjw8IC9UeXBlIC9Gb250IC9TdWJ0eXBlIC9UeXBlMSAvQmFzZUZvbnQgL0hlbHZldGljYSA+PgplbmRvYmoKeHJlZgowIDYKMDAwMDAwMDAwMCA2NTUzNSBmIAowMDAwMDAwMDA5IDAwMDAwIG4gCjAwMDAwMDAwNTggMDAwMDAgbiAKMDAwMDAwMDExNSAwMDAwMCBuIAowMDAwMDAwMjQxIDAwMDAwIG4gCjAwMDAwMDAzMzUgMDAwMDAgbiAKdHJhaWxlcgo8PCAvU2l6ZSA2IC9Sb290IDEgMCBSID4+CnN0YXJ0eHJlZgo0MDUKJSVFT0YK`

Paste this helper into the task page's console once per page load (`window.todoPick` feeds the task's **Add
files** input, `window.todoPickComment` the **Attach files** input of the comment form that holds the textarea
`textareaId`: `comment-new` for the add box, `comment-text-<comment id>` for an edit form):

```js
window.PNG_A = '...'; window.PDF_A = '...';   // paste the two strings above
window.todoFiles = (specs) => {
  // spec: {name, type, base64} | {name, type, text} | {name, type, size} (size = that many zero bytes)
  const dt = new DataTransfer();
  for (const s of specs) {
    let bytes;
    if (s.base64 !== undefined) bytes = Uint8Array.from(atob(s.base64), (c) => c.charCodeAt(0));
    else if (s.text !== undefined) bytes = new TextEncoder().encode(s.text);
    else bytes = new Uint8Array(s.size);
    dt.items.add(new File([bytes], s.name, { type: s.type || '' }));
  }
  return dt.files;
};
window.todoFeed = (input, specs) => {
  input.files = todoFiles(specs);
  input.dispatchEvent(new Event('change', { bubbles: true }));
};
window.todoPick = (specs) => todoFeed(document.getElementById('attachment-file-input'), specs);
window.todoPickComment = (textareaId, specs) =>
  todoFeed(document.getElementById(textareaId).form.querySelector('[data-files-input]'), specs);
```

Examples: `todoPick([{ name: 'photo.png', type: 'image/png', base64: PNG_A }])`,
`todoPickComment('comment-new', [{ name: 'notes.txt', type: 'text/plain', text: 'hello world\n' }])`.

---

### TODO-01 Logged-out visitors are sent to the login page

**Purpose:** every To-do page needs a login.
**Preconditions:** not logged in (fresh browser session).

| # | Action | Expected result |
|---|---|---|
| 1 | Go to `http://localhost:8765/todo/`. | Redirected to `/account/login/?next=/todo/`. |
| 2 | Go to `/todo/upcoming/`, `/todo/all/`, `/todo/completed/`, `/todo/trash/`, `/todo/labels/`, `/todo/settings/`, `/todo/search/?q=rent`, `/todo/filters/`, `/todo/reminders/history/` and `/todo/1000/`. | Each redirects to the login page with its path in `next`. Nothing about any task is revealed. |
| 3 | Sign in as `qa.user@example.com` / `QaE2e-Passw0rd!` from the `/todo/` redirect. | Lands on `/todo/`. |

### TODO-02 Empty Today and navigation

**Purpose:** a user with no tasks sees a clean empty state and the whole sidebar.
**Preconditions:** logged in as `qa.user@example.com`.

| # | Action | Expected result |
|---|---|---|
| 1 | Go to `/` and click **To-do** in the top navigation (check-square icon). | Lands on `/todo/`. Tab title "Today - To-do - ZipTrigo". |
| 2 | Look at the page. | A sidebar with, from the top: a "Search tasks" box (a `?` help button, **Regex** and **Include completed** checkboxes); Today (highlighted, no count), Upcoming, All, Completed, Labels, Trash, Settings, each with an icon; then a **Filters** heading with "No saved filters". Heading "Today". A quick-add box: a "Add a task" field, an **Add task** button, Date, Time and Priority controls and an "Add labels" button, and the line "0 of 10 free tasks used this month." Below, "Today <weekday date>" with an empty state (check icon, "Nothing due today", "Add a task above."). No Overdue section. No raw `{{ }}`/`{% %}`, broken icons or console errors. |
| 3 | Click each sidebar entry in turn. | Each page opens with its own heading and a sensible empty state ("No open tasks. Add one above.", "No completed tasks yet.", "The trash is empty.", "No labels yet. Add your first below."); Upcoming shows 14 day headings, the first "Tomorrow, <d Mon>", each with "Nothing due", and a "Later: see all tasks" link; the active entry is highlighted. |

### TODO-03 Quick add

**Purpose:** a task can be added from the box and errors are shown in the box.
**Preconditions:** TODO-02 done, on `/todo/`.

| # | Action | Expected result |
|---|---|---|
| 1 | Click **Add task** with the title empty. | The browser's own "fill out this field" prompt (the field is required); nothing is sent. |
| 2 | Type `Write report`, set the date to today and Priority to **Priority 1**, click **Add task**. | The button shows a spinner and "Adding..." briefly. The task appears under "Today" with a red flag (Priority 1), a green "Today" date and `#1000` next to the title. The box is cleared (title empty, date empty, Priority None). The sidebar count beside Today becomes **1**; the free line reads "1 of 10 free tasks used this month." Record `T_REPORT`=1000. |
| 3 | Add `Call dentist` with today's date and the time `10:30`. | Appears under Today with "Today 10:30". Count 2. `#1001`. |
| 4 | Add `Overdue thing` with yesterday's date. | An **Overdue** section appears above Today with the task, its date in red ("<d Mon>", and "(overdue)" for screen readers), and a **Reschedule overdue to today** button. Count 3. `#1002`. |
| 5 | Add `Future thing` with the date 5 days from today. | It is not on Today. Count unchanged. `#1003`. |
| 6 | Type only a time (`09:00`) with no date and a title `Time only`, click **Add task**. | A red message above the fields: "Time: A time needs a date." Nothing is added; what you typed stays. |
| 7 | Type a title of 501 characters (paste), click **Add task**. | The browser stops it (`maxlength` 500) or a red "Title: ..." line appears; nothing is added. |
| 8 | Reload the page. | The same tasks, in the same order: Overdue; then Today: `Write report`, `Call dentist` (undated-time ordering: date-only before timed, so Write report first). |

### TODO-04 The task page

**Purpose:** every field of a task can be edited on its page.
**Preconditions:** TODO-03 done (`Write report` is `#1000`).

| # | Action | Expected result |
|---|---|---|
| 1 | Click `Write report` in the list. | `/todo/1000/`: a back link "All tasks"; "#1000"; the title as a heading; then, in this order, a **Description** section with **Edit** and "No description."; the buttons **Complete**, **Postpone**, **Move to trash**; a **Details** section (an "Or type a date" box with the placeholder "Type a date... (every Monday at 9am)", then Date today, Time empty, Priority 1, "Add labels", **Repeat**, **Save**); **Reminders**; a **Subtasks** section with a "New subtask" box; and, at the bottom, **Attachments** and **Comments** (a **Referenced by** section sits between Subtasks and Attachments once another task mentions this one). |
| 2 | Click the pencil beside the title, change it to `Write the report`, press the check button. | The heading becomes `Write the report` (the whole panel re-renders; no page reload) and the browser tab title becomes `Write the report - To-do - ZipTrigo`. Press Escape (or the X) while editing instead: it cancels, and opening the editor again shows the saved title, not what you typed. |
| 3 | Click **Edit** on the description. | A textarea (monospace) with a hint line listing the Markdown syntax (bold, italic, code, headings, lists, checklists, quotes, links, `#1234`). **Cancel** and **Save**. Type something extra and press **Cancel** or Escape: reopening shows the saved text. |
| 4 | Enter `Plan:` then a blank line, then `- [ ] outline`, `- [ ] draft`, a blank line, `See #1001 and https://example.com and **bold**`; click **Save**. | The description renders: "Plan:", two unchecked boxes (outline, draft), "See" with `#1001` as a link, `https://example.com` as a link, **bold** in bold. |
| 5 | Click the `draft` checkbox. | It becomes checked without reloading the page. Click **Edit**: the text now reads `- [x] draft`. Cancel. Reload: still checked. |
| 6 | Click the `#1001` link. | Opens `/todo/1001/` (`Call dentist`). Go back. |
| 7 | Hover `https://example.com`; click it. | It opens in a new tab (`target=_blank`); close it. |
| 8 | In Details, set the time to `14:00`, Priority to **Priority 3**, click **Save**. | The panel re-renders with those values. The list (open `/todo/`) shows a blue flag and "Today 14:00" for the task. |
| 9 | Clear the Date field (leave the time), click **Save**. | Date and time are both empty afterwards (clearing the date clears the time). The task leaves Today. Set the date back to today and Save. |
| 10 | Edit the description to `<script>alert(1)</script> **x**` and save. | The text appears as plain text (escaped); no alert; **x** is bold. |
| 11 | Edit the description to 20,001 characters (paste a long text). | Either the textarea stops it (`maxlength`) or a red "Description: ..." message appears; nothing is saved. |

### TODO-05 Complete and uncomplete

**Purpose:** completing takes a task off the open lists and puts it in Completed.
**Preconditions:** TODO-03 done; on `/todo/`.

| # | Action | Expected result |
|---|---|---|
| 1 | Click the circle beside `Call dentist`. | The row disappears from Today without a reload; the sidebar count drops by one. |
| 2 | Open **Completed**. | `Call dentist` with a filled circle, struck through, "Completed <date>". |
| 3 | Click its filled circle (tooltip "Uncomplete"). | The row leaves Completed. Back on Today it is open again. |
| 4 | Open `/todo/1001/` and click **Complete**. | The panel shows "Completed <date, time>" in green and an **Uncomplete** button instead of Complete/Postpone. Click **Uncomplete**: open again. |

### TODO-06 Postpone and reschedule overdue

**Purpose:** the presets, a chosen date, and moving everything overdue to today.
**Preconditions:** TODO-03 done; `Write report` due today with a time of 14:00 or none.

| # | Action | Expected result |
|---|---|---|
| 1 | On Today click the calendar-plus icon on `Write report`. | A menu: Tomorrow, Next week, Next weekend, and a "Pick a date" box with **Set**. |
| 2 | Click **Tomorrow**. | The row leaves Today (count drops). In **Upcoming** it is under "Tomorrow, <d Mon>" with "Tomorrow" as its badge and its time unchanged. |
| 3 | From its page (`/todo/1000/`) use **Postpone > Next week**. | The date becomes the **next Monday after today** (a Monday today moves it 7 days). The panel shows it; the badge reads the weekday or "<d Mon>". |
| 4 | **Postpone > Next weekend**. | The date becomes the **next Saturday after today** (a Saturday today moves it 7 days). |
| 5 | **Postpone**, pick yesterday in the date box, **Set**. | A red message "Date: Choose today or a later date."; nothing changes. (The date box also has `min` = today.) |
| 6 | **Postpone**, pick a date 3 weeks ahead, **Set**. | The task moves there; it is not in the 14-day Upcoming list but is in **All**, with that date. |
| 7 | Set it back to today via the Details form. Open `/todo/`. Click **Reschedule overdue to today**. | A confirmation dialog "Reschedule overdue tasks": "Move the date of 1 overdue task to today? Their times stay." with **Cancel** and **Reschedule**. |
| 8 | Click **Cancel**, then reopen the dialog and click **Reschedule**. | Cancel closes it and nothing changes. After Reschedule the page reloads with "1 overdue task moved to today." The Overdue section is gone and `Overdue thing` is under Today. |

### TODO-07 Subtasks

**Purpose:** subtasks are one level deep, ordered by hand, and completed with their parent after asking.
**Preconditions:** TODO-04 done; open `/todo/1000/` (`T_PARENT`).

| # | Action | Expected result |
|---|---|---|
| 1 | In **New subtask** type `Sub one` and click **Add**. Repeat for `Sub two` and `Sub three`. | Each appears in the Subtasks list (rows with a drag handle, circle, title, `#number`) and the panel re-renders. Record their numbers (`#1004`..`#1006` if nothing else was created). |
| 2 | Open **All**. | `Write report` shows a subtask badge `0/3`; each subtask row shows "in Write report". |
| 3 | Back on the task page, drag `Sub three`'s handle above `Sub one`. | The order changes at once. Reload: the order is kept. (Keyboard ordering is TODO-24.) |
| 4 | Click the circle of `Sub one`. | It becomes a filled circle and stays in the list (struck through). The badge on All reads `1/3`. |
| 5 | Open `Sub two`'s page. | "Subtask of Write report" with a link; no Subtasks section (a subtask has no subtasks). |
| 6 | On the parent's page click **Complete**. | A dialog "Complete task": "Write report has 2 open subtasks. Completing it completes them too." with **Cancel** and **Complete**. Cancel closes it. (The count is 2 even if the page was loaded before step 4: completing a subtask refreshes the parent's button.) |
| 7 | Click **Complete** again, then **Complete** in the dialog. | The page reloads showing the parent completed and all three subtasks completed. **Completed** lists all four. |
| 8 | Uncomplete only `Sub two` (its page or the Completed list). | `Sub two` is open; the parent stays completed. |
| 9 | Uncomplete the parent. | The parent is open; the other subtasks stay completed. |

### TODO-08 Labels

**Purpose:** labels have a colour and a description tooltip and apply to any task.
**Preconditions:** logged in as `qa.user@example.com`.

| # | Action | Expected result |
|---|---|---|
| 1 | Open **Labels**, add `Home` with a blue colour and the description `Around the house`. | The list shows a drag handle, a colour dot, a chip `Home` (blue text on a light blue background), "0 tasks" and the description. |
| 2 | Add `home` (lower case). | A red message "Name: You already have a label with this name."; the list is unchanged. |
| 3 | Add `Work` (any colour), then `Errands` with no description. | Both appear in order Home, Work, Errands. |
| 4 | Add a label with an empty name. | The browser's required prompt, or "Name: Name is required.". |
| 5 | Click the pencil on `Work`, change the name to `Office`, the colour, and the description, then the check button. | The row updates. Escape or the X cancels an edit without saving. |
| 6 | Drag `Errands` to the top. | The order changes and is kept after a reload. |
| 7 | Open `/todo/1002/` (`Overdue thing`) and, in Details, open **Add labels**. | A panel lists the three labels as chips with checkboxes. Tick `Home` and `Office`, click **Save**. The button reads "2 selected" afterwards and the chips are shown on the task's rows in every list. |
| 8 | Hover the `Home` chip in a list row. | The browser's native tooltip shows "Around the house" (nothing custom, nothing else). |
| 9 | Open **Labels**. | `Home` and `Office` read "1 task". |
| 10 | Click the trash icon on `Home`. | The shared confirmation dialog: "Are you sure you want to delete Home? 1 task uses it; they keep their other labels." Confirm. |
| 11 | Check `Overdue thing`. | It no longer has `Home` but still has `Office`. A flash message "Deleted the label Home. ..." was shown. |
| 12 | On Today, use **Add labels** in the quick-add box to add a task with `Office`. | The new task has the chip. |

### TODO-09 Trash, restore, delete forever

**Purpose:** soft delete with restore, no refund.
**Preconditions:** logged in as `qa.user@example.com`; note the credit balance (`/billing/credits/`).

| # | Action | Expected result |
|---|---|---|
| 1 | In a list click the trash icon on `Future thing`. | The row disappears at once, no confirmation. |
| 2 | Open **Trash**. | `Future thing` with "deleted in 30 days", a **Restore** button and a delete-forever icon. The text says tasks are deleted forever 30 days after being trashed and credits are not refunded. |
| 3 | Open its page from the Trash. | "In the trash since <date>", struck-through title, **Restore** and **Delete forever**, no edit buttons or forms; the description and details are read-only. |
| 4 | Click **Restore** on the task page. | The panel becomes the normal editable task. |
| 5 | Trash `Write report` (it has subtasks) from its page, then open **Trash**. | The parent and all of its subtasks are listed. Open the parent's page from there: it has no **Subtasks** section (they were trashed with it). |
| 6 | Restore `Sub two` from the Trash. | A red message "Restore the parent task first."; it stays in the Trash. |
| 7 | Restore the parent. | The parent and the subtasks that were trashed with it come back. |
| 8 | Trash `Future thing` again, then click its delete-forever icon in the Trash. | A dialog "Delete forever" naming the task and "This can't be undone." Confirm: it is gone from the Trash with a message "Task deleted." Its number `#1003` is never reused: the next new task gets a higher number. |
| 9 | Open `/billing/credits/`. | The balance has not changed because of trashing or deleting (nothing is refunded). |
| 10 | Open `/todo/1003/`. | A 404 page (the task is gone). |

### TODO-10 Sorting and the default order

**Purpose:** lists sort by date, then date-only before timed, then time, then priority, then oldest.
**Preconditions:** open tasks exist; create more as needed with the quick-add box.

| # | Action | Expected result |
|---|---|---|
| 1 | Make sure these open tasks exist: `A` (today, no time), `B` (today, 08:00), `C` (today 08:00, Priority 1), `D` (tomorrow), `E` (no date), `F` (no date, Priority 1). Open **All**. | Order: the date-only task of today, then timed tasks of today by time (`C` before `B`: same time, higher priority first), then tomorrow's, then undated `F` (Priority 1) before `E`. |
| 2 | Click **Priority** in "Sort by". | Priority 1 tasks first (by date), then 2, then 3, then none; the address becomes `/todo/all/?sort=priority`. |
| 2b | Click **Priority** again. | The order is reversed (priority none first, then 3, 2, 1) and the address becomes `/todo/all/?sort=priority&dir=desc`; the active link shows a down arrow with the tooltip "Click to sort ascending" (screen-reader text "sorted descending"). A third click goes back to ascending (no `dir`). |
| 3 | Click **Newest**, then **Title**. | Newest first by creation; then alphabetical (accents ignored). Clicking the active **Newest** again reverses it (oldest first). |
| 4 | Reload on `?sort=title`. | The same sort and the Title link marked as current. |
| 5 | On **Today** drag `B` above `A` using the grip handle (or focus the handle and press the up arrow, TODO-24). | The order changes. Reload: kept. Add another task for today: it goes to the end of the manual order. |
| 6 | Look at **Upcoming**. | Tasks grouped under their day headings in date order; empty days say "Nothing due"; each row has the same actions as on Today. |

### TODO-11 Credits: free allowance and price

**Purpose:** the first 10 tasks of the month are free, then 10 credits each, and a refused task costs nothing.
**Preconditions:** register and confirm a fresh account `qa.todo.credits.<random>@example.com` (password
`TodoQa-4821!`): `/account/register/`, then the link of the "Confirm your ZipTrigo account email" email in
`/__e2e__/mail/?to=<address>` and **Confirm my email**, then sign in. It has the 1,000 welcome credits and no tasks
(the seeded `qa.user` has used part of its allowance by now, so it can't show the count from 0).

| # | Action | Expected result |
|---|---|---|
| 1 | Create 10 tasks (titles `Free 1` to `Free 10`) with the quick-add box. | All succeed; the free line counts up to "10 of 10 free tasks used this month."; the balance (`/billing/credits/`) is still 1,000. |
| 2 | Create `Paid 1`. | It succeeds; the balance becomes **990**; the ledger has a line of -10 described "To-do: task #<number>" (`#1010`). |
| 3 | Add a subtask under any task. | The balance becomes 980: subtasks count as tasks. |
| 4 | Open **Settings**. | "**10 of 10** free tasks used this month." (never more than 10, although 12 tasks were created), the line "After that each task costs 10 credits.", and "Subtasks and every occurrence of a repeating task count. Credits are not refunded when a task is completed or deleted." A link "account page" for the time zone. |
| 5 | Trash and delete forever `Paid 1`. | The balance is unchanged (980). Creating another task still costs 10 (the month's count never goes down): 970. |
| 6 | Log out and log in as `qa.broke@example.com`. Create tasks until the 11th. | The first 10 succeed. The 11th is refused with a red message "You do not have enough credits to add this task." and a **Top up your credits** link to `/billing/credits/`. No task is created and the free line is unchanged. |
| 7 | As `qa.broke@example.com`, add a subtask beyond the allowance. | The same refusal in the Subtasks section's message slot. |
| 8 | Log in as `qa.admin@example.com`, add 20 credits to `qa.broke@example.com` (admin credit adjustment tool), log in as the broke user again and add a task. | It succeeds and costs 10; the balance is 10. |

### TODO-12 Access control

**Purpose:** another user's tasks are invisible.
**Preconditions:** `qa.user@example.com` has `#1000`; `qa.broke@example.com` has its own `#1000`.

| # | Action | Expected result |
|---|---|---|
| 1 | Logged in as `qa.broke@example.com`, open `/todo/1000/`. | Their own task `#1000` (numbers are per user), not `qa.user`'s. |
| 2 | Open `/todo/9999/` and `/todo/1/`. | A 404 page for both. |
| 3 | Open `/todo/labels/` and look for another user's labels. | Only your own labels. |
| 4 | As `qa.user@example.com` write `#1000` in a description of a task. | The reference links to `qa.user`'s own task; as `qa.broke@example.com` a `#1500` that doesn't exist for them stays plain text. |

### TODO-13 Settings

**Purpose:** the default due time can be changed.
**Preconditions:** logged in as `qa.user@example.com`.

| # | Action | Expected result |
|---|---|---|
| 1 | Open **Settings**. | Heading "Settings" and, in order: a "Default due time" section with a time field showing `09:00` and the note that it is the time a date-only task is treated as due and such a task is still shown date-only; "Default reminder" (TODO-21); one **Save** button for both; "What tasks cost"; "Files" ("No files stored." for this user); "Add tasks by email" (TODO-33). |
| 2 | Change it to `07:45` and **Save**. | "Saved." in green; reload shows `07:45`. |
| 3 | Clear the field and Save. | The browser's required prompt (or "Default due time: Enter a time."). |
| 4 | Click the "account page" link. | Goes to the account page where the time zone can be changed. |

### TODO-14 The blocked banner (admin-seeded)

**Purpose:** the layout shows "Tasks are blocked" while creation is blocked. Completing a repeating task without credits
reaches it (TODO-20 step 6); here it is set from the admin shell.
**Preconditions:** logged in as `qa.user@example.com`; a shell (see Preconditions: without one mark the case **BLOCKED (needs-setup)**).

| # | Action | Expected result |
|---|---|---|
| 1 | (shell step) `python tests_e2e/ai/manage.py shell -c "from django.utils import timezone; from apps.todo.models import TodoAccount; print(TodoAccount.objects.filter(user__email='qa.user@example.com').update(creation_blocked_at=timezone.now()))"` (prints `1`). Reload `/todo/`. | An amber banner "Tasks are blocked. A repeating task is waiting to be paid. Adding tasks resumes when you add credits." with a **Top up** link on every To-do page. |
| 2 | Try to add a task. | A red message "Top up your credits to add tasks: a repeating task is waiting to be paid."; nothing is created. |
| 3 | (shell step) The same command with `update(creation_blocked_at=None)`. Reload. | The banner is gone; adding works. |

### TODO-15 Admin

**Purpose:** staff can see tasks and the settings.
**Preconditions:** logged in as `qa.admin@example.com`.

| # | Action | Expected result |
|---|---|---|
| 1 | Open `/admin/` and find the **To-do** section. | Entries (any order): Attachments, Comments, Inbound emails, Inbound messages, Labels, Recurrences, Reminder deliveries, Reminders, Saved filters, Tasks, To-do accounts, To-do settings (no "Deliverys"). |
| 2 | Open **Tasks**. | A read-only list (user, number, title, due date, priority, charged credits, unpaid, source) with filters for priority, source, completed, trash and unpaid, and a search box (title, number, email). No "Add" button; opening a task shows no Save button; deleting is refused. |
| 3 | Search `1000`. | The tasks numbered 1000 of every user. |
| 4 | Open **To-do settings**. | Straight to the one form: free tasks per month (10), task price (10), trash days (30), max open tasks per user (10000), max labels per user (200), max attachment bytes (52428800), max attachments per task (20), max inbound rows per day (500), each with its help text. Change the price to `0`, save; as `qa.user@example.com` the next task beyond the allowance costs nothing. Put it back to 10. |
| 5 | Open **To-do accounts**. | One row per user who visited, with next task number, "Created this month", "Task creation blocked", stored bytes and "Storage frozen"; opening one shows its email address codes inline (code, active, created, retired), read-only. Filters for blocked and frozen. |

### TODO-16 Dark mode and mobile layout

**Purpose:** the pages work in both themes and on a phone.
**Preconditions:** logged in as `qa.user@example.com` with some tasks, labels and a subtask.

| # | Action | Expected result |
|---|---|---|
| 1 | Set the theme to dark under Appearance on the account page; open Today, a task page, Labels and Settings. | Every surface has a dark twin: no white boxes, text readable (the due badges, flags, chips, red errors, the menu, the dialogs). |
| 2 | Set the viewport to 375 px wide and open `/todo/`. | No horizontal page scroll. The sidebar is a single horizontally scrolling row of links above the content (and a second one for **Filters** once any exist); when items are hidden to the right the last visible one fades out, a hint to scroll. The quick-add title is full width with the button below it; the date/time/priority controls wrap. |
| 3 | Open a task page and the Postpone menu. | The menu stays on screen; the Details controls wrap; the subtasks list fits. |
| 4 | Open Labels and edit a label. | The edit fields wrap onto several lines; the buttons stay reachable. |
| 5 | Tab through Today with the keyboard. | Every control (the drag handle, circle, title link, menu, trash) is reachable with a visible focus ring; the menus open with Enter and close with Escape. |
| 6 | Open a task page with a few comments, one edited and one deleted. | Comments, the "edited" marker, the deleted line and the add box all have dark twins; at 375 px the comment header, the menu and the textareas fit without horizontal scroll and the Save/Cancel buttons stay reachable. |

### TODO-17 Comments

**Purpose:** a task can be commented on in Markdown; a comment can be edited (its history stays visible), deleted
(its text stays viewable) and restored.
**Preconditions:** logged in as `qa.user@example.com` with a task `Write report` (`T_REPORT`, recorded in TODO-03)
that has no comments.

| # | Action | Expected result |
|---|---|---|
| 1 | Open `/todo/<T_REPORT>/` and scroll below the description and subtasks. | A **Comments** section with an "Add a comment" textarea (placeholder mentioning Ctrl+Enter), the Markdown hint ("... and `#1234` to link a task. Up to 10,000 characters.") and a **Comment** button. No comments yet. |
| 2 | Click **Comment** with the box empty. | A red message "Comment: Write a comment." under the Comments heading; nothing is added. |
| 3 | Type `First **comment** with a [link](https://example.com)` and press **Ctrl+Enter** (Cmd+Enter on a Mac). | The comment appears without a page reload: your initial in a round avatar, your name, "just now" (hover: the full local date and time), the text with "comment" in bold and "link" an underlined link that opens in a new tab. The heading reads "Comments (1)". The box is empty again. |
| 4 | Go to **All**. | The row of `Write report` shows a small comment icon with `1`. |
| 5 | Back on the task page, open the comment's menu (the "..." button) and choose **Edit**. | The comment text is replaced by a textarea holding the Markdown source, with the hint and **Cancel** / **Save**. Type something extra and click Cancel (or press Escape): it closes without changes, and opening Edit again shows the saved text, not what you typed. |
| 6 | Change it to `Second version` and **Save**. | The text shows `Second version` and a small "(edited)" marker appears after the time (hover: when). |
| 7 | Edit it again to `Third version`, then click "(edited)". | A box "Earlier versions" opens below the comment listing, newest first, `Second version` then `First comment with a link`, each with "Replaced <date and time>". **Hide** closes it. |
| 8 | Open the menu and choose **Delete**. | A confirmation dialog "Delete comment" ("You can still show and restore it afterwards."). **Cancel** closes it; confirm with **Delete**. |
| 9 | After confirming. | The page reloads at the comments; the comment is now a muted italic line "Comment deleted just now." with a **Show** link. The heading no longer counts it; on **All** the comment icon of the row is gone. |
| 10 | Click **Show**. | A dashed box with the last text (`Third version`) rendered, and **Restore**, **History** and **Hide** buttons. **History** lists the two earlier versions again. **Hide** closes the box. |
| 11 | Click **Show**, then **Restore**. | The comment is back as a normal comment with its "(edited)" marker and the text `Third version`; the heading counts it again. |
| 12 | Type `draft` in the add box (do not send it), then click **Complete**. | The task is completed and the page's panel updates, but the text `draft` is still in the add box (the comments section is not re-rendered). Send it: allowed, comments can be added to a completed task. |
| 13 | Move the task to the trash and open it from **Trash**. | The comments are visible and read-only: no add box, no menu. **Restore** the task afterwards. |
| 14 | Paste a 10,001-character text into the add box and submit. | A red message "Comment: Comment is too long (at most 10,000 characters)." (the browser may also stop typing at the limit). |
| 15 | Log in as `qa.broke@example.com` and open `/todo/<T_REPORT>/` (their own task with that number, if any) or `/todo/9999/`. | Nothing of `qa.user`'s comments is visible; an unknown number is a 404. |

### TODO-18 Task references

**Purpose:** `#1234` and task URLs link to the task, and a task lists the tasks that mention it.
**Preconditions:** logged in as `qa.user@example.com` with at least three tasks: `T_REPORT`, `T_CALL` and `T_LATE`
(recorded earlier).

| # | Action | Expected result |
|---|---|---|
| 1 | Edit the description of `T_CALL` to `Prepare for #<T_REPORT> and #999 and #8888.` and save. | The rendered description shows `#<T_REPORT>` as a link (green-ish task-ref link) to `/todo/<T_REPORT>/`; `#999` and `#8888` stay plain text (999 is below the first number, 8888 is no task of yours). |
| 2 | Open `/todo/<T_REPORT>/`. | A **Referenced by** section (between Subtasks and Attachments, above the Comments) lists `#<T_CALL> Call ...` as a link; no "in a comment". |
| 3 | On `T_LATE`, add the comment `Related: http://localhost:8765/todo/<T_REPORT>/ and [that one](/todo/<T_REPORT>/)` (write the first address with the host you opened the site on: it must equal the server's base URL, e.g. `http://qr.localhost:8775` on a parallel server). | Both render as links to the task (the absolute one is not opened in a new tab). |
| 4 | Open `T_REPORT`. | **Referenced by** now also lists `#<T_LATE> ...` followed by "in a comment"; the entries are ordered by task number. Clicking the `T_LATE` entry opens its page scrolled to that comment. |
| 5 | On `T_LATE`, edit the comment to remove the links, then open `T_REPORT`. | The `T_LATE` entry is gone. |
| 6 | Edit the same comment to mention `#<T_REPORT>` again, then **Delete** it. Open `T_REPORT`. | The `T_LATE` entry is gone again (a deleted comment references nothing). **Restore** the comment: the entry returns. |
| 7 | Move `T_CALL` to the trash. Open `T_REPORT`. | `T_CALL` is no longer listed. Restore it: it is listed again. |
| 8 | On `T_REPORT`, add a comment `#<T_REPORT>` (itself). | It renders as a link to itself, but `T_REPORT`'s own **Referenced by** does not list `T_REPORT`. |
| 9 | Log in as `qa.broke@example.com`, write `#<T_REPORT>` in a description of one of their tasks (they have no task with that number). | It stays plain text, and `qa.user`'s `T_REPORT` is unaffected. |

### TODO-19 Repeating task, fixed schedule

**Purpose:** a fixed-schedule repeat keeps to its schedule when a task is postponed or completed late.
**Preconditions:** logged in as `qa.user@example.com` with at least 4 free tasks left this month (or enough credits); on `/todo/`.
Compute dates from the heading of **Today**; "the 1st" below means the first date of the rule on or after today: the 1st
of next month, **except when today is the 1st**, when it is today (then `Pay rent` is on **Today** at step 3, "the 5th"
at step 5 is the 5th of this month, and step 7's new occurrence is due on the 1st of next month).

| # | Action | Expected result |
|---|---|---|
| 1 | In the quick-add box type `Pay rent`. Open **Repeat**. | A popover with **Frequency** (Does not repeat, Day, Week, Month, Year), defaulting to "Does not repeat", and a **Remove repeat** / **Done** row. No mode radios yet. |
| 2 | Choose **Month**; under **On** choose **Day of the month** and type `1`. | The button label reads "Every month on the 1st". "Every [1] month" is shown; **Ends** offers Never / After a number of times / On a date; two radios: "Fixed schedule: the next one is due on schedule even if this one is late" (selected) and "From completion: the next one is counted from when I complete this one". |
| 3 | Click **Done**, then **Add task**. | The task is created. It is not on Today (it is due on the 1st, unless today is the 1st); open **All**: the row shows `Pay rent`, the date of the 1st and a repeat icon with "Every month on the 1st". Record the number as `T_RENT`. |
| 4 | Open `/todo/<T_RENT>/`. | The Date field is the 1st (a rule with no date takes its first date). Below Details: the chip "Every month on the 1st (fixed schedule)" and a **Stop repeating** link. |
| 5 | Click **Postpone** > **Pick a date**, choose the 5th (of the month the 1st is in). | The Date field is the 5th. The chip still says "Every month on the 1st". |
| 6 | Click **Complete**. | The task shows Completed, with the note "Uncompleting this task keeps the next occurrence of the repeat if one was already created: nothing is deleted or refunded." |
| 7 | Open **All**. | A new `Pay rent` (number `T_RENT + 1`) is due on the 1st of the month **after** the one it was due in (not the 5th plus a month), with the same chip. Open **Completed**: the finished one is its own row. |
| 8 | Open `/todo/<T_RENT>/` and click **Uncomplete**. | It is open again; **All** still lists the next occurrence (two open `Pay rent` rows). Move this older one to the trash with its row's trash icon: with two open occurrences there is no "Also stop repeating" checkbox, it just moves. |
| 9 | On the newer occurrence's row click the trash icon. | Now the newest is the only open occurrence: a dialog "Move to trash" with an unticked checkbox "Also stop repeating". Tick it and confirm. The row leaves **All**. Open its page from **Trash**: "This task no longer repeats." and no chip. |
| 10 | Create `Weekly review` with Repeat = Week, ticking Monday and Wednesday, Ends "After a number of times" `2`. | The button reads "Every week on Monday, Wednesday, 2 times". After adding, its date is the next Monday or Wednesday on or after today. |
| 11 | Open `Weekly review`, click **Stop repeating**. | The chip and the link disappear and "This task no longer repeats." shows; the task stays open. Click **Repeat** > **Week** > **Done** > **Save**: the chip returns ("Every week"). |
| 12 | In the Repeat popover enter Month with "On" = **Day of the month** `31` and Save on a task. Open its page. | The chip reads "Every month on the 31st". (Completing it later skips months without a 31st: not checked here.) |
| 13 | In the Repeat popover choose **Ends** > **On a date** and pick a date. | The button label ends with `until 2 Nov 2026`-style text (day, short month, year), the same way the chip writes it. |
| 14 | Open the page of a **subtask**, then of a **completed** task. | Neither has a **Repeat** control in Details (a subtask never repeats and only an open task can). The quick-add box and an open top-level task do. |

### TODO-20 Repeating task, from completion

**Purpose:** a from-completion repeat counts from the day the task was completed.
**Preconditions:** logged in as `qa.user@example.com`; on `/todo/`.

| # | Action | Expected result |
|---|---|---|
| 1 | Add `Rotate tires` with today's date, Repeat = **Month**, "Every" `3`, "On" = **The same day of the month**, mode **From completion**. | The label reads "Every 3 months" and the chip after adding says "Every 3 months (from completion)". The task is due today. |
| 2 | Complete it from Today. | It leaves Today. Open **All**: a new `Rotate tires` due exactly 3 months from **today** (the completion day). Its subtasks (if any were open) are copied; the completed original is in **Completed**. |
| 3 | Open the new task, change Date to a day last week and **Save**, then **Complete**. | The next one is again 3 months from the day you completed it (today), not from the date you edited. |
| 4 | Create a task `Water plants`, Repeat = **Day**, Ends **On a date** = tomorrow, mode Fixed. Complete it twice. | After the first completion a new one is due tomorrow. After completing that one, no new `Water plants` is created and the last one shows "This task no longer repeats." |
| 5 | Add a task whose Repeat is **Year**, "On" = **A month and day**, left at day 1, month January. | The label reads "Every year on 1 January"; after adding, the date is the next 1 January on or after today. |
| 6 | Nothing to do here: an occurrence the balance can't pay for (owed, the "Tasks are blocked" banner appearing at once, the refusals and the settling after a top-up) is TODO-36. | -- |

### TODO-21 Reminders

**Purpose:** a task's reminders are listed, added, edited and removed, a new task with a date gets the default one, and a
due reminder shows in the notification bell and the history.
**Preconditions:** logged in as `qa.user@example.com` with at least 3 free tasks left this month (or enough credits).
The delivery steps 9, 11 and 14 are shell steps (the scheduler does not run on this server): without a shell mark them
**BLOCKED (needs-setup)**. Delivery by email is TODO-32.

| # | Action | Expected result |
|---|---|---|
| 1 | Open **Settings**. | A "Default reminder" section: a select "New tasks with a date" showing "Get a reminder", an amount `5`, a unit "minutes before", a "Default channel" select showing "In-app", the note "Email reminders go to your confirmed address only" and a **Reminder history** link. |
| 2 | In the quick-add box type `Call the bank`, set Date = today and Time = 23:00 (if it is already past 22:55 in your zone the default reminder is due at once and shows "Skipped": pick a later date), **Add task**. Record the number as `T_BANK`. Open `/todo/<T_BANK>/`. | A **Reminders** block lists "5 minutes before (in-app)" with "Fires" and today's date at 22:55. Its row on Today shows a small bell icon. Under it, **History** says "No reminders sent yet." |
| 3 | In the add row choose **1 hour before**, channel **Both**, and press **Add**. | The list now has "1 hour before (both)" (fires 22:00) above the 5 minute one. |
| 4 | Add **1 hour before** again. | A red message "Reminder: There is already a reminder at that time." and the list is unchanged. |
| 5 | Choose **Custom...**, enter `2` and unit `days before`, channel Email, **Add**. | "2 days before (email)" is listed (shown as "Skipped": its time, two days before the due time, is more than a day past, so it is created already sent and never delivered; it has **no** History row, which still reads "No reminders sent yet."). |
| 6 | Click the pen on "5 minutes before", change the amount to `15`, **Save**. | The row reads "15 minutes before (in-app)" and fires at 22:45. Press **Cancel** on another row's editor: nothing changes. |
| 7 | Click the bin on the "2 days before" row. | It is removed. |
| 8 | In **Details** clear the Date and press **Save**. | A browser confirmation "Clearing the date removes its 2 reminders. Continue?". Cancel: nothing changes. Accept: the Reminders block says "Set a due date to add reminders." and the bell is gone from the row (the time was cleared with the date). Set the date to today and the time to 23:00 again and Save: the default reminder ("5 minutes before (in-app)", fires 22:55) is back, alone. |
| 9 | (shell step) Make the reminder due a minute ago: `python tests_e2e/ai/manage.py shell -c "from datetime import timedelta; from django.utils import timezone; from apps.todo.models import Reminder; print(Reminder.objects.filter(task__user__email='qa.user@example.com', task__number=<T_BANK>).update(fire_at=timezone.now() - timedelta(minutes=1), sent_at=None))"` (prints `1`), then run the job: `python tests_e2e/ai/manage.py shell -c "from apps.todo.jobs import send_reminders; send_reminders()"`. Reload any To-do page. | The header bell shows a new notification titled `Call the bank` with the body "Due <today's date>, 23:00", which opens the task. |
| 10 | Open `/todo/<T_BANK>/`. | **History** lists the delivery (date, time, "In-app"). Open **Settings** > **Reminder history**: the same row with `#<T_BANK> Call the bank` linking to the task. |
| 11 | (shell step) Run the `send_reminders()` command of step 9 again. | Nothing new: the bell count and the history are unchanged (a reminder is delivered once). |
| 12 | In **Settings** choose "Get no reminder", **Save**, then add a task `No reminder please` with a date. | Its page says "No reminders." and its row has no bell. Restore the setting (5 minutes) afterwards. |
| 13 | Complete a repeating task that has a reminder (create `Pay rent`, Repeat = Week, with a 15 minute reminder, then **Complete**). Open the new occurrence. | It has the same reminder ("15 minutes before"), unsent, firing before the new date. |
| 14 | (Only if step 9 ran.) Postpone `Call the bank` to **Tomorrow** (the Postpone action on its row or the task page) and open `/todo/<T_BANK>/`. | The reminder it already delivered is armed again: the Reminders block shows "Fires" (not "Sent") with tomorrow's date at 22:55. (shell step) Running `send_reminders()` now delivers nothing (it is not due yet). |

### TODO-22 Search

**Purpose:** search finds tasks by title, description and comments, ignoring case, accents, punctuation and
formatting; `*` and `?` are wildcards, `/.../` or the Regex box is a regular expression.
**Preconditions:** logged in as `qa.user@example.com` with these tasks (create them first; the numbers do not
matter): `Pay the rent` (priority 1, label `Home`, due in 2 days), `Walk the Cão` (priority 2, description
`Take the **big** leash`, one comment `The plumber called`), `Old rent receipt` (completed) and `Parent 0`.

| # | Action | Expected result |
|---|---|---|
| 1 | Look at the sidebar of **Today**. | Above the menu a "Search tasks" box with a `?` button, a **Regex** checkbox and an **Include completed** checkbox; below the menu a **Filters** heading ("No saved filters" for now). On a 375 px wide screen the box sits at the top of the page. |
| 2 | Type `rent` in the box and press Enter. | `/todo/search/?q=rent`: the heading "Search", the search form with `rent` in it, "2 results" (`Pay the rent` and `Parent 0` -- "rent" inside "parent" --, and no completed task), each row with a line such as "Title: pay the **rent**" where the match is highlighted, and the note "matches shown without accents or formatting". `Old rent receipt` is not listed. |
| 3 | Tick **Include completed** (in the page's form). | The list refreshes without a page reload and the address bar changes to `...?q=rent&include_completed=1`: 3 results, `Old rent receipt` last, struck through. |
| 4 | Replace the text with `CAO` (capitals, no accent). | `Walk the Cão` is found ("Title: walk the **cao**"). Try `walk the cão!` too: found. |
| 5 | Search `big leash`, then `**big**`. | Both find `Walk the Cão` with "Description: take the **big** leash" (the Markdown marks are not part of the text). |
| 6 | Search `plumber`. | `Walk the Cão` is found with "Comment: the **plumber** called". |
| 7 | Search `pay*rent`, then `pa?ent`, then `pa.ent`. | `pay*rent` finds `Pay the rent`; `pa?ent` finds `Parent 0` ("pa" + any letter + "ent"); `pa.ent` finds nothing (a dot is not a wildcard) and the list says "Nothing matches. Completed tasks are left out unless you tick Include completed." (the second sentence only while that box is unticked). |
| 8 | Search `/^pay (the )?rent$/`. | `Pay the rent` only. The slashes alone make it a regular expression. Then type `^pay` with the **Regex** box ticked: `Pay the rent`. Then `/^Pay/` (a capital): also `Pay the rent`, because the letters you type are lower-cased for you. |
| 9 | Search `/(/`. | A red line "Invalid regular expression." above the results; nothing else changes. Try `/(a)\1/`, `/a(?=b)/`, `/(?>a+)b/`, `/a++b/` and `/(?P<x>a)b/`: the same message. Clear the box and press Search: "Type something to search for." |
| 10 | Type 201 characters. | The browser stops at 200 characters in the box. |
| 11 | Open the `?` help. | A popover lists `*`, `?`, `/.../` or the Regex box (with the note that a regular expression is matched against the normalised, lower-case text and that its letters are lower-cased for you), the shared syntax, "case, accents, punctuation and formatting are ignored" and "deleted comments and earlier edits are not searched". Escape closes it. |
| 12 | Delete the plumber comment (on the task page), then search `plumber`. | `Walk the Cão` is no longer found. Restore the comment: found again. Edit the comment to `The electrician called`: `plumber` finds nothing (the earlier text is not searched), `electrician` does. |
| 13 | Open the filter bar: tick **Priority 1**, with the text box empty. | Only priority 1 tasks (`Pay the rent`); no text is needed when a filter is chosen. With **Labels > @Home**: the same. With **Due date > Next 7 days**: `Pay the rent`. With **No date**: the undated ones. With **Custom** two date inputs appear. |
| 14 | Combine `rent` with Priority 2. | Nothing matches. Change the **Sort by** to Title: the order follows. |
| 15 | Search for something with more than 50 results (create 51 tasks named `bulk N` if needed) and open page 2 from the pager. | Page 2 loads in place with the rest, and the address bar shows `&page=2`; a reload shows the same page. |
| 16 | In the results, complete a task with its circle. | The row stays in the list, shown as completed (a search keeps completed tasks it lists). |
| 17 | Log in as `qa.broke@example.com` and search `rent`. | None of `qa.user`'s tasks appear. |
| 18 | As `qa.user@example.com` search `1000` (then `#1000`). | The task numbered exactly `#1000` is listed (without a snippet unless its text contains `1000`), next to any task whose text contains "1000". Searching `100` finds no task by number (an exact match only), and `1000*` does not match by number either. |

### TODO-23 Saved filters

**Purpose:** a search can be saved under a name, listed in the sidebar with a count, run, updated, renamed,
reordered and deleted.
**Preconditions:** logged in as `qa.user@example.com` with the tasks of TODO-22.

| # | Action | Expected result |
|---|---|---|
| 1 | Search `rent*` with **Include completed** ticked and click **Save as filter**. | A dialog "Save as filter" with a Name box focused. |
| 2 | Click **Save** with the name empty. | The browser asks to fill in the field; nothing is saved. |
| 3 | Name it `Rent stuff` and press Enter. | The page goes to `/todo/filters/<id>/` headed "Rent stuff", with a green message "Saved the filter Rent stuff." and the summary `"rent*" · including completed`; the same results as the search. The sidebar's **Filters** now lists `Rent stuff` highlighted, with a count of its **open** tasks (the count appears a moment after the page, not with it). |
| 4 | Save the same search again as `rent stuff` (another case). | The dialog shows "Name: You already have a filter with this name." and stays open. Cancel closes it. |
| 5 | On the filter's page tick **Priority 1**. | The results refresh, the address bar becomes `/todo/filters/<id>/?q=rent*&include_completed=1&priorities=1&sort=date` and an **Update filter** button appears beside **Save as filter**. Untick it: the button goes away. |
| 6 | Tick **Priority 1** again and click **Update filter**. | The page reloads at `/todo/filters/<id>/` with "Updated the filter Rent stuff."; the summary now starts `priority 1 ·`; the sidebar count changed. |
| 7 | Open another filter in the sidebar (save `Urgent` for priority 1 from the search page first). | Each runs its own params; the sidebar entry of the current one is highlighted. |
| 8 | Open **Filters** (the sidebar heading) `/todo/filters/`. | A list: name (a link), "N open tasks" (filled in just after the page shows) and the summary line, a drag handle, a pencil and a trash can. |
| 9 | Click the pencil on `Urgent`, change the name to `Rent stuff` and save; then to `Important`. | The first is refused with "Name: You already have a filter with this name." (nothing changes); the second renames it in place. Escape cancels the edit. |
| 10 | Drag `Important` above `Rent stuff`, then reload. | The new order sticks, also in the sidebar (the sidebar's Filters list already shows it right after the drop, without a reload). |
| 11 | Delete a label that a filter uses (Labels page), then open that filter. | The filter still runs; the deleted label is gone from its summary, and an amber notice says "A label this filter used has been deleted, so it no longer narrows the results" (adding "this filter now selects every open task" if the label was its only criterion) and "Change the filter and choose "Update filter" to fix it." On `/todo/filters/` its row says "A label this filter used was deleted and is ignored." After **Update filter** the notices are gone. |
| 12 | Click the trash can on a filter. | A confirmation "Delete filter" ("Your tasks are not affected."); Cancel closes it; Delete removes it and shows "Deleted the filter ...". |
| 13 | Create 21 filters (name them `F01`..`F21`). | The sidebar lists the first 20 and "and N more" linking to the filters page, which lists all of them. The 51st is refused with "You can have up to 50 saved filters." |
| 14 | Open `/todo/filters/<id of another user's filter>/` (log in as `qa.broke@example.com` with an id from `qa.user`). | A 404. |
| 15 | In dark mode and at 375 px wide, open the search page, the filters page and the save dialog. | Every surface has a dark twin; the highlighted match is readable; nothing scrolls horizontally; the dialog fits. |

### TODO-24 Reordering with the keyboard

**Purpose:** every drag-to-reorder list can be ordered with the arrow keys.
**Preconditions:** logged in as `qa.user@example.com` with at least three tasks due today, a parent with three
subtasks, three labels and two saved filters (TODO-07, TODO-08, TODO-23).

| # | Action | Expected result |
|---|---|---|
| 1 | On **Today** Tab to the grip handle of the second task. | The handle takes focus with a visible ring; it is a `role="button"` named "Reorder <title>: use the up and down arrow keys" (its tooltip says "Drag to reorder, or use the up and down arrow keys"). |
| 2 | Press the up arrow once, then wait a moment. | The row moves above the first one at once and keeps the focus on its handle; a screen-reader announcement "<title> moved to position 1 of N." is made (a polite live region); one request is sent for the burst. Reload: the order is kept. |
| 3 | Press the down arrow twice quickly. | The row moves two places down; still only one save after the burst. At the top or bottom of the list another press in that direction does nothing. |
| 4 | Repeat on a parent's **Subtasks**, on **Labels** and on **Filters** (`/todo/filters/`). | Each list reorders the same way and keeps the order after a reload. After a move on Filters the sidebar's Filters list shows the new order without reloading. |
| 5 | Press the arrow keys with the focus on a checkbox or the title link of a row. | Nothing is reordered (only a handle reacts). |

### TODO-25 Lists fix themselves when the last row leaves

**Purpose:** an action that removes the last row of a list shows the empty state at once, and trashing a parent takes
its subtasks away too.
**Preconditions:** logged in as `qa.user@example.com` with exactly one task due today (nothing overdue), one overdue
task, and a parent with two open subtasks.

| # | Action | Expected result |
|---|---|---|
| 1 | On **Today** complete the only task due today (click its circle). | The row leaves and, without a reload, the empty state appears in its place: a check icon, "Nothing due today", "Add a task above." (after a reload with an overdue task still listed it would read "Nothing else due today"). The sidebar count drops. |
| 2 | Complete the only overdue task from its circle (or, on a fresh overdue task, use **Reschedule overdue to today** and confirm). | When the Overdue list loses its last task the whole Overdue section disappears (its heading and its button too) without a reload. |
| 3 | Open **Upcoming**; trash the only task of a day. | That day's list shows "Nothing due" in its place. Do the same on **All** with the last open task: "No open tasks. Add one above."; on **Completed**: "No completed tasks yet."; on **Trash** delete the last one forever: "The trash is empty." |
| 4 | On **All** trash the parent that has two open subtasks. | The parent's row **and** both subtask rows leave the list at once. In **Trash** all three are listed; restoring or deleting the parent forever there removes the subtask rows too. A subtask trashed on its own earlier stays listed. |
| 5 | Trash a parent with subtasks, open **Trash** and restore one subtask (red "Restore the parent task first."), then restore the parent. | The red message is cleared by the successful restore. |

### TODO-26 Editors, tab titles and the settings page

**Purpose:** small behaviours: Cancel discards, the title follows the task, the labels' dark chips, Settings title.
**Preconditions:** logged in as `qa.user@example.com` with a label `Home` and a task.

| # | Action | Expected result |
|---|---|---|
| 1 | Edit a label's name, then press its X (or Escape); open the editor again. Do the same for a saved filter's rename and the task's description. | Each shows the saved text again, not what you typed, and a refusal an earlier save left in the message slot is gone. |
| 2 | Open `/todo/settings/`. | Tab title `Settings - To-do - ZipTrigo`. |
| 3 | In dark mode (account page > Appearance) open a task with the `Home` label and the Labels page. | The label chip text is a lighter tint of its colour so it reads on the dark card (contrast of at least 4.5:1); a rendered checklist box in the description is dark too, not a white box. |
| 4 | With a screen reader or the accessibility tree, inspect a rendered checklist box and the sidebar's Filters label. | The box is named after its item's text; "Filters" is a link inside a paragraph (not a heading placed before the page's h1). |

### TODO-27 A fresh account for the files, email and storage cases

**Purpose:** set up the account TODO-28 to TODO-33 and TODO-37 use, so their files, emails and paused storage
never touch the seeded accounts.
**Preconditions:** signed out (or another browser context).

| # | Action | Expected result |
|---|---|---|
| 1 | On `/account/register/` create `qa.todo.<random>@example.com` (record it as `T2`), name `Todo Files`, password `TodoQa-4821!`. | The neutral "Check your email" page. |
| 2 | Open `/__e2e__/mail/?to=<T2>` (reload until it arrives, up to a minute) and click the link of "Confirm your ZipTrigo account email"; press **Confirm my email**. | "Email Confirmed!". |
| 3 | Sign in as `T2` and open `/todo/`. | Today is empty, "0 of 10 free tasks used this month.". The bell has the welcome notification; `/billing/credits/` shows 1,000 credits ("A gift for your new account"). `/account/` shows a Time zone: the browser's (saved by the first page after sign-in). |
| 4 | Add `Files task` due today. Record its number as `T2_TASK` (`1000`). Open `/todo/1000/`. | Under the panel an **Attachments** section: a dashed drop zone "Drop files here, or" with an **Add files** button and "Up to 50.0 MB each, 20 per task. Stored files are billed by size, daily."; below it "No files attached.". Then **Comments**, whose add form has an **Attach files** button and "Up to 50.0 MB each; they count toward the task's 20 files and are billed by size, daily.". |
| 5 | Open **Settings**. | The "Files" section reads "No files stored." and "Files attached to your tasks are billed by size, once a day, at the same rate as the other products. Files on tasks in the trash are billed until the trash is emptied." |

### TODO-28 Attachments: upload, open in the browser or download

**Purpose:** files go straight to the bucket and are listed on the task; Open shows only a file whose bytes are an
image or a PDF, whatever its name or declared type.
**Preconditions:** TODO-27 done, signed in as `T2` on `/todo/<T2_TASK>/`; paste the file helper (Test data > Files).

| # | Action | Expected result |
|---|---|---|
| 1 | Click **Add files**. | The browser's file chooser opens (cancel it). The button is reachable with Tab and has a visible focus ring. |
| 2 | `todoPick([{ name: 'photo.png', type: 'image/png', base64: PNG_A }])`. | A row appears in the drop zone's list with the name, the size "1 KB", a progress bar (named "Upload progress of photo.png") and a cancel x ("Cancel upload of photo.png"); the status moves through "Preparing…", "Uploading…", "Finishing…" (may be too quick to see). In the network log: `POST .../attachments/start/` (201), a `PUT` to `localhost:4566` (200), `POST .../attachments/<id>/complete/` (200). The row then goes and the list below shows `photo.png`, "280 bytes", an image icon, **Download**, **Open** and **Remove**. No console errors. |
| 3 | `todoPick([{ name: 'scan.pdf', type: 'application/pdf', base64: PDF_A }, { name: 'notes.txt', type: 'text/plain', text: 'hello world\n' }, { name: 'fake.png', type: 'image/png', text: 'this is no png\n' }])`. | Three rows upload in parallel; the list then holds four files, oldest first (the three new ones in the order their uploads started, normally as picked): `photo.png`, `scan.pdf` (PDF icon, "588 bytes", **Open**), `notes.txt` ("12 bytes", a plain file icon, **no Open**), `fake.png` ("15 bytes", a plain file icon, **no Open**: its bytes are not a PNG, so the name and the declared type don't count). |
| 4 | Click **Open** on `photo.png`. | A new tab shows the light-gray image itself (not a download prompt). Close it. **Open** on `scan.pdf`: the PDF shows in the browser's viewer ("STAPLES 42.10"). |
| 5 | Click **Download** on `notes.txt`, then on `fake.png`. | Each downloads (no new page) under its own name, `notes.txt` with the 12 bytes `hello world`, `fake.png` with the 15 bytes of text. `photo.png`'s **Download** also downloads (does not open). |
| 6 | Reload the page. | The four files are still listed, in the same order. The task's row on **Today** has no attachment marker (rows don't show one). |
| 7 | Open **Settings**. | "Files": "**895 bytes** stored, less than 0.01 credits a day." (280 + 588 + 12 + 15). |
| 8 | On the task page click **Remove** on `fake.png`. | The shared dialog "Remove file": "Remove fake.png from this task? This can't be undone." **Cancel** closes it. Confirm with **Remove**: the page reloads with the flash "Removed fake.png." and three files are left. Settings now says "880 bytes". |
| 9 | Sign in as `qa.broke@example.com` (another context) and open `http://localhost:8765/todo/attachments/<id of photo.png>/download/` (the id is in its Download link) and the same with `?inline=1`. | A 404 page for both: another user's file is never served. |

### TODO-29 Attachment limits and task states

**Purpose:** the size and per-task limits are enforced, and only an open task takes files.
**Preconditions:** TODO-28 done (`T2_TASK` has 3 files); signed in as `T2` on its page with the helper pasted; an
admin context for `/admin/todo/todosettings/`.

| # | Action | Expected result |
|---|---|---|
| 1 | `todoPick([{ name: 'empty.txt', type: 'text/plain', size: 0 }])`. | A row "empty.txt", "0 KB", red "✕ This file is empty." with a dismiss x ("Dismiss the message about empty.txt"); no request reaches the server. Dismiss it. |
| 2 | As admin, open **To-do settings**, set "Max attachment bytes" to `1048576` and "Max attachments per task" to `4`, save. Reload the task page. | The drop zone's hint reads "Up to 1.0 MB each, 4 per task. ...". |
| 3 | `todoPick([{ name: 'big.bin', size: 2 * 1024 * 1024 }])`. | Row "big.bin", "2 MB", red "✕ This file is larger than 1 MB."; no request. |
| 4 | The server checks too: in the console run `fetch(location.pathname + 'attachments/start/', {method: 'POST', headers: {'Content-Type': 'application/json', 'X-CSRFToken': document.cookie.match(/csrftoken=([^;]+)/)[1]}, body: JSON.stringify({name: 'big.bin', size: 2097152, content_type: '', checksum_sha256: '47DEQpj8HBSa+/TImW+5JCeuQeRkm5NMpJWZG3hSuFU='})}).then(async r => [r.status, await r.json()])`. | `[422, {error: "The file is too large (max 1 MB)."}]`. |
| 5 | `todoPick([{ name: 'photo-2.png', type: 'image/png', base64: PNG_A }])`. | It uploads: 4 files. |
| 6 | `todoPick([{ name: 'photo-3.png', type: 'image/png', base64: PNG_A }])`. | The row ends in red "✕ A task can have up to 4 files."; the list still has 4. Remove `photo-2.png` (flash "Removed photo-2.png."). |
| 7 | As admin put the two settings back to `52428800` and `20`. | Saved. The task page's hint is "Up to 50.0 MB each, 20 per task. ..." again. |
| 8 | **Complete** the task. | The panel shows it completed. After a reload the Attachments section has **no** drop zone or **Add files** (a completed task takes no files), but the files are listed with Download / Open / Remove, and Download still works. The comment form still has **Attach files** (TODO-30). **Uncomplete** it. |
| 9 | **Move to trash**, then open the task from **Trash**. | No drop zone; the files are listed with **Download** (it works) and **Remove** (don't use it here). The comments are read-only. **Restore** the task: after a reload the drop zone is back. |
| 10 | Add a task `Throwaway` (no date), attach `scan.pdf` to it, note the "Files" line in Settings, then move it to the trash. | The Settings line is unchanged by the trash ("Files on tasks in the trash are billed until the trash is emptied"). |
| 11 | In **Trash** delete `Throwaway` forever and confirm. | "Task deleted."; Settings' "Files" drops by 588 bytes (its file was deleted with it). |

### TODO-30 Files on comments

**Purpose:** a comment carries files of its own, uploaded while the comment is written and saved with it.
**Preconditions:** TODO-29 done; signed in as `T2` on `/todo/<T2_TASK>/` (open), helper pasted.

| # | Action | Expected result |
|---|---|---|
| 1 | `todoPickComment('comment-new', [{ name: 'photo.png', type: 'image/png', base64: PNG_A }])`. | An upload row under the add box, then a chip: a paperclip, `photo.png`, "1 KB" and **Remove** ("Remove photo.png"). While it uploads the **Comment** button is disabled. The task's own **Attachments** list does not change. |
| 2 | Click **Comment** with the text empty. | Red "Comment: Write a comment." (and the same under the textarea); the chip stays. |
| 3 | Type `See the photo` and press Ctrl+Enter (Cmd+Enter on a Mac). | The comment appears with "See the photo" and, under it, `photo.png`, "280 bytes", **Download** and **Open**. The add box is empty and has no chip. The **Attachments** section still lists only the task's own files. |
| 4 | Click the comment's **Open**, then **Download**. | The image shows in a new tab; Download saves `photo.png`. |
| 5 | Pick `notes.txt` into the add box, then click the chip's **Remove** before sending. Send the comment `no file here`. | The chip goes (the draft file is deleted on the server); the new comment has no file. |
| 6 | Open the first comment's menu > **Edit**. | The edit form lists `photo.png` ("280 bytes") with a red **Remove** checkbox, and its own **Attach files** button. |
| 7 | `todoPickComment('comment-text-<that comment's id>', [{ name: 'scan.pdf', type: 'application/pdf', base64: PDF_A }])` (the id is in the comment's `id="comment-<id>"`), wait for the chip, **Save** without changing the text. | The comment now shows `photo.png` and `scan.pdf`, and **no** "(edited)" marker (a change of files only is not an edit of the text). |
| 8 | Edit again, tick **Remove** next to `photo.png`, **Save**. | Only `scan.pdf` is left under the comment. |
| 9 | Delete the comment (menu > **Delete**, confirm), **Show** it, then **Restore**. | While deleted its files are not shown; after Restore `scan.pdf` is back under it. |
| 10 | **Complete** the task and add a comment `done, receipt attached` with `notes.txt` attached. | Allowed: a completed task takes comment files (but no task attachments). **Uncomplete** the task. |
| 11 | As admin set "Max attachments per task" to the number of files the task has now (its Attachments list plus the files under its comments, deleted comments included), then attach one more file to a new comment. | The upload row ends in red "✕ A task can have up to <n> files." (comment files count toward the task's cap). Put the setting back to `20`. |
| 12 | Set the window to 375 px wide and dark mode (account page > Appearance). | The chips, the upload rows and the file lines under a comment wrap without horizontal scroll and have dark twins. |

### TODO-31 Paused storage

**Purpose:** when the storage can't be paid for, uploads and downloads stop (nothing is deleted), the owner is told by
email and in the app, and a top-up resumes it.
**Preconditions:** TODO-30 done (`T2` has stored files and about 1,000 credits); a shell; an admin context. The daily
metering that freezes an account does not run here, so the freeze is a shell step.

| # | Action | Expected result |
|---|---|---|
| 1 | (shell step) `python tests_e2e/ai/manage.py shell -c "from apps.todo.models import TodoAccount; from apps.todo.services.metering import freeze_account; freeze_account(TodoAccount.objects.get(user__email='<T2>'))"`. As `T2` reload `/todo/`. | An amber banner on every To-do page: "**Storage is paused.** Your credits couldn't cover the storage for your attachments. Nothing was deleted; uploads and downloads resume when you add credits." with **Top up** to `/billing/credits/`. |
| 2 | Open the bell. | A new notification "Your to-do file storage is paused" ("Your credit balance couldn't cover the storage for your attachments. Nothing was deleted; uploads and downloads are paused until you add credits."); clicking it opens `/todo/settings/`. |
| 3 | Open `/__e2e__/mail/?to=<T2>` (reload until it arrives). | One email "Your to-do file storage is paused": the balance couldn't cover the storage "(<size>, about 1 credit a day)", "Your files are safe, nothing was deleted.", uploads and downloads are paused and resume when the balance covers a day of storage; links **Add credits** and **Your to-do settings**, signed "The ZipTrigo Team". **Add credits** opens `/billing/credits/`, **Your to-do settings** `/todo/settings/`. |
| 4 | Open `/todo/<T2_TASK>/`. | The **Add files** button is disabled and the drop zone says "Uploads are paused until you add credits." with a **Top up** link; dropping a file or `todoPick(...)` does nothing. The comment form's **Attach files** is disabled with the same line. The files are still listed. |
| 5 | Click **Download** on a file, then **Open**. | Back on the task page with the red flash "Storage is paused: top up your credits to upload or download files."; nothing downloads. |
| 6 | Run the `fetch(... 'attachments/start/' ...)` of TODO-29 step 4 with `size: 280`. | `[422, {error: "Storage is paused: top up your credits to upload or download files.", frozen: true, top_up_url: "/billing/credits/"}]`. |
| 7 | **Remove** a file, and add a comment without files. | Both work ("Removed <name>."): removing saves money, and comments don't need storage. |
| 8 | Open **Settings**. | "Files" adds "Storage is paused until you add credits. **Top up**". As admin, `/admin/todo/todoaccount/` shows `T2` with "Storage frozen" ticked. |
| 9 | As admin, add 1 credit to `T2` with the adjustment tool. As `T2` reload the task page. | The banner is gone, **Add files** and **Attach files** work again, Download downloads. The "storage paused" notification is gone from the bell and `/notifications/`. |

### TODO-32 Reminders delivered by email and in the app

**Purpose:** a due reminder sends an email (with working links) and an in-app notification, once; a reminder long
past its time is skipped and says so; a completed task's reminder is not sent.
**Preconditions:** TODO-27 done; signed in as `T2`; a shell (without one mark this case **BLOCKED (needs-setup)**).

| # | Action | Expected result |
|---|---|---|
| 1 | Add `Email me <b>now</b>` due today at 23:30 (if it is later than 23:00 in your zone use tomorrow). Open it. Set the description to `Bring the **report** and #1000` and save. Record its number as `T2_MAIL`. | The default reminder "5 minutes before (in-app)" is listed. The title shows the angle brackets as text. |
| 2 | Remove that reminder (bin), then add **At time of task** with channel **Both**. | One reminder: "At time of task (both)", "Fires <date>, 23:30". |
| 3 | (shell step) `python tests_e2e/ai/manage.py shell -c "from datetime import timedelta; from django.utils import timezone; from apps.todo.models import Reminder; print(Reminder.objects.filter(task__user__email='<T2>', task__number=<T2_MAIL>).update(fire_at=timezone.now() - timedelta(minutes=1), sent_at=None))"` (prints `1`), then `python tests_e2e/ai/manage.py shell -c "from apps.todo.jobs import send_reminders; send_reminders()"`. | No error. |
| 4 | As `T2` reload the page and open the bell. | A notification titled `Email me <b>now</b>` (as text, not bold) with "Due <d Mon YYYY>, 23:30"; clicking it opens `/todo/<T2_MAIL>/`. The reminder now says "Sent <date, time>"; **History** lists two rows, "In-app" and "Email", with the time sent, and "All reminder history". |
| 5 | Open `/__e2e__/mail/?to=<T2>` (reload until it arrives). | One email "Reminder: Email me <b>now</b>". Text body: "Reminder: Email me <b>now</b>", "Due <d Mon YYYY>, 23:30 (<your zone, e.g. Europe/Lisbon>)", the description as written (`Bring the **report** and #1000`), "Open the task: http://localhost:8765/todo/<T2_MAIL>/" and the footer "You set this reminder on "Email me <b>now</b>". Change or remove it on the task page (...); your default for new tasks is in To-do settings (http://localhost:8765/todo/settings/)." In the HTML source the title is escaped (`&lt;b&gt;now&lt;/b&gt;`), **report** is `<strong>report</strong>` and `#1000` is plain text (no link). |
| 6 | Click "Open the task", then the To-do settings link. | They open `/todo/<T2_MAIL>/` and `/todo/settings/` (signed in as `T2`). |
| 7 | Open `/todo/reminders/history/` (Settings > **Reminder history**). | Heading "Reminder history", a back link "Settings", newest first: two rows `#<T2_MAIL> Email me <b>now</b>` (a link to the task), "In-app" and "Email", with the time. |
| 8 | (shell step) Run the `send_reminders()` command again. | No new email, notification or history row (one delivery per firing). |
| 9 | On `T2_MAIL` add **1 hour before** (in-app). (shell step) Make it long past: `python tests_e2e/ai/manage.py shell -c "from datetime import timedelta; from django.utils import timezone; from apps.todo.models import Reminder; now = timezone.now(); print(Reminder.objects.filter(task__user__email='<T2>', task__number=<T2_MAIL>, minutes_before=60).update(fire_at=now - timedelta(hours=2), created_at=now - timedelta(hours=3), sent_at=None))"`, then `send_reminders()`. | The reminder shows "Skipped <date, time>" (not "Sent"); **History** adds "In-app (skipped: its time had already passed)"; no notification and no email for it. The reminder history page shows the same row. |
| 10 | Add a task `Done already` due today at 23:45 with the default reminder, **Complete** it, make its reminder due (step 3's command with its number) and run `send_reminders()`. | Nothing is delivered: its page still lists "Fires ..." (read-only, a completed task's reminders can't be changed), no notification or email. |
| 11 | Move `T2_MAIL` to the trash, delete it forever from **Trash**, then open the reminder history page. | Its rows stay, as `#<T2_MAIL> Email me <b>now</b>` in plain text followed by "(task deleted)". |

### TODO-33 Settings: default reminder, storage line and the email address

**Purpose:** every part of the Settings page saves, refuses and reloads correctly.
**Preconditions:** TODO-27 done; signed in as `T2` on `/todo/settings/`.

| # | Action | Expected result |
|---|---|---|
| 1 | Choose "Get a reminder", clear the amount, **Save**. | Red "Default reminder: Enter an amount." (under the amount too); nothing saved. |
| 2 | Set `31` "days before", **Save**. | Red "Reminder: Choose a reminder time between 0 minutes and 30 days before."; nothing saved (the due time is not saved either: both or neither). |
| 3 | Set `1` "hours before", channel **Email**, **Save**. | Green "Saved."; reload: `1`, "hours before", "Email". Add a task `Default check` due tomorrow at 10:00: its page lists "1 hour before (email)", "Fires <tomorrow>, 09:00". |
| 4 | Choose "Get no reminder": the amount and unit hide. **Save**. Add `No default` due tomorrow. | Its page says "No reminders.". Then put the default back to 5 "minutes before", In-app. |
| 5 | Add `Date only` due tomorrow (no time). Open it. | "5 minutes before (in-app)", "Fires <tomorrow>, 08:55" (09:00, the default due time, minus 5 minutes). |
| 6 | In Settings set the default due time to `10:00`, **Save**; open `Date only` again. | Its reminder now fires at 09:55 (date-only tasks follow the default due time); the task itself still shows only its date. Put the due time back to `09:00`. |
| 7 | Look at "Add tasks by email". | An address `todo+<8 lower-case letters and digits>@<domain>` (default `ziptrigo.com`) in monospace, **Copy** and **Regenerate…** buttons; the text "Email a task to this address: the subject becomes the title, the text the description and the attachments are attached. A date in the subject ("pay rent on the 1st") is read for you. The task counts like any other: free allowance first, then 10 credits."; "Accepted senders" with **Anyone** selected and the textarea disabled; "Recent emails": "No emails yet.". Record the address. |
| 8 | Click **Copy**. | The button reads "Copied" with a check for a moment; the clipboard holds the address. |
| 9 | Click **Regenerate…**. | A dialog "Regenerate address": "Your old address stops working immediately. Mail already sent to it is dropped, so update any contacts or rules that use it." **Cancel** closes it with the address unchanged. Confirm with **Regenerate**: the page reloads with "Your new address is ready. The old one no longer works." and a different code. |
| 10 | Choose **Only these** (the textarea enables), leave it empty, **Save**. | Red "Accepted senders: Enter at least one address, or choose Anyone to accept all senders." above the radios and under the textarea; nothing saved. |
| 11 | Type `not-an-address bob@`, **Save**. | Red "Accepted senders: Use email addresses or *@domain.com. Not valid: not-an-address, bob@". |
| 12 | Type `Me+Shop@Example.com, *@Acme.com`, **Save**. | "Saved" with a check; the textarea now reads `me@example.com, *@acme.com` (lower-cased, the `+tag` dropped). Reload: **Only these** is still selected with the same text. |
| 13 | Choose **Anyone**, **Save**. | "Saved"; the textarea is disabled and empty after a reload. |
| 14 | Check the "Files" section. | "<size> stored, less than 0.01 credits a day." for `T2`'s files (a rate, not the 1-credit minimum); `qa.user`'s Settings says "No files stored.". |
| 15 | At 375 px wide and in dark mode look at the whole page. | The address breaks instead of overflowing, the buttons wrap, the radios and textarea stack; every section has a dark twin. |

### TODO-34 The AI date chip

**Purpose:** a date typed in the title is offered as a chip, applied only when accepted; the task page's "Or type a date"
box sets only the date fields.
**Preconditions:** signed in as `qa.user@example.com` on `/todo/all/`. The parser is the fake (see Preconditions): it
reads only `pay rent every month on the 1st at 9am` and `call the bank tomorrow`.

| # | Action | Expected result |
|---|---|---|
| 1 | Type `buy milk` in the quick-add title and wait two seconds. | No chip (a request to `/todo/parse/` may be made; it answers no date). |
| 2 | Type (key by key) `pay rent every month on the 1st at 9am` and stop. | About half a second after the last key a chip appears under the title (a group named "Suggested date"): a calendar icon, the label `<date>, 09:00 · Every month on the 1st` where `<date>` is the next 1st on or after today written `Today`, `Tomorrow`, `Mon 1 Dec` (within 6 days) or `1 Dec`, then **Accept** (check) and **Dismiss** (x). Hovering the chip shows "every month on the 1st at 9am". The Date, Time and Repeat controls are still empty. |
| 3 | Click **Accept**. | The chip goes; the title becomes `pay rent`, the Date the 1st, the Time `09:00`, the **Repeat** button reads "Every month on the 1st". Click **Add task**: the task is created as shown; its page has the chip "Every month on the 1st (fixed schedule)". |
| 4 | Type `call the bank tomorrow`. With the chip showing (label `Tomorrow`), click **Add task** without accepting. | The task is created with the title as typed and **no** date (submitting never applies the chip). |
| 5 | Type `call the bank tomorrow` again, set the Time to `18:00` by hand. | The chip reads `Tomorrow (time kept)`. Press Tab until the chip itself has focus and press Enter: accepted; the title is `call the bank`, the Date tomorrow, the Time still `18:00`. Clear the form. |
| 6 | Type `call the bank tomorrow`, focus the chip, press Escape. | The chip goes and the form is unchanged; the same text is not offered again (wait 2 s). Add a letter and remove it: still dismissed. Change it to `call the bank tomorrow!`: no chip (the fake doesn't read it). |
| 7 | Open the page of an open top-level task (not a subtask). In "Or type a date" type `call the bank tomorrow`. | A chip `Tomorrow` (`(time kept)` / `(repeat kept)` / `(time and repeat kept)` appended when the Details form already has a time or a repeat). **Accept**: the Date field is tomorrow, the box is emptied, the title is unchanged; nothing is saved until **Save** (reload without saving: the old date). |
| 8 | In the same box type `buy milk` and press Enter. | "No date found." under the box. |
| 9 | Type `pay rent every month on the 1st at 9am` there and accept. | Date the 1st, Time 09:00, Repeat "Every month on the 1st"; **Save** saves all three. |
| 10 | In dark mode and at 375 px look at both chips. | Readable, dark background, within the screen. |

### TODO-35 Every repeat the popover builds

**Purpose:** each shape of the Repeat popover gives the right sentence, first date and next date.
**Preconditions:** signed in as `qa.user@example.com` on `/todo/all/` (enough credits, see Preconditions). Create each
task with the quick-add box; "chip" is the repeat pill on its page.

| # | Action | Expected result |
|---|---|---|
| 1 | `Every other day`: Repeat > **Day**, "Every" `2`, date today. | Label "Every 2 days"; chip "Every 2 days (fixed schedule)". **Complete** it: the next is due today + 2 days. |
| 2 | `Fortnightly`: **Week**, "Every" `2`, tick only **Tuesday**, no date. | Label "Every 2 weeks on Tuesday"; the date is the next Tuesday on or after today. |
| 3 | `Month end`: **Month**, On **The last day**, no date. | Label "Every month on the last day"; the date is the last day of this month (today if today is the last day). |
| 4 | `Second Tuesday`: **Month**, On **A weekday of the month**, "second" "Tuesday". | Label "Every month on the second Tuesday"; the date is the next second Tuesday of a month on or after today. Then change it to "last" "Friday" on its page and **Save**: the chip reads "Every month on the last Friday (fixed schedule)" and the date stays as it was (a changed rule starts its series at the task's date). |
| 5 | `Birthday`: **Year**, On **The same date**, date today. | Label "Every year"; **Complete**: the next is due on the same day and month next year. |
| 6 | `Month 31`: **Month**, On **The same day of the month**, date 31 January of next year. **Complete** it, then complete the new one. | The next is due 28 February (29 in a leap year), then 31 March: a short month gets its last day and the series goes back to the 31st. |
| 7 | On any repeating task's page open Repeat, Ends **On a date** a week ago, **Done**, **Save**. | Red "Repeat: This repeat rule has no upcoming date."; nothing changes. |
| 8 | Open Repeat on a repeating task and click **Remove repeat**, then **Save**. | The button reads "Repeat"; the chip goes and "This task no longer repeats." shows. |
| 9 | Open the popover with the keyboard (Tab to **Repeat**, Enter), move through its controls with Tab, press Escape. | Every control is reachable; Escape closes the popover and the focus returns to the **Repeat** button. |
| 10 | Look at the rows of these tasks on **All**. | A fixed-schedule row shows the repeat icon and the sentence only; a from-completion one also says the mode (TODO-20). |

### TODO-36 An occurrence the balance can't pay for

**Purpose:** completing a repeating task always works; when its next occurrence can't be paid it is created owed,
task creation is blocked at once, the owed task can't be deleted forever, and a top-up settles it.
**Preconditions:** TODO-11 done: `qa.broke@example.com` has used this month's 10 free tasks and has **10** credits (if
not, set it to 10 with the adjustment tool). An admin context.

| # | Action | Expected result |
|---|---|---|
| 1 | As `qa.broke`, add `Owed rent` due today with Repeat **Day**. | Created; `/billing/credits/`: balance 0, a "-10" line "To-do: task #<N>". |
| 2 | Open its page and click **Complete**. | It completes. At once, without a reload, the amber banner "**Tasks are blocked.** A repeating task is waiting to be paid. Adding tasks resumes when you add credits." with **Top up** (to `/billing/credits/`) appears above the page. |
| 3 | Open **All** and the new `Owed rent` (`#<N+1>`, due tomorrow). | Under its number "Waiting for credits (10)" in amber. The balance is still 0 (owed, not spent). |
| 4 | Try to add a task, and a subtask under `Owed rent`. | Both refused with red "Top up your credits to add tasks: a repeating task is waiting to be paid."; nothing created. |
| 5 | Trash the owed `Owed rent` from its row: in the dialog leave "Also stop repeating" unticked, confirm. In **Trash** click its delete-forever icon and confirm. | Red "This task has not been paid for yet. Top up your credits first: it can be deleted forever once it is settled." with a **Top up your credits** link; the row stays. From its own page **Delete forever** gives the same message in the panel. **Restore** it. |
| 6 | As admin add **10** credits to `qa.broke@example.com`. | "Adjusted credits for qa.broke@example.com. New balance: 0." (the owed task was paid the moment the credits arrived). |
| 7 | As `qa.broke` reload. | The banner is gone; `#<N+1>` has no "Waiting for credits"; the ledger shows "+10" (the adjustment) and "-10" "To-do: task #<N+1>". |
| 8 | Add a task. | Refused with "You do not have enough credits to add this task." and **Top up your credits** (the allowance is used and the balance is 0), not the "blocked" message. |
| 9 | On `#<N+1>` click **Stop repeating**. | "This task no longer repeats."; completing it later creates nothing. |

### TODO-37 The open-task limit

**Purpose:** a user can't hold more open tasks than the limit, and a repeating task whose next occurrence doesn't fit
says so in the app.
**Preconditions:** TODO-27 done; signed in as `T2`, who has at least one open task besides the ones below
(`T2_TASK`); an admin context.

| # | Action | Expected result |
|---|---|---|
| 1 | As `T2` add `Cap repeat` due today, Repeat **Week**. | Created. |
| 2 | As admin set To-do settings "Max open tasks per user" to `1`. | Saved. |
| 3 | As `T2` add a task. | Red "Too many open tasks. Complete or delete some to add more."; nothing created. |
| 4 | **Complete** `Cap repeat`. | It completes, but no new `Cap repeat` appears in **All**. The bell has a notification "Couldn’t create the next “Cap repeat”" saying the next occurrence was not created, usually because of the open-task limit, and how to retry; clicking it opens the completed task. |
| 5 | As admin put the limit back to `10000`. As `T2`, **Uncomplete** then **Complete** `Cap repeat`. | The next `Cap repeat` is created (due a week after the first). The "Couldn’t create" notification is gone. |

### TODO-38 Markdown rendering

**Purpose:** the description and comments render exactly the supported Markdown, safely.
**Preconditions:** signed in as `qa.user@example.com`, on an open task's page.

| # | Action | Expected result |
|---|---|---|
| 1 | Set the description to the lines: `# Big heading`, `> a quote`, ``Some `code` here``, a fenced block (```` ```python ````, `print("hi")`, ```` ``` ````), `1. first`, `2. second`, `---`, `line one` and on the next line `line two`; **Save**. | A heading (no anchor link), a block quote, inline code, a monospace code block, a numbered list, a horizontal rule; "line one" and "line two" on two lines (a single newline is a line break). |
| 2 | Set it to `www.example.com, README.md, settings.py, someone@example.com and https://example.com/a?b=1`. | `www.example.com` and the https URL are links that open in a new tab; `someone@example.com` is a `mailto:` link; `README.md` and `settings.py` stay plain text. |
| 3 | Set it to `![logo](https://example.com/logo.png)` and `[bad](javascript:alert(1))` and `<b>raw</b>`. | No image is loaded: "logo" is a link to the image URL; "bad" is plain text with no link; `<b>raw</b>` shows literally. No alert, no console error. |
| 4 | Set it to a three-line Markdown table (a header row `a`, `b`, a dashes row and a row `1`, `2`, each cell between pipe characters), then `~~strike~~` and `[^1]`. | Shown as literal text (tables, strikethrough and footnotes are not supported). |
| 5 | Add a comment with the same text as step 2. | The comment renders the same way. |

### TODO-39 Quick add on Upcoming and All

**Purpose:** the quick-add box works on every page that has one and refreshes that page's list.
**Preconditions:** signed in as `qa.user@example.com`.

| # | Action | Expected result |
|---|---|---|
| 1 | On **Upcoming** add `In three days` with the date today + 3. | It appears under that day's heading without a reload; the box is cleared. The free-tasks line is under the box. |
| 2 | On **Upcoming** add `No date here` with no date. | Created (it shows on **All**, not on Upcoming). |
| 3 | On **All** (heading "All tasks") add `From All`. | It appears in the list in its sorted place. **Completed** and **Trash** have no quick-add box. |

---

## Out of scope

- **Tasks added by email.** Mail to the `todo+<code>@` address reaches the site only through SES, which drops it
  in S3 for the poller; nothing in this environment delivers it, so the "Recent emails" list stays "No emails yet."
  and the parser, sender authentication, attachments from mail and the `rejected_*` outcomes are unit-tested
  (`apps/todo/tests/test_inbound.py`). `./manage.py todo_ingest_eml` can feed a local `.eml` by hand, but it is a
  developer tool, not a user flow.
- **A real AI model.** The date chip runs against the fake of `tests_e2e/todo_fakes.py`; how a real model reads
  other phrasings, its time-outs and the "busy" back-off are unit-tested (`test_parsing.py`).
- **The real daily storage metering and trash purge.** They are scheduler jobs that act on a day's worth of time;
  TODO-31 freezes the storage directly and the purge is unit-tested. Likewise reminder delivery runs only through
  the shell steps (the scheduler does not run on this server).
- **A reminder email to an unconfirmed address** ("not sent: your email address is not confirmed"): reaching it
  needs an email change through the JWT API, which is out of scope here; unit-tested.
- **Rate limits** (switched off on this server), the JWT API `/api/todo/` and the `inv todo` CLI (unit tests).
