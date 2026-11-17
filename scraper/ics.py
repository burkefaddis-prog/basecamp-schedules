"""Render games as an .ics file that basecamp's parser reads correctly.

Basecamp's parser (src/utils/icalParser.ts) has quirks this file is shaped
around:

* It ignores TZID, so every timed event is written as explicit UTC
  ("...Z"). America/Chicago -> UTC goes through zoneinfo, which knows the
  DST rules, so a game in March gets -5h and one in January gets -6h.
* It decides home/away by looking for the words "home"/"away" in the
  SUMMARY, so the summary always carries "(Home)" or "(Away)" when known.
* It only unescapes "\\," and "\\n", so text is kept free of semicolons.

Output is deterministic (no generation timestamp, stable ordering), so the
GitHub Action only commits when a game actually changed.
"""

from __future__ import annotations

import hashlib
import re
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from .amais import Game

LOCAL_TZ = ZoneInfo("America/Chicago")
UID_DOMAIN = "basecamp-schedules"


def _slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")


def _escape(s: str) -> str:
    s = s.replace(";", ",")  # basecamp doesn't unescape \; -- avoid it entirely
    return s.replace("\\", "\\\\").replace(",", "\\,").replace("\n", "\\n")


def _fold(line: str) -> str:
    """RFC 5545 line folding at 75 octets without splitting a UTF-8 character."""
    out, cur, limit = [], b"", 75
    for ch in line:
        b = ch.encode("utf-8")
        if len(cur) + len(b) > limit:
            out.append(cur.decode("utf-8"))
            cur, limit = b"", 74  # continuation lines start with a space
        cur += b
    out.append(cur.decode("utf-8"))
    return "\r\n ".join(out)


def to_utc(day: date, t) -> datetime:
    local = datetime(day.year, day.month, day.day, t.hour, t.minute, tzinfo=LOCAL_TZ)
    return local.astimezone(timezone.utc)


def _fmt_utc(dt: datetime) -> str:
    return dt.strftime("%Y%m%dT%H%M%SZ")


def summary_for(label: str, g: Game) -> str:
    if g.opponent.upper() == "TBD":
        matchup = "opponent TBD"
    elif g.home_away == "away":
        matchup = f"at {g.opponent}"
    else:
        matchup = f"vs. {g.opponent}"
    side = {"home": " (Home)", "away": " (Away)"}.get(g.home_away or "", "")
    s = f"{label}: {matchup}{side}"
    if g.title:
        s += f" · {g.title}"
    return s


def location_for(g: Game) -> str:
    if g.location and g.address and g.address.lower() not in g.location.lower():
        return f"{g.location}, {g.address}"
    return g.location or g.address


def assign_uids(team_key: str, games: list[Game]) -> list[str]:
    """UID = hash(team, date, opponent). Stable across re-scrapes.

    team_key is the feed's output file name, not its label, so renaming a
    team's label in teams.yaml doesn't change every game's ID.

    Two games on the same day against the same opponent (e.g. two "TBD"
    tournament slots) would collide, so the start time is added only for
    those, then a counter as a last resort.
    """
    def h(*parts) -> str:
        return hashlib.sha1("|".join(parts).encode()).hexdigest()[:20]

    team = _slug(team_key)
    base = [h(team, g.day.isoformat(), _slug(g.opponent)) for g in games]
    uids = []
    seen: dict[str, int] = {}
    for g, b in zip(games, base):
        if base.count(b) > 1:
            b = h(team, g.day.isoformat(), _slug(g.opponent), g.start.isoformat() if g.start else "allday")
        n = seen.get(b, 0)
        seen[b] = n + 1
        uids.append(f"{b}{'-' + str(n) if n else ''}@{UID_DOMAIN}")
    return uids


def render(label: str, source_url: str, games: list[Game], duration_minutes: int = 90,
           team_key: str = "") -> str:
    uids = assign_uids(team_key or label, games)
    rows = sorted(zip(games, uids), key=lambda p: (p[0].day, p[0].start or datetime.min.time(), p[1]))

    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        f"PRODID:-//{UID_DOMAIN}//{_slug(team_key or label)}//EN",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        f"X-WR-CALNAME:{_escape(label)}",
    ]
    for g, uid in rows:
        if g.start:
            start = to_utc(g.day, g.start)
            dt = [f"DTSTART:{_fmt_utc(start)}",
                  f"DTEND:{_fmt_utc(start + timedelta(minutes=duration_minutes))}"]
            stamp = _fmt_utc(start)
        else:
            dt = [f"DTSTART;VALUE=DATE:{g.day:%Y%m%d}",
                  f"DTEND;VALUE=DATE:{g.day + timedelta(days=1):%Y%m%d}"]
            stamp = f"{g.day:%Y%m%d}T000000Z"

        desc = []
        if g.title:
            desc.append(g.title)
        if g.home_away:
            desc.append(g.home_away.capitalize())
        if not g.start:
            desc.append("Start time not posted")
        if g.map_url:
            desc.append(f"Map: {g.map_url.replace(' ', '+')}")
        desc.append(f"Schedule: {source_url}")

        lines += ["BEGIN:VEVENT", f"UID:{uid}", f"DTSTAMP:{stamp}", *dt,
                  f"SUMMARY:{_escape(summary_for(label, g))}"]
        loc = location_for(g)
        if loc:
            lines.append(f"LOCATION:{_escape(loc)}")
        lines += [f"DESCRIPTION:{_escape(chr(10).join(desc))}", "END:VEVENT"]
    lines.append("END:VCALENDAR")
    return "\r\n".join(_fold(l) for l in lines) + "\r\n"


def count_events(ics_text: str) -> int:
    return ics_text.count("BEGIN:VEVENT")
