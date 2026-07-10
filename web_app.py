import json
import os
import re
import socket
import threading
import webbrowser
from datetime import date, datetime
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from main import DB_PATH, StockStore, YahooFinanceClient


HOST = "0.0.0.0"
PORT = int(os.environ.get("PORT", "8765"))
APP_DIR = Path(__file__).resolve().parent


store = StockStore(DB_PATH)
client = YahooFinanceClient()
refresh_lock = threading.Lock()

PRELOADED_STOCKS = [
    ("PERSISTENT", "NSE", "Persistent Systems"),
    ("KEI", "NSE", "KEI Industries"),
    ("OLAELEC", "NSE", "Ola Electric Mobility"),
    ("TATASTEEL", "NSE", "Tata Steel"),
    ("EXIDEIND", "NSE", "Exide Industries"),
    ("J&KBANK", "NSE", "Jammu and Kashmir Bank"),
    ("BANDHANBNK", "NSE", "Bandhan Bank"),
    ("ZEEL", "NSE", "Zee Entertainment Enterprises"),
    ("TITAN", "NSE", "Titan Company"),
    ("MICEL", "NSE", "MIC Electronics"),
    ("TCS", "NSE", "Tata Consultancy Services"),
    ("MOTHERSON", "NSE", "Samvardhana Motherson International"),
    ("TTML", "NSE", "Tata Teleservices Maharashtra"),
    ("NBCC", "NSE", "NBCC India"),
    ("HINDCOPPER", "NSE", "Hindustan Copper"),
    ("BAJAJHFL", "NSE", "Bajaj Housing Finance"),
    ("PATELENG", "NSE", "Patel Engineering"),
    ("NHPC", "NSE", "NHPC"),
    ("SAGILITY", "NSE", "Sagility"),
    ("ITC", "NSE", "ITC"),
    ("M&MFIN", "NSE", "M&M Financial Services"),
    ("HDFCBANK", "NSE", "HDFC Bank"),
    ("FEDERALBNK", "NSE", "Federal Bank"),
    ("JIOFIN", "NSE", "Jio Financial Services"),
    ("LTF", "NSE", "L&T Finance"),
    ("ENGINERSIN", "NSE", "Engineers India"),
    ("ASHOKA", "NSE", "Ashoka Buildcon"),
    ("SWIGGY", "NSE", "Swiggy"),
    ("ETERNAL", "NSE", "Eternal"),
    ("TARIL", "NSE", "Transformers and Rectifiers India"),
    ("GTLINFRA", "NSE", "GTL Infrastructure"),
    ("BAJFINANCE", "NSE", "Bajaj Finance"),
]


def ensure_preloaded_stocks():
    if store.list_stocks():
        return
    for symbol, exchange, name in PRELOADED_STOCKS:
        store.add_stock(symbol, exchange, name)


def local_ip_address():
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect(("8.8.8.8", 80))
            return sock.getsockname()[0]
    except OSError:
        return "127.0.0.1"


def money(value, currency="INR"):
    if value is None:
        return ""
    prefix = "Rs " if (currency or "INR") == "INR" else f"{currency} "
    return f"{prefix}{value:,.2f}"


def change_values(price, previous_close):
    if price is None or previous_close in (None, 0):
        return None, None
    change = price - previous_close
    return change, (change / previous_close) * 100


def row_to_dict(row):
    change, change_pct = change_values(row["price"], row["previous_close"])
    return {
        "id": row["id"],
        "symbol": row["symbol"],
        "exchange": row["exchange"],
        "name": row["name"] or "",
        "created_at": row["created_at"],
        "price": row["price"],
        "previous_close": row["previous_close"],
        "currency": row["currency"] or "INR",
        "price_date": row["price_date"] or "",
        "market_time": row["market_time"] or "",
        "captured_at": row["captured_at"] or "",
        "change": change,
        "change_pct": change_pct,
        "price_display": money(row["price"], row["currency"]),
        "previous_close_display": money(row["previous_close"], row["currency"]),
    }


def history_to_dict(row):
    change, change_pct = change_values(row["price"], row["previous_close"])
    return {
        "price_date": row["price_date"],
        "price": row["price"],
        "previous_close": row["previous_close"],
        "currency": row["currency"] or "INR",
        "market_time": row["market_time"] or "",
        "captured_at": row["captured_at"] or "",
        "change": change,
        "change_pct": change_pct,
        "price_display": money(row["price"], row["currency"]),
        "previous_close_display": money(row["previous_close"], row["currency"]),
    }


def refresh_rows(rows):
    results = []
    with refresh_lock:
        for row in rows:
            label = f"{row['symbol']} {row['exchange']}"
            try:
                created_date = datetime.fromisoformat(row["created_at"]).date()
                name, daily_prices = client.fetch_daily_history(row["symbol"], row["exchange"], created_date, date.today())
                store.save_daily_prices(row["id"], name, daily_prices)
                try:
                    quote = client.fetch_quote(row["symbol"], row["exchange"])
                    store.save_quote(row["id"], quote)
                except Exception:
                    pass
                results.append({"label": label, "ok": True, "message": f"Backfilled {len(daily_prices)} rows"})
            except Exception as exc:
                try:
                    quote = client.fetch_quote(row["symbol"], row["exchange"])
                    store.save_quote(row["id"], quote)
                    results.append({"label": label, "ok": False, "message": f"Saved current price only. {exc}"})
                except Exception as quote_exc:
                    results.append({"label": label, "ok": False, "message": f"{exc} | Current price failed: {quote_exc}"})
    return results


class RequestHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        return

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path == "/":
            self.send_html(INDEX_HTML)
            return
        if parsed.path == "/api/stocks":
            self.send_json({"stocks": [row_to_dict(row) for row in store.list_stocks()]})
            return
        if parsed.path == "/api/server-info":
            host_header = self.headers.get("X-Forwarded-Host") or self.headers.get("Host") or f"127.0.0.1:{PORT}"
            proto = self.headers.get("X-Forwarded-Proto") or "http"
            public_url = f"{proto}://{host_header}"
            ip = local_ip_address()
            self.send_json(
                {
                    "local_url": f"http://127.0.0.1:{PORT}",
                    "public_url": public_url,
                    "phone_url": f"http://{ip}:{PORT}",
                    "port": PORT,
                }
            )
            return
        match = re.fullmatch(r"/api/stocks/(\d+)/history", parsed.path)
        if match:
            stock_id = int(match.group(1))
            self.send_json({"history": [history_to_dict(row) for row in store.history_for_stock(stock_id)]})
            return
        if parsed.path == "/api/export":
            output_path = APP_DIR / f"stock_price_history_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
            store.export_history_csv(output_path)
            self.send_json({"path": str(output_path), "name": output_path.name})
            return
        self.send_error(HTTPStatus.NOT_FOUND)

    def do_POST(self):
        parsed = urlparse(self.path)
        try:
            payload = self.read_json()
            if parsed.path == "/api/stocks":
                symbol = str(payload.get("symbol", "")).strip()
                exchange = str(payload.get("exchange", "NSE")).strip().upper()
                store.add_stock(symbol, exchange)
                self.send_json({"stocks": [row_to_dict(row) for row in store.list_stocks()]}, HTTPStatus.CREATED)
                return
            if parsed.path == "/api/refresh":
                ids = payload.get("ids") or []
                rows = store.list_stocks()
                if ids:
                    ids = {int(stock_id) for stock_id in ids}
                    rows = [row for row in rows if row["id"] in ids]
                results = refresh_rows(rows)
                self.send_json({"results": results, "stocks": [row_to_dict(row) for row in store.list_stocks()]})
                return
            match = re.fullmatch(r"/api/stocks/(\d+)/manual", parsed.path)
            if match:
                stock_id = int(match.group(1))
                stock = store.conn.execute("SELECT symbol, exchange, name FROM watchlist WHERE id = ?", (stock_id,)).fetchone()
                if not stock:
                    self.send_error(HTTPStatus.NOT_FOUND)
                    return
                price = float(payload["price"])
                previous_close = payload.get("previous_close")
                previous_close = float(previous_close) if previous_close not in (None, "") else None
                from main import Quote

                store.save_quote(
                    stock_id,
                    Quote(
                        symbol=stock["symbol"],
                        exchange=stock["exchange"],
                        name=stock["name"] or "",
                        price=price,
                        previous_close=previous_close,
                        currency="INR",
                        market_time="Manual entry",
                    ),
                )
                self.send_json({"stocks": [row_to_dict(row) for row in store.list_stocks()]})
                return
        except Exception as exc:
            self.send_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
            return
        self.send_error(HTTPStatus.NOT_FOUND)

    def do_DELETE(self):
        parsed = urlparse(self.path)
        match = re.fullmatch(r"/api/stocks/(\d+)", parsed.path)
        if match:
            store.remove_stock(int(match.group(1)))
            self.send_json({"stocks": [row_to_dict(row) for row in store.list_stocks()]})
            return
        self.send_error(HTTPStatus.NOT_FOUND)

    def read_json(self):
        length = int(self.headers.get("Content-Length", 0))
        if length == 0:
            return {}
        return json.loads(self.rfile.read(length).decode("utf-8"))

    def send_json(self, payload, status=HTTPStatus.OK):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def send_html(self, html_text):
        body = html_text.encode("utf-8")
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


INDEX_HTML = r"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>India Stock Watchlist</title>
  <style>
    :root {
      color-scheme: light;
      --bg: #f6f7f3;
      --panel: #ffffff;
      --ink: #18201d;
      --muted: #68736f;
      --line: #dfe5df;
      --accent: #0f7a65;
      --accent-strong: #075e4e;
      --gold: #c68b1c;
      --red: #b33a3a;
      --green: #0f7a43;
      --violet: #5b5f97;
      --shadow: 0 18px 45px rgba(35, 48, 42, 0.10);
      --radius: 8px;
      font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    }

    * { box-sizing: border-box; }

    body {
      margin: 0;
      min-height: 100vh;
      background:
        linear-gradient(180deg, rgba(15, 122, 101, 0.08), rgba(246, 247, 243, 0) 310px),
        var(--bg);
      color: var(--ink);
    }

    button, input, select {
      font: inherit;
    }

    button {
      border: 0;
      cursor: pointer;
    }

    .app {
      width: min(1440px, 100%);
      margin: 0 auto;
      padding: 18px;
    }

    .topbar {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 18px;
      margin-bottom: 18px;
    }

    .brand {
      display: flex;
      align-items: center;
      gap: 12px;
      min-width: 220px;
    }

    .mark {
      width: 42px;
      height: 42px;
      border-radius: var(--radius);
      display: grid;
      place-items: center;
      color: #fff;
      background: linear-gradient(135deg, var(--accent), #263f38);
      box-shadow: var(--shadow);
    }

    .brand h1 {
      margin: 0;
      font-size: 20px;
      line-height: 1.15;
      letter-spacing: 0;
    }

    .brand p {
      margin: 3px 0 0;
      color: var(--muted);
      font-size: 13px;
    }

    .actions {
      display: flex;
      align-items: center;
      justify-content: flex-end;
      gap: 8px;
      flex-wrap: wrap;
    }

    .phone-note {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 12px;
      margin: 0 0 18px;
      padding: 12px 14px;
      border: 1px solid #d9e2dc;
      border-radius: var(--radius);
      background: #eef7f2;
      color: #224840;
    }

    .phone-note strong {
      display: block;
      font-size: 13px;
    }

    .phone-note span {
      display: block;
      margin-top: 2px;
      font-size: 13px;
      color: var(--muted);
      overflow-wrap: anywhere;
    }

    .icon {
      width: 18px;
      height: 18px;
      stroke: currentColor;
      stroke-width: 2;
      fill: none;
      stroke-linecap: round;
      stroke-linejoin: round;
      flex: 0 0 auto;
    }

    .btn {
      min-height: 40px;
      border-radius: var(--radius);
      padding: 0 13px;
      display: inline-flex;
      align-items: center;
      justify-content: center;
      gap: 8px;
      color: var(--ink);
      background: #e9eee8;
      border: 1px solid #d7dfd6;
      transition: transform 150ms ease, background 150ms ease, box-shadow 150ms ease;
      white-space: nowrap;
    }

    .btn:hover {
      transform: translateY(-1px);
      box-shadow: 0 10px 24px rgba(35, 48, 42, 0.10);
    }

    .btn.primary {
      color: #fff;
      background: var(--accent);
      border-color: var(--accent);
    }

    .btn.primary:hover {
      background: var(--accent-strong);
    }

    .btn.danger {
      color: var(--red);
      background: #fff1ef;
      border-color: #f3d4ce;
    }

    .layout {
      display: grid;
      grid-template-columns: minmax(0, 1fr) 360px;
      gap: 18px;
      align-items: start;
    }

    .surface {
      background: rgba(255, 255, 255, 0.92);
      border: 1px solid var(--line);
      border-radius: var(--radius);
      box-shadow: var(--shadow);
    }

    .toolbar {
      display: grid;
      grid-template-columns: 1fr auto auto auto;
      gap: 10px;
      padding: 14px;
      align-items: center;
      border-bottom: 1px solid var(--line);
    }

    .field {
      display: grid;
      gap: 6px;
    }

    .field label {
      color: var(--muted);
      font-size: 12px;
      font-weight: 700;
      text-transform: uppercase;
      letter-spacing: 0;
    }

    .input, .select {
      width: 100%;
      min-height: 42px;
      border-radius: var(--radius);
      border: 1px solid #d8e0d8;
      background: #fbfcfa;
      color: var(--ink);
      padding: 0 12px;
      outline: none;
    }

    .input:focus, .select:focus {
      border-color: var(--accent);
      box-shadow: 0 0 0 3px rgba(15, 122, 101, 0.14);
    }

    .stats {
      display: grid;
      grid-template-columns: repeat(4, minmax(0, 1fr));
      gap: 1px;
      background: var(--line);
      border-bottom: 1px solid var(--line);
    }

    .stat {
      background: #fbfcfa;
      padding: 14px;
      min-width: 0;
    }

    .stat span {
      display: block;
      color: var(--muted);
      font-size: 12px;
      font-weight: 700;
      text-transform: uppercase;
      letter-spacing: 0;
    }

    .stat strong {
      display: block;
      margin-top: 4px;
      font-size: 22px;
      letter-spacing: 0;
      overflow-wrap: anywhere;
    }

    .table-wrap {
      overflow: auto;
      max-height: calc(100vh - 250px);
    }

    table {
      width: 100%;
      border-collapse: collapse;
      min-width: 920px;
    }

    th, td {
      padding: 13px 14px;
      text-align: left;
      border-bottom: 1px solid var(--line);
      vertical-align: middle;
      font-size: 14px;
    }

    th {
      position: sticky;
      top: 0;
      background: #f8faf7;
      z-index: 1;
      color: #49534f;
      font-size: 12px;
      text-transform: uppercase;
      letter-spacing: 0;
    }

    tbody tr {
      transition: background 140ms ease;
    }

    tbody tr:hover,
    tbody tr.selected {
      background: #edf7f2;
    }

    .symbol-cell {
      display: flex;
      align-items: center;
      gap: 10px;
      min-width: 0;
    }

    .exchange {
      width: 38px;
      height: 28px;
      display: grid;
      place-items: center;
      border-radius: 6px;
      background: #f1ead8;
      color: #79520d;
      font-size: 11px;
      font-weight: 800;
      flex: 0 0 auto;
    }

    .ticker {
      font-weight: 800;
      letter-spacing: 0;
    }

    .company {
      display: block;
      margin-top: 2px;
      color: var(--muted);
      font-size: 12px;
      max-width: 240px;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }

    .number {
      font-variant-numeric: tabular-nums;
      white-space: nowrap;
    }

    .pos { color: var(--green); font-weight: 800; }
    .neg { color: var(--red); font-weight: 800; }
    .muted { color: var(--muted); }

    .empty {
      padding: 44px 18px;
      text-align: center;
      color: var(--muted);
    }

    .side {
      display: grid;
      gap: 18px;
    }

    .panel-head {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 10px;
      padding: 14px 14px 0;
    }

    .panel-head h2 {
      margin: 0;
      font-size: 16px;
      letter-spacing: 0;
    }

    .panel-body {
      padding: 14px;
    }

    .history-list {
      display: grid;
      gap: 8px;
      max-height: 420px;
      overflow: auto;
    }

    .history-row {
      display: grid;
      grid-template-columns: 92px 1fr auto;
      gap: 10px;
      align-items: center;
      min-height: 52px;
      padding: 9px 10px;
      border: 1px solid var(--line);
      border-radius: var(--radius);
      background: #fbfcfa;
    }

    .history-row strong {
      font-size: 14px;
    }

    .history-row small {
      color: var(--muted);
      display: block;
      margin-top: 2px;
    }

    .manual-grid {
      display: grid;
      gap: 10px;
    }

    .status {
      position: fixed;
      right: 22px;
      bottom: 22px;
      max-width: min(440px, calc(100vw - 44px));
      padding: 12px 14px;
      border-radius: var(--radius);
      color: #fff;
      background: #263f38;
      box-shadow: var(--shadow);
      transform: translateY(18px);
      opacity: 0;
      pointer-events: none;
      transition: opacity 180ms ease, transform 180ms ease;
      z-index: 10;
    }

    .status.show {
      opacity: 1;
      transform: translateY(0);
    }

    .loading {
      opacity: 0.6;
      pointer-events: none;
    }

    @media (max-width: 1060px) {
      .layout {
        grid-template-columns: 1fr;
      }

      .side {
        grid-template-columns: repeat(2, minmax(0, 1fr));
      }
    }

    @media (max-width: 760px) {
      body {
        background:
          linear-gradient(180deg, rgba(15, 122, 101, 0.12), rgba(246, 247, 243, 0) 230px),
          var(--bg);
      }

      .app {
        padding: 12px 10px 92px;
      }

      .topbar {
        align-items: stretch;
        flex-direction: column;
        margin-bottom: 10px;
      }

      .brand {
        min-width: 0;
      }

      .brand h1 {
        font-size: 18px;
      }

      .brand p {
        font-size: 12px;
      }

      .actions {
        position: fixed;
        left: 0;
        right: 0;
        bottom: 0;
        z-index: 20;
        display: grid;
        grid-template-columns: repeat(3, minmax(0, 1fr));
        gap: 8px;
        padding: 10px 10px calc(10px + env(safe-area-inset-bottom));
        background: rgba(246, 247, 243, 0.96);
        border-top: 1px solid var(--line);
        box-shadow: 0 -14px 35px rgba(35, 48, 42, 0.12);
      }

      .actions .btn {
        min-width: 0;
        min-height: 52px;
        padding: 0 8px;
        flex-direction: column;
        gap: 4px;
        font-size: 12px;
      }

      .toolbar {
        grid-template-columns: 1fr;
        padding: 12px;
      }

      .stats {
        grid-template-columns: repeat(2, minmax(0, 1fr));
      }

      .stat {
        padding: 11px 12px;
      }

      .stat strong {
        font-size: 18px;
      }

      .phone-note {
        align-items: flex-start;
        flex-direction: column;
        margin-bottom: 10px;
        padding: 11px 12px;
      }

      .table-wrap {
        max-height: none;
        overflow: visible;
        padding: 10px;
      }

      table,
      tbody,
      tr,
      td {
        display: block;
        width: 100%;
      }

      table {
        min-width: 0;
        border-collapse: separate;
        border-spacing: 0;
      }

      thead {
        display: none;
      }

      tbody {
        display: grid;
        gap: 10px;
      }

      tbody tr {
        border: 1px solid var(--line);
        border-radius: var(--radius);
        background: #fbfcfa;
        box-shadow: 0 10px 24px rgba(35, 48, 42, 0.07);
        overflow: hidden;
      }

      tbody tr:hover,
      tbody tr.selected {
        background: #f4fbf7;
        border-color: rgba(15, 122, 101, 0.35);
      }

      td {
        min-height: 34px;
        padding: 8px 12px;
        border-bottom: 0;
        font-size: 13px;
      }

      td:not(:first-child):not(:last-child) {
        display: flex;
        justify-content: space-between;
        align-items: center;
        gap: 14px;
      }

      td:not(:first-child):not(:last-child)::before {
        color: var(--muted);
        font-size: 11px;
        font-weight: 800;
        text-transform: uppercase;
      }

      td:nth-child(2)::before { content: "Latest"; }
      td:nth-child(3)::before { content: "Prev Close"; }
      td:nth-child(4)::before { content: "Change"; }
      td:nth-child(5)::before { content: "Change %"; }
      td:nth-child(6)::before { content: "Date"; }
      td:nth-child(7)::before { content: "Saved"; }

      td:first-child {
        padding-top: 12px;
        padding-bottom: 10px;
        border-bottom: 1px solid var(--line);
      }

      td:last-child {
        padding-top: 2px;
        padding-bottom: 12px;
      }

      td:last-child .btn {
        width: 100%;
      }

      .symbol-cell {
        align-items: flex-start;
      }

      .exchange {
        width: 42px;
        height: 30px;
      }

      .ticker {
        font-size: 16px;
      }

      .company {
        max-width: calc(100vw - 108px);
        white-space: normal;
        line-height: 1.3;
      }

      .side {
        grid-template-columns: 1fr;
      }

      .surface {
        box-shadow: 0 10px 28px rgba(35, 48, 42, 0.08);
      }

      .history-list {
        max-height: none;
      }

      .history-row {
        grid-template-columns: 88px 1fr auto;
        padding: 10px;
      }

      .manual-grid {
        grid-template-columns: 1fr;
      }

      .status {
        left: 10px;
        right: 10px;
        bottom: 80px;
        max-width: none;
      }
    }
  </style>
</head>
<body>
  <div class="app">
    <header class="topbar">
      <div class="brand">
        <div class="mark" aria-hidden="true">
          <svg class="icon" viewBox="0 0 24 24"><path d="M3 3v18h18"/><path d="m7 15 4-4 3 3 5-7"/></svg>
        </div>
        <div>
          <h1>India Stock Watchlist</h1>
          <p>NSE and BSE daily tracker</p>
        </div>
      </div>
      <div class="actions">
        <button class="btn" id="refreshSelectedBtn" title="Refresh selected">
          <svg class="icon" viewBox="0 0 24 24"><path d="M21 12a9 9 0 0 1-15.5 6.2"/><path d="M3 12A9 9 0 0 1 18.5 5.8"/><path d="M18 2v4h4"/><path d="M6 22v-4H2"/></svg>
          Selected
        </button>
        <button class="btn primary" id="refreshAllBtn" title="Refresh all">
          <svg class="icon" viewBox="0 0 24 24"><path d="M21 12a9 9 0 1 1-3-6.7"/><path d="M21 3v6h-6"/></svg>
          Refresh All
        </button>
        <button class="btn" id="exportBtn" title="Export CSV">
          <svg class="icon" viewBox="0 0 24 24"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><path d="M7 10l5 5 5-5"/><path d="M12 15V3"/></svg>
          Export
        </button>
      </div>
    </header>

    <div class="phone-note">
      <div>
        <strong>Phone access</strong>
        <span id="phoneUrl">Open this site link on any phone. If running locally, use the same Wi-Fi phone link shown here.</span>
      </div>
      <button class="btn" id="copyPhoneBtn" title="Copy phone link">
        <svg class="icon" viewBox="0 0 24 24"><rect x="9" y="9" width="13" height="13" rx="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/></svg>
        Copy
      </button>
    </div>

    <main class="layout">
      <section class="surface">
        <form class="toolbar" id="addForm">
          <div class="field">
            <label for="symbolInput">Symbol or BSE code</label>
            <input class="input" id="symbolInput" autocomplete="off" placeholder="RELIANCE or 500325">
          </div>
          <div class="field">
            <label for="exchangeInput">Exchange</label>
            <select class="select" id="exchangeInput">
              <option>NSE</option>
              <option>BSE</option>
            </select>
          </div>
          <div class="field">
            <label for="searchInput">Search</label>
            <input class="input" id="searchInput" autocomplete="off" placeholder="Filter watchlist">
          </div>
          <button class="btn primary" type="submit">
            <svg class="icon" viewBox="0 0 24 24"><path d="M12 5v14"/><path d="M5 12h14"/></svg>
            Add
          </button>
        </form>

        <div class="stats">
          <div class="stat"><span>Stocks</span><strong id="statStocks">0</strong></div>
          <div class="stat"><span>NSE</span><strong id="statNse">0</strong></div>
          <div class="stat"><span>BSE</span><strong id="statBse">0</strong></div>
          <div class="stat"><span>Updated</span><strong id="statUpdated">None</strong></div>
        </div>

        <div class="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Stock</th>
                <th>Latest</th>
                <th>Prev Close</th>
                <th>Change</th>
                <th>Change %</th>
                <th>Date</th>
                <th>Saved</th>
                <th></th>
              </tr>
            </thead>
            <tbody id="stockRows"></tbody>
          </table>
          <div class="empty" id="emptyState">No stocks yet</div>
        </div>
      </section>

      <aside class="side">
        <section class="surface">
          <div class="panel-head">
            <h2 id="historyTitle">History</h2>
            <button class="btn" id="historyReloadBtn" title="Reload history">
              <svg class="icon" viewBox="0 0 24 24"><path d="M21 12a9 9 0 1 1-3-6.7"/><path d="M21 3v6h-6"/></svg>
            </button>
          </div>
          <div class="panel-body">
            <div class="history-list" id="historyList">
              <div class="empty">Select a stock</div>
            </div>
          </div>
        </section>

        <section class="surface">
          <div class="panel-head">
            <h2>Manual Price</h2>
          </div>
          <div class="panel-body">
            <form class="manual-grid" id="manualForm">
              <input class="input" id="manualPrice" type="number" step="0.01" min="0" placeholder="Today's price">
              <input class="input" id="manualPrev" type="number" step="0.01" min="0" placeholder="Previous close">
              <button class="btn primary" type="submit">
                <svg class="icon" viewBox="0 0 24 24"><path d="M19 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11l5 5v11a2 2 0 0 1-2 2Z"/><path d="M17 21v-8H7v8"/><path d="M7 3v5h8"/></svg>
                Save Price
              </button>
            </form>
          </div>
        </section>
      </aside>
    </main>
  </div>

  <div class="status" id="status"></div>

  <script>
    const state = {
      stocks: [],
      selectedId: null,
      loading: false,
      phoneUrl: "",
    };

    const $ = (selector) => document.querySelector(selector);
    const rowsEl = $("#stockRows");
    const emptyEl = $("#emptyState");
    const statusEl = $("#status");

    const icons = {
      trash: '<svg class="icon" viewBox="0 0 24 24"><path d="M3 6h18"/><path d="M8 6V4h8v2"/><path d="m19 6-1 14H6L5 6"/><path d="M10 11v6"/><path d="M14 11v6"/></svg>',
    };

    function fmtNumber(value, suffix = "") {
      if (value === null || value === undefined || Number.isNaN(value)) return "";
      const sign = value > 0 ? "+" : "";
      return `${sign}${Number(value).toFixed(2)}${suffix}`;
    }

    function cssChange(value) {
      if (value > 0) return "pos";
      if (value < 0) return "neg";
      return "muted";
    }

    function toast(message) {
      statusEl.textContent = message;
      statusEl.classList.add("show");
      window.clearTimeout(toast.timer);
      toast.timer = window.setTimeout(() => statusEl.classList.remove("show"), 3800);
    }

    async function api(path, options = {}) {
      const response = await fetch(path, {
        headers: { "Content-Type": "application/json" },
        ...options,
      });
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.error || "Request failed");
      return payload;
    }

    async function loadStocks() {
      const payload = await api("/api/stocks");
      state.stocks = payload.stocks;
      if (state.selectedId && !state.stocks.some((stock) => stock.id === state.selectedId)) {
        state.selectedId = null;
      }
      render();
      if (state.selectedId) loadHistory(state.selectedId);
    }

    async function loadServerInfo() {
      try {
        const payload = await api("/api/server-info");
        const isLocal = ["127.0.0.1", "localhost"].includes(window.location.hostname);
        state.phoneUrl = isLocal ? payload.phone_url : payload.public_url;
        $("#phoneUrl").textContent = isLocal ? `Same Wi-Fi phone link: ${payload.phone_url}` : `Phone link: ${payload.public_url}`;
      } catch (error) {
        $("#phoneUrl").textContent = "Phone link will appear after the app starts.";
      }
    }

    function render() {
      const query = $("#searchInput").value.trim().toLowerCase();
      const filtered = state.stocks.filter((stock) => {
        return [stock.symbol, stock.exchange, stock.name].join(" ").toLowerCase().includes(query);
      });

      rowsEl.innerHTML = "";
      emptyEl.style.display = filtered.length ? "none" : "block";

      for (const stock of filtered) {
        const tr = document.createElement("tr");
        tr.className = stock.id === state.selectedId ? "selected" : "";
        tr.innerHTML = `
          <td>
            <div class="symbol-cell">
              <span class="exchange">${stock.exchange}</span>
              <div>
                <span class="ticker">${stock.symbol}</span>
                <span class="company">${stock.name || "Name pending"}</span>
              </div>
            </div>
          </td>
          <td class="number">${stock.price_display || ""}</td>
          <td class="number">${stock.previous_close_display || ""}</td>
          <td class="number ${cssChange(stock.change)}">${fmtNumber(stock.change)}</td>
          <td class="number ${cssChange(stock.change_pct)}">${fmtNumber(stock.change_pct, "%")}</td>
          <td class="muted">${stock.price_date || ""}</td>
          <td class="muted">${stock.captured_at ? stock.captured_at.replace("T", " ") : ""}</td>
          <td><button class="btn danger" data-remove="${stock.id}" title="Remove">${icons.trash}</button></td>
        `;
        tr.addEventListener("click", (event) => {
          if (event.target.closest("[data-remove]")) return;
          state.selectedId = stock.id;
          render();
          loadHistory(stock.id);
        });
        rowsEl.appendChild(tr);
      }

      $("#statStocks").textContent = state.stocks.length;
      $("#statNse").textContent = state.stocks.filter((stock) => stock.exchange === "NSE").length;
      $("#statBse").textContent = state.stocks.filter((stock) => stock.exchange === "BSE").length;
      const latest = state.stocks.map((stock) => stock.price_date).filter(Boolean).sort().pop();
      $("#statUpdated").textContent = latest || "None";

      document.querySelectorAll("[data-remove]").forEach((button) => {
        button.addEventListener("click", async () => {
          const id = Number(button.dataset.remove);
          await api(`/api/stocks/${id}`, { method: "DELETE" });
          if (state.selectedId === id) state.selectedId = null;
          toast("Removed stock");
          loadStocks();
          renderHistory([]);
        });
      });
    }

    async function loadHistory(id) {
      const stock = state.stocks.find((item) => item.id === id);
      $("#historyTitle").textContent = stock ? `${stock.symbol} History` : "History";
      const payload = await api(`/api/stocks/${id}/history`);
      renderHistory(payload.history);
    }

    function renderHistory(history) {
      const list = $("#historyList");
      if (!history.length) {
        list.innerHTML = '<div class="empty">No history yet</div>';
        return;
      }
      list.innerHTML = history.map((row) => `
        <div class="history-row">
          <div>
            <strong>${row.price_date}</strong>
            <small>${row.market_time ? row.market_time.replace(" daily close", "") : ""}</small>
          </div>
          <div class="number">
            <strong>${row.price_display}</strong>
            <small>Prev ${row.previous_close_display || ""}</small>
          </div>
          <div class="number ${cssChange(row.change_pct)}">${fmtNumber(row.change_pct, "%")}</div>
        </div>
      `).join("");
    }

    async function refresh(ids) {
      document.body.classList.add("loading");
      try {
        const payload = await api("/api/refresh", {
          method: "POST",
          body: JSON.stringify({ ids }),
        });
        state.stocks = payload.stocks;
        render();
        if (state.selectedId) loadHistory(state.selectedId);
        const failed = payload.results.filter((result) => !result.ok).length;
        toast(failed ? `Refresh finished with ${failed} issue(s)` : "Daily prices updated");
      } finally {
        document.body.classList.remove("loading");
      }
    }

    $("#addForm").addEventListener("submit", async (event) => {
      event.preventDefault();
      const symbol = $("#symbolInput").value.trim();
      const exchange = $("#exchangeInput").value;
      if (!symbol) return toast("Enter a stock symbol or BSE code");
      const payload = await api("/api/stocks", {
        method: "POST",
        body: JSON.stringify({ symbol, exchange }),
      });
      state.stocks = payload.stocks;
      $("#symbolInput").value = "";
      render();
      toast("Added stock");
    });

    $("#refreshAllBtn").addEventListener("click", () => refresh([]));
    $("#refreshSelectedBtn").addEventListener("click", () => {
      if (!state.selectedId) return toast("Select a stock first");
      refresh([state.selectedId]);
    });
    $("#historyReloadBtn").addEventListener("click", () => {
      if (state.selectedId) loadHistory(state.selectedId);
    });
    $("#searchInput").addEventListener("input", render);

    $("#manualForm").addEventListener("submit", async (event) => {
      event.preventDefault();
      if (!state.selectedId) return toast("Select a stock first");
      const price = $("#manualPrice").value;
      const previous_close = $("#manualPrev").value;
      if (!price) return toast("Enter today's price");
      const payload = await api(`/api/stocks/${state.selectedId}/manual`, {
        method: "POST",
        body: JSON.stringify({ price, previous_close }),
      });
      state.stocks = payload.stocks;
      $("#manualPrice").value = "";
      $("#manualPrev").value = "";
      render();
      loadHistory(state.selectedId);
      toast("Manual price saved");
    });

    $("#exportBtn").addEventListener("click", async () => {
      const payload = await api("/api/export");
      toast(`Exported ${payload.name}`);
    });

    $("#copyPhoneBtn").addEventListener("click", async () => {
      if (!state.phoneUrl) return toast("Phone link is not ready yet");
      try {
        await navigator.clipboard.writeText(state.phoneUrl);
        toast("Phone link copied");
      } catch (error) {
        toast(state.phoneUrl);
      }
    });

    loadServerInfo();
    loadStocks().catch((error) => toast(error.message));
  </script>
</body>
</html>
"""


def main():
    ensure_preloaded_stocks()
    server = ThreadingHTTPServer((HOST, PORT), RequestHandler)
    local_url = f"http://127.0.0.1:{PORT}"
    phone_url = f"http://{local_ip_address()}:{PORT}"
    print(f"India Stock Watchlist running at {local_url}")
    print(f"Phone on same Wi-Fi can open: {phone_url}")
    try:
        webbrowser.open(local_url)
    except Exception:
        pass
    server.serve_forever()


if __name__ == "__main__":
    main()
