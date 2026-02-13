"""
Яндекс Маркет DBS бот — тестирование GET-запросов к API.

Запуск: python main.py
"""

import json
import sys
from yandex_api import YandexMarketAPI


def print_json(data, title=""):
    """Красиво вывести JSON-ответ."""
    if title:
        print(f"\n{'='*60}")
        print(f"  {title}")
        print(f"{'='*60}")
    print(json.dumps(data, indent=2, ensure_ascii=False))


def test_campaign_info(api: YandexMarketAPI):
    """Тест: получить информацию о магазине."""
    print_json(api.get_campaign_info(), "Информация о магазине (Campaign)")


def test_campaigns_list(api: YandexMarketAPI):
    """Тест: получить список магазинов в кабинете."""
    print_json(api.get_campaigns(), "Список магазинов (Campaigns)")


def test_orders_list(api: YandexMarketAPI):
    """Тест: получить список всех заказов."""
    print_json(api.get_orders(), "Список заказов (все статусы)")


def test_orders_by_status(api: YandexMarketAPI, status: str):
    """Тест: получить заказы по конкретному статусу."""
    print_json(api.get_orders(status=status), f"Заказы со статусом: {status}")


def main():
    print("=" * 60)
    print("  Яндекс Маркет DBS бот — тестирование API")
    print("=" * 60)

    with YandexMarketAPI() as api:

        # 1. Информация о магазине
        print("\n[1/2] Получаем информацию о магазине...")
        try:
            test_campaign_info(api)
        except Exception as e:
            print(f"  ОШИБКА: {e}")

        # 2. Список заказов
        print("\n[2/2] Получаем список заказов...")
        try:
            test_orders_list(api)
        except Exception as e:
            print(f"  ОШИБКА: {e}")

    print("\n" + "=" * 60)
    print("  Тестирование завершено!")
    print("=" * 60)


if __name__ == "__main__":
    main()
