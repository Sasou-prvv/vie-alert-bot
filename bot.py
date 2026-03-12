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
KEYWORDS = [
    "industrie", "industriel", "production", "manufacturing", "usine", "atelier",
    "lean", "amélioration continue", "kaizen", "5s", "six sigma",
    "gestion de production", "planification", "ordonnancement",
    "supply chain", "logistique", "achats", "procurement",
    "qualité", "qualite", "qse", "hse", "sécurité", "securite",
    "audit", "contrôle", "controle", "inspection", "conformité", "conformite",
    "iso", "certification", "norme", "énergie", "energie", "nucleaire", "nucléaire"
]

# Pays exclus (Gardés tels quels)
EXCLUDED_COUNTRIES = [
    "france", "allemagne", "espagne", "italie", "portugal", "belgique",
    "maroc", "tunisie", "algerie", "algérie", "sénégal", "senegal"
]

# Simulation d'un navigateur pour ne pas être bloqué
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
}

seen_ids = set()

def is_relevant(title, country=""):
    title_lower = title.lower()
    country_lower = country.lower()

    # Exclure les pays
    for excl in EXCLUDED_COUNTRIES:
        if excl in country_lower or excl in title_lower:
            return False

    # Accepter si mot-clé présent
    for kw in KEYWORDS:
        if kw in title_lower:
            return True
    return False

class VIEBot(discord.Client):
    async def on_ready(self):
        print(f'Bot connecté : {self.user}')
        self.loop.create_task(self.check_vie())

    async def check_vie(self):
        await self.wait_until_ready()
        channel = self.get_channel(CHANNEL_ID)
        
        while not self.is_closed():
            try:
                # On scanne la page de recherche directement
                url = "https://mon-volontariat-international.businessfrance.fr/recherche"
                async with aiohttp.ClientSession(headers=HEADERS) as session:
                    async with session.get(url, timeout=30) as resp:
                        if resp.status == 200:
                            html = await resp.text()
                            soup = BeautifulSoup(html, 'html.parser')
                            
                            # On cherche les cartes d'offres (structure typique Civiweb)
                            offers = soup.find_all('div', class_='v-card') 
                            
                            for offer in offers:
                                try:
                                    title_elem = offer.find('h2') or offer.find('div', class_='v-card__title')
                                    link_elem = offer.find('a')
                                    
                                    if not title_elem or not link_elem:
                                        continue
                                        
                                    title = title_elem.get_text(strip=True)
                                    link = "https://mon-volontariat-international.businessfrance.fr" + link_elem['href']
                                    offer_id = link.split('/')[-1]

                                    if offer_id not in seen_ids:
                                        seen_ids.add(offer_id)
                                        
                                        if is_relevant(title):
                                            embed = discord.Embed(
                                                title=f"🚀 Nouveau VIE : {title}",
                                                url=link,
                                                color=0x00ff00,
                                                timestamp=datetime.utcnow()
                                            )
                                            embed.set_footer(text="Business France • Alerte Automatique")
                                            await channel.send(embed=embed)
                                except Exception as e:
                                    continue
                        else:
                            print(f"Erreur Business France : {resp.status}")
            except Exception as e:
                print(f"Erreur de connexion : {e}")

            await asyncio.sleep(600) # Vérifie toutes les 10 minutes

# Lancement
intents = discord.Intents.default()
intents.message_content = True
client = VIEBot(intents=intents)
client.run(DISCORD_TOKEN)
