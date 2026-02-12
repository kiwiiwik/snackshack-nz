# Claudes Snackshack

## What is this?
A staff snack shop kiosk app for a New Zealand workplace. Staff select their name, scan/tap products, and purchases are deducted from their balance. Admins manage products, users, and balances.

## Tech Stack
- **Backend:** Flask (Python 3.10) with Flask-SQLAlchemy
- **Database:** MSSQL (Azure SQL) via pyodbc + ODBC Driver 18
- **Frontend:** Bootstrap 5.3, Font Awesome, vanilla JS
- **Hosting:** Azure App Service (Linux) with Gunicorn
- **Email:** SMTP2Go for purchase notifications

## Project Structure
```
app.py              - Flask app init, DB config, blueprint registration
routes.py           - All route handlers (~230 lines)
models.py           - SQLAlchemy models: Users, Products, Transactions
templates/
  index.html        - Main kiosk (user selection + storefront + modals)
  manage_products.html - Admin product CRUD
  manage_users.html    - Admin team management (add/edit/delete users, top-ups)
  monthly_report.html  - Monthly spending report
static/images/      - Product images (uploaded via admin)
```

## Key Features
- **User selection:** A-Z jump bar for 80+ staff, tap to log in
- **PIN security:** Users can set/clear a 4-digit PIN on their account
- **Barcode scanning:** Hidden form auto-captures scanner input
- **Product storefront:** Categorised products (Drinks, Snacks, Candy, Frozen, Coffee Pods, Sweepstake Tickets)
- **Email notifications:** Users opt in to receive email on every purchase (via SMTP2Go)
- **Admin Switchboard:** Products, Team management, Monthly Report, Reset Balances, Nuke History
- **Auto-logout:** 15-second inactivity timer (pauses when modals are open)

## Database Tables
- **Users** - User_ID, First_Name, Last_Name, Card_ID, Balance, last_seen, PIN, Email_Address, Notify_On_Purchase, Is_Admin
- **Products** - UPC_Code, Manufacturer, Description, Size, Price, Stock_Level, Is_Quick_Item, Image_URL, Last_Audited, Category
- **Transactions** - Transaction_ID, User_ID, UPC_Code, Amount, Transaction_Date

## Environment Variables (Azure App Settings)
| Variable | Purpose |
|----------|---------|
| `DB_USER` | MSSQL username |
| `DB_PASS` | MSSQL password |
| `DB_HOST` | MSSQL host |
| `DB_NAME` | Database name |
| `FLASK_SECRET_KEY` | Flask session secret |
| `SMTP_HOST` | SMTP2Go host (`mail.smtp2go.com`) |
| `SMTP_PORT` | SMTP port (`2525`) |
| `SMTP_USER` | SMTP2Go username |
| `SMTP_PASS` | SMTP2Go password |
| `SMTP_FROM` | Sender email address |

## Recent History
- **v1.6.6** - Added email settings + purchase notifications via SMTP2Go
- **v1.6.5** - Restored Admin Switchboard, Team Management, PIN toggle, Monthly Report (lost in v1.5.0 simplification)
- **v1.5.0** - Major simplification that accidentally removed admin/user management features
- Product images, A-Z user list, touch-optimised UI added across v1.5.x-v1.6.x

## Current Version
Kiosk v1.6.6 / Manager v1.6.5
