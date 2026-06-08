# ==========================================================
# FILE: app/services/groq_insight_service.py (ENTERPRISE v3.0)
# ==========================================================
# GROQ AI INSIGHT SERVICE
# - AI-powered root cause analysis
# - Trend analysis
# - Predictive analysis
# - Intelligent recommendations
# ==========================================================

import os
import json
from typing import Dict, Any, Optional
from datetime import datetime, timedelta
from sqlalchemy.orm import Session
from sqlalchemy import func, desc
from loguru import logger

from app.services.intent_engine import IntentType


try:
    from groq import Groq
    GROQ_AVAILABLE = True
except ImportError:
    GROQ_AVAILABLE = False
    logger.warning("Groq package not installed. Install with: pip install groq")


class GroqInsightService:
    """
    GROQ AI Insight Service
    
    Used ONLY for complex analytical queries:
    - Why sales decreased?
    - Why POD delayed?
    - Why dealer declined?
    - Root cause analysis
    - Trend analysis
    - Predictive analysis
    
    NOT used for: DN Lookup, Dealer Dashboard, Product Dashboard (these use SQL)
    """
    
    def __init__(self, db: Session, cache_service=None):
        self.db = db
        self.cache = cache_service
        self.ai_available = False
        self.client = None
        
        self._init_groq_client()
        logger.info(f"✅ Groq Insight Service initialized (AI available: {self.ai_available})")
    
    def _init_groq_client(self):
        """Initialize GROQ AI client"""
        if not GROQ_AVAILABLE:
            logger.error("GROQ package not available")
            return
        
        try:
            from app.config import config
            api_key = getattr(config, 'GROQ_API_KEY', None)
            if not api_key:
                api_key = os.environ.get("GROQ_API_KEY")
            
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
        
        context_data = self._gather_context_data(question, intent)
        
        if self.ai_available and self.client:
            return self._analyze_with_groq(question, intent, context_data)
        
        logger.info("GROQ not available - using rule-based fallback")
        return self._analyze_with_rules(question, intent, context_data)
    
    def _analyze_with_groq(self, question: str, intent: IntentType, context_data: Dict) -> Dict[str, Any]:
        """Analyze using GROQ API"""
        try:
            system_prompt = self._build_system_prompt(intent)
            user_prompt = self._build_user_prompt(question, context_data)
            
            completion = self.client.chat.completions.create(
                model="mixtral-8x7b-32768",
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt}
                ],
                temperature=0.7,
                max_tokens=800,
                top_p=0.9
            )
            
            insight = completion.choices[0].message.content
            logger.info(f"✅ GROQ Analysis complete: {len(insight)} chars")
            
            return {
                "insight": insight,
                "source": "groq_ai",
                "confidence": 0.85
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
            # Get basic counts
            total_dns = self.db.query(func.count(DeliveryReport.dn_no)).scalar() or 0
            pending_pgi = self.db.query(func.count(DeliveryReport.dn_no)).filter(
                DeliveryReport.pgi_status != "Completed"
            ).scalar() or 0
            pending_pod = self.db.query(func.count(DeliveryReport.dn_no)).filter(
                DeliveryReport.pgi_status == "Completed",
                DeliveryReport.pod_status != "Received"
            ).scalar() or 0
            
            context_data["metrics"] = {
                "total_dns": total_dns,
                "pending_pgi_count": pending_pgi,
                "pending_pod_count": pending_pod,
                "completion_rate": ((total_dns - pending_pgi) / total_dns * 100) if total_dns else 0
            }
        except Exception as e:
            logger.error(f"Error gathering context data: {e}")
        
        return context_data
    
    def _build_system_prompt(self, intent: IntentType) -> str:
        """Build system prompt for GROQ"""
        base_prompt = """You are Haier Pakistan's Logistics Intelligence AI Assistant.
Provide concise, actionable insights for WhatsApp. Use emojis and bullet points.
Keep responses under 1500 characters."""
        
        if intent == IntentType.ROOT_CAUSE_ANALYSIS:
            return base_prompt + " Focus on identifying root causes of delays."
        elif intent == IntentType.TREND_ANALYSIS:
            return base_prompt + " Focus on trends and patterns."
        elif intent == IntentType.PREDICTIVE_ANALYSIS:
            return base_prompt + " Focus on forecasts and predictions."
        
        return base_prompt
    
    def _build_user_prompt(self, question: str, context_data: Dict) -> str:
        """Build user prompt for GROQ"""
        prompt = f"User Question: {question}\n\n"
        
        metrics = context_data.get("metrics", {})
        if metrics:
            prompt += f"Data: {metrics.get('total_dns', 0)} total DNs, {metrics.get('pending_pgi_count', 0)} pending\n"
        
        return prompt
    
    # ==========================================================
    # RULE-BASED FALLBACK METHODS
    # ==========================================================
    
    def _root_cause_analysis(self, question: str, context_data: Dict) -> Dict[str, Any]:
        metrics = context_data.get("metrics", {})
        
        insight = f"""🔍 *ROOT CAUSE ANALYSIS*

📊 *KEY FINDINGS*
• {metrics.get('pending_pgi_count', 0)} DNs pending dispatch
• {metrics.get('pending_pod_count', 0)} DNs pending POD
• Completion Rate: {metrics.get('completion_rate', 0)}%

💡 *RECOMMENDATIONS*
• Prioritize oldest pending DNs
• Follow up on missing PODs
• Review warehouse processing times

For detailed AI analysis, configure GROQ API key."""
        
        return {"insight": insight, "source": "rule_based", "confidence": 0.70}
    
    def _trend_analysis(self, question: str, context_data: Dict) -> Dict[str, Any]:
        insight = """📈 *TREND ANALYSIS*

📊 *OBSERVATIONS*
• Monitor pending PGI and POD trends
• Focus on completion rate improvement
• Identify pattern in delays

💡 Type "Executive summary" for detailed metrics"""
        
        return {"insight": insight, "source": "rule_based", "confidence": 0.70}
    
    def _predictive_analysis(self, question: str, context_data: Dict) -> Dict[str, Any]:
        metrics = context_data.get("metrics", {})
        pending = metrics.get('pending_pgi_count', 0)
        
        insight = f"""🔮 *PREDICTIVE ANALYSIS*

📊 *FORECASTS*
• {pending} DNs currently pending
• Expected clearance: {pending // 5 + 1} days at current rate
• Focus on SLA compliance

💡 Type "Control tower" for critical alerts"""
        
        return {"insight": insight, "source": "rule_based", "confidence": 0.65}
    
    def _general_analysis(self, question: str, context_data: Dict) -> Dict[str, Any]:
        insight = f"""🤖 *AI INSIGHTS*

I understand you're asking about: "{question[:100]}"

💡 Try these commands:
• "DN 6243612278" - Track a delivery
• "Executive summary" - View dashboard
• "Help" - Complete menu

For AI-powered insights, configure GROQ API key."""
        
        return {"insight": insight, "source": "rule_based", "confidence": 0.60}


# ==========================================================
# FACTORY FUNCTION
# ==========================================================

def get_groq_insight_service(db: Session, cache_service=None) -> GroqInsightService:
    return GroqInsightService(db, cache_service)
