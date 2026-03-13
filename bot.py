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

# Headers pour simuler un navigateur et éviter les blocages
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
    
    # Récupération propre de l'indemnité (ex: 5046€ pour CAST)
    salary_val = offer.get("indemnite") or offer.get("remuneration")
    salary = f"{int(float(salary_val))} €" if salary_val else "N/C"
    
    date_start = format_date(offer.get("dateDebut"))
    url = f"https://mon-vie-via.businessfrance.fr/offres/{oid}"
    
    embed = discord.Embed(title=title[:256], url=url, color=0x2b2d31)
    
    # Mise en page fidèle au modèle CAST
    embed.add_field(name="🏢 Entreprise", value=f"**{company}**", inline=True)
    embed.add_field(name="📅 Durée (mois)", value=str(duration), inline=True)
    embed.add_field(name="🏙️ Ville", value=city, inline=True)
    embed.add_field(name="🌍 Pays", value=country, inline=True)
    embed.add_field(name="💰 Salaire", value=salary, inline=True)
    embed.add_field(name="🚀 Début", value=date_start, inline=True)
    
    embed.add_field(name="🔗 Lien", value=f"[Postuler sur Business France]({url})", inline=False)
    embed.set_footer(text=f"Alerte VIE • Business France • {datetime.now().strftime('%H:%M')}")
    return embed

async def fetch_offers(session):
    # Utilisation de POST pour éviter l'erreur HTTP 500
    payload = {"page": 1, "nbResultats": 50, "tri": "date"}
    url = "https://mon-vie-via.businessfrance.fr/api/offres/recherche"
    async with session.post(url, json=payload, headers=HEADERS) as resp:
        if resp.status == 200:
            data = await resp.json()
            return data.get("offres", [])
        print(f"⚠️ Erreur API {resp.status}")
        return []

class VIEBot(discord.Client):
    async def on_ready(self):
        print(f"✅ Connecté : {self.user}")
        self.loop.create_task(self.check_loop())

    async def check_loop(self):
        await self.wait_until_ready()
        channel = self.get_channel(CHANNEL_ID) or await self.fetch_channel(CHANNEL_ID)
        
        async with aiohttp.ClientSession() as session:
            while not self.is_closed():
                print("🔍 Scan API...")
                try:
                    offers = await fetch_offers(session)
                    print(f"📊 {len(offers)} offres récupérées.")
                    
                    if not SEEN_IDS:
                        # Test immédiat au démarrage avec la dernière offre
                        if offers:
                            print("🧪 Envoi de l'offre de test...")
                            await channel.send(content="✅ **Bot Opérationnel** - Dernière offre trouvée :", embed=build_embed(offers[0]))
                        for o in offers:
                            SEEN_IDS.add(str(o.get("id")))
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

client = VIEBot(intents=discord.Intents.default())
client.run(DISCORD_TOKEN)
