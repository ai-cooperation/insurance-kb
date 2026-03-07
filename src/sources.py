"""
完整保險資訊來源清單 v3
策略：Google News RSS 為主力 + 直接爬蟲為精準補充
涵蓋：保險資訊來源.md 所有公司/地區/來源
"""
from urllib.parse import quote


def _gnews(query, lang="en", days=7):
    q = quote(f"{query} when:{days}d")
    return f"https://news.google.com/rss/search?q={q}&hl={lang}"


# ===== 第一層：Google News RSS =====

RSS_SOURCES = [
    # ── 全球/亞太 ──
    {
        "id": "gnews_insurance_asia",
        "name": "GNews: 亞洲保險產業",
        "url": _gnews("insurance asia pacific"),
        "region": "亞太", "type": "新聞聚合", "method": "rss",
    },
    {
        "id": "gnews_insurance_global",
        "name": "GNews: 全球保險產業",
        "url": _gnews("global insurance industry reinsurance"),
        "region": "全球", "type": "新聞聚合", "method": "rss",
    },
    {
        "id": "gnews_insurtech",
        "name": "GNews: InsurTech",
        "url": _gnews("insurtech insurance technology digital"),
        "region": "全球", "type": "新聞聚合", "method": "rss",
    },

    # ── 新加坡 ──
    {
        "id": "gnews_sg_companies_1",
        "name": "GNews: 新加坡保險公司 (1)",
        "url": _gnews("\"Great Eastern\" OR \"AIA Singapore\" OR \"Prudential Singapore\" OR \"Manulife Singapore\" OR Singlife OR \"Income Insurance\" insurance"),
        "region": "新加坡", "type": "新聞聚合", "method": "rss",
    },
    {
        "id": "gnews_sg_companies_2",
        "name": "GNews: 新加坡保險公司 (2)",
        "url": _gnews("\"HSBC Life Singapore\" OR \"Tokio Marine Singapore\" OR \"Utmost International\" insurance Singapore"),
        "region": "新加坡", "type": "新聞聚合", "method": "rss",
    },
    {
        "id": "gnews_sg_regulator",
        "name": "GNews: MAS 保險監管",
        "url": _gnews("MAS Singapore insurance regulation OR LIA Singapore"),
        "region": "新加坡", "type": "新聞聚合", "method": "rss",
    },

    # ── 香港 ──
    {
        "id": "gnews_hk_companies_1",
        "name": "GNews: 香港保險公司 (1)",
        "url": _gnews("\"AIA Hong Kong\" OR \"Manulife Hong Kong\" OR \"AXA Hong Kong\" OR \"FWD Hong Kong\" OR \"Sun Life Hong Kong\" insurance"),
        "region": "香港", "type": "新聞聚合", "method": "rss",
    },
    {
        "id": "gnews_hk_companies_2",
        "name": "GNews: 香港保險公司 (2)",
        "url": _gnews("\"HSBC Insurance\" Hong Kong OR \"China Life\" Hong Kong OR \"Prudential Hong Kong\" OR \"Bank of China Life\" OR CFTLife insurance"),
        "region": "香港", "type": "新聞聚合", "method": "rss",
    },
    {
        "id": "gnews_hk_regulator",
        "name": "GNews: 香港保監局",
        "url": _gnews("Hong Kong Insurance Authority regulation"),
        "region": "香港", "type": "新聞聚合", "method": "rss",
    },
    {
        "id": "gnews_hk_zh",
        "name": "GNews: 香港保險 (中文)",
        "url": _gnews("香港 保險 壽險 OR 安盛 OR 中銀人壽 OR 富衛 OR 周大福人壽", lang="zh-TW"),
        "region": "香港", "type": "新聞聚合", "method": "rss",
    },

    # ── 中國 ──
    {
        "id": "gnews_cn_companies_1",
        "name": "GNews: 中國保險公司 (1)",
        "url": _gnews("中国平安 OR 中国人寿 OR 中国太保 OR 新华保险 OR 众安在线", lang="zh-TW"),
        "region": "中國", "type": "新聞聚合", "method": "rss",
    },
    {
        "id": "gnews_cn_companies_2",
        "name": "GNews: 中國保險公司 (2)",
        "url": _gnews("中国人保 OR 国华人寿 OR 太平人寿 OR 泰康人寿 OR 中邮人寿 保险", lang="zh-TW"),
        "region": "中國", "type": "新聞聚合", "method": "rss",
    },
    {
        "id": "gnews_cn_industry",
        "name": "GNews: 中國保險產業",
        "url": _gnews("中国 保险 监管 OR 银保监 OR 金融监管", lang="zh-TW"),
        "region": "中國", "type": "新聞聚合", "method": "rss",
    },

    # ── 日本 ──
    {
        "id": "gnews_jp_companies_ja",
        "name": "GNews: 日本保險公司 (日文)",
        "url": _gnews("日本生命 OR 東京海上 OR 第一生命 OR 損保ジャパン OR 住友生命 OR 明治安田 OR かんぽ生命", lang="ja"),
        "region": "日本", "type": "新聞聚合", "method": "rss",
    },
    {
        "id": "gnews_jp_companies_2",
        "name": "GNews: 日本保險公司 (2)",
        "url": _gnews("朝日生命 OR FWDジャパン OR MS&AD 保険", lang="ja"),
        "region": "日本", "type": "新聞聚合", "method": "rss",
    },
    {
        "id": "gnews_jp_en",
        "name": "GNews: Japan Insurance (EN)",
        "url": _gnews("\"Nippon Life\" OR \"Tokio Marine\" OR \"Dai-ichi Life\" OR Sompo OR MS&AD OR \"Sumitomo Life\" OR \"Meiji Yasuda\" insurance Japan"),
        "region": "日本", "type": "新聞聚合", "method": "rss",
    },
    {
        "id": "gnews_jp_industry",
        "name": "GNews: 日本保險產業",
        "url": _gnews("保険 生命保険 損害保険", lang="ja"),
        "region": "日本", "type": "新聞聚合", "method": "rss",
    },
    {
        "id": "gnews_jp_mini",
        "name": "GNews: 日本少額短期保険",
        "url": _gnews("少額短期保険", lang="ja"),
        "region": "日本", "type": "新聞聚合", "method": "rss",
    },

    # ── 韓國 ──
    {
        "id": "gnews_kr_companies_1",
        "name": "GNews: 韓國保險公司 (1)",
        "url": _gnews("삼성생명 OR 한화생명 OR 교보생명 OR NH생명 OR 신한라이프", lang="ko"),
        "region": "韓國", "type": "新聞聚合", "method": "rss",
    },
    {
        "id": "gnews_kr_companies_2",
        "name": "GNews: 韓國保險公司 (2)",
        "url": _gnews("SK라이프 OR 동양생명 OR 흥국생명 OR 현대라이프 보험", lang="ko"),
        "region": "韓國", "type": "新聞聚合", "method": "rss",
    },
    {
        "id": "gnews_kr_en",
        "name": "GNews: Korea Insurance (EN)",
        "url": _gnews("\"Samsung Life\" OR \"Hanwha Life\" OR \"Kyobo Life\" OR \"Shinhan Life\" insurance Korea"),
        "region": "韓國", "type": "新聞聚合", "method": "rss",
    },
    {
        "id": "gnews_kr_industry",
        "name": "GNews: 韓國保險產業",
        "url": _gnews("보험 생명보험 손해보험 금융감독원", lang="ko"),
        "region": "韓國", "type": "新聞聚合", "method": "rss",
    },

    # ── 再保公司 ──
    {
        "id": "gnews_reinsurers",
        "name": "GNews: 全球再保公司",
        "url": _gnews("\"Swiss Re\" OR \"Munich Re\" OR SCOR OR \"Hannover Re\" OR \"Berkshire Hathaway\" reinsurance"),
        "region": "全球", "type": "新聞聚合", "method": "rss",
    },

    # ── 顧問公司 ──
    {
        "id": "gnews_consultants",
        "name": "GNews: 顧問公司保險洞察",
        "url": _gnews("McKinsey OR Deloitte OR EY OR KPMG OR BCG insurance industry"),
        "region": "全球", "type": "新聞聚合", "method": "rss",
    },

    # ── 信評機構 ──
    {
        "id": "gnews_ratings_1",
        "name": "GNews: 信評機構 (1)",
        "url": _gnews("\"AM Best\" OR \"Fitch Ratings\" OR \"Moody's\" OR \"S&P Global\" insurance rating"),
        "region": "全球", "type": "新聞聚合", "method": "rss",
    },
    {
        "id": "gnews_ratings_2",
        "name": "GNews: 信評機構 (2)",
        "url": _gnews("KBRA OR \"Japan Credit Rating\" OR JCR insurance rating"),
        "region": "全球", "type": "新聞聚合", "method": "rss",
    },

    # ── 主流財經新聞 ──
    {
        "id": "gnews_wsj_insurance",
        "name": "GNews: WSJ 保險",
        "url": _gnews("site:wsj.com insurance"),
        "region": "全球", "type": "新聞媒體", "method": "rss",
    },
    {
        "id": "gnews_bloomberg_insurance",
        "name": "GNews: Bloomberg 保險",
        "url": _gnews("site:bloomberg.com insurance"),
        "region": "全球", "type": "新聞媒體", "method": "rss",
    },
    {
        "id": "gnews_nyt_insurance",
        "name": "GNews: NYT 保險",
        "url": _gnews("site:nytimes.com insurance"),
        "region": "全球", "type": "新聞媒體", "method": "rss",
    },
    {
        "id": "gnews_sina_insurance",
        "name": "GNews: 新浪財金保險",
        "url": _gnews("新浪 保险 OR 壽險 OR 理財", lang="zh-TW"),
        "region": "中國", "type": "新聞媒體", "method": "rss",
    },

    # ── ESG/永續 ──
    {
        "id": "gnews_esg_insurance",
        "name": "GNews: ESG 保險",
        "url": _gnews("insurance ESG sustainability climate risk"),
        "region": "全球", "type": "新聞聚合", "method": "rss",
    },

    # ── 保險服務公司 ──
    {
        "id": "gnews_hive_insurance",
        "name": "GNews: Hive Insurance",
        "url": _gnews("Hive insurance services platform"),
        "region": "全球", "type": "新聞聚合", "method": "rss",
    },

    # ── Nature Neuroscience (腦科學與保險) ──
    {
        "id": "gnews_neuroscience_insurance",
        "name": "GNews: 腦科學與保險",
        "url": _gnews("neuroscience OR brain health insurance longevity"),
        "region": "全球", "type": "研究機構", "method": "rss",
    },

    # ── 台灣 (補充) ──
    {
        "id": "gnews_tw_insurance",
        "name": "GNews: 台灣保險",
        "url": _gnews("台灣 保險 壽險 OR 金管會 OR 保險局", lang="zh-TW"),
        "region": "台灣", "type": "新聞聚合", "method": "rss",
    },
]

# ===== 第二層：直接爬蟲（精準來源） =====

DIRECT_SOURCES = [
    # ── 專業新聞媒體 ──
    {
        "id": "air_news",
        "name": "Asia Insurance Review",
        "url": "https://www.asiainsurancereview.com",
        "region": "亞太", "type": "新聞媒體", "method": "http",
        "selectors": {"list": "a[href*='/News/ViewNewsLetterArticle/']", "title": "a", "link": "a@href"},
    },

    # ── 監管機構 ──
    {
        "id": "mas_media",
        "name": "MAS Media Releases",
        "url": "https://www.mas.gov.sg/news/media-releases",
        "region": "新加坡", "type": "監管機構", "method": "playwright",
        "selectors": {"list": "a[href*='/news/media-releases/']", "title": "a", "link": "a@href"},
    },
    {
        "id": "lia_sg",
        "name": "LIA Singapore News",
        "url": "https://www.lia.org.sg/news-room/",
        "region": "新加坡", "type": "監管機構", "method": "http",
        "selectors": {"list": ".news-item a, article a", "title": "a", "link": "a@href"},
    },
    {
        "id": "hkia_press",
        "name": "HKIA Press Releases",
        "url": "https://www.ia.org.hk/tc/infocenter/press_releases/",
        "region": "香港", "type": "監管機構", "method": "playwright",
        "selectors": {"list": "a[href*='press_releases']", "title": "a", "link": "a@href"},
    },
    {
        "id": "liaj_news",
        "name": "LIAJ Japan News",
        "url": "https://www.seiho.or.jp/english/",
        "region": "日本", "type": "監管機構", "method": "http",
        "selectors": {"list": ".news-list li a, .topic-list li a", "title": "a", "link": "a@href"},
    },

    # ── 保險公司官網 ──
    {
        "id": "greateastern",
        "name": "Great Eastern Life",
        "url": "https://www.greateasternlife.com/sg/en/about-us/media-centre.html",
        "region": "新加坡", "type": "保險公司", "method": "http",
        "selectors": {"list": "a[href*='media-centre/']", "title": "a", "link": "a@href"},
    },
    {
        "id": "aia_hk",
        "name": "AIA Hong Kong",
        "url": "https://www.aia.com.hk/en/about-aia/media-centre.html",
        "region": "香港", "type": "保險公司", "method": "http",
        "selectors": {"list": "a[href*='media-centre/']", "title": "a", "link": "a@href"},
    },
    {
        "id": "pingan",
        "name": "中國平安",
        "url": "https://www.pingan.cn/news/index.shtml",
        "region": "中國", "type": "保險公司", "method": "http",
        "selectors": {"list": "a[href*='/news/']", "title": "a", "link": "a@href"},
    },
    {
        "id": "sompo_hd",
        "name": "Sompo Holdings",
        "url": "https://www.sompo-hd.com/en/news/",
        "region": "日本", "type": "保險公司", "method": "http",
        "selectors": {"list": "a[href*='/news/']", "title": "a", "link": "a@href"},
    },

    # ── 再保公司 ──
    {
        "id": "swissre_media",
        "name": "Swiss Re Media",
        "url": "https://www.swissre.com/media/news-releases.html",
        "region": "全球", "type": "再保公司", "method": "playwright",
        "selectors": {"list": "a[href*='news-releases']", "title": "a", "link": "a@href"},
    },
    {
        "id": "munichre_news",
        "name": "Munich Re News",
        "url": "https://www.munichre.com/en/company/media-relations/media-information-and-corporate-news.html",
        "region": "全球", "type": "再保公司", "method": "http",
        "selectors": {"list": "a[href*='media-information']", "title": "a", "link": "a@href"},
    },
]


# ===== 合併 =====
SOURCES = RSS_SOURCES + DIRECT_SOURCES


# 分類定義
CATEGORIES = {
    "監管動態": ["法規更新", "監理政策", "合規要求", "裁罰案例"],
    "產品創新": ["新商品", "數位保險", "健康險", "投資型商品", "退休金"],
    "市場趨勢": ["併購", "市占率", "財報分析", "市場預測", "IPO"],
    "科技應用": ["InsurTech", "AI應用", "數位轉型", "區塊鏈", "大數據"],
    "再保市場": ["費率趨勢", "巨災風險", "再保交易", "ILS"],
    "ESG永續": ["永續投資", "氣候風險", "綠色保險", "社會責任"],
    "消費者保護": ["理賠爭議", "申訴統計", "公平待客", "資訊揭露"],
    "人才與組織": ["人事異動", "培訓發展", "組織改造"],
}

REGIONS = ["新加坡", "香港", "中國", "日本", "韓國", "台灣", "美國", "歐洲", "亞太", "全球"]


def get_sources_by_method(method):
    return [s for s in SOURCES if s["method"] == method]


def get_sources_by_region(region):
    return [s for s in SOURCES if s["region"] == region]


def get_sources_by_type(source_type):
    return [s for s in SOURCES if s["type"] == source_type]
