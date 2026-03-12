import discord
import asyncio
import aiohttp
import xml.etree.ElementTree as ET
import os
from datetime import datetime

DISCORD_TOKEN = os.environ.get('DISCORD_TOKEN', '') 
CHANNEL_ID = int(os.environ.get('CHANNEL_ID', '0'))

# Mots-clés très larges - tout ce qui touche à l'industrie et plus
KEYWORDS = [
    # Industrie & Production
    "industrie", "industriel", "production", "manufacturing", "usine", "atelier",
    "lean", "amélioration continue", "kaizen", "5s", "six sigma",
    "gestion de production", "planification", "ordonnancement",
    "supply chain", "logistique", "achats", "procurement",
    # Qualité
    "qualité", "qualite", "qse", "hse", "sécurité", "securite",
    "audit", "contrôle", "controle", "inspection", "conformité", "conformite",
    "iso", "certification", "norme",
    # Matériaux & Procédés
    "matériaux", "materiaux", "procédés", "procedes", "métallurgie", "metallurgie",
    "composite", "polymère", "polymere", "céramique", "ceramique",
    "traitement de surface", "peinture", "revêtement", "revetement", "coating",
    "soudage", "usinage", "fonderie", "forge", "moulage", "assemblage",
    # Mécanique & Ingénierie
    "mécanique", "mecanique", "ingénierie", "ingenierie", "conception",
    "bureau d'études", "r&d", "recherche", "développement", "developpement",
    "simulation", "calcul", "dimensionnement", "cad", "cao", "solidworks",
    "catia", "ansys", "abaqus",
    # Aéronautique & Spatial
    "aéronautique", "aeronautique", "aérospatial", "aerospatial", "aviation",
    "avion", "drone", "satellite", "spatial", "moteur", "turbine",
    "safran", "airbus", "boeing", "thales", "dassault", "mbda",
    # Automobile & Mobilité
    "automobile", "automotive", "véhicule", "vehicule", "moteur",
    "électrique", "electrique", "hybride", "batterie",
    # Énergie
    "énergie", "energie", "pétrole", "petrole", "oil", "gas", "gaz",
    "nucléaire", "nucleaire", "solaire", "éolien", "eolien", "renouvelable",
    "électricité", "electricite", "réseau", "reseau", "turbine",
    # Construction & BTP
    "construction", "btp", "bâtiment", "batiment", "génie civil", "genie civil",
    "infrastructure", "travaux", "chantier", "architecture",
    # Data & Digital
    "power bi", "powerbi", "kpi", "tableau de bord", "dashboard",
    "data", "excel", "erp", "sap", "mis", "reporting", "analyse",
    "digital", "digitalisation", "industrie 4.0", "iot", "automatisation",
    # Environnement & Sciences
    "environnement", "rse", "développement durable", "developpement durable",
    "chimie", "biologie", "sciences", "laboratoire", "labo", "r&d",
    "pharmacie", "pharmaceutique", "cosmétique", "cosmetique", "beauté", "beaute",
    "médical", "medical", "paramédical", "paramedical", "santé", "sante",
    # Gestion & Management
    "chef de projet", "project manager", "coordinateur", "coordinatrice",
    "gestion", "management", "responsable", "directeur", "directrice",
    "business development", "commercial", "vente",
    # Public & Parapublic
    "public", "parapublic", "collectivité", "collectivite", "gouvernement",
    "ministère", "ministere", "agence", "institution"
]

# Pays exclus (Afrique + Europe)
EXCLUDED_COUNTRIES = [
    # Europe
    "france", "allemagne", "espagne", "italie", "portugal", "belgique",
    "pays-bas", "suisse", "autriche", "pologne", "roumanie", "hongrie",
    "grèce", "grece", "suède", "suede", "norvège", "norvege", "danemark",
    "finlande", "irlande", "luxembourg", "royaume-uni", "uk", "europe",
    # Afrique
    "afrique", "maroc", "tunisie", "algerie", "algérie", "sénégal", "senegal",
    "cameroun", "côte d'ivoire", "cote d'ivoire", "ghana", "nigeria",
    "kenya", "ethiopie", "madagascar", "mauritanie", "mali", "niger",
    "burkina", "togo", "bénin", "benin", "gabon", "congo", "angola",
    "mozambique", "tanzanie", "ouganda", "rwanda", "zimbabwe", "zambie",
    "egypte", "égypte", "libye", "soudan", "afrique du sud"
]

RSS_URL = "https://www.businessfrance.fr/vie-rss"

seen_ids = set()

def is_relevant(title, description, country=""):
    title_lower = title.lower()
    desc_lower = description.lower()
    country_lower = country.lower()
    
    # Exclure les pays non voulus
    for excl in EXCLUDED_COUNTRIES:
        if excl in country_lower or excl in title_lower:
            return False
    
    # Accepter si un mot-clé correspond
    for kw in KEYWORDS:
        if kw in title_lower or kw in desc_lower:
            return True
    
    return False

class VIEBot(discord.Client):
    async def on_ready(self):
        print(f'Bot connecté : {self.user}')
        self.loop.create_task(self.check_vie())

    async def check_vie(self):
        await self.wait_until_ready()
        channel = self.get_channel(CHANNEL_ID)
        
        while not self.is_closed():
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.get(RSS_URL, timeout=aiohttp.ClientTimeout(total=30)) as resp:
                        if resp.status == 200:
                            text = await resp.text()
                            root = ET.fromstring(text)
                            
                            for item in root.findall('.//item'):
                                title = item.findtext('title', '')
                                link = item.findtext('link', '')
                                desc = item.findtext('description', '')
                                guid = item.findtext('guid', link)
                                
                                if guid not in seen_ids:
                                    seen_ids.add(guid)
                                    
                                    if is_relevant(title, desc):
                                        embed = discord.Embed(
                                            title=f"🚀 Nouveau VIE : {title}",
                                            url=link,
                                            description=desc[:300] + "..." if len(desc) > 300 else desc,
                                            color=0x00ff00,
                                            timestamp=datetime.utcnow()
                                        )
                                        embed.set_footer(text="Business France • VIE Alert Bot")
                                        await channel.send(embed=embed)
            except Exception as e:
                print(f"Erreur: {e}")
            
            await asyncio.sleep(300)  # Vérifie toutes les 5 minutes

intents = discord.Intents.default()
intents.message_content = True
client = VIEBot(intents=intents)
client.run(DISCORD_TOKEN)

