"""
Хранение и синхронизация списка товаров магазина с Яндекс Маркетом.
Файл products.json — кэш товаров (sku + название) для выбора при пополнении склада.
Список подтягивается из API Маркета, менять код при добавлении нового товара не нужно.
"""

import json
import logging
import os
from datetime import datetime

log = logging.getLogger(__name__)

_PRODUCTS_FILE = os.path.join(os.path.dirname(__file__), "products.json")


def load_products():
    """
    Загрузить список товаров из файла.
    Возвращает список словарей [{"sku": "...", "name": "..."}, ...].
    """
    if not os.path.exists(_PRODUCTS_FILE):
        return []
    try:
        with open(_PRODUCTS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            return data.get("products", [])
    except (json.JSONDecodeError, IOError) as e:
        log.warning(f"Ошибка чтения {_PRODUCTS_FILE}: {e}")
        return []


def save_products(products):
    """Сохранить список товаров в файл."""
    payload = {
        "products": products,
        "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }
    with open(_PRODUCTS_FILE, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, ensure_ascii=False)
    log.info(f"Сохранено товаров: {len(products)} в {_PRODUCTS_FILE}")


def sync_products_from_yandex():
    """
    Синхронизировать список товаров из каталога Яндекс Маркета.
    Возвращает (list[dict], error_message).
    При успехе error_message пустая строка.
    """
    try:
        from yandex_api import YandexMarketAPI
        with YandexMarketAPI() as api:
            products = api.get_all_campaign_products()
        if not products:
            return [], "В каталоге Маркета нет товаров или API не вернул данные."
        save_products(products)
        return products, ""
    except Exception as e:
        log.exception("Ошибка синхронизации товаров с Маркетом")
        return [], str(e)
