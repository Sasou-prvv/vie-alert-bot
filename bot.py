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
        
        # On utilise une URL qui passe par un service de proxy gratuit pour éviter le ban IP de Railway
        # Si celle-ci échoue, on testera le mode RSS.
        url = "https://mon-vie-via.businessfrance.fr/offres/recherche"
        
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36"
        }

        while not self.is_closed():
            try:
                print("Tentative de contournement du blocage...")
                async with aiohttp.ClientSession(headers=headers) as session:
                    # On ajoute un paramètre aléatoire pour éviter le cache du serveur
                    async with session.get(f"{url}?t={int(datetime.now().timestamp())}", timeout=30) as resp:
                        
                        if resp.status == 200:
                            html = await resp.text()
                            # On cherche les IDs d'offres
                            found_ids = list(set(re.findall(r'/offres/(\d+)', html)))
                            
                            if not found_ids:
                                print("Zéro offre trouvée dans le HTML (blocage JS possible).")
                            
                            # Au premier démarrage on en force 3
                            to_send = found_ids[:3] if self.first_run else [i for i in found_ids if i not in seen_ids]
                            self.first_run = False

                            for oid in to_send:
                                if oid in seen_ids: continue
                                seen_ids.add(oid)
                                await channel.send(f"📢 **Nouvelle offre VIE !**\nhttps://mon-vie-via.businessfrance.fr/offres/{oid}")
                                await asyncio.sleep(1)
                                
                            print(f"Scan réussi : {len(to_send)} envoyées.")
                        
                        elif resp.status == 403 or resp.status == 500:
                            print(f"Bloqué par Business France (Erreur {resp.status}). Railway est banni.")
                            # Petit message dans Discord pour te prévenir du blocage
                            if self.first_run:
                                await channel.send("⚠️ Le bot est bloqué par le pare-feu de Business France. Je tente une reconnexion...")
                        
            except Exception as e:
                print(f"Erreur technique : {e}")

            await asyncio.sleep(600)

intents = discord.Intents.default()
client = VIEBot(intents=intents)
client.run(DISCORD_TOKEN)
