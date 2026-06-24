# Lokio Scraper

Automatický scraper pre rajec.sk → Supabase.
Beží každých 15 minút, používa Claude AI na parsovanie obsahu.

## Env premenné (nastav v Railway)

| Premenná | Hodnota |
|----------|---------|
| `SUPABASE_URL` | `https://hlloxipzroruzpjpeiqc.supabase.co` |
| `SUPABASE_SERVICE_KEY` | `sb_secret_...` z Supabase → Settings → API Keys |
| `ANTHROPIC_API_KEY` | tvoj Anthropic API kľúč |

## Lokálne testovanie

```bash
pip install -r requirements.txt

export SUPABASE_URL="https://hlloxipzroruzpjpeiqc.supabase.co"
export SUPABASE_SERVICE_KEY="sb_secret_..."
export ANTHROPIC_API_KEY="sk-ant-..."

# Jeden beh
python scraper.py --once

# Nekonečná slučka
python scraper.py
```

## Deploy na Railway

1. Vytvor účet na railway.app
2. New Project → Deploy from GitHub repo
3. Nahraj tento folder na GitHub
4. Nastav env premenné v Railway dashboard
5. Deploy → scraper beží automaticky každých 15 minút

## Čo scraper robí

- **Aktuality** – každých 15 minút stiahne rajec.sk/aktuality
- **Podujatia** – každých 15 minút stiahne podujatia, detekuje zrušené
- **Odpady** – raz denne o 6:00 stiahne harmonogram zberu
- Duplikáty sa automaticky preskočia (hash ID)
- Claude AI parsuje HTML → štruktúrovaný JSON → Supabase
