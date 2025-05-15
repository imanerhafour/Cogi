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
import psycopg2

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
    email = data.get("email", "").lower()  # 🔒 Normaliser l'email
    conn = get_db_connection()
    cur = conn.cursor()

    # Vérifier si l'email existe déjà
    cur.execute("SELECT 1 FROM users WHERE email = %s", (email,))
    if cur.fetchone():
        cur.close()
        conn.close()
        return False  # Email déjà utilisé

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
            data.get("dob") or None,  # ✅ Accepte None si vide
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



def is_strong_password(password):
    return re.match(r'^(?=.*[a-z])(?=.*[A-Z])(?=.*\d)(?=.*[!@#$%^&*(),.?\":{}|<>]).{8,}$', password)


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


def generate_bot_response(user_message):
    return "Ceci est une réponse automatique (à remplacer par ton modèle IA)"


# ---------- Routes principales ----------

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/chat', methods=['GET', 'POST'])
def chat():
    if "user" not in session:
        return redirect(url_for("login"))

    email = session["user"]
    user_data = get_user_by_email(email)

    if request.method == 'POST':
        user_message = request.form['message']
        bot_reply = generate_bot_response(user_message)

        # Sauvegarde dans la base PostgreSQL
        save_message(email, user_message, 'user')
        save_message(email, bot_reply, 'bot')

    # Charger l'historique depuis la base
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("""
        SELECT message, sender, timestamp
        FROM conversations
        WHERE user_email = %s
        ORDER BY timestamp ASC
    """, (email,))
    history = cur.fetchall()
    cur.close()
    conn.close()

    return render_template('chat.html',
                           username=email,
                           first_name=user_data.get("first_name", ""),
                           last_name=user_data.get("last_name", ""),
                           history=history)




@app.route('/login', methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form.get("username")
        password = request.form.get("password")

        if not username or not password:
            flash('Username and password are required!', 'error')
            return redirect(url_for("login"))

        user_data = get_user_by_email(username)
        if not user_data:
            flash("Unknown user.", "error")
            return redirect(url_for("login"))

        if not user_data.get("confirmed", False):
            flash("Please confirm your email before logging in.", "error")
            return redirect(url_for("login"))

        if user_data.get("attempts", 0) >= MAX_ATTEMPTS:
            flash("Too many failed attempts. Try again later.", "error")
            return redirect(url_for("login"))

        if not check_password_hash(user_data["password"], password):
            # Incrémenter les tentatives
            conn = get_db_connection()
            cur = conn.cursor()
            cur.execute("UPDATE users SET attempts = attempts + 1 WHERE email = %s", (username,))
            conn.commit()
            cur.close()
            conn.close()

            flash("Incorrect username or password.", "error")
            return redirect(url_for("login"))

        # Réinitialiser les tentatives
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("UPDATE users SET attempts = 0 WHERE email = %s", (username,))
        conn.commit()
        cur.close()
        conn.close()

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
        if not secret_key:
            flash("Configuration CAPTCHA manquante (clé secrète).", "error")
            return redirect(url_for("register"))

        payload = {'secret': secret_key, 'response': recaptcha_response}
        try:
            captcha_check = requests.post("https://www.google.com/recaptcha/api/siteverify", data=payload).json()
            print("CAPTCHA Google Response:", captcha_check)
        except Exception as e:
            flash("Erreur lors de la vérification CAPTCHA : " + str(e), "error")
            return redirect(url_for("register"))

        if not captcha_check.get('success'):
            flash("CAPTCHA verification failed. Code: " + ", ".join(captcha_check.get("error-codes", [])), "error")
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

    # ✅ Mise à jour dans PostgreSQL
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("UPDATE users SET confirmed = TRUE WHERE email = %s", (email.lower(),))
    conn.commit()
    cur.close()
    conn.close()

    flash("Email confirmé. Tu peux te connecter.", "success")
    return redirect(url_for("login"))


@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('index'))

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
        email = session['user']
        user_data = get_user_by_email(email)
        if user_data:
            return {
                'first_name': user_data.get('first_name', ''),
                'last_name': user_data.get('last_name', '')
            }
    return {'first_name': '', 'last_name': ''}


@app.route('/reset_request', methods=["GET", "POST"])
def reset_request():
    if request.method == "POST":
        email = request.form.get("email")
        users = load_users()

        if not email or email not in users:
            flash("Aucun compte associé à cet email.", "error")
            return redirect(url_for("reset_request"))

        token = s.dumps(email, salt='reset-password')
        reset_link = url_for("reset_token", token=token, _external=True)

        msg = Message("Réinitialisation de mot de passe",
                      sender=app.config['MAIL_USERNAME'],
                      recipients=[email])
        msg.body = f"Clique ici pour réinitialiser ton mot de passe : {reset_link}"
        mail.send(msg)

        flash("Un lien de réinitialisation a été envoyé à ton adresse email.", "success")
        return redirect(url_for("login"))

    return render_template("reset_request.html")

@app.route('/reset/<token>', methods=["GET", "POST"])
def reset_token(token):
    try:
        email = s.loads(token, salt='reset-password', max_age=3600)
    except SignatureExpired:
        flash("Le lien a expiré.", "danger")
        return redirect(url_for("reset_request"))
    except BadSignature:
        flash("Lien invalide ou modifié.", "danger")
        return redirect(url_for("reset_request"))

    if request.method == "POST":
        password = request.form.get("password")
        confirm = request.form.get("confirm")

        if password != confirm:
            flash("Les mots de passe ne correspondent pas.", "error")
            return render_template("reset_token.html", token=token)

        if not is_strong_password(password):
            flash("Mot de passe trop faible.", "error")
            return render_template("reset_token.html", token=token)

        users = load_users()
        users[email]["password"] = generate_password_hash(password, method='pbkdf2:sha256')
        save_users(users)

        flash("Mot de passe réinitialisé. Tu peux te connecter.", "success")
        return redirect(url_for("login"))

    return render_template("reset_token.html", token=token)


# ---------- Migration vers  de  PostgreSQL----------
def get_db_connection():
    return psycopg2.connect(
        dbname="cogi_db",
        user="mac",  
        password="", 
        host="localhost"
    )

# ---------- Sauvegarde de l'historique des chats  ----------
def save_message(user_email, message, sender):
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO conversations (user_email, message, sender)
        VALUES (%s, %s, %s)
    """, (user_email, message, sender))
    conn.commit()
    cur.close()
    conn.close()


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
