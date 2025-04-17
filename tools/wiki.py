import os
import sys
from gql import gql, Client
from gql.transport.requests import RequestsHTTPTransport
import requests
import logging
from datetime import datetime
import urllib3

# Disable InsecureRequestWarning
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# Add the pgpt directory to sys.path for credentials
script_dir = os.path.dirname(os.path.abspath(__file__))
pgpt_dir = os.path.abspath(os.path.join(script_dir, '..'))
if pgpt_dir not in sys.path:
    sys.path.insert(0, pgpt_dir)

from incidentgpt import credentials

apikey = credentials.WIKIAPITOKEN
apiurl = credentials.WIKIURL

class Page:
    def __init__(self, page_id, path, title):
        """
        Initializes a Page object with basic attributes.

        :param page_id: The ID of the page
        :param path: The path of the page
        :param title: The title of the page
        """
        self.id = page_id
        self.path = path
        self.title = title
        self.content = None
        self.created_at = None
        self.updated_at = None

    def fetch_page_details(self, client):
        """
        Fetches additional details (content, createdAt, updatedAt) from the API.

        :param client: GraphQL Client instance
        """
        query = gql("""
        {
          pages {
            single(id: %s) {
              content
              createdAt
              updatedAt
            }
          }
        }
        """ % self.id)

        response = client.execute(query)

        if response and "pages" in response and "single" in response["pages"]:
            details = response["pages"]["single"]
            self.content = details.get("content")
            self.created_at = details.get("createdAt")
            self.updated_at = details.get("updatedAt")

    def save_to_markdown(self, directory="pages"):
        """
        Saves the page content to a Markdown (.md) file.

        :param directory: The directory to save the file in (default is "pages")
        """
        if not self.content:
            print(f"Skipping {self.title}: No content available.")
            logging.info(f"Skipping {self.title}: No content available.")
            return

        # Ensure directory exists
        os.makedirs(directory, exist_ok=True)

        # Sanitize title for filename (remove special characters)
        safe_title = "".join(c if c.isalnum() or c in " _-" else "_" for c in self.title)

        # Parse the ISO format date and format for filename
        filenamedate = datetime.strptime(self.updated_at, '%Y-%m-%dT%H:%M:%S.%fZ')
        formatted_date = filenamedate.strftime('%Y-%m-%d_%H-%M-%S')
        filename = f"{self.id} - {safe_title} - {formatted_date}.md"

        # Full file path
        filepath = os.path.join(directory, filename)

        # Markdown content
        md_content = self.content

        # Write to file
        with open(filepath, "w", encoding="utf-8") as file:
            file.write(md_content)

        print(f"Saved: {filepath}")
        logging.info(f"Saved: {filepath}")

    def __repr__(self):
        return f"Page(id={self.id}, title='{self.title}', path='{self.path}', created_at={self.created_at}, updated_at={self.updated_at})"


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
                print(f"Successfully submitted file: {file_path}")
                logging.info(f"Successfully submitted file: {file_path}")
                return True
        except Exception as e:
            print(f"Error submitting file: {str(e)}")
            logging.error(f"Error submitting file: {str(e)}")
            return False

    def get_doc_info(self):
        """Gets document IDs and filenames for RAG files."""
        url = f"{self.base_url}/v1/ingest/list"
        
        try:
            response = requests.get(url, verify=False)
            response.raise_for_status()
            data = response.json()
            
    
            docs = []
            for doc in data["data"]:
                filename = doc["doc_metadata"]["file_name"]
                # Extract date from filename (format: ID - Name - YYYY-MM-DD_HH-MM-SS.md)
                try:
                    # Extract date from filename (format: ID - Name - YYYY-MM-DD_HH-MM-SS.md)
                    date_str = filename.split(' - ')[-1].replace('.md', '')
                    date = datetime.strptime(date_str, '%Y-%m-%d_%H-%M-%S')
                    docs.append({
                        "id": doc["doc_id"],
                        "filename": filename,
                        "update_date": date
                    })
                except (IndexError, ValueError) as e:
                    #print(f"Could not parse date from filename {filename}: {e}")
                    #logging.warning(f"Could not parse date from filename {filename}: {e}")
                    docs.append({
                        "id": doc["doc_id"],
                        "filename": filename,
                        "update_date": None
                    })
                    continue
                
            return docs
        except Exception as e:
            print(f"Error getting document IDs: {str(e)}")
            logging.error(f"Error getting document IDs: {str(e)}")
            return []

    def delete_document(self, doc_info):
        """Deletes a document by ID."""
        url = f"{self.base_url}/v1/ingest/{doc_info['id']}"
        
        try:
            response = requests.delete(url, verify=False)
            response.raise_for_status()
            print(f"Successfully deleted document: {doc_info['filename']} (ID: {doc_info['id']})")
            logging.info(f"Successfully deleted document: {doc_info['filename']} (ID: {doc_info['id']})")
            return True
        except Exception as e:
            print(f"Error deleting document {doc_info['filename']} (ID: {doc_info['id']}): {str(e)}")
            logging.error(f"Error deleting document {doc_info['filename']} (ID: {doc_info['id']}): {str(e)}")
            return False

    def cleanup_old_versions(self):
        """
        Identifies and deletes old versions of documents, keeping only the latest version
        of each document. Handles RAG document chunks where a single markdown file may be
        split into multiple documents with the same filename but different IDs.
        """
        print("Starting cleanup of old document versions...")
        logging.info("Starting cleanup of old document versions...")
        
        try:
            # Get all documents
            docs = self.get_doc_info()
            if not docs:
                print("No documents found to clean up")
                logging.info("No documents found to clean up")
                return
            
            # First, group documents by their complete filename
            filename_groups = {}
            for doc in docs:
                if doc["update_date"] is None:
                    continue
                
                filename = doc["filename"]
                if filename not in filename_groups:
                    filename_groups[filename] = []
                filename_groups[filename].append(doc)
            
            # Then group by base name (ID - Title) to compare dates
            base_groups = {}
            for filename, chunks in filename_groups.items():
                # Extract base name (ID - Title) from filename
                # Format: ID - Title - Date.md
                parts = filename.split(" - ")
                if len(parts) != 3:
                    continue
                
                base_name = f"{parts[0]} - {parts[1]}"
                if base_name not in base_groups:
                    base_groups[base_name] = []
                # Store the whole chunk group together
                base_groups[base_name].append(chunks)
            
            # Process each base group
            for base_name, chunk_groups in base_groups.items():
                if len(chunk_groups) <= 1:
                    continue
                
                # Sort chunk groups by date (newest first)
                # Use the first chunk's date since all chunks in a group have the same date
                chunk_groups.sort(key=lambda x: x[0]["update_date"], reverse=True)
                
                # Keep the newest chunk group, delete all others
                newest_group = chunk_groups[0]
                print(f"\nKeeping latest version: {newest_group[0]['filename']}")
                print(f"(Keeping {len(newest_group)} chunks)")
                logging.info(f"Keeping latest version: {newest_group[0]['filename']} ({len(newest_group)} chunks)")
                
                # Delete all chunks from older versions
                for older_group in chunk_groups[1:]:
                    print(f"\nDeleting older version: {older_group[0]['filename']}")
                    print(f"(Deleting {len(older_group)} chunks)")
                    logging.info(f"Deleting older version: {older_group[0]['filename']} ({len(older_group)} chunks)")
                    
                    for chunk in older_group:
                        self.delete_document(chunk)
            
            print("\nDocument cleanup completed")
            logging.info("Document cleanup completed")
            
        except Exception as e:
            print(f"Error during document cleanup: {str(e)}")
            logging.error(f"Error during document cleanup: {str(e)}")

def initialize_pages(api_response, client):
    """
    Parses API response and initializes Page objects with details.

    :param api_response: The initial API response containing a list of pages
    :param client: GraphQL Client instance to fetch additional details
    :return: List of Page objects
    """
    pages = []

    for page_data in api_response.get("pages", {}).get("list", []):
        page = Page(
            page_id=page_data["id"],
            path=page_data["path"],
            title=page_data["title"]
        )
        # Only fetch content since we already have updatedAt
        query = gql("""
        {
          pages {
            single(id: %s) {
              content
            }
          }
        }
        """ % page.id)

        response = client.execute(query)
        if response and "pages" in response and "single" in response["pages"]:
            page.content = response["pages"]["single"].get("content")
        # Set updatedAt from initial fetch
        page.updated_at = page_data.get("updatedAt")
        pages.append(page)

    return pages

def setup_logging():
    """Configure and initialize logging."""
    # Disable yappy loggers
    logging.getLogger('gql').setLevel(logging.WARNING)
    logging.getLogger('urllib3').setLevel(logging.WARNING)
    logging.getLogger('graphql').setLevel(logging.WARNING)
    
    log_filename = f"wiki_processor_{datetime.now().strftime('%Y-%m-%d_%H-%M-%S')}.log"
    logging.basicConfig(
        filename=log_filename,
        level=logging.INFO,
        format='%(asctime)s - %(levelname)s - %(message)s'
    )
    return logging.getLogger(__name__)

def initialize_client():
    """Initialize and return the GraphQL client."""
    try:
        transport = RequestsHTTPTransport(
            url=apiurl,
            verify=False,
            headers={"Authorization": f"Bearer {apikey}"}
        )
        client = Client(transport=transport, fetch_schema_from_transport=False)
        print("Successfully initialized GraphQL client")
        logging.info("Successfully initialized GraphQL client")
        return client
    except Exception as e:
        print(f"Failed to initialize GraphQL client: {str(e)}")
        logging.error(f"Failed to initialize GraphQL client: {str(e)}")
        raise

def fetch_pages(client):
    """Fetch all pages from the wiki."""
    query = gql("""
        query {
            pages {
                list (orderBy: ID) {
                    id
                    path
                    title
                    updatedAt
                }
            }
        }
    """)
    
    try:
        print("Fetching pages from the wiki...")
        logging.info("Fetching pages from the wiki...")
        response = client.execute(query)
        print(f"Successfully fetched {len(response.get('pages', {}).get('list', []))} pages")
        logging.info(f"Successfully fetched {len(response.get('pages', {}).get('list', []))} pages")
        return response
    except Exception as e:
        print(f"Failed to fetch pages: {str(e)}")
        logging.error(f"Failed to fetch pages: {str(e)}")
        raise

def construct_filename(page_id, title, updated_at):
    """Construct filename in the same format as save_to_markdown."""
    safe_title = "".join(c if c.isalnum() or c in " _-" else "_" for c in title)
    filenamedate = datetime.strptime(updated_at, '%Y-%m-%dT%H:%M:%S.%fZ')
    formatted_date = filenamedate.strftime('%Y-%m-%d_%H-%M-%S')
    return f"{page_id} - {safe_title} - {formatted_date}.md"

def filter_new_pages(pages, client):
    """Filter out pages that already exist in PrivateGPT."""
    try:
        # Get existing files from PrivateGPT
        ragdocs = client.get_doc_info()
        existing_files = [doc["filename"] for doc in ragdocs]
        for file in existing_files:
            print(f"Existing file: {file}")
            logging.info(f"Existing file: {file}")
        
        new_pages = []
        for page in pages.get("pages", {}).get("list", []):
            # Use updatedAt from the initial fetch
            if page.get("updatedAt"):
                # Construct expected filename
                expected_filename = construct_filename(
                    page["id"],
                    page["title"],
                    page["updatedAt"]
                )
                
                # Only add page if its filename doesn't exist
                if expected_filename not in existing_files:
                    new_pages.append(page)
        
        print(f"Filtered out {len(pages.get('pages', {}).get('list', [])) - len(new_pages)} existing pages")
        logging.info(f"Filtered out {len(pages.get('pages', {}).get('list', [])) - len(new_pages)} existing pages")
        return {"pages": {"list": new_pages}}
    except Exception as e:
        logging.error(f"Error filtering out existing pages: {str(e)}")
        raise
    

def save_pages(pages):
    """Save all pages to markdown files."""
    try:
        total_pages = len(pages)
        saved_pages = 0
        logging.info(f"Starting to save {total_pages} pages")
        
        for page in pages:
            try:
                page.save_to_markdown()
                saved_pages += 1
            except Exception as e:
                logging.error(f"Failed to save page {page.title}: {str(e)}")
        
        logging.info(f"Successfully saved {saved_pages} out of {total_pages} pages")
    except Exception as e:
        logging.error(f"Error during page saving process: {str(e)}")
        raise

def upload_pages(limit=None):
    """
    Upload pages from the pages directory to the API.
    
    :param limit: Maximum number of files to upload (None for unlimited)
    """
    ingest_client = IngestClient()
    success_count = 0
    total_files = 0
    
    try:
        # Get list of all markdown files
        md_files = [f for f in os.listdir("pages") if f.endswith(".md")]
        
        # Apply limit if specified
        if limit is not None:
            md_files = md_files[:limit]
            print(f"Limiting upload to {limit} files")
            logging.info(f"Limiting upload to {limit} files")
        
        # Process files
        for file in md_files:
            total_files += 1
            file_path = os.path.join("pages", file)
            if ingest_client.submit_file(file_path):
                success_count += 1
                logging.info(f"Successfully uploaded {file}")
            else:
                logging.error(f"Failed to upload {file}")
        
        logging.info(f"Upload complete: {success_count} of {total_files} files uploaded successfully")
        print(f"Upload complete: {success_count} of {total_files} files uploaded successfully")
        
    except Exception as e:
        logging.error(f"Error during file upload process: {str(e)}")
        raise

def main():
    """Main function."""
    try:
        # Delete contents of pages directory
        for file in os.listdir("pages"):
            file_path = os.path.join("pages", file)
            try:
                if os.path.isfile(file_path):
                    os.unlink(file_path)
            except Exception as e:
                logging.error(f"Error deleting file {file_path}: {str(e)}")

        logger = setup_logging()
        logger.info("Starting wiki page processing")
        print("Starting wiki page processing...")
        
        client = initialize_client()
        ingest_client = IngestClient()
        
        response = fetch_pages(client)
        response = filter_new_pages(response, ingest_client)
        pages = initialize_pages(response, client)
        save_pages(pages)
        
        logger.info("Wiki page processing completed successfully")
        print("Wiki page processing completed successfully")

        logger.info("Getting list of documents from PrivateGPT...")
        print("Getting list of documents from PrivateGPT...")
        ragdocs = ingest_client.get_doc_info()

        print(f"Documents in PrivateGPT: {len(ragdocs)}")
        print(f"New documents to submit: {len(pages)}")
        
        logger.info("Starting file upload process...")
        print("Starting file upload process...")
        upload_pages(limit=50)
        
        logger.info("Starting cleanup of old document versions...")
        print("\nStarting cleanup of old document versions...")
        ingest_client.cleanup_old_versions()
        
        # Exit with success code
        logger.info("All operations completed successfully")
        print("\nAll operations completed successfully")
        sys.exit(0)
        
    except Exception as e:
        logger.error(f"Wiki page processing failed: {str(e)}")
        sys.exit(1)

if __name__ == "__main__":
    main()
