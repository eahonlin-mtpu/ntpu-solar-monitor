# 太陽能監控 - 重新設計面板(自動重試版)

這個專案是原本 https://tatungsolarweb.azurewebsites.net/tv/1040091/ 這頁的
替代面板,重點是解決原網頁的兩個問題:

1. 畫面很陽春(舊版 AdminLTE 樣式)
2. 原本的更新邏輯(`$.ajax` 抓資料)**完全沒有錯誤處理**——只要那次請求
   失敗(逾時、對方 503、網路瞬斷…),畫面就會靜靜停在舊資料,不會重試、
   也不會告訴使用者。

## 架構

```
tatung-solar-dashboard/
├── index.html              重新設計的面板頁面
├── style.css               樣式
├── app.js                  前端邏輯:讀 data.json、失敗自動重試、顯示狀態
├── data.json               目前的發電資料(由 Actions 定期更新)
├── scripts/scrape.py       背景抓資料腳本(帶重試機制)
└── .github/workflows/
    └── fetch-data.yml      GitHub Actions 排程,定期執行 scrape.py
```

**為什麼不直接讓瀏覽器連去大同智能官網抓資料?**
測試過了,那個網站沒有開放跨網域存取(CORS),瀏覽器直接 fetch 會被擋下來
（`Failed to fetch`）。所以改成:GitHub Actions 在背景定期用 Python 模擬
原本網頁的行為(先 GET 拿 Django 的 CSRF token,再 POST 拿 JSON 資料),
寫進 `data.json`,面板只需要「同源」讀取這個檔案,完全不會碰到 CORS 問題。

## 兩層自動重試機制

- **背景抓取層**(`scripts/scrape.py`):每次執行時,如果抓取失敗,會用
  指數退避重試最多 5 次(2s → 4s → 8s → 16s → 32s)。全部失敗的話,不會
  用壞資料蓋掉 `data.json`,而是保留上次成功的資料,並記錄
  `status: "error"`、`last_attempt_at`、`error` 訊息。

- **前端顯示層**(`app.js`):
  - 如果連 `data.json` 本身都讀不到(GitHub Pages 短暫異常等),會顯示
    「重試中(第 N 次)」並自動用指數退避重試,同時提供「立即重新整理」
    按鈕。
  - 如果 `data.json` 顯示 `status: "error"`(代表背景抓取上游網站失敗),
    會顯示醒目的警示,說明目前看到的是「最後一次成功的資料」而非即時值。

## 部署步驟(GitHub)

1. 建立一個新的 GitHub repository,把這個資料夾的內容全部上傳上去
   (可以直接在 GitHub 網頁上拖曳上傳,或用 `git push`)。
2. 到 repo 的 **Settings → Pages**,Source 選 `Deploy from a branch`,
   Branch 選 `main` / `/(root)`,存檔。幾分鐘後就能用
   `https://<你的帳號>.github.io/<repo名稱>/` 打開面板。
3. 到 **Settings → Actions → General → Workflow permissions**,選
   `Read and write permissions`,存檔(這樣 Action 才能自動 commit 更新
   `data.json`)。
4. 到 **Actions** 分頁,可以手動點 `Run workflow` 先測試一次抓取是否成功;
   之後就會照 `.github/workflows/fetch-data.yml` 裡設定的排程
   (台灣時間每天 06:00–19:00,每 5 分鐘一次)自動執行。

## 想調整的地方

- **抓取頻率 / 時段**:改 `.github/workflows/fetch-data.yml` 裡的 `cron`
  設定(目前是 UTC 時間 `22-23,0-11` 點、每 5 分鐘一次,對應台灣時間
  06:00–19:00;5 分鐘是 GitHub Actions 排程支援的最短間隔)。
- **重試次數 / 等待秒數**:改 `scripts/scrape.py` 最上面的
  `MAX_ATTEMPTS`、`BACKOFF_BASE_SECONDS`、`BACKOFF_MAX_SECONDS`。
- **前端自動整理頻率**:改 `app.js` 最上面的 `REFRESH_INTERVAL_MS`。
- **要監控的案場**:改 `scripts/scrape.py` 裡的 `SITE_ID`
  (目前是 `1040091`,對應原網頁網址 `/tv/1040091/`)。

## 本機預覽

因為 `app.js` 是用 `fetch()` 讀取 `data.json`,大部分瀏覽器不允許直接用
`file://` 開啟(會被當成跨來源請求擋掉)。本機測試請先啟動一個簡單伺服器:

```bash
cd tatung-solar-dashboard
python3 -m http.server 8000
# 瀏覽器打開 http://localhost:8000
```
