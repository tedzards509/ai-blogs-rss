"""Generate RSS feed for the Qwen Research blog (https://qwen.ai/research/).

Simple Static pattern (no cache needed): the page itself is a heavily
obfuscated client-rendered SPA with anti-scraping protection, but its
"Research Index" section (the "Filtered" list, which duplicates and
supersedes "Latest Advancements") is populated from a plain JSON API that
returns every article in one response, with no auth/referer required and no
"Load more" pagination to drive:

    https://qwen.ai/api/v2/article/retrieval?type=qwen_ai&language=en-US

Each article's ``path`` field is used to build its reader-facing URL,
``https://qwen.ai/blog?id=<path>`` -- the API response also embeds a
canonical qwenlm.github.io/blog/<path>/ URL, but that one 404s, so it's
not used here.
"""

import json
import sys

from bs4 import BeautifulSoup
from feed_generators.util.utils import (
    fetch_page,
    parse_date,
    save_rss_feed,
    setup_feed_links,
    setup_logging,
    sort_posts_for_feed,
    stable_fallback_date,
)
from feedgen.feed import FeedGenerator

logger = setup_logging()

FEED_NAME = "qwen"
BLOG_URL = "https://qwen.ai/research/"
API_URL = "https://qwen.ai/api/v2/article/retrieval?type=qwen_ai&language=en-US"


def parse_articles(payload: str) -> list[dict]:
    data = json.loads(payload)
    articles = []

    for art in data.get("data", {}).get("articles", []):
        title = art.get("title")
        extra = art.get("extra", {})
        path = art.get("path")
        if not title or not path:
            continue

        link = f"https://qwen.ai/blog?id={path}"

        date = parse_date(extra.get("date"), fallback_id=link) if extra.get("date") else None
        if not date:
            date = stable_fallback_date(link)

        description = BeautifulSoup(extra.get("introduction", ""), "html.parser").get_text(separator=" ", strip=True)

        articles.append(
            {
                "title": title,
                "link": link,
                "date": date,
                "description": description or title,
                "thumbnail": extra.get("cover_small") or None,
            }
        )

    logger.info(f"Parsed {len(articles)} articles")
    return articles


def generate_rss_feed(articles: list[dict]) -> FeedGenerator:
    fg = FeedGenerator()
    fg.load_extension("media")
    fg.title("Qwen Research")
    fg.description("Research, releases, and open-source updates from the Qwen team")
    fg.language("en")
    fg.author({"name": "Qwen Team"})
    fg.subtitle("Alibaba's Qwen model family")
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
            fe.media.thumbnail([{"url": article["thumbnail"]}])

    logger.info(f"Generated RSS feed with {len(articles)} entries")
    return fg


def main() -> bool:
    payload = fetch_page(API_URL)
    articles = parse_articles(payload)

    if not articles:
        logger.warning("No articles found. Check the API response structure.")
        return False

    feed = generate_rss_feed(articles)
    save_rss_feed(feed, FEED_NAME)
    logger.info("Done!")
    return True


if __name__ == "__main__":
    sys.exit(0 if main() else 1)
