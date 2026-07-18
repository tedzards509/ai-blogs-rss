#!/usr/bin/env python3
"""Generates the README feeds table and feeds.opml from feeds.yaml.

Run after adding/removing/renaming a feed:
    uv run scripts/gen_readme_feeds_table.py
"""

import re
from pathlib import Path
from xml.dom import minidom
from xml.etree import ElementTree as ET

import yaml

ROOT = Path(__file__).resolve().parent.parent
FEEDS_YAML = ROOT / "feeds.yaml"
README = ROOT / "README.md"
OPML = ROOT / "feeds.opml"
RAW_BASE = "https://raw.githubusercontent.com/tedzards509/ai-blogs-rss/refs/heads/main/feeds"

START_MARKER = "<!-- FEEDS_TABLE_START -->"
END_MARKER = "<!-- FEEDS_TABLE_END -->"


def load_enabled_feeds() -> dict:
    data = yaml.safe_load(FEEDS_YAML.read_text())
    feeds = data["feeds"]
    return {name: cfg for name, cfg in feeds.items() if cfg.get("enabled", True)}


def build_table(feeds: dict) -> str:
    lines = ["| Source | Blog | RSS Feed |", "| --- | --- | --- |"]
    for name in sorted(feeds):
        cfg = feeds[name]
        blog_url = cfg["blog_url"]
        feed_url = f"{RAW_BASE}/feed_{name}.xml"
        title = name.replace("_", " ")
        lines.append(f"| {title} | [{blog_url}]({blog_url}) | [feed_{name}.xml]({feed_url}) |")

    return "\n".join(lines)


def update_readme(feeds: dict) -> None:
    readme = README.read_text()
    table = build_table(feeds)
    pattern = re.compile(re.escape(START_MARKER) + r".*?" + re.escape(END_MARKER), re.DOTALL)
    replacement = f"{START_MARKER}\n{table}\n{END_MARKER}"

    if pattern.search(readme):
        readme = pattern.sub(replacement, readme)
    else:
        readme = readme.rstrip() + "\n\n## Feeds\n\n" + replacement + "\n"

    README.write_text(readme)


def build_opml(feeds: dict) -> None:
    opml = ET.Element("opml", version="2.0")
    head = ET.SubElement(opml, "head")
    ET.SubElement(head, "title").text = "AI Blogs RSS"
    body = ET.SubElement(opml, "body")

    for name in sorted(feeds):
        cfg = feeds[name]
        title = name.replace("_", " ")
        ET.SubElement(
            body,
            "outline",
            type="rss",
            text=title,
            title=title,
            xmlUrl=f"{RAW_BASE}/feed_{name}.xml",
            htmlUrl=cfg["blog_url"],
        )

    rough = ET.tostring(opml, encoding="unicode")
    pretty = minidom.parseString(rough).toprettyxml(indent="  ")
    # Drop the blank lines minidom's pretty-printer inserts between elements.
    pretty = "\n".join(line for line in pretty.splitlines() if line.strip())
    OPML.write_text(pretty + "\n")


def main() -> None:
    feeds = load_enabled_feeds()
    update_readme(feeds)
    build_opml(feeds)


if __name__ == "__main__":
    main()
