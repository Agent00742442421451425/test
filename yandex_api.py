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

    def update_order_status(self, order_id, status, substatus=None):
        """
        Обновить статус заказа.
        PUT /campaigns/{campaignId}/orders/{orderId}/status

        Тело запроса (по документации):
        {
          "order": {
            "status": "PROCESSING",
            "substatus": "READY_TO_SHIP"   ← опционально
          }
        }
        """
        url = f"/campaigns/{self.campaign_id}/orders/{order_id}/status"
        order_body = {"status": status}
        if substatus:
            order_body["substatus"] = substatus
        body = {"order": order_body}

        log.info(f"PUT {url}  body={body}")
        response = self.client.put(url, json=body)
        self._raise_on_error(
            response,
            f"Смена статуса заказа {order_id} → {status}/{substatus}",
        )
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
            time.sleep(2)
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
                    log.info(f"Заказ {order_id}: boxes подтверждены ✅")
                except Exception as e:
                    error_str = str(e)
                    if "already" in error_str.lower() or "400" in error_str:
                        results.append(("ОТГРУЗКА", "уже подтверждена"))
                    else:
                        results.append(("ОТГРУЗКА", error_str))
            else:
                results.append(("ОТГРУЗКА", "нет shipment ID"))
        else:
            results.append(("ОТГРУЗКА", "нет shipments"))

        time.sleep(5)  # Увеличиваем время ожидания после boxes

        # ── Шаг 4: → DELIVERY ────────────────────────────────────
        # Делаем несколько попыток с проверкой статуса
        max_attempts = 3
        for attempt in range(max_attempts):
            order_data = self.get_order(order_id)
            order = order_data.get("order", {})
            cur_status = order.get("status", "")
            cur_sub = order.get("substatus", "")
            log.info(f"Заказ {order_id}: перед DELIVERY (попытка {attempt + 1}) → {cur_status}/{cur_sub}")

            if cur_status == "DELIVERY":
                results.append(("DELIVERY", "OK (автоматически после boxes)"))
                log.info(f"Заказ {order_id}: → DELIVERY ✅ (автоматически)")
                break
            elif cur_status == "DELIVERED":
                results.append(("DELIVERY", "пропуск — уже DELIVERED"))
                results.append(("ИТОГ", "Заказ уже доставлен"))
                return results
            elif cur_status == "PROCESSING":
                # После boxes заказ может быть в PROCESSING (включая READY_TO_SHIP)
                # Пытаемся перевести в DELIVERY
                try:
                    self.update_order_status(order_id, "DELIVERY")
                    time.sleep(2)  # Ждем обновления статуса
                    # Проверяем, что статус изменился
                    check_data = self.get_order(order_id)
                    check_order = check_data.get("order", {})
                    if check_order.get("status") == "DELIVERY":
                        results.append(("DELIVERY", "OK"))
                        log.info(f"Заказ {order_id}: → DELIVERY ✅")
                        break
                    else:
                        if attempt < max_attempts - 1:
                            log.warning(f"Заказ {order_id}: статус не изменился после DELIVERY, попытка {attempt + 2}")
                            time.sleep(2)
                            continue
                        else:
                            results.append(("DELIVERY", f"статус не изменился после обновления"))
                except Exception as e:
                    error_str = str(e)
                    if attempt < max_attempts - 1:
                        log.warning(f"Заказ {order_id}: ошибка DELIVERY, попытка {attempt + 2}: {error_str}")
                        time.sleep(2)
                        continue
                    else:
                        results.append(("DELIVERY", str(e)))
                        # Не прерываем — попробуем DELIVERED на случай если маркет уже сменил
            else:
                # Если статус не PROCESSING и не DELIVERY/DELIVERED, пробуем сразу DELIVERED
                results.append(("DELIVERY", f"пропуск — статус {cur_status}/{cur_sub}, попробуем DELIVERED"))
                break
            
            if attempt < max_attempts - 1:
                time.sleep(2)

        time.sleep(2)

        # ── Шаг 5: → DELIVERED ────────────────────────────────────
        # Для DIGITAL товаров делаем несколько попыток перевода в DELIVERED
        max_delivered_attempts = 5
        delivered_success = False
        
        for attempt in range(max_delivered_attempts):
            order_data = self.get_order(order_id)
            order = order_data.get("order", {})
            cur_status = order.get("status", "")
            cur_sub = order.get("substatus", "")
            delivery_type = order.get("delivery", {}).get("type", "")
            log.info(f"Заказ {order_id}: перед DELIVERED (попытка {attempt + 1}) → {cur_status}/{cur_sub}, тип доставки: {delivery_type}")

            if cur_status == "DELIVERED":
                results.append(("DELIVERED", "OK (уже доставлен)"))
                log.info(f"Заказ {order_id}: → DELIVERED ✅")
                delivered_success = True
                break
            elif cur_status == "DELIVERY":
                try:
                    self.update_order_status(order_id, "DELIVERED")
                    time.sleep(2)
                    # Проверяем, что статус изменился
                    check_data = self.get_order(order_id)
                    if check_data.get("order", {}).get("status") == "DELIVERED":
                        results.append(("DELIVERED", "OK"))
                        log.info(f"Заказ {order_id}: → DELIVERED ✅")
                        delivered_success = True
                        break
                    else:
                        if attempt < max_delivered_attempts - 1:
                            log.warning(f"Заказ {order_id}: статус не изменился после DELIVERED, попытка {attempt + 2}")
                            time.sleep(2)
                            continue
                except Exception as e:
                    error_str = str(e)
                    if attempt < max_delivered_attempts - 1:
                        log.warning(f"Заказ {order_id}: ошибка DELIVERED, попытка {attempt + 2}: {error_str}")
                        time.sleep(2)
                        continue
                    else:
                        results.append(("DELIVERED", str(e)))
            elif cur_status == "PROCESSING":
                # Если заказ все еще в PROCESSING после boxes, пробуем напрямую DELIVERED
                # (для цифровых товаров DIGITAL это может работать)
                try:
                    self.update_order_status(order_id, "DELIVERED")
                    time.sleep(2)
                    # Проверяем, что статус изменился
                    check_data = self.get_order(order_id)
                    if check_data.get("order", {}).get("status") == "DELIVERED":
                        results.append(("DELIVERED", "OK (напрямую из PROCESSING)"))
                        log.info(f"Заказ {order_id}: → DELIVERED ✅ (напрямую)")
                        delivered_success = True
                        break
                    else:
                        # Если не получилось напрямую, пробуем через DELIVERY
                        if attempt < max_delivered_attempts - 1:
                            try:
                                self.update_order_status(order_id, "DELIVERY")
                                time.sleep(2)
                                self.update_order_status(order_id, "DELIVERED")
                                time.sleep(2)
                                check_data2 = self.get_order(order_id)
                                if check_data2.get("order", {}).get("status") == "DELIVERED":
                                    results.append(("DELIVERED", "OK (через DELIVERY)"))
                                    log.info(f"Заказ {order_id}: → DELIVERED ✅ (через DELIVERY)")
                                    delivered_success = True
                                    break
                            except Exception as e2:
                                log.warning(f"Заказ {order_id}: ошибка через DELIVERY, попытка {attempt + 2}: {str(e2)}")
                                time.sleep(2)
                                continue
                except Exception as e:
                    error_str = str(e)
                    if attempt < max_delivered_attempts - 1:
                        log.warning(f"Заказ {order_id}: ошибка DELIVERED напрямую, попытка {attempt + 2}: {error_str}")
                        time.sleep(2)
                        continue
                    else:
                        results.append(("DELIVERED", f"ошибка: {str(e)}"))
            else:
                results.append((
                    "DELIVERED",
                    f"невозможно — текущий статус {cur_status}/{cur_sub}",
                ))
                break
            
            if attempt < max_delivered_attempts - 1:
                time.sleep(2)
        
        if not delivered_success:
            log.error(f"Заказ {order_id}: НЕ УДАЛОСЬ перевести в DELIVERED после {max_delivered_attempts} попыток")

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

        API Маркета принимает сообщение как multipart/form-data.
        Content-Type НЕ передаём вручную — httpx ставит его сам
        из параметра data=.
        """
        url = f"/businesses/{self.business_id}/chats/message"
        params = {"chatId": chat_id}

        log.info(f"POST {url}  chatId={chat_id}  len(msg)={len(message_text)}")

        # data= заставляет httpx отправить form-data
        # Content-Type выставится автоматически
        response = self.client.post(
            url,
            params=params,
            data={"message": message_text},
        )
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
