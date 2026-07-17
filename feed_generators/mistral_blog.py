"""Generate RSS feed for Mistral AI News (https://mistral.ai/news).

Selenium-driven numbered pagination. Unlike "Load more" SPAs that append content,
Mistral replaces the article grid on each page navigation, so we parse after
each click before advancing to the next page.
"""

import argparse
import time
from datetime import datetime

import pytz
from bs4 import BeautifulSoup
from feedgen.feed import FeedGenerator
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

from utils import (
    deserialize_entries,
    load_cache,
    merge_entries,
    save_cache,
    save_rss_feed,
    setup_feed_links,
    setup_logging,
    setup_selenium_driver,
    sort_posts_for_feed,
    stable_fallback_date,
)

logger = setup_logging()

FEED_NAME = "mistral"
BLOG_URL = "https://mistral.ai/news"
MAX_PAGES_FULL = 6
MAX_PAGES_INCREMENTAL = 1


def parse_page_articles(html: str) -> list[dict]:
    """Extract articles from a single page. Returns a deduped list per page.

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

        link = f"https://mistral.ai{href}"
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
                date_text = date_p.get_text(strip=True)
                for fmt in ("%b %d, %Y", "%B %d, %Y"):
                    try:
                        date = datetime.strptime(date_text, fmt).replace(tzinfo=pytz.UTC)
                        break
                    except ValueError:
                        continue
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

    logger.info(f"Parsed {len(articles)} articles from page")
    return articles


def fetch_all_articles(max_pages: int = MAX_PAGES_FULL) -> list[dict]:
    """Fetch articles across numbered pages using Selenium."""
    driver = None
    all_articles: list[dict] = []
    seen_links: set[str] = set()

    try:
        logger.info(f"Fetching articles from {BLOG_URL} (max_pages={max_pages})")
        driver = setup_selenium_driver()
        driver.get(BLOG_URL)
        time.sleep(5)

        try:
            WebDriverWait(driver, 15).until(EC.presence_of_element_located((By.CSS_SELECTOR, 'a[href^="/news/"]')))
        except Exception:
            logger.warning("Could not confirm articles loaded, proceeding anyway")

        for page_num in range(1, max_pages + 1):
            logger.info(f"Extracting articles from page {page_num}")
            page_articles = parse_page_articles(driver.page_source)
            new_count = 0
            for article in page_articles:
                if article["link"] not in seen_links:
                    all_articles.append(article)
                    seen_links.add(article["link"])
                    new_count += 1
            logger.info(f"Page {page_num}: {new_count} new articles (total: {len(all_articles)})")

            if page_num >= max_pages:
                break

            next_btns = driver.find_elements(By.CSS_SELECTOR, "#pagination-next")
            next_btn = next_btns[0] if next_btns else None

            if not next_btn or not next_btn.is_displayed() or next_btn.get_attribute("disabled") is not None:
                logger.info(f"No next button found after page {page_num}")
                break

            logger.info(f"Clicking next button to page {page_num + 1}")
            driver.execute_script("arguments[0].click();", next_btn)
            time.sleep(3)
            try:
                WebDriverWait(driver, 10).until(EC.presence_of_element_located((By.CSS_SELECTOR, 'a[href^="/news/"]')))
            except Exception:
                logger.warning("Timeout waiting for next page content")

        logger.info(f"Total articles fetched: {len(all_articles)}")
        return all_articles
    finally:
        if driver:
            driver.quit()


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


def main(full_reset: bool = False) -> bool:
    cache = load_cache(FEED_NAME)
    cached_entries = deserialize_entries(cache.get("entries", []))

    pages = MAX_PAGES_FULL if (full_reset or not cached_entries) else MAX_PAGES_INCREMENTAL
    mode = "full reset" if full_reset else "no cache exists" if not cached_entries else "incremental update"
    logger.info(f"Running {mode} (max_pages={pages})")
    new_articles = fetch_all_articles(max_pages=pages)

    if cached_entries and not full_reset:
        articles = merge_entries(new_articles, cached_entries)
    else:
        articles = sort_posts_for_feed(new_articles, date_field="date")

    if not articles:
        logger.warning("No articles found. Check the HTML structure.")
        return False

    save_cache(FEED_NAME, articles)
    feed = generate_rss_feed(articles)
    save_rss_feed(feed, FEED_NAME)
    logger.info("Done!")
    return True


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Generate Mistral AI News RSS feed")
    parser.add_argument("--full", action="store_true", help="Force full reset (fetch up to 6 pages)")
    args = parser.parse_args()
    main(full_reset=args.full)
