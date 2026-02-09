import csv
import requests
from io import StringIO
from flask import Blueprint, render_template, request, redirect, url_for, flash, session, make_response, jsonify
from models import db, Users, Products, Transactions
from datetime import datetime
from decimal import Decimal
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError

main = Blueprint('main', __name__)

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
            return {"status": "purchased", "description": product.description}
    return {"status": "not_found"}

@main.route('/')
def index():
    current_user = None
    all_staff, recent_audits = None, None
    needs_pin = request.args.get('needs_pin')
    pin_user = Users.query.get(int(needs_pin)) if needs_pin else None

    if 'user_id' in session:
        current_user = Users.query.get(int(session['user_id']))
        if current_user and current_user.is_admin:
            all_staff = Users.query.order_by(Users.first_name).all()
            recent_audits = Products.query.filter(Products.last_audited != None).order_by(Products.last_audited.desc()).limit(5).all()

    cat_order = ["Drinks", "Snacks", "Candy", "Frozen", "Coffee Pods", "Sweepstake Tickets"]
    quick_items = Products.query.filter_by(is_quick_item=True).all()
    grouped = {cat: [] for cat in cat_order}
    for p in quick_items:
        cat = p.category if p.category in grouped else "Snacks"
        grouped[cat].append(p)
    
    return render_template('index.html', user=current_user, users=Users.query.order_by(Users.last_seen.desc()).limit(30).all(), grouped_products={k: v for k, v in grouped.items() if v}, staff=all_staff, recent_audits=recent_audits, just_bought=request.args.get('bought'), needs_pin=needs_pin, pin_user=pin_user)

@main.route('/select_user/<int:user_id>')
def select_user(user_id):
    u = Users.query.get(int(user_id))
    if not u: return redirect(url_for('main.index'))
    if u.pin: return redirect(url_for('main.index', needs_pin=u.user_id))
    session['user_id'] = int(u.user_id)
    u.last_seen = datetime.utcnow()
    db.session.commit()
    return redirect(url_for('main.index'))

@main.route('/admin/users')
def manage_users():
    if 'user_id' not in session: return redirect(url_for('main.index'))
    admin = Users.query.get(int(session['user_id']))
    if not admin or not admin.is_admin: return redirect(url_for('main.index'))
    return render_template('manage_users.html', users=Users.query.order_by(Users.last_name).all())

@main.route('/admin/user/save', methods=['POST'])
def save_user():
    if 'user_id' not in session: return redirect(url_for('main.index'))
    uid = request.form.get('user_id')
    card_id = request.form.get('card_id').strip()
    user = Users.query.get(int(uid)) if uid else Users(card_id=card_id)
    if not uid: db.session.add(user)
    user.first_name, user.last_name, user.email = request.form.get('first_name'), request.form.get('last_name'), request.form.get('email')
    user.is_admin = 'is_admin' in request.form
    db.session.commit()
    return redirect(url_for('main.manage_users'))

@main.route('/admin/user/delete/<int:user_id>')
def delete_user(user_id):
    if 'user_id' not in session: return redirect(url_for('main.index'))
    if int(session['user_id']) == user_id: return redirect(url_for('main.manage_users'))
    user = Users.query.get(user_id)
    if user:
        try:
            db.session.delete(user)
            db.session.commit()
        except IntegrityError:
            db.session.rollback()
            flash("User has transaction history; cannot delete.", "danger")
    return redirect(url_for('main.manage_users'))

@main.route('/admin/monthly_report')
def monthly_report():
    uid = session.get('user_id')
    if not uid: return redirect(url_for('main.index'))
    current_user = Users.query.get(int(uid))
    if not current_user or not current_user.is_admin: return redirect(url_for('main.index'))
    
    ym = request.args.get('month', '').strip()
    today = datetime.utcnow().date()
    if not ym:
        year, month = (today.year - 1, 12) if today.month == 1 else (today.year, today.month - 1)
        ym = f"{year:04d}-{month:02d}"
    
    year, month = int(ym.split('-')[0]), int(ym.split('-')[1])
    start_dt = datetime(year, month, 1)
    end_dt = datetime(year + 1, 1, 1) if month == 12 else datetime(year, month + 1, 1)

    users = Users.query.order_by(Users.last_name.asc()).all()
    rows = []
    for u in users:
        spent = db.session.query(func.sum(Transactions.amount)).filter(Transactions.user_id == u.user_id, Transactions.transaction_date >= start_dt, Transactions.transaction_date < end_dt).scalar() or 0
        rows.append({"user": u, "spent": float(spent), "start_balance": 0, "end_balance": 0, "txs": []})
    
    return render_template("monthly_report.html", rows=rows, selected_month=ym, month_label=start_dt.strftime("%B %Y"), start_iso=start_dt.strftime("%Y-%m-%d"), end_iso=end_dt.strftime("%Y-%m-%d"))

@main.route('/admin/nuke-transactions')
def nuke_transactions():
    if 'user_id' not in session: return redirect(url_for('main.index'))
    admin = Users.query.get(int(session['user_id']))
    if admin and admin.is_admin:
        Transactions.query.delete()
        db.session.commit()
        flash("DATABASE NUKED.", "danger")
    return redirect(url_for('main.index', open_admin=1))

@main.route('/admin/reset-balances')
def reset_balances():
    if 'user_id' not in session: return redirect(url_for('main.index'))
    admin = Users.query.get(int(session['user_id']))
    if admin and admin.is_admin:
        Users.query.update({Users.balance: 0.00})
        db.session.commit()
        flash("Balances reset.", "warning")
    return redirect(url_for('main.index', open_admin=1))

@main.route('/admin/get-product/<barcode>')
def get_product(barcode):
    p = Products.query.get(barcode.strip())
    if p: return jsonify({"found": True, "mfg": p.manufacturer, "desc": p.description, "size": p.size, "price": str(p.price), "cat": p.category, "soh": p.stock_level})
    return jsonify({"found": False})

@main.route('/admin/audit-submit', methods=['POST'])
def audit_submit():
    barcode = request.form.get('barcode', '').strip()
    product = Products.query.get(barcode) or Products(upc_code=barcode)
    if not Products.query.get(barcode): db.session.add(product)
    product.manufacturer, product.description = request.form.get('manufacturer'), request.form.get('description')
    product.stock_level, product.last_audited = int(request.form.get('final_count', 0)), datetime.utcnow()
    db.session.commit()
    return redirect(url_for('main.index', open_admin=1))

@main.route('/admin/clear-history')
def clear_audit_history():
    Products.query.update({Products.last_audited: None})
    db.session.commit()
    return redirect(url_for('main.index', open_admin=1))

@main.route('/admin/products')
def manage_products():
    if 'user_id' not in session: return redirect(url_for('main.index'))
    return render_template('manage_products.html', products=Products.query.order_by(Products.description).all())

@main.route('/admin/product/save', methods=['POST'])
def save_product_manual():
    upc = request.form.get('upc_code', '').strip()
    product = Products.query.get(upc) or Products(upc_code=upc)
    if not Products.query.get(upc): db.session.add(product)
    product.description, product.price = request.form.get('description'), Decimal(request.form.get('price', '0.00'))
    product.category, product.stock_level = request.form.get('category'), int(request.form.get('stock_level', 0))
    product.is_quick_item = 'is_quick_item' in request.form
    db.session.commit()
    return redirect(url_for('main.manage_products'))

@main.route('/admin/product/delete/<upc>')
def delete_product(upc):
    product = Products.query.get(upc)
    if product:
        try:
            db.session.delete(product); db.session.commit()
        except:
            db.session.rollback(); flash("Cannot delete history item.", "danger")
    return redirect(url_for('main.manage_products'))

@main.route('/undo')
def undo():
    if 'user_id' in session:
        uid = int(session['user_id'])
        lt = Transactions.query.filter_by(user_id=uid).order_by(Transactions.transaction_date.desc()).first()
        if lt:
            u, p = Users.query.get(uid), Products.query.get(lt.upc_code)
            u.balance += lt.amount
            if p: p.stock_level += 1
            db.session.delete(lt); db.session.commit()
    return redirect(url_for('main.index'))

@main.route('/manual/<barcode>')
def manual_add(barcode=None):
    res = process_barcode(barcode)
    return redirect(url_for('main.index', bought=res.get("description"))) if res.get("status") == "purchased" else redirect(url_for('main.index'))

@main.route('/logout')
def logout():
    session.pop('user_id', None)
    return redirect(url_for('main.index'))

@main.route('/pin_verify', methods=['POST'])
def pin_verify():
    uid, pin = request.form.get('user_id'), request.form.get('pin', '').strip()
    u = Users.query.get(int(uid))
    if u and str(u.pin) == pin:
        session['user_id'] = u.user_id
        return redirect(url_for('main.index'))
    return redirect(url_for('main.index', needs_pin=uid))

@main.route('/pin_set', methods=['POST'])
def pin_set():
    u = Users.query.get(int(session['user_id']))
    if u: u.pin = request.form.get('pin'); db.session.commit()
    return redirect(url_for('main.index'))

@main.route('/pin_clear', methods=['POST'])
def pin_clear():
    u = Users.query.get(int(session['user_id']))
    if u: u.pin = None; db.session.commit()
    return redirect(url_for('main.index'))

@main.route('/scan', methods=['POST'])
def scan():
    res = process_barcode(request.form.get('barcode', '').strip())
    return redirect(url_for('main.index', bought=res.get('description'))) if res.get('status') == 'purchased' else redirect(url_for('main.index'))