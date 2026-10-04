import math
import re
from datetime import datetime, timezone
from urllib.parse import urlparse

try:
    from .config import VAT_RATE, SERVICE_CHARGE_RATE
except ImportError:
    from config import VAT_RATE, SERVICE_CHARGE_RATE

PHONE_RE = re.compile(r"^0[0-9]{9}$")
NAME_ALLOWED_RE = re.compile(r"^[\w\u0E00-\u0E7F .'-]+$", re.UNICODE)


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def clean_text(value, field, max_len=120):
    if not isinstance(value, str):
        raise ValueError(f"{field} ต้องเป็นข้อความ")
    value = value.strip()
    if not value:
        raise ValueError(f"{field} ห้ามว่าง")
    if len(value) > max_len:
        raise ValueError(f"{field} ยาวเกินกำหนด")
    return value


def validate_person_name(value, field="ชื่อ"):
    value = clean_text(value, field, 100)
    if len(value) < 2 or not any(ch.isalpha() or "\u0E00" <= ch <= "\u0E7F" for ch in value):
        raise ValueError(f"{field} ต้องมีตัวอักษรอย่างน้อย 1 ตัว")
    if not NAME_ALLOWED_RE.fullmatch(value):
        raise ValueError(f"{field} มีอักขระที่ไม่อนุญาต")
    return value


def validate_phone(value):
    value = clean_text(value, "เบอร์โทร", 10)
    if not PHONE_RE.fullmatch(value):
        raise ValueError("เบอร์โทรต้องเป็นตัวเลข 10 หลักและขึ้นต้นด้วย 0")
    return value


def to_positive_number(value, field, allow_zero=False, maximum=100000000):
    try:
        if isinstance(value, bool):
            raise ValueError
        number = float(value)
        if not math.isfinite(number) or number < 0 or (number == 0 and not allow_zero) or number > maximum:
            raise ValueError
        return number
    except (TypeError, ValueError):
        raise ValueError(f"{field} ต้องเป็นตัวเลขที่ถูกต้อง ไม่ติดลบ และไม่เกิน {maximum}")


def to_positive_int(value, field, allow_zero=False, maximum=99):
    try:
        if isinstance(value, bool):
            raise ValueError
        if isinstance(value, float) and not value.is_integer():
            raise ValueError
        text = str(value).strip()
        if not re.fullmatch(r"\d+", text):
            raise ValueError
        number = int(text)
        if number < 0 or (number == 0 and not allow_zero) or number > maximum:
            raise ValueError
        return number
    except (TypeError, ValueError):
        raise ValueError(f"{field} ต้องเป็นจำนวนเต็ม 1-{maximum}")


def validate_datetime(value, field="วันเวลา"):
    value = clean_text(value, field, 40)
    normalized = value.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        raise ValueError(f"{field} ต้องอยู่ในรูปแบบวันที่และเวลาที่ถูกต้อง")
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def validate_image_url(value):
    value = str(value or "").strip()
    if not value:
        return ""
    if len(value) > 2000:
        raise ValueError("URL รูปภาพยาวเกินกำหนด")
    parsed = urlparse(value)
    if parsed.scheme != "https" or not parsed.netloc:
        raise ValueError("URL รูปภาพต้องเป็น HTTPS URL")
    return value


def calculate_bill(items, discount=0, service_rate=SERVICE_CHARGE_RATE):
    try:
        subtotal = 0.0
        for item in items:
            qty = to_positive_int(item.get("quantity", 1), "จำนวน")
            price = to_positive_number(item.get("unit_price", 0), "ราคา")
            subtotal += price * qty
        discount = to_positive_number(discount or 0, "ส่วนลด", allow_zero=True)
        if discount > subtotal:
            raise ValueError("ส่วนลดต้องไม่มากกว่ายอดอาหาร")
        service_charge = max(0.0, (subtotal - discount) * float(service_rate))
        taxable = max(0.0, subtotal - discount + service_charge)
        tax = taxable * VAT_RATE
        total = taxable + tax
        return {"subtotal": round(subtotal, 2), "discount": round(discount, 2), "service_charge": round(service_charge, 2), "tax": round(tax, 2), "total": round(total, 2)}
    except Exception as exc:
        raise ValueError(f"คำนวณบิลไม่สำเร็จ: {exc}")


def paginate(items, page=1, page_size=10):
    try:
        page = max(1, int(page))
        page_size = max(1, min(100, int(page_size)))
        total = len(items)
        start = (page - 1) * page_size
        return {"items": items[start:start + page_size], "page": page, "page_size": page_size, "total": total, "total_pages": max(1, (total + page_size - 1) // page_size)}
    except Exception as exc:
        raise ValueError(f"แบ่งหน้าไม่สำเร็จ: {exc}")


def search_filter_sort(items, query="", category="", sort="name"):
    try:
        query = (query or "").strip().lower()
        category = (category or "").strip().lower()
        result = []
        for item in items:
            name = str(item.get("name", "")).lower()
            cat = str(item.get("category", "")).lower()
            if query and query not in name:
                continue
            if category and cat != category:
                continue
            result.append(item)
        if sort == "price_asc":
            result.sort(key=lambda x: float(x.get("price", 0)))
        elif sort == "price_desc":
            result.sort(key=lambda x: float(x.get("price", 0)), reverse=True)
        else:
            result.sort(key=lambda x: str(x.get("name", "")).lower())
        return result
    except Exception as exc:
        raise ValueError(f"ค้นหา/กรอง/เรียงข้อมูลไม่สำเร็จ: {exc}")


def ensure_table_can_order(table):
    if not table:
        raise ValueError("ไม่พบโต๊ะ")
    if table.get("status") == "available":
        raise ValueError("โต๊ะยังไม่เปิดบิล ไม่สามารถรับออเดอร์ได้")
    if table.get("status") == "reserved":
        raise ValueError("โต๊ะนี้ถูกจองไว้")
    if table.get("status") not in {"occupied", "waiting_bill"}:
        raise ValueError("สถานะโต๊ะไม่พร้อมรับออเดอร์")
    return True


def normalize_options(options):
    if options is None:
        return {}
    if not isinstance(options, dict):
        raise ValueError("ตัวเลือกเมนูต้องเป็นข้อมูลแบบ object")
    if len(options) > 20:
        raise ValueError("ตัวเลือกเมนูมีจำนวนมากเกินไป")
    normalized = {}
    for raw_key, raw_value in options.items():
        key = clean_text(str(raw_key), "ชื่อตัวเลือก", 60)
        value = clean_text(str(raw_value), "ค่าตัวเลือก", 100)
        normalized[key] = value
    return normalized


def validate_menu_payload(data):
    name = clean_text(data.get("name"), "ชื่อเมนู", 100)
    category = clean_text(data.get("category"), "หมวดหมู่", 60)
    price = to_positive_number(data.get("price"), "ราคา", maximum=100000)
    options = normalize_options(data.get("options"))
    return {"name": name, "category": category, "price": round(price, 2), "image_url": validate_image_url(data.get("image_url")), "is_out_of_stock": bool(data.get("is_out_of_stock", False)), "options": options}
