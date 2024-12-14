import requests
import json
import datetime
import credentials

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
    eventparams = {
        "output": "extend",  # Retrieve full details of each event
        "selectAcknowledges": "extend",  # Include acknowledgements subobject
        "selectTags": "extend",
        "selectHosts": "extend",
        "sortfield": ["clock", "eventid"],  # Sort by clock and eventid
        "sortorder": "DESC",  # Reverse chronological order
        "limit": 4000,
        "acknowledged": True
    }

    test_json = call_zabbix_api(ZABBIXURL, ZABBIXTOKEN, "event.get", eventparams)

    # Check if we got a valid response
    if not test_json or 'result' not in test_json:
        print("No events found or failed to retrieve data.")
        exit()

    # Get the most recent event timestamp
    latest_event = test_json['result'][0]
    most_recent_timestamp = int(latest_event['clock'])
    most_recent_date = datetime.datetime.utcfromtimestamp(most_recent_timestamp).strftime("%Y-%m-%d")

    # Set the filename based on the most recent event date
    filename = f"zabbix_events_{most_recent_date}.txt"

    # Open the file for writing
    with open(filename, 'w') as file:
        for event in test_json['result']:
            # Check if at least one 'ack_message' (acknowledgment) has a non-empty 'message'
            if any(ack_message['message'] for ack_message in event['acknowledges']):
                # Format and write event details
                timestamp = int(event['clock'])
                timestamp = datetime.datetime.utcfromtimestamp(timestamp)
                timestamp = timestamp.strftime("%B %d, %Y at %I:%M %p")
                host_names = ", ".join(host['host'] for host in event['hosts'])
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
                
                # Write acknowledgements if present
                for ack_message in event['acknowledges']:
                    if ack_message['message']:
                        file.write(f"Solution:\n{ack_message['message']}\n")
                
                # Add some spacing between events
                file.write("\n\n--------------------------------------------------------------\n\n\n")

    print(f"Event details written to {filename}")
    print(f"Number of events retrieved: {len(test_json['result'])}")

