import discord
import asyncio
import aiohttp
import os
from datetime import datetime

DISCORD_TOKEN = os.environ.get("DISCORD_TOKEN")
CHANNEL_ID = int(os.environ.get("CHANNEL_ID"))

seen_ids = set()

class VIEBot(discord.Client):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.first_run = True # Pour forcer l'envoi au démarrage

    async def on_ready(self):
        print(f"Bot connecté : {self.user}")
        self.loop.create_task(self.check_vie())

    async def check_vie(self):
        await self.wait_until_ready()
        channel = self.get_channel(CHANNEL_ID)
        
        api_url = "https://mon-vie-via.businessfrance.fr/api/v1/offres/search"
        
        payload = {
            "page": 1,
            "limit": 10,
            "sort": "date_publication",
            "direction": "desc"
        }

        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Content-Type": "application/json"
        }

        while not self.is_closed():
            try:
                print("Vérification des offres...")
                async with aiohttp.ClientSession(headers=headers) as session:
                    async with session.post(api_url, json=payload, timeout=30) as resp:
                        if resp.status == 200:
                            data = await resp.json()
                            offers = data.get('results', [])
                            
                            # Au premier run, on ne prend que les 3 dernières pour tester
                            if self.first_run:
                                offers_to_process = offers[:3]
                                self.first_run = False
                                print("Mode TEST : Envoi des 3 dernières offres.")
                            else:
                                offers_to_process = offers

                            found_count = 0
                            for offer in offers_to_process:
                                offer_id = str(offer.get('id', ''))
                                
                                if not offer_id or offer_id in seen_ids:
                                    continue
                                
                                seen_ids.add(offer_id)
                                found_count += 1
                                
                                title = offer.get('intitule', 'Sans titre')
                                country = offer.get('pays', {}).get('libelle', 'Monde')
                                company = offer.get('organisation', {}).get('nom', 'Entreprise confidentielle')
                                
                                link = f"https://mon-vie-via.businessfrance.fr/offres/{offer_id}"

                                embed = discord.Embed(
                                    title=f"🚀 {country} | {title}",
                                    url=link,
                                    color=0x004494,
                                    timestamp=datetime.utcnow()
                                )
                                embed.add_field(name="Entreprise", value=company, inline=True)
                                embed.set_footer(text=f"ID: {offer_id}")
                                
                                await channel.send(embed=embed)
                                await asyncio.sleep(1) # Petit délai pour éviter le spam

                            print(f"Fin du scan. {found_count} nouvelles offres traitées.")
                        else:
                            print(f"Erreur API Business France : {resp.status}")

            except Exception as e:
                print(f"Erreur bot : {e}")

            # On vérifie toutes les 10 minutes
            await asyncio.sleep(600)

intents = discord.Intents.default()
client = VIEBot(intents=intents)
client.run(DISCORD_TOKEN)
