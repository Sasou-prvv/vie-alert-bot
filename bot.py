import asyncio
import os
import re
from urllib.parse import quote_plus

import aiohttp
import discord

DISCORD_TOKEN = os.environ.get("DISCORD_TOKEN")
CHANNEL_ID_RAW = os.environ.get("CHANNEL_ID")

if not DISCORD_TOKEN:
    raise RuntimeError("DISCORD_TOKEN est manquant dans les variables d'environnement.")

if not CHANNEL_ID_RAW or not CHANNEL_ID_RAW.isdigit():
    raise RuntimeError("CHANNEL_ID est manquant ou invalide (doit être un entier).")

CHANNEL_ID = int(CHANNEL_ID_RAW)
CHECK_INTERVAL_SECONDS = int(os.environ.get("CHECK_INTERVAL_SECONDS", "600"))
REQUEST_TIMEOUT_SECONDS = int(os.environ.get("REQUEST_TIMEOUT_SECONDS", "30"))

SEEN_IDS: set[str] = set()

SEARCH_URL = "https://mon-vie-via.businessfrance.fr/offres/recherche"
SOURCE_URLS = [
    SEARCH_URL,
    f"https://api.allorigins.win/get?url={quote_plus(SEARCH_URL)}",
    f"https://r.jina.ai/http://{SEARCH_URL.replace('https://', '')}",
]


class VIEBot(discord.Client):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.first_run = True

    async def on_ready(self):
        print(f"Bot connecté : {self.user}")
        self.loop.create_task(self.check_vie())

    async def _fetch_html(self, session: aiohttp.ClientSession, url: str) -> str:
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
            )
        }
        timeout = aiohttp.ClientTimeout(total=REQUEST_TIMEOUT_SECONDS)
        async with session.get(url, headers=headers, timeout=timeout) as resp:
            body = await resp.text()
            if resp.status != 200:
                raise RuntimeError(f"HTTP {resp.status} depuis {url} | body[:180]={body[:180]!r}")

            if "api.allorigins.win" in url:
                try:
                    payload = await resp.json(content_type=None)
                except Exception as exc:
                    raise RuntimeError(f"Réponse non JSON depuis allorigins: {body[:180]!r}") from exc
                return payload.get("contents", "")

            return body

    async def _extract_offer_ids(self, session: aiohttp.ClientSession) -> list[str]:
        last_error = None

        for source in SOURCE_URLS:
            try:
                html = await self._fetch_html(session, source)
                found_ids = list(dict.fromkeys(re.findall(r"/offres/(\d+)", html)))
                if found_ids:
                    print(f"Source OK: {source} | {len(found_ids)} offres détectées")
                    return found_ids

                print(f"Source sans IDs: {source}")
            except Exception as exc:
                last_error = exc
                print(f"Source KO: {source} -> {exc}")

        raise RuntimeError(f"Aucune source exploitable. Dernière erreur: {last_error}")

    async def check_vie(self):
        await self.wait_until_ready()

        channel = self.get_channel(CHANNEL_ID)
        if channel is None:
            try:
                channel = await self.fetch_channel(CHANNEL_ID)
            except Exception as exc:
                raise RuntimeError(f"Impossible de récupérer le channel Discord {CHANNEL_ID}") from exc

        async with aiohttp.ClientSession() as session:
            while not self.is_closed():
                try:
                    print("Vérification des offres...")
                    found_ids = await self._extract_offer_ids(session)

                    if self.first_run:
                        to_send = found_ids[:3]
                        self.first_run = False
                        print("MODE TEST: envoi des 3 premières offres trouvées.")
                    else:
                        to_send = [oid for oid in found_ids if oid not in SEEN_IDS]

                    for oid in to_send:
                        if oid in SEEN_IDS:
                            continue

                        SEEN_IDS.add(oid)
                        link = f"https://mon-vie-via.businessfrance.fr/offres/{oid}"
                        await channel.send(f"✅ **Offre trouvée !**\n{link}")
                        await asyncio.sleep(1)

                    if not to_send and not self.first_run:
                        print("Rien de nouveau pour l'instant.")

                except Exception as exc:
                    print(f"Erreur technique: {exc}")

                await asyncio.sleep(CHECK_INTERVAL_SECONDS)


intents = discord.Intents.default()
client = VIEBot(intents=intents)
client.run(DISCORD_TOKEN)
