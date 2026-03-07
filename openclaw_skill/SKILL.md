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

If the user asks### POST `/stop`
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

## Behavior Rulesponse to confirm to the user that the background process was killed and how many orders were successfully canceled.
