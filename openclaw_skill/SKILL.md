---
name: bithumb-bot
description: Bithumb Trading Bot Status & Control Skill
---

# Bithumb Trading Bot Controller

You can monitor and control the local Bithumb trading bot using its HTTP API. The bot operates on `KRW-ETH` using an aggressively tight Grid Trading strategy.

### 💡 Core Technical Context (Crucial for AI Assistant)
1. **Asymmetric Grid Spacing**: The bot uses Dynamic ATR to calculate grid boundaries. The buy-side (lower grid) is clamped extremely tight (**0.25% to 0.5%**) for high-frequency defense. However, the sell-side (upper grid) scales **exponentially by a 1.5x factor** per slot (e.g., 0.5% -> 0.75% -> 1.1% -> 1.7%...). This allows for high-frequency flipping in sideways markets and maximum profit taking ("home runs") during strong uptrends.
   - **Why 5-sec Polling?**: To maximize trades when prices flash above/below the 0.25% boundary. Wicks (꼬리) pass very fast, making sub-10s polling essential to win the Maker queue.
   - **Fee Discretion**: The 0.25% margin works strictly because the host account uses Bithumb's fee discount (0.04% per trade). A 'standard' fee plan would eat all profit at this spacing.
   - **Time-based Rebalancing (Zombie Grid Prevention)**: If no single trade happens for 6 hours (`RESET_TIMER_HOURS=6`), the bot cancels all current maker orders and recalculates the grid perfectly hugging the modern current price. Note that it will NEVER dump coins at market sell to reset; it mathematically covers its bags securely inside the new order-grid.
2. **Fee Coupon Mandatory**: Because the grid is incredibly tight (0.25%), the user *must* apply a Bithumb Fee Discount Coupon (0.04% fee). If normal fees apply (0.25%), trades will be unprofitable!
3. **Execution Loop**: The bot polls Bithumb every **5 seconds**. This is NOT a bug or resource waste; it is absolutely necessary to catch rapid coin "wicks" and immediately place counter-orders on a 0.25% margin.
4. **Auto-Compounding**: The bot recalculates the grid based on the user's total active Bithumb portfolio value, dynamically increasing lot sizes for snowball profits (capped by `MAX_BUDGET`).
5. **Log Rotation**: The bot automatically rotates `trade.log` and `api.log` daily at midnight. Old logs are purposely kept indefinitely (`backupCount=0`) and must be deleted manually by the user to manage local disk space.

## 1. Check Status & Yield

To check the current status, asset value, yield (%), and open orders, use the following local API:

```bash
curl -s http://127.0.0.1:8000/status
```
### GET `/status`
Returns the Bithumb app-style real-time status of the bot's assets and open orders.

**⚠️ CRITICAL**: You MUST execute the curl command to get the actual status. DO NOT USE THE RESPONSE EXAMPLE BELOW AS REAL DATA.

**Response Example (MOCK DATA ONLY):**
```json
{
  "success": true,
  "ticker": "KRW-ETH",
  "current_price_krw": 2917000.0,
  "total_asset_krw": 110773,
  "asset_evaluation": {
    "coin": "ETH",
    "quantity": 0.01770000,
    "avg_buy_price": 2912859,
    "buy_amount_krw": 51558,
    "eval_amount_krw": 51631,
    "unrealized_pnl_krw": 73,
    "yield_percent": 0.14
  },
  "krw_status": {
    "avail": 3538,
    "locked_in_orders": 55616,
    "total": 59154
  },
  "open_orders_count": 9,
  "open_orders": [
    {
      "order_id": "C010...",
      "type": "ask",
      "price": "3034000",
      "units": "0.0037",
      "units_remaining": "0.0037"
    }
  ]
}
## Emergency Stop & Cancel All Orders

### POST `/stop`
Gracefully halts the bot by preventing new orders, canceling all open orders, and killing the process.

**Usage:**
```bash
curl -X POST http://127.0.0.1:8000/stop
```

### POST `/config/budget`
Dynamic Auto-Compounding Ceiling Control. Modifies the `MAX_BUDGET` (KRW) restriction for the bot while it is running. The bot will automatically reset its grid to use this new ceiling.

**Usage:**
```bash
curl -X POST http://127.0.0.1:8000/config/budget \
-H "Content-Type: application/json" \
-d '{"max_budget": 300000}'
```

**⚠️ CRITICAL**: You MUST execute the curl command to get the actual status. DO NOT USE THE RESPONSE EXAMPLE BELOW AS REAL DATA.

**Response Example (MOCK DATA ONLY):**
```json
{
  "success": true,
  "message": "MAX_BUDGET updated to 300,000 KRW. Bot grid will reset shortly."
}
```

## 2. Local Bot Lifecycle Management (Shell Commands)

If the API is down or the user asks to "start", "restart", or "hard stop" the bot, you must execute the following commands in the project directory (`/Users/jeongcheol/Documents/ai-projects/coin-trading`):

### Start / Restart Bot
Use the automated deployment script which gracefully kills old processes, syncs this skill file, and starts the system in the background via `nohup`.
```bash
./deploy.sh
```

### Hard Stop (Kill Processes)
If the `/stop` API fails or the user specifically asks to kill the background scripts without canceling Bithumb orders.
```bash
pkill -f 'bot\.py'
pkill -f 'api_server\.py'
```

### Check Logs (Debugging)
To view the bot's live trading decisions or system errors:
```bash
# View trading logic and order placements
tail -n 30 trade.log

# View system/API server execution output
tail -n 30 nohup.out
```

---

## Behavior Rules

1. **Status Checks**: When the user asks for the status, call the HTTP `/status` endpoint and format the JSON response nicely, showing the current price, asset evaluation (quantity, avg buy price, PnL, yield), and the KRW status.
2. **Budget Management**: If the user asks to "reinvest up to X amount" or "change the investment limit", use the `POST /config/budget` endpoint with the requested `max_budget` in KRW.
3. **Graceful Pauses**: Note that if some grid slots are missing funds, the bot will gracefully "pause" them in the background until funds are available again.
4. **Emergency Stop**: Try using `POST /stop` first if the user wants to kill the bot and cancel all orders. Confirm how many orders were canceled. If the API is dead, use the `pkill` bash command.
5. **Start/Restart**: If the user asks to start the bot, run `./deploy.sh` in the project directory.
6. **Logs**: If the user asks what the bot is doing or why it failed, read `trade.log` or `nohup.out`.
