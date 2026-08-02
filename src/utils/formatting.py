import re
from datetime import datetime, timedelta
import logging
import humanize

log = logging.getLogger(__name__)

class TimeFormatter:
    """Handles dynamic path resolution with time-based placeholders."""
    
    @staticmethod
    def format_path(path: str) -> str:
        """
        Replaces placeholders in the path with actual time strings.
        Supports: {TODAY}, {NOW}, {YESTERDAY}, {DATE}, {TIME}.
        Case-insensitive matching (fuzzy).
        """
        now = datetime.now()
        
        replacements = {
            "{TODAY}": now.strftime("%Y-%m-%d"),
            "{DATE}": now.strftime("%Y-%m-%d"),
            "{NOW}": now.strftime("%Y%m%d_%H%M%S"),
            "{TIME}": now.strftime("%H%M%S"),
            "{YESTERDAY}": (now - timedelta(days=1)).strftime("%Y-%m-%d"),
        }
        
        # Regex to find anything inside {}
        placeholders = re.findall(r"\{(\w+)\}", path)
        
        result_path = path
        for p in placeholders:
            key = f"{{{p.upper()}}}"
            if key in replacements:
                # Use a case-insensitive regex replace to handle fuzzy input
                pattern = re.compile(re.escape(f"{{{p}}}"), re.IGNORECASE)
                result_path = pattern.sub(replacements[key], result_path)
            elif p.upper() == "HUMAN_NOW":
                pattern = re.compile(re.escape(f"{{{p}}}"), re.IGNORECASE)
                result_path = pattern.sub(humanize.naturaltime(now).replace(" ", "_"), result_path)
            else:
                log.warning(f"Unknown placeholder: {{{p}}}")
                
        return result_path
