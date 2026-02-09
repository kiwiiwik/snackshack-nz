import csv
import requests
from io import StringIO
from flask import Blueprint, render_template, request, redirect, url_for, flash, session, make_response, jsonify
from models import db, Users, Products, Transactions
from datetime import datetime
from decimal import Decimal
from sqlalchemy.exc import IntegrityError

main = Blueprint('main', __name__)

def process_barcode(barcode):
    barcode = barcode.strip()
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
            # Hard stop: do not allow purchase when stock is zero (covers direct URL + scan)
            if product.stock_level is not None and product.stock_level <= 0:
                flash(f"Out of stock: {product.description}", "warning")
                return {"status": "out_of_stock", "description": product.description}

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
    
    return render_template('index.html', user=current_user, users=Users.query.order_by(Users.last_seen.desc()).limit(30).all(), grouped_products={k: v for k, v in grouped.items() if v}, staff=all_staff, recent_audits=recent_audits, just_bought=request.args.get('bought'))

@main.route('/admin/nuke-transactions')
def nuke_transactions():
    if 'user_id' not in session: return redirect(url_for('main.index'))
    admin = Users.query.get(int(session['user_id']))
    if admin and admin.is_admin:
        Transactions.query.delete()
        db.session.commit()
        flash("DATABASE NUKED: Transaction history cleared.", "danger")
    return redirect(url_for('main.index', open_admin=1))

@main.route('/admin/reset-balances')
def reset_balances():
    if 'user_id' not in session: return redirect(url_for('main.index'))
    admin = Users.query.get(int(session['user_id']))
    if admin and admin.is_admin:
        Users.query.update({Users.balance: 0.00})
        db.session.commit()
        flash("All account balances reset to $0.00.", "warning")
    return redirect(url_for('main.index', open_admin=1))

@main.route('/admin/get-product/<barcode>')
def get_product(barcode):
    barcode = barcode.strip()
    p = Products.query.get(barcode)
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

@main.route('/admin/audit-submit', methods=['POST'])
def audit_submit():
    if 'user_id' not in session: return redirect(url_for('main.index'))
    barcode = request.form.get('barcode', '').strip()
    mfg, desc, size = request.form.get('manufacturer', '').strip(), request.form.get('description', '').strip(), request.form.get('size', '').strip()
    count = int(request.form.get('final_count', 0))
    product = Products.query.get(barcode)
    if not product:
        product = Products(upc_code=barcode, manufacturer=mfg, description=desc, size=size, price=Decimal('2.50'), stock_level=count, is_quick_item=True, category='Snacks', last_audited=datetime.utcnow())
        db.session.add(product)
    else:
        product.manufacturer, product.description, product.size, product.stock_level, product.last_audited = mfg, desc, size, count, datetime.utcnow()
    db.session.commit()
    return redirect(url_for('main.index', open_admin=1))

@main.route('/admin/clear-history')
def clear_audit_history():
    if 'user_id' not in session: return redirect(url_for('main.index'))
    Products.query.update({Products.last_audited: None})
    db.session.commit()
    return redirect(url_for('main.index', open_admin=1))

@main.route('/admin/products')
def manage_products():
    if 'user_id' not in session: return redirect(url_for('main.index'))
    return render_template('manage_products.html', products=Products.query.order_by(Products.description).all())

@main.route('/admin/product/save', methods=['POST'])
def save_product_manual():
    if 'user_id' not in session: return redirect(url_for('main.index'))
    upc = request.form.get('upc_code', '').strip()
    product = Products.query.get(upc) or Products(upc_code=upc)
    if not Products.query.get(upc): db.session.add(product)
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
        try:
            db.session.delete(product)
            db.session.commit()
            flash("Item deleted.", "info")
        except IntegrityError:
            db.session.rollback()
            flash(f"Constraint Conflict: '{product.description}' is linked to historical sales. Set stock to 0 instead or use NUKE.", "danger")
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
            flash("Purchase Undone.", "info")
    return redirect(url_for('main.index'))

@main.route('/manual/<barcode>')
def manual_add(barcode=None):
    res = process_barcode(barcode)

    if res.get('status') == 'needs_pin':
        return redirect(url_for('main.index', needs_pin=res.get('user_id')))

    if res.get('status') == 'purchased':
        return redirect(url_for('main.index', bought=res.get('description')))

    # includes out_of_stock, not_found, etc (flash handles messaging)
    return redirect(url_for('main.index'))


@main.route('/scan', methods=['POST'])
def scan():
    barcode = request.form.get('barcode', '').strip()
    res = process_barcode(barcode)

    if res.get('status') == 'needs_pin':
        return redirect(url_for('main.index', needs_pin=res.get('user_id')))

    if res.get('status') == 'purchased':
        return redirect(url_for('main.index', bought=res.get('description')))

    return redirect(url_for('main.index'))

@main.route('/logout')
def logout():
    session.pop('user_id', None)
    return redirect(url_for('main.index'))