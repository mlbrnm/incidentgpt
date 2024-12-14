import csv
import re
import os
from datetime import datetime

class Record:
    def __init__(self, row_dict):
        """
        Initializes a Record object using a dictionary that contains the data for a single row.

        Args:
        row_dict (dict): Dictionary where keys are column headers and values are the data of the row.
        """
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
        self.work_notes_compact_cleaned = re.sub(r"Escalate in \d+ minutes to [^\n]+\n", "", self.work_notes_compact, flags=re.DOTALL)
        self.work_notes_compact_cleaned_further = self.work_notes_compact_cleaned.replace(" (Work notes)", "").replace("This incident escalation is in progress using the following escalation plan:", "Escalation in progress.")
        self.configuration_item = row_dict['cmdb_ci']

    @staticmethod
    def parse_date(date_str):
        """
        Converts a date string in the format 'YYYY-MM-DD HH:MM:SS' to a datetime object.
        Returns None if the date string is empty or invalid.

        Args:
        date_str (str): The date string to parse.

        Returns:
        datetime.datetime or None: The datetime object or None if conversion is not possible.
        """
        try:
            return datetime.strptime(date_str, "%Y-%m-%d %H:%M:%S")
        except ValueError:
            return None

    def __str__(self):
        """ Provides a string representation of the Record object. """
        return f"{self.number} - {self.short_description}"

    def print_record(self, print_work_notes = True):
        """ Turns the record into a formatted block of text for saving to a file. """
        if print_work_notes and self.work_notes:
            return f"""{self.number} | {self.opened_at.strftime('%B %d, %Y')}
Submitted by: {self.caller_id} | Resolved by: {self.resolved_by}
---- Problem:\n{self.configuration_item}\n{self.combined_desc}
---- Solution:\n{self.close_notes_compact}
---- Work Notes:\n{self.work_notes_compact_cleaned_further}
\n\n\n--------------------------------------------------------------\n\n\n\n"""
        else:
            return f"""{self.number} | {self.opened_at.strftime('%B %d, %Y')}
Submitted by: {self.caller_id} | Resolved by: {self.resolved_by}
---- Problem:\n{self.combined_desc}
---- Solution:\n{self.close_notes_compact}
\n\n\n--------------------------------------------------------------\n\n\n\n"""

def load_csv_to_dict(filename='incidents.csv'):
    """
    Load a CSV file and convert it into a list of Record objects.

    Each row in the CSV becomes an instance of the Record class, with the header row providing the keys.

    Args:
    filename (str): The path to the CSV file to load. Defaults to 'incidents.csv'.

    Returns:
    list[Record]: A list of Record objects representing each row in the CSV.
    """
    encodings = ['utf-8', 'iso-8859-1', 'windows-1252']  # Common encodings to try
    for encoding in encodings:
        try:
            with open(filename, mode='r', encoding=encoding) as file:
                csv_reader = csv.DictReader(file)
                records = [Record(row) for row in csv_reader]
                print(f"\nFile read successfully with {encoding} encoding.\n")
                return records
        except UnicodeDecodeError:
            pass  # Continue trying other encodings
        except FileNotFoundError:
            print(f"The file '{filename}' does not exist.")
            return []
        except Exception as e:
            print(f"An error occurred: {e}")
            return []
    print("Failed to read the file with any of the tried encodings.")
    return []

def save_to_text(records):
    mostrecentdate = get_most_recent_record(records).resolved_at.strftime('%Y-%m-%d_%H-%M-%S') or 'unknown'
    filename = mostrecentdate + '.txt'
    try:
        with open(filename, 'w', encoding='iso-8859-1') as file:
            for record in records:
                file.write(record.print_record(True))
        print(f"\nSuccessfully saved to {filename}\n")
    except Exception as e:
        print(f"An error occurred: {e}")

def get_most_recent_record(records):
    most_recent_record = max(
        (record for record in records if record.resolved_at), 
        key=lambda record: record.resolved_at,
        default=None
    )
    return most_recent_record

if __name__ == "__main__":
    print("\n---------------------------------------------\nWelcome to the ServiceNow -> RAG Ready Script")
    filename = input("Enter the filename (default: incident.csv): ")
    filename = filename.strip() or "incident.csv"
    records = load_csv_to_dict(filename)

    if records:
        count = 0
        for record in records:
            print(f"{record.number} {record.resolved_at.strftime('%Y-%m-%d')} - {count+1}")
            count += 1
        print(f"Total: {count} records")
        save_to_text(records)
        
    else:
        print("No records found or file could not be read.")
    
    
