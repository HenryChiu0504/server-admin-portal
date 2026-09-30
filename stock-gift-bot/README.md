# 股東會紀念品 LINE 提醒機器人（NAS 24H）

在 NAS 上跑的 LINE Bot。每個交易日 **09:10（零股盤中開始）** 提醒你：

1. **要買嗎？**：從最後買進日前 3 個交易日開始問，會附上代碼、名稱、昨收、紀念品、股東會日期、投票期間
   - 「要買」：之後每個交易日問「有購入了嗎？」，按鈕有「已購入！」、「明天再提醒」、「不買了」
   - 「不買」：這檔不再提醒
   - 過了最後買進日還沒買：自動過期，不再提醒
2. **投票了嗎？**：只問已購入的股票，在電子投票期間每天問
   - 「已投票」：不再提醒
   - 「未投票」：明天 09:10 再問
   - 過了投票截止日：自動停止

## 比原本需求多做的部分

| 功能 | 說明 |
|---|---|
| 休市日判斷 | 從證交所 OpenAPI 抓休市日，連假和週末不發買進提醒 |
| 一天只推播一次 | 所有股票合併成一則卡片輪播。LINE 免費方案的推播則數有限，按按鈕、下指令都是用免費的 reply |
| 最後一天警示 | 最後買進日當天、投票最後一天，卡片上會有紅字提醒 |
| eGift 提示 | 投票第一天提醒你，投完票可以在股東e服務領電子紀念品（有提供的公司才有） |
| 投票期間推估 | 抓不到投票期間時，用「開會前 30 天到前 3 天」推估，並標示「推估」。可以用指令手動修正 |
| 過濾 | `MAX_PRICE` 設定價格上限；紀念品寫「無／不發放／未定」的股票不問 |
| NAS 重開機補發 | 09:10 時 NAS 沒開也沒關係，13:30 前開機會補發當天的提醒，而且不會重複發 |
| 資料抓取失敗通知 | 當天資料更新失敗時，會在提醒裡告訴你 |
| 只回應你一個人 | 只理會 `LINE_USER_ID` 本人傳來的訊息 |
| 手動補資料 | 把 `data/manual_gifts.csv` 放進去，就能補上 HiStock 沒有的股票 |

## LINE 指令

```
說明                  指令列表
今天                  預覽今天的提醒
近期                  未來 14 天最後買進日一覽（含昨收、紀念品）
清單                  要買或已買的股票與狀態
查 2317               單檔資訊與按鈕
要買 2317 / 不買 2317
已購入 2317           自己先買了也能登記，之後就會提醒投票
已投票 2317
投票 2317 4/25-5/24   手動設定投票期間（以股東e服務為準）
更新                  立即重新抓資料
```

## 資料來源

| 資料 | 來源 |
|---|---|
| 紀念品、最後買進日、股東會日期 | [HiStock 股東會紀念品](https://histock.tw/stock/gift.aspx)，另可用 `data/manual_gifts.csv` 補資料 |
| 昨日收盤價 | 證交所 OpenAPI `STOCK_DAY_ALL`（上市）、櫃買中心 OpenAPI（上櫃） |
| 休市日 | 證交所 OpenAPI `holidaySchedule` |
| 投票期間 | 紀念品表有投票欄位就用；沒有就推估，可用指令手動修正 |

每天 08:40 更新資料，09:10 發提醒。

> ⚠️ 開發環境連不到這些網站，所以 HiStock 解析器是依表頭關鍵字（代號、最後買進、股東會日期、紀念品）找欄位寫的，還沒對真實網頁測過。第一次跑請傳「更新」，看抓到幾筆。如果是 0 筆，看 log 並調整 `bot/sources.py` 的 `HEADER_KEYWORDS`。

## 安裝

### 1. 建立 LINE Bot
1. 到 [LINE Developers](https://developers.line.biz/) 建立 Provider，再建一個 **Messaging API channel**
2. 記下 **Channel secret**（Basic settings 頁）和 **Channel access token**（Messaging API 頁，按 Issue）
3. 在 LINE Official Account Manager 關閉「自動回應訊息」，開啟「Webhook」

### 2. 讓 LINE 連得到 NAS（webhook 必須是 HTTPS）
任選一種方式：
- **Cloudflare Tunnel（推薦）**：不用開 port、不用固定 IP。在 Cloudflare Zero Trust 建立 Tunnel，Public Hostname 指到 `http://bot:8000`，把 token 填進 `.env`
- **Tailscale Funnel**：`tailscale funnel 8000`
- **NAS 內建反向代理 + DDNS + Let's Encrypt**：Synology 和 QNAP 都有

Webhook URL 填 `https://你的網域/callback`，然後按 Verify。

### 3. 在 NAS 上啟動（Synology Container Manager 或 QNAP Container Station）
```bash
cp .env.example .env      # 填入 LINE_CHANNEL_SECRET、LINE_CHANNEL_ACCESS_TOKEN
docker compose up -d --build
```
1. 用手機加 bot 好友，隨便傳一句話，bot 會回你的 **userId**
2. 把 userId 填進 `.env` 的 `LINE_USER_ID`，執行 `docker compose up -d` 重啟
3. 傳「更新」、「近期」、「今天」測試

健康檢查：`http://NAS:8000/health`

## 開發與測試
```bash
python -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt pytest httpx
pytest -q
```

## 注意事項
- 颱風假無法預先得知，當天還是會照常提醒
- 常會的最後買進日通常是開會前 62 天，臨時會則較短。一律以 HiStock 和公司公告為準
- 零股能不能領紀念品各家公司規定不同，買之前請確認
