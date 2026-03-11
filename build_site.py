#!/usr/bin/env python3
"""
生成 GitHub Pages 靜態卡片網站
卡片摘要 → 點擊展開全文中文翻譯 → 底部原文連結
"""

import html
import json
from collections import defaultdict
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).parent

REGION_COLORS = {
    "新加坡": "#e74c3c", "香港": "#e67e22", "中國": "#f39c12",
    "日本": "#2ecc71", "韓國": "#3498db", "台灣": "#9b59b6",
    "全球": "#1abc9c", "亞太": "#16a085", "美國": "#2c3e50", "歐洲": "#8e44ad",
}

IMPORTANCE_ICONS = {"高": "🔴", "中": "🟡", "低": "⚪"}

CATEGORY_ICONS = {
    "監管動態": "📋", "產品創新": "💡", "市場趨勢": "📈", "科技應用": "🤖",
    "再保市場": "🔄", "ESG永續": "🌱", "消費者保護": "🛡️", "人才與組織": "👥",
}


def _esc(s):
    """Escape HTML special characters"""
    return html.escape(str(s)) if s else ""


def build_site():
    index_path = ROOT / "index" / "master-index.json"
    if not index_path.exists():
        print("No index found")
        return

    all_articles = json.loads(index_path.read_text(encoding="utf-8"))
    articles = [a for a in all_articles if not a.get("filter")]
    articles.sort(key=lambda a: a.get("date", ""), reverse=True)

    by_date = defaultdict(list)
    for a in articles:
        by_date[a.get("date", "unknown")].append(a)

    total = len(articles)
    regions = defaultdict(int)
    categories = defaultdict(int)
    for a in articles:
        regions[a.get("region", "未知")] += 1
        categories[a.get("category", "未知")] += 1

    dates = sorted(by_date.keys(), reverse=True)
    latest_date = dates[0] if dates else "N/A"

    docs_dir = ROOT / "docs"
    docs_dir.mkdir(exist_ok=True)

    page = _generate_html(articles, by_date, dates, total, regions, categories, latest_date)
    (docs_dir / "index.html").write_text(page, encoding="utf-8")
    print(f"Site built: docs/index.html ({total} articles, {len(dates)} dates)")


def _generate_html(articles, by_date, dates, total, regions, categories, latest_date):
    region_buttons = ""
    for r in ["全部", "新加坡", "香港", "中國", "日本", "韓國", "台灣", "全球", "亞太"]:
        count = regions.get(r, 0) if r != "全部" else total
        color = REGION_COLORS.get(r, "#95a5a6")
        active = "active" if r == "全部" else ""
        region_buttons += f'<button class="filter-btn {active}" data-region="{r}" style="--btn-color:{color}">{r} ({count})</button>\n'

    cat_buttons = ""
    for c, icon in CATEGORY_ICONS.items():
        count = categories.get(c, 0)
        cat_buttons += f'<button class="filter-btn" data-category="{c}">{icon} {c} ({count})</button>\n'

    cards_html = ""
    for date in dates:
        date_articles = by_date[date]
        cards_html += f'<h2 class="date-header">{_esc(date)} ({len(date_articles)} 篇)</h2>\n'
        cards_html += '<div class="cards-grid">\n'
        for a in date_articles:
            region = a.get("region", "未知")
            category = a.get("category", "未知")
            importance = a.get("importance", "低")
            color = REGION_COLORS.get(region, "#95a5a6")
            icon = IMPORTANCE_ICONS.get(importance, "⚪")
            cat_icon = CATEGORY_ICONS.get(category, "📄")
            title = _esc(a.get("title", "無標題"))
            summary = _esc(a.get("summary", ""))
            # Preview: first 200 chars for card face
            preview = summary[:200] + "..." if len(summary) > 200 else summary
            source = _esc(a.get("source", ""))
            url = _esc(a.get("source_url", "#"))
            keywords = a.get("keywords", [])
            kw_html = " ".join(f'<span class="tag">{_esc(k)}</span>' for k in keywords[:5])
            companies = a.get("companies", [])
            co_html = " ".join(f'<span class="company-tag">{_esc(c)}</span>' for c in companies[:5]) if companies else ""

            cards_html += f'''<div class="card" data-region="{_esc(region)}" data-category="{_esc(category)}" data-importance="{_esc(importance)}" onclick="toggleCard(this)">
  <div class="card-header">
    <span class="region-badge" style="background:{color}">{_esc(region)}</span>
    <span class="importance">{icon}</span>
    <span class="category">{cat_icon} {_esc(category)}</span>
  </div>
  <h3 class="card-title">{title}</h3>
  <p class="card-preview">{preview}</p>
  <div class="card-full">
    <p class="card-summary">{summary}</p>
    <div class="card-tags">{kw_html}</div>
    {f'<div class="card-companies">🏢 {co_html}</div>' if co_html else ''}
    <div class="card-meta">
      <span class="source">📰 {source}</span>
    </div>
    <a class="source-link" href="{url}" target="_blank" rel="noopener" onclick="event.stopPropagation()">🔗 查看原文</a>
  </div>
</div>
'''
        cards_html += '</div>\n'

    now = datetime.now().strftime("%Y-%m-%d %H:%M")

    return f'''<!DOCTYPE html>
<html lang="zh-TW">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>保險產業資訊中心</title>
<style>
* {{ margin:0; padding:0; box-sizing:border-box; }}
body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Noto Sans TC", "Microsoft JhengHei", sans-serif; background:#f0f2f5; color:#333; }}
.container {{ max-width:1400px; margin:0 auto; padding:20px; }}

.header {{ background: linear-gradient(135deg, #1a2980, #26d0ce); color:white; padding:30px; border-radius:16px; margin-bottom:24px; }}
.header h1 {{ font-size:28px; margin-bottom:8px; }}
.header .stats {{ display:flex; gap:24px; flex-wrap:wrap; margin-top:12px; }}
.header .stat {{ background:rgba(255,255,255,0.15); padding:8px 16px; border-radius:8px; }}
.header .stat strong {{ font-size:20px; }}

.filters {{ background:white; padding:16px; border-radius:12px; margin-bottom:20px; box-shadow:0 1px 3px rgba(0,0,0,0.1); }}
.filters h3 {{ margin-bottom:8px; font-size:14px; color:#666; }}
.filter-row {{ display:flex; gap:8px; flex-wrap:wrap; margin-bottom:8px; }}
.filter-btn {{ padding:6px 14px; border:2px solid #e0e0e0; border-radius:20px; background:white; cursor:pointer; font-size:13px; transition:all 0.2s; }}
.filter-btn:hover {{ border-color:var(--btn-color, #3498db); color:var(--btn-color, #3498db); }}
.filter-btn.active {{ background:var(--btn-color, #3498db); color:white; border-color:var(--btn-color, #3498db); }}

.search-box {{ width:100%; padding:12px 16px; border:2px solid #e0e0e0; border-radius:12px; font-size:16px; margin-bottom:20px; }}
.search-box:focus {{ outline:none; border-color:#3498db; }}

.date-header {{ font-size:18px; color:#555; margin:24px 0 12px; padding-bottom:8px; border-bottom:2px solid #e0e0e0; }}

.cards-grid {{ display:grid; grid-template-columns:repeat(auto-fill, minmax(360px, 1fr)); gap:16px; }}

.card {{
  background:white; border-radius:12px; padding:16px;
  box-shadow:0 2px 8px rgba(0,0,0,0.08);
  transition:transform 0.2s, box-shadow 0.2s;
  border-left:4px solid #e0e0e0;
  cursor:pointer;
}}
.card:hover {{ transform:translateY(-2px); box-shadow:0 4px 16px rgba(0,0,0,0.12); }}
.card.expanded {{ border-left-color:#3498db; }}

.card-header {{ display:flex; align-items:center; gap:8px; margin-bottom:8px; }}
.region-badge {{ color:white; padding:2px 10px; border-radius:12px; font-size:12px; font-weight:600; }}
.importance {{ font-size:14px; }}
.category {{ font-size:12px; color:#777; }}

.card-title {{ font-size:15px; line-height:1.4; margin-bottom:8px; color:#2c3e50; }}

.card-preview {{ font-size:13px; color:#666; line-height:1.6; }}
.card.expanded .card-preview {{ display:none; }}

.card-full {{
  display:none;
  margin-top:12px;
  padding-top:12px;
  border-top:1px solid #eee;
}}
.card.expanded .card-full {{ display:block; }}

.card-summary {{
  font-size:14px; color:#444; line-height:1.8;
  margin-bottom:12px;
  white-space:pre-wrap;
}}

.card-meta {{ font-size:11px; color:#999; margin:8px 0; }}
.card-tags {{ display:flex; gap:4px; flex-wrap:wrap; margin-bottom:8px; }}
.tag {{ background:#f0f2f5; color:#555; padding:2px 8px; border-radius:8px; font-size:11px; }}
.card-companies {{ display:flex; gap:4px; flex-wrap:wrap; margin-bottom:8px; font-size:12px; }}
.company-tag {{ background:#e8f4fd; color:#2980b9; padding:2px 8px; border-radius:8px; font-size:11px; }}

.source-link {{
  display:inline-block;
  margin-top:8px;
  padding:8px 16px;
  background:#f8f9fa;
  border:1px solid #dee2e6;
  border-radius:8px;
  color:#495057;
  text-decoration:none;
  font-size:13px;
  transition:all 0.2s;
}}
.source-link:hover {{ background:#e9ecef; color:#212529; }}

.card.hidden {{ display:none; }}

.footer {{ text-align:center; color:#999; padding:24px; font-size:12px; }}

@media (max-width:768px) {{
  .cards-grid {{ grid-template-columns:1fr; }}
  .header .stats {{ gap:12px; }}
}}
</style>
</head>
<body>
<div class="container">

<div class="header">
  <h1>🏢 保險產業資訊中心</h1>
  <p>自動收集亞太及全球保險產業最新動態</p>
  <div class="stats">
    <div class="stat">📊 總計 <strong>{total}</strong> 篇</div>
    <div class="stat">📅 最新 <strong>{latest_date}</strong></div>
    <div class="stat">🌏 覆蓋 <strong>{len(regions)}</strong> 個地區</div>
    <div class="stat">🔄 更新於 <strong>{now}</strong></div>
    <div class="stat"><a href="https://insurance-kb.cooperation.tw/" style="color:white;text-decoration:none">💬 AI Chat</a></div>
  </div>
</div>

<input type="text" class="search-box" placeholder="🔍 搜尋標題、摘要、關鍵字..." id="searchBox">

<div class="filters">
  <h3>地區篩選</h3>
  <div class="filter-row" id="regionFilters">
    {region_buttons}
  </div>
  <h3>類別篩選</h3>
  <div class="filter-row" id="categoryFilters">
    <button class="filter-btn active" data-category="全部">全部</button>
    {cat_buttons}
  </div>
</div>

<div id="content">
{cards_html}
</div>

<div class="footer">
  保險產業資訊自動收集系統 · 資料來源：Google News RSS + 監管機構 + 保險公司官網<br>
  更新時間：{now}
</div>

</div>

<script>
let activeRegion = '全部';
let activeCategory = '全部';

function toggleCard(card) {{
  card.classList.toggle('expanded');
}}

function applyFilters() {{
  const search = document.getElementById('searchBox').value.toLowerCase();
  document.querySelectorAll('.card').forEach(card => {{
    const region = card.dataset.region;
    const category = card.dataset.category;
    const text = card.textContent.toLowerCase();
    const regionMatch = activeRegion === '全部' || region === activeRegion;
    const catMatch = activeCategory === '全部' || category === activeCategory;
    const searchMatch = !search || text.includes(search);
    card.classList.toggle('hidden', !(regionMatch && catMatch && searchMatch));
  }});
  document.querySelectorAll('.date-header').forEach(h => {{
    const grid = h.nextElementSibling;
    if (grid) {{
      const visible = grid.querySelectorAll('.card:not(.hidden)').length;
      h.style.display = visible ? '' : 'none';
      grid.style.display = visible ? '' : 'none';
    }}
  }});
}}

document.getElementById('regionFilters').addEventListener('click', e => {{
  if (e.target.classList.contains('filter-btn')) {{
    document.querySelectorAll('#regionFilters .filter-btn').forEach(b => b.classList.remove('active'));
    e.target.classList.add('active');
    activeRegion = e.target.dataset.region;
    applyFilters();
  }}
}});

document.getElementById('categoryFilters').addEventListener('click', e => {{
  if (e.target.classList.contains('filter-btn')) {{
    document.querySelectorAll('#categoryFilters .filter-btn').forEach(b => b.classList.remove('active'));
    e.target.classList.add('active');
    activeCategory = e.target.dataset.category;
    applyFilters();
  }}
}});

document.getElementById('searchBox').addEventListener('input', applyFilters);
</script>
</body>
</html>'''


if __name__ == "__main__":
    build_site()
