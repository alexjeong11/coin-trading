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

Parse the JSON response and present it clearly to the user. Mention:
1. Current ETH Price
2. Total Asset in KRW and Yield Percentage
3. Balances (KRW & ETH)
4. Open Grid Orders Count

## Emergency Stop & Cancel All Orders

If the user asks to stop trading, shut down the bot, or cancel all orders, hit the `/stop` endpoint:

```bash
curl -X POST -s http://127.0.0.1:8000/stop
```

Parse the JSON response to confirm to the user that the background process was killed and how many orders were successfully canceled.
