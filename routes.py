from flask import Blueprint, render_template, request, redirect, url_for, flash, session
from models import db, Users, Products, Transactions, Quick_Items
from datetime import datetime
from decimal import Decimal

main = Blueprint('main', __name__)

# --- HELPER: Handles Login and Purchases ---
def process_barcode(barcode):
    barcode = barcode.strip()
    
    # 1. Login Logic: Find user by Card_ID
    user = Users.query.filter_by(card_id=barcode).first()
    if user:
        session['user_id'] = int(user.user_id)
        user.last_seen = datetime.utcnow()
        db.session.commit()
        return True

    # 2. Purchase Logic: Requires active session
    if 'user_id' in session:
        product = Products.query.filter_by(upc_code=barcode).first()
        if product:
            u = Users.query.get(int(session['user_id']))
            # Decimal math for financial accuracy
            price = Decimal(str(product.price or 0.0))
            u.balance = Decimal(str(u.balance or 0.0)) - price
            
            # Record the purchase linking User and Product
            new_t = Transactions(
                user_id=u.user_id, 
                upc_code=product.upc_code, 
                amount=price,
                transaction_date=datetime.utcnow()
            )
            db.session.add(new_t)
            db.session.commit()
            return True
    return False

# --- THE ROUTES ---

@main.route('/')
def index():
    current_user = None
    last_item = None
    if 'user_id' in session:
        current_user = Users.query.get(int(session['user_id']))
        # Fetch the most recent transaction for this user
        last_t = Transactions.query.filter_by(user_id=current_user.user_id).order_by(Transactions.transaction_date.desc()).first()
        if last_t:
            # Map transaction back to product to get the description
            last_item = Products.query.get(last_t.upc_code)

    # Fetch 6x5 grid tiles
    vips = Users.query.order_by(Users.last_seen.desc()).limit(30).all()
    quick_items = Quick_Items.query.all()
    
    return render_template('index.html', 
                         user=current_user, 
                         users=vips, 
                         quick_items=quick_items, 
                         last_item=last_item)

@main.route('/all-staff')
def all_users():
    everyone = Users.query.order_by(Users.first_name.asc()).all()
    return render_template('users.html', users=everyone)

@main.route('/undo')
def undo():
    if 'user_id' in session:
        uid = int(session['user_id'])
        # Find absolute latest transaction to reverse
        lt = Transactions.query.filter_by(user_id=uid).order_by(Transactions.transaction_date.desc()).first()
        if lt:
            u = Users.query.get(uid)
            # Refund balance
            u.balance = Decimal(str(u.balance)) + Decimal(str(lt.amount))
            db.session.delete(lt)
            db.session.commit()
            flash("Purchase Undone.", "info")
    return redirect(url_for('main.index'))

@main.route('/manual/<barcode>')
def manual_add(barcode=None):
    if barcode and barcode not in ['None', '']:
        process_barcode(barcode)
    return redirect(url_for('main.index'))

@main.route('/scan', methods=['POST'])
def scan():
    barcode = request.form.get('barcode')
    if barcode:
        process_barcode(barcode)
    return redirect(url_for('main.index'))

@main.route('/logout')
def logout():
    session.pop('user_id', None)
    return redirect(url_for('main.index'))