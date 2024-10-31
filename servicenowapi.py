# This helper script is used to pull incidents from ServiceNow and send them to the webUI for processing.

import requests
from requests.auth import HTTPBasicAuth
import json
import credentials
import sqlite3
import time
import datetime

subjects_cache = set()


def log(message):
    """Logs a message to a file with a timestamp."""
    timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with open("log.txt", "a") as log_file:
        log_file.write(f"[{timestamp}] {message}\n")


def load_subjects_cache():
    """Loads all subjects from the database into the cache."""
    conn = sqlite3.connect("incidents.db")
    c = conn.cursor()
    c.execute("SELECT subject FROM incidents")
    subjects = c.fetchall()
    subjects_cache.update(subject[0] for subject in subjects)
    conn.close()


def check_incident(subject):
    """Checks if the incident is already in the cache. If not, checks the database. If the incident exists in either, returns True."""
    if subject in subjects_cache:
        return True
    conn = sqlite3.connect("incidents.db")
    c = conn.cursor()
    c.execute("SELECT 1 FROM incidents WHERE subject = ? LIMIT 1", (subject,))
    result = c.fetchone()
    conn.close()
    if result is not None:
        subjects_cache.add(subject)
        print(f"Subject {subject} was found in db, but not cache. Adding to cache.")
        return True
    print(f"Subject {subject} was not found in database. New incident.")
    log(f"Subject {subject} was not found in database. New incident.")
    return False


def get_id_from_inc(subject):
    """ServiceNow doesn't use the INC____ in the URLs, there's a different unique identifier. This pulls that sys_id for the incident."""
    headers = {"Content-Type": "application/json", "Accept": "application/json"}
    params = {
        "sysparm_limit": 10,
        "sysparm_display_value": True,
        "sysparm_fields": "sys_id, number",
        "number": f"{subject}",
        "sysparm_query": "ORDERBYDESCopened_at",
    }
    print(f"Pulling in sys_id for {subject}...")
    log(f"Pulling in sys_id for {subject}...")
    response = requests.get(
        credentials.endpoint,
        auth=HTTPBasicAuth(credentials.user, credentials.password),
        headers=headers,
        params=params,
    )
    if response.status_code == 200:
        incidents = response.json()["result"]
        if len(incidents) == 1:
            incident = incidents[0]
            return incident.get("sys_id")
        else:
            return "na"
    else:
        print(f"Error: {response.status_code} - {response.text}")
        log(f"Error: {response.status_code} - {response.text}")


def pull_servicenow_incidents():
    """Pulls incidents from ServiceNow and sends them to the webUI for processing."""
    headers = {"Content-Type": "application/json", "Accept": "application/json"}
    params = {
        "sysparm_limit": 10,
        "sysparm_display_value": True,
        "sysparm_fields": "sys_id, number, assignment_group, description, opened_at, assigned_to, short_description, u_email, cmdb_ci",
        "assignment_group": "Medical Devices-Medical System Middleware",
        "sysparm_query": "ORDERBYDESCopened_at",
    }
    response = requests.get(
        credentials.endpoint,
        auth=HTTPBasicAuth(credentials.user, credentials.password),
        headers=headers,
        params=params,
    )
    if response.status_code == 200:
        incidents = response.json()["result"]
        reversed_incidents = incidents[::-1]
        for incident in reversed_incidents:
            config_item = incident.get("cmdb_ci")
            config_item = config_item.get("display_value")
            subject = incident.get("number")
            if check_incident(subject) == True:
                continue
            shortdesc = incident.get("short_description")
            desc = incident.get("description")
            sys_id = incident.get("sys_id")
            sender = incident.get("u_email")
            timestamp = incident.get("opened_at")
            body = f"{config_item}<br>{shortdesc}<br>{desc}"
            snurl = f"{credentials.servicenow_instance}/nav_to.do?uri=incident.do?sys_id={sys_id}"
            url = "http://127.0.0.1:5001/receive-email"
            data = {
                "subject": f"{subject}",
                "sender": f"{sender}",
                "body": f"{body}",
                "snurl": f"{snurl}",
                "timestamp": f"{timestamp}",
            }
            headers = {"Content-Type": "application/json"}
            print(f"Sending {subject} to webUI...")
            log(f"Sending {subject} to webbUI...")
            response = requests.post(url, data=json.dumps(data), headers=headers)
            time.sleep(180)
    else:
        print(f"Error: {response.status_code} - {response.text}")
        log(f"Error: {response.status_code} - {response.text}")


def delete_from_cache(incident_id):
    """Deletes an incident from the cache. Runs when someone deletes something from the webUI."""
    subjects_cache.discard(incident_id)
    print(f"Incident with subject '{incident_id}' removed from cache.")
    log(f"Incident with subject '{incident_id}' removed from cache.")


def startup_and_loop():
    """Starts the loop and runs the initial cache load."""
    print("Starting loop. Incidents will be retrieved every 1 minute.")
    log("Starting loop. Incidents will be retrieved every 1 minute.")
    while True:
        load_subjects_cache()
        pull_servicenow_incidents()
        time.sleep(60)


if __name__ == "__main__":
    startup_and_loop()
