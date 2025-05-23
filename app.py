from flask import Flask, render_template, request, redirect, url_for, session, flash, jsonify
from werkzeug.security import check_password_hash, generate_password_hash
from flask import session, redirect, url_for
from datetime import timedelta
from datetime import datetime
from flask_mail import Mail, Message
from itsdangerous import URLSafeTimedSerializer, SignatureExpired, BadSignature
import os
import requests
from openai import OpenAI
from dotenv import load_dotenv
import re
import psycopg2
import uuid

# ------------------- Chargement de la configuration -------------------
load_dotenv()

app = Flask(__name__)
app.secret_key = 'supersecretkey'
app.permanent_session_lifetime = timedelta(minutes=10)

# Config API Together
api_key = os.environ.get("TOGETHER_API_KEY", "").strip()
if not api_key:
    raise ValueError("TOGETHER_API_KEY is missing")
client = OpenAI(api_key=api_key, base_url="https://api.together.xyz/v1")

# Config Mail
app.config['MAIL_SERVER'] = 'sandbox.smtp.mailtrap.io'
app.config['MAIL_PORT'] = 587
app.config['MAIL_USE_TLS'] = True
app.config['MAIL_USERNAME'] = os.environ.get("EMAIL_USER")
app.config['MAIL_PASSWORD'] = os.environ.get("EMAIL_PASS")
mail = Mail(app)

s = URLSafeTimedSerializer(app.secret_key)
MAX_ATTEMPTS = 5

# ------------------- Fonctions Utilitaires -------------------

def is_strong_password(password):
    return re.match(r'^(?=.*[a-z])(?=.*[A-Z])(?=.*\d)(?=.*[!@#$%^&*(),.?":{}|<>]).{8,}$', password)

def get_db_connection():
    return psycopg2.connect(dbname="cogi_db", user="mac", password="cogi123", host="localhost", port="5432")

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
    print(f"[DB] Saving message: {sender} | {message} | Session: {session_id}")
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
    try:
        response = client.chat.completions.create(
            model="mistralai/Mistral-7B-Instruct-v0.1",
            messages=[
                {"role": "system", "content": "You are a helpful mental health assistant."},
                {"role": "user", "content": user_message},
            ],
            max_tokens=200,
            temperature=0.7,
        )
        return response.choices[0].message.content.strip()
    except Exception as e:
        print("Erreur API IA:", e)
        return "⚠️ Je n'ai pas pu générer de réponse pour le moment."


# ------------------- Routes -------------------

@app.route('/')
def index():
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute('SELECT name, message, submitted_at FROM feedback ORDER BY id DESC LIMIT 10')
    
    feedbacks = [
        {"name": row[0], "message": row[1], "submitted_at": row[2]}
        for row in cur.fetchall()
    ]

    cur.close()
    conn.close()
    
    return render_template("index.html", feedbacks=feedbacks)






@app.route('/chat', methods=['GET', 'POST'])
def chat():
    if "user" not in session:
        return redirect(url_for("login"))

    email = session["user"]
    user_data = get_user_by_email(email)

    if request.args.get("session_id"):
        session["session_id"] = request.args.get("session_id")

    if request.args.get("new") or "session_id" not in session:
        session["session_id"] = str(uuid.uuid4())

    current_session_id = session["session_id"]

    if request.method == 'POST':
        user_message = request.form.get('message')
        if user_message:
            bot_reply = generate_bot_response(user_message)
            save_message(email, user_message, 'user', current_session_id)
            save_message(email, bot_reply, 'bot', current_session_id)

    conn = get_db_connection()
    cur = conn.cursor()

    cur.execute("""
        SELECT session_id, MIN(timestamp) AS timestamp
        FROM conversations
        WHERE user_email = %s
        GROUP BY session_id
        ORDER BY timestamp DESC
    """, (email,))
    session_rows = cur.fetchall()

    sessions = []
    for sid, timestamp in session_rows:
        cur.execute("SELECT title FROM conversations WHERE session_id = %s AND title IS NOT NULL ORDER BY timestamp LIMIT 1", (sid,))
        title_result = cur.fetchone()
        title = title_result[0] if title_result else None
        sessions.append({"id": sid, "timestamp": timestamp, "title": title})

    cur.execute("""
        SELECT message, sender, timestamp
        FROM conversations
        WHERE user_email = %s AND session_id = %s
        ORDER BY timestamp ASC
    """, (email, current_session_id))
    history = cur.fetchall()
    cur.close()
    conn.close()

    return render_template("chat.html", username=email, first_name=user_data.get("first_name", ""), last_name=user_data.get("last_name", ""), history=history, sessions=sessions, active_id=current_session_id)


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

        if not user_data.get("confirmed"):
            flash("Please confirm your email before logging in.", "error")
            return redirect(url_for("login"))

        if user_data["attempts"] >= MAX_ATTEMPTS:
            flash("Too many failed attempts. Try again later.", "error")
            return redirect(url_for("login"))

        if not check_password_hash(user_data["password"], password):
            conn = get_db_connection()
            cur = conn.cursor()
            cur.execute("UPDATE users SET attempts = attempts + 1 WHERE email = %s", (username,))
            conn.commit()
            cur.close()
            conn.close()
            flash("Incorrect username or password.", "error")
            return redirect(url_for("login"))

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

from datetime import date

@app.route('/register', methods=["GET", "POST"])
def register():
    if request.method == "POST":
        form_data = {
            key: request.form.get(key)
            for key in ["first_name", "last_name", "email", "password", "gender", "dob"]
        }
        recaptcha_response = request.form.get('g-recaptcha-response')

        # Check for missing fields
        if not all(form_data.values()) or not recaptcha_response:
            flash("Veuillez remplir tous les champs et valider le CAPTCHA.", "error")
            return redirect(url_for("register"))

        # CAPTCHA validation
        secret_key = os.environ.get("RECAPTCHA_SECRET")
        payload = {'secret': secret_key, 'response': recaptcha_response}
        captcha_check = requests.post("https://www.google.com/recaptcha/api/siteverify", data=payload).json()
        if not captcha_check.get('success'):
            flash("Échec de la vérification CAPTCHA.", "error")
            return redirect(url_for("register"))

        # Date of birth validation
        try:
            dob = date.fromisoformat(form_data["dob"])
            today = date.today()
            age = today.year - dob.year - ((today.month, today.day) < (dob.month, dob.day))

            if dob > today:
                flash("Date of birth cannot be in the future.", "error")
                return redirect(url_for("register"))
            if age < 13:
                flash("You must be at least 13 years old to register.", "error")
                return redirect(url_for("register"))
            if age > 100:
                flash("Please enter a realistic date of birth (under 100 years old).", "error")
                return redirect(url_for("register"))
        except ValueError:
            flash("Invalid date format for date of birth.", "error")
            return redirect(url_for("register"))

        # Password strength check
        if not is_strong_password(form_data["password"]):
            flash("Mot de passe trop faible.", "error")
            return redirect(url_for("register"))

        # Save user
        if not save_user(form_data):
            flash("Cet email est déjà enregistré.", "error")
            return redirect(url_for("register"))

        # Email confirmation
        token = s.dumps(form_data["email"], salt='email-confirm')
        link = url_for('confirm_email', token=token, _external=True)

        msg = Message(
            "Confirme ton adresse email",
            sender=app.config['MAIL_USERNAME'],
            recipients=[form_data["email"]]
        )
        msg.body = f"Bienvenue sur Cogi ! Clique ici pour confirmer ton adresse : {link}"
        mail.send(msg)

        flash("Inscription réussie ! Vérifie ton email pour activer ton compte.", "success")
        return redirect(url_for("login"))

    # GET method: show form with current date
    return render_template("register.html", current_date=date.today())


@app.route('/confirm/<token>')
def confirm_email(token):
    try:
        email = s.loads(token, salt='email-confirm', max_age=3600)
    except (SignatureExpired, BadSignature):
        flash("Lien invalide ou expiré.", "danger")
        return redirect(url_for("register"))

    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("UPDATE users SET confirmed = TRUE WHERE email = %s", (email.lower(),))
    conn.commit()
    cur.close()
    conn.close()

    flash("Email confirmé. Tu peux te connecter.", "success")
    return redirect(url_for("login"))

@app.route('/reset_request', methods=["POST"])
def reset_request():
    email = request.form.get("email")
    user = get_user_by_email(email)
    if not user:
        return jsonify({"status": "error", "message": "Aucun compte associé à cet email."}), 404

    token = s.dumps(email, salt='reset-password')
    reset_link = url_for("reset_token", token=token, _external=True)

    msg = Message("Réinitialisation du mot de passe", sender=app.config['MAIL_USERNAME'], recipients=[email])
    msg.body = f"Clique ici pour réinitialiser ton mot de passe : {reset_link}"
    try:
        mail.send(msg)
        return jsonify({"status": "success", "message": "Lien envoyé à ton adresse email."})
    except Exception as e:
        print("Erreur envoi email :", e)
        return jsonify({"status": "error", "message": "Erreur lors de l'envoi de l'email."}), 500

@app.route('/reset/<token>', methods=["GET", "POST"])
def reset_token(token):
    try:
        email = s.loads(token, salt='reset-password', max_age=3600)
    except (SignatureExpired, BadSignature):
        flash("Lien invalide ou expiré.", "danger")
        return redirect(url_for("login"))

    if request.method == "POST":
        password = request.form.get("password")
        confirm = request.form.get("confirm")

        if password != confirm:
            flash("Les mots de passe ne correspondent pas.", "error")
            return render_template("reset_token.html", token=token)

        if not is_strong_password(password):
            flash("Mot de passe trop faible.", "error")
            return render_template("reset_token.html", token=token)

        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("UPDATE users SET password = %s WHERE email = %s", (
            generate_password_hash(password, method='pbkdf2:sha256'),
            email
        ))
        conn.commit()
        cur.close()
        conn.close()

        flash("Mot de passe réinitialisé. Tu peux te connecter.", "success")
        return redirect(url_for("login"))

    return render_template("reset_token.html", token=token)

@app.route("/send_message", methods=["POST"])
def send_message():
    if "user" not in session:
        return jsonify({"reply": "Not authenticated"}), 401

    user_input = request.json.get("message", "")
    session_id = session.get("session_id")

    if not user_input:
        return jsonify({"reply": "❌ Message vide"}), 400

    try:
        # IA response
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

        # 🔒 Sauvegarde DB
        save_message(session["user"], user_input, 'user', session_id)
        save_message(session["user"], reply, 'bot', session_id)

        return jsonify({"reply": reply})
    except Exception as e:
        print("Erreur serveur:", e)
        return jsonify({"reply": f"⚠️ Erreur serveur : {e}"}), 500


@app.route('/feedback', methods=["GET", "POST"])
def feedback():
    if "user" not in session:
        flash("You must be logged in to send feedback.", "warning")
        return redirect(url_for("login"))

    conn = get_db_connection()
    cur = conn.cursor()

    if request.method == "POST":
        name = request.form.get("name")
        message = request.form.get("message")

        if not name or not message:
            flash("Both name and message are required.", "error")
            return redirect(url_for("index"))

        try:
            cur.execute(
                "INSERT INTO feedback (name, message) VALUES (%s, %s)",
                (name, message)
            )
            conn.commit()
            flash("✅ Feedback sent successfully!", "success")
        except Exception as e:
            conn.rollback()
            flash(f"Error submitting feedback: {e}", "error")

        cur.close()
        conn.close()
        return redirect(url_for("index"))

    else:  # GET method — afficher tous les feedbacks
        cur.execute("SELECT name, message, submitted_at FROM feedback ORDER BY submitted_at DESC")
        feedbacks = cur.fetchall()
        cur.close()
        conn.close()
        return render_template("feedback.html", feedbacks=[
            {"name": row[0], "message": row[1], "submitted_at": row[2]} for row in feedbacks
        ])



@app.route('/rename_chat', methods=["POST"])
def rename_chat():
    chat_id = request.form.get("chat_id")
    new_title = request.form.get("new_title", "").strip()
 
    if not new_title:
        flash("Le titre ne peut pas être vide.", "warning")
        return redirect(url_for("chat", session_id=chat_id))

    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("UPDATE conversations SET title = %s WHERE session_id = %s", (new_title, chat_id))
    conn.commit()
    cur.close()
    conn.close()

    flash("✅ Chat renamed successfully.", "success")
    return redirect(url_for("chat", session_id=chat_id))


@app.route('/delete_chat', methods=["POST"])
def delete_chat():
    chat_id = request.form.get("chat_id")
    if not chat_id:
        flash("Invalid delete request", "danger")
        return redirect(url_for("chat"))

    conn = get_db_connection()
    cur = conn.cursor()
    try:
        cur.execute("DELETE FROM conversations WHERE session_id = %s", (chat_id,))
        conn.commit()
    except Exception as e:
        flash(f"Delete failed: {e}", "danger")
    finally:
        cur.close()
        conn.close()
    return redirect(url_for("chat"))



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

@app.before_request
def check_session_timeout():
    if 'user' in session:
        session.modified = True
        now = datetime.utcnow()
        last_activity = session.get("last_activity")

        if last_activity:
            elapsed = now - datetime.fromisoformat(last_activity)
            if elapsed > app.permanent_session_lifetime:
                print("[DEBUG] Session expired")
                flash("Session expired due to inactivity.", "warning")
                session.clear()
                print("[DEBUG] Redirection vers login après expiration")

                return redirect(url_for("login"))

        session["last_activity"] = now.isoformat()




@app.route('/mission')
def mission():
    return render_template('mission.html')

@app.route("/subscribe", methods=["POST"])
def subscribe():
    email = request.form.get("email", "").strip().lower()

    if not email or "@" not in email:
        flash("Invalid email address", "danger")
        return redirect(url_for("index"))

    try:
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("INSERT INTO subscriber (email) VALUES (%s) ON CONFLICT DO NOTHING", (email,))
        conn.commit()
        flash("Thanks for subscribing!", "success")
    except Exception as e:
        print("❌ Error saving subscription:", e)
        flash("Something went wrong. Please try again.", "danger")
    finally:
        cur.close()
        conn.close()

    return redirect(url_for("index"))

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
