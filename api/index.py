import json
import uuid
import mimetypes
import os
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urlparse, parse_qs

try:
    from .config import (
        RESTAURANT_NAME, RESTAURANT_TAGLINE, POLL_INTERVAL_SECONDS,
        BOOTSTRAP_SECRET, is_configured, is_local_mode
    )
    from .firebase import get, put, patch, post, delete, FirebaseError
    from .auth import (
        firebase_signup, firebase_signin, verify_id_token, require_user, require_role,
        safe_email, validate_password, create_profile, get_profile, list_users,
        update_user_role, set_user_active, AuthError
    )
    from .biz_logic import (
        now_iso, clean_text, validate_person_name, validate_phone, validate_datetime,
        to_positive_number, to_positive_int, calculate_bill, paginate, search_filter_sort,
        ensure_table_can_order, validate_menu_payload
    )
except ImportError:
    # Supports `python api/index.py` from the project root as well as package imports on Vercel.
    from config import (
        RESTAURANT_NAME, RESTAURANT_TAGLINE, POLL_INTERVAL_SECONDS,
        BOOTSTRAP_SECRET, is_configured, is_local_mode
    )
    from firebase import get, put, patch, post, delete, FirebaseError
    from auth import (
        firebase_signup, firebase_signin, verify_id_token, require_user, require_role,
        safe_email, validate_password, create_profile, get_profile, list_users,
        update_user_role, set_user_active, AuthError
    )
    from biz_logic import (
        now_iso, clean_text, validate_person_name, validate_phone, validate_datetime,
        to_positive_number, to_positive_int, calculate_bill, paginate, search_filter_sort,
        ensure_table_can_order, validate_menu_payload
    )

PUBLIC_GETS = {"/api/health", "/api/config"}

def new_id(prefix):
    return f"{prefix}_{uuid.uuid4().hex[:12]}"

def json_body(handler):
    length = int(handler.headers.get("Content-Length", "0") or 0)
    if length > 2_000_000:
        raise ValueError("ข้อมูลที่ส่งมามีขนาดใหญ่เกินไป")
    raw = handler.rfile.read(length).decode("utf-8") if length else "{}"
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise ValueError("ข้อมูลต้องเป็น JSON object")
    return data

def response(handler, status, payload):
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Cache-Control", "no-store")
    handler.send_header("Access-Control-Allow-Origin", "*")
    handler.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")
    handler.send_header("Access-Control-Allow-Methods", "GET, POST, PUT, PATCH, DELETE, OPTIONS")
    handler.end_headers()
    handler.wfile.write(body)

def error_response(handler, status, message):
    response(handler, status, {"ok": False, "message": str(message)})

def audit(profile, action, target_type, target_id, detail=""):
    try:
        entry = {
            "id": new_id("log"), "user_id": profile.get("id"), "user_name": profile.get("name"),
            "role": profile.get("role"), "action": action, "target_type": target_type,
            "target_id": target_id, "detail": detail, "timestamp": now_iso()
        }
        post("audit_logs", entry)
    except Exception:
        pass

def find_by_id(collection, item_id):
    data = get(collection) or {}
    if isinstance(data, dict):
        item = data.get(item_id)
        if item:
            return item
        for value in data.values():
            if isinstance(value, dict) and value.get("id") == item_id:
                return value
    return None

def require_staff(profile):
    require_role(profile, "admin", "staff")

def build_order_items(raw_items):
    if not isinstance(raw_items, list) or not raw_items:
        raise ValueError("ออเดอร์ต้องมีรายการอาหาร")
    final_items = []
    for raw in raw_items:
        if not isinstance(raw, dict):
            raise ValueError("รายการอาหารไม่ถูกต้อง")
        menu = find_by_id("menus", raw.get("menu_id"))
        if not menu:
            raise ValueError("ไม่พบเมนู")
        if menu.get("is_out_of_stock"):
            raise ValueError(f"เมนู {menu.get('name')} หมด")
        qty = to_positive_int(raw.get("quantity", 1), "จำนวน", maximum=99)
        options = raw.get("options") or {}
        if not isinstance(options, dict) or len(options) > 20:
            raise ValueError("ตัวเลือกเมนูไม่ถูกต้อง")
        final_items.append({
            "menu_id": menu["id"], "name": menu["name"], "unit_price": float(menu["price"]),
            "quantity": qty, "options": options
        })
    return final_items

def public_menu_list():
    data = get("menus") or {}
    return [v for v in data.values() if isinstance(v, dict)] if isinstance(data, dict) else []

def reservation_conflicts(table_number, requested_at, exclude_id=None):
    reservations = get("reservations") or {}
    active = {"waiting", "confirmed", "seated"}
    for item in reservations.values() if isinstance(reservations, dict) else []:
        if not isinstance(item, dict) or item.get("id") == exclude_id:
            continue
        if item.get("status") not in active or str(item.get("table_number")) != str(table_number):
            continue
        try:
            existing = validate_datetime(item.get("datetime"))
        except ValueError:
            continue
        if abs((existing - requested_at).total_seconds()) < 90 * 60:
            return True
    return False

class handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        return

    def do_OPTIONS(self):
        response(self, 204, {})

    def serve_static(self, path):
        try:
            public_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "public"))
            clean_path = path.lstrip("/") or "index.html"
            if ".." in clean_path.split("/"):
                return error_response(self, 400, "เส้นทางไฟล์ไม่ถูกต้อง")
            file_path = os.path.abspath(os.path.join(public_dir, clean_path))
            if not file_path.startswith(public_dir + os.sep):
                return error_response(self, 403, "ไม่อนุญาตให้เข้าถึงไฟล์นี้")
            if not os.path.isfile(file_path):
                file_path = os.path.join(public_dir, "index.html")
            with open(file_path, "rb") as handle:
                body = handle.read()
            content_type = mimetypes.guess_type(file_path)[0] or "application/octet-stream"
            self.send_response(200)
            self.send_header("Content-Type", content_type + ("; charset=utf-8" if content_type.startswith("text/") or content_type in {"application/javascript", "application/json"} else ""))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)
        except Exception:
            return error_response(self, 404, "ไม่พบหน้าเว็บ")

    def do_GET(self):
        try:
            parsed = urlparse(self.path)
            path = parsed.path
            qs = parse_qs(parsed.query)

            # Local server also serves the SPA so `python api/index.py` is a one-command run.
            if not path.startswith("/api/"):
                return self.serve_static(path)
            if path == "/api/health":
                return response(self, 200, {"ok": True, "restaurant": RESTAURANT_NAME, "database_configured": is_configured()})
            if path == "/api/config":
                return response(self, 200, {"ok": True, "restaurant": RESTAURANT_NAME, "tagline": RESTAURANT_TAGLINE, "poll_interval": POLL_INTERVAL_SECONDS})

            profile, _ = require_user(self.headers)

            if path == "/api/me":
                return response(self, 200, {"ok": True, "user": profile})

            if path == "/api/menus":
                query = qs.get("q", [""])[0]
                category = qs.get("category", [""])[0]
                sort = qs.get("sort", ["name"])[0]
                page = qs.get("page", ["1"])[0]
                page_size = qs.get("page_size", ["8"])[0]
                menus = search_filter_sort(public_menu_list(), query, category, sort)
                return response(self, 200, {"ok": True, **paginate(menus, page, page_size)})

            if path == "/api/tables":
                require_staff(profile)
                tables = get("tables") or {}
                values = list(tables.values()) if isinstance(tables, dict) else []
                return response(self, 200, {"ok": True, "tables": values})

            if path == "/api/reservations":
                reservations = get("reservations") or {}
                values = list(reservations.values()) if isinstance(reservations, dict) else []
                if profile.get("role") == "customer":
                    values = [r for r in values if r.get("customer_id") == profile.get("id")]
                return response(self, 200, {"ok": True, "reservations": values})

            if path == "/api/orders":
                orders = get("orders") or {}
                values = list(orders.values()) if isinstance(orders, dict) else []
                if profile.get("role") == "customer":
                    values = [o for o in values if o.get("customer_id") == profile.get("id")]
                return response(self, 200, {"ok": True, "orders": values})

            if path == "/api/kitchen":
                require_staff(profile)
                kitchen = get("kitchen") or {}
                values = list(kitchen.values()) if isinstance(kitchen, dict) else []
                values.sort(key=lambda x: x.get("timestamp", ""))
                return response(self, 200, {"ok": True, "items": values})

            if path == "/api/users":
                require_role(profile, "admin")
                return response(self, 200, {"ok": True, "users": list_users()})

            if path == "/api/audit-logs":
                require_role(profile, "admin")
                logs = get("audit_logs") or {}
                values = list(logs.values()) if isinstance(logs, dict) else []
                values.sort(key=lambda x: x.get("timestamp", ""), reverse=True)
                return response(self, 200, {"ok": True, "logs": values})

            if path == "/api/dashboard":
                require_role(profile, "admin")
                orders = get("orders") or {}
                values = [o for o in orders.values() if isinstance(o, dict)] if isinstance(orders, dict) else []
                closed = [o for o in values if o.get("status") == "closed"]
                total_sales = round(sum(float(o.get("total", 0)) for o in closed), 2)
                today = now_iso()[:10]
                today_sales = round(sum(float(o.get("total", 0)) for o in closed if str(o.get("closed_at", "")).startswith(today)), 2)
                sold = {}
                for order in closed:
                    for item in order.get("items", []):
                        name = item.get("name", "Unknown")
                        sold[name] = sold.get(name, 0) + int(item.get("quantity", 0))
                best = sorted([{"name": k, "quantity": v} for k, v in sold.items()], key=lambda x: x["quantity"], reverse=True)[:8]
                return response(self, 200, {"ok": True, "today_sales": today_sales, "total_sales": total_sales, "closed_orders": len(closed), "best_sellers": best})

            return error_response(self, 404, "ไม่พบ API ที่ร้องขอ")
        except AuthError as exc:
            return error_response(self, 403, str(exc))
        except Exception as exc:
            return error_response(self, 500, "เกิดข้อผิดพลาดภายในระบบ กรุณาลองใหม่")

    def do_POST(self):
        try:
            parsed = urlparse(self.path)
            path = parsed.path
            data = json_body(self)

            if path == "/api/auth/register":
                email = str(data.get("email", "")).strip().lower()
                password = data.get("password")
                name = str(data.get("name", "")).strip()
                if not safe_email(email):
                    raise ValueError("อีเมลไม่ถูกต้อง")
                if not validate_password(password):
                    raise ValueError("รหัสผ่านต้องยาว 8-128 ตัวอักษร และมีพิมพ์ใหญ่ พิมพ์เล็ก ตัวเลข และอักขระพิเศษ")
                name = validate_person_name(name)
                auth = firebase_signup(email, password)
                profile = create_profile(auth["localId"], email, name, "customer", password=password)
                return response(self, 201, {"ok": True, "message": "สมัครสมาชิกสำเร็จ", "token": auth["idToken"], "user": profile})

            if path == "/api/auth/login":
                email = str(data.get("email", "")).strip().lower()
                password = data.get("password")
                if not safe_email(email) or not isinstance(password, str):
                    raise ValueError("กรุณากรอกอีเมลและรหัสผ่าน")
                auth = firebase_signin(email, password)
                profile = get_profile(auth["localId"])
                return response(self, 200, {"ok": True, "message": "เข้าสู่ระบบสำเร็จ", "token": auth["idToken"], "user": profile})

            if path == "/api/bootstrap":
                if not BOOTSTRAP_SECRET or data.get("secret") != BOOTSTRAP_SECRET:
                    return error_response(self, 403, "Bootstrap secret ไม่ถูกต้อง")
                email = str(data.get("email", "")).strip().lower()
                password = data.get("password")
                name = str(data.get("name", "itailaew Admin")).strip()
                if not safe_email(email) or not validate_password(password):
                    raise ValueError("ข้อมูล Admin ไม่ถูกต้อง")
                name = validate_person_name(name, "ชื่อ Admin")
                auth = firebase_signup(email, password)
                profile = create_profile(auth["localId"], email, name, "admin", password=password)
                return response(self, 201, {"ok": True, "message": "สร้าง Admin สำเร็จ", "user": profile})

            profile, _ = require_user(self.headers)

            if path == "/api/menus":
                require_role(profile, "admin")
                menu = validate_menu_payload(data)
                menu["id"] = new_id("menu")
                menu["created_at"] = now_iso()
                put(f"menus/{menu['id']}", menu)
                audit(profile, "CREATE_MENU", "menu", menu["id"], menu["name"])
                return response(self, 201, {"ok": True, "menu": menu})

            if path == "/api/users/staff":
                require_role(profile, "admin")
                email = str(data.get("email", "")).strip().lower()
                password = data.get("password")
                name = str(data.get("name", "")).strip()
                if not safe_email(email) or not validate_password(password):
                    raise ValueError("ข้อมูล Staff ไม่ถูกต้อง")
                name = validate_person_name(name, "ชื่อ Staff")
                auth = firebase_signup(email, password)
                staff = create_profile(auth["localId"], email, name, "staff", password=password)
                audit(profile, "CREATE_STAFF", "user", staff["id"], email)
                return response(self, 201, {"ok": True, "user": staff})

            if path == "/api/orders":
                require_staff(profile)
                table_id = clean_text(data.get("table_id"), "โต๊ะ", 50)
                table = find_by_id("tables", table_id)
                ensure_table_can_order(table)
                final_items = build_order_items(data.get("items"))
                order_id = new_id("order")
                bill = calculate_bill(final_items, 0)
                order = {
                    "id": order_id, "table_id": table_id, "table_number": table.get("table_number"),
                    "customer_id": data.get("customer_id"), "items": final_items, **bill,
                    "status": "open", "created_by": profile["id"], "created_at": now_iso()
                }
                put(f"orders/{order_id}", order)
                patch(f"tables/{table_id}", {"status": "occupied", "current_order_id": order_id})
                for item in final_items:
                    kid = new_id("kit")
                    put(f"kitchen/{kid}", {
                        "id": kid, "order_id": order_id, "order_item_id": f"{order_id}_{item['menu_id']}",
                        "menu_name": item["name"], "options": item["options"],
                        "table_number": table.get("table_number"), "quantity": item["quantity"],
                        "status": "pending", "timestamp": now_iso()
                    })
                audit(profile, "CREATE_ORDER", "order", order_id, f"table={table_id}")
                return response(self, 201, {"ok": True, "order": order})

            if path == "/api/orders/split":
                require_staff(profile)
                order_id = clean_text(data.get("order_id"), "Order ID", 80)
                order = find_by_id("orders", order_id)
                if not order or order.get("status") == "closed":
                    raise ValueError("ไม่พบออเดอร์หรือบิลถูกปิดแล้ว")
                selected_ids = data.get("item_indexes")
                new_table_id = clean_text(data.get("new_table_id"), "โต๊ะใหม่", 50)
                new_table = find_by_id("tables", new_table_id)
                if not isinstance(selected_ids, list) or not selected_ids:
                    raise ValueError("กรุณาเลือกรายการที่ต้องการแยก")
                if not new_table or new_table.get("status") != "available":
                    raise ValueError("โต๊ะใหม่ต้องว่าง")
                items = order.get("items", [])
                selected = [items[int(i)] for i in selected_ids if 0 <= int(i) < len(items)]
                if not selected:
                    raise ValueError("ไม่พบรายการที่ต้องการแยก")
                remaining = [item for i, item in enumerate(items) if i not in [int(x) for x in selected_ids]]
                new_id_value = new_id("order")
                new_bill = calculate_bill(selected, 0)
                new_order = {**new_bill, "id": new_id_value, "table_id": new_table_id,
                             "table_number": new_table.get("table_number"), "customer_id": order.get("customer_id"),
                             "items": selected, "status": "open", "created_by": profile["id"], "created_at": now_iso(),
                             "split_from": order_id}
                put(f"orders/{new_id_value}", new_order)
                old_bill = calculate_bill(remaining, order.get("discount", 0))
                patch(f"orders/{order_id}", {"items": remaining, **old_bill})
                patch(f"tables/{new_table_id}", {"status": "occupied", "current_order_id": new_id_value})
                audit(profile, "SPLIT_BILL", "order", order_id, f"new={new_id_value}")
                return response(self, 200, {"ok": True, "new_order": new_order})

            if path == "/api/customer/orders":
                require_role(profile, "customer")
                table_id = clean_text(data.get("table_id"), "โต๊ะ", 50)
                table = find_by_id("tables", table_id)
                if not table:
                    raise ValueError("ไม่พบโต๊ะ")
                if table.get("status") == "reserved":
                    raise ValueError("โต๊ะนี้ถูกจองไว้")
                if table.get("status") not in {"available", "occupied", "waiting_bill"}:
                    raise ValueError("โต๊ะนี้ยังไม่พร้อมรับออเดอร์")
                claimed_by = table.get("claimed_by")
                if claimed_by and claimed_by != profile["id"]:
                    raise ValueError("คุณไม่มีสิทธิ์สั่งอาหารจากโต๊ะนี้")
                idempotency_key = self.headers.get("Idempotency-Key", "").strip()
                if idempotency_key:
                    orders = get("orders") or {}
                    for existing in orders.values() if isinstance(orders, dict) else []:
                        if isinstance(existing, dict) and existing.get("idempotency_key") == idempotency_key and existing.get("customer_id") == profile["id"]:
                            return response(self, 200, {"ok": True, "order": existing, "duplicate": True})
                final_items = build_order_items(data.get("items"))
                order_id = new_id("order")
                bill = calculate_bill(final_items, 0)
                order = {
                    "id": order_id, "table_id": table_id, "table_number": table.get("table_number"),
                    "customer_id": profile["id"], "items": final_items, **bill,
                    "status": "open", "created_by": profile["id"], "created_at": now_iso(),
                    "source": "qr", "idempotency_key": idempotency_key or None
                }
                put(f"orders/{order_id}", order)
                patch(f"tables/{table_id}", {"status": "occupied", "current_order_id": order_id, "claimed_by": profile["id"]})
                for item in final_items:
                    kid = new_id("kit")
                    put(f"kitchen/{kid}", {
                        "id": kid, "order_id": order_id, "order_item_id": f"{order_id}_{item['menu_id']}",
                        "menu_name": item["name"], "options": item["options"],
                        "table_number": table.get("table_number"), "quantity": item["quantity"],
                        "status": "pending", "timestamp": now_iso()
                    })
                return response(self, 201, {"ok": True, "order": order})

            if path == "/api/tables/move":
                require_staff(profile)
                old_id = clean_text(data.get("old_table_id"), "โต๊ะเดิม", 50)
                new_table_id = clean_text(data.get("new_table_id"), "โต๊ะใหม่", 50)
                old_table = find_by_id("tables", old_id)
                new_table = find_by_id("tables", new_table_id)
                if not old_table or not new_table:
                    raise ValueError("ไม่พบโต๊ะที่ระบุ")
                if old_table.get("status") == "available":
                    raise ValueError("โต๊ะเดิมยังไม่มีออเดอร์")
                if new_table.get("status") != "available":
                    raise ValueError("โต๊ะใหม่ไม่ว่าง")
                order_id = old_table.get("current_order_id")
                patch(f"tables/{old_id}", {"status": "available", "current_order_id": None})
                patch(f"tables/{new_table_id}", {"status": "occupied", "current_order_id": order_id})
                if order_id:
                    patch(f"orders/{order_id}", {"table_id": new_table_id, "table_number": new_table.get("table_number")})
                audit(profile, "MOVE_TABLE", "table", old_id, f"to={new_table_id}")
                return response(self, 200, {"ok": True, "message": "ย้ายโต๊ะสำเร็จ"})

            if path == "/api/tables/merge":
                require_staff(profile)
                source_id = clean_text(data.get("source_table_id"), "โต๊ะต้นทาง", 50)
                target_id = clean_text(data.get("target_table_id"), "โต๊ะปลายทาง", 50)
                source = find_by_id("tables", source_id)
                target = find_by_id("tables", target_id)
                if not source or not target:
                    raise ValueError("ไม่พบโต๊ะ")
                if not source.get("current_order_id") or not target.get("current_order_id"):
                    raise ValueError("ต้องมีออเดอร์ทั้งสองโต๊ะก่อนรวมโต๊ะ")
                source_order = find_by_id("orders", source["current_order_id"])
                target_order = find_by_id("orders", target["current_order_id"])
                merged_items = target_order.get("items", []) + source_order.get("items", [])
                bill = calculate_bill(merged_items, target_order.get("discount", 0))
                patch(f"orders/{target_order['id']}", {"items": merged_items, **bill, "merged_table_ids": [source_id, target_id]})
                patch(f"tables/{source_id}", {"status": "available", "current_order_id": None})
                audit(profile, "MERGE_TABLE", "table", target_id, f"source={source_id}")
                return response(self, 200, {"ok": True, "message": "รวมโต๊ะสำเร็จ", "order_id": target_order["id"]})

            if path == "/api/reservations":
                name = validate_person_name(data.get("customer_name"), "ชื่อ")
                phone = validate_phone(data.get("phone"))
                table_number = clean_text(data.get("table_number"), "โต๊ะ", 20)
                tables = get("tables") or {}
                table = next((t for t in tables.values() if isinstance(t, dict) and str(t.get("table_number")) == table_number), None) if isinstance(tables, dict) else None
                if not table:
                    raise ValueError("ไม่พบโต๊ะที่ระบุ")
                requested_at = validate_datetime(data.get("datetime"), "วันเวลา")
                now = datetime.now(timezone.utc)
                if requested_at.tzinfo is None:
                    requested_at = requested_at.replace(tzinfo=timezone.utc)
                if requested_at < now + timedelta(minutes=30):
                    raise ValueError("กรุณาจองล่วงหน้าอย่างน้อย 30 นาที")
                if reservation_conflicts(table_number, requested_at):
                    raise ValueError("โต๊ะนี้มีการจองในช่วงเวลาดังกล่าวแล้ว")
                reservation = {
                    "id": new_id("res"), "customer_id": profile["id"], "customer_name": name,
                    "phone": phone, "table_number": table_number, "datetime": requested_at.isoformat(),
                    "status": "waiting", "created_at": now_iso()
                }
                put(f"reservations/{reservation['id']}", reservation)
                return response(self, 201, {"ok": True, "reservation": reservation})

            return error_response(self, 404, "ไม่พบ API ที่ร้องขอ")
        except AuthError as exc:
            return error_response(self, 403, str(exc))
        except (ValueError, FirebaseError) as exc:
            return error_response(self, 400, str(exc))
        except Exception:
            return error_response(self, 500, "เกิดข้อผิดพลาดภายในระบบ กรุณาลองใหม่")

    def do_PATCH(self):
        try:
            path = urlparse(self.path).path
            data = json_body(self)
            profile, _ = require_user(self.headers)

            if path.startswith("/api/menus/"):
                require_role(profile, "admin")
                menu_id = path.rsplit("/", 1)[-1]
                current = find_by_id("menus", menu_id)
                if not current:
                    return error_response(self, 404, "ไม่พบเมนู")
                merged = dict(current)
                merged.update(data)
                menu = validate_menu_payload(merged)
                patch(f"menus/{menu_id}", menu)
                audit(profile, "UPDATE_MENU", "menu", menu_id, menu["name"])
                return response(self, 200, {"ok": True, "menu": {**current, **menu, "id": menu_id}})

            if path.startswith("/api/users/"):
                require_role(profile, "admin")
                uid = path.rsplit("/", 1)[-1]
                if "role" in data:
                    update_user_role(uid, data["role"])
                    audit(profile, "UPDATE_ROLE", "user", uid, str(data["role"]))
                if "active" in data:
                    set_user_active(uid, data["active"])
                    audit(profile, "UPDATE_USER_ACTIVE", "user", uid, str(data["active"]))
                return response(self, 200, {"ok": True, "message": "อัปเดตผู้ใช้สำเร็จ"})

            if path.startswith("/api/tables/"):
                require_staff(profile)
                table_id = path.rsplit("/", 1)[-1]
                allowed = {"available", "occupied", "waiting_bill", "reserved"}
                status = data.get("status")
                if status not in allowed:
                    raise ValueError("สถานะโต๊ะไม่ถูกต้อง")
                patch(f"tables/{table_id}", {"status": status})
                return response(self, 200, {"ok": True, "message": "อัปเดตโต๊ะสำเร็จ"})

            if path.startswith("/api/kitchen/"):
                require_staff(profile)
                item_id = path.rsplit("/", 1)[-1]
                status = data.get("status")
                if status not in {"pending", "cooking", "done"}:
                    raise ValueError("สถานะครัวไม่ถูกต้อง")
                patch(f"kitchen/{item_id}", {"status": status, "updated_at": now_iso()})
                return response(self, 200, {"ok": True, "message": "อัปเดตสถานะครัวสำเร็จ"})

            if path.startswith("/api/orders/") and path.endswith("/checkout"):
                require_staff(profile)
                order_id = path.split("/")[-2]
                order = find_by_id("orders", order_id)
                if not order:
                    return error_response(self, 404, "ไม่พบออเดอร์")
                if order.get("status") == "closed":
                    return error_response(self, 400, "ไม่สามารถเช็คบิลซ้ำได้")
                discount = to_positive_number(data.get("discount", 0), "ส่วนลด", allow_zero=True)
                bill = calculate_bill(order.get("items", []), discount)
                closed = {**bill, "status": "closed", "closed_at": now_iso(), "closed_by": profile["id"]}
                patch(f"orders/{order_id}", closed)
                table_id = order.get("table_id")
                if table_id:
                    patch(f"tables/{table_id}", {"status": "available", "current_order_id": None})
                customer_id = order.get("customer_id")
                if customer_id:
                    points = int(float(bill["total"]) // 100)
                    user = get_profile(customer_id)
                    patch(f"users/{customer_id}", {"member_points": int(user.get("member_points", 0)) + points})
                audit(profile, "CHECKOUT_ORDER", "order", order_id, str(bill["total"]))
                return response(self, 200, {"ok": True, "order": {**order, **closed}})

            return error_response(self, 404, "ไม่พบ API ที่ร้องขอ")
        except AuthError as exc:
            return error_response(self, 403, str(exc))
        except (ValueError, FirebaseError) as exc:
            return error_response(self, 400, str(exc))
        except Exception:
            return error_response(self, 500, "เกิดข้อผิดพลาดภายในระบบ กรุณาลองใหม่")

    def do_DELETE(self):
        try:
            path = urlparse(self.path).path
            profile, _ = require_user(self.headers)
            if path.startswith("/api/menus/"):
                require_role(profile, "admin")
                menu_id = path.rsplit("/", 1)[-1]
                if not find_by_id("menus", menu_id):
                    return error_response(self, 404, "ไม่พบเมนู")
                delete(f"menus/{menu_id}")
                audit(profile, "DELETE_MENU", "menu", menu_id)
                return response(self, 200, {"ok": True, "message": "ลบเมนูสำเร็จ"})
            return error_response(self, 404, "ไม่พบ API ที่ร้องขอ")
        except AuthError as exc:
            return error_response(self, 403, str(exc))
        except Exception:
            return error_response(self, 500, "ไม่สามารถดำเนินการได้")


def ensure_local_admin():
    """Seed a usable Admin account once in Local mode for first-time setup."""
    if not is_local_mode():
        return
    try:
        users = get("users") or {}
        if isinstance(users, dict) and any(isinstance(u, dict) and u.get("role") == "admin" for u in users.values()):
            return
        admin_email = os.environ.get("LOCAL_ADMIN_EMAIL", "admin@itailaew.com")
        admin_password = os.environ.get("LOCAL_ADMIN_PASSWORD", "Admin@12345")
        auth = firebase_signup(admin_email, admin_password)
        create_profile(auth["localId"], admin_email, "itailaew Admin", "admin", password=admin_password)
        print(f"Local Admin created: {admin_email} / {admin_password}")
    except Exception as exc:
        print(f"Local Admin seed skipped: {exc}")



if __name__ == "__main__":
    ensure_local_admin()
    # Local development only. Vercel imports `handler` and does not execute this block.
    host = "127.0.0.1"
    port = 8000
    print(f"itailaew API running at http://{host}:{port}")
    print("Press Ctrl+C to stop.")
    try:
        HTTPServer((host, port), handler).serve_forever()
    except KeyboardInterrupt:
        print("\nitailaew API stopped.")
    except Exception as exc:
        print(f"ไม่สามารถเริ่ม Local API ได้: {exc}")
