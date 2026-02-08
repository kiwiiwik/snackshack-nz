import csv
import requests
from io import StringIO
from flask import Blueprint, render_template, request, redirect, url_for, flash, session, make_response, jsonify
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
    recent_audits = None
    just_bought = request.args.get('bought')
    
    if 'user_id' in session:
        current_user = Users.query.get(int(session['user_id']))
        if current_user and current_user.is_admin:
            all_staff = Users.query.order_by(Users.first_name).all()
            recent_audits = Products.query.filter(Products.last_audited != None)\
                                          .order_by(Products.last_audited.desc())\
                                          .limit(5).all()

    cat_order = ["Drinks", "Snacks", "Candy", "Frozen", "Coffee Pods", "Sweepstake Tickets"]
    quick_items = Products.query.filter_by(is_quick_item=True).all()
    grouped_products = {cat: [] for cat in cat_order}
    for p in quick_items:
        cat = p.category if p.category in grouped_products else "Snacks"
        grouped_products[cat].append(p)
    clean_groups = {k: v for k, v in grouped_products.items() if v}

    return render_template('index.html', user=current_user, users=Users.query.order_by(Users.last_seen.desc()).limit(30).all(), grouped_products=clean_groups, staff=all_staff, recent_audits=recent_audits, just_bought=just_bought)

@main.route('/manual/<barcode>')
def manual_add(barcode=None):
    result = process_barcode(barcode)
    if result["status"] == "needs_pin": return redirect(url_for('main.index', needs_pin=result["user_id"]))
    return redirect(url_for('main.index', bought=result.get("description")))

@main.route('/scan', methods=['POST'])
def scan():
    barcode = request.form.get('barcode')
    result = process_barcode(barcode)
    if result["status"] == "needs_pin": return redirect(url_for('main.index', needs_pin=result["user_id"]))
    return redirect(url_for('main.index', bought=result.get("description")))

@main.route('/admin/get-product/<barcode>')
def get_product(barcode):
    barcode = barcode.strip()
    p = Products.query.get(barcode)
    if p: return jsonify({"found": True, "mfg": p.manufacturer, "desc": p.description, "size": p.size, "price": str(p.price), "cat": p.category, "soh": p.stock_level})
    try:
        api_url = f"https://world.openfoodfacts.org/api/v0/product/{barcode}.json"
        response = requests.get(api_url, timeout=5)
        if response.status_code == 200:
            data = response.json()
            if data.get("status") == 1:
                prod = data.get("product", {})
                return jsonify({"found": True, "mfg": prod.get("brands", ""), "desc": prod.get("product_name", ""), "size": prod.get("quantity", ""), "soh": 0})
    except: pass
    return jsonify({"found": False})

@main.route('/admin/audit-submit', methods=['POST'])
def audit_submit():
    if 'user_id' not in session: return redirect(url_for('main.index'))
    barcode = request.form.get('barcode').strip()
    mfg, desc, size = request.form.get('manufacturer', '').strip(), request.form.get('description', '').strip(), request.form.get('size', '').strip()
    count = int(request.form.get('final_count', 0))
    product = Products.query.get(barcode)
    if not product:
        product = Products(upc_code=barcode, manufacturer=mfg, description=desc, size=size, price=Decimal('2.50'), stock_level=count, is_quick_item=True, category='Snacks', last_audited=datetime.utcnow())
        db.session.add(product)
    else:
        product.manufacturer, product.description, product.size, product.stock_level, product.last_audited = mfg, desc, size, count, datetime.utcnow()
    db.session.commit()
    return redirect(url_for('main.index'))

@main.route('/admin/products')
def manage_products():
    if 'user_id' not in session: return redirect(url_for('main.index'))
    return render_template('manage_products.html', products=Products.query.order_by(Products.description).all())

@main.route('/admin/product/save', methods=['POST'])
def save_product_manual():
    if 'user_id' not in session: return redirect(url_for('main.index'))
    upc = request.form.get('upc_code').strip()
    product = Products.query.get(upc)
    if not product:
        product = Products(upc_code=upc)
        db.session.add(product)
    product.manufacturer, product.description, product.size = request.form.get('manufacturer'), request.form.get('description'), request.form.get('size')
    product.price, product.category, product.stock_level = Decimal(request.form.get('price', '0.00')), request.form.get('category'), int(request.form.get('stock_level', 0))
    product.is_quick_item = 'is_quick_item' in request.form
    db.session.commit()
    return redirect(url_for('main.manage_products'))

@main.route('/admin/product/delete/<upc>')
def delete_product(upc):
    if 'user_id' not in session: return redirect(url_for('main.index'))
    product = Products.query.get(upc)
    if product:
        db.session.delete(product)
        db.session.commit()
    return redirect(url_for('main.manage_products'))

@main.route('/undo')
def undo():
    if 'user_id' in session:
        uid = int(session['user_id'])
        lt = Transactions.query.filter_by(user_id=uid).order_by(Transactions.transaction_date.desc()).first()
        if lt:
            u, p = Users.query.get(uid), Products.query.get(lt.upc_code)
            u.balance += Decimal(str(lt.amount))
            if p: p.stock_level += 1
            db.session.delete(lt)
            db.session.commit()
    return redirect(url_for('main.index'))

@main.route('/logout')
def logout():
    session.pop('user_id', None)
    return redirect(url_for('main.index'))