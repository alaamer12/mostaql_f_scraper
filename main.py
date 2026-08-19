import os
import sys
import uvicorn
import logging
from src.utils.logging_utils import setup_logging
from src.utils.port_utils import kill_port
from src.main_api import app

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
    # Railway injects PORT and RAILWAY_ENVIRONMENT / RAILWAY_STATIC_URL
    # When deployed (or RAILWAY_ENVIRONMENT is set), or if HOST is explicitly set, use that; otherwise default to 127.0.0.1 for local/development
    is_railway = bool(os.environ.get("RAILWAY_ENVIRONMENT") or os.environ.get("RAILWAY_STATIC_URL") or os.environ.get("RAILWAY_PROJECT_ID"))
    default_host = "0.0.0.0" if is_railway else "127.0.0.1"
    
    port = int(os.environ.get("PORT", 8000))
    host = os.environ.get("HOST", default_host)
    kill_existing = os.environ.get("KILL_EXISTING_PORT", "true").lower() in ("true", "1", "yes")
    
    if kill_existing:
        kill_port(port=port, host=host, logger=log)
        
    log.info(f"Starting Mostaql Scraper API on http://{host}:{port}")
    
    uvicorn.run("src.main_api:app", host=host, port=port, reload=False)

if __name__ == "__main__":
    main()
