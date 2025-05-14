from flask import Flask, render_template, request, redirect, url_for, session, flash, jsonify
from werkzeug.security import check_password_hash, generate_password_hash
from datetime import timedelta
from flask_mail import Mail, Message
from itsdangerous import URLSafeTimedSerializer, SignatureExpired, BadSignature
import json
import os
import requests
from openai import OpenAI
from dotenv import load_dotenv
import re

# Charger les variables d'environnement
load_dotenv()

# Configuration API Together (OpenAI compatible)
api_key = os.environ.get("TOGETHER_API_KEY", "").strip()
if not api_key:
    raise ValueError("TOGETHER_API_KEY is missing")

client = OpenAI(
    api_key=api_key,
    base_url="https://api.together.xyz/v1"
)

app = Flask(__name__)
app.secret_key = 'supersecretkey'
app.permanent_session_lifetime = timedelta(minutes=30)

# Config Mail
app.config['MAIL_SERVER'] = 'sandbox.smtp.mailtrap.io'
app.config['MAIL_PORT'] = 587
app.config['MAIL_USE_TLS'] = True
app.config['MAIL_USERNAME'] = os.environ.get("EMAIL_USER")
app.config['MAIL_PASSWORD'] = os.environ.get("EMAIL_PASS")
mail = Mail(app)

s = URLSafeTimedSerializer(app.secret_key)
USERS_FILE = 'users.json'
MAX_ATTEMPTS = 5

# ---------- Fonctions Utilitaires ----------

def load_users():
    if not os.path.exists(USERS_FILE):
        return {}
    with open(USERS_FILE, 'r') as f:
        return json.load(f)

def save_users(users):
    with open(USERS_FILE, 'w') as f:
        json.dump(users, f, indent=2)

def save_user(data):
    users = load_users()
    email = data.get("email")
    if email in users:
        return False
    users[email] = {
        "first_name": data.get("first_name"),
        "last_name": data.get("last_name"),
        "email": email,
        "password": generate_password_hash(data.get("password"), method='pbkdf2:sha256'),
        "gender": data.get("gender"),
        "dob": data.get("dob"),
        "confirmed": False,
        "attempts": 0
    }
    try:
        save_users(users)
        return True
    except Exception as e:
        print(f"Error while saving: {e}")
        return False

def is_strong_password(password):
    return re.match(r'^(?=.*[a-z])(?=.*[A-Z])(?=.*\d)(?=.*[\W_]).{8,}$', password)

# ---------- Routes principales ----------

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/chat')
def chat():
    if "user" not in session:
        return redirect(url_for("login"))
    users = load_users()
    email = session["user"]
    user_data = users.get(email, {})
    return render_template('chat.html',
                           username=email,
                           first_name=user_data.get("first_name", ""),
                           last_name=user_data.get("last_name", ""))

@app.route('/login', methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form.get("username")
        password = request.form.get("password")

        if not username or not password:
            flash('Username and password are required!', 'error')
            return redirect(url_for("login"))

        users = load_users()
        user_data = users.get(username)
        if not user_data:
            flash("Unknown user.", "error")
            return redirect(url_for("login"))

        if not user_data.get("confirmed", False):
            flash("Confirme d’abord ton adresse email.", "error")
            return redirect(url_for("login"))

        if user_data.get("attempts", 0) >= MAX_ATTEMPTS:
            flash("Too many failed attempts. Try again later.", "error")
            return redirect(url_for("login"))

        if not check_password_hash(user_data["password"], password):
            user_data["attempts"] += 1
            save_users(users)
            flash("Incorrect username or password.", "error")
            return redirect(url_for("login"))

        user_data["attempts"] = 0
        save_users(users)
        session.permanent = True
        session["user"] = username
        return redirect(url_for("chat"))

    return render_template("login.html")

@app.route('/register', methods=["GET", "POST"])
def register():
    if request.method == "POST":
        form_data = {key: request.form.get(key) for key in ["first_name", "last_name", "email", "password", "gender", "dob"]}
        recaptcha_response = request.form.get('g-recaptcha-response')

        if not all(form_data.values()) or not recaptcha_response:
            flash("Please fill all fields and complete the CAPTCHA.", "error")
            return redirect(url_for("register"))

        secret_key = os.environ.get("RECAPTCHA_SECRET")
        payload = {'secret': secret_key, 'response': recaptcha_response}
        response = requests.post("https://www.google.com/recaptcha/api/siteverify", data=payload)

        if not response.json().get('success'):
            flash("CAPTCHA verification failed.", "error")
            return redirect(url_for("register"))

        if not is_strong_password(form_data["password"]):
            flash("Mot de passe trop faible.", "error")
            return redirect(url_for("register"))

        if not save_user(form_data):
            flash("Cet email est déjà enregistré.", "error")
            return redirect(url_for("register"))

        token = s.dumps(form_data["email"], salt='email-confirm')
        link = url_for('confirm_email', token=token, _external=True)

        msg = Message("Confirme ton adresse email", sender=app.config['MAIL_USERNAME'], recipients=[form_data["email"]])
        msg.body = f"Bienvenue sur Cogi ! Clique ici pour confirmer ton adresse : {link}"
        mail.send(msg)

        flash("Inscription réussie ! Vérifie ton email.", "success")
        return redirect(url_for("login"))

    return render_template("register.html")

@app.route('/confirm/<token>')
def confirm_email(token):
    try:
        email = s.loads(token, salt='email-confirm', max_age=3600)
    except SignatureExpired:
        flash("Lien expiré.", "danger")
        return redirect(url_for("register"))
    except BadSignature:
        flash("Lien invalide.", "danger")
        return redirect(url_for("register"))

    users = load_users()
    if email in users:
        users[email]["confirmed"] = True
        save_users(users)
        flash("Email confirmé. Tu peux te connecter.", "success")
    return redirect(url_for("login"))

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('index'))

# ---------- Chatbot IA ----------

@app.route("/send_message", methods=["POST"])
def send_message():
    try:
        user_input = request.json.get("message", "")
        if not user_input:
            return jsonify({"reply": "❌ Message vide"}), 400

        response = client.chat.completions.create(
            model="mistralai/Mistral-7B-Instruct-v0.1",
            messages=[
                {"role": "system", "content": "You are a helpful mental health assistant."},
                {"role": "user", "content": user_input},
            ],
            max_tokens=200,
            temperature=0.7,
        )

        reply = response.choices[0].message.content.strip()
        return jsonify({"reply": reply})

    except Exception as e:
        print("Erreur serveur:", e)
        return jsonify({"reply": "⚠️ Erreur serveur : " + str(e)}), 500

@app.context_processor
def inject_user_info():
    if 'user' in session:
        users = load_users()
        email = session['user']
        if email in users:
            user_data = users[email]
            return {
                'first_name': user_data.get('first_name', ''),
                'last_name': user_data.get('last_name', '')
            }
    return {'first_name': '', 'last_name': ''}

if __name__ == "__main__":
    app.run(debug=True)
