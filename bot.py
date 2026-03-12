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

                url = "https://www.civiweb.com/FR/offres.aspx"

                headers = {
                    "User-Agent": "Mozilla/5.0"
                }

                async with aiohttp.ClientSession(headers=headers) as session:
                    async with session.get(url) as resp:

                        html = await resp.text()

                        import re

                        offers = re.findall(
                            r'Offre.aspx\?idOffre=(\d+).*?>(.*?)<',
                            html,
                            re.S
                        )

                        print(f"{len(offers)} offres trouvées")

                        for offer_id, title in offers:

                            if offer_id in seen_ids:
                                continue

                            seen_ids.add(offer_id)

                            link = f"https://www.civiweb.com/FR/offre.aspx?idOffre={offer_id}"

                            embed = discord.Embed(
                                title=title.strip(),
                                url=link,
                                color=0x00ff00,
                                timestamp=datetime.utcnow()
                            )

                            embed.set_footer(text="Nouvelle mission VIE/VIA")

                            await channel.send(embed=embed)

            except Exception as e:
                print("Erreur :", e)

            await asyncio.sleep(300)


intents = discord.Intents.default()

client = VIEBot(intents=intents)

client.run(DISCORD_TOKEN)
