/**
 * 前端面板邏輯。
 *
 * data.json 由 GitHub Actions(scripts/scrape.py)定期背景更新,
 * 這支 JS 只需要「同源」讀取這個 JSON 檔案,不會碰到原本從瀏覽器
 * 直接呼叫 tatungsolarweb.azurewebsites.net 會遇到的 CORS 限制。
 *
 * 這裡處理兩種不同層級的「資料抓不到」狀況,並分開顯示:
 *
 *   (A) 前端讀不到 data.json 本身(GitHub Pages 短暫異常、離線等)
 *       -> 用 fetchWithRetry() 做指數退避,自動重試,並在畫面上顯示
 *          「重試中(第 N 次)」的狀態。
 *
 *   (B) data.json 讀得到,但裡面 status === "error",代表 GitHub Actions
 *       那邊在抓上游(大同智能官方網站)資料時,重試多次後仍然失敗
 *       -> 顯示醒目的警示 banner,並附上「資料可能已過時」+ 最後成功時間,
 *          而不是像原網站一樣完全沒有任何提示。
 */

const REFRESH_INTERVAL_MS = 2 * 60 * 1000; // 正常情況下,每 2 分鐘重新讀一次 data.json(背景每 5 分鐘更新一次)
const RETRY_BASE_MS = 2000;                 // 重試退避基準:2s, 4s, 8s, 16s, 30s(上限)
const RETRY_MAX_MS = 30000;

const state = {
  retryCount: 0,
  retryTimer: null,
  refreshTimer: null,
};

const el = {
  statusPill: document.getElementById("status-pill"),
  statusText: document.getElementById("status-text"),
  refreshBtn: document.getElementById("refresh-btn"),
  banner: document.getElementById("banner"),
  lastUpdated: document.getElementById("last-updated"),
  summaryRow: document.getElementById("summary-row"),
  grid: document.getElementById("grid"),
};

function setStatus(kind, text) {
  el.statusPill.className = "status-pill " + kind;
  el.statusText.textContent = text;
}

function showBanner(kind, html) {
  el.banner.className = "banner show " + kind;
  el.banner.innerHTML = html;
}

function hideBanner() {
  el.banner.className = "banner";
  el.banner.innerHTML = "";
}

function fmt(n, digits = 0) {
  if (n === null || n === undefined || Number.isNaN(n)) return "—";
  return Number(n).toLocaleString("zh-TW", {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  });
}

function minutesAgo(isoString) {
  if (!isoString) return null;
  const then = new Date(isoString).getTime();
  if (Number.isNaN(then)) return null;
  return (Date.now() - then) / 60000;
}

async function fetchDataOnce() {
  const res = await fetch("./data.json?_=" + Date.now(), { cache: "no-store" });
  if (!res.ok) {
    throw new Error("HTTP " + res.status);
  }
  return res.json();
}

/** (A) 前端自己讀 data.json 失敗時的重試邏輯:指數退避 + 手動重試按鈕 */
async function fetchWithRetry() {
  clearTimeout(state.retryTimer);
  try {
    const data = await fetchDataOnce();
    state.retryCount = 0;
    render(data);
    setStatus("ok", "已連線");
    scheduleNextRefresh();
  } catch (err) {
    state.retryCount += 1;
    const delay = Math.min(RETRY_BASE_MS * 2 ** (state.retryCount - 1), RETRY_MAX_MS);
    setStatus(
      "retry",
      `重試中(第 ${state.retryCount} 次,${Math.round(delay / 1000)} 秒後再試)`
    );
    if (state.retryCount === 1) {
      showBanner(
        "err",
        `⚠️ 目前無法讀取面板資料(${escapeHtml(err.message)})。系統會自動重試,你也可以按右上角「立即重新整理」。`
      );
    }
    state.retryTimer = setTimeout(fetchWithRetry, delay);
  }
}

function scheduleNextRefresh() {
  clearTimeout(state.refreshTimer);
  state.refreshTimer = setTimeout(fetchWithRetry, REFRESH_INTERVAL_MS);
}

function escapeHtml(s) {
  const div = document.createElement("div");
  div.textContent = s;
  return div.innerHTML;
}

function render(data) {
  const sites = data.sites || [];

  // (B) 上游(GitHub Actions 抓大同智能官網)重試多次後仍失敗的狀況
  const staleMin = minutesAgo(data.fetched_at);
  if (data.status === "error") {
    showBanner(
      "err",
      `⚠️ 資料來源(大同智能太陽能監控網)目前無法連線,背景程式已自動重試 ${data.attempts || "多"} 次仍失敗。` +
        `以下顯示的是最後一次成功抓到的資料` +
        (staleMin !== null ? `(約 ${Math.round(staleMin)} 分鐘前)` : "") +
        `,並非即時數值。` +
        (data.error ? `<br><span style="opacity:.75">錯誤訊息:${escapeHtml(data.error)}</span>` : "")
    );
  } else {
    hideBanner();
  }

  el.lastUpdated.textContent = data.fetched_at
    ? "資料時間:" + new Date(data.fetched_at).toLocaleString("zh-TW")
    : "尚無成功抓取紀錄";

  if (!sites.length) {
    el.grid.innerHTML = `<div style="color:var(--muted);padding:20px;">目前沒有可顯示的資料。</div>`;
    el.summaryRow.innerHTML = "";
    return;
  }

  const sum = sites.reduce(
    (acc, s) => {
      acc.kwp += Number(s.kwp) || 0;
      acc.today += Number(s.today_kwh) || 0;
      acc.total += Number(s.total_kwh) || 0;
      acc.carbon += Number(s.carbon_ton) || 0;
      return acc;
    },
    { kwp: 0, today: 0, total: 0, carbon: 0 }
  );

  el.summaryRow.innerHTML = `
    <div class="summary-card"><div class="label">總裝置容量</div><div class="value">${fmt(sum.kwp, 2)}<span class="unit">kWp</span></div></div>
    <div class="summary-card"><div class="label">今日總發電</div><div class="value">${fmt(sum.today)}<span class="unit">kWh</span></div></div>
    <div class="summary-card"><div class="label">累積總發電</div><div class="value">${fmt(sum.total, 2)}<span class="unit">MWh</span></div></div>
    <div class="summary-card"><div class="label">總減碳量</div><div class="value">${fmt(sum.carbon, 2)}<span class="unit">噸</span></div></div>
  `;

  el.grid.innerHTML = sites
    .map(
      (s) => `
    <div class="card">
      <img class="thumb" src="${s.thumbnail_url}" alt="${escapeHtml(s.name_main || s.site_name || "")}" loading="lazy"
           onerror="this.style.display='none'">
      <div class="name">
        <div class="name-main">${escapeHtml(s.name_main || s.site_name || "(未命名案場)")}</div>
        ${s.name_sub ? `<div class="name-sub">(${escapeHtml(s.name_sub)})</div>` : ""}
      </div>
      <div class="metric-row">
        <div class="metric-icon red">◆</div>
        <div class="metric-text"><div class="label">裝置容量</div><div class="value">${fmt(s.kwp, 2)}<span class="unit">kWp</span></div></div>
      </div>
      <div class="metric-row">
        <div class="metric-icon orange">⚡</div>
        <div class="metric-text"><div class="label">今日發電</div><div class="value">${fmt(s.today_kwh)}<span class="unit">kWh</span></div></div>
      </div>
      <div class="metric-row">
        <div class="metric-icon green">☀</div>
        <div class="metric-text"><div class="label">累積發電</div><div class="value">${fmt(s.total_kwh, 2)}<span class="unit">MWh</span></div></div>
      </div>
      <div class="metric-row">
        <div class="metric-icon blue">↺</div>
        <div class="metric-text"><div class="label">總減碳量</div><div class="value">${fmt(s.carbon_ton, 2)}<span class="unit">噸</span></div></div>
      </div>
    </div>`
    )
    .join("");
}

el.refreshBtn.addEventListener("click", () => {
  state.retryCount = 0;
  clearTimeout(state.retryTimer);
  clearTimeout(state.refreshTimer);
  setStatus("retry", "重新整理中…");
  fetchWithRetry();
});

// 頁面載入後立刻開始抓資料
fetchWithRetry();
