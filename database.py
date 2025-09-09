"""
Warehouse Management System - Database Module
SQLite database operations with proper schema and foreign keys
"""

import sqlite3
import os
import logging
import hashlib
from datetime import datetime
from typing import List, Dict, Optional, Tuple
import json


class DatabaseManager:
    """Manages SQLite database operations for the warehouse system."""

    def __init__(self, db_path: str = "warehouse.db"):
        self.db_path = db_path
        self.logger = logging.getLogger(__name__)
        self._init_database()

    def _init_database(self):
        """Initialize database with proper schema and foreign keys."""
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.execute("PRAGMA foreign_keys = ON")
                cursor = conn.cursor()

                # Create tables
                self._create_tables(cursor)
                self._create_indices(cursor)
                self._create_default_data(cursor)

                conn.commit()
                self.logger.info("Database initialized successfully")

        except Exception as e:
            self.logger.error(f"Database initialization failed: {e}")
            raise

    def _create_tables(self, cursor):
        """Create all required tables."""

        # Users and roles table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL,
                role TEXT NOT NULL CHECK (role IN ('Admin1', 'Admin2', 'Admin3', 'Viewer', 'Operator')),
                created_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                last_login TIMESTAMP,
                is_active BOOLEAN DEFAULT 1
            )
        """)

        # Customers table with 4-digit PIN as ID
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS customers (
                pin INTEGER PRIMARY KEY CHECK (pin >= 1000 AND pin <= 9999),
                name TEXT NOT NULL,
                phone TEXT,
                email TEXT,
                created_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                notes TEXT
            )
        """)

        # Materials table (products)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS materials (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                model TEXT NOT NULL,
                manufacturer TEXT NOT NULL,
                description TEXT,
                price REAL DEFAULT 0.0,
                image_path TEXT,
                production_date DATE,
                acquisition_date DATE DEFAULT CURRENT_DATE,
                warranty_expiry DATE,
                is_used BOOLEAN DEFAULT 0,
                category TEXT DEFAULT 'General',
                created_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # Serial numbers table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS serial_numbers (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                material_id INTEGER NOT NULL,
                serial_number TEXT UNIQUE NOT NULL,
                status TEXT DEFAULT 'Available' CHECK (status IN ('Available', 'Assigned', 'Damaged', 'Returned')),
                created_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (material_id) REFERENCES materials (id) ON DELETE CASCADE
            )
        """)

        # Assignments table (customer-serial relationships)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS assignments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                customer_pin INTEGER NOT NULL,
                serial_id INTEGER NOT NULL,
                assigned_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                returned_date TIMESTAMP,
                assigned_by TEXT,
                notes TEXT,
                FOREIGN KEY (customer_pin) REFERENCES customers (pin) ON DELETE CASCADE,
                FOREIGN KEY (serial_id) REFERENCES serial_numbers (id) ON DELETE CASCADE
            )
        """)

        # Audit trail table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS audit_trail (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT NOT NULL,
                action TEXT NOT NULL,
                table_name TEXT NOT NULL,
                record_id TEXT,
                old_values TEXT,
                new_values TEXT,
                timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

    def _create_indices(self, cursor):
        """Create indices for better performance."""
        indices = [
            "CREATE INDEX IF NOT EXISTS idx_customers_name ON customers (name)",
            "CREATE INDEX IF NOT EXISTS idx_materials_model ON materials (model)",
            "CREATE INDEX IF NOT EXISTS idx_materials_manufacturer ON materials (manufacturer)",
            "CREATE INDEX IF NOT EXISTS idx_serial_numbers_material ON serial_numbers (material_id)",
            "CREATE INDEX IF NOT EXISTS idx_serial_numbers_status ON serial_numbers (status)",
            "CREATE INDEX IF NOT EXISTS idx_assignments_customer ON assignments (customer_pin)",
            "CREATE INDEX IF NOT EXISTS idx_assignments_serial ON assignments (serial_id)",
            "CREATE INDEX IF NOT EXISTS idx_audit_trail_username ON audit_trail (username)",
            "CREATE INDEX IF NOT EXISTS idx_audit_trail_timestamp ON audit_trail (timestamp)"
        ]

        for index in indices:
            cursor.execute(index)

    def _create_default_data(self, cursor):
        """Create default admin user and sample data."""
        # Check if default admin already exists
        cursor.execute("SELECT COUNT(*) FROM users WHERE username = ?", ("MallousG",))
        if cursor.fetchone()[0] > 0:
            self.logger.info("Default admin user already exists")
            return

        # Create default Admin1 user
        admin_password = self._hash_password("MallousG")
        try:
            cursor.execute("""
                INSERT INTO users (username, password_hash, role)
                VALUES (?, ?, ?)
            """, ("MallousG", admin_password, "Admin1"))
            self.logger.info("Default admin user created: MallousG/MallousG (Admin1)")
        except sqlite3.IntegrityError as e:
            self.logger.warning(f"Could not create default admin: {e}")

    def _hash_password(self, password: str) -> str:
        """Hash password using SHA-256."""
        return hashlib.sha256(password.encode()).hexdigest()

    def verify_login(self, username: str, password: str) -> Optional[Dict]:
        """Verify user login credentials."""
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.row_factory = sqlite3.Row
                cursor = conn.cursor()

                password_hash = self._hash_password(password)
                cursor.execute("""
                    SELECT * FROM users 
                    WHERE username = ? AND password_hash = ? AND is_active = 1
                """, (username, password_hash))

                user = cursor.fetchone()
                if user:
                    # Update last login
                    cursor.execute("""
                        UPDATE users SET last_login = CURRENT_TIMESTAMP
                        WHERE id = ?
                    """, (user['id'],))
                    conn.commit()

                    return dict(user)
                else:
                    # Debug: Check if user exists with different password
                    cursor.execute("SELECT username FROM users WHERE username = ?", (username,))
                    if cursor.fetchone():
                        self.logger.warning(f"User {username} exists but password mismatch")
                    else:
                        self.logger.warning(f"User {username} does not exist")

                return None

        except Exception as e:
            self.logger.error(f"Login verification failed: {e}")
            return None

    def add_customer(self, pin: int, name: str, phone: str = "", email: str = "", notes: str = "") -> bool:
        """Add new customer with 4-digit PIN."""
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    INSERT INTO customers (pin, name, phone, email, notes)
                    VALUES (?, ?, ?, ?, ?)
                """, (pin, name, phone, email, notes))
                conn.commit()
                self.logger.info(f"Customer added: {name} (PIN: {pin})")
                return True

        except sqlite3.IntegrityError:
            self.logger.warning(f"Customer PIN {pin} already exists")
            return False
        except Exception as e:
            self.logger.error(f"Failed to add customer: {e}")
            return False

    def update_customer(self, pin: int, name: str, phone: str = "", email: str = "", notes: str = "") -> bool:
        """Update existing customer."""
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    UPDATE customers 
                    SET name = ?, phone = ?, email = ?, notes = ?, updated_date = CURRENT_TIMESTAMP
                    WHERE pin = ?
                """, (name, phone, email, notes, pin))

                if cursor.rowcount > 0:
                    conn.commit()
                    self.logger.info(f"Customer updated: {name} (PIN: {pin})")
                    return True
                return False

        except Exception as e:
            self.logger.error(f"Failed to update customer: {e}")
            return False

    def delete_customer(self, pin: int) -> bool:
        """Delete customer and all related assignments."""
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute("DELETE FROM customers WHERE pin = ?", (pin,))

                if cursor.rowcount > 0:
                    conn.commit()
                    self.logger.info(f"Customer deleted: PIN {pin}")
                    return True
                return False

        except Exception as e:
            self.logger.error(f"Failed to delete customer: {e}")
            return False

    def search_customers(self, query: str = "") -> List[Dict]:
        """Search customers by name, phone, or PIN."""
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.row_factory = sqlite3.Row
                cursor = conn.cursor()

                if query:
                    cursor.execute("""
                        SELECT * FROM customers 
                        WHERE name LIKE ? OR phone LIKE ? OR CAST(pin AS TEXT) LIKE ?
                        ORDER BY name
                    """, (f"%{query}%", f"%{query}%", f"%{query}%"))
                else:
                    cursor.execute("SELECT * FROM customers ORDER BY name")

                return [dict(row) for row in cursor.fetchall()]

        except Exception as e:
            self.logger.error(f"Customer search failed: {e}")
            return []

    def get_customer(self, pin: int) -> Optional[Dict]:
        """Get customer by PIN."""
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.row_factory = sqlite3.Row
                cursor = conn.cursor()
                cursor.execute("SELECT * FROM customers WHERE pin = ?", (pin,))

                row = cursor.fetchone()
                return dict(row) if row else None

        except Exception as e:
            self.logger.error(f"Failed to get customer: {e}")
            return None

    def add_material(self, name: str, model: str, manufacturer: str, **kwargs) -> Optional[int]:
        """Add new material/product."""
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()

                fields = ["name", "model", "manufacturer"]
                values = [name, model, manufacturer]
                placeholders = ["?", "?", "?"]

                # Add optional fields
                for field, value in kwargs.items():
                    if value is not None and field in ["description", "price", "image_path",
                                                       "production_date", "acquisition_date",
                                                       "warranty_expiry", "is_used", "category"]:
                        fields.append(field)
                        values.append(value)
                        placeholders.append("?")

                query = f"""
                    INSERT INTO materials ({', '.join(fields)})
                    VALUES ({', '.join(placeholders)})
                """

                cursor.execute(query, values)
                material_id = cursor.lastrowid
                conn.commit()

                self.logger.info(f"Material added: {name} {model} (ID: {material_id})")
                return material_id

        except Exception as e:
            self.logger.error(f"Failed to add material: {e}")
            return None

    def update_material(self, material_id: int, **kwargs) -> bool:
        """Update existing material."""
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()

                # Build update query
                set_clauses = []
                values = []

                allowed_fields = ["name", "model", "manufacturer", "description", "price",
                                  "image_path", "production_date", "acquisition_date",
                                  "warranty_expiry", "is_used", "category"]

                for field, value in kwargs.items():
                    if field in allowed_fields:
                        set_clauses.append(f"{field} = ?")
                        values.append(value)

                if not set_clauses:
                    return False

                set_clauses.append("updated_date = CURRENT_TIMESTAMP")
                values.append(material_id)

                query = f"UPDATE materials SET {', '.join(set_clauses)} WHERE id = ?"
                cursor.execute(query, values)

                if cursor.rowcount > 0:
                    conn.commit()
                    self.logger.info(f"Material updated: ID {material_id}")
                    return True
                return False

        except Exception as e:
            self.logger.error(f"Failed to update material: {e}")
            return False

    def delete_material(self, material_id: int) -> bool:
        """Delete material and all related serials."""
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute("DELETE FROM materials WHERE id = ?", (material_id,))

                if cursor.rowcount > 0:
                    conn.commit()
                    self.logger.info(f"Material deleted: ID {material_id}")
                    return True
                return False

        except Exception as e:
            self.logger.error(f"Failed to delete material: {e}")
            return False

    def search_materials(self, query: str = "", is_used: Optional[bool] = None) -> List[Dict]:
        """Search materials by name, model, or manufacturer."""
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.row_factory = sqlite3.Row
                cursor = conn.cursor()

                base_query = """
                    SELECT m.*, COUNT(s.id) as serial_count,
                           COUNT(CASE WHEN s.status = 'Available' THEN 1 END) as available_count
                    FROM materials m
                    LEFT JOIN serial_numbers s ON m.id = s.material_id
                """

                conditions = []
                params = []

                if query:
                    conditions.append("(m.name LIKE ? OR m.model LIKE ? OR m.manufacturer LIKE ?)")
                    params.extend([f"%{query}%", f"%{query}%", f"%{query}%"])

                if is_used is not None:
                    conditions.append("m.is_used = ?")
                    params.append(is_used)

                if conditions:
                    base_query += " WHERE " + " AND ".join(conditions)

                base_query += " GROUP BY m.id ORDER BY m.name, m.model"

                cursor.execute(base_query, params)
                return [dict(row) for row in cursor.fetchall()]

        except Exception as e:
            self.logger.error(f"Material search failed: {e}")
            return []

    def get_material(self, material_id: int) -> Optional[Dict]:
        """Get material by ID with serial count."""
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.row_factory = sqlite3.Row
                cursor = conn.cursor()

                cursor.execute("""
                    SELECT m.*, COUNT(s.id) as serial_count,
                           COUNT(CASE WHEN s.status = 'Available' THEN 1 END) as available_count
                    FROM materials m
                    LEFT JOIN serial_numbers s ON m.id = s.material_id
                    WHERE m.id = ?
                    GROUP BY m.id
                """, (material_id,))

                row = cursor.fetchone()
                return dict(row) if row else None

        except Exception as e:
            self.logger.error(f"Failed to get material: {e}")
            return None

    def add_serials_to_material(self, material_id: int, serials: List[str]) -> int:
        """Add multiple serial numbers to a material. Returns count of successfully added serials."""
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                added_count = 0

                for serial in serials:
                    try:
                        cursor.execute("""
                            INSERT INTO serial_numbers (material_id, serial_number)
                            VALUES (?, ?)
                        """, (material_id, serial.strip()))
                        added_count += 1
                    except sqlite3.IntegrityError:
                        self.logger.warning(f"Serial number {serial} already exists")
                        continue

                conn.commit()
                self.logger.info(f"Added {added_count} serials to material {material_id}")
                return added_count

        except Exception as e:
            self.logger.error(f"Failed to add serials: {e}")
            return 0

    def get_material_serials(self, material_id: int) -> List[Dict]:
        """Get all serial numbers for a material."""
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.row_factory = sqlite3.Row
                cursor = conn.cursor()

                cursor.execute("""
                    SELECT s.*, c.name as customer_name, c.pin as customer_pin
                    FROM serial_numbers s
                    LEFT JOIN assignments a ON s.id = a.serial_id AND a.returned_date IS NULL
                    LEFT JOIN customers c ON a.customer_pin = c.pin
                    WHERE s.material_id = ?
                    ORDER BY s.serial_number
                """, (material_id,))

                return [dict(row) for row in cursor.fetchall()]

        except Exception as e:
            self.logger.error(f"Failed to get serials: {e}")
            return []

    def assign_serial_to_customer(self, serial_id: int, customer_pin: int, assigned_by: str, notes: str = "") -> bool:
        """Assign serial number to customer."""
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()

                # Check if serial is available
                cursor.execute("SELECT status FROM serial_numbers WHERE id = ?", (serial_id,))
                row = cursor.fetchone()
                if not row or row[0] != 'Available':
                    return False

                # Create assignment
                cursor.execute("""
                    INSERT INTO assignments (customer_pin, serial_id, assigned_by, notes)
                    VALUES (?, ?, ?, ?)
                """, (customer_pin, serial_id, assigned_by, notes))

                # Update serial status
                cursor.execute("""
                    UPDATE serial_numbers SET status = 'Assigned' WHERE id = ?
                """, (serial_id,))

                conn.commit()
                self.logger.info(f"Serial {serial_id} assigned to customer {customer_pin}")
                return True

        except Exception as e:
            self.logger.error(f"Failed to assign serial: {e}")
            return False

    def unassign_serial(self, serial_id: int) -> bool:
        """Unassign serial number from customer."""
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()

                # Mark assignment as returned
                cursor.execute("""
                    UPDATE assignments 
                    SET returned_date = CURRENT_TIMESTAMP
                    WHERE serial_id = ? AND returned_date IS NULL
                """, (serial_id,))

                # Update serial status
                cursor.execute("""
                    UPDATE serial_numbers SET status = 'Available' WHERE id = ?
                """, (serial_id,))

                conn.commit()
                self.logger.info(f"Serial {serial_id} unassigned")
                return True

        except Exception as e:
            self.logger.error(f"Failed to unassign serial: {e}")
            return False

    def transfer_serials_to_used(self, material_id: int) -> bool:
        """Transfer material and all its serials to used inventory."""
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()

                cursor.execute("""
                    UPDATE materials SET is_used = 1, updated_date = CURRENT_TIMESTAMP
                    WHERE id = ?
                """, (material_id,))

                if cursor.rowcount > 0:
                    conn.commit()
                    self.logger.info(f"Material {material_id} transferred to used inventory")
                    return True
                return False

        except Exception as e:
            self.logger.error(f"Failed to transfer to used: {e}")
            return False

    def get_customer_history(self, customer_pin: int) -> List[Dict]:
        """Get assignment history for customer."""
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.row_factory = sqlite3.Row
                cursor = conn.cursor()

                cursor.execute("""
                    SELECT a.*, s.serial_number, m.name, m.model, m.manufacturer
                    FROM assignments a
                    JOIN serial_numbers s ON a.serial_id = s.id
                    JOIN materials m ON s.material_id = m.id
                    WHERE a.customer_pin = ?
                    ORDER BY a.assigned_date DESC
                """, (customer_pin,))

                return [dict(row) for row in cursor.fetchall()]

        except Exception as e:
            self.logger.error(f"Failed to get customer history: {e}")
            return []

    def add_user(self, username: str, password: str, role: str) -> bool:
        """Add new user account."""
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                password_hash = self._hash_password(password)

                cursor.execute("""
                    INSERT INTO users (username, password_hash, role)
                    VALUES (?, ?, ?)
                """, (username, password_hash, role))

                conn.commit()
                self.logger.info(f"User added: {username} ({role})")
                return True

        except sqlite3.IntegrityError:
            self.logger.warning(f"Username {username} already exists")
            return False
        except Exception as e:
            self.logger.error(f"Failed to add user: {e}")
            return False

    def log_audit(self, username: str, action: str, table_name: str, record_id: str = "",
                  old_values: str = "", new_values: str = ""):
        """Log action to audit trail."""
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    INSERT INTO audit_trail (username, action, table_name, record_id, old_values, new_values)
                    VALUES (?, ?, ?, ?, ?, ?)
                """, (username, action, table_name, record_id, old_values, new_values))
                conn.commit()

        except Exception as e:
            self.logger.error(f"Failed to log audit: {e}")

    def debug_users(self):
        """Debug method to check users in database."""
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.row_factory = sqlite3.Row
                cursor = conn.cursor()
                cursor.execute("SELECT * FROM users")
                users = cursor.fetchall()

                print(f"Total users in database: {len(users)}")
                for user in users:
                    print(f"  User: {user['username']}, Role: {user['role']}, Active: {user['is_active']}")

                return [dict(user) for user in users]
        except Exception as e:
            print(f"Debug users failed: {e}")
            return []

    def close(self):
        """Close database connection if needed."""
        pass  # Using context managers, so no persistent connection to close