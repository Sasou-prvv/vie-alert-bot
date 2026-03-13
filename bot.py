import asyncio
import json
import os
import random
import re
from datetime import datetime
from html import unescape
from urllib.parse import quote_plus

import aiohttp
import discord

DISCORD_TOKEN = os.environ.get("DISCORD_TOKEN")
CHANNEL_ID_RAW = os.environ.get("CHANNEL_ID")
SCRAPERAPI_KEY = os.environ.get("SCRAPERAPI_KEY") 

if not DISCORD_TOKEN:
    raise RuntimeError("DISCORD_TOKEN est manquant.")

if not CHANNEL_ID_RAW or not CHANNEL_ID_RAW.isdigit():
    raise RuntimeError("CHANNEL_ID est manquant ou invalide.")

CHANNEL_ID = int(CHANNEL_ID_RAW)
CHECK_INTERVAL_SECONDS = int(os.environ.get("CHECK_INTERVAL_SECONDS", "60"))
REQUEST_TIMEOUT_SECONDS = int(os.environ.get("REQUEST_TIMEOUT_SECONDS", "20"))
SEND_STARTUP_TEST = os.environ.get("SEND_STARTUP_TEST", "1") == "1"
SOURCE_FAILURE_COOLDOWN_SECONDS = int(os.environ.get("SOURCE_FAILURE_COOLDOWN_SECONDS", "900"))

SEEN_IDS: set[str] = set()
SOURCE_FAIL_UNTIL: dict[str, float] = {}
SEARCH_URL = "https://mon-vie-via.businessfrance.fr/offres/recherche"
API_SEARCH_URL = "https://mon-vie-via.businessfrance.fr/api/offres/recherche"

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
]

def _clean_text(value: str) -> str:
    value = unescape(value or "")
    value = re.sub(r"<[^>]+>", " ", value)
    value = re.sub(r"\s+", " ", value)
    return value.strip()

def _format_french_date(date_str: str) -> str:
    """Transforme '01 juin 2026' en '01/06/2026'"""
    mois_fr = {
        "janvier": "01", "février": "02", "fevrier": "02", "mars": "03", 
        "avril": "04", "mai": "05", "juin": "06", "juillet": "07", 
        "août": "08", "aout": "08", "septembre": "09", "octobre": "10", 
        "novembre": "11", "décembre": "12", "decembre": "12"
    }
    d = date_str.lower().strip()
    for m_fr, m_num in mois_fr.items():
        if m_fr in d:
            parts = d.replace(m_fr, m_num).split()
            if len(parts) == 3:
                return f"{parts[0].zfill(2)}/{parts[1]}/{parts[2]}"
    return date_str

def _looks_like_noise(value: str | None) -> bool:
    if not value: return True
    text = _clean_text(value)
    lower = text.lower()
    noise_markers = ["function(w", "gtm.start", "javascript:", "my international volunteer program", "placeholder", "{", "}", '":"']
    return any(marker in lower for marker in noise_markers) or len(text) > 150

def _extract_business_france_fields(html: str) -> dict[str, str]:
    info: dict[str, str] = {}
    
    # Localisation (Pays / Ville)
    mission_anchor = re.search(r"LA\s+MISSION", html, re.IGNORECASE)
    if mission_anchor:
        tail = html[mission_anchor.end() : mission_anchor.end() + 1000]
        paren_line = re.search(r"([A-ZÀ-ÖØ-Þ\-\s']{3,})\s*\(([^\)\n\r]{2,})\)", tail)
        if paren_line:
            info["country"] = _clean_text(paren_line.group(1))
            info["city"] = _clean_text(paren_line.group(2))

    # Dates et Durée (uniquement le chiffre)
    date_range_match = re.search(
        r"du\s+([0-9]{1,2}\s+[^\s]+\s+[0-9]{4})\s+au\s+([0-9]{1,2}\s+[^\s]+\s+[0-9]{4})\s*\((\d+)\s*mois\)",
        html, re.IGNORECASE,
    )
    if date_range_match:
        info["start"] = _format_french_date(_clean_text(date_range_match.group(1)))
        info["deadline"] = _format_french_date(_clean_text(date_range_match.group(2)))
        info["duration"] = _clean_text(date_range_match.group(3))

    # Entreprise
    company_match = re.search(r"ETABLISSEMENT\s*:\s*([^<\n\r]+)", html, re.IGNORECASE)
    if company_match:
        info["company"] = _clean_text(company_match.group(1))

    # Salaire sans centimes
    salary_match = re.search(r"REMUNERATION\s+MENSUELLE\s*:\s*([0-9\s]+)[.,]?[0-9]*\s*€", html, re.IGNORECASE)
    if salary_match:
        info["salary"] = f"{salary_match.group(1).replace(' ', '')} €"

    return info

class VIEBot(discord.Client):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.startup_test_sent = False

    async def on_ready(self):
        print(f"Bot connecté : {self.user}")
        self.loop.create_task(self.check_vie())

    def _build_offer_embed(self, details: dict[str, str], offer_id: str, is_test: bool = False) -> discord.Embed:
        title = details.get("title", f"Offre VIE #{offer_id}")
        prefix = "🧪 TEST — " if is_test else ""

        embed = discord.Embed(
            title=f"{prefix}{title}"[:256],
            color=discord.Color.blue(),
            url=details.get("url", f"https://mon-vie-via.businessfrance.fr/offres/{offer_id}")
        )

        def add_field(name: str, key: str, inline: bool = True):
            value = details.get(key)
            if value:
                embed.add_field(name=name, value=str(value), inline=inline)

        add_field("🏢 Entreprise", "company")
        add_field("📅 Durée (mois)", "duration")
        add_field("🏙️ Ville", "city")
        add_field("🌍 Pays", "country")
        add_field("💰 Salaire", "salary")
        add_field("🚀 Début", "start")
        add_field("🏁 Fin", "deadline")

        embed.add_field(
            name="🔗 Lien",
            value=f"[Voir l'offre sur Business France]({details.get('url', f'https://mon-vie-via.businessfrance.fr/offres/{offer_id}')})",
            inline=False,
        )
        embed.set_footer(text="FR Alerte VIE • Business France")
        return embed

    async def check_vie(self):
        await self.wait_until_ready()
        channel = self.get_channel(CHANNEL_ID) or await self.fetch_channel(CHANNEL_ID)

        async with aiohttp.ClientSession() as session:
            while not self.is_closed():
                try:
                    # Ici on simplifie pour l'exemple, on fetch les IDs puis les détails
                    # (Ton ancienne logique de boucle reste identique)
                    found_ids = await self._extract_offer_ids(session)
                    
                    if not SEEN_IDS:
                        if found_ids and SEND_STARTUP_TEST and not self.startup_test_sent:
                            details = await self._fetch_offer_details(session, found_ids[0])
                            await channel.send(embed=self._build_offer_embed(details, found_ids[0], True))
                            self.startup_test_sent = True
                        SEEN_IDS.update(found_ids)
                    else:
                        for oid in [i for i in found_ids if i not in SEEN_IDS]:
                            SEEN_IDS.add(oid)
                            details = await self._fetch_offer_details(session, oid)
                            await channel.send(embed=self._build_offer_embed(details, oid))
                            await asyncio.sleep(2)
                except Exception as e:
                    print(f"Erreur: {e}")
                await asyncio.sleep(CHECK_INTERVAL_SECONDS)

    # ... (Garde tes fonctions _fetch_html, _extract_offer_ids, etc. telles quelles) ...
    async def _fetch_html(self, session: aiohttp.ClientSession, url: str) -> str:
        headers = {"User-Agent": random.choice(USER_AGENTS)}
        fetch_url = f"https://api.scraperapi.com?api_key={SCRAPERAPI_KEY}&url={quote_plus(url)}" if SCRAPERAPI_KEY else url
        async with session.get(fetch_url, headers=headers, timeout=REQUEST_TIMEOUT_SECONDS) as resp:
            return await resp.text()

    async def _extract_offer_ids(self, session: aiohttp.ClientSession) -> list[str]:
        html = await self._fetch_html(session, SEARCH_URL)
        return list(dict.fromkeys(re.findall(r"/offres/(\d+)", html)))

    async def _fetch_offer_details(self, session: aiohttp.ClientSession, offer_id: str) -> dict[str, str]:
        url = f"https://mon-vie-via.businessfrance.fr/offres/{offer_id}"
        html = await self._fetch_html(session, url)
        details = {"url": url}
        
        # Titre
        title_match = re.search(r"<title[^>]*>(.*?)</title>", html, re.IGNORECASE)
        if title_match:
            details["title"] = _clean_text(title_match.group(1)).split('|')[0].strip()
            
        # Extraction Business France
        details.update(_extract_business_france_fields(html))
        return details

intents = discord.Intents.default()
client = VIEBot(intents=intents)
client.run(DISCORD_TOKEN)
