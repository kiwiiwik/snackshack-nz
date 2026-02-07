from flask_sqlalchemy import SQLAlchemy
from datetime import datetime

db = SQLAlchemy()

class Users(db.Model):
    __tablename__ = 'Users'
    user_id = db.Column('User_ID', db.Integer, primary_key=True)
    first_name = db.Column('First_Name', db.String(50))
    last_name = db.Column('Last_Name', db.String(50))
    card_id = db.Column('Card_ID', db.String(50), unique=True)
    balance = db.Column('Balance', db.Numeric(10, 2), default=0.00)
    last_seen = db.Column('last_seen', db.DateTime, default=datetime.utcnow)
    pin = db.Column('PIN', db.String(4))
    email = db.Column('Email_Address', db.String(100))
    is_admin = db.Column('Is_Admin', db.Boolean, default=False)

class Products(db.Model):
    __tablename__ = 'Products'
    upc_code = db.Column('UPC_Code', db.String(50), primary_key=True)
    manufacturer = db.Column('Manufacturer', db.String(100))
    description = db.Column('Description', db.String(100))
    size = db.Column('Size', db.String(50))
    price = db.Column('Price', db.Numeric(10, 2))
    stock_level = db.Column('Stock_Level', db.Integer, nullable=False, default=0)

class Transactions(db.Model):
    __tablename__ = 'Transactions'
    transaction_id = db.Column('Transaction_ID', db.Integer, primary_key=True)
    user_id = db.Column('User_ID', db.Integer, db.ForeignKey('Users.User_ID'))
    upc_code = db.Column('UPC_Code', db.String(50), db.ForeignKey('Products.UPC_Code'))
    amount = db.Column('Amount', db.Numeric(10, 2))
    transaction_date = db.Column('Transaction_Date', db.DateTime, default=datetime.utcnow)

class Quick_Items(db.Model):
    __tablename__ = 'Quick_Items'
    item_id = db.Column('Item_ID', db.Integer, primary_key=True)
    label = db.Column('Label', db.String(50))
    barcode_val = db.Column('Barcode_Value', db.String(50)) 
    image_url = db.Column('Image_URL', db.String(255))