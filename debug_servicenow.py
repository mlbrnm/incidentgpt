#!/usr/bin/env python3
import requests
import json
import sqlite3
from datetime import datetime
import credentials
from requests.auth import HTTPBasicAuth
import logging
import ollama
import difflib

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

# Set up logging
logging.basicConfig(
    level=logging.INFO,
    format='[%(asctime)s] [%(levelname)s] %(message)s',
    handlers=[
        logging.FileHandler('debug.log'),
        logging.StreamHandler()
    ]
)

def get_rag_context(description):
    """
    Retrieve relevant context from RAG database.
    Returns the processed text (with HTML <br> for newlines)
    or an error message.
    """
    url = "https://wsmwsllm01.healthy.bewell.ca:8001/v1/chunks"
    headers = {"Content-Type": "application/json"}
    data = {
        "text": description,
        "limit": 5,
        "prev_next_chunks": 1
    }
    
    logging.info("Entered the get_rag_context function")
    try:
        response = requests.post(url, json=data, headers=headers, verify=False)
        logging.info(f"RAG Response status code: {response.status_code}")

        # Check if the endpoint responded with success
        if response.status_code != 200:
            logging.error(f"RAG returned an error. Status: {response.status_code}, Body: {response.text}")
            return f"Error from chunk service (status {response.status_code}): {response.text}"

        # Attempt to parse JSON. If it's malformed, return an error message.
        try:
            returndata = response.json()
        except ValueError as json_err:
            logging.error(f"Invalid JSON response from RAG: {json_err}")
            return "Invalid JSON response from chunk service."

        logging.info(f"RAG Data: {returndata}")

        # Ensure the expected structure is present
        if "data" not in returndata:
            logging.error(f"No 'data' key in RAG response: {returndata}")
            return "The chunk service response did not contain expected data."

        # Process each item in the response
        finaltext = ""
        for idx, item in enumerate(returndata["data"]):
            text = item.get("text", "")
            previous_texts = item.get("previous_texts", [])
            next_texts = item.get("next_texts", [])

            # Combine text blocks
            # If you intentionally want to reverse previous_texts, keep [::-1].
            # Otherwise, remove it.
            combined = "".join(previous_texts[::-1]) + text + "".join(next_texts)

            relevant = find_most_similar_section(combined, text)
            finaltext += f"---- {idx+1} ----\n{relevant}\n\n"

        # Convert newlines to <br> for better display
        return finaltext.replace("\n", "<br>")

    except Exception as e:
        # Catch anything unexpected (network errors, KeyError, etc.)
        logging.error(f"Unexpected error in get_rag_context: {str(e)}")
        return f"An error occurred while contacting the chunk service: {str(e)}"

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

def generate_solution(incident_number, description, work_notes, rag_context):
    """Generate new solution using LLM"""
    logging.info(f"Generating solution for incident {incident_number}")
    conn = None
    try:
        prompt = f"""You are an AI working for a healthcare IT team called the Middleware Services Team (MWS).
The following are solved/closed tickets that contain possible solutions to this problem.

Context from similar incidents:
{rag_context}

Current Incident:
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
        
        solution = response.get('response', 'Failed to generate solution')       
             
        return solution
    except Exception as e:
        print(f"Solution Generation Error: {str(e)}")
        return "Failed to generate solution", 0.0


def test_connection():
    """Test basic connectivity to ServiceNow"""
    logging.info("Testing ServiceNow connection...")
    
    try:
        # Test basic endpoint access
        response = requests.get(
            credentials.endpoint,
            auth=HTTPBasicAuth(credentials.user, credentials.password),
            headers={"Accept": "application/json"},
            params={"sysparm_limit": 1}
        )
        
        logging.info(f"ServiceNow Instance: {credentials.servicenow_instance}")
        #logging.info(f"Response Status: {response.status_code}")
        #logging.info(f"Response Headers: {json.dumps(dict(response.headers), indent=2)}")
        
        if response.status_code == 200:
            logging.info("Connection successful")
            return True
        else:
            logging.error(f"Connection failed: {response.text}")
            return False
            
    except Exception as e:
        logging.error(f"✗ Connection error: {str(e)}")
        return False

def test_incident_query():
    """Test incident query and analyze results"""
    logging.info("Testing incident query...")
    
    headers = {"Content-Type": "application/json", "Accept": "application/json"}
    params = {
        "sysparm_limit": 5000,  # Increased limit for testing
        "sysparm_display_value": True,  # Get readable values
        "sysparm_fields": "sys_id,number,assignment_group,description,opened_at,short_description,state,work_notes",
        "sysparm_query": "assignment_group=dcebd8cc1b5320d06d418622dd4bcbfe^stateNOT IN3,4,6,7,8^ORDERBYDESCopened_at"
    }
    
    try:
        response = requests.get(
            credentials.endpoint,
            auth=HTTPBasicAuth(credentials.user, credentials.password),
            headers=headers,
            params=params,
        )
        
        if response.status_code == 200:
            result = response.json()
            #logging.info("Raw Response:")
            #logging.info(json.dumps(result, indent=2))
            
            incidents = result["result"]
            logging.info(f"Found {len(incidents)} incidents")
            
            # Analyze state distribution
            states = {}
            for incident in incidents:
                state = incident.get("state")
                states[state] = states.get(state, 0) + 1
            
            logging.info("State Distribution:")
            for state, count in states.items():
                logging.info(f"  {state}: {count}")
            
            # Show sample incident details
            if incidents:
                sample = incidents[0]
                logging.info("\nSample Incident:")
                logging.info(f"  Number: {sample.get('number')}")
                logging.info(f"  State: {sample.get('state')}")
                logging.info(f"  Assignment Group: {sample.get('assignment_group').get('display_value')}")
                logging.info(f"  Has Work Notes: {'work_notes' in sample and bool(sample['work_notes'])}")
                logging.info(f"  Work Notes: {sample.get('work_notes')}")
            
            return incidents
        else:
            logging.error(f"Query failed: {response.text}")
            return None
            
    except Exception as e:
        logging.error(f"Query error: {str(e)}")
        return None

def test_database():
    """Test database operations"""
    logging.info("\nTesting database operations...")
    
    try:
        # Test database connection
        conn = sqlite3.connect('incidents.db')
        c = conn.cursor()
        
        # Check tables
        c.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = c.fetchall()
        logging.info("Database tables:")
        for table in tables:
            logging.info(f"  {table[0]}")
            c.execute(f"PRAGMA table_info({table[0]})")
            columns = c.fetchall()
            for col in columns:
                logging.info(f"    {col[1]} ({col[2]})")
        
        # Check incident count
        c.execute("SELECT COUNT(*) FROM incidents")
        incident_count = c.fetchone()[0]
        logging.info(f"\nStored incidents: {incident_count}")
        
        # Check solution count
        c.execute("SELECT COUNT(*) FROM solutions")
        solution_count = c.fetchone()[0]
        logging.info(f"Stored solutions: {solution_count}")
        
        # Sample data
        if incident_count > 0:
            c.execute('''
                SELECT i.incident_number, i.status, 
                       COUNT(s.id) as solution_count
                FROM incidents i
                LEFT JOIN solutions s ON i.incident_number = s.incident_number
                GROUP BY i.incident_number
                LIMIT 5
            ''')
            samples = c.fetchall()
            logging.info("\nSample Data:")
            for sample in samples:
                logging.info(f"  Incident {sample[0]}: {sample[1]} status, {sample[2]} solutions")
        
        conn.close()
        return True
        
    except Exception as e:
        logging.error(f"Database error: {str(e)}")
        return False

def main():
    """Run all tests"""
    logging.info("Starting ServiceNow Debug Tests")
    logging.info("=" * 50)
    
    # Test connection
    if not test_connection():
        logging.error("Stopping tests due to connection failure")
        return
    
    # Test incident query
    incidents = test_incident_query()
    if not incidents:
        logging.error("Stopping tests due to query failure")
        return
    
    # Test RAG retrieval
    description = incidents[0].get("description", "")
    rag_context = get_rag_context(description)
    logging.info(f"RAG Context:\n{rag_context}")

    # Test solution generation
    work_notes = incidents[0].get("work_notes", "")
    #solution = generate_solution(incidents[0].get("number"), description, work_notes, rag_context)
    #logging.info(f"Generated Solution:\n{solution}")

    # Test database
    #test_database()
    
    logging.info("\nDebug tests completed")

if __name__ == "__main__":
    main()
