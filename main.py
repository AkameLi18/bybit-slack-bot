import os
import json
import time
import hmac
import hashlib
import threading
import requests
from flask import Flask, Response
from websocket import WebSocketApp
from collections import deque  # 優化1: 用於固定長度的記憶體

# ========= 環境變數 =========
BYBIT_API_KEY = os.getenv("BYBIT_API_KEY")
BYBIT_API_SECRET = os.getenv("BYBIT_API_SECRET")
SLACK_WEBHOOK_URL = os.getenv("SLACK_WEBHOOK_URL")
TESTNET = os.getenv("BYBIT_TESTNET", "false").lower() == "true"
PORT = int(os.getenv("PORT", 8080))

WS_URL = (
    "wss://stream-testnet.bybit.com/v5/private"
    if TESTNET
    else "wss://stream.bybit.com/v5/private"
)

# ========= 狀態與優化 =========
# 優化1: 限制最大長度 1000，舊的會自動被擠出去，防止記憶體爆掉
seen_exec_ids = deque(maxlen=1000) 
last_activity_time = time.time() # 優化3: 用於健康檢查
ws_connected = False

app = Flask(__name__)

# ========= Flask (健康檢查優化) =========
@app.route("/")
def health():
    # 優化3: 如果超過 5 分鐘沒有 WebSocket 活動，回傳 500 錯誤
    # Railway 檢測到 500 會認為服務不健康，可能會觸發重啟 (視設定而定)
    if time.time() - last_activity_time > 300: 
        return Response("Bot seems stuck", status=500)
    return "ok"

# ========= 工具 =========
def sign_message(expires: int) -> str:
    return hmac.new(
        BYBIT_API_SECRET.encode(),
        f"GET/realtime{expires}".encode(),
        hashlib.sha256
    ).hexdigest()

def slack(payload: dict):
    # 修改: 接受 dict 以支援更豐富的排版
    try:
        requests.post(SLACK_WEBHOOK_URL, json=payload, timeout=10)
    except Exception as e:
        print("Slack error:", e)

# ========= WebSocket callbacks =========
def on_open(ws):
    global ws_connected
    ws_connected = True
    print("WS Connected, authenticating...")

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
    
    # 啟動通知 (僅文字)
    slack({"text": f"🟢 Bybit Bot 啟動成功 ({'Testnet' if TESTNET else 'Mainnet'})"})

def on_message(ws, message):
    global last_activity_time
    last_activity_time = time.time() # 更新心跳時間

    try:
        data = json.loads(message)
    except json.JSONDecodeError:
        return

    # 處理 Auth 成功與否
    if data.get("op") == "auth":
        if data.get("success"):
            print("Auth success")
        else:
            print(f"Auth failed: {data}")
            return

    if data.get("topic") != "execution":
        return

    for e in data.get("data", []):
        exec_id = e.get("execId")
        if not exec_id or exec_id in seen_exec_ids:
            continue

        seen_exec_ids.append(exec_id)

        # 優化2: Slack 美化排版
        side = e.get('side')
        symbol = e.get('symbol')
        price = e.get('execPrice')
        qty = e.get('execQty')
        
        # 根據買賣顯示不同顏色的 Emoji
        emoji = "🟢" if side == "Buy" else "🔴"
        color = "#36a64f" if side == "Buy" else "#ff0000"

        block_msg = {
            "attachments": [
                {
                    "color": color,
                    "blocks": [
                        {
                            "type": "section",
                            "text": {
                                "type": "mrkdwn",
                                "text": f"*{emoji} Bybit 成交通知*"
                            }
                        },
                        {
                            "type": "section",
                            "fields": [
                                {"type": "mrkdwn", "text": f"*幣種:*\n{symbol}"},
                                {"type": "mrkdwn", "text": f"*方向:*\n{side}"},
                                {"type": "mrkdwn", "text": f"*價格:*\n{price}"},
                                {"type": "mrkdwn", "text": f"*數量:*\n{qty}"}
                            ]
                        }
                    ]
                }
            ]
        }
        slack(block_msg)

def on_error(ws, error):
    print("WebSocket error:", error)

def on_close(ws, *_):
    global ws_connected
    ws_connected = False
    print("WebSocket closed")

# ========= WebSocket 主循環 =========
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
            # ping_interval 保持連線活躍
            ws.run_forever(ping_interval=20, ping_timeout=10)
        except Exception as e:
            print("WS crash:", e)
        
        print("Reconnecting in 5s...")
        time.sleep(5)

# ========= 進入點 =========
if __name__ == "__main__":
    # 啟動 WebSocket 執行緒
    threading.Thread(target=run_ws_forever, daemon=True).start()
    
    # 啟動 Flask (host=0.0.0.0 讓外部可訪問)
    # use_reloader=False 防止 Flask 開發模式下重複啟動兩次
    app.run(host="0.0.0.0", port=PORT, use_reloader=False)
