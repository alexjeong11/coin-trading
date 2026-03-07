import os
import json
import time
import subprocess
import python_bithumb
from fastapi import FastAPI, HTTPException, Body
from dotenv import load_dotenv
from pydantic import BaseModel
import math

# 1. Config & Env
load_dotenv()
CON_KEY = os.getenv("BITHUMB_CON_KEY")
SEC_KEY = os.getenv("BITHUMB_SEC_KEY")

if not CON_KEY or not SEC_KEY:
    raise RuntimeError("API Keys missing in .env")

bithumb = python_bithumb.Bithumb(CON_KEY, SEC_KEY)

TICKER = "KRW-ETH"
TARGET_COIN = "ETH"
try:
    INITIAL_BUDGET = float(os.getenv("INITIAL_BUDGET", "100000"))
except ValueError:
    INITIAL_BUDGET = 100000.0

# Models
class BudgetUpdate(BaseModel):
    max_budget: float

app = FastAPI(title="Bithumb Trading Bot Controller")

# 2. Helper Functions
def get_balance_raw(currency):
    balances = bithumb.get_balances()
    if not balances or type(balances) is not list or len(balances) == 0:
        return None
    for bal in balances:
        if bal.get('currency') == currency:
            return bal
    return None

def get_total_balance(currency):
    bal = get_balance_raw(currency)
    if not bal:
        return 0.0
    return float(bal.get('balance', 0)) + float(bal.get('locked', 0))

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
        # A. 시세 확인
        current_price = python_bithumb.get_current_price(TICKER)
        
        # B. 잔고 구조체 (KRW, ETH)
        krw_bal = get_balance_raw("KRW") or {}
        eth_bal = get_balance_raw(TARGET_COIN) or {}
        
        krw_total = float(krw_bal.get('balance', 0)) + float(krw_bal.get('locked', 0))
        krw_avail = float(krw_bal.get('balance', 0))
        krw_locked = float(krw_bal.get('locked', 0))
        
        eth_total = float(eth_bal.get('balance', 0)) + float(eth_bal.get('locked', 0))
        eth_avail = float(eth_bal.get('balance', 0))
        eth_locked = float(eth_bal.get('locked', 0))
        
        # C. 빗썸 모바일 앱 스타일 손익 평가 (ETH 기준)
        avg_buy_price = float(eth_bal.get('avg_buy_price', 0))
        buy_amount = round(eth_total * avg_buy_price)
        eval_amount = round(eth_total * current_price)
        unrealized_pnl = eval_amount - buy_amount
        yield_percent = round((unrealized_pnl / buy_amount * 100), 2) if buy_amount > 0 else 0.0
        
        # 총 자산 (코인 평가금액 + 원화 보유 총합)
        total_asset_krw = krw_total + eval_amount

        # D. 주문 목록 
        open_orders = get_open_orders()
        formatted_orders = []
        for o in open_orders:
            formatted_orders.append({
                "order_id": o.get('order_id'),
                "type": o.get('type'),
                "price": o.get('price'),
                "units": o.get('units'),
                "units_remaining": o.get('units_remaining')
            })

        return {
            "success": True,
            "ticker": TICKER,
            "current_price_krw": current_price,
            "total_asset_krw": round(total_asset_krw),
            "asset_evaluation": {
                "coin": TARGET_COIN,
                "quantity": round(eth_total, 8),
                "avg_buy_price": round(avg_buy_price),
                "buy_amount_krw": buy_amount,
                "eval_amount_krw": eval_amount,
                "unrealized_pnl_krw": unrealized_pnl,
                "yield_percent": yield_percent
            },
            "krw_status": {
                "avail": round(krw_avail),
                "locked_in_orders": round(krw_locked),
                "total": round(krw_total)
            },
            "open_orders_count": len(formatted_orders),
            "open_orders": formatted_orders
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/config/budget")
def update_max_budget(config: BudgetUpdate = Body(...)):
    """Update the MAX_BUDGET ceiling in .env dynamically"""
    try:
        env_file = ".env"
        if not os.path.exists(env_file):
            raise HTTPException(status_code=500, detail=".env file not found")
            
        with open(env_file, 'r') as f:
            lines = f.readlines()
            
        budget_found = False
        with open(env_file, 'w') as f:
            for line in lines:
                if line.startswith("MAX_BUDGET="):
                    f.write(f"MAX_BUDGET={config.max_budget}\n")
                    budget_found = True
                else:
                    f.write(line)
            
            if not budget_found:
                f.write(f"\nMAX_BUDGET={config.max_budget}\n")
                
        # Update current process env just in case
        os.environ["MAX_BUDGET"] = str(config.max_budget)
        
        # grid_state.json을 지워서 bot.py의 다음 루프 때 init_grid_bot()가 강제로 돌아서 
        # 즉시 새로운 MAX_BUDGET을 적용하도록 트리거함.
        if os.path.exists("grid_state.json"):
            os.remove("grid_state.json")
            
        return {"success": True, "message": f"MAX_BUDGET updated to {config.max_budget:,.0f} KRW. Bot grid will reset shortly."}
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
