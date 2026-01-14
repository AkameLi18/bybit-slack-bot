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
# 讀取 Railway 的 PORT，預設為 8080
PORT = int(os.getenv("PORT", 8080))
# 判斷是否為測試網
TESTNET = os.getenv("BYBIT_TESTNET", "false").lower() == "true"

WS_URL = (
    "wss://stream-testnet.bybit.com/v5/private"
    if TESTNET
    else "wss://stream.bybit.com/v5/private"
)

# ==========================================
# 2. 全域狀態變數
# ==========================================
# 只記錄最近 1000 筆成交 ID，防止記憶體洩漏
seen_exec_ids = deque(maxlen=1000)
# 記錄是否已經發送過啟動通知 (避免重連時一直吵)
startup_notified = False

# ==========================================
# 3. Flask 服務 (為了騙過 Railway 的健康檢查)
# ==========================================
app = Flask(__name__)

@app.route("/")
@app.route("/health")
def health():
    # 無論 Railway 檢查 / 還是 /health，都回傳 ok
    return "ok", 200

# ==========================================
# 4. 工具函式
# ==========================================
def sign_message(expires: int) -> str:
    """產生 Bybit 要求的簽名"""
    return hmac.new(
        BYBIT_API_SECRET.encode(),
        f"GET/realtime{expires}".encode(),
        hashlib.sha256
    ).hexdigest()

def slack(payload: dict):
    """發送訊息到 Slack"""
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

    # 1. 製作簽名並登入
    expires = int(time.time() * 1000) + 10_000
    sig = sign_message(expires)
    
    ws.send(json.dumps({
        "op": "auth",
        "args": [BYBIT_API_KEY, expires, sig]
    }))

    # 2. 訂閱成交頻道
    ws.send(json.dumps({
        "op": "subscribe",
        "args": ["execution"]
    }))

    # 3. 發送啟動通知 (僅限第一次)
    if not startup_notified:
        env_name = "Testnet (測試網)" if TESTNET else "Mainnet (正式網)"
        slack({"text": f"🟢 Bybit 監控機器人已啟動 - {env_name}"})
        startup_notified = True  # 設為 True，下次重連就不會再發了

def on_message(ws, message):
    try:
        data = json.loads(message)
    except json.JSONDecodeError:
        return

    # 處理 Auth 回應
    if data.get("op") == "auth":
        if data.get("success"):
            print("Auth 認證成功")
        else:
            print(f"Auth 認證失敗: {data}")
        return

    # 確保是成交推播
    if data.get("topic") != "execution":
        return

    # 處理每一筆成交
    for e in data.get("data", []):
        exec_id = e.get("execId")
        
        # 去除重複 (Deduplication)
        if not exec_id or exec_id in seen_exec_ids:
            continue
        seen_exec_ids.append(exec_id)

        # 準備 Slack 訊息內容
        symbol = e.get('symbol')
        side = e.get('side')        # Buy or Sell
        price = e.get('execPrice')
        qty = e.get('execQty')
        
        # 根據買賣方向決定顏色和 Emoji
        is_buy = side.lower() == "buy"
        emoji = "🟢" if is_buy else "🔴"
        color = "#36a64f" if is_buy else "#ff0000"  # 綠色 vs 紅色
        side_text = "買入做多 (Long)" if is_buy else "賣出做空 (Short)"

        # Block Kit 排版
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
# 6. 主程式邏輯
# ==========================================
def run_ws_forever():
    """維持 WebSocket 長期連線"""
    while True:
        try:
            ws = WebSocketApp(
                WS_URL,
                on_open=on_open,
                on_message=on_message,
                on_error=on_error,
                on_close=on_close
            )
            # ping_interval=20: 每20秒發送心跳，防止被交易所斷線
            ws.run_forever(ping_interval=20, ping_timeout=10)
        except Exception as e:
            print(f"WebSocket 發生異常: {e}")
        
        print("5 秒後嘗試重新連線...")
        time.sleep(5)

if __name__ == "__main__":
    # 1. 啟動 WebSocket 監聽 (在背景執行)
    threading.Thread(target=run_ws_forever, daemon=True).start()
    
    # 2. 啟動 Flask Web Server (佔用 Port 讓 Railway 知道我們活著)
    print(f"Starting Flask server on port {PORT}...")
    app.run(host="0.0.0.0", port=PORT, use_reloader=False)
