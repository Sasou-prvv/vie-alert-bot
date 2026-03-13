import asyncio
import json
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
    
    # Extraction basée précisément sur ton document CAST [cite: 27, 28, 29, 30]
    comp = re.search(r"ETABLISSEMENT\s*[:\-]?\s*([A-Z0-9\s\.\-\&]+)", clean_html, re.IGNORECASE)
    if comp: details["company"] = comp.group(1).strip()

    loc = re.search(r"LA MISSION\s+([A-ZÀ-ÖØ-Þ\-\s']{3,})\s*\(([^\)]+)\)", clean_html)
    if loc:
        details["country"] = loc.group(1).strip()
        details["city"] = loc.group(2).strip()

    dur = re.search(r"\((\d+\s*mois)\)", clean_html)
    if dur: details["duration"] = dur.group(1)
    
    dates = re.search(r"du\s+([0-9]{2}[^\s]*\s+[^\s]+\s+[0-9]{4})\s+au\s+([0-9]{2}[^\s]*\s+[^\s]+\s+[0-9]{4})", clean_html, re.IGNORECASE)
    if dates:
        details["start"] = dates.group(1).strip()
        details["end"] = dates.group(2).strip()

    sal = re.search(r"REMUNERATION\s+MENSUELLE\s*[:\-]?\s*([0-9\s.,]+€?)", clean_html, re.IGNORECASE)
    if sal: details["salary"] = sal.group(1).strip()

    return details

class VIEBot(discord.Client):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.seen_ids = set()

    async def on_ready(self):
        print(f"✅ Connecté : {self.user}")
        self.loop.create_task(self.check_loop())

    async def fetch(self, url):
        # On ajoute render=true et on attend un peu pour que le JS charge les offres
        proxy_url = f"https://api.scraperapi.com?api_key={SCRAPERAPI_KEY}&url={quote_plus(url)}&render=true&wait_until=networkidle"
        async with aiohttp.ClientSession() as session:
            try:
                async with session.get(proxy_url, timeout=60) as resp:
                    return await resp.text()
            except Exception as e:
                print(f"❌ Erreur Fetch: {e}")
                return ""

    async def check_loop(self):
        await self.wait_until_ready()
        channel = self.get_channel(CHANNEL_ID)
        
        while not self.is_closed():
            print("🔍 Scan des offres en cours...")
            html = await self.fetch("https://mon-vie-via.businessfrance.fr/offres/recherche")
            
            # Recherche d'IDs plus large pour attraper tout ce qui ressemble à une offre
            ids = re.findall(r"offres/(\d+)", html)
            # Suppression des doublons tout en gardant l'ordre
            ids = list(dict.fromkeys(ids)) 
            
            print(f"📊 Offres détectées sur la page : {len(ids)}")

            if not self.seen_ids:
                # MODE TEST : On envoie la première offre trouvée pour valider le visuel
                if ids:
                    print(f"🧪 Envoi d'une offre de test (ID: {ids[0]})")
                    self.seen_ids.update(ids)
                    await self.send_offer(channel, ids[0], is_test=True)
                else:
                    print("⚠️ Aucune offre trouvée pour l'initialisation. Vérifie ton quota ScraperAPI.")
            else:
                for oid in ids:
                    if oid not in self.seen_ids:
                        self.seen_ids.add(oid)
                        await self.send_offer(channel, oid)
                        await asyncio.sleep(5)

            await asyncio.sleep(600) # Scan toutes les 10 minutes pour économiser ScraperAPI

    async def send_offer(self, channel, oid, is_test=False):
        detail_html = await self.fetch(f"https://mon-vie-via.businessfrance.fr/offres/{oid}")
        info = _extract_details(detail_html)
        
        title_prefix = "🧪 TEST DÉMARRAGE — " if is_test else "🚀 NOUVELLE OFFRE — "
        
        embed = discord.Embed(
            title=f"{title_prefix}{info.get('company', 'Entreprise Innovante')}",
            url=f"https://mon-vie-via.businessfrance.fr/offres/{oid}",
            color=discord.Color.blue() if not is_test else discord.Color.green()
        )
        
        # Organisation en champs propres [cite: 16, 19, 30]
        if info.get("city"): embed.add_field(name="🏙️ Ville", value=info["city"], inline=True)
        if info.get("country"): embed.add_field(name="🌍 Pays", value=info["country"], inline=True)
        if info.get("duration"): embed.add_field(name="📅 Durée", value=info["duration"], inline=True)
        if info.get("salary"): embed.add_field(name="💰 Salaire approx.", value=info["salary"], inline=True)
        if info.get("start"): embed.add_field(name="🚀 Début", value=info["start"], inline=True)
        if info.get("end"): embed.add_field(name="🏁 Fin", value=info["end"], inline=True)
        
        embed.add_field(name="🔗 Lien Direct", value=f"[Postuler ici](https://mon-vie-via.businessfrance.fr/offres/{oid})", inline=False)
        embed.set_footer(text="Alerte V.I.E • Mise à jour en temps réel")
        
        await channel.send(embed=embed)

client = VIEBot(intents=discord.Intents.default())
client.run(DISCORD_TOKEN)
