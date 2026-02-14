# Avito Dashboard — веб-приложение для учёта объявлений и прибыли
# Запуск: uvicorn avito_dashboard.main:app --reload --port 8000
import json
import logging
import os
from datetime import date, timedelta
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, UploadFile, File, HTTPException
from fastapi.responses import HTMLResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .avito_api import AvitoAPIClient, AvitoAPIError
from .price_parser import load_price_list

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(title="Avito Dashboard")

# Конфиг из .env
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent / ".env")

AVITO_CLIENT_ID = os.getenv("AVITO_CLIENT_ID")
AVITO_CLIENT_SECRET = os.getenv("AVITO_CLIENT_SECRET")
if not AVITO_CLIENT_ID or not AVITO_CLIENT_SECRET:
    logger.warning("AVITO_CLIENT_ID или AVITO_CLIENT_SECRET не заданы. Добавьте в .env")

# Глобальное хранилище прайса (в продакшене — БД)
_price_list: list[dict] = []
_price_file_path: Optional[Path] = None

# Загрузка прайса при старте (если указан путь в .env)
AVITO_PRICE_FILE = os.getenv("AVITO_PRICE_FILE")
if AVITO_PRICE_FILE and Path(AVITO_PRICE_FILE).exists():
    try:
        _price_list = load_price_list(AVITO_PRICE_FILE)
        logger.info("Загружен прайс из %s: %d позиций", AVITO_PRICE_FILE, len(_price_list))
    except Exception as e:
        logger.warning("Не удалось загрузить прайс: %s", e)


def get_avito_client() -> AvitoAPIClient:
    if not AVITO_CLIENT_ID or not AVITO_CLIENT_SECRET:
        raise HTTPException(status_code=500, detail="Настройте AVITO_CLIENT_ID и AVITO_CLIENT_SECRET в .env")
    return AvitoAPIClient(AVITO_CLIENT_ID, AVITO_CLIENT_SECRET)


class DateRange(BaseModel):
    date_from: date
    date_to: date


def match_item_to_price(item: dict, price_list: list[dict]) -> tuple[float, float]:
    """Подобрать оптовую/розничную цену для объявления по названию/артикулу."""
    title = (item.get("title") or "").lower()
    price = float(item.get("price", 0) or 0)

    for p in price_list:
        art = (p.get("article") or "").lower()
        name = (p.get("name") or "").lower()
        if art and art in title:
            return p["wholesale_price"], p["retail_price"]
        if name and name in title:
            return p["wholesale_price"], p["retail_price"]

    return 0, price  # если не нашли — себестоимость 0, выручка = цена


def calc_profit(items: list[dict], stats: dict, price_list: list[dict]) -> dict:
    """Оценка прибыли по статистике и прайсу."""
    result = {
        "total_views": 0,
        "total_contacts": 0,
        "estimated_revenue": 0,
        "estimated_cost": 0,
        "estimated_profit": 0,
        "items": [],
    }

    items_by_id = {str(it.get("id")): it for it in items if it.get("id")}
    # Разные форматы ответа API: result.items, result.result.items, items
    stat_items = (
        stats.get("result", {}) or {}
    )
    if isinstance(stat_items, dict):
        stat_items = stat_items.get("items", stat_items.get("stats", []))
    if not stat_items:
        stat_items = stats.get("items", stats.get("stats", []))

    # Если нет постатейной статистики — создаём нулевую для каждого объявления
    if not stat_items and items:
        stat_items = [{"itemId": i["id"], "uniqViews": 0, "uniqContacts": 0} for i in items[:100]]

    for si in stat_items:
        iid = str(si.get("itemId", si.get("id", "")))
        item = items_by_id.get(iid, {})
        wholesale, retail = match_item_to_price(item, price_list)
        views = int(si.get("uniqViews", si.get("views", 0)) or 0)
        contacts = int(si.get("uniqContacts", si.get("contacts", 0)) or 0)

        result["total_views"] += views
        result["total_contacts"] += contacts

        # Упрощённая модель: каждый контакт = потенциальная продажа
        revenue = contacts * float(item.get("price", 0) or retail or 0)
        cost = contacts * wholesale
        profit = revenue - cost

        result["estimated_revenue"] += revenue
        result["estimated_cost"] += cost
        result["estimated_profit"] += profit
        result["items"].append({
            "item_id": iid,
            "title": item.get("title", ""),
            "views": views,
            "contacts": contacts,
            "revenue": round(revenue, 2),
            "cost": round(cost, 2),
            "profit": round(profit, 2),
        })

    result["estimated_revenue"] = round(result["estimated_revenue"], 2)
    result["estimated_cost"] = round(result["estimated_cost"], 2)
    result["estimated_profit"] = round(result["estimated_profit"], 2)
    return result


def build_checklist(profit_data: dict, price_count: int) -> list[dict]:
    """Чек-лист рекомендаций на основе статистики."""
    checklist = []

    if profit_data["total_contacts"] == 0 and profit_data["total_views"] > 0:
        checklist.append({
            "done": False,
            "text": "Есть просмотры, но нет контактов — улучшите описание и фото объявлений",
            "priority": "high",
        })
    if profit_data["total_views"] == 0:
        checklist.append({
            "done": False,
            "text": "Нет просмотров — проверьте продвижение и поднять объявления в выдаче",
            "priority": "high",
        })
    if profit_data["estimated_profit"] < 0:
        checklist.append({
            "done": False,
            "text": "Отрицательная маржа — пересмотрите цены или себестоимость",
            "priority": "high",
        })
    if price_count == 0:
        checklist.append({
            "done": False,
            "text": "Загрузите оптово-розничный прайс для точного расчёта прибыли",
            "priority": "medium",
        })
    if profit_data["total_contacts"] > 5:
        checklist.append({
            "done": True,
            "text": "Хороший отклик — отвечайте быстро на сообщения",
            "priority": "low",
        })
    if profit_data["estimated_profit"] > 0:
        checklist.append({
            "done": True,
            "text": "Положительная маржа — можно масштабировать рекламу",
            "priority": "low",
        })

    checklist.append({
        "done": False,
        "text": "Регулярно обновляйте объявления (поднимайте в выдаче)",
        "priority": "medium",
    })
    checklist.append({
        "done": False,
        "text": "Добавляйте новые товары по запросам с Авито",
        "priority": "medium",
    })

    return checklist


# API эндпоинты

@app.get("/api/items")
def api_items():
    """Список объявлений с Авито."""
    try:
        client = get_avito_client()
        data = client.get_items(limit=100)
        return data
    except AvitoAPIError as e:
        raise HTTPException(status_code=502, detail=str(e))


@app.get("/api/user")
def api_user():
    """ID пользователя Авито."""
    try:
        client = get_avito_client()
        uid = client.get_user_id()
        return {"user_id": uid}
    except AvitoAPIError as e:
        raise HTTPException(status_code=502, detail=str(e))


@app.post("/api/stats")
def api_stats(dates: DateRange):
    """Статистика за период."""
    global _price_list
    try:
        client = get_avito_client()
        uid = client.get_user_id()
        items_data = client.get_items(limit=500)
        items = items_data.get("result", {}).get("items", items_data.get("items", []))
        item_ids = [i["id"] for i in items if i.get("id")]

        try:
            stats = client.get_items_stats(uid, dates.date_from, dates.date_to, item_ids[:50])
        except AvitoAPIError:
            stats = client.get_operation_stats(uid, dates.date_from, dates.date_to)

        profit_data = calc_profit(items, stats, _price_list)
        checklist = build_checklist(profit_data, len(_price_list))

        return {
            "stats": stats,
            "profit": profit_data,
            "checklist": checklist,
        }
    except AvitoAPIError as e:
        raise HTTPException(status_code=502, detail=str(e))


@app.post("/api/price/upload")
async def api_price_upload(file: UploadFile = File(...)):
    """Загрузка прайс-листа (Excel/CSV)."""
    global _price_list, _price_file_path
    ext = Path(file.filename or "").suffix.lower()
    content = await file.read()

    if ext in (".xlsx", ".xls"):
        path = Path("/tmp/avito_price.xlsx")
        path.write_bytes(content)
        _price_list = load_price_list(path)
        _price_file_path = path
    else:
        _price_list = load_price_list(csv_content=content.decode("utf-8-sig"))

    return {"loaded": len(_price_list), "items": _price_list[:20]}


@app.get("/api/price")
def api_price():
    """Текущий прайс."""
    return {"items": _price_list, "count": len(_price_list)}


@app.get("/")
def index():
    return FileResponse(Path(__file__).parent / "static" / "index.html")


# Статика
static_dir = Path(__file__).parent / "static"
if static_dir.exists():
    app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")
