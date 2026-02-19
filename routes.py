import os
import re
import base64
import random
import hashlib
import smtplib
import threading
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import requests
from flask import Blueprint, render_template, request, redirect, url_for, flash, session, jsonify, current_app, make_response
from werkzeug.utils import secure_filename
from models import db, Users, Products, Transactions, Wallpapers
from datetime import datetime, timedelta
from decimal import Decimal
from sqlalchemy.exc import IntegrityError

ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif', 'webp'}

def hash_pin(pin):
    """Hash a 4-digit PIN with the app secret key as salt."""
    salt = os.environ.get('FLASK_SECRET_KEY', 'dev-key-default-123')
    return hashlib.sha256(f"{salt}:{pin}".encode()).hexdigest()

AVATAR_OPTIONS = [
    'cookie', 'cupcake', 'donut', 'icecream', 'pizza', 'taco',
    'burger', 'fries', 'sushi', 'avocado', 'peach', 'cherry',
    'grape', 'lemon', 'mango', 'strawberry', 'watermelon', 'coconut',
    'pineapple', 'banana', 'kiwi', 'blueberry', 'pancake', 'pretzel',
]

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

main = Blueprint('main', __name__)

def is_mobile_site():
    """Check if request is coming via the m. mobile subdomain."""
    host = request.host.split(':')[0].lower()
    return host.startswith('m.')

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
                display_name = u.screen_name or u.first_name
                send_purchase_email(current_app._get_current_object(), u.email, display_name, product.description, float(price), float(u.balance))
            return {"status": "purchased", "description": product.description}
    return {"status": "not_found"}

@main.route('/')
def index():
    mobile = is_mobile_site()
    current_user = None
    needs_pin = request.args.get('needs_pin')
    pin_user = Users.query.get(int(needs_pin)) if needs_pin else None
    if 'user_id' in session:
        current_user = Users.query.get(int(session['user_id']))

    # Alphabetical sorting for 80+ names - prefer screen_name, fallback to first_name
    all_users = Users.query.order_by(
        db.func.coalesce(Users.screen_name, Users.first_name).asc()
    ).all()

    cat_order = ["Drinks", "Snacks", "Candy", "Frozen", "Coffee Pods", "Sweepstake Tickets"]
    grouped = {cat: [] for cat in cat_order}
    for p in Products.query.filter_by(is_quick_item=True).all():
        cat = p.category or "Snacks"
        if cat not in grouped:
            grouped[cat] = []
        grouped[cat].append(p)

    _wallpapers = Wallpapers.query.order_by(Wallpapers.slot).all()
    wallpaper_slots = [
        {'slot': w.slot, 'land': bool(w.image_landscape), 'port': bool(w.image_portrait)}
        for w in _wallpapers if w.image_landscape or w.image_portrait
    ]

    return render_template('index.html',
        user=current_user,
        users=all_users,
        grouped_products={k: v for k, v in grouped.items() if v},
        needs_pin=needs_pin,
        pin_user=pin_user,
        just_bought=request.args.get('bought'),
        verify_email=request.args.get('verify_email'),
        pending_email=session.get('pending_email'),
        avatar_options=AVATAR_OPTIONS,
        is_mobile=mobile,
        show_register=request.args.get('show_register'),
        wallpaper_slots=wallpaper_slots)


@main.route('/terms')
def terms():
    """Show terms and conditions page before enrolment."""
    mobile = is_mobile_site()
    current_user = None
    if 'user_id' in session:
        current_user = Users.query.get(int(session['user_id']))
    return render_template('terms.html', user=current_user, is_mobile=mobile)

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
    default_cats = ["Drinks", "Snacks", "Candy", "Frozen", "Coffee Pods", "Sweepstake Tickets"]
    db_cats = [r[0] for r in db.session.query(Products.category).distinct() if r[0]]
    categories = list(dict.fromkeys(default_cats + db_cats))  # preserve order, deduplicate
    return render_template('manage_products.html', products=Products.query.order_by(Products.description).all(), categories=categories)

@main.route('/admin/product/save', methods=['POST'])
def save_product_manual():
    upc = request.form.get('upc_code', '').strip()
    p = Products.query.get(upc) or Products(upc_code=upc)
    if not Products.query.get(upc): db.session.add(p)
    p.manufacturer, p.description, p.size = request.form.get('manufacturer'), request.form.get('description'), request.form.get('size')
    p.price, p.category, p.stock_level = Decimal(request.form.get('price', '0.00')), request.form.get('category'), int(request.form.get('stock_level', 0))
    p.is_quick_item = 'is_quick_item' in request.form

    # Store image as base64 in DB so it persists across Azure redeploys
    file = request.files.get('product_image')
    img_saved = False
    if file:
        # Determine type from filename or MIME type (clipboard pastes may lack a filename)
        mime = None
        if file.filename and '.' in file.filename:
            ext = file.filename.rsplit('.', 1)[1].lower()
            if ext in ALLOWED_EXTENSIONS:
                mime = 'jpeg' if ext == 'jpg' else ext
        if not mime and file.content_type:
            mime_map = {'image/png': 'png', 'image/jpeg': 'jpeg', 'image/webp': 'webp', 'image/gif': 'gif'}
            mime = mime_map.get(file.content_type.lower())
        if mime:
            raw = file.read()
            if len(raw) <= 2 * 1024 * 1024:
                p.image_data = f"data:image/{mime};base64,{base64.b64encode(raw).decode()}"
                p.image_url = secure_filename(f"{upc}.{'jpg' if mime == 'jpeg' else mime}")
                img_saved = True
    if not img_saved:
        b64_data = request.form.get('image_base64', '').strip()
        if b64_data:
            match = re.match(r'^data:image/(png|jpe?g|gif|webp);base64,(.+)$', b64_data, re.DOTALL)
            if match:
                raw = base64.b64decode(match.group(2))
                if len(raw) <= 2 * 1024 * 1024:
                    p.image_data = b64_data
                    ext = match.group(1).replace('jpeg', 'jpg')
                    p.image_url = secure_filename(f"{upc}.{ext}")

    db.session.commit()
    return redirect(url_for('main.manage_products'))

@main.route('/admin/product/delete/<upc>')
def delete_product(upc):
    p = Products.query.get(upc)
    if p:
        try: db.session.delete(p); db.session.commit()
        except IntegrityError: db.session.rollback(); flash("History exists; delete failed.", "danger")
    return redirect(url_for('main.manage_products'))

@main.route('/product_image/<upc>')
def product_image(upc):
    """Serve product image from DB. Falls back to placeholder."""
    p = Products.query.get(upc)
    if p and p.image_data:
        match = re.match(r'^data:image/([\w+]+);base64,(.+)$', p.image_data, re.DOTALL)
        if match:
            mime, data = match.group(1), match.group(2)
            resp = make_response(base64.b64decode(data))
            resp.headers['Content-Type'] = f'image/{mime}'
            resp.headers['Cache-Control'] = 'public, max-age=86400'
            return resp
    return redirect(url_for('static', filename='images/placeholder.png'))

@main.route('/wallpaper/<int:slot>/<orientation>')
def wallpaper_image(slot, orientation):
    """Serve wallpaper image (landscape or portrait) from DB."""
    w = Wallpapers.query.get(slot)
    if w:
        data = w.image_landscape if orientation == 'landscape' else w.image_portrait
        if data:
            match = re.match(r'^data:image/([\w+]+);base64,(.+)$', data, re.DOTALL)
            if match:
                mime, encoded = match.group(1), match.group(2)
                resp = make_response(base64.b64decode(encoded))
                resp.headers['Content-Type'] = f'image/{mime}'
                resp.headers['Cache-Control'] = 'public, max-age=86400'
                return resp
    return redirect(url_for('static', filename='images/placeholder.png'))

@main.route('/admin/wallpapers')
def manage_wallpapers():
    if 'user_id' not in session:
        return redirect(url_for('main.index'))
    current = Users.query.get(int(session['user_id']))
    if not current or not (current.is_admin or current.is_super_admin):
        return redirect(url_for('main.index'))
    wallpapers = {w.slot: w for w in Wallpapers.query.all()}
    return render_template('manage_wallpapers.html', wallpapers=wallpapers, slots=range(1, 6))

@main.route('/admin/wallpaper/save', methods=['POST'])
def save_wallpaper():
    if 'user_id' not in session:
        return redirect(url_for('main.index'))
    current = Users.query.get(int(session['user_id']))
    if not current or not (current.is_admin or current.is_super_admin):
        return redirect(url_for('main.index'))
    slot = int(request.form.get('slot', 0))
    orientation = request.form.get('orientation', '')
    if slot < 1 or slot > 5 or orientation not in ('landscape', 'portrait'):
        flash("Invalid slot or orientation.", "danger")
        return redirect(url_for('main.manage_wallpapers'))
    w = Wallpapers.query.get(slot)
    if not w:
        w = Wallpapers(slot=slot)
        db.session.add(w)
    file = request.files.get('wallpaper_image')
    if file:
        mime = None
        if file.filename and '.' in file.filename:
            ext = file.filename.rsplit('.', 1)[1].lower()
            if ext in ALLOWED_EXTENSIONS:
                mime = 'jpeg' if ext == 'jpg' else ext
        if not mime and file.content_type:
            mime_map = {'image/png': 'png', 'image/jpeg': 'jpeg', 'image/webp': 'webp', 'image/gif': 'gif'}
            mime = mime_map.get(file.content_type.lower())
        if mime:
            raw = file.read()
            if len(raw) <= 5 * 1024 * 1024:
                encoded = f"data:image/{mime};base64,{base64.b64encode(raw).decode()}"
                if orientation == 'landscape':
                    w.image_landscape = encoded
                else:
                    w.image_portrait = encoded
                db.session.commit()
                flash(f"Wallpaper {slot} ({orientation}) saved.", "success")
            else:
                flash("Image too large (max 5 MB).", "danger")
        else:
            flash("Unsupported image format.", "danger")
    return redirect(url_for('main.manage_wallpapers'))

@main.route('/admin/wallpaper/delete/<int:slot>/<orientation>', methods=['POST'])
def delete_wallpaper(slot, orientation):
    if 'user_id' not in session:
        return redirect(url_for('main.index'))
    current = Users.query.get(int(session['user_id']))
    if not current or not (current.is_admin or current.is_super_admin):
        return redirect(url_for('main.index'))
    w = Wallpapers.query.get(slot)
    if w:
        if orientation == 'landscape':
            w.image_landscape = None
        else:
            w.image_portrait = None
        if not w.image_landscape and not w.image_portrait:
            db.session.delete(w)
        db.session.commit()
        flash(f"Wallpaper {slot} ({orientation}) removed.", "info")
    return redirect(url_for('main.manage_wallpapers'))

@main.route('/pin_verify', methods=['POST'])
def pin_verify():
    uid, pin = request.form.get('user_id'), request.form.get('pin', '').strip()
    u = Users.query.get(int(uid))
    if u and u.pin == hash_pin(pin): session['user_id'] = u.user_id; return redirect(url_for('main.index'))
    flash("Incorrect PIN.", "danger"); return redirect(url_for('main.index', needs_pin=uid))

@main.route('/pin_set', methods=['POST'])
def pin_set():
    if 'user_id' in session:
        u, pin = Users.query.get(int(session['user_id'])), request.form.get('pin', '').strip()
        if u and pin.isdigit() and len(pin) == 4: u.pin = hash_pin(pin); db.session.commit(); flash("PIN enabled.", "success")
    return redirect(url_for('main.index'))

@main.route('/pin_clear', methods=['POST'])
def pin_clear():
    if 'user_id' in session:
        u = Users.query.get(int(session['user_id']))
        if u: u.pin = None; db.session.commit(); flash("PIN removed.", "info")
    return redirect(url_for('main.index'))

@main.route('/admin/pin_reset/<int:user_id>', methods=['POST'])
def admin_pin_reset(user_id):
    if 'user_id' not in session:
        return redirect(url_for('main.index'))
    current = Users.query.get(int(session['user_id']))
    if not current or not (current.is_admin or current.is_super_admin):
        return redirect(url_for('main.index'))
    target = Users.query.get(user_id)
    if target:
        # Admins can only clear PINs for regular users, super admins can clear anyone's
        if not current.is_super_admin and (target.is_admin or target.is_super_admin):
            flash("Only super admins can clear PINs for admins.", "warning")
            return redirect(url_for('main.manage_users'))
        target.pin = None
        db.session.commit()
        flash(f"PIN cleared for {target.first_name} {target.last_name}.", "info")
    return redirect(url_for('main.manage_users'))

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
    send_sms_code(current_app._get_current_object(), new_phone, u.screen_name or u.first_name, code)
    flash("Verification code sent to your phone!", "info")
    return redirect(url_for('main.index', verify_email=1))

@main.route('/set_screen_name', methods=['POST'])
def set_screen_name():
    if 'user_id' not in session:
        return redirect(url_for('main.index'))
    u = Users.query.get(int(session['user_id']))
    if not u:
        return redirect(url_for('main.index'))
    u.screen_name = request.form.get('screen_name', '').strip() or None
    db.session.commit()
    flash("Screen name updated.", "success")
    return redirect(url_for('main.index'))

@main.route('/set_avatar', methods=['POST'])
def set_avatar():
    if 'user_id' not in session:
        return redirect(url_for('main.index'))
    u = Users.query.get(int(session['user_id']))
    if not u:
        return redirect(url_for('main.index'))
    avatar = request.form.get('avatar', '').strip()
    if avatar in AVATAR_OPTIONS:
        u.avatar = avatar
        u.avatar_data = None  # clear custom photo when picking a preset
        db.session.commit()
    return redirect(url_for('main.index'))

@main.route('/upload_avatar', methods=['POST'])
def upload_avatar():
    if 'user_id' not in session:
        return redirect(url_for('main.index'))
    u = Users.query.get(int(session['user_id']))
    if not u:
        return redirect(url_for('main.index'))
    file = request.files.get('avatar_photo')
    if not file or not file.filename:
        flash("No file received.", "warning")
        return redirect(url_for('main.index'))
    # Determine type from filename or fall back to MIME type (for clipboard pastes)
    mime = None
    if file.filename and '.' in file.filename:
        ext = file.filename.rsplit('.', 1)[1].lower()
        if ext in ALLOWED_EXTENSIONS:
            mime = 'jpeg' if ext == 'jpg' else ext
    if not mime and file.content_type:
        ct = file.content_type.lower()
        mime_map = {'image/png': 'png', 'image/jpeg': 'jpeg', 'image/webp': 'webp', 'image/gif': 'gif'}
        mime = mime_map.get(ct)
    if not mime:
        flash("Unsupported image format.", "warning")
        return redirect(url_for('main.index'))
    raw = file.read()
    if len(raw) > 2 * 1024 * 1024:
        flash("Image too large (max 2 MB).", "warning")
        return redirect(url_for('main.index'))
    try:
        u.avatar_data = f"data:image/{mime};base64,{base64.b64encode(raw).decode()}"
        u.avatar = None  # clear preset when uploading custom
        db.session.commit()
        flash("Photo updated!", "success")
    except Exception as e:
        db.session.rollback()
        flash(f"Failed to save photo: {e}", "danger")
    return redirect(url_for('main.index'))

@main.route('/user_avatar/<int:user_id>')
def user_avatar(user_id):
    """Serve custom avatar photo from DB."""
    u = Users.query.get(user_id)
    if u and u.avatar_data:
        match = re.match(r'^data:image/([\w+]+);base64,(.+)$', u.avatar_data, re.DOTALL)
        if match:
            mime, data = match.group(1), match.group(2)
            resp = make_response(base64.b64decode(data))
            resp.headers['Content-Type'] = f'image/{mime}'
            resp.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
            return resp
    return redirect(url_for('static', filename='images/placeholder.png'))

@main.route('/logout')
def logout(): session.pop('user_id', None); return redirect(url_for('main.index'))

@main.route('/register', methods=['POST'])
def register():
    first = request.form.get('first_name', '').strip()
    last = request.form.get('last_name', '').strip()
    if not first or not last:
        flash("Please enter both first and last name.", "danger")
        return redirect(url_for('main.index'))
    want_notify = request.form.get('want_notifications') == 'yes'
    new_email = request.form.get('email', '').strip() or None
    new_phone = request.form.get('phone', '').strip() or None
    card_id = f"SELF-{datetime.utcnow().strftime('%Y%m%d%H%M%S')}-{first[:3].upper()}"
    user = Users(card_id=card_id, first_name=first, last_name=last, balance=0.00)
    if want_notify and new_email and new_phone:
        user.notify_on_purchase = True
        # Store pending verification in session, don't save email until verified
        db.session.add(user)
        db.session.commit()
        session['user_id'] = user.user_id
        # Check daily SMS cap before sending
        allowed, count, cap = check_sms_cap()
        if allowed:
            code = f"{random.randint(0, 999999):06d}"
            session['pending_email'] = new_email
            session['pending_phone'] = new_phone
            session['sms_code'] = code
            send_sms_code(current_app._get_current_object(), new_phone, first, code)
            flash("Welcome! Verification code sent to your phone.", "info")
            return redirect(url_for('main.index', verify_email=1))
        else:
            flash("Welcome! SMS limit reached today - set up email later via the Email button.", "info")
            return redirect(url_for('main.index'))
    else:
        db.session.add(user)
        db.session.commit()
        session['user_id'] = user.user_id
        flash("Welcome to the Snack Shoppe!", "success")
        return redirect(url_for('main.index'))

@main.route('/select_user/<int:user_id>')
def select_user(user_id):
    u = Users.query.get(user_id)
    if u and u.pin: return redirect(url_for('main.index', needs_pin=u.user_id))
    if u:
        if is_mobile_site():
            session.permanent = True
        session['user_id'] = u.user_id
        u.last_seen = datetime.utcnow()
        db.session.commit()
    return redirect(url_for('main.index'))

# --- ADMIN USER MANAGEMENT ---

@main.route('/admin/users')
def manage_users():
    if 'user_id' not in session: return redirect(url_for('main.index'))
    current = Users.query.get(int(session['user_id']))
    return render_template('manage_users.html', users=Users.query.order_by(Users.last_name).all(), current_user=current)

@main.route('/admin/user/save', methods=['POST'])
def save_user():
    uid = request.form.get('user_id')
    user = Users.query.get(int(uid)) if uid else Users(card_id=request.form.get('card_id', '').strip())
    if not uid: db.session.add(user)
    user.first_name, user.last_name = request.form.get('first_name'), request.form.get('last_name')
    user.screen_name = request.form.get('screen_name', '').strip() or None
    # Only super admins can change role assignments
    current = Users.query.get(int(session.get('user_id', 0)))
    if current and current.is_super_admin:
        role = request.form.get('role', 'user')
        user.is_super_admin = (role == 'super_admin')
        user.is_admin = (role in ('admin', 'super_admin'))
    elif not (user.is_admin or user.is_super_admin):
        # Non-super-admins can only toggle admin on regular users, never downgrade admins
        user.is_admin = 'is_admin' in request.form
    db.session.commit()
    return redirect(url_for('main.manage_users'))

@main.route('/admin/user/delete/<int:user_id>')
def delete_user(user_id):
    user = Users.query.get(user_id)
    if user and int(session.get('user_id')) != user_id:
        try:
            Transactions.query.filter_by(user_id=user_id).delete()
            db.session.delete(user); db.session.commit()
        except Exception:
            db.session.rollback(); flash("Could not delete user.", "danger")
    return redirect(url_for('main.manage_users'))

@main.route('/admin/users/purge', methods=['POST'])
def purge_users():
    if 'user_id' not in session:
        return redirect(url_for('main.index'))
    users_with_tx = db.session.query(Transactions.user_id).distinct()
    idle_users = Users.query.filter(
        ~Users.user_id.in_(users_with_tx),
        Users.user_id != int(session['user_id'])
    ).all()
    count = len(idle_users)
    for u in idle_users:
        db.session.delete(u)
    db.session.commit()
    flash(f"Purged {count} user(s) with no purchase history.", "info")
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

# --- Nightly Report ---

def generate_nightly_report_html(app):
    """Build the nightly report HTML with 3 sections. Must be called within app context."""
    from datetime import timezone
    import pytz
    nz = pytz.timezone('Pacific/Auckland')
    now_nz = datetime.now(nz)
    today_start = nz.localize(datetime(now_nz.year, now_nz.month, now_nz.day))
    today_end = today_start + timedelta(days=1)
    # Convert to UTC for DB queries
    today_start_utc = today_start.astimezone(pytz.utc).replace(tzinfo=None)
    today_end_utc = today_end.astimezone(pytz.utc).replace(tzinfo=None)
    report_date = now_nz.strftime("%A %d %B %Y")

    # --- Section 1: Daily Transactions ---
    txs = db.session.query(Transactions, Users, Products)\
        .outerjoin(Users, Users.user_id == Transactions.user_id)\
        .outerjoin(Products, Products.upc_code == Transactions.upc_code)\
        .filter(Transactions.transaction_date >= today_start_utc,
                Transactions.transaction_date < today_end_utc)\
        .order_by(Transactions.transaction_date).all()

    tx_rows = ""
    daily_total = 0.0
    for t, u, p in txs:
        real = f"{u.first_name or ''} {u.last_name or ''}".strip() if u else "Unknown"
        name = f"{real} ({u.screen_name})" if u and u.screen_name else real
        desc = p.description if p else "Payment"
        amt = float(t.amount or 0)
        daily_total += amt
        tx_time = pytz.utc.localize(t.transaction_date).astimezone(nz).strftime("%H:%M") if t.transaction_date else ""
        tx_rows += f"<tr><td>{tx_time}</td><td>{name}</td><td>{desc}</td><td style='text-align:right'>${amt:.2f}</td></tr>\n"

    if not tx_rows:
        tx_rows = "<tr><td colspan='4' style='text-align:center;color:#999;'>No transactions today</td></tr>"

    # --- Section 2: Running Balances ---
    users = Users.query.order_by(Users.last_name, Users.first_name).all()
    bal_rows = ""
    for u in users:
        bal = float(u.balance or 0)
        colour = "#dc3545" if bal < 0 else "#333"
        real = f"{u.first_name or ''} {u.last_name or ''}".strip()
        display = f"{real} ({u.screen_name})" if u.screen_name else real
        bal_rows += f"<tr><td>{display}</td><td style='text-align:right;color:{colour};font-weight:bold'>${bal:.2f}</td></tr>\n"

    # --- Section 3: Stock Report (low stock first) ---
    products = Products.query.order_by(Products.stock_level.asc(), Products.description).all()
    stock_rows = ""
    for p in products:
        soh = p.stock_level or 0
        if soh <= 3:
            bg = "#fff3cd"
            badge = f"<span style='background:#dc3545;color:white;padding:2px 8px;border-radius:10px;font-size:0.8em;'>LOW</span>"
        elif soh <= 10:
            bg = ""
            badge = ""
        else:
            bg = ""
            badge = ""
        stock_rows += f"<tr style='background:{bg}'><td>{p.description or p.upc_code}</td><td>{p.category or ''}</td><td style='text-align:right;font-weight:bold'>{soh}</td><td>{badge}</td></tr>\n"

    html = f"""
    <div style="font-family:Arial,sans-serif;max-width:700px;margin:0 auto;color:#333;">
        <div style="background:#1a5276;color:white;padding:20px 24px;border-radius:12px 12px 0 0;">
            <h1 style="margin:0;font-size:1.5rem;">Snackshack Daily Report</h1>
            <p style="margin:4px 0 0;opacity:0.85;">{report_date}</p>
        </div>
        <div style="padding:20px 24px;background:#f8f9fa;border:1px solid #ddd;">

            <h2 style="color:#1a5276;border-bottom:2px solid #1a5276;padding-bottom:6px;margin-top:0;">
                Daily Transactions
            </h2>
            <table style="width:100%;border-collapse:collapse;font-size:0.9rem;">
                <thead>
                    <tr style="background:#e9ecef;">
                        <th style="padding:8px;text-align:left;">Time</th>
                        <th style="padding:8px;text-align:left;">Staff</th>
                        <th style="padding:8px;text-align:left;">Product</th>
                        <th style="padding:8px;text-align:right;">Amount</th>
                    </tr>
                </thead>
                <tbody>{tx_rows}</tbody>
                <tfoot>
                    <tr style="background:#e9ecef;font-weight:bold;">
                        <td colspan="3" style="padding:8px;">Total</td>
                        <td style="padding:8px;text-align:right;">${daily_total:.2f}</td>
                    </tr>
                </tfoot>
            </table>

            <h2 style="color:#1a5276;border-bottom:2px solid #1a5276;padding-bottom:6px;margin-top:24px;">
                Staff Balances
            </h2>
            <table style="width:100%;border-collapse:collapse;font-size:0.9rem;">
                <thead>
                    <tr style="background:#e9ecef;">
                        <th style="padding:8px;text-align:left;">Name</th>
                        <th style="padding:8px;text-align:right;">Balance</th>
                    </tr>
                </thead>
                <tbody>{bal_rows}</tbody>
            </table>

            <h2 style="color:#1a5276;border-bottom:2px solid #1a5276;padding-bottom:6px;margin-top:24px;">
                Stock Report
            </h2>
            <table style="width:100%;border-collapse:collapse;font-size:0.9rem;">
                <thead>
                    <tr style="background:#e9ecef;">
                        <th style="padding:8px;text-align:left;">Product</th>
                        <th style="padding:8px;text-align:left;">Category</th>
                        <th style="padding:8px;text-align:right;">Stock</th>
                        <th style="padding:8px;"></th>
                    </tr>
                </thead>
                <tbody>{stock_rows}</tbody>
            </table>

            <p style="margin-top:24px;font-size:0.8rem;color:#999;text-align:center;">
                - Claudes Snackshack -
            </p>
        </div>
    </div>"""
    return html

def send_nightly_report(app):
    """Send the nightly report email to all super admins."""
    with app.app_context():
        admins = Users.query.filter_by(is_super_admin=True).all()
        recipients = [a.email for a in admins if a.email]
        if not recipients:
            return False

        smtp_host = os.environ.get('SMTP_HOST', 'mail.smtp2go.com')
        smtp_port = int(os.environ.get('SMTP_PORT', 2525))
        smtp_user = os.environ.get('SMTP_USER', '')
        smtp_pass = os.environ.get('SMTP_PASS', '')
        smtp_from = os.environ.get('SMTP_FROM', smtp_user)
        if not smtp_user or not smtp_pass:
            return False

        html = generate_nightly_report_html(app)

        for addr in recipients:
            msg = MIMEMultipart('alternative')
            msg['Subject'] = f"Snackshack Daily Report - {datetime.now().strftime('%d %b %Y')}"
            msg['From'] = smtp_from
            msg['To'] = addr
            msg.attach(MIMEText(html, 'html'))
            try:
                with smtplib.SMTP(smtp_host, smtp_port) as server:
                    server.starttls()
                    server.login(smtp_user, smtp_pass)
                    server.sendmail(smtp_from, addr, msg.as_string())
            except Exception:
                pass
        return True

@main.route('/admin/send-nightly-report')
def trigger_nightly_report():
    if 'user_id' not in session:
        return redirect(url_for('main.index'))
    u = Users.query.get(int(session['user_id']))
    if not u or not u.is_super_admin:
        flash("Super admin access required.", "danger")
        return redirect(url_for('main.index'))
    result = send_nightly_report(current_app._get_current_object())
    if result:
        flash("Daily report emailed!", "success")
    else:
        flash("Could not send report - check SMTP settings and super admin emails.", "warning")
    return redirect(url_for('main.index'))