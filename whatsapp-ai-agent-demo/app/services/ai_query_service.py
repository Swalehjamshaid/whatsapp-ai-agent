# ==========================================================
# FILE: app/services/ai_query_service.py (v2.1 - INTENT DETECTION ENGINE)
# ==========================================================
# PURPOSE: Intent Detection and Query Planning
# ==========================================================

import re
from typing import Optional, Dict, Any, List, Tuple
from dataclasses import dataclass, field
from datetime import date, timedelta
from loguru import logger

from app.schemas.schema_service import get_schema_service


@dataclass
class QueryPlan:
    """Routing decision output"""
    intent: str
    entity: Optional[str] = None
    entity_type: Optional[str] = None
    metric: Optional[str] = None
    date_range: Optional[Dict[str, str]] = None
    filters: Dict[str, Any] = field(default_factory=dict)
    ranking_type: Optional[str] = None
    limit: int = 10
    sort_by: Optional[str] = None
    confidence_score: float = 0.0
    requires_groq: bool = False
    service: str = "analytics"
    original_message: str = ""
    normalized_message: str = ""
    from_context: bool = False


class AIQueryService:
    """INTENT DETECTION ENGINE - Brain of the Platform"""
    
    def __init__(self):
        self.schema = get_schema_service()
        self.today = date.today()
        logger.info("AIQueryService v2.1 initialized")
    
    async def process_query(self, question: str, context: Optional[Dict] = None) -> QueryPlan:
        normalized = self._normalize(question)
        intent, confidence = self._detect_intent(normalized, question)
        entities = self._extract_entities(normalized, question, intent, context)
        metric = self._extract_metric(normalized)
        date_range = self._extract_date_range(normalized)
        ranking = self._extract_ranking(normalized)
        
        query_plan = self._build_query_plan(
            intent=intent, entities=entities, metric=metric,
            date_range=date_range, ranking=ranking,
            normalized=normalized, original=question, context=context
        )
        query_plan.confidence_score = self._calculate_confidence(query_plan, confidence)
        query_plan.service = self._determine_service(query_plan)
        query_plan.requires_groq = self._determine_groq_requirement(query_plan)
        
        logger.info(f"QueryPlan: intent={query_plan.intent}, service={query_plan.service}")
        return query_plan
    
    def _normalize(self, text: str) -> str:
        if not text:
            return ""
        normalized = text.lower()
        normalized = re.sub(r'\s+', ' ', normalized)
        normalized = re.sub(r'[^\w\s\-&.]', '', normalized)
        return normalized.strip()
    
    def _detect_intent(self, normalized: str, original: str) -> Tuple[str, float]:
        if re.search(r'\b(\d{8,12})\b', original):
            return "dn_lookup", 1.0
        intent, confidence = self.schema.detect_intent(normalized)
        if intent:
            return intent, confidence
        return "general_ai", 0.3
    
    def _extract_entities(self, normalized: str, original: str, intent: str, context: Optional[Dict]) -> Dict[str, Any]:
        entities = {}
        
        dn_match = re.search(r'\b(\d{8,12})\b', original)
        if dn_match:
            entities['dn_number'] = dn_match.group(1)
            return entities
        
        for alias, full_name in self.schema.warehouses.items():
            if alias in normalized or full_name.lower() in normalized:
                entities['warehouse'] = full_name
                break
        
        for alias, full_name in self.schema.cities.items():
            if alias in normalized or full_name.lower() in normalized:
                entities['city'] = full_name
                break
        
        dealer_match = re.search(r'(?:dealer|show|display)\s+([a-z0-9\s&\-\.]+)', normalized)
        if dealer_match:
            candidate = dealer_match.group(1).strip()
            if not self.schema.is_logistics_keyword(candidate):
                resolved = self.schema.resolve_dealer(candidate)
                if resolved:
                    entities['dealer'] = resolved
        
        if len(normalized.split()) <= 5 and not self._is_question_word(normalized):
            if not self.schema.is_logistics_keyword(normalized):
                resolved = self.schema.resolve_dealer(original)
                if resolved:
                    entities['dealer'] = resolved
        
        if not entities and context:
            if context.get('last_dealer'):
                entities['dealer'] = context['last_dealer']
                entities['from_context'] = True
            elif context.get('last_warehouse'):
                entities['warehouse'] = context['last_warehouse']
                entities['from_context'] = True
        
        return entities
    
    def _is_question_word(self, text: str) -> bool:
        return any(w in text for w in ['what', 'how', 'why', 'when', 'where', 'who', 'which'])
    
    def _extract_metric(self, normalized: str) -> Optional[str]:
        return self.schema.detect_metric(normalized)
    
    def _extract_date_range(self, normalized: str) -> Optional[Dict[str, str]]:
        if 'today' in normalized:
            return {'start_date': self.today.isoformat(), 'end_date': self.today.isoformat()}
        if 'yesterday' in normalized:
            yesterday = self.today - timedelta(days=1)
            return {'start_date': yesterday.isoformat(), 'end_date': yesterday.isoformat()}
        day_matches = {'last 7 days': 7, 'last 15 days': 15, 'last 30 days': 30, 'last 90 days': 90}
        for phrase, days in day_matches.items():
            if phrase in normalized:
                start = self.today - timedelta(days=days)
                return {'start_date': start.isoformat(), 'end_date': self.today.isoformat()}
        if 'this month' in normalized:
            start = self.today.replace(day=1)
            return {'start_date': start.isoformat(), 'end_date': self.today.isoformat()}
        return None
    
    def _extract_ranking(self, normalized: str) -> Dict[str, Any]:
        ranking = {}
        if any(w in normalized for w in ['top', 'best', 'highest']):
            ranking['ranking_type'] = 'top'
        elif any(w in normalized for w in ['bottom', 'worst', 'lowest']):
            ranking['ranking_type'] = 'bottom'
        limit_match = re.search(r'(?:top|bottom)\s+(\d+)', normalized)
        ranking['limit'] = int(limit_match.group(1)) if limit_match else 10
        if 'revenue' in normalized:
            ranking['sort_by'] = 'revenue'
        elif 'units' in normalized:
            ranking['sort_by'] = 'units'
        return ranking
    
    def _build_query_plan(self, intent: str, entities: Dict[str, Any], metric: Optional[str],
                          date_range: Optional[Dict[str, str]], ranking: Dict[str, Any],
                          normalized: str, original: str, context: Optional[Dict]) -> QueryPlan:
        entity_type = None
        entity_value = None
        if entities.get('dealer'):
            entity_type, entity_value = 'dealer', entities['dealer']
        elif entities.get('warehouse'):
            entity_type, entity_value = 'warehouse', entities['warehouse']
        elif entities.get('dn_number'):
            entity_type, entity_value = 'dn', entities['dn_number']
        
        return QueryPlan(
            intent=intent, entity=entity_value, entity_type=entity_type,
            metric=metric, date_range=date_range,
            filters=self._extract_filters(normalized, entities),
            ranking_type=ranking.get('ranking_type'), limit=ranking.get('limit', 10),
            sort_by=ranking.get('sort_by'), original_message=original,
            normalized_message=normalized, from_context=bool(entities.get('from_context'))
        )
    
    def _extract_filters(self, normalized: str, entities: Dict[str, Any]) -> Dict[str, Any]:
        filters = {}
        if entities.get('city'):
            filters['city'] = entities['city']
        if entities.get('warehouse'):
            filters['warehouse'] = entities['warehouse']
        if 'pending' in normalized:
            filters['status'] = 'pending'
        elif 'delivered' in normalized:
            filters['status'] = 'delivered'
        return filters
    
    def _calculate_confidence(self, query_plan: QueryPlan, intent_confidence: float) -> float:
        score = intent_confidence * 0.3
        if query_plan.entity:
            score += 0.25
        if query_plan.metric:
            score += 0.20
        if query_plan.date_range:
            score += 0.15
        if query_plan.from_context:
            score += 0.10
        return round(min(score, 1.0), 2)
    
    def _determine_service(self, query_plan: QueryPlan) -> str:
        if query_plan.intent in ['pending_pgi', 'pending_pod', 'pgi_aging', 'pod_aging']:
            return "kpi"
        if query_plan.intent in ['general_ai', 'root_cause']:
            return "groq"
        return "analytics"
    
    def _determine_groq_requirement(self, query_plan: QueryPlan) -> bool:
        return query_plan.intent in ['general_ai', 'root_cause', 'executive_insight']


def get_ai_query_service() -> AIQueryService:
    return AIQueryService()
