"""Data scraper for X (Twitter) and site feeds with immediate-token detection."""

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

SITE_FEEDS = [
    "https://airdrops.io/feed/",
    "https://coinmarketcap.com/community/articles/rss/",
]

NITTER_INSTANCES = [
    "https://nitter.net",
    "https://nitter.poast.org",
    "https://nitter.privacydev.net",
]

IMMEDIATE_TOKEN_KEYWORDS = [
    "claim now",
    "claim live",
    "token live",
    "tge live",
    "airdrop live",
    "mint live",
    "instant reward",
    "redeem now",
]


def _parse_rss(url: str):
    import feedparser

    return feedparser.parse(url)


def _is_immediate_token_opportunity(text: str) -> bool:
    normalized = (text or "").lower()
    return any(keyword in normalized for keyword in IMMEDIATE_TOKEN_KEYWORDS)


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
            title = getattr(entry, "title", "")
            tweets.append(
                {
                    "link": getattr(entry, "link", ""),
                    "title": title,
                    "published": getattr(entry, "published", ""),
                    "source_instance": instance,
                    "source_type": "x",
                    "immediate_hint": _is_immediate_token_opportunity(title),
                }
            )
        return tweets

    raise RuntimeError(f"All Nitter instances failed for @{account}: {' | '.join(errors)}")


def fetch_site_feed_items(feed_url: str) -> list[dict[str, Any]]:
    """Fetch latest 5 opportunities from site RSS feeds."""
    feed = _parse_rss(feed_url)
    if getattr(feed, "bozo", 0):
        raise RuntimeError(f"Failed to parse site feed {feed_url}: {getattr(feed, 'bozo_exception', 'unknown parse error')}")

    items: list[dict[str, Any]] = []
    for entry in feed.entries[:5]:
        title = getattr(entry, "title", "")
        summary = getattr(entry, "summary", "")
        text_blob = f"{title} {summary}".strip()
        items.append(
            {
                "link": getattr(entry, "link", ""),
                "title": text_blob,
                "published": getattr(entry, "published", ""),
                "source_instance": feed_url,
                "source_type": "site",
                "immediate_hint": _is_immediate_token_opportunity(text_blob),
            }
        )
    return items


async def _process_item(item: dict[str, Any], account_label: str) -> None:
    raw_text = item.get("title", "")
    if not raw_text:
        return

    try:
        extracted = analyze_alpha_post(raw_text)
        score, priority = calculate_score(extracted)
    except Exception as exc:
        print(f"[WARN] Analysis failed for {account_label} item {item.get('link', '')}: {exc}")
        return

    immediate = bool(item.get("immediate_hint")) or _is_immediate_token_opportunity(extracted.get("action", ""))

    if "HIGH PRIORITY" not in priority and not immediate:
        return

    project_data = {
        "project": extracted.get("project", ""),
        "action": extracted.get("action", ""),
        "investors": extracted.get("investors", []),
        "score": score,
        "source": item.get("link", ""),
        "published": item.get("published", ""),
        "source_instance": item.get("source_instance", ""),
        "source_type": item.get("source_type", ""),
        "immediate_token": immediate,
    }

    try:
        await process_and_notify(project_data)
    except Exception as exc:
        print(f"[WARN] Notification failed for {account_label}: {exc}")


async def scouring_engine() -> None:
    """Every 15 minutes scan X + sites and trigger high-priority/immediate alerts."""
    while True:
        for account in TARGET_ACCOUNTS:
            try:
                tweets = fetch_latest_tweets(account)
            except Exception as exc:
                print(f"[WARN] RSS fetch failed for @{account}: {exc}")
                continue

            for tweet in tweets:
                await _process_item(tweet, f"@{account}")

        for feed_url in SITE_FEEDS:
            try:
                items = fetch_site_feed_items(feed_url)
            except Exception as exc:
                print(f"[WARN] Site feed fetch failed for {feed_url}: {exc}")
                continue

            for item in items:
                await _process_item(item, feed_url)

        await asyncio.sleep(15 * 60)


if __name__ == "__main__":
    asyncio.run(scouring_engine())
