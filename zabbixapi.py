import requests
from requests.auth import HTTPBasicAuth
import json
import credentials
import sqlite3
import time
import datetime
from app import combine_text_blocks, find_most_similar_section, ragresultparse

# This helper script is used to pull incidents from Zabbix and send them to the webUI for processing.

ZABBIX_SEVERITIES = {
    0: "Not classified",
    1: "Information",
    2: "Warning",
    3: "Average",
    4: "High",
    5: "Disaster",
}

# Severity to color mapping for the UI
SEVERITY_COLORS = {
    0: "#808080",  # Gray for not classified
    1: "#97AAB3",  # Light blue for information
    2: "#FFC859",  # Yellow for warning
    3: "#FFA059",  # Orange for average
    4: "#E97659",  # Red-orange for high
    5: "#E45959",  # Red for disaster
}

def log(message):
    timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with open("log.txt", "a") as log_file:
        log_file.write(f"[{timestamp}] {message}\n")

def setup_database():
    """Create or update the database schema."""
    print("Setting up database...")
    conn = sqlite3.connect("incidents.db")
    c = conn.cursor()
    
    # Check if severity and hostname columns exist
    c.execute("PRAGMA table_info(zabbixevents)")
    columns = [column[1] for column in c.fetchall()]
    
    if "severity" not in columns or "hostname" not in columns:
        print("Creating or updating table schema...")
        # Create temporary table with new schema
        c.execute("""
            CREATE TABLE IF NOT EXISTS zabbixevents_new (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT,
                subject TEXT UNIQUE,
                issuebody TEXT,
                relatedbody TEXT,
                severity INTEGER,
                hostname TEXT
            )
        """)
        
        # If old table exists, migrate data
        if "zabbixevents" in [table[0] for table in c.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]:
            c.execute("""
                INSERT INTO zabbixevents_new (timestamp, subject, issuebody, relatedbody)
                SELECT timestamp, subject, issuebody, relatedbody FROM zabbixevents
            """)
            c.execute("DROP TABLE zabbixevents")
        
        # Rename new table to original name
        c.execute("ALTER TABLE zabbixevents_new RENAME TO zabbixevents")
    
    conn.commit()
    conn.close()
    print("Database setup complete")

def check_incident(subject):
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

def call_zabbix_api(api_url, auth_token, method, params):
    """
    Calls the Zabbix API with the given method and parameters.
    https://www.zabbix.com/documentation/current/en/manual/api/reference/problem/get for the problem.get method.
    """
    print(f"\nCalling Zabbix API with method: {method}")
    headers = {
        "Content-Type": "application/json-rpc",
    }
    payload = {
        "jsonrpc": "2.0",
        "method": method,
        "params": params,
        "auth": auth_token,
        "id": 1,
    }
    try:
        response = requests.post(
            api_url, headers=headers, data=json.dumps(payload), verify=False
        )
        print(f"API Response Status Code: {response.status_code}")
        if response.status_code == 200:
            json_response = response.json()
            if "result" in json_response:
                print(f"API returned {len(json_response['result'])} results")
            return json_response
        else:
            print(f"API Error: {response.text}")
            response.raise_for_status()
    except requests.exceptions.RequestException as e:
        print(f"Error calling Zabbix API: {e}")
        return None

def pull_zabbix_events():
    """Looks for new Zabbix events and adds them to the database, gets related previous incidents, etc."""
    print("\n=== Starting Zabbix event pull ===")
    # Ensure database is properly set up
    setup_database()
    
    now = datetime.datetime.now()
    one_month_ago = now - datetime.timedelta(days=30)
    timestamp_one_month_ago = int(time.mktime(one_month_ago.timetuple()))
    
    # Base parameters for all queries - only get Warning and above severities
    base_params = {
        "output": "extend",
        "selectTags": "extend",
        "sortfield": ["eventid"],
        "sortorder": "DESC",
        "limit": 4000,
        "severities": [2, 3, 4, 5],  # Warning, Average, High, Disaster only
        "time_from": timestamp_one_month_ago,
    }
    
    # Different parameter combinations
    param_combinations = [
        {"acknowledged": False, "suppressed": False},
        {"acknowledged": True, "suppressed": False},
        {"acknowledged": False, "suppressed": True},
        {"acknowledged": True, "suppressed": True},
    ]
    
    combined_results = {"result": []}
    for params in param_combinations:
        print(f"\nTrying parameter combination: {params}")
        event_params = {**base_params, **params, "recent": True}
        resp = call_zabbix_api(
            credentials.ZABBIXURL, credentials.ZABBIXTOKEN, "problem.get", event_params
        )
        if resp and "result" in resp:
            print(f"Got {len(resp['result'])} events for this combination")
            combined_results["result"].extend(resp["result"])
    
    combined_results["result"].sort(key=lambda event: event["clock"], reverse=True)
    activeevents = [event["eventid"] for event in combined_results["result"]]
    
    print(f"\nTotal active events found: {len(activeevents)}")
    
    # Clean up old events
    conn = sqlite3.connect("incidents.db")
    c = conn.cursor()
    if activeevents:
        c.execute(
            """
            DELETE FROM zabbixevents
            WHERE subject NOT IN ({})
            """.format(
                ",".join("?" for _ in activeevents)
            ),
            activeevents,
        )
    print(f"Deleted {c.rowcount} old events from database")
    conn.commit()
    
    # Get existing subjects
    c.execute("""SELECT subject FROM zabbixevents""")
    subject_list = [subject[0] for subject in c.fetchall()]
    print(f"Current events in database: {len(subject_list)}")
    conn.close()
    
    # Process new events
    new_events_count = 0
    for event in combined_results["result"]:
        if event["eventid"] in subject_list:
            print(f"Event {event['eventid']} already exists, skipping")
            continue
            
        print(f"\nProcessing new event: {event['eventid']}")
        timestamp = datetime.datetime.utcfromtimestamp(int(event["clock"])).strftime("%B %d, %Y at %I:%M %p")
        host_names = get_hosts_for_event(event["eventid"])
        severity_num = int(event["severity"])
        severity = ZABBIX_SEVERITIES.get(severity_num)
        
        problembody = ", ".join(tag["value"] for tag in event["tags"]) + "\n"
        problembody += f"Problem: (Severity: {severity})\n"
        if event["name"]:
            problembody += f"{event['name']}\n"
        if event["opdata"]:
            problembody += f"{event['opdata']}\n"

        print("Getting related text for event")
        relatedbody = handle_new_zabbix_incident(problembody)
        problembody = f"{event['eventid']} | {host_names} | {timestamp}\n" + problembody
        problembody = problembody.replace("\n", "<br>")
        problembody = ragresultparse(problembody, True, True)
        relatedbody = ragresultparse(relatedbody, True)
        
        print("Inserting event into database")
        conn = sqlite3.connect("incidents.db")
        c = conn.cursor()
        try:
            c.execute(
                """INSERT INTO zabbixevents 
                   (timestamp, subject, issuebody, relatedbody, severity, hostname)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (timestamp, event["eventid"], problembody, relatedbody, severity_num, host_names),
            )
            conn.commit()
            new_events_count += 1
            print(f"Successfully inserted event {event['eventid']}")
        except sqlite3.Error as e:
            print(f"Database error inserting event {event['eventid']}: {e}")
        finally:
            conn.close()
    
    print(f"\n=== Zabbix event pull complete ===")
    print(f"Added {new_events_count} new events")
    print("Waiting for next cycle...\n")

def get_hosts_for_event(eventid):
    """Gets the host names for a given event ID."""
    print(f"Getting host names for event {eventid}")
    headers = {"Content-Type": "application/json-rpc"}
    eventparams = {
        "output": "extend",
        "selectTags": "extend",
        "selectHosts": "extend",
        "sortfield": ["eventid"],
        "sortorder": "DESC",
        "limit": 4000,
        "eventids": eventid,
    }
    payload = {
        "jsonrpc": "2.0",
        "method": "event.get",
        "params": eventparams,
        "auth": credentials.ZABBIXTOKEN,
        "id": 1,
    }
    try:
        response = requests.post(
            credentials.ZABBIXURL,
            headers=headers,
            data=json.dumps(payload),
            verify=False,
        )
        if response.status_code == 200:
            response = response.json()
            result = response["result"]
            event = result[0]
            host_names = ", ".join(host["host"] for host in event["hosts"])
            print(f"Found hosts: {host_names}")
            return host_names
        else:
            print(f"Error getting hosts: {response.status_code} - {response.text}")
            response.raise_for_status()
    except requests.exceptions.RequestException as e:
        print(f"Error calling Zabbix API: {e}")
        return None

def get_latest_zabbix_event_doc_id():
    response = requests.get("https://wsmwsllm01.healthy.bewell.ca:8001/v1/ingest/list", verify=False)
    data = response.json()

    latest_date = None
    latest_doc_id = None

    for document in data["data"]:
        file_name = document["doc_metadata"]["file_name"]
        if "zabbix_events" in file_name:
            try:
                date_part = file_name.replace("zabbix_events_", "").replace(".txt", "")
                date_object = datetime.datetime.strptime(date_part, "%Y-%m-%d")
                if latest_date is None or date_object > latest_date:
                    latest_date = date_object
                    latest_doc_id = document["doc_id"]
            except ValueError:
                continue

    return latest_doc_id

def handle_new_zabbix_incident(body):
    url = "https://wsmwsllm01.healthy.bewell.ca:8001/v1/chunks"
    headers = {
        "Content-Type": "application/json"
    }
    latestzabbixfile = get_latest_zabbix_event_doc_id()
    data = {
        "text": f"{body}",
        "limit": 3,
        "prev_next_chunks": 20,
        "context_filter": {"docs_ids": [f"{latestzabbixfile}"]},
    }

    response = requests.post(url, json=data, headers=headers, verify=False)
    print(f"Received chunk request. Status: {response.status_code}")
    log(f"Received chunk request. Status: {response.status_code}")
    returndata = response.json()
    finaltext = ""
    for idx, item in enumerate(returndata["data"]):
        text = item.get("text")
        previous_texts = item.get("previous_texts", [])
        next_texts = item.get("next_texts", [])
        finalchunk = combine_text_blocks(text, previous_texts, next_texts)
        theincident = find_most_similar_section(finalchunk, text)
        finaltext = finaltext + f"---- {idx+1} ----\n" + theincident + "\n\n\n"
    finaltext = finaltext.replace("\n", "<br>")
    return finaltext

def startup_and_loop():
    print("Starting loop. Incidents will be retrieved every 1 minute.")
    log("Starting loop. Incidents will be retrieved every 1 minute.")
    while True:
        pull_zabbix_events()
        print("Snoozing it up for a minute...")
        time.sleep(60)

if __name__ == "__main__":
    startup_and_loop()
