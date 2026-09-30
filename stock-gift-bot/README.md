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
| 多人使用 | 每個人的「要買、不買、已購入、已投票」各自獨立記錄。新使用者加好友後要在後台核准，也可以設成自動核准 |
| 後台 | 看爬到的資料、管理使用者、查誰買了什麼和投票了沒、測試，見下方 |
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
投票 2317 4/25-5/24   手動設定投票期間（以股東e服務為準，所有人共用）
```

## 後台（http://NAS:13999/admin）

先在 `.env` 設 `ADMIN_PASSWORD`，登入帳號預設為 `admin`。

| 頁面 | 功能 |
|---|---|
| 總覽 | 使用者數、進行中的股東會、收盤價日期、本月 LINE 推播用量、排程下次執行時間、最近爬取紀錄。可以手動「立即更新資料」或「發送今日提醒」 |
| 紀念品資料 | 已存進資料庫的股東會列表：昨收、紀念品、最後買進日、投票期間（可直接修改），以及每檔有幾人要買、已購入、已投票（點數字看是哪些人）。可以手動新增或刪除 |
| 即時爬取 | 當場抓 HiStock、證交所、櫃買中心，顯示原始解析結果與網頁上的表格表頭，**不會存檔**，用來確認爬蟲是否正常 |
| 使用者 | 核准、停用、改名、設為管理員（管理員會收到資料抓取失敗的通知）、刪除 |
| 購買/投票紀錄 | 誰買了哪檔、投票了沒。可依人或狀態篩選、直接修改，或幫某人登記 |
| 操作紀錄 | 每個人按過哪些按鈕、什麼時候按的（LINE、後台、系統自動過期都會記錄） |
| 測試 | 「連線自我檢查」會實際測試 LINE、HiStock、證交所、櫃買中心；「模擬某一天」可以選使用者和日期，預覽那天會收到什麼，也可以真的推播一則測試訊息到他的 LINE |

> 🔒 後台只用 Basic Auth 保護。建議 Cloudflare Tunnel 只開放 `/callback`，後台只在家裡區網使用。

## 資料來源

| 資料 | 來源 |
|---|---|
| 紀念品、最後買進日、股東會日期 | [HiStock 股東會紀念品](https://histock.tw/stock/gift.aspx)，另可用 `data/manual_gifts.csv` 補資料 |
| 昨日收盤價 | 證交所 OpenAPI `STOCK_DAY_ALL`（上市）、櫃買中心 OpenAPI（上櫃） |
| 休市日 | 證交所 OpenAPI `holidaySchedule` |
| 投票期間 | 紀念品表有投票欄位就用；沒有就推估，可用指令手動修正 |

每天 08:40 更新資料，09:10 發提醒。

> ⚠️ 開發環境連不到這些網站，所以 HiStock 解析器是依表頭關鍵字（代號、最後買進、股東會日期、紀念品）找欄位寫的，還沒對真實網頁測過。第一次跑請打開後台「即時爬取」頁。如果解析出 0 筆，看頁面上列出的表頭，調整 `bot/sources.py` 的 `HEADER_KEYWORDS`。

> 💡 LINE 免費方案每月推播則數有限（實際額度看後台總覽）。每人每天最多 1 則推播，按按鈕的回覆不算在內。人數多時請留意用量。

## 安裝

### 1. 建立 LINE Bot
1. 到 [LINE Developers](https://developers.line.biz/) 建立 Provider，再建一個 **Messaging API channel**
2. 記下 **Channel secret**（Basic settings 頁）和 **Channel access token**（Messaging API 頁，按 Issue）
3. 在 LINE Official Account Manager 關閉「自動回應訊息」，開啟「Webhook」

### 2. 讓 LINE 連得到 NAS（webhook 必須是 HTTPS）
任選一種方式：
- **Cloudflare Tunnel（推薦）**：不用開 port、不用固定 IP。在 Cloudflare Zero Trust 建立 Tunnel，Public Hostname 指到 `http://bot:13999`（Path 填 `callback`），把 token 填進 `.env`，並設 `COMPOSE_PROFILES=tunnel`
- **Tailscale Funnel**：`tailscale funnel 13999`
- **NAS 內建反向代理 + DDNS + Let's Encrypt**：Synology 和 QNAP 都有

Webhook URL 填 `https://你的網域/callback`，然後按 Verify。

### 3. 在 NAS 上啟動（Synology Container Manager 或 QNAP Container Station）
程式由 GitHub Actions 建成 Docker image，放在 `ghcr.io/henrychiu0504/stock-gift-bot`。**NAS 不需要 git，也不用自己 build**，只要三個檔案：

```bash
mkdir -p /volume1/docker/stock-gift-bot && cd /volume1/docker/stock-gift-bot   # QNAP 可用 /share/Container/...
# 從 GitHub 下載 docker-compose.yml、update.sh、.env.example 到這個資料夾
cp .env.example .env      # 填入 LINE_CHANNEL_SECRET、LINE_CHANNEL_ACCESS_TOKEN、ADMIN_PASSWORD
bash update.sh            # 第一次執行：下載 image 並啟動
```

> **Image 權限**：repo 是 private 時，image 預設也是 private，NAS 會下載失敗。二選一：
> - **把 image 設成公開（推薦）**：image 裡只有程式碼，沒有密碼（密碼都在 NAS 的 `.env`）。GitHub → 你的頭像 → Your profile → Packages → `stock-gift-bot` → Package settings → Change visibility → Public
> - **保持 private**：在 GitHub 建一個只有 `read:packages` 權限的 Personal Access Token（classic），在 NAS 以 root 執行一次 `docker login ghcr.io -u HenryChiu0504`，密碼貼 token

1. 打開 `http://NAS:13999/admin`，到「測試」頁按「連線自我檢查」
2. 用手機加 bot 好友並傳一句話，到後台「使用者」按**核准**，再按「設為管理員」
3. 家人朋友也加好友，一樣在後台核准
4. 到「測試」頁選自己和某個日期，預覽後按「推播」，確認 LINE 收得到

健康檢查：`http://NAS:13999/health`

## 更新（GitHub Actions 建 image → NAS 下載）

```
改程式 → git push 到 GitHub
       → GitHub Actions：跑測試 → 建 image（amd64 + arm64）→ 推到 ghcr.io
       → NAS 的 update.sh（排程每天跑）：下載新 image → 重啟 → 檢查 /health
```

- **main 分支**：建出來的 image 標為 `:latest`，NAS 預設追蹤這個
- **其他分支**：image 標為分支名稱（`/` 換成 `-`），例如 `:claude-zen-maxwell-0sgq77`。想先在 NAS 試某個分支，就在 `.env` 設 `IMAGE=ghcr.io/henrychiu0504/stock-gift-bot:分支名`
- **測試沒過**：不會產生 image，NAS 也就不會更新
- **新版啟動失敗**：`update.sh` 自動退回上一版，之後也不會再裝那個壞掉的 image，等 GitHub 上有更新的版本才會再試
- **不會動到的資料**：`.env` 和 `data/`（資料庫、紀錄）
- **紀錄與版本**：寫在 `data/update.log`；後台總覽會顯示目前版本（分支@commit）

```bash
bash update.sh            # 有新版才更新
bash update.sh --force    # 沒新版也重啟
```

### 設定自動更新
**Synology**：控制台 → 任務排程表 → 新增 → 排定的任務 → 使用者定義的指令碼
- 使用者：`root`
- 時間：每天 **03:00**（避開 08:40 更新資料、09:10 提醒）
- 指令：`bash /volume1/docker/stock-gift-bot/update.sh`

**QNAP 或其他 Linux**：`crontab -e`，加入：
```
0 3 * * * bash /share/Container/stock-gift-bot/update.sh >/dev/null 2>&1
```

想馬上更新：到 GitHub 的 Actions 頁確認出現綠勾，再在 NAS 執行 `bash update.sh`。

### 其他方式
- **Container Manager 介面**：「專案」→ 停止 → 「映像」頁更新 image → 啟動。這樣沒有自動退回舊版的保護
- **用原始碼自己 build**（開發用）：`docker compose -f docker-compose.yml -f docker-compose.build.yml up -d --build`

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
