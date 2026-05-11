import os
import json
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

URL = "https://www.secretflying.com"
SEEN = Path("seen_urls.json")

REQUEST_HEADERS = {
    "User-Agent": "Mozilla/5.0 SecretFlyingMonitor/1.0"
}


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


def main():
    print("=== Secret Flying Monitor Start ===")

    seen = load_seen()
    print("通知済みURL数:", len(seen))

    try:
        res = requests.get(
            URL,
            headers=REQUEST_HEADERS,
            timeout=20,
        )
        print("Secret Flying status:", res.status_code)
        res.raise_for_status()

    except Exception as e:
        print("Secret Flying の取得に失敗しました:", e)
        raise

    soup = BeautifulSoup(res.text, "html.parser")

    article_count = 0
    matched_count = 0
    notified_count = 0

    for art in soup.select("article"):
        article_count += 1

        a = art.find("a", href=True)

        if not a:
            continue

        url = urljoin(URL, a["href"])

        if url in seen:
            print("重複のため除外:", url)
            continue

        title_original = art.get_text(" ", strip=True)
        title = title_original.lower()

        # 日本 → ヨーロッパのみ
        if "japan" not in title or "europe" not in title:
            continue

        matched_count += 1

        # クラス判定
        if "business class" in title:
            head = "🔥 ビジネス"
        elif "economy" in title:
            head = "✈️ エコノミー"
        else:
            continue

        message = (
            f"{head}\n"
            f"🇯🇵 日本 → 🇪🇺 ヨーロッパ\n"
            f"{title_original}\n"
            f"{url}"
        )

        print("通知対象:")
        print(message)

        notify(message)
        print("✅ LINE通知完了")

        seen.add(url)
        notified_count += 1

    save_seen(seen)

    print("取得記事数:", article_count)
    print("条件一致記事数:", matched_count)
    print("通知件数:", notified_count)
    print("=== Secret Flying Monitor End ===")


if __name__ == "__main__":
    main()
