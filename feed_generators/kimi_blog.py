"""Generate RSS feed for Kimi Research (https://www.kimi.com/blog/).

Simple Static pattern (no cache needed): every post card (hero + grid) is
server-rendered directly into the initial HTML response, with no "load
more"/pagination control on the page. No Selenium required.
"""

from bs4 import BeautifulSoup
from feed_generators.util.utils import (
    absolute_url,
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

FEED_NAME = "kimi"
BLOG_URL = "https://www.kimi.com/blog/"


def parse_articles(html: str) -> list[dict]:
    """Extract articles from the blog page.

    Each post (both the featured hero card and the grid cards) is a
    ``div.menu-card`` wrapping an ``<a href="/blog/...">``, an
    ``h4.card-title``, and a ``p.card-date`` (format "YYYY/MM/DD"). The
    hero card is duplicated for desktop/mobile layouts, so dedupe by link.
    """
    soup = BeautifulSoup(html, "html.parser")
    articles = []
    seen_links = set()

    for card in soup.select("div.menu-card"):
        link_a = card.find("a", href=True)
        if not link_a:
            continue

        href = link_a.get("href", "")
        if "/blog/" not in href or href.rstrip("/").endswith("/blog"):
            continue

        link = absolute_url(href, "https://www.kimi.com")
        if link in seen_links:
            continue

        title_elem = card.find("h4")
        if not title_elem:
            continue
        title = title_elem.get_text(strip=True)
        if len(title) < 3:
            continue

        seen_links.add(link)

        date_elem = card.select_one("p.card-date")
        date = parse_date(date_elem.get_text(strip=True), fallback_id=link) if date_elem else None
        if not date:
            logger.warning(f"Could not parse date for article: {title}")
            date = stable_fallback_date(link)

        articles.append(
            {
                "title": title,
                "link": link,
                "date": date,
                "description": title,
            }
        )

    logger.info(f"Parsed {len(articles)} articles")
    return articles


def generate_rss_feed(articles: list[dict]) -> FeedGenerator:
    fg = FeedGenerator()
    fg.title("Kimi Research")
    fg.description("Research articles, technical blogs, and benchmark releases from Kimi (Moonshot AI)")
    fg.language("en")
    fg.author({"name": "Kimi"})
    fg.subtitle("Research, models, and product updates from Kimi")
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
    main()
