import csv
import re
import os
import sys
import json
import glob
import logging
import schedule
import time
import requests
from datetime import datetime
from unidecode import unidecode
from requests.auth import HTTPBasicAuth

# Add the pgpt directory to sys.path for credentials
script_dir = os.path.dirname(os.path.abspath(__file__))
pgpt_dir = os.path.abspath(os.path.join(script_dir, '..'))
if pgpt_dir not in sys.path:
    sys.path.insert(0, pgpt_dir)

from incidentgpt import credentials

class Record:
    """Represents a ServiceNow incident record with formatting capabilities."""
    
    def __init__(self, row_dict):
        self.number = row_dict['number']
        self.opened_at = self.parse_date(row_dict['opened_at'])
        self.description = row_dict['description'].strip()
        self.description_compact = re.sub(r'\n+', '\n', self.description)
        self.short_description = row_dict['short_description'].strip()
        self.short_description_compact = re.sub(r'\n+', '\n', self.short_description)
        self.combined_desc = f"{self.short_description_compact}\n{self.description_compact}"
        self.caller_id = row_dict['caller_id']
        self.category = row_dict['category']
        self.assignment_group = row_dict['assignment_group']
        self.assigned_to = row_dict['assigned_to']
        self.work_notes = row_dict['work_notes'].strip()
        self.work_notes_compact = re.sub(r'\n+', '\n', self.work_notes)
        self.resolved_at = self.parse_date(row_dict['resolved_at'])
        self.resolved_by = row_dict['resolved_by']
        self.close_notes = row_dict['close_notes'].strip()
        self.close_notes_compact = re.sub(r'\n+', '\n', self.close_notes)
        self.work_notes_compact_cleaned = re.sub(
            r"Escalate in \d+ minutes to [^\n]+\n", 
            "", 
            self.work_notes_compact, 
            flags=re.DOTALL
        )
        self.work_notes_compact_cleaned_further = self.work_notes_compact_cleaned.replace(
            " (Work notes)", ""
        ).replace(
            "This incident escalation is in progress using the following escalation plan:",
            "Escalation in progress."
        )
        self.configuration_item = row_dict['cmdb_ci']

    @staticmethod
    def parse_date(date_str):
        try:
            return datetime.strptime(date_str, "%Y-%m-%d %H:%M:%S")
        except ValueError:
            return None

    def print_record(self):
        if self.work_notes:
            return unidecode(f"""{self.number} | {self.opened_at.strftime('%B %d, %Y')}
Submitted by: {self.caller_id} | Resolved by: {self.resolved_by}
---- Problem:\n{self.configuration_item}\n{self.combined_desc}
---- Solution:\n{self.close_notes_compact}
---- Work Notes:\n{self.work_notes_compact_cleaned_further}
\n\n\n--------------------------------------------------------------\n\n\n\n""")
        else:
            return unidecode(f"""{self.number} | {self.opened_at.strftime('%B %d, %Y')}
Submitted by: {self.caller_id} | Resolved by: {self.resolved_by}
---- Problem:\n{self.combined_desc}
---- Solution:\n{self.close_notes_compact}
\n\n\n--------------------------------------------------------------\n\n\n\n""")

class ServiceNowClient:
    """Handles interactions with ServiceNow API."""
    
    def __init__(self):
        self.endpoint = credentials.endpoint
        self.auth = HTTPBasicAuth(credentials.user, credentials.password)
        self.headers = {"Content-Type": "application/json", "Accept": "application/json"}
        self.params = {
            "sysparm_limit": 10000,
            "sysparm_display_value": True,
            "sysparm_fields": (
                "number,opened_at,description,short_description,caller_id,category,"
                "assignment_group,assigned_to,work_notes,resolved_at,resolved_by,"
                "close_notes,cmdb_ci,comments_and_work_notes"
            ),
            "assignment_group": "Medical Devices-Medical System Middleware",
            "sysparm_query": "state=6^ORstate=7^ORDERBYDESCresolved_at",
        }

    def get_incidents(self):
        """Retrieves incidents from ServiceNow."""
        logging.info("Retrieving incidents from ServiceNow...")
        
        try:
            response = requests.get(
                self.endpoint,
                auth=self.auth,
                headers=self.headers,
                params=self.params
            )
            response.raise_for_status()
            incidents = response.json().get('result', [])
            
            if not incidents:
                logging.warning("No incidents retrieved from ServiceNow")
                return None
                
            logging.info(f"Retrieved {len(incidents)} incidents from ServiceNow")
            return incidents
            
        except Exception as e:
            logging.error(f"Error retrieving incidents: {str(e)}")
            return None

class RAGFormatter:
    """Handles conversion of incidents to RAG format and file operations."""
    
    @staticmethod
    def save_to_csv(incidents):
        """Saves incidents to a CSV file."""
        if not incidents:
            return None
            
        most_recent_date = incidents[0].get("resolved_at", "").split(" ")[0]
        csv_file_name = f"incidents_{most_recent_date}.csv"
        headers = incidents[0].keys()
        
        try:
            with open(csv_file_name, mode="w", newline="", encoding="utf-8") as file:
                writer = csv.DictWriter(file, fieldnames=headers)
                writer.writeheader()
                
                for incident in incidents:
                    filtered_incident = {}
                    for key in headers:
                        value = incident.get(key, "")
                        if isinstance(value, dict) and "display_value" in value:
                            filtered_incident[key] = value["display_value"]
                        else:
                            filtered_incident[key] = value
                    writer.writerow(filtered_incident)
                    
            logging.info(f"Created CSV file: {csv_file_name}")
            return csv_file_name
            
        except Exception as e:
            logging.error(f"Error saving CSV file: {str(e)}")
            return None

    @staticmethod
    def convert_to_records(csv_file):
        """Converts CSV file to Record objects."""
        try:
            with open(csv_file, mode='r', encoding='utf-8') as file:
                csv_reader = csv.DictReader(file)
                records = [Record(row) for row in csv_reader]
                logging.info(f"Converted {len(records)} records from CSV")
                return records
        except Exception as e:
            logging.error(f"Error converting CSV to records: {str(e)}")
            return None

    @staticmethod
    def save_to_rag(records):
        """Saves records in RAG format."""
        if not records:
            return None
            
        most_recent = max(
            (r for r in records if r.resolved_at),
            key=lambda r: r.resolved_at
        )
        rag_filename = f"incidents_rag_{most_recent.resolved_at.strftime('%Y-%m-%d_%H-%M-%S')}.txt"
        
        try:
            with open(rag_filename, 'w', encoding='iso-8859-1') as file:
                for record in records:
                    file.write(record.print_record())
            logging.info(f"Created RAG file: {rag_filename}")
            return rag_filename
        except Exception as e:
            logging.error(f"Error saving RAG file: {str(e)}")
            return None

class IngestClient:
    """Handles interactions with the ingest API."""
    
    def __init__(self):
        self.base_url = "https://wsmwsllm01.healthy.bewell.ca:8001"
        
    def submit_file(self, file_path):
        """Submits a file to the ingest endpoint."""
        url = f"{self.base_url}/v1/ingest/file"
        
        try:
            with open(file_path, 'rb') as f:
                files = {'file': (os.path.basename(file_path), f)}
                response = requests.post(url, files=files, verify=False)
                response.raise_for_status()
                logging.info(f"Successfully submitted file: {file_path}")
                return True
        except Exception as e:
            logging.error(f"Error submitting file: {str(e)}")
            return False

    def get_doc_info(self):
        """Gets document IDs and filenames for RAG files, sorted by date."""
        url = f"{self.base_url}/v1/ingest/list"
        
        try:
            response = requests.get(url, verify=False)
            response.raise_for_status()
            data = response.json()
            
            # Get both ID and filename for matching documents
            docs = []
            for doc in data["data"]:
                if "incidents_rag" in doc["doc_metadata"]["file_name"].lower():
                    filename = doc["doc_metadata"]["file_name"]
                    # Extract date from filename (format: incidents_rag_YYYY-MM-DD_HH-MM-SS.txt)
                    try:
                        date_str = filename.split('incidents_rag_')[1].split('.')[0]
                        date = datetime.strptime(date_str, '%Y-%m-%d_%H-%M-%S')
                        docs.append({
                            "id": doc["doc_id"],
                            "filename": filename,
                            "date": date
                        })
                    except (IndexError, ValueError) as e:
                        logging.warning(f"Could not parse date from filename {filename}: {e}")
                        continue
            
            # Sort documents by date, newest first
            docs.sort(key=lambda x: x["date"], reverse=True)
            
            if docs:
                logging.info(f"Found {len(docs)} RAG documents (sorted by date, newest first):")
                for doc in docs:
                    logging.info(f"  - {doc['filename']} (ID: {doc['id']}, Date: {doc['date'].strftime('%Y-%m-%d %H:%M:%S')})")
            else:
                logging.info("No RAG documents found")
                
            return docs
        except Exception as e:
            logging.error(f"Error getting document IDs: {str(e)}")
            return []

    def delete_document(self, doc_info):
        """Deletes a document by ID."""
        url = f"{self.base_url}/v1/ingest/{doc_info['id']}"
        
        try:
            response = requests.delete(url, verify=False)
            response.raise_for_status()
            logging.info(f"Successfully deleted document: {doc_info['filename']} (ID: {doc_info['id']})")
            return True
        except Exception as e:
            logging.error(f"Error deleting document {doc_info['filename']} (ID: {doc_info['id']}): {str(e)}")
            return False

class IncidentProcessor:
    """Main class that orchestrates the entire process."""
    
    def __init__(self):
        self.setup_logging()
        self.servicenow_client = ServiceNowClient()
        self.rag_formatter = RAGFormatter()
        self.ingest_client = IngestClient()

    def setup_logging(self):
        """Sets up logging configuration."""
        log_filename = f"incident_processor_{datetime.now().strftime('%Y-%m-%d')}.log"
        logging.basicConfig(
            filename=log_filename,
            level=logging.INFO,
            format='%(asctime)s - %(levelname)s - %(message)s'
        )

    def process_incidents(self):
        """Main processing workflow."""
        logging.info("Starting incident processing workflow")
        
        try:
            # 1. Get incidents from ServiceNow
            incidents = self.servicenow_client.get_incidents()
            if not incidents:
                return
            
            # 2. Save to CSV
            csv_file = self.rag_formatter.save_to_csv(incidents)
            if not csv_file:
                return
            
            # 3. Convert to records
            records = self.rag_formatter.convert_to_records(csv_file)
            if not records:
                return
            
            # 4. Save to RAG format
            rag_file = self.rag_formatter.save_to_rag(records)
            if not rag_file:
                return
            
            # 5. Submit to ingest
            if self.ingest_client.submit_file(rag_file):
                # 6. Clean up old documents
                self.cleanup_old_documents()
                
        except Exception as e:
            logging.error(f"Error in process_incidents: {str(e)}")

    def cleanup_old_documents(self):
        """Cleans up old ingested documents."""
        logging.info("Starting cleanup of old documents")
        
        try:
            docs = self.ingest_client.get_doc_info()
            if not docs:
                return
                
            # Keep the most recent document (already sorted by date)
            latest_doc = docs[0]
            docs_to_delete = docs[1:]
            
            if latest_doc:
                logging.info(f"Keeping most recent document: {latest_doc['filename']} (ID: {latest_doc['id']}, Date: {latest_doc['date'].strftime('%Y-%m-%d %H:%M:%S')})")
            
            success_count = 0
            for doc in docs_to_delete:
                logging.info(f"Attempting to delete document: {doc['filename']} (ID: {doc['id']}, Date: {doc['date'].strftime('%Y-%m-%d %H:%M:%S')})")
                if self.ingest_client.delete_document(doc):
                    success_count += 1
                    
            logging.info(f"Cleanup complete. Deleted {success_count} out of {len(docs_to_delete)} documents")
            
        except Exception as e:
            logging.error(f"Error in cleanup_old_documents: {str(e)}")

def main():
    """Main entry point."""
    processor = IncidentProcessor()
    
    # Schedule to run every Friday at 8:00 PM
    schedule.every().friday.at("16:15").do(processor.process_incidents)
    
    logging.info("Incident processor started. Scheduled for every Friday at 4:15 PM")
    
    while True:
        schedule.run_pending()
        time.sleep(60)

def main_test():
    """Test entry point that runs the process immediately."""
    processor = IncidentProcessor()
    logging.info("Starting test run of incident processor")
    processor.process_incidents()
    logging.info("Test run completed")

if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--test":
        main_test()
    else:
        main()
