"""
Telegram-бот для управления заказами Яндекс Маркет (DBS).
Запуск: python bot.py
"""

import asyncio
import json
import logging
import os
from datetime import datetime

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.error import BadRequest
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    filters,
    ContextTypes,
)

from config import TELEGRAM_BOT_TOKEN, TELEGRAM_GROUP_ID, ADMIN_IDS
from yandex_api import YandexMarketAPI
import database as db

# Логирование
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# Хранилище уже обработанных заказов (для уведомлений)
known_order_ids = set()


def is_admin(update: Update) -> bool:
    """Проверить, является ли пользователь администратором."""
    user_id = update.effective_user.id if update.effective_user else None
    return user_id in ADMIN_IDS


async def safe_edit_message(query, text, reply_markup=None, parse_mode="Markdown"):
    """
    Безопасно редактирует сообщение, игнорируя ошибку "Message is not modified".

    Игнорирует ошибку, когда содержимое сообщения не изменилось.
    Логирует другие ошибки.
    """
    try:
        await query.edit_message_text(
            text=text,
            reply_markup=reply_markup,
            parse_mode=parse_mode,
        )
    except BadRequest as e:
        if "Message is not modified" in str(e):
            logger.debug(f"Message not modified for query {query.id}. Ignoring.")
        else:
            logger.error(f"Ошибка редактирования сообщения для query {query.id}: {e}")
    except Exception as e:
        logger.error(f"Неизвестная ошибка редактирования сообщения для query {query.id}: {e}")

# Путь к файлу склада аккаунтов
ACCOUNTS_FILE = os.path.join(os.path.dirname(__file__), "accounts.json")


# ─── Работа со складом аккаунтов ────────────────────────────────────

def load_accounts():
    """Загрузить аккаунты из файла."""
    with open(ACCOUNTS_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def save_accounts(data):
    """Сохранить аккаунты в файл."""
    with open(ACCOUNTS_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def get_available_account(sku=None):
    """
    Получить первый свободный аккаунт со склада.
    Если указан sku — ищет по конкретному товару.
    """
    data = load_accounts()
    for acc in data["accounts"]:
        if acc.get("used", False):
            continue
        if sku and acc.get("sku") != sku:
            continue
        return acc
    return None


def get_stock_count_by_sku(sku=None):
    """
    Получить количество свободных аккаунтов по SKU.
    Если sku не указан, возвращает словарь {sku: count} для всех товаров.
    """
    data = load_accounts()
    if sku:
        # Подсчет для конкретного SKU
        count = sum(1 for acc in data["accounts"] 
                   if not acc.get("used", False) and acc.get("sku") == sku)
        return count
    else:
        # Подсчет для всех SKU
        stock = {}
        for acc in data["accounts"]:
            if not acc.get("used", False):
                acc_sku = acc.get("sku", "")
                if acc_sku:
                    stock[acc_sku] = stock.get(acc_sku, 0) + 1
        return stock


def mark_account_used(login):
    """Пометить аккаунт как использованный и синхронизировать остатки."""
    data = load_accounts()
    old_sku = None
    for acc in data["accounts"]:
        if acc["login"] == login:
            old_sku = acc.get("sku")
            acc["used"] = True
            break
    save_accounts(data)
    
    # Синхронизируем остатки в Яндекс Маркете
    if old_sku:
        try:
            sync_stock_to_yandex(old_sku)
        except Exception as e:
            logger.warning(f"Ошибка синхронизации остатков после использования аккаунта {login}: {e}")


def build_account_slip(account, product_name):
    """Собрать сообщение с данными аккаунта для покупателя (plain text для чата Маркета)."""
    text = (
        f"✅ Данные для доступа\n\n"
        f"📦 Товар: {product_name}\n\n"
        f"🔑 Данные для входа:\n"
        f"Логин: {account['login']}\n"
        f"Пароль: {account['password']}\n"
    )
    if account.get("2fa"):
        text += f"2FA: {account['2fa']}\n"

    text += (
        f"\n📋 Инструкция:\n"
        f"1. Скопируйте данные выше\n"
        f"2. Войдите в аккаунт\n"
        f"3. При необходимости смените пароль\n\n"
        f"⚠️ Важно:\n"
        f"• Не передавайте данные третьим лицам\n"
        f"• Смените пароль после первого входа\n"
        f"• Сохраните данные в надежном месте\n\n"
        f"🎉 Спасибо за покупку!\n"
        f"Если возникнут вопросы — напишите в чат."
    )
    return text


def sync_stock_to_yandex(sku=None):
    """
    Синхронизировать остатки товаров со складом аккаунтов в Яндекс Маркет.
    Если sku указан, обновляет только этот товар.
    Иначе обновляет все товары.
    """
    try:
        with YandexMarketAPI() as api:
            if sku:
                # Обновляем один товар
                count = get_stock_count_by_sku(sku)
                if count > 0:
                    api.update_offer_stock(sku, count)
                    logger.info(f"✅ Синхронизирован остаток: SKU {sku} → {count}")
                else:
                    # Если остаток 0, все равно обновляем, чтобы Яндекс Маркет знал
                    api.update_offer_stock(sku, 0)
                    logger.info(f"✅ Синхронизирован остаток: SKU {sku} → 0 (нет на складе)")
            else:
                # Обновляем все товары
                stock_counts = get_stock_count_by_sku()
                if stock_counts:
                    api.update_multiple_offers_stock(stock_counts)
                    logger.info(f"✅ Синхронизированы остатки: {len(stock_counts)} товаров")
                    for sku_item, count in stock_counts.items():
                        logger.info(f"  • SKU {sku_item}: {count}")
                else:
                    logger.warning("Нет товаров для синхронизации остатков")
    except Exception as e:
        logger.error(f"Ошибка синхронизации остатков с Яндекс Маркетом: {e}")
        # Не пробрасываем исключение, чтобы не ломать основной процесс
        logger.warning(f"Синхронизация остатков пропущена из-за ошибки: {e}")


def build_support_message():
    """Сообщение для ручной обработки — отправка клиента в чат поддержки."""
    return (
        "👋 Здравствуйте!\n\n"
        "Обратитесь к нам в службу поддержки на Яндекс Маркете.\n"
        "Оформим подписку внутри чата, выдадим инструкцию, гайд и аккаунт.\n\n"
        "⏰ Ждём вашего обращения с 10:00 по 23:00.\n\n"
        "Спасибо за покупку! 🙏"
    )


# ─── Главное меню ────────────────────────────────────────────────────

def main_menu_keyboard():
    """Клавиатура главного меню."""
    keyboard = [
        [InlineKeyboardButton("📦 Новые заказы", callback_data="orders_new")],
        [InlineKeyboardButton("📋 Все заказы", callback_data="orders_all")],
        [InlineKeyboardButton("🔄 Проверить заказ по ID", callback_data="order_check")],
        [InlineKeyboardButton("📦 Склад аккаунтов", callback_data="stock_info")],
        [InlineKeyboardButton("➕ Добавить аккаунты", callback_data="add_accounts")],
        [InlineKeyboardButton("🔄 Синхронизировать остатки", callback_data="sync_stock")],
    ]
    return InlineKeyboardMarkup(keyboard)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик /start — показать главное меню (только админу)."""
    if not is_admin(update):
        logger.warning(
            f"Неизвестный пользователь {update.effective_user.id} "
            f"(@{update.effective_user.username}) попытался использовать /start"
        )
        await update.message.reply_text("⛔ Доступ запрещён.")
        return

    await update.message.reply_text(
        "🟢 *Яндекс Маркет DBS Бот*\n\n"
        "Управление заказами магазина\n"
        "«Склад Ai Hub»\n\n"
        f"👤 Админ: `{update.effective_user.id}`\n\n"
        "Выберите действие:",
        reply_markup=main_menu_keyboard(),
        parse_mode="Markdown",
    )


async def menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик /menu — показать главное меню (только админу)."""
    if not is_admin(update):
        await update.message.reply_text("⛔ Доступ запрещён.")
        return

    await update.message.reply_text(
        "📌 *Главное меню*\n\nВыберите действие:",
        reply_markup=main_menu_keyboard(),
        parse_mode="Markdown",
    )


# ─── Обработка кнопок ───────────────────────────────────────────────

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик нажатий на inline-кнопки (только админ)."""
    query = update.callback_query

    if not is_admin(update):
            await query.answer("⛔ Доступ запрещён", show_alert=True)
        return

        await query.answer()

    data = query.data

    if data == "orders_new":
        await show_orders(query, status="PROCESSING")
    elif data == "orders_all":
        await show_orders(query, status=None)
    elif data == "orders_history":
        await show_orders_history(query)
    elif data.startswith("orders_history_page_"):
        page = int(data.replace("orders_history_page_", ""))
        await show_orders_history(query, page=page)
    elif data == "order_check":
        await safe_edit_message(
            query,
            "🔍 Отправьте ID заказа командой:\n"
            "`/order 54172200065`",
        )
    elif data == "stock_info":
        await show_stock_info(query)
    elif data == "sync_stock":
        await sync_stock_handler(query)
    elif data.startswith("order_detail_"):
        order_id = int(data.replace("order_detail_", ""))
        await show_order_detail(query, order_id)
    elif data.startswith("auto_deliver_"):
        order_id = int(data.replace("auto_deliver_", ""))
        await auto_deliver_account(query, order_id)
    elif data.startswith("manual_process_"):
        order_id = int(data.replace("manual_process_", ""))
        await manual_process_order(query, order_id, context)
    elif data.startswith("order_confirm_"):
        order_id = int(data.replace("order_confirm_", ""))
        await confirm_order(query, order_id)
    elif data.startswith("force_delivered_"):
        order_id = int(data.replace("force_delivered_", ""))
        await force_update_to_delivered(query, order_id)
    elif data == "add_accounts":
        await start_add_accounts(query, context)
    elif data == "back_menu":
        # Сбрасываем режим добавления аккаунтов при возврате в меню
        context.user_data.pop("awaiting_accounts", None)
        await safe_edit_message(
            query,
            "📌 *Главное меню*\n\nВыберите действие:",
            reply_markup=main_menu_keyboard(),
        )


# ─── Просмотр заказов ───────────────────────────────────────────────

async def show_orders(query, status=None):
    """Показать список заказов."""
    try:
        with YandexMarketAPI() as api:
            data = api.get_orders(status=status)

        orders = data.get("orders", [])
        total = data.get("pager", {}).get("total", 0)

        if not orders:
            status_text = f" (статус: {status})" if status else ""
            await safe_edit_message(
                query,
                f"📭 Заказов{status_text} не найдено.\n\n"
                f"Всего в системе: {total}",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("⬅️ Назад", callback_data="back_menu")]
                ]),
            )
            return

        text = f"📦 *Заказы* (найдено: {total})\n\n"
        keyboard = []

        for order in orders[:10]:
            oid = order["id"]
            order_status = order.get("status", "?")
            substatus = order.get("substatus", "")
            total_price = order.get("buyerTotal", 0)
            date = order.get("creationDate", "")

            text += (
                f"• `{oid}` — {total_price}₽\n"
                f"  Статус: {order_status}/{substatus}\n"
                f"  Дата: {date}\n\n"
            )
            keyboard.append([
                InlineKeyboardButton(
                    f"📋 Заказ {oid}", callback_data=f"order_detail_{oid}"
                )
            ])

        keyboard.append([InlineKeyboardButton("⬅️ Назад", callback_data="back_menu")])

        await safe_edit_message(
            query,
            text,
            reply_markup=InlineKeyboardMarkup(keyboard),
        )

    except Exception as e:
        logger.error(f"Ошибка получения заказов: {e}")
        await safe_edit_message(
            query,
            f"❌ Ошибка: {e}",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("⬅️ Назад", callback_data="back_menu")]
            ]),
        )


# ─── История заказов (из БД) ──────────────────────────────────────────

async def show_orders_history(query, page=1):
    """Показать историю всех заказов из БД с inline кнопками."""
    try:
        per_page = 10
        offset = (page - 1) * per_page
        orders = db.get_all_orders(limit=per_page, offset=offset)
        total_count = db.get_orders_count()
        total_pages = (total_count + per_page - 1) // per_page if total_count > 0 else 1
        page = max(1, min(page, total_pages))

        if not orders:
            await safe_edit_message(
                query,
                "📭 *История заказов пуста*\n\n"
                "Заказы будут записываться сюда автоматически при обработке.",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("⬅️ Назад", callback_data="back_menu")]
                ]),
            )
            return

        text = f"📊 *История заказов (БД)*\n\n"
        text += f"Страница {page} из {total_pages}\n"
        text += f"Всего заказов: {total_count}\n\n"

        keyboard = []

        for order in orders:
            oid = order["order_id"]
            status = order.get("status", "?")
            substatus = order.get("substatus", "")
            our_status = order.get("our_status", "НОВЫЙ")
            total_price = order.get("total", 0)
            date = order.get("created_at", "")

            status_emoji = {
                "НОВЫЙ": "🆕",
                "ВЫДАН": "✅",
                "ОШИБКА": "❌",
                "РУЧНАЯ": "👨‍💼",
            }.get(our_status, "📦")

            text += (
                f"{status_emoji} `{oid}` — {total_price}₽\n"
                f"   Статус: {status}/{substatus}\n"
                f"   Дата: {date}\n\n"
            )
            keyboard.append([
                InlineKeyboardButton(
                    f"📋 Заказ {oid}", callback_data=f"order_detail_{oid}"
                )
            ])

        # Кнопки навигации
        nav_buttons = []
        if page > 1:
            nav_buttons.append(InlineKeyboardButton("◀️ Назад", callback_data=f"orders_history_page_{page - 1}"))
        if page < total_pages:
            nav_buttons.append(InlineKeyboardButton("Вперёд ▶️", callback_data=f"orders_history_page_{page + 1}"))
        if nav_buttons:
            keyboard.append(nav_buttons)

        keyboard.append([InlineKeyboardButton("⬅️ Назад", callback_data="back_menu")])

        await safe_edit_message(
            query,
            text,
            reply_markup=InlineKeyboardMarkup(keyboard),
        )

    except Exception as e:
        logger.error(f"Ошибка получения истории заказов: {e}")
        await safe_edit_message(
            query,
            f"❌ Ошибка: {e}",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("⬅️ Назад", callback_data="back_menu")]
            ]),
        )


# ─── Детали заказа ───────────────────────────────────────────────────

async def show_order_detail(query, order_id):
    """Показать детали заказа с кнопками обработки."""
    try:
        with YandexMarketAPI() as api:
            data = api.get_order(order_id)

        order = data.get("order", {})
        items = order.get("items", [])
        buyer = order.get("buyer", {})
        delivery = order.get("delivery", {})

        items_text = ""
        for item in items:
            items_text += f"  • {item.get('offerName', '?')} × {item.get('count', 1)} — {item.get('buyerPrice', 0)}₽\n"

        text = (
            f"📦 *Заказ №{order_id}*\n\n"
            f"💰 Сумма: {order.get('buyerTotal', 0)}₽\n"
            f"📊 Статус: `{order.get('status', '?')}/{order.get('substatus', '?')}`\n"
            f"📅 Создан: {order.get('creationDate', '?')}\n"
            f"💳 Оплата: {order.get('paymentType', '?')}\n"
            f"🚚 Доставка: {delivery.get('type', '?')}\n\n"
            f"👤 Покупатель: {buyer.get('firstName', '')} {buyer.get('lastName', '')}\n\n"
            f"🛒 *Товары:*\n{items_text}"
        )

        # Сохраняем заказ в БД
        try:
            buyer_name = f"{buyer.get('firstName', '')} {buyer.get('lastName', '')}".strip()
            product_name = items[0].get("offerName", "") if items else ""
            db.save_order(
                order_id=order_id,
                status=order.get("status", "PROCESSING"),
                substatus=order.get("substatus", ""),
                our_status="НОВЫЙ",
                product=product_name,
                buyer_name=buyer_name,
                total=order.get("buyerTotal", 0),
                created_at=order.get("creationDate", ""),
                delivery_type=delivery.get("type", ""),
            )
        except Exception as e:
            logger.warning(f"Ошибка сохранения заказа {order_id} в БД: {e}")

        keyboard = []
        status = order.get("status", "")
        substatus = order.get("substatus", "")

        if status == "PROCESSING":
            # Кнопки обработки заказа
            keyboard.append([
                InlineKeyboardButton(
                    "🔑 Выдать аккаунт (авто)",
                    callback_data=f"auto_deliver_{order_id}",
                )
            ])
            keyboard.append([
                InlineKeyboardButton(
                    "👨‍💼 Ручная обработка (менеджер)",
                    callback_data=f"manual_process_{order_id}",
                )
            ])
            keyboard.append([
                InlineKeyboardButton(
                    "✅ Подтвердить передачу",
                    callback_data=f"order_confirm_{order_id}",
                )
            ])
            # Кнопка для принудительного обновления статуса (если заказ в READY_TO_SHIP)
            if substatus == "READY_TO_SHIP":
                keyboard.append([
                    InlineKeyboardButton(
                        "🔄 Обновить статус до DELIVERED",
                        callback_data=f"force_delivered_{order_id}",
                )
            ])

        keyboard.append([InlineKeyboardButton("⬅️ Назад", callback_data="back_menu")])

        await safe_edit_message(
            query,
            text,
            reply_markup=InlineKeyboardMarkup(keyboard),
        )

    except Exception as e:
        logger.error(f"Ошибка получения заказа {order_id}: {e}")
        await safe_edit_message(
            query,
            f"❌ Ошибка: {e}",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("⬅️ Назад", callback_data="back_menu")]
            ]),
        )


# ─── Общая логика выдачи аккаунта ────────────────────────────────────

def _do_deliver(api, order_id, order=None):
    """
    Внутренняя функция выдачи: берёт аккаунт, отправляет покупателю,
    проводит статусную цепочку DBS:
      PROCESSING → READY_TO_SHIP → boxes → DELIVERY → DELIVERED
    Возвращает (success: bool, report: str, account: dict | None).
    """
    if order is None:
        order_data = api.get_order(order_id)
        order = order_data.get("order", {})

    items = order.get("items", [])
    if not items:
        return False, "В заказе нет товаров", None

    sku = items[0].get("shopSku", "")
    product_name = items[0].get("offerName", "Товар")

    # 1. Берём аккаунт со склада
    account = get_available_account(sku=sku)
    if not account:
        account = get_available_account()  # любой свободный
    if not account:
        return False, f"Склад пуст! Нет аккаунтов для «{product_name}»", None

    # 2. Отправляем данные покупателю в чат Маркета
    slip = build_account_slip(account, product_name)
    chat_sent = False
    try:
        api.send_message_to_buyer(order_id, slip)
        chat_sent = True
    except Exception as e:
        logger.warning(
            f"Чат недоступен для заказа {order_id}: {e} — "
            "продолжаем без чата. Проверьте права API-ключа на «Чаты»."
        )

    # 3. Помечаем аккаунт использованным
    mark_account_used(account["login"])

    # 4. Полная цепочка DBS: READY_TO_SHIP → boxes → DELIVERY → DELIVERED
    status_results = api.deliver_digital_order(order_id)
    status_report = "\n".join(f"  • {s}: {r}" for s, r in status_results)
    
    # 5. Сохраняем заказ в БД с обновленным статусом
    try:
        buyer = order.get("buyer", {})
        buyer_name = f"{buyer.get('firstName', '')} {buyer.get('lastName', '')}".strip()
        final_status = "DELIVERED" if (any(step == "DELIVERED" and result == "OK" for step, result in status_results) or 
                                       any(step == "DELIVERED" and "уже" in result for step, result in status_results)) else order.get("status", "PROCESSING")
        db.save_order(
            order_id=order_id,
            status=final_status,
            substatus=order.get("substatus", ""),
        our_status="ВЫДАН",
            product=product_name,
            buyer_name=buyer_name,
            total=order.get("buyerTotal", 0),
            created_at=order.get("creationDate", ""),
            delivered_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        account_login=account["login"],
            delivery_type=order.get("delivery", {}).get("type", ""),
        )
    except Exception as e:
        logger.warning(f"Ошибка сохранения заказа {order_id} в БД после выдачи: {e}")

    # Проверяем, дошли ли до DELIVERED
    delivered_ok = any(
        step == "DELIVERED" and result == "OK"
        for step, result in status_results
    )
    already_delivered = any(
        step == "DELIVERED" and "уже" in result
        for step, result in status_results
    )

    if chat_sent:
        chat_status = "✅ Отправлено в чат покупателю"
    else:
        chat_status = "⚠️ Чат недоступен — данные только в Telegram"

    delivery_emoji = "✅" if (delivered_ok or already_delivered) else "⏳"
    report = (
        f"📦 Заказ: {order_id}\n"
        f"🛒 Товар: {product_name}\n"
        f"🔑 Логин: {account['login']}\n"
        f"📨 {chat_status}\n"
        f"{delivery_emoji} Доставка: {'DELIVERED' if (delivered_ok or already_delivered) else 'в процессе'}\n\n"
        f"📊 Обработка:\n{status_report}"
    )
    return True, report, account


# ─── Авто-выдача аккаунта (по кнопке) ───────────────────────────────

async def auto_deliver_account(query, order_id):
    """Выдача аккаунта покупателю через чат Маркета + полная цепочка статусов."""
    try:
        with YandexMarketAPI() as api:
            ok, report, account = await asyncio.to_thread(_do_deliver, api, order_id)

        if not ok:
            await safe_edit_message(
                query,
                f"❌ *Не удалось выдать аккаунт*\n\n{report}",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("👨‍💼 Ручная обработка", callback_data=f"manual_process_{order_id}")],
                    [InlineKeyboardButton("⬅️ Назад", callback_data="back_menu")],
                ]),
            )
            return

        await safe_edit_message(
            query,
            f"✅ *Аккаунт выдан и заказ доставлен!*\n\n{report}",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("📋 Детали заказа", callback_data=f"order_detail_{order_id}")],
                [InlineKeyboardButton("⬅️ Назад", callback_data="back_menu")],
            ]),
        )

        # Уведомление в группу (ЛС админу уже получил ответ через query)
            if TELEGRAM_GROUP_ID:
                try:
                await query.get_bot().send_message(
                        chat_id=TELEGRAM_GROUP_ID,
                    text=f"✅ *Аккаунт выдан (кнопка)*\n\n{report}",
                    parse_mode="Markdown",
                    )
                except Exception as e:
                    logger.error(f"Ошибка уведомления в группу: {e}")

    except Exception as e:
        logger.error(f"Ошибка авто-выдачи для заказа {order_id}: {e}")
        await safe_edit_message(
            query,
            f"❌ Ошибка: {e}\n\nПопробуйте ручную обработку.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("👨‍💼 Ручная обработка", callback_data=f"manual_process_{order_id}")],
                [InlineKeyboardButton("⬅️ Назад", callback_data="back_menu")],
            ]),
        )


# ─── Ручная обработка (менеджер) ────────────────────────────────────

async def manual_process_order(query, order_id, context):
    """Ручная обработка — запросить данные аккаунта у менеджера."""
    try:
        # Получаем информацию о заказе для отображения
        with YandexMarketAPI() as api:
            order_data = api.get_order(order_id)
            order = order_data.get("order", {})
            items = order.get("items", [])
            product_name = items[0].get("offerName", "Товар") if items else "Товар"

        # Сохраняем order_id в bot_data для обработки следующего сообщения
        user_id = query.from_user.id
        if "manual_orders" not in context.bot_data:
            context.bot_data["manual_orders"] = {}
        context.bot_data["manual_orders"][user_id] = order_id

        await safe_edit_message(
            query,
            f"👨‍💼 *Ручная обработка заказа*\n\n"
            f"📦 Заказ: `{order_id}`\n"
            f"🛒 Товар: {product_name}\n\n"
            f"📝 *Введите данные аккаунта в формате:*\n\n"
            f"`логин ; пароль ; 2fa`\n\n"
            f"*Примеры:*\n"
            f"`user@gmail.com ; Pass123!`\n"
            f"`user@mail.ru ; Pass456! ; BACKUP-CODE`\n\n"
            f"• Разделитель — точка с запятой `;`\n"
            f"• 2FA — необязательно\n"
            f"• После отправки данные будут переданы клиенту автоматически",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("❌ Отмена", callback_data=f"order_detail_{order_id}")],
            ]),
        )

    except Exception as e:
        logger.error(f"Ошибка ручной обработки {order_id}: {e}")
        await safe_edit_message(
            query,
            f"❌ Ошибка: {e}",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("⬅️ Назад", callback_data="back_menu")]
            ]),
        )


# ─── Принудительное обновление статуса до DELIVERED ───────────────────

async def force_update_to_delivered(query, order_id):
    """Принудительно обновить статус заказа до DELIVERED."""
    try:
        await safe_edit_message(
            query,
            f"🔄 *Обновление статуса заказа*\n\n"
            f"📦 Заказ: `{order_id}`\n"
            f"⏳ Пытаюсь перевести в DELIVERED...",
        )

        with YandexMarketAPI() as api:
            # Пытаемся перевести заказ в DELIVERED
            status_results = api.deliver_digital_order(order_id)
            status_report = "\n".join(f"  • {s}: {r}" for s, r in status_results)

            # Проверяем текущий статус
            order_data = api.get_order(order_id)
            order = order_data.get("order", {})
            final_status = order.get("status", "")
            final_sub = order.get("substatus", "")

            # Проверяем успешность
            delivered_ok = any(
                step == "DELIVERED" and result == "OK"
                for step, result in status_results
            )
            already_delivered = any(
                step == "DELIVERED" and "уже" in result
                for step, result in status_results
            )

            if final_status == "DELIVERED" or delivered_ok or already_delivered:
                result_text = (
                    f"✅ *Статус обновлён!*\n\n"
                    f"📦 Заказ: `{order_id}`\n"
                    f"📊 Статус: `DELIVERED`\n\n"
                    f"📋 *Детали обработки:*\n{status_report}"
                )
            else:
                result_text = (
                    f"⚠️ *Статус не обновлён*\n\n"
                    f"📦 Заказ: `{order_id}`\n"
                    f"📊 Текущий статус: `{final_status}/{final_sub}`\n\n"
                    f"📋 *Попытки обновления:*\n{status_report}\n\n"
                    f"Попробуйте ещё раз или обратитесь в поддержку."
                )

            await safe_edit_message(
                query,
                result_text,
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("📋 Детали заказа", callback_data=f"order_detail_{order_id}")],
                    [InlineKeyboardButton("🔄 Повторить", callback_data=f"force_delivered_{order_id}")],
                    [InlineKeyboardButton("⬅️ Назад", callback_data="back_menu")],
                ]),
            )

    except Exception as e:
        logger.error(f"Ошибка принудительного обновления статуса заказа {order_id}: {e}")
        await safe_edit_message(
            query,
            f"❌ *Ошибка обновления статуса*\n\n"
            f"Ошибка: `{str(e)[:200]}`\n\n"
            f"Попробуйте ещё раз.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔄 Повторить", callback_data=f"force_delivered_{order_id}")],
                [InlineKeyboardButton("📋 Детали заказа", callback_data=f"order_detail_{order_id}")],
                [InlineKeyboardButton("⬅️ Назад", callback_data="back_menu")],
            ]),
        )


# ─── Подтверждение заказа ────────────────────────────────────────────

async def confirm_order(query, order_id):
    """Подтвердить передачу заказа в доставку."""
    try:
        with YandexMarketAPI() as api:
            result = api.update_order_status(order_id, "PROCESSING", "READY_TO_SHIP")

        await safe_edit_message(
            query,
            f"✅ Заказ №{order_id} подтверждён!\n"
            f"Статус изменён на READY\\_TO\\_SHIP",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("📋 Детали заказа", callback_data=f"order_detail_{order_id}")],
                [InlineKeyboardButton("⬅️ Назад", callback_data="back_menu")],
            ]),
            )
        except Exception as e:
        logger.error(f"Ошибка подтверждения заказа {order_id}: {e}")
        await safe_edit_message(
            query,
            f"❌ Ошибка подтверждения: {e}",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("⬅️ Назад", callback_data="back_menu")]
            ]),
        )


# ─── Команда /order ─────────────────────────────────────────────────

async def order_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик /order <id> (только админ)."""
    if not is_admin(update):
        await update.message.reply_text("⛔ Доступ запрещён.")
        return

    if not context.args:
        await update.message.reply_text("Использование: `/order 54172200065`", parse_mode="Markdown")
        return

    try:
        order_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text("❌ ID заказа должен быть числом")
        return

    try:
        with YandexMarketAPI() as api:
            data = api.get_order(order_id)

        order = data.get("order", {})
        items = order.get("items", [])
        buyer = order.get("buyer", {})

        items_text = ""
        for item in items:
            items_text += f"  • {item.get('offerName', '?')} × {item.get('count', 1)} — {item.get('buyerPrice', 0)}₽\n"

        text = (
            f"📦 *Заказ №{order_id}*\n\n"
            f"💰 Сумма: {order.get('buyerTotal', 0)}₽\n"
            f"📊 Статус: `{order.get('status', '?')}/{order.get('substatus', '?')}`\n"
            f"📅 Создан: {order.get('creationDate', '?')}\n"
            f"💳 Оплата: {order.get('paymentType', '?')}\n\n"
            f"👤 Покупатель: {buyer.get('firstName', '')} {buyer.get('lastName', '')}\n\n"
            f"🛒 *Товары:*\n{items_text}"
        )

        keyboard = []
        if order.get("status") == "PROCESSING":
            keyboard.append([
                InlineKeyboardButton("🔑 Выдать аккаунт", callback_data=f"auto_deliver_{order_id}")
            ])
            keyboard.append([
                InlineKeyboardButton("👨‍💼 Ручная обработка", callback_data=f"manual_process_{order_id}")
            ])
        keyboard.append([InlineKeyboardButton("📌 Меню", callback_data="back_menu")])

        await update.message.reply_text(
            text,
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="Markdown",
        )

    except Exception as e:
        await update.message.reply_text(f"❌ Ошибка: {e}")


# ─── Синхронизация остатков ──────────────────────────────────────────

async def sync_stock_handler(query):
    """Синхронизировать остатки товаров с Яндекс Маркетом."""
    try:
        await safe_edit_message(
            query,
            "🔄 *Синхронизация остатков*\n\n"
            "⏳ Обновляю остатки товаров в Яндекс Маркете...",
        )
        
        # Получаем остатки со склада
        stock_counts = get_stock_count_by_sku()
        
        if not stock_counts:
            await safe_edit_message(
                query,
                "⚠️ *Нет товаров для синхронизации*\n\n"
                "На складе нет свободных аккаунтов.",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("⬅️ Назад", callback_data="back_menu")]
                ]),
        )
        return

        # Синхронизируем с Яндекс Маркетом
        sync_stock_to_yandex()
        
        # Формируем отчет
        text = "✅ *Остатки синхронизированы!*\n\n"
        text += f"📊 Обновлено товаров: {len(stock_counts)}\n\n"
        text += "*Остатки:*\n"
        for sku, count in sorted(stock_counts.items()):
            text += f"  • SKU `{sku}`: {count} шт.\n"
        
        await safe_edit_message(
            query,
        text,
        reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("⬅️ Назад", callback_data="back_menu")]
            ]),
        )
        
    except Exception as e:
        logger.error(f"Ошибка синхронизации остатков: {e}")
        await safe_edit_message(
            query,
            f"❌ *Ошибка синхронизации*\n\n"
            f"Ошибка: `{str(e)[:200]}`\n\n"
            f"Проверьте права API-ключа и настройки товаров.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔄 Повторить", callback_data="sync_stock")],
                [InlineKeyboardButton("⬅️ Назад", callback_data="back_menu")]
            ]),
        )


# ─── Информация о складе аккаунтов ──────────────────────────────────

async def show_stock_info(query):
    """Показать информацию о складе аккаунтов."""
    try:
        data = load_accounts()
        accounts = data.get("accounts", [])

        total = len(accounts)
        free = sum(1 for a in accounts if not a.get("used", False))
        used = total - free

        text = (
            f"📦 *Склад аккаунтов*\n\n"
            f"📊 Всего: {total}\n"
            f"✅ Свободных: {free}\n"
            f"❌ Выдано: {used}\n\n"
        )

        if free > 0:
            text += "*Свободные аккаунты:*\n"
            for acc in accounts:
                if not acc.get("used", False):
                    text += f"  • `{acc['login']}` — {acc.get('product', '?')}\n"

        await safe_edit_message(
            query,
            text,
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("⬅️ Назад", callback_data="back_menu")]
            ]),
        )
    except Exception as e:
        await safe_edit_message(query, f"❌ Ошибка чтения склада: {e}")


# ─── Добавление аккаунтов через бота ─────────────────────────────────

async def start_add_accounts(query, context):
    """Начать процесс добавления аккаунтов — показать инструкцию."""
        context.user_data["awaiting_accounts"] = True
    await safe_edit_message(
        query,
        "➕ *Добавление аккаунтов на склад*\n\n"
        "Отправьте аккаунты в формате (каждый с новой строки):\n\n"
        "`логин ; пароль ; 2fa`\n\n"
        "Примеры:\n"
        "`user1@gmail.com ; Pass123!`\n"
        "`user2@gmail.com ; Pass456! ; BACKUP-CODE`\n"
        "`user3@mail.ru ; Qwerty1 ;`\n\n"
        "• Разделитель — точка с запятой `;`\n"
        "• 2FA — необязательно, можно не указывать\n"
        "• Можно добавить сразу несколько строк",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("❌ Отмена", callback_data="back_menu")]
        ]),
    )


async def add_accounts_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик /add — быстрое добавление аккаунтов (только админ)."""
    if not is_admin(update):
        await update.message.reply_text("⛔ Доступ запрещён.")
        return

    text = update.message.text
    # Убираем саму команду /add из текста
    lines_text = text.split(None, 1)[1] if len(text.split(None, 1)) > 1 else ""

    if not lines_text.strip():
        # Если текста нет — включаем режим ожидания
            context.user_data["awaiting_accounts"] = True
            await update.message.reply_text(
            "➕ *Добавление аккаунтов*\n\n"
                "Отправьте аккаунты в формате:\n"
            "`логин ; пароль ; 2fa`\n\n"
                "Каждый аккаунт — с новой строки.\n"
                "2FA необязателен.",
            parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("❌ Отмена", callback_data="back_menu")]
            ]),
        )
        return

    # Если текст есть — сразу обрабатываем
    result = _parse_and_add_accounts(lines_text)
    await update.message.reply_text(
        result,
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("📦 Склад", callback_data="stock_info")],
            [InlineKeyboardButton("📌 Меню", callback_data="back_menu")],
        ]),
    )


def _parse_and_add_accounts(text):
    """
    Парсинг строк в формате `логин ; пароль ; 2fa` и добавление на склад.
    Возвращает текстовый отчёт.
    """
    lines = text.strip().split("\n")
    added = []
    errors = []

    data = load_accounts()

    for i, line in enumerate(lines, 1):
        line = line.strip()
        if not line:
            continue

        parts = [p.strip() for p in line.split(";")]

        if len(parts) < 2 or not parts[0] or not parts[1]:
            errors.append(f"Строка {i}: `{line}` — нужен логин и пароль")
            continue

        login = parts[0]
        password = parts[1]
        twofa = parts[2].strip() if len(parts) > 2 else ""

        # Проверка дубликатов
        duplicate = any(
            acc["login"] == login and not acc.get("used", False)
            for acc in data["accounts"]
        )
        if duplicate:
            errors.append(f"Строка {i}: `{login}` — уже на складе")
            continue

        account = {
            "product": "",
            "sku": "",
            "login": login,
            "password": password,
            "2fa": twofa,
            "used": False,
        }
        data["accounts"].append(account)
        added.append(login)
        
        # Синхронизируем остатки после добавления аккаунта (если есть SKU)
        if account.get("sku"):
            try:
                sync_stock_to_yandex(account["sku"])
            except Exception as e:
                logger.warning(f"Ошибка синхронизации остатков после добавления аккаунта {login}: {e}")

    save_accounts(data)

    # Формируем отчёт
    free = sum(1 for a in data["accounts"] if not a.get("used", False))
    report = ""

    if added:
        report += f"✅ *Добавлено: {len(added)}*\n"
        for login in added:
            report += f"  • `{login}`\n"
        report += "\n"

    if errors:
        report += f"⚠️ *Ошибки: {len(errors)}*\n"
        for err in errors:
            report += f"  • {err}\n"
        report += "\n"

    report += f"📦 Всего свободных на складе: *{free}*"
    return report


async def handle_text_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Обработчик текстовых сообщений (только админ).
    Обрабатывает:
    1. Добавление аккаунтов на склад
    2. Ввод данных аккаунта для ручной обработки заказа
    """
    if not is_admin(update):
        return  # Не админ — игнорируем

    user_id = update.effective_user.id
    text = update.message.text

    # Проверяем режим ручной обработки заказа
    manual_orders = context.bot_data.get("manual_orders", {})
    if user_id in manual_orders:
        order_id = manual_orders[user_id]
        del manual_orders[user_id]  # Удаляем из ожидающих
        
        # Парсим данные аккаунта
        parts = [p.strip() for p in text.split(";")]
        if len(parts) < 2 or not parts[0] or not parts[1]:
            await update.message.reply_text(
                "❌ *Неверный формат*\n\n"
                "Используйте формат:\n"
                "`логин ; пароль ; 2fa`\n\n"
                "Пример: `user@gmail.com ; Pass123!`",
                parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("❌ Отмена", callback_data=f"order_detail_{order_id}")],
                ]),
            )
        return

        login = parts[0]
        password = parts[1]
        twofa = parts[2].strip() if len(parts) > 2 else ""

        # Отправляем данные клиенту
        try:
            with YandexMarketAPI() as api:
                # Получаем информацию о заказе
                order_data = api.get_order(order_id)
                order = order_data.get("order", {})
                items = order.get("items", [])
                product_name = items[0].get("offerName", "Товар") if items else "Товар"

                # Формируем сообщение для клиента
                account_data = {
                    "login": login,
                    "password": password,
                    "2fa": twofa,
                }
                slip = build_account_slip(account_data, product_name)

                # Отправляем клиенту
                api.send_message_to_buyer(order_id, slip)

                # Обновляем статус заказа до DELIVERED
                status_results = api.deliver_digital_order(order_id)
                status_report = "\n".join(f"  • {s}: {r}" for s, r in status_results)
                
                # Сохраняем заказ в БД
                try:
                    buyer = order.get("buyer", {})
                    buyer_name = f"{buyer.get('firstName', '')} {buyer.get('lastName', '')}".strip()
                    final_status = "DELIVERED" if (any(step == "DELIVERED" and result == "OK" for step, result in status_results) or 
                                                   any(step == "DELIVERED" and "уже" in result for step, result in status_results)) else order.get("status", "PROCESSING")
                    db.save_order(
                        order_id=order_id,
                        status=final_status,
                        substatus=order.get("substatus", ""),
                        our_status="ВЫДАН",
                        product=product_name,
                        buyer_name=buyer_name,
                        total=order.get("buyerTotal", 0),
                        created_at=order.get("creationDate", ""),
                        delivered_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                        account_login=login,
                        delivery_type=order.get("delivery", {}).get("type", ""),
                    )
                except Exception as e:
                    logger.warning(f"Ошибка сохранения заказа {order_id} в БД после ручной обработки: {e}")

                # Проверяем успешность доставки
                delivered_ok = any(
                    step == "DELIVERED" and result == "OK"
                    for step, result in status_results
                )
                already_delivered = any(
                    step == "DELIVERED" and "уже" in result
                    for step, result in status_results
                )

                delivery_status = "✅ DELIVERED" if (delivered_ok or already_delivered) else "⏳ в процессе"

                # Отправляем подтверждение менеджеру
                success_text = (
                    f"✅ *Данные отправлены клиенту!*\n\n"
                    f"📦 Заказ: `{order_id}`\n"
                    f"🛒 Товар: {product_name}\n"
                    f"🔑 Логин: `{login}`\n"
                    f"📨 Сообщение отправлено в чат покупателю\n"
                    f"{delivery_status}\n\n"
                    f"📊 Обработка:\n{status_report}"
                )

                await update.message.reply_text(
                    success_text,
                    parse_mode="Markdown",
                    reply_markup=InlineKeyboardMarkup([
                        [InlineKeyboardButton("📋 Детали заказа", callback_data=f"order_detail_{order_id}")],
                        [InlineKeyboardButton("⬅️ Назад", callback_data="back_menu")],
                    ]),
                )

                # Уведомление в группу
                if TELEGRAM_GROUP_ID:
                    try:
                        await context.bot.send_message(
                            chat_id=TELEGRAM_GROUP_ID,
                            text=(
                                f"✅ *Ручная обработка завершена*\n\n"
                                f"📦 Заказ: `{order_id}`\n"
                                f"🔑 Логин: `{login}`\n"
                                f"👤 Менеджер: {update.effective_user.first_name}"
                            ),
                            parse_mode="Markdown",
                        )
                    except Exception as e:
                        logger.error(f"Ошибка уведомления в группу: {e}")

        except Exception as e:
            logger.error(f"Ошибка отправки данных клиенту для заказа {order_id}: {e}")
            await update.message.reply_text(
                f"❌ *Ошибка отправки данных*\n\n"
                f"Ошибка: `{str(e)[:200]}`\n\n"
                f"Попробуйте ещё раз или используйте автоматическую выдачу.",
                parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🔑 Автовыдача", callback_data=f"auto_deliver_{order_id}")],
                    [InlineKeyboardButton("📋 Детали заказа", callback_data=f"order_detail_{order_id}")],
                ]),
            )
        return

    # Проверяем режим добавления аккаунтов
    if context.user_data.get("awaiting_accounts"):
    context.user_data["awaiting_accounts"] = False
    result = _parse_and_add_accounts(text)

    await update.message.reply_text(
        result,
            parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("➕ Добавить ещё", callback_data="add_accounts")],
            [InlineKeyboardButton("📦 Склад", callback_data="stock_info")],
            [InlineKeyboardButton("📌 Меню", callback_data="back_menu")],
        ]),
    )


# ─── Фоновая проверка новых заказов (АВТОВЫДАЧА) ─────────────────────

async def poll_new_orders(context: ContextTypes.DEFAULT_TYPE):
    """
    Фоновая задача — каждые 30 сек:
    1. Проверяет новые заказы (PROCESSING)
    2. Если на складе есть аккаунт → АВТОМАТИЧЕСКИ выдаёт + меняет статус → DELIVERED
    3. Если склад пуст → уведомляет в группу с кнопками ручной обработки
    """
    try:
        with YandexMarketAPI() as api:
            data = api.get_orders(status="PROCESSING")
            orders = data.get("orders", [])

            for order in orders:
                oid = order["id"]
                if oid in known_order_ids:
                    continue
                known_order_ids.add(oid)

                items = order.get("items", [])
                buyer = order.get("buyer", {})
                items_text = ""
                for item in items:
                    items_text += f"  • {item.get('offerName', '?')} × {item.get('count', 1)} — {item.get('buyerPrice', 0)}₽\n"

                product_name = items[0].get("offerName", "Товар") if items else "?"

                logger.info(f"🔔 Новый заказ: {oid} — {product_name}")

                # Сохраняем новый заказ в БД
                try:
                    buyer_name = f"{buyer.get('firstName', '')} {buyer.get('lastName', '')}".strip()
                    db.save_order(
                        order_id=oid,
                        status=order.get("status", "PROCESSING"),
                        substatus=order.get("substatus", ""),
                        our_status="НОВЫЙ",
                        product=product_name,
                        buyer_name=buyer_name,
                        total=order.get("buyerTotal", 0),
                        created_at=order.get("creationDate", ""),
                        delivery_type=order.get("delivery", {}).get("type", ""),
                    )
                except Exception as e:
                    logger.warning(f"Ошибка сохранения нового заказа {oid} в БД: {e}")

                # ═══════ УВЕДОМЛЕНИЕ О НОВОМ ЗАКАЗЕ В ГРУППУ ═══════
                new_order_text = (
                    f"🔔 *НОВЫЙ ЗАКАЗ — ТРЕБУЕТ ОБРАБОТКИ!*\n\n"
                    f"📦 Заказ №`{oid}`\n"
                    f"💰 Сумма: {order.get('buyerTotal', 0)}₽\n"
                    f"📅 Дата: {order.get('creationDate', '?')}\n"
                    f"👤 Покупатель: {buyer.get('firstName', '')} {buyer.get('lastName', '')}\n\n"
                    f"🛒 *Товары:*\n{items_text}\n"
                    f"Выберите способ обработки:"
                )
                    detail_kb = InlineKeyboardMarkup([
                    [InlineKeyboardButton(
                        "🔑 Выдать аккаунт (авто)",
                        callback_data=f"auto_deliver_{oid}",
                    )],
                    [InlineKeyboardButton(
                        "👨‍💼 Ручная обработка (менеджер)",
                        callback_data=f"manual_process_{oid}",
                    )],
                        [InlineKeyboardButton(
                            "📋 Детали заказа",
                            callback_data=f"order_detail_{oid}",
                        )],
                    ])

                # Отправляем уведомление о новом заказе в группу
                    if TELEGRAM_GROUP_ID:
                        try:
                            await context.bot.send_message(
                                chat_id=TELEGRAM_GROUP_ID,
                            text=new_order_text,
                            reply_markup=detail_kb,
                            parse_mode="Markdown",
                            )
                        logger.info(f"✅ Уведомление о новом заказе {oid} отправлено в группу")
                        except Exception as e:
                        logger.error(f"Ошибка отправки уведомления о новом заказе в группу: {e}")

                # ═══════ ПОПЫТКА АВТОВЫДАЧИ ═══════
                ok, report, account = await asyncio.to_thread(
                    _do_deliver, api, oid, order
                )

                if ok:
                    # ✅ Аккаунт выдан автоматически
                    success_text = (
                        f"✅ *АВТОВЫДАЧА — заказ обработан!*\n\n"
                        f"{report}\n\n"
                        f"👤 Покупатель: {buyer.get('firstName', '')} {buyer.get('lastName', '')}\n\n"
                        f"🔑 *Данные аккаунта (копия):*\n"
                        f"Логин: `{account['login']}`\n"
                        f"Пароль: `{account['password']}`\n"
                    )
                    if account.get("2fa"):
                        success_text += f"2FA: `{account['2fa']}`\n"

                    success_kb = InlineKeyboardMarkup([
                        [InlineKeyboardButton(
                            "📋 Детали заказа",
                            callback_data=f"order_detail_{oid}",
                        )],
                    ])

                    # Отправляем уведомление об успешной автовыдаче в группу
                    if TELEGRAM_GROUP_ID:
                        try:
                            await context.bot.send_message(
                                chat_id=TELEGRAM_GROUP_ID,
                                text=success_text,
                                reply_markup=success_kb,
                                parse_mode="Markdown",
                            )
                        except Exception as e:
                            logger.error(f"Ошибка отправки уведомления об автовыдаче в группу: {e}")

                    # Отправляем детали админам в ЛС
                    for admin_id in ADMIN_IDS:
                        try:
                            await context.bot.send_message(
                                chat_id=admin_id,
                                text=success_text,
                                reply_markup=success_kb,
                                parse_mode="Markdown",
                            )
                        except Exception as e:
                            logger.error(f"Ошибка отправки админу {admin_id}: {e}")
                else:
                    # ❌ Не удалось — отправляем уведомление об ошибке в группу
                    error_text = (
                        f"⚠️ *АВТОВЫДАЧА НЕ УДАЛАСЬ*\n\n"
                        f"📦 Заказ №`{oid}`\n"
                        f"⚠️ *{report}*\n\n"
                        f"Требуется ручная обработка!"
                    )
                    if TELEGRAM_GROUP_ID:
                        try:
                            await context.bot.send_message(
                                chat_id=TELEGRAM_GROUP_ID,
                                text=error_text,
                                reply_markup=detail_kb,
                                parse_mode="Markdown",
                            )
                        except Exception as e:
                            logger.error(f"Ошибка отправки уведомления об ошибке в группу: {e}")

    except Exception as e:
        logger.error(f"Ошибка polling заказов: {e}")


# ─── Запуск бота ─────────────────────────────────────────────────────

def main():
    """Запуск Telegram-бота."""
    print("=" * 50)
    print("  Яндекс Маркет DBS — Telegram Бот")
    print("=" * 50)

    app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()

    # Команды
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("menu", menu))
    app.add_handler(CommandHandler("order", order_command))
    app.add_handler(CommandHandler("add", add_accounts_command))

    # Кнопки
    app.add_handler(CallbackQueryHandler(button_handler))

    # Текстовые сообщения (для добавления аккаунтов)
    app.add_handler(MessageHandler(
        filters.TEXT & ~filters.COMMAND,
        handle_text_message,
    ))

    # Инициализация БД
    try:
        db.init_db()
        print("✅ База данных инициализирована")
    except Exception as e:
        print(f"⚠️ Ошибка инициализации БД: {e}")

    # Фоновая проверка новых заказов — каждые 60 секунд
    app.job_queue.run_repeating(poll_new_orders, interval=60, first=5)

    print("✅ Бот запущен! Polling заказов каждые 60 сек.")
    print(f"👤 Админы: {', '.join(str(a) for a in ADMIN_IDS)}")
    print(f"📢 Уведомления в группу: {TELEGRAM_GROUP_ID}")

    # Загружаем склад при старте
    try:
        data = load_accounts()
        free = sum(1 for a in data["accounts"] if not a.get("used", False))
        print(f"📦 Склад: {free} свободных аккаунтов")
    except Exception as e:
        print(f"⚠️ Ошибка загрузки склада: {e}")

    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
