import aiosqlite
from datetime import datetime

DB_PATH = "shop.db"


async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS products (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                description TEXT NOT NULL,
                price INTEGER NOT NULL,
                photo_file_id TEXT NOT NULL,
                file_id TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS purchases (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                product_id INTEGER NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY,
                username TEXT,
                first_name TEXT,
                balance INTEGER DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS balance_transactions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                amount INTEGER NOT NULL,
                type TEXT NOT NULL,
                comment TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        await db.commit()


# ─── Продукты ─────────────────────────────────────────────────────────────────

async def add_product(name, description, price, photo_file_id, file_id):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO products (name, description, price, photo_file_id, file_id) VALUES (?, ?, ?, ?, ?)",
            (name, description, price, photo_file_id, file_id)
        )
        await db.commit()


async def get_all_products():
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM products ORDER BY created_at DESC") as cursor:
            return await cursor.fetchall()


async def get_product(product_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM products WHERE id = ?", (product_id,)) as cursor:
            return await cursor.fetchone()


async def delete_product(product_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM products WHERE id = ?", (product_id,))
        await db.commit()


# ─── Пользователи ─────────────────────────────────────────────────────────────

async def register_user(user_id, username, first_name):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT OR IGNORE INTO users (id, username, first_name) VALUES (?, ?, ?)",
            (user_id, username, first_name)
        )
        await db.commit()


async def get_user(user_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM users WHERE id = ?", (user_id,)) as cursor:
            return await cursor.fetchone()


async def get_all_users():
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM users") as cursor:
            return await cursor.fetchall()


# ─── Баланс ───────────────────────────────────────────────────────────────────

async def get_balance(user_id: int) -> int:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT balance FROM users WHERE id = ?", (user_id,)) as cursor:
            row = await cursor.fetchone()
            return row[0] if row else 0


async def add_balance(user_id: int, amount: int, comment: str = None):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT OR IGNORE INTO users (id, username, first_name) VALUES (?, ?, ?)",
            (user_id, None, str(user_id))
        )
        await db.execute(
            "UPDATE users SET balance = balance + ? WHERE id = ?",
            (amount, user_id)
        )
        await db.execute(
            "INSERT INTO balance_transactions (user_id, amount, type, comment) VALUES (?, ?, ?, ?)",
            (user_id, amount, "topup", comment)
        )
        await db.commit()


async def deduct_balance(user_id: int, amount: int, comment: str = None) -> bool:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT balance FROM users WHERE id = ?", (user_id,)) as cursor:
            row = await cursor.fetchone()
            if not row or row[0] < amount:
                return False
        await db.execute(
            "UPDATE users SET balance = balance - ? WHERE id = ?",
            (amount, user_id)
        )
        await db.execute(
            "INSERT INTO balance_transactions (user_id, amount, type, comment) VALUES (?, ?, ?, ?)",
            (user_id, -amount, "purchase", comment)
        )
        await db.commit()
        return True


# ─── Пополнение через Stars ───────────────────────────────────────────────────

async def topup_via_stars(user_id: int, stars: int, charge_id: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE users SET balance = balance + ? WHERE id = ?",
            (stars, user_id)
        )
        await db.execute(
            "INSERT INTO balance_transactions (user_id, amount, type, comment) VALUES (?, ?, ?, ?)",
            (user_id, stars, "topup_stars", charge_id)
        )
        await db.commit()


# ─── Покупки ──────────────────────────────────────────────────────────────────

async def save_purchase(user_id: int, product_id: int) -> int:
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "INSERT INTO purchases (user_id, product_id) VALUES (?, ?)",
            (user_id, product_id)
        )
        await db.commit()
        return cursor.lastrowid


async def get_user_purchases(user_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("""
            SELECT pu.id, p.name, p.description, p.price, p.file_id, pu.created_at
            FROM purchases pu
            JOIN products p ON p.id = pu.product_id
            WHERE pu.user_id = ?
            ORDER BY pu.created_at DESC
        """, (user_id,)) as cursor:
            return await cursor.fetchall()


async def get_purchase(purchase_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("""
            SELECT pu.id, pu.user_id, pu.created_at,
                   p.name, p.description, p.price, p.file_id
            FROM purchases pu
            JOIN products p ON p.id = pu.product_id
            WHERE pu.id = ?
        """, (purchase_id,)) as cursor:
            return await cursor.fetchone()


# ─── Статистика ───────────────────────────────────────────────────────────────

async def get_stats():
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT COUNT(*) FROM users") as c:
            users = (await c.fetchone())[0]
        async with db.execute("SELECT COUNT(*) FROM products") as c:
            products = (await c.fetchone())[0]
        async with db.execute("SELECT COUNT(*) FROM purchases") as c:
            purchases = (await c.fetchone())[0]
        async with db.execute("""
            SELECT COALESCE(SUM(p.price), 0)
            FROM purchases pu JOIN products p ON p.id = pu.product_id
        """) as c:
            revenue = (await c.fetchone())[0]
        return users, products, purchases, revenue
