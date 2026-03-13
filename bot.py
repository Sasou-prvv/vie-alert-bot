import asyncio
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

CHANNEL_ID = int(CHANNEL_ID_RAW) if CHANNEL_ID_RAW else 0
CHECK_INTERVAL_SECONDS = int(os.environ.get("CHECK_INTERVAL_SECONDS", "60"))
SEEN_IDS: set[str] = set()
SEARCH_URL = "https://mon-vie-via.businessfrance.fr/offres/recherche"

def _clean_text(value: str) -> str:
    value = unescape(value or "")
    value = re.sub(r"<[^>]+>", " ", value)
    value = re.sub(r"\s+", " ", value)
    return value.strip()

def _extract_business_france_fields(html: str) -> dict[str, str]:
    info: dict[str, str] = {}
    mission_match = re.search(r"LA\s+MISSION", html, re.IGNORECASE)
    if mission_match:
        fragment = html[mission_match.end() : mission_match.end() + 500]
        loc_match = re.search(r"([A-ZÀ-ÖØ-Þ\-\s']{3,})\s*\(([^)]+)\)", fragment)
        if loc_match:
            city = _clean_text(loc_match.group(2))
            # Protection contre le bug JS
            if "function" not in city.lower():
                info["country"] = _clean_text(loc_match.group(1))
                info["city"] = city

    company_match = re.search(r"ETABLISSEMENT\s*:\s*([^<\n\r]+)", html, re.IGNORECASE)
    if company_match: info["company"] = _clean_text(company_match.group(1))

    salary_match = re.search(r"REMUNERATION\s+MENSUELLE\s*:\s*([0-9\s\xa0]+)[.,]?[0-9]*\s*€", html, re.IGNORECASE)
    if salary_match: info["salary"] = f"{re.sub(r'\s+', '', salary_match.group(1))} €"

    return info

class VIEBot(discord.Client):
    async def on_ready(self):
        print(f"✅ Bot connecté : {self.user}")
        self.loop.create_task(self.check_vie())

    def _build_offer_embed(self, details: dict[str, str], offer_id: str, is_test: bool = False) -> discord.Embed:
        title = details.get("title", f"Offre VIE #{offer_id}")
        embed = discord.Embed(
            title=f"{'🧪 TEST RÉEL — ' if is_test else ''}{title}"[:256],
            color=discord.Color.blue(),
            url=details.get("url")
        )
        for label, key in [("🏢 Entreprise", "company"), ("🏙️ Ville", "city"), ("🌍 Pays", "country"), ("💰 Salaire", "salary")]:
            if details.get(key):
                embed.add_field(name=label, value=details[key], inline=True)
        
        embed.add_field(name="🔗 Lien", value=f"[Voir l'offre]({details.get('url')})", inline=False)
        embed.set_footer(text="FR Alerte VIE • Business France")
        return embed

    async def check_vie(self):
        await self.wait_until_ready()
        channel = self.get_channel(CHANNEL_ID)
        
        async with aiohttp.ClientSession() as session:
            # --- TEST AU DÉMARRAGE SUR UNE VRAIE OFFRE ---
            try:
                ids = await self._extract_offer_ids(session)
                if ids:
                    details = await self._fetch_offer_details(session, ids[0])
                    await channel.send(embed=self._build_offer_embed(details, ids[0], is_test=True))
                    SEEN_IDS.update(ids) # On marque tout comme "vu" pour ne pas spammer
            except Exception as e:
                print(f"❌ Erreur test initial : {e}")

            while not self.is_closed():
                try:
                    print(f"🔍 [{datetime.now().strftime('%H:%M')}] Scan...")
                    found_ids = await self._extract_offer_ids(session)
                    new_offers = [oid for oid in found_ids if oid not in SEEN_IDS]
                    
                    for oid in new_offers:
                        details = await self._fetch_offer_details(session, oid)
                        await channel.send(embed=self._build_offer_embed(details, oid))
                        SEEN_IDS.add(oid)
                        await asyncio.sleep(2)
                except Exception as e:
                    print(f"❌ Erreur : {e}")
                await asyncio.sleep(CHECK_INTERVAL_SECONDS)

    async def _fetch_html(self, session, url):
        target = f"https://api.scraperapi.com?api_key={SCRAPERAPI_KEY}&url={quote_plus(url)}&render=true"
        async with session.get(target, timeout=30) as r:
            return await r.text()

    async def _extract_offer_ids(self, session):
        html = await self._fetch_html(session, SEARCH_URL)
        return list(dict.fromkeys(re.findall(r"/offres/(\d+)", html)))

    async def _fetch_offer_details(self, session, oid):
        url = f"https://mon-vie-via.businessfrance.fr/offres/{oid}"
        html = await self._fetch_html(session, url)
        details = {"url": url, "title": f"Offre #{oid}"}
        t_match = re.search(r"<title[^>]*>(.*?)</title>", html, re.IGNORECASE)
        if t_match: details["title"] = _clean_text(t_match.group(1)).split('|')[0].strip()
        details.update(_extract_business_france_fields(html))
        return details

client = VIEBot(intents=discord.Intents.default())
client.run(DISCORD_TOKEN)
