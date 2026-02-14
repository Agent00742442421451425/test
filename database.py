"""
PostgreSQL база данных для бота Яндекс Маркет DBS.

Таблицы:
  • orders — история всех заказов
"""

import logging
from datetime import datetime
from contextlib import contextmanager

import psycopg2
from psycopg2 import pool
from psycopg2.extras import RealDictCursor

from config import DATABASE_URL

log = logging.getLogger(__name__)

# Connection pool
_connection_pool = None


def _get_pool():
    """Получить connection pool (создаёт при первом вызове)."""
    global _connection_pool
    if _connection_pool is None:
        try:
            _connection_pool = psycopg2.pool.SimpleConnectionPool(
                1, 20, DATABASE_URL
            )
            log.info("Connection pool создан")
        except Exception as e:
            log.error(f"Ошибка создания connection pool: {e}")
            raise
    return _connection_pool


@contextmanager
def _get_connection():
    """Контекстный менеджер для получения соединения из pool."""
    pool = _get_pool()
    conn = pool.getconn()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        pool.putconn(conn)


# ══════════════════════════════════════════════════════════════════════
#   Инициализация БД
# ══════════════════════════════════════════════════════════════════════

def init_db():
    """Создать таблицы в БД, если не существуют."""
    try:
        with _get_connection() as conn:
            with conn.cursor() as cur:
                # Таблица orders
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS orders (
                        id SERIAL PRIMARY KEY,
                        order_id BIGINT NOT NULL UNIQUE,
                        status VARCHAR(50) DEFAULT 'PROCESSING',
                        substatus VARCHAR(50) DEFAULT '',
                        our_status VARCHAR(50) DEFAULT 'НОВЫЙ',
                        product VARCHAR(500) DEFAULT '',
                        buyer_name VARCHAR(255) DEFAULT '',
                        total DECIMAL(10, 2) DEFAULT 0,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        delivered_at TIMESTAMP,
                        account_login VARCHAR(255),
                        delivery_type VARCHAR(50) DEFAULT '',
                        notes TEXT DEFAULT '',
                        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    )
                """)
                
                # Индексы для производительности
                cur.execute("""
                    CREATE INDEX IF NOT EXISTS idx_orders_order_id 
                    ON orders(order_id)
                """)
                cur.execute("""
                    CREATE INDEX IF NOT EXISTS idx_orders_our_status 
                    ON orders(our_status)
                """)
                cur.execute("""
                    CREATE INDEX IF NOT EXISTS idx_orders_created_at 
                    ON orders(created_at DESC)
                """)
                
                conn.commit()
                log.info("Таблицы БД инициализированы")
    except Exception as e:
        log.error(f"Ошибка инициализации БД: {e}")
        raise


# ══════════════════════════════════════════════════════════════════════
#   ЗАКАЗЫ — CRUD
# ══════════════════════════════════════════════════════════════════════

def save_order(order_id, status="PROCESSING", substatus="", our_status="НОВЫЙ",
               product="", buyer_name="", total=0, created_at="",
               delivered_at="", account_login="", delivery_type="",
               notes=""):
    """
    Сохранить/обновить заказ в БД.
    Если заказ уже есть — обновляет поля.
    Если нет — добавляет.
    """
    init_db()
    
    # Парсим даты
    created_at_ts = None
    if created_at:
        try:
            # Пробуем разные форматы
            for fmt in ["%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S", "%d-%m-%Y %H:%M:%S"]:
                try:
                    created_at_ts = datetime.strptime(created_at, fmt)
                    break
                except ValueError:
                    continue
        except Exception:
            pass
    
    delivered_at_ts = None
    if delivered_at:
        try:
            for fmt in ["%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S", "%d-%m-%Y %H:%M:%S"]:
                try:
                    delivered_at_ts = datetime.strptime(delivered_at, fmt)
                    break
                except ValueError:
                    continue
        except Exception:
            pass
    
    with _get_connection() as conn:
        with conn.cursor() as cur:
            # Проверяем существование заказа
            cur.execute("SELECT id FROM orders WHERE order_id = %s", (order_id,))
            exists = cur.fetchone()
            
            if exists:
                # Обновляем существующий
                update_fields = []
                update_values = []
                
                if status:
                    update_fields.append("status = %s")
                    update_values.append(status)
                if substatus is not None:
                    update_fields.append("substatus = %s")
                    update_values.append(substatus)
                if our_status:
                    update_fields.append("our_status = %s")
                    update_values.append(our_status)
                if product:
                    update_fields.append("product = %s")
                    update_values.append(product)
                if buyer_name:
                    update_fields.append("buyer_name = %s")
                    update_values.append(buyer_name)
                if total:
                    update_fields.append("total = %s")
                    update_values.append(total)
                if delivered_at_ts:
                    update_fields.append("delivered_at = %s")
                    update_values.append(delivered_at_ts)
                if account_login is not None:
                    update_fields.append("account_login = %s")
                    update_values.append(account_login)
                if delivery_type:
                    update_fields.append("delivery_type = %s")
                    update_values.append(delivery_type)
                if notes is not None:
                    update_fields.append("notes = %s")
                    update_values.append(notes)
                
                update_fields.append("updated_at = CURRENT_TIMESTAMP")
                
                if update_fields:
                    update_values.append(order_id)
                    cur.execute(
                        f"UPDATE orders SET {', '.join(update_fields)} WHERE order_id = %s",
                        update_values
                    )
            else:
                # Новый заказ
                cur.execute("""
                    INSERT INTO orders (
                        order_id, status, substatus, our_status, product, buyer_name, total,
                        created_at, delivered_at, account_login, delivery_type, notes
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """, (
                    order_id, status, substatus, our_status, product, buyer_name, total,
                    created_at_ts, delivered_at_ts, account_login, delivery_type, notes
                ))
    
    log.info(f"Заказ {order_id}: сохранён в БД ({our_status})")


def update_order_status(order_id, status=None, substatus=None, our_status=None,
                        account_login=None, delivered_at=None, notes=None):
    """Обновить поля заказа в БД."""
    init_db()
    
    delivered_at_ts = None
    if delivered_at:
        try:
            for fmt in ["%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S", "%d-%m-%Y %H:%M:%S"]:
                try:
                    delivered_at_ts = datetime.strptime(delivered_at, fmt)
                    break
                except ValueError:
                    continue
        except Exception:
            pass
    
    with _get_connection() as conn:
        with conn.cursor() as cur:
            update_fields = []
            update_values = []
            
            if status is not None:
                update_fields.append("status = %s")
                update_values.append(status)
            if substatus is not None:
                update_fields.append("substatus = %s")
                update_values.append(substatus)
            if our_status is not None:
                update_fields.append("our_status = %s")
                update_values.append(our_status)
            if account_login is not None:
                update_fields.append("account_login = %s")
                update_values.append(account_login)
            if delivered_at_ts is not None:
                update_fields.append("delivered_at = %s")
                update_values.append(delivered_at_ts)
            if notes is not None:
                update_fields.append("notes = %s")
                update_values.append(notes)
            
            update_fields.append("updated_at = CURRENT_TIMESTAMP")
            
            if update_fields:
                update_values.append(order_id)
                cur.execute(
                    f"UPDATE orders SET {', '.join(update_fields)} WHERE order_id = %s",
                    update_values
                )


def get_order_from_db(order_id):
    """Получить заказ из БД по ID. Возвращает dict или None."""
    init_db()
    
    with _get_connection() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("""
                SELECT order_id, status, substatus, our_status, product, buyer_name, total,
                       created_at, delivered_at, account_login, delivery_type, notes
                FROM orders
                WHERE order_id = %s
            """, (order_id,))
            
            row = cur.fetchone()
            if row:
                order = dict(row)
                # Приводим даты к строковому формату
                if order.get("created_at"):
                    order["created_at"] = order["created_at"].strftime("%Y-%m-%d %H:%M:%S")
                else:
                    order["created_at"] = ""
                if order.get("delivered_at"):
                    order["delivered_at"] = order["delivered_at"].strftime("%Y-%m-%d %H:%M:%S")
                else:
                    order["delivered_at"] = ""
                # Приводим total к числу
                if order.get("total"):
                    order["total"] = float(order["total"])
                else:
                    order["total"] = 0
                return order
    
    return None


def get_all_orders(limit=100, offset=0):
    """
    Получить все заказы из БД.
    Возвращает список словарей, отсортированный по дате (новые сверху).
    """
    init_db()
    
    with _get_connection() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("""
                SELECT order_id, status, substatus, our_status, product, buyer_name, total,
                       created_at, delivered_at, account_login, delivery_type, notes
                FROM orders
                ORDER BY created_at DESC
                LIMIT %s OFFSET %s
            """, (limit, offset))
            
            orders = []
            for row in cur.fetchall():
                order = dict(row)
                # Приводим даты к строковому формату
                if order.get("created_at"):
                    order["created_at"] = order["created_at"].strftime("%Y-%m-%d %H:%M:%S")
                else:
                    order["created_at"] = ""
                if order.get("delivered_at"):
                    order["delivered_at"] = order["delivered_at"].strftime("%Y-%m-%d %H:%M:%S")
                else:
                    order["delivered_at"] = ""
                # Приводим total к числу
                if order.get("total"):
                    order["total"] = float(order["total"])
                else:
                    order["total"] = 0
                orders.append(order)
            
            return orders


def get_orders_count():
    """Получить общее количество заказов в БД."""
    init_db()
    
    with _get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM orders")
            return cur.fetchone()[0]
