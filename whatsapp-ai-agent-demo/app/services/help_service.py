# ==========================================================
# FILE: app/services/help_service.py
# ==========================================================
# HELP SERVICE
# ==========================================================

from typing import Dict, Any


class HelpService:
    """Help and menu service"""
    
    def __init__(self, db=None):
        self.db = db
    
    def get_help(self) -> Dict[str, Any]:
        """Get help message"""
        from app.services.ai_query_service import WELCOME_MESSAGE
        return {"help": WELCOME_MESSAGE}
    
    def get_menu(self) -> Dict[str, Any]:
        """Get quick menu"""
        return {
            "menu": {
                "tracking": "DN <number>",
                "dealer": "<dealer name>",
                "rankings": "Top dealers / Top products",
                "pending": "Pending PODs / Pending PGI",
                "executive": "Executive summary / CEO briefing",
                "analytics": "Why delays? / Forecast sales",
                "alerts": "Control tower / Critical DNs"
            }
        }
