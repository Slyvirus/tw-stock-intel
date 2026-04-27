"""
台灣股市三大法人買賣超 — 每日資料抓取腳本
資料來源：台灣證交所 T86（免費、T+1、無需帳號）
執行時機：每個交易日 17:30 台北時間（GitHub Actions 自動觸發）
"""

import requests
import pandas as pd
import sqlite3
import datetime
import warnings
from pathlib import Path

warnings.filterwarnings('ignore')  # 壓制 TWSE SSL 憑證警告

BASE_DIR = Path(__file__).parent.parent
DB_PATH = BASE_DIR / 'data' / 'stocks.db'

TWSE_URL = 'https://www.twse.com.tw/rwd/zh/fund/T86'
HEADERS = {'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)'}

# TWSE T86 欄位索引
COL_STOCK_ID   = 0
COL_STOCK_NAME = 1
COL_FOREIGN_NET = 4   # 外陸資買賣超股數（不含外資自營商）
COL_TRUST_NET   = 10  # 投信買賣超股數
COL_DEALER_NET  = 11  # 自營商買賣超股數（合計）
COL_TOTAL_NET   = 18  # 三大法人買賣超股數


def init_db():
    conn = sqlite3.connect(DB_PATH)
    conn.execute('''
        CREATE TABLE IF NOT EXISTS institutional_data (
            date        TEXT NOT NULL,
            stock_id    TEXT NOT NULL,
            stock_name  TEXT,
            foreign_net INTEGER,
            trust_net   INTEGER,
            dealer_net  INTEGER,
            total_net   INTEGER,
            PRIMARY KEY (date, stock_id)
        )
    ''')
    conn.commit()
    conn.close()
    print(f'DB 初始化完成：{DB_PATH}')


def _to_shares(raw: str) -> int:
    try:
        return int(raw.replace(',', '').strip())
    except ValueError:
        return 0


def fetch_twse(date_str: str) -> pd.DataFrame | None:
    """抓取指定日期全市場三大法人資料，回傳 DataFrame 或 None（非交易日）"""
    params = {'date': date_str, 'selectType': 'ALLBUT0999', 'response': 'json'}
    try:
        r = requests.get(TWSE_URL, params=params, headers=HEADERS, timeout=15, verify=False)
        r.raise_for_status()
        payload = r.json()
    except Exception as e:
        print(f'  API 呼叫失敗：{e}')
        return None

    if payload.get('stat') != 'OK' or not payload.get('data'):
        return None

    date_fmt = f'{date_str[:4]}-{date_str[4:6]}-{date_str[6:]}'
    rows = []
    for row in payload['data']:
        try:
            rows.append({
                'date':        date_fmt,
                'stock_id':    row[COL_STOCK_ID].strip(),
                'stock_name':  row[COL_STOCK_NAME].strip(),
                'foreign_net': _to_shares(row[COL_FOREIGN_NET]) // 1000,
                'trust_net':   _to_shares(row[COL_TRUST_NET])   // 1000,
                'dealer_net':  _to_shares(row[COL_DEALER_NET])  // 1000,
                'total_net':   _to_shares(row[COL_TOTAL_NET])   // 1000,
            })
        except IndexError:
            continue

    return pd.DataFrame(rows) if rows else None


def get_last_trading_date() -> str | None:
    """找最近有資料的交易日（從今天開始往前找，今天 17:30 後資料已在 TWSE）"""
    for i in range(0, 8):
        date = datetime.date.today() - datetime.timedelta(days=i)
        date_str = date.strftime('%Y%m%d')
        params = {'date': date_str, 'selectType': 'ALLBUT0999', 'response': 'json'}
        try:
            r = requests.get(TWSE_URL, params=params, headers=HEADERS, timeout=10, verify=False)
            payload = r.json()
            if payload.get('stat') == 'OK' and payload.get('data'):
                return date_str
        except Exception:
            continue
    return None


def save_to_db(df: pd.DataFrame):
    """存入 SQLite，同日重複執行會覆寫"""
    conn = sqlite3.connect(DB_PATH)
    date_val = df['date'].iloc[0]
    conn.execute('DELETE FROM institutional_data WHERE date = ?', (date_val,))
    df.to_sql('institutional_data', conn, if_exists='append', index=False)
    conn.commit()
    conn.close()


def print_summary(df: pd.DataFrame):
    print(f'\n外資買超 Top 10：')
    top = df.nlargest(10, 'foreign_net')[['stock_id', 'stock_name', 'foreign_net', 'trust_net', 'total_net']]
    print(top.to_string(index=False))

    print(f'\n投信買超 Top 10：')
    top = df.nlargest(10, 'trust_net')[['stock_id', 'stock_name', 'foreign_net', 'trust_net', 'total_net']]
    print(top.to_string(index=False))

    cross = df[(df['foreign_net'] > 0) & (df['trust_net'] > 0)]
    print(f'\n外資 + 投信同時買超：{len(cross)} 檔')
    if not cross.empty:
        print(cross.nlargest(10, 'total_net')[['stock_id', 'stock_name', 'foreign_net', 'trust_net', 'total_net']].to_string(index=False))


if __name__ == '__main__':
    init_db()

    date_str = get_last_trading_date()
    if not date_str:
        print('找不到最近 7 天內的交易日資料，可能是假日或 API 異常')
        raise SystemExit(1)

    print(f'抓取 {date_str} 資料...')
    df = fetch_twse(date_str)

    if df is None or df.empty:
        print(f'{date_str} 無資料（非交易日或 API 異常）')
        raise SystemExit(0)

    save_to_db(df)
    print(f'完成！寫入 {len(df)} 筆，存至 {DB_PATH}')
    print_summary(df)
