"""Scrape every team in teams.yaml and write feeds/<output>.

Safety rail (the most important part of this repo): a team's file is only
written when the scrape looks trustworthy. It is left untouched, and the run
exits non-zero so the GitHub Action shows red and emails the repo owner, when:

  * the scrape raised any error (network, HTTP status, layout change), or
  * it found zero games, or
  * it found noticeably fewer games than the committed file already has.

Because the scraper asks for the whole school year, past games stay listed
and the count normally only grows. A drop of one or two games (a cancellation)
is allowed; anything beyond MAX_DROP is treated as a broken scrape.

To accept a real drop on purpose (e.g. the school deleted a tournament), run
the workflow manually with "allow_shrink" ticked, or locally:
    ALLOW_SHRINK=1 python -m scraper
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import yaml

from . import amais
from .ics import count_events, render

ROOT = Path(__file__).resolve().parent.parent
FEEDS = ROOT / "feeds"
MAX_DROP = 2              # games that may disappear between runs without failing
MAX_DROP_FRACTION = 0.10  # ...or 10% of the schedule, whichever is larger

PARSERS = {"amais": amais.scrape}


class SafetyError(Exception):
    pass


def check_safe(new_count: int, old_count: int, allow_shrink: bool) -> None:
    if new_count == 0:
        raise SafetyError("Scrape returned zero games. Refusing to empty the feed.")
    if old_count and not allow_shrink:
        allowed = max(MAX_DROP, int(old_count * MAX_DROP_FRACTION))
        if old_count - new_count > allowed:
            raise SafetyError(
                f"Scrape found {new_count} games but the committed feed has {old_count} "
                f"(more than {allowed} missing). Refusing to overwrite. If the schedule really "
                "shrank, re-run with allow_shrink."
            )


def run_team(team: dict, allow_shrink: bool) -> str:
    for key in ("label", "url", "output", "platform"):
        if not team.get(key):
            raise SafetyError(f"teams.yaml entry is missing '{key}': {team}")
    parser = PARSERS.get(team["platform"])
    if not parser:
        raise SafetyError(f"Unknown platform '{team['platform']}'. Known: {', '.join(PARSERS)}")

    games = parser(team["url"])
    out = FEEDS / team["output"]
    # read_bytes, not read_text: read_text turns \r\n into \n and every run looks "changed"
    old_text = out.read_bytes().decode("utf-8") if out.exists() else ""
    check_safe(len(games), count_events(old_text), allow_shrink)

    text = render(team["label"], team["url"], games, int(team.get("duration_minutes", 90)),
                  team_key=team["output"])
    if text == old_text:
        return f"unchanged ({len(games)} games)"
    FEEDS.mkdir(exist_ok=True)
    out.write_bytes(text.encode("utf-8"))
    return f"updated ({count_events(old_text)} -> {len(games)} games)"


def main() -> int:
    allow_shrink = os.environ.get("ALLOW_SHRINK", "").lower() in ("1", "true", "yes")
    teams = yaml.safe_load((ROOT / "teams.yaml").read_text(encoding="utf-8"))["teams"]
    failures = 0
    for team in teams:
        name = team.get("output", "?")
        try:
            print(f"OK   {name}: {run_team(team, allow_shrink)}")
        except (amais.ScrapeError, SafetyError) as e:
            failures += 1
            print(f"FAIL {name}: {e}  (left the existing file untouched)")
        except Exception as e:  # anything unexpected is also a failure, never a silent pass
            failures += 1
            print(f"FAIL {name}: unexpected {type(e).__name__}: {e}  (left the existing file untouched)")
    if failures:
        print(f"\n{failures} of {len(teams)} team(s) failed. Feeds that succeeded are still updated.")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
