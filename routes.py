from flask import Blueprint, render_template, request, redirect, url_for, flash, session
from models import db, Users, Products, Transactions, Quick_Items
from datetime import datetime
from decimal import Decimal

main = Blueprint('main', __name__)

def process_barcode(barcode):
    barcode = barcode.strip()
    user = Users.query.filter_by(card_id=barcode).first()
    if user:
        session['user_id'] = int(user.user_id)
        user.last_seen = datetime.utcnow()
        db.session.commit()
        return None # User logged in, no item purchased yet

    if 'user_id' in session:
        product = Products.query.filter_by(upc_code=barcode).first()
        if product:
            u = Users.query.get(int(session['user_id']))
            price = Decimal(str(product.price or 0.0))
            u.balance = Decimal(str(u.balance or 0.0)) - price
            new_t = Transactions(user_id=u.user_id, upc_code=product.upc_code, amount=price)
            db.session.add(new_t)
            db.session.commit()
            return product.description # Return the description for the alert bar
    return None

@main.route('/')
def index():
    current_user = None
    just_bought = request.args.get('bought') # Capture the item name from the URL redirect
    
    if 'user_id' in session:
        current_user = Users.query.get(int(session['user_id']))

    vips = Users.query.order_by(Users.last_seen.desc()).limit(30).all()
    
    # JOIN Quick_Items with Products to get the Full Description and Price
    quick_data = db.session.query(Quick_Items, Products.description, Products.price).join(
        Products, Quick_Items.barcode_val == Products.upc_code
    ).all()
    
    return render_template('index.html', user=current_user, users=vips, quick_items=quick_data, just_bought=just_bought)

@main.route('/manual/<barcode>')
def manual_add(barcode=None):
    bought_item = None
    if barcode: 
        bought_item = process_barcode(barcode)
    # Redirect back to index, carrying the name of the bought item if it exists
    return redirect(url_for('main.index', bought=bought_item))

@main.route('/scan', methods=['POST'])
def scan():
    barcode = request.form.get('barcode')
    bought_item = None
    if barcode: 
        bought_item = process_barcode(barcode)
    return redirect(url_for('main.index', bought=bought_item))

@main.route('/all-staff')
def all_users():
    everyone = Users.query.order_by(Users.first_name.asc()).all()
    return render_template('users.html', users=everyone)

@main.route('/undo')
def undo():
    if 'user_id' in session:
        uid = int(session['user_id'])
        lt = Transactions.query.filter_by(user_id=uid).order_by(Transactions.transaction_date.desc()).first()
        if lt:
            u = Users.query.get(uid)
            u.balance = Decimal(str(u.balance)) + Decimal(str(lt.amount))
            db.session.delete(lt)
            db.session.commit()
            flash("Purchase Undone.", "info")
    return redirect(url_for('main.index'))

@main.route('/logout')
def logout():
    session.pop('user_id', None)
    return redirect(url_for('main.index'))