# ==========================================================
# FILE: app/services/ai_query_service.py
# ==========================================================
# COMPLETE AI QUERY SERVICE - PRODUCTION READY
# IMPROVED: Question classification, AI detection, Logistics routing

from typing import Dict, Any, List, Optional, Tuple
from datetime import datetime
import re
import json
import time

from sqlalchemy.orm import Session
from loguru import logger

from app.models import AIResponseLog
from app.config import config
from app.services.analytics_service import AnalyticsService
from app.services.logistics_query_service import LogisticsQueryService

# Safe import for AI provider
try:
    from app.services.ai_provider_service import ai_provider_service
    AI_PROVIDER_AVAILABLE = True
except ImportError as e:
    logger.warning(f"AI Provider Service not available: {e}")
    AI_PROVIDER_AVAILABLE = False
    ai_provider_service = None


# ======================================================
# IMPROVED QUESTION CLASSIFIER
# ======================================================

class QuestionClassifier:
    """Classify questions by type before AI processing"""
    
    # PHASE 2: General AI Keywords
    GENERAL_AI_KEYWORDS = [
        "what", "why", "how", "when", "where", "who",
        "tell me", "explain", "describe", "write", "create",
        "joke", "story", "poem", "python", "code", "programming",
        "ai", "deepseek", "chatgpt", "weather", "news",
        "who is", "what is", "how to", "why is", "when will"
    ]
    
    # PHASE 3: Logistics Detection Keywords
    LOGISTICS_KEYWORDS = [
        "dealer", "customer", "delivery", "dispatch", "shipment",
        "warehouse", "godown", "stock", "inventory", "logistics",
        "dn", "delivery note", "pod", "pending", "backlog",
        "product", "material", "model", "sku", "item",
        "karachi", "lahore", "islamabad", "multan", "faisalabad",
        "hyderabad", "peshawar", "quetta", "rawalpindi",
        "situation", "performance", "status", "aging", "risk",
        "ceo", "executive", "dashboard", "kpi", "ranking"
    ]
    
    QUESTION_TYPES = {
        "DEALER": {
            "keywords": [
                "dealer", "customer", "dealer dashboard", "dealer summary",
                "dealer performance", "dealer score", "dealer rating",
                "show dealer", "tell me about dealer"
            ],
            "patterns": [
                r'(?:dealer|customer|for|of|show)\s+([A-Za-z0-9\s&]+)',
                r'(?:dashboard|performance|summary|details)\s+(?:for|of)\s+([A-Za-z0-9\s&]+)'
            ]
        },
        "DN": {
            "keywords": ["dn", "delivery note", "delivery number"],
            "patterns": [r'\b(\d{8,15})\b']
        },
        "PRODUCT": {
            "keywords": ["product", "material", "model", "sku", "item"],
            "patterns": [r'(?:product|material|model)\s+([A-Za-z0-9\-]+)']
        },
        "WAREHOUSE": {
            "keywords": ["warehouse", "godown", "stock location", "storage"],
            "patterns": [r'(?:warehouse|godown)\s+([A-Za-z0-9]+)']
        },
        # PHASE 6: Improved City Detection
        "CITY": {
            "keywords": [
                "city", "location", "region", "area",
                "situation", "status", "performance", "delivery status",
                "karachi", "lahore", "islamabad", "multan", "faisalabad",
                "hyderabad", "peshawar", "quetta", "rawalpindi", "gujranwala",
                "sialkot", "bahawalpur", "sukkur", "larkana"
            ],
            "patterns": [
                r'(?:in|for|at)\s+([A-Za-z\s]+?)(?:\s+only|\s+$|\.|\?|$)',
                r'(?:karachi|lahore|islamabad|multan|faisalabad|hyderabad|peshawar|quetta)'
            ]
        },
        "EXECUTIVE": {
            "keywords": [
                "ceo", "executive", "command center", "executive summary",
                "ceo dashboard", "what should i focus", "overview",
                "dashboard", "kpi", "performance report"
            ],
            "patterns": []
        },
        "COMPARISON": {
            "keywords": ["compare", "versus", "vs", "difference between", "better", "worse"],
            "patterns": [r'compare\s+([A-Za-z0-9\s]+)\s+(?:and|vs|versus)\s+([A-Za-z0-9\s]+)']
        },
        "RISK": {
            "keywords": ["risk", "critical", "urgent", "worst", "problem", "issue", "delay", "bottleneck"],
            "patterns": []
        },
        "FORECAST": {
            "keywords": ["forecast", "predict", "trend", "projection", "future", "upcoming", "expected"],
            "patterns": []
        },
        "PENDING": {
            "keywords": ["pending", "backlog", "waiting", "not delivered", "not dispatched", "pending deliveries"],
            "patterns": []
        },
        "POD": {
            "keywords": ["pod", "acknowledgement", "proof of delivery", "awaiting acknowledgement", "not acknowledged"],
            "patterns": []
        },
        "RANKING": {
            "keywords": ["top", "best", "ranking", "leaderboard", "highest", "lowest", "worst"],
            "patterns": []
        }
    }
    
    @classmethod
    def is_logistics_question(cls, question: str) -> bool:
        """PHASE 3: Check if question is logistics-related"""
        question_lower = question.lower()
        
        # Direct logistics keywords
        for keyword in cls.LOGISTICS_KEYWORDS:
            if keyword in question_lower:
                return True
        
        # Check for DN numbers
        if re.search(r'\b(\d{8,15})\b', question):
            return True
        
        return False
    
    @classmethod
    def classify(cls, question: str) -> Tuple[str, Optional[str]]:
        """
        Classify question into category and extract entity.
        Returns: (category, entity)
        """
        question_lower = question.lower().strip()
        words = question.strip().split()
        
        # PHASE 2: Check for general AI questions first (highest priority)
        for keyword in cls.GENERAL_AI_KEYWORDS:
            if keyword in question_lower:
                # Don't classify as GENERAL if it's clearly a logistics query
                if not cls.is_logistics_question(question):
                    logger.debug(f"Classified as GENERAL due to keyword: {keyword}")
                    return "GENERAL", None
        
        # PHASE 1: Remove automatic dealer fallback - only classify as dealer with explicit indicators
        dealer_indicators = [
            "dealer", "customer", "dealer dashboard", "dealer summary",
            "dealer performance", "show dealer", "tell me about dealer",
            "dashboard for", "performance of"
        ]
        
        is_explicit_dealer = any(indicator in question_lower for indicator in dealer_indicators)
        
        # Only classify as dealer if explicit indicators exist OR it's a short name and logistics question
        if is_explicit_dealer:
            # Extract dealer name
            for pattern in cls.QUESTION_TYPES["DEALER"]["patterns"]:
                match = re.search(pattern, question_lower)
                if match:
                    dealer_name = match.group(1).strip().title()
                    if dealer_name and len(dealer_name) > 1:
                        return "DEALER", dealer_name
        
        # Check if it's a single word name and logistics question
        if len(words) == 1 and 2 < len(question) < 30 and not re.search(r'\d', question):
            if cls.is_logistics_question(question):
                return "DEALER", question.strip().title()
        
        # Check each category
        for qtype, data in cls.QUESTION_TYPES.items():
            for keyword in data.get("keywords", []):
                if keyword in question_lower:
                    for pattern in data.get("patterns", []):
                        match = re.search(pattern, question_lower)
                        if match:
                            if qtype == "COMPARISON" and len(match.groups()) >= 2:
                                return qtype, (match.group(1).strip(), match.group(2).strip())
                            entity = match.group(1).strip() if match.groups() else None
                            if entity and len(entity) > 1:
                                return qtype, entity
                    return qtype, None
        
        # Check for DN number specifically
        dn_match = re.search(r'\b(\d{8,15})\b', question)
        if dn_match:
            return "DN", dn_match.group(1)
        
        # PHASE 3: If logistics related but no specific type found, return LOGISTICS
        if cls.is_logistics_question(question):
            return "LOGISTICS", None
        
        # Default to GENERAL for AI processing
        return "GENERAL", None


# ======================================================
# RESPONSE FORMATTER
# ======================================================

class ResponseFormatter:
    """Format responses for WhatsApp"""
    
    @staticmethod
    def dealer_response(dealer_name: str, dashboard: Dict, ai_insights: Dict = None) -> str:
        """Format dealer dashboard response with AI insights"""
        if dashboard.get("fuzzy"):
            return dashboard.get("summary", "Multiple dealers found")
        
        if not dashboard.get("success"):
            return f"❌ Dealer '{dealer_name}' not found. Please check the name and try again."
        
        response = dashboard.get("formatted_message", "")
        
        # PHASE 5: Add AI insights for dealer queries
        if ai_insights and ai_insights.get("success"):
            response += "\n\n━━━━━━━━━━━━━━━━━━━━\n"
            response += "🤖 *AI INSIGHTS*\n"
            response += "━━━━━━━━━━━━━━━━━━━━\n"
            
            if ai_insights.get("structured"):
                summary = ai_insights.get("summary", "")
                if summary:
                    response += f"📝 {summary[:200]}\n\n"
                
                risks = ai_insights.get("risks", [])[:3]
                if risks:
                    response += "⚠️ *Key Risks:*\n"
                    for risk in risks:
                        response += f"   • {risk}\n"
                    response += "\n"
                
                recommendations = ai_insights.get("recommendations", [])[:3]
                if recommendations:
                    response += "💡 *Recommendations:*\n"
                    for rec in recommendations:
                        response += f"   • {rec}\n"
            else:
                response += ai_insights.get("content", "")[:500]
        
        return response
    
    @staticmethod
    def dn_response(dn_details: Dict) -> str:
        """Format DN details response"""
        if not dn_details.get("success"):
            return f"❌ DN {dn_details.get('dn_no', 'unknown')} not found."
        
        dn_no = dn_details.get("dn_no", "Unknown")
        dealer = dn_details.get("dealer", "Unknown")
        city = dn_details.get("city", "Unknown")
        warehouse = dn_details.get("warehouse", "Unknown")
        status = dn_details.get("status", "Unknown")
        pod_status = dn_details.get("pod_status", "Pending")
        dispatch_age = dn_details.get("dispatch_age", 0)
        pod_age = dn_details.get("pod_age", 0)
        total_qty = dn_details.get("total_quantity", 0)
        total_amount = dn_details.get("total_amount", 0)
        products = dn_details.get("products", [])
        
        dn_date = ""
        if dn_details.get('dn_create_date'):
            if isinstance(dn_details['dn_create_date'], datetime):
                dn_date = dn_details['dn_create_date'].strftime('%d-%b-%Y')
            else:
                dn_date = str(dn_details['dn_create_date'])[:10]
        
        pgi_date = ""
        if dn_details.get('good_issue_date'):
            if isinstance(dn_details['good_issue_date'], datetime):
                pgi_date = dn_details['good_issue_date'].strftime('%d-%b-%Y')
            else:
                pgi_date = str(dn_details['good_issue_date'])[:10]
        
        response = f"🔹 *DN: {dn_no}*\n\n"
        response += f"📋 Dealer: {dealer}\n"
        response += f"📍 City: {city} | 🏭 Warehouse: {warehouse}\n"
        response += f"📅 DN Date: {dn_date}\n"
        response += f"🚚 PGI Date: {pgi_date if pgi_date else 'Not Dispatched'}\n\n"
        response += f"📋 Status: {status}\n"
        response += f"📋 POD: {pod_status}\n"
        response += f"⏱️ Dispatch Age: {dispatch_age} days\n"
        
        if pod_age > 0:
            response += f"⏱️ POD Age: {pod_age} days\n"
        
        response += f"\n📦 Total Qty: {total_qty:,.0f} units\n"
        response += f"💰 Total Value: Rs {total_amount:,.2f}\n\n"
        
        if products:
            response += "📦 *Products:*\n"
            for p in products[:5]:
                response += f"   • {p['product_name']}: {p['quantity']:,.0f} units\n"
            if len(products) > 5:
                response += f"   • +{len(products) - 5} more products\n"
        
        if dispatch_age > 15 or pod_age > 15:
            response += "\n⚠️ *CRITICAL:* This delivery requires immediate attention!"
        
        return response
    
    @staticmethod
    def product_response(product_data: Dict, ai_insights: Dict = None) -> str:
        """Format product performance response"""
        if not product_data.get("success"):
            return f"❌ Product not found."
        
        product = product_data.get("product", {})
        
        response = f"📦 *PRODUCT: {product.get('product_name', 'Unknown')}*\n\n"
        response += f"📊 Total Qty: {product.get('total_qty', 0):,.0f} units\n"
        response += f"💰 Total Value: Rs {product.get('total_value', 0):,.2f}\n"
        response += f"✅ Fulfillment Rate: {product.get('fulfillment_rate', 0)}%\n"
        response += f"⏳ Pending Qty: {product.get('pending_qty', 0):,.0f} units\n"
        response += f"📋 POD Pending: {product.get('pod_pending_qty', 0):,.0f} units\n"
        response += f"⚡ Velocity: {product.get('velocity', 'Normal')}\n"
        response += f"📋 # of DNs: {product.get('dn_count', 0)}\n"
        response += f"🏪 # of Dealers: {product.get('dealer_count', 0)}\n"
        response += f"⏱️ Avg Dispatch Days: {product.get('avg_dispatch_days', 0)} days\n"
        
        if ai_insights and ai_insights.get("success"):
            response += "\n━━━━━━━━━━━━━━━━━━━━\n🤖 *AI RECOMMENDATIONS*\n━━━━━━━━━━━━━━━━━━━━\n"
            response += ai_insights.get("content", "")[:300]
        
        return response
    
    @staticmethod
    def warehouse_response(warehouse_data: Dict, ai_insights: Dict = None) -> str:
        """Format warehouse response with AI insights"""
        response = f"🏭 *WAREHOUSE: {warehouse_data.get('warehouse', 'Unknown')}*\n\n"
        response += f"📊 Total DNs: {warehouse_data.get('total_dns', 0)}\n"
        response += f"⏳ Pending DNs: {warehouse_data.get('pending_dns', 0)}\n"
        response += f"📦 Pending Units: {warehouse_data.get('pending_units', 0):,.0f}\n"
        response += f"💰 Pending Value: Rs {warehouse_data.get('pending_value', 0):,.2f}\n"
        response += f"📋 POD Pending: {warehouse_data.get('pod_pending_dns', 0)}\n"
        response += f"⚡ Efficiency Score: {warehouse_data.get('efficiency_score', 0)}%\n"
        
        if ai_insights and ai_insights.get("success"):
            response += "\n━━━━━━━━━━━━━━━━━━━━\n🤖 *AI ANALYSIS*\n━━━━━━━━━━━━━━━━━━━━\n"
            if ai_insights.get("structured"):
                recommendations = ai_insights.get("recommendations", [])[:3]
                if recommendations:
                    for rec in recommendations:
                        response += f"💡 {rec}\n"
            else:
                response += ai_insights.get("content", "")[:300]
        
        return response
    
    @staticmethod
    def city_response(city_data: Dict, ai_insights: Dict = None) -> str:
        """Format city response with AI insights"""
        response = f"🌆 *CITY: {city_data.get('city', 'Unknown')}*\n\n"
        response += f"📊 Total DNs: {city_data.get('total_dns', 0)}\n"
        response += f"⏳ Pending DNs: {city_data.get('pending_dns', 0)}\n"
        response += f"📦 Pending Units: {city_data.get('pending_units', 0):,.0f}\n"
        response += f"💰 Pending Value: Rs {city_data.get('pending_value', 0):,.2f}\n"
        response += f"⚠️ Delay Rate: {city_data.get('delay_rate', 0)}%\n"
        response += f"📋 Performance Score: {city_data.get('performance_score', 0)}%\n"
        
        if ai_insights and ai_insights.get("success"):
            response += "\n━━━━━━━━━━━━━━━━━━━━\n🤖 *AI ANALYSIS*\n━━━━━━━━━━━━━━━━━━━━\n"
            
            if ai_insights.get("structured"):
                summary = ai_insights.get("summary", "")
                if summary:
                    response += f"📝 {summary[:200]}\n\n"
                
                risks = ai_insights.get("risks", [])[:2]
                if risks:
                    response += "⚠️ *Risk Factors:*\n"
                    for risk in risks:
                        response += f"   • {risk}\n"
                    response += "\n"
                
                recommendations = ai_insights.get("recommendations", [])[:2]
                if recommendations:
                    response += "💡 *Recommendations:*\n"
                    for rec in recommendations:
                        response += f"   • {rec}\n"
            else:
                response += ai_insights.get("content", "")[:300]
        
        return response
    
    @staticmethod
    def comparison_response(comparison: Dict, entity_type: str) -> str:
        """Format comparison response"""
        if not comparison.get("success"):
            return f"❌ {comparison.get('message', 'Comparison failed')}"
        
        entity1 = comparison.get(f"{entity_type}1")
        entity2 = comparison.get(f"{entity_type}2")
        comp_data = comparison.get("comparison", {})
        
        response = f"📊 *COMPARISON: {entity1} vs {entity2}*\n\n"
        response += "━━━━━━━━━━━━━━━━━━━━\n"
        
        for metric, data in comp_data.items():
            metric_name = metric.replace("_", " ").title()
            val1 = data.get(entity1, 0)
            val2 = data.get(entity2, 0)
            winner = data.get("winner", "Tie")
            
            winner_icon = "🏆" if winner == entity1 else "🥈" if winner == entity2 else "🤝"
            response += f"📈 *{metric_name}*\n"
            response += f"   {entity1}: {val1:,.0f}\n"
            response += f"   {entity2}: {val2:,.0f}\n"
            response += f"   {winner_icon} Winner: {winner}\n\n"
        
        scores = comparison.get("scores", {})
        if scores:
            response += "━━━━━━━━━━━━━━━━━━━━\n"
            response += "🎯 *OVERALL SCORES*\n"
            response += f"   {entity1}: {scores.get(entity1, 0)}/100\n"
            response += f"   {entity2}: {scores.get(entity2, 0)}/100\n"
            response += f"   🏆 Winner: {scores.get('winner', 'Tie')}\n"
        
        return response
    
    @staticmethod
    def executive_response(executive_data: Dict) -> str:
        """Format executive summary response with AI insights"""
        response = executive_data.get("formatted_message", "")
        
        ai_recs = executive_data.get("ai_recommendations", {})
        if ai_recs:
            response += "\n\n━━━━━━━━━━━━━━━━━━━━\n"
            response += "🤖 *AI ACTION PLAN*\n"
            response += "━━━━━━━━━━━━━━━━━━━━\n"
            for rec in ai_recs.get("recommendations", [])[:3]:
                priority_icon = "🔴" if rec.get("priority") == "HIGH" else "🟡"
                response += f"{priority_icon} *{rec.get('title', 'Action')}*\n"
                response += f"   {rec.get('description', '')}\n\n"
        
        return response
    
    @staticmethod
    def clarification_response() -> str:
        """PHASE 7: Return clarification response for delivery tracking"""
        return """
📦 *Delivery Tracking Help*

I can help you track your delivery. Please provide:

🔹 *Option 1: DN Number*
   Example: `DN 6243611264`

🔹 *Option 2: Dealer Name*
   Example: `Show Afzal dashboard`

🔹 *Option 3: General Status*
   Example: `Pending deliveries`

Which information do you have?
"""
    
    @staticmethod
    def error_response(message: str) -> str:
        """Format error response"""
        return f"❌ {message}\n\nPlease try rephrasing your question or type 'help' for assistance."
    
    @staticmethod
    def help_response() -> str:
        """Format help response"""
        return """
🤖 *AI LOGISTICS ASSISTANT*

I can help you with:

📊 *Dealer Queries*
• "Show Afzal dashboard"
• "Afzal performance"

📦 *DN Queries*
• "DN 6243611264"

🏭 *Warehouse Queries*
• "Warehouse HPK status"

🌆 *City Queries*
• "Karachi situation"
• "Lahore performance"

📈 *Executive Queries*
• "Executive summary"
• "What should I focus on?"

🏆 *Ranking Queries*
• "Top 10 dealers"

💬 *General Questions*
• "Who is Imran Khan?"
• "Tell me a joke"
• "What is Python?"

Just type your question naturally!
"""


# ======================================================
# MAIN AI QUERY SERVICE
# ======================================================

class AIQueryService:
    """
    Complete AI Query Service - The orchestrator for all queries.
    """
    
    def __init__(self, db: Session):
        self.db = db
        self.analytics = AnalyticsService(db)
        self.logistics = LogisticsQueryService()
        self.formatter = ResponseFormatter()
        
        self.ai_enabled = getattr(config, 'ENABLE_DEEPSEEK_LOGISTICS', False) and getattr(config, 'AI_ANALYSIS_ENABLED', False)
        self.ai_available = AI_PROVIDER_AVAILABLE and self.ai_enabled
    
    # ======================================================
    # MAIN PROCESSING PIPELINE
    # ======================================================
    
    def process_query(self, question: str, user_phone: str = None) -> Dict[str, Any]:
        """
        Main entry point for processing user questions.
        """
        start_time = time.time()
        
        question = question.strip()
        
        # PHASE 8: Add comprehensive logging
        logger.info(f"📝 PROCESSING QUERY: {question}")
        
        # Handle help command
        if question.lower() in ["help", "menu", "what can you do", "commands"]:
            return {
                "success": True,
                "response": self.formatter.help_response(),
                "question_type": "HELP",
                "processing_time_ms": int((time.time() - start_time) * 1000)
            }
        
        # Step 1: Classify the question
        qtype, entity = QuestionClassifier.classify(question)
        
        # PHASE 8: Log classification
        logger.info(f"🏷️ CLASSIFIED AS: {qtype} | ENTITY: {entity}")
        
        # Handle comparison specially
        if qtype == "COMPARISON" and isinstance(entity, tuple):
            qtype, entity1, entity2 = "COMPARISON", entity[0], entity[1]
        else:
            entity1, entity2 = None, None
        
        # Step 2: Route to appropriate handler
        try:
            if qtype == "DEALER":
                result = self._handle_dealer_query(entity or question, user_phone)
            elif qtype == "DN":
                result = self._handle_dn_query(entity or question, user_phone)
            elif qtype == "PRODUCT":
                result = self._handle_product_query(entity or question, user_phone)
            elif qtype == "WAREHOUSE":
                result = self._handle_warehouse_query(entity or question, user_phone)
            elif qtype == "CITY":
                result = self._handle_city_query(entity or question, user_phone)
            elif qtype == "EXECUTIVE":
                result = self._handle_executive_query(user_phone)
            elif qtype == "COMPARISON":
                result = self._handle_comparison_query(entity1, entity2, user_phone)
            elif qtype == "RISK":
                result = self._handle_risk_query(user_phone)
            elif qtype == "RANKING":
                result = self._handle_ranking_query(question, user_phone)
            elif qtype == "PENDING":
                result = self._handle_pending_query(user_phone)
            elif qtype == "POD":
                result = self._handle_pod_query(user_phone)
            elif qtype == "LOGISTICS":
                result = self._handle_logistics_query(question, user_phone)
            else:
                result = self._handle_general_query(question, user_phone)
        except Exception as e:
            logger.error(f"❌ Error processing query: {e}")
            return {
                "success": False,
                "response": self.formatter.error_response("An unexpected error occurred. Please try again."),
                "error": str(e),
                "processing_time_ms": int((time.time() - start_time) * 1000)
            }
        
        # Step 3: Add metadata
        result["processing_time_ms"] = int((time.time() - start_time) * 1000)
        result["question_type"] = qtype
        result["entity"] = entity
        
        # PHASE 8: Log AI usage
        logger.info(f"🤖 AI USED: {result.get('ai_used', False)} | RESPONSE LENGTH: {len(result.get('response', ''))}")
        
        # Step 4: Log the query
        self._log_query(question, result, user_phone)
        
        return result
    
    # ======================================================
    # HANDLER METHODS
    # ======================================================
    
    def _handle_dealer_query(self, dealer_name: str, user_phone: str = None) -> Dict[str, Any]:
        """Handle dealer-related queries with AI insights"""
        try:
            dashboard = self.logistics.get_dealer_complete_dashboard(self.db, dealer_name, page=1, page_size=10)
        except Exception as e:
            logger.error(f"Dealer dashboard error: {e}")
            return {
                "success": False,
                "response": self.formatter.error_response(f"Unable to fetch dealer data for '{dealer_name}'"),
                "ai_used": False
            }
        
        if not dashboard.get("success"):
            return {
                "success": False,
                "response": self.formatter.error_response(dashboard.get("message", f"Dealer '{dealer_name}' not found")),
                "ai_used": False
            }
        
        if dashboard.get("fuzzy"):
            return {
                "success": True,
                "fuzzy": True,
                "matches": dashboard.get("matches", []),
                "response": dashboard.get("summary", "Multiple dealers found. Please select one."),
                "ai_used": False
            }
        
        # PHASE 5: Add AI insights for dealer
        ai_response = None
        if self.ai_available and ai_provider_service:
            try:
                if hasattr(self.analytics, 'build_dealer_ai_context'):
                    ai_context = self.analytics.build_dealer_ai_context(dealer_name)
                    ai_response = ai_provider_service.analyze_dealer(ai_context, structured=True, user_phone=user_phone)
            except Exception as e:
                logger.error(f"AI analysis failed for dealer {dealer_name}: {e}")
        
        response = self.formatter.dealer_response(dealer_name, dashboard, ai_response)
        
        return {
            "success": True,
            "dealer_name": dealer_name,
            "dashboard": dashboard,
            "ai_insights": ai_response,
            "response": response,
            "ai_used": ai_response is not None and ai_response.get("success", False)
        }
    
    def _handle_dn_query(self, dn_no: str, user_phone: str = None) -> Dict[str, Any]:
        """Handle DN/Delivery Note queries"""
        try:
            dn_details = self.logistics.get_dn_product_breakdown(self.db, dn_no)
        except Exception as e:
            logger.error(f"DN query error: {e}")
            return {
                "success": False,
                "response": self.formatter.error_response(f"Unable to fetch DN {dn_no}"),
                "ai_used": False
            }
        
        if not dn_details.get("success"):
            return {
                "success": False,
                "response": self.formatter.error_response(f"DN {dn_no} not found"),
                "ai_used": False
            }
        
        response = self.formatter.dn_response(dn_details)
        
        return {
            "success": True,
            "dn_no": dn_no,
            "details": dn_details,
            "response": response,
            "ai_used": False
        }
    
    def _handle_product_query(self, product_name: str, user_phone: str = None) -> Dict[str, Any]:
        """Handle product-related queries with AI insights"""
        try:
            if hasattr(self.analytics, 'product_dashboard'):
                product_data = self.analytics.product_dashboard(product_name)
            else:
                return {
                    "success": False,
                    "response": self.formatter.error_response("Product analytics not available"),
                    "ai_used": False
                }
        except Exception as e:
            logger.error(f"Product query error: {e}")
            return {
                "success": False,
                "response": self.formatter.error_response(f"Unable to fetch product data for '{product_name}'"),
                "ai_used": False
            }
        
        if not product_data.get("success"):
            return {
                "success": False,
                "response": self.formatter.error_response(f"Product '{product_name}' not found"),
                "ai_used": False
            }
        
        # PHASE 5: Add AI insights for product
        ai_insights = None
        if self.ai_available and ai_provider_service:
            try:
                ai_insights = ai_provider_service.answer_question(
                    f"Analyze product performance for {product_name}",
                    product_data,
                    structured=True,
                    user_phone=user_phone
                )
            except Exception as e:
                logger.error(f"AI product analysis failed: {e}")
        
        response = self.formatter.product_response(product_data, ai_insights)
        
        return {
            "success": True,
            "product": product_name,
            "data": product_data,
            "response": response,
            "ai_used": ai_insights is not None and ai_insights.get("success", False)
        }
    
    def _handle_warehouse_query(self, warehouse_name: str, user_phone: str = None) -> Dict[str, Any]:
        """Handle warehouse-related queries with AI insights"""
        try:
            if hasattr(self.analytics, 'warehouse_rankings'):
                rankings = self.analytics.warehouse_rankings()
            else:
                return {
                    "success": False,
                    "response": self.formatter.error_response("Warehouse analytics not available"),
                    "ai_used": False
                }
        except Exception as e:
            logger.error(f"Warehouse query error: {e}")
            return {
                "success": False,
                "response": self.formatter.error_response(f"Unable to fetch warehouse data for '{warehouse_name}'"),
                "ai_used": False
            }
        
        warehouse_data = None
        for w in rankings.get("all_warehouses", []):
            if warehouse_name.upper() in w.get("warehouse", "").upper():
                warehouse_data = w
                break
        
        if not warehouse_data:
            return {
                "success": False,
                "response": self.formatter.error_response(f"Warehouse '{warehouse_name}' not found"),
                "ai_used": False
            }
        
        # PHASE 5: Add AI insights for warehouse
        ai_insights = None
        if self.ai_available and ai_provider_service:
            try:
                ai_insights = ai_provider_service.answer_question(
                    f"Analyze warehouse performance for {warehouse_name}",
                    warehouse_data,
                    structured=True,
                    user_phone=user_phone
                )
            except Exception as e:
                logger.error(f"AI warehouse analysis failed: {e}")
        
        response = self.formatter.warehouse_response(warehouse_data, ai_insights)
        
        return {
            "success": True,
            "warehouse": warehouse_name,
            "data": warehouse_data,
            "response": response,
            "ai_used": ai_insights is not None and ai_insights.get("success", False)
        }
    
    def _handle_city_query(self, city_name: str, user_phone: str = None) -> Dict[str, Any]:
        """Handle city-related queries with AI insights"""
        try:
            if hasattr(self.analytics, 'city_rankings'):
                rankings = self.analytics.city_rankings()
            else:
                return {
                    "success": False,
                    "response": self.formatter.error_response("City analytics not available"),
                    "ai_used": False
                }
        except Exception as e:
            logger.error(f"City query error: {e}")
            return {
                "success": False,
                "response": self.formatter.error_response(f"Unable to fetch city data for '{city_name}'"),
                "ai_used": False
            }
        
        city_data = None
        for c in rankings.get("all_cities", []):
            if city_name.lower() in c.get("city", "").lower():
                city_data = c
                break
        
        if not city_data:
            return {
                "success": False,
                "response": self.formatter.error_response(f"City '{city_name}' not found"),
                "ai_used": False
            }
        
        # PHASE 5: Add AI insights for city
        ai_insights = None
        if self.ai_available and ai_provider_service:
            try:
                ai_insights = ai_provider_service.answer_question(
                    f"Analyze city performance for {city_name}. What are the key issues, risks, and recommendations?",
                    city_data,
                    structured=True,
                    user_phone=user_phone
                )
            except Exception as e:
                logger.error(f"AI city analysis failed for {city_name}: {e}")
        
        response = self.formatter.city_response(city_data, ai_insights)
        
        return {
            "success": True,
            "city": city_name,
            "data": city_data,
            "ai_insights": ai_insights,
            "response": response,
            "ai_used": ai_insights is not None and ai_insights.get("success", False)
        }
    
    def _handle_executive_query(self, user_phone: str = None) -> Dict[str, Any]:
        """Handle executive/CEO queries with AI insights"""
        try:
            if hasattr(self.analytics, 'get_executive_summary_enhanced'):
                executive_data = self.analytics.get_executive_summary_enhanced(self.db)
            else:
                executive_data = {"formatted_message": "Executive summary not available"}
        except Exception as e:
            logger.error(f"Executive query error: {e}")
            executive_data = {"formatted_message": "Unable to fetch executive summary"}
        
        # PHASE 5: Add AI insights for executive
        ai_response = None
        if self.ai_available and ai_provider_service:
            try:
                if hasattr(self.analytics, 'build_executive_ai_context'):
                    ai_context = self.analytics.build_executive_ai_context()
                    ai_response = ai_provider_service.analyze_executive(ai_context, structured=True, user_phone=user_phone)
                    if ai_response.get("success"):
                        executive_data["ai_recommendations"] = ai_response
            except Exception as e:
                logger.error(f"AI executive analysis failed: {e}")
        
        response = self.formatter.executive_response(executive_data)
        
        return {
            "success": True,
            "data": executive_data,
            "response": response,
            "ai_used": ai_response is not None and ai_response.get("success", False)
        }
    
    def _handle_comparison_query(self, entity1: str, entity2: str, user_phone: str = None) -> Dict[str, Any]:
        """Handle comparison queries"""
        if not entity1 or not entity2:
            return {
                "success": False,
                "response": self.formatter.error_response("Please specify two entities to compare (e.g., 'Compare Dealer A and Dealer B')"),
                "ai_used": False
            }
        
        entity_type = self._detect_entity_type(entity1)
        comparison = None
        
        try:
            if entity_type == "DEALER" and hasattr(self.analytics, 'compare_dealers'):
                comparison = self.analytics.compare_dealers(entity1, entity2)
                response = self.formatter.comparison_response(comparison, "dealer")
            elif entity_type == "WAREHOUSE" and hasattr(self.analytics, 'compare_warehouses'):
                comparison = self.analytics.compare_warehouses(entity1, entity2)
                response = self.formatter.comparison_response(comparison, "warehouse")
            elif entity_type == "CITY" and hasattr(self.analytics, 'compare_cities'):
                comparison = self.analytics.compare_cities(entity1, entity2)
                response = self.formatter.comparison_response(comparison, "city")
            elif entity_type == "PRODUCT" and hasattr(self.analytics, 'compare_products'):
                comparison = self.analytics.compare_products(entity1, entity2)
                response = self.formatter.comparison_response(comparison, "product")
            else:
                return {
                    "success": False,
                    "response": self.formatter.error_response("Unable to compare these entities. Please specify dealer, warehouse, city, or product names."),
                    "ai_used": False
                }
        except Exception as e:
            logger.error(f"Comparison error: {e}")
            return {
                "success": False,
                "response": self.formatter.error_response("Comparison failed. Please try again."),
                "ai_used": False
            }
        
        return {
            "success": comparison.get("success", False) if comparison else False,
            "comparison": comparison,
            "response": response,
            "ai_used": False
        }
    
    def _handle_risk_query(self, user_phone: str = None) -> Dict[str, Any]:
        """Handle risk-related queries with AI insights"""
        risk_dealers = []
        risk_warehouses = []
        action_plan = []
        
        try:
            if hasattr(self.analytics, 'top_risk_dealers'):
                risk_dealers = self.analytics.top_risk_dealers(5)
            if hasattr(self.analytics, 'top_risk_warehouses'):
                risk_warehouses = self.analytics.top_risk_warehouses(5)
            if hasattr(self.analytics, 'generate_action_plan'):
                action_plan = self.analytics.generate_action_plan()
        except Exception as e:
            logger.error(f"Risk query error: {e}")
        
        response = "🚨 *RISK ASSESSMENT REPORT*\n\n"
        
        if risk_dealers:
            response += "━━━━━━━━━━━━━━━━━━━━\n"
            response += "⚠️ *TOP RISK DEALERS*\n"
            response += "━━━━━━━━━━━━━━━━━━━━\n"
            for i, d in enumerate(risk_dealers[:3], 1):
                response += f"{i}. *{d.get('dealer', 'Unknown')}*\n"
                response += f"   Pending: {d.get('pending_dns', 0)} DNs | Value: Rs {d.get('pending_value', 0):,.2f}\n\n"
        
        if risk_warehouses:
            response += "━━━━━━━━━━━━━━━━━━━━\n"
            response += "🏭 *TOP RISK WAREHOUSES*\n"
            response += "━━━━━━━━━━━━━━━━━━━━\n"
            for i, w in enumerate(risk_warehouses[:3], 1):
                response += f"{i}. *{w.get('warehouse', 'Unknown')}*\n"
                response += f"   Pending: {w.get('pending_dns', 0)} DNs | Units: {w.get('pending_units', 0):,.0f}\n\n"
        
        if action_plan:
            response += "━━━━━━━━━━━━━━━━━━━━\n"
            response += "🤖 *AI ACTION PLAN*\n"
            response += "━━━━━━━━━━━━━━━━━━━━\n"
            for action in action_plan[:3]:
                priority_icon = "🔴" if action.get("priority", 10) <= 2 else "🟡"
                response += f"{priority_icon} *{action.get('issue', 'Action')}*\n"
                response += f"   → {action.get('action', '')}\n\n"
        
        if not risk_dealers and not risk_warehouses and not action_plan:
            response += "No significant risks detected at this time.\n"
        
        return {
            "success": True,
            "risk_dealers": risk_dealers,
            "risk_warehouses": risk_warehouses,
            "action_plan": action_plan,
            "response": response,
            "ai_used": False
        }
    
    def _handle_ranking_query(self, question: str, user_phone: str = None) -> Dict[str, Any]:
        """Handle ranking queries"""
        question_lower = question.lower()
        
        rankings = None
        response = ""
        
        try:
            if "dealer" in question_lower or "customer" in question_lower:
                if hasattr(self.analytics, 'dealer_rankings'):
                    rankings = self.analytics.dealer_rankings(10)
                    response = self.formatter.ranking_response(rankings, "by_value", 10)
                else:
                    response = self.formatter.error_response("Dealer rankings not available")
            elif "warehouse" in question_lower:
                if hasattr(self.analytics, 'warehouse_rankings'):
                    rankings = self.analytics.warehouse_rankings(10)
                    response = self.formatter.ranking_response(rankings, "by_efficiency", 10)
                else:
                    response = self.formatter.error_response("Warehouse rankings not available")
            elif "city" in question_lower:
                if hasattr(self.analytics, 'city_rankings'):
                    rankings = self.analytics.city_rankings(10)
                    response = self.formatter.ranking_response(rankings, "by_performance", 10)
                else:
                    response = self.formatter.error_response("City rankings not available")
            elif "product" in question_lower:
                if hasattr(self.analytics, 'product_dashboard'):
                    product_dashboard = self.analytics.product_dashboard()
                    rankings = {"top_products": product_dashboard.get("top_products", [])}
                    response = self.formatter.ranking_response(rankings, "top_products", 10)
                else:
                    response = self.formatter.error_response("Product rankings not available")
            else:
                response = self.formatter.error_response("Unable to generate ranking. Please specify dealers, warehouses, cities, or products.")
        except Exception as e:
            logger.error(f"Ranking query error: {e}")
            response = self.formatter.error_response("Unable to generate ranking at this time.")
        
        return {
            "success": True,
            "rankings": rankings,
            "response": response,
            "ai_used": False
        }
    
    def _handle_pending_query(self, user_phone: str = None) -> Dict[str, Any]:
        """Handle pending deliveries query"""
        pending = {}
        aging = {}
        
        try:
            if hasattr(self.analytics, 'pending_metrics'):
                pending = self.analytics.pending_metrics()
            if hasattr(self.analytics, 'aging_summary'):
                aging = self.analytics.aging_summary()
        except Exception as e:
            logger.error(f"Pending query error: {e}")
        
        response = f"⏳ *PENDING DELIVERIES*\n\n"
        response += f"📊 Total Pending DNs: *{pending.get('pending_dns', 0)}*\n"
        response += f"📦 Total Pending Units: *{pending.get('pending_units', 0):,.0f}*\n"
        response += f"💰 Total Pending Value: *Rs {pending.get('pending_value', 0):,.2f}*\n\n"
        
        if aging.get("buckets"):
            response += "━━━━━━━━━━━━━━━━━━━━\n"
            response += "📅 *AGING BREAKDOWN*\n"
            response += "━━━━━━━━━━━━━━━━━━━━\n"
            
            buckets = aging.get("buckets", {})
            for bucket, data in buckets.items():
                if data.get("dns", 0) > 0:
                    response += f"• {bucket} days: {data['dns']} DNs ({data.get('units', 0):,.0f} units)\n"
        
        if aging.get("critical_count", 0) > 0:
            response += f"\n⚠️ *Critical:* {aging.get('critical_count', 0)} DNs older than 15 days"
        
        return {
            "success": True,
            "pending": pending,
            "aging": aging,
            "response": response,
            "ai_used": False
        }
    
    def _handle_pod_query(self, user_phone: str = None) -> Dict[str, Any]:
        """Handle POD pending queries"""
        pod = {}
        pod_aging = {}
        
        try:
            if hasattr(self.analytics, 'pod_metrics'):
                pod = self.analytics.pod_metrics()
            if hasattr(self.analytics, 'pod_aging_summary'):
                pod_aging = self.analytics.pod_aging_summary()
        except Exception as e:
            logger.error(f"POD query error: {e}")
        
        response = f"📋 *PENDING POD (Awaiting Acknowledgement)*\n\n"
        response += f"📊 Total POD Pending DNs: *{pod.get('pod_pending_dns', 0)}*\n"
        response += f"📦 Total Pending Units: *{pod.get('pod_pending_units', 0):,.0f}*\n"
        response += f"💰 Total Pending Value: *Rs {pod.get('pod_pending_value', 0):,.2f}*\n\n"
        
        if pod_aging.get("buckets"):
            response += "━━━━━━━━━━━━━━━━━━━━\n"
            response += "📅 *POD AGING BREAKDOWN*\n"
            response += "━━━━━━━━━━━━━━━━━━━━\n"
            
            buckets = pod_aging.get("buckets", {})
            for bucket, data in buckets.items():
                if data.get("dns", 0) > 0:
                    response += f"• {bucket} days: {data['dns']} DNs ({data.get('units', 0):,.0f} units)\n"
        
        if pod_aging.get("urgent_count", 0) > 0:
            response += f"\n⚠️ *Urgent:* {pod_aging.get('urgent_count', 0)} DNs older than 15 days"
        
        return {
            "success": True,
            "pod": pod,
            "pod_aging": pod_aging,
            "response": response,
            "ai_used": False
        }
    
    def _handle_logistics_query(self, question: str, user_phone: str = None) -> Dict[str, Any]:
        """PHASE 4: Handle logistics-related general questions with AI"""
        if self.ai_available and ai_provider_service:
            try:
                # Build logistics context
                logistics_context = {}
                try:
                    if hasattr(self.analytics, 'build_executive_ai_context'):
                        exec_context = self.analytics.build_executive_ai_context()
                        if exec_context:
                            logistics_context["executive_summary"] = exec_context
                    
                    # Add pending metrics
                    if hasattr(self.analytics, 'pending_metrics'):
                        logistics_context["pending_metrics"] = self.analytics.pending_metrics()
                    
                    # Add risk data
                    if hasattr(self.analytics, 'top_risk_dealers'):
                        logistics_context["top_risks"] = self.analytics.top_risk_dealers(3)
                except Exception as ctx_err:
                    logger.debug(f"Could not add logistics context: {ctx_err}")
                
                ai_response = ai_provider_service.answer_question(
                    question,
                    logistics_context,
                    structured=False,
                    user_phone=user_phone
                )
                
                if ai_response.get("success"):
                    return {
                        "success": True,
                        "response": ai_response.get("content", "No response generated."),
                        "ai_used": True,
                        "ai_response": ai_response
                    }
            except Exception as e:
                logger.error(f"AI logistics query failed: {e}")
        
        # PHASE 7: Fallback to clarification for delivery questions
        if "delivery" in question.lower() or "when" in question.lower():
            return {
                "success": True,
                "response": self.formatter.clarification_response(),
                "ai_used": False
            }
        
        return {
            "success": True,
            "response": self.formatter.help_response(),
            "ai_used": False
        }
    
    # PHASE 4: Improved general query handler
    def _handle_general_query(self, question: str, user_phone: str = None) -> Dict[str, Any]:
        """Handle general questions with AI"""
        if self.ai_available and ai_provider_service:
            try:
                # Simple context for general AI questions
                context = {
                    "type": "general_ai",
                    "question": question,
                    "timestamp": datetime.utcnow().isoformat()
                }
                
                ai_response = ai_provider_service.answer_question(
                    question,
                    context,
                    structured=False,
                    user_phone=user_phone
                )
                
                if ai_response.get("success"):
                    return {
                        "success": True,
                        "response": ai_response.get("content", "No response generated."),
                        "ai_used": True,
                        "ai_response": ai_response
                    }
            except Exception as e:
                logger.error(f"AI general query failed: {e}")
        
        return {
            "success": True,
            "response": self.formatter.help_response(),
            "ai_used": False
        }
    
    # ======================================================
    # HELPER METHODS
    # ======================================================
    
    def _detect_entity_type(self, entity_name: str) -> str:
        """Detect the type of entity from its name"""
        if not entity_name:
            return "UNKNOWN"
        
        if hasattr(self.analytics, 'dealer_rankings'):
            try:
                dealer_rankings = self.analytics.dealer_rankings(50)
                for d in dealer_rankings.get("by_value", []):
                    if entity_name.lower() in d.get("dealer", "").lower():
                        return "DEALER"
            except:
                pass
        
        if hasattr(self.analytics, 'warehouse_rankings'):
            try:
                warehouse_rankings = self.analytics.warehouse_rankings(50)
                for w in warehouse_rankings.get("all_warehouses", []):
                    if entity_name.upper() in w.get("warehouse", "").upper():
                        return "WAREHOUSE"
            except:
                pass
        
        if hasattr(self.analytics, 'city_rankings'):
            try:
                city_rankings = self.analytics.city_rankings(50)
                for c in city_rankings.get("all_cities", []):
                    if entity_name.lower() in c.get("city", "").lower():
                        return "CITY"
            except:
                pass
        
        return "UNKNOWN"
    
    def _log_query(self, question: str, result: Dict, user_phone: str = None):
        """Log query to database for analytics"""
        try:
            log_entry = AIResponseLog(
                question=question[:500],
                response=result.get("response", "")[:2000],
                intent=result.get("question_type", "unknown"),
                confidence=1.0 if result.get("success") else 0.0,
                response_time_ms=result.get("processing_time_ms", 0),
                user_phone=user_phone,
                created_at=datetime.utcnow()
            )
            self.db.add(log_entry)
            self.db.commit()
        except Exception as e:
            logger.error(f"Failed to log query: {e}")
            self.db.rollback()


# ======================================================
# SINGLETON INSTANCE FACTORY
# ======================================================

def get_ai_query_service(db: Session) -> AIQueryService:
    """Factory function to get AIQueryService instance"""
    return AIQueryService(db)


# ======================================================
# CONVENIENCE FUNCTION
# ======================================================

def process_whatsapp_query(question: str, db: Session, user_phone: str = None) -> str:
    """
    Convenience function for WhatsApp integration.
    Returns just the response string.
    """
    service = AIQueryService(db)
    result = service.process_query(question, user_phone)
    return result.get("response", "Unable to process your request. Please try again.")
