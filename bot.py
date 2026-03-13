import asyncio
import json
import os
import re
import random
from html import unescape
from urllib.parse import quote_plus

import aiohttp
import discord

# --- CONFIGURATION ---
DISCORD_TOKEN = os.environ.get("DISCORD_TOKEN")
CHANNEL_ID_RAW = os.environ.get("CHANNEL_ID")
SCRAPERAPI_KEY = os.environ.get("SCRAPERAPI_KEY") # Optionnel

if not DISCORD_TOKEN:
    raise RuntimeError("DISCORD_TOKEN est manquant.")

if not CHANNEL_ID_RAW or not CHANNEL_ID_RAW.isdigit():
    raise RuntimeError("CHANNEL_ID est manquant ou invalide.")

CHANNEL_ID = int(CHANNEL_ID_RAW)
CHECK_INTERVAL_SECONDS = int(os.environ.get("CHECK_INTERVAL_SECONDS", "300"))
REQUEST_TIMEOUT_SECONDS = int(os.environ.get("REQUEST_TIMEOUT_SECONDS", "30"))

SEEN_IDS: set[str] = set()
SEARCH_URL = "https://mon-vie-via.businessfrance.fr/offres/recherche"

# Liste de navigateurs récents pour la furtivité
USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:123.0) Gecko/20100101 Firefox/123.0"
]

# --- FONCTIONS UTILITAIRES ---

def _build_source_urls() -> list[str]:
    cache_buster = str(int(asyncio.get_running_loop().time() * 1000))
    target_url = f"{SEARCH_URL}?_ts={cache_buster}"
    return [
        target_url,
        f"https://api.allorigins.win/raw?url={quote_plus(target_url)}",
        f"https://api.allorigins.win/get?url={quote_plus(target_url)}",
        f"https://r.jina.ai/http://{target_url.replace('https://', '')}",
    ]

def _clean_text(value: str) -> str:
    value = unescape(value)
    value = re.sub(r"<[^>]+>", " ", value)
    value = re.sub(r"\s+", " ", value)
    return value.strip()

def _find_field(html: str, labels: list[str]) -> str | None:
    for label in labels:
        patterns = [
            rf"{label}\s*[:\-]\s*</?[^>]*>?\s*([^<\n\r]+)",
            rf"{label}\s*[:\-]\s*([^<\n\r]+)",
            rf'"{label}"\s*[:=]\s*"([^"]+)"',
        ]
        for pattern in patterns:
            match = re.search(pattern, html, re.IGNORECASE)
            if match:
                value = _clean_text(match.group(1))
                if value: return value
    return None

def _walk_json_values(node):
    if isinstance(node, dict):
        yield node
        for value in node.values(): yield from _walk_json_values(value)
    elif isinstance(node, list):
        for item in node: yield from _walk_json_values(item)

def _extract_json_ld_data(html: str) -> dict[str, str]:
    info: dict[str, str] = {}
    scripts = re.findall(r'<script[^>]*type="application/ld\+json"[^>]*>(.*?)</script>', html, flags=re.IGNORECASE | re.DOTALL)
    for script in scripts:
        try:
            parsed = json.loads(script.strip())
            entries = parsed if isinstance(parsed, list) else [parsed]
            for entry in entries:
                if not isinstance(entry, dict): continue
                if "title" not in info: info["title"] = (entry.get("title") or entry.get("name", "")).strip()
                if "company" not in info and isinstance(entry.get("hiringOrganization"), dict):
                    info["company"] = entry["hiringOrganization"].get("name", "").strip()
                if "location" not in info and isinstance(entry.get("jobLocation"), dict):
                    addr = entry["jobLocation"].get("address", {})
                    info["location"] = f"{addr.get('addressLocality', '')} {addr.get('addressCountry', '')}".strip()
        except: continue
    return info

def _extract_next_data(html: str) -> dict[str, str]:
    info: dict[str, str] = {}
    match = re.search(r'<script[^>]*id="__NEXT_DATA__"[^>]*>(.*?)</script>', html, flags=re.IGNORECASE | re.DOTALL)
    if not match: return info
    try:
        data = json.loads(match.group(1).strip())
        for obj in _walk_json_values(data):
            # Mapping simplifié pour la démo
            for k, fields in {"title": ["title", "intitule"], "company": ["entreprise", "company"], "location": ["lieu", "localisation"]}.items():
                if k not in info:
                    for field in fields:
                        if isinstance(obj.get(field), str): info[k] = obj[field].strip(); break
    except: pass
    return info

# --- BOT CORE ---

class VIEBot(discord.Client):
    async def on_ready(self):
        print(f"Bot connecté : {self.user}")
        self.loop.create_task(self.check_vie())

    async def _fetch_html(self, session: aiohttp.ClientSession, url: str) -> str:
        # Furtivité renforcée : Headers complets et aléatoires
        headers = {
            "User-Agent": random.choice(USER_AGENTS),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
            "Accept-Language": "fr-FR,fr;q=0.9,en-US;q=0.8,en;q=0.7",
            "Cache-Control": "no-cache",
            "Pragma": "no-cache",
            "Referer": "https://www.google.com/",
        }

        # Utilisation de ScraperAPI si la clé est présente et que ce n'est pas un proxy déjà
        fetch_url = url
        if SCRAPERAPI_KEY and "api.allorigins" not in url and "r.jina.ai" not in url:
            fetch_url = f"http://api.scraperapi.com?api_key={SCRAPERAPI_KEY}&url={quote_plus(url)}"

        timeout = aiohttp.ClientTimeout(total=REQUEST_TIMEOUT_SECONDS)
        
        async with session.get(fetch_url, headers=headers, timeout=timeout) as resp:
            body = await resp.text()
            if resp.status != 200:
                raise RuntimeError(f"HTTP {resp.status} | {url[:50]}... | body[:100]={body[:100]!r}")

            if "api.allorigins.win" in url:
                try:
                    payload = await resp.json(content_type=None)
                    return payload.get("contents", "")
                except:
                    raise RuntimeError("Échec parsing AllOrigins")

            return body

    async def _extract_offer_ids(self, session: aiohttp.ClientSession) -> list[str]:
        last_error = None
        for source in _build_source_urls():
            try:
                html = await self._fetch_html(session, source)
                found_ids = list(dict.fromkeys(re.findall(r"/offres/(\d+)", html)))
                if found_ids:
                    print(f"Source OK: {source[:40]}... | {len(found_ids)} offres")
                    return found_ids
                print(f"Source vide: {source[:40]}...")
            except Exception as exc:
                last_error = exc
                print(f"Source KO: {source[:40]}... -> {exc}")
        raise RuntimeError(f"Aucune source exploitable. Dernier log: {last_error}")

    async def _fetch_offer_details(self, session: aiohttp.ClientSession, offer_id: str) -> dict[str, str]:
        offer_url = f"https://mon-vie-via.businessfrance.fr/offres/{offer_id}"
        details = {"url": offer_url}
        try:
            html = await self._fetch_html(session, offer_url)
            details.update(_extract_json_ld_data(html))
            details.update(_extract_next_data(html))
            # Fallback regex si JSON vide
            if "title" not in details:
                m = re.search(r"<title[^>]*>(.*?)</title>", html, re.I | re.S)
                if m: details["title"] = _clean_text(m.group(1)).replace("| Mon V.I.E/V.I.A", "")
            
            details["location"] = details.get("location") or _find_field(html, ["Localisation", "Lieu", "Pays"])
            details["company"] = details.get("company") or _find_field(html, ["Entreprise", "Société"])
        except Exception as e:
            print(f"Erreur détails #{offer_id}: {e}")
        return {k: v for k, v in details.items() if v}

    def _format_message(self, details: dict[str, str], offer_id: str) -> str:
        title = details.get("title", f"Offre VIE #{offer_id}")
        lines = [f"✅ **Nouvelle offre VIE : {title}**"]
        if details.get("company"): lines.append(f"🏢 **Entreprise** : {details['company']}")
        if details.get("location"): lines.append(f"📍 **Lieu** : {details['location']}")
        lines.append(f"🔗 {details.get('url')}")
        return "\n".join(lines)

    async def check_vie(self):
        await self.wait_until_ready()
        channel = self.get_channel(CHANNEL_ID) or await self.fetch_channel(CHANNEL_ID)
        
        async with aiohttp.ClientSession() as session:
            while not self.is_closed():
                try:
                    print("Vérification en cours...")
                    found_ids = await self._extract_offer_ids(session)
                    
                    if not SEEN_IDS:
                        SEEN_IDS.update(found_ids)
                        print(f"Initialisation : {len(found_ids)} offres mémorisées.")
                        # Envoyer la dernière offre comme test
                        if found_ids:
                            latest_id = found_ids[0]
                            details = await self._fetch_offer_details(session, latest_id)
                            await channel.send("🧪 **Test de démarrage — dernière offre actuelle :**\n" + self._format_message(details, latest_id))
                    else:
                        new_ids = [oid for oid in found_ids if oid not in SEEN_IDS]
                        for oid in new_ids:
                            SEEN_IDS.add(oid)
                            details = await self._fetch_offer_details(session, oid)
                            await channel.send(self._format_message(details, oid))
                            await asyncio.sleep(2) # Pause entre envois Discord
                        if not new_ids: print("Rien de nouveau.")

                except Exception as exc:
                    print(f"Erreur technique: {exc}")

                await asyncio.sleep(CHECK_INTERVAL_SECONDS)

# Lancement
intents = discord.Intents.default()
client = VIEBot(intents=intents)
client.run(DISCORD_TOKEN)
