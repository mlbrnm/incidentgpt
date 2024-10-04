from flask import Flask, request, render_template, jsonify
from datetime import datetime
import sqlite3
import re



# Create SQLite database and table for incidents
def init_db():
    conn = sqlite3.connect('incidents.db')
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS incidents
                 (id INTEGER PRIMARY KEY AUTOINCREMENT,
                  timestamp TEXT,
                  subject TEXT,
                  sender TEXT,
                  body TEXT,
                  result TEXT)''')
    conn.commit()
    conn.close()



# Add incident to SQLite database
def add_incident(subject, sender, body, result):
    conn = sqlite3.connect('incidents.db')
    c = conn.cursor()
    c.execute('''INSERT INTO incidents (timestamp, subject, sender, body, result)
                 VALUES (?, ?, ?, ?, ?)''', 
                 (datetime.now().strftime('%Y-%m-%d %H:%M:%S'), subject, sender, body, result))
    conn.commit()
    conn.close()



# Retrieve all incidents from SQLite database
def get_incidents():
    conn = sqlite3.connect('incidents.db')
    c = conn.cursor()
    c.execute('SELECT timestamp, subject, body, result FROM incidents ORDER BY timestamp DESC')
    incidents = c.fetchall()
    conn.close()
    #print(incidents)
    return incidents



# Initialize Flask app
app = Flask(__name__)
    


# Placeholder function for the RAG lookup
def handle_new_incident(subject, sender, body):
    return f"This is a placeholder, in the future this will be the result of the PrivateGPT RAG looking for past incidents similar to the new issue.\nProcessed incident: {subject} from {sender}"



@app.route('/', methods=['GET'])
def index():
    """
    Renders the main page with a list of incidents from SQLite.
    """
    incidents = get_incidents()
    return render_template('index.html', incidents=incidents)



@app.route('/receive-email', methods=['POST'])
def receive_email():
    """
    Endpoint to receive POST requests from the Outlook VBA script.
    """
    # Check if POST request contains JSON data
    data = request.get_json()
    print(request)
    print(f"Received data: {data}")
    if not data:
        return jsonify({"error": "Invalid data"}), 400
    
    # Extract data from POST request
    subject = data.get("subject")
    subject = re.search(r"INC\d+", subject).group()
    sender = data.get("sender")
    body = data.get("body")
    print(body)
    body = re.search(r"Description:(.*?)(?=Configuration item:)", body, re.DOTALL).group(1).strip()
    print(body)
    
    # Process incident
    result = handle_new_incident(subject, sender, body)
    
    # Add to SQLite DB
    add_incident(subject, sender, body, result)
    
    # Return success message
    return jsonify({"message": "Incident received"}), 200



if __name__ == '__main__':
    # Initialize DB if not exists
    init_db()
    # Run Flask app
    app.run(debug=True, host='0.0.0.0')
