"""Regenerate tests/fixtures/scoreboard.json (a trimmed ESPN-shaped sample)."""
import json
from pathlib import Path


def bout(n, a, b, seg, time, weight, ra="10-2", rb="9-3", segname=None):
    return {
        "id": f"40{n}", "date": time, "startDate": time, "matchNumber": n,
        "type": {"id": "1", "abbreviation": weight[:3], "text": weight},
        "cardSegment": {"id": seg, "name": segname or seg, **({"description": seg} if not segname else {})},
        "broadcasts": [{"market": "national", "names": ["ESPN+"]}],
        "venue": {"fullName": "T-Mobile Arena", "address": {"city": "Las Vegas", "state": "NV"}},
        "competitors": [
            {"order": 1, "winner": False, "athlete": {"displayName": a}, "records": [{"summary": ra}]},
            {"order": 2, "winner": False, "athlete": {"displayName": b}, "records": [{"summary": rb}]},
        ],
    }


numbered = {
    "id": "600100", "name": "UFC 330: Alpha vs. Bravo", "date": "2026-11-15T00:00Z",
    "status": {"type": {"name": "STATUS_SCHEDULED"}},
    "competitions": [
        bout(1, "Early One", "Early Two", "Early Prelims", "2026-11-15T00:00Z", "Flyweight"),
        bout(2, "Early Three", "Early Four", "Early Prelims", "2026-11-15T00:00Z", "Bantamweight"),
        bout(3, "Prelim One", "Prelim Two", "Prelims", "2026-11-15T01:00Z", "Lightweight"),
        bout(4, "Prelim Three", "Prelim Four", "Prelims", "2026-11-15T01:00Z", "Welterweight"),
        bout(5, "Main One", "Main Two", "Main Card", "2026-11-15T03:00Z", "Middleweight"),
        bout(6, "Alpha", "Bravo", "Main Card", "2026-11-15T03:00Z", "Heavyweight"),
    ],
}

# Segment names that don't map cleanly; split by start time instead.
fight_night = {
    "id": "600200", "name": "UFC Fight Night: Charlie vs. Delta", "date": "2026-11-22T21:00Z",
    "competitions": [
        bout(1, "FN One", "FN Two", "p1", "2026-11-22T21:00Z", "Featherweight", segname="prelims1"),
        bout(2, "Charlie", "Delta", "m", "2026-11-23T00:00Z", "Lightweight", segname="main"),
    ],
}

dwcs = {
    "id": "600300", "name": "Dana White's Contender Series: Week 2", "date": "2026-11-11T00:00Z",
    "competitions": [
        bout(1, "Hopeful One", "Hopeful Two", "Main Card", "2026-11-11T00:00Z", "Welterweight"),
        bout(2, "Hopeful Three", "Hopeful Four", "Main Card", "2026-11-11T00:00Z", "Lightweight"),
    ],
}

data = {
    "leagues": [{"slug": "ufc", "calendar": [
        {"label": "UFC 331", "startDate": "2026-12-06T02:00Z", "endDate": "2026-12-06T07:00Z",
         "event": {"$ref": "http://sports.core.api.espn.com/v2/sports/mma/leagues/ufc/events/600400?lang=en"}},
        {"label": "UFC 330: Alpha vs. Bravo", "startDate": "2026-11-15T00:00Z",
         "event": {"$ref": "http://sports.core.api.espn.com/v2/sports/mma/leagues/ufc/events/600100"}},
    ]}],
    "events": [numbered, fight_night, dwcs],
}

Path(__file__).with_name("fixtures").joinpath("scoreboard.json").write_text(json.dumps(data, indent=2) + "\n")
