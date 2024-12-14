import requests
import json
import datetime
import credentials
import time

ZABBIXURL = credentials.ZABBIXURL
ZABBIXTOKEN = credentials.ZABBIXTOKEN

ZABBIX_SEVERITIES = {
    0: "Not classified",
    1: "Information",
    2: "Warning",
    3: "Average",
    4: "High",
    5: "Disaster"
}

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


if __name__ == "__main__":
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

    resp1 = call_zabbix_api(ZABBIXURL, ZABBIXTOKEN, "problem.get", eventparams1)
    resp2 = call_zabbix_api(ZABBIXURL, ZABBIXTOKEN, "problem.get", eventparams2)
    resp3 = call_zabbix_api(ZABBIXURL, ZABBIXTOKEN, "problem.get", eventparams3)
    resp4 = call_zabbix_api(ZABBIXURL, ZABBIXTOKEN, "problem.get", eventparams4)

    combined_results = {
        'result': []
    }

    # Combine all the 'result' lists into one
    combined_results['result'].extend(resp1.get('result', []))
    combined_results['result'].extend(resp2.get('result', []))
    combined_results['result'].extend(resp3.get('result', []))
    combined_results['result'].extend(resp4.get('result', []))
    combined_results['result'].sort(key=lambda event: event['clock'], reverse=True)

    

    # Get the most recent event timestamp
    latest_event = combined_results['result'][0]
    most_recent_timestamp = int(latest_event['clock'])
    most_recent_date = datetime.datetime.utcfromtimestamp(most_recent_timestamp).strftime("%Y-%m-%d")

    # Set the filename based on the most recent event date
    filename = f"zabbix_problems_{most_recent_date}.txt"

    # Open the file for writing
    with open(filename, 'w') as file:
        for event in combined_results['result']:
            if True:
                # Format and write event details
                timestamp = int(event['clock'])
                timestamp = datetime.datetime.utcfromtimestamp(timestamp)
                timestamp = timestamp.strftime("%B %d, %Y at %I:%M %p")
                host_names = get_hosts_for_event(event['eventid'])
                severity_num = int(event['severity'])
                severity = ZABBIX_SEVERITIES.get(severity_num)

                # Write to file
                file.write(f"{event['eventid']} | {host_names} | {timestamp}\n")
                file.write(", ".join(tag['value'] for tag in event['tags']) + "\n")
                file.write(f"Problem: (Severity: {severity})\n")
                if event['name']:
                    file.write(f"{event['name']}\n")
                if event['opdata']:
                    file.write(f"{event['opdata']}\n")
            
                
                # Add some spacing between events
                file.write("\n\n--------------------------------------------------------------\n\n\n")

    print(f"Event details written to {filename}")
    print(f"Number of events retrieved: {len(combined_results['result'])}")

