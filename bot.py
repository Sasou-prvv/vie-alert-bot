import asyncio
import json
import os
import random
import re
from html import unescape
from urllib.parse import quote_plus
import aiohttp
import discord

# --- CONFIGURATION (Tes variables Railway) ---
DISCORD_TOKEN = os.environ.get("DISCORD_TOKEN")
CHANNEL_ID = int(os.environ.get("CHANNEL_ID"))
SCRAPERAPI_KEY = os.environ.get("SCRAPERAPI_KEY")

# --- LOGIQUE D'EXTRACTION NETTOYÉE ---

def _clean_text(value: str) -> str:
    if not value: return ""
    # Supprime le JS, l'HTML et les espaces en trop
    value = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", value, flags=re.IGNORECASE | re.DOTALL)
    value = unescape(value)
    value = re.sub(r"<[^>]+>", " ", value)
    return " ".join(value.split()).strip()

def _extract_details(html: str) -> dict:
    """Extrait les infos proprement en évitant le texte 'bruit' du JS"""
    details = {}
    clean_html = _clean_text(html)

    # 1. Entreprise
    comp = re.search(r"ETABLISSEMENT\s*[:\-]?\s*([A-Z0-9\s\.\-\&]+)", clean_html, re.IGNORECASE)
    if comp: details["company"] = comp.group(1).strip()

    # 2. Localisation (Pays + Ville)
    loc = re.search(r"LA MISSION\s+([A-ZÀ-ÖØ-Þ\-\s']{3,})\s*\(([^\)]+)\)", clean_html)
    if loc:
        details["country"] = loc.group(1).strip()
        details["city"] = loc.group(2).strip()

    # 3. Durée et Dates
    dur = re.search(r"\((\d+\s*mois)\)", clean_html)
    if dur: details["duration"] = dur.group(1)
    
    dates = re.search(r"du\s+([0-9/.\s\w]+)\s+au\s+([0-9/.\s\w]+)", clean_html, re.IGNORECASE)
    if dates:
        details["start"] = dates.group(1).strip()
        details["end"] = dates.group(2).strip()

    # 4. Salaire
    sal = re.search(r"REMUNERATION\s+MENSUELLE\s*[:\-]?\s*([0-9\s.,]+€?)", clean_html, re.IGNORECASE)
    if sal: details["salary"] = sal.group(1).strip()

    return details

# --- BOT (Ta structure d'origine) ---

class VIEBot(discord.Client):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.seen_ids = set()

    async def on_ready(self):
        print(f"Connecté en tant que {self.user}")
        self.loop.create_task(self.check_loop())

    async def fetch(self, url):
        # Utilise ScraperAPI si dispo, sinon direct
        target = f"https://api.scraperapi.com?api_key={SCRAPERAPI_KEY}&url={quote_plus(url)}&render=true" if SCRAPERAPI_KEY else url
        async with aiohttp.ClientSession() as session:
            try:
                async with session.get(target, timeout=30) as resp:
                    return await resp.text()
            except:
                return ""

    async def check_loop(self):
        await self.wait_until_ready()
        channel = self.get_channel(CHANNEL_ID)
        
        while not self.is_closed():
            print("Scan en cours...")
            # On utilise l'URL de base qui marchait chez toi
            html = await self.fetch("https://mon-vie-via.businessfrance.fr/offres/recherche")
            
            # Extraction des IDs (Ta méthode)
            ids = re.findall(r"/offres/(\d+)", html)
            new_ids = [i for i in ids if i not in self.seen_ids]

            if not self.seen_ids: # Premier lancement
                self.seen_ids.update(ids)
                print(f"Initialisation : {len(ids)} offres mémorisées.")
            else:
                for oid in new_ids:
                    self.seen_ids.add(oid)
                    # Récupère les détails de la page spécifique
                    detail_html = await self.fetch(f"https://mon-vie-via.businessfrance.fr/offres/{oid}")
                    info = _extract_details(detail_html)
                    
                    # Construction de l'embed propre
                    embed = discord.Embed(
                        title=info.get("company", "Nouvelle offre V.I.E"),
                        url=f"https://mon-vie-via.businessfrance.fr/offres/{oid}",
                        color=discord.Color.blue()
                    )
                    
                    if info.get("city"): embed.add_field(name="🏙️ Ville", value=info["city"], inline=True)
                    if info.get("country"): embed.add_field(name="🌍 Pays", value=info["country"], inline=True)
                    if info.get("duration"): embed.add_field(name="📅 Durée", value=info["duration"], inline=True)
                    if info.get("salary"): embed.add_field(name="💰 Salaire", value=info["salary"], inline=True)
                    if info.get("start"): embed.add_field(name="🚀 Début", value=info["start"], inline=True)
                    
                    embed.set_footer(text="Business France • Alerte V.I.E")
                    
                    await channel.send(embed=embed)
                    await asyncio.sleep(2)

            await asyncio.sleep(300) # 5 minutes

client = VIEBot(intents=discord.Intents.default())
client.run(DISCORD_TOKEN)
