from flask import Flask, render_template, request, redirect, session, send_file
import sqlite3
import os
from werkzeug.security import generate_password_hash, check_password_hash
import csv
from io import StringIO, BytesIO
from fpdf import FPDF

app = Flask(__name__)
app.secret_key = os.environ.get('FLASK_SECRET_KEY', 'default_secret_key')

def get_db_connection():
    conn = sqlite3.connect('loans.db', check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            email TEXT UNIQUE NOT NULL,
            number TEXT UNIQUE NOT NULL,
            password TEXT NOT NULL
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS customers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            email TEXT,
            mobile TEXT UNIQUE NOT NULL,
            address TEXT,
            FOREIGN KEY(user_id) REFERENCES users(id)
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS transactions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            customer_id INTEGER NOT NULL,
            user_txn_id INTEGER,
            amount REAL NOT NULL,
            type TEXT NOT NULL,
            date TEXT NOT NULL,
            description TEXT,
            user_id INTEGER NOT NULL,
            FOREIGN KEY(customer_id) REFERENCES customers(id),
            FOREIGN KEY(user_id) REFERENCES users(id)
        )
    ''')
    conn.commit()
    conn.close()

@app.route('/')
def home():
    return redirect('/dashboard') if 'user_id' in session else redirect('/login')

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        email = request.form['email']
        password = request.form['password']
        conn = get_db_connection()
        user = conn.execute('SELECT * FROM users WHERE email = ?', (email,)).fetchone()
        conn.close()
        if user and check_password_hash(user['password'], password):
            session['user_id'] = user['id']
            session['user_name'] = user['name']
            return redirect('/dashboard')
        return "Invalid credentials"
    return render_template('login.html')

@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        name = request.form['name']
        email = request.form['email']
        password_raw = request.form['password']

        if not name or not email or not password_raw:
            return "All fields are required."

        password = generate_password_hash(password_raw)
        conn = get_db_connection()
        try:
            conn.execute('INSERT INTO users (name, email, password) VALUES (?, ?, ?)',
                         (name, email, password))
            conn.commit()
        except sqlite3.IntegrityError:
            return "Email already exists."
        finally:
            conn.close()
        return redirect('/login')
    return render_template('register.html')

@app.route('/dashboard')
def dashboard():
    if 'user_id' not in session:
        return redirect('/login')
    conn = get_db_connection()
    customers = conn.execute('SELECT * FROM customers WHERE user_id = ?', (session['user_id'],)).fetchall()
    conn.close()
    return render_template('dashboard.html', customers=customers)

@app.route('/logout')
def logout():
    session.clear()
    return redirect('/login')

@app.route('/profile')
def profile():
    if 'user_id' not in session:
        return redirect('/login')
    conn = get_db_connection()
    user = conn.execute('SELECT * FROM users WHERE id = ?', (session['user_id'],)).fetchone()
    conn.close()
    return render_template('profile.html', user=user)

@app.route('/customer/<int:customer_id>')
def customer_detail(customer_id):
    if 'user_id' not in session:
        return redirect('/login')
    conn = get_db_connection()
    customer = conn.execute(
        'SELECT * FROM customers WHERE id = ? AND user_id = ?', 
        (customer_id, session['user_id'])
    ).fetchone()
    transactions = conn.execute('SELECT * FROM transactions WHERE customer_id = ?', (customer_id,)).fetchall()
    conn.close()
    if customer is None:
        return "Customer not found or access denied."
    return render_template('customer-detail.html', customer=customer, transactions=transactions)

@app.route('/add-transaction/<int:customer_id>', methods=['POST'])
def add_transaction(customer_id):
    if 'user_id' not in session:
        return redirect('/login')
    amount = request.form['amount']
    txn_type = request.form['type']
    date = request.form['date']
    description = request.form.get('description', '')
    conn = get_db_connection()
    max_txn = conn.execute(
        'SELECT MAX(user_txn_id) FROM transactions WHERE user_id = ?', 
        (session['user_id'],)
    ).fetchone()[0]
    next_txn_id = 1 if max_txn is None else max_txn + 1
    conn.execute('''
        INSERT INTO transactions (customer_id, user_id, user_txn_id, amount, type, date, description)
        VALUES (?, ?, ?, ?, ?, ?, ?)''',
        (customer_id, session['user_id'], next_txn_id, amount, txn_type, date, description)
    )
    conn.commit()
    conn.close()
    return redirect(f'/customer/{customer_id}/transactions')

@app.route('/customer/<int:customer_id>/transactions')
def customer_transactions(customer_id):
    if 'user_id' not in session:
        return redirect('/login')
    conn = get_db_connection()
    customer = conn.execute(
        'SELECT * FROM customers WHERE id = ? AND user_id = ?',
        (customer_id, session['user_id'])
    ).fetchone()
    if customer is None:
        return "Customer not found or access denied."
    transactions = conn.execute('SELECT * FROM transactions WHERE customer_id = ?', (customer_id,)).fetchall()
    conn.close()
    return render_template('customer-transactions.html', customer=customer, transactions=transactions)

@app.route('/export-csv/<int:customer_id>', methods=['GET'])
def export_csv(customer_id):
    transactions = get_transactions_by_customer(customer_id)
    output = StringIO()
    writer = csv.writer(output)
    writer.writerow(['Transaction ID', 'Date', 'Amount', 'Type', 'Description'])
    for txn in transactions:
        writer.writerow([txn['id'], txn['date'], txn['amount'], txn['type'], txn['description']])
    output.seek(0)
    byte_output = BytesIO(output.getvalue().encode('utf-8'))
    return send_file(byte_output, mimetype='text/csv', as_attachment=True,
                     download_name=f"transactions_{customer_id}.csv")

from io import BytesIO
from flask import send_file
from fpdf import FPDF

@app.route('/export-pdf/<int:customer_id>', methods=['GET'])
def export_pdf(customer_id):
    # Fetch the transactions for the customer
    transactions = get_transactions_by_customer(customer_id)

    # Fetch customer name and mobile number from the database
    conn = get_db_connection()
    customer = conn.execute('SELECT name, mobile FROM customers WHERE id = ?', (customer_id,)).fetchone()
    conn.close()

    # Ensure the customer exists in the database
    if customer is None:
        return "Customer not found.", 404

    customer_name = customer['name']
    customer_mobile = customer['mobile']

    # Create a PDF document
    pdf = FPDF()
    pdf.add_page()
    pdf.set_font("Arial", size=12)

    # Title of the PDF
    pdf.cell(200, 10, txt=f"Transactions for {customer_name}", ln=True, align='C')
    pdf.set_font("Arial", size=10)
    pdf.cell(200, 10, txt=f"Mobile: {customer_mobile}", ln=True, align='C')
    pdf.ln(10)

    # Table headers
    pdf.set_font("Arial", size=10)
    pdf.cell(40, 10, 'S.No', border=1)
    pdf.cell(40, 10, 'Date', border=1)
    pdf.cell(40, 10, 'Amount', border=1)
    pdf.cell(40, 10, 'Type', border=1)
    pdf.cell(30, 10, 'Description', border=1)
    pdf.ln()

    #loop for auto increase s.no....
    index=0
    serial_no = index + 1

    # Table rows (transactions)
    total_amount = 0  # Track the total amount for calculations
    for transaction in transactions:
        pdf.cell(40, 10, str(serial_no), border=1)
        pdf.cell(40, 10, transaction['date'], border=1)
        pdf.cell(40, 10, f"${transaction['amount']}", border=1)
        pdf.cell(40, 10, transaction['type'], border=1)
        pdf.cell(30, 10, transaction['description'][:20], border=1)  # Truncate description to 20 chars
        pdf.ln()

        # Accumulate the total amount for the transactions
        total_amount += transaction['amount']

    # Add total amount at the bottom
    pdf.set_font("Arial", 'B', size=10)
    pdf.cell(150, 10, 'Total Amount:', border=1)
    pdf.cell(40, 10, f"${total_amount}", border=1)
    pdf.ln()

    # Output the PDF to BytesIO and send it as a response
    pdf_output = BytesIO(pdf.output(dest='S').encode('latin1'))
    return send_file(pdf_output, mimetype='application/pdf', as_attachment=True,
                     download_name=f"transactions_{customer_name.replace(' ', '_')}.pdf")


def get_transactions_by_customer(customer_id):
    # Ensure the session user_id is used for filtering, and transactions are fetched based on customer_id
    conn = get_db_connection()
    customer = conn.execute('SELECT * FROM customers WHERE id = ? AND user_id = ?', 
                            (customer_id, session['user_id'])).fetchone()
    
    if customer is None:
        conn.close()
        return []
    
    # Fetch transactions for the specific customer
    transactions = conn.execute('SELECT * FROM transactions WHERE customer_id = ?', 
                                (customer_id,)).fetchall()
    conn.close()
    return transactions


@app.route('/add-customer', methods=['GET', 'POST'])
def add_customer():
    if 'user_id' not in session:
        return redirect('/login')
    if request.method == 'POST':
        name = request.form['name']
        email = request.form['email']
        mobile = request.form['mobile']
        address = request.form['address']
        if not name:
            return "Customer name is required."
        conn = get_db_connection()
        conn.execute('''
            INSERT INTO customers (user_id, name, email, mobile, address)
            VALUES (?, ?, ?, ?, ?)''',
            (session['user_id'], name, email, mobile, address))
        conn.commit()
        conn.close()
        return redirect('/dashboard')
    return render_template('add-customer.html')

if __name__ == '__main__':
    init_db()
    app.run(debug=True)
