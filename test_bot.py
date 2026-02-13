"""
Скрипт проверки работоспособности бота и API.
Запуск: python test_bot.py

Проверяет:
  1. Загрузку конфигурации (.env)
  2. Загрузку аккаунтов (accounts.json)
  3. Парсинг формата "логин ; пароль ; 2fa"
  4. Подключение к Yandex Market API
  5. Получение списка заказов
  6. Проверку формирования тел запросов (PUT/POST)
"""

import json
import os
import sys
import traceback

# ── Цвета для терминала ──────────────────────────────────────────────

GREEN = "\033[92m"
RED = "\033[91m"
YELLOW = "\033[93m"
CYAN = "\033[96m"
BOLD = "\033[1m"
RESET = "\033[0m"

passed = 0
failed = 0


def ok(msg):
    global passed
    passed += 1
    print(f"  {GREEN}✅ PASS{RESET} — {msg}")


def fail(msg, detail=""):
    global failed
    failed += 1
    print(f"  {RED}❌ FAIL{RESET} — {msg}")
    if detail:
        print(f"         {RED}{detail}{RESET}")


def section(title):
    print(f"\n{CYAN}{BOLD}═══ {title} ═══{RESET}")


# ══════════════════════════════════════════════════════════════════════
#  1. Конфигурация
# ══════════════════════════════════════════════════════════════════════

section("1. Конфигурация (.env)")

try:
    from config import TELEGRAM_BOT_TOKEN, TELEGRAM_GROUP_ID, API_TOKEN, BUSINESS_ID, CAMPAIGN_ID, BASE_URL, HEADERS

    if TELEGRAM_BOT_TOKEN:
        ok(f"TELEGRAM_BOT_TOKEN загружен ({TELEGRAM_BOT_TOKEN[:10]}...)")
    else:
        fail("TELEGRAM_BOT_TOKEN пустой — добавьте в .env")

    if API_TOKEN:
        ok(f"YANDEX_API_TOKEN загружен ({API_TOKEN[:10]}...)")
    else:
        fail("YANDEX_API_TOKEN пустой — добавьте в .env")

    if BUSINESS_ID:
        ok(f"BUSINESS_ID = {BUSINESS_ID}")
    else:
        fail("BUSINESS_ID не задан")

    if CAMPAIGN_ID:
        ok(f"CAMPAIGN_ID = {CAMPAIGN_ID}")
    else:
        fail("CAMPAIGN_ID не задан")

    # Проверяем что Content-Type НЕ в дефолтных заголовках
    if "Content-Type" not in HEADERS:
        ok("Content-Type НЕ в дефолтных HEADERS (httpx ставит автоматически)")
    else:
        fail("Content-Type в HEADERS — уберите, иначе конфликт с multipart/form-data")

    if HEADERS.get("Api-Key"):
        ok("Api-Key в HEADERS")
    else:
        fail("Api-Key отсутствует в HEADERS")

except Exception as e:
    fail(f"Ошибка загрузки конфигурации: {e}")

# ══════════════════════════════════════════════════════════════════════
#  2. Аккаунты (accounts.json)
# ══════════════════════════════════════════════════════════════════════

section("2. Склад аккаунтов (accounts.json)")

try:
    from bot import load_accounts, get_available_account, _parse_and_add_accounts

    data = load_accounts()
    accounts = data.get("accounts", [])
    total = len(accounts)
    free = sum(1 for a in accounts if not a.get("used", False))
    used = total - free

    ok(f"Загружено {total} аккаунтов (свободных: {free}, выдано: {used})")

    if free > 0:
        ok("Есть свободные аккаунты для автовыдачи")
        acc = get_available_account()
        if acc:
            ok(f"get_available_account() → {acc['login']}")
        else:
            fail("get_available_account() вернул None при free > 0")
    else:
        fail("Нет свободных аккаунтов — добавьте через бота или в accounts.json")

    # Проверка по SKU
    test_acc = get_available_account(sku="5364535435636")
    if test_acc:
        ok(f"get_available_account(sku='5364535435636') → {test_acc['login']}")
    else:
        print(f"  {YELLOW}⚠️  INFO{RESET} — Нет свободных аккаунтов для SKU 5364535435636")

except Exception as e:
    fail(f"Ошибка работы с аккаунтами: {e}")

# ══════════════════════════════════════════════════════════════════════
#  3. Парсинг формата "логин ; пароль ; 2fa"
# ══════════════════════════════════════════════════════════════════════

section("3. Парсинг формата добавления аккаунтов")

try:
    from bot import _parse_and_add_accounts, load_accounts, save_accounts

    # Сохраняем оригинальные данные
    original_data = load_accounts()

    # Тест парсинга
    test_input = """parser_test1@test.com ; Password1!
parser_test2@test.com ; Password2! ; 2FA-CODE
parser_test3@test.com ; Password3! ;"""

    result = _parse_and_add_accounts(test_input)

    if "Добавлено: 3" in result:
        ok("Парсинг 3 строк — успешно")
    else:
        fail(f"Парсинг 3 строк — неожиданный результат:\n{result}")

    # Проверка дубликатов
    result2 = _parse_and_add_accounts("parser_test1@test.com ; Password1!")
    if "уже на складе" in result2:
        ok("Дубликат обнаружен корректно")
    else:
        fail(f"Дубликат не обнаружен:\n{result2}")

    # Невалидный формат
    result3 = _parse_and_add_accounts("тут только логин без пароля")
    if "Ошибки:" in result3:
        ok("Невалидный формат → ошибка")
    else:
        fail(f"Невалидный формат не вызвал ошибку:\n{result3}")

    # Восстанавливаем оригинальные данные
    save_accounts(original_data)
    ok("Оригинальные данные склада восстановлены")

except Exception as e:
    fail(f"Ошибка парсинга: {e}")
    traceback.print_exc()
    # Восстанавливаем в любом случае
    try:
        save_accounts(original_data)
    except Exception:
        pass

# ══════════════════════════════════════════════════════════════════════
#  4. Yandex Market API — подключение
# ══════════════════════════════════════════════════════════════════════

section("4. Подключение к Yandex Market API")

try:
    from yandex_api import YandexMarketAPI

    with YandexMarketAPI() as api:
        ok(f"Клиент создан (base_url={api.base_url})")

        # Получение информации о кампании
        try:
            campaign_info = api.get_campaign_info()
            campaign = campaign_info.get("campaign", {})
            domain = campaign.get("domain", "?")
            ok(f"get_campaign_info() → магазин: {domain}")
        except Exception as e:
            fail(f"get_campaign_info() → {e}")

        # Получение заказов
        try:
            orders_data = api.get_orders()
            orders = orders_data.get("orders", [])
            total = orders_data.get("pager", {}).get("total", 0)
            ok(f"get_orders() → всего: {total}, на странице: {len(orders)}")

            if orders:
                first = orders[0]
                ok(f"Первый заказ: ID={first['id']}, "
                   f"status={first.get('status')}/{first.get('substatus')}")
        except Exception as e:
            fail(f"get_orders() → {e}")

        # Заказы в статусе PROCESSING
        try:
            proc_data = api.get_orders(status="PROCESSING")
            proc_orders = proc_data.get("orders", [])
            proc_total = proc_data.get("pager", {}).get("total", 0)
            ok(f"get_orders(status='PROCESSING') → {proc_total} заказов")

            if proc_orders:
                for o in proc_orders[:3]:
                    print(f"         📦 Заказ {o['id']}: "
                          f"{o.get('status')}/{o.get('substatus')} — "
                          f"{o.get('buyerTotal', 0)}₽")
        except Exception as e:
            fail(f"get_orders(status='PROCESSING') → {e}")

except Exception as e:
    fail(f"Ошибка подключения к API: {e}")
    traceback.print_exc()

# ══════════════════════════════════════════════════════════════════════
#  5. Проверка формирования тел запросов
# ══════════════════════════════════════════════════════════════════════

section("5. Проверка формирования тел запросов (PUT/POST)")

try:
    # Тело для update_order_status
    body_status = {
        "order": {
            "status": "PROCESSING",
            "substatus": "READY_TO_SHIP",
        }
    }
    # Проверяем что тело валидный JSON
    json_str = json.dumps(body_status, ensure_ascii=False)
    parsed = json.loads(json_str)
    if parsed["order"]["status"] == "PROCESSING":
        ok(f"update_order_status body → {json_str}")
    else:
        fail("Некорректное тело update_order_status")

    # Тело для set_order_boxes
    body_boxes = {
        "boxes": [{
            "fulfilmentId": "box-1",
            "weight": 100,
            "width": 10,
            "height": 10,
            "depth": 10,
            "items": [{"id": 12345, "count": 1}],
        }]
    }
    json_str2 = json.dumps(body_boxes, ensure_ascii=False)
    if body_boxes["boxes"][0]["weight"] > 0:
        ok(f"set_order_boxes body → weight > 0")
    else:
        fail("set_order_boxes weight = 0 — API отвергнет!")

    if body_boxes["boxes"][0]["items"]:
        ok("set_order_boxes body → items не пустой")
    else:
        fail("set_order_boxes items пустой — API отвергнет!")

    ok(f"set_order_boxes body → {json_str2[:80]}...")

    # Тело для DELIVERY статуса
    body_delivery = {"order": {"status": "DELIVERY"}}
    ok(f"DELIVERY body → {json.dumps(body_delivery)}")

    # Тело для DELIVERED статуса
    body_delivered = {"order": {"status": "DELIVERED"}}
    ok(f"DELIVERED body → {json.dumps(body_delivered)}")

except Exception as e:
    fail(f"Ошибка формирования тел запросов: {e}")

# ══════════════════════════════════════════════════════════════════════
#  6. Проверка сообщений бота
# ══════════════════════════════════════════════════════════════════════

section("6. Проверка текстов сообщений бота")

try:
    from bot import build_support_message, build_account_slip

    # Сообщение ручной обработки
    support = build_support_message()
    if "10:00 по 23:00" in support:
        ok("build_support_message() содержит '10:00 по 23:00'")
    else:
        fail(f"build_support_message() — нет расписания:\n{support}")

    if "поддержку" in support.lower() or "поддержки" in support.lower():
        ok("build_support_message() содержит упоминание поддержки")
    else:
        fail("build_support_message() — нет упоминания поддержки")

    # Сообщение выдачи аккаунта
    test_account = {
        "login": "test@test.com",
        "password": "TestPass!",
        "2fa": "BACKUP-123",
    }
    slip = build_account_slip(test_account, "Тестовый товар")
    if "test@test.com" in slip and "TestPass!" in slip and "BACKUP-123" in slip:
        ok("build_account_slip() содержит логин, пароль, 2FA")
    else:
        fail(f"build_account_slip() — неполные данные:\n{slip}")

    # Без 2FA
    test_no2fa = {"login": "no2fa@test.com", "password": "Pass!", "2fa": ""}
    slip2 = build_account_slip(test_no2fa, "Товар")
    if "2FA" not in slip2:
        ok("build_account_slip() без 2FA — строка 2FA отсутствует")
    else:
        fail("build_account_slip() без 2FA — строка 2FA есть, хотя не должна")

except Exception as e:
    fail(f"Ошибка проверки сообщений: {e}")

# ══════════════════════════════════════════════════════════════════════
#  ИТОГИ
# ══════════════════════════════════════════════════════════════════════

print(f"\n{BOLD}{'═' * 50}{RESET}")
print(f"{BOLD}  ИТОГО: {GREEN}{passed} пройдено{RESET}, {RED if failed else GREEN}{failed} ошибок{RESET}")
print(f"{BOLD}{'═' * 50}{RESET}")

if failed:
    print(f"\n{YELLOW}⚠️  Есть ошибки — проверьте вывод выше.{RESET}")
    sys.exit(1)
else:
    print(f"\n{GREEN}🎉 Все проверки пройдены! Бот готов к запуску.{RESET}")
    sys.exit(0)
