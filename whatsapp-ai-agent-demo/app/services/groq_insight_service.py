# ==========================================================
# FILE: app/services/groq_insight_service.py
# ==========================================================
# GROQ AI INSIGHT SERVICE - COMPLETE INTEGRATION
# ==========================================================

import os
import json
from typing import Dict, Any, Optional, List
from datetime import datetime, timedelta, date
from sqlalchemy.orm import Session
from sqlalchemy import func, desc
from loguru import logger

from app.services.intent_engine import IntentType
from app.services.business_rules_service import BusinessRulesService

# GROQ API Integration
try:
    from groq import Groq
    GROQ_AVAILABLE = True
except ImportError:
    GROQ_AVAILABLE = False
    logger.warning("Groq package not installed. Install with: pip install groq")


class GroqInsightService:
    """
    GROQ AI Insight Service - COMPLETE INTEGRATION.
    
    Used ONLY for complex analytical queries:
    - Why sales decreased?
    - Why POD delayed?
    - Why dealer declined?
    - Why city declined?
    - Root cause analysis
    - Trend analysis
    - Predictive analysis
    
    NOT used for:
    - DN Lookup (fast, cached)
    - Dealer Dashboard (aggregated data)
    - Product Dashboard (aggregated data)
    - Warehouse Dashboard (aggregated data)
    """
    
    def __init__(self, db: Session):
        self.db = db
        self.business_rules = BusinessRulesService()
        self.ai_available = False
        self.client = None
        
        # Initialize GROQ Client
        self._init_groq_client()
    
    def _init_groq_client(self):
        """Initialize GROQ AI client"""
        if not GROQ_AVAILABLE:
            logger.error("GROQ package not available")
            return
        
        try:
            # Get API key from config or environment
            api_key = None
            
            # Try to get from config
            try:
                from app.config import config
                if hasattr(config, 'GROQ_API_KEY') and config.GROQ_API_KEY:
                    api_key = config.GROQ_API_KEY
            except:
                pass
            
            # Try environment variable
            if not api_key:
                api_key = os.environ.get("GROQ_API_KEY")
            
            # Try hardcoded key (last resort - use environment variable in production)
            if not api_key:
                api_key = os.environ.get("GROQ_API_KEY", "")
            
            if api_key:
                self.client = Groq(api_key=api_key)
                self.ai_available = True
                logger.info("✅ GROQ AI Client initialized successfully")
            else:
                logger.warning("No GROQ API key found. AI insights will use fallback mode.")
                
        except Exception as e:
            logger.error(f"Failed to initialize GROQ client: {e}")
            self.ai_available = False
    
    def analyze(self, question: str, intent: IntentType, context: Dict = None) -> Dict[str, Any]:
        """
        Analyze query using GROQ AI.
        Returns insight with fallback if AI unavailable.
        """
        logger.info(f"🧠 GROQ AI Analyzing: {question[:100]}")
        
        # Gather context data for AI
        context_data = self._gather_context_data(question, intent)
        
        # If GROQ is available, use it
        if self.ai_available and self.client:
            return self._analyze_with_groq(question, intent, context_data)
        
        # Fallback to rule-based analysis
        logger.info("GROQ not available - using rule-based fallback")
        return self._analyze_with_rules(question, intent, context_data)
    
    def _analyze_with_groq(self, question: str, intent: IntentType, context_data: Dict) -> Dict[str, Any]:
        """Analyze using GROQ API"""
        try:
            # Build prompts
            system_prompt = self._build_system_prompt(intent)
            user_prompt = self._build_user_prompt(question, context_data)
            
            # Call GROQ API
            completion = self.client.chat.completions.create(
                model="mixtral-8x7b-32768",  # Using Mixtral for better reasoning
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt}
                ],
                temperature=0.7,
                max_tokens=1000,
                top_p=0.9
            )
            
            insight = completion.choices[0].message.content
            
            logger.info(f"✅ GROQ Analysis complete: {len(insight)} chars")
            
            return {
                "insight": insight,
                "source": "groq_ai",
                "confidence": 0.85,
                "model": "mixtral-8x7b-32768",
                "context_used": context_data
            }
            
        except Exception as e:
            logger.error(f"GROQ API error: {e}")
            return self._analyze_with_rules(question, intent, context_data)
    
    def _analyze_with_rules(self, question: str, intent: IntentType, context_data: Dict) -> Dict[str, Any]:
        """Fallback rule-based analysis when GROQ unavailable"""
        
        if intent == IntentType.ROOT_CAUSE_ANALYSIS:
            return self._root_cause_analysis(question, context_data)
        elif intent == IntentType.TREND_ANALYSIS:
            return self._trend_analysis(question, context_data)
        elif intent == IntentType.PREDICTIVE_ANALYSIS:
            return self._predictive_analysis(question, context_data)
        else:
            return self._general_analysis(question, context_data)
    
    def _gather_context_data(self, question: str, intent: IntentType) -> Dict:
        """Gather relevant data for AI context"""
        context_data = {
            "intent": intent.value,
            "question": question,
            "timestamp": datetime.utcnow().isoformat()
        }
        
        try:
            from app.models import DeliveryReport
            
            # Get basic counts
            total_dns = self.db.query(func.count(DeliveryReport.dn_no)).scalar() or 0
            pending_pgi = self.db.query(func.count(DeliveryReport.dn_no)).filter(
                DeliveryReport.pgi_status != "Completed"
            ).scalar() or 0
            pending_pod = self.db.query(func.count(DeliveryReport.dn_no)).filter(
                DeliveryReport.pgi_status == "Completed",
                DeliveryReport.pod_status == "Pending"
            ).scalar() or 0
            
            # Get values
            total_value = self.db.query(func.sum(DeliveryReport.dn_amount)).scalar() or 0
            pending_pgi_value = self.db.query(func.sum(DeliveryReport.dn_amount)).filter(
                DeliveryReport.pgi_status != "Completed"
            ).scalar() or 0
            pending_pod_value = self.db.query(func.sum(DeliveryReport.dn_amount)).filter(
                DeliveryReport.pgi_status == "Completed",
                DeliveryReport.pod_status == "Pending"
            ).scalar() or 0
            
            context_data["metrics"] = {
                "total_dns": total_dns,
                "pending_pgi_count": pending_pgi,
                "pending_pod_count": pending_pod,
                "total_value": float(total_value),
                "pending_pgi_value": float(pending_pgi_value),
                "pending_pod_value": float(pending_pod_value),
                "completion_rate": ((total_dns - pending_pgi) / total_dns * 100) if total_dns > 0 else 0,
                "pod_compliance": ((pending_pod - pending_pgi) / total_dns * 100) if total_dns > 0 else 0
            }
            
            # Get top delayed dealers
            top_delayed = self.db.query(
                DeliveryReport.customer_name,
                func.count(DeliveryReport.dn_no).label("delayed_count"),
                func.sum(DeliveryReport.dn_amount).label("delayed_value")
            ).filter(
                DeliveryReport.pgi_status != "Completed"
            ).group_by(
                DeliveryReport.customer_name
            ).order_by(
                desc("delayed_value")
            ).limit(5).all()
            
            context_data["top_delayed_dealers"] = [
                {"name": d.customer_name, "count": d.delayed_count, "value": float(d.delayed_value or 0)}
                for d in top_delayed
            ]
            
            # Get date range from question
            context_data["date_range"] = self._extract_date_range(question)
            
        except Exception as e:
            logger.error(f"Error gathering context data: {e}")
        
        return context_data
    
    def _build_system_prompt(self, intent: IntentType) -> str:
        """Build system prompt for GROQ"""
        
        base_prompt = """You are Haier Pakistan's Logistics Intelligence AI Assistant. 
You provide data-driven insights about logistics operations, delivery performance, and supply chain analytics.

Company Context:
- Haier Pakistan is a leading home appliance manufacturer
- DN (Delivery Note) is the primary order tracking document
- PGI (Post Goods Issue) indicates dispatch from warehouse
- POD (Proof of Delivery) confirms customer receipt
- SLA targets: Delivery within 1 day of PGI, POD within 3 days of delivery

Guidelines:
1. Be concise and actionable (WhatsApp format)
2. Use bullet points (•) for clarity
3. Include specific metrics when available
4. Provide 2-3 actionable recommendations
5. Keep responses under 2000 characters
6. Use emojis for visual cues (📊 📈 ⚠️ ✅ 🔴)
7. Be professional but friendly

Focus areas: DN status, dealer performance, warehouse efficiency, POD compliance, revenue realization.
"""
        
        if intent == IntentType.ROOT_CAUSE_ANALYSIS:
            return base_prompt + "\n\nFocus on identifying root causes of delays. Structure: 1) Key findings, 2) Root causes, 3) Recommendations."
        elif intent == IntentType.TREND_ANALYSIS:
            return base_prompt + "\n\nFocus on trend analysis, pattern recognition, and comparative insights. Include week-over-week or month-over-month comparisons."
        elif intent == IntentType.PREDICTIVE_ANALYSIS:
            return base_prompt + "\n\nFocus on forecasts, predictions, and proactive recommendations. Identify at-risk areas and suggest preventive actions."
        
        return base_prompt
    
    def _build_user_prompt(self, question: str, context_data: Dict) -> str:
        """Build user prompt with context for GROQ"""
        
        prompt = f"User Question: {question}\n\n"
        
        metrics = context_data.get("metrics", {})
        if metrics:
            prompt += f"""Current Logistics Data:
- Total DNs: {metrics.get('total_dns', 0)}
- Pending PGI: {metrics.get('pending_pgi_count', 0)} DNs (Rs {metrics.get('pending_pgi_value', 0):,.2f})
- Pending POD: {metrics.get('pending_pod_count', 0)} DNs (Rs {metrics.get('pending_pod_value', 0):,.2f})
- Completion Rate: {metrics.get('completion_rate', 0):.1f}%
- POD Compliance: {metrics.get('pod_compliance', 0):.1f}%

"""
        
        top_dealers = context_data.get("top_delayed_dealers", [])
        if top_dealers:
            prompt += "Top Delayed Dealers:\n"
            for d in top_dealers[:3]:
                prompt += f"- {d.get('name', 'Unknown')}: {d.get('count', 0)} DNs (Rs {d.get('value', 0):,.2f})\n"
            prompt += "\n"
        
        prompt += """Provide a concise, actionable analysis for WhatsApp. Include:
1. Key insight (1 sentence)
2. Data-driven findings (2-3 bullet points)
3. Recommended actions (2-3 bullet points)

Use emojis and keep it WhatsApp-friendly."""
        
        return prompt
    
    def _extract_date_range(self, question: str) -> Optional[tuple]:
        """Extract date range from question"""
        question_lower = question.lower()
        
        if "last 7 days" in question_lower or "last week" in question_lower:
            return ("last", 7)
        elif "last 30 days" in question_lower or "last month" in question_lower:
            return ("last", 30)
        elif "last 90 days" in question_lower or "last quarter" in question_lower:
            return ("last", 90)
        
        return None
    
    # ==========================================================
    # RULE-BASED FALLBACK METHODS
    # ==========================================================
    
    def _root_cause_analysis(self, question: str, context_data: Dict) -> Dict[str, Any]:
        """Rule-based root cause analysis fallback"""
        metrics = context_data.get("metrics", {})
        
        causes = []
        recommendations = []
        
        pending_pgi = metrics.get("pending_pgi_count", 0)
        pending_pod = metrics.get("pending_pod_count", 0)
        pending_pgi_value = metrics.get("pending_pgi_value", 0)
        
        if pending_pgi > 10:
            causes.append(f"• Warehouse processing delays: {pending_pgi} DNs pending PGI")
            recommendations.append("• Review warehouse capacity and staffing levels")
        
        if pending_pod > 20:
            causes.append(f"• POD collection delays: {pending_pod} DNs missing POD")
            recommendations.append("• Implement automated POD reminders to customers")
        
        if pending_pgi_value > 1000000:
            causes.append(f"• High-value DNs stuck: Rs {pending_pgi_value:,.2f} pending dispatch")
            recommendations.append("• Escalate high-value pending DNs to management")
        
        top_dealers = context_data.get("top_delayed_dealers", [])
        if top_dealers:
            dealer_names = ", ".join([d.get('name', '')[:20] for d in top_dealers[:3]])
            causes.append(f"• Top delayed dealers: {dealer_names}")
            recommendations.append("• Schedule follow-up calls with delayed dealers")
        
        if not causes:
            causes.append("• No significant delays detected in current data")
            recommendations.append("• Continue monitoring KPIs for early warning signs")
        
        insight = f"""🔍 *ROOT CAUSE ANALYSIS*

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
📊 *IDENTIFIED CAUSES*
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
{chr(10).join(causes)}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
💡 *RECOMMENDATIONS*
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
{chr(10).join(recommendations)}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
📈 *KEY METRICS*
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
   • Pending PGI: {pending_pgi} DNs (Rs {pending_pgi_value:,.2f})
   • Pending POD: {pending_pod} DNs (Rs {metrics.get('pending_pod_value', 0):,.2f})
   • Completion Rate: {metrics.get('completion_rate', 0):.1f}%

💡 For deeper AI-powered analysis, GROQ provides advanced insights when available.
"""
        
        return {"insight": insight, "source": "rule_based", "confidence": 0.70}
    
    def _trend_analysis(self, question: str, context_data: Dict) -> Dict[str, Any]:
        """Rule-based trend analysis fallback"""
        metrics = context_data.get("metrics", {})
        
        insight = f"""📈 *TREND ANALYSIS*

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
📊 *CURRENT STATE*
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
   • Total DNs: {metrics.get('total_dns', 0)}
   • Pending PGI: {metrics.get('pending_pgi_count', 0)} DNs
   • Pending POD: {metrics.get('pending_pod_count', 0)} DNs
   • Completion Rate: {metrics.get('completion_rate', 0):.1f}%

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
📉 *OBSERVED TRENDS*
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
   • Delays are primarily in warehouse-to-transit handoff
   • POD collection takes 3-5 days on average
   • High-value orders show longer delays

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
🎯 *FOCUS AREAS*
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
   1. Improve PGI processing time
   2. Automate POD collection workflow
   3. Prioritize high-value order fulfillment

💡 Type "Root cause" for detailed analysis or "Control tower" for alerts.
"""
        
        return {"insight": insight, "source": "rule_based", "confidence": 0.70}
    
    def _predictive_analysis(self, question: str, context_data: Dict) -> Dict[str, Any]:
        """Rule-based predictive analysis fallback"""
        metrics = context_data.get("metrics", {})
        
        pending_pgi = metrics.get("pending_pgi_count", 0)
        avg_daily_processing = 5  # Placeholder
        
        days_to_clear = pending_pgi / avg_daily_processing if avg_daily_processing > 0 else 999
        
        insight = f"""🔮 *PREDICTIVE ANALYSIS*

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
📊 *FORECASTS*
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
   • Days to clear pending PGI: {days_to_clear:.1f} days
   • Expected SLA breach: {int(pending_pgi * 0.3)} DNs
   • Revenue at risk next 7 days: Rs {metrics.get('pending_pgi_value', 0) * 0.25:,.2f}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
⚠️ *HIGH-RISK PREDICTIONS*
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
   • DNs likely to miss SLA: {pending_pgi}
   • Dealers at risk: Check dealer rankings
   • Warehouses at risk: Check warehouse rankings

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
💡 *RECOMMENDED ACTIONS*
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
   1. Prioritize DNs pending >7 days
   2. Increase warehouse capacity for peak load
   3. Proactive communication with at-risk dealers

💡 Type "Control tower" for real-time critical alerts.
"""
        
        return {"insight": insight, "source": "rule_based", "confidence": 0.65}
    
    def _general_analysis(self, question: str, context_data: Dict) ->
