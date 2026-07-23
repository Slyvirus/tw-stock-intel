"""
融資融券監測 — 每日資料抓取腳本
資料來源：
  TWSE 上市：MI_MARGN（市場彙總 MS + 個股 ALL）、FMTQIK（加權指數）、STOCK_DAY_ALL（跌停 proxy）
  TPEx 上櫃：margin/balance（市場合計 summary + 個股）、tradingIndex（櫃買指數）
執行時機：每個交易日 17:30 台北時間，接在 daily_fetch.py 之後（GitHub Actions）

寫入兩張表（同一個 stocks.db，不動既有 institutional_data / signals）：
  margin_market — 每日市場層級（融資/融券餘額、指數、跌停家數），可回補歷史看趨勢
  margin_stock  — 個股融資曝險（餘額、使用率），僅保留最新一日

回補歷史：python fetch/margin_fetch.py --backfill 60   （回補近 60 個日曆日的市場層級資料）
"""

import csv
import io
import sys
import time
import sqlite3
import datetime
import warnings
from pathlib import Path

import requests

warnings.filterwarnings('ignore')

BASE_DIR = Path(__file__).parent.parent
DB_PATH  = BASE_DIR / 'data' / 'stocks.db'

HEADERS = {'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)'}

TWSE_MARGN_URL = 'https://www.twse.com.tw/rwd/zh/marginTrading/MI_MARGN'
TWSE_FMTQIK    = 'https://www.twse.com.tw/rwd/zh/afterTrading/FMTQIK'
TWSE_DAY_ALL   = 'https://www.twse.com.tw/rwd/zh/afterTrading/STOCK_DAY_ALL'
TPEX_BALANCE   = 'https://www.tpex.org.tw/www/zh-tw/margin/balance'
TPEX_INDEX     = 'https://www.tpex.org.tw/www/zh-tw/afterTrading/tradingIndex'

# 融資初始維持率（融資成數 6 成 → 1/0.6 ≈ 166.7%）。維持率估算基準用此常數。
INITIAL_MAINTENANCE = 166.7


# ── 共用工具 ──────────────────────────────────────────────

def _num(raw) -> float:
    """把 '1,234.56' / '-0.0900' / ' ' 轉成 float，失敗回 0.0"""
    try:
        return float(str(raw).replace(',', '').strip())
    except (ValueError, AttributeError):
        return 0.0


def _roc_to_iso(roc: str) -> 'str | None':
    """民國日期 '115/07/21' → '2026-07-21'"""
    try:
        y, m, d = roc.strip().split('/')
        return f'{int(y) + 1911:04d}-{int(m):02d}-{int(d):02d}'
    except Exception:
        return None


def _get_json(url: str, params: dict):
    try:
        r = requests.get(url, params=params, headers=HEADERS, timeout=20, verify=False)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        print(f'  API 失敗 {url}：{e}')
        return None


# ── 建表 ──────────────────────────────────────────────────

def init_db():
    conn = sqlite3.connect(DB_PATH)
    conn.execute('''
        CREATE TABLE IF NOT EXISTS margin_market (
            date               TEXT NOT NULL,
            market             TEXT NOT NULL,           -- TWSE / TPEX
            margin_bal_k       INTEGER,                 -- 融資今日餘額（仟元）
            margin_prev_k      INTEGER,                 -- 融資前日餘額（仟元）
            short_bal_lots     INTEGER,                 -- 融券今日餘額（張）
            short_prev_lots    INTEGER,                 -- 融券前日餘額（張）
            index_close        REAL,                    -- 加權/櫃買指數收盤
            turnover_k         INTEGER,                 -- 成交金額（仟元）
            limit_down_cnt     INTEGER,                 -- 跌停家數（估算，僅最新日）
            near_limit_cnt     INTEGER,                 -- 接近跌停 (跌幅≥9%) 家數（估算）
            PRIMARY KEY (date, market)
        )
    ''')
    conn.execute('''
        CREATE TABLE IF NOT EXISTS margin_stock (
            date          TEXT NOT NULL,
            market        TEXT NOT NULL,
            stock_id      TEXT NOT NULL,
            stock_name    TEXT,
            margin_bal    INTEGER,                       -- 融資今日餘額（張）
            margin_prev   INTEGER,                       -- 融資前日餘額（張）
            margin_quota  INTEGER,                       -- 融資限額（張）
            usage_rate    REAL,                          -- 融資使用率（%）
            short_bal     INTEGER,                       -- 融券今日餘額（張）
            PRIMARY KEY (date, market, stock_id)
        )
    ''')
    conn.commit()
    conn.close()
    print(f'DB 初始化完成：{DB_PATH}')


# ── TWSE 上市 ─────────────────────────────────────────────

def fetch_twse_market(date_str: str) -> 'dict | None':
    """MI_MARGN 市場彙總（selectType=MS）→ 融資融券餘額（date_str = YYYYMMDD 西元）"""
    payload = _get_json(TWSE_MARGN_URL,
                        {'date': date_str, 'selectType': 'MS', 'response': 'json'})
    if not payload or payload.get('stat') != 'OK':
        return None
    tables = payload.get('tables') or []
    data = tables[0].get('data') if tables else payload.get('data')
    if not data:
        return None

    margin_bal = margin_prev = short_bal = short_prev = None
    for row in data:
        label = row[0]
        if '融資金額' in label:                       # 仟元
            margin_prev = int(_num(row[4]))
            margin_bal  = int(_num(row[5]))
        elif label.startswith('融券') and '單位' in label:  # 張
            short_prev = int(_num(row[4]))
            short_bal  = int(_num(row[5]))
    if margin_bal is None:
        return None
    return {'margin_bal_k': margin_bal, 'margin_prev_k': margin_prev,
            'short_bal_lots': short_bal, 'short_prev_lots': short_prev}


def fetch_twse_index(date_str: str) -> 'dict | None':
    """FMTQIK → 指定日的加權指數收盤 + 成交金額（回傳整月，取當日）"""
    payload = _get_json(TWSE_FMTQIK, {'date': date_str, 'response': 'json'})
    if not payload or not payload.get('data'):
        return None
    iso_target = f'{date_str[:4]}-{date_str[4:6]}-{date_str[6:]}'
    out = {}
    for row in payload['data']:
        iso = _roc_to_iso(row[0])
        if iso:
            out[iso] = {'index_close': _num(row[4]), 'turnover_k': int(_num(row[2]))}
    return out.get(iso_target), out  # (當日, 整月 map)


def fetch_twse_stock_margin(date_str: str) -> 'list | None':
    """MI_MARGN 個股（selectType=ALL）→ 個股融資餘額/限額/使用率"""
    payload = _get_json(TWSE_MARGN_URL,
                        {'date': date_str, 'selectType': 'ALL', 'response': 'json'})
    if not payload or payload.get('stat') != 'OK':
        return None
    tables = payload.get('tables') or []
    # 個股明細通常在最後一張表
    data = None
    for t in tables:
        if t.get('data') and len(t['data'][0]) >= 8:
            data = t['data']
    if not data:
        return None

    rows = []
    for r in data:
        try:
            bal   = int(_num(r[6]))       # 融資今日餘額（張）
            prev  = int(_num(r[5]))
            quota = int(_num(r[7]))       # 次一營業日限額（張）
            short = int(_num(r[12]))      # 融券今日餘額（張）
            usage = round(bal / quota * 100, 2) if quota > 0 else 0.0
            rows.append({'stock_id': r[0].strip(), 'stock_name': r[1].strip(),
                        'margin_bal': bal, 'margin_prev': prev,
                        'margin_quota': quota, 'usage_rate': usage, 'short_bal': short})
        except (IndexError, ValueError):
            continue
    return rows or None


def fetch_twse_limit_down() -> 'tuple[int, int] | None':
    """STOCK_DAY_ALL（僅最新一日，CSV/JSON 皆容錯）→ (跌停家數, 接近跌停家數)"""
    try:
        r = requests.get(TWSE_DAY_ALL, params={'response': 'json'},
                        headers=HEADERS, timeout=20, verify=False)
        r.raise_for_status()
        text = r.text
    except Exception as e:
        print(f'  STOCK_DAY_ALL 失敗：{e}')
        return None

    records = []
    stripped = text.lstrip()
    if stripped.startswith('{'):                      # JSON 格式
        import json
        payload = json.loads(text)
        fields = payload.get('fields', [])
        data = payload.get('data', [])
        try:
            ci, di = fields.index('收盤價'), fields.index('漲跌價差')
        except ValueError:
            ci, di = 8, 9
        records = [(row[ci], row[di]) for row in data]
    else:                                             # CSV 格式
        reader = csv.reader(io.StringIO(text))
        rows = list(reader)
        if not rows:
            return None
        for row in rows[1:]:
            if len(row) >= 10:
                records.append((row[8], row[9]))      # 收盤價, 漲跌價差

    limit_down = near = 0
    for close_raw, chg_raw in records:
        close, chg = _num(close_raw), _num(chg_raw)
        prev = close - chg
        if prev <= 0 or chg >= 0:
            continue
        pct = chg / prev * 100
        if pct <= -9.5:
            limit_down += 1
        elif pct <= -9.0:
            near += 1
    return limit_down, near


# ── TPEx 上櫃 ─────────────────────────────────────────────

def fetch_tpex_market(iso: str) -> 'dict | None':
    """margin/balance summary → 上櫃融資融券市場合計（iso = YYYY-MM-DD）"""
    q = iso.replace('-', '/')
    payload = _get_json(TPEX_BALANCE, {'date': q, 'response': 'json'})
    if not payload:
        return None
    tables = payload.get('tables') or []
    if not tables:
        return None
    t = tables[0]
    summary = t.get('summary') or []
    margin_bal_lots = margin_prev_lots = None
    margin_bal_k = margin_prev_k = None
    short_bal = short_prev = None
    for s in summary:
        label = s[1] if len(s) > 1 else ''
        if '合計' in label:                            # 張數合計
            margin_prev_lots = int(_num(s[2]))
            margin_bal_lots  = int(_num(s[6]))
            short_prev = int(_num(s[10]))
            short_bal  = int(_num(s[14]))
        elif '融資金' in label:                         # 仟元
            margin_prev_k = int(_num(s[2]))
            margin_bal_k  = int(_num(s[6]))
    if margin_bal_k is None:
        return None
    return {'margin_bal_k': margin_bal_k, 'margin_prev_k': margin_prev_k,
            'short_bal_lots': short_bal, 'short_prev_lots': short_prev}


def fetch_tpex_stock_margin(iso: str) -> 'list | None':
    """margin/balance 個股明細 → 上櫃個股融資餘額/使用率"""
    q = iso.replace('-', '/')
    payload = _get_json(TPEX_BALANCE, {'date': q, 'response': 'json'})
    if not payload:
        return None
    tables = payload.get('tables') or []
    if not tables or not tables[0].get('data'):
        return None
    rows = []
    for r in tables[0]['data']:
        try:
            bal   = int(_num(r[6]))    # 資餘額（張）
            prev  = int(_num(r[2]))    # 前資餘額（張）
            usage = _num(r[8])         # 資使用率(%)
            quota = int(_num(r[9]))    # 資限額
            short = int(_num(r[14]))   # 券餘額
            rows.append({'stock_id': r[0].strip(), 'stock_name': r[1].strip(),
                        'margin_bal': bal, 'margin_prev': prev,
                        'margin_quota': quota, 'usage_rate': usage, 'short_bal': short})
        except (IndexError, ValueError):
            continue
    return rows or None


def fetch_tpex_index(iso: str) -> 'dict | None':
    """tradingIndex → 櫃買指數收盤 + 成交金額（回傳整月 map）"""
    q = iso.replace('-', '/')
    payload = _get_json(TPEX_INDEX, {'date': q, 'response': 'json'})
    if not payload:
        return None
    tables = payload.get('tables') or []
    data = tables[0].get('data') if tables else None
    if not data:
        return None
    out = {}
    for row in data:
        d = _roc_to_iso(row[0])
        if d:
            out[d] = {'index_close': _num(row[4]), 'turnover_k': int(_num(row[2]))}
    return out


# ── 寫入 ──────────────────────────────────────────────────

def save_market_row(conn, iso: str, market: str, m: dict, idx: 'dict | None',
                    limit_down=None, near=None):
    conn.execute('DELETE FROM margin_market WHERE date=? AND market=?', (iso, market))
    conn.execute('''INSERT INTO margin_market
        (date, market, margin_bal_k, margin_prev_k, short_bal_lots, short_prev_lots,
         index_close, turnover_k, limit_down_cnt, near_limit_cnt)
        VALUES (?,?,?,?,?,?,?,?,?,?)''',
        (iso, market, m.get('margin_bal_k'), m.get('margin_prev_k'),
         m.get('short_bal_lots'), m.get('short_prev_lots'),
         (idx or {}).get('index_close'), (idx or {}).get('turnover_k'),
         limit_down, near))


def save_stock_rows(conn, iso: str, market: str, rows: list):
    conn.execute('DELETE FROM margin_stock WHERE date=? AND market=?', (iso, market))
    conn.executemany('''INSERT INTO margin_stock
        (date, market, stock_id, stock_name, margin_bal, margin_prev,
         margin_quota, usage_rate, short_bal)
        VALUES (?,?,?,?,?,?,?,?,?)''',
        [(iso, market, r['stock_id'], r['stock_name'], r['margin_bal'],
          r['margin_prev'], r['margin_quota'], r['usage_rate'], r['short_bal'])
         for r in rows])


def last_trading_iso() -> 'str | None':
    """從今天回推找最近一個有 TWSE 融資彙總資料的交易日"""
    for i in range(0, 8):
        d = datetime.date.today() - datetime.timedelta(days=i)
        ds = d.strftime('%Y%m%d')
        if fetch_twse_market(ds):
            return ds
    return None


# ── 主流程：每日更新 ──────────────────────────────────────

def run_daily():
    date_str = last_trading_iso()
    if not date_str:
        print('找不到最近 8 天內的交易日融資資料')
        return
    iso = f'{date_str[:4]}-{date_str[4:6]}-{date_str[6:]}'
    print(f'抓取 {iso} 融資融券資料...')

    conn = sqlite3.connect(DB_PATH)

    # 上市市場層級
    tw_m = fetch_twse_market(date_str)
    tw_idx_today, _ = fetch_twse_index(date_str) or (None, {})
    limit_down = near = None
    ld = fetch_twse_limit_down()
    if ld:
        limit_down, near = ld
    if tw_m:
        save_market_row(conn, iso, 'TWSE', tw_m, tw_idx_today, limit_down, near)
        print(f'  上市：融資 {tw_m["margin_bal_k"]/1e5:,.0f} 億　跌停估 {limit_down}／接近 {near}')

    # 上櫃市場層級
    tpex_m = fetch_tpex_market(iso)
    tpex_idx = fetch_tpex_index(iso) or {}
    if tpex_m:
        save_market_row(conn, iso, 'TPEX', tpex_m, tpex_idx.get(iso))
        print(f'  上櫃：融資 {tpex_m["margin_bal_k"]/1e5:,.0f} 億')

    # 個股融資曝險（僅最新日）
    tw_stocks = fetch_twse_stock_margin(date_str)
    if tw_stocks:
        save_stock_rows(conn, iso, 'TWSE', tw_stocks)
        print(f'  上市個股融資：{len(tw_stocks)} 檔')
    tpex_stocks = fetch_tpex_stock_margin(iso)
    if tpex_stocks:
        save_stock_rows(conn, iso, 'TPEX', tpex_stocks)
        print(f'  上櫃個股融資：{len(tpex_stocks)} 檔')

    conn.commit()
    conn.close()
    print(f'完成！{iso} 融資監測資料已寫入 {DB_PATH}')


# ── 回補歷史（市場層級，看波段趨勢用）────────────────────

def run_backfill(days: int = 60):
    print(f'回補近 {days} 個日曆日的市場層級融資資料...')
    conn = sqlite3.connect(DB_PATH)

    # 先把整段期間的指數整月抓好（省呼叫次數）
    tw_idx_all, tpex_idx_all = {}, {}
    today = datetime.date.today()
    seen_months = set()
    for i in range(0, days + 1):
        d = today - datetime.timedelta(days=i)
        ym = (d.year, d.month)
        if ym in seen_months:
            continue
        seen_months.add(ym)
        ds = d.strftime('%Y%m01')
        res = fetch_twse_index(ds)
        if res:
            tw_idx_all.update(res[1])
        tpex_idx_all.update(fetch_tpex_index(f'{d.year}-{d.month:02d}-01') or {})
        time.sleep(0.4)

    count = 0
    for i in range(0, days + 1):
        d = today - datetime.timedelta(days=i)
        if d.weekday() >= 5:
            continue
        ds  = d.strftime('%Y%m%d')
        iso = d.strftime('%Y-%m-%d')

        tw_m = fetch_twse_market(ds)
        if tw_m:
            save_market_row(conn, iso, 'TWSE', tw_m, tw_idx_all.get(iso))
            count += 1
        tpex_m = fetch_tpex_market(iso)
        if tpex_m:
            save_market_row(conn, iso, 'TPEX', tpex_m, tpex_idx_all.get(iso))
        time.sleep(0.3)

    conn.commit()
    conn.close()
    print(f'回補完成，共寫入 {count} 個交易日的上市市場資料（上櫃同步回補）')


if __name__ == '__main__':
    init_db()
    if len(sys.argv) > 2 and sys.argv[1] == '--backfill':
        run_backfill(int(sys.argv[2]))
    elif len(sys.argv) > 1 and sys.argv[1] == '--backfill':
        run_backfill(60)
    else:
        run_daily()
