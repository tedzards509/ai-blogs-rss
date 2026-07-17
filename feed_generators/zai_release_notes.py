"""Generate RSS feed for Z.ai "New Released" release notes
(https://docs.z.ai/release-notes/new-released).

Simple Static pattern (no cache needed): despite being a Next.js/Mintlify
site, the page is server-rendered, so a plain request returns every
release entry. Each entry is a `div.update` block with `id="YYYY-MM-DD"`,
a short title label, and a content section.
"""

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

FEED_NAME = "zai_release_notes"
BLOG_URL = "https://docs.z.ai/release-notes/new-released"


def parse_articles(html: str) -> list[dict]:
    soup = BeautifulSoup(html, "html.parser")
    articles = []

    for update in soup.select("div.update.update-container"):
        date_id = update.get("id", "")
        date = parse_date(date_id, fallback_id=date_id)

        title_elem = update.select_one("[data-component-part=update-description]")
        title = title_elem.get_text(strip=True) if title_elem else date_id
        if not title:
            continue

        content_elem = update.select_one("[data-component-part=update-content]")
        description = content_elem.get_text(" ", strip=True)[:500] if content_elem else title

        link = f"{BLOG_URL}#{date_id}" if date_id else BLOG_URL

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
    fg.title("Z.ai Release Notes")
    fg.description("New model and feature releases from Z.ai")
    fg.language("en")
    fg.author({"name": "Z.ai"})
    fg.subtitle("Release notes from the Z.ai docs")
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
