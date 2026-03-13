import asyncio
import os
import random
import re
import traceback
from datetime import datetime
from html import unescape
from urllib.parse import quote_plus

import aiohttp
import discord

# --- CONFIGURATION (Tes variables Railway) ---
DISCORD_TOKEN = os.environ.get("DISCORD_TOKEN")
CHANNEL_ID_RAW = os.environ.get("CHANNEL_ID")
SCRAPERAPI_KEY = os.environ.get("SCRAPERAPI_KEY") 

CHANNEL_ID = int(CHANNEL_ID_RAW) if CHANNEL_ID_RAW and CHANNEL_ID_RAW.isdigit() else 0
CHECK_INTERVAL_SECONDS = int(os.environ.get("CHECK_INTERVAL_SECONDS", "60"))

SEEN_IDS: set[str] = set()
SEARCH_URL = "https://mon-vie-via.businessfrance.fr/offres/recherche"

# --- UTILITAIRES DE NETTOYAGE ---

def _clean_text(value: str) -> str:
    value = unescape(value or "")
    # Supprime les scripts JS pour éviter le bug d'affichage
    value = re.sub(r"<script.*?>.*?</script>", " ", value, flags=re.DOTALL)
    value = re.sub(r"<[^>]+>", " ", value)
    value = re.sub(r"\s+", " ", value)
    return value.strip()

def _format_french_date(date_str: str) -> str:
    mois_fr = {
        "janvier": "01", "février": "02", "fevrier": "02", "mars": "03", "avril": "04", 
        "mai": "05", "juin": "06", "juillet": "07", "août": "08", "aout": "08",
        "septembre": "09", "octobre": "10", "novembre": "11", "décembre": "12", "decembre": "12"
    }
    d = date_str.lower().strip()
    for m_fr, m_num in mois_fr.items():
        new_d, count = re.subn(rf"\b{m_fr}\b", m_num, d)
        if count > 0:
            parts = new_d.split()
            if len(parts) == 3:
                return f"{parts[0].zfill(2)}/{parts[1]}/{parts[2]}"
    return date_str

def _extract_fields(html: str) -> dict[str, str]:
    info: dict[str, str] = {}
    
    # Localisation (Pays et Ville) - Correction anti-JS
    mission_match = re.search(r"LA\s+MISSION", html, re.IGNORECASE)
    if mission_match:
        fragment = html[mission_match.end() : mission_match.end() + 1000]
        loc_match = re.search(r"([A-ZÀ-ÖØ-Þ\-\s']{3,})\s*\(([^)]+)\)", fragment)
        if loc_match:
            city = _clean_text(loc_match.group(2))
            if "function" not in city.lower():
                info["country"] = _clean_text(loc_match.group(1))
                info["city"] = city

    # Entreprise [cite: 29]
    company_match = re.search(r"ETABLISSEMENT\s*:\s*([^<\n\r]+)", html, re.IGNORECASE)
    if company_match:
        info["company"] = _clean_text(company_match.group(1))

    # Dates et Durée [cite: 28]
    dr_match = re.search(r"du\s+([0-9]{1,2}\s+[^\s]+\s+[0-9]{4})\s+au\s+([0-9]{1,2}\s+[^\s]+\s+[0-9]{4})\s*\((\d+)\s*mois\)", html, re.IGNORECASE)
    if dr_match:
        info["start"] = _format_french_date(_clean_text(dr_match.group(1)))
        info["deadline"] = _format_french_date(_clean_text(dr_match.group(2)))
        info["duration"] = _clean_text(dr_match.group(3))

    # Salaire [cite: 30]
    salary_match = re.search(r"REMUNERATION\s+MENSUELLE\s*:\s*([0-9\s\xa0]+)[.,]?[0-9]*\s*€", html, re.IGNORECASE)
    if salary_match:
        info["salary"] = f"{re.sub(r'\s+', '', salary_match.group(1))} €"

    return info

# --- BOT DISCORD ---

class VIEBot(discord.Client):
    async def on_ready(self):
        print(f"✅ Bot connecté : {self.user}")
        self.loop.create_task(self.check_vie())

    def _build_embed(self, details: dict[str, str], offer_id: str, is_test: bool = False) -> discord.Embed:
        title = details.get("title", f"Offre VIE #{offer_id}")
        embed = discord.Embed(
            title=f"{'🧪 TEST RÉEL — ' if is_test else ''}{title}"[:256],
            color=discord.Color.blue(),
            url=details.get("url")
        )
        fields = [
            ("🏢 Entreprise", "company"), ("📅 Durée (mois)", "duration"),
            ("🏙️ Ville", "city"), ("🌍 Pays", "country"),
            ("💰 Salaire", "salary"), ("🚀 Début", "start"), ("🏁 Fin", "deadline")
        ]
        for name, key in fields:
            if details.get(key):
                embed.add_field(name=name, value=details[key], inline=True)
        
        embed.add_field(name="🔗 Lien", value=f"[Voir l'offre sur Business France]({details.get('url')})", inline=False)
        embed.set_footer(text="FR Alerte VIE • Business France")
        return embed

    async def check_vie(self):
        await self.wait_until_ready()
        channel = self.get_channel(CHANNEL_ID)
        
        async with aiohttp.ClientSession() as session:
            # --- TEST INITIAL AUTOMATIQUE SUR LA DERNIÈRE OFFRE ---
            try:
                print("🧪 Récupération de la dernière offre réelle pour le test...")
                initial_ids = await self._extract_ids(session)
                if initial_ids:
                    latest_id = initial_ids[0]
                    details = await self._fetch_details(session, latest_id)
                    await channel.send(embed=self._build_embed(details, latest_id, is_test=True))
                    SEEN_IDS.update(initial_ids) # Marquer tout comme lu pour éviter le spam
                else:
                    print("⚠️ Aucune offre trouvée lors du test initial.")
            except Exception as e:
                print(f"❌ Erreur test initial : {e}")

            while not self.is_closed():
                try:
                    print(f"🔍 [{datetime.now().strftime('%H:%M')}] Scan des nouvelles offres...")
                    current_ids = await self._extract_ids(session)
                    new_ids = [oid for oid in current_ids if oid not in SEEN_IDS]
                    
                    for oid in new_ids:
                        details = await self._fetch_details(session, oid)
                        await channel.send(embed=self._build_embed(details, oid))
                        SEEN_IDS.add(oid)
                        await asyncio.sleep(2)
                            
                except Exception:
                    print(f"❌ Erreur scan :\n{traceback.format_exc()}")
                
                await asyncio.sleep(CHECK_INTERVAL_SECONDS)

    async def _fetch_html(self, session, url):
        # Utilisation impérative de render=true pour charger les offres
        target = f"https://api.scraperapi.com?api_key={SCRAPERAPI_KEY}&url={quote_plus(url)}&render=true"
        async with session.get(target, timeout=30) as resp:
            return await resp.text()

    async def _extract_ids(self, session):
        html = await self._fetch_html(session, SEARCH_URL)
        return list(dict.fromkeys(re.findall(r"/offres/(\d+)", html)))

    async def _fetch_details(self, session, oid):
        url = f"https://mon-vie-via.businessfrance.fr/offres/{oid}"
        html = await self._fetch_html(session, url)
        details = {"url": url, "title": f"Offre #{oid}"}
        t_match = re.search(r"<title[^>]*>(.*?)</title>", html, re.IGNORECASE)
        if t_match:
            details["title"] = _clean_text(t_match.group(1)).split('|')[0].strip()
        details.update(_extract_fields(html))
        return details

# --- LANCEMENT ---
intents = discord.Intents.default()
client = VIEBot(intents=intents)
client.run(DISCORD_TOKEN)
