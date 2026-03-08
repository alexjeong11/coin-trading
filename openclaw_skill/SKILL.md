---
name: bithumb-bot
description: Bithumb Trading Bot Status & Control Skill
---

# Bithumb Trading Bot Controller

You can monitor and control the local Bithumb trading bot using its HTTP API. The bot operates on `KRW-ETH` using a Grid Trading strategy.

## Check Status & Yield

To check the current status, asset value, yield (%), and open orders, use the following local API:

```bash
curl -s http://127.0.0.1:8000/status
```
### GET `/status`
Returns the Bithumb app-style real-time status of the bot's assets and open orders.

**Response Example:**
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

**Response Example:**
```json
{
  "success": true,
  "message": "MAX_BUDGET updated to 300,000 KRW. Bot grid will reset shortly."
}
```

---

## Behavior Rules

1. **Status Checks**: When the user asks for the status, call the `/status` endpoint and format the JSON response nicely, showing the current price, asset evaluation (quantity, avg buy price, PnL, yield), and the KRW status.
2. **Budget Management**: If the user asks to "reinvest up to X amount" or "change the investment limit", use the `POST /config/budget` endpoint with the requested `max_budget` in KRW.
3. **Graceful Pauses**: Note that if some grid slots are missing funds, the bot will gracefully "pause" them in the background until funds are available again.
4. **Emergency Stop**: Use `POST /stop` if the user wants to kill the bot and cancel all orders. Confirm to the user how many orders were canceled.
