import contextlib
import sys
import xml.etree.ElementTree as ET

import pytz
from bs4 import BeautifulSoup
from dateutil import parser as date_parser
from feed_generators.util.utils import (
    CacheCursor,
    absolute_url,
    deserialize_entries,
    load_cache,
    merge_entries,
    parse_full_reset_flag,
    save_cache,
    save_rss_feed,
    setup_feed_links,
    setup_logging,
    setup_selenium_driver,
    sort_posts_for_feed,
    stable_fallback_date,
)
from feedgen.feed import FeedGenerator
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

FEED_NAME = "anthropic_news"
BLOG_URL = "https://www.anthropic.com/news"

logger = setup_logging()


def fetch_news_content(cursor, url=BLOG_URL, max_clicks=20):
    """Fetch news articles using Selenium, clicking "See more" until a fold
    turns up nothing new (or something already cached), or max_clicks is hit.

    Args:
        cursor: CacheCursor tracking which articles are already cached.
        url: The URL to fetch.
        max_clicks: Safety cap on the number of "See more" clicks.

    Returns:
        cursor.new_entries: articles from this run not already cached.
    """
    driver = None
    try:
        logger.info(f"Fetching content from URL: {url} (max_clicks={max_clicks})")
        driver = setup_selenium_driver()
        driver.get(url)

        # Wait for news articles to be present
        try:
            WebDriverWait(driver, 20).until(EC.presence_of_element_located((By.CSS_SELECTOR, "a[href*='/news/']")))
            logger.info("News articles loaded successfully")
        except Exception:
            logger.warning("Could not confirm articles loaded, proceeding anyway...")

        if not cursor.ingest(parse_news_html(driver.page_source)):
            logger.info("No new articles (or hit cached article) on initial load")
            return cursor.new_entries

        # Click "See more" button repeatedly until it's no longer available
        clicks = 0
        while clicks < max_clicks:
            try:
                # Look for the "See more" button using multiple selectors
                see_more_button = None
                selectors = [
                    "[class*='seeMore']",
                    "[class*='see-more']",
                    "button[class*='More']",
                ]
                for selector in selectors:
                    try:
                        see_more_button = driver.find_element(By.CSS_SELECTOR, selector)
                        if see_more_button and see_more_button.is_displayed():
                            break
                        see_more_button = None
                    except Exception:
                        continue

                # Also try finding by text content using XPath
                if not see_more_button:
                    with contextlib.suppress(Exception):
                        see_more_button = driver.find_element(
                            By.XPATH,
                            "//*[contains(text(), 'See more') or contains(text(), 'Load more')]",
                        )

                if see_more_button and see_more_button.is_displayed():
                    count_before = len(driver.find_elements(By.CSS_SELECTOR, "a[href*='/news/']"))
                    logger.info(f"Clicking 'See more' button (click {clicks + 1})...")
                    driver.execute_script("arguments[0].click();", see_more_button)
                    clicks += 1
                    # Wait for new articles to appear after click
                    with contextlib.suppress(Exception):
                        WebDriverWait(driver, 5).until(
                            lambda d, n=count_before: len(d.find_elements(By.CSS_SELECTOR, "a[href*='/news/']")) > n
                        )
                    if not cursor.ingest(parse_news_html(driver.page_source)):
                        logger.info(f"No new articles (or hit cached article) after {clicks} clicks")
                        break
                else:
                    logger.info(f"No more 'See more' button found after {clicks} clicks")
                    break
            except Exception as e:
                # No more "See more" button found
                logger.info(f"No more 'See more' button found after {clicks} clicks: {e}")
                break

        logger.info(f"Total new articles fetched: {len(cursor.new_entries)}")
        return cursor.new_entries

    except Exception as e:
        logger.error(f"Error fetching content: {e}")
        raise
    finally:
        if driver:
            driver.quit()


def extract_title(card):
    """Extract title using multiple fallback selectors."""
    selectors = [
        # New FeaturedGrid layout
        "h2[class*='featuredTitle']",
        "h4[class*='title']",
        # New PublicationList layout
        "span[class*='title']",
        # Legacy selectors
        "h3.PostCard_post-heading__Ob1pu",
        "h3.Card_headline__reaoT",
        "h3[class*='headline']",
        "h3[class*='heading']",
        "h2[class*='headline']",
        "h2[class*='heading']",
        "h3",
        "h2",
    ]
    for selector in selectors:
        elem = card.select_one(selector)
        if elem and elem.text.strip():
            return elem.text.strip()
    return None


def extract_date(card):
    """Extract date using multiple fallback selectors, trying each candidate
    element's text as a date until one actually parses."""
    selectors = [
        # New layout selectors - time element is most reliable
        "time[class*='date']",
        "time",
        # Legacy selectors
        "p.detail-m",
        "div.PostList_post-date__djrOA",
        "p[class*='date']",
        "div[class*='date']",
    ]

    for selector in selectors:
        # Use select() to get all matching elements, not just the first one
        elems = card.select(selector)
        for elem in elems:
            date_text = elem.text.strip()
            try:
                dt = date_parser.parse(date_text)
            except (ValueError, TypeError, OverflowError):
                continue
            return dt if dt.tzinfo else dt.replace(tzinfo=pytz.UTC)

    return None


def extract_category(card, date_elem_text=None):
    """Extract category using multiple fallback selectors."""
    selectors = [
        # New layout selectors
        "span[class*='subject']",  # PublicationList layout
        "span.caption.bold",  # FeaturedGrid layout (category before date)
        # Legacy selectors
        "span.text-label",
        "p.detail-m",
        "span[class*='category']",
        "div[class*='category']",
    ]

    for selector in selectors:
        elem = card.select_one(selector)
        if elem:
            text = elem.text.strip()
            # Skip if this is the date element
            if date_elem_text and text == date_elem_text:
                continue
            # Skip if it looks like a date
            if any(
                month in text
                for month in [
                    "Jan",
                    "Feb",
                    "Mar",
                    "Apr",
                    "May",
                    "Jun",
                    "Jul",
                    "Aug",
                    "Sep",
                    "Oct",
                    "Nov",
                    "Dec",
                ]
            ):
                continue
            return text

    return "News"


def validate_article(article):
    """Validate that article has all required fields with reasonable values."""
    if not article.get("title") or len(article["title"]) < 5:
        logger.warning(f"Invalid title for article: {article.get('link', 'unknown')}")
        return False

    if not article.get("link") or not article["link"].startswith("http"):
        logger.warning(f"Invalid link for article: {article.get('title', 'unknown')}")
        return False

    if not article.get("date"):
        logger.warning(f"Missing date for article: {article.get('title', 'unknown')}")
        return False

    return True


def parse_news_html(html_content):
    """Parse the news HTML content and extract article information."""
    try:
        soup = BeautifulSoup(html_content, "html.parser")
        articles = []
        seen_links = set()
        unknown_structures = 0

        # Find all links that point to news articles
        # Use flexible selectors to catch current and future card types
        # Handle both relative (/news/...) and absolute (https://www.anthropic.com/news/...) URLs
        all_news_links = soup.select('a[href*="/news/"], a[href*="anthropic.com/news/"]')

        logger.info(f"Found {len(all_news_links)} potential news article links")

        for card in all_news_links:
            href = card.get("href", "")
            if not href:
                continue

            # Build full URL
            link = absolute_url(href, "https://www.anthropic.com")

            # Skip duplicates
            if link in seen_links:
                continue

            # Skip the main news page link and anchor links
            if link.endswith("/news") or link.endswith("/news/") or "/news#" in link:
                continue

            seen_links.add(link)

            # Extract title using fallback chain
            title = extract_title(card)
            if not title:
                logger.debug(f"Could not extract title for link: {link}")
                logger.debug(f"Card HTML preview: {str(card)[:200]}")
                unknown_structures += 1
                continue

            # Extract date using fallback chain
            date = extract_date(card)
            if not date:
                logger.warning(f"Could not extract date for article: {title}")
                date = stable_fallback_date(link)

            # Extract category
            category = extract_category(card)

            # Create article object
            article = {
                "title": title,
                "link": link,
                "date": date,
                "category": category,
                "description": title,  # Using title as description fallback
            }

            # Validate article before adding
            if validate_article(article):
                articles.append(article)
            else:
                unknown_structures += 1

        if unknown_structures > 0:
            logger.warning(f"Encountered {unknown_structures} links with unknown or invalid structures")

        logger.info(f"Successfully parsed {len(articles)} valid articles")
        return articles

    except Exception as e:
        logger.error(f"Error parsing HTML content: {e!s}")
        raise


def generate_rss_feed(articles):
    """Generate RSS feed from news articles."""
    try:
        fg = FeedGenerator()
        fg.title("Anthropic News")
        fg.description("Latest news and updates from Anthropic")
        fg.language("en")

        # Set feed metadata
        fg.author({"name": "Anthropic News"})
        fg.logo("https://www.anthropic.com/images/icons/apple-touch-icon.png")
        fg.subtitle("Latest updates from Anthropic's newsroom")
        setup_feed_links(fg, blog_url=BLOG_URL, feed_name=FEED_NAME)

        # Sort articles for correct feed order (newest first in output)
        articles_sorted = sort_posts_for_feed(articles, date_field="date")

        # Add entries
        for article in articles_sorted:
            fe = fg.add_entry()
            fe.title(article["title"])
            fe.description(article["description"])
            fe.link(href=article["link"])
            fe.published(article["date"])
            fe.category(term=article["category"])
            fe.id(article["link"])

        logger.info("Successfully generated RSS feed")
        return fg

    except Exception as e:
        logger.error(f"Error generating RSS feed: {e!s}")
        raise


def get_existing_links_from_feed(feed_path):
    """Parse the existing RSS feed and return a set of all article links."""
    existing_links = set()
    try:
        if not feed_path.exists():
            return existing_links
        tree = ET.parse(feed_path)
        root = tree.getroot()
        # RSS 2.0: items under channel/item
        for item in root.findall("./channel/item"):
            link_elem = item.find("link")
            if link_elem is not None and link_elem.text:
                existing_links.add(link_elem.text.strip())
    except Exception as e:
        logger.warning(f"Failed to parse existing feed for deduplication: {e!s}")
    return existing_links


def main(full_reset=False):
    """Main function to generate RSS feed from Anthropic's news page.

    Args:
        full_reset: If True, ignore cache and fetch until max_clicks is hit.
            If False, click "See more" until a fold turns up nothing new, then merge with cache.
    """
    try:
        cache = load_cache(FEED_NAME)
        cached_articles = deserialize_entries(cache.get("entries", []))

        mode = "full reset" if full_reset else "no cache exists" if not cached_articles else "incremental update"
        logger.info(f"Running {mode}")
        cursor = CacheCursor([] if full_reset else cached_articles)
        new_articles = fetch_news_content(cursor, max_clicks=20)

        if full_reset or not cached_articles:
            articles = new_articles
        else:
            logger.info(f"Found {len(new_articles)} new articles")
            articles = merge_entries(new_articles, cached_articles)

        if not articles:
            logger.warning("No articles found. Please check the HTML structure.")
            return False

        # Save to cache
        save_cache(FEED_NAME, articles)

        # Generate RSS feed with all articles
        feed = generate_rss_feed(articles)

        # Save feed to file
        save_rss_feed(feed, FEED_NAME)

        logger.info(f"Successfully generated RSS feed with {len(articles)} articles")
        return True

    except Exception as e:
        logger.error(f"Failed to generate RSS feed: {e!s}")
        return False


if __name__ == "__main__":
    sys.exit(0 if main(full_reset=parse_full_reset_flag("Generate Anthropic News RSS feed")) else 1)
