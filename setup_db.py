import sqlite3

conn = sqlite3.connect('pos.db')
cur = conn.cursor()

# USERS
cur.execute("""
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    pin TEXT NOT NULL,
    role TEXT NOT NULL
)
""")

# ROOMS
cur.execute("""
CREATE TABLE IF NOT EXISTS rooms (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    room_number TEXT,
    type TEXT,
    price REAL,
    status TEXT
)
""")

# DRINKS
cur.execute("""
CREATE TABLE IF NOT EXISTS drinks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT,
    price REAL,
    category TEXT
)
""")

# ORDERS
cur.execute("""
CREATE TABLE IF NOT EXISTS orders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    room_id INTEGER NULL
)
""")

# ORDER ITEMS
cur.execute("""
CREATE TABLE IF NOT EXISTS order_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id INTEGER,
    drink_id INTEGER,
    quantity INTEGER,
    price REAL
)
""")

# PAYMENTS
cur.execute("""
CREATE TABLE IF NOT EXISTS payments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    room_id INTEGER NULL,
    amount REAL,
    method TEXT
)
""")

# RECEIPTS
cur.execute("""
CREATE TABLE IF NOT EXISTS receipts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    receipt_no TEXT UNIQUE,
    order_id INTEGER,
    room_id INTEGER,
    amount REAL,
    method TEXT,
    waiter_name TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
)
""")

# RECEIPT ITEMS
cur.execute("""
CREATE TABLE IF NOT EXISTS receipt_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    receipt_id INTEGER,
    item_name TEXT,
    quantity INTEGER,
    unit_price REAL,
    line_total REAL
)
""")

# ACTIVITY LOGS
cur.execute("""
CREATE TABLE IF NOT EXISTS activity_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_name TEXT,
    role TEXT,
    action TEXT,
    room_id INTEGER,
    item_name TEXT,
    quantity INTEGER DEFAULT 0,
    amount REAL DEFAULT 0,
    details TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
)
""")

# DEFAULT ADMIN
cur.execute("""
INSERT INTO users (name, pin, role)
SELECT 'Admin', '1234', 'admin'
WHERE NOT EXISTS (
    SELECT 1 FROM users WHERE pin='1234'
)
""")

# SAMPLE ROOM
cur.execute("""
INSERT INTO rooms (room_number, type, price, status)
SELECT 'VIP-1', 'VIP', 5000, 'available'
WHERE NOT EXISTS (
    SELECT 1 FROM rooms WHERE room_number='VIP-1'
)
""")

# SAMPLE DRINK
cur.execute("""
INSERT INTO drinks (name, price, category)
SELECT 'Tusker', 300, 'Beer'
WHERE NOT EXISTS (
    SELECT 1 FROM drinks WHERE name='Tusker'
)
""")

conn.commit()
conn.close()

print("✅ Database created successfully")