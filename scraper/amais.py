"""Parser for AMAIS school athletics sites (e.g. goramblers.org, Loyola Academy).

How the site works, as observed on 2026-11-17:

* A team page lists games in `.cms-bb-sport-events-result` cards.
* Filtering and paging are plain query-string parameters. The page's own
  JavaScript (`searchSportEvents<featureId>(page, size)`) just rewrites the URL
  and reloads:
    featureId       id of the schedule widget on that page (differs per page)
    periodId=11     "Current School Year" -- past games stay in the list, so
                    the event count only grows as the season goes on. The
                    page's default (periodId=6, "Last Month Onwards") drops
                    old games, which would trip the shrink check every week.
    sportsPageSize  games per page
    pageNumber      1-based page
* The pager shows a sliding window of page links ("1 2 Next"), NOT the total,
  so we walk pages until one comes back empty or has no "Next" link.
* The site prints "vs." for away games too; the card's CSS class
  (cms-bb-sport-events-home / -away) is the real home/away signal.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, time
from typing import Optional
from urllib.parse import parse_qs, urlparse

import requests
from bs4 import BeautifulSoup

PERIOD_CURRENT_SCHOOL_YEAR = 11
PAGE_SIZE = 10
MAX_PAGES = 30  # 300 games; far beyond any real season, guards against a loop
BULK_PAGE_SIZE = 100  # used once as a cross-check on the paged walk (verified to work on 2026-11-17)

USER_AGENT = (
    "basecamp-schedules/1.0 (weekly family calendar sync; "
    "+https://github.com/burkefaddis-prog/basecamp-schedules)"
)

MONTHS = {m: i for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun",
     "jul", "aug", "sep", "oct", "nov", "dec"], start=1)}


class ScrapeError(Exception):
    """Anything that means 'do not trust this result'."""


@dataclass
class Game:
    day: date
    start: Optional[time]          # None when the site gives no usable time
    opponent: str                  # "TBD" kept as-is
    home_away: Optional[str]       # "home" | "away" | None
    title: str                     # tournament / event name, may be ""
    location: str                  # venue name, may be ""
    address: str                   # street address from the map link, may be ""
    map_url: str


def _text(node) -> str:
    return re.sub(r"\s+", " ", node.get_text(" ")).strip() if node else ""


def _parse_time(raw: str) -> Optional[time]:
    m = re.search(r"(\d{1,2}):(\d{2})\s*([AaPp])\.?\s*[Mm]", raw)
    if not m:
        return None
    h, mi, ap = int(m.group(1)), int(m.group(2)), m.group(3).lower()
    if not (1 <= h <= 12 and 0 <= mi <= 59):
        return None
    if ap == "p" and h != 12:
        h += 12
    if ap == "a" and h == 12:
        h = 0
    return time(h, mi)


def _clean_opponent(raw: str) -> str:
    s = re.sub(r"^(vs\.?|at|@)\s+", "", raw.strip(), flags=re.I).strip()
    return s or "TBD"


def parse_cards(html: str) -> list[Game]:
    soup = BeautifulSoup(html, "html.parser")
    games: list[Game] = []
    for card in soup.select(".cms-bb-sport-events-result"):
        month = _text(card.select_one(".cms-bb-sport-events-month")).lower()[:3]
        dnum = _text(card.select_one(".cms-bb-sport-events-date"))
        year = _text(card.select_one(".cms-bb-sport-events-year"))
        if month not in MONTHS or not dnum.isdigit() or not year.isdigit():
            raise ScrapeError(f"Unreadable date on a game card: {month!r} {dnum!r} {year!r}")
        day = date(int(year), MONTHS[month], int(dnum))

        classes = card.get("class", [])
        if "cms-bb-sport-events-home" in classes:
            home_away = "home"
        elif "cms-bb-sport-events-away" in classes:
            home_away = "away"
        else:
            word = _text(card.select_one(".cms-bb-sport-events-homeaway")).lower()
            home_away = "home" if word.startswith("home") else "away" if word.startswith("away") else None

        time_raw = _text(card.select_one(".cms-bb-sport-events-time"))
        loc_raw = _text(card.select_one(".cms-bb-sport-events-address"))
        location = re.sub(r"^Location:\s*", "", loc_raw, flags=re.I).strip()

        map_url, address = "", ""
        a = card.select_one(".cms-bb-sport-events-map a[href]")
        if a:
            map_url = a["href"].strip()
            q = parse_qs(urlparse(map_url).query).get("query", [""])[0]
            address = re.sub(r"\s+", " ", q).strip()

        games.append(Game(
            day=day,
            start=_parse_time(time_raw),
            opponent=_clean_opponent(_text(card.select_one(".cms-bb-sport-events-opponent"))),
            home_away=home_away,
            title=_text(card.select_one(".cms-bb-sport-events-title")),
            location=location,
            address=address,
            map_url=map_url,
        ))
    return games


def has_next_page(html: str) -> bool:
    soup = BeautifulSoup(html, "html.parser")
    return soup.select_one(".site-page-item-next a") is not None


def find_feature_id(html: str) -> str:
    ids = set(re.findall(r"searchSportEvents(\d+)\s*\(", html))
    if len(ids) != 1:
        raise ScrapeError(
            f"Expected exactly one schedule widget on the page, found {sorted(ids) or 'none'}. "
            "The page layout may have changed."
        )
    return ids.pop()


class Fetcher:
    def __init__(self, session: Optional[requests.Session] = None):
        self.s = session or requests.Session()
        self.s.headers.update({
            "User-Agent": USER_AGENT,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
        })

    def get(self, url: str, params: Optional[dict] = None) -> str:
        try:
            r = self.s.get(url, params=params, timeout=30)
        except requests.RequestException as e:
            raise ScrapeError(f"Request failed: {e}") from e
        if r.status_code != 200:
            hints = {k: r.headers[k] for k in ("Server", "cf-ray", "x-amz-cf-id", "x-sucuri-id", "x-iinfo")
                     if k in r.headers}
            body = re.sub(r"\s+", " ", BeautifulSoup(r.text, "html.parser").get_text(" "))[:200]
            raise ScrapeError(f"HTTP {r.status_code} from {r.url} | headers {hints} | page says: {body!r}")
        return r.text


def scrape(url: str, fetcher: Optional[Fetcher] = None) -> list[Game]:
    f = fetcher or Fetcher()
    base = url.split("?")[0]
    feature_id = find_feature_id(f.get(base))

    def params(page: int, size: int) -> dict:
        return {"featureId": feature_id, "periodId": PERIOD_CURRENT_SCHOOL_YEAR,
                "sportsPageSize": size, "pageNumber": page}

    games: list[Game] = []
    for page in range(1, MAX_PAGES + 1):
        html = f.get(base, params(page, PAGE_SIZE))
        batch = parse_cards(html)
        games.extend(batch)
        if not batch or not has_next_page(html):
            break
    else:
        raise ScrapeError(f"Still finding pages after {MAX_PAGES}; refusing to trust the result.")

    # Cross-check: one big page must hold exactly the same games.
    bulk = parse_cards(f.get(base, params(1, BULK_PAGE_SIZE)))
    if len(bulk) != len(games):
        raise ScrapeError(
            f"Paged walk found {len(games)} games but a single large page found {len(bulk)}. "
            "Pagination may have changed."
        )
    return games
