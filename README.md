# UFC Calendar

An auto-updating `.ics` feed of every UFC event plus Dana White's Contender Series, meant to be subscribed to from Apple Calendar, Google Calendar, Fastmail, Outlook, etc.

- **Split cards.** Numbered events and Fight Nights become up to three entries: Early Prelims, Prelims and Main Card, each at its own start time with that segment's bouts in the notes (headliner first, records, weight class, broadcaster). DWCS nights are a single entry.
- **Fights added as announced.** Events show up as soon as ESPN lists them, as a "card TBA" placeholder if no bouts are booked yet. Every run re-reads the card and rewrites the entries.
- **Change log.** Each entry's notes end with an "Updates" list: bouts added, removed (usually a cancellation), moved between segments, and segment start times that shifted.
- **Results.** After an event, bouts show `W: <name>` for the last 30 days, then the event ages out.
- **Quiet updates.** `SEQUENCE` and `LAST-MODIFIED` only change on entries that really changed, and the file is byte-identical between runs when nothing happened, so git history doubles as a record of card changes.
- Entries are marked free (not busy) so they don't block your schedule.

## How it works

```
GitHub Actions (every 3h)
  └─ ufc_calendar.py
       ├─ ESPN scoreboard JSON, one request per month for the next 12 months
       ├─ state.json  (what was published last run: bouts, start times, sequence numbers)
       └─ docs/ufc.ics
  └─ commit ufc.ics + state.json if changed
  └─ deploy docs/ to GitHub Pages  →  https://<you>.github.io/ufc-calendar/ufc.ics
```

Data comes from ESPN's public (unofficial, unauthenticated) scoreboard API, the same JSON that powers espn.com's MMA Fightcenter. It's standard library Python only; no dependencies.

Segment detection: ESPN tags each bout with a `cardSegment`. If those labels clearly say early prelims / prelims / main card they're used directly; otherwise the segments are ordered by start time and assigned from the main card backwards.

Safety: if a fetch fails the run exits without publishing, and if the feed suddenly has zero upcoming events when the last run had some, it refuses to overwrite the calendar.

## Setup

1. Create a new **public** GitHub repo (for example `ufc-calendar`) and copy the contents of this folder into its root, including `.github/`.
2. Repo **Settings → Pages → Build and deployment → Source: GitHub Actions**.
3. **Actions** tab → "Build UFC calendar" → **Run workflow** to do the first build.
4. Open `https://<you>.github.io/ufc-calendar/` and hit Subscribe, or add the `.ics` URL by hand:
   - **Apple Calendar**: File → New Calendar Subscription. Set auto-refresh to every hour.
   - **Google Calendar**: Other calendars → + → From URL. Google refreshes subscriptions on its own schedule (often 8 to 24 hours), so changes on fight day can lag.
   - **Fastmail**: Settings → Calendars → Subscribe to a calendar. Refresh interval is configurable.

Set your own alerts on the subscribed calendar; the feed doesn't include any.

## First live run: things to check

This was built and tested against a fixture shaped like ESPN's feed, because ESPN wasn't reachable from the environment it was written in. On the first real run, open the generated `docs/ufc.ics` and confirm:

- **Segments.** A typical Fight Night should show separate Prelims and Main Card entries at different times. If everything lands in "Main Card", ESPN isn't giving per-segment times for that event; check the `cardSegment` and `date` fields in the raw JSON (`https://site.api.espn.com/apis/site/v2/sports/mma/ufc/scoreboard?dates=20261101-20261130`).
- **Headliner order.** The main event should be listed first in Main Card notes. Bouts are sorted by ESPN's `matchNumber`, highest first; if it comes out reversed, flip `reverse=True` in `build_segments`.
- **DWCS.** Contender Series nights should appear (they run summers, roughly August to October). If they're missing, ESPN is filing them under another league slug: find it in the URL on ESPN's DWCS schedule page and add it, e.g. `--leagues ufc,<slug>` in the workflow.

## Local use

```bash
python ufc_calendar.py                          # fetch from ESPN, write docs/ufc.ics + state.json
python ufc_calendar.py --fixture tests/fixtures/scoreboard.json --out /tmp/ufc.ics --state /tmp/state.json
python -m unittest discover -s tests
```

Options: `--tz` (zone for times written in the notes, default `America/Los_Angeles`; calendar times are UTC and display in your own zone regardless), `--past-days` (default 30), `--future-days` (default 365), `--leagues` (default `ufc`).

## Notes

- GitHub disables scheduled workflows on repos with no activity for 60 days. The bot's own commits normally count, but if the schedule ever stops, re-enable it from the Actions tab.
- Entry IDs are stable (`ufc-<espn event id>-<segment>`), so calendar apps update entries in place rather than duplicating them. When a "card TBA" placeholder gets its first bouts it's replaced by the split entries.
- ESPN's API is unofficial and could change shape. Parsing is defensive about missing fields, and a broken fetch fails the run rather than publishing an empty calendar, so the worst case is a stale feed and a failed Actions run (GitHub emails you).
