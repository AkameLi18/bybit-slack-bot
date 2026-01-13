# Bybit → Slack 交易通知機器人

當 Bybit 帳戶出現新成交時，自動推播到 Slack 頻道  
支援 24 小時運行、Railway 免費方案

---

## 🚀 一鍵部署（推薦）

點擊下方按鈕：

[![Deploy on Railway](https://railway.app/button.svg)](https://railway.app/new/template?template=https://github.com/AkameLi18/bybit-slack-bot)

---

## 🔑 環境變數設定

| 變數 | 說明 |
|----|----|
| BYBIT_API_KEY | Bybit API Key（只讀） |
| BYBIT_API_SECRET | Bybit API Secret |
| SLACK_WEBHOOK_URL | Slack Incoming Webhook |
| BYBIT_TESTNET | false / true |

---

## 🔒 安全建議
- API **不要給交易權限**
- 僅使用 Read Orders / Executions
