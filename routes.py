import requests
from flask import Blueprint, render_template, request, redirect, url_for, flash, session, jsonify
from models import db, Users, Products, Transactions
from datetime import datetime
from decimal import Decimal
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
        just_bought=request.args.get('bought'))

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

@main.route('/logout')
def logout(): session.pop('user_id', None); return redirect(url_for('main.index'))

@main.route('/select_user/<int:user_id>')
def select_user(user_id):
    u = Users.query.get(user_id)
    if u and u.pin: return redirect(url_for('main.index', needs_pin=u.user_id))
    if u: session['user_id'] = u.user_id; u.last_seen = datetime.utcnow(); db.session.commit()
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