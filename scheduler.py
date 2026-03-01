"""Autonomous Discovery & Scheduling Engine for Alpha Hunter."""

import asyncio
import logging
import os
from datetime import datetime, timedelta
from dotenv import load_dotenv

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger

# Load environment variables from .env file
load_dotenv()

from alpha_aggregator import analyze_alpha_post, calculate_score, process_and_notify, init_db, seed_initial_projects
from x_scraper import fetch_site_feed_items, TARGET_ACCOUNTS, SITE_FEEDS, is_immediate_token_opportunity

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger("AlphaScheduler")

async def fetch_tweets(usernames: list[str]) -> list[dict]:
    """Fetch latest 5 tweets for each username using Twikit with basic error handling."""
    try:
        from twikit import Client
    except ImportError:
        logger.error("The 'twikit' library is not installed. Please run 'pip install twikit'.")
        return []

    client = Client('en-US')
    
    # Credentials from environment variables
    tw_user = os.getenv("TWITTER_USERNAME")
    tw_email = os.getenv("TWITTER_EMAIL")
    tw_pass = os.getenv("TWITTER_PASSWORD")
    
    if not all([tw_user, tw_email, tw_pass]):
        logger.error("Twitter credentials (TWITTER_USERNAME, TWITTER_EMAIL, TWITTER_PASSWORD) missing in .env")
        return []

    try:
        await client.login(
            auth_info_1=tw_user,
            auth_info_2=tw_email,
            password=tw_pass
        )
        logger.info("Successfully logged into Twitter via Twikit.")
    except Exception as e:
        logger.error(f"Twikit login failed: {e}")
        return []

    all_tweets = []
    for username in usernames:
        try:
            user = await client.get_user_by_screen_name(username)
            tweets = await user.get_tweets('Tweets', count=5)
            
            for tweet in tweets:
                all_tweets.append({
                    "link": f"https://x.com/{username}/status/{tweet.id}",
                    "title": tweet.text,
                    "published": tweet.created_at,
                    "source_instance": "twitter.com",
                    "source_type": "x",
                    "immediate_hint": is_immediate_token_opportunity(tweet.text),
                })
            # Respectful delay between accounts
            await asyncio.sleep(2)
        except Exception as e:
            logger.warning(f"Failed to fetch tweets for {username} via Twikit: {e}")
            
    return all_tweets

async def run_scan(source_name: str, items_fetcher, frequency_tag: str):
    """Generic scan runner with error handling and retries."""
    logger.info(f"Starting {frequency_tag} for {source_name}...")
    try:
        items = await items_fetcher()
        for item in items:
            raw_text = item.get("title", "") or item.get("text", "")
            if not raw_text:
                continue
            
            try:
                extracted = analyze_alpha_post(raw_text)
                score, label = calculate_score(extracted)
                
                project_data = {
                    "project": extracted.get("project", ""),
                    "action": extracted.get("action", ""),
                    "investors": extracted.get("investors", []),
                    "score": score,
                    "source": item.get("link", ""),
                    "frequency": frequency_tag,
                    "immediate_token": item.get("immediate_hint", False)
                }
                
                await process_and_notify(project_data)
            except Exception as e:
                logger.error(f"Error processing item from {source_name}: {e}")
                
    except Exception as e:
        logger.error(f"Scan failed for {source_name}: {e}. Retrying in 1 hour.")
        # Schedule a retry in 1 hour
        asyncio.get_event_loop().call_later(3600, lambda: asyncio.create_task(run_scan(source_name, items_fetcher, frequency_tag)))

async def fetch_x_and_telegram():
    all_items = []
    
    # Fetch tweets using Twikit
    logger.info("Fetching tweets for target accounts via Twikit...")
    try:
        all_items.extend(await fetch_tweets(TARGET_ACCOUNTS))
    except Exception as e:
        logger.error(f"Critical failure in Twikit fetch: {e}")
            
    # Fetch site feeds as before
    for feed_url in SITE_FEEDS:
        try:
            all_items.extend(fetch_site_feed_items(feed_url))
        except Exception as e:
            logger.warning(f"Failed to fetch site feed {feed_url}: {e}")
            
    return all_items

async def fetch_defillama():
    # Placeholder for DefiLlama API/Scraper
    logger.info("Fetching new protocols from DefiLlama (Placeholder)")
    return [
        {"title": "New yield protocol 'LlamaYield' launched on Arbitrum with support from Dragonfly.", "link": "https://defillama.com/protocols"}
    ]

async def fetch_substack():
    # Placeholder for Substack Scraper
    logger.info("Fetching alpha from Substack (Placeholder)")
    return [
        {"title": "Deep dive into the 'ZeroState' modular stack. Backed by Polychain.", "link": "https://substack.com/alpha-guides"}
    ]

async def fetch_cryptorank():
    # Placeholder for CryptoRank Scraper
    logger.info("Fetching funding rounds from CryptoRank (Placeholder)")
    return [
        {"title": "Project 'CyberVault' raised $15M in Series A led by a16z.", "link": "https://cryptorank.io/funding-rounds"}
    ]

async def daily_scan():
    await run_scan("X & Telegram", fetch_x_and_telegram, "daily_scan")

async def mid_term_scan():
    await run_scan("DefiLlama", fetch_defillama, "mid_term_scan")

async def weekly_research():
    await run_scan("Substack", fetch_substack, "weekly_research")

async def monthly_alpha():
    await run_scan("CryptoRank", fetch_cryptorank, "monthly_alpha")

async def main():
    # Initialize DB and Seed Data
    init_db()
    seed_initial_projects()
    
    scheduler = AsyncIOScheduler()
    
    # 1. Daily Scan: every 24 hours
    scheduler.add_job(daily_scan, IntervalTrigger(hours=24), id='daily_scan', replace_existing=True)
    
    # 2. Mid-term Scan: every 3 days
    scheduler.add_job(mid_term_scan, IntervalTrigger(days=3), id='mid_term_scan', replace_existing=True)
    
    # 3. Weekly Research: every 7 days
    scheduler.add_job(weekly_research, IntervalTrigger(days=7), id='weekly_research', replace_existing=True)
    
    # 4. Monthly Alpha: every 30 days
    scheduler.add_job(monthly_alpha, IntervalTrigger(days=30), id='monthly_alpha', replace_existing=True)
    
    # Initial run for all
    asyncio.create_task(daily_scan())
    asyncio.create_task(mid_term_scan())
    asyncio.create_task(weekly_research())
    asyncio.create_task(monthly_alpha())
    
    scheduler.start()
    logger.info("Scheduler started.")
    
    try:
        while True:
            await asyncio.sleep(1)
    except (KeyboardInterrupt, SystemExit):
        scheduler.shutdown()

if __name__ == "__main__":
    asyncio.run(main())
