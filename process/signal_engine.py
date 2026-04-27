"""
信號計算引擎
從 institutional_data 表計算法人交叉訊號，結果存入 signals 表
每次 daily_fetch.py 執行完後自動呼叫
"""

import sqlite3
import pandas as pd
from pathlib import Path

BASE_DIR = Path(__file__).parent.parent
DB_PATH = BASE_DIR / 'data' / 'stocks.db'


def init_signals_table():
    conn = sqlite3.connect(DB_PATH)
    conn.execute('''
        CREATE TABLE IF NOT EXISTS signals (
            date           TEXT NOT NULL,
            stock_id       TEXT NOT NULL,
            stock_name     TEXT,
            foreign_net    INTEGER,
            trust_net      INTEGER,
            dealer_net     INTEGER,
            total_net      INTEGER,
            foreign_consec INTEGER DEFAULT 0,
            trust_consec   INTEGER DEFAULT 0,
            cross_buy      INTEGER DEFAULT 0,
            all_three_buy  INTEGER DEFAULT 0,
            signal_strength TEXT,
            PRIMARY KEY (date, stock_id)
        )
    ''')
    conn.commit()
    conn.close()


def compute_consecutive_all(conn, as_of_date: str) -> pd.DataFrame:
    """一次讀取所有歷史資料，批量計算各股連續買超天數"""
    df = pd.read_sql_query(
        """SELECT date, stock_id, foreign_net, trust_net
           FROM institutional_data
           WHERE date <= ?
           ORDER BY stock_id, date DESC""",
        conn, params=(as_of_date,)
    )
    if df.empty:
        return pd.DataFrame(columns=['stock_id', 'foreign_consec', 'trust_consec'])

    results = []
    for stock_id, group in df.groupby('stock_id'):
        f_consec = 0
        for val in group['foreign_net']:
            if val > 0:
                f_consec += 1
            else:
                break
        t_consec = 0
        for val in group['trust_net']:
            if val > 0:
                t_consec += 1
            else:
                break
        results.append({'stock_id': stock_id, 'foreign_consec': f_consec, 'trust_consec': t_consec})

    return pd.DataFrame(results)


def classify_strength(row) -> str | None:
    if row['all_three_buy'] or row['foreign_consec'] >= 5:
        return 'strong'
    if row['cross_buy'] or row['foreign_consec'] >= 3:
        return 'medium'
    if row['foreign_net'] > 0 or row['trust_net'] > 0:
        return 'watch'
    return None


def calculate_signals(date: str = None):
    conn = sqlite3.connect(DB_PATH)

    if date is None:
        row = conn.execute("SELECT MAX(date) FROM institutional_data").fetchone()
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
    df = df.merge(consec, on='stock_id', how='left').fillna({'foreign_consec': 0, 'trust_consec': 0})
    df['foreign_consec'] = df['foreign_consec'].astype(int)
    df['trust_consec']   = df['trust_consec'].astype(int)

    df['cross_buy']     = ((df['foreign_net'] > 0) & (df['trust_net'] > 0)).astype(int)
    df['all_three_buy'] = ((df['foreign_net'] > 0) & (df['trust_net'] > 0) & (df['dealer_net'] > 0)).astype(int)
    df['signal_strength'] = df.apply(classify_strength, axis=1)

    signals = df[df['signal_strength'].notna()].copy()

    conn.execute("DELETE FROM signals WHERE date=?", (date,))
    signals[[
        'date', 'stock_id', 'stock_name',
        'foreign_net', 'trust_net', 'dealer_net', 'total_net',
        'foreign_consec', 'trust_consec',
        'cross_buy', 'all_three_buy', 'signal_strength'
    ]].to_sql('signals', conn, if_exists='append', index=False)
    conn.commit()
    conn.close()

    strong = signals[signals['signal_strength'] == 'strong']
    medium = signals[signals['signal_strength'] == 'medium']
    watch  = signals[signals['signal_strength'] == 'watch']

    print(f"\n{date} 信號計算完成")
    print(f"  🔴 強訊號：{len(strong)} 檔")
    print(f"  🟡 中訊號：{len(medium)} 檔")
    print(f"  ⚪ 觀察中：{len(watch)} 檔")

    if not strong.empty:
        print("\n強訊號個股：")
        print(strong[['stock_id', 'stock_name', 'foreign_net', 'trust_net', 'dealer_net', 'foreign_consec']].to_string(index=False))

    return signals


if __name__ == '__main__':
    init_signals_table()
    calculate_signals()
