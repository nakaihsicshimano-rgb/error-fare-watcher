import os
import json
import re
import requests
from bs4 import BeautifulSoup
from pathlib import Path
from linebot.v3.messaging import (
    Configuration, ApiClient, MessagingApi,
    PushMessageRequest, TextMessage
)

LINE_TOKEN = os.environ["LINE_CHANNEL_ACCESS_TOKEN"]
LINE_USER_ID = os.environ["LINE_USER_ID"]

URL = "https://www.secretflying.com"
SEEN = Path("seen_urls.json")

def load_seen():
    return set(json.loads(SEEN.read_text())) if SEEN.exists() else set()

def save_seen(s):
    SEEN.write_text(json.dumps(sorted(s)))

def notify(text):
    conf = Configuration(access_token=LINE_TOKEN)
    with ApiClient(conf) as api:
        MessagingApi(api).push_message(
            PushMessageRequest(
                to=LINE_USER_ID,
                messages=[TextMessage(text=text)]
            )
        )

def main():
    seen = load_seen()
    html = requests.get(URL, timeout=20).text
    soup = BeautifulSoup(html, "html.parser")

    for art in soup.select("article"):
        a = art.find("a", href=True)
        if not a:
            continue

        url = a["href"]
        if url in seen:
            continue

        title = art.get_text(" ", strip=True).lower()

        # 日本 → ヨーロッパのみ
        if "japan" not in title or "europe" not in title:
            continue

        # クラス判定
        if "business class" in title:
            head = "🔥 ビジネス"
        elif "economy" in title:
            head = "✈️ エコノミー"
        else:
            continue

        notify(
            f"{head}\n"
            f"🇯🇵 日本 → 🇪🇺 ヨーロッパ\n"
            f"{url}"
        )

        seen.add(url)

    save_seen(seen)

if __name__ == "__main__":
    main()
