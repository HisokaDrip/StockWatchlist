import csv
import html
import json
import re
import sqlite3
import sys
import threading
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from html.parser import HTMLParser
import os
from pathlib import Path

try:
    from tkinter import BOTH, END, LEFT, RIGHT, X, Y, Button, Entry, Frame, Label, StringVar, Tk, Toplevel, messagebox, simpledialog, ttk
except Exception:
    BOTH = END = LEFT = RIGHT = X = Y = None
    Button = Entry = Frame = Label = StringVar = Tk = Toplevel = messagebox = simpledialog = ttk = None


APP_DIR = Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parent
DB_PATH = Path(os.environ["STOCK_DB_PATH"]) if os.environ.get("STOCK_DB_PATH") else APP_DIR / "stock_watchlist.db"


@dataclass
class Quote:
    symbol: str
    exchange: str
    name: str
    price: float | None
    previous_close: float | None
    currency: str
    market_time: str


@dataclass
class DailyPrice:
    price_date: str
    price: float | None
    previous_close: float | None
    currency: str
    market_time: str


class StockStore:
    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self.conn = sqlite3.connect(db_path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self._init_db()

    def _init_db(self) -> None:
        with self.conn:
            self.conn.execute(
                """
                CREATE TABLE IF NOT EXISTS watchlist (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    symbol TEXT NOT NULL,
                    exchange TEXT NOT NULL CHECK(exchange IN ('NSE', 'BSE')),
                    name TEXT DEFAULT '',
                    created_at TEXT NOT NULL,
                    UNIQUE(symbol, exchange)
                )
                """
            )
            self.conn.execute(
                """
                CREATE TABLE IF NOT EXISTS price_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    stock_id INTEGER NOT NULL,
                    price_date TEXT NOT NULL,
                    price REAL,
                    previous_close REAL,
                    currency TEXT DEFAULT 'INR',
                    market_time TEXT DEFAULT '',
                    captured_at TEXT NOT NULL,
                    FOREIGN KEY(stock_id) REFERENCES watchlist(id) ON DELETE CASCADE,
                    UNIQUE(stock_id, price_date)
                )
                """
            )

    def add_stock(self, symbol: str, exchange: str, name: str = "") -> None:
        clean_symbol = symbol.strip().upper().replace(".NS", "").replace(".BO", "")
        if not clean_symbol:
            raise ValueError("Enter a stock symbol, for example RELIANCE or TCS.")
        with self.conn:
            self.conn.execute(
                """
                INSERT OR IGNORE INTO watchlist(symbol, exchange, name, created_at)
                VALUES (?, ?, ?, ?)
                """,
                (clean_symbol, exchange, name.strip(), datetime.now().isoformat(timespec="seconds")),
            )

    def remove_stock(self, stock_id: int) -> None:
        with self.conn:
            self.conn.execute("DELETE FROM price_history WHERE stock_id = ?", (stock_id,))
            self.conn.execute("DELETE FROM watchlist WHERE id = ?", (stock_id,))

    def list_stocks(self) -> list[sqlite3.Row]:
        return self.conn.execute(
            """
            SELECT
                w.id,
                w.symbol,
                w.exchange,
                w.name,
                w.created_at,
                h.price,
                h.previous_close,
                h.currency,
                h.market_time,
                h.captured_at,
                h.price_date
            FROM watchlist w
            LEFT JOIN price_history h
                ON h.id = (
                    SELECT ph.id
                    FROM price_history ph
                    WHERE ph.stock_id = w.id
                    ORDER BY ph.price_date DESC, ph.captured_at DESC
                    LIMIT 1
                )
            ORDER BY w.exchange, w.symbol
            """
        ).fetchall()

    def history_for_stock(self, stock_id: int) -> list[sqlite3.Row]:
        return self.conn.execute(
            """
            SELECT price_date, price, previous_close, currency, market_time, captured_at
            FROM price_history
            WHERE stock_id = ?
            ORDER BY price_date DESC
            """,
            (stock_id,),
        ).fetchall()

    def save_quote(self, stock_id: int, quote: Quote) -> None:
        today = date.today().isoformat()
        self.save_daily_price(
            stock_id,
            quote.name,
            DailyPrice(
                price_date=today,
                price=quote.price,
                previous_close=quote.previous_close,
                currency=quote.currency,
                market_time=quote.market_time,
            ),
        )

    def save_daily_price(self, stock_id: int, name: str, daily_price: DailyPrice) -> None:
        with self.conn:
            self.conn.execute(
                "UPDATE watchlist SET name = COALESCE(NULLIF(?, ''), name) WHERE id = ?",
                (name, stock_id),
            )
            self.conn.execute(
                """
                INSERT INTO price_history(
                    stock_id, price_date, price, previous_close, currency, market_time, captured_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(stock_id, price_date) DO UPDATE SET
                    price = excluded.price,
                    previous_close = excluded.previous_close,
                    currency = excluded.currency,
                    market_time = excluded.market_time,
                    captured_at = excluded.captured_at
                """,
                (
                    stock_id,
                    daily_price.price_date,
                    daily_price.price,
                    daily_price.previous_close,
                    daily_price.currency,
                    daily_price.market_time,
                    datetime.now().isoformat(timespec="seconds"),
                ),
            )

    def save_daily_prices(self, stock_id: int, name: str, daily_prices: list[DailyPrice]) -> None:
        for daily_price in daily_prices:
            self.save_daily_price(stock_id, name, daily_price)

    def export_history_csv(self, output_path: Path) -> None:
        rows = self.conn.execute(
            """
            SELECT
                w.symbol,
                w.exchange,
                w.name,
                h.price_date,
                h.price,
                h.previous_close,
                h.currency,
                h.market_time,
                h.captured_at
            FROM price_history h
            JOIN watchlist w ON w.id = h.stock_id
            ORDER BY h.price_date DESC, w.exchange, w.symbol
            """
        ).fetchall()
        with output_path.open("w", newline="", encoding="utf-8") as file:
            writer = csv.writer(file)
            writer.writerow(
                [
                    "symbol",
                    "exchange",
                    "name",
                    "price_date",
                    "price",
                    "previous_close",
                    "currency",
                    "market_time",
                    "captured_at",
                ]
            )
            for row in rows:
                writer.writerow([row[key] for key in row.keys()])


class YahooFinanceClient:
    SUFFIXES = {"NSE": ".NS", "BSE": ".BO"}
    GOOGLE_EXCHANGES = {"NSE": "NSE", "BSE": "BOM"}

    def fetch_quote(self, symbol: str, exchange: str) -> Quote:
        errors: list[str] = []
        for fetcher in (self._fetch_yahoo_quote, self._fetch_google_quote):
            try:
                return fetcher(symbol, exchange)
            except Exception as exc:
                errors.append(str(exc))
        raise RuntimeError(" | ".join(errors))

    def fetch_daily_history(self, symbol: str, exchange: str, start_date: date, end_date: date) -> tuple[str, list[DailyPrice]]:
        errors: list[str] = []
        for fetcher in (self._fetch_yahoo_daily_history, self._fetch_google_daily_history):
            try:
                return fetcher(symbol, exchange, start_date, end_date)
            except Exception as exc:
                errors.append(str(exc))
        raise RuntimeError(" | ".join(errors))

    def _fetch_yahoo_daily_history(self, symbol: str, exchange: str, start_date: date, end_date: date) -> tuple[str, list[DailyPrice]]:
        yahoo_symbol = f"{symbol.upper()}{self.SUFFIXES[exchange]}"
        from_date = start_date - timedelta(days=10)
        period1 = int(datetime.combine(from_date, datetime.min.time()).timestamp())
        period2 = int(datetime.combine(end_date + timedelta(days=1), datetime.min.time()).timestamp())
        encoded = urllib.parse.quote(yahoo_symbol)
        url = (
            f"https://query1.finance.yahoo.com/v8/finance/chart/{encoded}"
            f"?period1={period1}&period2={period2}&interval=1d&events=history"
        )
        request = urllib.request.Request(
            url,
            headers={
                "User-Agent": "IndiaStockWatchlist/1.0",
                "Accept": "application/json",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=20) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            raise RuntimeError(f"Yahoo Finance history returned HTTP {exc.code} for {yahoo_symbol}.") from exc
        except urllib.error.URLError as exc:
            raise RuntimeError(f"Could not connect for {yahoo_symbol} history: {exc.reason}") from exc

        result = payload.get("chart", {}).get("result") or []
        if not result:
            error = payload.get("chart", {}).get("error", {})
            message = error.get("description") or f"No daily history found for {yahoo_symbol}."
            raise RuntimeError(message)

        data = result[0]
        meta = data.get("meta", {})
        timestamps = data.get("timestamp", [])
        quote_rows = data.get("indicators", {}).get("quote", [{}])[0]
        closes = quote_rows.get("close", [])
        currency = meta.get("currency") or "INR"
        name = meta.get("longName") or meta.get("shortName") or ""

        seen_closes: list[float] = []
        daily_prices: list[DailyPrice] = []
        for timestamp, close in zip(timestamps, closes):
            if close is None:
                continue
            row_date = datetime.fromtimestamp(timestamp).date()
            close_value = float(close)
            previous_close = seen_closes[-1] if seen_closes else meta.get("chartPreviousClose")
            seen_closes.append(close_value)
            if start_date <= row_date <= end_date:
                daily_prices.append(
                    DailyPrice(
                        price_date=row_date.isoformat(),
                        price=close_value,
                        previous_close=float(previous_close) if previous_close is not None else None,
                        currency=currency,
                        market_time=f"{row_date.isoformat()} daily close",
                    )
                )

        if not daily_prices:
            raise RuntimeError(f"No daily rows returned from {start_date.isoformat()} to {end_date.isoformat()} for {yahoo_symbol}.")
        return name, daily_prices

    def _fetch_google_daily_history(self, symbol: str, exchange: str, start_date: date, end_date: date) -> tuple[str, list[DailyPrice]]:
        google_exchange = self.GOOGLE_EXCHANGES[exchange]
        google_symbol = f"{symbol.upper()}:{google_exchange}"
        encoded = urllib.parse.quote(google_symbol, safe=":")
        url = f"https://www.google.com/finance/quote/{encoded}"
        request = urllib.request.Request(
            url,
            headers={
                "User-Agent": "Mozilla/5.0",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=20) as response:
                page = response.read().decode("utf-8", "ignore")
        except urllib.error.HTTPError as exc:
            raise RuntimeError(f"Google Finance history returned HTTP {exc.code} for {google_symbol}.") from exc
        except urllib.error.URLError as exc:
            raise RuntimeError(f"Could not connect to Google Finance history for {google_symbol}: {exc.reason}") from exc

        daily_block_match = re.search(r"AF_initDataCallback\(\{key: 'ds:12'.*?</script>", page, re.S)
        daily_block = daily_block_match.group(0) if daily_block_match else page

        rows = [
            (
                datetime.fromisoformat(match.group(5)).date(),
                float(match.group(2)),
                match.group(5),
            )
            for match in re.finditer(
                r"\[(-?\d+(?:\.\d+)?),(-?\d+(?:\.\d+)?),(-?\d+(?:\.\d+)?),(-?\d+(?:\.\d+)?),"
                r"\"(\d{4}-\d{2}-\d{2}T[^\"]+)\",\d+\]",
                daily_block,
            )
        ]

        if not rows:
            raise RuntimeError(f"Google Finance did not expose daily history for {google_symbol}.")

        daily_prices: list[DailyPrice] = []
        previous_close: float | None = None
        seen_dates: set[date] = set()
        for row_date, close, iso_time in sorted(rows, key=lambda item: item[0]):
            if row_date in seen_dates:
                continue
            seen_dates.add(row_date)
            if start_date <= row_date <= end_date:
                daily_prices.append(
                    DailyPrice(
                        price_date=row_date.isoformat(),
                        price=close,
                        previous_close=previous_close,
                        currency="INR",
                        market_time=f"{iso_time} daily close",
                    )
                )
            previous_close = close

        if not daily_prices:
            earliest = min(row[0] for row in rows).isoformat()
            latest = max(row[0] for row in rows).isoformat()
            raise RuntimeError(
                f"Google Finance history for {google_symbol} only covered {earliest} to {latest}, not the requested dates."
            )
        return "", daily_prices

    def _fetch_yahoo_quote(self, symbol: str, exchange: str) -> Quote:
        yahoo_symbol = f"{symbol.upper()}{self.SUFFIXES[exchange]}"
        encoded = urllib.parse.quote(yahoo_symbol)
        url = f"https://query1.finance.yahoo.com/v8/finance/chart/{encoded}?range=5d&interval=1d"
        request = urllib.request.Request(
            url,
            headers={
                "User-Agent": "IndiaStockWatchlist/1.0",
                "Accept": "application/json",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=20) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            raise RuntimeError(f"Yahoo Finance returned HTTP {exc.code} for {yahoo_symbol}.") from exc
        except urllib.error.URLError as exc:
            raise RuntimeError(f"Could not connect for {yahoo_symbol}: {exc.reason}") from exc

        result = payload.get("chart", {}).get("result") or []
        if not result:
            error = payload.get("chart", {}).get("error", {})
            message = error.get("description") or f"No quote found for {yahoo_symbol}."
            raise RuntimeError(message)

        data = result[0]
        meta = data.get("meta", {})
        quote_rows = data.get("indicators", {}).get("quote", [{}])[0]
        closes = [value for value in quote_rows.get("close", []) if value is not None]
        timestamps = data.get("timestamp", [])

        price = meta.get("regularMarketPrice")
        if price is None and closes:
            price = closes[-1]

        previous_close = meta.get("chartPreviousClose")
        if previous_close is None and len(closes) >= 2:
            previous_close = closes[-2]

        market_time = ""
        market_ts = meta.get("regularMarketTime")
        if market_ts:
            market_time = datetime.fromtimestamp(market_ts).strftime("%Y-%m-%d %H:%M:%S")
        elif timestamps:
            market_time = datetime.fromtimestamp(timestamps[-1]).strftime("%Y-%m-%d %H:%M:%S")

        return Quote(
            symbol=symbol.upper(),
            exchange=exchange,
            name=meta.get("longName") or meta.get("shortName") or "",
            price=float(price) if price is not None else None,
            previous_close=float(previous_close) if previous_close is not None else None,
            currency=meta.get("currency") or "INR",
            market_time=market_time,
        )

    def _fetch_google_quote(self, symbol: str, exchange: str) -> Quote:
        google_exchange = self.GOOGLE_EXCHANGES[exchange]
        google_symbol = f"{symbol.upper()}:{google_exchange}"
        encoded = urllib.parse.quote(google_symbol, safe=":")
        url = f"https://www.google.com/finance/quote/{encoded}"
        request = urllib.request.Request(
            url,
            headers={
                "User-Agent": "Mozilla/5.0",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=20) as response:
                page = response.read().decode("utf-8", "ignore")
        except urllib.error.HTTPError as exc:
            raise RuntimeError(f"Google Finance returned HTTP {exc.code} for {google_symbol}.") from exc
        except urllib.error.URLError as exc:
            raise RuntimeError(f"Could not connect to Google Finance for {google_symbol}: {exc.reason}") from exc

        parser = TextCollector()
        parser.feed(page)
        tokens = [html.unescape(token).strip() for token in parser.tokens if token.strip()]
        start = tokens.index(google_symbol) if google_symbol in tokens else 0
        useful_tokens = tokens[start : start + 30]

        price_token = next((token for token in useful_tokens if re.fullmatch(r"\u20b9[\d,]+(?:\.\d+)?", token)), None)
        if not price_token:
            price_token = next(
                (match.group(0) for match in re.finditer(r"\u20b9[\d,]+(?:\.\d+)?", html.unescape(page))),
                None,
            )
        if not price_token:
            raise RuntimeError(f"Google Finance did not expose a price for {google_symbol}.")

        price = self._parse_rupee_value(price_token)
        name = ""
        if google_symbol in tokens:
            symbol_index = tokens.index(google_symbol)
            for token in tokens[symbol_index + 1 : symbol_index + 8]:
                if token and not token.startswith("check_") and not token.startswith("\u20b9"):
                    name = token
                    break

        change = None
        change_text = " ".join(useful_tokens)
        change_match = re.search(r"\(\s*([+-]?[\d,]+(?:\.\d+)?)\s*\)\s*1D", change_text)
        if change_match:
            change = float(change_match.group(1).replace(",", ""))
        previous_close = price - change if change is not None else None

        market_time = next((token for token in useful_tokens if "GMT" in token or token.endswith("INR")), "")

        return Quote(
            symbol=symbol.upper(),
            exchange=exchange,
            name=name,
            price=price,
            previous_close=previous_close,
            currency="INR",
            market_time=market_time,
        )

    @staticmethod
    def _parse_rupee_value(value: str) -> float:
        return float(value.replace("\u20b9", "").replace(",", "").strip())


class TextCollector(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.tokens: list[str] = []

    def handle_data(self, data: str) -> None:
        value = data.strip()
        if value:
            self.tokens.append(value)


class StockWatchApp:
    def __init__(self, root: Tk) -> None:
        self.root = root
        self.store = StockStore(DB_PATH)
        self.client = YahooFinanceClient()
        self.selected_stock_id: int | None = None
        self.status = StringVar(value="Ready")
        self.symbol_var = StringVar()
        self.exchange_var = StringVar(value="NSE")

        self.root.title("India Stock Watchlist")
        self.root.geometry("1100x680")
        self.root.minsize(900, 560)
        self._build_ui()
        self.load_watchlist()

    def _build_ui(self) -> None:
        style = ttk.Style()
        style.theme_use("clam")
        style.configure("Treeview", rowheight=28, font=("Segoe UI", 10))
        style.configure("Treeview.Heading", font=("Segoe UI", 10, "bold"))

        top = Frame(self.root, padx=14, pady=12)
        top.pack(fill=X)

        Label(top, text="Symbol").pack(side=LEFT)
        Entry(top, textvariable=self.symbol_var, width=18).pack(side=LEFT, padx=(6, 12))

        Label(top, text="Exchange").pack(side=LEFT)
        exchange = ttk.Combobox(top, textvariable=self.exchange_var, values=("NSE", "BSE"), width=8, state="readonly")
        exchange.pack(side=LEFT, padx=(6, 12))

        Button(top, text="Add", command=self.add_stock).pack(side=LEFT, padx=3)
        Button(top, text="Remove", command=self.remove_selected).pack(side=LEFT, padx=3)
        Button(top, text="Refresh Selected", command=self.refresh_selected).pack(side=LEFT, padx=3)
        Button(top, text="Refresh All", command=self.refresh_all).pack(side=LEFT, padx=3)
        Button(top, text="Manual Price", command=self.save_manual_price).pack(side=LEFT, padx=3)
        Button(top, text="Export CSV", command=self.export_csv).pack(side=LEFT, padx=3)

        main = Frame(self.root, padx=14)
        main.pack(fill=BOTH, expand=True)

        columns = (
            "symbol",
            "exchange",
            "name",
            "price",
            "previous_close",
            "change",
            "change_pct",
            "market_time",
            "captured_at",
        )
        self.watchlist = ttk.Treeview(main, columns=columns, show="headings", selectmode="browse")
        headings = {
            "symbol": "Symbol",
            "exchange": "Market",
            "name": "Name",
            "price": "Latest Price",
            "previous_close": "Prev Close",
            "change": "Change",
            "change_pct": "Change %",
            "market_time": "Market Time",
            "captured_at": "Saved At",
        }
        widths = {
            "symbol": 110,
            "exchange": 80,
            "name": 230,
            "price": 115,
            "previous_close": 135,
            "change": 100,
            "change_pct": 100,
            "market_time": 150,
            "captured_at": 150,
        }
        for col in columns:
            self.watchlist.heading(col, text=headings[col])
            self.watchlist.column(col, width=widths[col], anchor="w")

        y_scroll = ttk.Scrollbar(main, orient="vertical", command=self.watchlist.yview)
        self.watchlist.configure(yscrollcommand=y_scroll.set)
        self.watchlist.pack(side=LEFT, fill=BOTH, expand=True)
        y_scroll.pack(side=RIGHT, fill=Y)
        self.watchlist.bind("<<TreeviewSelect>>", self.on_select)
        self.watchlist.bind("<Double-1>", lambda _event: self.show_history())

        bottom = Frame(self.root, padx=14, pady=10)
        bottom.pack(fill=X)
        Button(bottom, text="Show History", command=self.show_history).pack(side=LEFT)
        Label(bottom, textvariable=self.status, anchor="w").pack(side=LEFT, padx=14, fill=X, expand=True)

    def add_stock(self) -> None:
        symbol = self.symbol_var.get()
        exchange = self.exchange_var.get()
        try:
            self.store.add_stock(symbol, exchange)
        except ValueError as exc:
            messagebox.showwarning("Missing symbol", str(exc))
            return
        self.symbol_var.set("")
        self.load_watchlist()
        self.status.set(f"Added {symbol.strip().upper()} on {exchange}. Use Refresh to backfill daily prices from today onward.")

    def remove_selected(self) -> None:
        stock_id = self._selected_id()
        if stock_id is None:
            messagebox.showinfo("Select stock", "Select a stock to remove.")
            return
        if not messagebox.askyesno("Remove stock", "Remove this stock and its saved price history?"):
            return
        self.store.remove_stock(stock_id)
        self.selected_stock_id = None
        self.load_watchlist()

    def refresh_selected(self) -> None:
        stock_id = self._selected_id()
        if stock_id is None:
            messagebox.showinfo("Select stock", "Select a stock to refresh.")
            return
        rows = [row for row in self.store.list_stocks() if row["id"] == stock_id]
        self._refresh_rows(rows)

    def refresh_all(self) -> None:
        self._refresh_rows(self.store.list_stocks())

    def save_manual_price(self) -> None:
        stock_id = self._selected_id()
        if stock_id is None:
            messagebox.showinfo("Select stock", "Select a stock before saving a manual price.")
            return
        stock = self.store.conn.execute("SELECT symbol, exchange, name FROM watchlist WHERE id = ?", (stock_id,)).fetchone()
        price = simpledialog.askfloat("Manual price", f"Today's price for {stock['symbol']} {stock['exchange']}:", minvalue=0)
        if price is None:
            return
        previous_close = simpledialog.askfloat(
            "Previous close",
            "Yesterday/previous close. Leave blank if unknown:",
            minvalue=0,
        )
        quote = Quote(
            symbol=stock["symbol"],
            exchange=stock["exchange"],
            name=stock["name"] or "",
            price=price,
            previous_close=previous_close,
            currency="INR",
            market_time="Manual entry",
        )
        self.store.save_quote(stock_id, quote)
        self.load_watchlist()
        self.status.set(f"Saved manual price for {stock['symbol']} {stock['exchange']}.")

    def _refresh_rows(self, rows: list[sqlite3.Row]) -> None:
        if not rows:
            self.status.set("Nothing to refresh yet. Add a stock first.")
            return

        def worker() -> None:
            failures: list[str] = []
            for index, row in enumerate(rows, start=1):
                label = f"{row['symbol']} {row['exchange']}"
                self._set_status(f"Backfilling daily prices for {label} ({index}/{len(rows)})...")
                try:
                    created_date = datetime.fromisoformat(row["created_at"]).date()
                    name, daily_prices = self.client.fetch_daily_history(row["symbol"], row["exchange"], created_date, date.today())
                    self.store.save_daily_prices(row["id"], name, daily_prices)
                    try:
                        quote = self.client.fetch_quote(row["symbol"], row["exchange"])
                        self.store.save_quote(row["id"], quote)
                    except Exception:
                        pass
                except Exception as exc:  # UI should keep refreshing other symbols.
                    try:
                        quote = self.client.fetch_quote(row["symbol"], row["exchange"])
                        self.store.save_quote(row["id"], quote)
                        failures.append(f"{label}: saved current price only. Daily backfill failed: {exc}")
                    except Exception as quote_exc:
                        failures.append(f"{label}: {exc} | Current price failed: {quote_exc}")
            self.root.after(0, self.load_watchlist)
            if failures:
                self._set_status(f"Done with {len(failures)} issue(s). Double-check symbols or internet.")
                self.root.after(0, lambda: messagebox.showwarning("Refresh issues", "\n".join(failures[:8])))
            else:
                self._set_status("All missing daily prices were backfilled.")

        threading.Thread(target=worker, daemon=True).start()

    def load_watchlist(self) -> None:
        self.watchlist.delete(*self.watchlist.get_children())
        for row in self.store.list_stocks():
            change, change_pct = self._change_values(row["price"], row["previous_close"])
            self.watchlist.insert(
                "",
                END,
                iid=str(row["id"]),
                values=(
                    row["symbol"],
                    row["exchange"],
                    row["name"] or "",
                    self._money(row["price"], row["currency"]),
                    self._money(row["previous_close"], row["currency"]),
                    self._number(change),
                    self._percent(change_pct),
                    row["market_time"] or "",
                    row["captured_at"] or "",
                ),
            )

    def on_select(self, _event=None) -> None:
        self.selected_stock_id = self._selected_id()

    def show_history(self) -> None:
        stock_id = self._selected_id()
        if stock_id is None:
            messagebox.showinfo("Select stock", "Select a stock to view history.")
            return
        selected = self.store.conn.execute("SELECT symbol, exchange FROM watchlist WHERE id = ?", (stock_id,)).fetchone()
        rows = self.store.history_for_stock(stock_id)

        window = Toplevel(self.root)
        window.title(f"History - {selected['symbol']} {selected['exchange']}")
        window.geometry("780x420")

        columns = ("date", "price", "previous_close", "change", "change_pct", "market_time", "captured_at")
        tree = ttk.Treeview(window, columns=columns, show="headings")
        headers = {
            "date": "Date",
            "price": "Price",
            "previous_close": "Prev Close",
            "change": "Change",
            "change_pct": "Change %",
            "market_time": "Market Time",
            "captured_at": "Saved At",
        }
        for col in columns:
            tree.heading(col, text=headers[col])
            tree.column(col, width=105 if col not in {"market_time", "captured_at"} else 145)
        for row in rows:
            change, change_pct = self._change_values(row["price"], row["previous_close"])
            tree.insert(
                "",
                END,
                values=(
                    row["price_date"],
                    self._money(row["price"], row["currency"]),
                    self._money(row["previous_close"], row["currency"]),
                    self._number(change),
                    self._percent(change_pct),
                    row["market_time"],
                    row["captured_at"],
                ),
            )
        tree.pack(fill=BOTH, expand=True, padx=12, pady=12)

    def export_csv(self) -> None:
        output_path = APP_DIR / f"stock_price_history_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
        self.store.export_history_csv(output_path)
        self.status.set(f"Exported history to {output_path.name}")
        messagebox.showinfo("Export complete", f"Saved:\n{output_path}")

    def _selected_id(self) -> int | None:
        selection = self.watchlist.selection()
        if not selection:
            return None
        return int(selection[0])

    def _set_status(self, value: str) -> None:
        self.root.after(0, lambda: self.status.set(value))

    @staticmethod
    def _change_values(price: float | None, previous_close: float | None) -> tuple[float | None, float | None]:
        if price is None or previous_close in (None, 0):
            return None, None
        change = price - previous_close
        return change, (change / previous_close) * 100

    @staticmethod
    def _money(value: float | None, currency: str | None) -> str:
        if value is None:
            return ""
        prefix = "Rs " if (currency or "INR") == "INR" else f"{currency} "
        return f"{prefix}{value:,.2f}"

    @staticmethod
    def _number(value: float | None) -> str:
        return "" if value is None else f"{value:+,.2f}"

    @staticmethod
    def _percent(value: float | None) -> str:
        return "" if value is None else f"{value:+.2f}%"


def main() -> None:
    if Tk is None:
        raise RuntimeError("Tkinter is not available. Use web_app.py for the browser version.")
    root = Tk()
    StockWatchApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
