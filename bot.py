import discord
import asyncio
import aiohttp
import os
from datetime import datetime

DISCORD_TOKEN = os.environ.get("DISCORD_TOKEN")
CHANNEL_ID = int(os.environ.get("CHANNEL_ID"))

seen_ids = set()

class VIEBot(discord.Client):

    async def on_ready(self):
        print(f"Bot connecté : {self.user}")
        self.loop.create_task(self.check_vie())

    async def check_vie(self):

        await self.wait_until_ready()
        channel = self.get_channel(CHANNEL_ID)

        while not self.is_closed():

            try:

                url = "https://mon-vie-via.businessfrance.fr/api/offers"

                async with aiohttp.ClientSession() as session:
                    async with session.get(url) as resp:

                        data = await resp.json()
                        offers = data.get("offers", [])

                        print(f"{len(offers)} offres trouvées")

                        for offer in offers:

                            offer_id = str(offer["id"])

                            if offer_id in seen_ids:
                                continue

                            seen_ids.add(offer_id)

                            title = offer.get("title", "Offre VIE")
                            country = offer.get("country", "Non précisé")
                            company = offer.get("company", "Entreprise inconnue")

                            link = f"https://mon-vie-via.businessfrance.fr/offres/{offer_id}"

                            embed = discord.Embed(
                                title=title,
                                url=link,
                                color=0x00ff00,
                                timestamp=datetime.utcnow()
                            )

                            embed.add_field(name="Entreprise", value=company, inline=True)
                            embed.add_field(name="Pays", value=country, inline=True)

                            embed.set_footer(text="Nouvelle offre VIE")

                            await channel.send(embed=embed)

            except Exception as e:
                print("Erreur :", e)

            await asyncio.sleep(600)


intents = discord.Intents.default()

client = VIEBot(intents=intents)

client.run(DISCORD_TOKEN)
