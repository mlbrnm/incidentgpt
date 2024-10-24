import requests
from requests.auth import HTTPBasicAuth
import json
import credentials
import sqlite3
import time
import datetime
from app import combine_text_blocks, find_most_similar_section, ragresultparse

ZABBIX_SEVERITIES = {
    0: "Not classified",
    1: "Information",
    2: "Warning",
    3: "Average",
    4: "High",
    5: "Disaster"
}

def log(message):
    # Get current time and format it
    timestamp = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    
    # Open the log file in append mode
    with open('log.txt', 'a') as log_file:
        log_file.write(f"[{timestamp}] {message}\n")


def check_incident(subject):
    # Check the in-memory cache first
    if subject in subjects_cache:
        #print(f"Subject {subject} was found in cache.")
        return True
    
    # If not found in cache, query the database
    conn = sqlite3.connect('incidents.db')
    c = conn.cursor()
    c.execute('SELECT 1 FROM incidents WHERE subject = ? LIMIT 1', (subject,))
    result = c.fetchone()
    conn.close()
    
    # If found in the database, add to the cache
    if result is not None:
        subjects_cache.add(subject)
        print(f"Subject {subject} was found in db, but not cache. Adding to cache.")
        return True
    
    print(f"Subject {subject} was not found in database. New incident.")
    log(f"Subject {subject} was not found in database. New incident.")
    return False

def call_zabbix_api(api_url, auth_token, method, params):
    """
    Function to call the Zabbix API.

    :param api_url: Zabbix API URL (e.g., http://your-zabbix-server/zabbix/api_jsonrpc.php)
    :param auth_token: The authentication token (Bearer Token or API Token)
    :param method: The API method to call (e.g., 'host.get')
    :param params: The parameters to pass to the API method
    :return: The response from the API in JSON format
    """
    
    # Define the headers with the auth token
    headers = {
        'Content-Type': 'application/json-rpc',
    }
    
    # Create the payload for the API request
    payload = {
        'jsonrpc': '2.0',
        'method': method,
        'params': params,
        'auth': auth_token,  # Required for methods that need authentication
        'id': 1  # Request ID, arbitrary, can be used to track the request
    }

    try:
        # Make the API request
        response = requests.post(api_url, headers=headers, data=json.dumps(payload), verify=False)  # Disable SSL verification for now
        
        # Check for a successful response
        if response.status_code == 200:
            return response.json()
        else:
            # Raise an exception if the request failed
            response.raise_for_status()
    except requests.exceptions.RequestException as e:
        print(f"Error calling Zabbix API: {e}")
        return None

def pull_zabbix_events():
    # Not suppressed, not acknowledged.
    now = datetime.datetime.now()
    one_month_ago = now - datetime.timedelta(days=30)
    timestamp_one_month_ago = int(time.mktime(one_month_ago.timetuple()))

    eventparams1 = {
        "output": "extend",  # Retrieve full details of each event
        "selectTags": "extend",
        "sortfield": ["eventid"],  # Sort by clock and eventid
        "sortorder": "DESC",  # Reverse chronological order
        "limit": 4000,
        "recent": True,
        "acknowledged": False,
        "severities": [2,3,4,5],
        "suppressed": False,
        "time_from": timestamp_one_month_ago
    }

    eventparams2 = {
        "output": "extend",  # Retrieve full details of each event
        "selectTags": "extend",
        "sortfield": ["eventid"],  # Sort by clock and eventid
        "sortorder": "DESC",  # Reverse chronological order
        "limit": 4000,
        "recent": True,
        "acknowledged": True,
        "severities": [2,3,4,5],
        "suppressed": False,
        "time_from": timestamp_one_month_ago
    }

    eventparams3 = {
        "output": "extend",  # Retrieve full details of each event
        "selectTags": "extend",
        "sortfield": ["eventid"],  # Sort by clock and eventid
        "sortorder": "DESC",  # Reverse chronological order
        "limit": 4000,
        "recent": True,
        "acknowledged": False,
        "severities": [2,3,4,5],
        "suppressed": True,
        "time_from": timestamp_one_month_ago
    }

    eventparams4 = {
        "output": "extend",  # Retrieve full details of each event
        "selectTags": "extend",
        "sortfield": ["eventid"],  # Sort by clock and eventid
        "sortorder": "DESC",  # Reverse chronological order
        "limit": 4000,
        "recent": True,
        "acknowledged": True,
        "severities": [2,3,4,5],
        "suppressed": True,
        "time_from": timestamp_one_month_ago
    }

    resp1 = call_zabbix_api(credentials.ZABBIXURL, credentials.ZABBIXTOKEN, "problem.get", eventparams1)
    resp2 = call_zabbix_api(credentials.ZABBIXURL, credentials.ZABBIXTOKEN, "problem.get", eventparams2)
    resp3 = call_zabbix_api(credentials.ZABBIXURL, credentials.ZABBIXTOKEN, "problem.get", eventparams3)
    resp4 = call_zabbix_api(credentials.ZABBIXURL, credentials.ZABBIXTOKEN, "problem.get", eventparams4)

    combined_results = {
        'result': []
    }


    # Combine all the 'result' lists into one
    combined_results['result'].extend(resp1.get('result', []))
    combined_results['result'].extend(resp2.get('result', []))
    combined_results['result'].extend(resp3.get('result', []))
    combined_results['result'].extend(resp4.get('result', []))
    combined_results['result'].sort(key=lambda event: event['clock'], reverse=True)

 

    activeevents = []
    for event in combined_results['result']:
        activeevents.append(event['eventid'])
    print(f"Found {len(activeevents)} events on Zabbix.")
    activeevents_tuple = tuple(activeevents)
    conn = sqlite3.connect('incidents.db')
    c = conn.cursor()
    c.execute('''
        DELETE FROM zabbixevents
        WHERE subject NOT IN ({})
    '''.format(','.join('?' for _ in activeevents)), activeevents_tuple)
    print(f"Deleted {c.rowcount} rows that are no longer active.")
    conn.commit()
    conn.close()

    conn = sqlite3.connect('incidents.db')
    c = conn.cursor()
    c.execute('''SELECT subject FROM zabbixevents''')
    subjects = c.fetchall()
    subject_list = [subject[0] for subject in subjects]
    print(f"Subjects found in database: {len(subject_list)}")
    conn.close()

    for event in combined_results['result']:
        if event['eventid'] in subject_list:
            print(f"{event['eventid']} already exists, skipping.")
            continue
        timestamp = int(event['clock'])
        timestamp = datetime.datetime.utcfromtimestamp(timestamp)
        timestamp = timestamp.strftime("%B %d, %Y at %I:%M %p")
        host_names = get_hosts_for_event(event['eventid'])
        severity_num = int(event['severity'])
        severity = ZABBIX_SEVERITIES.get(severity_num)
        
        subject = event['eventid']

        problembody = ""
        problembody = problembody + (", ".join(tag['value'] for tag in event['tags']) + "\n")
        problembody = problembody + (f"Problem: (Severity: {severity})\n")
        if event['name']:
            problembody = problembody + (f"{event['name']}\n")
        if event['opdata']:
            problembody = problembody + (f"{event['opdata']}\n")
        
        
        print("Getting related text.")
        relatedbody = handle_new_zabbix_incident(problembody)
        problembody = (f"{event['eventid']} | {host_names} | {timestamp}\n") +  problembody
        problembody = problembody.replace("\n", "<br>")
        problembody = ragresultparse(problembody, True, True)
        relatedbody = ragresultparse(relatedbody, True)

        conn = sqlite3.connect('incidents.db')
        c = conn.cursor()
        print(f"Attempting to insert {event['eventid']} into database.")
        c.execute('''INSERT INTO zabbixevents (timestamp, subject, issuebody, relatedbody)
                    VALUES (?, ?, ?, ?)''', 
                    (timestamp, subject, problembody, relatedbody))
        conn.commit()
        conn.close()


def get_hosts_for_event(eventid):
    headers = {
        'Content-Type': 'application/json-rpc'
    }
    eventparams = {
        "output": "extend",  # Retrieve full details of each event
        "selectTags": "extend",
        "selectHosts": "extend",
        "sortfield": ["eventid"],  # Sort by clock and eventid
        "sortorder": "DESC",  # Reverse chronological order
        "limit": 4000,
        "eventids": eventid
    }
    payload = {
        'jsonrpc': '2.0',
        'method': "event.get",
        'params': eventparams,
        'auth': credentials.ZABBIXTOKEN,  # Required for methods that need authentication
        'id': 1  # Request ID, arbitrary, can be used to track the request
    }
    try:
        # Make the API request
        response = requests.post(credentials.ZABBIXURL, headers=headers, data=json.dumps(payload), verify=False)  # Disable SSL verification for now
        
        # Check for a successful response
        if response.status_code == 200:
            response = response.json()
            result = response['result']
            event = result[0]
            host_names = ", ".join(host['host'] for host in event['hosts'])
            return host_names
        else:
            # Raise an exception if the request failed
            response.raise_for_status()
    except requests.exceptions.RequestException as e:
        print(f"Error calling Zabbix API: {e}")
        return None

def get_latest_zabbix_event_doc_id():
    # Placeholder for API call, for now assuming a mock response
    response = requests.get("http://wsvraloan1008:8001/v1/ingest/list")
    data = response.json()  # Parse JSON response
    
    latest_date = None
    latest_doc_id = None

    # Iterate through the list of documents
    for document in data['data']:
        file_name = document['doc_metadata']['file_name']
        # Look for the zabbix_events file in the filename
        if 'zabbix_events' in file_name:
            # Extract the date part and convert to datetime object
            try:
                date_part = file_name.replace('zabbix_events_', '').replace('.txt', '')
                date_object = datetime.datetime.strptime(date_part, '%Y-%m-%d')
                # Check if it's the latest date
                if latest_date is None or date_object > latest_date:
                    latest_date = date_object
                    latest_doc_id = document['doc_id']
            except ValueError:
                # If the date part is not in the right format, skip this entry
                continue
    
    return latest_doc_id

def handle_new_zabbix_incident(body):
    # This is the PrivateGPT server.
    url = "http://wsvraloan1008:8001/v1/chunks"
    headers = {
        "Content-Type": "application/json"
    }
    # Could be finetuned, 20 is usually excessive but it doesn't hurt to have more
    latestzabbixfile = get_latest_zabbix_event_doc_id()
    data = {
    "text": f"{body}",
    "limit": 3,
    "prev_next_chunks": 20,
    "context_filter": {
        "docs_ids": [f"{latestzabbixfile}"]
    }
}

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

def startup_and_loop():
    print("Starting loop. Incidents will be retrieved every 1 minute.")
    log("Starting loop. Incidents will be retrieved every 1 minute.")
    while True:
        pull_zabbix_events()
        print("Snoozing it up for a minute...")
        time.sleep(60)

if __name__ == '__main__':
    startup_and_loop()