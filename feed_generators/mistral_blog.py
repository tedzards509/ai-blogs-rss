"""Generate RSS feed for Mistral AI News (https://mistral.ai/news).

Simple Static pattern (no cache needed): the page's numbered pagination is
purely client-side JS filtering over data already embedded in the initial
HTML response -- ?page=N is a no-op on the server, and a single plain
request returns every article the site has. No Selenium required.
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
    stable_fallback_date,
)
from feedgen.feed import FeedGenerator

logger = setup_logging()

FEED_NAME = "mistral"
BLOG_URL = "https://mistral.ai/news/"


def parse_articles(html: str) -> list[dict]:
    """Extract articles from the news page.

    Each card is an <article> element wrapping an <a href="/news/..."> link.
    The hero card uses <h2 class="text-h4 ...">; grid cards use
    <h2 class="text-h5 ...">. Category comes from a
    span[data-category-slug], date from the first <p> in the <footer>.
    """
    soup = BeautifulSoup(html, "html.parser")
    articles = []
    seen_links = set()

    for article_elem in soup.find_all("article"):
        link_a = article_elem.find("a", href=True)
        if not link_a:
            continue

        href = link_a.get("href", "")
        if not href.startswith("/news/") or href.rstrip("/") == "/news":
            continue

        link = absolute_url(href, "https://mistral.ai")
        if link in seen_links:
            continue

        title_elem = article_elem.find("h1") or article_elem.find("h2")
        if not title_elem:
            continue
        title = title_elem.get_text(strip=True)
        if len(title) < 3:
            continue

        seen_links.add(link)

        category = "News"
        cat_span = article_elem.select_one("span[data-category-slug]")
        if cat_span:
            cat_text = cat_span.get_text(strip=True)
            if cat_text:
                category = cat_text

        description = title
        desc_elem = article_elem.select_one("p.text-body-base") or article_elem.select_one("p.text-body-large")
        if desc_elem:
            desc_text = desc_elem.get_text(strip=True)
            if desc_text:
                description = desc_text[:300]

        date = None
        footer = article_elem.find("footer")
        if footer:
            date_p = footer.select_one("p.text-body-small")
            if date_p:
                date = parse_date(date_p.get_text(strip=True), fallback_id=link)
        if not date:
            logger.warning(f"Could not parse date for article: {title}")
            date = stable_fallback_date(link)

        articles.append(
            {
                "title": title,
                "link": link,
                "date": date,
                "category": category,
                "description": description,
            }
        )

    logger.info(f"Parsed {len(articles)} articles")
    return articles


def generate_rss_feed(articles: list[dict]) -> FeedGenerator:
    fg = FeedGenerator()
    fg.title("Mistral AI News")
    fg.description("Latest news and updates from Mistral AI")
    fg.language("en")
    fg.author({"name": "Mistral AI"})
    fg.subtitle("News, research, and product updates from Mistral AI")
    setup_feed_links(fg, blog_url=BLOG_URL, feed_name=FEED_NAME)

    for article in sort_posts_for_feed(articles, date_field="date"):
        fe = fg.add_entry()
        fe.title(article["title"])
        fe.description(article["description"])
        fe.link(href=article["link"])
        fe.id(article["link"])
        fe.category(term=article["category"])
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
