import requests
from requests.auth import HTTPBasicAuth
import json
import credentials
import sqlite3
import time

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
    print("Subjects loaded from db into cache, theoretically.")

def check_incident(subject):
    # Check the in-memory cache first
    if subject in subjects_cache:
        print(f"Subject {subject} was found in cache.")
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
    
    print(f"Subject {subject} was not found. New incident.")
    return False

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
    print(f"Checking ServiceNow API for new incidents...")
    response = requests.get(credentials.endpoint, auth=HTTPBasicAuth(credentials.user, credentials.password), headers=headers, params=params)

    if response.status_code == 200:
        print(f"SUCCESS...")
        incidents = response.json()['result']
        reversed_incidents = incidents[::-1]
        for incident in reversed_incidents:
            subject = incident.get('number')
            if check_incident(subject) == True:
                print(f"Moving onto next incident...")
                continue
            shortdesc = incident.get('short_description')
            desc = incident.get('description')
            sys_id = incident.get('sys_id')
            sender = incident.get('u_email')

            body = f"{shortdesc}<br>{desc}"
            snurl = f"{credentials.servicenow_instance}/nav_to.do?uri=incident.do?sys_id={sys_id}"

            url = "http://localhost:5000/receive-email"

            # Define the data to send in the POST request
            data = {
                "subject": f"{subject}",
                "sender": f"{sender}",
                "body": f"{body}",
                "snurl": f"{snurl}"
            }

            # Convert the data to JSON format
            headers = {
                'Content-Type': 'application/json'
            }

            # Send the POST request
            print(f"Sending {subject} to webUI...")
            response = requests.post(url, data=json.dumps(data), headers=headers)
            print(f"Waiting 4 minutes for AI generation...")
            time.sleep(240)
    else:
        print(f"Error: {response.status_code} - {response.text}")



load_subjects_cache()
while True:
    pull_servicenow_incidents()
    time.sleep(60)