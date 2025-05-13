from flask import Flask, render_template, request, redirect, url_for, session, flash, jsonify
from werkzeug.security import check_password_hash, generate_password_hash
from datetime import timedelta
from flask_mail import Mail, Message
from itsdangerous import URLSafeTimedSerializer, SignatureExpired
import json
import os
import requests
from openai import OpenAI
from dotenv import load_dotenv

# Charger les variables d'environnement
load_dotenv()

# Config API Together
api_key = os.environ.get("TOGETHER_API_KEY", "").strip()
if not api_key:
    raise ValueError("TOGETHER_API_KEY is missing in .env or environment variables.")

client = OpenAI(api_key=api_key, base_url="https://api.together.xyz/v1")

app = Flask(__name__)
app.secret_key = 'supersecretkey'
app.permanent_session_lifetime = timedelta(minutes=30)

# Config email
app.config['MAIL_SERVER'] = 'smtp.gmail.com'
app.config['MAIL_PORT'] = 587
app.config['MAIL_USE_TLS'] = True
app.config['MAIL_USERNAME'] = os.environ.get("EMAIL_USER")
app.config['MAIL_PASSWORD'] = os.environ.get("EMAIL_PASS")
mail = Mail(app)
s = URLSafeTimedSerializer(app.secret_key)

USERS_FILE = 'users.json'
MAX_ATTEMPTS = 5

# GESTION UTILISATEURS

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
        "attempts": 0,
        "confirmed": False
    }

    try:
        save_users(users)
        return True
    except Exception as e:
        print(f"Error while saving: {e}")
        return False

def send_confirmation_email(email):
    token = s.dumps(email, salt='email-confirm')
    link = url_for('confirm_email', token=token, _external=True)
    msg = Message("Confirme ton adresse email", sender=app.config['MAIL_USERNAME'], recipients=[email])
    msg.body = f"Bienvenue sur Cogi ! Clique ici pour confirmer ton adresse : {link}"
    mail.send(msg)

def send_reset_email(email):
    token = s.dumps(email, salt='reset-password')
    link = url_for('reset_token', token=token, _external=True)
    msg = Message('🔐 Réinitialisation de ton mot de passe',
                  sender=app.config['MAIL_USERNAME'],
                  recipients=[email])
    msg.body = f'Cogi te permet de réinitialiser ton mot de passe ici : {link}'
    mail.send(msg)

# CHATBOT

def generate_response(prompt, system_message="You are a bilingual (French/Arabic) psychological assistant. Respond in the user's language."):
    try:
        response = client.chat.completions.create(
            model="mistralai/Mistral-7B-Instruct-v0.1",
            messages=[
                {"role": "system", "content": system_message},
                {"role": "user", "content": prompt}
            ],
            temperature=0.7,
            max_tokens=200
        )
        return response.choices[0].message.content
    except Exception as e:
        return f"Error: {str(e)}"

# ROUTES

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/submit-feedback', methods=['POST'])
def submit_feedback():
    message = request.form.get('message')
    name = request.form.get('name') or "Anonymous"
    print(f"Feedback received from {name}: {message}")
    return redirect('/')

@app.route('/send_message', methods=["POST"])
def send_message():
    if "user" not in session:
        return jsonify({"error": "Not authenticated"}), 401

    data = request.get_json()
    if not data or "message" not in data:
        return jsonify({"error": "Message required"}), 400

    response_text = generate_response(data["message"])
    return jsonify({"status": "success", "response": response_text})

@app.route('/login', methods=["GET", "POST"])
def login():
    if request.method == "POST":
        email = request.form.get("username")
        password = request.form.get("password")

        users = load_users()
        user = users.get(email)
        if not user:
            flash("Unknown user.", "error")
            return redirect(url_for("login"))

        if not user.get("confirmed", False):
            flash("Confirme d'abord ton adresse email.", "warning")
            return redirect(url_for("login"))

        if not check_password_hash(user["password"], password):
            user["attempts"] = user.get("attempts", 0) + 1
            save_users(users)
            flash("Incorrect credentials.", "error")
            return redirect(url_for("login"))

        user["attempts"] = 0
        save_users(users)
        session.permanent = True
        session["user"] = email
        return redirect(url_for("chat"))

    return render_template("login.html")

@app.route('/register', methods=["GET", "POST"])
def register():
    if request.method == "POST":
        data = request.form.to_dict()
        recaptcha_response = data.get('g-recaptcha-response')

        if not all(data.values()) or not recaptcha_response:
            flash("Please fill all fields and complete the CAPTCHA.", "error")
            return redirect(url_for("register"))

        # CAPTCHA
        secret_key = "6LdeuTgrAAAAAHq3joWnVZ18BY62ilrRfUmSkT_d"
        response = requests.post("https://www.google.com/recaptcha/api/siteverify", data={
            'secret': secret_key,
            'response': recaptcha_response
        }).json()

        if not response.get('success'):
            flash("CAPTCHA verification failed.", "error")
            return redirect(url_for("register"))

        success = save_user(data)
        if not success:
            flash("This email is already registered.", "error")
            return redirect(url_for("register"))

        send_confirmation_email(data["email"])
        flash("Registration successful! Check your email to confirm.", "info")
        return redirect(url_for("login"))

    return render_template("register.html")

@app.route('/confirm/<token>')
def confirm_email(token):
    try:
        email = s.loads(token, salt='email-confirm', max_age=3600)
    except SignatureExpired:
        flash("Lien expiré. Recommence l'inscription.", "danger")
        return redirect(url_for("register"))

    users = load_users()
    if email in users:
        users[email]["confirmed"] = True
        save_users(users)
        flash("Email confirmé. Tu peux te connecter.", "success")
    return redirect(url_for("login"))

@app.route('/reset-password', methods=["GET", "POST"])
def reset_password():
    if request.method == "POST":
        email = request.form.get("email")
        users = load_users()
        if email in users:
            send_reset_email(email)
            flash("📧 Lien de réinitialisation envoyé à ton email.", "info")
            return redirect(url_for("login"))
        else:
            flash("❌ Aucun compte trouvé avec cet email.", "danger")
            return redirect(url_for("reset_password"))
    return render_template("reset_request.html")

@app.route('/reset/<token>', methods=['GET', 'POST'])
def reset_token(token):
    try:
        email = s.loads(token, salt='reset-password', max_age=3600)
    except SignatureExpired:
        flash("❌ Lien expiré. Recommence la procédure.", "danger")
        return redirect(url_for('reset_password'))

    if request.method == 'POST':
        password = request.form['password']
        confirm = request.form['confirm']

        if password != confirm:
            flash("❌ Les mots de passe ne correspondent pas.", "warning")
            return redirect(request.url)

        users = load_users()
        if email in users:
            users[email]['password'] = generate_password_hash(password)
            save_users(users)
            flash("✅ Mot de passe mis à jour. Tu peux te connecter !", "success")
            return redirect(url_for('login'))

    return render_template("reset_token.html")

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

@app.context_processor
def inject_user_info():
    if 'user' in session:
        users = load_users()
        email = session['user']
        user_data = users.get(email, {})
        return {
            'first_name': user_data.get('first_name', ''),
            'last_name': user_data.get('last_name', '')
        }
    return {'first_name': '', 'last_name': ''}

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('index'))

@app.route('/mission')
def mission():
    return render_template('mission.html')

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
