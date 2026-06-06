from dataclasses import dataclass, field
from enum import Enum
from datetime import datetime, timedelta
from typing import Dict, List, Optional
import threading
import json
import os

# ==========================================================
# FIX #1: PERSISTENCE LAYER (Optional - Redis/PostgreSQL)
# ==========================================================

# Try to import Redis for persistence across restarts
try:
    import redis
    REDIS_AVAILABLE = True
except ImportError:
    REDIS_AVAILABLE = False

# Try to import PostgreSQL for persistence
try:
    from sqlalchemy import create_engine, Column, String, DateTime, Text, Integer
    from sqlalchemy.ext.declarative import declarative_base
    from sqlalchemy.orm import sessionmaker
    POSTGRES_AVAILABLE = True
except ImportError:
    POSTGRES_AVAILABLE = False


# ==========================================================
# USER ROLES
# ==========================================================

class UserRole(str, Enum):
    CEO = "ceo"
    MANAGER = "manager"
    WAREHOUSE = "warehouse"
    DEALER = "dealer"
    VENDOR = "vendor"
    GUEST = "guest"


# ==========================================================
# CONVERSATION CONTEXT
# ==========================================================

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


# ==========================================================
# USER SESSION
# ==========================================================

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
    
    # FIX #5: Dealer selection timeout
    pending_selection_time: Optional[datetime] = None

    conversation_history: List[dict] = field(default_factory=list)

    created_at: datetime = field(default_factory=datetime.utcnow)
    updated_at: datetime = field(default_factory=datetime.utcnow)
    
    # Extra metadata for analytics
    total_queries: int = 0
    last_query_time: Optional[datetime] = None


# ==========================================================
# SESSION SERVICE WITH IMPROVEMENTS
# ==========================================================

class SessionService:

    def __init__(self, db=None, use_persistence: bool = False):
        self.db = db
        self.use_persistence = use_persistence
        
        # FIX #8: Thread safety lock
        self._lock = threading.RLock()
        
        # In-memory cache (still needed for performance)
        if not hasattr(SessionService, "_sessions"):
            SessionService._sessions = {}
        
        # FIX #1: Redis persistence (optional)
        self.redis_client = None
        if use_persistence and REDIS_AVAILABLE:
            try:
                redis_url = os.getenv("REDIS_URL", "redis://localhost:6379")
                self.redis_client = redis.from_url(redis_url)
                print(f"✅ Redis persistence enabled at {redis_url}")
            except Exception as e:
                print(f"⚠️ Redis connection failed: {e}")
                self.redis_client = None
        
        # PostgreSQL persistence (optional)
        self.postgres_session = None
        if use_persistence and POSTGRES_AVAILABLE:
            try:
                from app.models import SessionModel  # Would need to create this model
                # This is a placeholder - actual implementation would use SQLAlchemy
                pass
            except Exception as e:
                print(f"⚠️ PostgreSQL session persistence not configured: {e}")
        
        # FIX #2: Start cleanup thread
        self._start_cleanup_thread()

    def _start_cleanup_thread(self):
        """Start background thread for session cleanup"""
        def cleanup_loop():
            while True:
                try:
                    import time
                    time.sleep(3600)  # Run every hour
                    self.cleanup_old_sessions()
                except Exception as e:
                    print(f"Cleanup thread error: {e}")
        
        import threading
        cleanup_thread = threading.Thread(target=cleanup_loop, daemon=True)
        cleanup_thread.start()

    # ==========================================================
    # FIX #2: SESSION CLEANUP
    # ==========================================================
    
    def cleanup_old_sessions(self, hours: int = 24):
        """
        Remove sessions older than specified hours
        FIX #2: Prevent memory leaks
        """
        with self._lock:
            now = datetime.utcnow()
            cutoff = now - timedelta(hours=hours)
            
            old_sessions = []
            for phone, session in SessionService._sessions.items():
                if session.updated_at < cutoff:
                    old_sessions.append(phone)
            
            for phone in old_sessions:
                del SessionService._sessions[phone]
                # Also clean from Redis if used
                if self.redis_client:
                    self.redis_client.delete(f"session:{phone}")
            
            if old_sessions:
                print(f"🧹 Cleaned up {len(old_sessions)} old sessions (older than {hours} hours)")
            
            return len(old_sessions)

    # ==========================================================
    # FIX #1: PERSISTENCE METHODS
    # ==========================================================
    
    def _save_to_redis(self, phone_number: str, session: UserSession):
        """Save session to Redis for persistence across restarts"""
        if not self.redis_client:
            return
        
        try:
            session_data = {
                "phone_number": session.phone_number,
                "user_role": session.user_role,
                "user_name": session.user_name,
                "department": session.department,
                "selected_dealer": session.selected_dealer,
                "selected_city": session.selected_city,
                "selected_warehouse": session.selected_warehouse,
                "selected_dn": session.selected_dn,
                "last_intent": session.last_intent,
                "last_question": session.last_question,
                "last_response": session.last_response,
                "executive_mode": session.executive_mode,
                "pending_dealer_matches": json.dumps(session.pending_dealer_matches),
                "pending_selection_time": session.pending_selection_time.isoformat() if session.pending_selection_time else None,
                "conversation_history": json.dumps(session.conversation_history[-20:]),
                "created_at": session.created_at.isoformat(),
                "updated_at": session.updated_at.isoformat(),
                "total_queries": session.total_queries,
                "last_query_time": session.last_query_time.isoformat() if session.last_query_time else None
            }
            self.redis_client.hset(f"session:{phone_number}", mapping=session_data)
            self.redis_client.expire(f"session:{phone_number}", 86400)  # 24 hours
        except Exception as e:
            print(f"Failed to save session to Redis: {e}")
    
    def _load_from_redis(self, phone_number: str) -> Optional[UserSession]:
        """Load session from Redis after restart"""
        if not self.redis_client:
            return None
        
        try:
            data = self.redis_client.hgetall(f"session:{phone_number}")
            if not data:
                return None
            
            # Decode bytes to strings
            decoded = {k.decode(): v.decode() for k, v in data.items()}
            
            session = UserSession(
                phone_number=decoded["phone_number"],
                user_role=decoded.get("user_role", "guest"),
                user_name=decoded.get("user_name", "Unknown"),
                department=decoded.get("department", "Unknown"),
                selected_dealer=decoded.get("selected_dealer"),
                selected_city=decoded.get("selected_city"),
                selected_warehouse=decoded.get("selected_warehouse"),
                selected_dn=decoded.get("selected_dn"),
                last_intent=decoded.get("last_intent"),
                last_question=decoded.get("last_question"),
                last_response=decoded.get("last_response"),
                executive_mode=decoded.get("executive_mode", "False") == "True",
                pending_dealer_matches=json.loads(decoded.get("pending_dealer_matches", "[]")),
                pending_selection_time=datetime.fromisoformat(decoded["pending_selection_time"]) if decoded.get("pending_selection_time") else None,
                conversation_history=json.loads(decoded.get("conversation_history", "[]")),
                created_at=datetime.fromisoformat(decoded["created_at"]),
                updated_at=datetime.fromisoformat(decoded["updated_at"]),
                total_queries=int(decoded.get("total_queries", 0)),
                last_query_time=datetime.fromisoformat(decoded["last_query_time"]) if decoded.get("last_query_time") else None
            )
            return session
        except Exception as e:
            print(f"Failed to load session from Redis: {e}")
            return None

    # ==========================================================
    # CORE SESSION METHODS (THREAD-SAFE)
    # ==========================================================
    
    def get_or_create_session(
        self,
        phone_number: str,
        user_role: str = "guest",
        user_name: str = "",
        department: str = ""
    ):
        """Get existing session or create new one - Thread-safe"""
        with self._lock:
            # Check memory cache first
            if phone_number in SessionService._sessions:
                return SessionService._sessions[phone_number]
            
            # FIX #1: Try to load from Redis after restart
            if self.use_persistence and self.redis_client:
                session = self._load_from_redis(phone_number)
                if session:
                    SessionService._sessions[phone_number] = session
                    return session
            
            # Create new session
            SessionService._sessions[phone_number] = UserSession(
                phone_number=phone_number,
                user_role=user_role,
                user_name=user_name or "Unknown",
                department=department or "Unknown"
            )
            
            return SessionService._sessions[phone_number]

    def update_activity(self, phone_number: str):
        """Update session activity timestamp"""
        with self._lock:
            session = self.get_or_create_session(phone_number)
            session.updated_at = datetime.utcnow()
            
            if self.use_persistence and self.redis_client:
                self._save_to_redis(phone_number, session)

    def get_context(self, phone_number: str):
        """Get conversation context"""
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
        """Update session context - Thread-safe"""
        with self._lock:
            session = self.get_or_create_session(phone_number)
            
            for key, value in kwargs.items():
                if hasattr(session, key):
                    setattr(session, key, value)
            
            session.updated_at = datetime.utcnow()
            session.total_queries += 1
            session.last_query_time = datetime.utcnow()
            
            if self.use_persistence and self.redis_client:
                self._save_to_redis(phone_number, session)

    def clear_context(self, phone_number: str):
        """Clear all context from session"""
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
            session.pending_selection_time = None  # FIX #5: Clear timeout
            
            session.updated_at = datetime.utcnow()
            
            if self.use_persistence and self.redis_client:
                self._save_to_redis(phone_number, session)

    # ==========================================================
    # FIX #3: CONVERSATION HISTORY WITH ENTITY
    # ==========================================================
    
    def add_to_conversation_history(
        self,
        phone_number: str,
        question: str,
        response: str,
        intent: str,
        entity: str = None  # FIX #3: Added entity parameter
    ):
        """Add entry to conversation history with entity tracking"""
        with self._lock:
            session = self.get_or_create_session(phone_number)
            
            # FIX #6: Increased history size from 20 to 50
            session.conversation_history.append({
                "question": question[:500],
                "response": response[:1000],
                "intent": intent,
                "entity": entity,  # FIX #3: Store entity for context
                "timestamp": datetime.utcnow().isoformat()
            })
            
            # Keep last 50 messages (FIX #6: increased from 20)
            session.conversation_history = session.conversation_history[-50:]
            
            session.updated_at = datetime.utcnow()
            
            if self.use_persistence and self.redis_client:
                self._save_to_redis(phone_number, session)

    # ==========================================================
    # FIX #5: DEALER SELECTION WITH TIMEOUT
    # ==========================================================
    
    def set_pending_dealer_selection(
        self,
        phone_number: str,
        matches: List[str],
        timeout_minutes: int = 10
    ):
        """Set pending dealer selection with timeout"""
        with self._lock:
            session = self.get_or_create_session(phone_number)
            
            session.pending_dealer_matches = matches
            session.pending_selection_time = datetime.utcnow() + timedelta(minutes=timeout_minutes)
            
            session.updated_at = datetime.utcnow()
            
            if self.use_persistence and self.redis_client:
                self._save_to_redis(phone_number, session)

    def handle_dealer_selection(
        self,
        phone_number: str,
        message: str
    ):
        """Handle dealer selection from numbered list - with timeout check"""
        with self._lock:
            session = self.get_or_create_session(phone_number)
            
            # FIX #5: Check if pending selection has expired
            if session.pending_selection_time and datetime.utcnow() > session.pending_selection_time:
                # Selection expired
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
                    
                    if self.use_persistence and self.redis_client:
                        self._save_to_redis(phone_number, session)
                    
                    return {
                        "handled": True,
                        "selected_dealer": dealer
                    }
            except ValueError:
                pass
            
            return {"handled": False}

    def get_session_by_phone(self, phone_number: str):
        """Get session by phone number"""
        with self._lock:
            return SessionService._sessions.get(phone_number)

    def get_active_sessions_count(self, hours: int = 24):
        """Get count of active sessions in last N hours"""
        with self._lock:
            now = datetime.utcnow()
            cutoff = now - timedelta(hours=hours)
            
            active = 0
            for session in SessionService._sessions.values():
                if session.updated_at > cutoff:
                    active += 1
            
            return active

    # ==========================================================
    # FIX #7: SESSION STATISTICS
    # ==========================================================
    
    def get_session_stats(self) -> Dict[str, int]:
        """
        Get detailed session statistics
        FIX #7: Analytics for WhatsApp usage monitoring
        """
        with self._lock:
            stats = {
                "total_sessions": len(SessionService._sessions),
                "active_24h": 0,
                "active_7d": 0,
                "by_role": {
                    "ceo": 0,
                    "manager": 0,
                    "warehouse": 0,
                    "dealer": 0,
                    "vendor": 0,
                    "guest": 0
                },
                "with_dealer_selected": 0,
                "with_city_selected": 0,
                "executive_mode_active": 0,
                "total_queries_all": 0,
                "avg_queries_per_session": 0
            }
            
            now = datetime.utcnow()
            cutoff_24h = now - timedelta(hours=24)
            cutoff_7d = now - timedelta(days=7)
            
            total_queries = 0
            
            for session in SessionService._sessions.values():
                if session.updated_at > cutoff_24h:
                    stats["active_24h"] += 1
                if session.updated_at > cutoff_7d:
                    stats["active_7d"] += 1
                
                # Role breakdown
                role = session.user_role
                if role in stats["by_role"]:
                    stats["by_role"][role] += 1
                
                # Context tracking
                if session.selected_dealer:
                    stats["with_dealer_selected"] += 1
                if session.selected_city:
                    stats["with_city_selected"] += 1
                if session.executive_mode:
                    stats["executive_mode_active"] += 1
                
                total_queries += session.total_queries
            
            if stats["total_sessions"] > 0:
                stats["avg_queries_per_session"] = round(total_queries / stats["total_sessions"], 1)
            stats["total_queries_all"] = total_queries
            
            return stats

    # ==========================================================
    # FIX #4: GREETING CONTEXT RESET (Helper Method)
    # ==========================================================
    
    def reset_on_greeting(self, phone_number: str, message: str) -> bool:
        """
        Check if message is a greeting and clear context if so
        Returns True if context was cleared
        """
        greeting_patterns = [
            r'\bhello\b', r'\bhi\b', r'\bhey\b', r'\bsalam\b',
            r'\bgood morning\b', r'\bgood evening\b'
        ]
        
        import re
        message_lower = message.lower()
        
        for pattern in greeting_patterns:
            if re.search(pattern, message_lower):
                self.clear_context(phone_number)
                return True
        
        return False


# ==========================================================
# DEPENDENCY (Thread-safe singleton)
# ==========================================================

_session_service_instance = None
_session_service_lock = threading.Lock()

def get_session_service(db=None, use_persistence: bool = False):
    """Get session service singleton - Thread-safe"""
    global _session_service_instance
    
    with _session_service_lock:
        if _session_service_instance is None:
            _session_service_instance = SessionService(db, use_persistence)
    
    return _session_service_instance


def reset_session_service():
    """Reset singleton - Useful for testing"""
    global _session_service_instance
    with _session_service_lock:
        _session_service_instance = None
        SessionService._sessions = {}
        print("🔄 Session service reset")
