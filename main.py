import os
import json
import time
import hmac
import hashlib
import threading
import requests
from flask import Flask
from websocket import WebSocketApp
from collections import deque

# ==========================================
# 1. 環境變數設定
# ==========================================
BYBIT_API_KEY = os.getenv("BYBIT_API_KEY")
BYBIT_API_SECRET = os.getenv("BYBIT_API_SECRET")
SLACK_WEBHOOK_URL = os.getenv("SLACK_WEBHOOK_URL")
PORT = int(os.getenv("PORT", 8080))
TESTNET = os.getenv("BYBIT_TESTNET", "false").lower() == "true"

WS_URL = (
    "wss://stream-testnet.bybit.com/v5/private"
    if TESTNET
    else "wss://stream.bybit.com/v5/private"
)

# ==========================================
# 2. 全域狀態變數
# ==========================================
seen_exec_ids = deque(maxlen=1000)
startup_notified = False

# ==========================================
# 3. Flask 服務
# ==========================================
app = Flask(__name__)

@app.route("/")
@app.route("/health")
def health():
    return "ok", 200

# ==========================================
# 4. 工具函式
# ==========================================
def sign_message(expires: int) -> str:
    return hmac.new(
        BYBIT_API_SECRET.encode(),
        f"GET/realtime{expires}".encode(),
        hashlib.sha256
    ).hexdigest()

def slack(payload: dict):
    try:
        requests.post(SLACK_WEBHOOK_URL, json=payload, timeout=10)
    except Exception as e:
        print(f"Slack發送失敗: {e}")

# ==========================================
# 5. WebSocket 事件處理
# ==========================================
def on_open(ws):
    global startup_notified
    print("WebSocket 連線成功，正在進行認證...")

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

    if not startup_notified:
        env_name = "Testnet (測試網)" if TESTNET else "Mainnet (正式網)"
        slack({"text": f"🟢 Bybit 監控機器人已啟動 - {env_name}"})
        startup_notified = True


def on_message(ws, message):
    try:
        data = json.loads(message)
    except json.JSONDecodeError:
        return

    # Auth 回應
    if data.get("op") == "auth":
        if data.get("success"):
            print("Auth 認證成功")
        else:
            print(f"Auth 認證失敗: {data}")
        return

    if data.get("topic") != "execution":
        return

    for e in data.get("data", []):

        # ✅ 1️⃣ 過濾 Funding / ADL / Delivery
        if e.get("execType") != "Trade":
            continue

        # ✅ 2️⃣ 過濾 0 數量異常
        if e.get("execQty") in ("0", 0, None):
            continue

        exec_id = e.get("execId")
        if not exec_id or exec_id in seen_exec_ids:
            continue
        seen_exec_ids.append(exec_id)

        symbol = e.get('symbol')
        side = e.get('side')
        price = e.get('execPrice')
        qty = e.get('execQty')

        is_buy = side.lower() == "buy"
        emoji = "🟢" if is_buy else "🔴"
        color = "#36a64f" if is_buy else "#ff0000"
        side_text = "買入做多 (Long)" if is_buy else "賣出做空 (Short)"

        block_msg = {
            "attachments": [
                {
                    "color": color,
                    "blocks": [
                        {
                            "type": "header",
                            "text": {
                                "type": "plain_text",
                                "text": f"{emoji} Bybit 成交通知"
                            }
                        },
                        {
                            "type": "section",
                            "fields": [
                                {"type": "mrkdwn", "text": f"*幣種:*\n{symbol}"},
                                {"type": "mrkdwn", "text": f"*方向:*\n{side_text}"},
                                {"type": "mrkdwn", "text": f"*價格:*\n{price}"},
                                {"type": "mrkdwn", "text": f"*數量:*\n{qty}"}
                            ]
                        },
                        {
                            "type": "context",
                            "elements": [
                                {"type": "plain_text", "text": f"ID: {exec_id}"}
                            ]
                        }
                    ]
                }
            ]
        }

        slack(block_msg)
        print(f"已發送通知: {symbol} {side} {price}")


def on_error(ws, error):
    print(f"WebSocket 錯誤: {error}")

def on_close(ws, close_status_code, close_msg):
    print("WebSocket 連線已關閉")

# ==========================================
# 6. WebSocket 主循環
# ==========================================
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
            print(f"WebSocket 發生異常: {e}")

        print("5 秒後嘗試重新連線...")
        time.sleep(5)

# ==========================================
# 7. Railway 防休眠 self-ping
# ==========================================
def self_ping():
    while True:
        try:
            requests.get(f"http://127.0.0.1:{PORT}/health", timeout=5)
        except:
            pass
        time.sleep(60)

# ==========================================
# 8. 主程式進入點
# ==========================================
if __name__ == "__main__":
    threading.Thread(target=run_ws_forever, daemon=True).start()
    threading.Thread(target=self_ping, daemon=True).start()

    print(f"Starting Flask server on port {PORT}...")
    app.run(host="0.0.0.0", port=PORT, use_reloader=False)
