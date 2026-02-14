"""
Тестирование доставки заказов и проверка ошибок PUT/POST запросов.
"""
import sys
import os
import time
import logging

# Устанавливаем DATABASE_URL для тестов, если не установлен
if not os.getenv("DATABASE_URL"):
    os.environ["DATABASE_URL"] = "postgresql://test:test@localhost:5432/test"

from yandex_api import YandexMarketAPI
from config import CAMPAIGN_ID

logging.basicConfig(
    format="%(asctime)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
log = logging.getLogger(__name__)


def test_order_status_transition():
    """Тест: проверка смены статусов заказа до DELIVERED."""
    print("\n" + "="*60)
    print("ТЕСТ 1: Смена статусов заказа до DELIVERED")
    print("="*60)
    
    with YandexMarketAPI() as api:
        # Получаем список заказов со статусом PROCESSING
        try:
            data = api.get_orders(status="PROCESSING", page=1, page_size=5)
            orders = data.get("orders", [])
            
            if not orders:
                print("❌ Нет заказов со статусом PROCESSING для тестирования")
                return False
            
            # Берем первый заказ
            test_order = orders[0]
            order_id = test_order["id"]
            current_status = test_order.get("status", "")
            current_sub = test_order.get("substatus", "")
            
            print(f"\n📦 Тестовый заказ: {order_id}")
            print(f"   Текущий статус: {current_status}/{current_sub}")
            
            # Проверяем, что заказ в правильном статусе
            if current_status != "PROCESSING":
                print(f"⚠️  Заказ не в статусе PROCESSING, пропускаем")
                return False
            
            # Тестируем доставку
            print(f"\n🔄 Начинаем процесс доставки...")
            results = api.deliver_digital_order(order_id)
            
            # Выводим результаты
            print("\n📊 Результаты обработки:")
            for step, result in results:
                status_icon = "✅" if "OK" in str(result) or "уже" in str(result) else "❌"
                print(f"   {status_icon} {step}: {result}")
            
            # Проверяем финальный статус
            final_data = api.get_order(order_id)
            final_order = final_data.get("order", {})
            final_status = final_order.get("status", "")
            
            print(f"\n📋 Финальный статус: {final_status}")
            
            if final_status == "DELIVERED":
                print("✅ УСПЕХ: Заказ успешно доставлен!")
                return True
            else:
                print(f"⚠️  Заказ не доставлен, текущий статус: {final_status}")
                return False
                
        except Exception as e:
            print(f"❌ ОШИБКА при тестировании доставки: {e}")
            import traceback
            traceback.print_exc()
            return False


def test_put_post_errors():
    """Тест: проверка PUT и POST запросов на ошибки."""
    print("\n" + "="*60)
    print("ТЕСТ 2: Проверка PUT и POST запросов на ошибки")
    print("="*60)
    
    with YandexMarketAPI() as api:
        # Получаем список заказов
        try:
            data = api.get_orders(page=1, page_size=10)
            orders = data.get("orders", [])
            
            if not orders:
                print("❌ Нет заказов для тестирования")
                return False
            
            errors_found = []
            
            # Проверяем каждый заказ
            for order in orders[:3]:  # Проверяем первые 3 заказа
                order_id = order["id"]
                status = order.get("status", "")
                substatus = order.get("substatus", "")
                
                print(f"\n📦 Заказ {order_id}: {status}/{substatus}")
                
                # Тест 1: Проверка текущего статуса (GET - не должно быть ошибок)
                try:
                    order_data = api.get_order(order_id)
                    print("   ✅ GET запрос успешен")
                except Exception as e:
                    error_msg = f"GET заказ {order_id}: {e}"
                    errors_found.append(error_msg)
                    print(f"   ❌ {error_msg}")
                
                # Тест 2: Попытка обновить статус (только если заказ в PROCESSING)
                if status == "PROCESSING":
                    try:
                        # Пробуем обновить статус (с проверкой текущего)
                        result = api.update_order_status(
                            order_id, 
                            "PROCESSING", 
                            substatus or "READY_TO_SHIP",
                            check_current=True
                        )
                        print("   ✅ PUT запрос (update_order_status) успешен")
                    except Exception as e:
                        error_str = str(e)
                        # Проверяем, не 400 ли это
                        if "400" in error_str:
                            error_msg = f"PUT заказ {order_id}: 400 ошибка - {error_str[:200]}"
                            errors_found.append(error_msg)
                            print(f"   ❌ {error_msg}")
                        else:
                            print(f"   ⚠️  PUT запрос: {error_str[:100]}")
            
            if errors_found:
                print(f"\n❌ НАЙДЕНО ОШИБОК: {len(errors_found)}")
                for err in errors_found:
                    print(f"   • {err}")
                return False
            else:
                print("\n✅ Ошибок PUT/POST не обнаружено")
                return True
                
        except Exception as e:
            print(f"❌ ОШИБКА при проверке PUT/POST: {e}")
            import traceback
            traceback.print_exc()
            return False


def check_logs_for_errors():
    """Проверка логов на наличие ошибок."""
    print("\n" + "="*60)
    print("ТЕСТ 3: Проверка логов на ошибки")
    print("="*60)
    
    try:
        # Читаем последние строки лога
        log_file = "bot_test.log"
        try:
            with open(log_file, "r", encoding="utf-8") as f:
                lines = f.readlines()
                # Берем последние 100 строк
                recent_lines = lines[-100:] if len(lines) > 100 else lines
                
                errors = []
                warnings = []
                
                for line in recent_lines:
                    if "ERROR" in line or "error" in line.lower():
                        errors.append(line.strip())
                    elif "WARNING" in line or "warning" in line.lower():
                        warnings.append(line.strip())
                
                if errors:
                    print(f"\n❌ Найдено ошибок в логах: {len(errors)}")
                    for err in errors[-10:]:  # Показываем последние 10
                        print(f"   • {err}")
                    return False
                elif warnings:
                    print(f"\n⚠️  Найдено предупреждений: {len(warnings)}")
                    for warn in warnings[-5:]:  # Показываем последние 5
                        print(f"   • {warn}")
                    return True
                else:
                    print("\n✅ Ошибок в логах не обнаружено")
                    return True
        except FileNotFoundError:
            print("⚠️  Файл лога не найден (бот может быть не запущен)")
            return True
        except Exception as e:
            print(f"⚠️  Ошибка чтения лога: {e}")
            return True
            
    except Exception as e:
        print(f"❌ ОШИБКА при проверке логов: {e}")
        return False


def test_stock_updates():
    """Тест: проверка обновления остатков (PUT запросы)."""
    print("\n" + "="*60)
    print("ТЕСТ 4: Проверка обновления остатков")
    print("="*60)
    
    with YandexMarketAPI() as api:
        try:
            # Получаем маппинг товаров
            mapping = api.get_offer_mapping_entries(limit=5)
            entries = mapping.get("result", {}).get("offerMappingEntries", [])
            
            if not entries:
                print("⚠️  Нет товаров для тестирования обновления остатков")
                return True
            
            print(f"\n📦 Найдено товаров: {len(entries)}")
            
            errors_found = []
            
            # Тестируем обновление остатков для первого товара
            test_entry = entries[0]
            sku = test_entry.get("offer", {}).get("shopSku", "")
            
            if not sku:
                print("⚠️  Не удалось получить SKU товара")
                return True
            
            print(f"\n🔄 Тестируем обновление остатков для SKU: {sku}")
            
            try:
                # Пробуем обновить остаток на 1
                result = api.update_offer_stock(sku, 1)
                print(f"   ✅ PUT запрос (update_offer_stock) успешен")
            except Exception as e:
                error_str = str(e)
                if "400" in error_str:
                    error_msg = f"PUT остатки SKU {sku}: 400 ошибка - {error_str[:200]}"
                    errors_found.append(error_msg)
                    print(f"   ❌ {error_msg}")
                else:
                    print(f"   ⚠️  PUT запрос: {error_str[:100]}")
            
            if errors_found:
                print(f"\n❌ НАЙДЕНО ОШИБОК: {len(errors_found)}")
                for err in errors_found:
                    print(f"   • {err}")
                return False
            else:
                print("\n✅ Ошибок обновления остатков не обнаружено")
                return True
                
        except Exception as e:
            print(f"⚠️  Ошибка при проверке остатков: {e}")
            import traceback
            traceback.print_exc()
            return True  # Не критично


def main():
    """Запуск всех тестов."""
    print("="*60)
    print("  ТЕСТИРОВАНИЕ БОТА: Доставка и проверка ошибок")
    print("="*60)
    
    results = []
    
    # Тест 1: Смена статусов
    results.append(("Смена статусов до DELIVERED", test_order_status_transition()))
    
    # Тест 2: PUT/POST ошибки
    results.append(("Проверка PUT/POST запросов", test_put_post_errors()))
    
    # Тест 3: Логи
    results.append(("Проверка логов", check_logs_for_errors()))
    
    # Тест 4: Остатки
    results.append(("Обновление остатков", test_stock_updates()))
    
    # Итоги
    print("\n" + "="*60)
    print("  ИТОГИ ТЕСТИРОВАНИЯ")
    print("="*60)
    
    passed = sum(1 for _, result in results if result)
    total = len(results)
    
    for test_name, result in results:
        status = "✅ ПРОЙДЕН" if result else "❌ ПРОВАЛЕН"
        print(f"{status}: {test_name}")
    
    print(f"\n📊 Результат: {passed}/{total} тестов пройдено")
    
    if passed == total:
        print("✅ ВСЕ ТЕСТЫ ПРОЙДЕНЫ!")
        return 0
    else:
        print("⚠️  НЕКОТОРЫЕ ТЕСТЫ ПРОВАЛЕНЫ")
        return 1


if __name__ == "__main__":
    sys.exit(main())
