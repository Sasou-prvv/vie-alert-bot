import discord
import asyncio
import aiohttp
import os
from datetime import datetime
from bs4 import BeautifulSoup

# Configuration Railway
DISCORD_TOKEN = os.environ.get('DISCORD_TOKEN', '')
CHANNEL_ID = int(os.environ.get('CHANNEL_ID', '0'))

# Mots-clés (Gardés tels quels)
KEYWORDS = ["industrie", "industriel", "production", "manufacturing", "usine", "atelier", "lean", "qualité", "securite", "logistique"]

# Pays exclus (Gardés tels quels)
EXCLUDED_COUNTRIES = ["france", "allemagne", "espagne", "italie", "portugal", "belgique"]

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
}

seen_ids = set()

def is_relevant(title, country=""):
    title_lower = title.lower()
    for excl in EXCLUDED_COUNTRIES:
        if excl in title_lower: return False
    for kw in KEYWORDS:
        if kw in title_lower: return True
    return False

class VIEBot(discord.Client):
    async def on_ready(self):
        print(f'Bot connecté : {self.user}')
        if not hasattr(self, 'task_started'):
            self.task_started = True
            self.loop.create_task(self.check_vie())

    async def check_vie(self):
        await self.wait_until_ready()
        channel = self.get_channel(CHANNEL_ID)
        
        while not self.is_closed():
            try:
                url = "https://mon-volontariat-international.businessfrance.fr/recherche"
                async with aiohttp.ClientSession(headers=HEADERS) as session:
                    async with session.get(url, timeout=30) as resp:
                        if resp.status == 200:
                            html = await resp.text()
                            soup = BeautifulSoup(html, 'html.parser')
                            offers = soup.find_all('div', class_='v-card')
                            print(f"Scan réussi : {len(offers)} offres trouvées")
                            
                            for offer in offers:
                                try:
                                    title_elem = offer.find('h2') or offer.find('div', class_='v-card__title')
                                    link_elem = offer.find('a')
                                    if not title_elem or not link_elem: continue
                                        
                                    title = title_elem.get_text(strip=True)
                                    link = "https://mon-volontariat-international.businessfrance.fr" + link_elem['href']
                                    offer_id = link.split('/')[-1]

                                    if offer_id not in seen_ids:
                                        seen_ids.add(offer_id)
                                        if is_relevant(title):
                                            embed = discord.Embed(title=f"🚀 Nouveau VIE : {title}", url=link, color=0x00ff00, timestamp=datetime.utcnow())
                                            await channel.send(embed=embed)
                                except: continue
                        else:
                            print(f"Erreur site : {resp.status}")
            except Exception as e:
                print(f"Erreur : {e}")
            await asyncio.sleep(600)

intents = discord.Intents.default()
intents.message_content = True
client = VIEBot(intents=intents)
client.run(DISCORD_TOKEN)
