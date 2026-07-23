"""Generate RSS feed for Black Forest Labs Blog (https://bfl.ai/blog).

Simple Static pattern (no cache needed): the blog grid paginates
client-side, but the initial HTML response embeds every post (the featured
hero plus all grid pages) as JSON inside the Next.js RSC payload
(``self.__next_f.push`` chunks), so a single request captures the full
post list — title, slug, ISO date, excerpt, and cover image. No Selenium
required.
"""

import json
import re
import sys

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

FEED_NAME = "bfl"
BLOG_URL = "https://bfl.ai/blog"
BASE_URL = "https://bfl.ai"

# Each chunk is a JS string literal: self.__next_f.push([1,"<chunk>"]).
# The JSON post data is split across many chunks.
NEXT_F_CHUNK_RE = re.compile(r'self\.__next_f\.push\(\[1,"(.*?)"\]\)', re.DOTALL)


def extract_rsc_payload(html: str) -> str:
    """Unescape and concatenate the RSC flight chunks into one payload string."""
    chunks = NEXT_F_CHUNK_RE.findall(html)
    try:
        return "".join(json.loads(f'"{chunk}"') for chunk in chunks)
    except json.JSONDecodeError as exc:
        logger.warning(f"Could not unescape RSC payload chunks: {exc}")
        return ""


def decode_json_value(payload: str, key: str) -> dict | list | None:
    """Decode the JSON value directly following ``key`` in the flight payload."""
    idx = payload.find(key)
    if idx == -1:
        logger.warning(f"Key {key} not found in RSC payload")
        return None
    try:
        value, _ = json.JSONDecoder().raw_decode(payload, idx + len(key))
        return value
    except json.JSONDecodeError as exc:
        logger.warning(f"Could not decode {key} from RSC payload: {exc}")
        return None


def post_to_article(post: dict) -> dict | None:
    slug = (post.get("slug") or {}).get("current")
    title = post.get("title")
    if not slug or not title:
        return None

    link = absolute_url(f"/blog/{slug}", BASE_URL)
    return {
        "title": title,
        "link": link,
        "date": parse_date(post.get("publishedAt"), fallback_id=link),
        "description": post.get("excerpt") or title,
        "thumbnail": (post.get("mainImage") or {}).get("url"),
    }


def parse_articles(html: str) -> list[dict]:
    payload = extract_rsc_payload(html)
    if not payload:
        return []

    raw_posts = []
    hero = decode_json_value(payload, '"firstPost":')
    if isinstance(hero, dict):
        raw_posts.append(hero)
    remaining = decode_json_value(payload, '"remainingPosts":')
    if isinstance(remaining, list):
        raw_posts.extend(remaining)

    articles = []
    seen_links = set()
    for post in raw_posts:
        article = post_to_article(post)
        if article and article["link"] not in seen_links:
            seen_links.add(article["link"])
            articles.append(article)

    logger.info(f"Parsed {len(articles)} articles")
    return articles


def generate_rss_feed(articles: list[dict]) -> FeedGenerator:
    fg = FeedGenerator()
    fg.load_extension("media")
    fg.title("Black Forest Labs Blog")
    fg.description("News, research, and product updates from Black Forest Labs")
    fg.language("en")
    fg.author({"name": "Black Forest Labs"})
    fg.subtitle("The team behind the FLUX family of models")
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
            fe.media.content([{"url": article["thumbnail"], "medium": "image"}])
            fe.media.thumbnail([{"url": article["thumbnail"]}])

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
