import copy
import datetime as dt
import json
import sys
import unittest
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import ufc_calendar as uc  # noqa: E402

FIXTURE = json.loads((ROOT / "tests" / "fixtures" / "scoreboard.json").read_text())
TZ = ZoneInfo("America/Los_Angeles")
NOW = dt.datetime(2026, 10, 24, 12, 0, tzinfo=dt.timezone.utc)
WINDOW = (NOW - dt.timedelta(days=30), NOW + dt.timedelta(days=365))


def build(data, state=None, now=NOW):
    return uc.build_calendar(uc.collect_events([("ufc", data)]), state or {}, now, TZ, WINDOW)


def unfold(ics):
    return ics.replace("\r\n ", "")


def vevents(ics):
    return [block.split("END:VEVENT")[0] for block in unfold(ics).split("BEGIN:VEVENT")[1:]]


def prop(block, name):
    for line in block.split("\r\n"):
        if line.startswith(name + ":"):
            return line[len(name) + 1:]
    return None


class SegmentTests(unittest.TestCase):
    def setUp(self):
        self.ics, self.state = build(FIXTURE)
        self.by_uid = {prop(v, "UID"): v for v in vevents(self.ics)}

    def test_numbered_event_splits_into_three(self):
        for kind in ("early", "prelims", "main"):
            self.assertIn(f"ufc-600100-{kind}@ufc-calendar", self.by_uid)
        main = self.by_uid["ufc-600100-main@ufc-calendar"]
        self.assertEqual(prop(main, "SUMMARY"), "UFC 330 Main Card: Alpha vs. Bravo")
        self.assertEqual(prop(main, "DTSTART"), "20261115T030000Z")
        self.assertEqual(prop(main, "DTEND"), "20261115T060000Z")

    def test_segment_ends_when_next_begins(self):
        early = self.by_uid["ufc-600100-early@ufc-calendar"]
        self.assertEqual(prop(early, "DTEND"), "20261115T010000Z")

    def test_headliner_listed_first(self):
        desc = prop(self.by_uid["ufc-600100-main@ufc-calendar"], "DESCRIPTION")
        self.assertLess(desc.index("Alpha (10-2) vs Bravo"), desc.index("Main One"))
        self.assertIn("Watch: ESPN+", desc)

    def test_unlabelled_segments_split_by_time(self):
        self.assertIn("ufc-600200-prelims@ufc-calendar", self.by_uid)
        self.assertIn("ufc-600200-main@ufc-calendar", self.by_uid)
        self.assertEqual(prop(self.by_uid["ufc-600200-prelims@ufc-calendar"], "SUMMARY"),
                         "UFC Fight Night Prelims: Charlie vs. Delta")

    def test_dwcs_is_single_entry(self):
        dwcs = [u for u in self.by_uid if u.startswith("ufc-600300-")]
        self.assertEqual(dwcs, ["ufc-600300-card@ufc-calendar"])
        self.assertEqual(prop(self.by_uid[dwcs[0]], "CATEGORIES"), "DWCS")

    def test_announced_event_without_bouts_is_placeholder(self):
        tba = self.by_uid["ufc-600400-card@ufc-calendar"]
        self.assertEqual(prop(tba, "SUMMARY"), "UFC 331 (card TBA)")
        self.assertEqual(prop(tba, "STATUS"), "TENTATIVE")

    def test_lines_folded_to_75_octets(self):
        for line in self.ics.split("\r\n"):
            self.assertLessEqual(len(line.encode()), 75)


class UpdateTests(unittest.TestCase):
    def test_rerun_without_changes_is_byte_identical(self):
        ics1, state1 = build(FIXTURE)
        ics2, state2 = build(FIXTURE, state1, NOW + dt.timedelta(hours=3))
        self.assertEqual(ics1, ics2)
        self.assertEqual(state1, state2)

    def test_added_and_removed_bouts_bump_sequence_and_log(self):
        _, state = build(FIXTURE)
        later = NOW + dt.timedelta(days=2)
        data = copy.deepcopy(FIXTURE)
        comps = data["events"][0]["competitions"]
        comps.pop(4)  # Main One vs Main Two falls off
        new = copy.deepcopy(comps[-1])
        new["matchNumber"] = 7
        new["competitors"][0]["athlete"]["displayName"] = "Late Add"
        new["competitors"][1]["athlete"]["displayName"] = "Short Notice"
        comps.append(new)

        ics, _ = build(data, state, later)
        by_uid = {prop(v, "UID"): v for v in vevents(ics)}
        main = by_uid["ufc-600100-main@ufc-calendar"]
        self.assertEqual(prop(main, "SEQUENCE"), "1")
        self.assertEqual(prop(main, "LAST-MODIFIED"), "20261026T120000Z")
        desc = prop(main, "DESCRIPTION")
        self.assertIn("Oct 26: Added Late Add (10-2) vs Short Notice", desc)
        self.assertIn("Oct 26: Removed Main One", desc)
        # Other events are untouched.
        self.assertEqual(prop(by_uid["ufc-600200-main@ufc-calendar"], "SEQUENCE"), "0")

    def test_start_time_change_logged(self):
        _, state = build(FIXTURE)
        data = copy.deepcopy(FIXTURE)
        for c in data["events"][2]["competitions"]:
            c["date"] = c["startDate"] = "2026-11-11T01:00Z"
        ics, _ = build(data, state, NOW + dt.timedelta(days=1))
        dwcs = {prop(v, "UID"): v for v in vevents(ics)}["ufc-600300-card@ufc-calendar"]
        self.assertIn("Card start moved to Tue Nov 10 5:00 PM PST", prop(dwcs, "DESCRIPTION"))

    def test_cancelled_event(self):
        data = copy.deepcopy(FIXTURE)
        data["events"][1]["status"] = {"type": {"name": "STATUS_CANCELED"}}
        ics, _ = build(data)
        fn = {prop(v, "UID"): v for v in vevents(ics)}["ufc-600200-main@ufc-calendar"]
        self.assertEqual(prop(fn, "STATUS"), "CANCELLED")
        self.assertTrue(prop(fn, "SUMMARY").startswith("CANCELLED: "))


class HelperTests(unittest.TestCase):
    def test_escape(self):
        self.assertEqual(uc.ics_escape("a,b;c\\d\ne"), "a\\,b\\;c\\\\d\\ne")

    def test_fold_keeps_multibyte_chars_whole(self):
        folded = uc.fold("DESCRIPTION:" + "é" * 80)
        for line in folded.split("\r\n"):
            self.assertLessEqual(len(line.encode()), 75)
        self.assertEqual(folded.replace("\r\n ", ""), "DESCRIPTION:" + "é" * 80)

    def test_month_ranges_cross_year(self):
        ranges = list(uc.month_ranges(dt.date(2026, 11, 20), dt.date(2027, 1, 3)))
        self.assertEqual([a.month for a, _ in ranges], [11, 12, 1])
        self.assertEqual(ranges[1][1], dt.date(2026, 12, 31))


if __name__ == "__main__":
    unittest.main()
