"""Data scraper for X (Twitter) and site feeds with immediate-token detection."""

import asyncio
from typing import Any
from dotenv import load_dotenv

load_dotenv()

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
    "https://nitter.poast.org",
    "https://xcancel.com",
    "https://nitter.privacydev.com",
    "https://nitter.projectsegfau.lt",
    "https://nitter.rawbit.ninja",
    "https://nitter.cz",
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


USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:122.0) Gecko/20100101 Firefox/122.0",
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_3 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.2 Mobile/15E148 Safari/604.1",
]


def _parse_rss(url: str):
    import feedparser
    import urllib.request
    import re
    import random

    try:
        # Fetch content with a random User-Agent
        req = urllib.request.Request(
            url, 
            headers={
                "User-Agent": random.choice(USER_AGENTS),
                "Accept": "application/rss+xml, application/xml;q=0.9, */*;q=0.8",
                "Accept-Language": "en-US,en;q=0.5",
            }
        )
        with urllib.request.urlopen(req, timeout=15) as response:
            raw_data = response.read()
            
            # Decode with error handling
            try:
                text = raw_data.decode("utf-8")
            except UnicodeDecodeError:
                text = raw_data.decode("latin-1", errors="replace")

            # Aggressive Cleaning: 
            # 1. Remove common invalid XML control characters
            cleaned_text = re.sub(r"[\x00-\x08\x0B\x0C\x0E-\x1F\x7F]", "", text)
            
            # 2. Fix potentially broken Nitter RSS (e.g., & inside attributes)
            # Replace & only if it's not part of an existing entity
            cleaned_text = re.sub(r"&(?!([a-zA-Z0-9]+|#[0-9]+|#x[0-9a-fA-F]+);)", "&amp;", cleaned_text)
            
            # 3. Strip any trailing garbage that might cause "mismatched tag" or "invalid token"
            cleaned_text = cleaned_text.strip()
            if not cleaned_text.endswith(">"):
                # Try to find the last closing tag
                last_tag_idx = cleaned_text.rfind(">")
                if last_tag_idx != -1:
                    cleaned_text = cleaned_text[:last_tag_idx + 1]
            
            return feedparser.parse(cleaned_text)
    except Exception as e:
        # Fallback to direct parsing if manual fetch fails
        return feedparser.parse(url)


def _is_immediate_token_opportunity(text: str) -> bool:
    normalized = (text or "").lower()
    return any(keyword in normalized for keyword in IMMEDIATE_TOKEN_KEYWORDS)


def fetch_latest_tweets(account: str) -> list[dict[str, Any]]:
    """Fetch and normalize the latest 5 tweets from available Nitter RSS feeds with robust rotation."""
    import random
    
    # Shuffle instances for load balancing and better success rates
    instances = list(NITTER_INSTANCES)
    random.shuffle(instances)
    
    errors: list[str] = []

    for instance in instances:
        rss_url = f"{instance}/{account}/rss"
        try:
            feed = _parse_rss(rss_url)
            
            # Check for common Nitter/RSS parsing failures
            if getattr(feed, "bozo", 0):
                exc = getattr(feed, "bozo_exception", "Unknown parse error")
                # Suppress common XML/HTML mismatch noise in logs
                errors.append(f"{instance}: {str(exc)[:50]}...")
                continue

            entries = getattr(feed, "entries", [])
            if not entries:
                errors.append(f"{instance}: No entries found")
                continue

            # If we got here, we have a valid feed
            tweets: list[dict[str, Any]] = []
            for entry in entries[:5]:
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

        except Exception as e:
            errors.append(f"{instance}: Exception {type(e).__name__}")
            continue

    error_summary = " | ".join(errors)
    raise RuntimeError(f"All Nitter instances failed for @{account}. Errors: {error_summary}")


def fetch_site_feed_items(feed_url: str) -> list[dict[str, Any]]:
    """Fetch latest 5 opportunities from site RSS feeds."""
    feed = _parse_rss(feed_url)
    if getattr(feed, "bozo", 0) and not getattr(feed, "entries", None):
        # Only raise if we have no entries at all
        raise RuntimeError(f"Failed to parse site feed {feed_url}: {getattr(feed, 'bozo_exception', 'unknown parse error')}")

    items: list[dict[str, Any]] = []
    entries = getattr(feed, "entries", [])
    for entry in entries[:5]:
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
    """Every 15 minutes scan target accounts and site feeds and trigger alerts."""
    while True:
        print("[INFO] Starting scraping cycle...")
        for account in TARGET_ACCOUNTS:
            try:
                tweets = fetch_latest_tweets(account)
                for tweet in tweets:
                    await _process_item(tweet, f"@{account}")
            except Exception as exc:
                print(f"[WARN] RSS fetch failed for @{account}: {exc}")

        for feed_url in SITE_FEEDS:
            try:
                items = fetch_site_feed_items(feed_url)
                for item in items:
                    await _process_item(item, feed_url)
            except Exception as exc:
                print(f"[WARN] Site feed fetch failed for {feed_url}: {exc}")

        print("[INFO] Scraping cycle complete. Waiting 15 minutes.")
        await asyncio.sleep(15 * 60)


if __name__ == "__main__":
    asyncio.run(scouring_engine())
