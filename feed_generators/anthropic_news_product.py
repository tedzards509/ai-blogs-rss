"""Generate an RSS feed of Anthropic News articles tagged "Product".

Reuses the fetching and parsing logic from anthropic_news_blog.py and
filters the result down to articles whose category is "Product". Kept as
a separate script (own FEED_NAME, own cache file) per the "multiple feeds
from one site" pattern described in AGENTS.md.
"""

import argparse

from feed_generators.util.utils import (
    deserialize_entries,
    load_cache,
    merge_entries,
    save_cache,
    save_rss_feed,
    setup_feed_links,
    setup_logging,
    sort_posts_for_feed,
)
from feedgen.feed import FeedGenerator

from anthropic_news_blog import BLOG_URL, fetch_news_content, parse_news_html

FEED_NAME = "anthropic_news_product"

logger = setup_logging()


def filter_product_articles(articles: list[dict]) -> list[dict]:
    """Keep only articles tagged "Product" (case-insensitive)."""
    return [a for a in articles if (a.get("category") or "").strip().lower() == "product"]


def generate_rss_feed(articles: list[dict]) -> FeedGenerator:
    """Generate RSS feed from Anthropic "Product" articles."""
    fg = FeedGenerator()
    fg.title("Anthropic News - Product")
    fg.description("Product announcements from Anthropic's newsroom")
    fg.language("en")

    fg.author({"name": "Anthropic News"})
    fg.logo("https://www.anthropic.com/images/icons/apple-touch-icon.png")
    fg.subtitle("Product updates from Anthropic's newsroom")
    setup_feed_links(fg, blog_url=BLOG_URL, feed_name=FEED_NAME)

    articles_sorted = sort_posts_for_feed(articles, date_field="date")

    for article in articles_sorted:
        fe = fg.add_entry()
        fe.title(article["title"])
        fe.description(article["description"])
        fe.link(href=article["link"])
        fe.published(article["date"])
        fe.category(term=article["category"])
        fe.id(article["link"])

    logger.info("Successfully generated RSS feed")
    return fg


def main(full_reset=False):
    """Main function to generate the Anthropic "Product" RSS feed.

    Args:
        full_reset: If True, fetch all articles (click "See more" up to 20 times).
                   If False, do incremental update (click 2-3 times, merge with cache).
    """
    try:
        cache = load_cache(FEED_NAME)
        cached_articles = deserialize_entries(cache.get("entries", []))

        if full_reset or not cached_articles:
            mode = "full reset" if full_reset else "no cache exists"
            logger.info(f"Running full fetch ({mode})")
            html_content = fetch_news_content(max_clicks=20)
            articles = filter_product_articles(parse_news_html(html_content))
        else:
            logger.info("Running incremental update (2 clicks only)")
            html_content = fetch_news_content(max_clicks=2)
            new_articles = filter_product_articles(parse_news_html(html_content))
            logger.info(f"Found {len(new_articles)} 'Product' articles from recent pages")
            articles = merge_entries(new_articles, cached_articles)

        if not articles:
            logger.warning("No 'Product' articles found. Please check the HTML structure.")
            return False

        save_cache(FEED_NAME, articles)

        feed = generate_rss_feed(articles)
        save_rss_feed(feed, FEED_NAME)

        logger.info(f"Successfully generated RSS feed with {len(articles)} articles")
        return True

    except Exception as e:
        logger.error(f"Failed to generate RSS feed: {e!s}")
        return False


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Generate Anthropic News 'Product' RSS feed")
    parser.add_argument("--full", action="store_true", help="Force full reset (fetch all articles)")
    args = parser.parse_args()
    main(full_reset=args.full)
