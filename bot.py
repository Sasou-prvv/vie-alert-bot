Voici le nouveau import asyncio
import os
import re
import aiohttp
import discord
from datetime import datetime

DISCORD_TOKEN = os.environ.get("DISCORD_TOKEN")
CHANNEL_ID_RAW = os.environ.get("CHANNEL_ID")
SEND_STARTUP_TEST = os.environ.get("SEND_STARTUP_TEST", "1") == "1"
CHECK_INTERVAL_SECONDS = int(os.environ.get("CHECK_INTERVAL_SECONDS", "300"))

if not DISCORD_TOKEN:
    raise RuntimeError("DISCORD_TOKEN manquant.")
if not CHANNEL_ID_RAW or not CHANNEL_ID_RAW.isdigit():
    raise RuntimeError("CHANNEL_ID invalide.")

CHANNEL_ID = int(CHANNEL_ID_RAW)
SEEN_IDS: set[str] = set()

API_URL = "https://mon-vie-via.businessfrance.fr/api/offres/recherche"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/126.0.0.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "fr-FR,fr;q=0.9",
    "Referer": "https://mon-vie-via.businessfrance.fr/offres/recherche",
    "Origin": "https://mon-vie-via.businessfrance.fr",
}

async def fetch_offers(session: aiohttp.ClientSession) -> list[dict]:
    params = {
        "page": 1,
        "nbResultats": 50,
        "tri": "date",
    }
    async with session.get(API_URL, headers=HEADERS, params=params, timeout=aiohttp.ClientTimeout(total=30)) as resp:
        if resp.status != 200:
            raise RuntimeError(f"API HTTP {resp.status}")
        data = await resp.json(content_type=None)
        return data.get("offres", data.get("results", data.get("data", [])))

def format_date(date_str: str | None) -> str:
    if not date_str:
        return ""
    try:
        for fmt in ["%Y-%m-%dT%H:%M:%S", "%Y-%m-%d", "%d/%m/%Y"]:
            try:
                return datetime.strptime(date_str[:10], fmt[:8]).strftime("%d/%m/%Y")
            except:
                continue
        return date_str[:10]
    except:
        return date_str or ""

def build_embed(offer: dict, is_test: bool = False) -> discord.Embed:
    offer_id = str(offer.get("id", offer.get("identifiant", offer.get("reference", "?"))))
    
    title = (
        offer.get("intitule") or
        offer.get("title") or
        offer.get("poste") or
        offer.get("titre") or
        f"Offre VIE #{offer_id}"
    )
    
    company = (
        offer.get("entreprise", {}).get("nom") if isinstance(offer.get("entreprise"), dict)
        else offer.get("entreprise") or offer.get("nomEntreprise") or offer.get("company") or ""
    )
    
    location_obj = offer.get("localisation") or offer.get("lieu") or {}
    if isinstance(location_obj, dict):
        city = location_obj.get("ville") or location_obj.get("city") or location_obj.get("localite") or ""
        country = location_obj.get("pays") or location_obj.get("country") or ""
    else:
        city = str(location_obj)
        country = offer.get("pays") or ""
    
    city = city or offer.get("ville") or offer.get("city") or ""
    country = country or offer.get("pays") or offer.get("country") or ""
    
    duration = str(
        offer.get("duree") or offer.get("duration") or
        offer.get("dureeMission") or ""
    )
    
    salary_raw = (
        offer.get("remuneration") or offer.get("salaire") or
        offer.get("indemnite") or offer.get("salary") or ""
    )
    salary = f"{salary_raw} €" if salary_raw and "€" not in str(salary_raw) else str(salary_raw)
    
    start = format_date(
        offer.get("dateDebut") or offer.get("startDate") or offer.get("debut") or ""
    )
    end = format_date(
        offer.get("dateFin") or offer.get("endDate") or offer.get("fin") or
        offer.get("dateCloture") or ""
    )
    
    url = f"https://mon-vie-via.businessfrance.fr/offres/{offer_id}"
    
    prefix = "🧪 TEST — " if is_test else ""
    embed = discord.Embed(
        title=f"{prefix}{title}"[:256],
        url=url,
        color=discord.Color.blue(),
    )
    
    if company:
        embed.add_field(name="🏢 Entreprise", value=company[:100], inline=True)
    if duration:
        embed.add_field(name="📅 Durée (mois)", value=duration[:50], inline=True)
    if city:
        embed.add_field(name="🏙️ Ville", value=city[:100], inline=True)
    if country:
        embed.add_field(name="🌍 Pays", value=country[:100], inline=True)
    if salary and salary.strip() and salary.strip() != "€":
        embed.add_field(name="💰 Salaire", value=salary[:100], inline=True)
    if start:
        embed.add_field(name="🚀 Début", value=start, inline=True)
    if end:
        embed.add_field(name="🏁 Fin", value=end, inline=True)
    
    embed.add_field(
        name="🔗 Lien",
        value=f"[Voir l'offre sur Business France]({url})",
        inline=False,
    )
    embed.set_footer(text="FR Alerte VIE • Business France")
    return embed

class VIEBot(discord.Client):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.startup_test_sent = False

    async def on_ready(self):
        print(f"Bot connecté : {self.user}")
        self.loop.create_task(self.check_vie())

    async def check_vie(self):
        await self.wait_until_ready()
        channel = self.get_channel(CHANNEL_ID) or await self.fetch_channel(CHANNEL_ID)

        async with aiohttp.ClientSession() as session:
            while not self.is_closed():
                try:
                    print("Vérification des offres...")
                    offers = await fetch_offers(session)
                    print(f"  {len(offers)} offres récupérées")

                    if offers:
                        # Log la structure de la première offre pour debug
                        print(f"  Clés offre: {list(offers[0].keys())[:15]}")

                    found_ids = []
                    for o in offers:
                        oid = str(o.get("id") or o.get("identifiant") or o.get("reference") or "")
                        if oid:
                            found_ids.append((oid, o))

                    if not SEEN_IDS:
                        if found_ids and SEND_STARTUP_TEST and not self.startup_test_sent:
                            oid, offer = found_ids[0]
                            await channel.send(embed=build_embed(offer, is_test=True))
                            self.startup_test_sent = True
                            await asyncio.sleep(1)
                        for oid, _ in found_ids:
                            SEEN_IDS.add(oid)
                        print(f"Initialisation: {len(found_ids)} offres mémorisées.")
                    else:
                        new = [(oid, o) for oid, o in found_ids if oid not in SEEN_IDS]
                        for oid, offer in new:
                            SEEN_IDS.add(oid)
                            await channel.send(embed=build_embed(offer))
                            await asyncio.sleep(1)
                        if not new:
                            print("Rien de nouveau.")

                except Exception as exc:
                    print(f"Erreur: {exc}")

                await asyncio.sleep(CHECK_INTERVAL_SECONDS)

intents = discord.Intents.default()
client = VIEBot(intents=intents)
client.run(DISCORD_TOKEN)
