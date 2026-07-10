# Hosted Phone Version

This version is for using the stock watchlist only from a phone, without keeping a computer running.

## Easiest Hosting Option

Use Render:

1. Create an account at `https://render.com`.
2. Create a new **Web Service**.
3. Upload/connect this `IndiaStockWatchlist` folder.
4. Use these settings:
   - Build command: `pip install -r requirements.txt`
   - Start command: `python web_app.py`
5. After deploy, Render gives a public website link.
6. Open that link on the phone.

## Notes

- The stock list is preloaded automatically on first start.
- Tap **Refresh All** to fetch current prices.
- Tap a stock card to see daily close history.
- A free host may sleep when unused, so the first open can take a little time.
- The current simple setup stores data in SQLite on the host. For serious long-term public use, add persistent storage or a managed database.
