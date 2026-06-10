# ==========================================================
# FILE: app/services/whatsapp_service.py
# VERSION: 4.0
# PURPOSE: WhatsApp Cloud API Communication Layer
# ARCHITECTURE: User → Webhook → AI Query Service → (Logistics|Analytics|KPI) → AI Provider → WhatsApp Service → User
# ==========================================================

import re
import json
import base64
import asyncio
from typing import Dict, Any, Optional, List, Tuple, Union
from datetime import datetime
from enum import Enum
from loguru import logger
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

import requests

from app.config import config


# ==========================================================
# MESSAGE TYPES
# ==========================================================

class MessageType(Enum):
    """WhatsApp message types"""
    TEXT = "text"
    TEMPLATE = "template"
    DOCUMENT = "document"
    IMAGE = "image"
    AUDIO = "audio"
    VIDEO = "video"
    LOCATION = "location"
    CONTACT = "contacts"
    INTERACTIVE = "interactive"
    REACTION = "reaction"
    STICKER = "sticker"


class MessageStatus(Enum):
    """Message delivery status"""
    SENT = "sent"
    DELIVERED = "delivered"
    READ = "read"
    FAILED = "failed"
    PENDING = "pending"


# ==========================================================
# MESSAGE TEMPLATES
# ==========================================================

class MessageTemplates:
    """Pre-defined message templates for common responses"""
    
    # Status messages
    WELCOME = "👋 Welcome to {company_name} Logistics!\n\nSend any 10+ digit number to track your delivery note."
    
    HELP = """📋 *Available Commands*

• *Track DN* - Send any 10+ digit number
• *Dealer Performance* - "Show dealer ABC Traders"
• *Warehouse Status* - "Stock at Mumbai warehouse"
• *Pending PODs* - "Pending POD Lahore"
• *KPI Dashboard* - "Show me KPIs"
• *Regional Report* - "North region performance"

Need help? Reply with your question."""
    
    ERROR = "⚠️ *Error*\n\n{error_message}\n\nPlease try again or contact support."
    
    LOADING = "⏳ Processing your request... Please wait."
    
    NOT_FOUND = "🔍 *Not Found*\n\nNo information found for: {query}\n\nPlease check and try again."
    
    DN_STATUS = """📋 *DN Intelligence Report*
━━━━━━━━━━━━━━━━━━━━━

*DN Number:* {dn_number}
*Date:* {date}
*Status:* {status}
*Priority:* {status_priority}

*Customer Details:*
• Name: {customer_name}
• City: {city}
• Region: {region}

*Order Details:*
• Amount: {amount}
• Items: {items_count}
• Warehouse: {warehouse}

*Tracking:*
• POD Status: {pod_status}
• PGI Status: {pgi_status}
• Aging: {aging_days} days

*Summary:* {summary}
━━━━━━━━━━━━━━━━━━━━━"""
    
    DEALER_REPORT = """📊 *Dealer Performance Report*
━━━━━━━━━━━━━━━━━━━━━

*Dealer:* {dealer_name}
*City:* {dealer_city}
*Region:* {dealer_region}

*Performance Metrics:*
• Total DNs: {total_dns}
• Completed: {completed_dns}
• Pending: {pending_count}
• Total Value: {total_value}
• Avg Delivery: {avg_delivery_days} days

*Score:* {score}% {score_icon}
━━━━━━━━━━━━━━━━━━━━━"""
    
    WAREHOUSE_STATUS = """🏭 *Warehouse Status Report*
━━━━━━━━━━━━━━━━━━━━━

*Warehouse:* {warehouse_name}
*City:* {warehouse_city}
*Region:* {warehouse_region}

*Capacity:* {capacity_used}/{capacity_total}
*Utilization:* {capacity_percentage}%

*Performance:*
• DNs Handled: {total_dns_handled}
• PGI Completed: {pgi_completed}
• PGI Pending: {pgi_pending}
• Avg Processing: {avg_pgi_processing_days} days

*Status:* {status_icon} {status}
━━━━━━━━━━━━━━━━━━━━━"""
    
    KPI_DASHBOARD = """📈 *Executive KPI Dashboard*
━━━━━━━━━━━━━━━━━━━━━

*Network Health:* {network_score}% {network_icon}
*POD Performance:* {pod_score}% {pod_icon}
*PGI Performance:* {pgi_score}% {pgi_icon}
*Delivery Performance:* {delivery_score}% {delivery_icon}

*Overall Score:* {overall_score}% {overall_icon}

*Top Priorities:*
{priorities}
━━━━━━━━━━━━━━━━━━━━━"""
    
    PENDING_REPORT = """⏳ *Pending Items Report*
━━━━━━━━━━━━━━━━━━━━━

*Summary:*
• Total Pending: {total_pending}
• High Priority: {high_priority} 🔴
• Medium Priority: {medium_priority} 🟡
• Low Priority: {low_priority} 🟢

*Breakdown:*
• Pending PODs: {pending_pods}
• Pending PGI: {pending_pgi}
• Pending Deliveries: {pending_deliveries}

*Top Dealers:*
{top_dealers}
━━━━━━━━━━━━━━━━━━━━━"""
    
    REGION_COMPARISON = """🌍 *Region Performance Comparison*
━━━━━━━━━━━━━━━━━━━━━

*Top Region:* {top_region} 🏆
*Score:* {top_score}%

*Bottom Region:* {bottom_region}
*Score:* {bottom_score}%

*Rankings:*
{rankings}
━━━━━━━━━━━━━━━━━━━━━"""
    
    ALERT = """🚨 *Critical Alert*
━━━━━━━━━━━━━━━━━━━━━

{message}

*Severity:* {severity} {severity_icon}
*Action Required:* {action}
━━━━━━━━━━━━━━━━━━━━━"""
    
    RECOMMENDATION = """💡 *AI Recommendation*
━━━━━━━━━━━━━━━━━━━━━

{recommendation}

*Impact:* {impact}
*Timeline:* {timeline}
━━━━━━━━━━━━━━━━━━━━━"""


# ==========================================================
# WHATSAPP SERVICE
# ==========================================================

class WhatsAppService:
    """
    WhatsApp Cloud API Communication Layer
    Handles all message sending, delivery tracking, and template management
    """
    
    def __init__(self):
        self.access_token = config.WHATSAPP_ACCESS_TOKEN
        self.phone_number_id = config.WHATSAPP_PHONE_NUMBER_ID
        self.api_version = "v18.0"
        
        self.base_url = f"https://graph.facebook.com/{self.api_version}/{self.phone_number_id}/messages"
        self.media_url = f"https://graph.facebook.com/{self.api_version}/{self.phone_number_id}/media"
        
        # Create session with retry strategy
        self.session = self._create_session()
        
        # Tracking
        self._rate_limit_remaining = None
        self._rate_limit_reset = None
        self._message_tracking = {}  # message_id -> status tracking
        
        # Company name for templates
        self.company_name = config.COMPANY_NAME if hasattr(config, 'COMPANY_NAME') else "Supply Chain"
        
        logger.info(f"WhatsApp Service v4.0 initialized (API v{self.api_version})")
    
    def _create_session(self) -> requests.Session:
        """Create requests session with retry strategy"""
        session = requests.Session()
        
        retry_strategy = Retry(
            total=3,
            backoff_factor=1,
            status_forcelist=[429, 500, 502, 503, 504],
            allowed_methods=["GET", "POST"]
        )
        
        adapter = HTTPAdapter(
            max_retries=retry_strategy,
            pool_connections=10,
            pool_maxsize=20
        )
        
        session.mount("https://", adapter)
        session.mount("http://", adapter)
        
        return session
    
    def _get_headers(self) -> Dict[str, str]:
        """Get request headers"""
        return {
            "Authorization": f"Bearer {self.access_token}",
            "Content-Type": "application/json"
        }
    
    def _clean_phone_number(self, phone_number: str) -> str:
        """Clean and format phone number for WhatsApp"""
        # Remove all non-digit characters
        cleaned = re.sub(r'\D', '', phone_number)
        
        # Ensure proper format (add country code if needed)
        if len(cleaned) == 10:
            cleaned = '91' + cleaned  # Default India country code
        elif len(cleaned) == 12 and cleaned.startswith('91'):
            pass  # Already has India code
        elif not cleaned.startswith('1') and len(cleaned) == 11:
            cleaned = cleaned  # Already has country code
        
        return cleaned
    
    def _get_status_icon(self, score: float) -> str:
        """Get status icon based on score"""
        if score >= 95:
            return "💚"
        elif score >= 90:
            return "🟢"
        elif score >= 80:
            return "🟡"
        elif score >= 70:
            return "🟠"
        else:
            return "🔴"
    
    def _format_currency(self, amount: float) -> str:
        """Format currency amount"""
        if amount >= 10000000:
            return f"₹{amount/10000000:.1f}Cr"
        elif amount >= 100000:
            return f"₹{amount/100000:.1f}L"
        else:
            return f"₹{amount:,.0f}"
    
    def _format_phone_display(self, phone_number: str) -> str:
        """Format phone number for display"""
        cleaned = self._clean_phone_number(phone_number)
        if len(cleaned) == 12:
            return f"+{cleaned[:2]} {cleaned[2:7]} {cleaned[7:]}"
        return cleaned
    
    # ==========================================================
    # CORE MESSAGE SENDING METHODS
    # ==========================================================
    
    def send_text_message(
        self,
        phone_number: str,
        message: str,
        preview_url: bool = False,
        message_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Send a text message via WhatsApp Cloud API
        
        Args:
            phone_number: Recipient's phone number
            message: Message content (supports markdown-like formatting)
            preview_url: Whether to show URL previews
            message_id: Optional message ID for tracking
        
        Returns:
            Dict with success status, message_id, and response data
        """
        
        if not self.access_token or not self.phone_number_id:
            logger.error("WhatsApp credentials missing")
            return {
                "success": False,
                "error": "WhatsApp service not configured",
                "status_code": 500
            }
        
        cleaned_number = self._clean_phone_number(phone_number)
        
        # Check message length (WhatsApp limit is 4096)
        if len(message) > 4000:
            message = message[:3997] + "..."
            logger.warning(f"Message truncated to {len(message)} chars")
        
        try:
            headers = self._get_headers()
            
            payload = {
                "messaging_product": "whatsapp",
                "recipient_type": "individual",
                "to": cleaned_number,
                "type": "text",
                "text": {
                    "preview_url": preview_url,
                    "body": message
                }
            }
            
            # Add context if this is a reply
            if message_id:
                payload["context"] = {"message_id": message_id}
            
            logger.debug(f"Sending message to {cleaned_number}: {message[:50]}...")
            
            response = self.session.post(
                self.base_url,
                headers=headers,
                json=payload,
                timeout=30
            )
            
            result = response.json()
            
            if response.status_code in [200, 201]:
                response_message_id = None
                if result.get("messages"):
                    response_message_id = result["messages"][0].get("id")
                    
                    # Track message
                    self._message_tracking[response_message_id] = {
                        "to": cleaned_number,
                        "status": MessageStatus.SENT.value,
                        "sent_at": datetime.utcnow().isoformat(),
                        "type": "text"
                    }
                
                logger.success(
                    f"✅ Message sent | To: {cleaned_number} | "
                    f"MsgID: {response_message_id}"
                )
                
                return {
                    "success": True,
                    "status_code": response.status_code,
                    "message_id": response_message_id,
                    "response": result,
                    "to": cleaned_number,
                    "timestamp": datetime.utcnow().isoformat()
                }
            
            # Handle error cases
            error_data = result.get("error", {})
            error_code = error_data.get("code")
            error_message = error_data.get("message", "Unknown error")
            
            logger.error(
                f"❌ API Error | To: {cleaned_number} | "
                f"Status: {response.status_code} | Error: {error_message}"
            )
            
            return {
                "success": False,
                "status_code": response.status_code,
                "error": error_message,
                "error_code": error_code,
                "response": result
            }
            
        except requests.Timeout:
            logger.error(f"Timeout sending message to {cleaned_number}")
            return {
                "success": False,
                "error": "Request timeout",
                "status_code": 408
            }
        except requests.ConnectionError as e:
            logger.error(f"Connection error: {e}")
            return {
                "success": False,
                "error": "Connection error",
                "status_code": 503
            }
        except Exception as e:
            logger.exception(f"Unexpected error sending message: {e}")
            return {
                "success": False,
                "error": str(e),
                "status_code": 500
            }
    
    def send_template_message(
        self,
        phone_number: str,
        template_name: str,
        language: str = "en_US",
        components: Optional[List[Dict]] = None,
        button_payload: Optional[Dict] = None
    ) -> Dict[str, Any]:
        """
        Send a template message via WhatsApp Cloud API
        
        Args:
            phone_number: Recipient's phone number
            template_name: Name of the approved template
            language: Language code (e.g., 'en_US', 'hi_IN')
            components: Optional template components
            button_payload: Optional button payload
        
        Returns:
            Dict with success status and response data
        """
        
        if not self.access_token or not self.phone_number_id:
            return {
                "success": False,
                "error": "WhatsApp service not configured"
            }
        
        cleaned_number = self._clean_phone_number(phone_number)
        
        try:
            headers = self._get_headers()
            
            payload = {
                "messaging_product": "whatsapp",
                "to": cleaned_number,
                "type": "template",
                "template": {
                    "name": template_name,
                    "language": {
                        "code": language
                    }
                }
            }
            
            if components:
                payload["template"]["components"] = components
            
            if button_payload:
                payload["template"]["components"] = payload["template"].get("components", [])
                payload["template"]["components"].append({
                    "type": "button",
                    "sub_type": "quick_reply",
                    "index": 0,
                    "parameters": [{"type": "payload", "payload": json.dumps(button_payload)}]
                })
            
            response = self.session.post(
                self.base_url,
                headers=headers,
                json=payload,
                timeout=30
            )
            
            result = response.json()
            
            if response.status_code in [200, 201]:
                message_id = None
                if result.get("messages"):
                    message_id = result["messages"][0].get("id")
                    
                    self._message_tracking[message_id] = {
                        "to": cleaned_number,
                        "status": MessageStatus.SENT.value,
                        "sent_at": datetime.utcnow().isoformat(),
                        "type": "template",
                        "template": template_name
                    }
                
                logger.success(f"✅ Template sent | To: {cleaned_number} | Template: {template_name}")
                
                return {
                    "success": True,
                    "status_code": response.status_code,
                    "message_id": message_id,
                    "response": result
                }
            
            logger.error(f"❌ Template error | Status: {response.status_code} | Error: {result}")
            
            return {
                "success": False,
                "status_code": response.status_code,
                "error": result.get("error", {}).get("message", "Unknown error"),
                "response": result
            }
            
        except Exception as e:
            logger.exception(f"Template send failed: {e}")
            return {
                "success": False,
                "error": str(e)
            }
    
    # ==========================================================
    # DOCUMENT & MEDIA SENDING METHODS
    # ==========================================================
    
    def send_document(
        self,
        phone_number: str,
        document_url: str,
        caption: Optional[str] = None,
        filename: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Send a document via WhatsApp
        
        Args:
            phone_number: Recipient's phone number
            document_url: Public URL of the document
            caption: Optional caption for the document
            filename: Optional filename (will be extracted from URL if not provided)
        
        Returns:
            Dict with success status
        """
        
        if not self.access_token or not self.phone_number_id:
            return {"success": False, "error": "Service not configured"}
        
        cleaned_number = self._clean_phone_number(phone_number)
        
        try:
            headers = self._get_headers()
            
            if not filename:
                filename = document_url.split("/")[-1] or "document.pdf"
            
            payload = {
                "messaging_product": "whatsapp",
                "to": cleaned_number,
                "type": "document",
                "document": {
                    "link": document_url,
                    "caption": caption or "",
                    "filename": filename
                }
            }
            
            response = self.session.post(
                self.base_url,
                headers=headers,
                json=payload,
                timeout=60
            )
            
            result = response.json()
            
            if response.status_code in [200, 201]:
                logger.success(f"✅ Document sent to {cleaned_number}: {filename}")
                return {
                    "success": True,
                    "status_code": response.status_code,
                    "message_id": result.get("messages", [{}])[0].get("id")
                }
            
            return {
                "success": False,
                "error": result.get("error", {}).get("message", "Unknown error"),
                "status_code": response.status_code
            }
            
        except Exception as e:
            logger.error(f"Failed to send document: {e}")
            return {"success": False, "error": str(e)}
    
    def send_image(
        self,
        phone_number: str,
        image_url: str,
        caption: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Send an image via WhatsApp
        
        Args:
            phone_number: Recipient's phone number
            image_url: Public URL of the image
            caption: Optional caption
        
        Returns:
            Dict with success status
        """
        
        if not self.access_token or not self.phone_number_id:
            return {"success": False, "error": "Service not configured"}
        
        cleaned_number = self._clean_phone_number(phone_number)
        
        try:
            headers = self._get_headers()
            
            payload = {
                "messaging_product": "whatsapp",
                "to": cleaned_number,
                "type": "image",
                "image": {
                    "link": image_url,
                    "caption": caption or ""
                }
            }
            
            response = self.session.post(
                self.base_url,
                headers=headers,
                json=payload,
                timeout=60
            )
            
            result = response.json()
            
            if response.status_code in [200, 201]:
                logger.success(f"✅ Image sent to {cleaned_number}")
                return {
                    "success": True,
                    "status_code": response.status_code,
                    "message_id": result.get("messages", [{}])[0].get("id")
                }
            
            return {
                "success": False,
                "error": result.get("error", {}).get("message", "Unknown error"),
                "status_code": response.status_code
            }
            
        except Exception as e:
            logger.error(f"Failed to send image: {e}")
            return {"success": False, "error": str(e)}
    
    def send_interactive_list(
        self,
        phone_number: str,
        header_text: str,
        body_text: str,
        button_text: str,
        sections: List[Dict[str, Any]]
    ) -> Dict[str, Any]:
        """
        Send an interactive list message for user choices
        
        Args:
            phone_number: Recipient's phone number
            header_text: Header text (optional)
            body_text: Main body text
            button_text: Button label text
            sections: List sections with options
        
        Returns:
            Dict with success status
        """
        
        if not self.access_token or not self.phone_number_id:
            return {"success": False, "error": "Service not configured"}
        
        cleaned_number = self._clean_phone_number(phone_number)
        
        try:
            headers = self._get_headers()
            
            payload = {
                "messaging_product": "whatsapp",
                "to": cleaned_number,
                "type": "interactive",
                "interactive": {
                    "type": "list",
                    "header": {"type": "text", "text": header_text} if header_text else None,
                    "body": {"text": body_text},
                    "action": {
                        "button": button_text,
                        "sections": sections
                    }
                }
            }
            
            # Remove None values
            if not header_text:
                del payload["interactive"]["header"]
            
            response = self.session.post(
                self.base_url,
                headers=headers,
                json=payload,
                timeout=30
            )
            
            result = response.json()
            
            if response.status_code in [200, 201]:
                logger.success(f"✅ Interactive list sent to {cleaned_number}")
                return {
                    "success": True,
                    "status_code": response.status_code,
                    "message_id": result.get("messages", [{}])[0].get("id")
                }
            
            return {
                "success": False,
                "error": result.get("error", {}).get("message", "Unknown error"),
                "status_code": response.status_code
            }
            
        except Exception as e:
            logger.error(f"Failed to send interactive list: {e}")
            return {"success": False, "error": str(e)}
    
    # ==========================================================
    # FORMATTED RESPONSE METHODS (Using Templates)
    # ==========================================================
    
    def send_welcome_message(self, phone_number: str) -> Dict[str, Any]:
        """Send welcome message to new users"""
        message = MessageTemplates.WELCOME.format(company_name=self.company_name)
        message += "\n\n" + MessageTemplates.HELP
        return self.send_text_message(phone_number, message)
    
    def send_help_message(self, phone_number: str) -> Dict[str, Any]:
        """Send help message with available commands"""
        return self.send_text_message(phone_number, MessageTemplates.HELP)
    
    def send_error_message(self, phone_number: str, error: str) -> Dict[str, Any]:
        """Send error message"""
        message = MessageTemplates.ERROR.format(error_message=error)
        return self.send_text_message(phone_number, message)
    
    def send_loading_indicator(self, phone_number: str) -> Dict[str, Any]:
        """Send loading indicator (typing simulation)"""
        # Note: WhatsApp doesn't support typing indicators via API
        # This sends a loading message instead
        return self.send_text_message(phone_number, MessageTemplates.LOADING)
    
    def send_dn_intelligence(self, phone_number: str, dn_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Send formatted DN intelligence report
        
        Args:
            phone_number: Recipient's phone number
            dn_data: DN intelligence data from logistics service
        
        Returns:
            Send result
        """
        
        # Format amount
        amount = dn_data.get('amount', 0)
        formatted_amount = self._format_currency(amount)
        
        # Get status icon
        aging = dn_data.get('aging_days', 0)
        if dn_data.get('pod_status') == 'RECEIVED':
            status_icon = "✅"
        elif aging > 7:
            status_icon = "🔴"
        elif aging > 3:
            status_icon = "🟡"
        else:
            status_icon = "🟢"
        
        message = MessageTemplates.DN_STATUS.format(
            dn_number=dn_data.get('dn_number', 'N/A'),
            date=dn_data.get('date', 'N/A'),
            status=f"{status_icon} {dn_data.get('status', 'N/A')}",
            status_priority=dn_data.get('status_priority', 'Normal'),
            customer_name=dn_data.get('customer_name', 'N/A'),
            city=dn_data.get('city', 'N/A'),
            region=dn_data.get('region', 'N/A'),
            amount=formatted_amount,
            items_count=dn_data.get('items_count', 0),
            warehouse=dn_data.get('warehouse', 'N/A'),
            pod_status=dn_data.get('pod_status', 'N/A'),
            pgi_status=dn_data.get('pgi_status', 'N/A'),
            aging_days=aging,
            summary=dn_data.get('summary', 'No summary available')
        )
        
        return self.send_text_message(phone_number, message)
    
    def send_dealer_report(self, phone_number: str, dealer_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Send formatted dealer performance report
        
        Args:
            phone_number: Recipient's phone number
            dealer_data: Dealer performance data
        
        Returns:
            Send result
        """
        
        # Calculate score
        total_dns = dealer_data.get('total_dns', 0)
        completed_dns = dealer_data.get('completed_count', 0)
        score = (completed_dns / total_dns * 100) if total_dns > 0 else 0
        
        score_icon = self._get_status_icon(score)
        
        message = MessageTemplates.DEALER_REPORT.format(
            dealer_name=dealer_data.get('dealer_name', 'N/A'),
            dealer_city=dealer_data.get('dealer_city', 'N/A'),
            dealer_region=dealer_data.get('dealer_region', 'N/A'),
            total_dns=total_dns,
            completed_dns=completed_dns,
            pending_count=dealer_data.get('pending_count', 0),
            total_value=self._format_currency(dealer_data.get('total_value', 0)),
            avg_delivery_days=dealer_data.get('avg_delivery_days', 0),
            score=round(score, 1),
            score_icon=score_icon
        )
        
        # Add recent orders if available
        recent_orders = dealer_data.get('recent_orders', [])
        if recent_orders:
            message += "\n\n*Recent Orders:*\n"
            for order in recent_orders[:3]:
                message += f"• {order.get('dn_number')}: {self._format_currency(order.get('amount', 0))}\n"
        
        return self.send_text_message(phone_number, message)
    
    def send_warehouse_status(self, phone_number: str, warehouse_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Send formatted warehouse status report
        
        Args:
            phone_number: Recipient's phone number
            warehouse_data: Warehouse status data
        
        Returns:
            Send result
        """
        
        capacity_percentage = warehouse_data.get('capacity_percentage', 0)
        
        if capacity_percentage > 85:
            status_icon = "🔴"
            status = "Critical Capacity"
        elif capacity_percentage > 70:
            status_icon = "🟡"
            status = "High Utilization"
        else:
            status_icon = "🟢"
            status = "Optimal"
        
        message = MessageTemplates.WAREHOUSE_STATUS.format(
            warehouse_name=warehouse_data.get('warehouse_name', 'N/A'),
            warehouse_city=warehouse_data.get('warehouse_city', 'N/A'),
            warehouse_region=warehouse_data.get('warehouse_region', 'N/A'),
            capacity_used=warehouse_data.get('capacity_used', 0),
            capacity_total=warehouse_data.get('capacity_total', 0),
            capacity_percentage=capacity_percentage,
            total_dns_handled=warehouse_data.get('total_dns_handled', 0),
            pgi_completed=warehouse_data.get('pgi_completed', 0),
            pgi_pending=warehouse_data.get('pgi_pending', 0),
            avg_pgi_processing_days=warehouse_data.get('avg_pgi_processing_days', 0),
            status_icon=status_icon,
            status=status
        )
        
        return self.send_text_message(phone_number, message)
    
    def send_kpi_dashboard(self, phone_number: str, dashboard_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Send formatted KPI dashboard
        
        Args:
            phone_number: Recipient's phone number
            dashboard_data: KPI dashboard data
        
        Returns:
            Send result
        """
        
        summary = dashboard_data.get('executive_summary', {})
        
        network_score = dashboard_data.get('network_health', {}).get('overall_score', 0)
        pod_score = dashboard_data.get('pod_performance', {}).get('overall_score', 0)
        pgi_score = dashboard_data.get('pgi_performance', {}).get('overall_score', 0)
        delivery_score = dashboard_data.get('delivery_performance', {}).get('overall_score', 0)
        overall_score = summary.get('overall_score', 0)
        
        # Format priorities
        priorities = dashboard_data.get('top_priorities', [])
        priorities_text = "\n".join([f"• {p}" for p in priorities[:3]]) if priorities else "• Maintain current performance"
        
        message = MessageTemplates.KPI_DASHBOARD.format(
            network_score=round(network_score, 1),
            network_icon=self._get_status_icon(network_score),
            pod_score=round(pod_score, 1),
            pod_icon=self._get_status_icon(pod_score),
            pgi_score=round(pgi_score, 1),
            pgi_icon=self._get_status_icon(pgi_score),
            delivery_score=round(delivery_score, 1),
            delivery_icon=self._get_status_icon(delivery_score),
            overall_score=round(overall_score, 1),
            overall_icon=self._get_status_icon(overall_score),
            priorities=priorities_text
        )
        
        return self.send_text_message(phone_number, message)
    
    def send_pending_report(self, phone_number: str, pending_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Send formatted pending items report
        
        Args:
            phone_number: Recipient's phone number
            pending_data: Pending items data
        
        Returns:
            Send result
        """
        
        top_dealers = pending_data.get('top_dealers', [])
        dealers_text = "\n".join([
            f"  {i+1}. {d['name']}: {d['pending_count']} items"
            for i, d in enumerate(top_dealers[:5])
        ]) if top_dealers else "  No pending items"
        
        message = MessageTemplates.PENDING_REPORT.format(
            total_pending=pending_data.get('total_pending', 0),
            high_priority=pending_data.get('high_priority', 0),
            medium_priority=pending_data.get('medium_priority', 0),
            low_priority=pending_data.get('low_priority', 0),
            pending_pods=pending_data.get('pending_pods', 0),
            pending_pgi=pending_data.get('pending_pgi', 0),
            pending_deliveries=pending_data.get('pending_deliveries', 0),
            top_dealers=dealers_text
        )
        
        return self.send_text_message(phone_number, message)
    
    def send_region_comparison(self, phone_number: str, region_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Send formatted region comparison report
        
        Args:
            phone_number: Recipient's phone number
            region_data: Region comparison data
        
        Returns:
            Send result
        """
        
        regions = region_data.get('regions', [])
        summary = region_data.get('summary', {})
        
        # Format rankings
        rankings_text = ""
        for i, region in enumerate(regions[:5], 1):
            icon = "🏆" if i == 1 else "📊" if i == 2 else "📈" if i == 3 else "📍"
            rankings_text += f"{icon} {i}. {region.get('region')}: {region.get('overall_score', 0)}%\n"
        
        message = MessageTemplates.REGION_COMPARISON.format(
            top_region=summary.get('top_region', 'N/A'),
            top_score=summary.get('top_score', 0),
            bottom_region=summary.get('bottom_region', 'N/A'),
            bottom_score=summary.get('bottom_score', 0),
            rankings=rankings_text.strip()
        )
        
        return self.send_text_message(phone_number, message)
    
    def send_alert(self, phone_number: str, alert_type: str, message_text: str, severity: str) -> Dict[str, Any]:
        """
        Send alert message
        
        Args:
            phone_number: Recipient's phone number
            alert_type: Type of alert
            message_text: Alert message
            severity: Severity level (HIGH/MEDIUM/LOW)
        
        Returns:
            Send result
        """
        
        severity_icons = {
            "HIGH": "🔴",
            "MEDIUM": "🟡",
            "LOW": "🟢"
        }
        
        actions = {
            "HIGH": "Immediate action required",
            "MEDIUM": "Review within 24 hours",
            "LOW": "Monitor progress"
        }
        
        message = MessageTemplates.ALERT.format(
            message=message_text,
            severity=severity,
            severity_icon=severity_icons.get(severity, "⚠️"),
            action=actions.get(severity, "Review as needed")
        )
        
        return self.send_text_message(phone_number, message)
    
    def send_recommendation(self, phone_number: str, recommendation: str, impact: str, timeline: str) -> Dict[str, Any]:
        """
        Send AI recommendation
        
        Args:
            phone_number: Recipient's phone number
            recommendation: Recommendation text
            impact: Expected impact
            timeline: Implementation timeline
        
        Returns:
            Send result
        """
        
        message = MessageTemplates.RECOMMENDATION.format(
            recommendation=recommendation,
            impact=impact,
            timeline=timeline
        )
        
        return self.send_text_message(phone_number, message)
    
    def send_ai_summary(self, phone_number: str, summary_text: str) -> Dict[str, Any]:
        """
        Send AI-generated summary
        
        Args:
            phone_number: Recipient's phone number
            summary_text: AI summary text
        
        Returns:
            Send result
        """
        
        formatted_summary = f"🤖 *AI Summary*\n━━━━━━━━━━━━━━━━━━━━━\n\n{summary_text}\n━━━━━━━━━━━━━━━━━━━━━"
        
        return self.send_text_message(phone_number, formatted_summary)
    
    # ==========================================================
    # MESSAGE TRACKING & STATUS METHODS
    # ==========================================================
    
    def get_message_status(self, message_id: str) -> Optional[Dict[str, Any]]:
        """
        Get status of a sent message
        
        Args:
            message_id: Message ID from WhatsApp
        
        Returns:
            Message status info or None
        """
        
        if message_id in self._message_tracking:
            return self._message_tracking[message_id]
        
        # Try to fetch from WhatsApp API
        try:
            headers = self._get_headers()
            url = f"https://graph.facebook.com/{self.api_version}/{message_id}"
            
            response = self.session.get(url, headers=headers, timeout=10)
            
            if response.status_code == 200:
                data = response.json()
                return {
                    "message_id": message_id,
                    "status": data.get("status", "unknown"),
                    "conversation": data.get("conversation"),
                    "timestamp": data.get("timestamp")
                }
            
            return None
            
        except Exception as e:
            logger.error(f"Failed to get message status: {e}")
            return None
    
    def mark_message_as_read(self, message_id: str) -> Dict[str, Any]:
        """
        Mark a message as read (send read receipt)
        
        Args:
            message_id: ID of the message to mark as read
        
        Returns:
            Dict with success status
        """
        
        if not self.access_token or not self.phone_number_id:
            return {"success": False, "error": "Service not configured"}
        
        try:
            headers = self._get_headers()
            
            payload = {
                "messaging_product": "whatsapp",
                "status": "read",
                "message_id": message_id
            }
            
            response = self.session.post(
                self.base_url,
                headers=headers,
                json=payload,
                timeout=10
            )
            
            success = response.status_code in [200, 201]
            
            if success:
                logger.debug(f"✅ Marked as read | MsgID: {message_id}")
            
            return {
                "success": success,
                "status_code": response.status_code
            }
            
        except Exception as e:
            logger.error(f"Failed to mark as read: {e}")
            return {"success": False, "error": str(e)}
    
    # ==========================================================
    # BULK SENDING METHODS
    # ==========================================================
    
    def bulk_send(self, phone_numbers: List[str], message: str) -> List[Dict[str, Any]]:
        """
        Send same message to multiple recipients
        
        Args:
            phone_numbers: List of phone numbers
            message: Message to send
        
        Returns:
            List of results for each recipient
        """
        
        results = []
        for phone in phone_numbers:
            result = self.send_text_message(phone, message)
            results.append(result)
            # Small delay to avoid rate limits
            time.sleep(0.5)
        
        return results
    
    async def bulk_send_async(self, phone_numbers: List[str], message: str) -> List[Dict[str, Any]]:
        """
        Send same message to multiple recipients asynchronously
        
        Args:
            phone_numbers: List of phone numbers
            message: Message to send
        
        Returns:
            List of results for each recipient
        """
        
        tasks = [self._async_send(phone, message) for phone in phone_numbers]
        results = await asyncio.gather(*tasks)
        return results
    
    async def _async_send(self, phone_number: str, message: str) -> Dict[str, Any]:
        """Async wrapper for send_text_message"""
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, self.send_text_message, phone_number, message)
    
    # ==========================================================
    # HEALTH CHECK & UTILITIES
    # ==========================================================
    
    def health_check(self) -> Dict[str, Any]:
        """
        Check service health and configuration
        
        Returns:
            Dict with service status and configuration info
        """
        
        return {
            "service": "whatsapp",
            "version": "4.0",
            "configured": bool(self.access_token and self.phone_number_id),
            "api_version": self.api_version,
            "phone_number_id": self.phone_number_id[:6] + "..." if self.phone_number_id else None,
            "token_configured": bool(self.access_token),
            "message_tracking_count": len(self._message_tracking),
            "base_url": self.base_url.replace(self.access_token, "***") if self.access_token else None
        }
    
    def get_media_url(self, media_id: str) -> Optional[str]:
        """
        Get download URL for media file
        
        Args:
            media_id: Media ID from WhatsApp
        
        Returns:
            Download URL or None
        """
        
        try:
            headers = self._get_headers()
            url = f"https://graph.facebook.com/{self.api_version}/{media_id}"
            
            response = self.session.get(url, headers=headers, timeout=30)
            
            if response.status_code == 200:
                data = response.json()
                return data.get("url")
            
            logger.error(f"Failed to get media URL: {response.status_code}")
            return None
            
        except Exception as e:
            logger.error(f"Error getting media URL: {e}")
            return None
    
    def upload_media(self, file_path: str, mime_type: str) -> Optional[str]:
        """
        Upload media to WhatsApp
        
        Args:
            file_path: Path to file
            mime_type: MIME type of file
        
        Returns:
            Media ID or None
        """
        
        try:
            headers = {
                "Authorization": f"Bearer {self.access_token}"
            }
            
            with open(file_path, "rb") as f:
                files = {
                    "file": (file_path, f, mime_type),
                    "type": (None, mime_type),
                    "messaging_product": (None, "whatsapp")
                }
                
                response = self.session.post(
                    self.media_url,
                    headers=headers,
                    files=files,
                    timeout=60
                )
            
            if response.status_code == 200:
                data = response.json()
                return data.get("id")
            
            logger.error(f"Failed to upload media: {response.status_code}")
            return None
            
        except Exception as e:
            logger.error(f"Error uploading media: {e}")
            return None


# ==========================================================
# SINGLETON INSTANCE
# ==========================================================

_whatsapp_service = None


def get_whatsapp_service() -> WhatsAppService:
    """Get or create the singleton WhatsApp service instance"""
    
    global _whatsapp_service
    
    if _whatsapp_service is None:
        _whatsapp_service = WhatsAppService()
    
    return _whatsapp_service


# ==========================================================
# COMPATIBILITY FUNCTIONS (Used by webhook.py)
# ==========================================================

def send_text_message(phone_number: str, message: str) -> Dict[str, Any]:
    """
    Compatibility function for webhook.py
    Direct function call interface
    
    Args:
        phone_number: Recipient's phone number
        message: Message text
    
    Returns:
        Dict with success status and details
    """
    service = get_whatsapp_service()
    return service.send_text_message(phone_number=phone_number, message=message)


def send_template_message(phone_number: str, template_name: str, **kwargs) -> Dict[str, Any]:
    """
    Compatibility function for template messages
    
    Args:
        phone_number: Recipient's phone number
        template_name: Template name
        **kwargs: Additional parameters (language, components)
    
    Returns:
        Dict with success status
    """
    service = get_whatsapp_service()
    return service.send_template_message(
        phone_number=phone_number,
        template_name=template_name,
        language=kwargs.get('language', 'en_US'),
        components=kwargs.get('components'),
        button_payload=kwargs.get('button_payload')
    )


def send_document(phone_number: str, document_url: str, caption: str = None, filename: str = None) -> Dict[str, Any]:
    """Compatibility function for sending documents"""
    service = get_whatsapp_service()
    return service.send_document(phone_number, document_url, caption, filename)


def send_image(phone_number: str, image_url: str, caption: str = None) -> Dict[str, Any]:
    """Compatibility function for sending images"""
    service = get_whatsapp_service()
    return service.send_image(phone_number, image_url, caption)


def mark_message_as_read(message_id: str) -> Dict[str, Any]:
    """Compatibility function for marking messages as read"""
    service = get_whatsapp_service()
    return service.mark_message_as_read(message_id)


def get_message_status(message_id: str) -> Optional[Dict[str, Any]]:
    """Compatibility function for getting message status"""
    service = get_whatsapp_service()
    return service.get_message_status(message_id)


def send_welcome_message(phone_number: str) -> Dict[str, Any]:
    """Send welcome message to new users"""
    service = get_whatsapp_service()
    return service.send_welcome_message(phone_number)


def send_help_message(phone_number: str) -> Dict[str, Any]:
    """Send help message"""
    service = get_whatsapp_service()
    return service.send_help_message(phone_number)


# ==========================================================
# ASYNC COMPATIBILITY (for background tasks)
# ==========================================================

async def send_text_message_async(phone_number: str, message: str) -> Dict[str, Any]:
    """
    Async wrapper for send_text_message
    Use this for background tasks to avoid blocking
    """
    service = get_whatsapp_service()
    return await service._async_send(phone_number, message)


async def send_template_message_async(phone_number: str, template_name: str, **kwargs) -> Dict[str, Any]:
    """
    Async wrapper for send_template_message
    """
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(
        None, 
        lambda: send_template_message(phone_number, template_name, **kwargs)
    )


# ==========================================================
# INITIALIZATION LOG
# ==========================================================

logger.info("=" * 60)
logger.info("📱 WhatsApp Service v4.0 Loaded - Communication Layer")
logger.info(f"   API Version: v{get_whatsapp_service().api_version}")
logger.info(f"   Configured: {bool(get_whatsapp_service().access_token and get_whatsapp_service().phone_number_id)}")
logger.info("   Features: Text | Templates | Documents | Images | Interactive | Bulk Send")
logger.info("=" * 60)
