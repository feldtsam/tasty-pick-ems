#!/usr/bin/env python3
# ============================================================
# send_to_notion.py
#
# Converts the daily MLB research markdown into Notion blocks
# and creates a new child page inside a parent Notion page.
#
# Usage:
#   python3 send_to_notion.py input.md
#
# Requires in .env:
#   NOTION_API_KEY=secret_xxxx
#   NOTION_PARENT_PAGE_ID=xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
# ============================================================

import os
import sys
import re
import json
import time
import requests
from datetime import datetime
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"))

NOTION_API_KEY       = os.getenv("NOTION_API_KEY")
NOTION_PARENT_PAGE_ID = os.getenv("NOTION_PARENT_PAGE_ID")
NOTION_VERSION       = "2022-06-28"
BASE_URL             = "https://api.notion.com/v1"

HEADERS = {
    "Authorization":  f"Bearer {NOTION_API_KEY}",
    "Notion-Version": NOTION_VERSION,
    "Content-Type":   "application/json",
}

# ── Notion block builders ──────────────────────────────────────

def rich_text(text: str) -> list:
    """Convert a markdown-inline string into a Notion rich_text array."""
    if not text.strip():
        return [{"type": "text", "text": {"content": ""}}]

    parts = []
    # We'll tokenize by bold, italic, code, links, [~]
    pattern = re.compile(
        r'(\*\*\*(.+?)\*\*\*)'          # bold+italic
        r'|(\*\*(.+?)\*\*)'             # bold
        r'|(\*(.+?)\*)'                 # italic
        r'|(`([^`]+)`)'                 # inline code
        r'|(\[([^\]]+)\]\(([^)]+)\))'  # link
        r'|(\[~\])'                     # uncertainty flag
    )

    last = 0
    for m in pattern.finditer(text):
        # Plain text before this match
        if m.start() > last:
            parts.append({
                "type": "text",
                "text": {"content": text[last:m.start()]},
                "annotations": {}
            })

        if m.group(1):   # bold+italic
            parts.append({"type": "text", "text": {"content": m.group(2)},
                          "annotations": {"bold": True, "italic": True}})
        elif m.group(3): # bold
            parts.append({"type": "text", "text": {"content": m.group(4)},
                          "annotations": {"bold": True}})
        elif m.group(5): # italic
            parts.append({"type": "text", "text": {"content": m.group(6)},
                          "annotations": {"italic": True}})
        elif m.group(7): # code
            parts.append({"type": "text", "text": {"content": m.group(8)},
                          "annotations": {"code": True}})
        elif m.group(9): # link
            parts.append({"type": "text", "text": {"content": m.group(10),
                          "link": {"url": m.group(11)}},
                          "annotations": {}})
        elif m.group(12): # [~]
            parts.append({"type": "text", "text": {"content": "[~]"},
                          "annotations": {"color": "orange"}})

        last = m.end()

    # Remaining plain text
    if last < len(text):
        parts.append({
            "type": "text",
            "text": {"content": text[last:]},
            "annotations": {}
        })

    # Notion requires at least one element
    return parts if parts else [{"type": "text", "text": {"content": text}}]


def heading_block(level: int, text: str) -> dict:
    key = f"heading_{level}"
    return {
        "object": "block",
        "type":   key,
        key:      {"rich_text": rich_text(text)},
    }


def paragraph_block(text: str) -> dict:
    return {
        "object": "block",
        "type":   "paragraph",
        "paragraph": {"rich_text": rich_text(text)},
    }


def bullet_block(text: str) -> dict:
    return {
        "object": "block",
        "type":   "bulleted_list_item",
        "bulleted_list_item": {"rich_text": rich_text(text)},
    }


def numbered_block(text: str) -> dict:
    return {
        "object": "block",
        "type":   "numbered_list_item",
        "numbered_list_item": {"rich_text": rich_text(text)},
    }


def code_block(text: str, language: str = "json") -> dict:
    # Notion code blocks have a 2000-char limit per block; chunk if needed
    chunks = [text[i:i+1990] for i in range(0, len(text), 1990)]
    blocks = []
    for chunk in chunks:
        blocks.append({
            "object": "block",
            "type":   "code",
            "code":   {
                "rich_text": [{"type": "text", "text": {"content": chunk}}],
                "language":  language if language in NOTION_LANGUAGES else "plain text",
            },
        })
    return blocks


def divider_block() -> dict:
    return {"object": "block", "type": "divider", "divider": {}}


def callout_block(text: str, emoji: str = "📋") -> dict:
    return {
        "object": "block",
        "type":   "callout",
        "callout": {
            "rich_text": rich_text(text),
            "icon": {"type": "emoji", "emoji": emoji},
            "color": "gray_background",
        },
    }


# Notion supported language list (subset)
NOTION_LANGUAGES = {
    "json", "python", "javascript", "typescript", "bash",
    "shell", "sql", "html", "css", "markdown", "plain text",
}

# ── Markdown → Notion blocks ───────────────────────────────────

SECTION_EMOJI = {
    '🔥': '🔥', '🎯': '🎯', '💣': '💣',
    '📈': '📈', '⚠️': '⚠️', '🎥': '🎥',
}


def md_to_blocks(md_text: str) -> list:
    lines    = md_text.splitlines()
    blocks   = []
    i        = 0
    in_code  = False
    code_buf = []
    code_lang = ""

    while i < len(lines):
        line = lines[i]

        # ── Code fence ──────────────────────────────────────────
        if line.strip().startswith("```"):
            if not in_code:
                code_lang = line.strip()[3:].strip() or "plain text"
                in_code   = True
                code_buf  = []
            else:
                result = code_block("\n".join(code_buf), code_lang)
                if isinstance(result, list):
                    blocks.extend(result)
                else:
                    blocks.append(result)
                in_code   = False
                code_buf  = []
                code_lang = ""
            i += 1
            continue

        if in_code:
            code_buf.append(line)
            i += 1
            continue

        # ── Horizontal rule ─────────────────────────────────────
        if re.match(r'^-{3,}$', line.strip()):
            blocks.append(divider_block())
            i += 1
            continue

        # ── Headings ────────────────────────────────────────────
        if line.startswith("### "):
            text  = line[4:].strip()
            emoji = text[0] if text and text[0] in SECTION_EMOJI else "📋"
            blocks.append(callout_block(text, emoji))
            i += 1
            continue

        if line.startswith("## "):
            blocks.append(heading_block(2, line[3:].strip()))
            i += 1
            continue

        if line.startswith("# ") and not line.startswith("## "):
            blocks.append(heading_block(1, line[2:].strip()))
            i += 1
            continue

        if line.startswith("#### "):
            blocks.append(heading_block(3, line[5:].strip()))
            i += 1
            continue

        # ── Bullet list ──────────────────────────────────────────
        if re.match(r'^[-*]\s+', line):
            item = re.sub(r'^[-*]\s+', '', line)
            blocks.append(bullet_block(item))
            i += 1
            continue

        # ── Numbered list ────────────────────────────────────────
        if re.match(r'^\d+\.\s+', line):
            item = re.sub(r'^\d+\.\s+', '', line)
            blocks.append(numbered_block(item))
            i += 1
            continue

        # ── Blank line ────────────────────────────────────────────
        if not line.strip():
            i += 1
            continue

        # ── Default: paragraph ────────────────────────────────────
        blocks.append(paragraph_block(line))
        i += 1

    return blocks


# ── Notion API calls ───────────────────────────────────────────

def create_page(title: str) -> str:
    """Create a new child page under NOTION_PARENT_PAGE_ID. Returns new page ID."""
    payload = {
        "parent": {"type": "page_id", "page_id": NOTION_PARENT_PAGE_ID},
        "properties": {
            "title": {
                "title": [{"type": "text", "text": {"content": title}}]
            }
        },
    }
    r = requests.post(f"{BASE_URL}/pages", headers=HEADERS, json=payload)
    r.raise_for_status()
    return r.json()["id"]


def append_blocks(page_id: str, blocks: list):
    """Append blocks to a page in batches of 100 (Notion API limit)."""
    for start in range(0, len(blocks), 100):
        chunk = blocks[start:start + 100]
        payload = {"children": chunk}
        r = requests.patch(
            f"{BASE_URL}/blocks/{page_id}/children",
            headers=HEADERS,
            json=payload,
        )
        if not r.ok:
            print(f"  [Notion] Error appending blocks: {r.status_code} {r.text[:300]}")
            r.raise_for_status()
        # Stay under Notion's rate limit (3 req/s)
        if start + 100 < len(blocks):
            time.sleep(0.4)


# ── Main ───────────────────────────────────────────────────────

def main():
    if not NOTION_API_KEY or not NOTION_PARENT_PAGE_ID:
        print("ERROR: Set NOTION_API_KEY and NOTION_PARENT_PAGE_ID in .env")
        sys.exit(1)

    if len(sys.argv) < 2:
        print(f"Usage: {sys.argv[0]} input.md")
        sys.exit(1)

    md_path = sys.argv[1]
    basename = os.path.basename(md_path)
    date_str = re.sub(r'\.md$', '', basename)

    try:
        date_label = datetime.strptime(date_str, "%Y-%m-%d").strftime("%A, %B %-d, %Y")
    except ValueError:
        date_label = date_str

    title = f"⚾ Tasty Pick Ems — {date_label}"

    with open(md_path, 'r', encoding='utf-8') as f:
        md_text = f.read()

    print(f"  Converting markdown to Notion blocks...")
    blocks = md_to_blocks(md_text)
    print(f"  → {len(blocks)} blocks")

    print(f"  Creating Notion page: '{title}'")
    page_id = create_page(title)
    print(f"  → Page ID: {page_id}")

    print(f"  Appending {len(blocks)} blocks (in batches of 100)...")
    append_blocks(page_id, blocks)

    page_url = f"https://notion.so/{page_id.replace('-', '')}"
    print(f"  → Done: {page_url}")
    return page_url


if __name__ == "__main__":
    main()
