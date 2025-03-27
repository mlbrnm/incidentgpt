from flask import Flask, render_template, jsonify
from flask_socketio import SocketIO
import sqlite3
import requests
import difflib
import ollama
import threading
import time
import urllib3
import logging
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor
from newIncidentGPT import (
    get_work_notes, 
    pull_servicenow_incidents, 
    STATE_MAPPING,
    get_rag_context,
    generate_solution
)

# Disable SSL warnings
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

def setup_logging():
    """Set up logging configuration for both parent and child processes"""
    from logging.handlers import RotatingFileHandler
    import os
    
    # Clear any existing handlers
    root_logger = logging.getLogger()
    root_logger.handlers = []
    
    formatter = logging.Formatter(
        '[%(asctime)s] [%(levelname)s] [PID:%(process)d] %(name)s - %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )
    
    # File handler with rotating files
    file_handler = RotatingFileHandler(
        'app.log',
        mode='a',  # Append mode instead of overwrite
        maxBytes=1024*1024,  # 1MB
        backupCount=3
    )
    file_handler.setFormatter(formatter)
    file_handler.setLevel(logging.INFO)
    
    # Console handler
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    console_handler.setLevel(logging.INFO)
    
    # Root logger configuration
    root_logger.setLevel(logging.INFO)
    root_logger.addHandler(file_handler)
    root_logger.addHandler(console_handler)

# Set up initial logger
setup_logging()
logger = logging.getLogger('incidentgpt')

# Initialize Flask app and SocketIO
app = Flask(__name__)
socketio = SocketIO(app, cors_allowed_origins="*")

# Solution generation queue and lock
solution_queue = []
queue_lock = threading.Lock()
is_processing = False

def process_solution_queue():
    """Process solutions in the queue one at a time"""
    global is_processing
    
    while True:
        try:
            with queue_lock:
                if not solution_queue:
                    is_processing = False
                    return
                
                # Get next incident from queue
                incident = solution_queue.pop(0)
                
            # Generate solution outside of lock
            try:
                generate_and_store_solution(incident)
                # Add cooldown between generations
                time.sleep(5)  # 5 second cooldown
            except Exception as e:
                logger.error(f"Error processing solution for incident {incident['number']}: {str(e)}")
                
        except Exception as e:
            logger.error(f"Error in solution processor: {str(e)}")
            time.sleep(5)  # Wait before retrying

def queue_solution_generation(incident):
    """Add incident to solution generation queue"""
    global is_processing
    
    with queue_lock:
        # Check if incident is already in queue
        if not any(i['number'] == incident['number'] for i in solution_queue):
            solution_queue.append(incident)
            logger.info(f"Queued solution generation for incident {incident['number']}")
            
            # Start processor if not running
            if not is_processing:
                is_processing = True
                thread = threading.Thread(target=process_solution_queue)
                thread.daemon = True
                thread.start()

def generate_and_store_solution(incident):
    """Generate and store solution for an incident"""
    try:
        # Get RAG context
        rag_context = get_rag_context(incident['description'])
        
        # Generate solution
        solution = generate_solution(
            incident_number=incident['number'],
            ci=incident['config_item'],
            description=incident['description'],
            work_notes=incident['work_notes'],
            rag_context=rag_context
        )
        
        # Store solution
        conn = get_db()
        try:
            c = conn.cursor()
            c.execute('''
                INSERT INTO solutions 
                (incident_number, solution, generated_at, work_notes_snapshot, rag_context)
                VALUES (?, ?, ?, ?, ?)
            ''', (
                incident['number'],
                solution,
                datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                incident['work_notes'],
                rag_context
            ))
            conn.commit()
            
            # Notify clients
            socketio.emit('solution_updated', {'incident_number': incident['number']})
            logger.info(f"Generated and stored solution for incident {incident['number']}")
        finally:
            conn.close()
            
    except Exception as e:
        logger.error(f"Error generating solution for incident {incident['number']}: {str(e)}")

def check_for_updates():
    """Background task to check for new incidents and generate solutions"""
    while True:
        try:
            # Get current incidents from ServiceNow
            incidents = pull_servicenow_incidents(logger)
            if incidents:
                # Store them in the database
                store_incidents(incidents)
                
                # Queue solutions for incidents that don't have one
                conn = get_db()
                try:
                    c = conn.cursor()
                    for incident in incidents:
                        # Check if incident already has a solution
                        c.execute('''
                            SELECT COUNT(*) FROM solutions 
                            WHERE incident_number = ?
                        ''', (incident['number'],))
                        if c.fetchone()[0] == 0:
                            # No solution exists, queue one
                            queue_solution_generation(incident)
                finally:
                    conn.close()
                
                # Notify clients
                socketio.emit('incidents_updated', {'updated': [i['number'] for i in incidents]})
            
            # Sleep for 5 minutes
            time.sleep(300)
        except Exception as e:
            logger.error(f"Error in update checker: {str(e)}")
            time.sleep(60)  # Sleep for 1 minute on error before retrying

def get_db():
    """Get database connection with timeout"""
    logger.debug("Getting database connection")
    return sqlite3.connect('incidents.db', timeout=30)

def init_db():
    """Initialize SQLite database with new schema"""
    logger.info("Initializing database")
    try:
        conn = get_db()
        c = conn.cursor()
        
        # Drop existing tables if they exist
        c.execute('DROP TABLE IF EXISTS incidents')
        c.execute('DROP TABLE IF EXISTS solutions')
        
        # Create new incidents table
        c.execute('''CREATE TABLE incidents (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            incident_number TEXT UNIQUE,
            description TEXT,
            short_description TEXT,
            config_item TEXT,
            status TEXT,
            work_notes TEXT,
            last_updated TEXT,
            snurl TEXT,
            archived BOOLEAN DEFAULT 0,
            resolved_at TEXT
        )''')
        
        # Create new solutions table
        c.execute('''CREATE TABLE solutions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            incident_number TEXT,
            solution TEXT,
            generated_at TEXT,
            work_notes_snapshot TEXT,
            rag_context TEXT,
            FOREIGN KEY (incident_number) REFERENCES incidents(incident_number)
        )''')
        
        conn.commit()
        logger.info("Database initialized successfully")
    except sqlite3.Error as e:
        logger.error(f"Database initialization error: {str(e)}")
        raise
    finally:
        if 'conn' in locals():
            conn.close()
            logger.debug("Database connection closed")

def has_incident_changed(stored, new):
    """Check if incident details have changed"""
    return (
        stored['work_notes'] != new['work_notes'] or
        stored['description'] != new['description'] or
        stored['config_item'] != new['config_item']
    )

def get_stored_incident(incident_number):
    """Get a single incident from the database"""
    conn = get_db()
    try:
        c = conn.cursor()
        c.execute('''
            SELECT * FROM incidents 
            WHERE incident_number = ?
        ''', (incident_number,))
        columns = [description[0] for description in c.description]
        row = c.fetchone()
        if row:
            return dict(zip(columns, row))
        return None
    except sqlite3.Error as e:
        logger.error(f"Database error while retrieving incident: {str(e)}")
        return None
    finally:
        conn.close()

def store_incidents(incidents):
    """Store incidents in the database and handle changes"""
    conn = get_db()
    try:
        c = conn.cursor()
        
        # Get current incident numbers from ServiceNow
        current_incident_numbers = set(incident['number'] for incident in incidents)
        
        # Get all non-archived incidents from database
        c.execute('SELECT incident_number FROM incidents WHERE archived = 0')
        stored_incident_numbers = set(row[0] for row in c.fetchall())
        
        # Find incidents that are no longer in ServiceNow
        resolved_incidents = stored_incident_numbers - current_incident_numbers
        if resolved_incidents:
            # Mark these incidents as archived
            now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            for incident_number in resolved_incidents:
                logger.info(f"Archiving resolved incident {incident_number}")
                c.execute('''
                    UPDATE incidents 
                    SET archived = 1, resolved_at = ? 
                    WHERE incident_number = ?
                ''', (now, incident_number))
        
        # Process current incidents
        for incident in incidents:
            # Check if incident exists and has changed
            stored_incident = get_stored_incident(incident['number'])
            needs_new_solution = False
            
            if stored_incident:
                # Check if important fields have changed
                if has_incident_changed(stored_incident, incident):
                    logger.info(f"Changes detected in incident {incident['number']}, will generate new solution")
                    needs_new_solution = True
            
            # Insert or update incident
            c.execute('''
                INSERT OR REPLACE INTO incidents 
                (incident_number, description, short_description, config_item, 
                status, work_notes, last_updated, snurl, archived, resolved_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0, NULL)
            ''', (
                incident['number'],
                incident['description'],
                incident['short_description'],
                incident['config_item'],
                incident['status'],
                incident['work_notes'],
                datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                incident['snurl']
            ))
            
            # Queue solution generation if needed
            if needs_new_solution:
                queue_solution_generation(incident)
                
        conn.commit()
    except sqlite3.Error as e:
        logger.error(f"Database error while storing incidents: {str(e)}")
        raise
    finally:
        conn.close()

def get_stored_incidents(archived=False):
    """Retrieve incidents from the database"""
    conn = get_db()
    try:
        c = conn.cursor()
        c.execute('''
            SELECT i.*, s.solution, s.generated_at
            FROM incidents i
            LEFT JOIN (
                SELECT incident_number, solution, generated_at,
                       ROW_NUMBER() OVER (PARTITION BY incident_number ORDER BY generated_at DESC) as rn
                FROM solutions
            ) s ON i.incident_number = s.incident_number AND s.rn = 1
            WHERE i.archived = ?
            ORDER BY i.last_updated DESC
        ''', (1 if archived else 0,))
        columns = [description[0] for description in c.description]
        incidents = []
        for row in c.fetchall():
            incident = dict(zip(columns, row))
            incidents.append(incident)
        return incidents
    except sqlite3.Error as e:
        logger.error(f"Database error while retrieving incidents: {str(e)}")
        return []
    finally:
        conn.close()

@app.route('/')
def index():
    """Render main page with active and archived incidents"""
    try:
        # Get incidents from ServiceNow
        incidents = pull_servicenow_incidents(logger)
        if incidents:
            # Store them in the database
            store_incidents(incidents)
        
        # Get active and archived incidents from database
        active_incidents = get_stored_incidents(archived=False)
        archived_incidents = get_stored_incidents(archived=True)
        
        return render_template('index.html', 
                             active_incidents=active_incidents,
                             archived_incidents=archived_incidents)
    except Exception as e:
        logger.error(f"Error in index route: {str(e)}")
        return render_template('index.html', 
                             active_incidents=[],
                             archived_incidents=[])

def get_solution_history(incident_number):
    """Get solution history for an incident"""
    conn = get_db()
    try:
        c = conn.cursor()
        c.execute('''
            SELECT solution, generated_at, work_notes_snapshot
            FROM solutions
            WHERE incident_number = ?
            ORDER BY generated_at DESC
        ''', (incident_number,))
        return c.fetchall()
    except sqlite3.Error as e:
        logger.error(f"Database error while getting solution history: {str(e)}")
        return []
    finally:
        conn.close()

@app.route('/solution-history/<incident_number>')
def solution_history(incident_number):
    """Get solution history for an incident"""
    solutions = get_solution_history(incident_number)
    return jsonify(solutions)

if __name__ == '__main__':
    try:
        logger.info("Starting application")
        init_db()
        
        # Start background task
        thread = threading.Thread(target=check_for_updates)
        thread.daemon = True
        thread.start()
        
        # Start web server with SocketIO
        logger.info("Starting web server on http://127.0.0.1:5001")
        socketio.run(app, host='127.0.0.1', port=5001, debug=False, allow_unsafe_werkzeug=True)
    except Exception as e:
        logger.error(f"Startup error: {str(e)}", exc_info=True)
