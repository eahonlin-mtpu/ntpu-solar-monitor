#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
抓取大同智能太陽能監控系統(tatungsolarweb)指定案場群組的即時發電資料。

原始網頁(https://tatungsolarweb.azurewebsites.net/tv/<site_id>/)是靠瀏覽器
內的 jQuery 在頁面載入後,對同一網址發送一個 POST 請求(帶 Django 的
csrfmiddlewaretoken)取得 JSON 資料,再用 JS 把資料組成卡片塞進頁面。
但原始程式碼的 $.ajax 呼叫「沒有寫 error callback」──也就是說,只要那次
POST 失敗(逾時、對方伺服器 503、網路瞬斷...),畫面就會靜靜地維持舊資料,
使用者完全不會被告知,也不會自動重試。

這支腳本做的事情,就是把同樣的流程在背景(GitHub Actions)重現一次,
差別在於:
  1. 失敗時用「指數退避(exponential backoff)」自動重試多次
  2. 全部重試失敗時,不會用壞資料覆蓋掉舊資料,而是保留上次成功的資料,
     並且額外記錄「最後一次成功時間 / 最後一次嘗試時間 / 錯誤訊息」,
     讓前端面板可以清楚顯示「資料可能已過時」的警示,而不是像原網頁一樣
     完全沒有任何提示。

輸出檔案:data.json(放在 repo 根目錄,給前端的 index.html 直接讀取,
同源讀取不會有 CORS 問題)。
"""

import json
import random
import re
import sys
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path

import requests

# ---------------------------------------------------------------------------
# 設定
# ---------------------------------------------------------------------------

SITE_ID = "1040091"  # 對應原網頁 https://tatungsolarweb.azurewebsites.net/tv/1040091/
BASE_URL = f"https://tatungsolarweb.azurewebsites.net/tv/{SITE_ID}/"

# 原始來源的案場名稱用「台北」、括號位置也不一致(有的沒有括號子標題)。
# 這裡用 site_no 對每個案場的顯示名稱做「正名」:
#   name_main -> 卡片標題第一行
#   name_sub  -> 卡片標題第二行(括號內的建築物名稱),故意讓每張卡片都
#                固定拆成兩行,排版才會整齊一致,不會因為建築物名稱長短
#                不同而有的卡片換行、有的沒換行。
SITE_NAME_OVERRIDES = {
    "1040091": {"main": "臺北大學-臺北校區", "sub": "教學大樓"},
    "2370041": {"main": "臺北大學 三峽校區", "sub": "崇越館"},
    "2370051": {"main": "臺北大學 三峽校區", "sub": "圖資大樓"},
    "2370061": {"main": "臺北大學 三峽校區", "sub": "體育館"},
    "2370071": {"main": "臺北大學 三峽校區", "sub": "行政大樓"},
}


def normalize_name(site_no: str, raw_name: str):
    """回傳 (name_main, name_sub)。優先用上面手動維護的對照表;
    如果之後案場清單改變、對照表沒更新到,則退回用「台北->臺北」
    取代 + 自動抓括號內容當作 name_sub 的通用規則。
    """
    override = SITE_NAME_OVERRIDES.get(site_no)
    if override:
        return override["main"], override["sub"]

    name = (raw_name or "").replace("台北", "臺北")
    m = re.match(r"^(.*?)\s*[(（](.*?)[)）]\s*$", name)
    if m:
        return m.group(1).strip(" -"), m.group(2).strip()
    return name, None

MAX_ATTEMPTS = 5          # 單次執行內,最多重試幾次
BACKOFF_BASE_SECONDS = 2  # 退避基準秒數:2, 4, 8, 16, 32 ...
BACKOFF_MAX_SECONDS = 60  # 單次等待時間上限
REQUEST_TIMEOUT = 15      # 單一 HTTP 請求逾時秒數

DATA_FILE = Path(__file__).resolve().parent.parent / "data.json"

USER_AGENT = (
    "Mozilla/5.0 (compatible; SolarDashboardFetcher/1.0; "
    "+https://github.com/) requests"
)


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def fetch_once() -> dict:
    """對原網站重現一次「GET 取得 CSRF token -> POST 取得 JSON」的流程。"""
    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT})

    get_resp = session.get(BASE_URL, timeout=REQUEST_TIMEOUT)
    get_resp.raise_for_status()

    csrf_token = session.cookies.get("csrftoken")
    if not csrf_token:
        raise RuntimeError("沒有拿到 csrftoken cookie,網站結構可能已變更")

    post_resp = session.post(
        BASE_URL,
        data={"banner": "banner", "csrfmiddlewaretoken": csrf_token},
        headers={
            "Referer": BASE_URL,
            "X-Requested-With": "XMLHttpRequest",
        },
        timeout=REQUEST_TIMEOUT,
    )
    post_resp.raise_for_status()

    payload = post_resp.json()
    sites_raw = payload.get("data", [])
    if not isinstance(sites_raw, list) or not sites_raw:
        raise ValueError("回傳的 JSON 沒有預期的 data 陣列(結構可能已變更)")

    sites = []
    for item in sites_raw:
        power = item.get("power", {})
        site_no = item.get("site_no")
        name_main, name_sub = normalize_name(site_no, item.get("site_name"))
        sites.append(
            {
                "site_no": site_no,
                "site_name": item.get("site_name"),
                "name_main": name_main,
                "name_sub": name_sub,
                "kwp": power.get("kwp"),
                "today_kwh": power.get("todaykwh"),
                "total_kwh": power.get("totalkwh"),
                "carbon_ton": power.get("carbon"),
                "thumbnail_url": (
                    f"https://newsolarwebstorage.blob.core.windows.net/"
                    f"sitess-public/{site_no}/cvr/thumbnail/"
                    f"{site_no}.jpg"
                ),
            }
        )

    return {"sites": sites}


def fetch_with_retry() -> dict:
    """帶指數退避的重試包裝。回傳 dict,包含成功/失敗的完整狀態資訊。"""

    last_error = None

    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            result = fetch_once()
            return {
                "status": "ok",
                "attempts": attempt,
                "fetched_at": now_iso(),
                "last_attempt_at": now_iso(),
                "error": None,
                "sites": result["sites"],
            }
        except Exception as exc:  # noqa: BLE001
            last_error = f"{type(exc).__name__}: {exc}"
            print(
                f"[attempt {attempt}/{MAX_ATTEMPTS}] 抓取失敗:{last_error}",
                file=sys.stderr,
            )
            if attempt < MAX_ATTEMPTS:
                delay = min(
                    BACKOFF_BASE_SECONDS * (2 ** (attempt - 1)),
                    BACKOFF_MAX_SECONDS,
                )
                delay += random.uniform(0, 1)
                print(f"  -> {delay:.1f} 秒後重試...", file=sys.stderr)
                time.sleep(delay)

    return {
        "status": "error",
        "attempts": MAX_ATTEMPTS,
        "fetched_at": None,
        "last_attempt_at": now_iso(),
        "error": last_error,
        "sites": None,
    }


def load_previous() -> dict:
    if DATA_FILE.exists():
        try:
            return json.loads(DATA_FILE.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def main() -> int:
    previous = load_previous()
    result = fetch_with_retry()

    if result["status"] == "ok":
        output = result
    else:
        output = {
            "status": "error",
            "attempts": result["attempts"],
            "fetched_at": previous.get("fetched_at"),
            "last_attempt_at": result["last_attempt_at"],
            "error": result["error"],
            "sites": previous.get("sites"),
        }

    DATA_FILE.parent.mkdir(parents=True, exist_ok=True)
    DATA_FILE.write_text(
        json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    if output["status"] == "ok":
        print(f"成功:抓到 {len(output['sites'])} 個案場,已寫入 {DATA_FILE}")
        return 0
    else:
        print(
            f"失敗:重試 {output['attempts']} 次後仍無法取得資料,"
            f"已保留舊資料。錯誤:{output['error']}",
            file=sys.stderr,
        )
        return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        traceback.print_exc()
        sys.exit(0)
