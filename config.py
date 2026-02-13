# Конфигурация Яндекс Маркет API (DBS) + Telegram Bot
import os
from dotenv import load_dotenv

load_dotenv()

# Telegram Bot
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_GROUP_ID = os.getenv("TELEGRAM_GROUP_ID")

# Telegram ID администратора — бот отвечает только ему
ADMIN_ID = int(os.getenv("ADMIN_ID", "7210745918"))

# Yandex Market API
API_TOKEN = os.getenv("YANDEX_API_TOKEN")
BUSINESS_ID = int(os.getenv("BUSINESS_ID", "216655442"))
CAMPAIGN_ID = int(os.getenv("CAMPAIGN_ID", "148995168"))

# Базовый URL API Яндекс Маркета
BASE_URL = "https://api.partner.market.yandex.ru"

# Заголовки для запросов
# Content-Type НЕ указываем — httpx ставит его автоматически:
#   json=  → application/json
#   data=  → application/x-www-form-urlencoded  (для чата Маркета)
HEADERS = {
    "Api-Key": API_TOKEN,
    "Accept": "application/json",
}
