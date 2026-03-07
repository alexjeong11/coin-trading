import os
import json
import time
import subprocess
import python_bithumb
from fastapi import FastAPI, HTTPException
from dotenv import load_dotenv

# 1. Config & Env
load_dotenv()
CON_KEY = os.getenv("BITHUMB_CON_KEY")
SEC_KEY = os.getenv("BITHUMB_SEC_KEY")

if not CON_KEY or not SEC_KEY:
    raise RuntimeError("API Keys missing in .env")

bithumb = python_bithumb.Bithumb(CON_KEY, SEC_KEY)

TICKER = "KRW-ETH"
TARGET_COIN = "ETH"
INITIAL_BUDGET = 100000.0

app = FastAPI(title="Bithumb Trading Bot Controller")

# 2. Helper Functions
def get_balance(currency):
    balances = bithumb.get_balance(currency)
    if not balances or type(balances) is not list or len(balances) == 0:
        return 0.0
    return float(balances[0].get('balance', 0))

def get_open_orders():
    orders = bithumb.get_orders(TICKER)
    open_orders_list = []
    
    if type(orders) is list:
        open_orders_list = orders
    elif type(orders) is dict and 'data' in orders and type(orders['data']) is list:
        open_orders_list = orders['data']
        
    # 간소화된 데이터만 반환
    results = []
    for o in open_orders_list:
        results.append({
            "order_id": o.get('uuid') or o.get('order_id'),
            "type": o.get('type') or o.get('side'),
            "price": o.get('price'),
            "units": o.get('units') or o.get('volume'),
            "units_remaining": o.get('units_remaining') or o.get('remaining_volume')
        })
    return results

def cancel_all_orders():
    open_orders = get_open_orders()
    canceled_count = 0
    for order in open_orders:
        order_id = order.get("order_id")
        if order_id:
            res = bithumb.cancel_order(order_id)
            if res:
                canceled_count += 1
    return canceled_count

# 3. API Endpoints
@app.get("/status")
def status():
    """현재 자산 상태, 수익률 및 다가오는 미체결 주문 현황 반환"""
    try:
        # A. 시세 및 잔고 확인
        current_price = python_bithumb.get_current_price(TICKER)
        krw_bal = get_balance("KRW")
        eth_bal = get_balance(TARGET_COIN)
        
        # B. 총 자산 평가 및 수익률 계산
        eth_value_krw = eth_bal * current_price
        total_asset_krw = krw_bal + eth_value_krw
        yield_pct = ((total_asset_krw - INITIAL_BUDGET) / INITIAL_BUDGET) * 100
        
        # C. 미체결 주문 현황
        open_orders = get_open_orders()
        
        return {
            "success": True,
            "ticker": TICKER,
            "current_price_krw": current_price,
            "bot_initial_budget": INITIAL_BUDGET,
            "total_asset_krw": round(total_asset_krw, 0),
            "yield_percent": round(yield_pct, 2),
            "balances": {
                "krw": round(krw_bal, 0),
                "eth": round(eth_bal, 4)
            },
            "open_orders_count": len(open_orders),
            "open_orders": open_orders
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/stop")
def stop_bot():
    """매매 봇 프로세스 강제 종료 및 모든 미체결 주문 취소"""
    try:
        # 1. 프로세스 종료 (pkill)
        # bot.py를 실행중인 파이썬 프로세스 찾아서 종료 
        kill_cmd = "pkill -f 'python bot.py'"
        subprocess.run(kill_cmd, shell=True, check=False)
        
        # 2. 빗썸 서버에 등록된 모든 미체결 주문 취소
        canceled_orders = cancel_all_orders()
        
        # 3. 추가 안전장치: state 파일 백업/삭제 처리 (다음 시작시 초기화 유도)
        if os.path.exists("grid_state.json"):
            os.rename("grid_state.json", f"grid_state_backup_{int(time.time())}.json")
            
        return {
            "success": True,
            "message": "Bot process terminated and all orders canceled.",
            "canceled_order_count": canceled_orders
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

if __name__ == "__main__":
    import uvicorn
    # 외부 접근 차단을 위해 로컬호스트(127.0.0.1)에만 바인딩
    uvicorn.run(app, host="127.0.0.1", port=8000)
