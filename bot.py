import discord
import asyncio
import aiohttp
import os
import re
from datetime import datetime
from bs4 import BeautifulSoup

DISCORD_TOKEN = os.environ.get("DISCORD_TOKEN")
CHANNEL_ID = int(os.environ.get("CHANNEL_ID"))

seen_ids = set()

class VIEBot(discord.Client):
    async def on_ready(self):
        print(f"Bot connecté : {self.user}")
        # On lance la boucle de vérification
        self.loop.create_task(self.check_vie())

    async def check_vie(self):
        await self.wait_until_ready()
        channel = self.get_channel(CHANNEL_ID)
        
        if not channel:
            print("Erreur : Salon Discord introuvable. Vérifiez CHANNEL_ID.")
            return

        while not self.is_closed():
            try:
                # URL de la liste des offres (version plus simple à lire)
                url = "https://mon-vie-via.businessfrance.fr/offres/recherche"
                
                headers = {
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8"
                }

                async with aiohttp.ClientSession(headers=headers) as session:
                    async with session.get(url, timeout=30) as resp:
                        if resp.status != 200:
                            print(f"Erreur site : Status {resp.status}")
                            await asyncio.sleep(60)
                            continue

                        html = await resp.text()
                        soup = BeautifulSoup(html, 'html.parser')
                        
                        # On cherche les cartes d'offres (balises <a> avec l'ID de l'offre)
                        # Note: Civiweb change souvent ses classes, on cible les liens d'offres
                        offer_links = soup.find_all('a', href=re.compile(r'/offres/\d+'))
                        
                        found_count = 0
                        for link in offer_links:
                            href = link['href']
                            offer_id = re.search(r'/offres/(\d+)', href).group(1)
                            
                            if offer_id in seen_ids:
                                continue
                            
                            # On récupère le titre (souvent dans un <h4> ou un <span> à l'intérieur)
                            title = link.get_text(separator=" ", strip=True)
                            if not title: title = "Nouvelle mission VIE"

                            seen_ids.add(offer_id)
                            found_count += 1
                            
                            full_link = f"https://mon-vie-via.businessfrance.fr{href}"

                            embed = discord.Embed(
                                title=title[:250], # Limite Discord
                                url=full_link,
                                color=0x004494, # Bleu Business France
                                timestamp=datetime.utcnow()
                            )
                            embed.set_footer(text=f"ID: {offer_id} | Business France")

                            await channel.send(embed=embed)
                        
                        print(f"Scan terminé : {found_count} nouvelles offres ajoutées au set.")

            except Exception as e:
                print(f"Erreur pendant le check : {e}")

            # Attendre 10 minutes avant le prochain scan (évite le bannissement d'IP)
            await asyncio.sleep(600)

intents = discord.Intents.default()
client = VIEBot(intents=intents)
client.run(DISCORD_TOKEN)
