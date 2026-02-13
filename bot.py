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


def mark_account_used(login):
    """Пометить аккаунт как использованный."""
    data = load_accounts()
    for acc in data["accounts"]:
        if acc["login"] == login:
            acc["used"] = True
            break
    save_accounts(data)


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
        [InlineKeyboardButton("ℹ️ Статус магазина", callback_data="shop_info")],
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
    elif data == "order_check":
        await query.edit_message_text(
            "🔍 Отправьте ID заказа командой:\n"
            "`/order 54172200065`",
            parse_mode="Markdown",
        )
    elif data == "shop_info":
        await show_shop_info(query)
    elif data == "stock_info":
        await show_stock_info(query)
    elif data.startswith("order_detail_"):
        order_id = int(data.replace("order_detail_", ""))
        await show_order_detail(query, order_id)
    elif data.startswith("auto_deliver_"):
        order_id = int(data.replace("auto_deliver_", ""))
        await auto_deliver_account(query, order_id)
    elif data.startswith("manual_process_"):
        order_id = int(data.replace("manual_process_", ""))
        await manual_process_order(query, order_id)
    elif data.startswith("order_confirm_"):
        order_id = int(data.replace("order_confirm_", ""))
        await confirm_order(query, order_id)
    elif data == "add_accounts":
        await start_add_accounts(query, context)
    elif data == "back_menu":
        # Сбрасываем режим добавления аккаунтов при возврате в меню
        context.user_data.pop("awaiting_accounts", None)
        await query.edit_message_text(
            "📌 *Главное меню*\n\nВыберите действие:",
            reply_markup=main_menu_keyboard(),
            parse_mode="Markdown",
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
            await query.edit_message_text(
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

        await query.edit_message_text(
            text,
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="Markdown",
        )

    except Exception as e:
        logger.error(f"Ошибка получения заказов: {e}")
        await query.edit_message_text(
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

        keyboard = []
        status = order.get("status", "")

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

        keyboard.append([InlineKeyboardButton("⬅️ Назад", callback_data="back_menu")])

        await query.edit_message_text(
            text,
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="Markdown",
        )

    except Exception as e:
        logger.error(f"Ошибка получения заказа {order_id}: {e}")
        await query.edit_message_text(
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
            await query.edit_message_text(
                f"❌ *Не удалось выдать аккаунт*\n\n{report}",
                parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("👨‍💼 Ручная обработка", callback_data=f"manual_process_{order_id}")],
                    [InlineKeyboardButton("⬅️ Назад", callback_data="back_menu")],
                ]),
            )
            return

        await query.edit_message_text(
            f"✅ *Аккаунт выдан и заказ доставлен!*\n\n{report}",
            parse_mode="Markdown",
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
        await query.edit_message_text(
            f"❌ Ошибка: {e}\n\nПопробуйте ручную обработку.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("👨‍💼 Ручная обработка", callback_data=f"manual_process_{order_id}")],
                [InlineKeyboardButton("⬅️ Назад", callback_data="back_menu")],
            ]),
        )


# ─── Ручная обработка (менеджер) ────────────────────────────────────

async def manual_process_order(query, order_id):
    """Ручная обработка — отправить клиента в чат поддержки."""
    try:
        support_msg = build_support_message()

        # Отправляем сообщение покупателю через чат Маркета
        with YandexMarketAPI() as api:
            result = api.send_message_to_buyer(order_id, support_msg)

        await query.edit_message_text(
            f"👨‍💼 *Ручная обработка*\n\n"
            f"📦 Заказ: `{order_id}`\n"
            f"📨 Покупателю отправлено сообщение в чат Маркета:\n"
            f"_«Ваш заказ принят и передан менеджеру»_\n\n"
            f"Откройте чат с покупателем в панели Маркета\n"
            f"и отправьте данные вручную.",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("📋 Детали заказа", callback_data=f"order_detail_{order_id}")],
                [InlineKeyboardButton("⬅️ Назад", callback_data="back_menu")],
            ]),
        )

        # Уведомление в группу
        if TELEGRAM_GROUP_ID:
            try:
                from telegram import Bot
                bot = Bot(token=TELEGRAM_BOT_TOKEN)
                await bot.send_message(
                    chat_id=TELEGRAM_GROUP_ID,
                    text=(
                        f"👨‍💼 *Заказ на ручную обработку*\n\n"
                        f"📦 Заказ: `{order_id}`\n"
                        f"⚠️ Менеджер, откройте чат с покупателем\n"
                        f"в панели Яндекс Маркета и отправьте данные."
                    ),
                    parse_mode="Markdown",
                )
            except Exception as e:
                logger.error(f"Ошибка уведомления в группу: {e}")

    except Exception as e:
        logger.error(f"Ошибка ручной обработки {order_id}: {e}")
        await query.edit_message_text(
            f"❌ Ошибка: {e}",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("⬅️ Назад", callback_data="back_menu")]
            ]),
        )


# ─── Подтверждение заказа ────────────────────────────────────────────

async def confirm_order(query, order_id):
    """Подтвердить передачу заказа в доставку."""
    try:
        with YandexMarketAPI() as api:
            result = api.update_order_status(order_id, "PROCESSING", "READY_TO_SHIP")

        await query.edit_message_text(
            f"✅ Заказ №{order_id} подтверждён!\n"
            f"Статус изменён на READY\\_TO\\_SHIP",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("📋 Детали заказа", callback_data=f"order_detail_{order_id}")],
                [InlineKeyboardButton("⬅️ Назад", callback_data="back_menu")],
            ]),
            parse_mode="Markdown",
        )
    except Exception as e:
        logger.error(f"Ошибка подтверждения заказа {order_id}: {e}")
        await query.edit_message_text(
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


# ─── Информация о магазине ───────────────────────────────────────────

async def show_shop_info(query):
    """Показать информацию о магазине."""
    try:
        with YandexMarketAPI() as api:
            data = api.get_campaign_info()

        campaign = data.get("campaign", {})
        business = campaign.get("business", {})

        text = (
            f"🏪 *Информация о магазине*\n\n"
            f"📛 Магазин: {campaign.get('domain', '?')}\n"
            f"🏢 Бизнес: {business.get('name', '?')}\n"
            f"🆔 Campaign ID: `{campaign.get('id', '?')}`\n"
            f"🆔 Business ID: `{business.get('id', '?')}`\n"
            f"📦 Тип: {campaign.get('placementType', '?')}\n"
            f"🔗 API: {campaign.get('apiAvailability', '?')}\n"
        )

        await query.edit_message_text(
            text,
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("⬅️ Назад", callback_data="back_menu")]
            ]),
            parse_mode="Markdown",
        )
    except Exception as e:
        await query.edit_message_text(f"❌ Ошибка: {e}")


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

        await query.edit_message_text(
            text,
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("⬅️ Назад", callback_data="back_menu")]
            ]),
            parse_mode="Markdown",
        )
    except Exception as e:
        await query.edit_message_text(f"❌ Ошибка чтения склада: {e}")


# ─── Добавление аккаунтов через бота ─────────────────────────────────

async def start_add_accounts(query, context):
    """Начать процесс добавления аккаунтов — показать инструкцию."""
    context.user_data["awaiting_accounts"] = True
    await query.edit_message_text(
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
        parse_mode="Markdown",
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
    Если включён режим добавления аккаунтов — парсим текст.
    """
    if not is_admin(update):
        return  # Не админ — игнорируем

    if not context.user_data.get("awaiting_accounts"):
        return  # Не в режиме добавления — игнорируем

    context.user_data["awaiting_accounts"] = False

    text = update.message.text
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

                # ═══════ ПОПЫТКА АВТОВЫДАЧИ ═══════
                ok, report, account = await asyncio.to_thread(
                    _do_deliver, api, oid, order
                )

                if ok:
                    # ✅ Аккаунт выдан автоматически
                    text = (
                        f"✅ *АВТОВЫДАЧА — заказ обработан!*\n\n"
                        f"{report}\n\n"
                        f"👤 Покупатель: {buyer.get('firstName', '')} {buyer.get('lastName', '')}\n\n"
                        f"🔑 *Данные аккаунта (копия):*\n"
                        f"Логин: `{account['login']}`\n"
                        f"Пароль: `{account['password']}`\n"
                    )
                    if account.get("2fa"):
                        text += f"2FA: `{account['2fa']}`\n"

                    keyboard = InlineKeyboardMarkup([
                        [InlineKeyboardButton(
                            "📋 Детали заказа",
                            callback_data=f"order_detail_{oid}",
                        )],
                    ])
                else:
                    # ❌ Не удалось — ручные кнопки
                    text = (
                        f"🔔 *НОВЫЙ ЗАКАЗ — ТРЕБУЕТ ОБРАБОТКИ!*\n\n"
                        f"📦 Заказ №`{oid}`\n"
                        f"💰 Сумма: {order.get('buyerTotal', 0)}₽\n"
                        f"📅 Дата: {order.get('creationDate', '?')}\n"
                        f"👤 Покупатель: {buyer.get('firstName', '')} {buyer.get('lastName', '')}\n\n"
                        f"🛒 *Товары:*\n{items_text}\n"
                        f"⚠️ *{report}*\n\n"
                        f"Выберите способ обработки:"
                    )
                    keyboard = InlineKeyboardMarkup([
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

                # Отправляем уведомления: в группу + всем админам в ЛС
                targets = []
                if TELEGRAM_GROUP_ID:
                    targets.append(("группа", TELEGRAM_GROUP_ID))
                for admin_id in ADMIN_IDS:
                    targets.append((f"админ {admin_id}", admin_id))

                for label, chat_id in targets:
                    try:
                        await context.bot.send_message(
                            chat_id=chat_id,
                            text=text,
                            reply_markup=keyboard,
                            parse_mode="Markdown",
                        )
                    except Exception as e:
                        logger.error(f"Ошибка отправки в {label} ({chat_id}): {e}")

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
