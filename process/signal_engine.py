"""
信號計算引擎
從 institutional_data 計算法人買超／賣超訊號 + 法人參與率，結果存入 signals 表
"""

import sqlite3
import pandas as pd
from pathlib import Path

BASE_DIR = Path(__file__).parent.parent
DB_PATH  = BASE_DIR / 'data' / 'stocks.db'


def init_signals_table():
    conn = sqlite3.connect(DB_PATH)
    cur  = conn.execute("PRAGMA table_info(signals)")
    existing = {row[1] for row in cur.fetchall()}
    if 'sell_strength' not in existing or 'institutional_ratio' not in existing:
        conn.execute('DROP TABLE IF EXISTS signals')
    conn.execute('''
        CREATE TABLE IF NOT EXISTS signals (
            date                 TEXT NOT NULL,
            stock_id             TEXT NOT NULL,
            stock_name           TEXT,
            foreign_net          INTEGER,
            trust_net            INTEGER,
            dealer_net           INTEGER,
            total_net            INTEGER,
            volume               INTEGER,
            institutional_ratio  REAL,
            foreign_consec       INTEGER DEFAULT 0,
            trust_consec         INTEGER DEFAULT 0,
            foreign_sell_consec  INTEGER DEFAULT 0,
            cross_buy            INTEGER DEFAULT 0,
            all_three_buy        INTEGER DEFAULT 0,
            cross_sell           INTEGER DEFAULT 0,
            all_three_sell       INTEGER DEFAULT 0,
            signal_strength      TEXT,
            sell_strength        TEXT,
            PRIMARY KEY (date, stock_id)
        )
    ''')
    conn.commit()
    conn.close()


def compute_consecutive_all(conn, as_of_date: str) -> pd.DataFrame:
    df = pd.read_sql_query(
        """SELECT date, stock_id, foreign_net, trust_net
           FROM institutional_data
           WHERE date <= ?
           ORDER BY stock_id, date DESC""",
        conn, params=(as_of_date,)
    )
    if df.empty:
        return pd.DataFrame(columns=[
            'stock_id', 'foreign_consec', 'trust_consec', 'foreign_sell_consec'
        ])

    results = []
    for stock_id, group in df.groupby('stock_id'):
        f_buy = 0
        for val in group['foreign_net']:
            if val > 0: f_buy += 1
            else: break

        f_sell = 0
        for val in group['foreign_net']:
            if val < 0: f_sell += 1
            else: break

        t_buy = 0
        for val in group['trust_net']:
            if val > 0: t_buy += 1
            else: break

        results.append({
            'stock_id':            stock_id,
            'foreign_consec':      f_buy,
            'trust_consec':        t_buy,
            'foreign_sell_consec': f_sell,
        })

    return pd.DataFrame(results)


def classify_buy(row) -> 'str | None':
    if row['all_three_buy'] or row['foreign_consec'] >= 5:
        return 'strong'
    if row['cross_buy'] or row['foreign_consec'] >= 3:
        return 'medium'
    if row['foreign_net'] > 0 or row['trust_net'] > 0:
        return 'watch'
    return None


def classify_sell(row) -> 'str | None':
    if row['all_three_sell'] or row['foreign_sell_consec'] >= 5:
        return 'strong'
    if row['cross_sell'] or row['foreign_sell_consec'] >= 3:
        return 'medium'
    if row['foreign_net'] < 0 or row['trust_net'] < 0:
        return 'watch'
    return None


def compute_ratio(total_net: int, volume) -> 'float | None':
    try:
        v = float(volume)
        if v <= 0:
            return None
        return round(abs(float(total_net)) / v, 4)
    except (TypeError, ValueError):
        return None


def calculate_signals(date: str = None):
    conn = sqlite3.connect(DB_PATH)

    if date is None:
        row  = conn.execute("SELECT MAX(date) FROM institutional_data").fetchone()
        date = row[0] if row else None

    if not date:
        print("institutional_data 表無資料")
        conn.close()
        return

    df = pd.read_sql_query(
        "SELECT * FROM institutional_data WHERE date=?", conn, params=(date,)
    )
    if df.empty:
        print(f"{date} 無資料")
        conn.close()
        return

    consec = compute_consecutive_all(conn, date)
    df = df.merge(consec, on='stock_id', how='left').fillna(
        {'foreign_consec': 0, 'trust_consec': 0, 'foreign_sell_consec': 0}
    )
    for col in ['foreign_consec', 'trust_consec', 'foreign_sell_consec']:
        df[col] = df[col].astype(int)

    # 法人參與率
    vol_col = df['volume'] if 'volume' in df.columns else None
    if vol_col is not None:
        df['institutional_ratio'] = df.apply(
            lambda r: compute_ratio(r['total_net'], r['volume']), axis=1
        )
    else:
        df['institutional_ratio'] = None

    # 買超訊號
    df['cross_buy']     = ((df['foreign_net'] > 0) & (df['trust_net'] > 0)).astype(int)
    df['all_three_buy'] = (
        (df['foreign_net'] > 0) & (df['trust_net'] > 0) & (df['dealer_net'] > 0)
    ).astype(int)
    df['signal_strength'] = df.apply(classify_buy, axis=1)

    # 賣超訊號
    df['cross_sell']     = ((df['foreign_net'] < 0) & (df['trust_net'] < 0)).astype(int)
    df['all_three_sell'] = (
        (df['foreign_net'] < 0) & (df['trust_net'] < 0) & (df['dealer_net'] < 0)
    ).astype(int)
    df['sell_strength'] = df.apply(classify_sell, axis=1)

    signals = df[df['signal_strength'].notna() | df['sell_strength'].notna()].copy()

    conn.execute("DELETE FROM signals WHERE date=?", (date,))

    out_cols = [
        'date', 'stock_id', 'stock_name',
        'foreign_net', 'trust_net', 'dealer_net', 'total_net',
        'volume', 'institutional_ratio',
        'foreign_consec', 'trust_consec', 'foreign_sell_consec',
        'cross_buy', 'all_three_buy',
        'cross_sell', 'all_three_sell',
        'signal_strength', 'sell_strength',
    ]
    signals[[c for c in out_cols if c in signals.columns]].to_sql(
        'signals', conn, if_exists='append', index=False
    )
    conn.commit()
    conn.close()

    buy_s  = signals[signals['signal_strength'] == 'strong']
    buy_m  = signals[signals['signal_strength'] == 'medium']
    buy_w  = signals[signals['signal_strength'] == 'watch']
    sell_s = signals[signals['sell_strength'] == 'strong']
    sell_m = signals[signals['sell_strength'] == 'medium']
    sell_w = signals[signals['sell_strength'] == 'watch']

    print(f"\n{date} 信號計算完成")
    print(f"  買超 → 🔴 強：{len(buy_s)}  🟡 中：{len(buy_m)}  ⚪ 觀察：{len(buy_w)}")
    print(f"  賣超 → 🔵 強：{len(sell_s)}  🔷 中：{len(sell_m)}  ○ 觀察：{len(sell_w)}")

    return signals


if __name__ == '__main__':
    init_signals_table()

    conn      = sqlite3.connect(DB_PATH)
    all_dates = pd.read_sql_query(
        "SELECT DISTINCT date FROM institutional_data ORDER BY date", conn
    )['date'].tolist()
    conn.close()

    for d in all_dates:
        calculate_signals(d)
