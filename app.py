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

# Sécurise la récupération de la clé API
api_key = os.environ.get("TOGETHER_API_KEY", "").strip()
if not api_key:
    raise ValueError("TOGETHER_API_KEY is missing in .env or environment variables.")

client = OpenAI(
    api_key=api_key,
    base_url="https://api.together.xyz/v1"
)

app = Flask(__name__)
app.secret_key = 'supersecretkey'
app.permanent_session_lifetime = timedelta(minutes=30)

USERS_FILE = 'users.json'
MAX_ATTEMPTS = 5

# Charger les utilisateurs
def load_users():
    if not os.path.exists(USERS_FILE):
        return {}
    with open(USERS_FILE, 'r') as f:
        return json.load(f)

# Sauvegarder les utilisateurs
def save_users(users):
    with open(USERS_FILE, 'w') as f:
        json.dump(users, f, indent=2)

# Ajouter un nouvel utilisateur
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
        "attempts": 0
    }

    try:
        save_users(users)
        return True
    except Exception as e:
        print(f"Error while saving: {e}")
        return False

# Générer réponse du chatbot
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

@app.route('/submit-feedback', methods=['POST'])
def submit_feedback():
    message = request.form.get('message')
    name = request.form.get('name') or "Anonymous"
    print(f"Feedback received from {name}: {message}")
    return redirect('/')

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/send_message', methods=["POST"])
def send_message():
    if "user" not in session:
        return jsonify({"error": "Not authenticated"}), 401

    data = request.get_json()
    if not data or "message" not in data:
        return jsonify({"error": "Message required"}), 400

    user_message = data["message"]
    system_message = data.get("system_message", "You are a psychological support assistant. Remain calm, human, and attentive.")
    
    try:
        response_text = generate_response(user_message, system_message)
        return jsonify({"status": "success", "response": response_text})
    except Exception as e:
        return jsonify({"status": "error", "error": str(e)}), 500

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

        if user_data.get("attempts", 0) >= MAX_ATTEMPTS:
            flash("Too many failed attempts. Please try again later.", "error")
            return redirect(url_for("login"))

        if not check_password_hash(user_data["password"], password):
            users[username]["attempts"] = user_data.get("attempts", 0) + 1
            save_users(users)
            flash("Incorrect username or password.", "error")
            return redirect(url_for("login"))

        users[username]["attempts"] = 0
        save_users(users)

        session.permanent = True
        session["user"] = username
        return redirect(url_for("chat"))

    return render_template("login.html")

@app.route('/reset-password', methods=["POST"])
def reset_password():
    email = request.form.get("email")
    flash("A reset link has been sent to your email address.", "success")
    return redirect(url_for('login'))

@app.route('/chat')
def chat():
    if "user" not in session:
        return redirect(url_for("login"))

    users = load_users()
    email = session["user"]

    if email in users:
        user_data = users[email]
        first_name = user_data.get("first_name", "")
        last_name = user_data.get("last_name", "")
    else:
        first_name = email.split('@')[0]
        last_name = ""

    return render_template('chat.html', 
                          username=email,
                          first_name=first_name, 
                          last_name=last_name)

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

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('index'))

@app.route('/mission')
def mission():
    return render_template('mission.html')

@app.route('/register', methods=["GET", "POST"])
def register():
    if request.method == "POST":
        first_name = request.form.get("first_name")
        last_name = request.form.get("last_name")
        email = request.form.get("email")
        password = request.form.get("password")
        gender = request.form.get("gender")
        dob = request.form.get("dob")
        recaptcha_response = request.form.get('g-recaptcha-response')

        if not first_name or not last_name or not email or not password or not gender or not dob or not recaptcha_response:
            flash("Please fill all fields and complete the CAPTCHA.", "error")
            return redirect(url_for("register"))

        secret_key = "6LdeuTgrAAAAAHq3joWnVZ18BY62ilrRfUmSkT_d"
        payload = {'secret': secret_key, 'response': recaptcha_response}
        response = requests.post("https://www.google.com/recaptcha/api/siteverify", data=payload)
        result = response.json()

        if not result.get('success'):
            flash("CAPTCHA verification failed.", "error")
            return redirect(url_for("register"))

        success = save_user({
            "first_name": first_name,
            "last_name": last_name,
            "email": email,
            "password": password,
            "gender": gender,
            "dob": dob
        })

        if not success:
            flash("This email is already registered.", "error")
            return redirect(url_for("register"))

        flash("Registration successful! You can now log in.", "success")
        return redirect(url_for("login"))

    return render_template("register.html")

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
