import asyncio
import json
import os
import re
from html import unescape
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
CHECK_INTERVAL_SECONDS = int(os.environ.get("CHECK_INTERVAL_SECONDS", "60"))
REQUEST_TIMEOUT_SECONDS = int(os.environ.get("REQUEST_TIMEOUT_SECONDS", "30"))

SEEN_IDS: set[str] = set()

SEARCH_URL = "https://mon-vie-via.businessfrance.fr/offres/recherche"


def _build_source_urls() -> list[str]:
    # Ajoute un cache-buster pour éviter les réponses figées sur les proxys miroirs.
    cache_buster = str(int(asyncio.get_running_loop().time() * 1000))
    target_url = f"{SEARCH_URL}?_ts={cache_buster}"
    return [
        target_url,
        f"https://api.allorigins.win/raw?url={quote_plus(target_url)}",
        f"https://api.allorigins.win/get?url={quote_plus(target_url)}",
        f"https://r.jina.ai/http://{target_url.replace('https://', '')}",
    ]


def _clean_text(value: str) -> str:
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


def _walk_json_values(node):
    if isinstance(node, dict):
        yield node
        for value in node.values():
            yield from _walk_json_values(value)
    elif isinstance(node, list):
        for item in node:
            yield from _walk_json_values(item)


def _extract_json_ld_data(html: str) -> dict[str, str]:
    info: dict[str, str] = {}
    scripts = re.findall(
        r'<script[^>]*type="application/ld\+json"[^>]*>(.*?)</script>',
        html,
        flags=re.IGNORECASE | re.DOTALL,
    )

    for script in scripts:
        raw = script.strip()
        if not raw:
            continue
        try:
            parsed = json.loads(raw)
        except Exception:
            continue

        entries = parsed if isinstance(parsed, list) else [parsed]
        for entry in entries:
            if not isinstance(entry, dict):
                continue

            title = entry.get("title") or entry.get("name")
            if isinstance(title, str) and title.strip():
                info["title"] = title.strip()

            hiring_org = entry.get("hiringOrganization")
            if isinstance(hiring_org, dict):
                org_name = hiring_org.get("name")
                if isinstance(org_name, str) and org_name.strip():
                    info["company"] = org_name.strip()

            base_salary = entry.get("baseSalary")
            if isinstance(base_salary, dict):
                value = base_salary.get("value")
                if isinstance(value, dict):
                    salary = value.get("value")
                    unit = value.get("unitText", "")
                    if salary is not None:
                        info["salary"] = f"{salary} {unit}".strip()

            location = entry.get("jobLocation")
            if isinstance(location, list) and location:
                location = location[0]
            if isinstance(location, dict):
                address = location.get("address")
                if isinstance(address, dict):
                    city = address.get("addressLocality")
                    country = address.get("addressCountry")
                    composed = " - ".join([v for v in [city, country] if isinstance(v, str) and v.strip()])
                    if composed:
                        info["location"] = composed

            valid_through = entry.get("validThrough")
            if isinstance(valid_through, str) and valid_through.strip():
                info["deadline"] = valid_through.strip()

    return info


def _extract_next_data(html: str) -> dict[str, str]:
    info: dict[str, str] = {}
    match = re.search(
        r'<script[^>]*id="__NEXT_DATA__"[^>]*>(.*?)</script>',
        html,
        flags=re.IGNORECASE | re.DOTALL,
    )
    if not match:
        return info

    raw = match.group(1).strip()
    try:
        data = json.loads(raw)
    except Exception:
        return info

    for obj in _walk_json_values(data):
        if "title" not in info:
            for key in ["title", "intitule", "name", "poste", "jobTitle"]:
                value = obj.get(key)
                if isinstance(value, str) and value.strip():
                    info["title"] = value.strip()
                    break

        if "company" not in info:
            for key in ["entreprise", "company", "societe", "organizationName", "nomEntreprise"]:
                value = obj.get(key)
                if isinstance(value, str) and value.strip():
                    info["company"] = value.strip()
                    break

        if "location" not in info:
            for key in ["lieu", "localisation", "ville", "country", "pays", "location"]:
                value = obj.get(key)
                if isinstance(value, str) and value.strip():
                    info["location"] = value.strip()
                    break

        if "duration" not in info:
            for key in ["duree", "duration", "dureeMission", "missionDuration"]:
                value = obj.get(key)
                if isinstance(value, str) and value.strip():
                    info["duration"] = value.strip()
                    break

        if "salary" not in info:
            for key in ["salaire", "remuneration", "indemnite", "salary", "compensation"]:
                value = obj.get(key)
                if isinstance(value, str) and value.strip():
                    info["salary"] = value.strip()
                    break

        if "start" not in info:
            for key in ["dateDebut", "startDate", "dateDeDebut", "debutMission"]:
                value = obj.get(key)
                if isinstance(value, str) and value.strip():
                    info["start"] = value.strip()
                    break

        if "deadline" not in info:
            for key in ["dateLimite", "dateCloture", "validThrough", "deadline"]:
                value = obj.get(key)
                if isinstance(value, str) and value.strip():
                    info["deadline"] = value.strip()
                    break

    return info


class VIEBot(discord.Client):
    async def on_ready(self):
        print(f"Bot connecté : {self.user}")
        self.loop.create_task(self.check_vie())

    async def _fetch_html(self, session: aiohttp.ClientSession, url: str) -> str:
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
            ),
            "Cache-Control": "no-cache, no-store, max-age=0",
            "Pragma": "no-cache",
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

        source_urls = _build_source_urls()
        for source in source_urls:
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

    async def _fetch_offer_details(self, session: aiohttp.ClientSession, offer_id: str) -> dict[str, str]:
        offer_url = f"https://mon-vie-via.businessfrance.fr/offres/{offer_id}"
        sources = [
            offer_url,
            f"https://api.allorigins.win/get?url={quote_plus(offer_url)}",
            f"https://r.jina.ai/http://{offer_url.replace('https://', '')}",
        ]

        html = ""
        for source in sources:
            try:
                html = await self._fetch_html(session, source)
                if html:
                    break
            except Exception as exc:
                print(f"Détail KO: {source} -> {exc}")

        details: dict[str, str] = {"url": offer_url}
        if not html:
            return details

        details.update(_extract_json_ld_data(html))
        next_data = _extract_next_data(html)
        for key, value in next_data.items():
            details.setdefault(key, value)

        if "title" not in details:
            title_match = re.search(r"<title[^>]*>(.*?)</title>", html, re.IGNORECASE | re.DOTALL)
            if title_match:
                details["title"] = _clean_text(title_match.group(1)).replace("| Mon V.I.E/V.I.A", "").strip()

        details["location"] = details.get("location") or _find_field(
            html, ["Localisation", "Lieu", "Pays", "Ville"]
        )
        details["duration"] = details.get("duration") or _find_field(html, ["Durée", "Duree", "Duration"])
        details["salary"] = details.get("salary") or _find_field(
            html, ["Salaire", "Rémunération", "Remuneration", "Indemnité", "Indemnite"]
        )
        details["company"] = details.get("company") or _find_field(
            html, ["Entreprise", "Société", "Societe", "Organisme"]
        )
        details["start"] = details.get("start") or _find_field(
            html, ["Date de début", "Date debut", "Début mission", "Start date"]
        )
        details["deadline"] = details.get("deadline") or _find_field(
            html, ["Date limite", "Date de clôture", "Date de cloture", "Deadline"]
        )

        return {k: v for k, v in details.items() if v}

    def _format_message(self, details: dict[str, str], offer_id: str) -> str:
        title = details.get("title", f"Offre VIE #{offer_id}")
        lines = [f"✅ **Nouvelle offre VIE : {title}**"]

        if details.get("company"):
            lines.append(f"🏢 **Entreprise** : {details['company']}")
        if details.get("location"):
            lines.append(f"📍 **Lieu** : {details['location']}")
        if details.get("duration"):
            lines.append(f"⏳ **Durée** : {details['duration']}")
        if details.get("salary"):
            lines.append(f"💰 **Salaire / indemnité** : {details['salary']}")
        if details.get("start"):
            lines.append(f"🗓️ **Début** : {details['start']}")
        if details.get("deadline"):
            lines.append(f"⏰ **Date limite** : {details['deadline']}")

        lines.append(f"🔗 {details.get('url', f'https://mon-vie-via.businessfrance.fr/offres/{offer_id}')}")
        return "\n".join(lines)

    async def check_vie(self):
        await self.wait_until_ready()

        channel = self.get_channel(CHANNEL_ID)
        if channel is None:
            channel = await self.fetch_channel(CHANNEL_ID)

        async with aiohttp.ClientSession() as session:
            while not self.is_closed():
                try:
                    print("Vérification des offres...")
                    found_ids = await self._extract_offer_ids(session)

                    if not SEEN_IDS:
                        SEEN_IDS.update(found_ids)
                        print(f"Initialisation: {len(found_ids)} offres déjà présentes ignorées.")
                    else:
                        new_ids = [oid for oid in found_ids if oid not in SEEN_IDS]
                        for oid in new_ids:
                            SEEN_IDS.add(oid)
                            details = await self._fetch_offer_details(session, oid)
                            await channel.send(self._format_message(details, oid))
                            await asyncio.sleep(1)

                        if not new_ids:
                            print("Rien de nouveau pour l'instant.")

                except Exception as exc:
                    print(f"Erreur technique: {exc}")

                await asyncio.sleep(CHECK_INTERVAL_SECONDS)


intents = discord.Intents.default()
client = VIEBot(intents=intents)
client.run(DISCORD_TOKEN)
