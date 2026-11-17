from datetime import date, time
from pathlib import Path

import pytest

from scraper import amais
from scraper.__main__ import SafetyError, check_safe
from scraper.ics import count_events, render, to_utc

FIX = Path(__file__).parent / "fixtures"
URL = "https://www.goramblers.org/basketball---girls---varsity"
LABEL = "Maddie (Louise) — Basketball"


class FakeFetcher:
    """Serves fixture pages the way goramblers.org does."""

    def __init__(self, bulk="all.html"):
        self.bulk = bulk
        self.calls = []

    def get(self, url, params=None):
        self.calls.append(params)
        if not params:
            name = "page1.html"
        elif params["sportsPageSize"] == amais.BULK_PAGE_SIZE:
            name = self.bulk
        else:
            name = {1: "page1.html", 2: "page2.html"}.get(params["pageNumber"], "empty.html")
        return (FIX / name).read_text()


def games():
    return amais.scrape(URL, FakeFetcher())


def test_walks_every_page_and_asks_for_whole_school_year():
    f = FakeFetcher()
    g = amais.scrape(URL, f)
    assert len(g) == 4
    paged = [c for c in f.calls if c and c["sportsPageSize"] == amais.PAGE_SIZE]
    assert [c["pageNumber"] for c in paged] == [1, 2]
    assert all(c["periodId"] == 11 and c["featureId"] == "266" for c in paged)


def test_cross_check_mismatch_fails():
    with pytest.raises(amais.ScrapeError):
        amais.scrape(URL, FakeFetcher(bulk="page1.html"))


def test_fields():
    fenwick, phillips, washington, tbd = games()
    assert fenwick.day == date(2026, 11, 17) and fenwick.start == time(18, 30)
    assert fenwick.home_away == "home" and fenwick.opponent == "Fenwick"
    assert fenwick.title == "New Trier Thanksgiving Tournament"
    # site prints "vs." for away games; the CSS class is the truth
    assert phillips.home_away == "away" and phillips.opponent == "Phillips"
    assert phillips.address == "244 E Pershing Rd Chicago 60653"
    assert washington.title == "" and washington.map_url == "" and washington.location == "Metamora High School"
    assert tbd.opponent == "TBD" and tbd.title == "Exam Jam"


def test_utc_conversion_handles_dst():
    # Chicago is UTC-6 in winter (CST) and UTC-5 in summer (CDT).
    assert to_utc(date(2026, 11, 17), time(18, 30)).strftime("%Y%m%dT%H%M") == "20261118T0030"
    assert to_utc(date(2027, 3, 13), time(18, 30)).strftime("%H%M") == "0030"  # day before DST
    assert to_utc(date(2027, 3, 15), time(18, 30)).strftime("%H%M") == "2330"  # day after DST


def test_ics_is_what_basecamp_expects():
    text = render(LABEL, URL, games())
    assert count_events(text) == 4
    unfolded = text.replace("\r\n ", "")
    assert "DTSTART:20261118T003000Z" in unfolded       # 6:30 PM CST
    assert "TZID" not in unfolded                       # basecamp ignores it
    assert "SUMMARY:Maddie (Louise) — Basketball: vs. Fenwick (Home) · New Trier Thanksgiving Tournament" in unfolded
    assert "SUMMARY:Maddie (Louise) — Basketball: at Phillips (Away)" in unfolded
    assert "opponent TBD (Away) · Exam Jam" in unfolded
    assert "LOCATION:Phillips High School\\, 244 E Pershing Rd Chicago 60653" in unfolded
    assert all(len(l.encode()) <= 75 for l in text.split("\r\n"))


def test_output_is_deterministic_and_uids_stable():
    a, b = render(LABEL, URL, games()), render(LABEL, URL, list(reversed(games())))
    assert a == b
    uids = [l for l in a.split("\r\n") if l.startswith("UID:")]
    assert len(set(uids)) == 4


def test_same_day_tbd_slots_get_distinct_uids():
    g = games()[3]
    twin = amais.Game(**{**g.__dict__, "start": time(9, 0)})
    text = render(LABEL, URL, [g, twin])
    assert len({l for l in text.split("\r\n") if l.startswith("UID:")}) == 2


def test_missing_time_becomes_all_day():
    g = amais.Game(**{**games()[0].__dict__, "start": None})
    text = render(LABEL, URL, [g])
    assert "DTSTART;VALUE=DATE:20261117" in text and "DTEND;VALUE=DATE:20261118" in text


@pytest.mark.parametrize("new,old,ok", [
    (32, 0, True),     # first run
    (33, 32, True),    # game added
    (30, 32, True),    # two cancellations
    (29, 32, True),    # three gone: 10% of 32 rounds to 3, allowed
    (28, 32, False),   # four gone at once -> suspicious
    (0, 0, False),     # empty, even on first run
    (10, 32, False),
])
def test_safety_rail(new, old, ok):
    if ok:
        check_safe(new, old, allow_shrink=False)
    else:
        with pytest.raises(SafetyError):
            check_safe(new, old, allow_shrink=False)


def test_allow_shrink_still_refuses_zero():
    check_safe(10, 32, allow_shrink=True)
    with pytest.raises(SafetyError):
        check_safe(0, 32, allow_shrink=True)
