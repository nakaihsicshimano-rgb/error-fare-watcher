import os
import json
import re
import requests
from bs4 import BeautifulSoup
from pathlib import Path
from urllib.parse import urljoin
from linebot.v3.messaging import (
    Configuration,
    ApiClient,
    MessagingApi,
    PushMessageRequest,
    TextMessage,
)

LINE_TOKEN = os.environ["LINE_CHANNEL_ACCESS_TOKEN"]
LINE_USER_ID = os.environ["LINE_USER_ID"]

MONITOR_URLS = [
    "https://www.secretflying.com/",
    "https://www.secretflying.com/errorfare/",
    "https://www.secretflying.com/posts/category/error-fare/",
    "https://www.secretflying.com/east-asia-flight-deals/",
]

SEEN = Path("seen_urls.json")

REQUEST_HEADERS = {
    "User-Agent": "Mozilla/5.0 SecretFlyingMonitor/1.0"
}

# =========================
# 日本発判定キーワード
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
    "chubu",
    "fukuoka",
    "fuk",
    "sapporo",
    "cts",
    "okinawa",
    "naha",
    "oka",
]

# =========================
# ヨーロッパ方面判定キーワード
# 仲井さん向けに、ミュンヘン・フランクフルト・イスタンブールを厚めに設定
# =========================
EUROPE_DEST_KEYWORDS = [
    "europe",

    # Germany
    "germany",
    "munich",
    "muc",
    "frankfurt",
    "fra",
    "berlin",
    "ber",
    "dusseldorf",
    "düsseldorf",
    "dus",
    "hamburg",
    "ham",

    # Turkey / Istanbul
    "turkey",
    "turkiye",
    "türkiye",
    "istanbul",
    "ist",
    "sabiha",
    "saw",

    # France
    "france",
    "paris",
    "cdg",
    "ory",
    "nice",
    "nce",

    # UK / Ireland
    "uk",
    "united kingdom",
    "england",
    "london",
    "lhr",
    "lgw",
    "manchester",
    "man",
    "edinburgh",
    "edi",
    "ireland",
    "dublin",
    "dub",

    # Italy
    "italy",
    "rome",
    "fco",
    "milan",
    "mxp",
    "venice",
    "vce",

    # Spain / Portugal
    "spain",
    "madrid",
    "mad",
    "barcelona",
    "bcn",
    "portugal",
    "lisbon",
    "lis",
    "porto",
    "opo",

    # Netherlands / Belgium / Switzerland / Austria
    "netherlands",
    "amsterdam",
    "ams",
    "belgium",
    "brussels",
    "bru",
    "switzerland",
    "zurich",
    "zrh",
    "geneva",
    "gva",
    "austria",
    "vienna",
    "vie",

    # Northern / Eastern Europe
    "denmark",
    "copenhagen",
    "cph",
    "sweden",
    "stockholm",
    "arn",
    "norway",
    "oslo",
    "osl",
    "finland",
    "helsinki",
    "hel",
    "poland",
    "warsaw",
    "waw",
    "gdansk",
    "gdn",
    "czech",
    "prague",
    "prg",
    "hungary",
    "budapest",
    "bud",
    "greece",
    "athens",
    "ath",
]

# 通知文に補足表示するためのキーワード
PREMIUM_KEYWORDS = [
    "business class",
    "premium economy",
    "first class",
    "lie-flat",
    "lie flat",
]

ERROR_FARE_KEYWORDS = [
    "error fare",
    "mistake fare",
    "glitch",
    "ota glitch",
    "fuel dump",
    "self-dump",
    "self dump",
]


def normalize(text):
    """
    改行や余分な空白を1つのスペースに整える。
    """
    return re.sub(r"\s+", " ", text or "").strip()


def load_seen():
    """
    通知済みURLを読み込む。
    ファイルが無い場合、空集合として扱う。
    """
    if not SEEN.exists():
        print("seen_urls.json が見つかりません。新規扱いにします。")
        return set()

    try:
        text = SEEN.read_text(encoding="utf-8").strip()

        if not text:
            print("seen_urls.json が空です。新規扱いにします。")
            return set()

        data = json.loads(text)

        if isinstance(data, list):
            return set(str(url) for url in data)

        print("seen_urls.json の形式が不正です。新規扱いにします。")
        return set()

    except Exception as e:
        print("seen_urls.json の読み込みに失敗しました:", e)
        return set()


def save_seen(seen):
    """
    通知済みURLを保存する。
    """
    SEEN.write_text(
        json.dumps(sorted(seen), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def notify(text):
    """
    LINEへPush通知する。
    """
    conf = Configuration(access_token=LINE_TOKEN)

    with ApiClient(conf) as api:
        MessagingApi(api).push_message(
            PushMessageRequest(
                to=LINE_USER_ID,
                messages=[
                    TextMessage(text=text)
                ],
            )
        )


def contains_any(text, keywords):
    """
    指定キーワードのいずれかが含まれるか判定する。
    """
    text = text.lower()

    return any(keyword.lower() in text for keyword in keywords)


def first_keyword_position(text, keywords):
    """
    指定キーワード群のうち、最初に出現する位置を返す。
    見つからない場合は None。
    """
    text = text.lower()
    positions = []

    for keyword in keywords:
        pos = text.find(keyword.lower())
        if pos >= 0:
            positions.append(pos)

    if not positions:
        return None

    return min(positions)


def cleanup_route_text(text):
    """
    ルート抽出時に余分な前置き表現を取り除く。
    """
    text = normalize(text.lower())

    prefixes = [
        "error fare",
        "cheap flights",
        "non-stop flights",
        "nonstop flights",
        "non-stop",
        "nonstop",
        "business class",
        "premium economy",
        "first class",
        "summer",
        "xmas",
        "new year",
        "roundtrip",
        "one-way",
    ]

    for prefix in prefixes:
        text = text.replace(prefix, " ")

    text = normalize(text)
    text = text.strip(" :-–—|")

    return text


def cut_destination_text(text):
    """
    destination側に価格や航空会社情報が続く場合、ルート部分だけに近づける。
    """
    cut_markers = [
        " for only ",
        " from only ",
        " with ",
        " roundtrip",
        " one-way",
        " return",
        " in ",
        " during ",
        " available",
    ]

    lowered = text.lower()
    cut_positions = []

    for marker in cut_markers:
        pos = lowered.find(marker)
        if pos >= 0:
            cut_positions.append(pos)

    if cut_positions:
        text = text[:min(cut_positions)]

    return normalize(text)


def parse_route_segments(title):
    """
    Secret Flyingの記事タイトルから origin / destination をざっくり抽出する。

    対応イメージ：
    - Tokyo, Japan to Frankfurt, Germany for only ...
    - Non-stop from Osaka, Japan to Paris, France for only ...
    - Cheap flights from Tokyo to Istanbul ...
    """
    text = normalize(title.lower())

    origin = None
    destination = None

    # パターン1：from A to B
    from_to_match = re.search(r"\bfrom\s+(.+?)\s+to\s+(.+)", text)

    if from_to_match:
        origin = cleanup_route_text(from_to_match.group(1))
        destination = cut_destination_text(from_to_match.group(2))
        destination = cleanup_route_text(destination)

        return {
            "origin": origin,
            "destination": destination,
        }

    # パターン2：A to B
    # 例：Tokyo, Japan to Frankfurt, Germany for only ...
    if " to " in text:
        parts = text.split(" to ", 1)

        origin = cleanup_route_text(parts[0])
        destination = cut_destination_text(parts[1])
        destination = cleanup_route_text(destination)

        return {
            "origin": origin,
            "destination": destination,
        }

    return {
        "origin": None,
        "destination": None,
    }


def is_japan_to_europe(title):
    """
    日本発ヨーロッパ行きかどうかを判定する。

    原則：
    - origin側に日本系キーワードがある
    - destination側にヨーロッパ系キーワードがある

    ただし、ルート抽出がうまくいかないタイトルに備えて、
    日本系キーワードがヨーロッパ系キーワードより前に出ている場合も拾う。
    """
    title_lower = normalize(title.lower())

    route = parse_route_segments(title_lower)
    origin = route["origin"]
    destination = route["destination"]

    print("route parse:", {"origin": origin, "destination": destination})

    # 方向が取れる場合：日本発 → 欧州着のみ通知
    if origin and destination:
        origin_is_japan = contains_any(origin, JAPAN_ORIGIN_KEYWORDS)
        destination_is_europe = contains_any(destination, EUROPE_DEST_KEYWORDS)

        if origin_is_japan and destination_is_europe:
            return True

        return False

    # 方向が取れない場合：日本系キーワードが欧州系キーワードより先に出ている場合のみ拾う
    japan_pos = first_keyword_position(title_lower, JAPAN_ORIGIN_KEYWORDS)
    europe_pos = first_keyword_position(title_lower, EUROPE_DEST_KEYWORDS)

    if japan_pos is not None and europe_pos is not None and japan_pos < europe_pos:
        return True

    return False


def get_match_reason(title):
    """
    通知文に表示する判定理由を作る。
    """
    title_lower = title.lower()

    reasons = []

    if contains_any(title_lower, ERROR_FARE_KEYWORDS):
        reasons.append("エラー運賃系キーワードあり")

    if contains_any(title_lower, PREMIUM_KEYWORDS):
        reasons.append("上位クラス系キーワードあり")

    route = parse_route_segments(title)
    origin = route["origin"]
    destination = route["destination"]

    if origin:
        reasons.append(f"出発地判定：{origin}")

    if destination:
        reasons.append(f"目的地判定：{destination}")

    if not reasons:
        reasons.append("日本発ヨーロッパ候補")

    return " / ".join(reasons)


def fetch_articles_from_url(page_url):
    """
    指定URLから記事候補を取得する。
    """
    print("Fetching:", page_url)

    try:
        res = requests.get(
            page_url,
            headers=REQUEST_HEADERS,
            timeout=20,
        )
        print("Status:", res.status_code, page_url)
        res.raise_for_status()

    except Exception as e:
        print("ページ取得に失敗しました:", page_url, e)
        return []

    soup = BeautifulSoup(res.text, "html.parser")

    articles = []
    seen_on_page = set()

    for art in soup.select("article"):
        a = art.find("a", href=True)

        if not a:
            continue

        url = urljoin(page_url, a["href"])

        if url in seen_on_page:
            continue

        seen_on_page.add(url)

        title_original = normalize(art.get_text(" ", strip=True))

        if not title_original:
            continue

        articles.append(
            {
                "title": title_original,
                "url": url,
                "source_page": page_url,
            }
        )

    return articles


def build_message(article):
    """
    LINE通知文を作成する。
    """
    title = article["title"]
    url = article["url"]
    source_page = article["source_page"]

    reason = get_match_reason(title)

    message = (
        "🔥 Sランク：即確認\n"
        "🇯🇵 日本発 → 🇪🇺 ヨーロッパ候補\n"
        "\n"
        f"{title}\n"
        "\n"
        f"判定理由：{reason}\n"
        f"監視元：{source_page}\n"
        "\n"
        f"{url}"
    )

    return message


def main():
    print("=== Secret Flying Monitor Start ===")

    seen = load_seen()
    print("通知済みURL数:", len(seen))

    all_articles = []
    collected_urls = set()

    for page_url in MONITOR_URLS:
        articles = fetch_articles_from_url(page_url)

        for article in articles:
            if article["url"] in collected_urls:
                continue

            collected_urls.add(article["url"])
            all_articles.append(article)

    print("取得記事数:", len(all_articles))

    matched_count = 0
    notified_count = 0

    for article in all_articles:
        title = article["title"]
        url = article["url"]

        if url in seen:
            print("重複のため除外:", url)
            continue

        print("確認中:", title)

        # Sランク：日本発ヨーロッパ候補のみ通知
        if not is_japan_to_europe(title):
            continue

        matched_count += 1

        message = build_message(article)

        print("通知対象:")
        print(message)

        notify(message)
        print("✅ LINE通知完了")

        seen.add(url)
        notified_count += 1

    save_seen(seen)

    print("条件一致記事数:", matched_count)
    print("通知件数:", notified_count)
    print("=== Secret Flying Monitor End ===")


if __name__ == "__main__":
    main()
