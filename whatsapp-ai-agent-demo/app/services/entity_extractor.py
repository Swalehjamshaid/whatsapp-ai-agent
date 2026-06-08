# ==========================================================
# FILE: app/services/entity_extractor.py
# ==========================================================

import re
from enum import Enum
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass


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
    Entity extraction engine.
    Extracts structured entities from natural language.
    """
    
    # Entity patterns
    PATTERNS = {
        EntityType.DN_NUMBER: [
            r'\b(\d{6,15})\b',
            r'DN[:\s]*(\d{6,15})',
            r'Delivery\s*Note[:\s]*(\d{6,15})',
        ],
        EntityType.DEALER: [
            r'dealer[:\s]+([A-Za-z0-9\s&\.]+?)(?:\s+(?:dashboard|performance|risk|sales|report)|$|,|\.)',
            r'(?:for|of|with)\s+dealer\s+([A-Za-z0-9\s&\.]+?)(?:\s+(?:dashboard|performance)|$|,|\.)',
        ],
        EntityType.PRODUCT: [
            r'product[:\s]+([A-Z0-9\-]+)',
            r'([A-Z]{2,3}-[0-9A-Z\-]+)',
            r'\b(HSU|HSP|HSW|HSE|HRF|HVF)[-\s]*[0-9A-Z]+\b',
        ],
        EntityType.WAREHOUSE: [
            r'warehouse[:\s]+([A-Za-z\s]+?)(?:\s+(?:dashboard|performance|report)|$|,|\.)',
            r'wh[:\s]+([A-Za-z\s]+?)(?:\s+(?:performance)|$|,)',
        ],
        EntityType.CITY: [
            r'city[:\s]+([A-Za-z\s]+?)(?:\s+(?:dashboard|performance|report)|$|,|\.)',
            r'in\s+([A-Za-z\s]+?)(?:\s+(?:city|region)|$|,|\.)',
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
        ],
    }
    
    # City names (Pakistan major cities)
    PAKISTAN_CITIES = {
        'karachi', 'lahore', 'islamabad', 'rawalpindi', 'faisalabad',
        'multan', 'peshawar', 'quetta', 'gujranwala', 'sialkot',
        'hyderabad', 'sukkur', 'bahawalpur', 'sargodha', 'jhelum'
    }
    
    # Warehouse names
    WAREHOUSES = {
        'lahore', 'karachi', 'islamabad', 'faisalabad', 'multan',
        'sukkur', 'hyderabad', 'peshawar', 'quetta', 'gujranwala'
    }
    
    def __init__(self):
        # Compile all patterns
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
                    # Get the captured group
                    groups = match.groups()
                    if groups:
                        value = groups[0].strip()
                        
                        # Post-process value
                        value = self._post_process(entity_type, value)
                        
                        if value:
                            entities[entity_type] = ExtractedEntity(
                                type=entity_type,
                                value=value,
                                confidence=0.95
                            )
                            break  # Take first match for this entity type
        
        # Special handling: detect cities without "city" keyword
        self._detect_implicit_cities(text, entities)
        
        # Special handling: detect products by pattern
        self._detect_product_patterns(text, entities)
        
        return entities
    
    def _post_process(self, entity_type: EntityType, value: str) -> Optional[str]:
        """Post-process extracted value"""
        value = value.strip()
        
        if entity_type == EntityType.DEALER:
            # Remove common suffixes
            for suffix in ['dashboard', 'performance', 'risk', 'sales', 'report']:
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
                # Check if not already extracted as city
                if EntityType.CITY not in entities:
                    # Check if it's likely a city reference
                    if any(keyword in text_lower for keyword in ['in', 'at', 'from', 'to', 'for']):
                        entities[EntityType.CITY] = ExtractedEntity(
                            type=EntityType.CITY,
                            value=city.title(),
                            confidence=0.80
                        )
                        break
    
    def _detect_product_patterns(self, text: str, entities: Dict):
        """Detect product codes using specific patterns"""
        if EntityType.PRODUCT in entities:
            return
        
        # Look for Haier product patterns
        haier_pattern = re.compile(r'\b(H[A-Z]{2}-\d{2,}[A-Z0-9]*)\b', re.IGNORECASE)
        match = haier_pattern.search(text.upper())
        if match:
            entities[EntityType.PRODUCT] = ExtractedEntity(
                type=EntityType.PRODUCT,
                value=match.group(1),
                confidence=0.90
            )
    
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
        for pattern in self.compiled_patterns[EntityType.PRODUCT]:
            match = pattern.search(text.upper())
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
                    # Relative range: "last 30 days"
                    days = int(groups[0])
                    unit = groups[1]
                    return ('relative', f"{days} {unit}")
                elif len(groups) == 3:
                    # Absolute range: "from 2024-01-01 to 2024-12-31"
                    return ('absolute', groups[1], groups[2])
        return None
