import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone

import httpx
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

BASE_URL = "https://autach.pl"
AUCTIONS_API = f"{BASE_URL}/api-v2/auctions"
PAGE_SIZE = 100

# Detail pages are an Angular SPA; the prerender layer only returns rendered
# HTML for crawler User-Agents. Without this header we get an empty <app-root>.
BOT_UA = "Mozilla/5.0 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)"

# Detail-page spec labels (Googlebot prerender returns English labels).
SPEC_MAP = {
    "Fuel": "Вид палива",
    "Engine capacity": "Об'єм двигуна",
    "Gearbox type": "Коробка передач",
    "Drive": "Привiд",
    "Body Type / Doors": "Кузов",
    "Color": "Колiр",
}


def format_remaining(seconds: int) -> str:
    """Format seconds remaining as 'Xд Yг Zхв'."""
    if seconds >= 999999:
        return "невідомо"
    d = seconds // 86400
    h = (seconds % 86400) // 3600
    m = (seconds % 3600) // 60
    parts = []
    if d:
        parts.append(f"{d}д")
    if h:
        parts.append(f"{h}г")
    parts.append(f"{m}хв")
    return " ".join(parts)


def _parse_iso_end(text: str) -> int:
    """Parse 'YYYY-MM-DDTHH:MM:SS' (assumed UTC) -> seconds remaining.
    Returns 999999 on failure."""
    try:
        end_dt = datetime.fromisoformat(text).replace(tzinfo=timezone.utc)
        diff = (end_dt - datetime.now(timezone.utc)).total_seconds()
        return max(0, int(diff))
    except (ValueError, TypeError):
        return 999999


@dataclass
class OfferItem:
    id: str
    title: str
    year: str
    mileage: str
    auction_end: str
    url: str
    image_url: str
    source: str = ""
    auction_end_seconds: int = 999999


@dataclass
class OfferDetail:
    title: str
    year: str
    mileage: str
    fuel: str
    engine: str
    transmission: str
    photos: list[str] = field(default_factory=list)
    specs: dict[str, str] = field(default_factory=dict)


def _auction_to_offer(a: dict) -> OfferItem | None:
    aid = a.get("id")
    end_iso = a.get("offerEnd") or ""
    if aid is None or not end_iso:
        return None

    folder = a.get("photosFolder") or ""
    main = a.get("mainPhoto") or ""
    image_url = f"{BASE_URL}/images/offer/{folder}/{main}" if folder and main else ""

    link = a.get("offerLink") or ""
    url = f"{BASE_URL}{link}" if link.startswith("/") else link

    reg = a.get("firstRegistrationDate") or ""
    year = reg[:4] if reg else ""

    mileage_val = a.get("mileage")
    mileage = str(mileage_val) if mileage_val is not None else ""

    return OfferItem(
        id=str(aid),
        title=a.get("name") or f'{a.get("brand", "")} {a.get("model", "")}'.strip(),
        year=year,
        mileage=mileage,
        auction_end=end_iso.replace("T", " "),
        url=url,
        image_url=image_url,
        source=a.get("websiteName") or "",
        auction_end_seconds=_parse_iso_end(end_iso),
    )


async def _fetch_page(client: httpx.AsyncClient, page: int) -> dict:
    resp = await client.get(
        AUCTIONS_API,
        params={
            "type": "all",
            "house": "all",
            "sort": "ending",
            "page": page,
            "pageSize": PAGE_SIZE,
        },
    )
    resp.raise_for_status()
    return resp.json()


async def fetch_offers() -> list[OfferItem]:
    """Fetch all active auctions via /api-v2/auctions, sorted by ending time ascending."""
    async with httpx.AsyncClient(timeout=20) as client:
        try:
            first = await _fetch_page(client, 1)
        except Exception as e:
            logger.error("Auctions API page 1 failed: %s", e)
            return []

        total = int(first.get("totalCount") or 0)
        items = list(first.get("auctions") or [])

        if total > PAGE_SIZE:
            pages = (total + PAGE_SIZE - 1) // PAGE_SIZE
            results = await asyncio.gather(
                *(_fetch_page(client, p) for p in range(2, pages + 1)),
                return_exceptions=True,
            )
            for p, res in enumerate(results, start=2):
                if isinstance(res, Exception):
                    logger.warning("Auctions API page %d failed: %s", p, res)
                    continue
                items.extend(res.get("auctions") or [])

    offers: list[OfferItem] = []
    seen: set[str] = set()
    for a in items:
        offer = _auction_to_offer(a)
        if offer is None or not offer.id or offer.id in seen:
            continue
        seen.add(offer.id)
        offers.append(offer)

    offers.sort(key=lambda o: o.auction_end_seconds)
    return offers


async def fetch_offer_detail(url: str) -> OfferDetail | None:
    async with httpx.AsyncClient(timeout=20, headers={"User-Agent": BOT_UA}) as client:
        resp = await client.get(url)
        resp.raise_for_status()

    soup = BeautifulSoup(resp.text, "lxml")

    title_el = soup.select_one("h1.premium-auction-title-detail") or soup.select_one("h1")
    title = title_el.get_text(" ", strip=True) if title_el else ""

    specs: dict[str, str] = {}
    fuel = ""
    engine = ""
    transmission = ""
    year = ""
    mileage = ""
    for row in soup.select(".premium-spec-row"):
        label_el = row.select_one(".premium-spec-label")
        value_el = row.select_one(".premium-spec-value")
        if not label_el or not value_el:
            continue
        key = label_el.get_text(strip=True)
        val = value_el.get_text(strip=True)
        if not val:
            continue

        if key == "Fuel":
            fuel = val
        elif key == "Engine capacity":
            engine = val
        elif key == "Gearbox type":
            transmission = val
        elif key == "Mileage (km)":
            mileage = val.replace("\xa0", "").replace(" ", "")
        elif key == "First inv.":
            year = val[-4:] if len(val) >= 4 else val

        if key in SPEC_MAP:
            specs[SPEC_MAP[key]] = val

    photos: list[str] = []
    for img in soup.find_all("img"):
        src = img.get("src", "")
        if "/images/offer/" not in src:
            continue
        if "/thumb_" in src or "/thumb-" in src:
            continue
        if src not in photos:
            photos.append(src)

    return OfferDetail(
        title=title,
        year=year,
        mileage=mileage,
        fuel=fuel,
        engine=engine,
        transmission=transmission,
        photos=photos,
        specs=specs,
    )
