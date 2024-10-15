import requests
from requests.auth import HTTPBasicAuth
import json
import credentials
import sqlite3
import time
import datetime

def log(message):
    # Get current time and format it
    timestamp = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    
    # Open the log file in append mode
    with open('log.txt', 'a') as log_file:
        log_file.write(f"[{timestamp}] {message}\n")

subjects_cache = set()

def load_subjects_cache():
    # Connect to the database
    conn = sqlite3.connect('incidents.db')
    c = conn.cursor()

    # Query to get all subjects from the database
    c.execute('SELECT subject FROM incidents')
    
    # Fetch all subjects and add them to the in-memory cache (set)
    subjects = c.fetchall()
    subjects_cache.update(subject[0] for subject in subjects)  # subject[0] because fetchall returns tuples
    
    # Close the connection
    conn.close()
    #print("Subjects loaded from db into cache, theoretically.")

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


def get_id_from_inc(subject):
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json"
    }

    params = {
        'sysparm_limit': 10,
        'sysparm_display_value': True,
        'sysparm_fields':'sys_id, number',
        'number':f'{subject}',
        'sysparm_query': 'ORDERBYDESCopened_at'
    }
    print(f"Pulling in sys_id for {subject}...")
    log(f"Pulling in sys_id for {subject}...")
    response = requests.get(credentials.endpoint, auth=HTTPBasicAuth(credentials.user, credentials.password), headers=headers, params=params)

    if response.status_code == 200:
        #print(f"SUCCESS...")
        incidents = response.json()['result']
        if len(incidents) == 1:
            incident = incidents[0]
            return incident.get('sys_id')
        else:
            return "na"
        

    else:
        print(f"Error: {response.status_code} - {response.text}")
        log(f"Error: {response.status_code} - {response.text}")




def pull_servicenow_incidents():
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json"
    }

    params = {
        'sysparm_limit': 10,
        'sysparm_display_value': True,
        'sysparm_fields':'sys_id, number, assignment_group, description, opened_at, assigned_to, short_description, u_email,',
        'assignment_group':'Medical Devices-Medical System Middleware',
        'sysparm_query': 'ORDERBYDESCopened_at'
    }
    #print(f"Checking ServiceNow API for new incidents...")
    response = requests.get(credentials.endpoint, auth=HTTPBasicAuth(credentials.user, credentials.password), headers=headers, params=params)

    if response.status_code == 200:
        #print(f"SUCCESS...")
        incidents = response.json()['result']
        reversed_incidents = incidents[::-1]
        for incident in reversed_incidents:
            subject = incident.get('number')
            if check_incident(subject) == True:
                #print(f"Moving onto next incident...")
                continue
            shortdesc = incident.get('short_description')
            desc = incident.get('description')
            sys_id = incident.get('sys_id')
            sender = incident.get('u_email')
            timestamp = incident.get('opened_at')

            body = f"{shortdesc}<br>{desc}"
            snurl = f"{credentials.servicenow_instance}/nav_to.do?uri=incident.do?sys_id={sys_id}"

            url = "http://127.0.0.1:5001/receive-email"

            # Define the data to send in the POST request
            data = {
                "subject": f"{subject}",
                "sender": f"{sender}",
                "body": f"{body}",
                "snurl": f"{snurl}",
                "timestamp": f"{timestamp}"
            }

            # Convert the data to JSON format
            headers = {
                'Content-Type': 'application/json'
            }

            # Send the POST request
            print(f"Sending {subject} to webUI...")
            log(f"Sending {subject} to webUI...")
            response = requests.post(url, data=json.dumps(data), headers=headers)
            #print(f"Waiting 3 minutes for AI generation...")
            time.sleep(180)
    else:
        print(f"Error: {response.status_code} - {response.text}")
        log(f"Error: {response.status_code} - {response.text}")

def delete_from_cache(incident_id):
    subjects_cache.discard(incident_id)
    print(f"Incident with subject '{incident_id}' removed from cache.")
    log(f"Incident with subject '{incident_id}' removed from cache.")

def startup_and_loop():
    print("Starting loop. Incidents will be retrieved every 1 minute.")
    log("Starting loop. Incidents will be retrieved every 1 minute.")
    while True:
        load_subjects_cache()
        pull_servicenow_incidents()
        #print("Snoozing it up for a minute...")
        time.sleep(60)

if __name__ == '__main__':
    startup_and_loop()