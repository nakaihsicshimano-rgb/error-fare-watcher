import json
import os
import re
import time
import html
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Tuple
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup


# =========================
# 基本設定
# =========================

JST = timezone(timedelta(hours=9))

MONITOR_URLS = [
    "https://www.secretflying.com/",
    "https://www.secretflying.com/errorfare/",
    "https://www.secretflying.com/posts/category/error-fare/",
    "https://www.secretflying.com/east-asia-flight-deals/",
]

SEEN_URLS_FILE = "seen_urls.json"

HTTP_TIMEOUT_SECONDS = 20
MAX_RETRY_COUNT = 2
RETRY_WAIT_SECONDS = 3

MAX_ARTICLE_AGE_DAYS = 14

LINE_CHANNEL_ACCESS_TOKEN = os.getenv("LINE_CHANNEL_ACCESS_TOKEN", "").strip()
LINE_USER_ID = os.getenv("LINE_USER_ID", "").strip()

USER_AGENT = "Mozilla/5.0 SecretFlyingMonitor/1.1"


# =========================
# 判定キーワード
# =========================

JAPAN_ORIGIN_KEYWORDS = [
    "japan",
    "tokyo",
    "osaka",
    "kansai",
    "kix",
    "narita",
    "nrt",
    "haneda",
    "hnd",
    "nagoya",
    "ngo",
    "fukuoka",
    "fuk",
    "sapporo",
    "cts",
    "okinawa",
    "oka",
]

S_RANK_DESTINATION_KEYWORDS = [
    # Europe
    "europe",
    "uk",
    "united kingdom",
    "london",
    "england",
    "france",
    "paris",
    "germany",
    "frankfurt",
    "munich",
    "italy",
    "rome",
    "milan",
    "spain",
    "madrid",
    "barcelona",
    "netherlands",
    "amsterdam",
    "switzerland",
    "zurich",
    "austria",
    "vienna",
    "turkey",
    "istanbul",
    "poland",
    "warsaw",
    "ireland",
    "dublin",
    "portugal",
    "lisbon",
    "denmark",
    "copenhagen",
    "finland",
    "helsinki",
    "norway",
    "oslo",
    "sweden",
    "stockholm",
    "greece",
    "athens",
    "belgium",
    "brussels",
    "czech",
    "prague",
    "hungary",
    "budapest",

    # North America
    "usa",
    "united states",
    "america",
    "new york",
    "los angeles",
    "san francisco",
    "seattle",
    "chicago",
    "boston",
    "washington",
    "canada",
    "vancouver",
    "toronto",
    "montreal",

    # Middle East
    "dubai",
    "abu dhabi",
    "uae",
    "doha",
    "qatar",
    "middle east",
]

A_RANK_DESTINATION_KEYWORDS = [
    "singapore",
    "bangkok",
    "thailand",
    "kuala lumpur",
    "malaysia",
    "taipei",
    "taiwan",
    "hong kong",
    "seoul",
    "korea",
    "manila",
    "philippines",
    "jakarta",
    "indonesia",
    "bali",
    "vietnam",
    "ho chi minh",
    "hanoi",
    "delhi",
    "india",
    "asia",
    "east asia",
    "southeast asia",
]

EXCLUDE_TITLE_KEYWORDS = [
    "announcement",
    "membership",
    "premium",
    "deal locked",
    "guide",
    "things to do",
    "best things",
    "hotel",
    "hotels",
    "credit card",
    "travel guide",
    "complete guide",
]


# =========================
# データ構造
# =========================

@dataclass
class Article:
    title: str
    url: str
    source_url: str
    text: str
    date: Optional[datetime] = None


@dataclass
class Candidate:
    article: Article
    origin: str
    destination: str
    price_text: Optional[str]
    rank: str
    rank_reason: str


# =========================
# 共通関数
# =========================

def normalize_text(text: Optional[str]) -> str:
    if text is None:
        return ""
    text = html.unescape(text)
    text = text.replace("\xa0", " ")
    text = re.sub(r"\s+", " ", text)
    return text.strip().lower()


def contains_any(text: str, keywords: List[str]) -> bool:
    text = normalize_text(text)
    return any(keyword in text for keyword in keywords)


def now_jst() -> datetime:
    return datetime.now(JST)


# =========================
# 履歴管理
# =========================

def load_seen_urls() -> List[str]:
    if not os.path.exists(SEEN_URLS_FILE):
        return []

    try:
        with open(SEEN_URLS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)

        if isinstance(data, list):
            return [str(x) for x in data]

        if isinstance(data, dict) and "seen_urls" in data:
            return [str(x) for x in data["seen_urls"]]

        print("seen_urls.json の形式が想定外です。空として扱います。")
        return []

    except Exception as e:
        print(f"seen_urls.json の読み込みに失敗しました: {e}")
        return []


def save_seen_urls(seen_urls: List[str]) -> None:
    unique_urls = sorted(set(seen_urls))
    with open(SEEN_URLS_FILE, "w", encoding="utf-8") as f:
        json.dump(unique_urls, f, ensure_ascii=False, indent=2)


# =========================
# HTTP取得
# =========================

def fetch_url(url: str) -> Optional[str]:
    headers = {
        "User-Agent": USER_AGENT,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    }

    for attempt in range(1, MAX_RETRY_COUNT + 2):
        try:
            print(f"Fetching: {url}")
            response = requests.get(url, headers=headers, timeout=HTTP_TIMEOUT_SECONDS)
            print(f"Status: {response.status_code} {url}")

            if response.status_code == 200:
                return response.text

            print(f"HTTP status が200ではありません: {response.status_code}")

        except Exception as e:
            print(f"取得失敗 attempt={attempt}: {url} / {e}")

        if attempt <= MAX_RETRY_COUNT:
            time.sleep(RETRY_WAIT_SECONDS)

    return None


# =========================
# 記事抽出
# =========================

def is_secretflying_article_url(url: str) -> bool:
    parsed = urlparse(url)

    if "secretflying.com" not in parsed.netloc:
        return False

    path = parsed.path.strip("/").lower()

    if not path:
        return False

    excluded_path_parts = [
        "category",
        "tag",
        "page",
        "wp-content",
        "privacy",
        "terms",
        "contact",
        "about",
        "membership",
        "premium",
    ]

    if any(part in path for part in excluded_path_parts):
        return False

    return True


def parse_article_date(text: str) -> Optional[datetime]:
    month_names = (
        "January|February|March|April|May|June|July|August|September|October|November|December"
    )

    pattern = rf"\b({month_names})\s+(\d{{1,2}}),\s+(\d{{4}})\b"
    m = re.search(pattern, text, flags=re.IGNORECASE)

    if not m:
        return None

    date_text = m.group(0)

    try:
        dt = datetime.strptime(date_text, "%B %d, %Y")
        return dt.replace(tzinfo=JST)
    except Exception:
        return None


def is_recent_article(article_date: Optional[datetime]) -> bool:
    if article_date is None:
        # 日付が取れない場合は、初期版では除外しすぎを避けるため通す
        return True

    threshold = now_jst() - timedelta(days=MAX_ARTICLE_AGE_DAYS)
    return article_date >= threshold


def extract_articles_from_html(source_url: str, html_text: str) -> List[Article]:
    soup = BeautifulSoup(html_text, "html.parser")
    articles: List[Article] = []
    seen = set()

    # h1/h2/h3配下のリンクを優先
    for heading in soup.find_all(["h1", "h2", "h3"]):
        a = heading.find("a", href=True)
        if not a:
            continue

        title = a.get_text(" ", strip=True)
        href = urljoin(source_url, a["href"])

        if not title or not is_secretflying_article_url(href):
            continue

        if href in seen:
            continue

        parent_text = ""
        parent = heading.find_parent()
        if parent:
            parent_text = parent.get_text(" ", strip=True)

        combined_text = f"{title} {parent_text}".strip()
        article_date = parse_article_date(combined_text)

        article = Article(
            title=html.unescape(title).strip(),
            url=href,
            source_url=source_url,
            text=html.unescape(combined_text).strip(),
            date=article_date,
        )

        articles.append(article)
        seen.add(href)

    # hタグで拾えない場合に備えて通常リンクも見る
    for a in soup.find_all("a", href=True):
        title = a.get_text(" ", strip=True)
        href = urljoin(source_url, a["href"])

        if not title or len(title) < 15:
            continue

        if not is_secretflying_article_url(href):
            continue

        if href in seen:
            continue

        parent = a.find_parent()
        parent_text = parent.get_text(" ", strip=True) if parent else title
        combined_text = f"{title} {parent_text}".strip()
        article_date = parse_article_date(combined_text)

        article = Article(
            title=html.unescape(title).strip(),
            url=href,
            source_url=source_url,
            text=html.unescape(combined_text).strip(),
            date=article_date,
        )

        articles.append(article)
        seen.add(href)

    return articles


def collect_articles() -> List[Article]:
    all_articles: List[Article] = []
    seen_urls = set()

    for url in MONITOR_URLS:
        html_text = fetch_url(url)
        if not html_text:
            continue

        articles = extract_articles_from_html(url, html_text)

        for article in articles:
            if article.url not in seen_urls:
                all_articles.append(article)
                seen_urls.add(article.url)

    return all_articles


# =========================
# 航空券記事判定
# =========================

def is_probable_flight_article(article: Article) -> Tuple[bool, str]:
    text = normalize_text(f"{article.title} {article.text}")

    if contains_any(text, EXCLUDE_TITLE_KEYWORDS):
        return False, "航空券記事ではない可能性が高い除外キーワードに一致"

    required_keywords = [
        " to ",
        " from ",
        "roundtrip",
        "one-way",
        "non-stop",
        "flights",
        "flight",
        "error fare",
        "business class",
    ]

    if not any(keyword in text for keyword in required_keywords):
        return False, "航空券記事らしいキーワードが不足"

    return True, ""


# =========================
# ルート・価格解析
# =========================

def clean_route_part(text: str) -> str:
    text = html.unescape(text)
    text = normalize_text(text)

    remove_prefixes = [
        "🔥",
        "⚠️",
        "😲",
        "crazy hot",
        "error fare",
        "business class",
        "summer",
        "xmas",
        "new year",
        "open-jaw",
        "non-stop",
        "cheap flights from",
        "flights from",
        "flight from",
        "from",
    ]

    for prefix in remove_prefixes:
        text = text.replace(prefix, " ")

    text = re.sub(r"\b(january|february|march|april|may|june|july|august|september|october|november|december)\s+\d{1,2},\s+\d{4}\b", " ", text)
    text = re.sub(r"\s+", " ", text)
    text = text.strip(" -:,.()[]")

    return text


def parse_route(article: Article) -> Optional[Dict[str, str]]:
    text = normalize_text(article.title)

    # 日付を除去
    text = re.sub(
        r"\b(january|february|march|april|may|june|july|august|september|october|november|december)\s+\d{1,2},\s+\d{4}\b",
        " ",
        text,
    )
    text = re.sub(r"\s+", " ", text).strip()

    patterns = [
        # from Tokyo, Japan to Singapore for only ...
        r"(?:non-stop\s+)?from\s+(.+?)\s+to\s+(.+?)(?:\s+for\s+only|\s+from\s+only|\s+for\s+|\s+from\s+|$)",

        # Tokyo, Japan to Singapore for only ...
        r"^(.+?)\s+to\s+(.+?)(?:\s+for\s+only|\s+from\s+only|\s+for\s+|\s+from\s+|$)",
    ]

    for pattern in patterns:
        m = re.search(pattern, text, flags=re.IGNORECASE)
        if not m:
            continue

        origin = clean_route_part(m.group(1))
        destination = clean_route_part(m.group(2))

        if origin and destination and origin != destination:
            return {
                "origin": origin,
                "destination": destination,
            }

    return None


def extract_price_text(text: str) -> Optional[str]:
    text = html.unescape(text)

    patterns = [
        r"(?:only\s+)?([€$£¥]\s?[0-9,]+(?:\s?(?:usd|cad|aud|eur|gbp|jpy))?(?:\s?(?:one-way|roundtrip))?)",
        r"from\s+only\s+([€$£¥]\s?[0-9,]+(?:\s?(?:usd|cad|aud|eur|gbp|jpy))?(?:\s?(?:one-way|roundtrip))?)",
    ]

    for pattern in patterns:
        m = re.search(pattern, text, flags=re.IGNORECASE)
        if m:
            return re.sub(r"\s+", " ", m.group(1)).strip()

    return None


# =========================
# 日本発判定・ランク判定
# =========================

def is_japan_origin(origin_text: str) -> bool:
    return contains_any(origin_text, JAPAN_ORIGIN_KEYWORDS)


def classify_destination_rank(destination_text: str) -> Tuple[str, str]:
    destination_text = normalize_text(destination_text)

    if contains_any(destination_text, S_RANK_DESTINATION_KEYWORDS):
        return "S", "日本発の長距離・欧州/北米/中東方面候補"

    if contains_any(destination_text, A_RANK_DESTINATION_KEYWORDS):
        return "A", "日本発のアジア方面候補"

    return "B", "日本発のその他方面候補"


def build_candidate(article: Article) -> Optional[Candidate]:
    print(f"確認中: {article.title}")

    if not is_recent_article(article.date):
        date_text = article.date.strftime("%Y-%m-%d") if article.date else "unknown"
        print(f"除外理由: 古い記事のため除外 date={date_text}")
        return None

    is_flight, reason = is_probable_flight_article(article)
    if not is_flight:
        print(f"除外理由: {reason}")
        return None

    route = parse_route(article)
    print(f"route parse: {route}")

    if not route:
        print("除外理由: ルート解析不可")
        return None

    origin = route["origin"]
    destination = route["destination"]

    if not is_japan_origin(origin):
        print(f"日本発判定: false / origin={origin}")
        print("除外理由: 出発地が日本ではない")
        return None

    rank, rank_reason = classify_destination_rank(destination)
    price_text = extract_price_text(article.title) or extract_price_text(article.text)

    print(f"日本発判定: true / origin={origin}")
    print(f"通知ランク: {rank} / {rank_reason}")
    print(f"destination={destination}")

    return Candidate(
        article=article,
        origin=origin,
        destination=destination,
        price_text=price_text,
        rank=rank,
        rank_reason=rank_reason,
    )


# =========================
# LINE通知
# =========================

def build_line_message(candidate: Candidate) -> str:
    rank_emoji = {
        "S": "🔥",
        "A": "🇯🇵",
        "B": "🛫",
    }.get(candidate.rank, "🛫")

    header = f"{rank_emoji} {candidate.rank}ランク：日本発セール候補"

    price_line = f"価格：{candidate.price_text}" if candidate.price_text else "価格：記事内で確認"

    message = f"""{header}

{candidate.article.title}

{price_line}
出発地：{candidate.origin}
目的地：{candidate.destination}

{candidate.article.url}"""

    return message.strip()


def send_line_message(message: str) -> bool:
    if not LINE_CHANNEL_ACCESS_TOKEN:
        print("LINE_CHANNEL_ACCESS_TOKEN が設定されていません。")
        return False

    if not LINE_USER_ID:
        print("LINE_USER_ID が設定されていません。")
        return False

    url = "https://api.line.me/v2/bot/message/push"

    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {LINE_CHANNEL_ACCESS_TOKEN}",
    }

    payload = {
        "to": LINE_USER_ID,
        "messages": [
            {
                "type": "text",
                "text": message,
            }
        ],
    }

    for attempt in range(1, MAX_RETRY_COUNT + 2):
        try:
            response = requests.post(
                url,
                headers=headers,
                json=payload,
                timeout=HTTP_TIMEOUT_SECONDS,
            )

            print(f"LINE status: {response.status_code}")
            print(f"LINE response: {response.text}")

            if 200 <= response.status_code < 300:
                return True

        except Exception as e:
            print(f"LINE送信失敗 attempt={attempt}: {e}")

        if attempt <= MAX_RETRY_COUNT:
            time.sleep(RETRY_WAIT_SECONDS)

    return False


# =========================
# メイン処理
# =========================

def main() -> None:
    print("=== Secret Flying Monitor Start ===")
    print(f"実行日時 JST: {now_jst().strftime('%Y-%m-%d %H:%M:%S')}")

    seen_urls = load_seen_urls()
    print(f"通知済みURL数: {len(seen_urls)}")

    articles = collect_articles()
    print(f"取得記事数: {len(articles)}")

    candidates: List[Candidate] = []
    already_seen_count = 0

    for article in articles:
        if article.url in seen_urls:
            already_seen_count += 1
            print(f"通知済みのため除外: {article.url}")
            continue

        candidate = build_candidate(article)
        if candidate:
            candidates.append(candidate)

    print(f"通知済み除外数: {already_seen_count}")
    print(f"条件一致記事数: {len(candidates)}")
    print(f"通知件数: {len(candidates)}")

    if not candidates:
        print("通知対象なし")
        print("=== Secret Flying Monitor End ===")
        return

    newly_sent_urls: List[str] = []

    for candidate in candidates:
        message = build_line_message(candidate)
        print("通知文:")
        print(message)

        success = send_line_message(message)

        if success:
            newly_sent_urls.append(candidate.article.url)
        else:
            print("LINE送信失敗のため、以降の履歴更新は行いません。")
            print("=== Secret Flying Monitor End ===")
            return

    updated_seen_urls = sorted(set(seen_urls + newly_sent_urls))
    save_seen_urls(updated_seen_urls)

    print(f"seen_urls.json updated: +{len(newly_sent_urls)}")
    print("=== Secret Flying Monitor End ===")


if __name__ == "__main__":
    main()
