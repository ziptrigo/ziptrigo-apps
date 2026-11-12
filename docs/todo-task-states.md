# To-do: task states, resolutions and blockers (issue #271)

Functional spec agreed in a Q&A session on 2026-11-11. Implementation details come later.

## 1. Model in one paragraph

Every task is **open** or **closed**. An open task has a **state**: To do (default), In progress,
Blocked or On hold. A closed task has a **resolution**: Done or Won't do. Tasks can **block** other
tasks; the links drive the Blocked state. Every change to a task is kept in an **activity log**.

## 2. Open states

| State | Meaning | Muted |
|---|---|---|
| To do | Default for every new task (web, quick add, API, email, recurrence) | no |
| In progress | Started | no |
| Blocked | Has at least one open blocker (see 4) | yes |
| On hold | Deliberately paused, no end date | yes |

There is no "Waiting" state and On hold has no "until" date.

**Muted** (Blocked, On hold):

- Shown dimmed, never shown as overdue (no red badge), never moved by "Reschedule overdue to today".
- Where a muted overdue task appears on Today is a **To-do setting**: "Today, dimmed" (default, at
  the bottom of Overdue), "Hidden from Today", or "Own section" (a collapsed "On hold & blocked (N)"
  section at the bottom).
- **Reminders are held**: a reminder whose time passes while the task is muted is not delivered.
  When the task becomes To do or In progress again, **one** reminder is sent for the task, on the
  channels the held reminders used (each channel once: in-app, email and/or push); the held
  reminders are marked skipped.

**Sorting**: within each day of Today and Upcoming, In progress first, then To do, then muted, then
the usual order. On Today's manual (drag) order, the three are groups and you drag within a group;
changing state moves the row to its group. All, filters and search are unchanged.

**Changing the state**: on the task page, from the state chip on a list row, in quick add, and
through the API and CLI. Parent and subtask states are independent (nothing propagates).

## 3. Closing and resolutions

- **Done**: what Complete does today, everywhere (the circle, the Complete button, the phone
  notification's Complete button, the API). Unchanged behaviour.
- **Won't do**: "Close as won't do" on the **task page** and through the **API and CLI** only (not
  from a list row, not from a push notification). A dialog offers an **optional note**; when filled it
  is added to the task as a comment.
- A closed task's resolution can be **changed directly** (Mark as done / Mark as won't do) without
  reopening; that creates no occurrence and touches no subtasks or blockers.
- **Reopening** puts the task back to **Blocked** when any of its blockers is still open, otherwise
  to **To do**, whatever it was before closing.
- **Closing with open blockers** (either resolution) is allowed after a confirmation naming them; the
  links are kept.
- **Subtasks**: closing a parent (Done or Won't do) with open subtasks shows them and asks: close
  them as Done, as Won't do, or leave them open. This replaces today's "complete the open subtasks
  too" question. Where nobody can be asked (the phone notification's Complete button, an API call
  that doesn't say), Done closes the open subtasks as Done, as today.
- **Repeating tasks**: Won't do means "skip this one": the next occurrence is created and billed
  exactly as on Done. The next occurrence starts **To do with no blockers**.
- **Looks**: a Won't do row has a crossed circle (instead of the tick), struck-through title and a
  "Won't do" chip.
- **The Completed page becomes "Closed"**: both resolutions, each row showing its resolution, with a
  filter All / Done / Won't do.
- Existing completed tasks become Done; existing open tasks become To do.

## 4. Blockers

- "Blocked by" is a persistent link between two of the owner's tasks, shown on both (Blocks /
  Blocked by). Subtasks can block and be blocked. Self-links and cycles are refused.
- Adding an open blocker to an open task sets it **Blocked**.
- When **all** its blockers are closed (any resolution) or in the Trash, a task **in the Blocked
  state** goes back to **To do** automatically, with an **in-app** notification ("#1234 … is no
  longer blocked"). A task with blockers that the user set to another state by hand keeps that
  state (the notice is still sent). Links are kept.
- **A blocker reopened** (or restored from the Trash) does **not** re-block: its dependents are only
  notified ("#1200 was reopened and blocks #1234").
- Blockers are set on the task page, in quick add, and through the API and CLI.

## 5. Search and saved filters

- A **state** filter (multi-select of To do, In progress, Blocked, On hold).
- "Include completed" becomes **"Include closed"**, with a **resolution** filter (All / Done / Won't
  do) when it is on.
- All of them are saved in a filter.

## 6. Activity log

Every change to a task is recorded and listed on the task page: state, resolution, blockers added or
removed, automatic unblocks, and also title, due date, priority, labels and the repeat rule. Each
entry has its time and says whether it was automatic.

## 7. API and CLI

A **breaking** change, cut in one release: `completed_at` becomes `closed_at`, `view=completed`
becomes `view=closed`, new `state`, `resolution` and blocker fields and a close endpoint taking a
resolution. The API's major version is bumped and the Android app is updated and released in step.
The CLI gets `--state`, `--blocked-by`, `close --wont-do` and the new list filters.

## 8. Delivery

Built as four issues under #271, in this order:

1. #272 States, Won't do, the Closed page, search and saved filters.
2. #273 Blockers.
3. #274 The activity log.
4. #275 The API change, the CLI and the Android app.
