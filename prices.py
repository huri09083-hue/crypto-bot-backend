"""
Получение цен криптовалют через бесплатный CoinGecko API.
Никакой API-ключ не нужен для базовых запросов, но лимит очень маленький
(на практике часто ловится 429 Too Many Requests при частых обращениях).

Поэтому здесь добавлено простое кэширование в памяти: реальный запрос
к CoinGecko уходит не чаще раза в CACHE_TTL секунд, а все запросы юзеров
между этими моментами получают сохранённый результат.
"""
import time
import httpx

COINGECKO_BASE = "https://api.coingecko.com/api/v3"
CACHE_TTL = 60  # секунд — как часто реально обновлять цены с CoinGecko

# Топ монет для отображения по умолчанию в Mini App
POPULAR_COINS = [
    "bitcoin", "ethereum", "the-open-network", "solana",
    "binancecoin", "ripple", "dogecoin", "cardano", "tron", "avalanche-2",
]

# Простой кэш в памяти: {ключ_запроса: (время_записи, данные)}
_cache: dict[str, tuple[float, dict]] = {}


async def get_prices(coin_ids: list[str]) -> dict:
    """
    Возвращает {coin_id: {"usd": price, "usd_24h_change": percent}}
    Использует кэш, чтобы не превышать лимит запросов CoinGecko.
    """
    if not coin_ids:
        return {}

    cache_key = ",".join(sorted(coin_ids))
    now = time.time()

    cached = _cache.get(cache_key)
    if cached and (now - cached[0]) < CACHE_TTL:
        return cached[1]

    ids_param = ",".join(coin_ids)
    url = f"{COINGECKO_BASE}/simple/price"
    params = {
        "ids": ids_param,
        "vs_currencies": "usd",
        "include_24hr_change": "true",
    }
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(url, params=params)
            resp.raise_for_status()
            data = resp.json()
            _cache[cache_key] = (now, data)
            return data
    except httpx.HTTPStatusError as e:
        # Если словили лимит (429) или другую ошибку — отдаём последний
        # известный кэш, даже если он устарел, лишь бы не показывать пустоту.
        if cached:
            return cached[1]
        raise e


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
