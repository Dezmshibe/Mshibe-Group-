"""Database models for Mshibe Group multi-service platform."""

import json
from datetime import datetime, timezone

from flask_sqlalchemy import SQLAlchemy

db = SQLAlchemy()


# ---------------------------------------------------------------------------
# Site configuration
# ---------------------------------------------------------------------------
class SiteSetting(db.Model):
    __tablename__ = "site_settings"

    id = db.Column(db.Integer, primary_key=True)
    company_name = db.Column(db.String(120), default="Mshibe Group")
    tagline = db.Column(db.String(200), default="Food. Appointments. Delivered.")
    about_text = db.Column(db.Text, default="")
    email = db.Column(db.String(120), default="MshibeGroup@gmail.com")
    phone = db.Column(db.String(40), default="")
    address = db.Column(db.String(200), default="Johannesburg, South Africa")

    delivery_fee = db.Column(db.Float, default=25.0)
    currency_symbol = db.Column(db.String(5), default="R")

    slot_duration = db.Column(db.Integer, default=30)
    open_time = db.Column(db.String(5), default="09:00")
    close_time = db.Column(db.String(5), default="17:00")
    working_days = db.Column(db.String(20), default="0,1,2,3,4")

    admin_username = db.Column(db.String(60), default="admin")
    admin_password_hash = db.Column(db.String(255))

    @property
    def working_days_list(self):
        out = []
        for p in (self.working_days or "").split(","):
            p = p.strip()
            if p.isdigit():
                out.append(int(p))
        return out

    def is_open_on(self, weekday: int) -> bool:
        return weekday in self.working_days_list


# ---------------------------------------------------------------------------
# Food ordering
# ---------------------------------------------------------------------------
class MenuCategory(db.Model):
    __tablename__ = "menu_categories"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(80), nullable=False)
    display_order = db.Column(db.Integer, default=0)

    items = db.relationship(
        "MenuItem", back_populates="category",
        cascade="all, delete-orphan",
    )


class MenuItem(db.Model):
    __tablename__ = "menu_items"

    id = db.Column(db.Integer, primary_key=True)
    category_id = db.Column(
        db.Integer, db.ForeignKey("menu_categories.id"), nullable=False
    )
    category = db.relationship("MenuCategory", back_populates="items")

    name = db.Column(db.String(120), nullable=False)
    description = db.Column(db.Text, default="")
    price = db.Column(db.Float, default=0.0)
    image_url = db.Column(db.String(300), default="")
    available = db.Column(db.Boolean, default=True, nullable=False)
    featured = db.Column(db.Boolean, default=False)


class Order(db.Model):
    __tablename__ = "orders"

    id = db.Column(db.Integer, primary_key=True)
    reference = db.Column(db.String(12), unique=True, index=True, nullable=False)

    customer_name = db.Column(db.String(120), nullable=False)
    customer_email = db.Column(db.String(120), nullable=False)
    customer_phone = db.Column(db.String(40), default="")
    delivery_address = db.Column(db.String(300), default="")
    notes = db.Column(db.Text, default="")

    order_type = db.Column(db.String(20), default="delivery")   # delivery | pickup
    subtotal = db.Column(db.Float, default=0.0)
    delivery_fee = db.Column(db.Float, default=0.0)
    total = db.Column(db.Float, default=0.0)

    status = db.Column(db.String(20), default="pending", nullable=False)
    # pending | preparing | ready | out_for_delivery | completed | cancelled

    created_at = db.Column(
        db.DateTime, default=lambda: datetime.now(timezone.utc)
    )

    items = db.relationship(
        "OrderItem", back_populates="order", cascade="all, delete-orphan"
    )

    STATUSES = (
        "pending", "preparing", "ready",
        "out_for_delivery", "completed", "cancelled",
    )


class OrderItem(db.Model):
    __tablename__ = "order_items"

    id = db.Column(db.Integer, primary_key=True)
    order_id = db.Column(db.Integer, db.ForeignKey("orders.id"), nullable=False)
    order = db.relationship("Order", back_populates="items")

    menu_item_id = db.Column(db.Integer, db.ForeignKey("menu_items.id"))
    name = db.Column(db.String(120), nullable=False)     # snapshot
    price = db.Column(db.Float, nullable=False)          # snapshot
    quantity = db.Column(db.Integer, default=1)

    @property
    def line_total(self):
        return self.price * self.quantity


# ---------------------------------------------------------------------------
# Appointment booking
# ---------------------------------------------------------------------------
class Service(db.Model):
    __tablename__ = "services"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    description = db.Column(db.Text, default="")
    duration = db.Column(db.Integer, default=30)
    price = db.Column(db.Float, default=0.0)
    icon = db.Column(db.String(20), default="calendar")
    active = db.Column(db.Boolean, default=True, nullable=False)

    appointments = db.relationship("Appointment", back_populates="service")


class Appointment(db.Model):
    __tablename__ = "appointments"

    id = db.Column(db.Integer, primary_key=True)
    reference = db.Column(db.String(12), unique=True, index=True, nullable=False)

    service_id = db.Column(db.Integer, db.ForeignKey("services.id"), nullable=False)
    service = db.relationship("Service", back_populates="appointments")

    client_name = db.Column(db.String(120), nullable=False)
    client_email = db.Column(db.String(120), nullable=False)
    client_phone = db.Column(db.String(40), default="")
    notes = db.Column(db.Text, default="")

    date = db.Column(db.String(10), index=True, nullable=False)
    time = db.Column(db.String(5), nullable=False)

    status = db.Column(db.String(20), default="pending", nullable=False)
    created_at = db.Column(
        db.DateTime, default=lambda: datetime.now(timezone.utc)
    )

    STATUSES = ("pending", "confirmed", "completed", "cancelled")


# ---------------------------------------------------------------------------
# Contact messages
# ---------------------------------------------------------------------------
class ContactMessage(db.Model):
    __tablename__ = "contact_messages"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    email = db.Column(db.String(120), nullable=False)
    category = db.Column(db.String(80), nullable=False)
    other_category = db.Column(db.String(160), default="")
    subject = db.Column(db.String(200), nullable=False)
    message = db.Column(db.Text, nullable=False)
    status = db.Column(db.String(20), default="new", nullable=False)
    created_at = db.Column(
        db.DateTime, default=lambda: datetime.now(timezone.utc)
    )

    STATUSES = ("new", "read")
    CATEGORIES = (
        "Chowza",
        "Appoint",
        "Mshibe Group",
        "Technical Support",
        "Business Partnership",
        "General Enquiry",
        "Other",
    )
