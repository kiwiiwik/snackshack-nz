from flask_sqlalchemy import SQLAlchemy
from datetime import datetime

db = SQLAlchemy()

class Users(db.Model):
    __tablename__ = 'Users' # Matches your SQL table exactly
    user_id = db.Column(db.Integer, primary_key=True)
    first_name = db.Column(db.String(50))
    last_name = db.Column(db.String(50))
    card_id = db.Column(db.String(50), unique=True)
    balance = db.Column(db.Numeric(10, 2), default=0.00)
    last_seen = db.Column(db.DateTime, default=datetime.utcnow)

class Products(db.Model):
    __tablename__ = 'Products'
    upc_code = db.Column(db.String(50), primary_key=True)
    description = db.Column(db.String(100))
    price = db.Column(db.Numeric(10, 2))
    image_url = db.Column(db.String(255))

class Transactions(db.Model):
    __tablename__ = 'Transactions'
    transaction_id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('Users.user_id'))
    upc_code = db.Column(db.String(50), db.ForeignKey('Products.upc_code'))
    amount = db.Column(db.Numeric(10, 2))
    transaction_date = db.Column(db.DateTime, default=datetime.utcnow)

class Quick_Items(db.Model):
    __tablename__ = 'Quick_Items'
    # Change 'id' to 'Quick_Item_ID' (or whatever your SQL column is actually named)
    quick_item_id = db.Column('Quick_Item_ID', db.Integer, primary_key=True) 
    label = db.Column(db.String(50))
    barcode_val = db.Column(db.String(50))
    image_url = db.Column(db.String(255))