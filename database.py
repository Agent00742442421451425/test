"""
Хранение заказов в JSON-файле (без PostgreSQL).
Файл: orders.json — список заказов.
"""

import json
import logging
import os
import threading
from datetime import datetime

log = logging.getLogger(__name__)

# Путь к файлу заказов (рядом с bot.py)
_ORDERS_FILE = os.path.join(os.path.dirname(__file__), "orders.json")
_lock = threading.Lock()

_DATE_FORMATS = ["%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S", "%d-%m-%Y %H:%M:%S"]


def _parse_date(s):
    """Парсинг даты из строки. Возвращает datetime или None."""
    if not s:
        return None
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    return None


def _serialize_order(o):
    """Привести заказ к виду для отдачи (даты и total — как в старом API)."""
    out = dict(o)
    if out.get("created_at") and hasattr(out["created_at"], "strftime"):
        out["created_at"] = out["created_at"].strftime("%Y-%m-%d %H:%M:%S")
    if not out.get("created_at"):
        out["created_at"] = ""
    if out.get("delivered_at") and hasattr(out["delivered_at"], "strftime"):
        out["delivered_at"] = out["delivered_at"].strftime("%Y-%m-%d %H:%M:%S")
    if not out.get("delivered_at"):
        out["delivered_at"] = ""
    if "total" in out and out["total"] is not None:
        try:
            out["total"] = float(out["total"])
        except (TypeError, ValueError):
            out["total"] = 0
    return out


def _load_orders():
    """Загрузить список заказов из файла."""
    with _lock:
        if not os.path.exists(_ORDERS_FILE):
            return []
        try:
            with open(_ORDERS_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                return data.get("orders", [])
        except (json.JSONDecodeError, IOError) as e:
            log.warning(f"Ошибка чтения {_ORDERS_FILE}: {e}")
            return []


def _order_to_json(o):
    """Подготовить заказ для записи в JSON (datetime → строка)."""
    out = dict(o)
    for key in ("created_at", "delivered_at"):
        val = out.get(key)
        if val and hasattr(val, "strftime"):
            out[key] = val.strftime("%Y-%m-%d %H:%M:%S")
    return out


def _save_orders(orders):
    """Сохранить список заказов в файл."""
    with _lock:
        payload = [_order_to_json(o) for o in orders]
        with open(_ORDERS_FILE, "w", encoding="utf-8") as f:
            json.dump({"orders": payload}, f, indent=2, ensure_ascii=False)


def init_db():
    """Создать файл заказов, если не существует."""
    if not os.path.exists(_ORDERS_FILE):
        _save_orders([])
        log.info("Файл заказов создан: %s", _ORDERS_FILE)
    else:
        log.debug("Файл заказов уже существует")


def save_order(order_id, status="PROCESSING", substatus="", our_status="НОВЫЙ",
               product="", buyer_name="", total=0, created_at="",
               delivered_at="", account_login="", delivery_type="",
               notes=""):
    """
    Сохранить/обновить заказ.
    Если заказ уже есть — обновляет поля. Если нет — добавляет.
    """
    init_db()
    orders = _load_orders()
    created_at_ts = _parse_date(created_at)
    delivered_at_ts = _parse_date(delivered_at)

    # Ищем существующий заказ
    found = None
    for i, o in enumerate(orders):
        if o.get("order_id") == order_id:
            found = i
            break

    order_row = {
        "order_id": order_id,
        "status": status or "PROCESSING",
        "substatus": substatus or "",
        "our_status": our_status or "НОВЫЙ",
        "product": product or "",
        "buyer_name": buyer_name or "",
        "total": total if total is not None else 0,
        "created_at": created_at_ts,
        "delivered_at": delivered_at_ts,
        "account_login": account_login or "",
        "delivery_type": delivery_type or "",
        "notes": notes or "",
    }

    if found is not None:
        existing = orders[found]
        if status:
            existing["status"] = status
        if substatus is not None:
            existing["substatus"] = substatus
        if our_status:
            existing["our_status"] = our_status
        if product:
            existing["product"] = product
        if buyer_name:
            existing["buyer_name"] = buyer_name
        if total:
            existing["total"] = total
        if delivered_at_ts:
            existing["delivered_at"] = delivered_at_ts
        if account_login is not None:
            existing["account_login"] = account_login
        if delivery_type:
            existing["delivery_type"] = delivery_type
        if notes is not None:
            existing["notes"] = notes
    else:
        order_row["created_at"] = created_at_ts
        orders.append(order_row)

    _save_orders(orders)
    log.info(f"Заказ {order_id}: сохранён в JSON ({our_status})")


def update_order_status(order_id, status=None, substatus=None, our_status=None,
                        account_login=None, delivered_at=None, notes=None):
    """Обновить поля заказа."""
    init_db()
    orders = _load_orders()
    delivered_at_ts = _parse_date(delivered_at) if delivered_at else None

    for o in orders:
        if o.get("order_id") == order_id:
            if status is not None:
                o["status"] = status
            if substatus is not None:
                o["substatus"] = substatus
            if our_status is not None:
                o["our_status"] = our_status
            if account_login is not None:
                o["account_login"] = account_login
            if delivered_at_ts is not None:
                o["delivered_at"] = delivered_at_ts
            if notes is not None:
                o["notes"] = notes
            _save_orders(orders)
            return
    log.warning(f"Заказ {order_id} не найден для update_order_status")


def get_order_from_db(order_id):
    """Получить заказ по ID. Возвращает dict или None."""
    orders = _load_orders()
    for o in orders:
        if o.get("order_id") == order_id:
            return _serialize_order(o)
    return None


def get_all_orders(limit=100, offset=0):
    """
    Получить заказы, отсортированные по дате (новые сверху).
    Возвращает список словарей в том же формате, что и раньше.
    """
    orders = _load_orders()
    # Сортировка по created_at (новые первые)
    def sort_key(o):
        ct = o.get("created_at")
        if ct is None:
            return datetime.min
        if isinstance(ct, str):
            ct = _parse_date(ct) or datetime.min
        return ct

    orders = sorted(orders, key=sort_key, reverse=True)
    page = orders[offset:offset + limit]
    return [_serialize_order(o) for o in page]


def get_orders_count():
    """Общее количество заказов."""
    return len(_load_orders())
