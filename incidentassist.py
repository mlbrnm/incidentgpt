import requests
from requests.auth import HTTPBasicAuth
import json
import credentials
from datetime import datetime
import difflib
import ollama
import logging
import re

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
        # Make the request to ServiceNow API
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

def get_rag_context(description, ci, logging):
    """Get relevant context from RAG database"""
    # PrivateGPT API endpoint
    url = "https://wsmwsllm01.healthy.bewell.ca:8001/v1/chunks"
    headers = {"Content-Type": "application/json"}
    # https://docs.privategpt.dev/api-reference/api-reference/context-chunks/chunks-retrieval
    data = {
        "text": description,
        "limit": 5,
        "prev_next_chunks": 20
    }
    
    try:
        # Make the request to the RAG API
        logging.info(f"Fetching RAG context for description: {description[:100]}...")
        response = requests.post(url, json=data, headers=headers, verify=False)
        logging.info(f"RAG API response status: {response.status_code}")
        
        if response.status_code == 200:
            returndata = response.json()
            logging.info(f"RAG API returned {len(returndata.get('data', []))} chunks")
            
            finaltext = ""
            # The response contains a list of chunks which we must combine into a single text block
            for idx, item in enumerate(returndata.get("data", [])):
                text = item.get("text", "")
                previous_texts = item.get("previous_texts", [])
                next_texts = item.get("next_texts", [])
                
                # Combine text blocks
                previous_texts = previous_texts or []  # Convert None to empty list
                next_texts = next_texts or []  # Convert None to empty list
                combined = "".join(previous_texts[::-1]) + text + "".join(next_texts) # The previous texts are in reverse order
                # After combining, we need to find the most relevant section since we'll have the beginning/end of other irrelevant incidents in there too. Just a limitation of how the RAG works.
                relevant = find_most_similar_section(combined, text)
                # Filter out irrelevant sections based on CI
                if ci in relevant or ci == "Unknown CI":
                    finaltext += f"---- {idx+1} ----\n{relevant}\n\n"
            
            if not finaltext:
                logging.warning("RAG context processing resulted in empty text")
                return "No relevant previous incidents found."
                
            logging.info(f"Generated RAG context length: {len(finaltext)} characters")
            finaltext = replace_inc_with_url(finaltext, logging)  # Replace INCxxxxxxxxx numbers with actual ServiceNow URLs
            return finaltext.replace("\n", "<br>") # Replace \n with <br> for HTML display
        else: 
            logging.error(f"RAG API error: {response.status_code} - {response.text}")
            return "Error fetching relevant incidents."
            
    except Exception as e:
        logging.error(f"RAG Error: {str(e)}", exc_info=True)
        return "Error processing relevant incidents."

def find_most_similar_section(big_string, substring, separator="--------------------------------------------------------------"):
    """Find most relevant section in combined text blocks.
    After doing the RAG API call, we need to find the most relevant section since we'll have the beginning/end of other irrelevant incidents in there too."""
    sections = big_string.split(separator) # In the dataset, the sections are separated by a long line of dashes
    max_similarity = 0
    best_section = None
    
    normalized_substring = ' '.join(substring.lower().split())
    # Normalize the sections by removing extra spaces and converting to lowercase
    for section in sections:
        normalized_section = ' '.join(section.lower().split())
        # Calculate similarity using difflib. Kind of a weird solution but it works and the CPU time is negligible compared to the LLM call.
        similarity = difflib.SequenceMatcher(None, normalized_substring, normalized_section).ratio()
        
        if similarity > max_similarity:
            max_similarity = similarity
            best_section = section.strip()
    
    return best_section if best_section else "Not found."

def generate_solution(incident_number, ci, description, work_notes, rag_context):
    """Generate new solution using LLM (ollama)"""
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
                if not incident_number:
                    logging.error("Skipping incident with missing number")
                    continue
                    
                state = incident.get("state", "")
                status_text = STATE_MAPPING.get(state, "Unknown")
                
                logging.info(f"\nProcessing Incident: {incident_number}")
                logging.info(f"Raw number field: {incident.get('number', 'NOT FOUND')}")
                logging.info(f"Status: {status_text} (state: {state})")
                logging.info(f"Short Description: {incident.get('short_description', '')}")
                logging.info(f"Config Item: {incident.get('cmdb_ci', {}).get('display_value', '')}")
                logging.info(f"Work Notes Length: {len(incident.get('work_notes', ''))}")
                
                # Turn \n into <br> for HTML display
                worknotesbr = incident.get("work_notes", "").replace("\n", "<br>")
                worknotesbrlinks = replace_inc_with_url(worknotesbr, logging)

                # Create incident dictionary with validated number
                incident_data = {
                    "number": incident_number,  # Validated number
                    "description": incident.get("description", ""),
                    "short_description": incident.get("short_description", ""),
                    "config_item": incident.get("cmdb_ci", {}).get("display_value", ""),
                    "status": state,  # Store text state value
                    "work_notes": worknotesbrlinks,
                    "snurl": f"{credentials.servicenow_instance}/nav_to.do?uri=incident.do?sys_id={incident.get('sys_id')}"
                }
                
                # Log the complete incident data for debugging
                logging.debug(f"Adding incident with data: {json.dumps(incident_data, indent=2)}")
                incidents.append(incident_data)
            
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

def get_id_from_inc(subject, logging):
    """ServiceNow doesn't use the INC____ in the URLs, there's a different unique identifier. This pulls that 'sys_id' for the incident."""
    headers = {"Content-Type": "application/json", "Accept": "application/json"}
    params = {
        "sysparm_limit": 10,
        "sysparm_display_value": True,
        "sysparm_fields": "sys_id, number",
        "number": f"{subject}",
        "sysparm_query": "ORDERBYDESCopened_at",
    }
    print(f"Pulling in sys_id for {subject}...")
    logging.info(f"Pulling in sys_id for {subject}...")
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
        logging.error(f"Error: {response.status_code} - {response.text}")


def replace_inc_with_url(text, logging):
    """Finds all INC numbers in the text, looks up their sys_id, and replaces them with an HTML formatted URL."""
    
    # Find all INC numbers using regex
    inc_numbers = re.findall(r'INC\d+', text)
    
    # Replace each INC number with the corresponding URL
    for inc in inc_numbers:
        sys_id = get_id_from_inc(inc, logging)
        if sys_id != "na":
            # Format the replacement URL
            url = f'<a href="https://albertahealthservices.service-now.com/nav_to.do?uri=incident.do?sys_id={sys_id}">{inc}</a>'
            # Replace the INC number with the formatted URL in the text
            text = text.replace(inc, url)
    
    return text