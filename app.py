from flask import Flask, request, render_template, jsonify
from datetime import datetime
import sqlite3
import re
import requests
import difflib

def init_db():
    conn = sqlite3.connect('incidents.db')
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS incidents
                 (id INTEGER PRIMARY KEY AUTOINCREMENT,
                  timestamp TEXT,
                  subject TEXT,
                  sender TEXT,
                  body TEXT,
                  result TEXT,
                  snurl TEXT)''')
    conn.commit()
    conn.close()



def add_incident(subject, sender, body, result, snurl):
    conn = sqlite3.connect('incidents.db')
    c = conn.cursor()
    c.execute('''INSERT INTO incidents (timestamp, subject, sender, body, result, snurl)
                 VALUES (?, ?, ?, ?, ?, ?)''', 
                 (datetime.now().strftime('%Y-%m-%d %H:%M:%S'), subject, sender, body, result, snurl))
    conn.commit()
    conn.close()




def get_incidents():
    conn = sqlite3.connect('incidents.db')
    c = conn.cursor()
    c.execute('SELECT timestamp, subject, body, result, snurl FROM incidents ORDER BY timestamp DESC')
    incidents = c.fetchall()
    conn.close()
    return incidents




app = Flask(__name__)
    



def handle_new_incident(subject, sender, body, snurl):
    url = "http://wsvraloan1008:8001/v1/chunks"
    headers = {
        "Content-Type": "application/json"
    }
    data = {
        "text":f"{body}",
        "limit":5,
        "prev_next_chunks":20
    }
    print(data)
    response = requests.post(url, json=data, headers=headers)
    print(f"Received chunk request. Status: {response.status_code}")
    returndata = response.json()
    list_of_relevant_texts = []
    finaltext = ""
    for idx, item in enumerate(returndata["data"]):
        text = item.get("text")
        previous_texts = item.get("previous_texts", [])
        next_texts = item.get("next_texts", [])
        finalchunk = combine_text_blocks(text,previous_texts,next_texts)
        theincident = find_most_similar_section(finalchunk, text)
        finaltext = finaltext + f"---- {idx+1} ----\n" + theincident + "\n\n\n"
    finaltext = finaltext.replace("\n","<br>")
    return finaltext

def combine_text_blocks(main_text, previous_texts, next_texts):
    previous_texts_reversed = previous_texts[::-1]
    combined_text = (
        "".join(previous_texts_reversed) + main_text +
        "".join(next_texts)
    )
    
    return combined_text.strip()


def find_most_similar_section(big_string, substring, separator="--------------------------------------------------------------"):
    sections = big_string.split(separator)
    max_similarity = 0
    best_section = None
    normalized_substring = ' '.join(substring.lower().split())
    
    for section in sections:
        normalized_section = ' '.join(section.lower().split())
        similarity = difflib.SequenceMatcher(None, normalized_substring, normalized_section).ratio()
        
        if similarity > max_similarity:
            max_similarity = similarity
            best_section = section.strip()
    
    if best_section is not None:
        return best_section
    else:
        return "Not found."





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
    data = request.get_json()
    print(request)
    print(f"Received data: {data}")
    if not data:
        return jsonify({"error": "Invalid data"}), 400
    
    subject = data.get("subject")
    subject = re.search(r"INC\d+", subject).group()
    sender = data.get("sender")
    shortdesc = data.get("body")
    shortdesc = re.search(r"Short description:(.*?)(?=Description:)", shortdesc, re.DOTALL).group(1).strip()
    body = data.get("body")
    body = re.search(r"Description:(.*?)(?=Configuration item:)", body, re.DOTALL).group(1).strip()
    combinedbody = f"{shortdesc}\n{body}"
    combinedbody = combinedbody.replace("\n","<br>")

    
    url_pattern = r'Click here to view record:  INC[0-9]+\s*<([^>]+)>'
    urls = re.findall(url_pattern, data.get("body"))
    snurl = urls[0] if urls else None
    
    result = handle_new_incident(subject, sender, combinedbody, snurl)
    result = re.sub(r'---- (\d+) ----', r'<strong style="font-size: 0.9rem; margin-left: -8px;">Result \1</strong>', result)
    result = result.replace("---- Problem:", "<strong>Problem:</strong>")
    result = result.replace("---- Solution:", "<strong>Solution:</strong>")
    result = result.replace("---- Work Notes:", "<strong>Work Notes:</strong>")
    
    add_incident(subject, sender, combinedbody, result, snurl)
    
    return jsonify({"message": "Incident received"}), 200



if __name__ == '__main__':
    # Initialize DB if not exists
    init_db()
    # Run Flask app
    app.run(debug=True, host='0.0.0.0')
