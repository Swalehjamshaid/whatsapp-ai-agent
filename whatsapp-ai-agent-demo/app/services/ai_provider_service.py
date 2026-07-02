"""
AI Provider Service for WhatsApp AI Agent Demo
Handles multiple AI providers with fallback mechanisms
"""

import os
import json
import logging
from typing import Dict, Any, Optional, List
from datetime import datetime
import re

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class AIProviderService:
    """
    Service for handling AI operations with multiple provider support
    and graceful fallback for unavailable services
    """
    
    def __init__(self, config: Optional[Dict] = None):
        """
        Initialize AI Provider Service
        
        Args:
            config: Configuration dictionary with provider settings
        """
        self.config = config or {}
        self.provider = self.config.get('provider', 'groq')
        self.api_key = self.config.get('api_key', os.getenv('GROQ_API_KEY'))
        self.providers = {
            'groq': self._groq_operation,
            'openai': self._openai_operation,
            'fallback': self._fallback_operation
        }
        
        # Cache for responses to avoid repeated API calls
        self.cache = {}
        
        logger.info(f"AI Provider Service initialized with provider: {self.provider}")
    
    def generate_city_dashboard(self, city_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Generate a city dashboard from raw data
        
        Args:
            city_data: Dictionary containing city metrics
            
        Returns:
            Formatted city dashboard data
        """
        try:
            # Extract and format data
            dashboard = {
                "city": city_data.get("city", "Unknown"),
                "warehouse": city_data.get("warehouse", "Unknown"),
                "warehouse_code": city_data.get("warehouse_code", "N/A"),
                "sales_office": city_data.get("sales_office", "Unknown"),
                "sales_manager": city_data.get("sales_manager", "Unknown"),
                "division": city_data.get("division", "Unknown"),
                
                # Financial Metrics
                "revenue": self._format_currency(city_data.get("revenue", 0)),
                "units": city_data.get("units", 0),
                "dn_count": city_data.get("dn_count", 0),
                "dealers": city_data.get("dealers", 0),
                "pending_dn": city_data.get("pending_dn", 0),
                "avg_revenue_per_dn": self._format_currency(city_data.get("avg_revenue_per_dn", 0)),
                
                # Performance Metrics
                "delivery_success": city_data.get("delivery_success", 0.0),
                "pgi_success": city_data.get("pgi_success", 0.0),
                "pod_success": city_data.get("pod_success", 0.0),
                "pending_rate": city_data.get("pending_rate", 0.0),
                "avg_delivery_days": city_data.get("avg_delivery_days", 0.0),
                "avg_pod_days": city_data.get("avg_pod_days", 0.0),
                "transit_days": city_data.get("transit_days", 0.0),
                
                # Aging Metrics
                "pgi_aging": city_data.get("pgi_aging", 0.0),
                "pod_aging": city_data.get("pod_aging", 0.0),
                "delivery_aging": city_data.get("delivery_aging", 0.0),
                
                # Status & Scores
                "status": city_data.get("status", "Unknown"),
                "business_score": city_data.get("business_score", 0.0),
                "risk_score": city_data.get("risk_score", 0.0),
                "performance_grade": city_data.get("performance_grade", "C"),
                
                # Logistics
                "distance": city_data.get("distance", "Unknown"),
                "driving_time": city_data.get("driving_time", "Unknown"),
                "estimated_delivery": city_data.get("estimated_delivery", "Unknown"),
                
                # Top Products
                "top_products": city_data.get("top_products", []),
                "top_product": city_data.get("top_product", "N/A"),
                
                # Additional Info
                "timestamp": datetime.now().isoformat(),
                "raw_data": city_data
            }
            
            # Calculate performance grade if not provided
            if dashboard["performance_grade"] == "C":
                dashboard["performance_grade"] = self._calculate_performance_grade(
                    dashboard["business_score"]
                )
            
            return {
                "success": True,
                "data": dashboard,
                "formatted": self._format_dashboard(dashboard)
            }
            
        except Exception as e:
            logger.error(f"Error generating city dashboard: {str(e)}")
            return {
                "success": False,
                "error": str(e),
                "data": None
            }
    
    def process_natural_query(self, query: str, context: Optional[Dict] = None) -> Dict[str, Any]:
        """
        Process natural language queries with AI
        
        Args:
            query: Natural language query from user
            context: Optional context dictionary
            
        Returns:
            Processed query response
        """
        try:
            # Use cache for repeated queries
            cache_key = f"query_{query}"
            if cache_key in self.cache:
                return self.cache[cache_key]
            
            # Check if query matches known patterns
            response = self._match_query_patterns(query.lower(), context or {})
            
            # If no pattern matched, try AI provider
            if not response["matched"]:
                response = self._call_ai_provider(query, context)
            
            # Cache the response
            self.cache[cache_key] = response
            
            return response
            
        except Exception as e:
            logger.error(f"Error processing natural query: {str(e)}")
            return {
                "success": False,
                "error": str(e),
                "matched": False,
                "message": "AI service is currently unavailable. Please try again later."
            }
    
    def _match_query_patterns(self, query: str, context: Dict) -> Dict[str, Any]:
        """
        Match query against known patterns without AI
        """
        patterns = {
            "city": r"(city|dashboard).*(lahore|karachi|islamabad|rawalpindi|faisalabad)",
            "dealer": r"(dealer|show).*([a-z]+)",
            "dn": r"^(\d{10})$",  # DN number pattern
            "menu": r"^(menu|help|options|main)$",
            "pending": r"(pending|delay|overdue)",
            "ranking": r"(top|best|worst|lowest|highest).*(city|dealer|product)"
        }
        
        matched = False
        response = {
            "success": False,
            "matched": False,
            "type": None,
            "data": None,
            "message": None
        }
        
        for pattern_type, pattern in patterns.items():
            if re.search(pattern, query, re.IGNORECASE):
                matched = True
                response["matched"] = True
                response["type"] = pattern_type
                
                # Extract specific values
                if pattern_type == "city":
                    city_match = re.search(r"(lahore|karachi|islamabad|rawalpindi|faisalabad)", query, re.IGNORECASE)
                    if city_match:
                        city_name = city_match.group(1).capitalize()
                        response["data"] = {"city": city_name}
                        response["message"] = f"Showing dashboard for {city_name}"
                        response["success"] = True
                
                elif pattern_type == "dn":
                    dn_match = re.search(r"\d{10}", query)
                    if dn_match:
                        response["data"] = {"dn_number": dn_match.group()}
                        response["message"] = f"Tracking DN: {dn_match.group()}"
                        response["success"] = True
                    else:
                        response["message"] = "Please provide a valid DN number (10 digits)"
                
                elif pattern_type == "menu":
                    response["data"] = {"action": "show_menu"}
                    response["message"] = self._get_menu()
                    response["success"] = True
                
                elif pattern_type == "dealer":
                    # Extract dealer name
                    dealer_match = re.search(r"(?:dealer|show)\s+(.+?)(?:\s+in|\s+city|$)", query, re.IGNORECASE)
                    if dealer_match:
                        dealer_name = dealer_match.group(1).strip()
                        response["data"] = {"dealer": dealer_name}
                        response["message"] = f"Showing analytics for {dealer_name}"
                        response["success"] = True
                
                break
        
        return response
    
    def _call_ai_provider(self, query: str, context: Dict) -> Dict[str, Any]:
        """
        Call the configured AI provider
        """
        provider_func = self.providers.get(self.provider, self._fallback_operation)
        return provider_func(query, context)
    
    def _groq_operation(self, query: str, context: Dict) -> Dict[str, Any]:
        """
        Operation using Groq API
        """
        try:
            # Check if Groq is available
            if not self.api_key:
                logger.warning("Groq API key not found, falling back to local processing")
                return self._fallback_operation(query, context)
            
            # Import groq only when needed
            try:
                from groq import Groq
            except ImportError:
                logger.error("Groq library not installed, falling back")
                return self._fallback_operation(query, context)
            
            client = Groq(api_key=self.api_key)
            
            # Prepare messages
            messages = [
                {"role": "system", "content": "You are a logistics analytics assistant for HPK Logistics."},
                {"role": "user", "content": query}
            ]
            
            # Make API call
            response = client.chat.completions.create(
                model="mixtral-8x7b-32768",
                messages=messages,
                temperature=0.7,
                max_tokens=500
            )
            
            return {
                "success": True,
                "matched": True,
                "type": "ai_response",
                "data": {"ai_response": response.choices[0].message.content},
                "message": response.choices[0].message.content
            }
            
        except Exception as e:
            logger.error(f"Groq operation failed: {str(e)}")
            # Generate a reference ID
            import uuid
            ref_id = str(uuid.uuid4())[:8]
            
            return {
                "success": False,
                "error": str(e),
                "reference_id": ref_id,
                "message": f"groq_service does not support the requested operation. Reference ID: {ref_id}"
            }
    
    def _openai_operation(self, query: str, context: Dict) -> Dict[str, Any]:
        """
        Operation using OpenAI API
        """
        try:
            # Similar implementation for OpenAI
            # ... OpenAI specific code here
            
            # For now, fallback
            return self._fallback_operation(query, context)
            
        except Exception as e:
            logger.error(f"OpenAI operation failed: {str(e)}")
            return {
                "success": False,
                "error": str(e),
                "message": "OpenAI service is currently unavailable."
            }
    
    def _fallback_operation(self, query: str, context: Dict) -> Dict[str, Any]:
        """
        Fallback operation when AI services are unavailable
        """
        # Try local pattern matching again with more flexibility
        response = self._match_query_patterns(query.lower(), context)
        
        if not response.get("matched", False):
            response = {
                "success": False,
                "matched": False,
                "type": "unrecognized",
                "message": "⚠️ AI service is currently unavailable. Please try again later.",
                "data": None
            }
        
        return response
    
    def _format_currency(self, amount: float) -> str:
        """
        Format currency in PKR
        """
        if amount == 0:
            return "PKR 0.00"
        return f"PKR {amount:,.2f}".replace(",", ",")
    
    def _calculate_performance_grade(self, score: float) -> str:
        """
        Calculate performance grade based on score
        """
        if score >= 90:
            return "A"
        elif score >= 80:
            return "B"
        elif score >= 70:
            return "C"
        elif score >= 60:
            return "D"
        else:
            return "F"
    
    def _format_dashboard(self, dashboard: Dict) -> str:
        """
        Format dashboard data into a readable string
        """
        lines = []
        lines.append("🏙️ City Dashboard")
        lines.append("")
        lines.append(f"City: {dashboard['city']}")
        lines.append(f"Warehouse: {dashboard['warehouse']}")
        lines.append(f"Warehouse Code: {dashboard['warehouse_code']}")
        lines.append(f"Sales Office: {dashboard['sales_office']}")
        lines.append(f"Sales Manager: {dashboard['sales_manager']}")
        lines.append(f"Division: {dashboard['division']}")
        lines.append("")
        lines.append("─" * 20)
        lines.append("")
        lines.append(f"Revenue: {dashboard['revenue']}")
        lines.append(f"Units: {dashboard['units']}")
        lines.append(f"DN: {dashboard['dn_count']}")
        lines.append(f"Dealers: {dashboard['dealers']}")
        lines.append(f"Pending DN: {dashboard['pending_dn']}")
        lines.append(f"Average Revenue/DN: {dashboard['avg_revenue_per_dn']}")
        lines.append("")
        lines.append("─" * 20)
        lines.append("")
        lines.append(f"Delivery Success: {dashboard['delivery_success']:.1f}%")
        lines.append(f"PGI Success: {dashboard['pgi_success']:.1f}%")
        lines.append(f"POD Success: {dashboard['pod_success']:.1f}%")
        lines.append(f"Pending Rate: {dashboard['pending_rate']:.1f}%")
        lines.append(f"Average Delivery: {dashboard['avg_delivery_days']:.1f} Days")
        lines.append(f"Average POD: {dashboard['avg_pod_days']:.1f} Days")
        lines.append(f"Transit Days: {dashboard['transit_days']:.1f} Days")
        lines.append("")
        lines.append("─" * 20)
        lines.append("")
        lines.append(f"PGI Aging: {dashboard['pgi_aging']:.1f} Days")
        lines.append(f"POD Aging: {dashboard['pod_aging']:.1f} Days")
        lines.append(f"Delivery Aging: {dashboard['delivery_aging']:.1f} Days")
        lines.append("")
        lines.append("─" * 20)
        lines.append("")
        
        # Status with emoji
        status_emoji = {
            "Watch": "🟠",
            "Good": "🟢",
            "Warning": "🟡",
            "Critical": "🔴"
        }.get(dashboard['status'], "⚪")
        
        lines.append(f"{status_emoji} Status: {dashboard['status']}")
        lines.append(f"Business Score: {dashboard['business_score']:.1f}/100")
        lines.append(f"Risk Score: {dashboard['risk_score']:.1f}/100")
        lines.append(f"Performance Grade: {dashboard['performance_grade']}")
        lines.append("")
        lines.append("─" * 20)
        lines.append("")
        lines.append(f"Distance: {dashboard['distance']}")
        lines.append(f"Driving Time: {dashboard['driving_time']}")
        lines.append(f"Estimated Delivery: {dashboard['estimated_delivery']}")
        lines.append("")
        lines.append("─" * 20)
        lines.append("")
        
        if dashboard['top_products']:
            lines.append(f"Top Product: {dashboard['top_products'][0][:15]}...")
        elif dashboard['top_product']:
            lines.append(f"Top Product: {dashboard['top_product']}")
        
        return "\n".join(lines)
    
    def _get_menu(self) -> str:
        """
        Generate the main menu
        """
        menu = """
👋 Welcome to HPK Logistics AI Assistant

Please select an option by replying with the number:

1️⃣ DN Services
2️⃣ Dealer Analytics
3️⃣ Warehouse Analytics
4️⃣ City Analytics
5️⃣ Product Analytics
6️⃣ National KPI Dashboard
7️⃣ Pending Deliveries
8️⃣ Reports & Rankings
9️⃣ AI Assistant
0️⃣ Help

💡 Tip: You can also type natural questions like 'Show dealer Taj Electronics'
        """
        return menu.strip()
    
    def get_city_with_highest_sales(self) -> Dict[str, Any]:
        """
        Get city with highest sales (mock data for now)
        """
        # This would normally fetch from database
        mock_data = {
            "city": "Karachi",
            "warehouse": "Karachi",
            "warehouse_code": "KHI",
            "sales_office": "Karachi Office",
            "revenue": 50884.00,
            "units": 2,
            "dn_count": 1,
            "dealers": 1,
            "business_score": 52.5,
            "risk_score": 47.5,
            "performance_grade": "C",
            "status": "Watch"
        }
        return self.generate_city_dashboard(mock_data)
    
    def get_city_with_lowest_sales(self) -> Dict[str, Any]:
        """
        Get city with lowest sales (mock data for now)
        """
        mock_data = {
            "city": "Lahore",
            "warehouse": "Lahore",
            "warehouse_code": "LHE",
            "sales_office": "Lahore Office",
            "revenue": 21828.00,
            "units": 2,
            "dn_count": 1,
            "dealers": 1,
            "business_score": 51.1,
            "risk_score": 48.9,
            "performance_grade": "C",
            "status": "Watch"
        }
        return self.generate_city_dashboard(mock_data)


# Example usage
if __name__ == "__main__":
    # Initialize the service
    service = AIProviderService()
    
    # Example: Generate Lahore City Dashboard
    lahore_data = {
        "city": "Change, Lahore",
        "warehouse": "Lahore",
        "warehouse_code": "LHE",
        "sales_office": "Lahore Office",
        "sales_manager": "E-commerce",
        "division": "Small Appliances",
        "revenue": 21828.00,
        "units": 2,
        "dn_count": 1,
        "dealers": 1,
        "pending_dn": 0,
        "avg_revenue_per_dn": 21828.00,
        "delivery_success": 100.0,
        "pgi_success": 0.0,
        "pod_success": 0.0,
        "pending_rate": 0.0,
        "avg_delivery_days": 1.0,
        "avg_pod_days": 1.0,
        "transit_days": 1.0,
        "pgi_aging": 0.0,
        "pod_aging": 0.0,
        "delivery_aging": 0.0,
        "status": "Watch",
        "business_score": 51.1,
        "risk_score": 48.9,
        "performance_grade": "C",
        "distance": "12.4 KM",
        "driving_time": "0 Hours 14 Minutes",
        "estimated_delivery": "Same Day",
        "top_products": ["HMW-20MPS"],
        "top_product": "HMW-20MPS"
    }
    
    result = service.generate_city_dashboard(lahore_data)
    if result["success"]:
        print(result["formatted"])
    else:
        print(f"Error: {result['error']}")
