# basecamp-schedules

Turns high school sports schedule pages that don't offer a calendar feed into
`.ics` files that basecamp (or any calendar app) can subscribe to.

A GitHub Action re-scrapes every Monday and commits a feed only when a game
was actually added, moved, or changed.

## Feeds

| Team | Subscribe URL |
|---|---|
| Maddie — Basketball | `https://raw.githubusercontent.com/burkefaddis-prog/basecamp-schedules/main/feeds/maddie-basketball.ics` |
| Ryan — Sophomore Basketball | `https://raw.githubusercontent.com/burkefaddis-prog/basecamp-schedules/main/feeds/ryan-basketball.ics` |

## Adding a team

1. Add a block to `teams.yaml` (label, url, output, platform).
2. Commit it. The push itself triggers a run, and the new file appears in `feeds/`.
3. Add the raw URL in basecamp.

Only AMAIS school sites (`platform: amais`) are supported so far. A team on
another site platform needs a new parser in `scraper/`.

## When it fails

A failed run means **nothing was overwritten**. The scraper refuses to write a
feed when the scrape errors, finds zero games, or finds noticeably fewer games
than the committed file has (more than 2, or 10%, whichever is larger). GitHub
emails the repo owner when a run fails.

If a school really did remove games, go to **Actions → Update feeds → Run
workflow** and tick **allow_shrink**.

At the end of a season the school-year view stays populated, so feeds keep
working. When the new school year starts and the page empties, the run will
fail on "zero games" every week until next season's schedule is posted, or
until the team is removed from `teams.yaml`.

## Running locally

```
pip install -r requirements.txt
python -m pytest -q
python -m scraper
```
