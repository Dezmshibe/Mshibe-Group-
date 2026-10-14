import os, csv, io, secrets
from datetime import datetime, timedelta
from functools import wraps

from flask import (
    Flask, render_template, request, redirect, url_for, session,
    flash, abort, send_from_directory, Response
)
from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
from sqlalchemy import func

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
UPLOAD_DIR = os.path.join(BASE_DIR, 'uploads')
os.makedirs(UPLOAD_DIR, exist_ok=True)

app = Flask(__name__)
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'fixflow-dev-secret-change-me')
app.config['SQLALCHEMY_DATABASE_URI'] = os.environ.get('DATABASE_URL', 'sqlite:///fixflow.db')
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['UPLOAD_FOLDER'] = UPLOAD_DIR
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024  # 16 MB

db = SQLAlchemy(app)

PRIORITIES = ['Low', 'Medium', 'High', 'Emergency']
STATUSES = ['Reported', 'Assigned', 'Quote Pending', 'Quote Approved',
            'In Progress', 'Completed', 'Invoiced', 'Paid', 'Closed']
TRADES = ['Plumbing', 'Electrical', 'HVAC', 'Appliance',
          'Structural', 'Pest Control', 'General']
ALLOWED_EXT = {'png', 'jpg', 'jpeg', 'gif', 'webp', 'pdf'}

CURRENCY = 'R'


# =========================================================
# Models
# =========================================================

class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(120), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    name = db.Column(db.String(120), nullable=False)
    phone = db.Column(db.String(30))
    role = db.Column(db.String(20), nullable=False)  # landlord | tenant | contractor
    company = db.Column(db.String(120))              # contractors
    trades = db.Column(db.String(255))               # contractors, comma-separated
    property_id = db.Column(db.Integer, db.ForeignKey('property.id'))
    unit_id = db.Column(db.Integer, db.ForeignKey('unit.id'))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    properties_owned = db.relationship(
        'Property', backref='landlord', lazy=True,
        foreign_keys='Property.landlord_id'
    )
    requests_made = db.relationship(
        'MaintenanceRequest', backref='tenant', lazy=True,
        foreign_keys='MaintenanceRequest.tenant_id'
    )
    jobs = db.relationship(
        'MaintenanceRequest', backref='contractor', lazy=True,
        foreign_keys='MaintenanceRequest.contractor_id'
    )


class Property(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    landlord_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    name = db.Column(db.String(120), nullable=False)
    address = db.Column(db.String(255), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    units = db.relationship('Unit', backref='property', lazy=True, cascade='all, delete-orphan')
    requests = db.relationship('MaintenanceRequest', backref='property', lazy=True)


class Unit(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    property_id = db.Column(db.Integer, db.ForeignKey('property.id'), nullable=False)
    label = db.Column(db.String(50), nullable=False)
    rent_amount = db.Column(db.Float)

    requests = db.relationship('MaintenanceRequest', backref='unit', lazy=True)


class MaintenanceRequest(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    tenant_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    property_id = db.Column(db.Integer, db.ForeignKey('property.id'), nullable=False)
    unit_id = db.Column(db.Integer, db.ForeignKey('unit.id'))

    title = db.Column(db.String(120), nullable=False)
    description = db.Column(db.Text, nullable=False)
    priority = db.Column(db.String(20), default='Medium')
    trade_category = db.Column(db.String(50), default='General')
    status = db.Column(db.String(30), default='Reported')

    contractor_id = db.Column(db.Integer, db.ForeignKey('user.id'))

    quote_amount = db.Column(db.Float)
    quote_approved = db.Column(db.Boolean, default=False)
    invoice_amount = db.Column(db.Float)
    invoice_paid = db.Column(db.Boolean, default=False)

    rating = db.Column(db.Integer)  # 1-5
    rating_comment = db.Column(db.Text)

    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    resolved_at = db.Column(db.DateTime)

    comments = db.relationship('RequestComment', backref='request', lazy=True,
                               cascade='all, delete-orphan', order_by='RequestComment.created_at')
    history = db.relationship('StatusHistory', backref='request', lazy=True,
                              cascade='all, delete-orphan', order_by='StatusHistory.created_at')
    attachments = db.relationship('Attachment', backref='request', lazy=True,
                                  cascade='all, delete-orphan', order_by='Attachment.created_at')


class RequestComment(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    request_id = db.Column(db.Integer, db.ForeignKey('maintenance_request.id'), nullable=False)
    author_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    body = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    author = db.relationship('User', foreign_keys=[author_id])


class StatusHistory(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    request_id = db.Column(db.Integer, db.ForeignKey('maintenance_request.id'), nullable=False)
    from_status = db.Column(db.String(30))
    to_status = db.Column(db.String(30), nullable=False)
    changed_by_id = db.Column(db.Integer, db.ForeignKey('user.id'))
    note = db.Column(db.String(255))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    changed_by = db.relationship('User', foreign_keys=[changed_by_id])


class Attachment(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    request_id = db.Column(db.Integer, db.ForeignKey('maintenance_request.id'), nullable=False)
    uploader_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    filename = db.Column(db.String(255), nullable=False)      # original
    stored_name = db.Column(db.String(255), nullable=False)   # on disk
    kind = db.Column(db.String(30), default='photo')          # photo | quote | invoice | other
    size = db.Column(db.Integer)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    uploader = db.relationship('User', foreign_keys=[uploader_id])


class Notification(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    message = db.Column(db.String(255), nullable=False)
    link = db.Column(db.String(255))
    read = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


# =========================================================
# Helpers
# =========================================================

def login_required(role=None):
    def decorator(f):
        @wraps(f)
        def wrapper(*args, **kwargs):
            if 'user_id' not in session:
                flash('Please log in.', 'error')
                return redirect(url_for('login'))
            user = User.query.get(session['user_id'])
            if not user:
                session.clear()
                return redirect(url_for('login'))
            if role and user.role != role:
                flash('Access denied.', 'error')
                return redirect(url_for('dashboard'))
            return f(*args, **kwargs)
        return wrapper
    return decorator


def current_user():
    if 'user_id' in session:
        return User.query.get(session['user_id'])
    return None


def notify(user_id, message, link=None):
    if not user_id:
        return
    db.session.add(Notification(user_id=user_id, message=message, link=link))


def log_status(req, from_status, to_status, user_id, note=None):
    if from_status == to_status:
        return
    db.session.add(StatusHistory(
        request_id=req.id, from_status=from_status,
        to_status=to_status, changed_by_id=user_id, note=note
    ))


def can_view_request(user, req):
    if not user:
        return False
    if user.role == 'landlord' and req.property.landlord_id == user.id:
        return True
    if user.role == 'tenant' and req.tenant_id == user.id:
        return True
    if user.role == 'contractor' and req.contractor_id == user.id:
        return True
    return False


def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXT


@app.context_processor
def inject_globals():
    user = current_user()
    unread = 0
    if user:
        unread = Notification.query.filter_by(user_id=user.id, read=False).count()
    return dict(
        current_user=user,
        unread_count=unread,
        CURRENCY=CURRENCY,
        PRIORITIES=PRIORITIES,
        STATUSES=STATUSES,
        TRADES=TRADES,
        now=datetime.utcnow(),
    )


@app.template_filter('money')
def money(v):
    if v is None:
        return 'â€”'
    return f'R{v:,.2f}'


@app.template_filter('dt')
def dt(v):
    if not v:
        return 'â€”'
    return v.strftime('%d %b %Y, %H:%M')


# =========================================================
# Auth
# =========================================================

@app.route('/')
def index():
    return render_template('index.html')


@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        email = request.form['email'].strip().lower()
        name = request.form['name'].strip()
        password = request.form['password']
        role = request.form['role']
        if role not in ('landlord', 'tenant', 'contractor'):
            flash('Invalid role.', 'error')
            return redirect(url_for('register'))
        if User.query.filter_by(email=email).first():
            flash('Email already registered.', 'error')
            return redirect(url_for('register'))
        u = User(
            email=email, name=name, role=role,
            phone=request.form.get('phone', '').strip() or None,
            company=request.form.get('company', '').strip() or None,
            trades=request.form.get('trades', '').strip() or None,
            password_hash=generate_password_hash(password),
        )
        db.session.add(u)
        db.session.commit()
        flash('Registration successful. Please log in.', 'success')
        return redirect(url_for('login'))
    return render_template('register.html')


@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        email = request.form['email'].strip().lower()
        password = request.form['password']
        user = User.query.filter_by(email=email).first()
        if user and check_password_hash(user.password_hash, password):
            session['user_id'] = user.id
            flash('Logged in.', 'success')
            return redirect(url_for('dashboard'))
        flash('Invalid email or password.', 'error')
    return render_template('login.html')


@app.route('/logout')
def logout():
    session.clear()
    flash('Logged out.', 'success')
    return redirect(url_for('index'))


@app.route('/dashboard')
@login_required()
def dashboard():
    u = current_user()
    if u.role == 'landlord':
        return redirect(url_for('landlord_dashboard'))
    if u.role == 'tenant':
        return redirect(url_for('tenant_dashboard'))
    if u.role == 'contractor':
        return redirect(url_for('contractor_dashboard'))
    return redirect(url_for('index'))


@app.route('/profile', methods=['GET', 'POST'])
@login_required()
def profile():
    u = current_user()
    if request.method == 'POST':
        action = request.form.get('action')
        if action == 'profile':
            u.name = request.form['name'].strip()
            u.phone = request.form.get('phone', '').strip() or None
            if u.role == 'contractor':
                u.company = request.form.get('company', '').strip() or None
                u.trades = request.form.get('trades', '').strip() or None
            db.session.commit()
            flash('Profile updated.', 'success')
        elif action == 'password':
            current = request.form['current_password']
            new = request.form['new_password']
            if not check_password_hash(u.password_hash, current):
                flash('Current password incorrect.', 'error')
            elif len(new) < 6:
                flash('New password must be at least 6 characters.', 'error')
            else:
                u.password_hash = generate_password_hash(new)
                db.session.commit()
                flash('Password changed.', 'success')
        return redirect(url_for('profile'))
    return render_template('profile.html')


# =========================================================
# Landlord
# =========================================================

@app.route('/landlord')
@login_required(role='landlord')
def landlord_dashboard():
    u = current_user()
    properties = Property.query.filter_by(landlord_id=u.id).order_by(Property.name).all()
    property_ids = [p.id for p in properties]

    q = request.args.get('q', '').strip()
    f_status = request.args.get('status', '').strip()
    f_priority = request.args.get('priority', '').strip()
    f_property = request.args.get('property_id', '').strip()

    query = MaintenanceRequest.query
    if property_ids:
        query = query.filter(MaintenanceRequest.property_id.in_(property_ids))
    else:
        query = query.filter(db.false())
    if q:
        like = f'%{q}%'
        query = query.filter(db.or_(
            MaintenanceRequest.title.ilike(like),
            MaintenanceRequest.description.ilike(like)
        ))
    if f_status:
        query = query.filter(MaintenanceRequest.status == f_status)
    if f_priority:
        query = query.filter(MaintenanceRequest.priority == f_priority)
    if f_property and f_property.isdigit():
        query = query.filter(MaintenanceRequest.property_id == int(f_property))

    requests_list = query.order_by(MaintenanceRequest.created_at.desc()).all()
    contractors = User.query.filter_by(role='contractor').order_by(User.name).all()

    total_invoiced = db.session.query(
        func.coalesce(func.sum(MaintenanceRequest.invoice_amount), 0.0)
    ).filter(MaintenanceRequest.property_id.in_(property_ids or [-1])).scalar() or 0.0

    total_paid = db.session.query(
        func.coalesce(func.sum(MaintenanceRequest.invoice_amount), 0.0)
    ).filter(
        MaintenanceRequest.property_id.in_(property_ids or [-1]),
        MaintenanceRequest.invoice_paid.is_(True)
    ).scalar() or 0.0

    outstanding = total_invoiced - total_paid
    open_count = MaintenanceRequest.query.filter(
        MaintenanceRequest.property_id.in_(property_ids or [-1]),
        MaintenanceRequest.status.notin_(['Paid', 'Closed'])
    ).count()

    return render_template(
        'landlord.html',
        properties=properties,
        requests=requests_list,
        contractors=contractors,
        total_invoiced=total_invoiced,
        total_paid=total_paid,
        outstanding=outstanding,
        open_count=open_count,
        q=q, f_status=f_status, f_priority=f_priority, f_property=f_property,
    )


@app.route('/landlord/property/add', methods=['POST'])
@login_required(role='landlord')
def add_property():
    u = current_user()
    db.session.add(Property(
        landlord_id=u.id,
        name=request.form['name'].strip(),
        address=request.form['address'].strip(),
    ))
    db.session.commit()
    flash('Property added.', 'success')
    return redirect(url_for('landlord_dashboard'))


@app.route('/landlord/unit/add', methods=['POST'])
@login_required(role='landlord')
def add_unit():
    u = current_user()
    prop = Property.query.filter_by(id=int(request.form['property_id']), landlord_id=u.id).first_or_404()
    rent = request.form.get('rent_amount')
    db.session.add(Unit(
        property_id=prop.id,
        label=request.form['label'].strip(),
        rent_amount=float(rent) if rent else None,
    ))
    db.session.commit()
    flash('Unit added.', 'success')
    return redirect(url_for('landlord_dashboard'))


@app.route('/landlord/property/<int:pid>')
@login_required(role='landlord')
def property_detail(pid):
    u = current_user()
    prop = Property.query.filter_by(id=pid, landlord_id=u.id).first_or_404()
    return render_template('property_detail.html', prop=prop)


@app.route('/landlord/cleanup_duplicates', methods=['POST'])
@login_required(role='landlord')
def cleanup_duplicates():
    u = current_user()
    seen = {}
    for prop in Property.query.filter_by(landlord_id=u.id).all():
        key = (prop.name.strip().lower(), prop.address.strip().lower())
        if key in seen:
            master = seen[key]
            for unit in prop.units:
                unit.property_id = master.id
            for req in prop.requests:
                req.property_id = master.id
            db.session.delete(prop)
        else:
            seen[key] = prop
    db.session.commit()
    flash('Duplicate properties merged.', 'success')
    return redirect(url_for('landlord_dashboard'))


@app.route('/landlord/export.csv')
@login_required(role='landlord')
def export_csv():
    u = current_user()
    pids = [p.id for p in Property.query.filter_by(landlord_id=u.id).all()]
    reqs = MaintenanceRequest.query.filter(
        MaintenanceRequest.property_id.in_(pids or [-1])
    ).order_by(MaintenanceRequest.created_at.desc()).all()

    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow([
        'ID', 'Created', 'Property', 'Unit', 'Tenant', 'Title',
        'Priority', 'Trade', 'Status', 'Contractor',
        'Quote', 'Quote Approved', 'Invoice', 'Paid', 'Rating'
    ])
    for r in reqs:
        w.writerow([
            r.id, r.created_at.strftime('%Y-%m-%d'),
            r.property.name, r.unit.label if r.unit else '',
            r.tenant.name, r.title, r.priority, r.trade_category,
            r.status, r.contractor.name if r.contractor else '',
            f'{r.quote_amount:.2f}' if r.quote_amount else '',
            'yes' if r.quote_approved else 'no',
            f'{r.invoice_amount:.2f}' if r.invoice_amount else '',
            'yes' if r.invoice_paid else 'no',
            r.rating or '',
        ])
    return Response(
        buf.getvalue(),
        mimetype='text/csv',
        headers={'Content-Disposition': 'attachment; filename=fixflow-export.csv'}
    )


@app.route('/landlord/analytics')
@login_required(role='landlord')
def analytics():
    u = current_user()
    pids = [p.id for p in Property.query.filter_by(landlord_id=u.id).all()] or [-1]

    total_invoiced = db.session.query(
        func.coalesce(func.sum(MaintenanceRequest.invoice_amount), 0.0)
    ).filter(MaintenanceRequest.property_id.in_(pids)).scalar() or 0.0

    total_paid = db.session.query(
        func.coalesce(func.sum(MaintenanceRequest.invoice_amount), 0.0)
    ).filter(
        MaintenanceRequest.property_id.in_(pids),
        MaintenanceRequest.invoice_paid.is_(True)
    ).scalar() or 0.0

    outstanding = total_invoiced - total_paid

    total_jobs = MaintenanceRequest.query.filter(
        MaintenanceRequest.property_id.in_(pids)
    ).count()

    open_jobs = MaintenanceRequest.query.filter(
        MaintenanceRequest.property_id.in_(pids),
        MaintenanceRequest.status.notin_(['Paid', 'Closed'])
    ).count()

    by_trade = db.session.query(
        MaintenanceRequest.trade_category,
        func.coalesce(func.sum(MaintenanceRequest.invoice_amount), 0.0)
    ).filter(MaintenanceRequest.property_id.in_(pids)).group_by(
        MaintenanceRequest.trade_category
    ).all()

    by_prop = db.session.query(
        Property.name,
        func.coalesce(func.sum(MaintenanceRequest.invoice_amount), 0.0)
    ).join(MaintenanceRequest, MaintenanceRequest.property_id == Property.id).filter(
        Property.id.in_(pids)
    ).group_by(Property.name).all()

    twelve_ago = datetime.utcnow() - timedelta(days=365)

    invoiced_rows = db.session.query(
        func.strftime('%Y-%m', MaintenanceRequest.created_at),
        func.coalesce(func.sum(MaintenanceRequest.invoice_amount), 0.0)
    ).filter(
        MaintenanceRequest.property_id.in_(pids),
        MaintenanceRequest.created_at >= twelve_ago
    ).group_by(func.strftime('%Y-%m', MaintenanceRequest.created_at)).all()

    paid_rows = db.session.query(
        func.strftime('%Y-%m', MaintenanceRequest.created_at),
        func.coalesce(func.sum(MaintenanceRequest.invoice_amount), 0.0)
    ).filter(
        MaintenanceRequest.property_id.in_(pids),
        MaintenanceRequest.invoice_paid.is_(True),
        MaintenanceRequest.created_at >= twelve_ago
    ).group_by(func.strftime('%Y-%m', MaintenanceRequest.created_at)).all()

    invoiced_map = {m: float(v) for m, v in invoiced_rows}
    paid_map = {m: float(v) for m, v in paid_rows}

    month_labels = []
    month_invoiced = []
    month_paid = []
    today = datetime.utcnow().replace(day=1)
    for i in range(11, -1, -1):
        y = today.year
        m = today.month - i
        while m <= 0:
            m += 12
            y -= 1
        key = f'{y:04d}-{m:02d}'
        month_labels.append(datetime(y, m, 1).strftime('%b %Y'))
        month_invoiced.append(invoiced_map.get(key, 0.0))
        month_paid.append(paid_map.get(key, 0.0))

    by_status = db.session.query(
        MaintenanceRequest.status,
        func.count(MaintenanceRequest.id)
    ).filter(MaintenanceRequest.property_id.in_(pids)).group_by(
        MaintenanceRequest.status
    ).all()

    by_priority = db.session.query(
        MaintenanceRequest.priority,
        func.count(MaintenanceRequest.id)
    ).filter(MaintenanceRequest.property_id.in_(pids)).group_by(
        MaintenanceRequest.priority
    ).all()

    top_contractors = db.session.query(
        User.name,
        func.count(MaintenanceRequest.id),
        func.coalesce(func.sum(MaintenanceRequest.invoice_amount), 0.0)
    ).join(MaintenanceRequest, MaintenanceRequest.contractor_id == User.id).filter(
        MaintenanceRequest.property_id.in_(pids)
    ).group_by(User.name).order_by(func.count(MaintenanceRequest.id).desc()).limit(8).all()

    resolved = MaintenanceRequest.query.filter(
        MaintenanceRequest.property_id.in_(pids),
        MaintenanceRequest.resolved_at.isnot(None)
    ).all()
    if resolved:
        avg_days = sum((r.resolved_at - r.created_at).days for r in resolved) / len(resolved)
    else:
        avg_days = None

    rated = MaintenanceRequest.query.filter(
        MaintenanceRequest.property_id.in_(pids),
        MaintenanceRequest.rating.isnot(None)
    ).all()
    avg_rating = sum(r.rating for r in rated) / len(rated) if rated else None

    invoiced_jobs = MaintenanceRequest.query.filter(
        MaintenanceRequest.property_id.in_(pids),
        MaintenanceRequest.invoice_amount.isnot(None)
    ).all()
    avg_cost = (sum(r.invoice_amount for r in invoiced_jobs) / len(invoiced_jobs)) if invoiced_jobs else 0.0

    return render_template(
        'analytics.html',
        total_invoiced=total_invoiced,
        total_paid=total_paid,
        outstanding=outstanding,
        total_jobs=total_jobs,
        open_jobs=open_jobs,
        avg_days=avg_days,
        avg_rating=avg_rating,
        avg_cost=avg_cost,
        by_trade=[(t, float(v)) for t, v in by_trade],
        by_prop=[(n, float(v)) for n, v in by_prop],
        by_status=[(s, int(c)) for s, c in by_status],
        by_priority=[(p, int(c)) for p, c in by_priority],
        top_contractors=[(n, int(c), float(v)) for n, c, v in top_contractors],
        month_labels=month_labels,
        month_invoiced=month_invoiced,
        month_paid=month_paid,
    )

# =========================================================
# Request actions
# =========================================================

@app.route('/request/<int:rid>')
@login_required()
def request_detail(rid):
    u = current_user()
    req = MaintenanceRequest.query.get_or_404(rid)
    if not can_view_request(u, req):
        abort(403)
    contractors = []
    if u.role == 'landlord':
        contractors = User.query.filter_by(role='contractor').order_by(User.name).all()
    return render_template('request_detail.html', req=req, contractors=contractors)


@app.route('/request/<int:rid>/comment', methods=['POST'])
@login_required()
def add_comment(rid):
    u = current_user()
    req = MaintenanceRequest.query.get_or_404(rid)
    if not can_view_request(u, req):
        abort(403)
    body = request.form['body'].strip()
    if body:
        db.session.add(RequestComment(request_id=req.id, author_id=u.id, body=body))
        # Notify other parties
        recipients = {req.tenant_id}
        if req.contractor_id:
            recipients.add(req.contractor_id)
        recipients.add(req.property.landlord_id)
        recipients.discard(u.id)
        for uid in recipients:
            notify(uid, f'New comment on request #{req.id}', url_for('request_detail', rid=req.id))
        db.session.commit()
        flash('Comment added.', 'success')
    return redirect(url_for('request_detail', rid=req.id))


@app.route('/request/<int:rid>/upload', methods=['POST'])
@login_required()
def upload_attachment(rid):
    u = current_user()
    req = MaintenanceRequest.query.get_or_404(rid)
    if not can_view_request(u, req):
        abort(403)
    file = request.files.get('file')
    if not file or not file.filename:
        flash('No file selected.', 'error')
        return redirect(url_for('request_detail', rid=req.id))
    if not allowed_file(file.filename):
        flash('File type not allowed.', 'error')
        return redirect(url_for('request_detail', rid=req.id))
    original = file.filename
    ext = original.rsplit('.', 1)[1].lower()
    stored = f'{secrets.token_hex(12)}.{ext}'
    path = os.path.join(app.config['UPLOAD_FOLDER'], stored)
    file.save(path)
    kind = request.form.get('kind', 'photo')
    db.session.add(Attachment(
        request_id=req.id, uploader_id=u.id,
        filename=original, stored_name=stored,
        kind=kind, size=os.path.getsize(path),
    ))
    db.session.commit()
    flash('File uploaded.', 'success')
    return redirect(url_for('request_detail', rid=req.id))


@app.route('/uploads/<path:name>')
@login_required()
def download_upload(name):
    att = Attachment.query.filter_by(stored_name=name).first_or_404()
    u = current_user()
    if not can_view_request(u, att.request):
        abort(403)
    return send_from_directory(app.config['UPLOAD_FOLDER'], name, as_attachment=False,
                               download_name=att.filename)


@app.route('/request/<int:rid>/rate', methods=['POST'])
@login_required(role='tenant')
def rate_request(rid):
    u = current_user()
    req = MaintenanceRequest.query.get_or_404(rid)
    if req.tenant_id != u.id:
        abort(403)
    if req.status not in ('Paid', 'Closed'):
        flash('You can only rate a completed and paid job.', 'error')
        return redirect(url_for('request_detail', rid=req.id))
    rating = int(request.form['rating'])
    if rating not in (1, 2, 3, 4, 5):
        flash('Rating must be between 1 and 5.', 'error')
        return redirect(url_for('request_detail', rid=req.id))
    req.rating = rating
    req.rating_comment = request.form.get('rating_comment', '').strip() or None
    if req.contractor_id:
        notify(req.contractor_id, f'You received a {rating}-star rating on request #{req.id}',
               url_for('request_detail', rid=req.id))
    db.session.commit()
    flash('Thanks for the rating.', 'success')
    return redirect(url_for('request_detail', rid=req.id))


# =========================================================
# Tenant
# =========================================================

@app.route('/tenant')
@login_required(role='tenant')
def tenant_dashboard():
    u = current_user()
    prop = Property.query.get(u.property_id) if u.property_id else None
    unit = Unit.query.get(u.unit_id) if u.unit_id else None
    reqs = MaintenanceRequest.query.filter_by(tenant_id=u.id).order_by(
        MaintenanceRequest.created_at.desc()
    ).all()
    return render_template('tenant.html', prop=prop, unit=unit, requests=reqs)


@app.route('/tenant/request/new', methods=['POST'])
@login_required(role='tenant')
def new_request():
    u = current_user()
    if not u.property_id:
        flash('You are not assigned to a property yet.', 'error')
        return redirect(url_for('tenant_dashboard'))
    req = MaintenanceRequest(
        tenant_id=u.id, property_id=u.property_id, unit_id=u.unit_id,
        title=request.form['title'].strip(),
        description=request.form['description'].strip(),
        priority=request.form['priority'],
        trade_category=request.form['trade_category'],
        status='Reported',
    )
    db.session.add(req)
    db.session.commit()
    db.session.add(StatusHistory(request_id=req.id, to_status='Reported',
                                 changed_by_id=u.id, note='Request submitted'))
    notify(req.property.landlord_id, f'New request #{req.id}: {req.title}',
           url_for('request_detail', rid=req.id))
    db.session.commit()
    flash('Request submitted.', 'success')
    return redirect(url_for('request_detail', rid=req.id))


# =========================================================
# Contractor
# =========================================================

@app.route('/contractor')
@login_required(role='contractor')
def contractor_dashboard():
    u = current_user()
    jobs = MaintenanceRequest.query.filter_by(contractor_id=u.id).order_by(
        MaintenanceRequest.created_at.desc()
    ).all()
    return render_template('contractor.html', jobs=jobs)


# =========================================================
# Landlord / contractor request actions
# =========================================================

@app.route('/request/<int:rid>/assign', methods=['POST'])
@login_required(role='landlord')
def assign_contractor(rid):
    u = current_user()
    req = MaintenanceRequest.query.get_or_404(rid)
    if req.property.landlord_id != u.id:
        abort(403)
    cid = request.form.get('contractor_id')
    old = req.status
    if cid:
        req.contractor_id = int(cid)
        if req.status == 'Reported':
            req.status = 'Assigned'
        notify(req.contractor_id, f'You have been assigned job #{req.id}: {req.title}',
               url_for('request_detail', rid=req.id))
    else:
        req.contractor_id = None
    log_status(req, old, req.status, u.id, 'Contractor assigned')
    db.session.commit()
    flash('Contractor updated.', 'success')
    return redirect(request.referrer or url_for('landlord_dashboard'))


@app.route('/request/<int:rid>/approve_quote', methods=['POST'])
@login_required(role='landlord')
def approve_quote(rid):
    u = current_user()
    req = MaintenanceRequest.query.get_or_404(rid)
    if req.property.landlord_id != u.id:
        abort(403)
    old = req.status
    req.quote_approved = True
    if req.status == 'Quote Pending':
        req.status = 'Quote Approved'
    log_status(req, old, req.status, u.id, 'Quote approved')
    if req.contractor_id:
        notify(req.contractor_id, f'Quote approved for job #{req.id}',
               url_for('request_detail', rid=req.id))
    db.session.commit()
    flash('Quote approved.', 'success')
    return redirect(request.referrer or url_for('landlord_dashboard'))


@app.route('/request/<int:rid>/status', methods=['POST'])
@login_required(role='landlord')
def update_request_status(rid):
    u = current_user()
    req = MaintenanceRequest.query.get_or_404(rid)
    if req.property.landlord_id != u.id:
        abort(403)
    new = request.form['status']
    if new not in STATUSES:
        abort(400)
    old = req.status
    req.status = new
    if new in ('Paid', 'Closed') and not req.resolved_at:
        req.resolved_at = datetime.utcnow()
    log_status(req, old, new, u.id, 'Status updated by landlord')
    notify(req.tenant_id, f'Request #{req.id} is now {new}', url_for('request_detail', rid=req.id))
    if req.contractor_id and req.contractor_id != u.id:
        notify(req.contractor_id, f'Request #{req.id} is now {new}', url_for('request_detail', rid=req.id))
    db.session.commit()
    flash('Status updated.', 'success')
    return redirect(request.referrer or url_for('landlord_dashboard'))


@app.route('/request/<int:rid>/mark_paid', methods=['POST'])
@login_required(role='landlord')
def mark_paid(rid):
    u = current_user()
    req = MaintenanceRequest.query.get_or_404(rid)
    if req.property.landlord_id != u.id:
        abort(403)
    old = req.status
    req.invoice_paid = True
    req.status = 'Paid'
    if not req.resolved_at:
        req.resolved_at = datetime.utcnow()
    log_status(req, old, 'Paid', u.id, 'Invoice marked paid')
    if req.contractor_id:
        notify(req.contractor_id, f'Invoice for job #{req.id} marked paid',
               url_for('request_detail', rid=req.id))
    db.session.commit()
    flash('Invoice marked paid.', 'success')
    return redirect(request.referrer or url_for('landlord_dashboard'))


@app.route('/contractor/job/<int:rid>/update', methods=['POST'])
@login_required(role='contractor')
def contractor_update_job(rid):
    u = current_user()
    req = MaintenanceRequest.query.get_or_404(rid)
    if req.contractor_id != u.id:
        abort(403)
    old = req.status
    if request.form.get('status') in STATUSES:
        req.status = request.form['status']
    q = request.form.get('quote_amount')
    if q:
        req.quote_amount = float(q)
        if req.status == 'Assigned':
            req.status = 'Quote Pending'
    i = request.form.get('invoice_amount')
    if i:
        req.invoice_amount = float(i)
        if req.status == 'Completed':
            req.status = 'Invoiced'
    if req.status in ('Paid', 'Closed') and not req.resolved_at:
        req.resolved_at = datetime.utcnow()
    log_status(req, old, req.status, u.id, 'Contractor updated job')
    if old != req.status:
        notify(req.property.landlord_id, f'Job #{req.id} is now {req.status}',
               url_for('request_detail', rid=req.id))
        notify(req.tenant_id, f'Job #{req.id} is now {req.status}',
               url_for('request_detail', rid=req.id))
    db.session.commit()
    flash('Job updated.', 'success')
    return redirect(request.referrer or url_for('contractor_dashboard'))


# =========================================================
# Notifications
# =========================================================

@app.route('/notifications')
@login_required()
def notifications():
    u = current_user()
    items = Notification.query.filter_by(user_id=u.id).order_by(
        Notification.created_at.desc()
    ).limit(200).all()
    return render_template('notifications.html', items=items)


@app.route('/notifications/read/<int:nid>', methods=['POST'])
@login_required()
def mark_notification_read(nid):
    u = current_user()
    n = Notification.query.filter_by(id=nid, user_id=u.id).first_or_404()
    n.read = True
    db.session.commit()
    return redirect(n.link or url_for('notifications'))


@app.route('/notifications/read_all', methods=['POST'])
@login_required()
def mark_all_read():
    u = current_user()
    Notification.query.filter_by(user_id=u.id, read=False).update({'read': True})
    db.session.commit()
    flash('All notifications marked read.', 'success')
    return redirect(url_for('notifications'))


# =========================================================
# Demo
# =========================================================

@app.route('/demo')
def demo():
    if User.query.count() > 0:
        flash('Demo data already exists. Please log in.', 'info')
        return redirect(url_for('login'))
    flash('Run "python seed_demo.py" to create the demo database.', 'info')
    return redirect(url_for('index'))

# =========================================================
# Landlord: tenant & contractor management
# =========================================================

@app.route('/landlord/tenants')
@login_required(role='landlord')
def landlord_tenants():
    u = current_user()
    pids = [p.id for p in Property.query.filter_by(landlord_id=u.id).all()] or [-1]
    units = Unit.query.filter(Unit.property_id.in_(pids)).order_by(Unit.property_id, Unit.label).all()
    tenants = User.query.filter_by(role='tenant').order_by(User.name).all()

    # occupancy map: unit_id -> tenant
    occupancy = {t.unit_id: t for t in tenants if t.unit_id}

    return render_template('landlord_tenants.html',
                           units=units, tenants=tenants, occupancy=occupancy)


@app.route('/landlord/tenant/<int:tid>/assign', methods=['POST'])
@login_required(role='landlord')
def assign_tenant(tid):
    u = current_user()
    tenant = User.query.filter_by(id=tid, role='tenant').first_or_404()
    unit_id = request.form.get('unit_id')
    if not unit_id:
        flash('Select a unit.', 'error')
        return redirect(url_for('landlord_tenants'))
    unit = Unit.query.get_or_404(int(unit_id))
    if unit.property.landlord_id != u.id:
        abort(403)
    existing = User.query.filter_by(unit_id=unit.id, role='tenant').first()
    if existing and existing.id != tenant.id:
        flash(f'Unit is already occupied by {existing.name}.', 'error')
        return redirect(url_for('landlord_tenants'))
    tenant.property_id = unit.property_id
    tenant.unit_id = unit.id
    notify(tenant.id,
           f'You have been assigned to {unit.property.name} / {unit.label}',
           url_for('tenant_dashboard'))
    db.session.commit()
    flash(f'{tenant.name} assigned to {unit.property.name} / {unit.label}.', 'success')
    return redirect(url_for('landlord_tenants'))


@app.route('/landlord/tenant/<int:tid>/unassign', methods=['POST'])
@login_required(role='landlord')
def unassign_tenant(tid):
    u = current_user()
    tenant = User.query.filter_by(id=tid, role='tenant').first_or_404()
    if tenant.property_id:
        prop = Property.query.get(tenant.property_id)
        if prop and prop.landlord_id != u.id:
            abort(403)
    tenant.property_id = None
    tenant.unit_id = None
    db.session.commit()
    flash(f'{tenant.name} unassigned.', 'success')
    return redirect(url_for('landlord_tenants'))


@app.route('/landlord/contractors')
@login_required(role='landlord')
def landlord_contractors():
    u = current_user()
    pids = [p.id for p in Property.query.filter_by(landlord_id=u.id).all()] or [-1]
    contractors = User.query.filter_by(role='contractor').order_by(User.name).all()
    stats = {}
    for c in contractors:
        jobs = MaintenanceRequest.query.filter_by(contractor_id=c.id).filter(
            MaintenanceRequest.property_id.in_(pids)
        ).all()
        ratings = [j.rating for j in jobs if j.rating]
        stats[c.id] = {
            'jobs': len(jobs),
            'open': sum(1 for j in jobs if j.status not in ('Paid', 'Closed')),
            'invoiced': sum(j.invoice_amount or 0 for j in jobs),
            'avg_rating': (sum(ratings) / len(ratings)) if ratings else None,
        }
    return render_template('landlord_contractors.html',
                           contractors=contractors, stats=stats)


# =========================================================
# Landlord: property / unit edit + delete
# =========================================================

@app.route('/landlord/property/<int:pid>/edit', methods=['POST'])
@login_required(role='landlord')
def edit_property(pid):
    u = current_user()
    prop = Property.query.filter_by(id=pid, landlord_id=u.id).first_or_404()
    prop.name = request.form['name'].strip()
    prop.address = request.form['address'].strip()
    db.session.commit()
    flash('Property updated.', 'success')
    return redirect(url_for('property_detail', pid=pid))


@app.route('/landlord/property/<int:pid>/delete', methods=['POST'])
@login_required(role='landlord')
def delete_property(pid):
    u = current_user()
    prop = Property.query.filter_by(id=pid, landlord_id=u.id).first_or_404()
    if prop.requests:
        flash('Cannot delete a property that has maintenance requests.', 'error')
        return redirect(url_for('property_detail', pid=pid))
    # Unassign any tenants
    for t in User.query.filter_by(property_id=prop.id, role='tenant').all():
        t.property_id = None
        t.unit_id = None
    db.session.delete(prop)
    db.session.commit()
    flash('Property deleted.', 'success')
    return redirect(url_for('landlord_dashboard'))


@app.route('/landlord/unit/<int:uid>/edit', methods=['POST'])
@login_required(role='landlord')
def edit_unit(uid):
    u = current_user()
    unit = Unit.query.get_or_404(uid)
    if unit.property.landlord_id != u.id:
        abort(403)
    unit.label = request.form['label'].strip()
    rent = request.form.get('rent_amount')
    unit.rent_amount = float(rent) if rent else None
    db.session.commit()
    flash('Unit updated.', 'success')
    return redirect(url_for('property_detail', pid=unit.property_id))


@app.route('/landlord/unit/<int:uid>/delete', methods=['POST'])
@login_required(role='landlord')
def delete_unit(uid):
    u = current_user()
    unit = Unit.query.get_or_404(uid)
    if unit.property.landlord_id != u.id:
        abort(403)
    for t in User.query.filter_by(unit_id=unit.id, role='tenant').all():
        t.property_id = None
        t.unit_id = None
    pid = unit.property_id
    db.session.delete(unit)
    db.session.commit()
    flash('Unit deleted.', 'success')
    return redirect(url_for('property_detail', pid=pid))


# =========================================================
# Tenant: edit / cancel / duplicate own requests
# =========================================================

@app.route('/tenant/request/<int:rid>/edit', methods=['GET', 'POST'])
@login_required(role='tenant')
def edit_request(rid):
    u = current_user()
    req = MaintenanceRequest.query.get_or_404(rid)
    if req.tenant_id != u.id:
        abort(403)
    if req.status != 'Reported':
        flash('You can only edit a request before it is assigned.', 'error')
        return redirect(url_for('request_detail', rid=rid))
    if request.method == 'POST':
        req.title = request.form['title'].strip()
        req.description = request.form['description'].strip()
        req.priority = request.form['priority']
        req.trade_category = request.form['trade_category']
        db.session.add(StatusHistory(request_id=req.id, from_status=req.status,
                                     to_status=req.status, changed_by_id=u.id,
                                     note='Request details edited by tenant'))
        db.session.commit()
        flash('Request updated.', 'success')
        return redirect(url_for('request_detail', rid=rid))
    return render_template('edit_request.html', req=req)


@app.route('/tenant/request/<int:rid>/cancel', methods=['POST'])
@login_required(role='tenant')
def cancel_request(rid):
    u = current_user()
    req = MaintenanceRequest.query.get_or_404(rid)
    if req.tenant_id != u.id:
        abort(403)
    if req.status not in ('Reported', 'Assigned'):
        flash('You can only cancel a request before work starts.', 'error')
        return redirect(url_for('request_detail', rid=rid))
    old = req.status
    req.status = 'Closed'
    log_status(req, old, 'Closed', u.id, 'Cancelled by tenant')
    notify(req.property.landlord_id, f'Request #{req.id} cancelled by tenant',
           url_for('request_detail', rid=req.id))
    if req.contractor_id:
        notify(req.contractor_id, f'Request #{req.id} cancelled by tenant',
               url_for('request_detail', rid=req.id))
    db.session.commit()
    flash('Request cancelled.', 'success')
    return redirect(url_for('request_detail', rid=rid))


@app.route('/request/<int:rid>/duplicate', methods=['POST'])
@login_required(role='tenant')
def duplicate_request(rid):
    u = current_user()
    src = MaintenanceRequest.query.get_or_404(rid)
    if src.tenant_id != u.id:
        abort(403)
    new = MaintenanceRequest(
        tenant_id=u.id,
        property_id=src.property_id,
        unit_id=src.unit_id,
        title=(src.title + ' (repeat)')[:120],
        description=src.description,
        priority=src.priority,
        trade_category=src.trade_category,
        status='Reported',
    )
    db.session.add(new)
    db.session.commit()
    db.session.add(StatusHistory(request_id=new.id, to_status='Reported',
                                 changed_by_id=u.id,
                                 note=f'Duplicated from #{src.id}'))
    notify(src.property.landlord_id, f'New request #{new.id}: {new.title}',
           url_for('request_detail', rid=new.id))
    db.session.commit()
    flash('Request duplicated.', 'success')
    return redirect(url_for('request_detail', rid=new.id))


# =========================================================
# Reopen request (any party with visibility)
# =========================================================

@app.route('/request/<int:rid>/reopen', methods=['POST'])
@login_required()
def reopen_request(rid):
    u = current_user()
    req = MaintenanceRequest.query.get_or_404(rid)
    if not can_view_request(u, req):
        abort(403)
    if req.status != 'Closed':
        flash('Only closed requests can be reopened.', 'error')
        return redirect(url_for('request_detail', rid=rid))
    old = req.status
    req.status = 'Reported'
    req.resolved_at = None
    req.invoice_paid = False
    log_status(req, old, 'Reported', u.id, f'Reopened by {u.role}')
    notify(req.property.landlord_id, f'Request #{req.id} reopened',
           url_for('request_detail', rid=req.id))
    notify(req.tenant_id, f'Request #{req.id} reopened',
           url_for('request_detail', rid=req.id))
    if req.contractor_id:
        notify(req.contractor_id, f'Request #{req.id} reopened',
               url_for('request_detail', rid=req.id))
    db.session.commit()
    flash('Request reopened.', 'success')
    return redirect(url_for('request_detail', rid=rid))


# =========================================================
# Calendar + global search
# =========================================================

@app.route('/calendar')
@login_required()
def calendar():
    u = current_user()
    if u.role == 'landlord':
        pids = [p.id for p in Property.query.filter_by(landlord_id=u.id).all()] or [-1]
        reqs = MaintenanceRequest.query.filter(
            MaintenanceRequest.property_id.in_(pids)
        ).order_by(MaintenanceRequest.created_at).all()
    elif u.role == 'tenant':
        reqs = MaintenanceRequest.query.filter_by(
            tenant_id=u.id
        ).order_by(MaintenanceRequest.created_at).all()
    else:
        reqs = MaintenanceRequest.query.filter_by(
            contractor_id=u.id
        ).order_by(MaintenanceRequest.created_at).all()

    by_date = {}
    for r in reqs:
        key = r.created_at.strftime('%Y-%m-%d')
        by_date.setdefault(key, []).append(r)
    # Sort dates descending
    by_date = dict(sorted(by_date.items(), reverse=True))
    return render_template('calendar.html', by_date=by_date)


@app.route('/search')
@login_required()
def search():
    u = current_user()
    q = request.args.get('q', '').strip()
    results = []
    if q:
        like = f'%{q}%'
        if u.role == 'landlord':
            pids = [p.id for p in Property.query.filter_by(landlord_id=u.id).all()] or [-1]
            results = MaintenanceRequest.query.filter(
                MaintenanceRequest.property_id.in_(pids),
                db.or_(MaintenanceRequest.title.ilike(like),
                       MaintenanceRequest.description.ilike(like))
            ).order_by(MaintenanceRequest.created_at.desc()).all()
        elif u.role == 'tenant':
            results = MaintenanceRequest.query.filter(
                MaintenanceRequest.tenant_id == u.id,
                db.or_(MaintenanceRequest.title.ilike(like),
                       MaintenanceRequest.description.ilike(like))
            ).order_by(MaintenanceRequest.created_at.desc()).all()
        else:
            results = MaintenanceRequest.query.filter(
                MaintenanceRequest.contractor_id == u.id,
                db.or_(MaintenanceRequest.title.ilike(like),
                       MaintenanceRequest.description.ilike(like))
            ).order_by(MaintenanceRequest.created_at.desc()).all()
    return render_template('search.html', q=q, results=results)
# =========================================================
# Create database and run
# =========================================================

with app.app_context():
    db.create_all()



# =========================================================
# Error handlers
# =========================================================

@app.errorhandler(404)
def page_not_found(e):
    return render_template('404.html'), 404


@app.errorhandler(500)
def server_error(e):
    db.session.rollback()
    return render_template('500.html'), 500


if __name__ == '__main__':
    app.run(debug=True, port=5001)
