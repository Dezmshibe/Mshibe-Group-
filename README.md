# Mshibe Group — Multi-Service Website

A complete Flask website for **Mshibe Group** offering two services:

1. **Food ordering** — browse the menu, add to cart, checkout for delivery or pickup
2. **Appointment booking** — browse services, choose a date & time, book instantly

Plus a professional homepage, about, contact, and a full admin panel.

## Features

### Public site
- Modern landing page highlighting both services
- Food menu with category filters, featured items, cart, and checkout
- Delivery or pickup option with configurable delivery fee
- Appointment service catalog with live available-slot lookup
- Confirmation pages with booking/order references and self-service cancel
- About and contact pages

### Admin
- Dashboard with KPIs (revenue, orders, upcoming appointments)
- Order management (status pipeline + delete)
- Appointment management (status filter, date filter, delete)
- Menu management (categories + items, featured/available toggles)
- Service management
- Full site settings (company info, hours, working days, delivery fee, currency, admin credentials)

## Quick start

```bash
python -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate
pip install -r requirements.txt
python app.py
Visit http://127.0.0.1:5000

The database mshibe.db is created automatically with sample menu items and services.

Admin access
URL	Purpose
/admin/login	Sign in
/admin	Dashboard
/admin/orders	Manage food orders
/admin/appointments	Manage bookings
/admin/menu	Manage menu & categories
/admin/services	Manage appointment services
/admin/settings	Company info, hours, password
Default credentials: admin / admin123 — change immediately in Settings.

Configuration
Environment variables:

bash
export SECRET_KEY="long-random-string"
export DATABASE_URL="sqlite:///mshibe.db"   # or postgresql://...
Production
bash
pip install gunicorn
gunicorn -w 4 -b 0.0.0.0:8000 "app:app"
