import unittest
from unittest.mock import patch

import app as app_module


class FakeCursor:
    def __init__(self, existing=False):
        self.existing = existing
        self.executed = []
        self.fetchone_result = None

    def execute(self, sql, params=None):
        self.executed.append((sql, params))
        if sql.strip().startswith("SELECT id FROM bookings WHERE external_id=%s"):
            self.fetchone_result = {"id": 101} if self.existing else None
        elif sql.strip().startswith("UPDATE bookings SET"):
            self.fetchone_result = {"id": 101}
        elif sql.strip().startswith("INSERT INTO bookings"):
            self.fetchone_result = {"id": 101}

    def fetchone(self):
        return self.fetchone_result

    def close(self):
        return None


class FakeConn:
    def __init__(self, cursor):
        self.cursor_obj = cursor

    def cursor(self):
        return self.cursor_obj

    def commit(self):
        return None

    def close(self):
        return None


class BookingPersistenceTests(unittest.TestCase):
    def test_upsert_booking_record_creates_pending_record(self):
        cursor = FakeCursor(existing=False)
        conn = FakeConn(cursor)

        with patch.object(app_module, "get_db_conn", return_value=conn):
            row = app_module.upsert_booking_record(
                {
                    "service_type": "daily",
                    "from": "Pune",
                    "to": "Kolhapur",
                    "date": "2026-07-01",
                    "time": "12:30",
                    "seats": "2",
                    "amount": "1200",
                    "user_name": "Test User",
                    "user_phone": "9999999999",
                    "user_email": "test@example.com",
                },
                external_id="order_123",
                payment_status="pending",
            )

        self.assertEqual(row["id"], 101)
        self.assertTrue(any("SELECT id FROM bookings WHERE external_id=%s" in sql for sql, _ in cursor.executed))
        self.assertTrue(any("INSERT INTO bookings" in sql for sql, _ in cursor.executed))

    def test_upsert_booking_record_updates_existing_record(self):
        cursor = FakeCursor(existing=True)
        conn = FakeConn(cursor)

        with patch.object(app_module, "get_db_conn", return_value=conn):
            row = app_module.upsert_booking_record(
                {
                    "service_type": "daily",
                    "from": "Pune",
                    "to": "Kolhapur",
                    "date": "2026-07-01",
                    "time": "12:30",
                    "seats": "2",
                    "amount": "1200",
                    "user_name": "Test User",
                    "user_phone": "9999999999",
                    "user_email": "test@example.com",
                },
                external_id="order_123",
                payment_status="paid",
                payment_id="pay_123",
            )

        self.assertEqual(row["id"], 101)
        self.assertTrue(any("UPDATE bookings SET" in sql for sql, _ in cursor.executed))


if __name__ == "__main__":
    unittest.main()
