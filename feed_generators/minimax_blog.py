"""Generate RSS feed for MiniMax Blog (https://www.minimax.io/blog).

Simple Static pattern (no cache needed): the blog only has a handful of
posts and no pagination/infinite-scroll -- confirmed via a headless
browser scroll test that the static HTML already contains every post.
Each post is an <a href="/blog/..."> wrapping a title <h3>, a date span,
and a description <article>.
"""

import sys

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

FEED_NAME = "minimax_blog"
BLOG_URL = "https://www.minimax.io/blog"


def parse_articles(html: str) -> list[dict]:
    soup = BeautifulSoup(html, "html.parser")
    articles = []
    seen_links = set()

    for desc_elem in soup.find_all("article"):
        anchor = desc_elem.find_parent("a", href=True)
        if not anchor:
            continue

        href = anchor.get("href", "")
        if not href.startswith("/blog/"):
            continue

        link = absolute_url(href, "https://www.minimax.io")
        if link in seen_links:
            continue
        seen_links.add(link)

        title_elem = anchor.find("h3")
        if not title_elem:
            continue
        title = title_elem.get_text(strip=True)
        if len(title) < 3:
            continue

        date = None
        for span in anchor.find_all("span"):
            text = span.get_text(strip=True)
            if len(text) == 10 and text.count("-") == 2:
                date = parse_date(text, fallback_id=link)
                break

        description = desc_elem.get_text(" ", strip=True)[:500] or title

        articles.append(
            {
                "title": title,
                "link": link,
                "date": date,
                "description": description,
            }
        )

    logger.info(f"Parsed {len(articles)} articles")
    return articles


def generate_rss_feed(articles: list[dict]) -> FeedGenerator:
    fg = FeedGenerator()
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
