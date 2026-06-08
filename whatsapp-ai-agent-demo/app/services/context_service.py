# ==========================================================
# FILE: app/services/context_service.py
# ==========================================================

from typing import Dict, Any, Optional
from datetime import datetime, timedelta
from collections import deque

from sqlalchemy.orm import Session
from loguru import logger

from app.services.entity_extractor import EntityType


class ContextService:
    """
    Context management service.
    Stores and retrieves conversation context per user.
    """
    
    # Context expiry (24 hours)
    CONTEXT_EXPIRY_HOURS = 24
    
    def __init__(self, db: Session):
        self.db = db
        # In-memory cache for active contexts (can be moved to Redis)
        self.contexts: Dict[str, Dict] = {}
        self.history: Dict[str, deque] = {}
    
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
            self.contexts[phone_number] = self._create_empty_context()
        
        context = self.contexts[phone_number]
        
        # Check expiry
        last_ts = context.get('last_timestamp')
        if last_ts:
            last_time = datetime.fromisoformat(last_ts) if isinstance(last_ts, str) else last_ts
            if datetime.utcnow() - last_time > timedelta(hours=self.CONTEXT_EXPIRY_HOURS):
                # Context expired, create new
                self.contexts[phone_number] = self._create_empty_context()
                context = self.contexts[phone_number]
        
        return context
    
    def save_context(
        self,
        phone_number: str,
        entities: Dict,
        intent: 'IntentType',
        response: str = None
    ):
        """Save context after processing a query"""
        context = self.get_context(phone_number)
        
        # Update context with extracted entities
        from app.services.entity_extractor import EntityType
        
        if EntityType.DN_NUMBER in entities:
            context['last_dn'] = entities[EntityType.DN_NUMBER].value
        
        if EntityType.DEALER in entities:
            context['last_dealer'] = entities[EntityType.DEALER].value
        
        if EntityType.PRODUCT in entities:
            context['last_product'] = entities[EntityType.PRODUCT].value
        
        if EntityType.CITY in entities:
            context['last_city'] = entities[EntityType.CITY].value
        
        if EntityType.WAREHOUSE in entities:
            context['last_warehouse'] = entities[EntityType.WAREHOUSE].value
        
        if EntityType.MANAGER in entities:
            context['last_manager'] = entities[EntityType.MANAGER].value
        
        context['last_intent'] = intent.value
        context['last_timestamp'] = datetime.utcnow().isoformat()
        
        # Store in history
        if phone_number not in self.history:
            self.history[phone_number] = deque(maxlen=20)
        
        self.history[phone_number].append({
            'intent': intent.value,
            'entities': {k.value: v.value for k, v in entities.items()},
            'response_preview': response[:200] if response else None,
            'timestamp': datetime.utcnow().isoformat()
        })
        
        logger.debug(f"📚 Context saved for {phone_number}: last_intent={intent.value}")
    
    def resolve_follow_up(self, phone_number: str, question: str) -> Dict[str, str]:
        """
        Resolve follow-up questions using context.
        
        Example:
        User: "DN 80012345" -> context stores last_dn = "80012345"
        User: "What products?" -> resolves to {"dn_number": "80012345"}
        """
        context = self.get_context(phone_number)
        question_lower = question.lower()
        resolved = {}
        
        # Check for DN references
        if any(word in question_lower for word in ['it', 'this dn', 'the dn', 'that dn']):
            if context.get('last_dn'):
                resolved['dn_number'] = context['last_dn']
        
        # Check for dealer references
        if any(word in question_lower for word in ['the dealer', 'this dealer']):
            if context.get('last_dealer'):
                resolved['dealer'] = context['last_dealer']
        
        # Check for product references
        if any(word in question_lower for word in ['the product', 'this product', 'it']):
            if context.get('last_product'):
                resolved['product'] = context['last_product']
        
        # Check for warehouse references
        if any(word in question_lower for word in ['the warehouse', 'this warehouse']):
            if context.get('last_warehouse'):
                resolved['warehouse'] = context['last_warehouse']
        
        # Check for city references
        if any(word in question_lower for word in ['the city', 'this city']):
            if context.get('last_city'):
                resolved['city'] = context['last_city']
        
        return resolved
    
    def set_dealer_mapping(self, phone_number: str, dealer_name: str):
        """Map phone number to dealer for self-service"""
        context = self.get_context(phone_number)
        context['dealer_name'] = dealer_name
        context['dealer_mapped_at'] = datetime.utcnow().isoformat()
        logger.info(f"📞 Mapped {phone_number} to dealer: {dealer_name}")
    
    def get_dealer_for_phone(self, phone_number: str) -> Optional[str]:
        """Get dealer name mapped to phone number"""
        context = self.get_context(phone_number)
        return context.get('dealer_name')
    
    def clear_context(self, phone_number: str):
        """Clear context for a user"""
        if phone_number in self.contexts:
            self.contexts[phone_number] = self._create_empty_context()
        logger.info(f"🗑️ Context cleared for {phone_number}")
    
    def get_history(self, phone_number: str, limit: int = 5) -> list:
        """Get recent conversation history"""
        if phone_number not in self.history:
            return []
        return list(self.history[phone_number])[-limit:]
    
    def _create_empty_context(self) -> Dict:
        """Create empty context structure"""
        return {
            'last_dn': None,
            'last_dealer': None,
            'last_product': None,
            'last_city': None,
            'last_warehouse': None,
            'last_manager': None,
            'last_intent': None,
            'last_entity': None,
            'last_timestamp': None,
            'dealer_name': None,
            'dealer_mapped_at': None,
        }
