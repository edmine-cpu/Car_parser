from dataclasses import dataclass

from bs4 import BeautifulSoup
from playwright.async_api import async_playwright

OFFERS_URL = "https://autach.pl/offers?from=axa"


@dataclass
class OfferItem:
    title: str
    image_url: str
    detail_url: str


async def fetch_offers() -> list[OfferItem]:
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()
        await page.goto(OFFERS_URL)
        await page.wait_for_selector(".offer-card h3", timeout=15000)
        html = await page.content()
        await browser.close()

    soup = BeautifulSoup(html, "lxml")
    cards = soup.select(".offer-container .offer-card")

    offers: list[OfferItem] = []
    for card in cards:
        h3 = card.select_one("h3")
        if not h3:
            continue
        title = h3.get_text(strip=True)

        img = card.select_one(".swiper-slide img")
        image_url = img["src"] if img and img.get("src") else ""

        link = card.select_one("a.offer-link")
        detail_url = link["href"] if link and link.get("href") else ""

        if title and image_url and detail_url:
            offers.append(OfferItem(title=title, image_url=image_url, detail_url=detail_url))

    return offers
