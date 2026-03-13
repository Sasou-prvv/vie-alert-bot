import asyncio
import json
import os
import random
import re
from html import unescape
from urllib.parse import quote_plus

import aiohttp
import discord

# --- CONFIGURATION ---
DISCORD_TOKEN = os.environ.get("DISCORD_TOKEN")
CHANNEL_ID_RAW = os.environ.get("CHANNEL_ID")
SCRAPERAPI_KEY = os.environ.get("SCRAPERAPI_KEY")

if not DISCORD_TOKEN:
    raise RuntimeError("DISCORD_TOKEN est manquant dans les variables d'environnement.")

if not CHANNEL_ID_RAW or not CHANNEL_ID_RAW.isdigit():
    raise RuntimeError("CHANNEL_ID est manquant ou invalide (doit être un entier).")

CHANNEL_ID = int(CHANNEL_ID_RAW)
CHECK_INTERVAL_SECONDS = int(os.environ.get("CHECK_INTERVAL_SECONDS", "300"))
REQUEST_TIMEOUT_SECONDS = int(os.environ.get("REQUEST_TIMEOUT_SECONDS", "30")) # Augmenté un peu pour ScraperAPI JS
SEND_STARTUP_TEST = os.environ.get("SEND_STARTUP_TEST", "1") == "1"
SOURCE_FAILURE_COOLDOWN_SECONDS = int(os.environ.get("SOURCE_FAILURE_COOLDOWN_SECONDS", "900"))

SEEN_IDS: set[str] = set()
SOURCE_FAIL_UNTIL: dict[str, float] = {}
SEARCH_URL = "https://mon-vie-via.businessfrance.fr/offres/recherche"

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:127.0) Gecko/20100101 Firefox/127.0",
]

# --- UTILITAIRES ---

def _build_source_urls() -> list[str]:
    cache_buster = str(int(asyncio.get_running_loop().time() * 1000))
    target_url = f"{SEARCH_URL}?_ts={cache_buster}"
    return [
        target_url,
        f"https://api.allorigins.win/raw?url={quote_plus(target_url)}",
        f"https://api.allorigins.win/get?url={quote_plus(target_url)}",
        f"https://r.jina.ai/http://{target_url.replace('https://', '')}",
    ]

def _clean_text(value: str) -> str:
    if not value:
        return ""
    # 1. Détruire les balises <script> et <style> et tout leur contenu (JS/CSS)
    value = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", value, flags=re.IGNORECASE | re.DOTALL)
    # 2. Nettoyer le reste de l'HTML
    value = unescape(value)
    value = re.sub(r"<[^>]+>", " ", value)
    value = re.sub(r"\s+", " ", value)
    return value.strip()

def _find_field(html: str, labels: list[str]) -> str | None:
    for label in labels:
        patterns = [
            rf"{label}\s*[:\-]\s*</?[^>]*>?\s*([^<\n\r]+)",
            rf"{label}\s*[:\-]\s*([^<\n\r]+)",
            rf'"{label}"\s*[:=]\s*"([^"]+)"',
        ]
        for pattern in patterns:
            match = re.search(pattern, html, re.IGNORECASE)
            if match:
                value = _clean_text(match.group(1))
                if value:
                    return value
    return None

def _looks_like_noise(value: str | None) -> bool:
    if not value:
        return True
    text = _clean_text(value)
    lower = text.lower()
    noise_markers = [
        "placeholder", "to find out more about this recruiter", "log in or create",
        "applyoffersimple", "viewnotifications", "interestedin this position",
        "non-contractual compensation", "{", "}", '":"', "function(w,d,s,l,i"
    ]
    if any(marker in lower for marker in noise_markers):
        return True
    if len(text) > 120:
        return True
    return False

def _pick_valid_value(obj: dict, keys: list[str]) -> str | None:
    for key in keys:
        value = obj.get(key)
        if isinstance(value, str):
            cleaned = _clean_text(value)
            if cleaned and not _looks_like_noise(cleaned):
                return cleaned
    return None

def _walk_json_values(node):
    if isinstance(node, dict):
        yield node
        for value in node.values(): yield from _walk_json_values(value)
    elif isinstance(node, list):
        for item in node: yield from _walk_json_values(item)

def _extract_json_ld_data(html: str) -> dict[str, str]:
    info: dict[str, str] = {}
    scripts = re.findall(r'<script[^>]*type="application/ld\+json"[^>]*>(.*?)</script>', html, flags=re.IGNORECASE | re.DOTALL)
    for script in scripts:
        raw = script.strip()
        if not raw: continue
        try:
            parsed = json.loads(raw)
            entries = parsed if isinstance(parsed, list) else [parsed]
            for entry in entries:
                if not isinstance(entry, dict): continue
                title = entry.get("title") or entry.get("name")
                if isinstance(title, str) and title.strip(): info["title"] = title.strip()
                hiring_org = entry.get("hiringOrganization")
                if isinstance(hiring_org, dict):
                    org_name = hiring_org.get("name")
                    if isinstance(org_name, str) and org_name.strip(): info["company"] = org_name.strip()
                base_salary = entry.get("baseSalary")
                if isinstance(base_salary, dict) and isinstance(base_salary.get("value"), dict):
                    salary = base_salary["value"].get("value")
                    unit = base_salary["value"].get("unitText", "")
                    if salary is not None: info["salary"] = f"{salary} {unit}".strip()
                loc = entry.get("jobLocation", [{}])[0] if isinstance(entry.get("jobLocation"), list) else entry.get("jobLocation", {})
                if isinstance(loc, dict) and isinstance(loc.get("address"), dict):
                    addr = loc["address"]
                    composed = " - ".join([v for v in [addr.get("addressLocality"), addr.get("addressCountry")] if isinstance(v, str) and v.strip()])
                    if composed: info["location"] = composed
                valid_through = entry.get("validThrough")
                if isinstance(valid_through, str) and valid_through.strip(): info["deadline"] = valid_through.strip()
        except Exception: continue
    return info

def _extract_next_data(html: str) -> dict[str, str]:
    info: dict[str, str] = {}
    match = re.search(r'<script[^>]*id="__NEXT_DATA__"[^>]*>(.*?)</script>', html, flags=re.IGNORECASE | re.DOTALL)
    if not match: return info
    try:
        data = json.loads(match.group(1).strip())
        for obj in _walk_json_values(data):
            if "title" not in info: info["title"] = _pick_valid_value(obj, ["title", "intitule", "name", "poste", "jobTitle"]) or info.get("title")
            if "company" not in info: info["company"] = _pick_valid_value(obj, ["entreprise", "company", "societe", "organizationName", "nomEntreprise"]) or info.get("company")
            if "location" not in info: info["location"] = _pick_valid_value(obj, ["lieu", "localisation", "ville", "country", "pays", "location"]) or info.get("location")
            if "duration" not in info: info["duration"] = _pick_valid_value(obj, ["duree", "duration", "dureeMission", "missionDuration"]) or info.get("duration")
            if "salary" not in info: info["salary"] = _pick_valid_value(obj, ["salaire", "remuneration", "indemnite", "salary", "compensation"]) or info.get("salary")
            if "start" not in info: info["start"] = _pick_valid_value(obj, ["dateDebut", "startDate", "dateDeDebut", "debutMission"]) or info.get("start")
            if "deadline" not in info: info["deadline"] = _pick_valid_value(obj, ["dateLimite", "dateCloture", "validThrough", "deadline"]) or info.get("deadline")
    except Exception: pass
    return {k: v for k, v in info.items() if v}

def _extract_business_france_fields(html: str) -> dict[str, str]:
    """Extraction robuste basée sur le texte visible de Business France."""
    info: dict[str, str] = {}
    clean_text = _clean_text(html)

    # 1. Pays et Ville (ex: "ETATS-UNIS (NEW-YORK-NY-)")
    mission_match = re.search(r"LA MISSION\s+([A-ZÀ-ÖØ-Þ\-\s']{3,})\s*\(([^\)]+)\)", clean_text)
    if mission_match:
        info["country"] = mission_match.group(1).strip()
        info["city"] = mission_match.group(2).strip()

    # 2. Dates et Durée
    date_range = re.search(r"du\s+([0-9]{1,2}\s+\w+\s+[0-9]{4})\s+au\s+([0-9]{1,2}\s+\w+\s+[0-9]{4})\s*\((\d+\s*mois)\)", clean_text, re.IGNORECASE)
    if date_range:
        info["start"] = date_range.group(1).strip()
        info["deadline"] = date_range.group(2).strip()
        info["duration"] = date_range.group(3).strip()

    # 3. Entreprise (ex: "ETABLISSEMENT : CAST")
    company_match = re.search(r"ETABLISSEMENT\s*[:\-]?\s*([A-Z0-9\s\.\-\&]+)(?=\s+REMUNERATION|\s+Télétravail|\s+du\s)", clean_text, re.IGNORECASE)
    if company_match:
        info["company"] = company_match.group(1).strip()

    # 4. Salaire (ex: "5046.14 €")
    salary_match = re.search(r"REMUNERATION\s+MENSUELLE\s*[:\-]?\s*([0-9\s.,]+\s*€?)", clean_text, re.IGNORECASE)
    if salary_match:
        info["salary"] = salary_match.group(1).strip()
        if "€" not in info["salary"]:
            info["salary"] += " €"

    return {k: v for k, v in info.items() if v and not _looks_like_noise(v)}

def _extract_city_country(location: str | None) -> tuple[str, str]:
    if not location: return "", ""
    cleaned = _clean_text(location)
    for sep in [" - ", ",", "|"]:
        if sep in cleaned:
            parts = [p.strip() for p in cleaned.split(sep) if p.strip()]
            if len(parts) >= 2: return parts[0], parts[-1]
    paren_match = re.search(r"([^\(]+)\(([^\)]+)\)", cleaned)
    if paren_match:
        left, right = _clean_text(paren_match.group(1)), _clean_text(paren_match.group(2))
        if left and right: return right, left
    return cleaned, ""

# --- BOT ---

class VIEBot(discord.Client):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.startup_test_sent = False

    async def on_ready(self):
        print(f"Bot connecté : {self.user}")
        self.loop.create_task(self.check_vie())

    def _build_headers(self) -> dict[str, str]:
        return {
            "User-Agent": random.choice(USER_AGENTS),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "fr-FR,fr;q=0.9,en-US;q=0.8,en;q=0.7",
            "Cache-Control": "no-cache",
            "Pragma": "no-cache",
        }

    def _resolve_fetch_url(self, url: str) -> str:
        # L'ajout de &render=true est CRUCIAL pour forcer ScraperAPI à exécuter le JS du site
        if SCRAPERAPI_KEY and "mon-vie-via.businessfrance.fr" in url and "api.allorigins.win" not in url and "r.jina.ai" not in url:
            return f"https://api.scraperapi.com?api_key={SCRAPERAPI_KEY}&url={quote_plus(url)}&render=true"
        return url

    async def _fetch_html(self, session: aiohttp.ClientSession, url: str) -> str:
        headers = self._build_headers()
        timeout = aiohttp.ClientTimeout(total=REQUEST_TIMEOUT_SECONDS)
        fetch_url = self._resolve_fetch_url(url)

        async with session.get(fetch_url, headers=headers, timeout=timeout) as resp:
            body = await resp.text()
            if resp.status != 200:
                raise RuntimeError(f"HTTP {resp.status} depuis {url}")

            if "api.allorigins.win/get" in url:
                payload = await resp.json(content_type=None)
                return payload.get("contents", "")

            return body

    async def _extract_offer_ids(self, session: aiohttp.ClientSession) -> list[str]:
        last_error = None
        now = asyncio.get_running_loop().time()

        for source in _build_source_urls():
            fail_until = SOURCE_FAIL_UNTIL.get(source, 0)
            if fail_until > now:
                continue

            try:
                html = await self._fetch_html(session, source)
                found_ids = list(dict.fromkeys(re.findall(r"/offres/(\d+)", html)))
                if found_ids:
                    print(f"Source OK: {source[:40]}... | {len(found_ids)} offres")
                    return found_ids
            except Exception as exc:
                last_error = exc
                SOURCE_FAIL_UNTIL[source] = now + SOURCE_FAILURE_COOLDOWN_SECONDS
                print(f"Source KO: {source[:40]} -> {exc}")

        if last_error:
            raise RuntimeError(f"Aucune source exploitable. Dernière erreur: {last_error}")
        return []

    async def _fetch_offer_details(self, session: aiohttp.ClientSession, offer_id: str) -> dict[str, str]:
        offer_url = f"https://mon-vie-via.businessfrance.fr/offres/{offer_id}"
        sources = [offer_url, f"https://api.allorigins.win/get?url={quote_plus(offer_url)}"]

        html = ""
        for source in sources:
            try:
                html = await self._fetch_html(session, source)
                if html: break
            except Exception: pass

        details: dict[str, str] = {"url": offer_url}
        if not html: return details

        # Update depuis les différentes méthodes d'extraction
        details.update(_extract_json_ld_data(html))
        bf_data = _extract_business_france_fields(html)
        for key, value in bf_data.items(): details.setdefault(key, value)
        
        next_data = _extract_next_data(html)
        for key, value in next_data.items(): details.setdefault(key, value)

        # Fallback pour le titre s'il est générique (sécurité contre le JS non chargé)
        if "title" not in details or "My International Volunteer Program" in details.get("title", ""):
            h1_match = re.search(r"<h1[^>]*>(.*?)</h1>", html, re.IGNORECASE | re.DOTALL)
            if h1_match:
                details["title"] = _clean_text(h1_match.group(1))
            else:
                title_match = re.search(r"<title[^>]*>(.*?)</title>", html, re.IGNORECASE | re.DOTALL)
                if title_match:
                    details["title"] = _clean_text(title_match.group(1)).replace("| Mon V.I.E/V.I.A", "").strip()

        # Nettoyage final
        for key in ["title", "company", "location", "duration", "salary", "start", "deadline"]:
            value = details.get(key)
            if isinstance(value, str):
                cleaned = _clean_text(value)
                if not cleaned or _looks_like_noise(cleaned): details.pop(key, None)
                else: details[key] = cleaned

        city, country = _extract_city_country(details.get("location"))
        if city: details.setdefault("city", city)
        if country: details.setdefault("country", country)

        return {k: v for k, v in details.items() if v}

    def _build_offer_embed(self, details: dict[str, str], offer_id: str, is_test: bool = False) -> discord.Embed:
        title = details.get("title", f"Offre VIE #{offer_id}")
        prefix = "🧪 TEST — " if is_test else ""

        embed = discord.Embed(
            title=f"{prefix}{title}"[:256],
            description="Voir l'offre sur Business France",
            color=discord.Color.blue(),
            url=details.get("url", f"https://mon-vie-via.businessfrance.fr/offres/{offer_id}"),
        )

        def add_field(name: str, key: str, inline: bool = True):
            value = details.get(key)
            if value:
                embed.add_field(name=name, value=str(value)[:1024], inline=inline)

        add_field("🏢 Entreprise", "company")
        add_field("📅 Durée (mois)", "duration")
        add_field("🏙️ Ville", "city")
        add_field("🌍 Pays", "country")
        if not details.get("city") and not details.get("country"):
            add_field("📍 Localisation", "location")
        add_field("💰 Salaire", "salary")
        add_field("🚀 Début", "start")
        add_field("🏁 Fin", "deadline")

        embed.add_field(
            name="🔗 Lien",
            value=f"[Voir l'offre sur Business France]({details.get('url', f'https://mon-vie-via.businessfrance.fr/offres/{offer_id}')})",
            inline=False,
        )
        embed.set_footer(text="FR Alerte VIE • Business France")
        return embed

    async def check_vie(self):
        await self.wait_until_ready()
        channel = self.get_channel(CHANNEL_ID) or await self.fetch_channel(CHANNEL_ID)

        async with aiohttp.ClientSession() as session:
            while not self.is_closed():
                try:
                    print("Vérification des offres...")
                    found_ids = await self._extract_offer_ids(session)

                    if not SEEN_IDS:
                        if found_ids and SEND_STARTUP_TEST and not self.startup_test_sent:
                            latest_id = found_ids[0]
                            details = await self._fetch_offer_details(session, latest_id)
                            await channel.send(embed=self._build_offer_embed(details, latest_id, is_test=True))
                            self.startup_test_sent = True
                            await asyncio.sleep(1)

                        SEEN_IDS.update(found_ids)
                        print(f"Initialisation: {len(found_ids)} offres déjà présentes mémorisées.")
                    else:
                        new_ids = [oid for oid in found_ids if oid not in SEEN_IDS]
                        for oid in new_ids:
                            SEEN_IDS.add(oid)
                            details = await self._fetch_offer_details(session, oid)
                            await channel.send(embed=self._build_offer_embed(details, oid))
                            await asyncio.sleep(1)

                        if not new_ids:
                            print("Rien de nouveau pour l'instant.")

                except Exception as exc:
                    print(f"Erreur technique: {exc}")

                await asyncio.sleep(CHECK_INTERVAL_SECONDS)


intents = discord.Intents.default()
client = VIEBot(intents=intents)
client.run(DISCORD_TOKEN)
