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
from dataclasses import dataclass
from typing import List, Dict, Any, Optional
from logging.handlers import TimedRotatingFileHandler
  
# 1. Logging Setup
logger = logging.getLogger("GridBot")
logger.setLevel(logging.INFO)
formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')

# Rotate log daily at midnight. backupCount=0 means never automatically delete old logs.
file_handler = TimedRotatingFileHandler("trade.log", when="midnight", interval=1, backupCount=0, encoding="utf-8")
file_handler.setFormatter(formatter)

console_handler = logging.StreamHandler()
console_handler.setFormatter(formatter)

logger.addHandler(file_handler)
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
STATE_FILE = "grid_state.json"

try:
    INITIAL_BUDGET = float(os.getenv("INITIAL_BUDGET", "100000"))
    MAX_BUDGET_CEILING = float(os.getenv("MAX_BUDGET", "200000")) # 투자 상한선 설정 (예: 최대 20만 원까지만 자동 투자)

    # [리밸런싱 기능] 일정 시간(시간) 동안 체결이 없으면 횡보/방치로 간주하고 그물망 재가설 (기본: 6시간)
    RESET_TIMER_HOURS = float(os.getenv("RESET_TIMER_HOURS", "6"))

    GRID_COUNT = int(os.getenv("GRID_COUNT", "10"))
    GRID_STEP_RATIO = float(os.getenv("GRID_STEP_RATIO", "0.01"))
except ValueError:
    logger.error("Invalid configuration in .env. Resorting to defaults.")
    INITIAL_BUDGET = 100000.0
    MAX_BUDGET_CEILING = 200000.0
    RESET_TIMER_HOURS = 6.0
    GRID_COUNT = 10
    GRID_STEP_RATIO = 0.01

# 4. State Management
@dataclass
class GridBotState:
    grids: List[float]
    slots: Dict[str, Any]
    init_time: str
    active_budget: float
    lot_size: float
    last_trade_time: float = 0.0  # 마지막 체결 시간 기록 (리밸런싱용)

def load_state() -> Optional[GridBotState]:
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, "r") as f:
                data = json.load(f)
                return GridBotState(
                    grids=data.get('grids', []), 
                    slots=data.get('slots', {}),
                    init_time=data.get('init_time', datetime.datetime.now().isoformat()),
                    active_budget=data.get('active_budget', INITIAL_BUDGET),
                    lot_size=data.get('lot_size', INITIAL_BUDGET / GRID_COUNT),
                    last_trade_time=data.get('last_trade_time', time.time())
                )
        except Exception as e:
            logger.error(f"Error loading state: {e}")
    return None

def save_state(state: GridBotState):
    try:
        data = {
            'grids': state.grids,
            'slots': state.slots,
            'init_time': state.init_time,
            'active_budget': state.active_budget,
            'lot_size': state.lot_size,
            'last_trade_time': state.last_trade_time
        }
        with open(STATE_FILE, "w") as f:
            json.dump(data, f, indent=4)
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
        
        # [최적화] 최소 간격을 기존 0.5%에서 0.25%로 대폭 하향하여 횡보장에서도 회전율을 극대화
        # 단, 차익 실현을 위해 빗썸 '수수료 쿠폰(0.04%)' 사용이 필수적임 (왕복 수수료 0.08% 제외하고 0.17%의 순수익 창출)
        dynamic_spacing = max(0.0025, min(0.005, dynamic_spacing))
        
        logger.info(f"📊 Dynamic ATR Grid Spacing Calculated: {dynamic_spacing*100:.2f}% (Raw ATR%: {atr_percent*100:.2f}%)")
        logger.info(f"💡 [Tip] 촘촘한 (0.25%) 간격 매매는 빗썸 '수수료 쿠폰'이 적용되어야 순수익이 극대화됩니다.")
        return dynamic_spacing
        
    except Exception as e:
        logger.error(f"Error computing ATR for grid spacing: {e}")
        return default_spacing

def round_to_tick(price):
    """
    빗썸 호가단위(Tick Size)에 맞게 가격을 반올림합니다.
    (예: 100만원 이상 -> 1000원 단위)
    """
    if price >= 1000000:
        return round(price / 1000) * 1000
    elif price >= 100000:
        return round(price / 100) * 100
    elif price >= 10000:
        return round(price / 10) * 10
    elif price >= 1000:
        return round(price)
    elif price >= 100:
        return round(price, 1)
    elif price >= 10:
        return round(price, 2)
    return round(price, 4)

# 6. Grid Initialization & Logic
def init_grid_bot() -> Optional[GridBotState]:
    state = load_state()
    if state:
        logger.info("Existing grid state found. Resuming grid trading...")
        return state

    logger.info("Initializing new grid state with Auto-Compounding...")
    current_price = python_bithumb.get_current_price(TICKER)
    if not current_price:
        logger.error("Failed to fetch current price. Retrying...")
        time.sleep(5)
        return None
        
    # [전략 C 고도화] 자산 평가 기반 (Auto-Compounding) + 상한선 구조
    active_budget = INITIAL_BUDGET
    order_krw = active_budget / GRID_COUNT
    try:
        krw_total = 0.0
        coin_total = 0.0
        avg_buy_price = 0.0
        bals = bithumb.get_balances()
        if type(bals) is list:
            for b in bals:
                if b.get('currency') == 'KRW':
                    krw_total = float(b.get('balance', 0)) + float(b.get('locked', 0))
                elif b.get('currency') == TARGET_COIN:
                    coin_total = float(b.get('balance', 0)) + float(b.get('locked', 0))
                    avg_buy_price = float(b.get('avg_buy_price', 0))
            
            # evaluate total asset
            total_eval_krw = krw_total + (coin_total * current_price)
            
            # --- DAILY ASSET SNAPSHOT (For Web Dashboard) ---
            try:
                today_str = datetime.datetime.now().strftime("%Y-%m-%d")
                history_file = "asset_history.json"
                history = []
                if os.path.exists(history_file):
                    with open(history_file, 'r') as f:
                        try:
                            history = json.load(f)
                        except:
                            pass
                if not history or history[-1].get("date") != today_str:
                    history.append({
                        "date": today_str,
                        "total_asset": round(total_eval_krw),
                        "eth_price": float(current_price)
                    })
                    with open(history_file, 'w') as f:
                        json.dump(history, f, indent=4)
                    logger.info(f"📊 Daily Asset Snapshot Saved - Date: {today_str}, Total: {total_eval_krw:,.0f} KRW")
            except Exception as e:
                logger.error(f"Error saving daily asset snapshot: {e}")
            # Active budget is capped by MAX_BUDGET_CEILING
            active_budget = min(total_eval_krw, MAX_BUDGET_CEILING)
            
            # 수수료 및 호가단위 버퍼를 위해 평가 자산의 95%만 활용합니다
            active_budget = active_budget * 0.95
            
            # Ensure it doesn't drop below the initial minimum (e.g., 10만원)
            active_budget = max(active_budget, INITIAL_BUDGET)
            
            order_krw = active_budget / GRID_COUNT
            logger.info(f"💰 Auto-Compounded Budget: {active_budget:,.0f} KRW (Total Asset: {total_eval_krw:,.0f} KRW, Ceiling: {MAX_BUDGET_CEILING:,.0f} KRW)")
            logger.info(f"💰 New Lot Size per grid: {order_krw:,.0f} KRW")
        else:
            logger.warning("Could not fetch balances, using INITIAL_BUDGET for active_budget.")
            active_budget = INITIAL_BUDGET
            order_krw = active_budget / GRID_COUNT
            
    except Exception as e:
        logger.error(f"Error evaluating assets for auto-compounding: {e}. Using INITIAL_BUDGET.")
        active_budget = INITIAL_BUDGET
        order_krw = active_budget / GRID_COUNT

    # [전략 A 고도화] ATR을 이용한 동적 그리드 간격 계산
    dynamic_spacing = compute_atr_grid_spacing(TICKER, default_spacing=GRID_STEP_RATIO)

    # N개의 슬롯을 위한 GRID_COUNT + 1 개의 촘촘한 그리드 선 생성
    num_price_points = GRID_COUNT + 1
    half_points = num_price_points // 2
    grids = []
    
    # 1. 하단 가격대 (가변 탄력성 방어막 - Elastic Buy Grid)
    down_grids = []
    down_accumulated = 0.0
    down_growth = 1.0
    for i in range(1, half_points + 1):
        if i <= 2:
            step = dynamic_spacing
        else:
            down_growth *= 1.5
            step = dynamic_spacing * down_growth
        
        down_accumulated += step
        down_grids.insert(0, current_price * (1 - down_accumulated))
        
    grids.extend(down_grids)
        
    # 2. 현재가 포함
    grids.append(current_price)
    
    # 3. 상단 가격대 (하이퍼 스캘핑 + 능동형 코어 장기 홀딩)
    up_accumulated = 0.0
    up_growth = 1.0
    for i in range(1, num_price_points - half_points):
        if i <= 2:
            # 1~2번 익절 슬롯은 0.15%~0.2% 극초단타 마진 고정
            step = min(0.002, dynamic_spacing)
        else:
            # 3번 이상 물량(코어)은 1.5배 지수 확장으로 보합/반등장에서 더 자주 익절하여 자본 회전율 향상
            up_growth *= 1.5 
            step = dynamic_spacing * up_growth
            
        up_accumulated += step
        grids.append(current_price * (1 + up_accumulated))
    
    # 빗썸 호가단위에 맞춰 반올림 후 중복제거
    grids = [round_to_tick(p) for p in grids]
    grids = sorted(list(set(grids)))
    
    # 길이가 의도한 것 보다 길어졌을 때만 자르기 (floating point 등)
    if len(grids) > num_price_points:
        mid_idx = len(grids) // 2
        start_idx = max(0, mid_idx - num_price_points // 2)
        grids = grids[start_idx : start_idx + num_price_points]

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
            
            # [No-Loss Guarantee] 절대 평단가+0.25% 이하로는 매도망을 생성하지 않음
            if avg_buy_price > 0:
                min_sell_price = round_to_tick(avg_buy_price * 1.0025)
                if sell_price < min_sell_price:
                    sell_price = min_sell_price
            
            eth_to_buy_krw += order_krw # Use dynamic order_krw
            
        slots[str(i)] = {
            "state": slot_state,
            "buy_price": buy_price,
            "sell_price": sell_price,
            "order_id": None
        }

    # Market buy initial ETH required for the "ETH" slots
    # eth_to_buy_units 는 소수점 4자리까지만 허용 (빗썸 최소수량 정책)
    eth_to_buy_units = max(0.0, round(float(eth_to_buy_krw / current_price) - coin_total, 4))
            
    # eth_to_buy_units 가 0보다 크면 시장가 매수 진행
    # 방어 2: 최소 주문금액(5000 KRW) 미만인 경우 빗썸에서 에러가 발생하므로, 
    # 필요 금액이 5000원이 조금 넘도록(5100원 기준) 강제 보정
    krw_needed_to_buy = eth_to_buy_units * current_price
    if 0 < krw_needed_to_buy < 5100:
        eth_to_buy_units = round(5100 / current_price, 4)
        logger.warning(f"Initial buy amount adjusted to meet 5000 KRW minimum: {eth_to_buy_units} ETH")

    logger.info(f"Grid Initialization: Need to buy {eth_to_buy_units:.4f} ETH ({krw_needed_to_buy:,.0f} KRW) out of {coin_total:.4f} already owned.")
    
    if krw_needed_to_buy >= 5000: # 최소주문액 확인
        try:
            order = bithumb.buy_market_order(TICKER, krw_needed_to_buy)
            logger.info(f"Initial Market Buy Executed: {order}")
        except Exception as e:
            logger.error(f"Failed to execute initial market buy: {e}")
        time.sleep(3) 

    # 새 상태 저장 시 last_trade_time을 현재 시간으로 세팅하여 타이머 초기화 (시작점)
    new_state = GridBotState(
        grids=grids, 
        slots=slots,
        init_time=datetime.datetime.now().isoformat(),
        active_budget=active_budget, 
        lot_size=order_krw,
        last_trade_time=time.time()
    )
    save_state(new_state)
    logger.info("Grid initialized and saved.")
    return new_state

def place_limit_order(slot_id, slot_data, bot_state: GridBotState):
    """
    현재 slot_data의 state(KRW or ETH)에 맞는 지정가 주문을 생성합니다.
    (모든 주문은 지정가(Maker)로 진행)
    """
    time.sleep(0.3) # API Rate Limit 보호를 위한 지연 
    
    order_krw = bot_state.lot_size

    try:
        order_id = None # Initialize order_id
        if slot_data["state"] == "KRW":
            # KRW를 가지고 매수(Buy) 대기 중
            order_price = float(slot_data["buy_price"])
            order_volume = round(float(order_krw / order_price), 4) # 소수점 4자리
            result = bithumb.buy_limit_order(TICKER, order_price, order_volume)
            logger.info(f"Slot {slot_id} - Placed BUY limit at {order_price:,.0f} KRW (Vol: {order_volume}).")
            order_id = extract_order_id(result)
            
        elif slot_data["state"] == "ETH":
            # ETH를 가지고 매도(Sell) 대기 중
            # 매도 수량은 처음 샀던 가격 기준으로 산정된 주문금액(lot_size) 만큼을 다시 파는 개념
            # 즉 11,000원어치를 샀으면, 팔때도 11,000원어치만 판다 (나머지 차액은 코인 수량으로 킵됨 = 복리 스노우볼)
            order_price = float(slot_data["sell_price"])
            order_volume = round(float(order_krw / slot_data["buy_price"]), 4) # 소수점 4자리
            result = bithumb.sell_limit_order(TICKER, order_price, order_volume)
            logger.info(f"Slot {slot_id} - Placed SELL limit at {order_price:,.0f} KRW (Vol: {order_volume}).")
            order_id = extract_order_id(result)
            
        if order_id:
            slot_data["order_id"] = order_id
            return True
        else:
            logger.warning(f"Failed to place limit order for Slot {slot_id}. Response: {result}")
            if "insufficient_funds" in str(result).lower() or "주문가능한" in str(result):
                slot_data["insufficient_funds"] = True
                logger.warning(f"Slot {slot_id} paused due to insufficient funds.")
    except Exception as e:
        error_msg = str(e)
        if "insufficient_funds" in error_msg.lower() or "주문가능한" in error_msg:
            slot_data["insufficient_funds"] = True
            logger.warning(f"Slot {slot_id} paused due to insufficient funds. Will retry when balance changes.")
        else:
            logger.error(f"Exception during order placement for Slot {slot_id}: {e}")
        
    return False

def cancel_all_orders(bot_state: GridBotState):
    """모든 미체결 주문을 취소합니다."""
    logger.info("Canceling all open orders...")
    open_ids = get_open_order_ids()
    if open_ids:
        for order_id in open_ids:
            try:
                bithumb.cancel_order(order_id)
                logger.info(f"Canceled order: {order_id}")
                time.sleep(0.1) # API rate limit
            except Exception as e:
                logger.error(f"Error canceling order {order_id}: {e}")
    # Clear order_ids from slots in state
    for slot_data in bot_state.slots.values():
        slot_data["order_id"] = None
    save_state(bot_state)


# 7. Main Loop
def main():
    # TOTAL_BUDGET is no longer a global constant, it's dynamic.
    # We will log the active_budget from the state after initialization.
    
    try:
        state = init_grid_bot()
        if not state: # init_grid_bot can return False on failure
            logger.error("Failed to initialize grid bot. Exiting.")
            return
        bot_state = state # Use bot_state for clarity as it's a dataclass instance
            
        logger.info(f"Starting Bithumb Grid Trading Bot ({TICKER})... Active Budget: {bot_state.active_budget:,.0f} KRW")
    except Exception as e:
        logger.error(f"Fatal error during initialization: {e}")
        return

    
    while True:
        try:
            open_ids = get_open_order_ids()
            if open_ids is None:
                # API 호출 실패시 대기 후 재시도
                time.sleep(5)
                continue
                
            current_price = get_current_price_retry(TICKER)
            if not current_price:
                logger.warning("Could not get current price, skipping this loop iteration.")
                time.sleep(5)
                continue
            
            # [전략 C] Out of Range 안전 장치
            upper_bound = max(bot_state.grids)
            lower_bound = min(bot_state.grids)
            if current_price > upper_bound * 1.05 or current_price < lower_bound * 0.95:
                logger.warning(f"🚨🚨 Price Out of Range! Current: {current_price:,.0f}, Range: [{lower_bound:,.0f} ~ {upper_bound:,.0f}]")
                logger.warning("Canceling all orders and resetting grids with new dynamic ATR spacing...")
                
                cancel_all_orders(bot_state)
                
                # 그리드 상태 완전 초기화 후 다음 루프에서 재생성 유도 
                if os.path.exists(STATE_FILE):
                    os.remove(STATE_FILE)
                
                logger.warning("Bot will rebuild the grid around the new price on next tick.")
                # break 대신 빈 dict로 덮어씌워서 while루프 상단에서 새로 init되게 만듬
                bot_state = init_grid_bot() # This will cause init_grid_bot to be called again
                if not bot_state: # If re-initialization fails
                    logger.error("Failed to re-initialize grid bot after price out of range. Exiting.")
                    return
                continue # Restart loop with new state

            state_changed = False
            for slot_id, slot_data in bot_state.slots.items():
                current_order_id = slot_data.get("order_id")
                
                # 주문 내역이 없는 슬롯은 새로 생성
                if not current_order_id:
                    if slot_data.get("insufficient_funds"):
                        continue # 자금 부족으로 일시정지된 슬롯은 건너뜀
                        
                    if place_limit_order(slot_id, slot_data, bot_state):
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
                        
                        # ⏰ [리밸런싱 타임 리셋] 체결이 발생했으므로 방치 타이머를 지금 이 순간으로 갱신
                        bot_state.last_trade_time = time.time()

                        # 하나의 그리드라도 체결되면 자금 상황이 바뀌었으므로 정지된 모든 슬롯의 플래그를 해제하고 재시도
                        for s_id in bot_state.slots:
                            if bot_state.slots[s_id].get("insufficient_funds"):
                                bot_state.slots[s_id]["insufficient_funds"] = False
                                
                        state_changed = True
                        
                    elif order_status in ["cancel", "unknown"]:
                        # 주문이 사용자 수동/에러/기한만료로 취소된 경우 상태 유지하고 주문만 비움
                        logger.warning(f"⚠️ Slot {slot_id} Order {current_order_id} was CANCELED or is UNKNOWN. Re-submitting identical order.")
                        slot_data["order_id"] = None
                        state_changed = True
                        
                    # 다음 loop 에서 자동으로 해당 state에 맞는 주문이 다시 생성됨
                    
            if state_changed:
                save_state(bot_state)
            
            # 1시간 주기로 Alive 상태 로깅 
            now = datetime.datetime.now()
            if now.minute == 0 and now.second < 10:
                cur_price = get_current_price_retry(TICKER)
                logger.info(f"[System Alive] Current Time: {now}. {TICKER} Price: {cur_price}. Tracking {len(bot_state.slots)} slots.")
                save_state(bot_state) # Ensure state is saved hourly even if no trades
                time.sleep(10) # Sleep to avoid multiple logs within the same minute
                
            # ------------------------------------------------------------------
            # [방어 2: 동적 리밸런싱 (Time-based Auto-Reset)]
            # 마지막 체결(done) 시점으로부터 설정된 시간(RESET_TIMER_HOURS)이 지나면, 장기간 무거래 횡보상태(Zombie Grid)로 간주.
            # 기존 체결 안된 그물을 모두 치우고 수수료 소비 없이 현재가를 중심으로 그물망을 새로 칠 준비를 합니다.
            # ------------------------------------------------------------------
            current_time = time.time()
            hours_since_last_trade = (current_time - bot_state.last_trade_time) / 3600.0
            
            if hours_since_last_trade >= RESET_TIMER_HOURS:
                logger.warning(f"⏳ [PARTIAL REBALANCING] Idle for {hours_since_last_trade:.1f} hours. Migrating KRW slots to hover below {current_price:,.0f} KRW.")
                
                cancel_all_orders(bot_state)
                
                krw_slots = [s_id for s_id, s_data in bot_state.slots.items() if s_data["state"] == "KRW"]
                if krw_slots:
                    krw_slots = sorted(krw_slots, key=int)
                    
                    dynamic_spacing = compute_atr_grid_spacing(TICKER, default_spacing=GRID_STEP_RATIO)
                    new_buy_grids = []
                    down_accumulated = 0.0
                    down_growth = 1.0
                    for i in range(len(krw_slots)):
                        if i < 2:
                            step = min(0.0025, dynamic_spacing)
                        else:
                            down_growth *= 1.5
                            step = dynamic_spacing * down_growth
                        down_accumulated += step
                        new_buy_grids.append(current_price * (1 - down_accumulated))
                    
                    new_buy_grids = [round_to_tick(p) for p in new_buy_grids]
                    new_buy_grids.sort(reverse=True)
                    
                    for i, s_id in enumerate(krw_slots):
                        if i < len(new_buy_grids):
                            buy_p = new_buy_grids[i]
                            sell_p = buy_p * (1 + min(0.005, dynamic_spacing * 1.5))
                            
                            bot_state.slots[s_id]["buy_price"] = buy_p
                            bot_state.slots[s_id]["sell_price"] = round_to_tick(sell_p)
                    
                    logger.info(f"✅ Executed Trailing Scalp: {len(krw_slots)} KRW slots migrated. Existing ETH positions are 100% PRESERVED!")
                else:
                    logger.info("No KRW slots available for trailing. All funds are in ETH waiting for high targets.")
                    
                bot_state.last_trade_time = current_time
                save_state(bot_state)
                continue

            time.sleep(5) # 5초 간격 모니터링
            
        except Exception as e:
            logger.error(f"Unexpected Loop Error: {e}")
            logger.error(traceback.format_exc())
            time.sleep(10)

if __name__ == "__main__":
    main()
