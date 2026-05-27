#!/usr/bin/env python3
# ============================================================
# research_to_html.py
#
# Converts the claude-generated MLB research markdown into a
# styled standalone HTML file matching the Tasty Pick Ems theme.
#
# Usage:
#   python3 research_to_html.py input.md output.html
# ============================================================

import sys
import re
import os
from datetime import datetime

def md_inline(text):
    """Convert inline markdown (bold, italic, code, links) to HTML."""
    # Links: [text](url)
    text = re.sub(r'\[([^\]]+)\]\(([^)]+)\)',
                  r'<a href="\2" target="_blank" rel="noopener">\1</a>', text)
    # Bold+italic: ***text***
    text = re.sub(r'\*\*\*(.+?)\*\*\*', r'<strong><em>\1</em></strong>', text)
    # Bold: **text**
    text = re.sub(r'\*\*(.+?)\*\*', r'<strong>\1</strong>', text)
    # Italic: *text*
    text = re.sub(r'\*(.+?)\*', r'<em>\1</em>', text)
    # Inline code: `code`
    text = re.sub(r'`([^`]+)`', r'<code>\1</code>', text)
    # [~] uncertainty flag
    text = re.sub(r'\[~\]', r'<span class="uncertain">[~]</span>', text)
    # ⚠️ warning prefix on list items
    text = re.sub(r'^(⚠️\s*)', r'<span class="warn-flag">\1</span>', text)
    return text


def convert(md_text: str, date_label: str) -> str:
    lines = md_text.splitlines()
    html_parts = []
    i = 0

    # Section emoji → CSS class map
    SECTION_CLASS = {
        '🔥': 'section-fire',
        '🎯': 'section-target',
        '💣': 'section-bomb',
        '📈': 'section-value',
        '⚠️': 'section-trap',
        '🎥': 'section-content',
    }

    in_list   = False
    in_code   = False
    code_lang = ''
    code_buf  = []
    in_para   = False

    def close_list():
        nonlocal in_list
        if in_list:
            html_parts.append('</ul>')
            in_list = False

    def close_para():
        nonlocal in_para
        if in_para:
            html_parts.append('</p>')
            in_para = False

    while i < len(lines):
        line = lines[i]

        # ── Code fence ─────────────────────────────────────────
        if line.strip().startswith('```'):
            if not in_code:
                close_list()
                close_para()
                code_lang = line.strip()[3:].strip()
                in_code   = True
                code_buf  = []
            else:
                # End code block
                code_content = '\n'.join(code_buf)
                lang_label   = code_lang.upper() if code_lang else 'CODE'
                # Pretty-print the JSON block with copy button
                if code_lang.lower() == 'json':
                    safe = code_content.replace('\\', '\\\\').replace('`', '\\`').replace('${', '\\${')
                    html_parts.append(f'''
<div class="code-block json-block">
  <div class="code-header">
    <span class="code-lang">{lang_label}</span>
    <button class="code-copy-btn" onclick="copyCode(this)">Copy JSON</button>
  </div>
  <pre><code>{_escape(code_content)}</code></pre>
</div>''')
                else:
                    html_parts.append(f'''
<div class="code-block">
  <div class="code-header"><span class="code-lang">{lang_label}</span></div>
  <pre><code>{_escape(code_content)}</code></pre>
</div>''')
                in_code   = False
                code_lang = ''
                code_buf  = []
            i += 1
            continue

        if in_code:
            code_buf.append(line)
            i += 1
            continue

        # ── Horizontal rule ─────────────────────────────────────
        if re.match(r'^-{3,}$', line.strip()):
            close_list()
            close_para()
            html_parts.append('<hr class="section-rule"/>')
            i += 1
            continue

        # ── H1 ──────────────────────────────────────────────────
        if line.startswith('# ') and not line.startswith('## '):
            close_list(); close_para()
            html_parts.append(f'<h1>{md_inline(line[2:].strip())}</h1>')
            i += 1
            continue

        # ── H2 ──────────────────────────────────────────────────
        if line.startswith('## '):
            close_list(); close_para()
            html_parts.append(f'<h2>{md_inline(line[3:].strip())}</h2>')
            i += 1
            continue

        # ── H3 (section headers) ─────────────────────────────────
        if line.startswith('### '):
            close_list(); close_para()
            text  = line[4:].strip()
            emoji = text[0] if text else ''
            cls   = SECTION_CLASS.get(emoji, 'section-default')
            html_parts.append(f'<h3 class="section-heading {cls}">{md_inline(text)}</h3>')
            i += 1
            continue

        # ── H4 ──────────────────────────────────────────────────
        if line.startswith('#### '):
            close_list(); close_para()
            html_parts.append(f'<h4>{md_inline(line[5:].strip())}</h4>')
            i += 1
            continue

        # ── Bullet list item ─────────────────────────────────────
        if re.match(r'^[-*]\s+', line):
            close_para()
            if not in_list:
                html_parts.append('<ul>')
                in_list = True
            item = re.sub(r'^[-*]\s+', '', line)

            # TikTok content angle: "Hook | Data | CTA"
            if ' | ' in item and any(hook in item for hook in ['"', '"', '"']):
                parts = [p.strip() for p in item.split(' | ')]
                if len(parts) == 3:
                    hook, data, cta = parts
                    hook_clean = re.sub(r'^["\'""]|["\'""]$', '', hook)
                    safe_hook  = hook_clean.replace("'", "\\'")
                    html_parts.append(f'''<li class="tiktok-angle-item">
  <div class="tiktok-hook">"{hook_clean}"</div>
  <div class="tiktok-data">{md_inline(data)}</div>
  <div class="tiktok-cta">{md_inline(cta)}</div>
  <button class="grab-btn" onclick="copyText(this,'{safe_hook}')">📲 Copy Hook</button>
</li>''')
                    i += 1
                    continue

            html_parts.append(f'<li>{md_inline(item)}</li>')
            i += 1
            continue

        # ── Numbered list ─────────────────────────────────────────
        if re.match(r'^\d+\.\s+', line):
            close_para()
            if not in_list:
                html_parts.append('<ul class="numbered-list">')
                in_list = True
            item = re.sub(r'^\d+\.\s+', '', line)
            html_parts.append(f'<li>{md_inline(item)}</li>')
            i += 1
            continue

        # ── Blank line ────────────────────────────────────────────
        if line.strip() == '':
            close_list()
            close_para()
            i += 1
            continue

        # ── Bold game header (e.g. **1. HOU @ CHC — Wrigley**) ───
        if line.startswith('**') and line.endswith('**') and '@' in line:
            close_list(); close_para()
            html_parts.append(f'<div class="game-header">{md_inline(line)}</div>')
            i += 1
            continue

        # ── Sources block (italic asterisk lines) ─────────────────
        if line.startswith('*All data sourced') or line.startswith('*Sources'):
            close_list(); close_para()
            html_parts.append(f'<p class="sources-note">{md_inline(line.strip("*"))}</p>')
            i += 1
            continue

        # ── Default: paragraph ────────────────────────────────────
        close_list()
        if not in_para:
            html_parts.append('<p>')
            in_para = True
        else:
            html_parts.append('<br/>')
        html_parts.append(md_inline(line))
        i += 1

    close_list()
    close_para()

    body = '\n'.join(html_parts)
    return _wrap_html(body, date_label)


def _escape(text: str) -> str:
    return text.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')


def _wrap_html(body: str, date_label: str) -> str:
    generated = datetime.now().strftime("%I:%M %p")
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8"/>
  <meta name="viewport" content="width=device-width, initial-scale=1.0"/>
  <title>Tasty Pick Ems — Research {date_label}</title>
  <link rel="preconnect" href="https://fonts.googleapis.com"/>
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin/>
  <link href="https://fonts.googleapis.com/css2?family=Bebas+Neue&family=Inter:wght@400;500;600;700;900&display=swap" rel="stylesheet"/>
  <style>
    :root {{
      --black:      #0d0d0d;
      --card-bg:    #141414;
      --border:     #2a2a2a;
      --green:      #39FF14;
      --green-dim:  #26b30e;
      --green-glow: rgba(57,255,20,0.08);
      --white:      #f0f0f0;
      --gray:       #888;
      --yellow:     #FFD700;
      --orange:     #FF8C00;
      --red:        #FF4444;
    }}

    *, *::before, *::after {{ box-sizing: border-box; margin: 0; padding: 0; }}

    body {{
      background: var(--black);
      color: var(--white);
      font-family: 'Inter', sans-serif;
      font-size: 14px;
      line-height: 1.65;
      padding-bottom: 80px;
    }}

    /* ── Header ── */
    .report-header {{
      background: var(--card-bg);
      border-bottom: 2px solid var(--green);
      padding: 20px;
      position: sticky;
      top: 0;
      z-index: 50;
    }}
    .report-header-inner {{
      max-width: 900px;
      margin: 0 auto;
      display: flex;
      align-items: center;
      justify-content: space-between;
      flex-wrap: wrap;
      gap: 12px;
    }}
    .report-brand {{
      font-family: 'Bebas Neue', sans-serif;
      font-size: 22px;
      letter-spacing: 2px;
      color: var(--green);
      text-shadow: 0 0 10px rgba(57,255,20,0.4);
    }}
    .report-brand span {{ color: var(--white); text-shadow: none; }}
    .report-meta {{ font-size: 11px; color: var(--gray); margin-top: 2px; }}
    .report-date {{
      font-family: 'Bebas Neue', sans-serif;
      font-size: 16px;
      letter-spacing: 1px;
      color: var(--white);
    }}
    .report-badge {{
      background: var(--green);
      color: var(--black);
      font-size: 11px;
      font-weight: 900;
      letter-spacing: 1px;
      text-transform: uppercase;
      padding: 5px 12px;
      border-radius: 6px;
    }}

    /* ── Layout ── */
    .container {{
      max-width: 900px;
      margin: 0 auto;
      padding: 32px 20px;
    }}

    /* ── Typography ── */
    h1 {{
      font-family: 'Bebas Neue', sans-serif;
      font-size: 28px;
      letter-spacing: 2px;
      color: var(--white);
      margin: 28px 0 12px;
    }}
    h2 {{
      font-family: 'Bebas Neue', sans-serif;
      font-size: 20px;
      letter-spacing: 1px;
      color: var(--gray);
      margin: 24px 0 10px;
      text-transform: uppercase;
    }}
    h3.section-heading {{
      font-family: 'Bebas Neue', sans-serif;
      font-size: 22px;
      letter-spacing: 2px;
      padding: 14px 18px;
      margin: 32px 0 16px;
      border-radius: 10px 10px 0 0;
      border-left: 4px solid currentColor;
    }}
    h3.section-fire    {{ color: #FF6B35; background: rgba(255,107,53,0.08); border-color: #FF6B35; }}
    h3.section-target  {{ color: var(--green); background: var(--green-glow); border-color: var(--green); }}
    h3.section-bomb    {{ color: var(--yellow); background: rgba(255,215,0,0.08); border-color: var(--yellow); }}
    h3.section-value   {{ color: #60B4FF; background: rgba(96,180,255,0.08); border-color: #60B4FF; }}
    h3.section-trap    {{ color: var(--red); background: rgba(255,68,68,0.08); border-color: var(--red); }}
    h3.section-content {{ color: #C084FC; background: rgba(192,132,252,0.08); border-color: #C084FC; }}
    h3.section-default {{ color: var(--white); background: rgba(255,255,255,0.04); border-color: var(--border); }}
    h4 {{
      font-size: 13px;
      font-weight: 700;
      color: var(--gray);
      text-transform: uppercase;
      letter-spacing: 1px;
      margin: 20px 0 8px;
    }}

    p {{ margin: 10px 0; color: var(--white); }}

    a {{ color: var(--green); text-decoration: none; }}
    a:hover {{ text-decoration: underline; }}

    code {{
      background: #1e1e1e;
      border: 1px solid var(--border);
      border-radius: 4px;
      padding: 1px 6px;
      font-family: 'SF Mono', 'Fira Code', monospace;
      font-size: 12px;
      color: #a8ff6e;
    }}

    strong {{ color: var(--white); font-weight: 700; }}
    em     {{ color: var(--gray); font-style: italic; }}

    hr.section-rule {{
      border: none;
      border-top: 1px solid var(--border);
      margin: 28px 0;
    }}

    /* ── Lists ── */
    ul {{
      list-style: none;
      margin: 8px 0 16px;
      padding: 0;
      display: flex;
      flex-direction: column;
      gap: 6px;
    }}
    ul.numbered-list {{ counter-reset: item; }}
    li {{
      background: var(--card-bg);
      border: 1px solid var(--border);
      border-radius: 8px;
      padding: 10px 14px;
      position: relative;
      padding-left: 28px;
    }}
    li::before {{
      content: '▸';
      position: absolute;
      left: 10px;
      color: var(--green);
      font-size: 10px;
      top: 12px;
    }}
    ul.numbered-list li {{ counter-increment: item; }}
    ul.numbered-list li::before {{
      content: counter(item);
      font-weight: 700;
      font-size: 11px;
      color: var(--green);
      top: 11px;
    }}

    /* ── Game header blocks ── */
    .game-header {{
      background: #1a1a1a;
      border: 1px solid var(--green);
      border-radius: 8px;
      padding: 10px 16px;
      margin: 18px 0 6px;
      font-weight: 700;
      font-size: 14px;
      color: var(--green);
    }}

    /* ── Code blocks ── */
    .code-block {{
      background: #0a0a0a;
      border: 1px solid var(--border);
      border-radius: 10px;
      overflow: hidden;
      margin: 16px 0;
    }}
    .json-block {{ border-color: var(--yellow); }}
    .code-header {{
      display: flex;
      align-items: center;
      justify-content: space-between;
      background: #111;
      padding: 8px 14px;
      border-bottom: 1px solid var(--border);
    }}
    .code-lang {{
      font-size: 10px;
      font-weight: 900;
      letter-spacing: 1.5px;
      color: var(--yellow);
      text-transform: uppercase;
    }}
    .code-copy-btn {{
      background: none;
      border: 1px solid var(--yellow);
      color: var(--yellow);
      font-size: 11px;
      font-weight: 700;
      padding: 3px 10px;
      border-radius: 4px;
      cursor: pointer;
      transition: background 0.15s;
    }}
    .code-copy-btn:hover {{ background: rgba(255,215,0,0.1); }}
    pre {{
      padding: 16px;
      overflow-x: auto;
      font-family: 'SF Mono', 'Fira Code', 'Menlo', monospace;
      font-size: 12px;
      line-height: 1.6;
      color: #a8ff6e;
    }}
    pre code {{
      background: none;
      border: none;
      padding: 0;
      font-size: inherit;
      color: inherit;
    }}

    /* ── TikTok content angle items ── */
    li.tiktok-angle-item {{
      padding: 14px 16px;
      flex-direction: column;
      gap: 6px;
      display: flex;
      border-color: #C084FC;
    }}
    li.tiktok-angle-item::before {{ display: none; }}
    .tiktok-hook {{
      font-size: 15px;
      font-weight: 700;
      color: var(--white);
      line-height: 1.4;
    }}
    .tiktok-data {{
      font-size: 12px;
      color: #C084FC;
    }}
    .tiktok-cta {{
      font-size: 12px;
      color: var(--gray);
      font-style: italic;
    }}

    /* ── Copy / grab buttons ── */
    .grab-btn {{
      display: inline-flex;
      align-items: center;
      gap: 6px;
      background: var(--green-glow);
      border: 1px solid var(--green);
      color: var(--green);
      font-size: 12px;
      font-weight: 700;
      padding: 6px 14px;
      border-radius: 6px;
      cursor: pointer;
      margin-top: 6px;
      transition: background 0.15s;
      width: fit-content;
    }}
    .grab-btn:hover {{ background: rgba(57,255,20,0.15); }}

    /* ── Uncertainty flag ── */
    .uncertain {{
      color: var(--orange);
      font-size: 11px;
      font-weight: 700;
    }}
    .warn-flag {{ color: var(--red); }}

    /* ── Sources note ── */
    .sources-note {{
      font-size: 11px;
      color: var(--gray);
      border-top: 1px solid var(--border);
      padding-top: 12px;
      margin-top: 16px;
    }}
  </style>
</head>
<body>

  <div class="report-header">
    <div class="report-header-inner">
      <div>
        <div class="report-brand">TASTY <span>PICK EMS</span></div>
        <div class="report-meta">Research Agent · Generated at {generated} · runs daily at 6:45 AM</div>
      </div>
      <div class="report-date">{date_label}</div>
      <a class="report-badge" href="../index.html">← All Reports</a>
    </div>
  </div>

  <div class="container">
    {body}
  </div>

  <script>
    function copyText(btn, text) {{
      navigator.clipboard.writeText(text).then(() => {{
        const orig = btn.innerHTML;
        btn.textContent = '✓ Copied!';
        btn.style.background = 'rgba(57,255,20,0.2)';
        setTimeout(() => {{ btn.innerHTML = orig; btn.style.background = ''; }}, 2000);
      }});
    }}
    function copyCode(btn) {{
      const code = btn.closest('.code-block').querySelector('pre code').innerText;
      navigator.clipboard.writeText(code).then(() => {{
        const orig = btn.textContent;
        btn.textContent = '✓ Copied!';
        setTimeout(() => {{ btn.textContent = orig; }}, 2000);
      }});
    }}
  </script>

</body>
</html>"""


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print(f"Usage: {sys.argv[0]} input.md output.html")
        sys.exit(1)

    md_path   = sys.argv[1]
    html_path = sys.argv[2]

    # Derive a human-readable date from the filename (YYYY-MM-DD)
    basename   = os.path.basename(md_path)
    date_str   = re.sub(r'\.md$', '', basename)
    try:
        date_label = datetime.strptime(date_str, "%Y-%m-%d").strftime("%A, %B %-d, %Y")
    except ValueError:
        date_label = date_str

    with open(md_path, 'r', encoding='utf-8') as f:
        md_text = f.read()

    html = convert(md_text, date_label)

    with open(html_path, 'w', encoding='utf-8') as f:
        f.write(html)

    print(f"→ {html_path}")
