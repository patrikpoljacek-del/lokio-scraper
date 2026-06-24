"""
Lokio scraper – rajec.sk → Supabase
Spúšťa sa každých 15 minút (Railway Cron alebo while loop).
"""

import os
import json
import time
import hashlib
import logging
from datetime import datetime, date
from typing import Optional

import requests
from bs4 import BeautifulSoup
from supabase import create_client, Client
import anthropic

# ── Logging ──────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)

# ── Config (z env premenných) ─────────────────────────────────────────────────
SUPABASE_URL = os.environ["SUPABASE_URL"]          # https://xxx.supabase.co
SUPABASE_KEY = os.environ["SUPABASE_SERVICE_KEY"]  # sb_secret_... (nie publishable!)
ANTHROPIC_KEY = os.environ["ANTHROPIC_API_KEY"]

CITY = "Rajec"
BASE_URL = "https://rajec.sk"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "sk-SK,sk;q=0.9",
}

# ── Klienti ───────────────────────────────────────────────────────────────────
sb: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
ai = anthropic.Anthropic(api_key=ANTHROPIC_KEY)


# ── Pomocné funkcie ───────────────────────────────────────────────────────────
def fetch(url: str) -> Optional[str]:
    """Stiahne URL a vráti HTML alebo None pri chybe."""
    try:
        r = requests.get(url, headers=HEADERS, timeout=15)
        r.raise_for_status()
        r.encoding = "utf-8"
        return r.text
    except Exception as e:
        log.error(f"Fetch failed {url}: {e}")
        return None


def make_id(text: str) -> str:
    """Deterministické UUID z textu – zabraňuje duplikátom."""
    return hashlib.md5(text.encode()).hexdigest()


def already_exists(table: str, record_id: str) -> bool:
    """Skontroluje či záznam už existuje v Supabase."""
    try:
        res = sb.table(table).select("id").eq("id", record_id).execute()
        return len(res.data) > 0
    except Exception:
        return False


def ask_claude(prompt: str) -> dict:
    """
    Pošle HTML/text Claude a dostane späť štruktúrovaný JSON.
    Vracia prázdny dict pri chybe.
    """
    try:
        msg = ai.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=2000,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = msg.content[0].text.strip()
        # Odstráň markdown code fences ak sú tam
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        return json.loads(raw.strip())
    except Exception as e:
        log.error(f"Claude error: {e}")
        return {}


# ── Scraper: Aktuality ────────────────────────────────────────────────────────
def scrape_news():
    log.info("Scraping aktuality...")
    html = fetch(f"{BASE_URL}/aktuality")
    if not html:
        return

    soup = BeautifulSoup(html, "html.parser")

    # Vyber relevantný text – nie celú stránku
    # Rajec.sk má zoznam článkov s dátumom, tagom a názvom
    articles_text = []
    
    # Skús rôzne selektory (stránky miest majú rôzne CMS)
    containers = (
        soup.select("article") or
        soup.select(".news-item") or
        soup.select(".aktualita") or
        soup.select(".post") or
        soup.select("li.item")
    )

    if not containers:
        # Fallback: vezmi celý main content
        main = soup.select_one("main") or soup.select_one(".content") or soup.body
        if main:
            articles_text = [main.get_text(separator="\n", strip=True)[:3000]]
    else:
        for c in containers[:15]:
            articles_text.append(c.get_text(separator="\n", strip=True))

    if not articles_text:
        log.warning("Žiadne aktuality nenájdené")
        return

    combined = "\n\n---\n\n".join(articles_text[:10])

    prompt = f"""Z tohto textu zo stránky mesta Rajec extrahuj aktuality/správy.
Vráť VÝLUČNE JSON pole (bez akéhokoľvek iného textu):
[
  {{
    "title": "názov správy",
    "tag": "kategória (Kultúra/Šport/Oznam/Ostatné)",
    "detail": "krátky popis ak je dostupný",
    "source_url": "URL ak je dostupná inak prázdny string",
    "published_at": "dátum vo formáte YYYY-MM-DD ak je dostupný inak null"
  }}
]

Text:
{combined}"""

    result = ask_claude(prompt)
    if not isinstance(result, list):
        log.warning("Claude nevrátil list pre news")
        return

    inserted = 0
    for item in result:
        title = item.get("title", "").strip()
        if not title:
            continue
        record_id = make_id(f"news-{CITY}-{title}")
        if already_exists("news", record_id):
            continue
        try:
            sb.table("news").insert({
                "id": record_id,
                "city": CITY,
                "title": title,
                "tag": item.get("tag", "Ostatné"),
                "detail": item.get("detail", ""),
                "source_url": item.get("source_url", BASE_URL),
                "published_at": item.get("published_at"),
            }).execute()
            inserted += 1
        except Exception as e:
            log.error(f"Insert news error: {e}")

    log.info(f"News: {inserted} nových záznamo")


# ── Scraper: Podujatia ────────────────────────────────────────────────────────
def scrape_events():
    log.info("Scraping podujatia...")

    # Skús sekciu podujatí/kultúry
    urls_to_try = [
        f"{BASE_URL}/podujatia",
        f"{BASE_URL}/kultúra",
        f"{BASE_URL}/sport-a-kultura",
        f"{BASE_URL}",  # hlavná stránka má preview podujatí
    ]

    html = None
    for url in urls_to_try:
        html = fetch(url)
        if html and len(html) > 1000:
            log.info(f"Events source: {url}")
            break

    if not html:
        return

    soup = BeautifulSoup(html, "html.parser")
    main = soup.select_one("main") or soup.body
    text = main.get_text(separator="\n", strip=True)[:4000] if main else ""

    prompt = f"""Z tohto textu zo stránky mesta Rajec extrahuj podujatia/eventy/akcie.
Vráť VÝLUČNE JSON pole (bez akéhokoľvek iného textu):
[
  {{
    "title": "názov podujatia",
    "date": "dátum vo formáte YYYY-MM-DDTHH:MM:00+00:00, ak nie je čas použi 12:00",
    "location": "miesto konania",
    "category": "Kultúra alebo Šport alebo Trhy alebo Deti alebo Ostatné",
    "description": "popis ak je dostupný",
    "cancelled": false
  }}
]
Ak nájdeš text 'zrušené' alebo 'cancelled' pri podujatí, nastav cancelled: true.
Ak nenájdeš žiadne podujatia vráť prázdne pole [].

Text:
{text}"""

    result = ask_claude(prompt)
    if not isinstance(result, list):
        return

    inserted = 0
    for item in result:
        title = item.get("title", "").strip()
        date_str = item.get("date", "")
        if not title or not date_str:
            continue
        record_id = make_id(f"event-{CITY}-{title}-{date_str}")
        if already_exists("events", record_id):
            continue
        try:
            sb.table("events").insert({
                "id": record_id,
                "city": CITY,
                "title": title,
                "date": date_str,
                "location": item.get("location", "Rajec"),
                "category": item.get("category", "Ostatné"),
                "description": item.get("description", ""),
                "lat": 49.0897,   # Rajec default koordináty
                "lng": 18.6536,
                "cancelled": item.get("cancelled", False),
            }).execute()
            inserted += 1
            # Ak je zrušené, updatni existujúce podujatia s rovnakým názvom
            if item.get("cancelled"):
                sb.table("events").update({"cancelled": True}).eq("city", CITY).eq("title", title).execute()
        except Exception as e:
            log.error(f"Insert event error: {e}")

    log.info(f"Events: {inserted} nových záznamov")


# ── Scraper: Odpady (raz za rok) ──────────────────────────────────────────────
def scrape_waste():
    log.info("Scraping odpady...")

    # Skontroluj či už máme tohtoročné odpady
    current_year = date.today().year
    try:
        existing = sb.table("waste_pickups").select("id").eq("city", CITY).gte("date", f"{current_year}-01-01").execute()
        if len(existing.data) > 50:
            log.info(f"Odpady pre rok {current_year} už existujú ({len(existing.data)} záznamov), preskakujem")
            return
    except Exception:
        pass

    html = fetch(f"{BASE_URL}/odpady")
    if not html:
        # Skús alternatívne URL
        html = fetch(f"{BASE_URL}/zivotne-prostredie/odpady")
    if not html:
        log.warning("Stránka odpadov nenájdená")
        return

    soup = BeautifulSoup(html, "html.parser")
    main = soup.select_one("main") or soup.body
    text = main.get_text(separator="\n", strip=True)[:6000] if main else ""

    prompt = f"""Z tohto textu extrahuj harmonogram zberu odpadov pre mesto Rajec na rok {current_year}.
Vráť VÝLUČNE JSON pole (bez akéhokoľvek iného textu):
[
  {{
    "date": "YYYY-MM-DD",
    "category": "Zmesový odpad alebo Plasty alebo Papier alebo Bioodpad alebo Sklo",
    "building_types": ["rodinny_dom", "bytovka"],
    "note": "poznámka ak je"
  }}
]
building_types môže obsahovať: "rodinny_dom", "bytovka", "podnikatel"
Ak nie je špecifikované pre koho, použi ["rodinny_dom", "bytovka"].
Ak nenájdeš harmonogram vráť prázdne pole [].

Text:
{text}"""

    result = ask_claude(prompt)
    if not isinstance(result, list) or not result:
        log.warning("Harmonogram odpadov nenájdený")
        return

    inserted = 0
    for item in result:
        date_str = item.get("date", "")
        category = item.get("category", "")
        if not date_str or not category:
            continue
        record_id = make_id(f"waste-{CITY}-{date_str}-{category}")
        if already_exists("waste_pickups", record_id):
            continue
        try:
            sb.table("waste_pickups").insert({
                "id": record_id,
                "city": CITY,
                "date": date_str,
                "category": category,
                "building_types": item.get("building_types", ["rodinny_dom", "bytovka"]),
                "note": item.get("note"),
            }).execute()
            inserted += 1
        except Exception as e:
            log.error(f"Insert waste error: {e}")

    log.info(f"Waste: {inserted} nových záznamov")


# ── Hlavná slučka ─────────────────────────────────────────────────────────────
def run_once():
    """Jeden beh scrapera."""
    log.info(f"=== Lokio scraper štart – {datetime.now().strftime('%Y-%m-%d %H:%M')} ===")
    scrape_news()
    scrape_events()
    # Odpady len raz za deň (nie každých 15 minút)
    if datetime.now().hour == 6 and datetime.now().minute < 15:
        scrape_waste()
    log.info("=== Hotovo ===")


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "--once":
        run_once()
    else:
        # Nekonečná slučka – každých 15 minút
        while True:
            run_once()
            log.info("Čakám 15 minút...")
            time.sleep(900)
