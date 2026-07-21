# AGENTS.md <!-- omit in toc -->

Instructions for Claude Code and contributors working on this repository.

## Table of Contents <!-- omit in toc -->

- [Project Overview](#project-overview)
- [Setup](#setup)
- [Running Feeds](#running-feeds)
- [Architecture](#architecture)
  - [Directory Layout](#directory-layout)
  - [Feed Registry (`feeds.yaml`)](#feed-registry-feedsyaml)
  - [Package Layout & Imports](#package-layout--imports)
  - [Feed Generator Patterns](#feed-generator-patterns)
  - [Shared Helpers (`feed_generators/util/utils.py`)](#shared-helpers-feed_generatorsutilutilspy)
  - [Feed Link Setup (Important)](#feed-link-setup-important)
- [Adding a New Feed](#adding-a-new-feed)
- [Code Style](#code-style)
- [Troubleshooting](#troubleshooting)
- [GitHub Actions](#github-actions)

## Project Overview

RSS Feed Generator creates RSS feeds for blogs that don't provide them natively. Each feed generator scrapes a blog and writes `feeds/feed_<name>.xml`. A GitHub Action runs on a schedule to regenerate and commit updated feeds.

This is a pared-down fork: most of the original project's feeds have been removed, and only a handful remain. There is no `Makefile` — use `uv run` / `uv run ruff` directly.

## Setup

```bash
uv sync --group dev
```

`uv sync` installs the project itself (`rss-feeds`) in editable mode, which is what makes `feed_generators` importable from anywhere — see [Package Layout & Imports](#package-layout--imports). No `PYTHONPATH` juggling needed.

Selenium-based generators need a local Chrome/Chromium binary. If it isn't at one of the standard paths (`google-chrome`, `google-chrome-stable`), point at it explicitly:

```bash
export CHROME_BINARY_PATH=/usr/bin/chromium-browser
```

## Running Feeds

Run any of these from the repo root, no extra environment setup required:

```bash
# Run a single generator directly
uv run feed_generators/mistral_blog.py
uv run feed_generators/mistral_blog.py --full   # ignore cache, full re-fetch

# Run everything via the registry (feeds.yaml)
uv run feed_generators/util/run_all_feeds.py
uv run feed_generators/util/run_all_feeds.py --feed=mistral
uv run feed_generators/util/run_all_feeds.py --feed=mistral --full
uv run feed_generators/util/run_all_feeds.py --skip-selenium   # requests-only feeds
uv run feed_generators/util/run_all_feeds.py --selenium-only   # selenium-only feeds
```

`--full` forces a full reset (ignore cache, fetch everything up to the generator's safety cap). Without it, generators fetch incrementally and stop as soon as they hit content already in the cache (see [Shared Helpers](#shared-helpers-feed_generatorsutilutilspy)).

## Architecture

### Directory Layout

```text
feed_generators/           # One script per blog, run directly with `uv run`
  util/
    utils.py               # Shared helpers (cache, dates, URLs, Selenium, feed setup)
    models.py               # Pydantic models for feeds.yaml + GlobalSettings
    run_all_feeds.py        # Orchestrator that reads feeds.yaml and runs generators
  <source>_blog.py          # Individual feed generators
feeds/                      # Output: feed_*.xml (committed to git)
cache/                      # JSON cache for incremental fetches (gitignored)
feeds.yaml                  # Feed registry — single source of truth for run_all_feeds.py
```

### Feed Registry (`feeds.yaml`)

`run_all_feeds.py` doesn't scan `feed_generators/` for scripts — it reads `feeds.yaml`:

```yaml
feeds:
  <name>:
    script: <name>_blog.py   # must exist in feed_generators/
    type: requests            # or "selenium"
    blog_url: https://example.com/blog
    enabled: true              # optional, defaults to true
```

`feed_generators/util/models.py` validates this file with Pydantic (`FeedConfig`, `load_feed_registry`) and errors immediately if a referenced script is missing.

### Package Layout & Imports

`utils.py` and `models.py` live in `feed_generators/util/`, one level deeper than the individual `<source>_blog.py` scripts. Every script and helper imports shared code with the fully-qualified path:

```python
from feed_generators.util.utils import ...
from feed_generators.util.models import ...
```

For this to resolve, `feed_generators` has to be importable as a package regardless of which script you run or what your cwd is — plain `uv run feed_generators/<script>.py` only puts that script's *own* directory on `sys.path`, not the repo root, so a fully-qualified import like the above would otherwise fail. `pyproject.toml` declares a `[build-system]` + `[tool.setuptools.packages.find]` for exactly this reason: `uv sync` installs `rss-feeds` itself into `.venv` in **editable mode**, which puts `feed_generators` on `sys.path` unconditionally, independent of cwd or invocation style. That's what makes every import above resolve without `PYTHONPATH`.

This only works if imports stay internally consistent — a bare `from models import ...` inside `utils.py` (instead of `from feed_generators.util.models import ...`) would still fail even with the editable install, since a bare top-level name isn't covered by the package installation. If you add a new shared module under `feed_generators/util/`, import it the fully-qualified way everywhere, including from sibling modules in `util/`.

If you relocate `utils.py`/`models.py` again, also update the `__file__`-relative path arithmetic in both files (`get_project_root()` in `utils.py`, `script_must_exist`/`load_feed_registry` in `models.py`, and `run_feed()`'s `script_path` in `run_all_feeds.py`) — these compute the repo root / `feed_generators/` directory relative to `__file__` for cache/feeds output and script lookup, and silently point at the wrong directory if the arithmetic is off by a level (independent of the import-resolution mechanism above).

### Feed Generator Patterns

Two patterns exist based on how the target site loads content:

#### 1. Simple Static (Default) <!-- omit in toc -->

For blogs where all content is available in a single request (or a source RSS feed to re-filter, like `openai_news_product.py`). No cache needed — the full result set is small enough to refetch every run.

**Key functions**: `fetch_page(url)` / `parse_source_rss(xml)`, `generate_rss_feed(items)`, `save_rss_feed(fg, feed_name)`.

#### 2. Cache-Backed Incremental Fetch <!-- omit in toc -->

For blogs with pagination (`deeplearningai_the_batch.py`) or a Selenium "Load more"/"See more" button (`anthropic_news_blog.py`, `meta_ai_blog.py`, `mistral_blog.py`). These maintain a JSON cache in `cache/<feed_name>_posts.json` and fetch incrementally by default.

**One fetch, multiple feeds**: when a single page needs to produce more than one output feed (e.g. `anthropic_news_blog.py` also writes the "Product"-only `feed_anthropic_news_product.xml` by filtering the same fetched/cached article list), do it in one script rather than a second full fetch — a second Selenium run of the same page just to re-filter is wasted cost and doubles bot-detection exposure. Register each output feed as its own `feeds.yaml` entry (so README/OPML get a row and the file has its own `blog_url`/output path), but point every such entry's `script:` at the same shared generator; `run_all_feeds.py` dedupes registry entries by `script` path and runs each unique script once per invocation, fanning its result out to all feed names that map to it.

**Key functions**:

- `load_cache(feed_name)` / `save_cache(feed_name, entries)` / `deserialize_entries(entries)` / `merge_entries(new, cached)` — cache persistence, from `utils.py`.
- `CacheCursor` (`utils.py`) — wraps a feed's cached entries and tracks new-vs-cached IDs across a fetch loop. Call `cursor.ingest(page_or_fold_entries)` once per page/click; it returns `True` if the loop should keep fetching (found new, uncached entries) or `False` if it should stop (nothing new, or a cached entry was hit). `cursor.new_entries` accumulates everything genuinely new across the run. Build the cursor from `cached_entries` for incremental runs, or `CacheCursor([])` for `--full` runs (so nothing looks "already cached" and the loop runs to its safety cap instead of stopping early).
- `setup_selenium_driver()` — headless Chrome via `undetected-chromedriver`, for the button-click generators.

**Cache behavior**:

- **Incremental (default)**: build `CacheCursor(cached_entries)`, fetch page-by-page or click-by-click, feeding each page/fold to `cursor.ingest(...)`. The loop exits as soon as a page/fold contains no new entries or contains an already-cached entry — there's no fixed page/click budget to tune.
- **`--full`**: build `CacheCursor([])` so nothing is treated as cached, and fetch up to the generator's `MAX_PAGES`/`max_clicks` safety cap.
- **Dedupe**: by `link`, via `merge_entries()`; sorted newest-first for the feed via `sort_posts_for_feed()`.

### Shared Helpers (`feed_generators/util/utils.py`)

Reuse these instead of reimplementing per-script — see the file for full docstrings:

| Helper | Purpose |
| -------------------------------------- | ------- |
| `fetch_page(url)` | `requests.get` with the shared `DEFAULT_HEADERS` |
| `parse_date(value, fallback_id="")` | `dateutil`-based date parsing, tz-normalized to UTC, falls back to `stable_fallback_date(fallback_id)` on empty/unparseable input |
| `stable_fallback_date(identifier)` | Deterministic pseudo-date (hash-based) for posts with no parseable date, so cache entries don't churn |
| `absolute_url(href, base_domain)` | Resolves a relative `href` against a site's origin |
| `load_cache` / `save_cache` / `deserialize_entries` / `merge_entries` | Cache persistence and dedupe/merge |
| `CacheCursor` | Tracks new-vs-cached entries across a fetch loop; see above |
| `sort_posts_for_feed(posts)` | Sorts ascending so `feedgen` (which reverses on write) outputs newest-first |
| `setup_feed_links(fg, blog_url, feed_name)` | Correct `<link>`/`rel="self"` ordering — see below |
| `save_rss_feed(fg, feed_name)` | Writes `feeds/feed_<feed_name>.xml` |
| `setup_selenium_driver()` | Headless Chrome/`undetected-chromedriver`, auto-detects Chrome version/binary |
| `parse_full_reset_flag(description)` | Standard `--full` CLI flag; use as `main(full_reset=parse_full_reset_flag("..."))` |
| `setup_logging(name=None)` | Consistent logger config; call once per module as `logger = setup_logging()` |

Some scripts still parse dates by scanning several candidate elements/selectors until one succeeds (e.g. `anthropic_news_blog.extract_date`, `meta_ai_blog.parse_date`) rather than calling `utils.parse_date` directly — `utils.parse_date` always returns a value (falling back on failure), which isn't the right fit for "keep trying selectors until one is a real date." Use `dateutil.parser.parse` inline with a narrow `except (ValueError, TypeError, OverflowError): continue` for that shape instead, and only call `utils.parse_date` at the final call site where a fallback is the natural default (see `mistral_blog.py`'s footer-date lookup for an example of this being safe to call directly).

### Adding Thumbnails (Media RSS)

When a source page/API exposes a cover image per post, emit it as Media RSS (`media:thumbnail` and `media:content`) so feed readers can show it. See `feed_generators/qwen_blog.py`, `bfl_blog.py`, `kimi_blog.py`, `minimax_blog.py`, `mistral_blog.py`, `meta_ai_blog.py`, `xainews_blog.py`, and `deeplearningai_the_batch.py` for working examples.

1. **Load the extension once, at the feed level** (entries inherit it automatically):

   ```python
   fg = FeedGenerator()
   fg.load_extension("media")
   ```

   Do **not** also call `fe.load_extension("media")` on an entry -- it's already inherited from `fg`, and calling it again raises `ImportError: Extension already loaded`.

2. **Extract an image URL per post** alongside the other fields (`title`, `link`, `date`, ...), and resolve it to an absolute URL with `absolute_url()` if it's relative. Cards commonly hide the real image behind a lazy-loading placeholder:
   - A plain `<img src="...">` in the card/article container -- the common case (`bfl_blog.py`, `kimi_blog.py`, `mistral_blog.py`).
   - The image sits on a shared parent, not the anchor/card itself -- walk up to `find_parent(...)` or search the parent before falling back (`minimax_blog.py`).
   - Next.js `<Image loading="lazy">` puts a base64 placeholder in the visible `<img src>` and the real image only in a `<noscript><img src="/_next/image?url=<encoded>&...">` fallback -- parse the `url` query param out of that proxy URL (`deeplearningai_the_batch.py`'s `extract_thumbnail()`).
   - For Selenium-rendered pages, some cards only inject their `<img>` via an IntersectionObserver once scrolled into view, so the initial `driver.page_source` won't have it for every card -- that's a genuine site limitation, not a bug; just emit the tag when present (see `xainews_blog.py`).

   Store it as `article["thumbnail"]` (or `None` if not found -- always treat this as optional, "if available").

3. **Emit both elements per entry**, only when a thumbnail was found:

   ```python
   if article.get("thumbnail"):
       fe.media.content([{"url": article["thumbnail"], "medium": "image"}])
       fe.media.thumbnail([{"url": article["thumbnail"]}])
   ```

   Both render inside a shared `<media:group>`; `media:content` is what most readers use to display the image above the post body, `media:thumbnail` is the smaller preview variant. Emitting the same URL for both is fine.

### Feed Link Setup (Important)

The main `<link>` element must point to the original blog, not the feed URL. Use `setup_feed_links(fg, blog_url, feed_name)` from `utils.py`.

**Why this matters**: In `feedgen`, link order determines which URL becomes the main `<link>`: `rel="self"` must be set **first**, `rel="alternate"` must be set **last**. Wrong order produces `<link>https://.../feed_<name>.xml</link>` instead of the blog URL.

## Adding a New Feed

1. **Analyze the target blog**: does it paginate via URL (`?page=2`) or a JS button, or is everything on one page? Check DevTools → Network for JS-rendered content (curl returning a near-empty shell is the tell).
2. **Write `feed_generators/<source>_blog.py`** following the closest existing pattern (see [Feed Generator Patterns](#feed-generator-patterns)) and reusing the [shared helpers](#shared-helpers-feed_generatorsutilutilspy) — don't reimplement date parsing, URL resolution, cache handling, or the `--full` flag.
3. **Register it in `feeds.yaml`**:

   ```yaml
   <source>:
     script: <source>_blog.py
     type: requests   # or selenium
     blog_url: https://example.com/blog
   ```

4. **Test locally**:

   ```bash
   uv run feed_generators/<source>_blog.py
   uv run feed_generators/<source>_blog.py --full
   cat feeds/feed_<source>.xml | head -50
   ```

5. **Verify**:
   - [ ] Feed XML is valid, `<link>` points to the blog (not the feed URL)
   - [ ] Posts have titles, dates, links; newest-first order
   - [ ] For cache-backed feeds: `cache/<source>_posts.json` is created on first run, and a second run logs an early stop (nothing new / hit cached entry) instead of refetching everything
   - [ ] `uv run ruff check feed_generators/` and `uv run ruff format --check feed_generators/` pass

6. **Regenerate the README feeds table and `feeds.opml`**:

   ```bash
   uv run scripts/gen_readme_feeds_table.py
   ```

   This reads `feeds.yaml` and rewrites the table between the `<!-- FEEDS_TABLE_START -->`/`<!-- FEEDS_TABLE_END -->` markers in `README.md`, plus `feeds.opml`, so don't hand-edit either.

## Code Style

Ruff handles both linting and formatting (config in `pyproject.toml`); pre-commit runs it automatically (`.pre-commit-config.yaml`, `uv sync --group dev && pre-commit install`).

```bash
uv run ruff check feed_generators/            # lint
uv run ruff check --fix feed_generators/      # lint, auto-fix
uv run ruff format feed_generators/           # format
```

## Troubleshooting

**"No posts found" or empty feed**

- HTML structure may have changed; re-download a sample and update selectors.
- For Selenium: increase wait times, or the site may be blocking headless browsers.

**`ModuleNotFoundError: No module named 'feed_generators'` or `'models'`**

- The project isn't installed. Run `uv sync` (or `uv sync --group dev`) from the repo root — this installs `rss-feeds` in editable mode, which is what makes `feed_generators` importable. See [Package Layout & Imports](#package-layout--imports).
- If `uv sync` already ran and this still happens, check that any new shared module imports its siblings the fully-qualified way (`from feed_generators.util.x import ...`), not as a bare name — a bare import will fail even with the editable install.

**Feed/cache files showing up under `feed_generators/feeds/` or `feed_generators/cache/` instead of the top-level `feeds/`/`cache/`**

- One of the `__file__`-relative path calculations in `utils.py`/`models.py`/`run_all_feeds.py` is off by a directory level. See [Package Layout & Imports](#package-layout--imports).

**Feed `<link>` shows the XML URL instead of the blog URL**

- Use `setup_feed_links()`; ensure `rel="self"` is set before `rel="alternate"`.

**Selenium bot detection / timeouts**

- `undetected-chromedriver` handles most cases; try increasing wait times.
- Make sure `CHROME_BINARY_PATH` points at a real Chrome/Chromium binary if it isn't at a standard path.

**Cache not updating / stale data**

- Delete `cache/<source>_posts.json` and rerun with `--full`.
- Check `CacheCursor` wiring — the loop should call `cursor.ingest(...)` on every page/fold, and `main()` should build the cursor from `CacheCursor([])` (not the real cache) when `full_reset` is true, or it'll stop early even during a full reset.

**Date parsing errors**

- Prefer `utils.parse_date` unless the generator needs to try several candidate elements before accepting one — see the note in [Shared Helpers](#shared-helpers-feed_generatorsutilutilspy).

## GitHub Actions

- `.github/workflows/run_feeds.yml` — runs on a schedule, `uv sync`s then executes `feed_generators/util/run_all_feeds.py`, commits updated `feeds/*.xml`.
