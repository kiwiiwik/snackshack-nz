import os
import random
import smtplib
import threading
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import requests
from flask import Blueprint, render_template, request, redirect, url_for, flash, session, jsonify, current_app
from werkzeug.utils import secure_filename
from models import db, Users, Products, Transactions
from datetime import datetime
from decimal import Decimal
from sqlalchemy.exc import IntegrityError

ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif', 'webp'}

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

main = Blueprint('main', __name__)

# SMS daily counter (resets on new day)
_sms_counter = {'date': None, 'count': 0}

def normalise_nz_phone(phone):
    """Convert NZ local mobile number to international format for SMS API."""
    phone = phone.replace(' ', '').replace('-', '')
    if phone.startswith('0'):
        phone = '+64' + phone[1:]
    elif phone.startswith('64') and not phone.startswith('+'):
        phone = '+' + phone
    return phone

def check_sms_cap():
    """Check if daily SMS cap has been reached. Returns (allowed, count, cap)."""
    daily_cap = int(os.environ.get('SMS_DAILY_CAP', '20'))
    today = datetime.utcnow().date()
    if _sms_counter['date'] != today:
        _sms_counter['date'] = today
        _sms_counter['count'] = 0
    return _sms_counter['count'] < daily_cap, _sms_counter['count'], daily_cap

def send_sms_code(app, phone_number, user_name, code):
    """Send verification code via MessageMedia SMS."""
    def _send():
        with app.app_context():
            api_key = os.environ.get('MESSAGEMEDIA_API_KEY', '')
            api_secret = os.environ.get('MESSAGEMEDIA_API_SECRET', '')
            if not api_key or not api_secret:
                return
            intl_phone = normalise_nz_phone(phone_number)
            message = f"Snackshack code: {code} - Enter this on the kiosk to verify your email."
            try:
                resp = requests.post(
                    'https://api.messagemedia.com/v1/messages',
                    auth=(api_key, api_secret),
                    headers={'Content-Type': 'application/json', 'Accept': 'application/json'},
                    json={'messages': [{'content': message, 'destination_number': intl_phone}]},
                    timeout=10
                )
                if resp.status_code in (200, 201, 202):
                    _sms_counter['count'] += 1
                    # Notify admin about every SMS sent
                    notify_email = os.environ.get('SMS_NOTIFY_EMAIL', '')
                    if notify_email:
                        _send_sms_admin_notification(app, notify_email, user_name, intl_phone,
                                                     _sms_counter['count'], int(os.environ.get('SMS_DAILY_CAP', '20')))
            except Exception:
                pass
    threading.Thread(target=_send, daemon=True).start()

def _send_sms_admin_notification(app, admin_email, user_name, phone, count, cap):
    """Email admin whenever an SMS is sent, showing daily usage."""
    smtp_host = os.environ.get('SMTP_HOST', 'mail.smtp2go.com')
    smtp_port = int(os.environ.get('SMTP_PORT', 2525))
    smtp_user = os.environ.get('SMTP_USER', '')
    smtp_pass = os.environ.get('SMTP_PASS', '')
    smtp_from = os.environ.get('SMTP_FROM', smtp_user)
    if not smtp_user or not smtp_pass:
        return
    msg = MIMEMultipart('alternative')
    msg['Subject'] = f"Snackshack SMS sent ({count}/{cap} today)"
    msg['From'] = smtp_from
    msg['To'] = admin_email
    body = f"""SMS verification code sent:

  User: {user_name}
  Phone: {phone}
  Daily usage: {count} of {cap}

- Claudes Snackshack"""
    msg.attach(MIMEText(body, 'plain'))
    try:
        with smtplib.SMTP(smtp_host, smtp_port) as server:
            server.starttls()
            server.login(smtp_user, smtp_pass)
            server.sendmail(smtp_from, admin_email, msg.as_string())
    except Exception:
        pass

def send_purchase_email(app, user_email, user_name, product_desc, price, new_balance):
    """Send purchase notification email via SMTP2Go in a background thread."""
    def _send():
        with app.app_context():
            smtp_host = os.environ.get('SMTP_HOST', 'mail.smtp2go.com')
            smtp_port = int(os.environ.get('SMTP_PORT', 2525))
            smtp_user = os.environ.get('SMTP_USER', '')
            smtp_pass = os.environ.get('SMTP_PASS', '')
            smtp_from = os.environ.get('SMTP_FROM', smtp_user)
            if not smtp_user or not smtp_pass:
                return
            msg = MIMEMultipart('alternative')
            msg['Subject'] = f"Snackshack Purchase: {product_desc}"
            msg['From'] = smtp_from
            msg['To'] = user_email
            body = f"""Hi {user_name},

A purchase was just recorded on your Snackshack account:

  Item: {product_desc}
  Amount: ${price:.2f}
  New Balance: ${new_balance:.2f}

If this wasn't you, please speak to an admin.

- Claudes Snackshack"""
            msg.attach(MIMEText(body, 'plain'))
            try:
                with smtplib.SMTP(smtp_host, smtp_port) as server:
                    server.starttls()
                    server.login(smtp_user, smtp_pass)
                    server.sendmail(smtp_from, user_email, msg.as_string())
            except Exception:
                pass  # Don't break purchases if email fails
    threading.Thread(target=_send, daemon=True).start()

def process_barcode(barcode):
    if not barcode: return {"status": "not_found"}
    barcode = str(barcode).strip()
    user = Users.query.filter_by(card_id=barcode).first()
    if user:
        if user.pin: return {"status": "needs_pin", "user_id": user.user_id}
        session['user_id'] = int(user.user_id)
        user.last_seen = datetime.utcnow()
        db.session.commit()
        return {"status": "logged_in"}
    if 'user_id' in session:
        product = Products.query.filter_by(upc_code=barcode).first()
        if product:
            if product.stock_level is not None and product.stock_level <= 0:
                flash(f"Out of stock: {product.description}", "warning")
                return {"status": "out_of_stock"}
            u = Users.query.get(int(session['user_id']))
            price = Decimal(str(product.price or 0.0))
            u.balance = Decimal(str(u.balance or 0.0)) - price
            product.stock_level = (product.stock_level or 0) - 1
            db.session.add(Transactions(user_id=u.user_id, upc_code=product.upc_code, amount=price))
            db.session.commit()
            if u.email and u.notify_on_purchase:
                send_purchase_email(current_app._get_current_object(), u.email, u.first_name, product.description, float(price), float(u.balance))
            return {"status": "purchased", "description": product.description}
    return {"status": "not_found"}

@main.route('/')
def index():
    current_user = None
    needs_pin = request.args.get('needs_pin')
    pin_user = Users.query.get(int(needs_pin)) if needs_pin else None
    if 'user_id' in session:
        current_user = Users.query.get(int(session['user_id']))
    
    # Alphabetical sorting for 80+ names
    all_users = Users.query.order_by(Users.first_name.asc()).all()
    
    cat_order = ["Drinks", "Snacks", "Candy", "Frozen", "Coffee Pods", "Sweepstake Tickets"]
    grouped = {cat: [] for cat in cat_order}
    for p in Products.query.filter_by(is_quick_item=True).all():
        cat = p.category if p.category in grouped else "Snacks"
        grouped[cat].append(p)
        
    return render_template('index.html',
        user=current_user,
        users=all_users,
        grouped_products={k: v for k, v in grouped.items() if v},
        needs_pin=needs_pin,
        pin_user=pin_user,
        just_bought=request.args.get('bought'),
        verify_email=request.args.get('verify_email'),
        pending_email=session.get('pending_email'))

@main.route('/manual/<barcode>')
def manual_add(barcode=None):
    res = process_barcode(barcode)
    return redirect(url_for('main.index', bought=res.get("description"))) if res.get("status") == "purchased" else redirect(url_for('main.index'))

@main.route('/scan', methods=['POST'])
def scan():
    res = process_barcode(request.form.get('barcode', '').strip())
    return redirect(url_for('main.index', bought=res.get('description'))) if res.get('status') == 'purchased' else redirect(url_for('main.index'))

@main.route('/undo')
def undo():
    uid = session.get('user_id')
    if uid:
        lt = Transactions.query.filter_by(user_id=uid).order_by(Transactions.transaction_date.desc()).first()
        if lt:
            u, p = Users.query.get(uid), Products.query.get(lt.upc_code)
            u.balance += lt.amount
            if p and lt.amount > 0: p.stock_level += 1
            db.session.delete(lt); db.session.commit()
    return redirect(url_for('main.index'))

@main.route('/admin/products')
def manage_products():
    if 'user_id' not in session: return redirect(url_for('main.index'))
    return render_template('manage_products.html', products=Products.query.order_by(Products.description).all())

@main.route('/admin/product/save', methods=['POST'])
def save_product_manual():
    upc = request.form.get('upc_code', '').strip()
    p = Products.query.get(upc) or Products(upc_code=upc)
    if not Products.query.get(upc): db.session.add(p)
    p.manufacturer, p.description, p.size = request.form.get('manufacturer'), request.form.get('description'), request.form.get('size')
    p.price, p.category, p.stock_level = Decimal(request.form.get('price', '0.00')), request.form.get('category'), int(request.form.get('stock_level', 0))
    p.is_quick_item = 'is_quick_item' in request.form

    file = request.files.get('product_image')
    if file and file.filename and allowed_file(file.filename):
        ext = file.filename.rsplit('.', 1)[1].lower()
        filename = secure_filename(f"{upc}.{ext}")
        upload_dir = os.path.join(current_app.static_folder, 'images')
        os.makedirs(upload_dir, exist_ok=True)
        file.save(os.path.join(upload_dir, filename))
        p.image_url = filename

    db.session.commit()
    return redirect(url_for('main.manage_products'))

@main.route('/admin/product/delete/<upc>')
def delete_product(upc):
    p = Products.query.get(upc)
    if p:
        try: db.session.delete(p); db.session.commit()
        except IntegrityError: db.session.rollback(); flash("History exists; delete failed.", "danger")
    return redirect(url_for('main.manage_products'))

@main.route('/pin_verify', methods=['POST'])
def pin_verify():
    uid, pin = request.form.get('user_id'), request.form.get('pin', '').strip()
    u = Users.query.get(int(uid))
    if u and str(u.pin) == pin: session['user_id'] = u.user_id; return redirect(url_for('main.index'))
    flash("Incorrect PIN.", "danger"); return redirect(url_for('main.index', needs_pin=uid))

@main.route('/pin_set', methods=['POST'])
def pin_set():
    if 'user_id' in session:
        u, pin = Users.query.get(int(session['user_id'])), request.form.get('pin', '').strip()
        if u and pin.isdigit() and len(pin) == 4: u.pin = pin; db.session.commit(); flash("PIN enabled.", "success")
    return redirect(url_for('main.index'))

@main.route('/pin_clear', methods=['POST'])
def pin_clear():
    if 'user_id' in session:
        u = Users.query.get(int(session['user_id']))
        if u: u.pin = None; db.session.commit(); flash("PIN removed.", "info")
    return redirect(url_for('main.index'))

@main.route('/email_settings', methods=['POST'])
def email_settings():
    if 'user_id' not in session:
        return redirect(url_for('main.index'))
    u = Users.query.get(int(session['user_id']))
    if not u:
        return redirect(url_for('main.index'))

    new_email = request.form.get('email', '').strip() or None
    new_phone = request.form.get('phone', '').strip() or None
    verify_code = request.form.get('verify_code', '').strip()
    notify = 'notify_on_purchase' in request.form

    # If user is just toggling notification (no email/phone change), save directly
    if new_email == u.email and (new_phone or None) == (u.phone_number or None):
        u.notify_on_purchase = notify
        db.session.commit()
        flash("Notification preference saved.", "success")
        return redirect(url_for('main.index'))

    # If user is clearing their email
    if not new_email:
        u.email = None
        u.notify_on_purchase = False
        session.pop('pending_email', None)
        session.pop('pending_phone', None)
        session.pop('sms_code', None)
        db.session.commit()
        flash("Email removed.", "info")
        return redirect(url_for('main.index'))

    # If user submitted a verification code, check it
    if verify_code:
        if verify_code == session.get('sms_code') and new_email == session.get('pending_email'):
            u.email = new_email
            u.phone_number = session.get('pending_phone')
            u.notify_on_purchase = notify
            session.pop('pending_email', None)
            session.pop('pending_phone', None)
            session.pop('sms_code', None)
            db.session.commit()
            flash("Email verified and saved!", "success")
        else:
            flash("Incorrect code. Try again.", "danger")
            return redirect(url_for('main.index', verify_email=1))
        return redirect(url_for('main.index'))

    # New/changed email — send SMS verification code
    if not new_phone:
        flash("Enter your mobile number to receive a verification code.", "danger")
        return redirect(url_for('main.index'))

    # Check daily SMS cap
    allowed, count, cap = check_sms_cap()
    if not allowed:
        flash("SMS limit reached for today. Try again tomorrow.", "warning")
        return redirect(url_for('main.index'))

    code = f"{random.randint(0, 999999):06d}"
    session['pending_email'] = new_email
    session['pending_phone'] = new_phone
    session['sms_code'] = code
    send_sms_code(current_app._get_current_object(), new_phone, u.first_name, code)
    flash("Verification code sent to your phone!", "info")
    return redirect(url_for('main.index', verify_email=1))

@main.route('/logout')
def logout(): session.pop('user_id', None); return redirect(url_for('main.index'))

@main.route('/select_user/<int:user_id>')
def select_user(user_id):
    u = Users.query.get(user_id)
    if u and u.pin: return redirect(url_for('main.index', needs_pin=u.user_id))
    if u: session['user_id'] = u.user_id; u.last_seen = datetime.utcnow(); db.session.commit()
    return redirect(url_for('main.index'))

# --- ADMIN USER MANAGEMENT ---

@main.route('/admin/users')
def manage_users():
    if 'user_id' not in session: return redirect(url_for('main.index'))
    return render_template('manage_users.html', users=Users.query.order_by(Users.last_name).all())

@main.route('/admin/user/save', methods=['POST'])
def save_user():
    uid = request.form.get('user_id')
    user = Users.query.get(int(uid)) if uid else Users(card_id=request.form.get('card_id', '').strip())
    if not uid: db.session.add(user)
    user.first_name, user.last_name = request.form.get('first_name'), request.form.get('last_name')
    user.is_admin = 'is_admin' in request.form
    db.session.commit()
    return redirect(url_for('main.manage_users'))

@main.route('/admin/user/delete/<int:user_id>')
def delete_user(user_id):
    user = Users.query.get(user_id)
    if user and int(session.get('user_id')) != user_id:
        try:
            db.session.delete(user); db.session.commit()
        except IntegrityError:
            db.session.rollback(); flash("User has history; delete failed.", "danger")
    return redirect(url_for('main.manage_users'))

@main.route('/admin/user/payment', methods=['POST'])
def record_payment():
    uid, amount = request.form.get('user_id'), Decimal(request.form.get('amount', '0.00'))
    user = Users.query.get(int(uid))
    if user:
        user.balance = Decimal(str(user.balance or 0.0)) + amount
        db.session.add(Transactions(user_id=user.user_id, upc_code='PAYMENT', amount=-amount))
        db.session.commit()
        flash(f"Balance updated.", "success")
    return redirect(url_for('main.manage_users'))

# --- REPORTING ---

@main.route('/admin/monthly_report')
def monthly_report():
    ym = request.args.get('month', datetime.utcnow().strftime("%Y-%m"))
    start_dt = datetime.strptime(ym, "%Y-%m")
    end_dt = datetime(start_dt.year + (1 if start_dt.month == 12 else 0), (start_dt.month % 12) + 1, 1)
    tx_rows = db.session.query(Transactions, Products).outerjoin(Products, Products.upc_code == Transactions.upc_code).filter(Transactions.transaction_date >= start_dt, Transactions.transaction_date < end_dt).all()
    rows = []
    for u in Users.query.order_by(Users.last_name).all():
        user_txs = [(t, p) for t, p in tx_rows if t.user_id == u.user_id]
        spent = sum(float(t.amount or 0) for t, p in user_txs)
        end_balance = float(u.balance or 0)
        start_balance = end_balance + spent
        txs = [{"when": t.transaction_date.strftime("%d %b %H:%M"), "desc": p.description if p else "Payment", "amount": float(t.amount or 0)} for t, p in user_txs]
        rows.append({"user": u, "spent": spent, "end_balance": end_balance, "start_balance": start_balance, "txs": txs})
    return render_template("monthly_report.html", rows=rows, selected_month=ym, month_label=start_dt.strftime("%B %Y"), start_iso=start_dt.strftime("%Y-%m-%d"), end_iso=end_dt.strftime("%Y-%m-%d"))

# --- DANGER ZONE ---

@main.route('/admin/nuke-transactions')
def nuke_transactions():
    Transactions.query.delete(); db.session.commit(); flash("HISTORY NUKED.", "danger")
    return redirect(url_for('main.index'))

@main.route('/admin/reset-balances')
def reset_balances():
    Users.query.update({Users.balance: 0.00}); db.session.commit(); flash("Balances reset.", "warning")
    return redirect(url_for('main.index'))

@main.route('/admin/get-product/<barcode>')
def get_product(barcode):
    p = Products.query.get(barcode.strip())
    if p: return jsonify({"found": True, "mfg": p.manufacturer, "desc": p.description, "size": p.size, "price": str(p.price), "cat": p.category, "soh": p.stock_level})
    try:
        res = requests.get(f"https://world.openfoodfacts.org/api/v0/product/{barcode}.json", timeout=5)
        if res.status_code == 200:
            d = res.json()
            if d.get("status") == 1:
                prod = d.get("product", {})
                return jsonify({"found": True, "mfg": prod.get("brands", ""), "desc": prod.get("product_name", ""), "size": prod.get("quantity", ""), "soh": 0})
    except: pass
    return jsonify({"found": False})