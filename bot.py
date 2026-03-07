import os
import time
import json
import logging
import datetime
import traceback
import python_bithumb
import pandas as pd
import numpy as np
from dotenv import load_dotenv
  
# 1. Logging Setup
logger = logging.getLogger("GridBot")
logger.setLevel(logging.INFO)
formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')

file_handler = logging.FileHandler("trade.log", encoding="utf-8")
file_handler.setFormatter(formatter)
logger.addHandler(file_handler)

console_handler = logging.StreamHandler()
console_handler.setFormatter(formatter)
logger.addHandler(console_handler)

# 2. Config & Env
load_dotenv()
CON_KEY = os.getenv("BITHUMB_CON_KEY")
SEC_KEY = os.getenv("BITHUMB_SEC_KEY")

if not CON_KEY or not SEC_KEY:
    logger.error("API Keys missing in .env")
    exit(1)

bithumb = python_bithumb.Bithumb(CON_KEY, SEC_KEY)

# 3. Grid Strategy Config
TICKER = "KRW-ETH"
TARGET_COIN = "ETH"
TOTAL_BUDGET = 100000
GRID_COUNT = 9 # Number of grid slots (10 price lines)
GRID_STEP_RATIO = 0.01  # 1% spacing per grid (상/하단 박스권)
STATE_FILE = "grid_state.json"

ORDER_KRW = TOTAL_BUDGET / GRID_COUNT # approx 11,111 KRW per slot

# 4. State Management
def load_state():
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, "r") as f:
                return json.load(f)
        except Exception as e:
            logger.error(f"Error loading state: {e}")
    return None

def save_state(state):
    try:
        with open(STATE_FILE, "w") as f:
            json.dump(state, f, indent=4)
    except Exception as e:
        logger.error(f"Error saving state: {e}")

# 5. Bithumb API Wrappers
def get_current_price_retry(ticker, retries=3):
    for i in range(retries):
        try:
            return python_bithumb.get_current_price(ticker)
        except Exception as e:
            logger.warning(f"Failed to fetch price, retrying... ({i+1}/{retries}) Error: {e}")
            time.sleep(2)
    return None

def get_open_order_ids():
    orders = []
    try:
        orders = bithumb.get_orders(TICKER)
    except Exception as e:
        logger.error(f"Error fetching open orders: {e}")
        return None # Return None to indicate failure (dont process)
    
    # python-bithumb 래퍼의 V1 API 반환값 파싱 (uuid 혹은 order_id)
    ids = []
    if type(orders) is list:
        for o in orders:
            _id = o.get('uuid') or o.get('order_id')
            if _id: ids.append(_id)
    elif type(orders) is dict and 'data' in orders and type(orders['data']) is list:
        for o in orders['data']:
            _id = o.get('uuid') or o.get('order_id')
            if _id: ids.append(_id)
            
    return ids

def extract_order_id(order_resp):
    if type(order_resp) is dict:
        return order_resp.get('uuid') or order_resp.get('order_id')
    elif type(order_resp) is list and len(order_resp) > 0:
        return order_resp[0].get('uuid') or order_resp[0].get('order_id')
    elif type(order_resp) is str:
        return order_resp
    return None

def check_order_status(order_id):
    """
    주문 uuid를 통해 해당 주문의 상태(done, cancel, wait 등)를 반환합니다.
    """
    try:
        order_info = bithumb.get_order(order_id)
        if type(order_info) is dict and 'state' in order_info:
            return order_info['state']
        elif type(order_info) is dict and 'data' in order_info and 'state' in order_info['data']:
            return order_info['data']['state']
    except Exception as e:
        logger.error(f"Error checking order status {order_id}: {e}")
    return "unknown"

def compute_atr_grid_spacing(ticker, default_spacing=0.01, period=14):
    """
    최근 일봉 데이터를 기반으로 ATR을 계산하여 동적인 그리드 간격(퍼센트)을 반환합니다.
    시장의 변동성이 크면 간격을 넓히고, 작으면 좁히지만, 안전을 위해 최소/최대 한계치를 둡니다.
    """
    try:
        df = python_bithumb.get_ohlcv(ticker)
        if df is None or len(df) < period:
            return default_spacing
            
        # ATR 계산
        high_low = df['high'] - df['low']
        high_close = np.abs(df['high'] - df['close'].shift())
        low_close = np.abs(df['low'] - df['close'].shift())
        ranges = pd.concat([high_low, high_close, low_close], axis=1)
        true_range = np.max(ranges, axis=1)
        atr = true_range.rolling(period).mean().iloc[-1]
        
        current_price = df['close'].iloc[-1]
        atr_percent = atr / current_price
        
        # 그리드 간격은 보통 ATR의 50% ~ 100%를 사용 (여기서는 50%를 한 칸 간격으로 설정)
        dynamic_spacing = atr_percent * 0.5
        
        # 최소 0.5%, 최대 3%로 제한 
        dynamic_spacing = max(0.005, min(0.03, dynamic_spacing))
        
        logger.info(f"📊 Dynamic ATR Grid Spacing Calculated: {dynamic_spacing*100:.2f}% (Raw ATR%: {atr_percent*100:.2f}%)")
        return dynamic_spacing
        
    except Exception as e:
        logger.error(f"Error computing ATR for grid spacing: {e}")
        return default_spacing

# 6. Grid Initialization & Logic
def init_grid_bot():
    state = load_state()
    if state is not None:
        logger.info("Existing grid state found. Resuming grid trading...")
        return state

    logger.info("Initializing new grid state...")
    current_price = python_bithumb.get_current_price(TICKER)
    if not current_price:
        logger.error("Failed to fetch current price. Retrying...")
        time.sleep(5)
        return False
        
    # [전략 A 고도화] ATR을 이용한 동적 그리드 간격 계산
    dynamic_spacing = compute_atr_grid_spacing(TICKER, default_spacing=GRID_STEP_RATIO)

    # N개의 촘촘한 그리드 생성 (교차 매매를 위해 중앙 기준 위아래로 배치)
    half_grid = GRID_COUNT // 2
    grids = []
    
    # 하단 그리드 (매수 대기선)
    for i in range(half_grid, 0, -1):
        grids.append(current_price * (1 - dynamic_spacing * i))
    
    # 상단 그리드 (매도 대기선) 
    for i in range(1, GRID_COUNT - half_grid + 1):
        grids.append(current_price * (1 + dynamic_spacing * i))
    
    grids = sorted(list(set(grids)))
    mid_idx = len(grids) // 2
    start_idx = mid_idx - (GRID_COUNT + 1) // 2
    grids = grids[start_idx : start_idx + GRID_COUNT + 1]

    slots = {}
    eth_to_buy_krw = 0
    
    # Assign states to slots
    for i in range(GRID_COUNT):
        buy_price = grids[i]
        sell_price = grids[i+1]
        
        if sell_price <= current_price:
            slot_state = "KRW"
        else:
            slot_state = "ETH"
            eth_to_buy_krw += ORDER_KRW
            
        slots[str(i)] = {
            "state": slot_state,
            "buy_price": buy_price,
            "sell_price": sell_price,
            "order_id": None
        }

    # Market buy initial ETH required for the "ETH" slots
    logger.info(f"Grid Initialization: Buying {eth_to_buy_krw:,.0f} KRW worth of {TARGET_COIN} for initial sell limits.")
    if eth_to_buy_krw >= 5000: # 최소주문액 확인
        try:
            order = bithumb.buy_market_order(TICKER, eth_to_buy_krw)
            logger.info(f"Initial Market Buy Executed: {order}")
        except Exception as e:
            logger.error(f"Failed to execute initial market buy: {e}")
        time.sleep(3) 

    state = {
        "grids": grids,
        "slots": slots,
        "init_time": datetime.datetime.now().isoformat()
    }
    save_state(state)
    logger.info("Grid initialized and saved.")
    return state

def place_limit_order(slot_id, slot_data):
    """
    현재 slot_data의 state(KRW or ETH)에 맞는 지정가 주문을 생성합니다.
    (모든 주문은 지정가(Maker)로 진행)
    """
    time.sleep(0.3) # API Rate Limit 보호를 위한 지연 
    
    try:
        if slot_data["state"] == "KRW":
            price = slot_data["buy_price"]
            volume = round(ORDER_KRW / price, 4)
            logger.info(f"Placing BUY Limit: Slot {slot_id} / Price: {price:,.0f} / Vol: {volume}")
            order = bithumb.buy_limit_order(TICKER, price, volume)
            order_id = extract_order_id(order)
            
        elif slot_data["state"] == "ETH":
            price = slot_data["sell_price"]
            # 매도 볼륨은 매수했던 볼륨과 동일하게 설정하여 교차 차익(KRW)을 남김
            volume = round(ORDER_KRW / slot_data["buy_price"], 4) 
            logger.info(f"Placing SELL Limit: Slot {slot_id} / Price: {price:,.0f} / Vol: {volume}")
            order = bithumb.sell_limit_order(TICKER, price, volume)
            order_id = extract_order_id(order)
            
        if order_id:
            slot_data["order_id"] = order_id
            return True
        else:
            logger.warning(f"Failed to place limit order for Slot {slot_id}. Response: {order}")
    except Exception as e:
        logger.error(f"Exception during order placement for Slot {slot_id}: {e}")
        
    return False

# 7. Main Loop
def main():
    logger.info(f"Starting Bithumb Grid Trading Bot ({TICKER})... Total Budget: {TOTAL_BUDGET:,.0f} KRW")
    
    try:
        state = init_grid_bot()
    except Exception as e:
        logger.error(f"Fatal error during initialization: {e}")
        return

    slots = state["slots"]
    
    while True:
        try:
            open_ids = get_open_order_ids()
            if open_ids is None:
                # API 호출 실패시 대기 후 재시도
                time.sleep(5)
                continue
                
            current_price = get_current_price_retry(TICKER)
            
            # [전략 C] Out of Range 안전 장치
            if current_price:
                upper_bound = max(state["grids"])
                lower_bound = min(state["grids"])
                if current_price > upper_bound * 1.05 or current_price < lower_bound * 0.95:
                    logger.warning(f"🚨🚨 Price Out of Range! Current: {current_price:,.0f}, Range: [{lower_bound:,.0f} ~ {upper_bound:,.0f}]")
                    logger.warning("Canceling all orders and resetting grids with new dynamic ATR spacing...")
                    # 모든 미체결 취소 
                    for _slot_id, _slot_data in slots.items():
                        _oid = _slot_data.get("order_id")
                        if _oid and _oid in open_ids:
                            bithumb.cancel_order(_oid)
                            
                    # 그리드 상태 완전 초기화 후 다음 루프에서 재생성 유도 
                    if os.path.exists(STATE_FILE):
                        os.remove(STATE_FILE)
                    
                    logger.warning("Bot will rebuild the grid around the new price on next tick.")
                    # break 대신 빈 dict로 덮어씌워서 while루프 상단에서 새로 init되게 만듬
                    state = {}
                    break

            state_changed = False
            for slot_id, slot_data in slots.items():
                current_order_id = slot_data.get("order_id")
                
                # 주문 내역이 없는 슬롯은 새로 생성
                if not current_order_id:
                    if place_limit_order(slot_id, slot_data):
                        state_changed = True
                    continue
                
                # 열려있는 주문 목록에 없다면 체결(혹은 취소)된 것으로 판단
                if current_order_id not in open_ids:
                    # [전략 A 보완] 실제로 체결되었는지 빗썸 거래내역에 질의하여 검증
                    order_status = check_order_status(current_order_id)
                    time.sleep(0.3) # API Limit 방지
                    
                    if order_status == "done":
                        if slot_data["state"] == "KRW":
                            logger.info(f"🎉 Slot {slot_id} BUY Filled at {slot_data['buy_price']:,.0f} KRW! Reversing to SELL.")
                            slot_data["state"] = "ETH"
                        else:
                            logger.info(f"🎉 Slot {slot_id} SELL Filled at {slot_data['sell_price']:,.0f} KRW! Reversing to BUY.")
                            slot_data["state"] = "KRW"
                        
                        slot_data["order_id"] = None
                        state_changed = True
                        
                    elif order_status in ["cancel", "unknown"]:
                        # 주문이 사용자 수동/에러/기한만료로 취소된 경우 상태 유지하고 주문만 비움
                        logger.warning(f"⚠️ Slot {slot_id} Order {current_order_id} was CANCELED or is UNKNOWN. Re-submitting identical order.")
                        slot_data["order_id"] = None
                        state_changed = True
                        
                    # 다음 loop 에서 자동으로 해당 state에 맞는 주문이 다시 생성됨
                    
            if state_changed:
                save_state(state)
            
            # 1시간 주기로 Alive 상태 로깅 
            now = datetime.datetime.now()
            if now.minute == 0 and now.second < 10:
                cur_price = get_current_price_retry(TICKER)
                logger.info(f"[System Alive] Current Time: {now}. {TICKER} Price: {cur_price}. Tracking {GRID_COUNT} slots.")
                time.sleep(10)
                
            time.sleep(5) # 5초 간격 모니터링
            
        except Exception as e:
            logger.error(f"Unexpected Loop Error: {e}")
            logger.error(traceback.format_exc())
            time.sleep(10)

if __name__ == "__main__":
    main()
