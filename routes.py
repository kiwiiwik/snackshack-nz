import csv
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

    return render_template('index.html', 
                         user=current_user, 
                         users=Users.query.order_by(Users.last_seen.desc()).limit(30).all(), 
                         grouped_products=clean_groups, 
                         staff=all_staff, 
                         recent_audits=recent_audits,
                         just_bought=just_bought)

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

@main.route('/admin/get-product/<barcode>')
def get_product(barcode):
    p = Products.query.get(barcode.strip())
    if p:
        return jsonify({
            "found": True, 
            "mfg": p.manufacturer, 
            "desc": p.description, 
            "size": p.size, 
            "price": str(p.price),
            "cat": p.category,
            "soh": p.stock_level
        })
    return jsonify({"found": False})

@main.route('/admin/audit-submit', methods=['POST'])
def audit_submit():
    if 'user_id' not in session: return redirect(url_for('main.index'))
    admin = Users.query.get(int(session['user_id']))
    if not admin or not admin.is_admin: return redirect(url_for('main.index'))

    barcode = request.form.get('barcode').strip()
    category = request.form.get('category', 'Snacks')
    mfg = request.form.get('manufacturer', '').strip()
    desc = request.form.get('description', '').strip()
    size = request.form.get('size', '').strip()
    price_val = Decimal(request.form.get('price', '2.50'))
    count = int(request.form.get('final_count', 0))

    product = Products.query.get(barcode)
    if not product:
        product = Products(upc_code=barcode, manufacturer=mfg, description=desc, 
                           size=size, price=price_val, stock_level=count, 
                           is_quick_item=True, category=category, 
                           last_audited=datetime.utcnow())
        db.session.add(product)
    else:
        product.category = category
        product.manufacturer = mfg
        product.description = desc
        product.size = size
        product.price = price_val
        product.stock_level = count
        product.last_audited = datetime.utcnow()
    
    db.session.commit()
    return redirect(url_for('main.index'))

@main.route('/admin/export-snapshot')
def export_snapshot():
    if 'user_id' not in session: return redirect(url_for('main.index'))
    admin = Users.query.get(int(session['user_id']))
    if not admin or not admin.is_admin: return redirect(url_for('main.index'))

    si = StringIO()
    cw = csv.writer(si)
    cw.writerow(["--- PRODUCTS ---"])
    cw.writerow(["UPC_Code", "Manufacturer", "Description", "Size", "Category", "Price", "Stock_Level"])
    for p in Products.query.all():
        cw.writerow([p.upc_code, p.manufacturer, p.description, p.size, p.category, p.price, p.stock_level])
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