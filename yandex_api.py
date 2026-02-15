"""
Клиент для Yandex Market Partner API (DBS).
Документация: https://yandex.ru/dev/market/partner-api/doc/ru/

Цепочка статусов DBS:
  PROCESSING/STARTED → PROCESSING/READY_TO_SHIP → (boxes) → DELIVERY → DELIVERED

Ошибки, которые мы раньше получали:
  PUT  400 — попытка прыгнуть на DELIVERED минуя DELIVERY
  POST 403 — нет прав на чаты или конфликт Content-Type
"""

import logging
import time
from datetime import date

import httpx

from config import BASE_URL, HEADERS, CAMPAIGN_ID, BUSINESS_ID

log = logging.getLogger(__name__)


class YandexMarketAPI:
    """HTTP-клиент для работы с API Яндекс Маркета."""

    def __init__(self):
        self.base_url = BASE_URL
        self.headers = HEADERS
        self.campaign_id = CAMPAIGN_ID
        self.business_id = BUSINESS_ID
        self.client = httpx.Client(
            base_url=self.base_url,
            headers=self.headers,
            timeout=30.0,
        )

    def close(self):
        """Закрыть HTTP-клиент."""
        self.client.close()

    # ─── Утилита: безопасный вызов с логированием ─────────────────────

    def _raise_on_error(self, response, context=""):
        """Если статус >= 400, логируем и бросаем понятную ошибку."""
        if response.status_code < 400:
            return
        try:
            body = response.json()
            errors = body.get("errors", body.get("error", {}))
            detail = str(errors)
        except Exception:
            detail = response.text[:500]
        msg = f"{context} → HTTP {response.status_code}: {detail}"
        log.error(msg)
        raise RuntimeError(msg)

    # ─── PUT-запросы: Обновление остатков ────────────────────────────
    
    def get_offer_mapping_entries(self, sku=None, limit=50):
        """
        Получить маппинг товаров (offer mapping entries).
        GET /campaigns/{campaignId}/offer-mapping-entries
        
        Нужно для получения offerMappingEntryId и warehouseId для обновления остатков.
        """
        url = f"/campaigns/{self.campaign_id}/offer-mapping-entries"
        params = {"limit": limit}
        if sku:
            params["shopSku"] = sku
        
        log.info(f"GET {url}  params={params}")
        response = self.client.get(url, params=params)
        self._raise_on_error(response, f"Получение маппинга товаров")
        return response.json()
    
    def update_offer_stock(self, sku, count):
        """
        Обновить остаток товара по SKU.
        Использует правильный endpoint для DBS: /campaigns/{campaignId}/offer-mapping-entries/{offerMappingEntryId}/warehouses/{warehouseId}/stock
        
        Args:
            sku: SKU товара (shopSku)
            count: Количество товара на складе
        """
        try:
            # 1. Получаем маппинг товара
            mapping_data = self.get_offer_mapping_entries(sku=sku)
            entries = mapping_data.get("result", {}).get("offerMappingEntries", [])
            
            if not entries:
                raise RuntimeError(f"Товар с SKU {sku} не найден в маппинге")
            
            entry = entries[0]
            offer_mapping_entry_id = entry.get("offerMappingEntry", {}).get("id")
            if not offer_mapping_entry_id:
                raise RuntimeError(f"Не найден offerMappingEntryId для SKU {sku}")
            
            # 2. Получаем warehouse ID (обычно из настроек кампании или первого склада)
            # Для DBS используем первый доступный склад
            warehouses = entry.get("warehouses", [])
            if not warehouses:
                # Если складов нет в маппинге, пробуем получить из кампании
                # Для DBS обычно используется склад по умолчанию
                # Пробуем использовать endpoint без warehouseId или с дефолтным
                log.warning(f"Склад не найден в маппинге для SKU {sku}, пробуем альтернативный метод")
                return self._update_stock_alternative(sku, count)
            
            warehouse_id = warehouses[0].get("id")
            if not warehouse_id:
                log.warning(f"Warehouse ID не найден, пробуем альтернативный метод")
                return self._update_stock_alternative(sku, count)
            
            # 3. Обновляем остаток через правильный endpoint
            url = f"/campaigns/{self.campaign_id}/offer-mapping-entries/{offer_mapping_entry_id}/warehouses/{warehouse_id}/stock"
            body = {
                "count": int(count),
                "type": "FIT"
            }
            
            log.info(f"PUT {url}  sku={sku}, count={count}, entryId={offer_mapping_entry_id}, warehouseId={warehouse_id}")
            response = self.client.put(url, json=body)
            self._raise_on_error(
                response,
                f"Обновление остатка товара {sku} → {count}",
            )
            return response.json()
            
        except Exception as e:
            # Если основной метод не сработал, пробуем альтернативный
            log.warning(f"Ошибка обновления остатка через маппинг: {e}, пробуем альтернативный метод")
            return self._update_stock_alternative(sku, count)
    
    def _update_stock_alternative(self, sku, count):
        """
        Альтернативный метод обновления остатков через /campaigns/{campaignId}/offers/stock
        Используется, если основной метод не работает.
        """
        url = f"/campaigns/{self.campaign_id}/offers/stock"
        
        # Пробуем разные форматы запроса
        formats_to_try = [
            # Формат 1: с items
            {
                "skus": [
                    {
                        "sku": str(sku),
                        "items": [
                            {
                                "count": int(count),
                                "type": "FIT"
                            }
                        ]
                    }
                ]
            },
            # Формат 2: без items, напрямую count
            {
                "skus": [
                    {
                        "sku": str(sku),
                        "count": int(count)
                    }
                ]
            },
            # Формат 3: через offers
            {
                "offers": [
                    {
                        "shopSku": str(sku),
                        "available": int(count) > 0,
                        "count": int(count) if int(count) > 0 else 0
                    }
                ]
            }
        ]
        
        last_error = None
        for i, body in enumerate(formats_to_try, 1):
            try:
                log.info(f"PUT {url} (format {i})  sku={sku}, count={count}, body={body}")
                response = self.client.put(url, json=body)
                
                # Проверяем ответ
                if response.status_code < 400:
                    log.info(f"✅ Остаток обновлен успешно (формат {i})")
                    return response.json()
                else:
                    # Логируем ошибку, но пробуем следующий формат
                    try:
                        error_body = response.json()
                        last_error = f"HTTP {response.status_code}: {error_body}"
                    except:
                        last_error = f"HTTP {response.status_code}: {response.text[:200]}"
                    log.warning(f"Формат {i} не сработал: {last_error}")
                    continue
                    
            except Exception as e:
                last_error = str(e)
                log.warning(f"Ошибка при попытке формата {i}: {last_error}")
                continue
        
        # Если все форматы не сработали, пробрасываем ошибку
        raise RuntimeError(f"Не удалось обновить остаток товара {sku} ни одним из форматов. Последняя ошибка: {last_error}")
    
    def update_multiple_offers_stock(self, sku_counts):
        """
        Обновить остатки нескольких товаров за один запрос.
        Использует альтернативный метод через /campaigns/{campaignId}/offers/stock
        
        Args:
            sku_counts: Словарь {sku: count} или список кортежей [(sku, count), ...]
        """
        url = f"/campaigns/{self.campaign_id}/offers/stock"
        
        # Преобразуем входные данные в список словарей
        if isinstance(sku_counts, dict):
            sku_counts = list(sku_counts.items())
        
        skus = []
        for sku, count in sku_counts:
            skus.append({
                "sku": str(sku),
                "items": [
                    {
                        "count": int(count),
                        "type": "FIT"
                    }
                ]
            })
        
        body = {"skus": skus}
        
        log.info(f"PUT {url}  updating {len(skus)} offers")
        response = self.client.put(url, json=body)
        self._raise_on_error(
            response,
            f"Обновление остатков {len(skus)} товаров",
        )
        return response.json()

    # ─── GET-запросы: Заказы ─────────────────────────────────────────

    def get_orders(self, status=None, page=1, page_size=50, fake=True):
        """
        Получить список заказов магазина.
        GET /campaigns/{campaignId}/orders
        """
        params = {
            "page": page,
            "pageSize": page_size,
            "fake": str(fake).lower(),
        }
        if status:
            params["status"] = status

        url = f"/campaigns/{self.campaign_id}/orders"
        response = self.client.get(url, params=params)
        self._raise_on_error(response, f"GET {url}")
        return response.json()

    def get_order(self, order_id):
        """
        Получить информацию о конкретном заказе.
        GET /campaigns/{campaignId}/orders/{orderId}
        """
        url = f"/campaigns/{self.campaign_id}/orders/{order_id}"
        response = self.client.get(url)
        self._raise_on_error(response, f"GET {url}")
        return response.json()

    # ─── PUT-запросы: Обновление статуса заказа ─────────────────────

    def update_order_status(self, order_id, status, substatus=None, check_current=True, real_delivery_date=None):
        """
        Обновить статус заказа.
        PUT /campaigns/{campaignId}/orders/{orderId}/status

        Для DBS при переходе в DELIVERY/DELIVERED обязательны правильные substatus:
          DELIVERY → substatus=DELIVERY_SERVICE_RECEIVED
          DELIVERED → substatus=DELIVERY_SERVICE_DELIVERED

        real_delivery_date: для DBS при переходе в DELIVERED не в день доставки (формат YYYY-MM-DD).
        """
        # Проверяем текущий статус перед обновлением, чтобы избежать лишних запросов
        if check_current:
            try:
                order_data = self.get_order(order_id)
                current_order = order_data.get("order", {})
                current_status = current_order.get("status", "")
                current_sub = current_order.get("substatus", "")
                if current_status == status and (substatus is None or current_sub == substatus):
                    log.info(f"Заказ {order_id}: статус уже {status}/{substatus}, пропускаем обновление")
                    return {"order": {"status": status, "substatus": substatus or current_sub}}
            except Exception as e:
                log.warning(f"Не удалось проверить текущий статус заказа {order_id}: {e}, продолжаем обновление")

        url = f"/campaigns/{self.campaign_id}/orders/{order_id}/status"
        # DBS требует субстатус для DELIVERY/DELIVERED — без него API возвращает 400 STATUS_NOT_ALLOWED
        if status == "DELIVERY" and not substatus:
            substatus = "DELIVERY_SERVICE_RECEIVED"
        if status == "DELIVERED" and not substatus:
            substatus = "DELIVERY_SERVICE_DELIVERED"
        order_body = {"status": status}
        if substatus:
            order_body["substatus"] = substatus
        if real_delivery_date and status in ("DELIVERED", "PICKUP"):
            order_body["delivery"] = {"dates": {"realDeliveryDate": real_delivery_date}}
        body = {"order": order_body}

        log.info(f"PUT {url}  body={body}")
        response = self.client.put(url, json=body)
        
        # Обрабатываем ошибки более мягко
        try:
            self._raise_on_error(
                response,
                f"Смена статуса заказа {order_id} → {status}/{substatus}",
            )
        except RuntimeError as e:
            error_str = str(e)
            # Если ошибка связана с тем, что статус уже установлен или переход невозможен,
            # проверяем текущий статус и возвращаем его
            if "400" in error_str or "already" in error_str.lower() or "invalid" in error_str.lower():
                try:
                    order_data = self.get_order(order_id)
                    current_order = order_data.get("order", {})
                    current_status = current_order.get("status", "")
                    current_sub = current_order.get("substatus", "")
                    log.warning(f"Заказ {order_id}: не удалось установить {status}/{substatus}, текущий статус: {current_status}/{current_sub}")
                    # Если текущий статус уже целевой, возвращаем успех
                    if current_status == status:
                        return {"order": {"status": status, "substatus": current_sub}}
                except:
                    pass
            raise
        
        return response.json()

    def set_order_boxes(self, order_id, shipment_id, items=None):
        """
        Подтвердить отгрузку — установить boxes для shipment.
        PUT /campaigns/{campaignId}/orders/{orderId}/delivery/shipments/{shipmentId}/boxes

        items — список товаров из order["items"].
        Для каждого передаём id и count, чтобы API не ругался.
        """
        url = (
            f"/campaigns/{self.campaign_id}/orders/{order_id}"
            f"/delivery/shipments/{shipment_id}/boxes"
        )

        # Формируем список товаров для коробки
        box_items = []
        if items:
            for item in items:
                item_id = item.get("id")
                if item_id:
                    box_items.append({
                        "id": item_id,
                        "count": item.get("count", 1),
                    })

        body = {
            "boxes": [
                {
                    "fulfilmentId": f"digital-{order_id}",
                    "weight": 100,   # грамм (мин. значение > 0)
                    "width": 1,      # см
                    "height": 1,
                    "depth": 1,
                    "items": box_items,
                }
            ]
        }

        log.info(f"PUT {url}  boxes items={len(box_items)}")
        response = self.client.put(url, json=body)
        self._raise_on_error(response, f"Boxes заказа {order_id}")
        return response.json()

    # ─── Пошаговые переходы статусов (для кнопок админки) ───────────

    def ship_ready_and_boxes_only(self, order_id):
        """
        Только шаги 1–3: READY_TO_SHIP + boxes. Не переводит в DELIVERY/DELIVERED.
        Возвращает список (step, result). После этого наш статус «Отгружен».
        """
        results = []
        order_data = self.get_order(order_id)
        order = order_data.get("order", {})
        cur_status = order.get("status", "")
        cur_sub = order.get("substatus", "")

        if cur_status == "PROCESSING" and cur_sub in ("STARTED", ""):
            try:
                self.update_order_status(order_id, "PROCESSING", "READY_TO_SHIP")
                results.append(("READY_TO_SHIP", "OK"))
                time.sleep(1)
            except Exception as e:
                results.append(("READY_TO_SHIP", str(e)))
                return results
        elif cur_status == "PROCESSING" and cur_sub == "READY_TO_SHIP":
            results.append(("READY_TO_SHIP", "уже в этом статусе"))
        elif cur_status in ("DELIVERY", "DELIVERED"):
            results.append(("READY_TO_SHIP", f"пропуск — уже {cur_status}"))
            return results

        order_data = self.get_order(order_id)
        order = order_data.get("order", {})
        cur_status = order.get("status", "")
        delivery = order.get("delivery", {})
        shipments = delivery.get("shipments", [])
        items = order.get("items", [])

        if cur_status in ("DELIVERY", "DELIVERED"):
            results.append(("ОТГРУЗКА", "пропуск"))
            return results
        if shipments:
            shipment_id = shipments[0].get("id")
            if shipment_id:
                try:
                    self.set_order_boxes(order_id, shipment_id, items)
                    results.append(("ОТГРУЗКА", "OK"))
                except Exception as e:
                    if "already" in str(e).lower() or "400" in str(e):
                        results.append(("ОТГРУЗКА", "уже подтверждена"))
                    else:
                        results.append(("ОТГРУЗКА", str(e)[:80]))
            else:
                results.append(("ОТГРУЗКА", "нет shipment ID"))
        else:
            if delivery.get("type") == "DIGITAL":
                results.append(("ОТГРУЗКА", "пропуск — DIGITAL без shipments"))
            else:
                results.append(("ОТГРУЗКА", "нет shipments"))
        return results

    def set_status_to_delivery(self, order_id):
        """
        Перевести заказ в DELIVERY. Обязательно: READY_TO_SHIP → boxes (если есть shipments) → DELIVERY.
        Без подтверждения boxes API возвращает 400 STATUS_NOT_ALLOWED.
        """
        order_data = self.get_order(order_id)
        order = order_data.get("order", {})
        cur_status = order.get("status", "")
        cur_sub = order.get("substatus", "")
        if cur_status == "DELIVERY":
            return True, "уже в статусе Отправлен"
        if cur_status == "DELIVERED":
            return True, "заказ уже доставлен"
        if cur_status != "PROCESSING" or cur_sub != "READY_TO_SHIP":
            try:
                self.update_order_status(order_id, "PROCESSING", "READY_TO_SHIP")
                time.sleep(1)
                order_data = self.get_order(order_id)
                order = order_data.get("order", {})
                cur_status, cur_sub = order.get("status", ""), order.get("substatus", "")
            except Exception as e:
                return False, f"READY_TO_SHIP: {str(e)[:80]}"
        if cur_status != "PROCESSING" or cur_sub != "READY_TO_SHIP":
            return False, f"текущий статус {cur_status}/{cur_sub}"

        # Обязательный шаг перед DELIVERY: подтвердить отгрузку (boxes). Без этого API даёт 400 STATUS_NOT_ALLOWED.
        order_data = self.get_order(order_id)
        order = order_data.get("order", {})
        delivery = order.get("delivery", {})
        shipments = delivery.get("shipments", [])
        items = order.get("items", [])
        if shipments:
            shipment_id = shipments[0].get("id")
            if shipment_id:
                try:
                    self.set_order_boxes(order_id, shipment_id, items)
                    time.sleep(1)
                except Exception as e:
                    if "already" in str(e).lower() or "already confirmed" in str(e).lower():
                        pass
                    else:
                        return False, f"boxes: {str(e)[:80]}"

        try:
            self.update_order_status(order_id, "DELIVERY", "DELIVERY_SERVICE_RECEIVED")
            return True, "OK"
        except Exception as e:
            return False, str(e)[:120]

    def set_status_to_delivered(self, order_id):
        """
        Перевести заказ в DELIVERED (только из DELIVERY).
        Возвращает (ok: bool, message: str).
        """
        order_data = self.get_order(order_id)
        order = order_data.get("order", {})
        cur_status = order.get("status", "")
        if cur_status == "DELIVERED":
            return True, "уже доставлен"
        if cur_status != "DELIVERY":
            return False, f"нужен статус Отправлен, текущий: {cur_status}"
        try:
            self.update_order_status(
                order_id, "DELIVERED", "DELIVERY_SERVICE_DELIVERED",
                real_delivery_date=date.today().isoformat(),
            )
            return True, "OK"
        except Exception as e:
            return False, str(e)[:120]

    # ─── Полная цепочка доставки (DBS, цифровой товар) ───────────────

    def deliver_digital_order(self, order_id):
        """
        Цепочка доставки цифрового товара (DBS):

        1. PROCESSING/STARTED  → PROCESSING/READY_TO_SHIP
        2. Подтверждение отгрузки (boxes)
        3. → DELIVERY
        4. → DELIVERED

        Между шагами перечитываем статус, чтобы не сломать переход.
        """
        results = []

        # ── Шаг 1: Получаем текущий статус ────────────────────────
        order_data = self.get_order(order_id)
        order = order_data.get("order", {})
        cur_status = order.get("status", "")
        cur_sub = order.get("substatus", "")
        log.info(f"Заказ {order_id}: текущий статус {cur_status}/{cur_sub}")

        # ── Шаг 2: PROCESSING → READY_TO_SHIP ────────────────────
        if cur_status == "PROCESSING" and cur_sub in ("STARTED", ""):
            try:
                self.update_order_status(order_id, "PROCESSING", "READY_TO_SHIP")
                results.append(("READY_TO_SHIP", "OK"))
                log.info(f"Заказ {order_id}: → READY_TO_SHIP ✅")
            except Exception as e:
                results.append(("READY_TO_SHIP", str(e)))
                return results
            time.sleep(1)
        elif cur_status == "PROCESSING" and cur_sub == "READY_TO_SHIP":
            results.append(("READY_TO_SHIP", "уже в этом статусе"))
        elif cur_status in ("DELIVERY", "DELIVERED"):
            results.append(("READY_TO_SHIP", f"пропуск — заказ уже {cur_status}"))
        else:
            results.append(("READY_TO_SHIP", f"пропуск — {cur_status}/{cur_sub}"))

        # ── Шаг 3: Подтверждение boxes (отгрузка) ────────────────
        # Перечитываем заказ — после READY_TO_SHIP могут появиться shipments
        order_data = self.get_order(order_id)
        order = order_data.get("order", {})
        cur_status = order.get("status", "")
        delivery = order.get("delivery", {})
        delivery_type = delivery.get("type", "")
        shipments = delivery.get("shipments", [])
        items = order.get("items", [])

        if cur_status in ("DELIVERY", "DELIVERED"):
            results.append(("ОТГРУЗКА", "пропуск — заказ уже отправлен"))
        elif shipments:
            shipment_id = shipments[0].get("id")
            if shipment_id:
                try:
                    self.set_order_boxes(order_id, shipment_id, items)
                    results.append(("ОТГРУЗКА", "OK"))
                    log.info(f"Заказ {order_id}: boxes подтверждены ✅ (тип доставки: {delivery_type})")
                except Exception as e:
                    error_str = str(e)
                    if "already" in error_str.lower() or "400" in error_str or "already confirmed" in error_str.lower():
                        results.append(("ОТГРУЗКА", "уже подтверждена"))
                        log.info(f"Заказ {order_id}: boxes уже подтверждены")
                    else:
                        results.append(("ОТГРУЗКА", error_str))
                        log.error(f"Заказ {order_id}: ошибка подтверждения boxes: {error_str}")
            else:
                results.append(("ОТГРУЗКА", "нет shipment ID"))
                log.warning(f"Заказ {order_id}: нет shipment ID в shipments")
        else:
            # Для DIGITAL товаров shipments могут отсутствовать
            # В этом случае пробуем сразу перейти к DELIVERY/DELIVERED
            if delivery_type == "DIGITAL":
                results.append(("ОТГРУЗКА", "пропуск — DIGITAL товар, нет shipments"))
                log.info(f"Заказ {order_id}: DIGITAL товар без shipments, пропускаем boxes")
            else:
                results.append(("ОТГРУЗКА", "нет shipments"))
                log.warning(f"Заказ {order_id}: нет shipments (тип доставки: {delivery_type})")

        time.sleep(1)

        # ── Шаг 4: → DELIVERY ────────────────────────────────────
        # Проверяем статус и переходим в DELIVERY только если нужно
        order_data = self.get_order(order_id)
        order = order_data.get("order", {})
        cur_status = order.get("status", "")
        cur_sub = order.get("substatus", "")
        log.info(f"Заказ {order_id}: перед DELIVERY → {cur_status}/{cur_sub}")

        if cur_status == "DELIVERY":
            results.append(("DELIVERY", "OK (автоматически после boxes)"))
            log.info(f"Заказ {order_id}: → DELIVERY ✅ (автоматически)")
        elif cur_status == "DELIVERED":
            results.append(("DELIVERY", "пропуск — уже DELIVERED"))
            results.append(("ИТОГ", "Заказ уже доставлен"))
            return results
        elif cur_status == "PROCESSING":
            # API разрешает DELIVERY только из PROCESSING/READY_TO_SHIP. Сначала гарантируем субстатус.
            if cur_sub != "READY_TO_SHIP":
                try:
                    self.update_order_status(order_id, "PROCESSING", "READY_TO_SHIP", check_current=True)
                    results.append(("DELIVERY", "сначала выставлен READY_TO_SHIP"))
                    time.sleep(1)
                    order_data = self.get_order(order_id)
                    order = order_data.get("order", {})
                    cur_status = order.get("status", "")
                    cur_sub = order.get("substatus", "")
                    log.info(f"Заказ {order_id}: после READY_TO_SHIP → {cur_status}/{cur_sub}")
                except Exception as e:
                    results.append(("DELIVERY", f"не удалось выставить READY_TO_SHIP: {str(e)[:80]}"))
                    cur_status = ""  # не пробуем DELIVERY
            if cur_status == "PROCESSING" and cur_sub == "READY_TO_SHIP":
                try:
                    self.update_order_status(order_id, "DELIVERY", "DELIVERY_SERVICE_RECEIVED", check_current=True)
                    time.sleep(1)
                    check_data = self.get_order(order_id)
                    check_order = check_data.get("order", {})
                    if check_order.get("status") == "DELIVERY":
                        results.append(("DELIVERY", "OK"))
                        log.info(f"Заказ {order_id}: → DELIVERY ✅")
                    else:
                        if check_order.get("status") == "DELIVERED":
                            results.append(("DELIVERY", "пропуск — автоматически перешел в DELIVERED"))
                            log.info(f"Заказ {order_id}: автоматически перешел в DELIVERED")
                        else:
                            results.append(("DELIVERY", f"статус не изменился, текущий: {check_order.get('status')}"))
                except Exception as e:
                    error_str = str(e)
                    try:
                        check_data = self.get_order(order_id)
                        check_status = check_data.get("order", {}).get("status")
                        if check_status == "DELIVERY":
                            results.append(("DELIVERY", "OK (изменился автоматически)"))
                            log.info(f"Заказ {order_id}: → DELIVERY ✅ (автоматически)")
                        elif check_status == "DELIVERED":
                            results.append(("DELIVERY", "пропуск — автоматически перешел в DELIVERED"))
                        else:
                            results.append(("DELIVERY", f"ошибка: {error_str[:100]}"))
                    except Exception:
                        results.append(("DELIVERY", f"ошибка: {error_str[:100]}"))
            elif cur_status == "PROCESSING":
                results.append(("DELIVERY", f"пропуск — нужен субстатус READY_TO_SHIP, текущий: {cur_sub}"))
        else:
            # Если статус не PROCESSING и не DELIVERY/DELIVERED, пропускаем DELIVERY
            results.append(("DELIVERY", f"пропуск — статус {cur_status}/{cur_sub}, попробуем DELIVERED"))

        time.sleep(1)

        # ── Шаг 5: → DELIVERED ────────────────────────────────────
        # Для DIGITAL товаров делаем одну попытку перевода в DELIVERED
        # с проверкой текущего статуса перед обновлением
        order_data = self.get_order(order_id)
        order = order_data.get("order", {})
        cur_status = order.get("status", "")
        cur_sub = order.get("substatus", "")
        delivery_type = order.get("delivery", {}).get("type", "")
        log.info(f"Заказ {order_id}: перед DELIVERED → {cur_status}/{cur_sub}, тип доставки: {delivery_type}")

        if cur_status == "DELIVERED":
            results.append(("DELIVERED", "OK (уже доставлен)"))
            log.info(f"Заказ {order_id}: → DELIVERED ✅")
        elif cur_status == "DELIVERY":
            # Стандартный переход: DELIVERY → DELIVERED
            try:
                self.update_order_status(
                    order_id, "DELIVERED", "DELIVERY_SERVICE_DELIVERED",
                    check_current=True, real_delivery_date=date.today().isoformat()
                )
                time.sleep(1)  # Даем время API обработать
                # Проверяем результат
                check_data = self.get_order(order_id)
                if check_data.get("order", {}).get("status") == "DELIVERED":
                    results.append(("DELIVERED", "OK"))
                    log.info(f"Заказ {order_id}: → DELIVERED ✅")
                else:
                    results.append(("DELIVERED", f"статус не изменился, текущий: {check_data.get('order', {}).get('status')}"))
            except Exception as e:
                error_str = str(e)
                # Проверяем, может статус уже изменился
                try:
                    check_data = self.get_order(order_id)
                    if check_data.get("order", {}).get("status") == "DELIVERED":
                        results.append(("DELIVERED", "OK (изменился автоматически)"))
                        log.info(f"Заказ {order_id}: → DELIVERED ✅ (автоматически)")
                    else:
                        results.append(("DELIVERED", f"ошибка: {error_str[:100]}"))
                except:
                    results.append(("DELIVERED", f"ошибка: {error_str[:100]}"))
        elif cur_status == "PROCESSING":
            # Для DIGITAL товаров после boxes может быть разрешен прямой переход
            # Но сначала пробуем через DELIVERY
            try:
                # Пробуем сначала DELIVERY
                if cur_sub == "READY_TO_SHIP":
                    try:
                        self.update_order_status(order_id, "DELIVERY", "DELIVERY_SERVICE_RECEIVED", check_current=True)
                        time.sleep(1)
                        check_data = self.get_order(order_id)
                        if check_data.get("order", {}).get("status") == "DELIVERY":
                            # Теперь пробуем DELIVERED
                            self.update_order_status(
                                order_id, "DELIVERED", "DELIVERY_SERVICE_DELIVERED",
                                check_current=True, real_delivery_date=date.today().isoformat()
                            )
                            time.sleep(1)
                            check_data2 = self.get_order(order_id)
                            if check_data2.get("order", {}).get("status") == "DELIVERED":
                                results.append(("DELIVERED", "OK (через DELIVERY)"))
                                log.info(f"Заказ {order_id}: → DELIVERED ✅ (через DELIVERY)")
                            else:
                                results.append(("DELIVERED", f"не удалось перейти из DELIVERY, текущий: {check_data2.get('order', {}).get('status')}"))
                        else:
                            # Если DELIVERY не сработал, пробуем напрямую DELIVERED
                            self.update_order_status(
                                order_id, "DELIVERED", "DELIVERY_SERVICE_DELIVERED",
                                check_current=True, real_delivery_date=date.today().isoformat()
                            )
                            time.sleep(1)
                            check_data3 = self.get_order(order_id)
                            if check_data3.get("order", {}).get("status") == "DELIVERED":
                                results.append(("DELIVERED", "OK (напрямую из PROCESSING)"))
                                log.info(f"Заказ {order_id}: → DELIVERED ✅ (напрямую)")
                            else:
                                results.append(("DELIVERED", f"не удалось перейти, текущий: {check_data3.get('order', {}).get('status')}"))
                    except Exception as e2:
                        error_str = str(e2)
                        # Проверяем текущий статус
                        try:
                            check_data = self.get_order(order_id)
                            current_status = check_data.get("order", {}).get("status")
                            if current_status == "DELIVERED":
                                results.append(("DELIVERED", "OK (изменился автоматически)"))
                            else:
                                results.append(("DELIVERED", f"ошибка: {error_str[:100]}"))
                        except:
                            results.append(("DELIVERED", f"ошибка: {error_str[:100]}"))
                else:
                    results.append(("DELIVERED", f"пропуск — PROCESSING/{cur_sub}, не READY_TO_SHIP"))
            except Exception as e:
                error_str = str(e)
                results.append(("DELIVERED", f"ошибка: {error_str[:100]}"))
        else:
            results.append((
                "DELIVERED",
                f"невозможно — текущий статус {cur_status}/{cur_sub}",
            ))

        # Если всё ещё не DELIVERED (например застряли в DELIVERY) — ещё одна попытка
        order_data = self.get_order(order_id)
        final_status = order_data.get("order", {}).get("status", "")
        if final_status == "DELIVERY":
            time.sleep(1)
            try:
                self.update_order_status(
                    order_id, "DELIVERED", "DELIVERY_SERVICE_DELIVERED",
                    check_current=True, real_delivery_date=date.today().isoformat()
                )
                time.sleep(1)
                check = self.get_order(order_id)
                if check.get("order", {}).get("status") == "DELIVERED":
                    results.append(("DELIVERED (повтор)", "OK"))
                    log.info(f"Заказ {order_id}: → DELIVERED ✅ (повторная попытка)")
            except Exception as e:
                log.warning(f"Заказ {order_id}: повтор DELIVERED не удался: {e}")

        results.append(("ИТОГ", "Обработка завершена"))
        return results

    # ─── GET-запросы: Кабинет и магазин ──────────────────────────────

    def get_campaigns(self):
        """
        Получить список магазинов (кампаний) в кабинете.
        GET /businesses/{businessId}/campaigns
        """
        url = f"/businesses/{self.business_id}/campaigns"
        response = self.client.get(url)
        self._raise_on_error(response, f"GET {url}")
        return response.json()

    def get_campaign_info(self):
        """
        Получить информацию о магазине (кампании).
        GET /campaigns/{campaignId}
        """
        url = f"/campaigns/{self.campaign_id}"
        response = self.client.get(url)
        self._raise_on_error(response, f"GET {url}")
        return response.json()

    # ─── POST-запросы: Чаты (отправка сообщений покупателю) ─────────

    def create_chat(self, order_id):
        """
        Создать новый чат с покупателем по заказу.
        POST /businesses/{businessId}/chats/new

        ⚠️ Если получаем 403 — скорее всего у API-ключа нет
        разрешения «Чаты с покупателями».
        Включите его: Кабинет → Настройки → API и модули → API-ключи.
        """
        url = f"/businesses/{self.business_id}/chats/new"
        body = {"orderId": order_id}
        log.info(f"POST {url}  body={body}")
        response = self.client.post(url, json=body)
        if response.status_code == 403:
            log.error(
                "403 Forbidden при создании чата. "
                "Проверьте, что у API-ключа есть разрешение «Чаты с покупателями» "
                "в настройках кабинета Яндекс Маркета."
            )
        self._raise_on_error(response, f"Создание чата для заказа {order_id}")
        return response.json()

    def send_chat_message(self, chat_id, message_text):
        """
        Отправить текстовое сообщение в чат покупателю.
        POST /businesses/{businessId}/chats/message

        API Маркета принимает только application/json (не x-www-form-urlencoded).
        """
        url = f"/businesses/{self.business_id}/chats/message"
        params = {"chatId": chat_id}
        body = {"message": message_text}

        log.info(f"POST {url}  chatId={chat_id}  len(msg)={len(message_text)}")

        response = self.client.post(url, params=params, json=body)
        if response.status_code == 403:
            log.error(
                "403 Forbidden при отправке сообщения. "
                "Проверьте разрешение «Чаты с покупателями» у API-ключа."
            )
        self._raise_on_error(response, f"Отправка сообщения в чат {chat_id}")
        return response.json()

    def send_message_to_buyer(self, order_id, message_text):
        """
        Создать чат (если нет) и отправить сообщение покупателю.
        Возвращает chat_id и результат отправки.
        """
        # 1. Пытаемся найти существующий чат по заказу
        chats_data = self.get_chats(order_id=order_id)
        chats = chats_data.get("result", {}).get("chats", [])

        if chats:
            chat_id = chats[0]["chatId"]
        else:
            # 2. Создаём новый чат
            new_chat = self.create_chat(order_id)
            chat_id = new_chat.get("result", {}).get("chatId")

        # 3. Отправляем сообщение
        result = self.send_chat_message(chat_id, message_text)
        return {"chatId": chat_id, "result": result}

    # ─── POST-запросы: Получение чатов ────────────────────────────────

    def get_chats(self, order_id=None, chat_type=None, page_token=None):
        """
        Получить список чатов.
        POST /businesses/{businessId}/chats
        (Маркет использует POST для получения чатов с фильтрами)

        ⚠️ 403 = нет прав на чаты у API-ключа.
        """
        url = f"/businesses/{self.business_id}/chats"
        body = {}
        if order_id:
            body["orderIds"] = [int(order_id)]
        if chat_type:
            body["types"] = [chat_type]

        params = {}
        if page_token:
            params["page_token"] = page_token

        log.info(f"POST {url}  body={body}")
        response = self.client.post(url, json=body, params=params)
        if response.status_code == 403:
            log.error(
                "403 Forbidden при получении чатов. "
                "Проверьте разрешение «Чаты с покупателями» у API-ключа "
                "в настройках кабинета: Настройки → API и модули → API-ключи."
            )
        self._raise_on_error(response, f"Получение чатов")
        return response.json()

    def get_chat_history(self, chat_id, page_token=None):
        """
        Получить историю сообщений чата.
        POST /businesses/{businessId}/chats/history
        """
        url = f"/businesses/{self.business_id}/chats/history"
        body = {"chatId": chat_id}

        params = {}
        if page_token:
            params["page_token"] = page_token

        response = self.client.post(url, json=body, params=params)
        self._raise_on_error(response, f"История чата {chat_id}")
        return response.json()

    # ─── Утилиты ─────────────────────────────────────────────────────

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()
