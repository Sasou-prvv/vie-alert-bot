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

# --- NETTOYAGE DES DONNÉES ---

def _clean_text(value: str) -> str:
    value = unescape(value or "")
    # Supprime les scripts JS pour éviter le bug "function(w,d..."
    value = re.sub(r"<script.*?>.*?</script>", " ", value, flags=re.DOTALL)
    value = re.sub(r"<[^>]+>", " ", value)
    value = re.sub(r"\s+", " ", value)
    return value.strip()

def _extract_fields(html: str) -> dict[str, str]:
    info: dict[str, str] = {}
    
    # 1. Localisation (Pays et Ville) - Correction anti-JS
    mission_match = re.search(r"LA\s+MISSION", html, re.IGNORECASE)
    if mission_match:
        frag = html[mission_match.end() : mission_match.end() + 1000]
        # Recherche le format "PAYS (VILLE)" [cite: 27]
        loc = re.search(r"([A-ZÀ-ÖØ-Þ\-\s']{3,})\s*\(([^)]+)\)", frag)
        if loc and "function" not in loc.group(2).lower():
            info["country"] = _clean_text(loc.group(1))
            info["city"] = _clean_text(loc.group(2))

    # 2. Entreprise [cite: 29]
    comp = re.search(r"ETABLISSEMENT\s*:\s*([^<\n]+)", html, re.IGNORECASE)
    if comp: info["company"] = _clean_text(comp.group(1))

    # 3. Salaire [cite: 30]
    sal = re.search(r"REMUNERATION\s+MENSUELLE\s*:\s*([0-9\s\xa0]+)[.,]?[0-9]*\s*€", html, re.IGNORECASE)
    if sal: info["salary"] = f"{re.sub(r'\s+', '', sal.group(1))} €"

    return info

# --- BOT ---

class VIEBot(discord.Client):
    async def on_ready(self):
        print(f"✅ Bot connecté : {self.user}")
        self.loop.create_task(self.check_vie())

    def _build_embed(self, details: dict[str, str], is_test=False) -> discord.Embed:
        embed = discord.Embed(
            title=f"{'🧪 TEST — ' if is_test else ''}{details.get('title')}"[:256],
            color=discord.Color.blue(),
            url=details.get("url")
        )
        for label, key in [("🏢 Entreprise", "company"), ("🏙️ Ville", "city"), ("🌍 Pays", "country"), ("💰 Salaire", "salary")]:
            if details.get(key):
                embed.add_field(name=label, value=details[key], inline=True)
        
        embed.set_footer(text="FR Alerte VIE • Business France")
        return embed

    async def check_vie(self):
        await self.wait_until_ready()
        channel = self.get_channel(CHANNEL_ID)
        
        # --- TEST FORCÉ AVEC LES INFOS DU PDF (CAST) ---
        test_data = {
            "title": "IT Infrastructure & Network Administrator (H/F)", # [cite: 15]
            "company": "CAST", "city": "NEW-YORK -NY-", "country": "ETATS-UNIS", # [cite: 14, 27, 29]
            "salary": "5046 €", "url": "https://mon-vie-via.businessfrance.fr/offres/237889" # [cite: 30, 71]
        }
        try:
            print("✉️ Envoi du message de test...")
            await channel.send(embed=self._build_embed(test_data, is_test=True))
        except Exception as e:
            print(f"❌ Erreur Discord (Vérifie tes permissions ou ton ID) : {e}")

        async with aiohttp.ClientSession() as session:
            while not self.is_closed():
                try:
                    print(f"🔍 [{datetime.now().strftime('%H:%M')}] Scan avec ScraperAPI (Render ON)...")
                    html = await self._fetch_html(session, SEARCH_URL)
                    
                    # Extraction des IDs d'offres
                    found_ids = list(dict.fromkeys(re.findall(r"/offres/(\d+)", html)))
                    print(f"📊 {len(found_ids)} offres détectées.")
                    
                    if not SEEN_IDS:
                        SEEN_IDS.update(found_ids)
                    else:
                        for oid in [i for i in found_ids if i not in SEEN_IDS]:
                            SEEN_IDS.add(oid)
                            details = await self._fetch_details(session, oid)
                            await channel.send(embed=self._build_embed(details))
                            await asyncio.sleep(2)
                except Exception:
                    print(f"❌ Erreur Scan :\n{traceback.format_exc()}")
                
                await asyncio.sleep(CHECK_INTERVAL_SECONDS)

    async def _fetch_html(self, session, url):
        # Utilisation de render=true pour charger les offres JS
        target = f"https://api.scraperapi.com?api_key={SCRAPERAPI_KEY}&url={quote_plus(url)}&render=true"
        async with session.get(target, timeout=30) as r:
            return await r.text()

    async def _fetch_details(self, session, oid):
        url = f"https://mon-vie-via.businessfrance.fr/offres/{oid}"
        html = await self._fetch_html(session, url)
        details = {"url": url, "title": f"Offre #{oid}"}
        t_match = re.search(r"<title[^>]*>(.*?)</title>", html, re.IGNORECASE)
        if t_match: details["title"] = _clean_text(t_match.group(1)).split('|')[0]
        details.update(_extract_fields(html))
        return details

client = VIEBot(intents=discord.Intents.default())
client.run(DISCORD_TOKEN)
