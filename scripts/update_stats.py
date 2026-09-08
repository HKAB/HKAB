#!/usr/bin/env python3

import base64
import json
import os
import re
from collections import Counter
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import requests


GITHUB_USER = "HKAB"
README = Path("README.md")
STATS_DIR = Path("stats")

GITHUB_TOKEN = os.environ["GITHUB_TOKEN"]
WAKATIME_API_KEY = os.environ.get("WAKATIME_API_KEY")


def bar(value, maximum, width=24):
    if maximum <= 0:
        return "░" * width

    n = round(value / maximum * width)
    n = max(0, min(width, n))

    return "█" * n + "░" * (width - n)


def human_duration(seconds):
    seconds = int(seconds or 0)
    hours, rem = divmod(seconds, 3600)
    minutes = rem // 60

    if hours:
        return f"{hours}h {minutes:02d}m"

    return f"{minutes}m"


def github_graphql(query, variables):
    response = requests.post(
        "https://api.github.com/graphql",
        headers={
            "Authorization": f"Bearer {GITHUB_TOKEN}",
            "Accept": "application/vnd.github+json",
        },
        json={
            "query": query,
            "variables": variables,
        },
        timeout=30,
    )

    response.raise_for_status()
    payload = response.json()

    if payload.get("errors"):
        raise RuntimeError(json.dumps(payload["errors"], indent=2))

    return payload["data"]


def fetch_github():
    today = date.today()
    start = today - timedelta(days=364)

    query = """
    query($login: String!, $from: DateTime!, $to: DateTime!) {
      user(login: $login) {
        login

        followers {
          totalCount
        }

        repositories(
          first: 100
          ownerAffiliations: OWNER
          privacy: PUBLIC
          orderBy: {field: UPDATED_AT, direction: DESC}
        ) {
          totalCount

          nodes {
            name
            stargazerCount
            forkCount

            primaryLanguage {
              name
            }
          }
        }

        contributionsCollection(from: $from, to: $to) {
          totalCommitContributions
          totalIssueContributions
          totalPullRequestContributions
          totalPullRequestReviewContributions
          restrictedContributionsCount

          contributionCalendar {
            totalContributions

            weeks {
              contributionDays {
                date
                contributionCount
                weekday
              }
            }
          }
        }
      }
    }
    """

    data = github_graphql(
        query,
        {
            "login": GITHUB_USER,
            "from": f"{start.isoformat()}T00:00:00Z",
            "to": f"{today.isoformat()}T23:59:59Z",
        },
    )

    user = data["user"]
    contributions = user["contributionsCollection"]

    repos = user["repositories"]["nodes"]

    stars = sum(repo["stargazerCount"] for repo in repos)
    forks = sum(repo["forkCount"] for repo in repos)

    languages = Counter(
        repo["primaryLanguage"]["name"]
        for repo in repos
        if repo.get("primaryLanguage")
    )

    days = []

    for week in contributions["contributionCalendar"]["weeks"]:
        for day in week["contributionDays"]:
            days.append(
                {
                    "date": day["date"],
                    "count": day["contributionCount"],
                    "weekday": day["weekday"],
                }
            )

    days.sort(key=lambda x: x["date"])

    result = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "repos": user["repositories"]["totalCount"],
        "followers": user["followers"]["totalCount"],
        "stars": stars,
        "forks": forks,
        "commits_365d": contributions["totalCommitContributions"],
        "issues_365d": contributions["totalIssueContributions"],
        "prs_365d": contributions["totalPullRequestContributions"],
        "reviews_365d": contributions[
            "totalPullRequestReviewContributions"
        ],
        "contributions_365d": contributions[
            "contributionCalendar"
        ]["totalContributions"],
        "restricted_contributions": contributions[
            "restrictedContributionsCount"
        ],
        "languages_by_repo": dict(languages.most_common()),
        "days": days,
    }

    return result


def fetch_wakatime():
    if not WAKATIME_API_KEY:
        return None

    token = base64.b64encode(
        WAKATIME_API_KEY.encode()
    ).decode()

    response = requests.get(
        "https://api.wakatime.com/api/v1/users/current/stats/last_7_days",
        headers={
            "Authorization": f"Basic {token}",
        },
        timeout=30,
    )

    response.raise_for_status()

    data = response.json()["data"]

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "total_seconds": data.get("total_seconds", 0),
        "daily_average": data.get("daily_average", 0),
        "languages": [
            {
                "name": lang["name"],
                "seconds": lang["total_seconds"],
                "percent": lang["percent"],
                "text": lang["text"],
            }
            for lang in data.get("languages", [])
        ],
        "editors": data.get("editors", []),
        "projects": data.get("projects", []),
        "best_day": data.get("best_day"),
    }


def streaks(days):
    counts = {
        datetime.strptime(d["date"], "%Y-%m-%d").date(): d["count"]
        for d in days
    }

    sorted_dates = sorted(counts)

    longest = 0
    running = 0

    for d in sorted_dates:
        if counts[d] > 0:
            running += 1
            longest = max(longest, running)
        else:
            running = 0

    current = 0

    d = max(sorted_dates)

    # Today's contribution count may still be zero early in the day.
    # If so, begin checking from yesterday.
    if counts.get(d, 0) == 0:
        d -= timedelta(days=1)

    while counts.get(d, 0) > 0:
        current += 1
        d -= timedelta(days=1)

    active = sum(1 for d in days if d["count"] > 0)

    return current, longest, active


def sparkline(values):
    chars = "▁▂▃▄▅▆▇█"

    if not values:
        return ""

    high = max(values)

    if high == 0:
        return "▁" * len(values)

    return "".join(
        chars[
            min(
                len(chars) - 1,
                int(value / high * (len(chars) - 1)),
            )
        ]
        for value in values
    )


def render_github(g):
    current, longest, active = streaks(g["days"])

    last_30 = g["days"][-30:]
    last_7 = g["days"][-7:]

    max_7 = max(
        (day["count"] for day in last_7),
        default=1,
    )

    lines = []

    lines.append("```text")
    lines.append(
        f"snapshot     {date.today().isoformat()}    "
        f"github/{GITHUB_USER}"
    )
    lines.append(
        "────────────────────────────────────────────────────────────"
    )
    lines.append("")
    lines.append(
        f"repositories  {g['repos']:<6} "
        f"stars          {g['stars']}"
    )
    lines.append(
        f"followers     {g['followers']:<6} "
        f"forks           {g['forks']}"
    )
    lines.append("")
    lines.append("365 DAY ACTIVITY")
    lines.append("")

    signal = sparkline(
        [d["count"] for d in g["days"]]
    )

    # split the year signal so GitHub mobile doesn't become absurdly wide
    width = 60

    for i in range(0, len(signal), width):
        lines.append("  " + signal[i:i + width])

    lines.append("")
    lines.append(
        f"contributions  {g['contributions_365d']}"
    )
    lines.append(
        f"commits        {g['commits_365d']}"
    )
    lines.append(
        f"pull requests  {g['prs_365d']}"
    )
    lines.append(
        f"issues         {g['issues_365d']}"
    )
    lines.append(
        f"reviews        {g['reviews_365d']}"
    )
    lines.append("")
    lines.append(
        f"active days    {active:>3} / {len(g['days'])}"
    )
    lines.append(
        f"current streak {current:>3} days"
    )
    lines.append(
        f"longest streak {longest:>3} days"
    )

    lines.append("")
    lines.append("LAST 7 DAYS")
    lines.append("")

    for day in last_7:
        dt = datetime.strptime(
            day["date"], "%Y-%m-%d"
        )

        label = dt.strftime("%a")
        count = day["count"]

        lines.append(
            f"{label}  {bar(count, max_7, 22)}  {count:>3}"
        )

    if g["languages_by_repo"]:
        lines.append("")
        lines.append("PRIMARY LANGUAGES / PUBLIC REPOSITORIES")
        lines.append("")

        langs = list(
            g["languages_by_repo"].items()
        )[:8]

        maximum = max(count for _, count in langs)

        for language, count in langs:
            lines.append(
                f"{language[:12]:<12} "
                f"{bar(count, maximum, 20)}  "
                f"{count:>3}"
            )

    lines.append("```")

    return "\n".join(lines)


def render_waka(w):
    if not w:
        return (
            "```text\n"
            "WakaTime telemetry unavailable\n"
            "```"
        )

    lines = []

    lines.append("```text")
    lines.append("WAKATIME / LAST 7 DAYS")
    lines.append(
        "────────────────────────────────────────────────────────────"
    )
    lines.append("")
    lines.append(
        f"total          {human_duration(w['total_seconds'])}"
    )
    lines.append(
        f"daily average  {human_duration(w['daily_average'])}"
    )

    languages = w["languages"][:8]

    if languages:
        lines.append("")
        lines.append("LANGUAGES")
        lines.append("")

        max_seconds = max(
            x["seconds"] for x in languages
        )

        for lang in languages:
            lines.append(
                f"{lang['name'][:12]:<12} "
                f"{bar(lang['seconds'], max_seconds, 22)} "
                f"{lang['percent']:>5.1f}%  "
                f"{human_duration(lang['seconds'])}"
            )

    projects = w.get("projects", [])[:5]

    if projects:
        lines.append("")
        lines.append("PROJECTS")
        lines.append("")

        max_seconds = max(
            x.get("total_seconds", 0)
            for x in projects
        )

        for project in projects:
            lines.append(
                f"{project['name'][:18]:<18} "
                f"{bar(project.get('total_seconds', 0), max_seconds, 18)} "
                f"{human_duration(project.get('total_seconds', 0))}"
            )

    lines.append("```")

    return "\n".join(lines)


def replace_section(text, name, content):
    start = f"<!--START_SECTION:{name}-->"
    end = f"<!--END_SECTION:{name}-->"

    pattern = (
        re.escape(start)
        + r".*?"
        + re.escape(end)
    )

    replacement = (
        f"{start}\n"
        f"{content}\n"
        f"{end}"
    )

    result, count = re.subn(
        pattern,
        replacement,
        text,
        flags=re.DOTALL,
    )

    if count != 1:
        raise RuntimeError(
            f"Could not uniquely locate section: {name}"
        )

    return result


def main():
    STATS_DIR.mkdir(exist_ok=True)

    github = fetch_github()

    try:
        waka = fetch_wakatime()
    except Exception as exc:
        print(f"WakaTime fetch failed: {exc}")
        waka = None

    Path("stats/github.json").write_text(
        json.dumps(
            github,
            indent=2,
            ensure_ascii=False,
        )
        + "\n"
    )

    if waka:
        Path("stats/wakatime.json").write_text(
            json.dumps(
                waka,
                indent=2,
                ensure_ascii=False,
            )
            + "\n"
        )

    readme = README.read_text()

    readme = replace_section(
        readme,
        "github",
        render_github(github),
    )

    readme = replace_section(
        readme,
        "waka",
        render_waka(waka),
    )

    README.write_text(readme)


if __name__ == "__main__":
    main()
