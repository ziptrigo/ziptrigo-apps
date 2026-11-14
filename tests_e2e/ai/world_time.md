# World Time

## Scope

The World Time product (`apps/world_time`, issue #269; PR 1 of 3: tabs, locations, the grid and picking a time
range; PR 2: shared events). A world clock: named tabs of locations, each row showing the hours of the home row's day on its own clock.
Free: nothing here costs credits.

- the page `/time/` (the first tab) and `/time/<tab id>/`, with `?date=YYYY-MM-DD` for another day;
- the tabs (new, rename, delete; at most 10) and the locations of a tab (add by search, rename, make home, remove,
  reorder; at most 20);
- the **Add a location** search: cities (by name, without accents, by another name such as `Lisboa`), time zones by
  name (`UTC`, `Europe/Lisbon`) and by abbreviation (`PST`, `IST`);
- the grid: hour tiles in the account's time format, the shading (working hours, early and late, night, weekend), the
  tile where a local day starts, the offset from the home row, the live clock, the "now" line, the daylight-saving
  note, the day picker and the previous / next day links;
- picking a time range (click, drag, keyboard, the Start and End fields) and **Copy times**;
- shared events (WT-08 to WT-11): **Share as an event** from a selected range, the owner's list `/time/events/` and an
  event's page (link, QR code, edit, delete), and the public page `/w/<code>` (the time in the visitor's zone, a
  countdown, each location, the neutral page of a dead link);
- access control: logged-out visitors and another user's tabs.

The admin side is in `admin.md` (ADM-62, ADM-63). The API and CLI (PR 3) are not built yet.

## Preconditions

- The server was started with `python tests_e2e/ai/serve.py` (it seeds the city tables; `/admin/world_time/citydata/`
  shows about 32,000 cities).
- Two browser contexts: **Context A** (the main user) and **Visitor** (nobody signed in).

## Test data

| What | Value |
|---|---|
| Main user | `qa.user@example.com` / `QaE2e-Passw0rd!` |
| Other user | `qa.broke@example.com` / `QaE2e-Passw0rd!` |
| Admin | `qa.admin@example.com` / `QaE2e-Passw0rd!` |
| Visitor | a private/incognito browser context where nobody is logged in |

Before WT-02, set the main user's time zone to `Europe/Lisbon` on `/account/` (Profile) and keep the date format
`mm/dd/yyyy` and the time format `12-hour` (Appearance), unless a case says otherwise. Record `TAB_A`, the id in the
address of the first tab.

---

### WT-01 Access control and the first visit

**Purpose:** the app needs a login; a first visit gets one tab whose only row is the account's time zone.
**Preconditions:** a brand-new account `qa.wt.<random>@example.com` (register, confirm) signed in in Context A with **no**
time zone saved yet (dismiss nothing: check `/account/` says "Not set (UTC)", or clear it); Visitor has no session.

| # | Action | Expected result |
|---|---|---|
| 1 | Visitor: open `/time/`, `/time/<any uuid>/` and `/time/events/`. | Each redirects to `/account/login/?next=...`. |
| 2 | Context A: open `/`. | The header has a **World Time** link (a globe icon) and the home page a **World Time** card, "A world clock: compare time zones and find a time that works.", opening `/time/`. |
| 3 | Open `/time/`. | Tab title `My times - World Time - ZipTrigo`; heading **World Time**; one tab, **My times**, marked current; a pencil (Rename this tab) and **New tab**, and **no** delete button (the last tab can't be deleted); an **Add a location** search box with the placeholder "City or time zone: Porto, Tokyo, UTC, PST"; a day picker showing today between a previous and a next arrow, and no **Today** button. |
| 4 | Look at the grid. | One row. If the account has no time zone it is **UTC**; otherwise the city part of the zone (e.g. **Lisbon**). It has a drag handle, a house icon (the home row), the current time and date there, and 24 hour tiles starting with the weekday and date (e.g. `Sat` over `11/14`). Under the grid: a legend (Working hours, Early and late, Night, Weekend) and "Click an hour, or drag across the hours, to pick a time range and see it in every location." At the bottom: "City data from GeoNames, licensed CC BY 4.0." (GeoNames is a link). |
| 5 | Open `/billing/costs/`. | A **World Time** section with one line, "Tabs, locations, the time grid and shared events", **Free**, "World Time is free." |
| 6 | Sign in as the Other user in another context and open `/time/<TAB id of the new account>/`. | 404. |

### WT-02 Add locations: cities, other names, time zones, abbreviations

**Purpose:** the search finds a place however it is typed, and each suggestion adds a row.
**Preconditions:** Context A as the main user (time zone Europe/Lisbon), on `/time/`.

| # | Action | Expected result |
|---|---|---|
| 1 | Type `p` in **Add a location**. | Nothing is suggested (two letters are needed). |
| 2 | Type `porto`. | A list appears under the box: **Porto**, Portugal first among several places starting with "Porto" (most populous first), each with a pin icon and its region and country; time zones (a clock icon, "(Time zone)") come last. |
| 3 | Click **Porto**, Portugal. | A second row **Porto** appears, offset `+0`, with its own clock; the box is emptied and the list closes. No page reload. |
| 4 | Type `sao paulo` (no accent) and press Enter. | The first suggestion, **São Paulo**, São Paulo, Brazil, is added (offset `-3` in November). |
| 5 | Type `roma`. | **Rome**, Lazio, Italy is the first suggestion, "(also Roma)", ahead of smaller places that start with "Roma". Then type `lisboa`. | **Lisbon**, Portugal is suggested with "(also Lisboa)". Press Escape: the list closes and the focus stays in the box. |
| 6 | Type `münchen`, then replace it with `munchen`. | Both suggest **Munich**, Bavaria, Germany "(also München)". Add it. |
| 7 | Type `pst`. | The first suggestion is **Los Angeles**, "Pacific Time (PST)" with a clock icon. Add it: the row is **Los Angeles** (hover the name: `America/Los Angeles`), not a fixed "PST" row. |
| 8 | Type `ist`. | Three zones first: **Kolkata** "India Standard Time (IST)", **Dublin** "Irish Standard Time (IST)", **Jerusalem** "Israel Standard Time (IST)"; cities and **Istanbul** after them. |
| 9 | Type `utc` and add **UTC**; type `europe/lisbon`. | UTC is added. **Lisbon**, "Europe/Lisbon (Time zone)" is suggested; clicking it shows, under the box, in red: "This location is already in the tab." (the home row is that zone) and nothing is added. |
| 10 | Type `zzzz`. | "No place matches "zzzz"." |
| 11 | Use the keyboard only: type `tokyo`, press the down arrow, then Enter. | The down arrow moves the focus to the first suggestion (highlighted); Enter adds **Tokyo**, Japan. The up arrow on the first suggestion returns to the box. |
| 12 | Add `Kathmandu`. | Its tiles read `5:45`, `6:45`, ... in a smaller type that fits the tile; its offset is `+5:45`. |

### WT-03 The grid

**Purpose:** a column is one instant everywhere; the shading and the day marks follow each row's own clock.
**Preconditions:** WT-02 (rows Lisbon, Porto, São Paulo, Munich, Los Angeles, UTC, Tokyo, Kathmandu).

| # | Action | Expected result |
|---|---|---|
| 1 | Open `/time/?date=2026-12-10` (a Thursday). | The day picker shows `12/10/2026`; a **Today** button appears. The Lisbon row starts `Thu` / `12/10`, then `1 am` ... `11 pm`. In the same column as Lisbon's `9 am`: Munich `10 am`, São Paulo `6 am`, Los Angeles `1 am`, Tokyo `6 pm`, Kathmandu `2:45 pm`. |
| 2 | Look at the shading of the Lisbon row. | 8 am to 5 pm are the lightest (working hours), 6 to 7 am and 6 to 9 pm a middle tone, 10 pm to 5 am dark with light text. Each row is shaded by its own hours (Tokyo is dark where Lisbon is light). |
| 3 | Look at the Tokyo row. | The tile at Lisbon's 3 pm is `Fri` / `12/11` with a line on its left: Tokyo's Friday starts there. Hover a tile: a tooltip with that row's full local date and time, e.g. `Fri 12/11/2026, 12:00 AM`. |
| 4 | Click the next-day arrow twice (to Saturday 12/12). | The address ends `?date=2026-12-12`; every Saturday and Sunday tile has a stripe along its bottom; Los Angeles's first tiles (still Friday there) have none. |
| 5 | Hover the offset of a row (e.g. `+9` on Tokyo). | A tooltip like `Asia/Tokyo · JST · UTC+9`. |
| 6 | Click **Today**. | Back to `/time/<TAB_A>/` with today's date; a thin red vertical line marks the current time across all rows, and each row's clock shows the current local time (it follows the minute without a reload). |
| 7 | On `/account/` set Time format `24-hour` and Date format `dd/mm/yyyy`; reload `/time/?date=2026-12-10`. | Tiles read `1` ... `23` with no am/pm, the first `Thu` / `10/12`, Kathmandu `14:45`; clocks read like `15:30`; the day picker shows `10/12/2026`. Set both back. |
| 8 | Open `/time/?date=2026-10-25`. | The Lisbon row has **25** tiles: `Sun`, `1`, `1`, `2`, ... (the clocks go back in Lisbon that morning). `/time/?date=2026-03-29` has 23. |
| 9 | Open `/time/?date=nonsense` and `/time/?date=2099-01-01`. | Both show today (a date more than ten years away is ignored). |
| 10 | Shell: nothing. In a week when a row's clocks change within 7 days (e.g. the last week of October or March for Lisbon, early November or March for Los Angeles), open `/time/`. | That row shows an amber line under its name: "Clocks go back 1 hour on Sun 10/25/2026" (or "forward"). Outside such a week no row has the line. |
| 11 | Narrow the window to a phone width (375 px). | The location column stays fixed on the left while the hour tiles scroll sideways under it; the page itself does not scroll sideways; the current time (or the morning) is in view. |
| 12 | Switch the theme to Dark, then a different colour scheme, on `/account/`. | The tiles, the selection and the text stay readable in each; night tiles are the darkest in both themes. |

### WT-03b A clock change in the middle of a tile

**Purpose:** when the home row is off the hour, another row's clock change falls inside a tile; the times stay right.
**Preconditions:** a new tab `Half hours` whose home is `Kolkata` (make it home, remove the first row) with a second row `New York`.

| # | Action | Expected result |
|---|---|---|
| 1 | Open the tab with `?date=2026-03-08`; click the **right** half of Kolkata's `12 pm` tile. | Kolkata `Sun 03/08/2026, 12:30 PM – 1:30 PM`; New York `Sun 03/08/2026, 3:00 AM – 4:00 AM` (never 2:00 AM, which that day does not have in New York). |
| 2 | Click the **left** half of the same tile. | Kolkata `12:00 PM – 1:00 PM`; New York `1:30 AM – 3:30 AM` (one real hour across the change). |

### WT-04 Pick a time range

**Purpose:** a range is shown in every location's local time and can be copied.
**Preconditions:** WT-03, on `/time/?date=2026-12-10`, 12-hour format.

| # | Action | Expected result |
|---|---|---|
| 1 | Click the left half of Lisbon's `9 am` tile. | A band covers that hour in every row; a **Selected time** card appears with `1 h`, **Start** `9:00 AM`, **End** `10:00 AM`, "Start and end are on Lisbon's clock, within the day shown." and one line per location: Lisbon `Thu 12/10/2026, 9:00 AM – 10:00 AM`, Tokyo `Thu 12/10/2026, 6:00 PM – 7:00 PM`, Kathmandu `Thu 12/10/2026, 2:45 PM – 3:45 PM`, Los Angeles `Thu 12/10/2026, 1:00 AM – 2:00 AM`. |
| 2 | Click the right half of the `9 am` tile. | The range is 9:30 AM to 10:30 AM. |
| 3 | Drag from the start of `2 pm` to the middle of `4 pm` on any row. | `2:00 PM – 4:30 PM`, `2 h 30 min`; the band follows the pointer in half-hour steps. |
| 4 | Drag across Lisbon's `4 pm` to `6 pm`. | Tokyo's line spans two days: `Fri 12/11/2026, 1:00 AM – 3:00 AM`; a range crossing Tokyo's midnight reads `Thu 12/10/2026, 11:00 PM – Fri 12/11/2026, 1:00 AM`. |
| 5 | In **Start** type `10:10` and press Enter. | The range starts at 10:10 AM on Lisbon's clock (Kathmandu `3:55 PM`); the band's left edge moves a little. In **End** type `11:45am`: the range ends there. Type an End earlier than the Start (`9:00`): nothing changes and the field shows the previous end again. |
| 6 | Press Tab until the hours are focused (a focus ring around the tiles), then the right arrow twice, Shift + right arrow once, Shift + left arrow once. | The range moves 30 minutes later per arrow; Shift makes it 30 minutes longer, then shorter. A screen reader hears e.g. "Selected Thu 12/10/2026, 11:10 AM – 12:45 PM, Lisbon time." |
| 7 | Click **Copy times**; paste into a text field. | The button reads **Copied** for a moment. The text has one line per location in the grid's order, `Lisbon: Thu 12/10/2026, 11:10 AM – 12:45 PM`, etc. |
| 8 | Add `Sydney` while the range is selected. | The grid reloads with the new row and the same range still selected; Sydney has its own line. |
| 9 | Press Escape with the hours focused, or click **Clear**. | The band and the card disappear; the hint line is back. With no selection, Enter on the focused hours selects an hour (the current one today, 9 AM on another day). |
| 10 | Phone width: tap a tile. | The hour is selected (dragging scrolls the grid instead of selecting); the card stacks; Start and End can still be typed. |

### WT-05 Rename, home, reorder and remove a row

**Purpose:** the rows are the user's to arrange; one is always the home.
**Preconditions:** WT-02.

| # | Action | Expected result |
|---|---|---|
| 1 | Click the three dots of **Tokyo**. | Under its name: **Make home**, **Rename**, **Remove**. The three dots of the home row offer no **Make home**. |
| 2 | **Rename**, type `Tokyo office`, press Enter. | The row is **Tokyo office**; hovering it still says `Tokyo, Japan`. Rename again with 61 characters (paste into the field with devtools removing `maxlength`): red "Label: Keep the label to 60 characters or fewer." above the grid. Rename to an empty name: the row is **Tokyo** again. Escape while renaming discards what was typed. |
| 3 | **Make home** on Tokyo. | The house icon moves to Tokyo; the grid now starts at Tokyo's midnight (`?date` unchanged); Lisbon's offset reads `-9`. |
| 4 | Drag Kathmandu's handle above Lisbon. Reload. | The order is kept. |
| 5 | Focus a handle with Tab and press the up arrow twice quickly, then the down arrow at the last row. | The row moves two places and stays focused; a screen reader hears "<name> moved to position N of M."; at the end "<name> is already last." Reload: the order is kept. If a range is selected, its lines follow the new order. |
| 6 | **Remove** on Los Angeles. | A dialog **Remove location**, "Remove Los Angeles from the tab My times?", **Cancel** / **Remove**. Cancel: nothing changes. Remove: the row is gone and the day shown is kept. |
| 7 | Remove the home row (Tokyo). | The first remaining row becomes the home (house icon). |
| 8 | Remove every row. | "No locations in this tab yet", "Search for a city or a time zone above to add the first one. It becomes the home row." Add `Porto`: it is the home. |
| 9 | Admin: set **Max locations per tab** to `2` in `/admin/world_time/worldtimesettings/`. Context A: add a third location. | Red under the search: "This tab is full. Remove a location first." Restore 20. |

### WT-06 Tabs

**Purpose:** several sets of locations, each with its own home and order.
**Preconditions:** Context A on `/time/`.

| # | Action | Expected result |
|---|---|---|
| 1 | **New tab**, name `Family`, **Add tab**. | The page of the new tab (`/time/<id>/`, title `Family - World Time - ZipTrigo`), **Family** marked current next to **My times**, with one row: Lisbon (the account's zone), the home. A red delete button now shows next to the pencil. |
| 2 | **New tab** with an empty name (remove `required` with devtools), then `family`. | "Name: Give the tab a name."; "Name: You already have a tab with this name." (case ignored). Cancel or Escape closes the form. |
| 3 | The pencil: rename `Family` to `Relatives`, **Save**. | The tab and the page title change; a `?date=` in the address is kept. |
| 4 | Add `Tokyo` to Relatives; open **My times**. | Its rows are unchanged: each tab has its own. |
| 5 | Delete **Relatives** (the red button). | A dialog **Delete tab**, "Are you sure you want to delete the tab Relatives and its locations?"; **Delete**: back on `/time/` with "Deleted the tab Relatives."; only **My times** is left and it has no delete button. |
| 6 | Admin: set **Max tabs per user** to `1`. Context A: **New tab** `Extra`. | "You have too many tabs. Delete one first." Restore 10. |
| 7 | Other user: open `/time/<TAB_A>/`, and POST to `/time/<TAB_A>/rename/` (devtools). | 404 both. |

### WT-07 The city list is not loaded

**Purpose:** before the cities are seeded the search still finds time zones and says why cities are missing.
**Preconditions:** a server whose city tables are empty: `python tests_e2e/ai/manage.py shell -c "from apps.world_time.models import City, CityData; City.objects.all().delete(); CityData.objects.all().delete()"`.

| # | Action | Expected result |
|---|---|---|
| 1 | Type `porto` in **Add a location**. | "No place matches "porto". The city list has not been loaded yet, so only time zones (UTC, Europe/Lisbon, PST) can be found." |
| 2 | Type `utc`. | **UTC** is suggested and can be added. Existing rows (cities included) still show: they keep their own copy of the place. |
| 3 | Shell: `python tests_e2e/ai/manage.py world_time_seed_cities`, twice. | First "Seeded 31,821 cities and 63,077 other names." (the numbers follow the file), then "City data is up to date (31,821 cities)." `porto` finds Porto again. |

### WT-08 Share a time range as an event

**Purpose:** a selected range becomes an event with a public link.
**Preconditions:** Context A on a tab with Lisbon (home) and a renamed `Tokyo office` row, `?date=2026-12-10`, 12-hour format.

| # | Action | Expected result |
|---|---|---|
| 1 | Select 9:00 AM to 10:30 AM (drag, or click then type the End). | The **Selected time** card ends with **Share this time as an event**: a title box ("Title, e.g. Team call"), a **Share as an event** button and "You get a link anyone can open to see this time in their own time zone, with the locations of this tab under the names you gave them." |
| 2 | **Share as an event** with an empty title (remove `required` with devtools). | Red under the form: "Title: Give the event a title."; nothing is created. |
| 3 | Title `Team call`, **Share as an event**. | The event's page `/time/events/<id>/`: a green "Event created. Anyone with its link can see it."; heading **Team call**; "Starts in N days ..." and `1 h 30 min`; a box with the link `<site>/w/<22 characters>`, **Copy link**, **QR code** and **Open**; "Anyone who has this link can see the title, the time and the places below (with the names you gave them), without signing in."; **In each location**: Lisbon (a house icon) `Thu 12/10/2026, 9:00 AM – 10:30 AM` `WET · UTC+0`, Tokyo office (under it `Tokyo, Japan`) `Thu 12/10/2026, 6:00 PM – 7:30 PM` `JST · UTC+9`; an **Edit** form with Title, Date `12/10/2026`, Start `9:00 AM`, End `10:30 AM` and "On Lisbon's clock. ..." |
| 4 | **Copy link**, **QR code**. | The link is copied ("Copied"); a dialog with a black-on-white QR code and the link under it; Escape closes it and focus returns to the button. Scanning it opens the link. |
| 5 | Back on the grid, the tab bar. | An **Events** button opens `/time/events/`. |
| 6 | Admin: set **Max events per user** to `1`. Share another range. | "You have too many events. Delete one first." under the form. Restore 50. |
| 7 | Open `?date=` of a day last year, select an hour and share it. | "End: That time has already passed. Choose one that has not ended yet."; nothing is created. |
| 8 | Remove every row of a new tab, select nothing: the card is hidden, so nothing can be shared. POST to `/time/<tab>/events/` with a title and two instants (devtools). | "Add a location to the tab first." |

### WT-09 The public event page

**Purpose:** anyone with the link sees the event in their own time zone; nobody else's data shows.
**Preconditions:** WT-08's link, `EVENT_URL`; Visitor (no session).

| # | Action | Expected result |
|---|---|---|
| 1 | Visitor: open `EVENT_URL`. | Tab title `Team call - ZipTrigo`; heading **Team call**; "Starts in ..." and `1 h 30 min`; **In your time zone**: Starts and Ends in the visitor's own zone (compare with the device clock's zone), with "Shown in the time zone of this device (<zone>)."; **Around the world**: Lisbon `Thu Dec 10, 2026, 9:00 AM – 10:30 AM`, Tokyo office `Thu Dec 10, 2026, 6:00 PM – 7:30 PM` (the public wording: month as three letters, 12-hour, whatever any account's formats), with **no** house icon on any row; a **QR code** button; "Shared with ZipTrigo World Time." Nothing names the owner. |
| 2 | Leave the page open across a minute. | The "Starts in ..." line counts down without a reload. For an event in progress it reads "Happening now: ends in ..."; after the end, "Ended". |
| 3 | Open it signed in as the owner with Date format `dd/mm/yyyy` and 24-hour. | Still the public wording (`Thu Dec 10, 2026, 9:00 AM`). |
| 4 | Devtools, Network: the page's response headers. View source. | `Cache-Control: no-store, private`, `Referrer-Policy: strict-origin`, `X-Robots-Tag: noindex, nofollow`; a `<meta name="robots" content="noindex">`. |
| 5 | Open `/w/made-up-code` and `EVENT_URL` with its last character changed. | The same small standalone page for both, status 404: "This event isn't available", "The link may be wrong, or the event may have been removed.", a link to ZipTrigo. No site header. |
| 6 | Create an event titled `<b>bold</b> <script>alert(1)</script>` and open its link. | The title shows as typed text; no alert, nothing bold. |
| 7 | Disable JavaScript and reload `EVENT_URL`. | The page still reads: the times "... UTC", the status line as it was when the page was made, every location. |
| 8 | Phone width and dark mode. | Nothing scrolls sideways; each location stacks its name over its time. |

### WT-10 Edit and delete an event

**Purpose:** the owner changes an event without changing its link, and deleting it kills the link.
**Preconditions:** WT-08; `EVENT_URL` open in Visitor.

| # | Action | Expected result |
|---|---|---|
| 1 | On the event's page set Title `Planning`, Start `11:00 PM`, End `12:30 AM`; **Save**. | "Saved."; the heading is **Planning**; Lisbon reads `Thu 12/10/2026, 11:00 PM – Fri 12/11/2026, 12:30 AM` (an end at or before the start is the next day). The link is unchanged. Visitor reloads: the new title and time. |
| 2 | Clear the Title; **Save**. Then clear Start. | "Title: Give the event a title."; "Start: Choose when it starts." (the field's own message under it). Nothing changed. |
| 3 | Set the Date ten years and a month ahead. | "Start: Choose a date within ten years of today." A date in the past: "End: That time has already passed. ..." |
| 3b | For an event whose home is `New York`: Date `03/14/2027`, Start `2:30 AM`, End `4:00 AM`. | "Start: That time doesn't exist on that day: the clocks go forward over it. Choose another." With Start `1:30 AM`, End `3:00 AM` it saves, and the page says `30 min`. |
| 3c | Change only the Title of an event and **Save** (times untouched). | Only the title changes; the start and end are exactly as before (also for an event longer than a day, whose page says "This event lasts more than a day: changing its time here makes it end within a day of the new start."). |
| 4 | Rename `Tokyo office` on the tab, add a location, or delete the tab; reload the event's page and `EVENT_URL`. | The event still shows the locations it was shared with, under their old names: a snapshot. |
| 5 | `/time/events/`. | The event with its time "(Lisbon time)", its link with a copy button, Edit and Delete; "An event is deleted 30 days after it ends, and its link stops working." Events ahead come first, ended ones after with an **Ended** pill. With none: "No events yet" and **Open World Time**. |
| 6 | **Delete** (from the list or the page). | A dialog **Delete event**, "Delete Planning? Its link will stop working for everyone who has it."; **Delete**: back on the list with "Deleted the event Planning. Its link no longer works." Visitor reloads `EVENT_URL`: the neutral "This event isn't available" page. |
| 7 | Other user: open `/time/events/<id of the main user's event>/` and POST to its `edit/` and `delete/`. | 404 each. |

### WT-11 Ended events are purged

**Purpose:** an event disappears some days after it ends.
**Preconditions:** an event `Old one` whose end is in the past: create one, then
`python tests_e2e/ai/manage.py shell -c "from apps.world_time.models import Event; from django.utils import timezone; from datetime import timedelta; e = Event.objects.get(title='Old one'); e.starts_at = timezone.now() - timedelta(days=31, hours=1); e.ends_at = timezone.now() - timedelta(days=31); e.save()"`.

| # | Action | Expected result |
|---|---|---|
| 1 | `/time/events/` and the event's page. | **Ended** pill; the page says "Ended" and "It will be deleted 30 days after it ended."; its link still opens and says "Ended". |
| 2 | Shell: `python tests_e2e/ai/manage.py shell -c "from apps.world_time import jobs; jobs.purge_events()"`. | The event is gone from the list; its link is the neutral page. An event that has not ended, or ended less than 30 days ago, is untouched. |

## Out of scope

- The JWT API and the `inv worldtime` CLI: PR 3 of #269.
- Rebuilding the city file from GeoNames (`./manage.py world_time_build_cities` downloads from geonames.org).
- Rate limits (`WT_EDIT_USER`, `WT_SEARCH_USER`) are off on this server; unit tests cover them.
