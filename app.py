from flask import Flask, render_template, request, redirect, url_for, session, flash, jsonify
from werkzeug.security import check_password_hash, generate_password_hash
from datetime import timedelta
from flask_mail import Mail, Message
from itsdangerous import URLSafeTimedSerializer, SignatureExpired, BadSignature
import os
import requests
from openai import OpenAI
from dotenv import load_dotenv
import re
import psycopg2
import uuid

load_dotenv()

app = Flask(__name__)
app.secret_key = 'supersecretkey'
app.permanent_session_lifetime = timedelta(minutes=30)

# OpenAI Client
api_key = os.environ.get("TOGETHER_API_KEY", "").strip()
if not api_key:
    raise ValueError("TOGETHER_API_KEY is missing")
client = OpenAI(api_key=api_key, base_url="https://api.together.xyz/v1")

# Mail Config
app.config['MAIL_SERVER'] = 'sandbox.smtp.mailtrap.io'
app.config['MAIL_PORT'] = 587
app.config['MAIL_USE_TLS'] = True
app.config['MAIL_USERNAME'] = os.environ.get("EMAIL_USER")
app.config['MAIL_PASSWORD'] = os.environ.get("EMAIL_PASS")
mail = Mail(app)

s = URLSafeTimedSerializer(app.secret_key)
MAX_ATTEMPTS = 5

# ------------------- Utils -------------------

def is_strong_password(password):
    return re.match(r'^(?=.*[a-z])(?=.*[A-Z])(?=.*\d)(?=.*[!@#$%^&*(),.?":{}|<>]).{8,}$', password)

def get_db_connection():
    return psycopg2.connect(dbname="cogi_db", user="mac", password="", host="localhost")

def get_user_by_email(email):
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT email, password, first_name, last_name, confirmed, attempts FROM users WHERE email = %s", (email.lower(),))
    result = cur.fetchone()
    cur.close()
    conn.close()
    if result:
        return {
            "email": result[0],
            "password": result[1],
            "first_name": result[2],
            "last_name": result[3],
            "confirmed": result[4],
            "attempts": result[5]
        }
    return None

def save_user(data):
    email = data.get("email", "").lower()
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT 1 FROM users WHERE email = %s", (email,))
    if cur.fetchone():
        cur.close()
        conn.close()
        return False

    try:
        cur.execute("""
            INSERT INTO users (email, password, first_name, last_name, gender, dob, confirmed, attempts)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        """, (
            email,
            generate_password_hash(data.get("password"), method='pbkdf2:sha256'),
            data.get("first_name"),
            data.get("last_name"),
            data.get("gender"),
            data.get("dob") or None,
            False,
            0
        ))
        conn.commit()
        return True
    except Exception as e:
        print("❌ Erreur enregistrement utilisateur :", e)
        conn.rollback()
        return False
    finally:
        cur.close()
        conn.close()

def save_message(email, message, sender, session_id):
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO conversations (user_email, message, sender, session_id)
        VALUES (%s, %s, %s, %s)
    """, (email, message, sender, session_id))
    conn.commit()
    cur.close()
    conn.close()

def generate_bot_response(user_message):
    return "Ceci est une réponse automatique (à remplacer par ton modèle IA)"

# ------------------- Routes -------------------
@app.route('/')
def index():
    return render_template('index.html')

@app.route('/chat', methods=['GET', 'POST'])
def chat():
    if "user" not in session:
        return redirect(url_for("login"))

    email = session["user"]
    user_data = get_user_by_email(email)

    if "session_id" not in session or request.args.get("new"):
        session["session_id"] = str(uuid.uuid4())

    current_session_id = session["session_id"]

    if request.method == 'POST':
        user_message = request.form['message']
        bot_reply = generate_bot_response(user_message)
        save_message(email, user_message, 'user', current_session_id)
        save_message(email, bot_reply, 'bot', current_session_id)

    conn = get_db_connection()
    cur = conn.cursor()

    cur.execute("SELECT DISTINCT session_id, MIN(timestamp) FROM conversations WHERE user_email = %s GROUP BY session_id ORDER BY MIN(timestamp) DESC", (email,))
    sessions = cur.fetchall()
    session_list = [{'id': s[0], 'timestamp': s[1]} for s in sessions]

    cur.execute("SELECT message, sender, timestamp FROM conversations WHERE user_email = %s AND session_id = %s ORDER BY timestamp ASC", (email, current_session_id))
    history = cur.fetchall()
    cur.close()
    conn.close()

    return render_template('chat.html',
                           username=email,
                           first_name=user_data.get("first_name", ""),
                           last_name=user_data.get("last_name", ""),
                           history=history,
                           sessions=session_list,
                           active_id=current_session_id)

@app.route('/login', methods=["GET", "POST"])
def login():
    # login code
    pass

@app.route('/register', methods=["GET", "POST"])
def register():
    # registration code
    pass

@app.route('/confirm/<token>')
def confirm_email(token):
    # confirmation code
    pass

@app.route('/reset_request', methods=["POST"])
def reset_request():
    # reset email code
    pass

@app.route('/reset/<token>', methods=["GET", "POST"])
def reset_token(token):
    # reset token code
    pass

@app.route('/send_message', methods=["POST"])
def send_message():
    # API message code
    pass

@app.route('/feedback', methods=["GET", "POST"])
def feedback():
    # feedback form + display
    pass

@app.context_processor
def inject_user_info():
    if 'user' in session:
        email = session['user']
        user_data = get_user_by_email(email)
        if user_data:
            return {
                'first_name': user_data.get('first_name', ''),
                'last_name': user_data.get('last_name', '')
            }
    return {'first_name': '', 'last_name': ''}

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('index'))

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
