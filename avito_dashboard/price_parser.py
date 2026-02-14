# Парсер прайс-листа (оптово-розничный)
# Поддерживает: Excel (.xlsx), CSV
import io
import logging
from pathlib import Path
from typing import Any, Optional, List, Dict, Union

logger = logging.getLogger(__name__)


def parse_excel(file_path: Union[str, Path]) -> List[Dict[str, Any]]:
    """Парсит Excel-файл. Ожидает колонки: артикул/название, опт, розница (или вариации)."""
    try:
        import openpyxl
    except ImportError:
        raise ImportError("Установите openpyxl: pip install openpyxl")

    wb = openpyxl.load_workbook(file_path, read_only=True, data_only=True)
    ws = wb.active
    rows = list(ws.iter_rows(values_only=True))

    if not rows:
        return []

    # Определяем заголовки по первой строке
    headers = [str(h).strip().lower() if h else "" for h in rows[0]]
    result = []

    # Маппинг возможных названий колонок
    art_col = None
    name_col = None
    opt_col = None
    retail_col = None

    for i, h in enumerate(headers):
        h_lower = (h or "").lower()
        if "артикул" in h_lower or "art" in h_lower or "код" in h_lower:
            art_col = i
        elif "название" in h_lower or "наименование" in h_lower or "товар" in h_lower:
            name_col = i
        elif "опт" in h_lower or "оптов" in h_lower or "wholesale" in h_lower:
            opt_col = i
        elif "розниц" in h_lower or "розн" in h_lower or "retail" in h_lower or "цена" in h_lower:
            if opt_col is None:
                opt_col = i
            else:
                retail_col = i

    # Если одна колонка цены — считаем и оптом и розницей
    if retail_col is None and opt_col is not None:
        retail_col = opt_col

    for row in rows[1:]:
        if not any(row):
            continue
        art = str(row[art_col] or row[name_col] or "").strip() if (art_col is not None or name_col is not None) else ""
        name = str(row[name_col] or row[art_col] or "").strip() if (name_col is not None or art_col is not None) else ""
        if not art and not name:
            continue

        try:
            opt = float(row[opt_col]) if opt_col is not None and row[opt_col] is not None else 0
        except (ValueError, TypeError):
            opt = 0
        try:
            retail = float(row[retail_col]) if retail_col is not None and row[retail_col] is not None else opt
        except (ValueError, TypeError):
            retail = opt

        result.append({
            "article": art or name,
            "name": name or art,
            "wholesale_price": opt,
            "retail_price": retail,
        })

    return result


def parse_csv(content: str) -> List[Dict[str, Any]]:
    """Парсит CSV (разделитель ; или ,)."""
    import csv
    reader = csv.reader(io.StringIO(content), delimiter=";")
    rows = list(reader)
    if not rows:
        if "," in content:
            reader = csv.reader(io.StringIO(content), delimiter=",")
            rows = list(reader)
    if not rows:
        return []

    headers = [str(h).strip().lower() if h else "" for h in rows[0]]
    art_col = name_col = opt_col = retail_col = None

    for i, h in enumerate(headers):
        h_lower = (h or "").lower()
        if "артикул" in h_lower or "art" in h_lower or "код" in h_lower:
            art_col = i
        elif "название" in h_lower or "наименование" in h_lower or "товар" in h_lower:
            name_col = i
        elif "опт" in h_lower or "оптов" in h_lower:
            opt_col = i
        elif "розниц" in h_lower or "розн" in h_lower or "цена" in h_lower:
            retail_col = i if opt_col is not None else opt_col or i

    if retail_col is None:
        retail_col = opt_col

    result = []
    for row in rows[1:]:
        if not row or not any(row):
            continue
        while len(row) <= max((art_col or 0), (name_col or 0), (opt_col or 0), (retail_col or 0)):
            row.append("")
        art = str(row[art_col] or row[name_col] or "").strip() if (art_col is not None or name_col is not None) else ""
        name = str(row[name_col] or row[art_col] or "").strip() if (name_col is not None or art_col is not None) else ""
        if not art and not name:
            continue
        try:
            opt = float(str(row[opt_col] or "0").replace(",", ".")) if opt_col is not None else 0
        except ValueError:
            opt = 0
        try:
            retail = float(str(row[retail_col] or "0").replace(",", ".")) if retail_col is not None else opt
        except ValueError:
            retail = opt
        result.append({
            "article": art or name,
            "name": name or art,
            "wholesale_price": opt,
            "retail_price": retail,
        })
    return result


def load_price_list(file_path: Optional[Union[str, Path]] = None, csv_content: Optional[str] = None) -> List[Dict[str, Any]]:
    """Загрузить прайс из файла или CSV-строки."""
    if file_path:
        path = Path(file_path)
        if path.suffix.lower() in (".xlsx", ".xls"):
            return parse_excel(path)
        return parse_csv(path.read_text(encoding="utf-8-sig"))
    if csv_content:
        return parse_csv(csv_content)
    return []
