import os
from flask import Flask
from models import db
from routes import main

app = Flask(__name__)

# Use Environment Variable for security in Azure
app.secret_key = os.environ.get('FLASK_SECRET_KEY', 'dev-key-default-123')

# Database Connection Logic
db_user = os.environ.get('DB_USER')
db_pass = os.environ.get('DB_PASS')
db_host = os.environ.get('DB_HOST')
db_name = os.environ.get('DB_NAME')

app.config['SQLALCHEMY_DATABASE_URI'] = f"mssql+pyodbc://{db_user}:{db_pass}@{db_host}/{db_name}?driver=ODBC+Driver+18+for+SQL+Server"
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db.init_app(app)
app.register_blueprint(main)

if __name__ == '__main__':
    app.run()