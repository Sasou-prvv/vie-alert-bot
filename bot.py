import asyncio
import os
import re
import aiohttp
import discord
from datetime import datetime

# --- CONFIGURATION ---
DISCORD_TOKEN = os.environ.get("DISCORD_TOKEN")
CHANNEL_ID_RAW = os.environ.get("CHANNEL_ID")
CHECK_INTERVAL_SECONDS = int(os.environ.get("CHECK_INTERVAL_SECONDS", "300"))
SEND_STARTUP_TEST = os.environ.get("SEND_STARTUP_TEST", "1") == "1"

if not DISCORD_TOKEN:
    raise RuntimeError("DISCORD_TOKEN manquant dans les variables d'environnement.")
if not CHANNEL_ID_RAW or not CHANNEL_ID_RAW.isdigit():
    raise RuntimeError("CHANNEL_ID invalide ou manquant.")

CHANNEL_ID = int(CHANNEL_ID_RAW)
SEEN_IDS: set[str] = set()

# L'API est beaucoup plus fiable que le scraping HTML
API_URL = "https://mon-vie-via.businessfrance.fr/api/offres/recherche"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Referer": "https://mon-vie-via.businessfrance.fr/offres/recherche",
}

# --- OUTILS ---

def format_date(date_str: str | None) -> str:
    if not date_str: return "N/C"
    try:
        # Nettoyage pour les formats ISO (ex: 2026-06-01T00:00:00)
        clean_date = date_str.split('T')[0]
        dt = datetime.strptime(clean_date, "%Y-%m-%d")
        return dt.strftime("%d/%m/%Y")
    except:
        return date_str

def build_embed(offer: dict, is_test: bool = False) -> discord.Embed:
    # Récupération des données avec fallbacks
    oid = str(offer.get("id") or offer.get("identifiant") or "0")
    title = offer.get("intitule") or "Offre V.I.E"
    company = offer.get("organisationName") or offer.get("entreprise", {}).get("nom") or "Entreprise non précisée"
    
    # Localisation
    city = offer.get("ville") or ""
    country = offer.get("pays") or ""
    
    # Détails mission
    duration = str(offer.get("dureeMois") or offer.get("duree") or "N/C")
    salary_val = offer.get("indemnite") or offer.get("remuneration")
    salary = f"{salary_val} €" if salary_val else "Selon barème"
    
    start = format_date(offer.get("dateDebut"))
    
    url = f"https://mon-vie-via.businessfrance.fr/offres/{oid}"
    prefix = "🧪 TEST — " if is_test else "🚀 NOUVELLE OFFRE — "

    embed = discord.Embed(
        title=f"{prefix}{title}"[:256],
        url=url,
        color=discord.Color.blue() if not is_test else discord.Color.green(),
        timestamp=datetime.now()
    )

    # Mise en page style "CAST"
    embed.add_field(name="🏢 Entreprise", value=f"**{company}**", inline=False)
    embed.add_field(name="📅 Durée (mois)", value=duration, inline=True)
    embed.add_field(name="🏙️ Ville", value=city if city else "N/C", inline=True)
    embed.add_field(name="🌍 Pays", value=country if country else "N/C", inline=True)
    embed.add_field(name="💰 Salaire", value=salary, inline=True)
    embed.add_field(name="🚀 Début", value=start, inline=True)

    embed.add_field(
        name="🔗 Lien Direct", 
        value=f"[Voir l'offre sur Business France]({url})", 
        inline=False
    )
    
    embed.set_footer(text="FR Alerte VIE • Business France")
    return embed

# --- LOGIQUE DU BOT ---

async def fetch_offers(session: aiohttp.ClientSession) -> list[dict]:
    params = {"page": 1, "nbResultats": 50, "tri": "date"}
    async with session.get(API_URL, headers=HEADERS, params=params, timeout=20) as resp:
        if resp.status != 200:
            print(f"⚠️ Erreur API: HTTP {resp.status}")
            return []
        data = await resp.json()
        # L'API renvoie souvent les offres dans une clé 'offres'
        return data.get("offres", [])

class VIEBot(discord.Client):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.startup_test_sent = False

    async def on_ready(self):
        print(f"✅ Bot connecté en tant que {self.user}")
        self.loop.create_task(self.check_loop())

    async def check_loop(self):
        await self.wait_until_ready()
        channel = self.get_channel(CHANNEL_ID) or await self.fetch_channel(CHANNEL_ID)

        async with aiohttp.ClientSession() as session:
            while not self.is_closed():
                try:
                    print("🔍 Scan de l'API...")
                    offers = await fetch_offers(session)
                    
                    if not offers:
                        print("ℹ️ Aucune offre reçue (API vide ou erreur).")
                    else:
                        found_ids = {str(o.get("id")): o for o in offers if o.get("id")}
                        
                        if not SEEN_IDS:
                            # Premier démarrage
                            if found_ids and SEND_STARTUP_TEST and not self.startup_test_sent:
                                first_id = list(found_ids.keys())[0]
                                await channel.send(embed=build_embed(found_ids[first_id], is_test=True))
                                self.startup_test_sent = True
                            
                            SEEN_IDS.update(found_ids.keys())
                            print(f"📦 Initialisation terminée : {len(SEEN_IDS)} offres mémorisées.")
                        else:
                            # Nouveautés
                            for oid, offer_data in found_ids.items():
                                if oid not in SEEN_IDS:
                                    print(f"✨ Nouvelle offre : {oid}")
                                    SEEN_IDS.add(oid)
                                    await channel.send(embed=build_embed(offer_data))
                                    await asyncio.sleep(2)

                except Exception as e:
                    print(f"❌ Erreur dans la boucle : {e}")

                await asyncio.sleep(CHECK_INTERVAL_SECONDS)

# --- EXECUTION ---
intents = discord.Intents.default()
client = VIEBot(intents=intents)
client.run(DISCORD_TOKEN)
