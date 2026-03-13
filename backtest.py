import python_bithumb as pybithumb
import pandas as pd
import numpy as np

TICKER = "KRW-ETH"
GRID_COUNT = 10
INITIAL_BUDGET = 200000.0

def compute_atr_grid_spacing(df, default_spacing=0.01, period=14):
    if len(df) < period:
        return default_spacing
    high_low = df['high'] - df['low']
    high_close = np.abs(df['high'] - df['close'].shift())
    low_close = np.abs(df['low'] - df['close'].shift())
    ranges = pd.concat([high_low, high_close, low_close], axis=1)
    true_range = np.max(ranges, axis=1)
    atr = true_range.rolling(period).mean().iloc[-1]
    
    current_price = df['close'].iloc[-1]
    atr_percent = atr / current_price
    dynamic_spacing = atr_percent * 0.5
    dynamic_spacing = max(0.0025, min(0.005, dynamic_spacing))
    return dynamic_spacing

def round_to_tick(price):
    if price >= 1000000: return round(price / 1000) * 1000
    elif price >= 100000: return round(price / 100) * 100
    elif price >= 10000: return round(price / 10) * 10
    elif price >= 1000: return round(price)
    elif price >= 100: return round(price, 1)
    elif price >= 10: return round(price, 2)
    return round(price, 4)

def run_backtest():
    print(f"Loading 1-hour data for {TICKER} (this is usually accurate for the last few weeks)...")
    # pybithumb API does not officially support minute1 easily without getting truncated
    # let's try to get a 10-minute or 1-hour chart, or just run over the recent daily if that's all it gives
    df = pybithumb.get_ohlcv(TICKER, interval="minute10")
    if df is None or len(df) == 0:
        print("Failed to fetch minute10, trying minute60")
        df = pybithumb.get_ohlcv(TICKER, interval="minute60")
        
    if df is None or len(df) == 0:
        print("Failed to fetch OHLCV")
        return
        
    print(f"Data rows: {len(df)}")
    
    # Let's take the most recent 1000 rows
    df = df.tail(1000)
    
    start_idx = 50
    
    if len(df) <= start_idx:
        print("Not enough data")
        return

    # Initialize Grid at start_idx
    init_df = df.iloc[:start_idx]
    start_price = float(init_df['close'].iloc[-1])
    dynamic_spacing = compute_atr_grid_spacing(init_df)
    
    print(f"--- INIT ---")
    print(f"Start Price: {start_price:,.0f} KRW")
    print(f"Initial ATR Spacing: {dynamic_spacing*100:.2f}%")
    
    num_price_points = GRID_COUNT + 1
    half_points = num_price_points // 2
    grids = []
    
    # 📉 매수망 (선형)
    for i in range(half_points, 0, -1):
        grids.append(start_price * (1 - dynamic_spacing * i))
    
    grids.append(start_price)
    
    # 📈 매도망 (지수적 비대칭)
    growth_factor = 1.0
    accumulated_spacing = 0.0
    for i in range(1, num_price_points - half_points):
        accumulated_spacing += dynamic_spacing * growth_factor
        grids.append(start_price * (1 + accumulated_spacing))
        growth_factor *= 1.5
        
    grids = sorted(list(set([round_to_tick(p) for p in grids])))
    
def simulate(df, grids, start_price, grid_name):
    order_krw = INITIAL_BUDGET / GRID_COUNT
    slots = {}
    fee_rate = 0.0004 # 0.04% fee
    krw_balance = INITIAL_BUDGET
    eth_balance = 0.0
    
    for i in range(GRID_COUNT):
        buy_p = grids[i]
        sell_p = grids[i+1]
        
        if sell_p <= start_price:
            state = "KRW"
        else:
            state = "ETH"
            vol = order_krw / start_price
            fee = order_krw * fee_rate
            krw_balance -= (order_krw + fee)
            eth_balance += vol
            
        slots[i] = {"state": state, "buy_price": buy_p, "sell_price": sell_p, "vol": order_krw / buy_p if state == "KRW" else order_krw / start_price}

    trade_count = 0
    total_profit_krw = 0
    
    for index, row in df.iterrows():
        high = row['high']
        low = row['low']
        
        for i, s in slots.items():
            if s['state'] == 'KRW':
                if low <= s['buy_price']:
                    vol = order_krw / s['buy_price']
                    fee = order_krw * fee_rate
                    krw_balance -= (order_krw + fee)
                    eth_balance += vol
                    s['state'] = 'ETH'
                    s['vol'] = vol
                    trade_count += 1
            elif s['state'] == 'ETH':
                if high >= s['sell_price']:
                    krw_gained = s['vol'] * s['sell_price']
                    fee = krw_gained * fee_rate
                    krw_balance += (krw_gained - fee)
                    eth_balance -= s['vol']
                    profit = (krw_gained - fee) - order_krw
                    total_profit_krw += profit
                    s['state'] = 'KRW'
                    trade_count += 1
                    
    end_price = float(df['close'].iloc[-1])
    final_eval = krw_balance + (eth_balance * end_price)
    yield_pct = ((final_eval / INITIAL_BUDGET) - 1.0) * 100
    
    print(f"\n[{grid_name} Strategy]")
    print(f"Total Trades Simulated: {trade_count}")
    print(f"Grid Realized Profit (Pure Trading): {total_profit_krw:,.0f} KRW")
    print(f"Final Total Asset Eval: {final_eval:,.0f} KRW (Yield: {yield_pct:+.2f}%)")
    return final_eval

def run_backtest():
    print(f"Loading 1-hour data for {TICKER} (this is usually accurate for the last few weeks)...")
    df = pybithumb.get_ohlcv(TICKER, interval="minute10")
    if df is None or len(df) == 0:
        df = pybithumb.get_ohlcv(TICKER, interval="minute60")
        
    if df is None or len(df) == 0:
        return
        
    df = df.tail(1000)
    start_idx = 50
    if len(df) <= start_idx: return

    init_df = df.iloc[:start_idx]
    start_price = float(init_df['close'].iloc[-1])
    dynamic_spacing = compute_atr_grid_spacing(init_df)
    
    print(f"--- INIT ---")
    print(f"Start Price: {start_price:,.0f} KRW")
    print(f"End Price: {float(df['close'].iloc[-1]):,.0f} KRW")
    print(f"Initial ATR Spacing: {dynamic_spacing*100:.2f}%\n")
    
    num_price_points = GRID_COUNT + 1
    half_points = num_price_points // 2
    
    # 1. Linear Grids (Old Way)
    grids_linear = []
    for i in range(half_points, 0, -1): grids_linear.append(start_price * (1 - dynamic_spacing * i))
    grids_linear.append(start_price)
    for i in range(1, num_price_points - half_points): grids_linear.append(start_price * (1 + dynamic_spacing * i))
    grids_linear = sorted(list(set([round_to_tick(p) for p in grids_linear])))
    
    # 2. Asymmetric Grids (New Way)
    grids_asym = []
    for i in range(half_points, 0, -1): grids_asym.append(start_price * (1 - dynamic_spacing * i))
    grids_asym.append(start_price)
    growth_factor = 1.0
    accumulated_spacing = 0.0
    for i in range(1, num_price_points - half_points):
        accumulated_spacing += dynamic_spacing * growth_factor
        grids_asym.append(start_price * (1 + accumulated_spacing))
        growth_factor *= 1.5
    grids_asym = sorted(list(set([round_to_tick(p) for p in grids_asym])))
    
    test_df = df.iloc[start_idx:]
    
    simulate(test_df, grids_linear, start_price, "Linear Grid (Old)")
    simulate(test_df, grids_asym, start_price, "Asymmetric Grid (New)")

if __name__ == "__main__":
    run_backtest()
