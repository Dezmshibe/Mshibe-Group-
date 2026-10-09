# ai_receptionist.py
# =============================================================================
# AI Voice Receptionist — plug-in module for the Mshibe Group Flask app.
#
# Registers:
#   GET  /api/voice/health   -> AI status
#   POST /api/voice/chat     -> AI reply with tool calling
#
# Expanded edition: menu lookups, order status, service comparison,
# recommendations, small talk, and multi-intent conversation.
# =============================================================================

import os
import json
import uuid
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from dotenv import load_dotenv
from flask import jsonify, request

load_dotenv()

BOT_NAME = os.getenv("RECEPTIONIST_NAME", "Neo").strip() or "Neo"


def _build_client():
    groq_key = os.getenv("GROQ_API_KEY", "").strip()
    openai_key = os.getenv("OPENAI_API_KEY", "").strip()
    if groq_key:
        from openai import OpenAI
        return (OpenAI(api_key=groq_key, base_url="https://api.groq.com/openai/v1"),
                "groq",
                os.getenv("OPENAI_MODEL", "openai/gpt-oss-120b").strip())
    if openai_key:
        from openai import OpenAI
        return (OpenAI(api_key=openai_key),
                "openai",
                os.getenv("OPENAI_MODEL", "gpt-4o-mini").strip())
    return None, "fallback", None


def register(app, get_settings, Service, Appointment, db, MenuItem=None,
             MenuCategory=None, Order=None):
    """Attach AI receptionist routes. MenuItem and Order are optional
    (they enable menu + order-status tools if present)."""

    try:
        from flask_cors import CORS
_allowed = os.getenv("ALLOWED_ORIGINS", "").strip()
if _allowed:
    CORS(app, resources={r"/api/*": {"origins": [o.strip() for o in _allowed.split(",")]}})
else:
    # Same-origin only (voice.html is served from the same Flask app)
    CORS(app, resources={r"/api/*": {"origins": []}})    except ImportError:
        pass

    client, provider, model = _build_client()
    _conversations = {}

    # =========================================================================
    # SYSTEM PROMPT — built fresh from the live database on every call
    # =========================================================================
    def system_prompt():
        s = get_settings()

        # ---- services ----
        services = Service.query.filter_by(active=True).order_by(Service.name).all()
        svc_lines = "\n".join(
            "- id=" + str(x.id) + " | " + x.name
            + " | " + s.currency_symbol + str(int(x.price or 0))
            + (" | " + str(x.duration) + " min" if x.duration else "")
            + (" | " + x.description if x.description else "")
            for x in services
        ) or "- No services are currently listed."

        # ---- menu ----
        menu_block = ""
        if MenuItem is not None:
            try:
                cats = MenuCategory.query.order_by(MenuCategory.display_order).all() if MenuCategory else []
                cat_names = ", ".join(c.name for c in cats) if cats else "no categories"
                menu_block = (
                    "MENU CATEGORIES\n" + cat_names + "\n"
                    "(Use search_menu to fetch specific items with prices.)\n\n"
                )
            except Exception:
                pass

        return (
       "You are " + BOT_NAME + ", the AI receptionist for " + s.company_name
+ ", speaking with customers by telephone.\n\n"

"IDENTITY\n"
"- Your name is " + BOT_NAME + ".\n"
"- On the FIRST message of every new call, introduce yourself by name.\n"
"- Example: 'Hi, thank you for calling " + s.company_name + ", this is " + BOT_NAME + ". How can I help you today?'\n"
"- On later messages, don't repeat your name unless the customer asks.\n"
"- If asked 'who are you?' or 'what's your name?', say you are " + BOT_NAME + ", the virtual receptionist.\n\n"

            "PERSONALITY\n"
            "- Warm, professional, patient, natural, conversational.\n"
            "- Sound like a real receptionist. Never robotic.\n"
            "- Speak in short sentences — 1 to 3 sentences per reply.\n"
            "- This is a phone call. No bullet points, no markdown, no emoji.\n"
            "- Say prices naturally: 'R450' is 'four hundred and fifty rand'.\n"
            "- Never invent information. If you don't have it, say so and offer to check.\n\n"

            "BUSINESS FACTS (only use these)\n"
            "Company: " + s.company_name + "\n"
            "Tagline: " + (s.tagline or "not set") + "\n"
            "Email: " + (s.email or "not set") + "\n"
            "Phone: " + (s.phone or "not set") + "\n"
            "Address: " + (s.address or "not set") + "\n"
            "Default hours: " + (s.open_time or "09:00") + " to " + (s.close_time or "17:00") + "\n"
            "About: " + (s.about_text or "") + "\n\n"

            "SERVICES\n" + svc_lines + "\n\n"
            + menu_block
            + "YOU CAN HANDLE ALL OF THESE\n"
            "- Questions about services, prices, durations, descriptions\n"
            "- Comparing services ('what's the difference between X and Y?')\n"
            "- Recommendations ('which one suits a small business?')\n"
            "- Menu items, categories, prices\n"
            "- Order status (customer gives a reference like ABC123)\n"
            "- Opening hours and whether the business is open right now\n"
            "- Location, address, parking, directions\n"
            "- Contact details (phone, email)\n"
            "- Booking appointments (service, day, time, name, phone)\n"
            "- Callbacks (name, phone, reason)\n"
            "- General small talk, greetings, thanks\n"
            "- Multi-part requests ('hi, I want to book AND ask about your menu')\n"
            "- Requests to repeat, clarify, or rephrase something\n"
            "- Escalating to a human when the customer is angry, or asks for one,\n"
            "  or asks something you genuinely cannot answer\n\n"

            "HOW TO ANSWER — GENERAL RULES\n"
            "1. Understand meaning, not just keywords.\n"
            "2. Remember everything the customer tells you. Never ask twice.\n"
            "3. Ask ONE question at a time.\n"
            "4. If the request is vague, ask a short clarifying question first.\n"
            "5. Use tools for facts. Never guess prices, availability, or hours.\n"
            "6. If a tool returns nothing useful, say so honestly and offer a human.\n"
            "7. Never claim an action happened unless a tool confirmed it.\n"
            "8. If angry or asking for a human, transfer immediately.\n"
            "9. Keep replies concise — this is speech, not text.\n"
            "10. Use the customer's first name occasionally once you know it.\n"
            "11. If the customer says 'thanks' or sounds finished, offer a warm goodbye.\n"
            "12. If they change their mind mid-flow, adapt naturally.\n"
            "13. If they ask something you don't have a tool for, offer to transfer\n"
            "    or to take a callback.\n\n"

            "BOOKING FLOW — follow this exact order\n"
            "1. Identify which service (use get_service_details if unsure).\n"
            "2. Call get_available_slots with the day they mentioned.\n"
            "3. Offer the ACTUAL times returned by the tool. Do NOT invent times.\n"
            "4. Wait for them to pick a time.\n"
            "5. Only THEN ask for their full name.\n"
            "6. Then ask for their phone number.\n"
            "7. Then call create_booking with service_id, date (YYYY-MM-DD),\n"
            "   time (HH:MM), name, phone.\n"
            "8. Confirm by repeating day, time, and service back to them.\n\n"

            "TONE EXAMPLES\n"
            "Customer: 'How are you?'\n"
            "You: 'I'm doing well, thanks for asking. How can I help you today?'\n\n"
            "Customer: 'What services do you offer?'\n"
            "You: 'We offer [list names naturally]. Which one would you like to know more about?'\n\n"
            "Customer: 'I'm not sure what I need.'\n"
            "You: 'No problem — tell me a bit about what you're trying to do and I can suggest the right service.'\n\n"
            "Customer: 'Thanks, that's helpful.'\n"
            "You: 'You're welcome. Anything else I can help with?'\n\n"

            "When you have enough information to act, act."
        )

    # =========================================================================
    # TOOL SCHEMA
    # =========================================================================
    def tools_schema():
        def mk(name, desc, props=None, required=None):
            return {"type": "function", "function": {
                "name": name, "description": desc,
                "parameters": {"type": "object",
                               "properties": props or {},
                               "required": required or []}}}

        base = [
            mk("get_business_info",
               "Get the company name, phone, email, address, tagline and about text."),
            mk("get_business_hours",
               "Get current opening status, weekly opening times, and working days."),
            mk("get_services",
               "List every bookable service with id, name, price, duration, and description."),
            mk("get_service_details",
               "Get full details for one service by name or description.",
               {"query": {"type": "string",
                          "description": "Free text, e.g. 'wellness' or 'advisory'"}},
               ["query"]),
            mk("compare_services",
               "Compare two or more services side by side (price, duration, description).",
               {"names": {"type": "array", "items": {"type": "string"},
                          "description": "Service names to compare, e.g. ['Business Advisory','Wellness Session']"}},
               ["names"]),
            mk("get_available_slots",
               "Available appointment slots for a specific day. Call BEFORE asking for name.",
               {"day_hint": {"type": "string"},
                "service_id": {"type": "integer"}},
               ["day_hint"]),
            mk("create_booking",
               "Create a booking. Only after service_id, date, time, name, phone.",
               {"service_id": {"type": "integer"},
                "date": {"type": "string"},
                "time": {"type": "string"},
                "name": {"type": "string"},
                "phone": {"type": "string"},
                "email": {"type": "string"},
                "notes": {"type": "string"}},
               ["service_id", "date", "time", "name", "phone"]),
            mk("request_callback",
               "Log a callback request with name, phone, and reason.",
               {"name": {"type": "string"},
                "phone": {"type": "string"},
                "reason": {"type": "string"}},
               ["name", "phone"]),
            mk("transfer_to_human",
               "Transfer the call to a human. Use if the customer asks, is angry,\n"
               "or if you cannot answer confidently.",
               {"reason": {"type": "string"}}, ["reason"]),
        ]

        if MenuItem is not None:
            base.append(mk("search_menu",
                           "Search or list menu items. Optional category filter and keyword search.",
                           {"category": {"type": "string",
                                         "description": "Optional category name, e.g. 'Mains'"},
                            "query": {"type": "string",
                                      "description": "Optional keyword like 'chicken', 'vegetarian', 'cold'"}}))
            base.append(mk("list_menu_categories",
                           "List all available menu categories."))
        if Order is not None:
            base.append(mk("get_order_status",
                           "Look up an order by reference (e.g. 'AB12CD34').",
                           {"reference": {"type": "string"}}, ["reference"]))

        return base

    # =========================================================================
    # HELPERS
    # =========================================================================
    def _match_service(query):
        q = (query or "").lower().strip()
        if not q:
            return None
        best, score = None, 0
        for s in Service.query.filter_by(active=True).all():
            hits = 0
            for w in s.name.lower().split():
                if len(w) > 3 and w in q:
                    hits += 2
            if s.description:
                for w in s.description.lower().split():
                    if len(w) > 4 and w in q:
                        hits += 1
            if hits > score:
                score, best = hits, s
        return best

    def _fmt_money(v):
        s = get_settings()
        try:
            return s.currency_symbol + "{:,.2f}".format(float(v))
        except (TypeError, ValueError):
            return s.currency_symbol + "0.00"

    # =========================================================================
    # TOOL IMPLEMENTATIONS
    # =========================================================================
    def t_info():
        s = get_settings()
        return {"name": s.company_name, "tagline": s.tagline,
                "phone": s.phone, "email": s.email, "address": s.address,
                "about": s.about_text,
                "currency_symbol": s.currency_symbol}

    def t_hours():
        s = get_settings()
        now = datetime.now(ZoneInfo("Africa/Johannesburg"))
        working = [int(x) for x in (s.working_days or "").split(",") if x.strip().isdigit()]
        is_open = False
        if not working or now.weekday() in working:
            try:
                oh, om = (int(x) for x in s.open_time.split(":"))
                ch, cm = (int(x) for x in s.close_time.split(":"))
                m = now.hour * 60 + now.minute
                is_open = oh * 60 + om <= m < ch * 60 + cm
            except Exception:
                pass
        days = ["Monday","Tuesday","Wednesday","Thursday","Friday","Saturday","Sunday"]
        return {"is_open_now": bool(is_open),
                "local_time": now.strftime("%A %H:%M"),
                "open_time": s.open_time,
                "close_time": s.close_time,
                "working_days": s.working_days or "Mon-Fri",
                "working_day_names": [days[i] for i in working] if working else days[:5]}

    def t_services():
        out = []
        for x in Service.query.filter_by(active=True).order_by(Service.name).all():
            out.append({"id": x.id, "name": x.name,
                        "price": float(x.price or 0),
                        "price_display": _fmt_money(x.price),
                        "duration_minutes": x.duration,
                        "description": x.description})
        return {"services": out}

    def t_service_details(query):
        s = _match_service(query)
        if not s:
            return {"found": False,
                    "message": "No matching service. Ask the customer to describe what they need.",
                    "available": [x.name for x in Service.query.filter_by(active=True).all()]}
        return {"found": True, "id": s.id, "name": s.name,
                "price": float(s.price or 0),
                "price_display": _fmt_money(s.price),
                "duration_minutes": s.duration,
                "description": s.description}

    def t_compare(names):
        out = []
        for n in (names or []):
            s = _match_service(n)
            if s:
                out.append({"name": s.name,
                            "price_display": _fmt_money(s.price),
                            "duration_minutes": s.duration,
                            "description": s.description})
        if not out:
            return {"found": False, "message": "Couldn't match those service names."}
        return {"found": True, "services": out}

    def t_slots(day_hint, service_id=None):
        s = get_settings()
        now = datetime.now(ZoneInfo("Africa/Johannesburg"))
        h = (day_hint or "").lower()
        offset = 1
        if "today" in h:
            offset = 0
        elif "tomorrow" in h:
            offset = 1
        else:
            names = ["monday","tuesday","wednesday","thursday","friday","saturday","sunday"]
            for i, nm in enumerate(names):
                if nm in h:
                    offset = (i - now.weekday()) % 7 or 7
                    break
            if "next week" in h:
                offset = 7
        target = now.date() + timedelta(days=offset)
        working = [int(x) for x in (s.working_days or "").split(",") if x.strip().isdigit()]
        if working and target.weekday() not in working:
            return {"date": target.isoformat(), "day": target.strftime("%A"),
                    "slots": [], "closed": True}
        try:
            oh, om = (int(x) for x in s.open_time.split(":"))
            ch, cm = (int(x) for x in s.close_time.split(":"))
        except Exception:
            oh, om, ch, cm = 9, 0, 17, 0
        duration = s.slot_duration or 30
        svc = db.session.get(Service, service_id) if service_id else None
        if svc and svc.duration:
            duration = svc.duration
        day_start = datetime.combine(target, datetime.min.time()).replace(hour=oh, minute=om)
        day_end = datetime.combine(target, datetime.min.time()).replace(hour=ch, minute=cm)
        existing = Appointment.query.filter(
            Appointment.date == target.isoformat(),
            Appointment.status != "cancelled").all()
        busy = []
        for a in existing:
            try:
                ah, am = (int(x) for x in a.time.split(":"))
                a_start = day_start.replace(hour=ah, minute=am)
                a_dur = (a.service.duration if a.service and a.service.duration else duration)
                busy.append((a_start, a_start + timedelta(minutes=a_dur)))
            except Exception:
                continue
        step = timedelta(minutes=s.slot_duration or 30)
        dur_delta = timedelta(minutes=duration)
        now_naive = datetime.now()
        slots = []
        cursor = day_start
        while cursor + dur_delta <= day_end and len(slots) < 8:
            slot_end = cursor + dur_delta
            if target == datetime.now().date() and cursor <= now_naive:
                cursor += step
                continue
            if not any(cursor < b_end and b_start < slot_end for b_start, b_end in busy):
                slots.append(cursor.strftime("%H:%M"))
            cursor += step
        return {"date": target.isoformat(), "day": target.strftime("%A"),
                "slots": slots, "closed": False}

    def t_create_booking(args):
        svc = db.session.get(Service, args.get("service_id"))
        if not svc:
            return {"confirmed": False, "error": "Unknown service id"}
        ref = uuid.uuid4().hex[:8].upper()
        appt = Appointment(
            reference=ref,
            service_id=svc.id,
            client_name=args.get("name", ""),
            client_email=args.get("email", ""),
            client_phone=args.get("phone", ""),
            notes=args.get("notes", ""),
            date=args.get("date", ""),
            time=args.get("time", ""),
            status="confirmed")
        db.session.add(appt)
        db.session.commit()
        return {"confirmed": True, "reference": ref, "service": svc.name,
                "date": appt.date, "time": appt.time, "name": appt.client_name}

    def t_callback(args):
        try:
            from models import ContactMessage
            msg = ContactMessage(
                name=args.get("name", ""), email="",
                category="Callback", subject="Callback requested",
                message=("Phone: " + args.get("phone", "") + "\n"
                         "Reason: " + args.get("reason", "General enquiry")))
            db.session.add(msg)
            db.session.commit()
            return {"logged": True, "id": msg.id}
        except Exception as e:
            return {"logged": False, "error": str(e)}

    def t_menu_categories():
        if MenuCategory is None:
            return {"error": "Menu categories not available"}
        cats = MenuCategory.query.order_by(MenuCategory.display_order).all()
        return {"categories": [{"id": c.id, "name": c.name} for c in cats]}

    def t_menu(args):
        if MenuItem is None:
            return {"error": "Menu not available"}
        cat = (args.get("category") or "").strip().lower()
        query = (args.get("query") or "").strip().lower()
        items = MenuItem.query.filter_by(available=True).order_by(MenuItem.name).all()
        out = []
        for it in items:
            try:
                cat_name = it.category.name if it.category else ""
            except Exception:
                cat_name = ""
            if cat and cat not in cat_name.lower():
                continue
            if query:
                hay = (it.name + " " + (it.description or "")).lower()
                if query not in hay:
                    continue
            out.append({"name": it.name,
                        "price": float(it.price or 0),
                        "price_display": _fmt_money(it.price),
                        "category": cat_name,
                        "description": it.description})
        return {"items": out[:25], "count": len(out)}

    def t_order_status(reference):
        if Order is None:
            return {"error": "Order lookup not available"}
        ref = (reference or "").strip().upper()
        order = Order.query.filter_by(reference=ref).first()
        if not order:
            return {"found": False, "message": "No order found with that reference."}
        items = []
        try:
            for it in order.items:
                items.append({"name": it.name, "qty": it.quantity})
        except Exception:
            pass
        return {"found": True,
                "reference": order.reference,
                "status": order.status,
                "order_type": order.order_type,
                "total_display": _fmt_money(order.total),
                "items": items}

    def t_transfer(reason):
        return {"transferred": True, "reason": reason or "Customer request"}

    def dispatch(name, args):
        try:
            if name == "get_business_info":     return t_info()
            if name == "get_business_hours":    return t_hours()
            if name == "get_services":          return t_services()
            if name == "get_service_details":   return t_service_details(args.get("query", ""))
            if name == "compare_services":      return t_compare(args.get("names", []))
            if name == "get_available_slots":
                return t_slots(args.get("day_hint", "tomorrow"), args.get("service_id"))
            if name == "create_booking":        return t_create_booking(args)
            if name == "request_callback":      return t_callback(args)
            if name == "list_menu_categories":  return t_menu_categories()
            if name == "search_menu":           return t_menu(args)
            if name == "get_order_status":      return t_order_status(args.get("reference", ""))
            if name == "transfer_to_human":     return t_transfer(args.get("reason", ""))
        except Exception as e:
            app.logger.exception("Tool %s failed", name)
            return {"error": str(e)}
        return {"error": "unknown tool " + name}

    # =========================================================================
    # FALLBACK (only if no API key)
    # =========================================================================
    def fallback_reply(message):
        s = get_settings()
        t = (message or "").lower()
        if any(k in t for k in ["human","agent","manager","speak to someone"]):
            return {"reply": "Of course. Let me transfer you to a member of our team.",
                    "escalate": True}
        svc = _match_service(t)
        if svc and any(w in t for w in ["price","cost","how much","charge"]):
            return {"reply": svc.name + " is " + _fmt_money(svc.price) + "."}
        if any(w in t for w in ["hours","open","close","what time"]):
            return {"reply": "We are open " + (s.open_time or "09:00") + " to " + (s.close_time or "17:00") + "."}
        if any(w in t for w in ["where","location","address"]):
            return {"reply": "We are at " + (s.address or "our offices") + "."}
        if any(w in t for w in ["book","appointment","schedule"]):
            return {"reply": "I can help with that. Which service would you like to book?"}
        if any(w in t for w in ["hi","hello","hey"]):
            return {"reply": "Hello, thank you for calling " + s.company_name + ". How can I help?"}
        return {"reply": "I'm not sure I caught that. Could you tell me a little more?"}

    # =========================================================================
    # ROUTES
    # =========================================================================
    @app.route("/api/voice/health", methods=["GET"])
    def voice_health():
        return jsonify({"ok": True, "backend": "flask",
                        "ai": provider, "model": model})

    @app.route("/api/voice/chat", methods=["POST", "OPTIONS"])
    def voice_chat():
        if request.method == "OPTIONS":
            return ("", 204)
               data = request.get_json(silent=True) or {}
        message = (data.get("message") or "").strip()
        conversation_id = (data.get("conversation_id") or "default").strip()

        # ---- input validation ----
        if not message:
            return jsonify({"reply": "Sorry, I didn't catch that."}), 400
        if len(message) > 800:
            message = message[:800]
        if len(conversation_id) > 64:
            conversation_id = conversation_id[:64]

        # Strip control characters that could confuse the model.
        message = "".join(ch for ch in message if ch.isprintable() or ch in " \n\t")

        # Prompt-injection guard: block obvious overrides.
        lowered = message.lower()
        injection_markers = [
            "ignore previous", "ignore all previous", "system prompt",
            "developer message", "reveal your instructions",
            "print your prompt", "you are now", "act as", "jailbreak",
            "disregard your instructions", "forget your rules",
        ]
        if any(m in lowered for m in injection_markers):
            return jsonify({
                "reply": "I can only help with questions about Mshibe Group. "
                         "Would you like me to connect you with a member of the team?",
                "escalate": False
            })
        if not client:
            return jsonify(fallback_reply(message))

        history = _conversations.setdefault(conversation_id, [])
        history.append({"role": "user", "content": message})
        if len(history) > 50:
            history[:] = history[-50:]

        try:
            messages = [{"role": "system", "content": system_prompt()}] + history
            tools = tools_schema()
            resp = client.chat.completions.create(
                model=model, messages=messages, tools=tools,
                tool_choice="auto", temperature=0.65)
            msg = resp.choices[0].message
            escalate = False

            for _ in range(8):
                if not getattr(msg, "tool_calls", None):
                    break
                messages.append({
                    "role": "assistant",
                    "content": msg.content or "",
                    "tool_calls": [
                        {"id": tc.id, "type": "function",
                         "function": {"name": tc.function.name,
                                      "arguments": tc.function.arguments}}
                        for tc in msg.tool_calls]})
                for tc in msg.tool_calls:
                    try:
                        args = json.loads(tc.function.arguments or "{}")
                    except Exception:
                        args = {}
                    result = dispatch(tc.function.name, args)
                    if tc.function.name == "transfer_to_human":
                        escalate = True
                    messages.append({"role": "tool", "tool_call_id": tc.id,
                                     "content": json.dumps(result, default=str)})
                resp = client.chat.completions.create(
                    model=model, messages=messages, tools=tools,
                    tool_choice="auto", temperature=0.65)
                msg = resp.choices[0].message

            reply_text = (msg.content or "").strip() or "Sorry, could you say that again?"
            history.append({"role": "assistant", "content": reply_text})
            return jsonify({"reply": reply_text, "escalate": escalate})
        except Exception as e:
            app.logger.exception("AI error")
            fb = fallback_reply(message)
            fb["error"] = str(e)
            return jsonify(fb)

    engine = (provider + " " + model) if client else "FALLBACK (no API key set)"
    print("  AI receptionist: " + engine)
    print("  Endpoints:       GET /api/voice/health   POST /api/voice/chat")


if __name__ == "__main__":
    from flask import Flask
    from flask_cors import CORS
    from models import Service, Appointment, SiteSetting, db as _db, MenuItem, MenuCategory, Order
    mini = Flask(__name__)
    CORS(mini)
    mini.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///mshibe.db"
    mini.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
    _db.init_app(mini)
    def _get_settings():
        s = SiteSetting.query.first()
        if s is None:
            s = SiteSetting(company_name="Mshibe Group")
            _db.session.add(s); _db.session.commit()
        return s
    with mini.app_context():
        register(mini, _get_settings, Service, Appointment, _db,
                 MenuItem=MenuItem, MenuCategory=MenuCategory, Order=Order)
    mini.run(host="0.0.0.0", port=5001, debug=False)
