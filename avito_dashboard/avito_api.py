# Avito API клиент — получение объявлений и статистики
# Документация: https://developers.avito.ru/
import logging
from datetime import date, timedelta
from typing import Any, Optional

import httpx

logger = logging.getLogger(__name__)

AVITO_API_BASE = "https://api.avito.ru"


class AvitoAPIError(Exception):
    """Ошибка API Авито."""
    pass


class AvitoAPIClient:
    def __init__(self, client_id: str, client_secret: str):
        self.client_id = client_id
        self.client_secret = client_secret
        self._access_token: Optional[str] = None

    def _get_token(self) -> str:
        """Получить access token (client credentials)."""
        if self._access_token:
            return self._access_token

        with httpx.Client() as client:
            resp = client.post(
                f"{AVITO_API_BASE}/token",
                data={
                    "grant_type": "client_credentials",
                    "client_id": self.client_id,
                    "client_secret": self.client_secret,
                },
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
            logger.info("Avito token request: %s %s", resp.status_code, resp.text[:200])

        if resp.status_code != 200:
            raise AvitoAPIError(
                f"Ошибка авторизации Avito: {resp.status_code} — {resp.text}"
            )

        data = resp.json()
        self._access_token = data["access_token"]
        return self._access_token

    def _request(
        self,
        method: str,
        path: str,
        params: Optional[dict] = None,
        json_body: Optional[dict] = None,
    ) -> dict:
        """Выполнить запрос к API."""
        token = self._get_token()
        url = f"{AVITO_API_BASE}{path}"

        with httpx.Client() as client:
            resp = client.request(
                method,
                url,
                params=params,
                json=json_body,
                headers={"Authorization": f"Bearer {token}"},
            )

        logger.info("Avito API %s %s: %s", method, path, resp.status_code)

        if resp.status_code >= 400:
            raise AvitoAPIError(
                f"Avito API {path}: {resp.status_code} — {resp.text}"
            )

        return resp.json() if resp.text else {}

    def get_user_id(self) -> int:
        """Получить ID пользователя (для запросов к статистике)."""
        for path in ("/core/v1/accounts/self", "/autoload/v1/accounts/self"):
            try:
                data = self._request("GET", path)
                return data.get("id", data.get("user_id", 0))
            except AvitoAPIError:
                continue
        return 0

    def get_items(self, offset: int = 0, limit: int = 50) -> dict:
        """Список объявлений пользователя."""
        uid = self.get_user_id()
        last_error = None
        for path, params in [
            (f"/core/v1/accounts/{uid}/items/", {"offset": offset, "limit": limit}),
            (f"/core/v1/accounts/{uid}/items/", {"offset": offset, "per_page": limit}),
            ("/core/v1/items/", {"offset": offset, "limit": limit}),
            ("/core/v1/items/", {"offset": offset, "per_page": limit}),
            ("/autoload/v1/items/", {"offset": offset, "limit": limit}),
        ]:
            try:
                return self._request("GET", path, params=params)
            except AvitoAPIError as e:
                last_error = e
                continue
        # Если API объявлений недоступен (другой тариф) — возвращаем пустой список
        logger.warning("Items API недоступен: %s. Возвращаем пустой список.", last_error)
        return {"result": {"items": []}, "resources": []}

    def get_item(self, item_id: int) -> dict:
        """Детали объявления."""
        return self._request("GET", f"/core/v1/items/{item_id}")

    def get_items_stats(
        self,
        user_id: int,
        date_from: date,
        date_to: date,
        item_ids: Optional[list] = None,
    ) -> dict:
        """Статистика по объявлениям за период."""
        params = {
            "dateFrom": date_from.isoformat(),
            "dateTo": date_to.isoformat(),
        }
        if item_ids:
            params["itemIds"] = ",".join(str(i) for i in item_ids)
        try:
            return self._request(
                "GET",
                f"/stats/v1/accounts/{user_id}/items",
                params=params,
            )
        except AvitoAPIError as e:
            logger.warning("Stats items API: %s", e)
            return {"result": {"items": []}, "items": []}

    def get_operation_stats(
        self,
        user_id: int,
        date_from: date,
        date_to: date,
    ) -> dict:
        """Статистика операций (просмотры, контакты и т.д.)."""
        try:
            return self._request(
                "GET",
                f"/stats/v1/accounts/{user_id}/stats",
                params={
                    "dateFrom": date_from.isoformat(),
                    "dateTo": date_to.isoformat(),
                },
            )
        except AvitoAPIError as e:
            logger.warning("Stats API: %s", e)
            return {"result": {}, "items": []}
