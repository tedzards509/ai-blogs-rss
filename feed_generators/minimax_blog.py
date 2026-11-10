"""Generate RSS feed for MiniMax Blog (https://www.minimax.io/blog).

Simple Static pattern (no cache needed): every post is present in the initial
HTML -- there is no pagination or infinite-scroll button. Each post is an
``<a class="BlogList_card...">`` wrapping an ``<article>`` with an ``<h2>``
title, a ``<time datetime="...">`` and a Next.js ``/_next/image`` cover.

The cards themselves no longer carry an excerpt, so each entry's description
comes from the article page's ``<meta name="description">`` instead.
"""

import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from urllib.parse import parse_qs, urlparse

import requests
from bs4 import BeautifulSoup
from feed_generators.util.utils import (
    absolute_url,
    fetch_page,
    parse_date,
    save_rss_feed,
    setup_feed_links,
    setup_logging,
    sort_posts_for_feed,
)
from feedgen.feed import FeedGenerator

logger = setup_logging()

FEED_NAME = "minimax"
BLOG_URL = "https://www.minimax.io/blog"
BASE_URL = "https://www.minimax.io"
CARD_SELECTOR = 'a[class*="BlogList_card"]'
MAX_DESCRIPTION_WORKERS = 4


def extract_thumbnail(card) -> str | None:
    """Decode a card's Next.js image proxy URL back to the original CDN link.

    Next.js renders covers as ``/_next/image?url=<encoded>&w=...&q=75``, so the
    real image URL is the proxy's ``url`` query param.
    """
    img = card.find("img")
    if not img:
        return None
    src = (img.get("src") or "").strip()
    if not src or src.startswith("data:"):
        return None
    if "/_next/image" in src:
        original = parse_qs(urlparse(src).query).get("url", [None])[0]
        return absolute_url(original, BASE_URL) if original else None
    return absolute_url(src, BASE_URL)


def extract_date(card, link: str) -> datetime | None:
    """Read the card's ``<time>`` element, falling back to a stable pseudo-date."""
    time_elem = card.find("time")
    if not time_elem:
        return None
    return parse_date(time_elem.get("datetime") or time_elem.get_text(strip=True), fallback_id=link)


def parse_articles(html: str) -> list[dict]:
    soup = BeautifulSoup(html, "html.parser")
    articles = []
    seen_links = set()

    for card in soup.select(CARD_SELECTOR):
        link = absolute_url(card.get("href", ""), BASE_URL)
        if not urlparse(link).path.startswith("/blog/") or link in seen_links:
            continue
        seen_links.add(link)

        title_elem = card.find(["h2", "h3"])
        if not title_elem:
            continue
        title = title_elem.get_text(strip=True)
        if len(title) < 3:
            continue

        articles.append(
            {
                "title": title,
                "link": link,
                "date": extract_date(card, link),
                "description": None,
                "thumbnail": extract_thumbnail(card),
            }
        )

    logger.info(f"Parsed {len(articles)} articles")
    return articles


def fetch_description(link: str) -> str | None:
    """Read a post's excerpt from its own page (cards have no excerpt text)."""
    try:
        soup = BeautifulSoup(fetch_page(link), "html.parser")
    except requests.RequestException as exc:
        logger.warning(f"Could not fetch description for {link}: {exc}")
        return None
    meta = soup.find("meta", attrs={"name": "description"})
    description = (meta.get("content") or "").strip() if meta else ""
    return description or None


def add_descriptions(articles: list[dict]) -> None:
    """Fill in descriptions in parallel, falling back to the title."""
    if not articles:
        return
    with ThreadPoolExecutor(max_workers=MAX_DESCRIPTION_WORKERS) as executor:
        descriptions = list(executor.map(fetch_description, [article["link"] for article in articles]))
    for article, description in zip(articles, descriptions, strict=True):
        article["description"] = description or article["title"]


def generate_rss_feed(articles: list[dict]) -> FeedGenerator:
    fg = FeedGenerator()
    fg.load_extension("media")
    fg.title("MiniMax Blog")
    fg.description("Latest news and research from MiniMax")
    fg.language("en")
    fg.author({"name": "MiniMax"})
    fg.subtitle("News, research, and product updates from MiniMax")
    setup_feed_links(fg, blog_url=BLOG_URL, feed_name=FEED_NAME)

    for article in sort_posts_for_feed(articles, date_field="date"):
        fe = fg.add_entry()
        fe.title(article["title"])
        fe.description(article["description"])
        fe.link(href=article["link"])
        fe.id(article["link"])
        if article.get("date"):
            fe.published(article["date"])
        if article.get("thumbnail"):
            fe.media.content([{"url": article["thumbnail"], "medium": "image"}])
            fe.media.thumbnail([{"url": article["thumbnail"]}])

    logger.info(f"Generated RSS feed with {len(articles)} entries")
    return fg


def main() -> bool:
    html = fetch_page(BLOG_URL)
    articles = parse_articles(html)

    if not articles:
        logger.warning("No articles found. Check the HTML structure.")
        return False

    add_descriptions(articles)
    feed = generate_rss_feed(articles)
    save_rss_feed(feed, FEED_NAME)
    logger.info("Done!")
    return True


if __name__ == "__main__":
    sys.exit(0 if main() else 1)
