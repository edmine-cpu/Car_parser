# План міграції парсера autach.pl

Сайт перейшов на Angular SPA. Стара логіка `bot/services/parser.py` (3 запити до
`/offers?from=...&sortby=ending` + BeautifulSoup) **повністю не працює**: будь-який
URL віддає однакову SPA-оболонку без даних, селектори (`.offer-card`, `p.auction-id`,
`.offer-details`) на сторінці більше не існують.

Хороша новина — у новому фронті є **публічний JSON API без авторизації**, який
віддає рівно ті дані, що нам потрібні для списку. Це не «правка регулярок», це
повна заміна частини зі списком, але вона стає **простішою**, а не складнішою.

---

## Що змінилось на сайті

### Списки
- `/offers?from=axa|allianz|rest&sortby=ending` — більше немає (повертає SPA-оболонку, 0 карток).
- Картки на головній рендеряться через Angular і у звичайному `httpx`-запиті відсутні. Prerender повертає вміст лише для bot User-Agent (Googlebot тощо).
- Знайдено внутрішній API:
  - `GET https://autach.pl/api-v2/auctions` — публічний, без авторизації, повертає JSON.
  - Параметри (виявлено в `chunk-IU7EBP43.js`): `searchQuery`, `mileageFrom`, `mileageTo`, `yearFrom`, `yearTo`, `type` (наприклад `all`, `new24`), `house` (`all`, `AXA`, `BCP`, `REST`, `Allianz`), `sort` (`ending`).
  - Пагінація: `page=N`, по 20 на сторінку, поле `totalCount` у відповіді.
  - Сервер уже повертає список **відсортованим** за `sort=ending` (найгарячіші зверху).

Приклад відповіді (фрагмент):
```json
{
  "auctions": [{
    "id": 154830,
    "websiteName": "REST",
    "brand": "Audi", "model": "A6 Allroad", "name": "Audi A6 Allroad",
    "mileage": 262338,
    "photosFolder": "V3JWK3FjSFVBLzJORkZucThhSnpOQT09",
    "mainPhoto": "bjYVZ8krXc.jpg",
    "offerStart": "2026-04-23T15:57:14",
    "offerEnd":   "2026-04-26T15:56:14",
    "firstRegistrationDate": "2005-07-12",
    "currency": "CHF",
    "offerLink": "/offer/154830/audi-a6-allroad-4"
  }],
  "totalCount": 900
}
```
Зараз 900 активних лотів сумарно по всіх «домах».

> Нова значуща зміна: з’явився **новий source `BCP`**, якого не було в старому коді. Старі AXA / REST / Allianz також лишилися.

### Детальна сторінка
- Стара логіка скрейпить HTML за `url` з картки. URL досі виглядає як `/offer/{id}/{slug}`, але вміст знову рендериться SPA — потрібен **bot User-Agent** (Googlebot), щоб prerender повернув готовий HTML.
- JSON-аналог `/api-v2/{house}/offers/{id}` віддає **403** без логіну → не годиться.
- Селектори на детальній сторінці змінені:
  - Заголовок: `h1.premium-auction-title-detail` (раніше `h2`).
  - Характеристики: `.premium-spec-row` → `.premium-spec-label` / `.premium-spec-value` (раніше `.table-row` → `.table-col`).
  - Лейбли в prerender віддаються **англійською** («Fuel», «Engine», «Transmission», «Drive», «Body», «Color»), а не німецькою. Старий `SPEC_MAP` (Treibstoff/Hubraum/Getriebe/Antrieb/Karosserie/Aussenfarbe) потрібно або замінити, або розширити (мова, ймовірно, керується `Accept-Language`).
  - Фото: `<img src="/images/offer/{folder}/{name}.jpg">` всередині галереї (`.premium-gallery-container`).

---

## Що треба правити в `bot/services/parser.py`

### 1. `fetch_offers()` — переписати на JSON API
- Прибрати `OFFERS_URLS` і три HTML-запити.
- Один запит на `https://autach.pl/api-v2/auctions?type=all&house=all&sort=ending&page=1` (за потреби — кілька сторінок через `page`).
- Розпарсити JSON у `OfferItem`:
  - `id` ← `str(a["id"])`
  - `title` ← `a["name"]` (або `f'{brand} {model}'`)
  - `year` ← `a["firstRegistrationDate"][:4]` (або повна дата, як було раніше)
  - `mileage` ← `str(a["mileage"])` (число, без « km»)
  - `auction_end` ← нормалізувати `offerEnd` (`2026-04-26T15:56:14`) у формат, який очікує `format_remaining` / решта коду
  - `auction_end_seconds` ← `_parse_auction_end` адаптувати під ISO-формат (`fromisoformat`)
  - `url` ← `f'https://autach.pl{a["offerLink"]}'`
  - `image_url` ← `f'https://autach.pl/images/offer/{a["photosFolder"]}/{a["mainPhoto"]}'`
  - `source` ← `a["websiteName"]`
- Дедуплікація і повторне сортування за `auction_end_seconds` — лишити як safety net, але серверна вже коректна.
- **Часовий пояс `offerEnd`** треба перевірити (в JSON немає TZ). Імовірно це Europe/Warsaw або UTC. Старий код примусово ставив UTC; уточнити порівнянням з countdown-таймером і виправити, інакше «залишилось» рахуватиметься на ±2 год.

### 2. `fetch_offer_detail(url)` — оновити селектори і UA
- При запиті HTML деталки слати `User-Agent: Mozilla/5.0 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)`. Інакше httpx отримає порожню SPA-оболонку.
- Розбір:
  - `title` ← `soup.select_one('h1.premium-auction-title-detail')`
  - Цикл по `.premium-spec-row`, ключ із `.premium-spec-label`, значення з `.premium-spec-value`.
  - `SPEC_MAP` переписати на англійські ключі (`Fuel` → «Вид палива», `Engine` → «Об'єм двигуна», `Transmission` → «Коробка передач», `Drive` → «Привід», `Body` → «Кузов», `Color` → «Колір»). За бажання — лишити німецькі як fallback.
  - `mileage`/`year` краще брати з даних списку (`firstRegistrationDate`, `mileage`), бо детальна сторінка ці поля показує іншою розкладкою.
  - `photos` ← унікальні `src`-и з `.premium-gallery-container img` (фільтрувати лише `/images/offer/...`).

### 3. Дрібні правки
- Винести `BASE_URL = "https://autach.pl"` і `BOT_UA` у константи.
- Тайм-аут `httpx` на 15с лишити; додати один `headers={"User-Agent": ...}` на клієнт для HTML-запитів.
- У `OFFERS_URLS` вже немає сенсу, видалити.
- `poller.py` варто перевірити, чи він не залежить від конкретних значень `source` (`AXA/REST/Allianz`), бо тепер з’явився `BCP`.

---

## Оцінка обсягу

| Блок                     | Складність | Час     |
|--------------------------|-----------|---------|
| Список через JSON API     | низька    | 1–2 год |
| Детальна сторінка (селектори + UA) | низька–середня | 1 год |
| Адаптація часового поясу `offerEnd` | низька (треба перевірити фактом) | 15–30 хв |
| Перевірка `poller.py` / БД на новий source `BCP` | низька | 15 хв |
| Ручне тестування end-to-end | —         | 30–60 хв |
| **Разом**                |           | **~3–5 годин** |

Підсумок: для мене зміна **проста**. Сайт став SPA, але дав публічний JSON API, який покриває весь список. Найбільший ризик — недокументований API (`/api-v2`) може мовчки змінитися, тому варто залишити захисну логіку (try/except, дефолти на пусті поля) і не падати, якщо одне поле зникло.

---

## Подальші кроки

1. Затвердити цей план (зокрема — чи тягнемо лише page=1 з `sort=ending`, чи всі 900 лотів через пагінацію).
2. Переписати `fetch_offers()` на JSON.
3. Оновити селектори в `fetch_offer_detail()` + UA.
4. Перевірити `poller.py` і модель БД на новий source `BCP`.
5. Прогнати бота локально, переконатись що картки і деталки відображаються.
