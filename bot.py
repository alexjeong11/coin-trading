import os
import time
import json
import logging
import datetime
import traceback
import python_bithumb
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

# 6. Grid Initialization & Logic
def init_grid_bot():
    state = load_state()
    if state is not None:
        logger.info("Existing grid state found. Resuming grid trading...")
        return state

    logger.info("Initializing new grid state...")
    current_price = get_current_price_retry(TICKER)
    if not current_price:
        raise Exception("Cannot fetch current price for initialization.")
        
    base_price = round(current_price / 1000) * 1000
    grid_spacing_krw = round((base_price * GRID_STEP_RATIO) / 1000) * 1000
    
    # 10 Price Lines
    grids = []
    half_lines = (GRID_COUNT + 1) // 2
    for i in range(-half_lines, half_lines + 1):
        grids.append(base_price + i * grid_spacing_krw)
    
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
                    if slot_data["state"] == "KRW":
                        logger.info(f"🎉 Slot {slot_id} BUY Filled at {slot_data['buy_price']:,.0f} KRW! Reversing to SELL.")
                        slot_data["state"] = "ETH"
                    else:
                        logger.info(f"🎉 Slot {slot_id} SELL Filled at {slot_data['sell_price']:,.0f} KRW! Reversing to BUY.")
                        slot_data["state"] = "KRW"
                        
                    slot_data["order_id"] = None
                    state_changed = True
                    # 다음 loop 에서 자동으로 반대 주문이 생성됨
                    
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
