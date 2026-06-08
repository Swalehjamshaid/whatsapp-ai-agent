# ==========================================================
# FILE: app/services/context_service.py (ENTERPRISE v3.0)
# ==========================================================
# CONTEXT MANAGEMENT SERVICE
# - Stores and retrieves conversation context per user
# - Supports follow-up resolution
# - Phone number to dealer mapping for self-service
# - Context expiry handling
# ==========================================================

from typing import Dict, Any, Optional, List
from datetime import datetime, timedelta
from collections import deque
from dataclasses import dataclass, field

from sqlalchemy.orm import Session
from loguru import logger

from app.services.entity_extractor import EntityType


@dataclass
class ConversationContext:
    """User conversation context"""
    last_dn: Optional[str] = None
    last_dealer: Optional[str] = None
    last_product: Optional[str] = None
    last_city: Optional[str] = None
    last_warehouse: Optional[str] = None
    last_manager: Optional[str] = None
    last_intent: Optional[str] = None
    last_entity: Optional[str] = None
    last_timestamp: Optional[datetime] = None
    dealer_name: Optional[str] = None
    dealer_mapped_at: Optional[datetime] = None
    conversation_history: List[Dict] = field(default_factory=list)
    
    def update(self, **kwargs):
        """Update context fields"""
        for key, value in kwargs.items():
            if hasattr(self, key):
                setattr(self, key, value)
        self.last_timestamp = datetime.utcnow()
    
    def add_to_history(self, entry: Dict):
        """Add entry to conversation history"""
        self.conversation_history.append(entry)
        # Keep only last 20 messages
        if len(self.conversation_history) > 20:
            self.conversation_history = self.conversation_history[-20:]
    
    def is_expired(self, expiry_hours: int = 24) -> bool:
        """Check if context has expired"""
        if not self.last_timestamp:
            return True
        return datetime.utcnow() - self.last_timestamp > timedelta(hours=expiry_hours)


class ContextService:
    """
    Context Management Service
    
    Manages conversation context per user.
    Stores last queried entities for follow-up resolution.
    Supports phone number to dealer mapping for self-service.
    """
    
    # Context expiry (24 hours)
    CONTEXT_EXPIRY_HOURS = 24
    
    def __init__(self, db: Session):
        self.db = db
        # In-memory cache for active contexts (can be moved to Redis)
        self.contexts: Dict[str, ConversationContext] = {}
        self.history: Dict[str, deque] = {}
        
        logger.info("✅ Context Service initialized")
    
    def get_context(self, phone_number: str) -> Dict[str, Any]:
        """
        Get context for a user.
        
        Returns context dict with:
        - last_dn: Last DN queried
        - last_dealer: Last dealer queried
        - last_product: Last product queried
        - last_city: Last city queried
        - last_warehouse: Last warehouse queried
        - last_manager: Last manager queried
        - last_intent: Last intent
        - last_entity: Last entity value
        - last_timestamp: Last activity timestamp
        - dealer_name: Dealer name (if mapped from phone)
        """
        # Get or create context
        if phone_number not in self.contexts:
            self.contexts[phone_number] = ConversationContext()
        
        context = self.contexts[phone_number]
        
        # Check expiry
        if context.is_expired(self.CONTEXT_EXPIRY_HOURS):
            logger.info(f"Context expired for {phone_number}, creating new")
            self.contexts[phone_number] = ConversationContext()
            context = self.contexts[phone_number]
        
        # Return as dict for compatibility
        return {
            "last_dn": context.last_dn,
            "last_dealer": context.last_dealer,
            "last_product": context.last_product,
            "last_city": context.last_city,
            "last_warehouse": context.last_warehouse,
            "last_manager": context.last_manager,
            "last_intent": context.last_intent,
            "last_entity": context.last_entity,
            "last_timestamp": context.last_timestamp,
            "dealer_name": context.dealer_name,
            "dealer_mapped_at": context.dealer_mapped_at
        }
    
    def save_context(
        self,
        phone_number: str,
        entities: Dict,
        intent: 'IntentType',
        response: str = None
    ):
        """Save context after processing a query"""
        context = self._get_or_create_context(phone_number)
        
        from app.services.entity_extractor import EntityType
        
        # Update context with extracted entities
        if EntityType.DN_NUMBER in entities:
            context.last_dn = self._extract_entity_value(entities[EntityType.DN_NUMBER])
        
        if EntityType.DEALER in entities:
            context.last_dealer = self._extract_entity_value(entities[EntityType.DEALER])
        
        if EntityType.PRODUCT in entities:
            context.last_product = self._extract_entity_value(entities[EntityType.PRODUCT])
        
        if EntityType.CITY in entities:
            context.last_city = self._extract_entity_value(entities[EntityType.CITY])
        
        if EntityType.WAREHOUSE in entities:
            context.last_warehouse = self._extract_entity_value(entities[EntityType.WAREHOUSE])
        
        if EntityType.MANAGER in entities:
            context.last_manager = self._extract_entity_value(entities[EntityType.MANAGER])
        
        context.last_intent = intent.value if hasattr(intent, 'value') else str(intent)
        context.last_timestamp = datetime.utcnow()
        
        # Add to history
        context.add_to_history({
            'intent': context.last_intent,
            'entities': {k.value: self._extract_entity_value(v) for k, v in entities.items()},
            'response_preview': response[:200] if response else None,
            'timestamp': datetime.utcnow().isoformat()
        })
        
        logger.debug(f"📚 Context saved for {phone_number}: last_intent={context.last_intent}")
    
    def resolve_follow_up(self, phone_number: str, question: str) -> Dict[str, str]:
        """
        Resolve follow-up questions using context.
        
        Example:
        User: "DN 80012345" -> context stores last_dn = "80012345"
        User: "What products?" -> resolves to {"dn_number": "80012345"}
        """
        context = self._get_or_create_context(phone_number)
        question_lower = question.lower()
        resolved = {}
        
        # Check for DN references
        if any(word in question_lower for word in ['it', 'this dn', 'the dn', 'that dn']):
            if context.last_dn:
                resolved['dn_number'] = context.last_dn
        
        # Check for dealer references
        if any(word in question_lower for word in ['the dealer', 'this dealer']):
            if context.last_dealer:
                resolved['dealer'] = context.last_dealer
        
        # Check for product references
        if any(word in question_lower for word in ['the product', 'this product', 'it']):
            if context.last_product:
                resolved['product'] = context.last_product
        
        # Check for city references
        if any(word in question_lower for word in ['the city', 'this city']):
            if context.last_city:
                resolved['city'] = context.last_city
        
        # Check for warehouse references
        if any(word in question_lower for word in ['the warehouse', 'this warehouse']):
            if context.last_warehouse:
                resolved['warehouse'] = context.last_warehouse
        
        # Check for intent-based follow-up
        if context.last_intent and any(word in question_lower for word in ['more', 'details', 'elaborate']):
            resolved['last_intent'] = context.last_intent
        
        if resolved:
            logger.debug(f"🔄 Follow-up resolved: {resolved}")
        
        return resolved
    
    def set_dealer_mapping(self, phone_number: str, dealer_name: str):
        """Map phone number to dealer for self-service"""
        context = self._get_or_create_context(phone_number)
        context.dealer_name = dealer_name
        context.dealer_mapped_at = datetime.utcnow()
        logger.info(f"📞 Mapped {phone_number} to dealer: {dealer_name}")
    
    def get_dealer_for_phone(self, phone_number: str) -> Optional[str]:
        """Get dealer name mapped to phone number"""
        context = self._get_or_create_context(phone_number)
        return context.dealer_name
    
    def clear_context(self, phone_number: str):
        """Clear context for a user"""
        if phone_number in self.contexts:
            self.contexts[phone_number] = ConversationContext()
            logger.info(f"🗑️ Context cleared for {phone_number}")
    
    def get_history(self, phone_number: str, limit: int = 5) -> List[Dict]:
        """Get recent conversation history"""
        context = self._get_or_create_context(phone_number)
        return context.conversation_history[-limit:]
    
    def _get_or_create_context(self, phone_number: str) -> ConversationContext:
        """Get or create conversation context"""
        if phone_number not in self.contexts:
            self.contexts[phone_number] = ConversationContext()
        return self.contexts[phone_number]
    
    def _extract_entity_value(self, entity) -> Optional[str]:
        """Safely extract string value from entity"""
        if not entity:
            return None
        if isinstance(entity, str):
            return entity
        if hasattr(entity, 'value'):
            return str(entity.value)
        if isinstance(entity, dict):
            return str(entity.get('value', ''))
        return str(entity)
    
    def get_context_summary(self, phone_number: str) -> Dict:
        """Get human-readable summary of current context"""
        context = self._get_or_create_context(phone_number)
        return {
            "has_context": bool(context.last_intent),
            "last_intent": context.last_intent,
            "last_entity": context.last_entity or context.last_dn or context.last_dealer,
            "is_dealer_mapped": bool(context.dealer_name),
            "dealer_name": context.dealer_name,
            "conversation_length": len(context.conversation_history)
        }
