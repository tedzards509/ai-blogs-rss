"""Generate an RSS feed of OpenAI News items tagged "Product".

OpenAI already publishes a full RSS feed at https://openai.com/news/rss.xml,
so this generator simply re-fetches it each run and re-emits only the items
whose <category> is "Product". Simple Static pattern (no cache needed): the
source feed is small and always returns its full history.
"""

import argparse
import xml.etree.ElementTree as ET

import pytz
from dateutil import parser as date_parser
from feed_generators.util.utils import (
    fetch_page,
    save_rss_feed,
    setup_feed_links,
    setup_logging,
    sort_posts_for_feed,
    stable_fallback_date,
)
from feedgen.feed import FeedGenerator

FEED_NAME = "openai_news_product"
BLOG_URL = "https://openai.com/news"
SOURCE_RSS_URL = "https://openai.com/news/rss.xml"
TARGET_CATEGORY = "product"

logger = setup_logging()


def parse_date(value: str | None, fallback_id: str = ""):
    """Parse an RFC 822 pubDate string into a timezone-aware datetime."""
    if not value:
        return stable_fallback_date(fallback_id)
    try:
        dt = date_parser.parse(value)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=pytz.UTC)
        return dt
    except (ValueError, TypeError) as exc:
        logger.warning(f"Unable to parse date {value!r} ({exc}); using fallback")
        return stable_fallback_date(fallback_id)


def parse_source_rss(xml_content: str) -> list[dict]:
    """Parse OpenAI's RSS feed and return items tagged "Product"."""
    root = ET.fromstring(xml_content)
    items = []

    for item in root.findall("./channel/item"):
        categories = [c.text.strip() for c in item.findall("category") if c.text]
        if not any(c.lower() == TARGET_CATEGORY for c in categories):
            continue

        title_elem = item.find("title")
        link_elem = item.find("link")
        if title_elem is None or not title_elem.text or link_elem is None or not link_elem.text:
            logger.warning("Skipping 'Product' item with missing title/link")
            continue

        title = title_elem.text.strip()
        link = link_elem.text.strip()
        description_elem = item.find("description")
        description = description_elem.text.strip() if description_elem is not None and description_elem.text else title
        pub_date_elem = item.find("pubDate")
        date = parse_date(pub_date_elem.text if pub_date_elem is not None else None, fallback_id=link)

        items.append(
            {
                "title": title,
                "link": link,
                "date": date,
                "category": "Product",
                "description": description,
            }
        )

    logger.info(f"Found {len(items)} 'Product' items in source feed")
    return items


def generate_rss_feed(items: list[dict]) -> FeedGenerator:
    """Generate RSS feed from OpenAI "Product" news items."""
    fg = FeedGenerator()
    fg.title("OpenAI News - Product")
    fg.description("Product announcements from OpenAI")
    fg.language("en")

    fg.author({"name": "OpenAI News"})
    fg.subtitle("Product updates from OpenAI's newsroom")
    setup_feed_links(fg, blog_url=BLOG_URL, feed_name=FEED_NAME)

    items_sorted = sort_posts_for_feed(items, date_field="date")

    for item in items_sorted:
        fe = fg.add_entry()
        fe.title(item["title"])
        fe.description(item["description"])
        fe.link(href=item["link"])
        fe.published(item["date"])
        fe.category(term=item["category"])
        fe.id(item["link"])

    logger.info("Successfully generated RSS feed")
    return fg


def main():
    """Main function to generate the OpenAI News "Product" RSS feed."""
    try:
        xml_content = fetch_page(SOURCE_RSS_URL)
        items = parse_source_rss(xml_content)

        if not items:
            logger.warning("No 'Product' items found. Please check the source feed structure.")
            return False

        feed = generate_rss_feed(items)
        save_rss_feed(feed, FEED_NAME)

        logger.info(f"Successfully generated RSS feed with {len(items)} items")
        return True

    except Exception as e:
        logger.error(f"Failed to generate RSS feed: {e!s}")
        return False


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Generate OpenAI News 'Product' RSS feed")
    parser.parse_args()
    main()
