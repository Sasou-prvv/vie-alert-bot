import asyncio
import os
import re
from html import unescape
from urllib.parse import quote_plus
import aiohttp
import discord

# --- CONFIGURATION ---
DISCORD_TOKEN = os.environ.get("DISCORD_TOKEN")
CHANNEL_ID = int(os.environ.get("CHANNEL_ID"))
SCRAPERAPI_KEY = os.environ.get("SCRAPERAPI_KEY")

def _clean_text(value: str) -> str:
    if not value: return ""
    value = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", value, flags=re.IGNORECASE | re.DOTALL)
    value = unescape(value)
    value = re.sub(r"<[^>]+>", " ", value)
    return " ".join(value.split()).strip()

def _extract_details(html: str) -> dict:
    details = {}
    clean_html = _clean_text(html)
    
    # Titre du poste (H1)
    title_match = re.search(r"<h1[^>]*>(.*?)</h1>", html, re.IGNORECASE | re.DOTALL)
    if title_match: details["title"] = _clean_text(title_match.group(1))

    # Extraction précise (basée sur ton PDF CAST)
    comp = re.search(r"ETABLISSEMENT\s*[:\-]?\s*([A-Z0-9\s\.\-\&]+)", clean_html, re.IGNORECASE)
    if comp: details["company"] = comp.group(1).strip()

    loc = re.search(r"LA MISSION\s+([A-ZÀ-ÖØ-Þ\-\s']{3,})\s*\(([^\)]+)\)", clean_html)
    if loc:
        details["country"] = loc.group(1).strip()
        details["city"] = loc.group(2).strip()

    dur = re.search(r"\((\d+\s*mois)\)", clean_html)
    if dur: details["duration"] = dur.group(1)
    
    sal = re.search(r"REMUNERATION\s+MENSUELLE\s*[:\-]?\s*([0-9\s.,]+€?)", clean_html, re.IGNORECASE)
    if sal: details["salary"] = sal.group(1).strip()

    return details

class VIEBot(discord.Client):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.last_checked_id = 237800 # On part d'un ID récent connu

    async def on_ready(self):
        print(f"✅ Bot connecté : {self.user}")
        self.loop.create_task(self.check_loop())

    async def fetch_page(self, url):
        # Utilisation de ScraperAPI en mode simple (plus rapide)
        proxy_url = f"https://api.scraperapi.com?api_key={SCRAPERAPI_KEY}&url={quote_plus(url)}"
        async with aiohttp.ClientSession() as session:
            try:
                async with session.get(proxy_url, timeout=30) as resp:
                    if resp.status == 200:
                        return await resp.text()
                    return None
            except:
                return None

    async def check_loop(self):
        await self.wait_until_ready()
        channel = self.get_channel(CHANNEL_ID)
        
        while not self.is_closed():
            print(f"🔍 Vérification à partir de l'ID {self.last_checked_id}...")
            
            # On teste les 10 prochains IDs pour voir si de nouvelles offres sont nées
            for i in range(1, 11):
                target_id = self.last_checked_id + i
                url = f"https://mon-vie-via.businessfrance.fr/offres/{target_id}"
                
                html = await self.fetch_page(url)
                
                if html and "ETABLISSEMENT" in html:
                    print(f"✨ Nouvelle offre trouvée ! ID: {target_id}")
                    info = _extract_details(html)
                    
                    embed = discord.Embed(
                        title=info.get("title", f"Offre V.I.E #{target_id}"),
                        url=url,
                        color=discord.Color.blue()
                    )
                    embed.add_field(name="🏢 Entreprise", value=info.get("company", "N/C"), inline=False)
                    embed.add_field(name="🏙️ Ville", value=info.get("city", "N/C"), inline=True)
                    embed.add_field(name="🌍 Pays", value=info.get("country", "N/C"), inline=True)
                    embed.add_field(name="💰 Salaire", value=info.get("salary", "N/C"), inline=True)
                    embed.add_field(name="📅 Durée", value=info.get("duration", "N/C"), inline=True)
                    
                    await channel.send(embed=embed)
                    self.last_checked_id = target_id
                    await asyncio.sleep(2)
                
            await asyncio.sleep(300) # Attendre 5 min avant de retenter les IDs suivants

client = VIEBot(intents=discord.Intents.default())
client.run(DISCORD_TOKEN)
