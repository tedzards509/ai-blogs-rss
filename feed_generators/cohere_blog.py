"""Generate RSS feed for the Cohere Blog (https://cohere.com/blog).

Cache-Backed Incremental Fetch pattern: the "Browse all" section
(`<section id="blog-browse-all">`) is real server-rendered pagination --
`/blog?page=2`, `/blog?page=3`, ... return genuinely different articles
(confirmed by diffing page 1 vs page 2), not a client-side-only filter over
one big payload. Each card already has title, link, and date inline, so no
per-post page fetch is needed.
"""

import sys

import requests
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

FEED_NAME = "cohere"
BLOG_URL = "https://cohere.com/blog"
MAX_PAGES = 30  # Safety limit for pagination


def _extract_thumbnail(title_a) -> str | None:
    """Find the card's cover image.

    The title anchor (`a.flex.flex-1.flex-col`) only wraps the text content;
    the `<img>` lives in a sibling "image" div under the same outer card
    container (`title_a.parent.parent`), both for grid cards and the
    featured/hero cards -- e.g.:

        <div class="h-full ... flex-col ...">      <!-- outer card container -->
          <div class="... aspect-video">            <!-- image div -->
            <a href="..."><img src="..." srcset="..."/></a>
          </div>
          <div class="flex h-full flex-col ...">     <!-- title_a.parent -->
            <a class="flex flex-1 flex-col" href="...">...</a>  <!-- title_a -->
          </div>
        </div>
    """
    container = title_a.parent.parent if title_a.parent else None
    if not container:
        return None
    img = container.find("img")
    if not img or not img.get("src"):
        return None
    return absolute_url(img["src"], "https://cohere.com")


def _parse_card(title_a) -> dict | None:
    """Build an article dict from an `a.flex.flex-1.flex-col` card.

    Its first `<p>` is the title, and the first `<p>` inside the trailing
    `<span>` is the date (e.g. "Jul 07, 2026").
    """
    if not title_a.get("href"):
        return None

    title_p = title_a.find("p")
    if not title_p:
        return None
    title = title_p.get_text(strip=True)
    if not title:
        return None

    link = absolute_url(title_a["href"], "https://cohere.com")

    date_p = title_a.find("span")
    date_text = date_p.find("p").get_text(strip=True) if date_p and date_p.find("p") else None
    date = parse_date(date_text, fallback_id=link)

    thumbnail = _extract_thumbnail(title_a)

    return {"title": title, "link": link, "date": date, "description": title, "thumbnail": thumbnail}


def parse_featured_articles(html: str) -> list[dict]:
    """Extract the 4 featured articles pinned above the "Browse all" grid.

    They live in a two-column row: one large hero card (in a div matched by
    `lg:max-w-[880px]`) plus 3 smaller cards in the sibling column. Both use
    the same `a.flex.flex-1.flex-col` card markup as the paginated grid, but
    aren't part of `ul.grid`/`section#blog-browse-all`, so `parse_articles`
    never sees them and page 1 of pagination silently duplicates them later.
    """
    soup = BeautifulSoup(html, "html.parser")
    hero = soup.select_one('div[class*="lg:max-w-[880px]"]')
    if not hero:
        return []
    row = hero.parent

    articles = []
    for title_a in row.select("a.flex.flex-1.flex-col"):
        article = _parse_card(title_a)
        if article:
            articles.append(article)

    logger.info(f"Parsed {len(articles)} featured articles")
    return articles


def parse_articles(html: str) -> list[dict]:
    """Extract articles from the "Browse all" grid on a `/blog?page=N` response.

    Each card is an `<li>` in `ul.grid` wrapping an `a.flex.flex-1.flex-col`
    that links to the post.
    """
    soup = BeautifulSoup(html, "html.parser")
    section = soup.select_one("section#blog-browse-all")
    if not section:
        return []
    grid = section.select_one("ul.grid")
    if not grid:
        return []

    articles = []
    for li in grid.find_all("li", recursive=False):
        title_a = li.select_one("a.flex.flex-1.flex-col")
        if not title_a:
            continue
        article = _parse_card(title_a)
        if article:
            articles.append(article)

    logger.info(f"Parsed {len(articles)} articles")
    return articles


def fetch_all_posts(cursor: CacheCursor, max_pages: int | None = MAX_PAGES) -> list[dict]:
    """Fetch posts page-by-page until a page turns up nothing new (or
    something already cached), an empty/missing grid is hit, or max_pages
    is reached.

    Args:
        max_pages: Safety cap on page fetches. None means no cap (used for
            `--full`, to walk the whole "Browse all" listing).

    Returns cursor.new_entries: posts from this run not already cached.
    """
    page_num = 1
    while max_pages is None or page_num <= max_pages:
        url = BLOG_URL if page_num == 1 else f"{BLOG_URL}?page={page_num}"

        try:
            html = fetch_page(url)
        except requests.exceptions.RequestException as e:
            logger.info(f"Error fetching page {page_num}, stopping pagination: {e}")
            break

        page_articles = parse_articles(html)
        if page_num == 1:
            featured = parse_featured_articles(html)
            seen_links = {a["link"] for a in page_articles}
            page_articles = [a for a in featured if a["link"] not in seen_links] + page_articles

        if not page_articles:
            logger.info(f"No articles found on page {page_num}, stopping pagination")
            break

        logger.info(f"Page {page_num}: found {len(page_articles)} articles")
        if not cursor.ingest(page_articles):
            logger.info("No new articles (or hit cached article), stopping pagination")
            break

        page_num += 1

    logger.info(f"Total new posts fetched: {len(cursor.new_entries)}")
    return cursor.new_entries


def build_feed(posts: list[dict]) -> FeedGenerator:
    fg = FeedGenerator()
    fg.load_extension("media")
    fg.title("Cohere Blog")
    fg.description("Latest news, research, and product updates from Cohere")
    fg.language("en")
    fg.author({"name": "Cohere"})
    setup_feed_links(fg, blog_url=BLOG_URL, feed_name=FEED_NAME)

    for post in sort_posts_for_feed(posts, date_field="date"):
        fe = fg.add_entry()
        fe.title(post["title"])
        fe.link(href=post["link"])
        fe.id(post["link"])
        fe.published(post["date"])
        fe.description(post["description"])
        if post.get("thumbnail"):
            fe.media.content([{"url": post["thumbnail"], "medium": "image"}])
            fe.media.thumbnail([{"url": post["thumbnail"]}])

    return fg


def main(full_reset=False) -> bool:
    """Main function to generate RSS feed.

    Args:
        full_reset: If True, ignore cache and fetch every page of the
            "Browse all" listing (no max_pages cap). If False, fetch until
            a page turns up nothing new, then merge with cache.
    """
    cache = load_cache(FEED_NAME)
    cached_posts = deserialize_entries(cache.get("entries", []))

    mode = "full reset" if full_reset else "no cache exists" if not cached_posts else "incremental update"
    logger.info(f"Running {mode}")

    cursor = CacheCursor([] if full_reset else cached_posts)
    new_posts = fetch_all_posts(cursor, max_pages=None if full_reset else MAX_PAGES)

    if full_reset or not cached_posts:
        posts = new_posts
    else:
        logger.info(f"Found {len(new_posts)} new posts")
        posts = merge_entries(new_posts, cached_posts)

    if not posts:
        logger.warning("No posts found")
        return False

    save_cache(FEED_NAME, posts)

    feed = build_feed(posts)
    save_rss_feed(feed, FEED_NAME)
    logger.info(f"Successfully generated RSS feed with {len(posts)} posts")
    return True


if __name__ == "__main__":
    sys.exit(0 if main(full_reset=parse_full_reset_flag("Generate Cohere Blog RSS feed")) else 1)
