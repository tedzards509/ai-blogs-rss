"""Generate RSS feed for DeepSeek API Change Log (https://api-docs.deepseek.com/updates).

Simple Static pattern (no cache needed): the changelog is a single
Docusaurus-rendered page listing every dated entry, so a plain request
returns the full history. Each dated section (<h2 id="date-YYYY-MM-DD">)
groups one or more <h3> subsections; each <h3> becomes one feed item, with
its content taken from the sibling elements that follow it up to the next
<h2>/<h3>.
"""

import sys

from bs4 import BeautifulSoup
from feed_generators.util.utils import (
    fetch_page,
    parse_date,
    save_rss_feed,
    setup_feed_links,
    setup_logging,
    sort_posts_for_feed,
)
from feedgen.feed import FeedGenerator

logger = setup_logging()

FEED_NAME = "deepseek_updates"
BLOG_URL = "https://api-docs.deepseek.com/updates"


def parse_articles(html: str) -> list[dict]:
    soup = BeautifulSoup(html, "html.parser")
    container = soup.select_one("div.theme-doc-markdown.markdown div.col--12")
    if not container:
        logger.warning("Could not find changelog container")
        return []

    articles = []
    current_date = None

    for elem in container.find_all(["h2", "h3", "p", "ul", "ol"], recursive=False):
        if elem.name == "h2":
            # Prefer the "date-YYYY-MM-DD" anchor id over the visible text,
            # which is padded with a zero-width space that trips up dateutil.
            heading_id = elem.get("id", "")
            date_text = heading_id.removeprefix("date-") or elem.get_text(strip=True).removeprefix("Date:").strip()
            current_date = parse_date(date_text, fallback_id=heading_id)
            continue

        if elem.name == "h3":
            title = elem.get_text(strip=True).rstrip(chr(0x200B))
            anchor_id = elem.get("id", "")
            link = f"{BLOG_URL}#{anchor_id}" if anchor_id else BLOG_URL
            articles.append(
                {
                    "title": title,
                    "link": link,
                    "date": current_date,
                    "description_parts": [],
                }
            )
            continue

        if articles:
            text = elem.get_text(" ", strip=True)
            if text:
                articles[-1]["description_parts"].append(text)

    for article in articles:
        parts = article.pop("description_parts")
        article["description"] = " ".join(parts)[:500] if parts else article["title"]

    logger.info(f"Parsed {len(articles)} articles")
    return articles


def generate_rss_feed(articles: list[dict]) -> FeedGenerator:
    fg = FeedGenerator()
    fg.title("DeepSeek API Change Log")
    fg.description("Change log and updates from the DeepSeek API docs")
    fg.language("en")
    fg.author({"name": "DeepSeek"})
    fg.subtitle("API updates and model releases from DeepSeek")
    setup_feed_links(fg, blog_url=BLOG_URL, feed_name=FEED_NAME)

    for article in sort_posts_for_feed(articles, date_field="date"):
        fe = fg.add_entry()
        fe.title(article["title"])
        fe.description(article["description"])
        fe.link(href=article["link"])
        fe.id(article["link"])
        if article.get("date"):
            fe.published(article["date"])

    logger.info(f"Generated RSS feed with {len(articles)} entries")
    return fg


def main() -> bool:
    html = fetch_page(BLOG_URL)
    articles = parse_articles(html)

    if not articles:
        logger.warning("No articles found. Check the HTML structure.")
        return False

    feed = generate_rss_feed(articles)
    save_rss_feed(feed, FEED_NAME)
    logger.info("Done!")
    return True


if __name__ == "__main__":
    sys.exit(0 if main() else 1)
