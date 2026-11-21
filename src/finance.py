# src/market_poller.py
import json, os, time, requests
from datetime import datetime, timezone
from dotenv import load_dotenv

load_dotenv()
KEY = os.environ["FINNHUB_API_KEY"]
SYMBOLS = ["NVDA", "VTI", "QQQ"]
INTERVAL = 30  # segundos
OUT = "data/market_snapshot.json"

def get_quote(symbol):
    r = requests.get(
        "https://finnhub.io/api/v1/quote",
        params={"symbol": symbol, "token": KEY},
        timeout=10,
    )
    r.raise_for_status()
    q = r.json()
    change = (q["c"] / q["pc"] - 1) * 100 if q.get("pc") else None
    return {"price": q["c"], "prev_close": q["pc"], "change_pct": change}

while True:
    try:
        snapshot = {
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "quotes": {s: get_quote(s) for s in SYMBOLS},
        }
        with open(OUT + ".tmp", "w") as f:
            json.dump(snapshot, f, indent=2)
        os.replace(OUT + ".tmp", OUT)  # escritura atómica
    except Exception as e:
        print("Error:", e)  # no tumbar el bucle por un fallo puntual
    time.sleep(INTERVAL)