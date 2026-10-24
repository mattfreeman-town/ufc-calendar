#!/usr/bin/env python3
"""Build a subscribable UFC calendar (.ics) from ESPN's public JSON feed.

Each numbered event or Fight Night becomes up to three calendar entries
(Early Prelims, Prelims, Main Card), each listing that segment's bouts.
Dana White's Contender Series nights stay as a single entry.

A small state file remembers what was published last time so that:
  * SEQUENCE / LAST-MODIFIED only change when an entry really changes
    (the .ics is byte-for-byte stable between runs when nothing happened),
  * each entry carries a short "Updates" log of added, removed and moved
    bouts and start-time changes.

Standard library only. Python 3.11+.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from zoneinfo import ZoneInfo

ESPN_SCOREBOARD = "https://site.api.espn.com/apis/site/v2/sports/mma/{league}/scoreboard"
FIGHTCENTER_URL = "https://www.espn.com/mma/fightcenter/_/id/{id}/league/{league}"
USER_AGENT = "ufc-calendar/1.0"

SEGMENTS = ("early", "prelims", "main")
SEGMENT_LABEL = {"early": "Early Prelims", "prelims": "Prelims", "main": "Main Card", "card": "Card"}
LAST_SEGMENT_HOURS = 3
PLACEHOLDER_HOURS = 4
MAX_LOG_LINES = 8
UTC = dt.timezone.utc


# ---------------------------------------------------------------------------
# Fetching
# ---------------------------------------------------------------------------

def fetch_json(url: str, retries: int = 4) -> dict:
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
            with urllib.request.urlopen(req, timeout=30) as resp:
                return json.load(resp)
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError):
            if attempt == retries - 1:
                raise
            time.sleep(2 ** (attempt + 1))
    raise RuntimeError("unreachable")


def month_ranges(start: dt.date, end: dt.date):
    cur = dt.date(start.year, start.month, 1)
    while cur <= end:
        nxt = dt.date(cur.year + cur.month // 12, cur.month % 12 + 1, 1)
        yield cur, nxt - dt.timedelta(days=1)
        cur = nxt


def collect_events(pages: list[tuple[str, dict]]) -> list[dict]:
    """Merge scoreboard pages into one event list, tagged with their league.

    Events that only appear in the league calendar (announced, no bouts yet)
    are added as placeholders with an empty card.
    """
    events: dict[str, dict] = {}
    calendar: dict[str, dict] = {}
    for league, data in pages:
        for ev in data.get("events") or []:
            if ev.get("id") is None:
                continue
            ev = dict(ev, _league=league)
            events[str(ev["id"])] = ev
        for lg in data.get("leagues") or []:
            for entry in lg.get("calendar") or []:
                if not isinstance(entry, dict):
                    continue
                ref = (entry.get("event") or {}).get("$ref", "")
                m = re.search(r"/events/(\d+)", ref)
                if m and entry.get("startDate"):
                    calendar[m.group(1)] = {
                        "id": m.group(1),
                        "name": entry.get("label") or "UFC",
                        "date": entry["startDate"],
                        "competitions": [],
                        "_league": league,
                    }
    for eid, placeholder in calendar.items():
        events.setdefault(eid, placeholder)
    return list(events.values())


def fetch_events(leagues: list[str], start: dt.date, end: dt.date) -> list[dict]:
    pages = []
    for league in leagues:
        base = ESPN_SCOREBOARD.format(league=league)
        for a, b in month_ranges(start, end):
            pages.append((league, fetch_json(f"{base}?dates={a:%Y%m%d}-{b:%Y%m%d}&limit=200")))
    return collect_events(pages)


# ---------------------------------------------------------------------------
# Parsing ESPN's shape into something calendar-friendly
# ---------------------------------------------------------------------------

def parse_time(value) -> dt.datetime | None:
    if not value or not isinstance(value, str):
        return None
    try:
        t = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if t.tzinfo is None:
        t = t.replace(tzinfo=UTC)
    return t.astimezone(UTC)


def is_dwcs(name: str) -> bool:
    n = name.lower()
    return "contender series" in n or "dwcs" in n


def _fighter(comp: dict) -> dict:
    athlete = comp.get("athlete") or {}
    name = athlete.get("displayName") or athlete.get("fullName") or "TBA"
    record = next((r.get("summary") for r in comp.get("records") or [] if r.get("summary")), None)
    return {"name": name, "record": record, "winner": bool(comp.get("winner"))}


def parse_bout(comp: dict, index: int) -> dict:
    fighters = [_fighter(c) for c in sorted(comp.get("competitors") or [], key=lambda c: c.get("order", 99))]
    while len(fighters) < 2:
        fighters.append({"name": "TBA", "record": None, "winner": False})
    ctype = comp.get("type") or {}
    seg = comp.get("cardSegment") or {}
    return {
        "key": " vs ".join(sorted(f["name"] for f in fighters[:2])).lower(),
        "fighters": fighters[:2],
        "weight": ctype.get("text") or ctype.get("abbreviation"),
        "time": parse_time(comp.get("startDate") or comp.get("date")),
        "segment_raw": str(seg.get("description") or seg.get("name") or "").strip(),
        "order": comp.get("matchNumber") if isinstance(comp.get("matchNumber"), int) else index,
        "broadcasts": [n for b in comp.get("broadcasts") or [] for n in (b.get("names") or [])],
    }


def bout_line(bout: dict) -> str:
    parts = []
    for f in bout["fighters"]:
        s = f["name"] + (f" ({f['record']})" if f["record"] else "")
        parts.append(s)
    line = " vs ".join(parts)
    if bout["weight"]:
        line += f" · {bout['weight']}"
    winner = next((f["name"] for f in bout["fighters"] if f["winner"]), None)
    if winner:
        line += f" · W: {winner}"
    return line


def _label_kind(raw: str) -> str | None:
    raw = raw.lower()
    if "early" in raw:
        return "early"
    if "prelim" in raw:
        return "prelims"
    if "main" in raw:
        return "main"
    return None


def split_segments(bouts: list[dict]) -> dict[str, list[dict]]:
    """Group bouts into early/prelims/main.

    Uses ESPN's segment labels when they map cleanly; otherwise orders the
    raw segments by start time and assigns them from the main card backwards.
    """
    groups: dict[str, list[dict]] = {}
    for b in bouts:
        groups.setdefault(b["segment_raw"].lower(), []).append(b)

    kinds = {raw: _label_kind(raw) for raw in groups}
    labelled = [k for k in kinds.values() if k]
    if None in kinds.values() or len(set(labelled)) != len(labelled):
        far = dt.datetime.max.replace(tzinfo=UTC)
        ordered = sorted(groups, key=lambda raw: min((b["time"] or far) for b in groups[raw]))
        kinds = {}
        for pos, raw in enumerate(reversed(ordered)):
            kinds[raw] = SEGMENTS[max(len(SEGMENTS) - 1 - pos, 0)]

    out: dict[str, list[dict]] = {}
    for raw, bs in groups.items():
        out.setdefault(kinds[raw], []).extend(bs)
    return out


def _venue(ev: dict) -> str:
    comps = ev.get("competitions") or []
    venue = (comps[0].get("venue") if comps else None) or (ev.get("venues") or [{}])[0] or {}
    addr = venue.get("address") or {}
    bits = [venue.get("fullName"), addr.get("city"), addr.get("state") or addr.get("country")]
    return ", ".join(b for b in bits if b)


def _event_status(ev: dict) -> str | None:
    status = ((ev.get("status") or {}).get("type") or {}).get("name", "")
    if "CANCEL" in status:
        return "CANCELLED"
    if "POSTPONE" in status:
        return "POSTPONED"
    return None


def build_segments(ev: dict) -> list[dict]:
    """Turn one ESPN event into a list of calendar segments."""
    name = (ev.get("name") or ev.get("shortName") or "UFC").strip()
    event_time = parse_time(ev.get("date"))
    bouts = [parse_bout(c, i) for i, c in enumerate(ev.get("competitions") or [])]
    league = ev.get("_league", "ufc")
    base = {
        "event_id": str(ev["id"]),
        "event_name": name,
        "location": _venue(ev),
        "url": FIGHTCENTER_URL.format(id=ev["id"], league=league),
        "status": _event_status(ev),
        "dwcs": is_dwcs(name),
    }

    if not bouts:
        if event_time is None:
            return []
        return [dict(base, kind="card", bouts=[], start=event_time,
                     end=event_time + dt.timedelta(hours=PLACEHOLDER_HOURS), tba=True)]

    groups = {"card": bouts} if base["dwcs"] else split_segments(bouts)
    order = ["card"] if base["dwcs"] else [k for k in SEGMENTS if k in groups]
    segs = []
    for kind in order:
        bs = sorted(groups[kind], key=lambda b: b["order"], reverse=True)  # headliner first
        start = min((b["time"] for b in bs if b["time"]), default=event_time)
        if start is None:
            continue
        segs.append(dict(base, kind=kind, bouts=bs, start=start, tba=False))
    for i, seg in enumerate(segs):
        nxt = segs[i + 1]["start"] if i + 1 < len(segs) else None
        seg["end"] = nxt if nxt and nxt > seg["start"] else seg["start"] + dt.timedelta(hours=LAST_SEGMENT_HOURS)
    return segs


def summary_for(seg: dict) -> str:
    name = seg["event_name"]
    if seg["kind"] == "card":
        title = name + (" (card TBA)" if seg["tba"] else "")
    else:
        label = SEGMENT_LABEL[seg["kind"]]
        head, sep, tail = name.partition(":")
        title = f"{head.strip()} {label}: {tail.strip()}" if sep else f"{name} {label}"
    if seg["status"]:
        title = f"{seg['status']}: {title}"
    return title


# ---------------------------------------------------------------------------
# Change tracking
# ---------------------------------------------------------------------------

def _fmt_local(t: dt.datetime, tz: ZoneInfo) -> str:
    local = t.astimezone(tz)
    return local.strftime("%a %b %-d %-I:%M %p %Z")


def track_changes(event_segs: list[dict], prev: dict | None, now: dt.datetime, tz: ZoneInfo) -> dict:
    """Diff this event against last run, append to its log, return new state."""
    bouts = {b["key"]: {"line": bout_line(b), "kind": s["kind"]} for s in event_segs for b in s["bouts"]}
    starts = {s["kind"]: s["start"].isoformat() for s in event_segs}
    log = list((prev or {}).get("log", []))
    if prev:
        stamp = now.astimezone(tz).strftime("%b %-d")
        old_bouts, old_starts = prev.get("bouts", {}), prev.get("starts", {})
        for key, b in bouts.items():
            if key not in old_bouts:
                log.append(f"{stamp}: Added {b['line']} ({SEGMENT_LABEL[b['kind']]})")
            elif old_bouts[key]["kind"] != b["kind"]:
                log.append(f"{stamp}: Moved to {SEGMENT_LABEL[b['kind']]}: {b['line']}")
        for key, b in old_bouts.items():
            if key not in bouts:
                log.append(f"{stamp}: Removed {b['line']}")
        for kind, iso in starts.items():
            if kind in old_starts and old_starts[kind] != iso:
                when = _fmt_local(dt.datetime.fromisoformat(iso), tz)
                log.append(f"{stamp}: {SEGMENT_LABEL[kind]} start moved to {when}")
    return {"bouts": bouts, "starts": starts, "log": log[-MAX_LOG_LINES:],
            "last_start": max(s["start"] for s in event_segs).isoformat()}


def description_for(seg: dict, log: list[str], tz: ZoneInfo) -> str:
    lines = [f"{SEGMENT_LABEL[seg['kind']]} starts {_fmt_local(seg['start'], tz)}"]
    networks = sorted({n for b in seg["bouts"] for n in b["broadcasts"]})
    if networks:
        lines.append("Watch: " + ", ".join(networks))
    lines.append("")
    if seg["bouts"]:
        lines += [bout_line(b) for b in seg["bouts"]]
    else:
        lines.append("Fights not announced yet. This entry fills in as bouts are booked.")
    if log:
        lines += ["", "Updates:"] + log
    lines += ["", f"Full card: {seg['url']}", "Times and bouts can change; this calendar refreshes every few hours."]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# iCalendar output
# ---------------------------------------------------------------------------

def ics_escape(text: str) -> str:
    return (text.replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,")
            .replace("\r\n", "\n").replace("\n", "\\n"))


def fold(line: str) -> str:
    """Fold a content line at 75 octets without splitting UTF-8 characters."""
    out, cur, size, limit = [], "", 0, 75
    for ch in line:
        n = len(ch.encode("utf-8"))
        if size + n > limit:
            out.append(cur)
            cur, size, limit = " ", 1, 75
        cur += ch
        size += n
    out.append(cur)
    return "\r\n".join(out)


def ics_time(t: dt.datetime) -> str:
    return t.astimezone(UTC).strftime("%Y%m%dT%H%M%SZ")


def build_calendar(events: list[dict], state: dict, now: dt.datetime, tz: ZoneInfo,
                   window: tuple[dt.datetime, dt.datetime], cal_name: str = "UFC") -> tuple[str, dict]:
    prev_events = state.get("events", {})
    prev_vevents = state.get("vevents", {})
    new_state = {"events": {}, "vevents": {}}
    vevents = []

    for ev in events:
        segs = build_segments(ev)
        segs = [s for s in segs if window[0] <= s["start"] <= window[1]]
        if not segs:
            continue
        eid = segs[0]["event_id"]
        ev_state = track_changes(segs, prev_events.get(eid), now, tz)
        new_state["events"][eid] = ev_state

        for seg in segs:
            uid = f"ufc-{eid}-{seg['kind']}@ufc-calendar"
            props = {
                "DTSTART": ics_time(seg["start"]),
                "DTEND": ics_time(seg["end"]),
                "SUMMARY": ics_escape(summary_for(seg)),
                "DESCRIPTION": ics_escape(description_for(seg, ev_state["log"], tz)),
                "LOCATION": ics_escape(seg["location"]),
                "URL": seg["url"],
                "CATEGORIES": ics_escape("DWCS" if seg["dwcs"] else f"UFC,{SEGMENT_LABEL[seg['kind']]}"),
                "STATUS": "CANCELLED" if seg["status"] == "CANCELLED" else "CONFIRMED" if seg["bouts"] else "TENTATIVE",
            }
            digest = hashlib.sha256(json.dumps(props, sort_keys=True).encode()).hexdigest()
            prev = prev_vevents.get(uid)
            if prev and prev["hash"] == digest:
                meta = prev
            else:
                meta = {"hash": digest, "seq": prev["seq"] + 1 if prev else 0, "modified": ics_time(now)}
            new_state["vevents"][uid] = meta
            vevents.append((seg["start"], uid, props, meta))

    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//ufc-calendar//EN",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        f"X-WR-CALNAME:{ics_escape(cal_name)}",
        "X-WR-CALDESC:UFC events and Dana White's Contender Series\\, split by card segment.",
        "REFRESH-INTERVAL;VALUE=DURATION:PT6H",
        "X-PUBLISHED-TTL:PT6H",
    ]
    for _, uid, props, meta in sorted(vevents, key=lambda v: (v[0], v[1])):
        lines += ["BEGIN:VEVENT", f"UID:{uid}", f"DTSTAMP:{meta['modified']}",
                  f"LAST-MODIFIED:{meta['modified']}", f"SEQUENCE:{meta['seq']}"]
        lines += [f"{k}:{v}" for k, v in props.items() if v]
        lines += ["TRANSP:TRANSPARENT", "END:VEVENT"]
    lines.append("END:VCALENDAR")
    return "\r\n".join(fold(l) for l in lines) + "\r\n", new_state


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--out", default="docs/ufc.ics")
    p.add_argument("--state", default="state.json")
    p.add_argument("--leagues", default="ufc", help="comma-separated ESPN MMA league slugs")
    p.add_argument("--past-days", type=int, default=30)
    p.add_argument("--future-days", type=int, default=365)
    p.add_argument("--tz", default="America/Los_Angeles", help="zone for times shown in descriptions")
    p.add_argument("--fixture", help="read a saved scoreboard JSON instead of calling ESPN")
    args = p.parse_args(argv)

    now = dt.datetime.now(UTC).replace(microsecond=0)
    tz = ZoneInfo(args.tz)
    window = (now - dt.timedelta(days=args.past_days), now + dt.timedelta(days=args.future_days))
    state_path, out_path = Path(args.state), Path(args.out)
    state = json.loads(state_path.read_text()) if state_path.exists() else {}

    if args.fixture:
        events = collect_events([("ufc", json.loads(Path(args.fixture).read_text()))])
    else:
        leagues = [l.strip() for l in args.leagues.split(",") if l.strip()]
        events = fetch_events(leagues, window[0].date(), window[1].date())

    ics, new_state = build_calendar(events, state, now, tz, window)

    # Don't wipe subscribers' calendars because of a bad fetch.
    had_future = any(dt.datetime.fromisoformat(e["last_start"]) > now for e in state.get("events", {}).values())
    has_future = any(dt.datetime.fromisoformat(e["last_start"]) > now for e in new_state["events"].values())
    if had_future and not has_future:
        print("No upcoming events in feed but last run had some; refusing to publish.", file=sys.stderr)
        return 1

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_bytes(ics.encode("utf-8"))
    state_path.write_text(json.dumps(new_state, indent=2, sort_keys=True) + "\n")
    print(f"Wrote {len(new_state['vevents'])} entries for {len(new_state['events'])} events to {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
