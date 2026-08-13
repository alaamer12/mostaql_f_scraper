import os
import sys
import uvicorn
import logging
from src.utils.logging_utils import setup_logging

def main():
    """Main entry point for the Mostaql Scraper application.
    
    Default behavior is to start the FastAPI server.
    CLI commands can be added back here if needed, but the primary 
    deployment target is now the FastAPI backend.
    """
    setup_logging()
    log = logging.getLogger("mostaql_main")
    
    # Check if we should run the API or something else
    # For now, we always default to API for production readiness on Railway
    port = int(os.environ.get("PORT", 8000))
    log.info(f"Starting Mostaql Scraper API on port {port}")
    
    uvicorn.run("src.main_api:app", host="0.0.0.0", port=port, reload=False)

if __name__ == "__main__":
    main()
