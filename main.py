import os
import json
import time
import hmac
import hashlib
import threading
import requests
from flask import Flask
from websocket import WebSocketApp

# ===== 環境變數 =====
BYBIT_API_KEY = os.getenv("BYBIT_API_KEY")
BYBIT_API_SECRET = os.getenv("BYBIT_API_SECRET")
SLACK_WEBHOOK_URL = os.getenv("SLACK_WEBHOOK_URL")
TESTNET = os.getenv("BYBIT_TESTNET", "false").lower() == "true"

WS_URL = (
    "wss://stream-testnet.bybit.com/v5/private"
    if TESTNET
    else "wss://stream.bybit.com/v5/private"
)

PORT = int(os.getenv("PORT", 8080))

# ===== 狀態 =====
seen_exec_ids = set()
started_notified = False

# ===== Flask（騙過 Railway 用）=====
app = Flask(__name__)

@app.route("/")
def health():
    return "ok"


# ===== 工具 =====
def sign_message(expires: int) -> str:
    return hmac.new(
        BYBIT_API_SECRET.encode(),
        f"GET/realtime{expires}".encode(),
        hashlib.sha256
    ).hexdigest()


def slack(text: str):
    try:
        requests.post(
            SLACK_WEBHOOK_URL,
            json={"text": text},
            timeout=10
        )
    except Exception as e:
        print("Slack error:", e)


# ===== WebSocket callbacks =====
def on_open(ws):
    global started_notified

    expires = int(time.time() * 1000) + 10_000
    sig = sign_message(expires)

    ws.send(json.dumps({
        "op": "auth",
        "args": [BYBIT_API_KEY, expires, sig]
    }))

    ws.send(json.dumps({
        "op": "subscribe",
        "args": ["execution"]
    }))

    if not started_notified:
        slack("🟢 Bybit 交易通知機器人已啟動")
        started_notified = True


def on_message(ws, message):
    try:
        data = json.loads(message)
    except json.JSONDecodeError:
        return

    if data.get("topic") != "execution":
        return

    for e in data.get("data", []):
        exec_id = e.get("execId")
        if not exec_id or exec_id in seen_exec_ids:
            continue

        seen_exec_ids.add(exec_id)

        msg = (
            "📌 *新成交*\n"
            f"交易對：{e.get('symbol')}\n"
            f"方向：{e.get('side')}\n"
            f"價格：{e.get('execPrice')}\n"
            f"數量：{e.get('execQty')}"
        )
        slack(msg)


def on_error(ws, error):
    print("WebSocket error:", error)


def on_close(ws, *_):
    print("WebSocket closed")


# ===== WebSocket 主循環（永不結束）=====
def run_ws_forever():
    while True:
        try:
            ws = WebSocketApp(
                WS_URL,
                on_open=on_open,
                on_message=on_message,
                on_error=on_error,
                on_close=on_close
            )
            ws.run_forever(ping_interval=20, ping_timeout=10)
        except Exception as e:
            print("WS crash:", e)

        time.sleep(5)


# ===== 進入點 =====
if __name__ == "__main__":
    # 背景執行 WebSocket bot
    threading.Thread(target=run_ws_forever, daemon=True).start()

    # HTTP Server（Railway 需要）
    app.run(host="0.0.0.0", port=PORT)
