import csv
from io import StringIO
from flask import Blueprint, render_template, request, redirect, url_for, flash, session, make_response
from models import db, Users, Products, Transactions
from datetime import datetime
from decimal import Decimal

main = Blueprint('main', __name__)

def process_barcode(barcode):
    barcode = barcode.strip()
    user = Users.query.filter_by(card_id=barcode).first()
    if user:
        if user.pin:
            return {"status": "needs_pin", "user_id": user.user_id}
        session['user_id'] = int(user.user_id)
        user.last_seen = datetime.utcnow()
        db.session.commit()
        return {"status": "logged_in"}

    if 'user_id' in session:
        product = Products.query.filter_by(upc_code=barcode).first()
        if product:
            u = Users.query.get(int(session['user_id']))
            price = Decimal(str(product.price or 0.0))
            u.balance = Decimal(str(u.balance or 0.0)) - price
            if product.stock_level > 0:
                product.stock_level -= 1
            new_t = Transactions(user_id=u.user_id, upc_code=product.upc_code, amount=price)
            db.session.add(new_t)
            db.session.commit()
            return {"status": "purchased", "description": product.description}
    return {"status": "not_found"}

@main.route('/')
def index():
    current_user = None
    all_staff = None
    all_products = None
    just_bought = request.args.get('bought')
    
    if 'user_id' in session:
        current_user = Users.query.get(int(session['user_id']))
        if current_user and current_user.is_admin:
            all_staff = Users.query.order_by(Users.first_name).all()
            all_products = Products.query.order_by(Products.description).all()

    vips = Users.query.order_by(Users.last_seen.desc()).limit(30).all()
    quick_data = Products.query.filter_by(is_quick_item=True).all()
    
    return render_template('index.html', 
                         user=current_user, 
                         users=vips, 
                         quick_items=quick_data, 
                         staff=all_staff, 
                         products=all_products, 
                         just_bought=just_bought)

@main.route('/admin/export-snapshot')
def export_snapshot():
    if 'user_id' not in session: return redirect(url_for('main.index'))
    admin = Users.query.get(int(session['user_id']))
    if not admin or not admin.is_admin: return redirect(url_for('main.index'))

    si = StringIO()
    cw = csv.writer(si)
    cw.writerow(["--- PRODUCTS ---"])
    cw.writerow(["UPC_Code", "Manufacturer", "Description", "Size", "Price", "Stock_Level"])
    for p in Products.query.all():
        cw.writerow([p.upc_code, p.manufacturer, p.description, p.size, p.price, p.stock_level])
    cw.writerow([])
    cw.writerow(["--- USERS ---"])
    cw.writerow(["Card_ID", "First_Name", "Last_Name", "Balance"])
    for u in Users.query.all():
        cw.writerow([u.card_id, u.first_name, u.last_name, u.balance])

    output = make_response(si.getvalue())
    timestamp = datetime.now().strftime("%Y-%m-%d_%H%M")
    output.headers["Content-Disposition"] = f"attachment; filename=snackshack_snapshot_{timestamp}.csv"
    output.headers["Content-type"] = "text/csv"
    return output

@main.route('/admin/audit-submit', methods=['POST'])
def audit_submit():
    if 'user_id' not in session: return redirect(url_for('main.index'))
    admin = Users.query.get(int(session['user_id']))
    if not admin or not admin.is_admin: return redirect(url_for('main.index'))

    barcode = request.form.get('barcode').strip()
    raw_count = request.form.get('new_count', '0')
    
    # Safety: Default to 0 if input is empty to prevent crash
    try:
        new_count = int(raw_count) if raw_count else 0
    except ValueError:
        new_count = 0

    product = Products.query.get(barcode)
    if product:
        product.stock_level = new_count
        db.session.commit()
        flash(f"Audit Success: {product.description} set to {new_count}.", "success")
    return redirect(url_for('main.index'))

@main.route('/admin/update-stock', methods=['POST'])
def update_stock():
    if 'user_id' not in session: return redirect(url_for('main.index'))
    admin = Users.query.get(int(session['user_id']))
    if not admin or not admin.is_admin: return redirect(url_for('main.index'))

    barcode = request.form.get('barcode').strip()
    qty_str = request.form.get('quantity', '0')
    qty = int(qty_str) if qty_str else 0
    
    product = Products.query.get(barcode)
    if product:
        product.stock_level += qty
        db.session.commit()
        flash(f"Restocked {product.description} (+{qty})", "success")
    return redirect(url_for('main.index'))

@main.route('/verify-pin', methods=['POST'])
def verify_pin():
    user_id = request.form.get('user_id')
    entered_pin = request.form.get('pin')
    user = Users.query.get(int(user_id))
    if user and user.pin == entered_pin:
        session['user_id'] = user.user_id
        user.last_seen = datetime.utcnow()
        db.session.commit()
        return redirect(url_for('main.index'))
    flash("Incorrect PIN.", "danger")
    return redirect(url_for('main.index'))

@main.route('/admin/reset-pin/<int:target_id>')
def admin_reset_pin(target_id):
    if 'user_id' not in session: return redirect(url_for('main.index'))
    admin = Users.query.get(int(session['user_id']))
    if admin and admin.is_admin:
        target_user = Users.query.get(target_id)
        if target_user:
            target_user.pin = None
            db.session.commit()
            flash(f"PIN for {target_user.first_name} cleared.", "success")
    return redirect(url_for('main.index'))

@main.route('/update-pin/<action>', methods=['GET', 'POST'])
def update_pin(action):
    if 'user_id' not in session: return redirect(url_for('main.index'))
    user = Users.query.get(int(session['user_id']))
    if action == 'remove':
        entered_pin = request.form.get('pin')
        if user.pin == entered_pin:
            user.pin = None
            flash("PIN removed.", "info")
    elif action == 'set':
        new_pin = request.args.get('pin')
        if new_pin and len(new_pin) == 4:
            user.pin = new_pin
            flash("PIN set successfully!", "success")
    db.session.commit()
    return redirect(url_for('main.index'))

@main.route('/manual/<barcode>')
def manual_add(barcode=None):
    result = process_barcode(barcode)
    if result["status"] == "needs_pin":
        return redirect(url_for('main.index', needs_pin=result["user_id"]))
    return redirect(url_for('main.index', bought=result.get("description")))

@main.route('/scan', methods=['POST'])
def scan():
    barcode = request.form.get('barcode')
    result = process_barcode(barcode)
    if result["status"] == "needs_pin":
        return redirect(url_for('main.index', needs_pin=result["user_id"]))
    return redirect(url_for('main.index', bought=result.get("description")))

@main.route('/undo')
def undo():
    if 'user_id' in session:
        uid = int(session['user_id'])
        lt = Transactions.query.filter_by(user_id=uid).order_by(Transactions.transaction_date.desc()).first()
        if lt:
            u = Users.query.get(uid)
            p = Products.query.get(lt.upc_code)
            u.balance = Decimal(str(u.balance)) + Decimal(str(lt.amount))
            if p: p.stock_level += 1
            db.session.delete(lt)
            db.session.commit()
            flash("Purchase Undone.", "info")
    return redirect(url_for('main.index'))

@main.route('/logout')
def logout():
    session.pop('user_id', None)
    return redirect(url_for('main.index'))