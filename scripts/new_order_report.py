#!/usr/bin/env python3
"""
Отчёт по заказам «New order» из экспорта Telegram-чата.

Использование:
  1. В Telegram Desktop: правый клик по чату (ID 5143855909) → Export chat history → JSON.
  2. Положите result.json в папку проекта или укажите путь.
  3. Запуск: python scripts/new_order_report.py [путь к result.json]

Правила цен:
  - Cursor (не Ultra): до 29.01.2025 = 10$, с 29.01 = по прайсу (22–35$).
  - Cursor Ultra: всегда 100$.
  - Остальные позиции: по оптово-розничному прайсу (файл Оптово-розничный прайс.xlsx).
  - Если в сообщении или ответе указана цена в $ — используется она.
"""

import json
import os
import re
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path

# Путь к прайсу: задайте PRAIS_PATH или положите файл в Работа/Отчёты (или Очёты)
DEFAULT_PRAIS_PATH = os.path.expanduser(
    os.getenv("PRAIS_PATH", "~/Downloads/Работа/Отчёты/Оптово-розничный прайс.xlsx")
)
DEFAULT_PRAIS_PATH_ALT = os.path.expanduser(
    "~/Downloads/Работа/Очёты/Оптово-розничный прайс.xlsx"
)
CURSOR_CUTOFF_DATE = datetime(2025, 1, 29)  # после этой даты Cursor по прайсу
CURSOR_OLD_PRICE_USD = 10
CURSOR_ULTRA_PRICE_USD = 100


def load_prais():
    """Загрузить прайс из Excel. Возвращает dict: название товара -> цена $."""
    try:
        import openpyxl
    except ImportError:
        print("Установите openpyxl: pip install openpyxl")
        sys.exit(1)

    for path in (DEFAULT_PRAIS_PATH, DEFAULT_PRAIS_PATH_ALT):
        if os.path.exists(path):
            break
    else:
        print("Файл прайса не найден. Задайте PRAIS_PATH или положите прайс в ~/Downloads/Работа/Очёты/")
        return {}

    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    prices = {}
    for sheet in wb.worksheets:
        for row in sheet.iter_rows(min_row=2, values_only=True):
            if not row or not row[0]:
                continue
            name = str(row[0]).strip()
            # Колонка "Опт цена / доллары" (индекс 2)
            try:
                val = row[2]
                if val is not None and str(val).strip():
                    prices[name] = float(val)
            except (TypeError, ValueError):
                pass
    return prices


def message_text(msg):
    """Извлечь плоский текст из поля text (строка или массив entity)."""
    t = msg.get("text")
    if t is None:
        return ""
    if isinstance(t, str):
        return t
    if isinstance(t, list):
        parts = []
        for item in t:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict) and "text" in item:
                parts.append(item["text"])
        return "".join(parts)
    return str(t)


def parse_telegram_export(data):
    """
    Извлечь все сообщения из экспорта.
    data — содержимое result.json (dict).
    Возвращает список сообщений: [{"id", "date", "text", "reply_to_message_id"}, ...].
    """
    messages = []
    # Полный экспорт: data["chats"]["list"]; экспорт одного чата: data["messages"]
    if "chats" in data and "list" in data["chats"]:
        for chat in data["chats"]["list"]:
            for msg in chat.get("messages", []):
                if msg.get("type") == "service":
                    continue
                messages.append({
                    "id": msg.get("id"),
                    "date": msg.get("date"),
                    "date_unixtime": msg.get("date_unixtime"),
                    "text": message_text(msg),
                    "reply_to_message_id": msg.get("reply_to_message_id"),
                    "from": msg.get("from"),
                })
    elif "messages" in data:
        for msg in data["messages"]:
            if msg.get("type") == "service":
                continue
            messages.append({
                "id": msg.get("id"),
                "date": msg.get("date"),
                "date_unixtime": msg.get("date_unixtime"),
                "text": message_text(msg),
                "reply_to_message_id": msg.get("reply_to_message_id"),
                "from": msg.get("from"),
            })
    return messages


def parse_message_date(msg):
    """Дата сообщения как datetime."""
    d = msg.get("date") or msg.get("date_unixtime")
    if not d:
        return None
    if isinstance(d, (int, float)):
        return datetime.utcfromtimestamp(d)
    # ISO
    for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M:%S.%f", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(str(d).replace("Z", "").split(".")[0], fmt)
        except ValueError:
            continue
    return None


def is_new_order_message(text):
    if not text:
        return False
    text_lower = text.lower()
    return "новый заказ" in text_lower or "new order" in text_lower or "новый заказ — требует обработки" in text_lower


def is_cancelled(msg, messages_by_id, reply_to_this):
    """Проверить, отменён ли заказ: в тексте сообщения или в ответах есть «отмена»."""
    text = (msg.get("text") or "").lower()
    if "отмена" in text or "отменен" in text or "отменили" in text:
        return True
    for reply in reply_to_this:
        rt = (reply.get("text") or "").lower()
        if "отмена" in rt or "отменен" in rt or "отменили" in rt:
            return True
    return False


def extract_order_id(text):
    """Заказ №`123` или Заказ № 123"""
    m = re.search(r"заказ\s*[№#]?\s*[`]?(\d+)[`]?", text, re.I)
    return m.group(1) if m else None


def extract_products_from_message(text):
    """
    Из блока «Товары:» извлечь строки вида «  • Название × N — XXX₽».
    Возвращает список пар (название_товара, количество).
    """
    products = []
    in_goods = False
    for line in text.split("\n"):
        line = line.strip()
        if "товары" in line.lower() or "товар" in line.lower():
            in_goods = True
            continue
        if in_goods and line.startswith("•"):
            # • Cursor Pro × 1 — 2090₽  или  • Название × 2 — 399₽
            m = re.match(r"•\s*(.+?)\s*[×x]\s*(\d+)\s*—", line, re.I)
            if m:
                name = m.group(1).strip()
                qty = int(m.group(2))
                products.append((name, qty))
        elif in_goods and line and not line.startswith("•"):
            # Конец блока товаров (кнопки и т.д.)
            if "выберите" in line.lower() or "callback" in line.lower():
                break
    return products


def find_manual_price_usd(text):
    """Если в тексте указана цена в долларах (например 22$ или 22 $) — вернуть её."""
    # Ищем числа перед $ или после "долл"
    m = re.search(r"(\d+(?:[.,]\d+)?)\s*\$|\$\s*(\d+(?:[.,]\d+)?)|(\d+(?:[.,]\d+)?)\s*долл", text, re.I)
    if m:
        for g in m.groups():
            if g:
                return float(g.replace(",", "."))
    return None


def match_product_price(product_name, msg_date, prais, manual_usd=None):
    """
    Цена в USD для товара.
    manual_usd — если в переписке указана цена, используем её.
    Cursor: до 29.01.2025 = 10$, Cursor Ultra = 100$, иначе по прайсу.
    """
    if manual_usd is not None:
        return manual_usd
    name_lower = product_name.lower()
    is_ultra = "ultra" in name_lower or "ультра" in name_lower
    is_cursor = "cursor" in name_lower or "курсор" in name_lower
    if is_cursor and is_ultra:
        return CURSOR_ULTRA_PRICE_USD
    if is_cursor and msg_date and msg_date < CURSOR_CUTOFF_DATE:
        return CURSOR_OLD_PRICE_USD
    # Точное совпадение по прайсу
    for key, price in prais.items():
        if key.lower() == product_name.lower():
            return price
    # Частичное: продукт может называться в заказе иначе (например "Ключ Cursor Pro")
    for key, price in prais.items():
        if key.lower() in product_name.lower() or product_name.lower() in key.lower():
            if "cursor" in key.lower() and is_ultra:
                return CURSOR_ULTRA_PRICE_USD
            if "cursor" in key.lower() and msg_date and msg_date < CURSOR_CUTOFF_DATE:
                return CURSOR_OLD_PRICE_USD
            return price
    return None


def main():
    export_path = sys.argv[1] if len(sys.argv) > 1 else None
    if not export_path:
        # Ищем result.json в текущей папке и в корне проекта
        base = Path(__file__).resolve().parent.parent
        for name in ("result.json", "export/result.json", "telegram_export/result.json"):
            p = base / name
            if p.exists():
                export_path = str(p)
                break
        if not export_path:
            print("Использование: python new_order_report.py <путь к result.json>")
            print("Экспорт чата: Telegram Desktop → правый клик по чату → Export chat history → JSON.")
            sys.exit(1)

    with open(export_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    prais = load_prais()
    if not prais:
        print("Прайс пустой — будут использоваться только ручные цены и правила Cursor.")
    else:
        print("Загружен прайс: {} позиций.".format(len(prais)))

    messages = parse_telegram_export(data)
    msg_by_id = {m["id"]: m for m in messages if m.get("id") is not None}

    # Собираем сообщения «New order»
    new_order_messages = [m for m in messages if is_new_order_message(m.get("text") or "")]

    # Ответы на каждое сообщение (reply_to_message_id)
    replies_to = defaultdict(list)
    for m in messages:
        rid = m.get("reply_to_message_id")
        if rid is not None:
            replies_to[rid].append(m)

    orders = []  # {order_id, date, products: [(name, qty, price_usd)], cancelled, manual_price}

    for msg in new_order_messages:
        text = msg.get("text") or ""
        reply_list = replies_to.get(msg["id"], [])
        if is_cancelled(msg, msg_by_id, reply_list):
            continue
        msg_date = parse_message_date(msg)
        order_id = extract_order_id(text) or "?"
        products = extract_products_from_message(text)
        manual_usd = find_manual_price_usd(text)
        for reply in reply_list:
            u = find_manual_price_usd(reply.get("text") or "")
            if u is not None:
                manual_usd = u
        if not products:
            # Может быть одна строка с товаром в другом формате
            if "•" in text:
                for line in text.split("\n"):
                    if "×" in line or "—" in line:
                        m = re.search(r"•\s*(.+?)\s*[×x]\s*(\d+)\s*—", line)
                        if m:
                            products.append((m.group(1).strip(), int(m.group(2))))
            if not products:
                # Пытаемся взять название из заголовка или первой строки
                products.append(("Неизвестный товар", 1))
        for name, qty in products:
            price = match_product_price(name, msg_date, prais, manual_usd)
            if price is None:
                price = 0  # не нашли в прайсе
            orders.append({
                "order_id": order_id,
                "date": msg_date,
                "product": name,
                "qty": qty,
                "price_usd": price,
                "sum_usd": price * qty,
            })

    # Агрегация по позициям и общая сумма
    by_product = defaultdict(lambda: {"count": 0, "sum_usd": 0, "dates": [], "orders": []})
    total_usd = 0
    for o in orders:
        key = o["product"]
        by_product[key]["count"] += o["qty"]
        by_product[key]["sum_usd"] += o["sum_usd"]
        total_usd += o["sum_usd"]
        if o["date"]:
            by_product[key]["dates"].append(o["date"])
        by_product[key]["orders"].append((o["order_id"], o["date"], o["qty"], o["price_usd"], o["sum_usd"]))

    # Вывод отчёта
    print()
    print("=" * 70)
    print("ОТЧЁТ ПО ЗАКАЗАМ «NEW ORDER» (без учёта отменённых)")
    print("=" * 70)
    print("Экспорт: {}".format(export_path))
    print("Всего позиций в заказах: {}".format(len(orders)))
    print("Общая сумма (USD): {:.2f}".format(total_usd))
    print()
    print("-" * 70)
    print("ПО ПОЗИЦИЯМ")
    print("-" * 70)

    for product in sorted(by_product.keys(), key=lambda x: (-by_product[x]["sum_usd"], x)):
        info = by_product[product]
        dates = sorted(set(info["dates"])) if info["dates"] else []
        time_range = ""
        if dates:
            time_range = "{} — {}".format(
                min(dates).strftime("%d.%m.%Y %H:%M"),
                max(dates).strftime("%d.%m.%Y %H:%M"),
            )
        print()
        print("Позиция: {}".format(product))
        print("  Количество: {}".format(info["count"]))
        print("  Сумма (USD): {:.2f}".format(info["sum_usd"]))
        if time_range:
            print("  Период: {}".format(time_range))
        print("  Заказы (№ заказа, дата, кол-во, цена $, сумма $):")
        for order_id, dt, qty, price, sum_u in info["orders"][:20]:
            dt_str = dt.strftime("%d.%m.%Y %H:%M") if dt else "?"
            print("    №{}  {}  ×{}  {} $  = {:.2f} $".format(order_id, dt_str, qty, price, sum_u))
        if len(info["orders"]) > 20:
            print("    ... и ещё {} записей.".format(len(info["orders"]) - 20))

    print()
    print("=" * 70)
    print("ИТОГО (USD): {:.2f}".format(total_usd))
    print("=" * 70)


if __name__ == "__main__":
    main()
