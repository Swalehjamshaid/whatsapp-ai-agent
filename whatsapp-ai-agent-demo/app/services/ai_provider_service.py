# ==========================================================
# FILE: app/services/ai_query_service.py
# VERSION: 3.0
# PURPOSE: Master Brain of the Application - Query Understanding & Routing
# ARCHITECTURE: Webhook → ai_query_service → (logistics|analytics|kpi|ai_provider)
# ==========================================================

import re
import json
from typing import Dict, Any, Optional, List, Tuple
from datetime import datetime, timedelta
from collections import defaultdict
from loguru import logger
from sqlalchemy.orm import Session

from app.config import config

# ==========================================================
# SERVICE IMPORTS
# ==========================================================

# Try to import all dependent services gracefully
LOGISTICS_AVAILABLE = False
ANALYTICS_AVAILABLE = False
KPI_AVAILABLE = False
AI_PROVIDER_AVAILABLE = False

try:
    from app.services.logistics_query_service import LogisticsQueryService
    LOGISTICS_AVAILABLE = True
    logger.info("✅ Logistics Query Service loaded")
except ImportError as e:
    logger.warning(f"⚠️ Logistics Query Service not available: {e}")

try:
    from app.services.analytics_service import AnalyticsService
    ANALYTICS_AVAILABLE = True
    logger.info("✅ Analytics Service loaded")
except ImportError as e:
    logger.warning(f"⚠️ Analytics Service not available: {e}")

try:
    from app.services.kpi_service import KPIService
    KPI_AVAILABLE = True
    logger.info("✅ KPI Service loaded")
except ImportError as e:
    logger.warning(f"⚠️ KPI Service not available: {e}")

try:
    from app.services.ai_provider_service import AIProviderService
    AI_PROVIDER_AVAILABLE = True
    logger.info("✅ AI Provider Service loaded")
except ImportError as e:
    logger.warning(f"⚠️ AI Provider Service not available: {e}")

# ==========================================================
# QUERY TYPES
# ==========================================================

class QueryType:
    DN_QUERY = "dn_query"              # Delivery Note query
    DEALER_QUERY = "dealer_query"      # Dealer information query
    WAREHOUSE_QUERY = "warehouse_query" # Warehouse status query
    KPI_QUERY = "kpi_query"            # KPI metrics query
    DASHBOARD_QUERY = "dashboard_query" # Dashboard summary query
    ANALYTICS_QUERY = "analytics_query" # Analytics/trends query
    POD_QUERY = "pod_query"            # POD specific query
    PENDING_QUERY = "pending_query"    # Pending items query
    REGION_QUERY = "region_query"      # Region-based query
    GENERAL_AI = "general_ai"          # General AI conversation
    UNKNOWN = "unknown"                # Unknown query type

# ==========================================================
# QUERY PATTERNS
# ==========================================================

PATTERNS = {
    # DN Patterns
    'dn_number': re.compile(r'\b(\d{8,15})\b', re.IGNORECASE),
    'dn_prefix': re.compile(r'(?:DN|Delivery[- ]?Note|DO|Delivery[- ]?Order)[\s:]*#?(\d{6,15})', re.IGNORECASE),
    'dn_status': re.compile(r'(?:status|check|track|where|find).*?(?:DN|delivery)', re.IGNORECASE),
    
    # Dealer Patterns
    'dealer_name': re.compile(r'(?:dealer|distributor|customer)[\s:]+([A-Za-z0-9\s&\.]+?)(?:\s+(?:in|from|of|for|$))', re.IGNORECASE),
    'dealer_code': re.compile(r'(?:DLR|DEALER)[\s:]*#?(\d{4,8})', re.IGNORECASE),
    
    # Warehouse Patterns
    'warehouse_name': re.compile(r'(?:warehouse|godown|stock point)[\s:]+([A-Za-z\s]+?)(?:\s+(?:in|from|$))', re.IGNORECASE),
    'warehouse_city': re.compile(r'(?:warehouse|stock).*?(?:in|at|of)\s+([A-Za-z\s]+?)(?:\?|$)', re.IGNORECASE),
    
    # Region/City Patterns
    'city': re.compile(r'\b(in|at|for|of)\s+([A-Z][a-z]+(?:\s+[A-Z][a-z]+)*)\b', re.IGNORECASE),
    'region': re.compile(r'\b(region|zone|area)[\s:]+([A-Za-z\s]+?)(?:\s|$)', re.IGNORECASE),
    
    # Query Intent Patterns
    'pending': re.compile(r'\b(pending|outstanding|due|not yet|remaining)\b', re.IGNORECASE),
    'pod': re.compile(r'\b(POD|proof of delivery|delivery proof|signed)\b', re.IGNORECASE),
    'aging': re.compile(r'\b(aging|old|overdue|delay|late)\b', re.IGNORECASE),
    'kpi': re.compile(r'\b(KPI|performance|metric|target|achievement)\b', re.IGNORECASE),
    'dashboard': re.compile(r'\b(dashboard|summary|overview|total|all)\b', re.IGNORECASE),
    'analytics': re.compile(r'\b(analytics|trend|pattern|analysis|insight)\b', re.IGNORECASE),
    'top': re.compile(r'\b(top|best|highest|maximum|leading)\b', re.IGNORECASE),
    'bottom': re.compile(r'\b(bottom|worst|lowest|minimum|least)\b', re.IGNORECASE),
    'today': re.compile(r'\b(today|current|latest)\b', re.IGNORECASE),
    'week': re.compile(r'\b(this week|weekly|last 7 days)\b', re.IGNORECASE),
    'month': re.compile(r'\b(this month|monthly|last 30 days)\b', re.IGNORECASE),
    
    # Product Patterns
    'product_name': re.compile(r'\b(product|item|sku)[\s:]+([A-Z0-9\-]+)\b', re.IGNORECASE),
}

# ==========================================================
# WHATSAPP FORMATTING HELPER
# ==========================================================

class WhatsAppFormatter:
    """Format responses for WhatsApp display"""
    
    @staticmethod
    def bold(text: str) -> str:
        return f"*{text}*"
    
    @staticmethod
    def italic(text: str) -> str:
        return f"_{text}_"
    
    @staticmethod
    def strikethrough(text: str) -> str:
        return f"~{text}~"
    
    @staticmethod
    def code(text: str) -> str:
        return f"```{text}```"
    
    @staticmethod
    def bullet_list(items: List[str]) -> str:
        """Format bullet list for WhatsApp"""
        if not items:
            return ""
        return "\n".join([f"• {item}" for item in items])
    
    @staticmethod
    def numbered_list(items: List[str]) -> str:
        """Format numbered list for WhatsApp"""
        if not items:
            return ""
        return "\n".join([f"{i+1}. {item}" for i, item in enumerate(items)])
    
    @staticmethod
    def simple_table(headers: List[str], rows: List[List[str]], max_width: int = 30) -> str:
        """
        Create a simple ASCII table for WhatsApp
        
        Args:
            headers: Column headers
            rows: List of row data
            max_width: Maximum width per column
        
        Returns:
            Formatted table string
        """
        if not rows:
            return "No data available"
        
        # Calculate column widths
        col_widths = []
        for i, header in enumerate(headers):
            max_content = max([len(str(row[i])) for row in rows] + [len(header)])
            col_widths.append(min(max_content, max_width))
        
        # Create separator
        separator = "+" + "+".join(["-" * (w + 2) for w in col_widths]) + "+"
        
        # Create header
        header_line = "|"
        for i, header in enumerate(headers):
            header_line += f" {header:<{col_widths[i]}} |"
        
        # Create rows
        lines = [separator, header_line, separator]
        for row in rows:
            row_line = "|"
            for i, cell in enumerate(row):
                cell_str = str(cell)[:col_widths[i]]
                row_line += f" {cell_str:<{col_widths[i]}} |"
            lines.append(row_line)
        lines.append(separator)
        
        return "\n".join(lines)
    
    @staticmethod
    def key_value_table(data: Dict[str, Any], title: str = None) -> str:
        """Format key-value pairs as a table"""
        if not data:
            return "No data available"
        
        lines = []
        if title:
            lines.append(WhatsAppFormatter.bold(title))
            lines.append("─" * min(len(title), 40))
        
        max_key_len = max([len(str(k)) for k in data.keys()])
        
        for key, value in data.items():
            key_str = str(key).replace("_", " ").title()
            lines.append(f"{key_str:<{max_key_len+2}}: {value}")
        
        return "\n".join(lines)
    
    @staticmethod
    def executive_summary(title: str, metrics: Dict[str, Any], highlights: List[str] = None) -> str:
        """Create executive summary format"""
        lines = [
            WhatsAppFormatter.bold(f"📊 {title}"),
            "=" * min(len(title) + 4, 50),
            ""
        ]
        
        # Add metrics
        for key, value in metrics.items():
            key_display = key.replace("_", " ").title()
            lines.append(f"• {key_display}: {WhatsAppFormatter.bold(str(value))}")
        
        # Add highlights
        if highlights:
            lines.append("")
            lines.append(WhatsAppFormatter.bold("Key Highlights:"))
            for highlight in highlights:
                lines.append(f"  ✓ {highlight}")
        
        return "\n".join(lines)
    
    @staticmethod
    def dn_intelligence_report(dn_data: Dict[str, Any]) -> str:
        """Format DN Intelligence Report"""
        lines = [
            WhatsAppFormatter.bold(f"📋 DN INTELLIGENCE REPORT"),
            "=" * 35,
            ""
        ]
        
        # Basic info
        lines.append(WhatsAppFormatter.bold(f"DN Number: {dn_data.get('dn_number', 'N/A')}"))
        lines.append(f"Date: {dn_data.get('date', 'N/A')}")
        lines.append(f"Status: {dn_data.get('status', 'N/A')}")
        lines.append("")
        
        # Customer info
        lines.append(WhatsAppFormatter.bold("Customer Details:"))
        lines.append(f"  Name: {dn_data.get('customer_name', 'N/A')}")
        lines.append(f"  City: {dn_data.get('city', 'N/A')}")
        lines.append(f"  Region: {dn_data.get('region', 'N/A')}")
        lines.append("")
        
        # Order details
        lines.append(WhatsAppFormatter.bold("Order Details:"))
        lines.append(f"  Amount: {dn_data.get('amount', 'N/A')}")
        lines.append(f"  Items: {dn_data.get('items_count', 'N/A')}")
        lines.append(f"  Weight: {dn_data.get('weight', 'N/A')}")
        lines.append("")
        
        # Tracking
        lines.append(WhatsAppFormatter.bold("Tracking:"))
        lines.append(f"  Pending: {dn_data.get('pending_items', 'N/A')}")
        lines.append(f"  POD Status: {dn_data.get('pod_status', 'N/A')}")
        
        if dn_data.get('aging_days'):
            aging = dn_data['aging_days']
            aging_icon = "🟢" if aging <= 3 else "🟡" if aging <= 7 else "🔴"
            lines.append(f"  Aging: {aging_icon} {aging} days")
        
        return "\n".join(lines)

# ==========================================================
# MAIN AI QUERY SERVICE
# ==========================================================

class AIQueryService:
    """
    Master Brain of the Application
    Handles query understanding, routing, and response generation
    """
    
    def __init__(self, db: Session):
        self.db = db
        self.formatter = WhatsAppFormatter()
        
        # Initialize sub-services if available
        self.logistics_service = LogisticsQueryService(db) if LOGISTICS_AVAILABLE else None
        self.analytics_service = AnalyticsService(db) if ANALYTICS_AVAILABLE else None
        self.kpi_service = KPIService(db) if KPI_AVAILABLE else None
        self.ai_provider = AIProviderService() if AI_PROVIDER_AVAILABLE else None
        
        logger.info("AI Query Service initialized (Master Brain v3.0)")
    
    # ==========================================================
    # QUERY UNDERSTANDING
    # ==========================================================
    
    def detect_query_type(self, query: str) -> str:
        """
        Detect the type of query from user input
        
        Args:
            query: User's query text
        
        Returns:
            Query type constant
        """
        query_lower = query.lower().strip()
        
        # Check for DN queries (highest priority)
        if PATTERNS['dn_number'].search(query):
            logger.debug(f"Detected DN number in query")
            return QueryType.DN_QUERY
        
        if PATTERNS['dn_prefix'].search(query):
            logger.debug(f"Detected DN prefix in query")
            return QueryType.DN_QUERY
        
        # Check for KPI queries
        if PATTERNS['kpi'].search(query_lower):
            logger.debug(f"Detected KPI query")
            return QueryType.KPI_QUERY
        
        # Check for dashboard/summary queries
        if PATTERNS['dashboard'].search(query_lower):
            logger.debug(f"Detected dashboard query")
            return QueryType.DASHBOARD_QUERY
        
        # Check for analytics queries
        if PATTERNS['analytics'].search(query_lower):
            logger.debug(f"Detected analytics query")
            return QueryType.ANALYTICS_QUERY
        
        # Check for pending + POD combined
        if PATTERNS['pending'].search(query_lower) and PATTERNS['pod'].search(query_lower):
            logger.debug(f"Detected pending POD query")
            return QueryType.POD_QUERY
        
        # Check for general POD query
        if PATTERNS['pod'].search(query_lower):
            logger.debug(f"Detected POD query")
            return QueryType.POD_QUERY
        
        # Check for pending items query
        if PATTERNS['pending'].search(query_lower):
            logger.debug(f"Detected pending query")
            return QueryType.PENDING_QUERY
        
        # Check for warehouse query
        if 'warehouse' in query_lower or 'stock' in query_lower:
            logger.debug(f"Detected warehouse query")
            return QueryType.WAREHOUSE_QUERY
        
        # Check for dealer query
        if 'dealer' in query_lower or 'distributor' in query_lower:
            logger.debug(f"Detected dealer query")
            return QueryType.DEALER_QUERY
        
        # Check for region query
        if PATTERNS['region'].search(query) or 'region' in query_lower:
            logger.debug(f"Detected region query")
            return QueryType.REGION_QUERY
        
        # Default to general AI
        logger.debug(f"Defaulting to general AI query")
        return QueryType.GENERAL_AI
    
    def extract_dn_number(self, query: str) -> Optional[str]:
        """Extract DN number from query"""
        # Try direct DN number pattern
        match = PATTERNS['dn_number'].search(query)
        if match:
            return match.group(1)
        
        # Try DN prefix pattern
        match = PATTERNS['dn_prefix'].search(query)
        if match:
            return match.group(1)
        
        return None
    
    def extract_dealer_info(self, query: str) -> Dict[str, Optional[str]]:
        """Extract dealer information from query"""
        result = {
            'name': None,
            'code': None,
            'city': None
        }
        
        # Extract dealer name
        match = PATTERNS['dealer_name'].search(query)
        if match:
            result['name'] = match.group(1).strip()
        
        # Extract dealer code
        match = PATTERNS['dealer_code'].search(query)
        if match:
            result['code'] = match.group(1)
        
        # Extract city if present
        match = PATTERNS['city'].search(query)
        if match:
            result['city'] = match.group(2).strip()
        
        return result
    
    def extract_warehouse_info(self, query: str) -> Dict[str, Optional[str]]:
        """Extract warehouse information from query"""
        result = {
            'name': None,
            'city': None
        }
        
        match = PATTERNS['warehouse_name'].search(query)
        if match:
            result['name'] = match.group(1).strip()
        
        match = PATTERNS['warehouse_city'].search(query)
        if match:
            result['city'] = match.group(1).strip()
        
        return result
    
    def extract_region_city(self, query: str) -> Dict[str, Optional[str]]:
        """Extract region or city from query"""
        result = {
            'region': None,
            'city': None
        }
        
        # Extract region
        match = PATTERNS['region'].search(query)
        if match:
            result['region'] = match.group(2).strip()
        
        # Extract city
        match = PATTERNS['city'].search(query)
        if match:
            result['city'] = match.group(2).strip()
        
        return result
    
    def extract_time_period(self, query: str) -> Dict[str, Any]:
        """Extract time period from query"""
        query_lower = query.lower()
        
        if PATTERNS['today'].search(query_lower):
            return {'type': 'today', 'start_date': datetime.now().date(), 'end_date': datetime.now().date()}
        elif PATTERNS['week'].search(query_lower):
            today = datetime.now().date()
            start = today - timedelta(days=today.weekday())
            return {'type': 'week', 'start_date': start, 'end_date': today}
        elif PATTERNS['month'].search(query_lower):
            today = datetime.now().date()
            start = today.replace(day=1)
            return {'type': 'month', 'start_date': start, 'end_date': today}
        
        return {'type': 'all', 'start_date': None, 'end_date': None}
    
    # ==========================================================
    # QUERY ROUTING
    # ==========================================================
    
    def route_query(self, query_type: str, extracted_data: Dict[str, Any], user_id: str) -> Dict[str, Any]:
        """
        Route query to appropriate service based on type
        
        Args:
            query_type: Type of query detected
            extracted_data: Extracted entities from query
            user_id: User identifier
        
        Returns:
            Service response
        """
        
        logger.info(f"Routing query type: {query_type}")
        
        # DN Query
        if query_type == QueryType.DN_QUERY:
            dn_number = extracted_data.get('dn_number')
            if dn_number and self.logistics_service:
                return self._handle_dn_query(dn_number)
            elif self.ai_provider:
                return self._handle_general_ai(extracted_data.get('original_query', ''), user_id)
        
        # Dealer Query
        elif query_type == QueryType.DEALER_QUERY:
            dealer_info = extracted_data.get('dealer_info', {})
            if self.logistics_service:
                return self._handle_dealer_query(dealer_info)
        
        # Warehouse Query
        elif query_type == QueryType.WAREHOUSE_QUERY:
            warehouse_info = extracted_data.get('warehouse_info', {})
            if self.logistics_service:
                return self._handle_warehouse_query(warehouse_info)
        
        # KPI Query
        elif query_type == QueryType.KPI_QUERY:
            time_period = extracted_data.get('time_period', {})
            if self.kpi_service:
                return self._handle_kpi_query(time_period)
        
        # Dashboard Query
        elif query_type == QueryType.DASHBOARD_QUERY:
            if self.kpi_service:
                return self._handle_dashboard_query()
        
        # Analytics Query
        elif query_type == QueryType.ANALYTICS_QUERY:
            time_period = extracted_data.get('time_period', {})
            if self.analytics_service:
                return self._handle_analytics_query(time_period)
        
        # POD Query
        elif query_type == QueryType.POD_QUERY:
            region = extracted_data.get('region')
            if self.logistics_service:
                return self._handle_pod_query(region)
        
        # Pending Query
        elif query_type == QueryType.PENDING_QUERY:
            region = extracted_data.get('region')
            if self.logistics_service:
                return self._handle_pending_query(region)
        
        # Region Query
        elif query_type == QueryType.REGION_QUERY:
            region = extracted_data.get('region')
            if self.logistics_service:
                return self._handle_region_query(region)
        
        # General AI
        elif query_type == QueryType.GENERAL_AI and self.ai_provider:
            return self._handle_general_ai(extracted_data.get('original_query', ''), user_id)
        
        # Fallback
        return self._fallback_response(query_type)
    
    # ==========================================================
    # QUERY HANDLERS
    # ==========================================================
    
    def _handle_dn_query(self, dn_number: str) -> Dict[str, Any]:
        """Handle DN number query"""
        try:
            # Get DN intelligence
            intelligence = self.logistics_service.get_complete_dn_intelligence(dn_number)
            
            if intelligence and 'error' not in intelligence:
                formatted_response = self.formatter.dn_intelligence_report(intelligence)
                return {
                    'success': True,
                    'response': formatted_response,
                    'data': intelligence,
                    'query_type': QueryType.DN_QUERY
                }
            else:
                return {
                    'success': False,
                    'response': f"❌ DN {dn_number} not found. Please check the number and try again.",
                    'error': intelligence.get('error') if intelligence else 'Not found'
                }
        except Exception as e:
            logger.error(f"DN query error: {e}")
            return {
                'success': False,
                'response': f"⚠️ Error retrieving DN {dn_number}. Please try again later."
            }
    
    def _handle_dealer_query(self, dealer_info: Dict) -> Dict[str, Any]:
        """Handle dealer query"""
        try:
            dealer_name = dealer_info.get('name')
            if not dealer_name:
                return {
                    'success': False,
                    'response': "Please specify the dealer name. Example: 'Show me ABC Traders performance'"
                }
            
            # Get dealer performance
            performance = self.logistics_service.get_dealer_performance(dealer_name)
            
            if performance:
                metrics = {
                    'Total DNs': performance.get('total_dns', 0),
                    'Pending': performance.get('pending_count', 0),
                    'Completed': performance.get('completed_count', 0),
                    'Avg Aging': f"{performance.get('avg_aging', 0):.1f} days",
                    'Total Value': f"₹{performance.get('total_value', 0):,.0f}"
                }
                
                response = self.formatter.executive_summary(
                    title=f"Dealer Performance: {dealer_name}",
                    metrics=metrics
                )
                
                return {
                    'success': True,
                    'response': response,
                    'data': performance
                }
            else:
                return {
                    'success': False,
                    'response': f"Dealer '{dealer_name}' not found in the system."
                }
        except Exception as e:
            logger.error(f"Dealer query error: {e}")
            return {
                'success': False,
                'response': f"⚠️ Error retrieving dealer information."
            }
    
    def _handle_warehouse_query(self, warehouse_info: Dict) -> Dict[str, Any]:
        """Handle warehouse query"""
        try:
            warehouse_name = warehouse_info.get('name')
            if not warehouse_name:
                return {
                    'success': False,
                    'response': "Please specify the warehouse name. Example: 'What's the stock at Mumbai warehouse?'"
                }
            
            # Get warehouse status
            status = self.logistics_service.get_warehouse_status(warehouse_name)
            
            if status:
                metrics = {
                    'Total Stock': status.get('total_stock', 0),
                    'Pending Dispatch': status.get('pending_dispatch', 0),
                    'In Transit': status.get('in_transit', 0),
                    'Capacity Used': f"{status.get('capacity_used', 0)}%"
                }
                
                response = self.formatter.key_value_table(metrics, title=f"Warehouse: {warehouse_name}")
                
                return {
                    'success': True,
                    'response': response,
                    'data': status
                }
            else:
                return {
                    'success': False,
                    'response': f"Warehouse '{warehouse_name}' not found."
                }
        except Exception as e:
            logger.error(f"Warehouse query error: {e}")
            return {
                'success': False,
                'response': f"⚠️ Error retrieving warehouse status."
            }
    
    def _handle_kpi_query(self, time_period: Dict) -> Dict[str, Any]:
        """Handle KPI metrics query"""
        try:
            kpis = self.kpi_service.get_all_kpis(time_period)
            
            if kpis:
                response = self.formatter.executive_summary(
                    title="KPI Dashboard",
                    metrics=kpis.get('metrics', {}),
                    highlights=kpis.get('highlights', [])
                )
                
                return {
                    'success': True,
                    'response': response,
                    'data': kpis
                }
            else:
                return {
                    'success': False,
                    'response': "Unable to retrieve KPI data at this time."
                }
        except Exception as e:
            logger.error(f"KPI query error: {e}")
            return {
                'success': False,
                'response': f"⚠️ Error retrieving KPI metrics."
            }
    
    def _handle_dashboard_query(self) -> Dict[str, Any]:
        """Handle dashboard/summary query"""
        try:
            dashboard = self.kpi_service.get_dashboard_summary()
            
            if dashboard:
                # Create executive summary
                metrics = {
                    'Total DNs': dashboard.get('total_dns', 0),
                    'Pending PODs': dashboard.get('pending_pods', 0),
                    'Completion Rate': f"{dashboard.get('completion_rate', 0)}%",
                    'Total Value': f"₹{dashboard.get('total_value', 0):,.0f}",
                    'Active Dealers': dashboard.get('active_dealers', 0)
                }
                
                highlights = [
                    f"Top Dealer: {dashboard.get('top_dealer', 'N/A')}",
                    f"Aging: {dashboard.get('avg_aging', 0):.1f} days"
                ]
                
                response = self.formatter.executive_summary(
                    title="Executive Dashboard",
                    metrics=metrics,
                    highlights=highlights
                )
                
                return {
                    'success': True,
                    'response': response,
                    'data': dashboard
                }
            else:
                return {
                    'success': False,
                    'response': "Unable to retrieve dashboard data."
                }
        except Exception as e:
            logger.error(f"Dashboard query error: {e}")
            return {
                'success': False,
                'response': f"⚠️ Error retrieving dashboard."
            }
    
    def _handle_pod_query(self, region: Optional[str] = None) -> Dict[str, Any]:
        """Handle POD-specific query"""
        try:
            pod_data = self.logistics_service.get_pod_status(region)
            
            if pod_data:
                if region:
                    response = f"📋 *POD Status for {region}*\n\n"
                else:
                    response = "📋 *POD Status Overview*\n\n"
                
                response += self.formatter.bullet_list([
                    f"Total Pending PODs: {pod_data.get('pending_count', 0)}",
                    f"Completed Today: {pod_data.get('completed_today', 0)}",
                    f"Average Aging: {pod_data.get('avg_aging', 0):.1f} days",
                    f"Top Pending Dealer: {pod_data.get('top_pending_dealer', 'N/A')}"
                ])
                
                return {
                    'success': True,
                    'response': response,
                    'data': pod_data
                }
            else:
                return {
                    'success': False,
                    'response': "Unable to retrieve POD status."
                }
        except Exception as e:
            logger.error(f"POD query error: {e}")
            return {
                'success': False,
                'response': f"⚠️ Error retrieving POD status."
            }
    
    def _handle_pending_query(self, region: Optional[str] = None) -> Dict[str, Any]:
        """Handle pending items query"""
        try:
            pending_data = self.logistics_service.get_pending_items(region)
            
            if pending_data:
                response_lines = ["⏳ *Pending Items Report*", ""]
                
                if region:
                    response_lines.append(f"Region/City: {region}")
                    response_lines.append("")
                
                response_lines.extend([
                    f"• Total Pending: {pending_data.get('total_pending', 0)}",
                    f"• High Priority: {pending_data.get('high_priority', 0)}",
                    f"• Medium Priority: {pending_data.get('medium_priority', 0)}",
                    f"• Low Priority: {pending_data.get('low_priority', 0)}",
                    "",
                    f"📊 *Top 5 Pending by Dealer:*",
                ])
                
                top_dealers = pending_data.get('top_dealers', [])
                for i, dealer in enumerate(top_dealers[:5], 1):
                    response_lines.append(f"  {i}. {dealer['name']}: {dealer['pending_count']} items")
                
                response = "\n".join(response_lines)
                
                return {
                    'success': True,
                    'response': response,
                    'data': pending_data
                }
            else:
                return {
                    'success': False,
                    'response': "No pending items found." if not region else f"No pending items found for {region}."
                }
        except Exception as e:
            logger.error(f"Pending query error: {e}")
            return {
                'success': False,
                'response': f"⚠️ Error retrieving pending items."
            }
    
    def _handle_analytics_query(self, time_period: Dict) -> Dict[str, Any]:
        """Handle analytics query"""
        try:
            analytics = self.analytics_service.get_trends(time_period)
            
            if analytics:
                response = self.formatter.executive_summary(
                    title="Analytics Report",
                    metrics=analytics.get('summary', {}),
                    highlights=analytics.get('insights', [])
                )
                
                return {
                    'success': True,
                    'response': response,
                    'data': analytics
                }
            else:
                return {
                    'success': False,
                    'response': "Unable to retrieve analytics data."
                }
        except Exception as e:
            logger.error(f"Analytics query error: {e}")
            return {
                'success': False,
                'response': f"⚠️ Error retrieving analytics."
            }
    
    def _handle_region_query(self, region: Optional[str] = None) -> Dict[str, Any]:
        """Handle region-based query"""
        try:
            region_data = self.logistics_service.get_region_performance(region)
            
            if region_data:
                metrics = {
                    'Total DNs': region_data.get('total_dns', 0),
                    'Pending': region_data.get('pending_count', 0),
                    'Completed': region_data.get('completed_count', 0),
                    'Total Value': f"₹{region_data.get('total_value', 0):,.0f}",
                    'Top Dealer': region_data.get('top_dealer', 'N/A')
                }
                
                response = self.formatter.key_value_table(
                    metrics,
                    title=f"Region Performance: {region or 'All Regions'}"
                )
                
                return {
                    'success': True,
                    'response': response,
                    'data': region_data
                }
            else:
                return {
                    'success': False,
                    'response': f"No data found for region: {region}" if region else "No region data available."
                }
        except Exception as e:
            logger.error(f"Region query error: {e}")
            return {
                'success': False,
                'response': f"⚠️ Error retrieving region data."
            }
    
    def _handle_general_ai(self, query: str, user_id: str) -> Dict[str, Any]:
        """Handle general AI conversation"""
        try:
            ai_response = self.ai_provider.chat(query, user_id)
            
            return {
                'success': True,
                'response': ai_response,
                'query_type': QueryType.GENERAL_AI
            }
        except Exception as e:
            logger.error(f"AI provider error: {e}")
            return {
                'success': False,
                'response': "🤖 I'm here to help! You can ask me about:\n\n• DN status (Send any 10+ digit number)\n• Dealer performance\n• Warehouse stock\n• Pending PODs\n• KPI metrics\n• Regional performance\n\nHow can I assist you today?"
            }
    
    def _fallback_response(self, query_type: str) -> Dict[str, Any]:
        """Fallback response when no service is available"""
        return {
            'success': False,
            'response': "I understand your query, but the required service is temporarily unavailable. Please try again in a few moments.\n\nYou can ask me about:\n• DN Status\n• Dealer Performance\n• Warehouse Status\n• Pending PODs\n• KPI Metrics"
        }
    
    # ==========================================================
    # RESPONSE GENERATION
    # ==========================================================
    
    def generate_response(self, query: str, user_id: str = "guest") -> str:
        """
        Main entry point - Generate response for user query
        
        This is the primary method called by webhook.py
        
        Args:
            query: User's query text
            user_id: User identifier
        
        Returns:
            Formatted response string for WhatsApp
        """
        
        logger.info(f"Generating response for user: {user_id}, query: {query[:100]}")
        
        # Step 1: Detect query type
        query_type = self.detect_query_type(query)
        logger.info(f"Detected query type: {query_type}")
        
        # Step 2: Extract entities
        extracted_data = {
            'original_query': query,
            'dn_number': self.extract_dn_number(query),
            'dealer_info': self.extract_dealer_info(query),
            'warehouse_info': self.extract_warehouse_info(query),
            'region_city': self.extract_region_city(query),
            'time_period': self.extract_time_period(query),
            'region': self.extract_region_city(query).get('region') or self.extract_region_city(query).get('city')
        }
        
        # Step 3: Route query to appropriate handler
        result = self.route_query(query_type, extracted_data, user_id)
        
        # Step 4: Return formatted response
        if result.get('success'):
            response = result.get('response')
            # Add footer for better user experience
            if len(response) < 1500:
                response += "\n\n" + self.formatter.italic("Reply with 'help' for available commands")
            return response
        else:
            return result.get('response', "I'm having trouble processing your request. Please try again.")
    
    def format_whatsapp_response(self, data: Any, format_type: str = "auto") -> str:
        """
        Format data for WhatsApp display
        
        Args:
            data: Data to format
            format_type: Type of formatting (auto, table, list, summary)
        
        Returns:
            Formatted string
        """
        if isinstance(data, dict):
            if format_type == "summary":
                return self.formatter.executive_summary("Report", data)
            else:
                return self.formatter.key_value_table(data)
        elif isinstance(data, list):
            if format_type == "numbered":
                return self.formatter.numbered_list(data)
            else:
                return self.formatter.bullet_list(data)
        else:
            return str(data)


# ==========================================================
# COMPATIBILITY FUNCTIONS (Used by webhook.py)
# ==========================================================

def process_whatsapp_query(query: str, db: Session, phone_number: str, user_id: str = "guest") -> str:
    """
    Main entry point for WhatsApp queries
    
    Args:
        query: User's query text
        db: Database session
        phone_number: User's phone number
        user_id: User identifier
    
    Returns:
        Formatted response for WhatsApp
    """
    try:
        service = AIQueryService(db)
        response = service.generate_response(query, user_id or phone_number)
        return response
    except Exception as e:
        logger.exception(f"Error processing query: {e}")
        return "⚠️ Sorry, I encountered an error processing your request. Please try again later."


# ==========================================================
# INITIALIZATION LOG
# ==========================================================

logger.info("=" * 60)
logger.info("🤖 AI Query Service v3.0 - Master Brain Loaded")
logger.info(f"   Logistics Service: {'✅' if LOGISTICS_AVAILABLE else '❌'}")
logger.info(f"   Analytics Service: {'✅' if ANALYTICS_AVAILABLE else '❌'}")
logger.info(f"   KPI Service: {'✅' if KPI_AVAILABLE else '❌'}")
logger.info(f"   AI Provider: {'✅' if AI_PROVIDER_AVAILABLE else '❌'}")
logger.info("=" * 60)
