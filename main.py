import os
from linebot.v3.messaging import (
    Configuration, ApiClient, MessagingApi,
    PushMessageRequest, TextMessage
)

def main():
    conf = Configuration(
        access_token=os.environ["LINE_CHANNEL_ACCESS_TOKEN"]
    )
    with ApiClient(conf) as api:
        MessagingApi(api).push_message(
            PushMessageRequest(
                to=os.environ["LINE_USER_ID"],
                messages=[
                    TextMessage(
                        text="✅ テスト成功：Secret Flying Monitor は正常に動いています"
                    )
                ]
            )
        )

if __name__ == "__main__":
    main()
