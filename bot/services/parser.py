import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone

import httpx
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

OFFERS_URLS = [
    "https://autach.pl/offers?from=axa&sortby=ending",
    "https://autach.pl/offers?from=rest&sortby=ending",
    "https://autach.pl/offers?from=allianz&sortby=ending",
]

SPEC_MAP = {
    "Treibstoff": "Вид палива",
    "Hubraum": "Об'єм двигуна",
    "Getriebe": "Коробка передач",
    "Antrieb": "Привiд",
    "Karosserie": "Кузов",
    "Aussenfarbe": "Колiр",
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


def _parse_auction_end(text: str) -> int:
    """Parse 'YYYY-MM-DD HH:MM:SS' -> seconds remaining until auction end.
    Returns 999999 on failure."""
    try:
        end_dt = datetime.strptime(text.strip(), "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
        now = datetime.now(timezone.utc)
        diff = (end_dt - now).total_seconds()
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


def _parse_cards(html: str, source: str) -> list[OfferItem]:
    soup = BeautifulSoup(html, "lxml")
    cards = soup.select(".offer-card")

    offers: list[OfferItem] = []
    for card in cards:
        h3 = card.select_one("h3")
        if not h3:
            continue
        title = h3.get_text(strip=True)

        auction_id_el = card.select_one("p.auction-id")
        lot_id = ""
        if auction_id_el:
            text = auction_id_el.get_text(strip=True)
            if ":" in text:
                lot_id = text.split(":")[1].strip().split()[0]

        year = ""
        mileage = ""
        auction_end = ""
        labels = card.select(".offer-details p.label")
        for label_el in labels:
            label_text = label_el.get_text(strip=True)
            value_el = label_el.find_next_sibling("p", class_="value")
            if not value_el:
                continue
            value_text = value_el.get_text(strip=True)

            if "Rejestracja" in label_text:
                year = value_text
            elif "Przebieg" in label_text:
                mileage = value_text.replace(" km", "").replace("\xa0", "").strip()
            elif label_text == "Data zakończenia":
                auction_end = value_text

        auction_end_seconds = _parse_auction_end(auction_end)

        link_el = card.select_one("a[href*='/offer/']")
        detail_url = link_el["href"] if link_el else ""

        img_el = card.select_one(".swiper-slide img")
        image_url = img_el["src"] if img_el and img_el.get("src") else ""

        offers.append(OfferItem(
            id=lot_id,
            title=title,
            year=year,
            mileage=mileage,
            auction_end=auction_end,
            url=detail_url,
            image_url=image_url,
            source=source,
            auction_end_seconds=auction_end_seconds,
        ))

    return offers


async def fetch_offers() -> list[OfferItem]:
    """Fetch from all sources, merge, sort by auction_end_seconds ascending (most urgent first)."""
    async with httpx.AsyncClient(timeout=15) as client:
        tasks = [client.get(url) for url in OFFERS_URLS]
        responses = await asyncio.gather(*tasks, return_exceptions=True)

    all_offers: list[OfferItem] = []
    sources = ["AXA", "REST", "Allianz"]
    for resp, source in zip(responses, sources):
        if isinstance(resp, Exception):
            logger.warning("Source %s fetch error: %s", source, resp)
            continue
        if resp.status_code != 200:
            logger.warning("Source %s returned HTTP %s", source, resp.status_code)
            continue
        all_offers.extend(_parse_cards(resp.text, source))

    # Deduplicate by offer ID
    seen: set[str] = set()
    unique: list[OfferItem] = []
    for o in all_offers:
        if o.id and o.id not in seen:
            seen.add(o.id)
            unique.append(o)

    # Sort by auction_end_seconds ascending (most urgent first)
    unique.sort(key=lambda o: o.auction_end_seconds)
    return unique


async def fetch_offer_detail(url: str) -> OfferDetail | None:
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.get(url)
        resp.raise_for_status()

    soup = BeautifulSoup(resp.text, "lxml")

    h2 = soup.select_one("h2")
    title = h2.get_text(strip=True) if h2 else ""

    year = ""
    mileage = ""
    for label_el in soup.select("p.label"):
        label_text = label_el.get_text(strip=True).lower()
        value_el = label_el.find_next_sibling("p")
        if not value_el:
            continue
        val = value_el.get_text(strip=True)
        if "rejestracja" in label_text or "registrierung" in label_text:
            year = val
        elif "przebieg" in label_text:
            mileage = val.replace(" km", "").replace("\xa0", "").strip()

    specs: dict[str, str] = {}
    fuel = ""
    engine = ""
    transmission = ""
    for row in soup.select(".table-row"):
        cols = row.select(".table-col")
        if len(cols) < 2:
            continue
        key = cols[0].get_text(strip=True)
        val = cols[1].get_text(strip=True)
        if not val:
            continue

        if key == "Treibstoff":
            fuel = val
        elif key == "Hubraum":
            engine = val
        elif key == "Getriebe":
            transmission = val

        if key in SPEC_MAP:
            specs[SPEC_MAP[key]] = val

    photos = []
    for img in soup.select(".image-slider img"):
        src = img.get("src", "")
        if src and src not in photos:
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
