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
    pin = db.Column('PIN', db.String(64))
    email = db.Column('Email_Address', db.String(100))
    notify_on_purchase = db.Column('Notify_On_Purchase', db.Boolean, default=False)
    phone_number = db.Column('Phone_Number', db.String(20))
    is_admin = db.Column('Is_Admin', db.Boolean, default=False)
    is_super_admin = db.Column('Is_Super_Admin', db.Boolean, default=False)
    avatar = db.Column('Avatar', db.String(50))
    avatar_data = db.Column('Avatar_Data', db.Text)

    def to_dict(self):
        return {
            'user_id': self.user_id,
            'first_name': self.first_name or "",
            'last_name': self.last_name or "",
            'card_id': self.card_id or "",
            'balance': float(self.balance) if self.balance else 0.0,
            'is_admin': self.is_admin,
            'is_super_admin': self.is_super_admin,
            'avatar': self.avatar or ""
        }

class Products(db.Model):
    __tablename__ = 'Products'
    upc_code = db.Column('UPC_Code', db.String(50), primary_key=True)
    manufacturer = db.Column('Manufacturer', db.String(100))
    description = db.Column('Description', db.String(100))
    size = db.Column('Size', db.String(50))
    price = db.Column('Price', db.Numeric(10, 2))
    stock_level = db.Column('Stock_Level', db.Integer, nullable=False, default=0)
    is_quick_item = db.Column('Is_Quick_Item', db.Boolean, default=False)
    image_url = db.Column('Image_URL', db.String(255))
    image_data = db.Column('Image_Data', db.Text)
    last_audited = db.Column('Last_Audited', db.DateTime)
    category = db.Column('Category', db.String(50))

    def to_dict(self):
        return {
            'upc_code': self.upc_code,
            'manufacturer': self.manufacturer or "",
            'description': self.description or "",
            'size': self.size or "",
            'price': float(self.price) if self.price else 0.0,
            'stock_level': self.stock_level or 0,
            'is_quick_item': self.is_quick_item,
            'category': self.category or "Snacks",
            'image_url': self.image_url or ""
        }

class Transactions(db.Model):
    __tablename__ = 'Transactions'
    transaction_id = db.Column('Transaction_ID', db.Integer, primary_key=True)
    user_id = db.Column('User_ID', db.Integer, db.ForeignKey('Users.User_ID'))
    upc_code = db.Column('UPC_Code', db.String(50), db.ForeignKey('Products.UPC_Code'))
    amount = db.Column('Amount', db.Numeric(10, 2))
    transaction_date = db.Column('Transaction_Date', db.DateTime, default=datetime.utcnow)