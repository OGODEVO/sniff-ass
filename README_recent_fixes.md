# Recent Trading Bot Safety Upgrades

This bot has been upgraded with strict safety protocols to reduce losses and improve trade execution quality.

## Execution Upgrades
* **Instant Fills (FAK):** Limit orders now use Fill-And-Kill (FAK) rather than Good-Till-Cancelled (GTC). Small fractional 2¢ premiums cross the bid-ask spread instantly to guarantee execution, or fail safely if liquidity disappears.
* **L2 Order Book Accuracy:** The CLOB WebSocket stream now reconstructs the full L2 book (`clob_stream.py`), preventing the bot from relying on stale or hallucinated prices between polling cycles.
* **Sub-Second Latency:** Decoupled slow REST API polling from the fast evaluation loop. The bot now reacts to Binance websocket ticks in 0.5 seconds to beat market makers to the spread.

## Risk Management Gating
* **`MIN_EDGE = 0.09`**: Adjusted up from 0.07 to properly absorb the 2-cent spread-crossing premium.
* **`MIN_PRICE = 0.15`**: Blocks "lottery ticket" trades on assets that have already effectively lost.
* **`MAX_EDGE = 0.45`**: Blocks hallucinated trades where the math model deviates too wildly from market consensus. 
* **`MAX_SAME_DIRECTION = 2`**: Correlation block that prevents betting the entire bankroll on 4 "UP" bets simultaneously during crypto market moves.
* **Indicator Validation**: Failsafes against Binance WebSocket disconnects (e.g. `HTTP 451` errors) by refusing to trade if RSI defaults to 50.
