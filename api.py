"""
API-сервер для Mini App.
Mini App стучится сюда за списком монет, ценами, добавляет/удаляет отслеживание.

Запуск: uvicorn api:app --reload --port 8000
"""
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

import database as db
import prices
import nft
import os

# Секретный ключ для служебных эндпоинтов (выдача премиума напрямую).
# Без него /premium/grant мог бы вызвать кто угодно и получить премиум бесплатно.
ADMIN_SECRET = os.environ.get("ADMIN_SECRET", "смени-меня-в-render-environment")

app = FastAPI(title="Crypto Tracker API")

# Разрешаем запросы с любого домена — Mini App грузится из Telegram
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

db.init_db()


class AddCoinRequest(BaseModel):
    user_id: int
    coin_id: str
    alert_percent: float = 5.0


class RemoveCoinRequest(BaseModel):
    user_id: int
    coin_id: str


class AddNftRequest(BaseModel):
    user_id: int
    nft_id: str
    alert_percent: float = 5.0


class RemoveNftRequest(BaseModel):
    user_id: int
    nft_id: str


class SettingsRequest(BaseModel):
    user_id: int
    default_alert_percent: float


class SetCoinAlertRequest(BaseModel):
    user_id: int
    coin_id: str
    alert_percent: float


class SetNftAlertRequest(BaseModel):
    user_id: int
    nft_id: str
    alert_percent: float


class PromoRequest(BaseModel):
    user_id: int
    code: str


class CreatePromoRequest(BaseModel):
    secret: str
    code: str
    days: int
    max_uses: int | None = None


class LogAlertRequest(BaseModel):
    secret: str
    user_id: int
    item_type: str
    item_id: str
    item_name: str
    change_percent: float
    price: float


class UpdatePriceRequest(BaseModel):
    secret: str
    user_id: int
    item_id: str
    price: float


class LogPaymentRequest(BaseModel):
    secret: str
    user_id: int
    telegram_charge_id: str
    amount_stars: int
    days_granted: int


@app.get("/")
def root():
    return {"status": "ok"}


@app.get("/popular")
async def popular_coins():
    """Список популярных монет с текущими ценами и иконками — для главного экрана."""
    return await prices.get_popular_with_images()


@app.get("/search")
async def search(query: str):
    """Поиск монеты по названию."""
    return await prices.search_coin(query)


@app.get("/user/{user_id}")
def get_user(user_id: int):
    """Инфо о юзере: премиум статус + список отслеживаемых монет и NFT."""
    user = db.get_or_create_user(user_id)
    tracked_coins = db.get_tracked_coins(user_id)
    tracked_nfts = db.get_tracked_nfts(user_id)
    return {
        "user_id": user_id,
        "is_premium": db.is_premium(user_id),
        "premium_until": user.get("premium_until"),
        "default_alert_percent": user.get("default_alert_percent", 5.0),
        "tracked_coins": tracked_coins,
        "tracked_nfts": tracked_nfts,
        "free_limit": 3,
    }


@app.post("/track")
def add_coin(req: AddCoinRequest):
    db.get_or_create_user(req.user_id)
    percent = req.alert_percent if req.alert_percent != 5.0 else db.get_default_alert_percent(req.user_id)
    ok = db.add_tracked_coin(req.user_id, req.coin_id, percent)
    if not ok:
        raise HTTPException(
            status_code=403,
            detail="Достигнут лимит бесплатных монет (3). Оформи премиум для безлимита.",
        )
    return {"success": True}


@app.post("/untrack")
def remove_coin(req: RemoveCoinRequest):
    db.remove_tracked_coin(req.user_id, req.coin_id)
    return {"success": True}


@app.get("/nfts/popular")
async def popular_nfts():
    """Список популярных NFT-коллекций с floor price."""
    return await nft.get_popular_nfts()


@app.post("/nfts/track")
def add_nft(req: AddNftRequest):
    db.get_or_create_user(req.user_id)
    percent = req.alert_percent if req.alert_percent != 5.0 else db.get_default_alert_percent(req.user_id)
    ok = db.add_tracked_nft(req.user_id, req.nft_id, percent)
    if not ok:
        raise HTTPException(
            status_code=403,
            detail="Достигнут лимит бесплатных коллекций (3). Оформи премиум для безлимита.",
        )
    return {"success": True}


@app.post("/nfts/untrack")
def remove_nft(req: RemoveNftRequest):
    db.remove_tracked_nft(req.user_id, req.nft_id)
    return {"success": True}


@app.post("/premium/grant")
def grant_premium(user_id: int, days: int = 30, secret: str = ""):
    """Служебный эндпоинт — требует ADMIN_SECRET, иначе кто угодно мог бы выдать себе премиум."""
    if secret != ADMIN_SECRET:
        raise HTTPException(status_code=403, detail="Неверный секретный ключ")
    db.grant_premium(user_id, days)
    return {"success": True}


@app.post("/settings")
def update_settings(req: SettingsRequest):
    """Обновляет порог % по умолчанию для новых отслеживаний."""
    db.get_or_create_user(req.user_id)
    db.set_default_alert_percent(req.user_id, req.default_alert_percent)
    return {"success": True}


@app.post("/track/alert")
def set_coin_alert(req: SetCoinAlertRequest):
    """Меняет порог % у уже отслеживаемой монеты."""
    db.set_coin_alert_percent(req.user_id, req.coin_id, req.alert_percent)
    return {"success": True}


@app.post("/nfts/track/alert")
def set_nft_alert(req: SetNftAlertRequest):
    """Меняет порог % у уже отслеживаемой NFT-коллекции."""
    db.set_nft_alert_percent(req.user_id, req.nft_id, req.alert_percent)
    return {"success": True}


@app.get("/alerts/{user_id}")
def get_alerts(user_id: int):
    """История последних сработавших алертов — для раздела «Алерты»."""
    return db.get_recent_alerts(user_id)


@app.post("/promo/redeem")
def redeem_promo(req: PromoRequest):
    """Активирует промокод — премиум создаёт/продлевает бот-модуль database.py."""
    db.get_or_create_user(req.user_id)
    result = db.redeem_promo_code(req.user_id, req.code)
    if not result["success"]:
        raise HTTPException(status_code=400, detail=result["message"])
    return result


# ---- Служебные эндпоинты для бота (фоновая проверка цен, админ-команды) ----
# Всё под секретным ключом: эти ручки видят/меняют данные всех юзеров сразу,
# в отличие от обычных ручек выше, которые работают в рамках одного user_id.

def _check_secret(secret: str):
    if secret != ADMIN_SECRET:
        raise HTTPException(status_code=403, detail="Неверный секретный ключ")


@app.get("/admin/tracked")
def admin_get_tracked(secret: str):
    """Все отслеживаемые монеты и NFT всех юзеров — для фоновой проверки алертов."""
    _check_secret(secret)
    return {
        "coins": db.get_all_tracked_coins(),
        "nfts": db.get_all_tracked_nfts(),
    }


@app.post("/admin/update_coin_price")
def admin_update_coin_price(req: UpdatePriceRequest):
    _check_secret(req.secret)
    db.update_last_price(req.user_id, req.item_id, req.price)
    return {"success": True}


@app.post("/admin/update_nft_price")
def admin_update_nft_price(req: UpdatePriceRequest):
    _check_secret(req.secret)
    db.update_last_nft_price(req.user_id, req.item_id, req.price)
    return {"success": True}


@app.post("/admin/log_alert")
def admin_log_alert(req: LogAlertRequest):
    _check_secret(req.secret)
    db.log_alert(req.user_id, req.item_type, req.item_id, req.item_name, req.change_percent, req.price)
    return {"success": True}


@app.post("/admin/log_payment")
def admin_log_payment(req: LogPaymentRequest):
    _check_secret(req.secret)
    is_new = db.log_payment(req.user_id, req.telegram_charge_id, req.amount_stars, req.days_granted)
    return {"is_new": is_new}


@app.post("/admin/create_promo")
def admin_create_promo(req: CreatePromoRequest):
    _check_secret(req.secret)
    ok = db.create_promo_code(req.code, req.days, req.max_uses)
    return {"success": ok}
