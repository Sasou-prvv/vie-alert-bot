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

# --- CONFIGURATION ---
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
SEND_STARTUP_TEST = True # Forcé à True pour vérifier que ça marche

SEEN_IDS: set[str] = set()
SEARCH_URL = "https://mon-vie-via.businessfrance.fr/offres/recherche"

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
]

# --- UTILITAIRES ---

def _clean_text(value: str) -> str:
    value = unescape(value or "")
    value = re.sub(r"<[^>]+>", " ", value)
    value = re.sub(r"\s+", " ", value)
    return value.strip()

def _format_french_date(date_str: str) -> str:
    mois_fr = {
        "janvier": "01", "février": "02", "fevrier": "02", "mars": "03", 
        "avril": "04", "mai": "05", "juin": "06", "juillet": "07", 
        "août": "08", "aout": "08", "septembre": "09", "octobre": "10", 
        "novembre": "11", "décembre": "12", "decembre": "12"
    }
    d = date_str.lower().strip()
    for m_fr, m_num in mois_fr.items():
        new_d, count = re.subn(rf"\b{m_fr}\b", m_num, d)
        if count > 0:
            parts = new_d.split()
            if len(parts) == 3:
                return f"{parts[0].zfill(2)}/{parts[1]}/{parts[2]}"
    return date_str

def _extract_business_france_fields(html: str) -> dict[str, str]:
    info: dict[str, str] = {}
    
    # Correction Localisation : on cherche spécifiquement après "LA MISSION"
    # et on exclut les balises scripts pour éviter le bug "function(w...)"
    mission_match = re.search(r"LA\s+MISSION", html, re.IGNORECASE)
    if mission_match:
        # On regarde les 500 caractères après "LA MISSION" en ignorant le JS
        fragment = html[mission_match.end() : mission_match.end() + 500]
        # Format attendu : PAYS (VILLE) [cite: 27]
        loc_match = re.search(r"([A-ZÀ-ÖØ-Þ\-\s']{3,})\s*\(([^)]+)\)", fragment)
        if loc_match:
            country = _clean_text(loc_match.group(1))
            city = _clean_text(loc_match.group(2))
            # Sécurité anti-code JS
            if "function" not in city.lower() and "{" not in city:
                info["country"] = country
                info["city"] = city

    # Dates et Durée [cite: 28]
    date_range_match = re.search(
        r"du\s+([0-9]{1,2}\s+[^\s]+\s+[0-9]{4})\s+au\s+([0-9]{1,2}\s+[^\s]+\s+[0-9]{4})\s*\((\d+)\s*mois\)",
        html, re.IGNORECASE,
    )
    if date_range_match:
        info["start"] = _format_french_date(_clean_text(date_range_match.group(1)))
        info["deadline"] = _format_french_date(_clean_text(date_range_match.group(2)))
        info["duration"] = _clean_text(date_range_match.group(3))

    # Entreprise [cite: 29]
    company_match = re.search(r"ETABLISSEMENT\s*:\s*([^<\n\r]+)", html, re.IGNORECASE)
    if company_match:
        info["company"] = _clean_text(company_match.group(1))

    # Salaire sans centimes [cite: 30]
    salary_match = re.search(r"REMUNERATION\s+MENSUELLE\s*:\s*([0-9\s\xa0]+)[.,]?[0-9]*\s*€", html, re.IGNORECASE)
    if salary_match:
        clean_sal = re.sub(r"\s+", "", salary_match.group(1))
        info["salary"] = f"{clean_sal} €"

    return info

# --- BOT DISCORD ---

class VIEBot(discord.Client):
    async def on_ready(self):
        print(f"✅ Bot connecté : {self.user}")
        self.loop.create_task(self.check_vie())

    def _build_offer_embed(self, details: dict[str, str], offer_id: str, is_test: bool = False) -> discord.Embed:
        title = details.get("title", f"Offre VIE #{offer_id}")
        # Titre propre basé sur le PDF [cite: 15]
        embed = discord.Embed(
            title=f"{'🧪 TEST — ' if is_test else ''}{title}"[:256],
            color=discord.Color.blue(),
            url=details.get("url")
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
            value=f"[Voir l'offre sur Business France]({details.get('url')})",
            inline=False,
        )
        embed.set_footer(text="FR Alerte VIE • Business France")
        return embed

    async def check_vie(self):
        await self.wait_until_ready()
        channel = self.get_channel(CHANNEL_ID)
        first_run = True

        async with aiohttp.ClientSession() as session:
            while not self.is_closed():
                try:
                    print(f"🔍 [{datetime.now().strftime('%H:%M:%S')}] Vérification des offres...")
                    found_ids = await self._extract_offer_ids(session)
                    print(f"📊 {len(found_ids)} offres trouvées sur la page.")
                    
                    if first_run:
                        # On envoie une offre en test pour confirmer que tout est OK
                        if found_ids:
                            print(f"✉️ Envoi d'un message de test (ID: {found_ids[0]})")
                            details = await self._fetch_offer_details(session, found_ids[0])
                            await channel.send(embed=self._build_offer_embed(details, found_ids[0], True))
                        SEEN_IDS.update(found_ids)
                        first_run = False
                    else:
                        new_offers = [oid for oid in found_ids if oid not in SEEN_IDS]
                        if new_offers:
                            print(f"✨ {len(new_offers)} nouvelles offres détectées !")
                            for oid in new_offers:
                                SEEN_IDS.add(oid)
                                details = await self._fetch_offer_details(session, oid)
                                await channel.send(embed=self._build_offer_embed(details, oid))
                                await asyncio.sleep(2)
                        else:
                            print("😴 Pas de nouvelle offre pour le moment.")

                except Exception as e:
                    print(f"❌ Erreur: {e}")
                
                await asyncio.sleep(CHECK_INTERVAL_SECONDS)

    async def _fetch_html(self, session: aiohttp.ClientSession, url: str) -> str:
        headers = {"User-Agent": random.choice(USER_AGENTS)}
        target_url = f"https://api.scraperapi.com?api_key={SCRAPERAPI_KEY}&url={quote_plus(url)}" if SCRAPERAPI_KEY else url
        async with session.get(target_url, headers=headers, timeout=REQUEST_TIMEOUT_SECONDS) as resp:
            return await resp.text()

    async def _extract_offer_ids(self, session: aiohttp.ClientSession) -> list[str]:
        html = await self._fetch_html(session, SEARCH_URL)
        return list(dict.fromkeys(re.findall(r"/offres/(\d+)", html)))

    async def _fetch_offer_details(self, session: aiohttp.ClientSession, offer_id: str) -> dict[str, str]:
        url = f"https://mon-vie-via.businessfrance.fr/offres/{offer_id}"
        html = await self._fetch_html(session, url)
        details = {"url": url}
        title_match = re.search(r"<title[^>]*>(.*?)</title>", html, re.IGNORECASE)
        if title_match:
            details["title"] = _clean_text(title_match.group(1)).split('|')[0].strip()
        details.update(_extract_business_france_fields(html))
        return details

intents = discord.Intents.default()
client = VIEBot(intents=intents)
client.run(DISCORD_TOKEN)
