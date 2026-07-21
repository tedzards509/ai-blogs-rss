"""Generate RSS feed for Black Forest Labs Blog (https://bfl.ai/blog).

Simple Static pattern (no cache needed): every post (the featured hero
plus the grid) is server-rendered directly into the initial HTML response,
with no "load more"/pagination control on the page. No Selenium required.
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

FEED_NAME = "bfl"
BLOG_URL = "https://bfl.ai/blog"


def parse_hero_article(soup: BeautifulSoup) -> dict | None:
    """Extract the featured hero post, which sits outside the grid's
    ``<article>`` elements. Its title is the page's only ``h3.text-bf-h3``
    (the "All Posts" section heading is an ``h2`` with the same class, so
    tag name disambiguates); date/link live two ancestors up.
    """
    title_elem = soup.find("h3", class_="text-bf-h3")
    if not title_elem:
        return None

    container = title_elem.parent.parent if title_elem.parent else None
    link_a = container.find("a", href=True) if container else None
    if not link_a:
        return None

    title = title_elem.get_text(strip=True)
    link = absolute_url(link_a["href"], "https://bfl.ai")

    date_elem = container.find("span")
    date = parse_date(date_elem.get_text(strip=True), fallback_id=link) if date_elem else None
    if not date:
        date = stable_fallback_date(link)

    desc_elem = container.find("p")
    description = desc_elem.get_text(strip=True) if desc_elem else title

    img_elem = container.find("img")
    thumbnail = absolute_url(img_elem["src"], "https://bfl.ai") if img_elem and img_elem.get("src") else None

    return {"title": title, "link": link, "date": date, "description": description, "thumbnail": thumbnail}


def parse_articles(html: str) -> list[dict]:
    soup = BeautifulSoup(html, "html.parser")
    articles = []
    seen_links = set()

    hero = parse_hero_article(soup)
    if hero:
        seen_links.add(hero["link"])
        articles.append(hero)

    for art in soup.find_all("article", id=lambda x: x and x.startswith("blog-post-")):
        link_a = art.find("a", href=True)
        if not link_a:
            continue

        link = absolute_url(link_a["href"], "https://bfl.ai")
        if link in seen_links:
            continue

        title_elem = art.find("h2") or art.find("h3")
        if not title_elem:
            continue
        title = title_elem.get_text(strip=True)
        if len(title) < 3:
            continue

        seen_links.add(link)

        time_elem = art.find("time")
        date = parse_date(time_elem.get("datetime"), fallback_id=link) if time_elem else None
        if not date:
            logger.warning(f"Could not parse date for article: {title}")
            date = stable_fallback_date(link)

        desc_elem = art.find("p")
        description = desc_elem.get_text(strip=True) if desc_elem else title

        img_elem = art.find("img")
        thumbnail = absolute_url(img_elem["src"], "https://bfl.ai") if img_elem and img_elem.get("src") else None

        articles.append(
            {
                "title": title,
                "link": link,
                "date": date,
                "description": description,
                "thumbnail": thumbnail,
            }
        )

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
