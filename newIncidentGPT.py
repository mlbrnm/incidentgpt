import requests
from requests.auth import HTTPBasicAuth
import json
import credentials
from datetime import datetime
import difflib
import ollama
import logging

STATE_MAPPING = {
    "1": "New",
    "2": "In Progress",
    "3": "On Hold",
    "4": "Pending",
    "5": "Pending Approval",
    "6": "Resolved",
    "7": "Closed",
    "8": "Canceled"
}

def get_work_notes(sys_id, logging):
    """Gets the work notes for a specific incident."""
    headers = {"Content-Type": "application/json", "Accept": "application/json"}
    params = {
        "sysparm_display_value": True,
        "sysparm_fields": "work_notes,state",
        "sysparm_query": f"sys_id={sys_id}",
    }
    
    try:
        logging.info(f"Fetching work notes for incident {sys_id}")
        response = requests.get(
            credentials.endpoint,
            auth=HTTPBasicAuth(credentials.user, credentials.password),
            headers=headers,
            params=params,
        )
        
        if response.status_code == 200:
            incident = response.json()["result"][0]
            logging.debug(f"Work notes fetched successfully for {sys_id}")
            return {
                "work_notes": incident.get("work_notes", ""),
                "state": incident.get("state", "")
            }
        else:
            logging.error(f"Failed to fetch work notes: {response.status_code} - {response.text}")
    except Exception as e:
        logging.error(f"Error getting work notes: {str(e)}")
    
    return {"work_notes": "", "state": ""}

def get_rag_context(description):
    """Get relevant context from RAG database"""
    url = "https://wsmwsllm01.healthy.bewell.ca:8001/v1/chunks"
    headers = {"Content-Type": "application/json"}
    data = {
        "text": description,
        "limit": 5
    }
    
    try:
        response = requests.post(url, json=data, headers=headers, verify=False)
        if response.status_code == 200:
            returndata = response.json()
            finaltext = ""
            for idx, item in enumerate(returndata["data"]):
                text = item.get("text", "")
                previous_texts = item.get("previous_texts", [])
                next_texts = item.get("next_texts", [])
                
                # Combine text blocks
                combined = "".join(previous_texts[::-1]) + text + "".join(next_texts)
                relevant = find_most_similar_section(combined, text)
                finaltext += f"---- {idx+1} ----\n{relevant}\n\n"
            
            return finaltext.replace("\n", "<br>")
    except Exception as e:
        print(f"RAG Error: {str(e)}")
    return ""

def find_most_similar_section(big_string, substring, separator="--------------------------------------------------------------"):
    """Find most relevant section in combined text blocks"""
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
    
    return best_section if best_section else "Not found."

def generate_solution(incident_number, ci, description, work_notes, rag_context):
    """Generate new solution using LLM"""
    logging.info(f"Generating solution for incident {incident_number}")
    try:
        prompt = f"""You are an AI working for a healthcare IT team called the Middleware Services Team (MWS).
The following are solved/closed tickets that contain possible solutions to this problem.

Context from similar incidents:
{rag_context}

Current Incident:
CI: {ci}
Problem: {description}

Work Notes History:
{work_notes}

Based on ALL available information above, determine a concise potential solution.
If the context is not relevant, answer that you do not know.
Output only a few sentences or less, with no preamble."""

        response = ollama.generate(
            model="llama3.1:8b-instruct-q4_K_M",
            prompt=prompt,
            keep_alive="120m"
        )
        
        return response.get('response', 'Failed to generate solution')
    except Exception as e:
        logging.error(f"Solution Generation Error: {str(e)}")
        return "Failed to generate solution"
    
def pull_servicenow_incidents(logging):
    """Pulls unresolved incidents from ServiceNow."""
    headers = {"Content-Type": "application/json", "Accept": "application/json"}
    params = {
        "sysparm_limit": 50,
        "sysparm_display_value": True,
        "sysparm_fields": "sys_id,number,assignment_group,description,opened_at,short_description,u_email,cmdb_ci,state,work_notes",
        "sysparm_query": "assignment_group=dcebd8cc1b5320d06d418622dd4bcbfe^stateNOT IN3,4,6,7,8^ORDERBYDESCopened_at"
    }
    
    try:
        start_time = datetime.now()
        logging.info(f"Making ServiceNow API request to: {credentials.endpoint}")
        logging.info(f"Using assignment group: dcebd8cc1b5320d06d418622dd4bcbfe")
        logging.info(f"Query parameters:\n{json.dumps(params, indent=2)}")
        
        response = requests.get(
            credentials.endpoint,
            auth=HTTPBasicAuth(credentials.user, credentials.password),
            headers=headers,
            params=params,
        )
        
        api_time = datetime.now() - start_time
        logging.info(f"ServiceNow API response time: {api_time.total_seconds():.2f}s")
        #logging.info(f"Response status code: {response.status_code}")
        #logging.info(f"Response headers:\n{json.dumps(dict(response.headers), indent=2)}")
        
        if response.status_code == 200:
            result = response.json()["result"]
            logging.info(f"Retrieved {len(result)} incidents from ServiceNow")
            
            incidents = []
            for incident in result:
                # Debug raw incident data
                #logging.info(f"\nRaw incident data: {json.dumps(incident, indent=2)}")
                
                incident_number = incident.get("number")
                state = incident.get("state", "")
                status_text = STATE_MAPPING.get(state, "Unknown")
                
                logging.info(f"\nProcessing Incident: {incident_number}")
                logging.info(f"Raw number field: {incident.get('number', 'NOT FOUND')}")
                logging.info(f"Status: {status_text} (state: {state})")
                logging.info(f"Short Description: {incident.get('short_description', '')}")
                logging.info(f"Config Item: {incident.get('cmdb_ci', {}).get('display_value', '')}")
                logging.info(f"Work Notes Length: {len(incident.get('work_notes', ''))}")
                
                incidents.append({
                    "number": incident_number,
                    "description": incident.get("description", ""),
                    "short_description": incident.get("short_description", ""),
                    "config_item": incident.get("cmdb_ci", {}).get("display_value", ""),
                    "status": state,  # Store text state value
                    "work_notes": incident.get("work_notes", ""),
                    "snurl": f"{credentials.servicenow_instance}/nav_to.do?uri=incident.do?sys_id={incident.get('sys_id')}"
                })
            
            total_time = datetime.now() - start_time
            logging.info(f"\nTotal processing time: {total_time.total_seconds():.2f}s")
            logging.info("Incident pull completed successfully")
            return incidents
            
        else:
            logging.error(f"ServiceNow API Error:")
            logging.error(f"Status Code: {response.status_code}")
            logging.error(f"Response Headers: {json.dumps(dict(response.headers), indent=2)}")
            logging.error(f"Response Body: {response.text}")
            
    except Exception as e:
        logging.error("ServiceNow API Error:", exc_info=True)
        logging.error(f"Error Details: {str(e)}")
    
    return []
