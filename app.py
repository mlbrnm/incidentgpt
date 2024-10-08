from flask import Flask, request, render_template, jsonify
from datetime import datetime
import sqlite3
import re
import requests
import difflib
import ollama


# Initialize SQLite DB
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
                  snurl TEXT,
                  aisolution TEXT)''')
    conn.commit()
    conn.close()


# Add incident to SQLite DB
def add_incident(subject, sender, body, result, snurl, aisolution="Generation in progress..."):
    conn = sqlite3.connect('incidents.db')
    c = conn.cursor()
    c.execute('''INSERT INTO incidents (timestamp, subject, sender, body, result, snurl, aisolution)
                 VALUES (?, ?, ?, ?, ?, ?, ?)''', 
                 (datetime.now().strftime('%Y-%m-%d %H:%M:%S'), subject, sender, body, result, snurl, aisolution))
    conn.commit()
    conn.close()

def add_ai_solution(subject, body, result):
    # AI solution is generated first
    contextprompt = f'''You are an AI working for a healthcare IT team called the Middleware Services Team (MWS). The following are solved/closed tickets that contain possible solutions to a new problem.\n----------------\n{result}\n----------------\Based on the above context, identify common themes and determine a concise potential solution to the user submitted problem below. If the context is not relevant, answer that you do not know. Output only a few sentences or less.\nQuestion:\n{body}'''
    
    airesponse = ollama.generate(model="gemma2:2b-instruct-q4_K_M", prompt=contextprompt)
    aisolution = airesponse.get('response')

    conn = sqlite3.connect('incidents.db')
    c = conn.cursor()
    c.execute(
        '''UPDATE incidents SET aisolution = ? WHERE subject = ?''', (aisolution, subject))
    conn.commit()
    conn.close()


# Get all incidents from SQLite DB - used for the web UI
def get_incidents():
    conn = sqlite3.connect('incidents.db')
    c = conn.cursor()
    c.execute('SELECT timestamp, subject, body, result, snurl, aisolution FROM incidents ORDER BY timestamp DESC')
    incidents = c.fetchall()
    conn.close()
    return incidents



# Initialize Flask app
app = Flask(__name__)
    


# The main function for pulling in the relevant text chunks from the PrivateGPT RAG database
def handle_new_incident(subject, sender, body, snurl):
    # This is the PrivateGPT server.
    url = "http://wsvraloan1008:8001/v1/chunks"
    headers = {
        "Content-Type": "application/json"
    }
    # Could be finetuned, 20 is usually excessive but it doesn't hurt to have more
    data = {
        "text":f"{body}",
        "limit":5,
        "prev_next_chunks":20
    }
    response = requests.post(url, json=data, headers=headers)
    print(f"Received chunk request. Status: {response.status_code}")
    returndata = response.json()
    # Just initializing the final text
    finaltext = ""
    # This iterates through each relevant text chunk pulled in by the PrivateGPT API
    for idx, item in enumerate(returndata["data"]):
        # The main chunk with the relevant text
        text = item.get("text")
        # The previous and next text chunks
        previous_texts = item.get("previous_texts", [])
        next_texts = item.get("next_texts", [])
        # Just combining them all into one string since they come separately
        finalchunk = combine_text_blocks(text,previous_texts,next_texts)
        # After they've been combined, there will be irrelevant sections that need to be cut off the beginning/end
        theincident = find_most_similar_section(finalchunk, text)
        # Adding a header / spacing to each section
        finaltext = finaltext + f"---- {idx+1} ----\n" + theincident + "\n\n\n"
    # Replacing newlines with HTML line breaks for the web UI
    finaltext = finaltext.replace("\n","<br>")
    return finaltext

# Helper functions for the text chunks - combines the main text with the preceding and following text blocks
def combine_text_blocks(main_text, previous_texts, next_texts):
    # Reversing the previous texts so that they are in the correct order
    previous_texts_reversed = previous_texts[::-1]
    combined_text = (
        "".join(previous_texts_reversed) + main_text +
        "".join(next_texts)
    )
    
    return combined_text.strip()

# After combining the main text chunk with the preceding and following text blocks, this function finds the part of the text we're looking for and cuts off the irrelevant stuff from the beginning and end
# It seems silly to use a fuzzy search for this, but it works well and the extra processing time is imperceptible compared to the time it takes to pull in the text chunks from the PrivateGPT server
# I was trying to do it with more conventional methods but there were always edge cases where it didn't work
def find_most_similar_section(big_string, substring, separator="--------------------------------------------------------------"):
    # Splits the text into sections based on the separator
    sections = big_string.split(separator)
    # Initializing the maximum similarity and the best section
    max_similarity = 0
    best_section = None
    # Normalizing the substring
    normalized_substring = ' '.join(substring.lower().split())
    # Iterating through the sections and finding the one with the highest similarity
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


def subjectparse(data):
    subject = data.get("subject")
    subject = re.search(r"INC\d+", subject).group()
    return subject

def bodyparse(data):
    shortdesc = data.get("body")
    shortdesc = re.search(r"Short description:(.*?)(?=Description:)", shortdesc, re.DOTALL).group(1).strip()
    body = data.get("body")
    body = re.search(r"Description:(.*?)(?=Configuration item:)", body, re.DOTALL).group(1).strip()
    combinedbody = f"{shortdesc}\n{body}"
    combinedbody = combinedbody.replace("\n","<br>")
    return combinedbody

def urlparse(data):
    url_pattern = r'Click here to view record:  INC[0-9]+\s*<([^>]+)>'
    urls = re.findall(url_pattern, data.get("body"))
    return urls[0] if urls else None

def ragresultparse(result):
    result = re.sub(r'---- (\d+) ----', r'<strong style="font-size: 0.9rem; margin-left: -8px;">Result \1</strong>', result)
    result = result.replace("---- Problem:", "<strong>Problem:</strong>")
    result = result.replace("---- Solution:", "<strong>Solution:</strong>")
    result = result.replace("---- Work Notes:", "<strong>Work Notes:</strong>")
    return result


@app.route('/', methods=['GET'])
def index():
    """
    Renders the main page with a list of incidents from SQLite.
    """
    # Get all incidents from SQLite DB
    incidents = get_incidents()
    # Render the web UI
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
    
    subject = subjectparse(data)
    sender = data.get("sender")
    combinedbody = bodyparse(data)
    snurl = urlparse(data)
    result = ragresultparse(handle_new_incident(subject, sender, combinedbody, snurl))
    add_incident(subject, sender, combinedbody, result, snurl)
    add_ai_solution(subject, combinedbody, result)
    # Return a success message
    return jsonify({"message": "Incident received"}), 200



if __name__ == '__main__':
    # Initialize DB if not exists
    init_db()
    # Run Flask app
    app.run(debug=True, host='0.0.0.0')
