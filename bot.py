import asyncio
import os
import aiohttp
import discord
from datetime import datetime

# --- CONFIGURATION ---
DISCORD_TOKEN = os.environ.get("DISCORD_TOKEN")
CHANNEL_ID = int(os.environ.get("CHANNEL_ID"))
CHECK_INTERVAL = int(os.environ.get("CHECK_INTERVAL_SECONDS", "300"))

SEEN_IDS = set()

# URL de l'API (nécessite un POST)
API_URL = "https://mon-vie-via.businessfrance.fr/api/offres/recherche"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Content-Type": "application/json",
    "Accept": "application/json",
    "Origin": "https://mon-vie-via.businessfrance.fr",
    "Referer": "https://mon-vie-via.businessfrance.fr/offres/recherche"
}

def format_date(date_str):
    if not date_str: return "N/C"
    try:
        # Formate 2026-06-01T00:00:00 -> 01/06/2026
        dt = datetime.fromisoformat(date_str.replace('Z', '+00:00'))
        return dt.strftime("%d/%m/%Y")
    except:
        return date_str

def build_embed(offer):
    oid = str(offer.get("id", "0"))
    title = offer.get("intitule", "Offre V.I.E")
    company = offer.get("organisationName", "N/C")
    city = offer.get("ville", "N/C")
    country = offer.get("pays", "N/C")
    duration = offer.get("dureeMois", "N/C")
    
    # Récupère l'indemnité (5046.14 dans ton PDF)
    salary_val = offer.get("indemnite") or offer.get("remuneration")
    salary = f"{int(float(salary_val))} €" if salary_val else "N/C"
    
    date_start = format_date(offer.get("dateDebut"))
    
    url = f"https://mon-vie-via.businessfrance.fr/offres/{oid}"
    
    embed = discord.Embed(
        title=title[:256],
        url=url,
        color=0x2b2d31 # Couleur sombre pro
    )
    
    # Disposition en colonnes comme sur ta capture CAST
    embed.add_field(name="🏢 Entreprise", value=company, inline=True)
    embed.add_field(name="📅 Durée (mois)", value=duration, inline=True)
    embed.add_field(name="🏙️ Ville", value=city, inline=True)
    embed.add_field(name="🌍 Pays", value=country, inline=True)
    embed.add_field(name="💰 Salaire", value=salary, inline=True)
    embed.add_field(name="🚀 Début", value=date_start, inline=True)
    
    embed.add_field(name="🔗 Lien", value=f"[Voir l'offre sur Business France]({url})", inline=False)
    
    embed.set_footer(text=f"FR Alerte VIE • Business France • Aujourd'hui à {datetime.now().strftime('%H:%M')}")
    return embed

async def fetch_offers(session):
    # Changement CRUCIAL : On utilise un POST avec un body JSON
    payload = {
        "page": 1,
        "nbResultats": 50,
        "tri": "date"
    }
    async with session.post(API_URL, json=payload, headers=HEADERS) as resp:
        if resp.status == 200:
            data = await resp.json()
            return data.get("offres", [])
        else:
            print(f"⚠️ Erreur API {resp.status}")
            return []

class MyBot(discord.Client):
    async def on_ready(self):
        print(f"✅ Connecté : {self.user}")
        self.loop.create_task(self.check_loop())

    async def check_loop(self):
        channel = self.get_channel(CHANNEL_ID)
        async with aiohttp.ClientSession() as session:
            while not self.is_closed():
                print("🔍 Scan API...")
                try:
                    offers = await fetch_offers(session)
                    print(f"📊 {len(offers)} offres trouvées.")
                    
                    if not SEEN_IDS: # Initialisation
                        for o in offers: SEEN_IDS.add(str(o.get("id")))
                        print("Initialisation terminée.")
                    else:
                        for o in offers:
                            oid = str(o.get("id"))
                            if oid not in SEEN_IDS:
                                SEEN_IDS.add(oid)
                                await channel.send(embed=build_embed(o))
                                await asyncio.sleep(1)
                except Exception as e:
                    print(f"❌ Erreur : {e}")
                
                await asyncio.sleep(CHECK_INTERVAL)

client = MyBot(intents=discord.Intents.default())
client.run(DISCORD_TOKEN)
