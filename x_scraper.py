"""Data scraper module for X (Twitter) via RSS/Nitter feeds."""

import asyncio
from typing import Any

from alpha_aggregator import analyze_alpha_post, calculate_score, process_and_notify

TARGET_ACCOUNTS = [
    "zachxbt",
    "Airdrop_Advise",
    "olivier_levy",
    "BanklessHQ",
    "milesjennings",
]

NITTER_INSTANCES = [
    "https://nitter.net",
    "https://nitter.poast.org",
    "https://nitter.privacydev.net",
]


def _parse_rss(url: str):
    import feedparser

    return feedparser.parse(url)


def fetch_latest_tweets(account: str) -> list[dict[str, Any]]:
    """Fetch and normalize the latest 5 tweets from available Nitter RSS feeds."""
    errors: list[str] = []

    for instance in NITTER_INSTANCES:
        rss_url = f"{instance}/{account}/rss"
        feed = _parse_rss(rss_url)

        if getattr(feed, "bozo", 0):
            errors.append(f"{instance}: {getattr(feed, 'bozo_exception', 'unknown parse error')}")
            continue

        if not getattr(feed, "entries", None):
            errors.append(f"{instance}: empty feed entries")
            continue

        tweets: list[dict[str, Any]] = []
        for entry in feed.entries[:5]:
            tweets.append(
                {
                    "link": getattr(entry, "link", ""),
                    "title": getattr(entry, "title", ""),
                    "published": getattr(entry, "published", ""),
                    "source_instance": instance,
                }
            )
        return tweets

    raise RuntimeError(f"All Nitter instances failed for @{account}: {' | '.join(errors)}")


async def scouring_engine() -> None:
    """Every 15 minutes scan target accounts and trigger high-priority alerts."""
    while True:
        for account in TARGET_ACCOUNTS:
            try:
                tweets = fetch_latest_tweets(account)
            except Exception as exc:
                print(f"[WARN] RSS fetch failed for @{account}: {exc}")
                continue

            for tweet in tweets:
                raw_text = tweet.get("title", "")
                if not raw_text:
                    continue

                try:
                    extracted = analyze_alpha_post(raw_text)
                    score, priority = calculate_score(extracted)
                except Exception as exc:
                    print(f"[WARN] Analysis failed for @{account} tweet {tweet.get('link', '')}: {exc}")
                    continue

                if "HIGH PRIORITY" not in priority:
                    continue

                project_data = {
                    "project": extracted.get("project", ""),
                    "action": extracted.get("action", ""),
                    "investors": extracted.get("investors", []),
                    "score": score,
                    "source": tweet.get("link", ""),
                    "published": tweet.get("published", ""),
                    "source_instance": tweet.get("source_instance", ""),
                }

                try:
                    await process_and_notify(project_data)
                except Exception as exc:
                    print(f"[WARN] Notification failed for @{account}: {exc}")

        await asyncio.sleep(15 * 60)


if __name__ == "__main__":
    asyncio.run(scouring_engine())
