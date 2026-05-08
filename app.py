from flask import Flask, render_template, render_template_string, request, redirect, session, jsonify
import sqlite3
from flask import g
from decimal import Decimal
from functools import wraps
from datetime import datetime, timedelta
import logging
import os

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'golden-hearts-secret-key-2024')

# Logging configuration
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('pos_system.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

DATABASE = 'pos.db'

def get_db():
    db = getattr(g, '_database', None)
    if db is None:
        db = g._database = sqlite3.connect(DATABASE)
        db.row_factory = sqlite3.Row
    return db

@app.teardown_appcontext
def close_connection(exception):
    db = getattr(g, '_database', None)
    if db is not None:
        db.close()

_schema_ready = False

# ============ CONSTANTS ============
LOUNGE_NAME = "Golden Hearts Lounge"
PAYBILL_NUMBER = "522533"
ACCOUNT_NUMBER = "6085248"

# ============ DECORATORS ============
def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user' not in session:
            return redirect('/')
        return f(*args, **kwargs)
    return decorated_function

def admin_only(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if session.get('role') != 'admin':
            logger.warning(f"Unauthorized admin access attempt by {session.get('user', 'Unknown')}")
            return jsonify({"error": "❌ Admin access only"}), 403
        return f(*args, **kwargs)
    return decorated_function

# ============ UTILITIES ============
def as_decimal(value):
    """Convert value to Decimal safely"""
    if isinstance(value, Decimal):
        return value
    if value is None:
        return Decimal("0")
    return Decimal(str(value))

def ensure_activity_log_table():
    """Create activity_logs table if it doesn't exist"""
    try:
        cur = get_db().cursor()
        cur.execute("""
            CREATE TABLE IF NOT EXISTS activity_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_name VARCHAR(100) NOT NULL,
                role VARCHAR(20) NOT NULL,
                action VARCHAR(50) NOT NULL,
                room_id INT NULL,
                item_name VARCHAR(150) NULL,
                quantity INT NOT NULL DEFAULT 0,
                amount DECIMAL(10,2) NOT NULL DEFAULT 0,
                details TEXT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        get_db().commit()
    except Exception as e:
        get_db().rollback()

def ensure_receipt_tables():
    """Create receipt tables if they don't exist"""
    try:
        cur = get_db().cursor()
        cur.execute("""
            CREATE TABLE IF NOT EXISTS receipts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                receipt_no VARCHAR(30) DEFAULT NULL UNIQUE,
                order_id INT NULL,
                room_id INT NULL,
                amount DECIMAL(10,2) NOT NULL DEFAULT 0,
                method VARCHAR(50) NOT NULL,
                waiter_name VARCHAR(100) DEFAULT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS receipt_items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                receipt_id INT NOT NULL,
                item_name VARCHAR(150) NOT NULL,
                quantity INT NOT NULL,
                unit_price DECIMAL(10,2) NOT NULL,
                line_total DECIMAL(10,2) NOT NULL,
                FOREIGN KEY (receipt_id) REFERENCES receipts(id) ON DELETE CASCADE
            )
        """)
        get_db().commit()
    except Exception as e:
        get_db().rollback()

def ensure_walkin_support():
    """Ensure database supports walk-in orders"""
    global _schema_ready
    if _schema_ready:
        return
    
    ensure_activity_log_table()
    ensure_receipt_tables()
    _schema_ready = True

def log_action(user_name, role, action, room_id=None, item_name=None, quantity=0, amount=0, details=None):
    """Log user actions for audit trail"""
    try:
        ensure_activity_log_table()
        cur = get_db().cursor()
        cur.execute("""
            INSERT INTO activity_logs
                (user_name, role, action, room_id, item_name, quantity, amount, details)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (user_name, role, action, room_id, item_name, quantity, amount, details))
        get_db().commit()
        logger.info(f"[{user_name}] {action}: {details}")
    except Exception as e:
        logger.error(f"Error logging action: {e}")
        get_db().rollback()

def save_receipt(order_id, room_id, amount, method):
    """Save receipt with all order items"""
    try:
        ensure_receipt_tables()
        cur = get_db().cursor()

        if order_id is not None:
            cur.execute("""
                SELECT drinks.name, order_items.quantity, order_items.price
                FROM order_items
                JOIN drinks ON drinks.id = order_items.drink_id
                WHERE order_items.order_id=?
            """, (order_id,))
            items = cur.fetchall()
        else:
            items = []

        cur.execute("""
            INSERT INTO receipts (receipt_no, order_id, room_id, amount, method, waiter_name)
            VALUES (NULL, ?, ?, ?, ?, ?)
        """, (order_id, room_id, amount, method, session.get('user', 'Unknown')))

        receipt_id = cur.lastrowid
        receipt_no = f"GHL-{receipt_id:06d}"
        cur.execute("UPDATE receipts SET receipt_no=? WHERE id=?", (receipt_no, receipt_id))

        for item_name, qty, unit_price in items:
            line_total = as_decimal(qty) * as_decimal(unit_price)
            cur.execute("""
                INSERT INTO receipt_items (receipt_id, item_name, quantity, unit_price, line_total)
                VALUES (?, ?, ?, ?, ?)
            """, (receipt_id, item_name, qty, unit_price, line_total))

        get_db().commit()
        logger.info(f"Receipt {receipt_no} created for amount {amount}")
        return receipt_id
    except Exception as e:
        logger.error(f"Error saving receipt: {e}")
        get_db().rollback()
        return None

def build_receipt_context(receipt_id):
    """Build receipt data for display"""
    try:
        ensure_receipt_tables()
        cur = get_db().cursor()

        cur.execute("""
            SELECT r.id, r.receipt_no, r.order_id, r.room_id, r.amount, r.method,
                   r.waiter_name, r.created_at, rooms.room_number
            FROM receipts r
            LEFT JOIN rooms ON rooms.id = r.room_id
            WHERE r.id=?
        """, (receipt_id,))
        receipt = cur.fetchone()

        if not receipt:
            return None

        cur.execute("""
            SELECT item_name, quantity, unit_price, line_total
            FROM receipt_items
            WHERE receipt_id=?
            ORDER BY id ASC
        """, (receipt_id,))
        items = cur.fetchall()

        room_label = "Walk-in" if receipt[3] is None else (receipt[8] if receipt[8] else f"Room {receipt[3]}")

        return receipt, items, room_label
    except Exception as e:
        logger.error(f"Error building receipt context: {e}")
        return None

# ============ GLOBAL CONTEXT ============
@app.context_processor
def inject_user():
    return {
        'role': session.get('role'),
        'username': session.get('user'),
        'lounge_name': LOUNGE_NAME,
        'mpesa_paybill': PAYBILL_NUMBER
    }

# ============ LOGIN ================
@app.route('/', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        pin = request.form['pin']
        cur = get_db().cursor()
        cur.execute("SELECT * FROM users WHERE pin=?", (pin,))
        user = cur.fetchone()

        if user:
            session['user'] = user[1]
            session['role'] = user[3]
            log_action(user[1], user[3], 'LOGIN', details='Logged into the POS')
            return redirect('/dashboard')

    return render_template('login.html')

# ============ LOGOUT ================
@app.route('/logout')
@login_required
def logout():
    user = session.get('user')
    log_action(user, session.get('role'), 'LOGOUT', details='Logged out of POS')
    session.clear()
    return redirect('/')

# ============ DASHBOARD ============
@app.route('/dashboard')
@login_required
def dashboard():
    cur = get_db().cursor()
    cur.execute("SELECT id, room_number, type, price, status FROM rooms ORDER BY id ASC")
    rooms = cur.fetchall()
    
    # Get stats
    cur.execute("SELECT COUNT(*) FROM rooms WHERE status='occupied'")
    occupied_count = cur.fetchone()[0] or 0
    
    # ✅ FIXED: Use DATE('now') instead of CURDATE()
    cur.execute("""
        SELECT COALESCE(SUM(amount), 0) 
        FROM receipts 
        WHERE DATE(created_at) = DATE('now')
    """)
    today_revenue = cur.fetchone()[0] or 0
    
    log_action(session.get('user'), session.get('role'), 'VIEW_DASHBOARD', details='Viewed dashboard')
    
    return render_template('dashboard.html', rooms=rooms, occupied_count=occupied_count, today_revenue=today_revenue)

# ============ BOOK ROOM ============
@app.route('/book/<int:id>')
@login_required
def book_room(id):
    cur = get_db().cursor()
    cur.execute("SELECT room_number FROM rooms WHERE id=?", (id,))
    room = cur.fetchone()

    if not room:
        return "❌ Room not found"

    cur.execute("UPDATE rooms SET status='occupied' WHERE id=?", (id,))
    get_db().commit()

    room_name = room[0] if room else f"Room {id}"
    log_action(
        session.get('user', 'Unknown'),
        session.get('role', 'unknown'),
        'BOOK_ROOM',
        room_id=id,
        item_name=room_name,
        details=f"Booked room {room_name}"
    )
    return redirect('/dashboard')

# ============ RELEASE ROOM ============
@app.route('/release/<int:id>')
@login_required
def release_room(id):
    if session.get('role') != 'admin':
        return "❌ Admin only"

    cur = get_db().cursor()
    cur.execute("SELECT room_number FROM rooms WHERE id=?", (id,))
    room = cur.fetchone()

    cur.execute("UPDATE rooms SET status='available' WHERE id=?", (id,))
    get_db().commit()

    room_name = room[0] if room else f"Room {id}"
    log_action(
        session.get('user', 'Unknown'),
        session.get('role', 'unknown'),
        'RELEASE_ROOM',
        room_id=id,
        item_name=room_name,
        details=f"Released room {room_name}"
    )
    return redirect('/dashboard')

# ============ ORDER (ROOM) ============
@app.route('/order/<int:room_id>', methods=['GET', 'POST'])
@login_required
def order(room_id):
    cur = get_db().cursor()

    cur.execute("SELECT status, room_number FROM rooms WHERE id=?", (room_id,))
    room = cur.fetchone()

    if not room:
        return "❌ Room not found"

    if room[0] != 'occupied':
        return "❌ Please book the room first"

    cur.execute("""
        SELECT id FROM orders
        WHERE room_id=?
        ORDER BY id DESC LIMIT 1
    """, (room_id,))
    order = cur.fetchone()

    if not order:
        cur.execute("INSERT INTO orders (room_id) VALUES (?)", (room_id,))
        get_db().commit()
        cur.execute("""
            SELECT id FROM orders
            WHERE room_id=?
            ORDER BY id DESC LIMIT 1
        """, (room_id,))
        order = cur.fetchone()

    order_id = order[0]

    if request.method == 'POST':
        drink_id = request.form['drink']
        qty = int(request.form['qty'])

        cur.execute("SELECT name, price FROM drinks WHERE id=?", (drink_id,))
        drink = cur.fetchone()

        if not drink:
            return "❌ Drink removed. Refresh."

        drink_name, price = drink
        line_total = as_decimal(price) * as_decimal(qty)

        cur.execute("""
            INSERT INTO order_items (order_id, drink_id, quantity, price)
            VALUES (?,?,?,?)
        """, (order_id, drink_id, qty, price))
        get_db().commit()

        log_action(
            session.get('user', 'Unknown'),
            session.get('role', 'unknown'),
            'ADD_DRINK',
            room_id=room_id,
            item_name=drink_name,
            quantity=qty,
            amount=line_total,
            details=f"Added {qty}x {drink_name} to {room[1]}"
        )

    cur.execute("SELECT id, name, price, category FROM drinks ORDER BY id ASC")
    drinks = cur.fetchall()

    cur.execute("""
        SELECT drinks.name, order_items.quantity, order_items.price
        FROM order_items
        JOIN drinks ON drinks.id = order_items.drink_id
        WHERE order_items.order_id=?
    """, (order_id,))
    items = cur.fetchall()

    current_total = sum(
        (as_decimal(item[1]) * as_decimal(item[2]) for item in items),
        Decimal("0")
    )

    return render_template('order.html', drinks=drinks, items=items, room_id=room_id, room_number=room[1], current_total=current_total)

# ============ WALK-IN ORDER ============
@app.route('/walkin/order', methods=['GET', 'POST'])
@login_required
def walkin_order():
    ensure_walkin_support()
    cur = get_db().cursor()

    order_id = session.get('walkin_order_id')
    if order_id:
        cur.execute("SELECT id FROM orders WHERE id=? AND room_id IS NULL", (order_id,))
        if not cur.fetchone():
            order_id = None
            session.pop('walkin_order_id', None)

    if not order_id:
        cur.execute("INSERT INTO orders (room_id) VALUES (NULL)")
        get_db().commit()
        order_id = cur.lastrowid
        session['walkin_order_id'] = order_id

        log_action(
            session.get('user', 'Unknown'),
            session.get('role', 'unknown'),
            'WALKIN_START',
            details=f"Started walk-in order #{order_id}"
        )

    if request.method == 'POST':
        drink_id = request.form['drink']
        qty = int(request.form['qty'])

        cur.execute("SELECT name, price FROM drinks WHERE id=?", (drink_id,))
        drink = cur.fetchone()

        if not drink:
            return "❌ Item removed. Refresh."

        item_name, price = drink
        line_total = as_decimal(price) * as_decimal(qty)

        cur.execute("""
            INSERT INTO order_items (order_id, drink_id, quantity, price)
            VALUES (?,?,?,?)
        """, (order_id, drink_id, qty, price))
        get_db().commit()

        log_action(
            session.get('user', 'Unknown'),
            session.get('role', 'unknown'),
            'ADD_DRINK',
            room_id=None,
            item_name=item_name,
            quantity=qty,
            amount=line_total,
            details=f"Walk-in: Added {qty}x {item_name}"
        )

    cur.execute("SELECT id, name, price, category FROM drinks ORDER BY id ASC")
    drinks = cur.fetchall()

    cur.execute("""
        SELECT drinks.name, order_items.quantity, order_items.price
        FROM order_items
        JOIN drinks ON drinks.id = order_items.drink_id
        WHERE order_items.order_id=?
    """, (order_id,))
    items = cur.fetchall()

    current_total = sum(
        (as_decimal(item[1]) * as_decimal(item[2]) for item in items),
        Decimal("0")
    )

    return render_template_string("""
<!DOCTYPE html>
<html>
<head>
    <title>Walk-in Order</title>
    <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css" rel="stylesheet">
</head>
<body>
<div class="container py-4">
    <div class="d-flex justify-content-between align-items-center mb-3">
        <h3>🍹 Walk-in Order</h3>
        <a href="/dashboard" class="btn btn-secondary">← Back</a>
    </div>
    <div class="card p-4 mb-4">
        <form method="POST" class="row g-3">
            <div class="col-md-5">
                <label class="form-label">Select Item</label>
                <select name="drink" class="form-select" required>
                    <option value="">-- Select Item --</option>
                    {% for d in drinks %}
                    <option value="{{ d[0] }}">{{ d[1] }} - Ksh {{ "%.2f"|format(d[2]) }}</option>
                    {% endfor %}
                </select>
            </div>
            <div class="col-md-3">
                <label class="form-label">Quantity</label>
                <input type="number" name="qty" class="form-control" min="1" value="1" required>
            </div>
            <div class="col-md-4">
                <button class="btn btn-success w-100">➕ Add Item</button>
            </div>
        </form>
    </div>
    <div class="card p-4">
        <h5>📋 Current Items</h5>
        <table class="table table-sm">
            <thead>
                <tr>
                    <th>Item</th>
                    <th class="text-center">Qty</th>
                    <th class="text-end">Price</th>
                    <th class="text-end">Total</th>
                </tr>
            </thead>
            <tbody>
                {% for item in items %}
                <tr>
                    <td>{{ item[0] }}</td>
                    <td class="text-center">{{ item[1] }}</td>
                    <td class="text-end">Ksh {{ "%.2f"|format(item[2]) }}</td>
                    <td class="text-end">Ksh {{ "%.2f"|format(item[1] * item[2]) }}</td>
                </tr>
                {% endfor %}
            </tbody>
        </table>
        <hr>
        <div class="d-flex justify-content-between align-items-center">
            <h5>💰 Total: Ksh {{ "%.2f"|format(current_total) }}</h5>
            {% if items|length > 0 %}
            <a href="/walkin/bill/{{ order_id }}" class="btn btn-primary">💳 Proceed to Bill</a>
            {% endif %}
        </div>
    </div>
</div>
</body>
</html>
    """,
        drinks=drinks,
        items=items,
        order_id=order_id,
        current_total=current_total
    )

# ============ WALK-IN BILL ============
@app.route('/walkin/bill/<int:order_id>')
@login_required
def walkin_bill(order_id):
    ensure_walkin_support()
    cur = get_db().cursor()

    cur.execute("""
        SELECT id FROM orders
        WHERE id=? AND room_id IS NULL
    """, (order_id,))
    order = cur.fetchone()

    if not order:
        return "❌ Walk-in order not found"

    cur.execute("""
        SELECT drinks.name, order_items.quantity, order_items.price
        FROM order_items
        JOIN drinks ON drinks.id = order_items.drink_id
        WHERE order_items.order_id=?
    """, (order_id,))
    items = cur.fetchall()

    total = sum(
        (as_decimal(item[1]) * as_decimal(item[2]) for item in items),
        Decimal("0")
    )

    log_action(
        session.get('user'),
        session.get('role'),
        'VIEW_WALKIN_BILL',
        details=f"Viewed walk-in bill for order #{order_id}"
    )

    return render_template_string("""
<!DOCTYPE html>
<html>
<head>
    <title>Walk-in Bill</title>
    <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css" rel="stylesheet">
</head>
<body>
<div class="container py-4">
    <div class="d-flex justify-content-between align-items-center mb-3">
        <h3>💳 Walk-in Bill</h3>
        <a href="/dashboard" class="btn btn-secondary">← Back</a>
    </div>
    <div class="card p-4 mb-4">
        <h5>📋 Items Summary</h5>
        <table class="table table-sm">
            <thead>
                <tr>
                    <th>Item</th>
                    <th class="text-center">Qty</th>
                    <th class="text-end">Price</th>
                    <th class="text-end">Total</th>
                </tr>
            </thead>
            <tbody>
                {% for item in items %}
                <tr>
                    <td>{{ item[0] }}</td>
                    <td class="text-center">{{ item[1] }}</td>
                    <td class="text-end">Ksh {{ "%.2f"|format(item[2]) }}</td>
                    <td class="text-end">Ksh {{ "%.2f"|format(item[1] * item[2]) }}</td>
                </tr>
                {% endfor %}
            </tbody>
        </table>
    </div>
    <div class="card p-4">
        <div class="alert alert-info mb-4">
            <h5>💰 Total Due: Ksh {{ "%.2f"|format(total) }}</h5>
        </div>
        <form method="POST" action="/walkin/pay/{{ order_id }}" class="row g-3">
            <div class="col-md-6">
                <label class="form-label">Payment Method</label>
                <select name="method" class="form-select" required>
                    <option value="">-- Select --</option>
                    <option value="Cash">💵 Cash</option>
                    <option value="M-Pesa">📱 M-Pesa</option>
                    <option value="Card">💳 Card</option>
                    <option value="Bank">🏦 Bank Transfer</option>
                </select>
            </div>
            <div class="col-md-6">
                <label class="form-label">Amount Paid</label>
                <input type="number" name="amount" class="form-control" value="{{ total }}" step="0.01" required>
            </div>
            <div class="col-12">
                <button class="btn btn-success w-100">✅ Complete Payment</button>
            </div>
        </form>
    </div>
</div>
</body>
</html>
    """,
        items=items,
        total=total,
        order_id=order_id
    )

# ============ WALK-IN PAYMENT ============
@app.route('/walkin/pay/<int:order_id>', methods=['POST'])
@login_required
def walkin_pay(order_id):
    ensure_walkin_support()
    method = request.form['method']
    amount = as_decimal(request.form['amount'])

    cur = get_db().cursor()

    cur.execute("""
        SELECT id FROM orders WHERE id=? AND room_id IS NULL
    """, (order_id,))
    order = cur.fetchone()

    if not order:
        return "❌ Walk-in order not found"

    cur.execute("""
        INSERT INTO payments (room_id, amount, method)
        VALUES (NULL, ?, ?)
    """, (amount, method))

    receipt_id = save_receipt(order_id, None, amount, method)

    cur.execute("DELETE FROM order_items WHERE order_id=?", (order_id,))
    cur.execute("DELETE FROM orders WHERE id=? AND room_id IS NULL", (order_id,))
    get_db().commit()

    if session.get('walkin_order_id') == order_id:
        session.pop('walkin_order_id', None)

    log_action(
        session.get('user', 'Unknown'),
        session.get('role', 'unknown'),
        'PAYMENT',
        room_id=None,
        amount=amount,
        details=f"Walk-in payment via {method}, Receipt: {receipt_id}"
    )

    return redirect(f'/receipt/{receipt_id}')

# ============ BILL (ROOM) ============
@app.route('/bill/<int:room_id>')
@login_required
def bill(room_id):
    cur = get_db().cursor()

    cur.execute("SELECT status, price, room_number FROM rooms WHERE id=?", (room_id,))
    room = cur.fetchone()

    if not room:
        return "❌ Room not found"

    status, room_price, room_number = room

    if status != 'occupied':
        return "❌ Room not occupied"

    cur.execute("""
        SELECT id FROM orders
        WHERE room_id=?
        ORDER BY id DESC LIMIT 1
    """, (room_id,))
    order = cur.fetchone()

    if not order:
        items = []
        drinks_total = Decimal("0")
    else:
        order_id = order[0]
        cur.execute("""
            SELECT drinks.name, order_items.quantity, order_items.price
            FROM order_items
            JOIN drinks ON drinks.id = order_items.drink_id
            WHERE order_items.order_id=?
        """, (order_id,))
        items = cur.fetchall()
        drinks_total = sum(
            (as_decimal(item[1]) * as_decimal(item[2]) for item in items),
            Decimal("0")
        )

    room_price = as_decimal(room_price)
    total = drinks_total + room_price

    log_action(
        session.get('user'),
        session.get('role'),
        'VIEW_BILL',
        room_id=room_id,
        item_name=room_number,
        amount=total,
        details=f"Viewed bill for {room_number}"
    )

    return render_template(
        'billing.html',
        items=items,
        drinks_total=drinks_total,
        room_price=room_price,
        total=total,
        room_id=room_id,
        room_number=room_number
    )

# ============ PAYMENT (ROOM) ============
@app.route('/pay/<int:room_id>', methods=['POST'])
@login_required
def pay(room_id):

    try:

        method = request.form['method']
        amount = as_decimal(request.form['amount'])

        cur = get_db().cursor()

        # Get latest room order
        cur.execute("""
            SELECT id FROM orders
            WHERE room_id=?
            ORDER BY id DESC LIMIT 1
        """, (room_id,))

        order = cur.fetchone()
        order_id = order[0] if order else None

        # Save payment
        cur.execute("""
            INSERT INTO payments (room_id, amount, method)
            VALUES (?,?,?)
        """, (room_id, amount, method))

        # Create receipt
        receipt_id = save_receipt(order_id, room_id, amount, method)

        # Remove completed order
        if order_id is not None:
            cur.execute("DELETE FROM order_items WHERE order_id=?", (order_id,))
            cur.execute("DELETE FROM orders WHERE id=?", (order_id,))

        # SAFE room fetch
        cur.execute("SELECT room_number FROM rooms WHERE id=?", (room_id,))
        room_data = cur.fetchone()

        if room_data:
            room_num = room_data[0]
        else:
            room_num = f"Room {room_id}"

        # Release room
        cur.execute("UPDATE rooms SET status='available' WHERE id=?", (room_id,))

        get_db().commit()

        # Log activity
        log_action(
            session.get('user', 'Unknown'),
            session.get('role', 'unknown'),
            'PAYMENT',
            room_id=room_id,
            item_name=room_num,
            amount=amount,
            details=f"Payment completed via {method}"
        )

        # SUCCESS PAGE
        return render_template_string("""
        <!DOCTYPE html>
        <html>
        <head>
            <title>Payment Successful</title>
            <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css" rel="stylesheet">
        </head>
        <body>
        <div class="container py-5">
            <div class="text-center">
                <h1 class="text-success mb-4">✅ Payment Successful</h1>
                <p class="fs-5">Thank you for choosing <strong>{{ lounge_name }}</strong></p>
                <div class="btn-group mt-4" role="group">
                    <a href="/receipt/{{ receipt_id }}" class="btn btn-primary">🧾 View Receipt</a>
                    <a href="/dashboard" class="btn btn-success">← Back Dashboard</a>
                </div>
            </div>
        </div>
        </body>
        </html>
        """, receipt_id=receipt_id, lounge_name=LOUNGE_NAME)

    except Exception as e:
        logger.error(f"Payment Error: {e}")
        get_db().rollback()
        return f"❌ Payment processing failed: {str(e)}"

# ============ ADMIN ============
@app.route('/admin')
@login_required
def admin():
    if session.get('role') != 'admin':
        return "❌ Admin only"

    cur = get_db().cursor()
    cur.execute("SELECT * FROM rooms")
    rooms = cur.fetchall()
    cur.execute("SELECT * FROM drinks")
    drinks = cur.fetchall()
    cur.execute("SELECT * FROM users")
    users = cur.fetchall()

    log_action(session.get('user'), session.get('role'), 'VIEW_ADMIN', details='Accessed admin panel')

    return render_template('admin.html', rooms=rooms, drinks=drinks, users=users)

# ============ ROOM CRUD ============
@app.route('/add_room', methods=['GET', 'POST'])
@login_required
def add_room():
    if session.get('role') != 'admin':
        return "❌ Admin only"

    if request.method == 'POST':
        cur = get_db().cursor()
        cur.execute(
            "INSERT INTO rooms (room_number, type, price, status) VALUES (?,?,?,'available')",
            (request.form['name'], request.form['type'], request.form['price'])
        )
        get_db().commit()

        log_action(
            session.get('user', 'Unknown'),
            session.get('role', 'unknown'),
            'ADD_ROOM',
            item_name=request.form['name'],
            details=f"Created new room: {request.form['name']}"
        )
        return redirect('/admin')

    return render_template('edit_room.html', room=None)

@app.route('/edit_room/<int:id>', methods=['GET', 'POST'])
@login_required
def edit_room(id):
    if session.get('role') != 'admin':
        return "❌ Admin only"

    cur = get_db().cursor()

    if request.method == 'POST':
        cur.execute(
            "UPDATE rooms SET room_number=?, type=?, price=? WHERE id=?",
            (request.form['name'], request.form['type'], request.form['price'], id)
        )
        get_db().commit()

        log_action(
            session.get('user', 'Unknown'),
            session.get('role', 'unknown'),
            'EDIT_ROOM',
            room_id=id,
            item_name=request.form['name'],
            details=f"Updated room #{id}"
        )
        return redirect('/admin')

    cur.execute("SELECT * FROM rooms WHERE id=?", (id,))
    room = cur.fetchone()
    return render_template('edit_room.html', room=room)

@app.route('/delete_room/<int:id>')
@login_required
def delete_room(id):
    if session.get('role') != 'admin':
        return "❌ Admin only"

    cur = get_db().cursor()
    cur.execute("SELECT room_number FROM rooms WHERE id=?", (id,))
    room = cur.fetchone()
    
    cur.execute("DELETE FROM rooms WHERE id=?", (id,))
    get_db().commit()

    log_action(
        session.get('user', 'Unknown'),
        session.get('role', 'unknown'),
        'DELETE_ROOM',
        room_id=id,
        item_name=room[0] if room else f"Room {id}",
        details=f"Deleted room #{id}"
    )
    return redirect('/admin')

# ============ DRINK CRUD ============
@app.route('/add_drink', methods=['GET', 'POST'])
@login_required
def add_drink():
    if session.get('role') != 'admin':
        return "❌ Admin only"

    if request.method == 'POST':
        cur = get_db().cursor()
        cur.execute(
            "INSERT INTO drinks (name, price, category) VALUES (?,?,?)",
            (request.form['name'], request.form['price'], request.form['category'])
        )
        get_db().commit()

        log_action(
            session.get('user', 'Unknown'),
            session.get('role', 'unknown'),
            'ADD_DRINK_ADMIN',
            item_name=request.form['name'],
            amount=as_decimal(request.form['price']),
            details=f"Added new item: {request.form['name']} - Ksh {request.form['price']}"
        )
        return redirect('/admin')

    return render_template('add_drink.html')

@app.route('/edit_drink/<int:id>', methods=['GET', 'POST'])
@login_required
def edit_drink(id):
    if session.get('role') != 'admin':
        return "❌ Admin only"

    cur = get_db().cursor()

    if request.method == 'POST':
        cur.execute(
            "UPDATE drinks SET name=?, price=?, category=? WHERE id=?",
            (request.form['name'], request.form['price'], request.form['category'], id)
        )
        get_db().commit()

        log_action(
            session.get('user', 'Unknown'),
            session.get('role', 'unknown'),
            'EDIT_DRINK',
            item_name=request.form['name'],
            amount=as_decimal(request.form['price']),
            details=f"Updated item #{id}: {request.form['name']}"
        )
        return redirect('/admin')

    cur.execute("SELECT * FROM drinks WHERE id=?", (id,))
    drink = cur.fetchone()
    return render_template('edit_drink.html', drink=drink)

@app.route('/delete_drink/<int:id>')
@login_required
def delete_drink(id):
    if session.get('role') != 'admin':
        return "❌ Admin only"

    cur = get_db().cursor()
    cur.execute("SELECT name FROM drinks WHERE id=?", (id,))
    drink = cur.fetchone()
    
    cur.execute("DELETE FROM drinks WHERE id=?", (id,))
    get_db().commit()

    log_action(
        session.get('user', 'Unknown'),
        session.get('role', 'unknown'),
        'DELETE_DRINK',
        item_name=drink[0] if drink else f"Item {id}",
        details=f"Deleted item #{id}"
    )
    return redirect('/admin')

# ============ USER CRUD ============
@app.route('/add_user', methods=['POST'])
@login_required
def add_user():
    if session.get('role') != 'admin':
        return "❌ Admin only"

    cur = get_db().cursor()
    cur.execute(
        "INSERT INTO users (name, pin, role) VALUES (?,?, 'waiter')",
        (request.form['name'], request.form['pin'])
    )
    get_db().commit()

    log_action(
        session.get('user', 'Unknown'),
        session.get('role', 'unknown'),
        'ADD_USER',
        item_name=request.form['name'],
        details=f"Created new waiter account: {request.form['name']}"
    )
    return redirect('/admin')

@app.route('/delete_user/<int:id>')
@login_required
def delete_user(id):
    if session.get('role') != 'admin':
        return "❌ Admin only"

    cur = get_db().cursor()
    cur.execute("SELECT name FROM users WHERE id=?", (id,))
    user = cur.fetchone()
    
    cur.execute("DELETE FROM users WHERE id=?", (id,))
    get_db().commit()

    log_action(
        session.get('user', 'Unknown'),
        session.get('role', 'unknown'),
        'DELETE_USER',
        item_name=user[0] if user else f"User {id}",
        details=f"Deleted user account: {user[0] if user else f'User {id}'}"
    )
    return redirect('/admin')

# ============ RECEIPTS ============
@app.route('/receipt/<int:receipt_id>')
@login_required
def receipt(receipt_id):
    data = build_receipt_context(receipt_id)
    if not data:
        return "❌ Receipt not found"

    receipt_row, items, room_label = data
    
    log_action(
        session.get('user'),
        session.get('role'),
        'VIEW_RECEIPT',
        amount=receipt_row[4],
        details=f"Viewed receipt #{receipt_row[1]}"
    )

    return render_template(
        'receipt.html',
        receipt=receipt_row,
        items=items,
        room_label=room_label
    )

@app.route('/receipt/regenerate/<int:receipt_id>')
@login_required
def regenerate_receipt(receipt_id):
    data = build_receipt_context(receipt_id)
    if not data:
        return "❌ Receipt not found"

    receipt_row, items, room_label = data
    
    log_action(
        session.get('user'),
        session.get('role'),
        'PRINT_RECEIPT',
        amount=receipt_row[4],
        details=f"Reprinted receipt #{receipt_row[1]}"
    )

    return render_template(
        'receipt.html',
        receipt=receipt_row,
        items=items,
        room_label=room_label
    )

@app.route('/receipts')
@login_required
def receipts():
    if session.get('role') != 'admin':
        return "❌ Admin only"

    ensure_receipt_tables()
    cur = get_db().cursor()

    cur.execute("""
        SELECT r.id, r.receipt_no, r.order_id, r.room_id, r.amount, r.method, r.waiter_name, r.created_at, rooms.room_number
        FROM receipts r
        LEFT JOIN rooms ON rooms.id = r.room_id
        ORDER BY r.id DESC
    """)
    data = cur.fetchall()

    log_action(
        session.get('user'),
        session.get('role'),
        'VIEW_RECEIPTS_LIST',
        details=f"Viewed all receipts list"
    )

    return render_template('receipts.html', data=data)

# ============ REPORTS ============
@app.route('/reports')
@login_required
def reports():
    if session.get('role') != 'admin':
        return "❌ Admin only"

    ensure_activity_log_table()
    ensure_receipt_tables()
    cur = get_db().cursor()

    # Revenue metrics
    cur.execute("SELECT COALESCE(SUM(amount), 0) FROM receipts")
    total_revenue = cur.fetchone()[0] or 0

    # ✅ FIXED: Use DATE('now') instead of CURDATE()
    cur.execute("""
        SELECT COALESCE(SUM(amount), 0) 
        FROM receipts 
        WHERE DATE(created_at) = DATE('now')
    """)
    today_revenue = cur.fetchone()[0] or 0

    cur.execute("SELECT COUNT(*) FROM receipts")
    receipts_count = cur.fetchone()[0] or 0

    cur.execute("SELECT COUNT(*) FROM rooms WHERE status='occupied'")
    occupied_rooms = cur.fetchone()[0] or 0

    # Waiter performance
    cur.execute("""
        SELECT COALESCE(waiter_name,'Unknown') AS waiter,
               COUNT(*) AS receipt_count,
               COALESCE(SUM(amount),0) AS total_sales
        FROM receipts
        GROUP BY waiter_name
        ORDER BY total_sales DESC
    """)
    waiter_stats = cur.fetchall()

    # Recent receipts
    cur.execute("""
        SELECT r.id, r.receipt_no, r.room_id, r.amount, r.method, r.waiter_name, r.created_at, rooms.room_number
        FROM receipts r
        LEFT JOIN rooms ON rooms.id = r.room_id
        ORDER BY r.id DESC
        LIMIT 12
    """)
    recent_receipts = cur.fetchall()

    # Activity logs
    cur.execute("""
        SELECT user_name, role, action, item_name, quantity, amount, details, created_at
        FROM activity_logs
        ORDER BY created_at DESC
        LIMIT 500
    """)
    activity_logs = cur.fetchall()

    # Payment method breakdown
    cur.execute("""
        SELECT method, COUNT(*) as count, COALESCE(SUM(amount), 0) as total
        FROM receipts
        GROUP BY method
    """)
    payment_methods = cur.fetchall()

    # Daily revenue
    cur.execute("""
        SELECT DATE(created_at) as date, COUNT(*) as transactions, COALESCE(SUM(amount), 0) as revenue
        FROM receipts
        GROUP BY DATE(created_at)
        ORDER BY date DESC
        LIMIT 30
    """)
    daily_revenue = cur.fetchall()

    log_action(
        session.get('user'),
        session.get('role'),
        'VIEW_REPORTS',
        details=f"Accessed full reports dashboard"
    )

    return render_template(
        'reports.html',
        total_revenue=total_revenue,
        today_revenue=today_revenue,
        receipts_count=receipts_count,
        occupied_rooms=occupied_rooms,
        waiter_stats=waiter_stats,
        recent_receipts=recent_receipts,
        activity_logs=activity_logs,
        payment_methods=payment_methods,
        daily_revenue=daily_revenue
    )

# ============ ADMIN: RESET ACTIVITIES ============
@app.route('/admin/reset-activities', methods=['POST'])
@login_required
@admin_only
def reset_activities():
    """Reset all activity logs - ADMIN ONLY"""
    try:
        ensure_activity_log_table()
        cur = get_db().cursor()
        
        # Log the reset action BEFORE clearing
        log_action(
            session.get('user', 'Unknown'),
            session.get('role', 'unknown'),
            'RESET_ACTIVITIES',
            details=f"✅ Admin reset all activity logs"
        )
        
        # Clear activity logs
        cur.execute("DELETE FROM activity_logs")
        get_db().commit()
        
        logger.warning(f"⚠️ [{session.get('user')}] RESET ALL ACTIVITIES - All logs cleared")
        
        return redirect('/reports')
    except Exception as e:
        logger.error(f"❌ Error resetting activities: {e}")
        get_db().rollback()
        return "❌ Error resetting activities", 500

# ============ ADMIN: RESET REVENUE ============
@app.route('/admin/reset-revenue', methods=['POST'])
@login_required
@admin_only
def reset_revenue():
    """Reset all revenue data - ADMIN ONLY"""
    try:
        cur = get_db().cursor()
        
        # Log the reset action BEFORE clearing
        log_action(
            session.get('user', 'Unknown'),
            session.get('role', 'unknown'),
            'RESET_REVENUE',
            details=f"✅ Admin reset all revenue data (Receipts, Orders, Payments)"
        )
        
        # Clear all revenue-related data
        cur.execute("DELETE FROM receipt_items")
        cur.execute("DELETE FROM receipts")
        cur.execute("DELETE FROM payments")
        cur.execute("DELETE FROM order_items")
        cur.execute("DELETE FROM orders")
        
        get_db().commit()
        
        logger.warning(f"⚠️ [{session.get('user')}] RESET ALL REVENUE - All revenue data cleared")
        
        return redirect('/reports')
    except Exception as e:
        logger.error(f"❌ Error resetting revenue: {e}")
        get_db().rollback()
        return "❌ Error resetting revenue", 500

# ============ RUN ============
if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
