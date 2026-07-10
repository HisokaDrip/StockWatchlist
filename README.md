# India Stock Watchlist

A stock watchlist app for tracking NSE and BSE prices. You can add stocks to a watchlist, refresh prices, and keep a daily SQLite history from the date you added each stock.

## Features

- Add stocks by symbol and exchange: `NSE` or `BSE`
- Fetch live quotes using Yahoo Finance symbols:
  - NSE: `RELIANCE.NS`, `TCS.NS`, `INFY.NS`
  - BSE: `RELIANCE.BO`, `TCS.BO`, `INFY.BO`
- Google Finance fallback:
  - NSE: enter normal symbols like `RELIANCE`
  - BSE: enter BSE scrip codes like `500325` for Reliance
- Backfill daily price history from the date you added a stock through today
- Store one price record per stock per trading day in `stock_watchlist.db`
- Save the previous trading day's close for each daily row
- Compare the latest price with the previous close
- Save a manual price if public quote sources throttle or block
- View saved history for each stock
- Export all saved history to CSV

## Requirements

- Python 3.10 or newer
- Internet connection when refreshing prices

No third-party Python packages are required.

## Run

For the new web UI, double-click:

```text
run_web_app.bat
```

Then open this in your browser if it does not open automatically:

```text
http://127.0.0.1:8765
```

## Use on Phone

1. Keep the computer and phone on the same Wi-Fi.
2. Double-click `run_web_app.bat` on the computer.
3. If Windows Firewall asks, allow access on the private/home network.
4. The app will show a **Phone link** near the top, for example:

```text
http://10.173.152.141:8765
```

5. Open that link in the phone browser.
6. Tap **Refresh All** to update prices.
7. Tap any stock card to view its daily history.

The older desktop UI is still available. Double-click:

```text
run_app.bat
```

## How to Use

1. Enter a stock code.
   - NSE examples: `RELIANCE`, `TCS`, `INFY`
   - BSE example: `500325` for Reliance
2. Choose `NSE` or `BSE`.
3. Click **Add**.
4. Click **Refresh All** to backfill daily prices from the date you added each stock.
5. Select a row to view its day-by-day history.
6. Use **Manual Price** if a public quote source blocks the refresh.
7. Use **Export** to save all history as a CSV file.

## Command Line

Run the web UI from PowerShell:

```powershell
python web_app.py
```

Run the older desktop UI from PowerShell:

```powershell
python main.py
```

## Notes

When you click **Refresh Selected** or **Refresh All**, the app tries to fill every missing trading day from the date you added the stock through today. For example, if you add a stock today and open the app after one week, refresh will try to save each trading day's price for that week.

The app first tries Yahoo Finance's public chart endpoint for daily history, then falls back to current quote sources when history is blocked. If a BSE symbol does not refresh, add the BSE scrip code instead of the stock symbol. You can also use **Manual Price** to save today's rate when a public source blocks automated requests.
