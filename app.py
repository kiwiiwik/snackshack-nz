import os
import urllib.parse
from flask import Flask
from dotenv import load_dotenv
from models import db
from routes import main

# --- BUILD CONFIG ---
BUILD_NUMBER = "v1.0.6 (Azure Live)" 

load_dotenv()

def create_app():
    app = Flask(__name__)

    # This looks for the key in Azure. 
    # If it can't find it, it uses 'dev-key-only' as a backup for your local PC.
    app.secret_key = os.environ.get('FLASK_SECRET_KEY', 'dev-key-only')
    
    # 1. Database Config
    # We use os.getenv to safely get the string from Azure or your local .env file
    conn_str = os.environ.get('AZURE_SQL_CONNECTION_STRING')
    
    if conn_str:
        params = urllib.parse.quote_plus(conn_str)
        app.config['SQLALCHEMY_DATABASE_URI'] = "mssql+pyodbc:///?odbc_connect=%s" % params
    else:
        # Fallback/Safety if variable is missing
        print("WARNING: AZURE_SQL_CONNECTION_STRING not found.")
        
    app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

    # 2. Initialize Plugins
    db.init_app(app)

    # 3. Register Routes
    app.register_blueprint(main)

    # 4. Inject Build Number into ALL templates automatically
    @app.context_processor
    def inject_build():
        return dict(build_number=BUILD_NUMBER)

    # 5. Cache Buster (Important for PIN/Logout updates)
    @app.after_request
    def add_header(response):
        response.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate, max-age=0'
        return response

    return app

# --- PRODUCTION ENTRY POINT ---
# CRITICAL FIX: This variable must be GLOBAL (outside the if-statement below)
# Azure looks for this specific variable named 'app'
app = create_app()

if __name__ == '__main__':
    # This only runs when you start it locally on your machine
    app.run(host='0.0.0.0', port=5000, debug=True)