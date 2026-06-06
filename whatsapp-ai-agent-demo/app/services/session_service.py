# ==========================================================
# FILE: app/services/session_service.py
# ==========================================================

from dataclasses import dataclass, field
from enum import Enum
from datetime import datetime, timedelta
from typing import Dict, List, Optional
import threading

class UserRole(str, Enum):
    CEO = "ceo"
    MANAGER = "manager"
    WAREHOUSE = "warehouse"
    DEALER = "dealer"
    VENDOR = "vendor"
    GUEST = "guest"

@dataclass
class ConversationContext:
    selected_dealer: Optional[str] = None
    selected_city: Optional[str] = None
    selected_warehouse: Optional[str] = None
    selected_dn: Optional[str] = None
    last_intent: Optional[str] = None
    last_question: Optional[str] = None
    last_response: Optional[str] = None
    executive_mode: bool = False

@dataclass
class UserSession:
    phone_number: str
    user_role: str = "guest"
    user_name: str = "Unknown"
    department: str = "Unknown"
    selected_dealer: Optional[str] = None
    selected_city: Optional[str] = None
    selected_warehouse: Optional[str] = None
    selected_dn: Optional[str] = None
    last_intent: Optional[str] = None
    last_question: Optional[str] = None
    last_response: Optional[str] = None
    executive_mode: bool = False
    pending_dealer_matches: List[str] = field(default_factory=list)
    pending_selection_time: Optional[datetime] = None
    conversation_history: List[dict] = field(default_factory=list)
    created_at: datetime = field(default_factory=datetime.utcnow)
    updated_at: datetime = field(default_factory=datetime.utcnow)
    total_queries: int = 0
    last_query_time: Optional[datetime] = None

class SessionService:
    def __init__(self, db=None):
        self.db = db
        self._lock = threading.RLock()
        if not hasattr(SessionService, "_sessions"):
            SessionService._sessions = {}
    
    def get_or_create_session(self, phone_number: str, user_role: str = "guest", user_name: str = "", department: str = ""):
        with self._lock:
            if phone_number not in SessionService._sessions:
                SessionService._sessions[phone_number] = UserSession(
                    phone_number=phone_number,
                    user_role=user_role,
                    user_name=user_name or "Unknown",
                    department=department or "Unknown"
                )
            return SessionService._sessions[phone_number]
    
    def update_activity(self, phone_number: str):
        with self._lock:
            session = self.get_or_create_session(phone_number)
            session.updated_at = datetime.utcnow()
    
    def get_context(self, phone_number: str):
        with self._lock:
            session = self.get_or_create_session(phone_number)
            return ConversationContext(
                selected_dealer=session.selected_dealer,
                selected_city=session.selected_city,
                selected_warehouse=session.selected_warehouse,
                selected_dn=session.selected_dn,
                last_intent=session.last_intent,
                last_question=session.last_question,
                last_response=session.last_response,
                executive_mode=session.executive_mode
            )
    
    def update_session_context(self, phone_number: str, **kwargs):
        with self._lock:
            session = self.get_or_create_session(phone_number)
            for key, value in kwargs.items():
                if hasattr(session, key):
                    setattr(session, key, value)
            session.updated_at = datetime.utcnow()
            session.total_queries += 1
            session.last_query_time = datetime.utcnow()
    
    def clear_context(self, phone_number: str):
        with self._lock:
            session = self.get_or_create_session(phone_number)
            session.selected_dealer = None
            session.selected_city = None
            session.selected_warehouse = None
            session.selected_dn = None
            session.last_intent = None
            session.last_question = None
            session.last_response = None
            session.executive_mode = False
            session.pending_dealer_matches = []
            session.pending_selection_time = None
            session.updated_at = datetime.utcnow()
    
    def add_to_conversation_history(self, phone_number: str, question: str, response: str, intent: str, entity: str = None):
        with self._lock:
            session = self.get_or_create_session(phone_number)
            session.conversation_history.append({
                "question": question[:500],
                "response": response[:1000],
                "intent": intent,
                "entity": entity,
                "timestamp": datetime.utcnow().isoformat()
            })
            session.conversation_history = session.conversation_history[-50:]
            session.updated_at = datetime.utcnow()
    
    def set_pending_dealer_selection(self, phone_number: str, matches: List[str], timeout_minutes: int = 10):
        with self._lock:
            session = self.get_or_create_session(phone_number)
            session.pending_dealer_matches = matches
            session.pending_selection_time = datetime.utcnow() + timedelta(minutes=timeout_minutes)
            session.updated_at = datetime.utcnow()
    
    def handle_dealer_selection(self, phone_number: str, message: str):
        with self._lock:
            session = self.get_or_create_session(phone_number)
            
            if session.pending_selection_time and datetime.utcnow() > session.pending_selection_time:
                session.pending_dealer_matches = []
                session.pending_selection_time = None
                return {"handled": False, "expired": True}
            
            if not session.pending_dealer_matches:
                return {"handled": False}
            
            try:
                index = int(message.strip()) - 1
                if 0 <= index < len(session.pending_dealer_matches):
                    dealer = session.pending_dealer_matches[index]
                    session.selected_dealer = dealer
                    session.pending_dealer_matches = []
                    session.pending_selection_time = None
                    session.updated_at = datetime.utcnow()
                    return {"handled": True, "selected_dealer": dealer}
            except ValueError:
                pass
            
            return {"handled": False}
    
    def get_session_by_phone(self, phone_number: str):
        return SessionService._sessions.get(phone_number)
    
    def get_active_sessions_count(self, hours: int = 24):
        with self._lock:
            cutoff = datetime.utcnow() - timedelta(hours=hours)
            return sum(1 for s in SessionService._sessions.values() if s.updated_at > cutoff)


_session_service_instance = None
_session_service_lock = threading.Lock()

def get_session_service(db=None):
    global _session_service_instance
    with _session_service_lock:
        if _session_service_instance is None:
            _session_service_instance = SessionService(db)
    return _session_service_instance
