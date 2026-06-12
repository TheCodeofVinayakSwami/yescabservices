import os
from datetime import datetime
import time
from flask import Flask, render_template, request, url_for, redirect, g, jsonify
import psycopg2
import psycopg2.extras
from dotenv import load_dotenv
import razorpay
import hmac
import hashlib
import base64
import json
import logging
import requests
import re

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(BASE_DIR, ".env"))  # optional .env

DATABASE_URL = os.environ.get("DATABASE_URL")  # Neon URI

app = Flask(__name__, template_folder="template", static_folder="static")

# Canonical city mapping and helper to normalize user-entered city names
_CITY_CANON = {
    "nashik": "Nashik",
    "ahmednagar": "Ahmednagar",
    "ahmed nagar": "Ahmednagar",
    "ahmad nagr": "Ahmednagar",
    "chhatrapati sambhaji nagar aurangabad": "Chhatrapati Sambhaji Nagar (Aurangabad)",
    "chh shambaji nagar aurangabad": "Chhatrapati Sambhaji Nagar (Aurangabad)",
    "aurangabad": "Aurangabad",
    "shirdi": "Shirdi",
    "shridi": "Shirdi",
    "sangli": "Sangli",
    "sangali": "Sangli",
    "solapur": "Solapur",
    "solhapur": "Solapur",
    "satara": "Satara",
    "pune": "Pune",
    "kolhapur": "Kolhapur",
    "mumbai thane": "Mumbai & Thane",
    "mumbai": "Mumbai",
    "belgav": "Belgav",
    "belgaum": "Belgav",
    "bengaluru": "Bengaluru",
    "goa": "Goa"
}


def canonicalize_city(name):
    """Return a canonical city name for known variants, otherwise return the trimmed original or None.

    This is lenient and compares a normalized key (lowercased, punctuation removed).
    """
    if not name:
        return None
    key = ''.join(ch for ch in name.lower() if ch.isalnum() or ch.isspace()).strip()
    key = ' '.join(key.split())
    return _CITY_CANON.get(key) or name.strip()


def get_db_conn():
    conn = getattr(g, "_pg_conn", None)
    if conn is None:
        if not DATABASE_URL:
            raise RuntimeError("DATABASE_URL not set")
        conn = psycopg2.connect(DATABASE_URL, cursor_factory=psycopg2.extras.RealDictCursor)
        g._pg_conn = conn
    return conn


def init_db():
    conn = psycopg2.connect(DATABASE_URL, cursor_factory=psycopg2.extras.RealDictCursor)
    cur = conn.cursor()
    cur.execute("""
    CREATE TABLE IF NOT EXISTS bookings (
      id SERIAL PRIMARY KEY,
      service_type TEXT,
      from_city TEXT,
      from_point TEXT,
      to_city TEXT,
      to_point TEXT,
      journey_date DATE,
      journey_time TEXT,
      pickup_time TIME,
      seats INTEGER,
      amount NUMERIC,
      user_name TEXT,
      user_phone TEXT,
      user_email TEXT,
      created_at TIMESTAMPTZ DEFAULT now()
    );
    """)
    conn.commit()
    # Ensure additional columns exist for payment tracking
    cur.execute("ALTER TABLE bookings ADD COLUMN IF NOT EXISTS external_id TEXT;")
    cur.execute("ALTER TABLE bookings ADD COLUMN IF NOT EXISTS payment_status TEXT;")
    cur.execute("ALTER TABLE bookings ADD COLUMN IF NOT EXISTS payment_id TEXT;")
    cur.execute("ALTER TABLE bookings ADD COLUMN IF NOT EXISTS webhook_payload JSONB;")

    # Table to store raw incoming payment webhooks for debugging/processing
    cur.execute("""
    CREATE TABLE IF NOT EXISTS payment_webhooks (
        id SERIAL PRIMARY KEY,
        provider TEXT,
        external_id TEXT,
        payload JSONB,
        headers JSONB,
        processed BOOLEAN DEFAULT FALSE,
        received_at TIMESTAMPTZ DEFAULT now()
    );
    """)

    conn.commit()
    cur.close()
    conn.close()


# Initialize DB only if explicitly requested (comment out auto-init for now)
# if DATABASE_URL:
#     with app.app_context():
#         init_db()

# Instead, try to init on first request
_db_initialized = False

@app.before_request
def before_req():
    global _db_initialized
    if not _db_initialized and DATABASE_URL:
        try:
            init_db()
            _db_initialized = True
        except Exception as e:
            print(f"DB init deferred: {e}")


@app.teardown_appcontext
def close_connection(exception):
    conn = getattr(g, "_pg_conn", None)
    if conn is not None:
        try:
            conn.close()
        except Exception:
            pass


@app.route('/')
def index():
    return render_template('index.html')


@app.route("/policy")
def policy():
    return render_template("policy.html")


@app.route("/car")
def car():
    return render_template("car.html")


@app.route("/contact")
def contact():
    return render_template("contact.html")


@app.route('/googled0ce29d311862936.html')
def google_verification():
    # Serve Google site verification file from the static folder so it's reachable at the site root
    return app.send_static_file('googled0ce29d311862936.html')


@app.route("/booking")
def booking():
    params = request.args.to_dict()
    # Render booking.html and pass query params if needed
    return render_template('booking.html', params=params)


@app.route("/booking.html")
def booking_html():
    # support legacy/static-style links that point to booking.html
    return redirect(url_for('booking'))


@app.route("/api/book", methods=["POST"])
def api_book():
    data = request.get_json(force=True)
    if not data:
        return jsonify({"error": "no json"}), 400

    # minimal validation
    service = data.get("service_type") or data.get("service")
    name = data.get("user_name")
    phone = data.get("user_phone")
    if not service or not name or not phone:
        return jsonify({"error": "missing required fields (service, user_name, user_phone)"}), 400

    # Normalize some fields before saving
    from_city = canonicalize_city(data.get("from"))
    to_city = canonicalize_city(data.get("to"))
    from_point = data.get("from_point")
    to_point = data.get("to_point")
    journey_date = data.get("date") or None
    journey_time = data.get("time")
    pickup_time = data.get("pickup_time") or None
    seats = int(data.get("seats") or 0)
    amount = float(data.get("amount") or 0)

    conn = get_db_conn()
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO bookings
          (service_type, from_city, from_point, to_city, to_point, journey_date, journey_time, pickup_time, seats, amount, user_name, user_phone, user_email, created_at)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
        RETURNING id
        """,
        (
            service,
            from_city,
            from_point,
            to_city,
            to_point,
            journey_date,
            journey_time,
            pickup_time,
            seats,
            amount,
            name,
            phone,
            data.get("user_email"),
            datetime.utcnow(),
        ),
    )
    row = cur.fetchone()
    conn.commit()
    cur.close()
    return jsonify({"id": row["id"]}), 201


@app.route("/api/bookings", methods=["GET"])
def list_bookings():
    conn = get_db_conn()
    cur = conn.cursor()
    cur.execute("SELECT * FROM bookings ORDER BY created_at DESC")
    rows = cur.fetchall()
    cur.close()
    return jsonify(rows)


@app.route("/admin")
def admin():
    """
    Read all bookings from Neon (Postgres) and render admin page.
    """
    conn = get_db_conn()
    cur = conn.cursor()
    # select columns you created in the bookings table
    cur.execute("""
        SELECT id, service_type, from_city, from_point, to_city, to_point,
        
               journey_date, journey_time, pickup_time, seats, amount,
               user_name, user_phone, user_email, created_at
        FROM bookings
        ORDER BY created_at DESC
    """)
    bookings = cur.fetchall()  # RealDictCursor => list of dict-like rows
    cur.close()
    return render_template("admin.html", bookings=bookings)


# Razorpay keys (read from environment; do NOT hard-code secrets)
RZP_KEY_ID = os.environ.get("RZP_KEY_ID")
RZP_KEY_SECRET = os.environ.get("RZP_KEY_SECRET")
rzp_client = razorpay.Client(auth=(RZP_KEY_ID, RZP_KEY_SECRET)) if RZP_KEY_ID and RZP_KEY_SECRET else None
# Merchant UPI ID (VPA) - set via environment variable, e.g. MERCHANT_UPI=merchant@bank
MERCHANT_UPI = os.environ.get("MERCHANT_UPI")
# Webhook secret configured in Razorpay dashboard (optional but recommended)
RZP_WEBHOOK_SECRET = os.environ.get("RZP_WEBHOOK_SECRET")
# Telegram admin notifications (token and admin chat id must be configured in env)
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_ADMIN_CHAT_ID = os.environ.get("TELEGRAM_ADMIN_CHAT_ID")
# Optional base URL (used to build admin links in messages)
APP_BASE_URL = os.environ.get("APP_BASE_URL", "")
# Google Places configuration (optional — set in .env)
GOOGLE_API_KEY = os.environ.get("GOOGLE_API_KEY")
GOOGLE_PLACE_ID = os.environ.get("GOOGLE_PLACE_ID")
GOOGLE_PLACE_NAME = os.environ.get("GOOGLE_PLACE_NAME")
# Simple in-memory cache for Google reviews
_google_reviews_cache = {"expires": 0, "data": None}

logging.basicConfig(level=logging.INFO)


def send_telegram_to_admin(message: str, parse_mode: str = "HTML") -> bool:
    """Send a text message to the configured admin Telegram chat.

    Returns True on success, False otherwise. This is best-effort and
    does not raise on failure.
    """
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_ADMIN_CHAT_ID:
        logging.warning("Telegram not configured: skip sending admin notification")
        return False
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        resp = requests.post(url, data={
            "chat_id": TELEGRAM_ADMIN_CHAT_ID,
            "text": message,
            "parse_mode": parse_mode,
        }, timeout=10)
        if not resp.ok:
            logging.warning("Telegram API error %s: %s", resp.status_code, resp.text)
            return False
        return True
    except Exception:
        logging.exception("Exception while sending Telegram message")
        return False


def fetch_google_place_reviews(api_key: str, place_id: str = None, place_name: str = None, ttl: int = 300):
    """Fetch place details and reviews from Google Places API.

    Returns a dict with keys: place_id, name, address, reviews (list) or {'error': msg}.
    Uses a simple in-memory cache for `ttl` seconds.
    """
    now = time.time()
    if _google_reviews_cache.get("expires", 0) > now and _google_reviews_cache.get("data"):
        return _google_reviews_cache["data"]

    if not api_key:
        return {"error": "GOOGLE_API_KEY not configured"}

    try:
        # If we don't have a place_id, try to find one by text (business name)
        if not place_id:
            if not place_name:
                return {"error": "No GOOGLE_PLACE_ID or GOOGLE_PLACE_NAME configured"}
            # Find place by text
            find_url = "https://maps.googleapis.com/maps/api/place/findplacefromtext/json"
            params = {
                "input": place_name,
                "inputtype": "textquery",
                "fields": "place_id,name,formatted_address",
                "key": api_key,
            }
            r = requests.get(find_url, params=params, timeout=8)
            j = r.json() if r.ok else {}
            candidates = j.get("candidates") or []
            if not candidates:
                return {"error": "Place not found via GOOGLE_PLACE_NAME"}
            place_id = candidates[0].get("place_id")

        # Now fetch place details including reviews
        details_url = "https://maps.googleapis.com/maps/api/place/details/json"
        params = {
            "place_id": place_id,
            "fields": "name,rating,reviews,formatted_address,place_id",
            "key": api_key,
        }
        r = requests.get(details_url, params=params, timeout=8)
        if not r.ok:
            return {"error": f"Google Places request failed: {r.status_code}"}
        j = r.json()
        status = j.get("status")
        if status != "OK":
            return {"error": f"Google Places API status: {status}", "raw": j}

        result = j.get("result", {})
        reviews = result.get("reviews", []) or []
        # Keep only common review fields to reduce payload
        cleaned = []
        for rv in reviews:
            cleaned.append({
                "author_name": rv.get("author_name"),
                "rating": rv.get("rating"),
                "text": rv.get("text"),
                "relative_time_description": rv.get("relative_time_description"),
                "profile_photo_url": rv.get("profile_photo_url"),
                "time": rv.get("time"),
            })

        data = {
            "place_id": result.get("place_id") or place_id,
            "name": result.get("name"),
            "address": result.get("formatted_address"),
            "reviews": cleaned,
        }
        # Cache result
        _google_reviews_cache["data"] = data
        _google_reviews_cache["expires"] = now + ttl
        return data
    except Exception:
        logging.exception("Failed to fetch Google place reviews")
        return {"error": "exception while fetching reviews"}


def extract_reviews_from_share_html(html: str):
    """Extract simple review blocks from a Google share page HTML string.

    This uses heuristic regexes to pull author and review text. Returns
    a list of {author_name, rating, text} dictionaries.
    """
    try:
        reviews = []
        # Pattern: Author name followed by [Image: 5 stars] and the review paragraph
        pat = re.compile(r'([A-Z][A-Za-z .]{2,80})\s*\[Image:\s*5\s*stars\]\s*(?:[0-9]+\s+(?:months?|years?|days?)\s+ago\s*)?([\s\S]{30,500}?)(?=(?:Like\s|Share\s|\.{3}|\[Image:|Map data))', re.I)
        for m in pat.finditer(html):
            author = re.sub(r"\s+", " ", m.group(1)).strip()
            text = re.sub(r"\s+", " ", m.group(2)).strip()
            text = re.sub(r"\.{2,}$", "", text).strip()
            text = text.split(' Like')[0].split('Share')[0].strip()
            if len(text) > 10:
                reviews.append({"author_name": author, "rating": 5, "text": text})

        # Fallback: look for 'X months ago' followed by a review paragraph
        if not reviews:
            pat2 = re.compile(r"\d+\s+(?:months?|days?|years?)\s+ago\s+([\s\S]{30,400}?)(?=(?:Like\s|Share\s|\.{3}|\[Image:|Map data))", re.I)
            for m in pat2.finditer(html):
                text = re.sub(r"\s+", " ", m.group(1)).strip()
                text = text.split(' Like')[0].split('Share')[0].strip()
                if len(text) > 10:
                    reviews.append({"author_name": "Customer", "rating": 5, "text": text})

        return reviews
    except Exception:
        logging.exception("Failed to parse share HTML for reviews")
        return []





@app.route("/api/create_order", methods=["POST"])
def create_order():
    """
    Expects JSON booking summary: { amount: "1200", currency: "INR", service: "...", ... }
    Returns Razorpay order (id, amount) to open checkout on client.
    """
    body = request.get_json(force=True)
    if not body:
        return jsonify({"error":"missing json"}), 400
    amount_rupees = body.get("amount")
    if not amount_rupees:
        return jsonify({"error":"missing amount"}), 400
    try:
        amount_paise = int(float(amount_rupees) * 100)  # Razorpay needs paise
    except Exception:
        return jsonify({"error":"invalid amount"}), 400

    if not rzp_client:
        return jsonify({"error":"Razorpay keys not configured"}), 500

    # create razorpay order
    order_data = {
        "amount": amount_paise,
        "currency": body.get("currency", "INR"),
        "receipt": f"rcpt_{int(datetime.utcnow().timestamp())}",
        "payment_capture": 1  # auto-capture
    }
    try:
        order = rzp_client.order.create(data=order_data)
        # Return order + pass-through booking summary (you may also store temporary record)
        return jsonify({"order": order, "booking": body}), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500





@app.route("/api/verify_payment", methods=["POST"])
def verify_payment_and_save():
    """
    Verify signature from client and save booking to DB when payment is successful.
    Expects JSON:
    {
      "razorpay_payment_id": "...",
      "razorpay_order_id": "...",
      "razorpay_signature": "...",
      "booking": { ... }  // same booking summary and user info
    }
    """
    data = request.get_json(force=True)
    if not data:
        return jsonify({"error":"missing json"}), 400

    payment_id = data.get("razorpay_payment_id")
    order_id = data.get("razorpay_order_id")
    signature = data.get("razorpay_signature")
    booking = data.get("booking")

    if not payment_id or not order_id or not signature or not booking:
        return jsonify({"error":"missing required fields"}), 400

    # Verify signature: hmac_sha256(order_id + '|' + payment_id, RZP_KEY_SECRET)
    msg = f"{order_id}|{payment_id}"
    generated_signature = hmac.new(
        RZP_KEY_SECRET.encode('utf-8'),
        msg.encode('utf-8'),
        hashlib.sha256
    ).hexdigest()

    if not hmac.compare_digest(generated_signature, signature):
        return jsonify({"error":"signature mismatch"}), 400

    # Payment verified -> save booking into DB (record payment id/status)
    try:
        conn = get_db_conn()
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO bookings
              (service_type, from_city, from_point, to_city, to_point, journey_date, journey_time, pickup_time, seats, amount, user_name, user_phone, user_email, external_id, payment_status, payment_id, webhook_payload, created_at)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            RETURNING id, user_name, user_phone, user_email, from_city, to_city, journey_date, seats, amount
            """,
            (
                (booking.get("service_type") or booking.get("service")),
                canonicalize_city(booking.get("from")),
                booking.get("from_point"),
                canonicalize_city(booking.get("to")),
                booking.get("to_point"),
                booking.get("date") or None,
                booking.get("time"),
                booking.get("pickup_time") or None,
                int(booking.get("seats") or 0),
                float(booking.get("amount") or 0),
                booking.get("user_name"),
                booking.get("user_phone"),
                booking.get("user_email"),
                order_id,
                'paid',
                payment_id,
                json.dumps(data),
                datetime.utcnow(),
            ),
        )
        row = cur.fetchone()
        conn.commit()
        cur.close()

        # Notify admin via Telegram (do not send to user)
        try:
            msg = (
                f"<b>Payment Received</b>\n"
                f"Booking ID: {row.get('id')}\n"
                f"Payment ID: {payment_id}\n"
                f"Order ID: {order_id}\n"
                f"Amount: {float(row.get('amount') or 0)}\n"
                f"Name: {row.get('user_name')}\n"
                f"Phone: {row.get('user_phone')}\n"
                f"From: {row.get('from_city')} → To: {row.get('to_city')}\n"
                f"Date: {row.get('journey_date')}\n"
                f"Seats: {row.get('seats')}\n"
            )
            if APP_BASE_URL:
                msg += f"\nAdmin: {APP_BASE_URL.rstrip('/')}/admin"
            send_telegram_to_admin(msg)
        except Exception:
            logging.exception("Failed to build/send admin notification")

        return jsonify({"status":"ok","id": row["id"], "payment_id": payment_id}), 201
    except Exception as e:
        logging.exception('Failed to save booking after payment')
        return jsonify({"error": str(e)}), 500


@app.route("/api/webhook/razorpay", methods=["POST"])
def razorpay_webhook():
    """Endpoint to receive Razorpay webhooks.

    Verifies the signature, stores the raw webhook, updates matching booking
    by `external_id` (order id) and notifies the admin Telegram chat.
    """
    raw_body = request.get_data()
    signature = request.headers.get("X-Razorpay-Signature") or request.headers.get("x-razorpay-signature")
    secret = RZP_WEBHOOK_SECRET or RZP_KEY_SECRET
    if not signature or not secret:
        return jsonify({"error": "missing signature or webhook secret"}), 400

    # Verify signature
    try:
        if rzp_client:
            # razorpay SDK expects the body as string
            rzp_client.utility.verify_webhook_signature(raw_body.decode("utf-8"), signature, secret)
        else:
            gen = hmac.new(secret.encode("utf-8"), raw_body, hashlib.sha256).hexdigest()
            if not hmac.compare_digest(gen, signature):
                return jsonify({"error": "signature mismatch"}), 400
    except Exception:
        logging.exception("Webhook signature verification failed")
        return jsonify({"error": "signature mismatch"}), 400

    # Parse payload
    try:
        payload = json.loads(raw_body.decode("utf-8"))
    except Exception:
        logging.exception("Invalid webhook payload")
        return jsonify({"error": "invalid json"}), 400

    # Persist raw webhook for debugging
    try:
        conn = get_db_conn()
        cur = conn.cursor()
        payment_entity = payload.get("payload", {}).get("payment", {}).get("entity", {}) or {}
        order_id = payment_entity.get("order_id")
        cur.execute(
            "INSERT INTO payment_webhooks (provider, external_id, payload, headers, processed) VALUES (%s,%s,%s,%s,%s)",
            ("razorpay", order_id, json.dumps(payload), json.dumps(dict(request.headers)), False),
        )
        conn.commit()
        cur.close()
    except Exception:
        logging.exception("Failed to persist webhook")

    event = payload.get("event")
    # Handle payment events
    if event and event.startswith("payment."):
        payment_entity = payload.get("payload", {}).get("payment", {}).get("entity", {}) or {}
        order_id = payment_entity.get("order_id")
        payment_id = payment_entity.get("id")
        status = payment_entity.get("status")
        amount = (payment_entity.get("amount") or 0) / 100.0
        updated = None
        try:
            conn = get_db_conn()
            cur = conn.cursor()
            cur.execute(
                "UPDATE bookings SET payment_status=%s, payment_id=%s, webhook_payload=%s WHERE external_id=%s RETURNING id, user_name, user_phone, user_email, from_city, to_city, journey_date, seats, amount",
                (status, payment_id, json.dumps(payload), order_id),
            )
            updated = cur.fetchone()
            conn.commit()
            cur.close()
        except Exception:
            logging.exception("Failed to update booking from webhook")
            updated = None

        if updated:
            try:
                msg = (
                    f"<b>New Booking Paid</b>\n"
                    f"Booking ID: {updated.get('id')}\n"
                    f"Payment ID: {payment_id}\n"
                    f"Order ID: {order_id}\n"
                    f"Amount: {amount}\n"
                    f"Name: {updated.get('user_name')}\n"
                    f"Phone: {updated.get('user_phone')}\n"
                    f"From: {updated.get('from_city')} → {updated.get('to_city')}\n"
                    f"Date: {updated.get('journey_date')}\n"
                    f"Seats: {updated.get('seats')}\n"
                )
                if APP_BASE_URL:
                    msg += f"\nAdmin: {APP_BASE_URL.rstrip('/')}/admin"
                send_telegram_to_admin(msg)
            except Exception:
                logging.exception("Failed to notify admin about webhook booking")

    return jsonify({"status": "ok"}), 200


@app.route("/api/google_reviews")
def api_google_reviews():
    """Return recent Google reviews for the configured place.

    If `GOOGLE_API_KEY` (and optionally `GOOGLE_PLACE_ID` or `GOOGLE_PLACE_NAME`) are set,
    this will attempt to fetch live reviews. Otherwise a small static fallback is returned.
    """
    share_url = request.args.get('share_url')

    # Try live Places API first (if configured)
    if GOOGLE_API_KEY:
        data = fetch_google_place_reviews(GOOGLE_API_KEY, place_id=GOOGLE_PLACE_ID, place_name=GOOGLE_PLACE_NAME)
        if not data.get("error"):
            return jsonify({"source": "google", "place": data.get("name"), "place_id": data.get("place_id"), "reviews": data.get("reviews", [])}), 200

    # If a share URL is provided, try to fetch and parse its HTML for review text
    if share_url:
        try:
            headers = {"User-Agent": "Mozilla/5.0 (compatible)"}
            r = requests.get(share_url, headers=headers, timeout=8)
            if r.ok:
                items = extract_reviews_from_share_html(r.text)
                if items:
                    return jsonify({"source": "share", "reviews": items}), 200
        except Exception:
            logging.exception("Failed to fetch/parse share URL")

    # fallback static reviews (user-provided content)
    static_reviews = [
        {"author_name": "Dr. Sandip Trivedi", "rating": 5, "text": "I had a wonderful experience with this cab service. The booking process was easy, the driver arrived on time, and the vehicle was neat and well-maintained. Truly reliable service — will definitely use again and recommend to others.", "relative_time_description": "2 months ago"},
        {"author_name": "Customer", "rating": 5, "text": "Very well maintained car, professional and safe driving experience.", "relative_time_description": ""},
        {"author_name": "Customer", "rating": 5, "text": "Excellent Service, Clean and new cars Good Driver,Ok time service", "relative_time_description": ""},
    ]
    return jsonify({"source": "static", "reviews": static_reviews}), 200


@app.route("/api/config")
def get_config():
    return jsonify({
        "rzp_key_id": RZP_KEY_ID or "",
        "merchant_upi": MERCHANT_UPI or ""
    })


@app.route("/api/save_failed", methods=["POST"])
def api_save_failed():
    """
    Save a booking marked as failed (Razorpay checkout dismissed or payment failed).
    Expects JSON: { booking: {...}, reason: "dismissed" | "failed", failure: {...} }
    """
    data = request.get_json(force=True) or {}
    booking = data.get("booking") or {}
    failure = data.get("failure") or data.get("reason") or None
    payment_resp = data.get("payment_response") or None
    try:
        conn = get_db_conn()
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO bookings
              (service_type, from_city, from_point, to_city, to_point, journey_date, journey_time, pickup_time, seats, amount, user_name, user_phone, user_email, payment_status, created_at, webhook_payload)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            RETURNING id
            """,
            (
                (booking.get("service_type") or booking.get("service")),
                canonicalize_city(booking.get("from")),
                booking.get("from_point"),
                canonicalize_city(booking.get("to")),
                booking.get("to_point"),
                booking.get("date") or None,
                booking.get("time"),
                booking.get("pickup_time") or None,
                int(booking.get("seats") or 0),
                float(booking.get("amount") or 0),
                booking.get("user_name"),
                booking.get("user_phone"),
                booking.get("user_email"),
                "failed",
                datetime.utcnow(),
                json.dumps({"failure": failure, "payment_response": payment_resp}),
            ),
        )
        row = cur.fetchone()
        conn.commit()
        cur.close()
        bid = row[0] if row and not isinstance(row, dict) else (row["id"] if row and isinstance(row, dict) and "id" in row else None)
        return jsonify({"status":"ok","id": bid}), 201
    except Exception as e:
        logging.exception("Failed to persist failed booking")
        return jsonify({"error": str(e)}), 500


@app.route("/payment")
def payment_page():
    """
    Render payment page showing ticket details. Expects query param `booking_id`.
    """
    booking_id = request.args.get("booking_id")
    if not booking_id:
        return render_template("payment.html", error="Missing booking_id")
    try:
        conn = get_db_conn()
        cur = conn.cursor()
        cur.execute("SELECT * FROM bookings WHERE id = %s", (int(booking_id),))
        row = cur.fetchone()
        cur.close()
        if not row:
            return render_template("payment.html", error="Booking not found")
        return render_template("payment.html", booking=row)
    except Exception:
        logging.exception("Failed to load booking for payment page")
        return render_template("payment.html", error="Server error"), 500


@app.route("/_ping")
def ping():
    """Lightweight health endpoint for uptime probes."""
    return "OK", 200


# Debug endpoint: list registered routes (safe to remove after debugging)
@app.route("/_routes")
def _routes():
    routes = []
    for rule in sorted(app.url_map.iter_rules(), key=lambda r: r.rule):
        routes.append({
            "rule": rule.rule,
            "endpoint": rule.endpoint,
            "methods": sorted([m for m in rule.methods if m not in ("HEAD", "OPTIONS")])
        })
    return jsonify({"routes": routes})


# Log registered routes at startup
try:
    logging.info("Registered routes: %s", [r.rule for r in app.url_map.iter_rules()])
except Exception:
    pass

# Debug prints to ensure output appears in Render logs during import
try:
    print("APP_MODULE_FILE:", __file__)
    print("REGISTERED_ROUTES:", [r.rule for r in app.url_map.iter_rules()])
except Exception as _e:
    print("ROUTE_LIST_ERROR:", _e)


if __name__ == "__main__":
    # Use port from environment for compatibility with hosts like Render
    port = int(os.environ.get("PORT", 5000))
    debug = os.environ.get("FLASK_DEBUG", "0") in ("1", "true", "True")
    app.run(debug=debug, host="0.0.0.0", port=port)
    