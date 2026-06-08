# ==========================================================
# FILE: app/services/entity_extractor.py (ENTERPRISE v3.0)
# ==========================================================
# ENTITY EXTRACTION ENGINE
# - Extracts structured entities from natural language
# - Supports DN, Dealer, Product, City, Warehouse, etc.
# - Fuzzy matching for dealer names
# ==========================================================

import re
from enum import Enum
from typing import Dict, Any, Optional, List, Tuple
from dataclasses import dataclass
from collections import defaultdict


class EntityType(str, Enum):
    """All supported entity types"""
    DN_NUMBER = "dn_number"
    DEALER = "dealer"
    PRODUCT = "product"
    WAREHOUSE = "warehouse"
    CITY = "city"
    DIVISION = "division"
    MANAGER = "manager"
    DATE_RANGE = "date_range"
    PHONE_NUMBER = "phone_number"


@dataclass
class ExtractedEntity:
    """Represents an extracted entity"""
    type: EntityType
    value: str
    confidence: float = 1.0
    metadata: Dict = None


class EntityExtractor:
    """
    Entity Extraction Engine
    
    Extracts structured entities from natural language queries.
    Supports pattern matching, keyword detection, and fuzzy matching.
    """
    
    # ==========================================================
    # ENTITY PATTERNS
    # ==========================================================
    
    PATTERNS = {
        EntityType.DN_NUMBER: [
            r'\b(\d{10,15})\b',
            r'DN[:\s]*(\d{10,15})',
            r'Delivery\s*Note[:\s]*(\d{10,15})',
            r'DN\s*#?\s*(\d{10,15})',
        ],
        EntityType.DEALER: [
            r'dealer[:\s]+([A-Za-z0-9\s&\.]+?)(?:\s+(?:dashboard|performance|risk|sales|report)|$|,|\.)',
            r'(?:for|of|with)\s+dealer\s+([A-Za-z0-9\s&\.]+?)(?:\s+(?:dashboard|performance)|$|,|\.)',
            r'^([A-Za-z0-9\s&\.]{2,50})(?:\s+(?:dashboard|performance|report|sales|details|info|status)|$)',
        ],
        EntityType.PRODUCT: [
            r'product[:\s]+([A-Z0-9\-]+)',
            r'([A-Z]{2,3}-[0-9A-Z\-]+)',
            r'\b(HSU|HSP|HSW|HSE|HRF|HVF|HWM|HTV)[-\s]*[0-9A-Z]+\b',
        ],
        EntityType.WAREHOUSE: [
            r'warehouse[:\s]+([A-Za-z\s]+?)(?:\s+(?:dashboard|performance|report)|$|,|\.)',
            r'wh[:\s]+([A-Za-z\s]+?)(?:\s+(?:performance)|$|,)',
            r'^([A-Za-z\s]+)\s+warehouse$',
        ],
        EntityType.CITY: [
            r'city[:\s]+([A-Za-z\s]+?)(?:\s+(?:dashboard|performance|report)|$|,|\.)',
            r'in\s+([A-Za-z\s]+?)(?:\s+(?:city|region)|$|,|\.)',
            r'^([A-Za-z\s]+)\s+city$',
        ],
        EntityType.DIVISION: [
            r'division[:\s]+([A-Za-z\s]+?)(?:\s+(?:dashboard|performance)|$|,|\.)',
            r'div[:\s]+([A-Za-z\s]+?)(?:\s+(?:dashboard)|$|,)',
        ],
        EntityType.MANAGER: [
            r'manager[:\s]+([A-Za-z\s]+?)(?:\s+(?:dashboard|performance)|$|,|\.)',
            r'regional\s+manager[:\s]+([A-Za-z\s]+?)(?:\s+performance|$|,)',
        ],
        EntityType.PHONE_NUMBER: [
            r'\b(\+92|0|0092)[0-9]{10}\b',
            r'\b(03[0-9]{9})\b',
        ],
        EntityType.DATE_RANGE: [
            r'(?:last|past)\s+(\d+)\s+(day|week|month|year)s?',
            r'(?:from|between)\s+(\d{4}-\d{2}-\d{2})\s+(?:to|and)\s+(\d{4}-\d{2}-\d{2})',
            r'(?:last|previous)\s+(week|month|quarter|year)',
        ],
    }
    
    # Known cities in Pakistan (for implicit detection)
    PAKISTAN_CITIES = {
        'karachi', 'lahore', 'islamabad', 'rawalpindi', 'faisalabad',
        'multan', 'peshawar', 'quetta', 'gujranwala', 'sialkot',
        'hyderabad', 'sukkur', 'bahawalpur', 'sargodha', 'jhelum',
        'gujrat', 'sheikhupura', 'jhang', 'dera ghazi khan'
    }
    
    # Known warehouses
    WAREHOUSES = {
        'lahore', 'karachi', 'islamabad', 'rawalpindi', 'faisalabad',
        'multan', 'sukkur', 'hyderabad', 'peshawar', 'quetta', 'gujranwala',
        'lahore warehouse', 'karachi warehouse', 'islamabad warehouse'
    }
    
    def __init__(self):
        """Initialize with compiled regex patterns"""
        self.compiled_patterns = {}
        for entity_type, patterns in self.PATTERNS.items():
            self.compiled_patterns[entity_type] = [
                re.compile(pattern, re.IGNORECASE) for pattern in patterns
            ]
    
    def extract_all(self, text: str) -> Dict[EntityType, ExtractedEntity]:
        """
        Extract all entities from text.
        
        Returns:
            Dictionary of EntityType -> ExtractedEntity
        """
        entities = {}
        
        # Extract using patterns
        for entity_type, patterns in self.compiled_patterns.items():
            for pattern in patterns:
                match = pattern.search(text)
                if match:
                    groups = match.groups()
                    if groups:
                        value = groups[0].strip()
                        value = self._post_process(entity_type, value)
                        if value:
                            entities[entity_type] = ExtractedEntity(
                                type=entity_type,
                                value=value,
                                confidence=0.95
                            )
                            break
        
        # Implicit detection for cities
        self._detect_implicit_cities(text, entities)
        
        # Implicit detection for warehouses
        self._detect_implicit_warehouses(text, entities)
        
        return entities
    
    def _post_process(self, entity_type: EntityType, value: str) -> Optional[str]:
        """Post-process extracted value"""
        value = value.strip()
        
        if entity_type == EntityType.DEALER:
            # Remove common suffixes
            for suffix in ['dashboard', 'performance', 'risk', 'sales', 'report', 'details', 'info', 'status']:
                if value.lower().endswith(suffix):
                    value = value[:-len(suffix)].strip()
        
        elif entity_type == EntityType.WAREHOUSE:
            # Standardize warehouse names
            value_lower = value.lower()
            if value_lower in self.WAREHOUSES:
                value = value_lower.title()
        
        elif entity_type == EntityType.CITY:
            # Standardize city names
            value_lower = value.lower()
            if value_lower in self.PAKISTAN_CITIES:
                value = value_lower.title()
        
        return value if value else None
    
    def _detect_implicit_cities(self, text: str, entities: Dict):
        """Detect city names without explicit 'city' keyword"""
        text_lower = text.lower()
        
        for city in self.PAKISTAN_CITIES:
            if city in text_lower:
                if EntityType.CITY not in entities:
                    if any(keyword in text_lower for keyword in ['in', 'at', 'from', 'to', 'for', 'city']):
                        entities[EntityType.CITY] = ExtractedEntity(
                            type=EntityType.CITY,
                            value=city.title(),
                            confidence=0.80
                        )
                        break
    
    def _detect_implicit_warehouses(self, text: str, entities: Dict):
        """Detect warehouse names without explicit 'warehouse' keyword"""
        text_lower = text.lower()
        
        for warehouse in self.WAREHOUSES:
            if warehouse in text_lower:
                if EntityType.WAREHOUSE not in entities:
                    if any(keyword in text_lower for keyword in ['warehouse', 'wh', 'from', 'at']):
                        entities[EntityType.WAREHOUSE] = ExtractedEntity(
                            type=EntityType.WAREHOUSE,
                            value=warehouse.title(),
                            confidence=0.80
                        )
                        break
    
    # ==========================================================
    # SINGLE ENTITY EXTRACTION METHODS
    # ==========================================================
    
    def extract_dn_number(self, text: str) -> Optional[str]:
        """Extract DN number specifically"""
        for pattern in self.compiled_patterns[EntityType.DN_NUMBER]:
            match = pattern.search(text)
            if match:
                return match.group(1).strip()
        return None
    
    def extract_dealer(self, text: str) -> Optional[str]:
        """Extract dealer name specifically"""
        for pattern in self.compiled_patterns[EntityType.DEALER]:
            match = pattern.search(text)
            if match:
                value = match.group(1).strip()
                return self._post_process(EntityType.DEALER, value)
        return None
    
    def extract_product(self, text: str) -> Optional[str]:
        """Extract product code specifically"""
        text_upper = text.upper()
        for pattern in self.compiled_patterns[EntityType.PRODUCT]:
            match = pattern.search(text_upper)
            if match:
                return match.group(1).strip()
        return None
    
    def extract_warehouse(self, text: str) -> Optional[str]:
        """Extract warehouse name specifically"""
        for pattern in self.compiled_patterns[EntityType.WAREHOUSE]:
            match = pattern.search(text)
            if match:
                value = match.group(1).strip()
                return self._post_process(EntityType.WAREHOUSE, value)
        return None
    
    def extract_city(self, text: str) -> Optional[str]:
        """Extract city name specifically"""
        for pattern in self.compiled_patterns[EntityType.CITY]:
            match = pattern.search(text)
            if match:
                value = match.group(1).strip()
                return self._post_process(EntityType.CITY, value)
        
        # Implicit detection
        text_lower = text.lower()
        for city in self.PAKISTAN_CITIES:
            if city in text_lower:
                return city.title()
        
        return None
    
    def extract_date_range(self, text: str) -> Optional[Tuple[str, str]]:
        """Extract date range"""
        for pattern in self.compiled_patterns[EntityType.DATE_RANGE]:
            match = pattern.search(text)
            if match:
                groups = match.groups()
                if len(groups) == 2:
                    return ('relative', groups[0], groups[1])
                elif len(groups) == 3:
                    return ('absolute', groups[1], groups[2])
        return None
    
    def get_entity_summary(self, entities: Dict) -> Dict:
        """Get human-readable summary of extracted entities"""
        summary = {}
        for entity_type, entity in entities.items():
            summary[entity_type.value] = {
                "value": entity.value,
                "confidence": entity.confidence
            }
        return summary
