# ═══════════════════════════════════════════════════════════════
# Handler — обработчики (карта для навигации и отладки)
# ═══════════════════════════════════════════════════════════════
# Все обработчики кнопок и сообщений находятся в bot.py.
# Ниже — соответствие callback_data / команды → функция и краткое описание.
# При ошибках в логах ищите по callback_data или имени функции.
# ═══════════════════════════════════════════════════════════════

HANDLERS_MAP = {
    # Inline-кнопки (callback_data → функция в bot.py)
    "orders_new": "show_orders(query, status='PROCESSING', page=1) — список новых заказов",
    "orders_processing_page_*": "show_orders(query, status='PROCESSING', page=N) — пагинация новых",
    "orders_history": "show_orders_history(query) — история заказов",
    "orders_history_page_*": "show_orders_history(query, page=N) — пагинация истории",
    "order_check": "подсказка /order <id>",
    "stock_info": "show_stock_info(query) — информация о складе",
    "sync_stock": "sync_stock_handler(query) — синхронизация остатков",
    "order_detail_<id>": "show_order_detail(query, order_id) — детали заказа + кнопки этапов",
    "auto_deliver_<id>": "auto_deliver_account(query, order_id) — выдать аккаунт + отгрузить",
    "manual_process_<id>": "manual_process_order(query, order_id, context) — ручной ввод данных",
    "order_confirm_<id>": "confirm_order(query, order_id) — подтвердить передачу (READY_TO_SHIP)",
    "force_delivered_<id>": "force_update_to_delivered(query, order_id) — принудительно DELIVERED",
    "step_delivery_<id>": "step_delivery_handler(query, order_id) — этап «Отправить» (DELIVERY)",
    "step_delivered_<id>": "step_delivered_handler(query, order_id) — этап «Доставлен» (DELIVERED)",
    "add_accounts": "start_add_accounts(query, context) — добавление аккаунтов на склад",
    "back_menu": "возврат в главное меню",
}

# Этапы заказа (our_status в orders.json)
OUR_STATUS_FLOW = [
    "НОВЫЙ",      # только что создан → кнопки: Выдать / Ручная / Подтвердить
    "ОТГРУЖЕН",  # выдан ключ, READY_TO_SHIP + boxes → кнопка: Отправить
    "ОТПРАВЛЕН", # в Маркете DELIVERY → кнопка: Доставлен
    "ЗАВЕРШЕН",  # в Маркете DELIVERED, заказ не в заявках
]

# Цепочка статусов API (не пропускать этапы)
API_STATUS_FLOW = [
    "PROCESSING/STARTED или ''",
    "PROCESSING/READY_TO_SHIP",
    "boxes (отгрузка)",
    "DELIVERY (Отправлен)",
    "DELIVERED (Доставлен)",
]
