import re
import sys

from bs4 import BeautifulSoup
from feed_generators.util.utils import (
    absolute_url,
    deserialize_entries,
    load_cache,
    merge_entries,
    parse_date,
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

logger = setup_logging()

FEED_NAME = "xainews"
BLOG_URL = "https://x.ai/news"


def fetch_news_content(url=BLOG_URL):
    """Fetch the fully loaded HTML content of xAI's news page using Selenium.

    The xAI news page is JS-rendered, so a simple HTTP request returns an empty
    shell. We need Selenium to wait for the content to load.
    """
    driver = None
    try:
        logger.info(f"Fetching content from URL: {url}")
        driver = setup_selenium_driver()
        driver.get(url)

        # Wait for news articles to load
        try:
            WebDriverWait(driver, 20).until(EC.presence_of_element_located((By.CSS_SELECTOR, "a[href*='/news/']")))
            logger.info("News articles loaded successfully")
        except Exception:
            logger.warning("Could not confirm articles loaded, proceeding anyway...")

        html_content = driver.page_source
        logger.info("Successfully fetched HTML content")
        return html_content

    except Exception as e:
        logger.error(f"Error fetching content: {e}")
        raise
    finally:
        if driver:
            driver.quit()


MONTH_NAMES = [
    "January",
    "February",
    "March",
    "April",
    "May",
    "June",
    "July",
    "August",
    "September",
    "October",
    "November",
    "December",
]


DATE_RE = re.compile(r"^[A-Za-z]+\.? \d{1,2},? \d{4}$")


def looks_like_date(text):
    """Check if text looks like a "Month Day, Year" date string."""
    return bool(DATE_RE.match(text))


def extract_articles(soup):
    """Extract article information from the parsed HTML.

    Each news item is an <a href="/news/..."> card. The featured card at the
    top has an <h1> (desktop) and duplicate <h2> (mobile); regular grid cards
    use <h3>. The publish date sits in a sibling <div> right above the title;
    only the featured card has a <p> description.
    """
    articles = []
    seen_links = set()

    cards = soup.select('a[href*="/news/"]')
    logger.info(f"Found {len(cards)} potential article cards")

    for card in cards:
        try:
            href = card.get("href", "")
            if not href:
                continue

            # Build full URL
            link = absolute_url(href, "https://x.ai")

            # Skip the main news page link
            if link.rstrip("/").endswith("/news"):
                continue

            # Skip duplicates
            if link in seen_links:
                continue

            # Extract title - featured card uses h1/h2, grid cards use h3
            title_elem = card.find("h1") or card.find("h2") or card.find("h3")
            if not title_elem:
                logger.debug(f"Could not extract title for link: {link}")
                continue

            title = title_elem.text.strip()
            if len(title) < 2:
                continue

            seen_links.add(link)

            # Extract description (only present on the featured card)
            description_elem = card.find("p")
            description = description_elem.text.strip() if description_elem else title

            # Extract date - look for a leaf div whose text looks like a date
            date = None
            for div in card.find_all("div"):
                if div.find("div"):
                    continue
                text = div.text.strip()
                if looks_like_date(text):
                    date = parse_date(text, fallback_id=link)
                    break

            # Fallback: use stable date
            if not date:
                logger.warning(f"Could not extract date for article: {title}")
                date = stable_fallback_date(link)

            img_elem = card.find("img")
            thumbnail = absolute_url(img_elem["src"], "https://x.ai") if img_elem and img_elem.get("src") else None

            article = {
                "title": title,
                "link": link,
                "date": date,
                "category": "News",
                "description": description,
                "thumbnail": thumbnail,
            }

            articles.append(article)
            logger.debug(f"Extracted article: {title} ({date})")

        except Exception as e:
            logger.warning(f"Error parsing article card: {e!s}")
            continue

    logger.info(f"Successfully parsed {len(articles)} articles")
    return articles


def parse_news_html(html_content):
    """Parse the news HTML content and extract article information."""
    try:
        soup = BeautifulSoup(html_content, "html.parser")
        return extract_articles(soup)
    except Exception as e:
        logger.error(f"Error parsing HTML content: {e!s}")
        raise


def generate_rss_feed(articles):
    """Generate RSS feed from news articles."""
    fg = FeedGenerator()
    fg.load_extension("media")
    fg.title("xAI News")
    fg.description("Latest news and updates from xAI")
    fg.language("en")

    fg.author({"name": "xAI"})
    fg.subtitle("Latest updates from xAI")
    setup_feed_links(fg, blog_url=BLOG_URL, feed_name=FEED_NAME)

    # Sort articles for correct feed order (newest first in output)
    articles_sorted = sort_posts_for_feed(articles, date_field="date")

    for article in articles_sorted:
        fe = fg.add_entry()
        fe.title(article["title"])
        fe.description(article["description"])
        fe.link(href=article["link"])
        fe.published(article["date"])
        fe.category(term=article["category"])
        fe.id(article["link"])
        if article.get("thumbnail"):
            fe.media.content([{"url": article["thumbnail"], "medium": "image"}])
            fe.media.thumbnail([{"url": article["thumbnail"]}])

    logger.info("Successfully generated RSS feed")
    return fg


def main(full_reset=False):
    """Main function to generate RSS feed from xAI's news page.

    Args:
        full_reset: If True, ignore cache and fetch fresh.
                   If False, merge with cached articles.
    """
    try:
        cache = load_cache(FEED_NAME)
        cached_articles = deserialize_entries(cache.get("entries", []))

        if full_reset or not cached_articles:
            mode = "full reset" if full_reset else "no cache exists"
            logger.info(f"Running full fetch ({mode})")
        else:
            logger.info("Running incremental update")

        # Fetch news content using Selenium (xAI is JS-rendered)
        html_content = fetch_news_content()

        # Parse articles from HTML
        new_articles = parse_news_html(html_content)

        if not new_articles and not cached_articles:
            logger.warning("No articles found!")
            return False

        # Merge with cache or use fresh articles
        if cached_articles and not full_reset:
            articles = merge_entries(new_articles, cached_articles)
        else:
            articles = new_articles

        # Save to cache
        save_cache(FEED_NAME, articles)

        # Generate and save RSS feed
        feed = generate_rss_feed(articles)
        save_rss_feed(feed, FEED_NAME)

        logger.info(f"Successfully generated RSS feed with {len(articles)} articles")
        return True

    except Exception as e:
        logger.error(f"Failed to generate RSS feed: {e!s}")
        return False


if __name__ == "__main__":
    sys.exit(0 if main(full_reset=parse_full_reset_flag("Generate xAI News RSS feed")) else 1)
