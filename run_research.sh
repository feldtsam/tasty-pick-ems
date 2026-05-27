#!/bin/bash
# ============================================================
# run_research.sh — runs the MLB research agent daily
# Called by cron at 6:45 AM or manually any time
# ============================================================

CLAUDE="/Users/samfeldt/.local/bin/claude"
PYTHON="/usr/bin/python3"
PROMPT="/Users/samfeldt/tasty-pick-ems/mlb_research.md"
CONVERTER="/Users/samfeldt/tasty-pick-ems/research_to_html.py"
OUT_DIR="/Users/samfeldt/Desktop/tasty-pick-ems-reports/research"
DATE_FILE=$(date +"%Y-%m-%d")
DATE_LABEL=$(date +"%B %d, %Y")

mkdir -p "$OUT_DIR"
MD_FILE="$OUT_DIR/$DATE_FILE.md"
HTML_FILE="$OUT_DIR/$DATE_FILE.html"

echo "[$DATE_LABEL] Starting MLB research agent..."

# 1. Inject today's date and pipe resolved prompt into claude
sed \
  -e "s|\$(date +\"%B %d, %Y\")|$DATE_LABEL|g" \
  -e "s|{DATE}|$DATE_LABEL|g" \
  "$PROMPT" \
| "$CLAUDE" --print --tools WebSearch --dangerously-skip-permissions \
> "$MD_FILE" 2>&1

STATUS=$?

if [ $STATUS -ne 0 ] || [ ! -s "$MD_FILE" ]; then
  echo "ERROR — claude failed (exit $STATUS). Check $MD_FILE"
  exit 1
fi

echo "  → Markdown: $MD_FILE ($(wc -l < "$MD_FILE") lines)"

# 2. Convert markdown → styled HTML
"$PYTHON" "$CONVERTER" "$MD_FILE" "$HTML_FILE"

if [ -s "$HTML_FILE" ]; then
  SIZE=$(du -h "$HTML_FILE" | cut -f1)
  echo "  → HTML:     $HTML_FILE ($SIZE)"
  # Open the report in the default browser
  open "$HTML_FILE"
else
  echo "ERROR — HTML conversion failed."
  exit 1
fi

# 3. Send to Notion (only if credentials are set in .env)
source /Users/samfeldt/tasty-pick-ems/.env 2>/dev/null
if [ -n "$NOTION_API_KEY" ] && [ -n "$NOTION_PARENT_PAGE_ID" ]; then
  echo "  Sending to Notion..."
  "$PYTHON" /Users/samfeldt/tasty-pick-ems/send_to_notion.py "$MD_FILE"
else
  echo "  Skipping Notion (NOTION_API_KEY or NOTION_PARENT_PAGE_ID not set in .env)"
fi

echo "Done."
