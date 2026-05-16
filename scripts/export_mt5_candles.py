# -*- coding: utf-8 -*-
import sys, io, requests, json
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

SUPABASE_URL = "https://lkznmelzulctjqzhvzuy.supabase.co"
SUPABASE_KEY = "sb_publishable_AwZG_gkBJdFwY3Iex3OfvQ_Hmfkfcpl"

headers = {
    "apikey": SUPABASE_KEY,
    "Authorization": f"Bearer {SUPABASE_KEY}",
    "Content-Type": "application/json"
}

all_rows = []
limit = 1000
offset = 0

print("Выгружаем свечи из mt5_candles...")

while True:
    url = (f"{SUPABASE_URL}/rest/v1/mt5_candles"
           f"?select=*&order=time.asc&limit={limit}&offset={offset}")
    resp = requests.get(url, headers=headers)
    if resp.status_code != 200:
        print(f"Ошибка: {resp.status_code} {resp.text}")
        break
    batch = resp.json()
    if not batch:
        break
    all_rows.extend(batch)
    print(f"  Загружено: {len(all_rows)} свечей (batch: {len(batch)})")
    if len(batch) < limit:
        break
    offset += limit

print(f"\nВсего: {len(all_rows)} свечей")
if all_rows:
    times = [r['time'] for r in all_rows]
    print(f"Период: {min(times)} ... {max(times)}")

    out = r"D:\Works\ASTRA ANALYZER CHART\mt5 candles\mt5_candles_all.json"
    with open(out, 'w', encoding='utf-8') as f:
        json.dump(all_rows, f, ensure_ascii=False)
    print(f"Сохранено: {out}")
