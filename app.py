"""Mshibe Group - multi-service platform.

Services offered:
  * Food ordering (delivery or pickup)
  * Appointment booking

Default admin login -> admin / admin123
"""

import os
import uuid
from datetime import date as date_cls, datetime, time, timedelta
from functools import wraps

from flask import (
    Flask,
    Response,
    abort,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    session,
    url_for,
    send_from_directory,
)

from werkzeug.security import check_password_hash, generate_password_hash

from models import (
    Appointment, ContactMessage, MenuCategory, MenuItem, Order, OrderItem,
    Service, SiteSetting, db,
)

# ---------------------------------------------------------------------------
# App setup
# ---------------------------------------------------------------------------
app = Flask(__name__)
app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", "mshibe-dev-secret-change-me")
app.config["SQLALCHEMY_DATABASE_URI"] = os.environ.get(
    "DATABASE_URL", "sqlite:///mshibe.db"
)
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

db.init_app(app)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def get_settings() -> SiteSetting:
    s = SiteSetting.query.first()
    if s is None:
        s = SiteSetting(
            company_name="Mshibe Group",
            tagline="Great food and easy appointments — all in one place.",
            about_text=(
                "Mshibe Group is a multi-service company proudly serving our "
                "community. From fresh, delicious meals delivered to your "
                "doorstep, to professional appointment-based services, we make "
                "everyday life simpler."
            ),
        )
        s.admin_password_hash = generate_password_hash("admin123")
        db.session.add(s)
        db.session.commit()
    else:
        # Keep the public corporate contact details aligned with the current brand.
        changed = False
        if s.email != "MshibeGroup@gmail.com":
            s.email = "MshibeGroup@gmail.com"
            changed = True
        if s.phone:
            s.phone = ""
            changed = True
        if changed:
            db.session.commit()
    return s


def parse_hhmm(v: str):
    h, m = v.split(":")
    return int(h), int(m)


def make_reference() -> str:
    return uuid.uuid4().hex[:8].upper()


def admin_required(view):
    @wraps(view)
    def wrapper(*args, **kwargs):
        if not session.get("admin_logged_in"):
            return redirect(url_for("admin_login", next=request.path))
        return view(*args, **kwargs)
    return wrapper


def get_available_slots(date_str: str, service: Service):
    """Return list of free 'HH:MM' strings for a service on a date."""
    settings = get_settings()

    try:
        day = datetime.strptime(date_str, "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return []

    if not settings.is_open_on(day.weekday()) or day < date_cls.today():
        return []

    open_h, open_m = parse_hhmm(settings.open_time)
    close_h, close_m = parse_hhmm(settings.close_time)

    day_start = datetime.combine(day, time(open_h, open_m))
    day_end = datetime.combine(day, time(close_h, close_m))

    step = timedelta(minutes=settings.slot_duration or 30)
    duration = timedelta(minutes=service.duration or settings.slot_duration or 30)

    existing = Appointment.query.filter(
        Appointment.date == date_str,
        Appointment.status != "cancelled",
    ).all()

    busy = []
    for appt in existing:
        a_h, a_m = parse_hhmm(appt.time)
        a_start = datetime.combine(day, time(a_h, a_m))
        mins = appt.service.duration if appt.service else settings.slot_duration
        busy.append((a_start, a_start + timedelta(minutes=mins or 30)))

    now = datetime.now()
    slots = []
    cursor = day_start
    while cursor + duration <= day_end:
        if day == date_cls.today() and cursor <= now:
            cursor += step
            continue
        slot_end = cursor + duration
        if not any(cursor < b_end and b_start < slot_end for b_start, b_end in busy):
            slots.append(cursor.strftime("%H:%M"))
        cursor += step
    return slots


# ---------------------------------------------------------------------------
# Template helpers
# ---------------------------------------------------------------------------
@app.context_processor
def inject_globals():
    return {
        "settings": get_settings(),
        "current_year": datetime.now().year,
        "cart_count": sum(session.get("cart", {}).values())
        if session.get("cart") else 0,
    }


@app.template_filter("fmt_time")
def fmt_time(value):
    try:
        h, m = parse_hhmm(value)
    except Exception:
        return value
    suffix = "AM" if h < 12 else "PM"
    return f"{h % 12 or 12}:{m:02d} {suffix}"


@app.template_filter("fmt_date")
def fmt_date(value):
    try:
        return datetime.strptime(value, "%Y-%m-%d").strftime("%a, %b %d, %Y")
    except Exception:
        return value


@app.template_filter("money")
def money(value):
    s = get_settings()
    try:
        return f"{s.currency_symbol}{float(value):,.2f}"
    except (TypeError, ValueError):
        return f"{s.currency_symbol}0.00"


# ---------------------------------------------------------------------------
# Public pages
# ---------------------------------------------------------------------------
@app.route("/")
def index():
    featured_items = MenuItem.query.filter_by(
        available=True, featured=True
    ).limit(4).all()
    if not featured_items:
        featured_items = MenuItem.query.filter_by(available=True).limit(4).all()

    services = Service.query.filter_by(active=True).limit(3).all()
    categories = MenuCategory.query.order_by(MenuCategory.display_order).all()

    return render_template(
        "index.html",
        featured_items=featured_items,
        services=services,
        categories=categories,
    )


@app.route("/about")
def about():
    return render_template("about.html")


@app.route("/food")
def food_platform():
    return render_template("food_platform.html")


@app.route("/appointment")
def appointment_platform():
    return render_template("appointment_platform.html")


# ---------------------------------------------------------------------------
# Dedicated Mshibe Group service pages
# ---------------------------------------------------------------------------
CORPORATE_SERVICES = {
    "technology": {
        "number": "01",
        "title": "Technology",
        "eyebrow": "DIGITAL PRODUCTS & SYSTEMS",
        "icon": "cpu",
        "intro": "We design practical digital products, platforms and systems that make everyday business simpler, faster and more connected.",
        "body": "From customer-facing experiences to internal workflows, we focus on useful technology that can start small, prove value and grow with the business.",
        "capabilities": [
            ("Digital Products", "Web platforms and customer experiences designed around real user journeys."),
            ("Business Systems", "Simple tools and workflows that reduce friction and improve day-to-day operations."),
            ("Automation", "Connected processes that remove repetitive work and keep information moving."),
            ("Product Development", "From early concept to working product, we turn ideas into something people can use."),
        ],
    },
    "media-marketing": {
        "number": "02",
        "title": "Media & Marketing",
        "eyebrow": "BRANDS, CONTENT & GROWTH",
        "icon": "broadcast",
        "intro": "We help businesses become easier to discover, understand and remember through strategy, content and digital growth.",
        "body": "Our approach combines brand thinking with practical execution: clear positioning, strong content and measurable digital activity built around the customer journey.",
        "capabilities": [
            ("Content Strategy", "Content plans built around audience needs, business goals and consistent brand direction."),
            ("Social Media", "Platform-specific content and management designed to build attention and trust."),
            ("SEO & Discovery", "Improve how people find your business across search and digital channels."),
            ("Performance Analytics", "Use meaningful data to understand what is working and where to improve."),
        ],
    },
    "commerce": {
        "number": "03",
        "title": "Commerce",
        "eyebrow": "CUSTOMER EXPERIENCES",
        "icon": "shop",
        "intro": "We build technology-enabled commerce experiences that connect customers with products, services and local businesses.",
        "body": "Chowza is one example of this approach: a focused ordering experience designed to make discovering food, building an order and completing checkout easier.",
        "capabilities": [
            ("Ordering Experiences", "Clear digital journeys that help customers browse, choose and buy with less friction."),
            ("Local Commerce", "Technology that helps smaller and local businesses participate in digital commerce."),
            ("Customer Journeys", "Thoughtful flows from discovery through checkout, confirmation and follow-up."),
            ("Commerce Platforms", "Flexible foundations that can evolve as the business and customer base grows."),
        ],
    },
    "business-solutions": {
        "number": "04",
        "title": "Business Solutions",
        "eyebrow": "PRACTICAL TOOLS & SERVICES",
        "icon": "lightbulb",
        "intro": "We create practical tools and services that help ambitious businesses operate better, serve customers and grow.",
        "body": "The goal is not complexity. We focus on solutions that solve a clear business problem, are easy to use and can be improved over time.",
        "capabilities": [
            ("Business Workflows", "Clear processes and tools for the work that keeps a business moving."),
            ("Customer Operations", "Systems that make enquiries, bookings, orders and follow-ups easier to manage."),
            ("Digital Presence", "Web experiences that give customers a clear place to understand and engage with a business."),
            ("Growth Support", "Practical digital support for businesses ready to improve their next stage of growth."),
        ],
    },
}


@app.route("/services/<slug>")
def corporate_service(slug):
    service = CORPORATE_SERVICES.get(slug)
    if not service:
        abort(404)
    return render_template("service_detail.html", service=service, slug=slug)


@app.route("/chowza")
def chowza():
    return redirect(url_for("food_platform"))


@app.route("/appoint")
def appoint():
    return redirect(url_for("appointment_platform"))


@app.route("/contact", methods=["GET", "POST"])
def contact():
    if request.method == "POST":
        name = (request.form.get("name") or "").strip()
        email = (request.form.get("email") or "").strip()
        category = (request.form.get("category") or "").strip()
        other_category = (request.form.get("other_category") or "").strip()
        subject = (request.form.get("subject") or "").strip()
        message = (request.form.get("message") or "").strip()

        if not name or not email or not subject or not message:
            flash("Please complete all required fields.", "error")
            return render_template("contact.html", form=request.form)

        if category not in ContactMessage.CATEGORIES:
            flash("Please choose a valid category.", "error")
            return render_template("contact.html", form=request.form)

        if category == "Other" and not other_category:
            flash("Please tell us what your enquiry is about.", "error")
            return render_template("contact.html", form=request.form)

        contact_message = ContactMessage(
            name=name,
            email=email,
            category=category,
            other_category=other_category if category == "Other" else "",
            subject=subject,
            message=message,
        )
        db.session.add(contact_message)
        db.session.commit()
        flash("Thanks for reaching out! Your message has been sent.", "success")
        return redirect(url_for("contact"))
    return render_template("contact.html", form={})


@app.route("/terms")
def terms():
    return render_template("terms.html")


@app.route("/privacy")
def privacy():
    return render_template("privacy.html")


# ---------------------------------------------------------------------------
# Food ordering
# ---------------------------------------------------------------------------
@app.route("/menu")
def menu():
    categories = MenuCategory.query.order_by(MenuCategory.display_order).all()
    selected_cat = request.args.get("category", type=int)
    query = MenuItem.query.filter_by(available=True)
    if selected_cat:
        query = query.filter_by(category_id=selected_cat)
    items = query.order_by(MenuItem.name).all()
    return render_template(
        "food/menu.html",
        categories=categories,
        items=items,
        selected_cat=selected_cat,
    )


@app.route("/cart/add/<int:item_id>", methods=["POST"])
def cart_add(item_id):
    item = db.session.get(MenuItem, item_id)
    if not item or not item.available:
        abort(404)

    cart = session.get("cart", {})
    key = str(item.id)
    cart[key] = cart.get(key, 0) + 1
    session["cart"] = cart

    if request.headers.get("X-Requested-With") == "XMLHttpRequest":
        return jsonify({"ok": True, "cart_count": sum(cart.values())})

    flash(f"{item.name} added to your cart.", "success")
    return redirect(request.referrer or url_for("menu"))


@app.route("/cart")
def cart():
    cart_data = session.get("cart", {})
    items = []
    subtotal = 0.0

    for item_id, qty in cart_data.items():
        item = db.session.get(MenuItem, int(item_id))
        if not item:
            continue
        line_total = item.price * qty
        subtotal += line_total
        items.append({"item": item, "qty": qty, "line_total": line_total})

    settings = get_settings()
    delivery_fee = 0.0  # decided at checkout based on delivery/pickup
    return render_template(
        "food/cart.html",
        items=items,
        subtotal=subtotal,
        delivery_fee=settings.delivery_fee,
    )


@app.route("/cart/update/<int:item_id>", methods=["POST"])
def cart_update(item_id):
    qty = request.form.get("qty", type=int) or 0
    cart = session.get("cart", {})
    key = str(item_id)

    if qty <= 0:
        cart.pop(key, None)
    else:
        cart[key] = qty

    session["cart"] = cart
    return redirect(url_for("cart"))


@app.route("/cart/remove/<int:item_id>", methods=["POST"])
def cart_remove(item_id):
    cart = session.get("cart", {})
    cart.pop(str(item_id), None)
    session["cart"] = cart
    flash("Item removed from your cart.", "success")
    return redirect(url_for("cart"))


@app.route("/checkout", methods=["POST"])
def checkout():
    cart_data = session.get("cart", {})
    if not cart_data:
        flash("Your cart is empty.", "error")
        return redirect(url_for("menu"))

    name = (request.form.get("customer_name") or "").strip()
    email = (request.form.get("customer_email") or "").strip()
    phone = (request.form.get("customer_phone") or "").strip()
    address = (request.form.get("delivery_address") or "").strip()
    notes = (request.form.get("notes") or "").strip()
    order_type = request.form.get("order_type", "delivery")

    if not name or not email:
        flash("Name and email are required.", "error")
        return redirect(url_for("cart"))

    if order_type == "delivery" and not address:
        flash("Please provide a delivery address, or choose pickup.", "error")
        return redirect(url_for("cart"))

    settings = get_settings()

    order = Order(
        reference=make_reference(),
        customer_name=name,
        customer_email=email,
        customer_phone=phone,
        delivery_address=address if order_type == "delivery" else "PICKUP",
        notes=notes,
        order_type=order_type,
        status="pending",
    )

    subtotal = 0.0
    for item_id, qty in cart_data.items():
        item = db.session.get(MenuItem, int(item_id))
        if not item or not item.available:
            continue
        subtotal += item.price * qty
        order.items.append(
            OrderItem(
                menu_item_id=item.id,
                name=item.name,
                price=item.price,
                quantity=qty,
            )
        )

    delivery_fee = settings.delivery_fee if order_type == "delivery" else 0.0

    order.subtotal = round(subtotal, 2)
    order.delivery_fee = round(delivery_fee, 2)
    order.total = round(subtotal + delivery_fee, 2)

    db.session.add(order)
    db.session.commit()

    session["cart"] = {}
    return redirect(url_for("order_confirmation", reference=order.reference))


@app.route("/order/<reference>")
def order_confirmation(reference):
    order = Order.query.filter_by(reference=reference).first()
    if not order:
        abort(404)
    return render_template("food/order_confirmation.html", order=order)


# ---------------------------------------------------------------------------
# Appointments
# ---------------------------------------------------------------------------
@app.route("/appointments")
def appointments_index():
    services = Service.query.filter_by(active=True).order_by(Service.name).all()
    return render_template("appointments/services.html", services=services)


@app.route("/appointments/service/<int:service_id>")
def appointment_service_detail(service_id):
    service = Service.query.filter_by(id=service_id, active=True).first_or_404()
    return render_template("appointments/service_detail.html", service=service)


@app.route("/appointments/book", methods=["GET", "POST"])
def appointments_book():
    services = Service.query.filter_by(active=True).order_by(Service.name).all()
    if not services:
        flash("No services are available for booking right now.", "error")
        return redirect(url_for("appointments_index"))

    if request.method == "POST":
        service_id = request.form.get("service_id", type=int)
        date_str = (request.form.get("date") or "").strip()
        time_str = (request.form.get("time") or "").strip()
        name = (request.form.get("client_name") or "").strip()
        email = (request.form.get("client_email") or "").strip()
        phone = (request.form.get("client_phone") or "").strip()
        notes = (request.form.get("notes") or "").strip()

        service = db.session.get(Service, service_id) if service_id else None

        def fail(msg):
            flash(msg, "error")
            return render_template(
                "appointments/book.html",
                services=services,
                selected_service=service_id,
                form=request.form,
            ), 400

        if not service or not service.active:
            return fail("Please choose a valid service.")
        if not name or not email:
            return fail("Name and email are required.")
        if not date_str or not time_str:
            return fail("Please choose a date and time slot.")

        if time_str not in get_available_slots(date_str, service):
            return fail("That time slot is no longer available. Please pick another.")

        appt = Appointment(
            reference=make_reference(),
            service_id=service.id,
            client_name=name,
            client_email=email,
            client_phone=phone,
            notes=notes,
            date=date_str,
            time=time_str,
            status="pending",
        )
        db.session.add(appt)
        db.session.commit()
        return redirect(url_for("appointment_confirmation", reference=appt.reference))

    selected = request.args.get("service", type=int)
    return render_template(
        "appointments/book.html",
        services=services,
        selected_service=selected,
        form=None,
    )


@app.route("/appointments/confirmation/<reference>")
def appointment_confirmation(reference):
    appt = Appointment.query.filter_by(reference=reference).first()
    if not appt:
        abort(404)
    return render_template("appointments/confirmation.html", appointment=appt)


@app.route("/appointments/cancel/<reference>", methods=["POST"])
def appointment_cancel(reference):
    appt = Appointment.query.filter_by(reference=reference).first()
    if not appt:
        abort(404)
    if appt.status in ("cancelled", "completed"):
        flash("This appointment can no longer be cancelled.", "error")
    else:
        appt.status = "cancelled"
        db.session.commit()
        flash("Your appointment has been cancelled.", "success")
    return redirect(url_for("appointment_confirmation", reference=reference))


@app.route("/api/slots")
def api_slots():
    date_str = request.args.get("date", "")
    service_id = request.args.get("service_id", type=int)
    service = db.session.get(Service, service_id) if service_id else None
    if not service:
        return jsonify({"slots": [], "message": "Select a service first."})
    slots = get_available_slots(date_str, service)
    return jsonify({
        "slots": slots,
        "message": "" if slots else "No available times for this date.",
    })


# ---------------------------------------------------------------------------
# Admin auth
# ---------------------------------------------------------------------------
@app.route("/admin/login", methods=["GET", "POST"])
def admin_login():
    settings = get_settings()
    if request.method == "POST":
        username = (request.form.get("username") or "").strip()
        password = request.form.get("password") or ""
        if username == settings.admin_username and check_password_hash(
            settings.admin_password_hash or "", password
        ):
            session["admin_logged_in"] = True
            flash("Welcome back!", "success")
            return redirect(request.args.get("next") or url_for("admin_dashboard"))
        flash("Invalid username or password.", "error")
    return render_template("admin/login.html")


@app.route("/admin/logout")
def admin_logout():
    session.pop("admin_logged_in", None)
    flash("Logged out.", "success")
    return redirect(url_for("index"))


# ---------------------------------------------------------------------------
# Admin dashboard
# ---------------------------------------------------------------------------
@app.route("/admin")
@admin_required
def admin_dashboard():
    today_str = date_cls.today().strftime("%Y-%m-%d")
    stats = {
        "orders_total": Order.query.count(),
        "orders_pending": Order.query.filter(
            Order.status.in_(("pending", "preparing", "ready", "out_for_delivery"))
        ).count(),
        "orders_today": Order.query.filter(
            db.func.date(Order.created_at) == today_str
        ).count(),
        "appt_total": Appointment.query.count(),
        "appt_upcoming": Appointment.query.filter(
            Appointment.date >= today_str,
            Appointment.status.in_(("pending", "confirmed")),
        ).count(),
        "messages_new": ContactMessage.query.filter_by(status="new").count(),
        "revenue": db.session.query(
            db.func.coalesce(db.func.sum(Order.total), 0.0)
        ).filter(Order.status != "cancelled").scalar(),
    }
    recent_orders = Order.query.order_by(Order.created_at.desc()).limit(5).all()
    upcoming_appts = Appointment.query.filter(
        Appointment.date >= today_str
    ).order_by(Appointment.date, Appointment.time).limit(5).all()
    return render_template(
        "admin/dashboard.html",
        stats=stats,
        recent_orders=recent_orders,
        upcoming_appts=upcoming_appts,
    )


# ---------------------------------------------------------------------------
# Admin: orders
# ---------------------------------------------------------------------------
@app.route("/admin/orders")
@admin_required
def admin_orders():
    status_filter = request.args.get("status", "")
    query = Order.query
    if status_filter in Order.STATUSES:
        query = query.filter(Order.status == status_filter)
    orders = query.order_by(Order.created_at.desc()).all()
    return render_template(
        "admin/orders.html", orders=orders, status_filter=status_filter
    )


@app.route("/admin/orders/<int:order_id>/status", methods=["POST"])
@admin_required
def admin_order_status(order_id):
    order = db.session.get(Order, order_id)
    if not order:
        abort(404)
    new_status = request.form.get("status", "")
    if new_status in Order.STATUSES:
        order.status = new_status
        db.session.commit()
        flash(f"Order {order.reference} set to {new_status}.", "success")
    return redirect(request.referrer or url_for("admin_orders"))


@app.route("/admin/orders/<int:order_id>/delete", methods=["POST"])
@admin_required
def admin_order_delete(order_id):
    order = db.session.get(Order, order_id)
    if not order:
        abort(404)
    db.session.delete(order)
    db.session.commit()
    flash("Order deleted.", "success")
    return redirect(url_for("admin_orders"))


# ---------------------------------------------------------------------------
# Admin: appointments
# ---------------------------------------------------------------------------
@app.route("/admin/appointments")
@admin_required
def admin_appointments():
    status_filter = request.args.get("status", "")
    date_filter = request.args.get("date", "")
    query = Appointment.query
    if status_filter in Appointment.STATUSES:
        query = query.filter(Appointment.status == status_filter)
    if date_filter:
        query = query.filter(Appointment.date == date_filter)
    appts = query.order_by(
        Appointment.date.desc(), Appointment.time.asc()
    ).all()
    return render_template(
        "admin/appointments.html",
        appointments=appts,
        status_filter=status_filter,
        date_filter=date_filter,
    )


@app.route("/admin/appointments/<int:appt_id>/status", methods=["POST"])
@admin_required
def admin_appt_status(appt_id):
    appt = db.session.get(Appointment, appt_id)
    if not appt:
        abort(404)
    new_status = request.form.get("status", "")
    if new_status in Appointment.STATUSES:
        appt.status = new_status
        db.session.commit()
        flash(f"Appointment {appt.reference} set to {new_status}.", "success")
    return redirect(request.referrer or url_for("admin_appointments"))


@app.route("/admin/appointments/<int:appt_id>/delete", methods=["POST"])
@admin_required
def admin_appt_delete(appt_id):
    appt = db.session.get(Appointment, appt_id)
    if not appt:
        abort(404)
    db.session.delete(appt)
    db.session.commit()
    flash("Appointment deleted.", "success")
    return redirect(url_for("admin_appointments"))


# ---------------------------------------------------------------------------
# Admin: menu items
# ---------------------------------------------------------------------------
@app.route("/admin/menu")
@admin_required
def admin_menu():
    categories = MenuCategory.query.order_by(MenuCategory.display_order).all()
    items = MenuItem.query.order_by(MenuItem.name).all()
    return render_template(
        "admin/menu_items.html", categories=categories, items=items
    )


@app.route("/admin/menu/new", methods=["GET", "POST"])
@admin_required
def admin_menu_new():
    categories = MenuCategory.query.order_by(MenuCategory.display_order).all()
    if request.method == "POST":
        item = MenuItem(
            category_id=request.form.get("category_id", type=int),
            name=(request.form.get("name") or "").strip(),
            description=(request.form.get("description") or "").strip(),
            price=request.form.get("price", type=float) or 0.0,
            image_url=(request.form.get("image_url") or "").strip(),
            available=bool(request.form.get("available")),
            featured=bool(request.form.get("featured")),
        )
        if not item.name or not item.category_id:
            flash("Name and category are required.", "error")
        else:
            db.session.add(item)
            db.session.commit()
            flash("Menu item created.", "success")
            return redirect(url_for("admin_menu"))
    return render_template(
        "admin/menu_item_form.html", item=None, categories=categories
    )


@app.route("/admin/menu/<int:item_id>/edit", methods=["GET", "POST"])
@admin_required
def admin_menu_edit(item_id):
    item = db.session.get(MenuItem, item_id)
    if not item:
        abort(404)
    categories = MenuCategory.query.order_by(MenuCategory.display_order).all()
    if request.method == "POST":
        item.category_id = request.form.get("category_id", type=int)
        item.name = (request.form.get("name") or "").strip()
        item.description = (request.form.get("description") or "").strip()
        item.price = request.form.get("price", type=float) or 0.0
        item.image_url = (request.form.get("image_url") or "").strip()
        item.available = bool(request.form.get("available"))
        item.featured = bool(request.form.get("featured"))
        if not item.name or not item.category_id:
            flash("Name and category are required.", "error")
        else:
            db.session.commit()
            flash("Menu item updated.", "success")
            return redirect(url_for("admin_menu"))
    return render_template(
        "admin/menu_item_form.html", item=item, categories=categories
    )


@app.route("/admin/menu/<int:item_id>/delete", methods=["POST"])
@admin_required
def admin_menu_delete(item_id):
    item = db.session.get(MenuItem, item_id)
    if not item:
        abort(404)
    db.session.delete(item)
    db.session.commit()
    flash("Menu item deleted.", "success")
    return redirect(url_for("admin_menu"))


@app.route("/admin/categories/new", methods=["POST"])
@admin_required
def admin_category_new():
    name = (request.form.get("name") or "").strip()
    if name:
        max_order = db.session.query(
            db.func.coalesce(db.func.max(MenuCategory.display_order), 0)
        ).scalar()
        db.session.add(MenuCategory(name=name, display_order=max_order + 1))
        db.session.commit()
        flash(f"Category '{name}' added.", "success")
    return redirect(url_for("admin_menu"))


@app.route("/admin/categories/<int:cat_id>/delete", methods=["POST"])
@admin_required
def admin_category_delete(cat_id):
    cat = db.session.get(MenuCategory, cat_id)
    if not cat:
        abort(404)
    if cat.items:
        flash("Delete or move items in this category first.", "error")
    else:
        db.session.delete(cat)
        db.session.commit()
        flash("Category deleted.", "success")
    return redirect(url_for("admin_menu"))


# ---------------------------------------------------------------------------
# Admin: services
# ---------------------------------------------------------------------------
@app.route("/admin/services")
@admin_required
def admin_services():
    services = Service.query.order_by(Service.name).all()
    return render_template("admin/services.html", services=services)


@app.route("/admin/services/new", methods=["GET", "POST"])
@admin_required
def admin_service_new():
    if request.method == "POST":
        service = Service(
            name=(request.form.get("name") or "").strip(),
            description=(request.form.get("description") or "").strip(),
            duration=request.form.get("duration", type=int) or 30,
            price=request.form.get("price", type=float) or 0.0,
            active=bool(request.form.get("active")),
        )
        if not service.name:
            flash("Service name is required.", "error")
        else:
            db.session.add(service)
            db.session.commit()
            flash("Service created.", "success")
            return redirect(url_for("admin_services"))
    return render_template("admin/service_form.html", service=None)


@app.route("/admin/services/<int:service_id>/edit", methods=["GET", "POST"])
@admin_required
def admin_service_edit(service_id):
    service = db.session.get(Service, service_id)
    if not service:
        abort(404)
    if request.method == "POST":
        service.name = (request.form.get("name") or "").strip()
        service.description = (request.form.get("description") or "").strip()
        service.duration = request.form.get("duration", type=int) or 30
        service.price = request.form.get("price", type=float) or 0.0
        service.active = bool(request.form.get("active"))
        if not service.name:
            flash("Service name is required.", "error")
        else:
            db.session.commit()
            flash("Service updated.", "success")
            return redirect(url_for("admin_services"))
    return render_template("admin/service_form.html", service=service)


@app.route("/admin/services/<int:service_id>/delete", methods=["POST"])
@admin_required
def admin_service_delete(service_id):
    service = db.session.get(Service, service_id)
    if not service:
        abort(404)
    if service.appointments:
        service.active = False
        db.session.commit()
        flash("Service has bookings — deactivated instead.", "error")
    else:
        db.session.delete(service)
        db.session.commit()
        flash("Service deleted.", "success")
    return redirect(url_for("admin_services"))


# ---------------------------------------------------------------------------
# Admin: contact messages
# ---------------------------------------------------------------------------
@app.route("/admin/messages")
@admin_required
def admin_messages():
    category_filter = request.args.get("category", "")
    status_filter = request.args.get("status", "")
    query = ContactMessage.query
    if category_filter in ContactMessage.CATEGORIES:
        query = query.filter(ContactMessage.category == category_filter)
    if status_filter in ContactMessage.STATUSES:
        query = query.filter(ContactMessage.status == status_filter)
    messages = query.order_by(ContactMessage.created_at.desc()).all()
    return render_template(
        "admin/messages.html",
        messages=messages,
        categories=ContactMessage.CATEGORIES,
        statuses=ContactMessage.STATUSES,
        category_filter=category_filter,
        status_filter=status_filter,
    )


@app.route("/admin/messages/<int:message_id>/read", methods=["POST"])
@admin_required
def admin_message_read(message_id):
    contact_message = db.session.get(ContactMessage, message_id)
    if not contact_message:
        abort(404)
    contact_message.status = "read"
    db.session.commit()
    return redirect(request.referrer or url_for("admin_messages"))


@app.route("/admin/messages/<int:message_id>/delete", methods=["POST"])
@admin_required
def admin_message_delete(message_id):
    contact_message = db.session.get(ContactMessage, message_id)
    if not contact_message:
        abort(404)
    db.session.delete(contact_message)
    db.session.commit()
    flash("Message deleted.", "success")
    return redirect(request.referrer or url_for("admin_messages"))


# ---------------------------------------------------------------------------
# Admin: settings
# ---------------------------------------------------------------------------
@app.route("/admin/settings", methods=["GET", "POST"])
@admin_required
def admin_settings():
    settings = get_settings()
    if request.method == "POST":
        settings.company_name = (request.form.get("company_name") or "").strip() or "Mshibe Group"
        settings.tagline = (request.form.get("tagline") or "").strip()
        settings.about_text = (request.form.get("about_text") or "").strip()
        settings.email = (request.form.get("email") or "").strip()
        settings.phone = (request.form.get("phone") or "").strip()
        settings.address = (request.form.get("address") or "").strip()
        settings.delivery_fee = request.form.get("delivery_fee", type=float) or 0.0
        settings.currency_symbol = (request.form.get("currency_symbol") or "R").strip()[:5]
        settings.slot_duration = request.form.get("slot_duration", type=int) or 30
        settings.open_time = request.form.get("open_time") or "09:00"
        settings.close_time = request.form.get("close_time") or "17:00"

        days = request.form.getlist("working_days")
        settings.working_days = ",".join(sorted(days, key=int)) if days else ""

        settings.admin_username = (request.form.get("admin_username") or "admin").strip()

        new_password = request.form.get("new_password") or ""
        if new_password:
            settings.admin_password_hash = generate_password_hash(new_password)
            flash("Password updated.", "success")

        db.session.commit()
        flash("Settings saved.", "success")
        return redirect(url_for("admin_settings"))
    return render_template("admin/settings.html", settings=settings)
# ---------------------------------------------------------------------------
# Google Search Console verification
# ---------------------------------------------------------------------------
@app.route("/google0f9a3b5ae4cf7087.html")
def google_verification():
    return send_from_directory(
        app.root_path,
        "google0f9a3b5ae4cf7087.html",
        mimetype="text/html",
    )


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------
@app.errorhandler(404)
def not_found(_):
    return render_template("404.html"), 404

# ---------------------------------------------------------------------------
# SEO: sitemap.xml + robots.txt
# ---------------------------------------------------------------------------
def _absolute(path: str) -> str:
    """Build a full URL, honouring reverse proxies (X-Forwarded-Proto/Host)."""
    return request.url_root.rstrip("/") + path


@app.route("/sitemap.xml")
def sitemap():
    """Generate a clean sitemap containing only important public pages."""
    today = datetime.now().strftime("%Y-%m-%d")

    pages = [
        ("/", "weekly", "1.0"),
        ("/about", "monthly", "0.7"),
        ("/contact", "monthly", "0.7"),
        ("/food", "monthly", "0.9"),
        ("/appointment", "monthly", "0.9"),
        ("/menu", "weekly", "0.8"),
        ("/appointments", "weekly", "0.8"),
        ("/terms", "yearly", "0.3"),
        ("/privacy", "yearly", "0.3"),
    ]

    # Corporate service pages
    for slug in CORPORATE_SERVICES:
        pages.append((
            f"/services/{slug}",
            "monthly",
            "0.8",
        ))

    # Active appointment services
    for service in Service.query.filter_by(active=True).order_by(Service.id).all():
        pages.append((
            f"/appointments/service/{service.id}",
            "monthly",
            "0.7",
        ))

    xml = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">',
    ]

    for path, changefreq, priority in pages:
        url = _absolute(path)

        xml.extend([
            "  <url>",
            f"    <loc>{url}</loc>",
            f"    <lastmod>{today}</lastmod>",
            f"    <changefreq>{changefreq}</changefreq>",
            f"    <priority>{priority}</priority>",
            "  </url>",
        ])

    xml.append("</urlset>")

    return Response(
        "\n".join(xml),
        mimetype="application/xml",
    )

@app.route("/robots.txt")
def robots():
    sitemap_url = _absolute("/sitemap.xml")
    lines = [
        "User-agent: *",
        "Allow: /",
        "Disallow: /admin/",
        "Disallow: /admin",
        "Disallow: /cart",
        "Disallow: /checkout",
        "Disallow: /order/",
        "Disallow: /appointments/confirmation/",
        "Disallow: /api/",
        "",
        f"Sitemap: {sitemap_url}",
    ]
   return Response(
    "\n".join(xml),
    status=200,
    content_type="text/xml; charset=utf-8",
)

# ---------------------------------------------------------------------------
# Seed data
# ---------------------------------------------------------------------------
def seed_data():
    get_settings()

    if MenuCategory.query.count() == 0:
        mains = MenuCategory(name="Mains", display_order=1)
        sides = MenuCategory(name="Sides", display_order=2)
        drinks = MenuCategory(name="Drinks", display_order=3)
        desserts = MenuCategory(name="Desserts", display_order=4)
        db.session.add_all([mains, sides, drinks, desserts])
        db.session.commit()

        db.session.add_all([
            MenuItem(category_id=mains.id, name="Grilled Chicken Plate",
                     description="Flame-grilled chicken with pap and chakalaka.",
                     price=95.0, featured=True, available=True),
            MenuItem(category_id=mains.id, name="Beef Stew & Rice",
                     description="Slow-cooked beef stew served with fragrant rice.",
                     price=110.0, featured=True, available=True),
            MenuItem(category_id=mains.id, name="Veg Curry Bowl",
                     description="Hearty vegetable curry with basmati rice.",
                     price=80.0, available=True),
            MenuItem(category_id=mains.id, name="Boerewors Roll",
                     description="Classic boerewors in a fresh roll with relish.",
                     price=55.0, available=True),
            MenuItem(category_id=sides.id, name="Pap & Chakalaka",
                     description="Traditional side dish.", price=25.0, available=True),
            MenuItem(category_id=sides.id, name="Garden Salad",
                     description="Crisp mixed greens.", price=35.0, available=True),
            MenuItem(category_id=sides.id, name="Chips",
                     description="Golden crispy chips.", price=30.0, available=True),
            MenuItem(category_id=drinks.id, name="Coca-Cola 440ml",
                     description="Ice-cold can.", price=15.0, available=True),
            MenuItem(category_id=drinks.id, name="Fresh Orange Juice",
                     description="Freshly squeezed.", price=28.0, available=True),
            MenuItem(category_id=drinks.id, name="Still Water 500ml",
                     description="Bottled water.", price=12.0, available=True),
            MenuItem(category_id=desserts.id, name="Malva Pudding",
                     description="Warm malva pudding with custard.",
                     price=45.0, featured=True, available=True),
            MenuItem(category_id=desserts.id, name="Ice Cream",
                     description="Two scoops of vanilla.", price=30.0, available=True),
        ])

    if Service.query.count() == 0:
        db.session.add_all([
            Service(name="General Consultation",
                    description="A 30-minute session with one of our specialists to discuss your needs.",
                    duration=30, price=0.0, active=True),
            Service(name="Business Advisory",
                    description="A 60-minute advisory session for growing businesses.",
                    duration=60, price=450.0, active=True),
            Service(name="Wellness Session",
                    description="A 45-minute wellness and lifestyle session.",
                    duration=45, price=300.0, active=True),
        ])

    db.session.commit()


with app.app_context():
    db.create_all()
    seed_data()


if __name__ == "__main__":
    app.run(debug=True, port=5000)
