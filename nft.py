"""
Получение floor price NFT-коллекций через CoinGecko NFT Data API.
Тот же демо-ключ, что и для крипты — доступен на бесплатном Demo-плане.

Важно: покрытие NFT у CoinGecko — это в основном крупные Ethereum-коллекции
(Bored Ape, CryptoPunks, Pudgy Penguins и т.д.). TON/Getgems-коллекции туда
почти не входят — под них при необходимости нужен отдельный источник данных.
"""
import time
import httpx

from prices import COINGECKO_BASE, _HEADERS

CACHE_TTL = 120  # NFT данные обновляются реже, чем крипта — кэш подольше

# Популярные коллекции для отображения по умолчанию (id из CoinGecko)
POPULAR_NFTS = [
    "bored-ape-yacht-club",
    "cryptopunks",
    "pudgy-penguins",
    "azuki",
    "doodles-official",
    "mutant-ape-yacht-club",
    "moonbirds",
    "clonex",
]

_cache: dict[str, tuple[float, dict]] = {}


async def get_nft_data(nft_id: str) -> dict | None:
    """
    Возвращает данные одной NFT-коллекции: floor price, % изменения за 24ч и т.д.
    Кэшируется по каждой коллекции отдельно.
    """
    now = time.time()
    cached = _cache.get(nft_id)
    if cached and (now - cached[0]) < CACHE_TTL:
        return cached[1]

    url = f"{COINGECKO_BASE}/nfts/{nft_id}"
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(url, headers=_HEADERS)
            resp.raise_for_status()
            data = resp.json()
            _cache[nft_id] = (now, data)
            return data
    except httpx.HTTPStatusError:
        if cached:
            return cached[1]
        return None


async def get_popular_nfts() -> list[dict]:
    """Список популярных коллекций с floor price — для главного экрана NFT."""
    result = []
    for nft_id in POPULAR_NFTS:
        data = await get_nft_data(nft_id)
        if data is None:
            continue
        floor = data.get("floor_price", {})
        result.append({
            "id": nft_id,
            "name": data.get("name", nft_id),
            "image": data.get("image", {}).get("small"),
            "floor_price_usd": floor.get("usd"),
            "floor_price_native": floor.get("native_currency"),
            "native_currency_symbol": data.get("native_currency_symbol", "ETH"),
            "change_24h": data.get("floor_price_in_usd_24h_percentage_change"),
        })
    return result
