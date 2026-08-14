"""
Получение цен криптовалют через бесплатный CoinGecko API.
Никакой API-ключ не нужен для базовых запросов (есть лимит: ~10-30 запросов/минуту).
"""
import httpx

COINGECKO_BASE = "https://api.coingecko.com/api/v3"

# Топ монет для отображения по умолчанию в Mini App
POPULAR_COINS = [
    "bitcoin", "ethereum", "the-open-network", "solana",
    "binancecoin", "ripple", "dogecoin", "cardano", "tron", "avalanche-2",
]


async def get_prices(coin_ids: list[str]) -> dict:
    """
    Возвращает {coin_id: {"usd": price, "usd_24h_change": percent}}
    """
    if not coin_ids:
        return {}
    ids_param = ",".join(coin_ids)
    url = f"{COINGECKO_BASE}/simple/price"
    params = {
        "ids": ids_param,
        "vs_currencies": "usd",
        "include_24hr_change": "true",
    }
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.get(url, params=params)
        resp.raise_for_status()
        return resp.json()


async def search_coin(query: str) -> list[dict]:
    """Поиск монеты по названию, для добавления в отслеживание."""
    url = f"{COINGECKO_BASE}/search"
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.get(url, params={"query": query})
        resp.raise_for_status()
        data = resp.json()
        coins = data.get("coins", [])[:5]
        return [
            {"id": c["id"], "name": c["name"], "symbol": c["symbol"]}
            for c in coins
        ]


async def get_popular_with_prices() -> list[dict]:
    """Список популярных монет с ценами — для главного экрана Mini App."""
    prices = await get_prices(POPULAR_COINS)
    result = []
    for coin_id in POPULAR_COINS:
        p = prices.get(coin_id, {})
        result.append({
            "id": coin_id,
            "price": p.get("usd"),
            "change_24h": p.get("usd_24h_change"),
        })
    return result
