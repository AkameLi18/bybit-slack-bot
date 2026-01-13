import os
import json
import time
import hmac
import hashlib
import requests
from websocket import WebSocketApp

BYBIT_API_KEY = os.getenv("BYBIT_API_KEY")
BYBIT_API_SECRET = os.getenv("BYBIT_API_SECRET")
SLACK_WEBHOOK_URL = os.getenv("SLACK_WEBHOOK_URL")
TESTNET = os.getenv("BYBIT_TESTNET", "false").lower() == "true"

WS_URL = (
    "wss://stream-testnet.bybit.com/v5/private"
    if TESTNET
    else "wss://stream.bybit.com/v5/private"
)

seen_exec_ids = set()

def sign_message(expires):
    return hmac.new(
        BYBIT_API_SECRET.encode(),
        f"GET/realtime{expires}".encode(),
        hashlib.sha256
    ).hexdigest()

def slack(text):
    requests.post(SLACK_WEBHOOK_URL, json={"text": text}, timeout=10)

def on_open(ws):
    expires = int(time.time() * 1000) + 10000
    sig = sign_message(expires)

    ws.send(json.dumps({
        "op": "auth",
        "args": [BYBIT_API_KEY, expires, sig]
    }))

    ws.send(json.dumps({
        "op": "subscribe",
        "args": ["execution"]
    }))

    slack("🟢 Bybit 交易通知機器人已啟動")

def on_message(ws, message):
    data = json.loads(message)
    if data.get("topic") != "execution":
        return

    for e in data.get("data", []):
        if e["execId"] in seen_exec_ids:
            continue
        seen_exec_ids.add(e["execId"])

        msg = (
            "📌 *新成交*\n"
            f"交易對：{e['symbol']}\n"
            f"方向：{e['side']}\n"
            f"價格：{e['execPrice']}\n"
            f"數量：{e['execQty']}"
        )
        slack(msg)

def on_close(ws, *_):
    time.sleep(5)
    start()

def start():
    ws = WebSocketApp(
        WS_URL,
        on_open=on_open,
        on_message=on_message,
        on_close=on_close
    )
    ws.run_forever(ping_interval=20)

if __name__ == "__main__":
    start()
