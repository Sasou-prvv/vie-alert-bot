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
        
        # On passe par un service de rendu pour contourner le blocage IP
        # Ce service va lire la page pour nous et nous renvoyer le texte
        proxy_url = "https://api.allorigins.win/get?url="
        target_url = "https://mon-vie-via.businessfrance.fr/offres/recherche"
        
        while not self.is_closed():
            try:
                print("Tentative via bypass Gateway...")
                async with aiohttp.ClientSession() as session:
                    # On encode l'URL cible pour passer par le proxy
                    encoded_url = f"{proxy_url}{target_url}?t={int(datetime.now().timestamp())}"
                    
                    async with session.get(encoded_url, timeout=30) as resp:
                        if resp.status == 200:
                            data = await resp.json()
                            html = data.get('contents', '')
                            
                            # Extraction des IDs d'offres
                            found_ids = list(dict.fromkeys(re.findall(r'/offres/(\d+)', html)))
                            
                            print(f"Brut : {len(found_ids)} IDs détectés.")

                            if self.first_run:
                                # On force l'envoi des 3 premières pour confirmer que ça marche
                                to_send = found_ids[:3]
                                self.first_run = False
                                print("MODE TEST : Envoi des premières offres trouvées.")
                            else:
                                to_send = [oid for oid in found_ids if oid not in seen_ids]

                            for oid in to_send:
                                if oid in seen_ids: continue
                                seen_ids.add(oid)
                                
                                link = f"https://mon-vie-via.businessfrance.fr/offres/{oid}"
                                await channel.send(f"✅ **Offre trouvée !**\n{link}")
                                await asyncio.sleep(1)
                            
                            if not to_send and not self.first_run:
                                print("Rien de nouveau pour l'instant.")
                        else:
                            print(f"Le proxy a répondu avec l'erreur : {resp.status}")

            except Exception as e:
                print(f"Erreur technique : {e}")

            # Attente de 10 minutes
            await asyncio.sleep(600)

intents = discord.Intents.default()
client = VIEBot(intents=intents)
client.run(DISCORD_TOKEN)
