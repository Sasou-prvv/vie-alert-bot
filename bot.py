import discord
import asyncio
import aiohttp
import os
import re
from datetime import datetime

DISCORD_TOKEN = os.environ.get("DISCORD_TOKEN")
CHANNEL_ID = int(os.environ.get("CHANNEL_ID"))

seen_ids = set()

class VIEBot(discord.Client):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.first_run = True

    async def on_ready(self):
        print(f"Bot connecté : {self.user}")
        self.loop.create_task(self.check_vie())

    async def check_vie(self):
        await self.wait_until_ready()
        channel = self.get_channel(CHANNEL_ID)
        
        # URL de secours qui fonctionne sans login et sans requêtes complexes
        url = "https://mon-vie-via.businessfrance.fr/offres/recherche?query="
        
        headers = {
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
            "Accept-Language": "fr-FR,fr;q=0.9,en-US;q=0.8,en;q=0.7"
        }

        while not self.is_closed():
            try:
                print("Tentative de récupération des offres...")
                async with aiohttp.ClientSession(headers=headers) as session:
                    async with session.get(url, timeout=30) as resp:
                        if resp.status == 200:
                            html = await resp.text()
                            
                            # On cherche les IDs d'offres directement dans le texte HTML
                            # Le format dans le code source est souvent href="/offres/123456"
                            found_ids = re.findall(r'/offres/(\d+)', html)
                            
                            # On garde les IDs uniques
                            unique_ids = []
                            for oid in found_ids:
                                if oid not in unique_ids:
                                    unique_ids.append(oid)

                            if self.first_run:
                                ids_to_send = unique_ids[:5] # On en envoie 5 pour tester
                                self.first_run = False
                                print(f"Mode TEST : {len(ids_to_send)} offres trouvées.")
                            else:
                                ids_to_send = [oid for oid in unique_ids if oid not in seen_ids]

                            for oid in ids_to_send:
                                if oid in seen_ids: continue
                                
                                seen_ids.add(oid)
                                link = f"https://mon-vie-via.businessfrance.fr/offres/{oid}"
                                
                                # Message simple pour être sûr que ça passe
                                await channel.send(f"📢 **Nouvelle offre VIE trouvée !**\nLien : {link}")
                                await asyncio.sleep(2)

                            print(f"Scan fini. IDs en mémoire : {len(seen_ids)}")
                        else:
                            print(f"Erreur site : {resp.status}")

            except Exception as e:
                print(f"Erreur : {e}")

            await asyncio.sleep(600)

intents = discord.Intents.default()
client = VIEBot(intents=intents)
client.run(DISCORD_TOKEN)
