from flask import Flask, request, render_template, jsonify
from datetime import datetime
import sqlite3
import re
import requests
import difflib
import ollama
from concurrent.futures import ThreadPoolExecutor
from servicenowapi import get_id_from_inc, delete_from_cache
import datetime

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
    c.execute('''DROP TABLE zabbixevents''')
    c.execute('''CREATE TABLE IF NOT EXISTS zabbixevents
                (id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT,
                subject TEXT,
                issuebody TEXT,
                relatedbody TEXT)''')
    conn.commit()
    conn.close()


# Add incident to SQLite DB
def add_incident(timestamp, subject, sender, body, result, snurl, aisolution="Generation in progress..."):
    conn = sqlite3.connect('incidents.db')
    c = conn.cursor()
    c.execute('''INSERT INTO incidents (timestamp, subject, sender, body, result, snurl, aisolution)
                 VALUES (?, ?, ?, ?, ?, ?, ?)''', 
                 (timestamp, subject, sender, body, result, snurl, aisolution))
    conn.commit()
    conn.close()


def add_ai_solution(subject, body, result):
    contextprompt = f'''You are an AI working for a healthcare IT team called the Middleware Services Team (MWS). The following are solved/closed tickets that contain possible solutions to a new problem.\n----------------\n{result}\n----------------\Based on the above context, determine a concise potential solution to the user submitted problem below. If the context is not relevant, answer that you do not know. Output only a few sentences or less, with no preamble.\nQuestion:\n{body}'''
    try:
        airesponse = ollama.generate(model="llama3.2:3b-instruct-q4_K_M", prompt=contextprompt)
        aisolution = airesponse.get('response')
        print(f"Added AI solution for {subject}")
        log(f"Added AI solution for {subject}")
    except Exception as e:
        aisolution = f"Failed to generate AI solution: {str(e)}"
        print(f"Failed to add AI solution for {subject}")
        log(f"Failed to add AI solution for {subject}")
    conn = sqlite3.connect('incidents.db')
    c = conn.cursor()
    c.execute(
        '''UPDATE incidents SET aisolution = ? WHERE subject = ?''', (aisolution, subject))
    conn.commit()
    conn.close()
    


# Get all incidents from SQLite DB - used for the web UI
def get_sn_incidents():
    conn = sqlite3.connect('incidents.db')
    c = conn.cursor()
    c.execute('SELECT timestamp, subject, body, result, snurl, aisolution FROM incidents ORDER BY timestamp DESC')
    incidents = c.fetchall()
    conn.close()
    return incidents

def get_zabbix_events():
    conn = sqlite3.connect('incidents.db')
    c = conn.cursor()
    c.execute('SELECT timestamp, subject, issuebody, relatedbody FROM zabbixevents ORDER BY timestamp DESC')
    incidents = c.fetchall()
    conn.close()
    return incidents

def log(message):
    # Get current time and format it
    timestamp = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    
    # Open the log file in append mode
    with open('log.txt', 'a') as log_file:
        log_file.write(f"[{timestamp}] {message}\n")


# Initialize Flask app and async thing
app = Flask(__name__)
executor = ThreadPoolExecutor()
    


# The main function for pulling in the relevant text chunks from the PrivateGPT RAG database
def handle_new_sn_incident(subject, sender, body, snurl):
    # This is the PrivateGPT server.
    url = "http://wsmwsllm01:8001/v1/chunks"
    headers = {
        "Content-Type": "application/json"
    }
    # Could be finetuned, 20 is usually excessive but it doesn't hurt to have more
    data = {
        "text":f"{body}",
        "limit":5,
        "prev_next_chunks":20
    }
    try:
        response = requests.post(url, json=data, headers=headers)
        print(f"Received chunk request. Status: {response.status_code}")
        log(f"Received chunk request. Status: {response.status_code}")
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
    except Exception as e:
        print(f"Failed to get chunks: {str(e)}")
        log(f"Failed to get chunks: {str(e)}")
        return (f"Failed to get chunks. {str(e)}")

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


def replace_inc_with_url(text):
    """Finds all INC numbers in the text, looks up their sys_id, and replaces them with a URL."""
    
    # Find all INC numbers using regex
    inc_numbers = re.findall(r'INC\d+', text)
    
    # Replace each INC number with the corresponding URL
    for inc in inc_numbers:
        sys_id = get_id_from_inc(inc)
        if sys_id != "na":
            # Format the replacement URL
            url = f'<a href="https://albertahealthservices.service-now.com/nav_to.do?uri=incident.do?sys_id={sys_id}">{inc}</a>'
            # Replace the INC number with the formatted URL in the text
            text = text.replace(inc, url)
    
    return text


def ragresultparse(result, zabbix=False, colorize=False):
    result = re.sub(r'---- (\d+) ----', r'<strong style="font-size: 0.9rem; margin-left: -8px;">Result \1</strong>', result)
    if zabbix == False:
        result = replace_inc_with_url(result)
        result = result.replace("---- Work Notes:", "<strong>Work Notes:</strong>")
        result = result.replace("---- Problem:", "<strong>Problem:</strong>")
        result = result.replace("---- Solution:", "<strong>Solution:</strong>")

    if zabbix == True:
        result = result.replace("Problem:", "")
        result = result.replace("Solution:", "<strong>Solution:</strong>")
        if colorize == True:
            result = color_severity(result)
       
    return result


def color_severity(text):
    # Define the regex pattern to capture "Severity: <Level>"
    pattern = r"\(Severity: ([A-Za-z\s]+)\)"
    severity_styles = {
    "Not classified": {"color": "gray", "background": "lightgray"},
    "Information": {"color": "blue", "background": "lightblue"},
    "Warning": {"color": "black", "background": "yellow"},
    "Average": {"color": "white", "background": "orange"},
    "High": {"color": "white", "background": "lightcoral"},  # Light red
    "Disaster": {"color": "white", "background": "red"}
    }
    
    # Function to replace severity level with styled HTML
    def replace_severity(match):
        severity = match.group(1)  # Extract severity level
        style = severity_styles.get(severity, {"color": "black", "background": "white"})  # Default style
        return (f'<br><span style="color:{style["color"]}; '
                f'background-color:{style["background"]}; '
                f'padding: 2px 5px; border-radius: 4px; font-weight: bold;">'
                f'(Severity: {severity})</span><br>')
    
    # Use re.sub() to replace all occurrences in the text
    colored_text = re.sub(pattern, replace_severity, text)
    return colored_text

@app.route('/', methods=['GET'])
def index():
    """
    Renders the main page with a list of incidents from SQLite.
    """
    # Get all incidents from SQLite DB
    incidents = get_sn_incidents()
    log("Web UI loaded.")
    # Render the web UI
    return render_template('index.html', incidents=incidents)

@app.route('/zabbix')
def zabbix():
    '''
    Renders the Zabbix page with a list of incidents from SQLite.
    '''
    zabbix_incidents = get_zabbix_events()
    log("Zabbix UI loaded.")
    return render_template('zabbix.html', zabbix_incidents=zabbix_incidents)


@app.route('/receive-email', methods=['POST'])
def receive_email():
    """
    Endpoint to receive POST requests for new incidents.
    """
    data = request.get_json()
    print(request)
    print(f"Received data: {data}")
    log(f"Received data: {data}")
    if not data:
        return jsonify({"error": "Invalid data"}), 400
    
    subject = data.get("subject")
    sender = data.get("sender")
    combinedbody = data.get("body")
    snurl = data.get("snurl")
    timestamp = data.get("timestamp")
    result = ragresultparse(handle_new_sn_incident(subject, sender, combinedbody, snurl))
    add_incident(timestamp, subject, sender, combinedbody, result, snurl)
    executor.submit(add_ai_solution, subject, combinedbody, result)
    # Return a success message
    return jsonify({"message": "Incident received"}), 200


@app.route('/delete_incident/<incident_id>', methods=['DELETE'])
def delete_incident(incident_id):
    conn = sqlite3.connect('incidents.db')
    c = conn.cursor()
    c.execute('DELETE FROM incidents WHERE subject = ?', (incident_id,))
    if c.rowcount == 0:
        print(f"Couldn't delete {incident_id} for some reason.")
        log(f"Couldn't delete {incident_id} for some reason.")
    else:
        print(f"Deleted {incident_id} from db.")
        log(f"Deleted {incident_id} from db.")
        delete_from_cache(incident_id)
    conn.commit()
    conn.close()
    return jsonify({'success': True}), 200


if __name__ == '__main__':
    # Initialize DB if not exists
    init_db()
    # Run Flask app
    log(f"Starting app...")
    app.run(debug=True, host='127.0.0.1', port=5001)


