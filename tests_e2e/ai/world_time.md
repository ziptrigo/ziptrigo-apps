# World Time

## Scope

The World Time product (`apps/world_time`, issue #269, PR 1 of 3: tabs, locations, the grid and picking a time
range). A world clock: named tabs of locations, each row showing the hours of the home row's day on its own clock.
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
- access control: logged-out visitors and another user's tabs.

The admin side is in `admin.md` (ADM-62). Shared events (PR 2) and the API and CLI (PR 3) are not built yet.

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
| 1 | Visitor: open `/time/` and `/time/<any uuid>/`. | Each redirects to `/account/login/?next=...`. |
| 2 | Context A: open `/`. | The header has a **World Time** link (a globe icon) and the home page a **World Time** card, "A world clock: compare time zones and find a time that works.", opening `/time/`. |
| 3 | Open `/time/`. | Tab title `My times - World Time - ZipTrigo`; heading **World Time**; one tab, **My times**, marked current; a pencil (Rename this tab) and **New tab**, and **no** delete button (the last tab can't be deleted); an **Add a location** search box with the placeholder "City or time zone: Porto, Tokyo, UTC, PST"; a day picker showing today between a previous and a next arrow, and no **Today** button. |
| 4 | Look at the grid. | One row. If the account has no time zone it is **UTC**; otherwise the city part of the zone (e.g. **Lisbon**). It has a drag handle, a house icon (the home row), the current time and date there, and 24 hour tiles starting with the weekday and date (e.g. `Sat` over `11/14`). Under the grid: a legend (Working hours, Early and late, Night, Weekend) and "Click an hour, or drag across the hours, to pick a time range and see it in every location." At the bottom: "City data from GeoNames, licensed CC BY 4.0." (GeoNames is a link). |
| 5 | Open `/billing/costs/`. | A **World Time** section with one line, "Tabs, locations and the time grid", **Free**, "World Time is free." |
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

## Out of scope

- Shared event pages (`/w/<code>`), the JWT API and the `inv worldtime` CLI: later PRs of #269.
- Rebuilding the city file from GeoNames (`./manage.py world_time_build_cities` downloads from geonames.org).
- Rate limits (`WT_EDIT_USER`, `WT_SEARCH_USER`) are off on this server; unit tests cover them.
