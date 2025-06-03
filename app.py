from flask import Flask, render_template, request, redirect, url_for, flash, session
import psycopg2
import uuid
from datetime import datetime, date
from werkzeug.security import generate_password_hash, check_password_hash

app = Flask(__name__)
app.secret_key = 'your_secret_key'

# Database connection
def get_db_connection():
    return psycopg2.connect(
        host="localhost",
        database="cogi_db",
        user="mac",
        password="cogi123",
        port=5433
    )

# Home
@app.route('/')
def index():
    return render_template('index.html')

# Register
@app.route('/register', methods=['GET', 'POST'])
def register():
    current_date = date.today()

    if request.method == 'POST':
        email = request.form.get('email')
        password = request.form.get('password')

        if not email or not password:
            flash("Please fill out all required fields.", "warning")
            return redirect(url_for('register'))

        hashed_pw = generate_password_hash(password)

        try:
            conn = get_db_connection()
            cur = conn.cursor()
            cur.execute("SELECT * FROM users WHERE email = %s", (email,))
            if cur.fetchone():
                flash("An account with this email already exists.", "danger")
                return redirect(url_for('register'))

            cur.execute("INSERT INTO users (email, password) VALUES (%s, %s)", (email, hashed_pw))
            conn.commit()
            flash("Your account has been created successfully!", "success")
        except Exception as e:
            conn.rollback()
            flash("An unexpected error occurred. Please try again later.", "danger")
            print(e)
        finally:
            cur.close()
            conn.close()

        return redirect(url_for('login'))

    return render_template('register.html', current_date=current_date)

@app.route('/mission')
def mission():
    return render_template('mission.html')

# Subscribe
@app.route('/subscribe', methods=['POST'])
def subscribe():
    email = request.form.get('email')
    if not email:
        flash("Please provide an email address.", "warning")
        return redirect(url_for('index'))

    try:
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("INSERT INTO subscriber (email) VALUES (%s)", (email,))
        conn.commit()
        flash("You've been successfully subscribed to the newsletter!", "success")
    except psycopg2.errors.UniqueViolation:
        conn.rollback()
        flash("This email is already subscribed.", "danger")
    except Exception as e:
        conn.rollback()
        flash("An error occurred. Please try again later.", "danger")
        print(e)
    finally:
        cur.close()
        conn.close()

    return redirect(url_for('index'))

# Login
@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        email = request.form.get('username')
        password = request.form.get('password')

        if not email or not password:
            flash("Please fill out all fields.", "warning")
            return redirect(url_for('login'))

        try:
            conn = get_db_connection()
            cur = conn.cursor()
            cur.execute("SELECT * FROM users WHERE email = %s", (email,))
            user = cur.fetchone()
            cur.close()
            conn.close()

            if user and check_password_hash(user[2], password):
                session['user'] = user[1]
                session['first_name'] = user[3]
                session['last_name'] = user[4]
                flash("You have logged in successfully.", "success")
                return redirect(url_for('chat'))
            else:
                flash("Invalid email or password. Please try again.", "danger")
                return redirect(url_for('login'))

        except Exception as e:
            flash("An unexpected error occurred. Please try again later.", "danger")
            print(e)
            return redirect(url_for('login'))

    return render_template('login.html')

# Logout
@app.route('/logout')
def logout():
    session.pop('user', None)
    flash("You have been logged out.", "success")
    return redirect(url_for('login'))

# Save message helper
def save_message(email, message, sender, session_id):
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO conversations (user_email, message, sender, session_id)
            VALUES (%s, %s, %s, %s)
        """, (email, message, sender, session_id))
        conn.commit()
        cur.close()
        conn.close()
    except Exception as e:
        print("[ERROR] Failed to save message:", e)

# Chat page
@app.route('/chat', methods=['GET', 'POST'])
def chat():
    if 'user' not in session:
        flash("You must be logged in to access the chat.", "danger")
        return redirect(url_for('login'))

    conn = get_db_connection()
    cur = conn.cursor()

    if 'session_id' not in session:
        session['session_id'] = str(uuid.uuid4())
    session_id = session['session_id']

    if request.method == 'POST':
        message = request.form.get('message', '').strip()
        if message:
            save_message(session['user'], message, 'user', session_id)
            bot_reply = generate_bot_response(message)
            save_message(session['user'], bot_reply, 'bot', session_id)
        return redirect(url_for('chat'))

    cur.execute("""
        SELECT message, sender, timestamp FROM conversations
        WHERE user_email = %s AND session_id = %s
        ORDER BY timestamp ASC
    """, (session['user'], session_id))
    history = cur.fetchall()

    cur.execute("SELECT first_name, last_name FROM users WHERE email = %s", (session['user'],))
    row = cur.fetchone()
    first_name = row[0] if row else ""
    last_name = row[1] if row else ""

    cur.close()
    conn.close()

    return render_template("chat.html", history=history, first_name=first_name, last_name=last_name, active_id=session_id, sessions=[])

def generate_bot_response(user_input):
    return "I'm here for you. Let's talk more about that."

@app.route('/feedback', methods=['GET', 'POST'])
def feedback():
    conn = get_db_connection()
    cur = conn.cursor()

    if request.method == 'POST':
        name = request.form['name']
        message = request.form['message']
        cur.execute("INSERT INTO feedback (name, message) VALUES (%s, %s)", (name, message))
        conn.commit()
        flash("Thank you for your feedback!", "success")
        return redirect(url_for('feedback'))

    cur.execute("""
        SELECT name, message, submitted_at FROM feedback ORDER BY submitted_at DESC
    """)
    rows = cur.fetchall()
    feedbacks = [{'name': r[0], 'message': r[1], 'submitted_at': r[2]} for r in rows]

    cur.close()
    conn.close()
    return render_template('feedback.html', feedbacks=feedbacks)

# Chat message API (not directly used now)
@app.route('/send_message', methods=['POST'])
def send_message():
    if 'user' not in session:
        flash("Please log in to send messages.", "danger")
        return redirect(url_for('login'))

    message = request.form.get('message')
    email = session['user']
    sender = 'user'
    session_id = str(uuid.uuid4())

    try:
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO conversations (user_email, message, sender, session_id)
            VALUES (%s, %s, %s, %s)
        """, (email, message, sender, session_id))
        conn.commit()
        cur.close()
        conn.close()
        flash("Message sent successfully.", "success")
    except Exception as e:
        flash("Failed to send message. Please try again.", "danger")
        print(e)

    return redirect(url_for('chat'))

# History
@app.route('/history')
def history():
    if 'user' not in session:
        flash("Please log in to view history.", "danger")
        return redirect(url_for('login'))

    email = session['user']
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("""
        SELECT message, sender, timestamp FROM conversations
        WHERE user_email = %s
        ORDER BY timestamp DESC
        LIMIT 50
    """, (email,))
    conversations = cur.fetchall()
    cur.close()
    conn.close()

    return render_template('history.html', conversations=conversations)

# Run app
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
