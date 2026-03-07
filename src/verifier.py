"""
三層驗證機制
Layer 1: 爬取驗證（在 crawler.py 已完成）
Layer 2: AI 輸出驗證 — 結構/欄位/合理性檢查
Layer 3: 完整性審計 — 地區/類別覆蓋率 + 缺口回填
"""

import json
import logging
import subprocess
from collections import Counter
from datetime import datetime, timezone, timedelta

logger = logging.getLogger("verifier")

VALID_CATEGORIES = [
    "監管動態", "產品創新", "市場趨勢", "科技應用",
    "再保市場", "ESG永續", "消費者保護", "人才與組織",
]
VALID_REGIONS = [
    "新加坡", "香港", "中國", "日本", "韓國", "台灣",
    "美國", "歐洲", "亞太", "全球",
]
VALID_IMPORTANCE = ["高", "中", "低"]

REQUIRED_FIELDS = ["title_zh", "summary_zh", "category", "region", "importance"]

TZ_UTC8 = timezone(timedelta(hours=8))


# ===== Layer 2: AI 輸出驗證 =====

def verify_ai_output(ai_result, source_region=""):
    """驗證 AI 分析結果的結構和合理性，回傳 (is_valid, issues, fixed_result)"""
    issues = []
    fixed = dict(ai_result)

    # 1. 必填欄位檢查
    for field in REQUIRED_FIELDS:
        if not fixed.get(field):
            issues.append(f"missing_{field}")

    # 2. 類別合法性
    if fixed.get("category") and fixed["category"] not in VALID_CATEGORIES:
        best = _fuzzy_match(fixed["category"], VALID_CATEGORIES)
        if best:
            issues.append(f"category_corrected:{fixed['category']}->{best}")
            fixed["category"] = best
        else:
            issues.append(f"invalid_category:{fixed['category']}")
            fixed["category"] = "市場趨勢"

    # 3. 地區合法性
    if fixed.get("region") and fixed["region"] not in VALID_REGIONS:
        best = _fuzzy_match(fixed["region"], VALID_REGIONS)
        if best:
            issues.append(f"region_corrected:{fixed['region']}->{best}")
            fixed["region"] = best
        elif source_region and source_region in VALID_REGIONS:
            fixed["region"] = source_region
            issues.append(f"region_fallback_to_source:{source_region}")
        else:
            fixed["region"] = "全球"
            issues.append("region_default_global")

    # 4. 重要程度合法性
    if fixed.get("importance") and fixed["importance"] not in VALID_IMPORTANCE:
        fixed["importance"] = "中"
        issues.append("importance_default_medium")

    # 5. 摘要長度檢查
    summary = fixed.get("summary_zh", "")
    if len(summary) < 50:
        issues.append(f"summary_too_short:{len(summary)}")
    elif len(summary) < 200:
        issues.append(f"summary_short:{len(summary)}")

    # 6. 標題品質
    title = fixed.get("title_zh", "")
    if len(title) < 5:
        issues.append(f"title_too_short:{len(title)}")
    if title and not any('\u4e00' <= c <= '\u9fff' for c in title):
        issues.append("title_not_chinese")

    # 7. keywords 和 companies 應為 list
    if not isinstance(fixed.get("keywords", []), list):
        fixed["keywords"] = []
        issues.append("keywords_not_list")
    if not isinstance(fixed.get("companies", []), list):
        fixed["companies"] = []
        issues.append("companies_not_list")

    is_valid = not any(i.startswith("missing_") for i in issues)
    return is_valid, issues, fixed


def verify_batch(processed_articles):
    """批次驗證所有文章，回傳統計摘要"""
    stats = {
        "total": len(processed_articles),
        "valid": 0,
        "fixed": 0,
        "invalid": 0,
        "issue_counts": Counter(),
    }

    for article in processed_articles:
        ai = article.get("ai", {})
        source_region = article.get("source", {}).get("region", "")
        is_valid, issues, fixed = verify_ai_output(ai, source_region)

        if issues:
            article["ai"] = fixed
            article["verification_issues"] = issues
            for issue in issues:
                key = issue.split(":")[0]
                stats["issue_counts"][key] += 1
            if is_valid:
                stats["fixed"] += 1
            else:
                stats["invalid"] += 1
        else:
            stats["valid"] += 1

    logger.info(
        f"Verification: {stats['valid']} valid, {stats['fixed']} fixed, "
        f"{stats['invalid']} invalid out of {stats['total']}"
    )
    if stats["issue_counts"]:
        top_issues = stats["issue_counts"].most_common(5)
        logger.info(f"Top issues: {top_issues}")

    return stats


# ===== Layer 3: 完整性審計 =====

def audit_completeness(processed_articles, health_results):
    """審計爬取完整性，檢查地區/類別覆蓋"""
    region_counts = Counter()
    category_counts = Counter()
    source_type_counts = Counter()

    for article in processed_articles:
        ai = article.get("ai", {})
        source = article.get("source", {})
        region_counts[ai.get("region", source.get("region", "未知"))] += 1
        category_counts[ai.get("category", "未知")] += 1
        source_type_counts[source.get("type", "未知")] += 1

    gaps = []
    search_suggestions = []

    # 地區覆蓋檢查
    core_regions = ["新加坡", "香港", "中國", "日本", "韓國"]
    for region in core_regions:
        count = region_counts.get(region, 0)
        if count == 0:
            gaps.append(f"{region}: 無任何文章")
            search_suggestions.append(f"{region} insurance news")
        elif count < 3:
            gaps.append(f"{region}: 僅 {count} 篇，可能不足")

    # 類別覆蓋檢查
    core_categories = ["監管動態", "產品創新", "市場趨勢", "科技應用"]
    for cat in core_categories:
        count = category_counts.get(cat, 0)
        if count == 0:
            gaps.append(f"類別 {cat}: 無任何文章")

    # 來源健康度
    failed_sources = [h for h in health_results if h.get("status") != "ok"]
    if failed_sources:
        for f in failed_sources:
            gaps.append(f"來源失敗: {f.get('source_id', '?')} ({f.get('error', '')[:40]})")

    # 計算覆蓋分數
    region_score = sum(1 for r in core_regions if region_counts.get(r, 0) > 0) / len(core_regions) * 50
    cat_score = sum(1 for c in core_categories if category_counts.get(c, 0) > 0) / len(core_categories) * 30
    source_score = max(0, (1 - len(failed_sources) / max(len(health_results), 1)) * 20)
    total_score = int(region_score + cat_score + source_score)

    audit = {
        "timestamp": datetime.now(TZ_UTC8).isoformat(),
        "total_articles": len(processed_articles),
        "region_counts": dict(region_counts),
        "category_counts": dict(category_counts),
        "coverage_score": total_score,
        "gaps": gaps,
        "search_suggestions": search_suggestions,
        "failed_sources": len(failed_sources),
    }

    logger.info(f"Audit: coverage={total_score}%, gaps={len(gaps)}, articles={len(processed_articles)}")
    return audit


def gemini_gap_scan(processed_articles):
    """用 Gemini CLI 做智慧補漏分析（取代 Groq API）"""
    region_summary = Counter()
    cat_summary = Counter()
    titles_sample = []

    for a in processed_articles:
        ai = a.get("ai", {})
        region_summary[ai.get("region", "未知")] += 1
        cat_summary[ai.get("category", "未知")] += 1
        if len(titles_sample) < 30:
            titles_sample.append(ai.get("title_zh", a.get("crawl", {}).get("title", ""))[:60])

    prompt = f"""你是保險產業分析師。分析以下今日爬蟲收集結果的完整性：

地區分佈: {json.dumps(dict(region_summary), ensure_ascii=False)}
類別分佈: {json.dumps(dict(cat_summary), ensure_ascii=False)}
文章樣本:
{chr(10).join('- ' + t for t in titles_sample)}

請評估：
1. 地區覆蓋是否完整（新加坡/香港/中國/日本/韓國必須有）
2. 類別是否均衡
3. 近期是否有重大保險新聞被遺漏

回傳 JSON（不要 markdown code block）：
{{"gaps": ["遺漏1"], "search_queries": ["建議搜尋1"], "coverage_score": 85, "assessment": "一句話評估"}}"""

    try:
        result = subprocess.run(
            ["gemini", "-p", prompt],
            capture_output=True, text=True, timeout=120,
        )
        if result.returncode != 0:
            logger.error(f"Gemini gap scan failed: {result.stderr[:200]}")
            return None

        raw = result.stdout.strip()
        if raw.startswith("```"):
            raw = raw.split("\n", 1)[1].rsplit("```", 1)[0].strip()
        return json.loads(raw)
    except Exception as e:
        logger.error(f"Gemini gap scan error: {e}")
        return None


# ===== 工具函式 =====

def _fuzzy_match(text, valid_list):
    """簡單的模糊匹配"""
    text_lower = text.lower().strip()
    for item in valid_list:
        if item.lower() in text_lower or text_lower in item.lower():
            return item
    return None
