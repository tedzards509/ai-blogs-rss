import sys

from bs4 import BeautifulSoup
from feed_generators.util.utils import (
    CacheCursor,
    absolute_url,
    deserialize_entries,
    fetch_page,
    load_cache,
    merge_entries,
    parse_date,
    parse_full_reset_flag,
    save_cache,
    save_rss_feed,
    setup_feed_links,
    setup_logging,
    sort_posts_for_feed,
)
from feedgen.feed import FeedGenerator

logger = setup_logging()

FEED_NAME = "artificialanalysis"
BLOG_URL = "https://artificialanalysis.ai/articles"
MAX_PAGES = 30  # Safety limit for pagination


def normalize_link(href: str) -> str:
    return absolute_url(href, "https://artificialanalysis.ai")


def parse_articles_from_html(html_content: str) -> list[dict]:
    """Parse article cards from the articles listing page.

    Cards are plain `<a href="/articles/<slug>">` elements containing an
    `<img>`, an `<h3>` title, and a `<p>` publish date -- fully server
    rendered, no JS needed.
    """
    soup = BeautifulSoup(html_content, "lxml")
    articles = []

    for anchor in soup.find_all("a", href=True):
        href = anchor["href"]
        if not href.startswith("/articles/") or href == "/articles":
            continue

        heading = anchor.find("h3")
        if not heading:
            continue
        title = heading.get_text(" ", strip=True)
        if not title:
            continue

        link = normalize_link(href)

        date_el = anchor.find("p")
        date_text = date_el.get_text(" ", strip=True) if date_el else None
        date = parse_date(date_text, fallback_id=link)

        img = anchor.find("img")
        thumbnail = normalize_link(img["src"]) if img and img.get("src") else None

        articles.append(
            {
                "title": title,
                "link": link,
                "date": date,
                "description": title,
                "thumbnail": thumbnail,
            }
        )

    logger.info(f"Parsed {len(articles)} articles from HTML")
    return articles


def fetch_all_articles(cursor: CacheCursor, max_pages: int = MAX_PAGES) -> list[dict]:
    """Fetch articles by iterating through `?page=N` until a page turns up
    nothing new (or something already cached), or max_pages is hit.

    Out-of-range page numbers clamp to the last page (same articles repeat)
    rather than 404ing, so the cursor's "no new entries" check is what
    actually stops the loop at the end of the archive.
    """
    for page_num in range(1, max_pages + 1):
        url = BLOG_URL if page_num == 1 else f"{BLOG_URL}?page={page_num}"

        try:
            html_content = fetch_page(url)
        except Exception as e:
            logger.info(f"Error fetching page {page_num}, stopping pagination: {e}")
            break

        page_articles = parse_articles_from_html(html_content)

        if not page_articles:
            logger.info(f"No articles found on page {page_num}, stopping pagination")
            break

        logger.info(f"Page {page_num}: found {len(page_articles)} articles")
        if not cursor.ingest(page_articles):
            logger.info("No new articles (or hit cached article), stopping pagination")
            break

    logger.info(f"Total new articles fetched: {len(cursor.new_entries)}")
    return cursor.new_entries


def build_feed(articles: list[dict]) -> FeedGenerator:
    fg = FeedGenerator()
    fg.load_extension("media")
    fg.title("Articles | Artificial Analysis")
    fg.description("Independent AI model benchmarks and analysis from Artificial Analysis.")
    fg.language("en")
    setup_feed_links(fg, blog_url=BLOG_URL, feed_name=FEED_NAME)

    articles_sorted = sort_posts_for_feed(articles, date_field="date")

    for article in articles_sorted:
        entry = fg.add_entry()
        entry.title(article["title"])
        entry.link(href=article["link"])
        entry.id(article["link"])
        entry.published(article["date"])
        entry.description(article["description"])
        if article.get("thumbnail"):
            entry.media.content([{"url": article["thumbnail"], "medium": "image"}])
            entry.media.thumbnail([{"url": article["thumbnail"]}])

    return fg


def main(full_reset=False):
    cache = load_cache(FEED_NAME)
    cached_articles = deserialize_entries(cache.get("entries", []))

    mode = "full reset" if full_reset else "no cache exists" if not cached_articles else "incremental update"
    logger.info(f"Running {mode}")
    cursor = CacheCursor([] if full_reset else cached_articles)
    new_articles = fetch_all_articles(cursor, max_pages=MAX_PAGES)

    if full_reset or not cached_articles:
        articles = new_articles
    else:
        logger.info(f"Found {len(new_articles)} new articles")
        articles = merge_entries(new_articles, cached_articles)

    if not articles:
        logger.warning("No articles found")
        return False

    save_cache(FEED_NAME, articles)

    feed = build_feed(articles)
    save_rss_feed(feed, FEED_NAME)
    logger.info(f"Successfully generated RSS feed with {len(articles)} articles")
    return True


if __name__ == "__main__":
    sys.exit(0 if main(full_reset=parse_full_reset_flag("Generate Artificial Analysis Articles RSS feed")) else 1)
