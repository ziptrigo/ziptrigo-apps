# To-do

## Scope

The To-do product's web UI (`apps/todo`, issues #178, #179, #180 and #182: tasks, repeating tasks (TODO-19 and TODO-20),
comments and task references (TODO-17 and TODO-18), reminders (TODO-21); search, attachments, AI parsing and email
intake are later issues and are not covered):

- the sidebar (Today with a count, Upcoming, All, Completed, Labels, Trash, Settings; a top bar on
  phones) and the "Tasks are blocked" banner;
- **Today** `/todo/` (the quick-add box, the Overdue section with "Reschedule overdue to today", today's
  tasks, drag-and-drop order), **Upcoming** `/todo/upcoming/` (14 days, "Nothing due"), **All**
  `/todo/all/` (sorts, 50 per page), **Completed**, **Trash**;
- the task page `/todo/<number>/`: inline title edit, the Markdown description with clickable
  checklists, due date, time, priority, labels, subtasks (add, complete, reorder), Complete,
  Postpone, Move to trash, Restore, Delete forever;
- comments on the task page (add, edit with a visible history, delete and restore, Markdown, `#1234` links) and the
  "Referenced by" panel;
- **Labels** `/todo/labels/` (add, edit, colour, description tooltip, reorder, delete) and
  **Settings** `/todo/settings/` (default due time, default reminder and channel, the free-tasks line) and the reminder
  history `/todo/reminders/history/`;
- credits: the first 10 tasks of a calendar month are free, then 10 credits each; subtasks count;
- access control: logged-out visitors, another user's task numbers.

Not covered here: the JWT API and the CLI (later issue), the credit settling of an unpaid repeating
task (covered by unit tests, and by TODO-14 for the banner), the daily purge job (unit tests).

## Preconditions

- The test server is running at `http://localhost:8765`, freshly restarted (empty database), as
  described in [`README.md`](README.md). Rate limiting is off.
- "Today" is the date of the machine the server runs on, in the user's time zone (a freshly seeded
  account has none set, so UTC). Cases below say "today", "tomorrow" and "yesterday"; compute the
  dates from the date shown on **Today**'s heading ("Today  Wednesday 28 Oct"). Date inputs are
  `type=date`: type the date the way the browser expects (`10/28/2026` in an English browser).
- Prices are the defaults: **10 free tasks per calendar month**, then **10 credits** per task. Subtasks
  count as tasks. `qa.user@example.com` has 100 credits, `qa.broke@example.com` has none.
- Cases run in order; later ones reuse tasks created earlier. Task numbers start at **1000** per user:
  the first task created by `qa.user@example.com` is `#1000`.

## Test data

| Name | Value |
|---|---|
| Main user | `qa.user@example.com` / `QaE2e-Passw0rd!` (100 credits) |
| Broke user | `qa.broke@example.com` / `QaE2e-Passw0rd!` (0 credits) |
| Admin | `qa.admin@example.com` / `QaE2e-Passw0rd!` |
| Visitor | a private/incognito browser context where nobody is logged in |

Record as you go: `T_REPORT` (the number of `Write report`), `T_CALL`, `T_LATE`, `T_FUTURE`,
`T_PARENT`, and the subtasks' numbers.

---

### TODO-01 Logged-out visitors are sent to the login page

**Purpose:** every To-do page needs a login.
**Preconditions:** not logged in (fresh browser session).

| # | Action | Expected result |
|---|---|---|
| 1 | Go to `http://localhost:8765/todo/`. | Redirected to `/account/login/?next=/todo/`. |
| 2 | Go to `/todo/upcoming/`, `/todo/all/`, `/todo/completed/`, `/todo/trash/`, `/todo/labels/`, `/todo/settings/` and `/todo/1000/`. | Each redirects to the login page with its path in `next`. Nothing about any task is revealed. |
| 3 | Sign in as `qa.user@example.com` / `QaE2e-Passw0rd!` from the `/todo/` redirect. | Lands on `/todo/`. |

### TODO-02 Empty Today and navigation

**Purpose:** a user with no tasks sees a clean empty state and the whole sidebar.
**Preconditions:** logged in as `qa.user@example.com`.

| # | Action | Expected result |
|---|---|---|
| 1 | Go to `/` and click **To-do** in the top navigation (check-square icon). | Lands on `/todo/`. Tab title "Today - To-do - ZipTrigo". |
| 2 | Look at the page. | A sidebar with Today (highlighted, no count), Upcoming, All, Completed, Labels, Trash, Settings, each with an icon. Heading "Today". A quick-add box: a "Add a task" field, an **Add task** button, Date, Time and Priority controls and an "Add labels" button, and the line "0 of 10 free tasks used this month." Below, "Today <weekday date>" with "Nothing due today. Add a task above." No Overdue section. No raw `{{ }}`/`{% %}`, broken icons or console errors. |
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
| 1 | Click `Write report` in the list. | `/todo/1000/`: "#1000", the title as a heading, buttons **Complete**, **Postpone**, **Move to trash**; a Details section (Date today, Time empty, Priority 1, "Add labels", **Save**); a Description section with **Edit** and "No description."; a Subtasks section with a "New subtask" box. A back link "All tasks". |
| 2 | Click the pencil beside the title, change it to `Write the report`, press the check button. | The heading becomes `Write the report` (the whole panel re-renders; no page reload). Press Escape while editing instead: it cancels. |
| 3 | Click **Edit** on the description. | A textarea (monospace) with a hint line listing the Markdown syntax (bold, italic, code, headings, lists, checklists, quotes, links, `#1234`). **Cancel** and **Save**. |
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
| 8 | Click **Cancel**. | Dialog closes, nothing changes. Reopen it and click **Reschedule**. | The page reloads with "1 overdue task moved to today." The Overdue section is gone and `Overdue thing` is under Today. |

### TODO-07 Subtasks

**Purpose:** subtasks are one level deep, ordered by hand, and completed with their parent after asking.
**Preconditions:** TODO-04 done; open `/todo/1000/` (`T_PARENT`).

| # | Action | Expected result |
|---|---|---|
| 1 | In **New subtask** type `Sub one` and click **Add**. Repeat for `Sub two` and `Sub three`. | Each appears in the Subtasks list (rows with a drag handle, circle, title, `#number`) and the panel re-renders. Record their numbers (`#1004`..`#1006` if nothing else was created). |
| 2 | Open **All**. | `Write report` shows a subtask badge `0/3`; each subtask row shows "in Write report". |
| 3 | Back on the task page, drag `Sub three`'s handle above `Sub one`. | The order changes at once. Reload: the order is kept. |
| 4 | Click the circle of `Sub one`. | It becomes a filled circle and stays in the list (struck through). The badge on All reads `1/3`. |
| 5 | Open `Sub two`'s page. | "Subtask of Write report" with a link; no Subtasks section (a subtask has no subtasks). |
| 6 | On the parent's page click **Complete**. | A dialog "Complete task": "Write report has 2 open subtasks. Completing it completes them too." with **Cancel** and **Complete**. Cancel closes it. |
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
| 5 | Trash `Write report` (it has subtasks) from its page, then open **Trash**. | The parent and all of its subtasks are listed. |
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
| 3 | Click **Newest**, then **Title**. | Newest first by creation; then alphabetical (accents ignored). |
| 4 | Reload on `?sort=title`. | The same sort and the Title link marked as current. |
| 5 | On **Today** drag `B` above `A` using the grip handle. | The order changes. Reload: kept. Add another task for today: it goes to the end of the manual order. |
| 6 | Look at **Upcoming**. | Tasks grouped under their day headings in date order; empty days say "Nothing due"; each row has the same actions as on Today. |

### TODO-11 Credits: free allowance and price

**Purpose:** the first 10 tasks of the month are free, then 10 credits each, and a refused task costs nothing.
**Preconditions:** a fresh server (restart it), logged in as `qa.user@example.com`; balance 100.

| # | Action | Expected result |
|---|---|---|
| 1 | Create 10 tasks (titles `Free 1` to `Free 10`) with the quick-add box. | All succeed; the free line counts up to "10 of 10 free tasks used this month."; the balance (`/billing/credits/`) is still 100. |
| 2 | Create `Paid 1`. | It succeeds; the balance becomes **90**; the ledger has a line of -10 described "To-do: task #<number>". |
| 3 | Add a subtask under any task. | The balance becomes 80: subtasks count as tasks. |
| 4 | Open **Settings**. | "**10 of 10** free tasks used this month." (never more than 10, although 12 tasks were created), the line "After that each task costs 10 credits.", and the note that subtasks and every occurrence count and credits are not refunded. A link to the account page for the time zone. |
| 5 | Trash and delete forever `Paid 1`. | The balance is unchanged (80). Creating another task still costs 10 (the month's count never goes down). |
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
| 1 | Open **Settings**. | A "Default due time" section with a time field showing `09:00`, **Save**, a note that it is the time a date-only task is treated as due and such a task is still shown date-only; a "What tasks cost" section. |
| 2 | Change it to `07:45` and **Save**. | "Saved." in green; reload shows `07:45`. |
| 3 | Clear the field and Save. | The browser's required prompt (or "Default due time: Enter a time."). |
| 4 | Click the "account page" link. | Goes to the account page where the time zone can be changed. |

### TODO-14 The blocked banner (admin-seeded)

**Purpose:** the layout shows "Tasks are blocked" while creation is blocked. Completing a repeating task without credits
reaches it (TODO-20 step 6); here it is set from the admin shell.
**Preconditions:** logged in as `qa.user@example.com`; shell access to the test server database (skip this case without it).

| # | Action | Expected result |
|---|---|---|
| 1 | In a Django shell set `TodoAccount.objects.filter(user__email='qa.user@example.com').update(creation_blocked_at=timezone.now())`. Reload `/todo/`. | An amber banner "Tasks are blocked. A repeating task is waiting to be paid. Adding tasks resumes when you add credits." with a **Top up** link on every To-do page. |
| 2 | Try to add a task. | A red message "Top up your credits to add tasks: a repeating task is waiting to be paid."; nothing is created. |
| 3 | Clear it again (`creation_blocked_at=None`). | The banner is gone; adding works. |

### TODO-15 Admin

**Purpose:** staff can see tasks and the settings.
**Preconditions:** logged in as `qa.admin@example.com`.

| # | Action | Expected result |
|---|---|---|
| 1 | Open `/admin/` and find the **To-do** section. | Entries: Comments, To-do accounts, Labels, Tasks, To-do settings. |
| 2 | Open **Tasks**. | A read-only list (user, number, title, due date, priority, charged credits, unpaid, source) with filters for priority, source, completed, trash and unpaid, and a search box (title, number, email). No "Add" button; opening a task shows no Save button; deleting is refused. |
| 3 | Search `1000`. | The tasks numbered 1000 of every user. |
| 4 | Open **To-do settings**. | Straight to the one form: free tasks per month (10), task price (10), trash days (30), max open tasks per user (10000), max labels per user (200). Change the price to `0`, save; as `qa.user@example.com` the next task beyond the allowance costs nothing. Put it back to 10. |
| 5 | Open **To-do accounts**. | One row per user who visited, with next task number, "created this month", and the blocked flag. |

### TODO-16 Dark mode and mobile layout

**Purpose:** the pages work in both themes and on a phone.
**Preconditions:** logged in as `qa.user@example.com` with some tasks, labels and a subtask.

| # | Action | Expected result |
|---|---|---|
| 1 | Set the theme to dark under Appearance on the account page; open Today, a task page, Labels and Settings. | Every surface has a dark twin: no white boxes, text readable (the due badges, flags, chips, red errors, the menu, the dialogs). |
| 2 | Set the viewport to 375 px wide and open `/todo/`. | No horizontal page scroll. The sidebar is a single horizontally scrolling row of links above the content. The quick-add title is full width with the button below it; the date/time/priority controls wrap. |
| 3 | Open a task page and the Postpone menu. | The menu stays on screen; the Details controls wrap; the subtasks list fits. |
| 4 | Open Labels and edit a label. | The edit fields wrap onto several lines; the buttons stay reachable. |
| 5 | Tab through Today with the keyboard. | Every control (circle, title link, menu, trash) is reachable with a visible focus ring; the menus open with Enter and close with Escape. |
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
| 2 | Open `/todo/<T_REPORT>/`. | Below the comments a **Referenced by** section lists `#<T_CALL> Call ...` as a link; no "in a comment". |
| 3 | On `T_LATE`, add the comment `Related: http://localhost:8765/todo/<T_REPORT>/ and [that one](/todo/<T_REPORT>/)`. | Both render as links to the task (the absolute one is not opened in a new tab). |
| 4 | Open `T_REPORT`. | **Referenced by** now also lists `#<T_LATE> ...` followed by "in a comment"; the entries are ordered by task number. Clicking the `T_LATE` entry opens its page scrolled to that comment. |
| 5 | On `T_LATE`, edit the comment to remove the links, then open `T_REPORT`. | The `T_LATE` entry is gone. |
| 6 | Edit the same comment to mention `#<T_REPORT>` again, then **Delete** it. Open `T_REPORT`. | The `T_LATE` entry is gone again (a deleted comment references nothing). **Restore** the comment: the entry returns. |
| 7 | Move `T_CALL` to the trash. Open `T_REPORT`. | `T_CALL` is no longer listed. Restore it: it is listed again. |
| 8 | On `T_REPORT`, add a comment `#<T_REPORT>` (itself). | It renders as a link to itself, but `T_REPORT`'s own **Referenced by** does not list `T_REPORT`. |
| 9 | Log in as `qa.broke@example.com`, write `#<T_REPORT>` in a description of one of their tasks (they have no task with that number). | It stays plain text, and `qa.user`'s `T_REPORT` is unaffected. |

### TODO-19 Repeating task, fixed schedule

**Purpose:** a fixed-schedule repeat keeps to its schedule when a task is postponed or completed late.
**Preconditions:** logged in as `qa.user@example.com` with at least 4 free tasks left this month (or enough credits); on `/todo/`.
Compute dates from the heading of **Today**; "the 1st" below means the first day of next month.

| # | Action | Expected result |
|---|---|---|
| 1 | In the quick-add box type `Pay rent`. Open **Repeat**. | A popover with **Frequency** (Does not repeat, Day, Week, Month, Year), defaulting to "Does not repeat", and a **Remove repeat** / **Done** row. No mode radios yet. |
| 2 | Choose **Month**; under **On** choose **Day of the month** and type `1`. | The button label reads "Every month on the 1st". "Every [1] month" is shown; **Ends** offers Never / After a number of times / On a date; two radios: "Fixed schedule: the next one is due on schedule even if this one is late" (selected) and "From completion: the next one is counted from when I complete this one". |
| 3 | Click **Done**, then **Add task**. | The task is created. It is not on Today (it is due on the 1st); open **All**: the row shows `Pay rent`, the date of the 1st and a repeat icon with "Every month on the 1st". Record the number as `T_RENT`. |
| 4 | Open `/todo/<T_RENT>/`. | The Date field is the 1st (a rule with no date takes its first date). Below Details: the chip "Every month on the 1st (fixed schedule)" and a **Stop repeating** link. |
| 5 | Click **Postpone** > **Pick a date**, choose the 5th (of next month). | The Date field is the 5th. The chip still says "Every month on the 1st". |
| 6 | Click **Complete**. | The task shows Completed, with the note "Uncompleting this task keeps the next occurrence of the repeat if one was already created: nothing is deleted or refunded." |
| 7 | Open **All**. | A new `Pay rent` (number `T_RENT + 1`) is due on the 1st of the month **after** (not the 5th plus a month), with the same chip. Open **Completed**: the finished one is its own row. |
| 8 | Open `/todo/<T_RENT>/` and click **Uncomplete**. | It is open again; **All** still lists the next occurrence (two open `Pay rent` rows). Move this older one to the trash with its row's trash icon: with two open occurrences there is no "Also stop repeating" checkbox, it just moves. |
| 9 | On the newer occurrence's row click the trash icon. | Now the newest is the only open occurrence: a dialog "Move to trash" with an unticked checkbox "Also stop repeating". Tick it and confirm. The row leaves **All**. Open its page from **Trash**: "This task no longer repeats." and no chip. |
| 10 | Create `Weekly review` with Repeat = Week, ticking Monday and Wednesday, Ends "After a number of times" `2`. | The button reads "Every week on Monday, Wednesday, 2 times". After adding, its date is the next Monday or Wednesday on or after today. |
| 11 | Open `Weekly review`, click **Stop repeating**. | The chip and the link disappear and "This task no longer repeats." shows; the task stays open. Click **Repeat** > **Week** > **Done** > **Save**: the chip returns ("Every week"). |
| 12 | In the Repeat popover enter Month with "On" = **Day of the month** `31` and Save on a task. Open its page. | The chip reads "Every month on the 31st". (Completing it later skips months without a 31st: not checked here.) |

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
| 6 | As `qa.broke@example.com` (no credits; the first 10 tasks of the month are free), create 10 tasks including one repeating task, complete the repeating one. | Completing works and its next occurrence is created (owed): the page shows the "Tasks are blocked" banner with **Top up**, and adding a task is refused until credits are added. (Skip when this takes too long.) |

### TODO-21 Reminders

**Purpose:** a task's reminders are listed, added, edited and removed, a new task with a date gets the default one, and a
due reminder shows in the notification bell and the history.
**Preconditions:** logged in as `qa.user@example.com` with at least 3 free tasks left this month (or enough credits).
The delivery step needs shell access to the test server database (the scheduler does not run there): without it skip steps
9 to 11.

| # | Action | Expected result |
|---|---|---|
| 1 | Open **Settings**. | A "Default reminder" section: a select "New tasks with a date" showing "Get a reminder", an amount `5`, a unit "minutes before", a "Default channel" select showing "In-app", the note "Email reminders go to your confirmed address only" and a **Reminder history** link. |
| 2 | In the quick-add box type `Call the bank`, set Date = today and Time = 23:00, **Add task**. Record the number as `T_BANK`. Open `/todo/<T_BANK>/`. | A **Reminders** block lists "5 minutes before (in-app)" with "Fires" and today's date at 22:55. Its row on Today shows a small bell icon. Under it, **History** says "No reminders sent yet." |
| 3 | In the add row choose **1 hour before**, channel **Both**, and press **Add**. | The list now has "1 hour before (both)" (fires 22:00) above the 5 minute one. |
| 4 | Add **1 hour before** again. | A red message "Reminder: There is already a reminder at that time." and the list is unchanged. |
| 5 | Choose **Custom...**, enter `2` and unit `days before`, channel Email, **Add**. | "2 days before (email)" is listed (shown as "Sent": its time, two days before the due time, is more than a day past, so it is created already sent and never delivered). |
| 6 | Click the pen on "5 minutes before", change the amount to `15`, **Save**. | The row reads "15 minutes before (in-app)" and fires at 22:45. Press **Cancel** on another row's editor: nothing changes. |
| 7 | Click the bin on the "2 days before" row. | It is removed. |
| 8 | In **Details** clear the Date and press **Save**. | A browser confirmation "Clearing the date removes its 2 reminders. Continue?". Cancel: nothing changes. Accept: the Reminders block says "Set a due date to add reminders." and the bell is gone from the row. Set the date to today again and Save: the default reminder (5 minutes before) is back. |
| 9 | In a Django shell set the reminder's `fire_at` to a minute ago (`Reminder.objects.filter(task__number=<T_BANK>).update(fire_at=timezone.now() - timedelta(minutes=1), sent_at=None)`) and run `from apps.todo.services import reminders; reminders.deliver_due()`. Reload any To-do page. | The header bell shows a new notification titled `Call the bank` with the body "Due <today's date>, 23:00", which opens the task. |
| 10 | Open `/todo/<T_BANK>/`. | **History** lists the delivery (date, time, "In-app"). Open **Settings** > **Reminder history**: the same row with `#<T_BANK> Call the bank` linking to the task. |
| 11 | Run `deliver_due()` again. | Nothing new: the bell count and the history are unchanged (a reminder is delivered once). |
| 12 | In **Settings** choose "Get no reminder", **Save**, then add a task `No reminder please` with a date. | Its page says "No reminders." and its row has no bell. Restore the setting (5 minutes) afterwards. |
| 13 | Complete a repeating task that has a reminder (create `Pay rent`, Repeat = Week, with a 15 minute reminder, then **Complete**). Open the new occurrence. | It has the same reminder ("15 minutes before"), unsent, firing before the new date. |
