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
用 SSH 登入 NAS，**用 git clone 下載**（之後才能自動更新）：
```bash
cd /volume1/docker        # Synology 範例；QNAP 可用 /share/Container
git clone https://github.com/HenryChiu0504/server-admin-portal.git
cd server-admin-portal/stock-gift-bot
cp .env.example .env      # 填入 LINE_CHANNEL_SECRET、LINE_CHANNEL_ACCESS_TOKEN、ADMIN_PASSWORD
docker compose up -d --build
```
> 如果 repo 是 private，clone 時密碼欄請貼 GitHub 的 Personal Access Token（只需 Contents: Read 權限）。
> 可以用 `git config --global credential.helper store` 讓 NAS 記住 token，排程更新時就不用再輸入。
> Synology 請先到套件中心安裝 **Git Server**（或 SynoCommunity 的 Git）才有 `git` 指令。
1. 打開 `http://NAS:13999/admin`，到「測試」頁按「連線自我檢查」
2. 用手機加 bot 好友並傳一句話，到後台「使用者」按**核准**，再按「設為管理員」
3. 家人朋友也加好友，一樣在後台核准
4. 到「測試」頁選自己和某個日期，預覽後按「推播」，確認 LINE 收得到

健康檢查：`http://NAS:13999/health`

## 更新

改好的程式推到 GitHub 的 `main` 分支後，NAS 執行 `update.sh` 就會更新：

```bash
cd /volume1/docker/server-admin-portal/stock-gift-bot
./update.sh               # 有新版才更新
./update.sh --force       # 沒新版也重建
BRANCH=其他分支 ./update.sh  # 追蹤 main 以外的分支
```

`update.sh` 的流程：git fetch → 有新版才 pull → `docker compose up -d --build` → 檢查 `/health`。

- **新版啟動失敗**：自動退回上一版，之後也不會再裝那個壞掉的版本。等 GitHub 上有更新的版本才會再試
- **不會動到的資料**：`.env` 和 `data/`（資料庫、紀錄）都不在 git 裡，更新不會影響
- **本機改過程式檔**：更新會停下來，不會覆蓋你的修改。客製化請寫在 `.env` 或 `docker-compose.override.yml`
- **紀錄與版本**：寫在 `data/update.log`；目前版本會顯示在後台總覽

### 自動更新（推薦）
**Synology**：控制台 → 任務排程表 → 新增 → 排定的任務 → 使用者定義的指令碼
- 使用者：`root`
- 時間：每天 **03:00**（避開 08:40 更新資料、09:10 提醒）
- 指令：`bash /volume1/docker/server-admin-portal/stock-gift-bot/update.sh`

**QNAP 或其他 Linux**：`crontab -e`，加入：
```
0 3 * * * bash /share/Container/server-admin-portal/stock-gift-bot/update.sh >/dev/null 2>&1
```

### 其他更新方式
| 方式 | 做法 | 適合 |
|---|---|---|
| 排程自動更新（上面） | 每天自動檢查 GitHub | 推薦，不用管 |
| 手動 | SSH 登入後執行 `./update.sh` | 想自己控制更新時間 |
| Container Manager 介面 | 先 `git pull`，再在「專案」按「建置」 | 不熟指令但會用 NAS 介面 |
| GitHub Actions 建 image + Watchtower | Actions 把 image 推到 ghcr.io，NAS 用 Watchtower 自動拉 | NAS 效能很弱、不想在 NAS 上 build（需要時可以再加） |

### GitHub 上的自動測試
每次推送 `stock-gift-bot/` 的變更，GitHub Actions 都會跑測試並試 build Docker image（`.github/workflows/stock-gift-bot.yml`）。在 GitHub 的 Actions 頁看到綠勾，再讓 NAS 更新比較安心。

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
