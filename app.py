"""Mshibe Group - multi-service platform.

Services offered:
  * Appointment booking

Default admin login -> admin / admin123

Database: Supabase Postgres in production, SQLite fallback locally.
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
    Appointment, ContactMessage,
    Service, SiteSetting, db,
)
from ai_receptionist import register as register_ai_receptionist
from security import (
    register_security, rate_limit,
    login_allowed, record_login_fail, clear_login_fails,
)

# ---------------------------------------------------------------------------
# App setup
# ---------------------------------------------------------------------------
app = Flask(__name__)
app.config["SECRET_KEY"] = os.environ.get(
    "SECRET_KEY", "mshibe-dev-secret-change-me"
)

# Database URL — Supabase Postgres in production, SQLite fallback locally.
# Render's free tier wipes its disk on every deploy, so SQLite loses data.
db_url = os.environ.get("DATABASE_URL", "sqlite:///mshibe.db")

# Supabase hands out postgres://... URLs. SQLAlchemy 2.x needs the driver
# specified explicitly, so pin it to psycopg2.
for _prefix in ("postgres://", "postgresql://"):
    if db_url.startswith(_prefix):
        db_url = "postgresql+psycopg2://" + db_url[len(_prefix):]

app.config["SQLALCHEMY_DATABASE_URI"] = db_url
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

# Connection pooling only matters for Postgres. These settings keep
# connections healthy behind Supabase's pooler.
if db_url.startswith("postgresql"):
    app.config["SQLALCHEMY_ENGINE_OPTIONS"] = {
        "pool_pre_ping": True,
        "pool_recycle": 280,
        "pool_size": 1,
        "max_overflow": 0,
    }

db.init_app(app)
register_security(app)


def _clean_text(text: str) -> str:
    """Strip UTF-8 BOM if present. Protects sitemap.xml, robots.txt."""
    if text and text[0] == "\ufeff":
        return text[1:]
    return text


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def get_settings() -> SiteSetting:
    s = SiteSetting.query.first()
    if s is None:
        s = SiteSetting(
            company_name="Mshibe Group",
            tagline="Professional appointment-based services — all in one place.",
            about_text=(
                "Mshibe Group is a multi-service company proudly serving our "
                "community. From professional appointment-based services to "
                "digital solutions, we make everyday life simpler."
            ),
        )
        s.admin_password_hash = generate_password_hash("admin123")
        db.session.add(s)
        db.session.commit()
    else:
        changed = False
        if s.email != "mshibegroup@gmail.com":
            s.email = "mshibegroup@gmail.com"
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
@app.route("/welcome")
def welcome():
    return render_template("welcome.html")


@app.route("/")
def index():
    # Check if the visitor has already seen the welcome page
    if not request.cookies.get("seen_welcome"):
        return redirect(url_for("welcome"))

    services = Service.query.filter_by(active=True).limit(3).all()
    return render_template(
        "index.html",
        services=services,
    )

@app.route("/welcome/skip")
def welcome_skip():
    # Set a cookie that lasts for 30 days, then send them to the homepage
    resp = redirect(url_for("index"))
    resp.set_cookie("seen_welcome", "1", max_age=60 * 60 * 24 * 30)
    return resp

@app.route("/demo")
def ai_demo():
    return send_from_directory(app.root_path, "voice.html")


@app.route("/about")
def about():
    return render_template("about.html")


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
    "business-solutions": {
        "number": "03",
        "title": "Business Solutions",
        "eyebrow": "PRACTICAL TOOLS & SERVICES",
        "icon": "lightbulb",
        "intro": "We create practical tools and services that help ambitious businesses operate better, serve customers and grow.",
        "body": "The goal is not complexity. We focus on solutions that solve a clear business problem, are easy to use and can be improved over time.",
        "capabilities": [
            ("Business Workflows", "Clear processes and tools for the work that keeps a business moving."),
            ("Customer Operations", "Systems that make enquiries, bookings and follow-ups easier to manage."),
            ("Digital Presence", "Web experiences that give customers a clear place to understand and engage with a business."),
            ("Growth Support", "Practical digital support for businesses ready to improve their next stage of growth."),
        ],
    },
}
PORTFOLIO = {
    "chowza": {
        "number": "01",
        "title": "Chowza",
        "category": "Commerce platform",
        "tagline": "A streamlined food ordering experience.",
        "hero_copy": "From menu to doorstep — a food ordering platform built for local businesses.",
        "year": "2025",
        "status": "Live",
        # url_endpoint removed because /food no longer exists
        "challenge": "Local food businesses were losing customers to complex ordering systems and slow checkout flows. We needed an experience that felt immediate and trustworthy from the first tap.",
        "approach": [
            "Designed a linear ordering flow: browse menu → add to cart → review → pay. No hidden steps, no confusion.",
            "Built a real-time cart that persists across pages, so customers can browse without losing their order.",
            "Added a clear confirmation page with the order reference, so customers know their order was received.",
        ],
        "results": [
            ("Category", "Food ordering"),
            ("Built in", "2025"),
            ("Status", "Live"),
        ],
        "stack": ["Flask", "Supabase", "Stripe-ready", "Mobile-first"],
    },
    "appoint": {
        "number": "02",
        "title": "Appoint",
        "category": "Service booking",
        "tagline": "Appointment booking made simple.",
        "hero_copy": "A booking platform that lets customers discover a service, choose a time, and confirm — all without leaving the page.",
        "year": "2025",
        "status": "Live",
        "url_endpoint": "appointment_platform", # This exists, so it's safe
        "challenge": "Appointment-based businesses were losing bookings to complicated calendars and back-and-forth calls. The booking process was killing conversions.",
        "approach": [
            "Replaced phone bookings with a live availability calendar that updates in real time.",
            "Built a single-page booking form — service, date, time, details — with clear validation at each step.",
            "Sent confirmation references so customers have proof of booking and can cancel if needed.",
        ],
        "results": [
            ("Category", "Service booking"),
            ("Built in", "2025"),
            ("Status", "Live"),
        ],
        "stack": ["Flask", "Supabase", "Real-time slots", "Mobile-first"],
    },
    "fixflow": {
        "number": "03",
        "title": "FixFlow",
        "category": "Property maintenance",
        "tagline": "One shared record for every repair.",
        "hero_copy": "A platform that lets landlords, tenants and contractors track repairs from first report to final invoice.",
        "year": "2026",
        "status": "Live",
        "url_endpoint": "contact", # Changed to contact since fixflow_platform isn't a route here
        "challenge": "Repairs get lost in WhatsApp threads and forwarded emails. Nobody knows the current status of a job. Contractors invoice weeks later through a different channel.",
        "approach": [
            "Built a nine-stage workflow that tracks every repair from Report → Closed.",
            "Created three separate dashboards — landlord, tenant, contractor — that all read from the same record.",
            "Added analytics showing spend by trade, property and month, so landlords can spot problems before they grow.",
        ],
        "results": [
            ("Category", "Property maintenance"),
            ("Built in", "2026"),
            ("Status", "Live"),
        ],
        "stack": ["Flask", "Supabase", "Chart.js", "Role-based access"],
    },
}
@app.route("/services/<slug>")
def corporate_service(slug):
    service = CORPORATE_SERVICES.get(slug)
    if not service:
        abort(404)
    return render_template("service_detail.html", service=service, slug=slug)


@app.route("/appoint")
def appoint():
    return redirect(url_for("appointment_platform"))


@app.route("/contact", methods=["GET", "POST"])
@rate_limit("contact", 10, 300)
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

@app.route("/portfolio")
def portfolio():
    return render_template("portfolio.html", projects=PORTFOLIO)


@app.route("/portfolio/<slug>")
def portfolio_item(slug):
    project = PORTFOLIO.get(slug)
    if not project:
        abort(404)
    return render_template("portfolio_item.html", project=project, slug=slug)

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
@rate_limit("book", 10, 300)
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

        from sqlalchemy.exc import IntegrityError

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
        try:
            db.session.commit()
        except IntegrityError:
            db.session.rollback()
            return fail("That time slot was just taken. Please pick another.")
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
@rate_limit("slots", 60, 60)
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
SECRET_ADMIN_PATH = "donda"

@app.route(f"/{SECRET_ADMIN_PATH}/login", methods=["GET", "POST"])
@rate_limit("login", 10, 300)
def admin_login():
    settings = get_settings()
    ip = (
        request.headers.get("X-Forwarded-For", "")
        or request.remote_addr
        or "unknown"
    ).split(",")[0].strip()

    if request.method == "POST":
        if not login_allowed(ip):
            flash("Too many failed attempts. Please wait a few minutes.", "error")
            return render_template("admin/login.html"), 429

        username = (request.form.get("username") or "").strip()
        password = request.form.get("password") or ""

        ok = (
            username == settings.admin_username
            and check_password_hash(settings.admin_password_hash or "", password)
        )
        if ok:
            clear_login_fails(ip)
            session.clear()
            session["admin_logged_in"] = True
            session.permanent = True
            flash("Welcome back!", "success")
            return redirect(request.args.get("next") or url_for("admin_dashboard"))

        record_login_fail(ip)
        flash("Invalid username or password.", "error")

    return render_template("admin/login.html")


@app.route(f"/{SECRET_ADMIN_PATH}/logout")
def admin_logout():
    session.pop("admin_logged_in", None)
    flash("Logged out.", "success")
    return redirect(url_for("index"))


# ---------------------------------------------------------------------------
# Admin dashboard
# ---------------------------------------------------------------------------
@app.route(f"/{SECRET_ADMIN_PATH}")
@admin_required
def admin_dashboard():
    today_str = date_cls.today().strftime("%Y-%m-%d")
    stats = {
        "appt_total": Appointment.query.count(),
        "appt_upcoming": Appointment.query.filter(
            Appointment.date >= today_str,
            Appointment.status.in_(("pending", "confirmed")),
        ).count(),
        "messages_new": ContactMessage.query.filter_by(status="new").count(),
    }
    upcoming_appts = Appointment.query.filter(
        Appointment.date >= today_str
    ).order_by(Appointment.date, Appointment.time).limit(5).all()
    return render_template(
        "admin/dashboard.html",
        stats=stats,
        upcoming_appts=upcoming_appts,
    )


# ---------------------------------------------------------------------------
# Admin: appointments
# ---------------------------------------------------------------------------
@app.route(f"/{SECRET_ADMIN_PATH}/appointments")
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


@app.route(f"/{SECRET_ADMIN_PATH}/appointments/<int:appt_id>/status", methods=["POST"])
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


@app.route(f"/{SECRET_ADMIN_PATH}/appointments/<int:appt_id>/delete", methods=["POST"])
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
# Admin: services
# ---------------------------------------------------------------------------
@app.route(f"/{SECRET_ADMIN_PATH}/services")
@admin_required
def admin_services():
    services = Service.query.order_by(Service.name).all()
    return render_template("admin/services.html", services=services)


@app.route(f"/{SECRET_ADMIN_PATH}/services/new", methods=["GET", "POST"])
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


@app.route(f"/{SECRET_ADMIN_PATH}/services/<int:service_id>/edit", methods=["GET", "POST"])
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


@app.route(f"/{SECRET_ADMIN_PATH}/services/<int:service_id>/delete", methods=["POST"])
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
@app.route(f"/{SECRET_ADMIN_PATH}/messages")
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


@app.route(f"/{SECRET_ADMIN_PATH}/messages/<int:message_id>/read", methods=["POST"])
@admin_required
def admin_message_read(message_id):
    contact_message = db.session.get(ContactMessage, message_id)
    if not contact_message:
        abort(404)
    contact_message.status = "read"
    db.session.commit()
    return redirect(request.referrer or url_for("admin_messages"))


@app.route(f"/{SECRET_ADMIN_PATH}/messages/<int:message_id>/delete", methods=["POST"])
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
@app.route(f"/{SECRET_ADMIN_PATH}/settings", methods=["GET", "POST"])
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
# Static file catch-all — MUST be last so it doesn't shadow other routes
# ---------------------------------------------------------------------------
ALLOWED_ROOT_EXTS = {
    ".html", ".js", ".css",
    ".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp", ".ico",
    ".mp4", ".webm", ".mp3", ".wav",
    ".woff", ".woff2", ".ttf", ".otf",
    ".txt", ".xml",
}


@app.route("/<path:filename>")
def serve_static_file(filename):
    """Serve root-level static files (voice.html, widget.js, etc.)."""
    if filename.startswith(("admin", "api", "static", "fixflow")):
        return render_template("404.html"), 404

    ext = os.path.splitext(filename)[1].lower()
    if ext not in ALLOWED_ROOT_EXTS:
        return render_template("404.html"), 404

    parts = filename.split("/")
    if any(p.startswith(".") or p == "__pycache__" for p in parts):
        return render_template("404.html"), 404

    full_path = os.path.abspath(os.path.join(app.root_path, filename))
    root = os.path.abspath(app.root_path)
    if not full_path.startswith(root + os.sep):
        return render_template("404.html"), 404

    if os.path.isfile(full_path):
        return send_from_directory(app.root_path, filename)

    return render_template("404.html"), 404


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
    root = request.url_root.rstrip("/")
    if root.startswith("http://") and "localhost" not in root and "127.0.0.1" not in root:
        root = "https://" + root[len("http://"):]
    return root + path

@app.route("/sitemap.xml")
def sitemap():
    """Generate a clean sitemap containing only important public pages."""
    today = datetime.utcnow().strftime("%Y-%m-%d")

    pages = [
        ("/",              "weekly",  "1.0"),
        ("/about",         "monthly", "0.7"),
        ("/contact",       "monthly", "0.7"),
        ("/appointment",   "monthly", "0.9"),
        ("/appointments",  "weekly",  "0.8"),
        ("/welcome",       "monthly", "0.5"),
        ("/terms",         "yearly",  "0.3"),
        ("/privacy",       "yearly",  "0.3"),
        
        # --- PORTFOLIO ADDED HERE ---
        ("/portfolio",     "monthly", "0.8"),
    ]

    # Corporate service pages
    for slug in CORPORATE_SERVICES:
        pages.append((f"/services/{slug}", "monthly", "0.8"))

    # --- INDIVIDUAL PORTFOLIO PAGES ADDED HERE ---
    for slug in PORTFOLIO:
        pages.append((f"/portfolio/{slug}", "monthly", "0.7"))

    # Active appointment services
    for service in Service.query.filter_by(active=True).order_by(Service.id).all():
        pages.append((f"/appointments/service/{service.id}", "monthly", "0.7"))

    xml_parts = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">',
    ]

    for path, changefreq, priority in pages:
        url = _absolute(path)
        xml_parts.append(
            "  <url>"
            f"<loc>{url}</loc>"
            f"<lastmod>{today}</lastmod>"
            f"<changefreq>{changefreq}</changefreq>"
            f"<priority>{priority}</priority>"
            "</url>"
        )

    xml_parts.append("</urlset>")
    xml_body = _clean_text("\n".join(xml_parts))

    response = Response(xml_body, status=200)
    response.headers["Content-Type"] = "application/xml; charset=utf-8"
    response.headers["Content-Length"] = str(len(xml_body.encode("utf-8")))
    response.headers["Cache-Control"] = "public, max-age=3600"
    return response

@app.route("/robots.txt")
def robots():
    """Serve robots.txt dynamically so the sitemap URL stays in sync."""
    sitemap_url = _absolute("/sitemap.xml")
    lines = [
        "User-agent: *",
        "Allow: /",
        "Disallow: /admin/",
        "Disallow: /admin",
        "Disallow: /appointments/confirmation/",
        "Disallow: /api/",
        "",
        f"Sitemap: {sitemap_url}",
    ]
    body = _clean_text("\n".join(lines))

    response = Response(body, status=200)
    response.headers["Content-Type"] = "text/plain; charset=utf-8"
    response.headers["Content-Length"] = str(len(body.encode("utf-8")))
    response.headers["Cache-Control"] = "public, max-age=3600"
    return response


# ---------------------------------------------------------------------------
# Seed data
# ---------------------------------------------------------------------------
def seed_data():
    get_settings()

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


# =============================================================================
# App startup
# =============================================================================
with app.app_context():
    db.create_all()
    seed_data()
    register_ai_receptionist(
        app,
        get_settings,
        Service,
        Appointment,
        db,
    )


if __name__ == "__main__":
    _port = int(os.environ.get("PORT", 5000))
    _debug = os.environ.get("FLASK_DEBUG", "0") == "1"
    app.run(host="0.0.0.0", debug=_debug, port=_port)