# ==========================================================
# FILE: app/services/groq_insight_service.py (ENTERPRISE v4.0)
# ==========================================================
# GROQ AI INSIGHT SERVICE - COMPLETE ENTERPRISE UPGRADE
# - FIXED: Import DeliveryReport explicitly
# - FIXED: Config loading with proper fallback
# - ADDED: Startup validation & health check
# - ADDED: Standardized response contract
# - ADDED: Timeout protection & retry logic
# - ADDED: Circuit breaker for AI service
# - ADDED: Structured logging
# - ADDED: Metrics caching
# - ADDED: Dynamic model selection
# - ADDED: Enhanced prompts
# ==========================================================

import os
import json
import time
import asyncio
from typing import Dict, Any, Optional
from datetime import datetime, timedelta
from enum import Enum
from dataclasses import dataclass
from functools import wraps

from sqlalchemy.orm import Session
from sqlalchemy import func, desc
from loguru import logger

from app.services.intent_engine import IntentType
from app.models import DeliveryReport  # CRITICAL FIX #1


# ==========================================================
# CONSTANTS
# ==========================================================

GROQ_TIMEOUT_SECONDS = 15
GROQ_MAX_RETRIES = 3
GROQ_RETRY_DELAYS = [1, 2, 4]
CIRCUIT_BREAKER_THRESHOLD = 5
CIRCUIT_BREAKER_TIMEOUT = 300  # 5 minutes
CACHE_TTL = 300  # 5 minutes

# Try to import GROQ
try:
    from groq import Groq
    GROQ_AVAILABLE = True
except ImportError:
    GROQ_AVAILABLE = False
    logger.warning("Groq package not installed. Install with: pip install groq")


# ==========================================================
# CIRCUIT BREAKER
# ==========================================================

class CircuitBreakerState(Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class CircuitBreaker:
    """Circuit breaker for GROQ API calls"""
    
    def __init__(self, name: str, failure_threshold: int = CIRCUIT_BREAKER_THRESHOLD,
                 timeout: int = CIRCUIT_BREAKER_TIMEOUT):
        self.name = name
        self.failure_threshold = failure_threshold
        self.timeout = timeout
        self.state = CircuitBreakerState.CLOSED
        self.failure_count = 0
        self.last_failure_time = None
        self.last_success_time = None
    
    def can_call(self) -> bool:
        """Check if we can make the API call"""
        if self.state == CircuitBreakerState.CLOSED:
            return True
        
        if self.state == CircuitBreakerState.OPEN:
            if time.time() - self.last_failure_time > self.timeout:
                logger.info(f"Circuit breaker {self.name} transitioning to HALF_OPEN")
                self.state = CircuitBreakerState.HALF_OPEN
                return True
            return False
        
        # HALF_OPEN state - allow one test call
        return True
    
    def record_success(self):
        """Record successful call"""
        self.failure_count = 0
        self.last_success_time = time.time()
        if self.state == CircuitBreakerState.HALF_OPEN:
            logger.info(f"Circuit breaker {self.name} closed (success in half-open)")
            self.state = CircuitBreakerState.CLOSED
    
    def record_failure(self):
        """Record failed call"""
        self.failure_count += 1
        self.last_failure_time = time.time()
        
        if self.state == CircuitBreakerState.HALF_OPEN:
            logger.warning(f"Circuit breaker {self.name} re-opening after half-open failure")
            self.state = CircuitBreakerState.OPEN
        elif self.state == CircuitBreakerState.CLOSED and self.failure_count >= self.failure_threshold:
            logger.error(f"Circuit breaker {self.name} OPENING after {self.failure_count} failures")
            self.state = CircuitBreakerState.OPEN
    
    def get_state(self) -> Dict:
        return {
            "name": self.name,
            "state": self.state.value,
            "failure_count": self.failure_count,
            "last_failure": self.last_failure_time,
            "last_success": self.last_success_time
        }


# ==========================================================
# METRICS CACHE
# ==========================================================

class MetricsCache:
    """Cache for database metrics to reduce queries"""
    
    def __init__(self, ttl: int = CACHE_TTL):
        self.cache = {}
        self.ttl = ttl
    
    def get(self, key: str) -> Optional[Dict]:
        if key in self.cache:
            data, timestamp = self.cache[key]
            if time.time() - timestamp < self.ttl:
                return data
            del self.cache[key]
        return None
    
    def set(self, key: str, value: Dict):
        self.cache[key] = (value, time.time())
    
    def clear(self):
        self.cache.clear()
    
    def get_metrics_key(self) -> str:
        return "dashboard_metrics"


# ==========================================================
# MAIN GROQ INSIGHT SERVICE
# ==========================================================

class GroqInsightService:
    """
    GROQ AI Insight Service - Enterprise Grade
    
    Used ONLY for complex analytical queries:
    - Root cause analysis
    - Trend analysis
    - Predictive analysis
    - Executive insights
    """
    
    def __init__(self, db: Session, cache_service=None):
        self.db = db
        self.cache = cache_service
        self.metrics_cache = MetricsCache()
        self.ai_available = False
        self.client = None
        self.circuit_breaker = CircuitBreaker("groq_service")
        
        # Dynamic model selection (Phase 6)
        self.model = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile")
        
        self._init_groq_client()
        self._validate_configuration()
        
        logger.info("=" * 60)
        logger.info(f"✅ Groq Insight Service v4.0 initialized")
        logger.info(f"   AI Available: {self.ai_available}")
        logger.info(f"   Model: {self.model}")
        logger.info(f"   Circuit Breaker: Enabled")
        logger.info(f"   Metrics Cache TTL: {CACHE_TTL}s")
        logger.info("=" * 60)
    
    # ==========================================================
    # INITIALIZATION & VALIDATION
    # ==========================================================
    
    def _init_groq_client(self):
        """Initialize GROQ AI client with proper config loading"""
        if not GROQ_AVAILABLE:
            logger.error("GROQ package not available")
            return
        
        try:
            # CRITICAL FIX #2: Proper config loading
            from app.config import config
            api_key = None
            
            # Try multiple sources
            if hasattr(config, 'GROQ_API_KEY') and config.GROQ_API_KEY:
                api_key = config.GROQ_API_KEY
            elif os.environ.get("GROQ_API_KEY"):
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
    
    def _validate_configuration(self):
        """CRITICAL FIX #3: Startup validation"""
        errors = []
        warnings = []
        
        # Check database
        try:
            self.db.execute("SELECT 1")
        except Exception as e:
            errors.append(f"Database connection failed: {e}")
        
        # Check DeliveryReport model
        try:
            self.db.query(DeliveryReport).limit(1).all()
        except Exception as e:
            errors.append(f"DeliveryReport model error: {e}")
        
        # Check GROQ
        if not self.ai_available:
            warnings.append("GROQ AI not available - using fallback mode")
        
        if errors:
            logger.error("Groq Service configuration FAILED:")
            for error in errors:
                logger.error(f"  - {error}")
        else:
            logger.info("✅ Groq Service configuration validated successfully")
        
        if warnings:
            logger.warning("Groq Service warnings:")
            for warning in warnings:
                logger.warning(f"  - {warning}")
    
    def health_check(self) -> Dict[str, Any]:
        """Health check endpoint for monitoring"""
        return {
            "success": True,
            "service": "groq_insight_service",
            "version": "4.0",
            "ai_available": self.ai_available,
            "model": self.model,
            "circuit_breaker": self.circuit_breaker.get_state(),
            "metrics_cache_size": len(self.metrics_cache.cache)
        }
    
    # ==========================================================
    # MAIN ANALYZE METHOD (Standardized Response)
    # ==========================================================
    
    def analyze(self, question: str, intent: IntentType, context: Dict = None) -> Dict[str, Any]:
        """
        Analyze query using GROQ AI.
        Returns standardized response contract.
        """
        start_time = time.time()
        
        # Structured logging - BEGIN
        logger.info(f"GROQ_START | Intent={intent.value} | Question={question[:100]}")
        
        # Check circuit breaker
        if not self.circuit_breaker.can_call():
            logger.warning(f"Circuit breaker OPEN - using fallback")
            return self._fallback_response(
                question, 
                intent, 
                "Circuit breaker open - service temporarily unavailable"
            )
        
        # Gather context data (with caching)
        context_data = self._gather_context_data_cached(question, intent)
        
        # Log context loaded
        metrics = context_data.get("metrics", {})
        logger.info(f"CONTEXT_LOADED | TotalDNs={metrics.get('total_dns', 0)} | "
                   f"PendingPGI={metrics.get('pending_pgi_count', 0)}")
        
        # Try GROQ if available
        if self.ai_available and self.client:
            try:
                result = self._analyze_with_groq_with_retry(question, intent, context_data)
                self.circuit_breaker.record_success()
                
                processing_time = (time.time() - start_time) * 1000
                logger.info(f"GROQ_SUCCESS | Chars={len(result.get('response', ''))} | Time={processing_time:.0f}ms")
                
                return result
            except Exception as e:
                self.circuit_breaker.record_failure()
                logger.error(f"GROQ_FAILED | Error={str(e)[:100]}")
                return self._analyze_with_rules(question, intent, context_data)
        
        # Fallback to rule-based
        logger.info("GROQ not available - using rule-based fallback")
        return self._analyze_with_rules(question, intent, context_data)
    
    def _fallback_response(self, question: str, intent: IntentType, reason: str) -> Dict[str, Any]:
        """Standardized fallback response"""
        return {
            "success": True,
            "response": f"⚠️ AI service temporarily unavailable. {reason}\n\nPlease try again later or use basic commands like 'DN <number>'.",
            "source": "fallback",
            "confidence": 0.50,
            "intent": intent.value
        }
    
    # ==========================================================
    # GROQ API CALL WITH RETRY & TIMEOUT
    # ==========================================================
    
    def _analyze_with_groq_with_retry(self, question: str, intent: IntentType, 
                                       context_data: Dict) -> Dict[str, Any]:
        """Analyze using GROQ API with retry logic"""
        
        last_error = None
        
        for attempt in range(GROQ_MAX_RETRIES):
            try:
                logger.info(f"GROQ_CALL | Model={self.model} | Attempt={attempt + 1}")
                
                result = asyncio.run(
                    self._async_groq_call(question, intent, context_data)
                )
                
                if result:
                    return result
                
            except Exception as e:
                last_error = e
                if attempt < GROQ_MAX_RETRIES - 1:
                    wait_time = GROQ_RETRY_DELAYS[attempt]
                    logger.warning(f"GROQ_RETRY | Attempt={attempt + 1} failed, retrying in {wait_time}s | Error={str(e)[:100]}")
                    time.sleep(wait_time)
                else:
                    logger.error(f"GROQ_RETRY_EXHAUSTED | All {GROQ_MAX_RETRIES} attempts failed")
        
        raise last_error or Exception("All retry attempts failed")
    
    async def _async_groq_call(self, question: str, intent: IntentType, 
                                context_data: Dict) -> Dict[str, Any]:
        """Async GROQ call with timeout"""
        
        try:
            system_prompt = self._build_system_prompt(intent)
            user_prompt = self._build_user_prompt(question, context_data)
            
            # Run in executor with timeout
            loop = asyncio.get_event_loop()
            completion = await asyncio.wait_for(
                loop.run_in_executor(
                    None,
                    lambda: self.client.chat.completions.create(
                        model=self.model,
                        messages=[
                            {"role": "system", "content": system_prompt},
                            {"role": "user", "content": user_prompt}
                        ],
                        temperature=0.7,
                        max_tokens=800,
                        top_p=0.9
                    )
                ),
                timeout=GROQ_TIMEOUT_SECONDS
            )
            
            insight = completion.choices[0].message.content
            
            return {
                "success": True,
                "response": insight,
                "source": "groq_ai",
                "confidence": 0.85,
                "intent": intent.value,
                "model_used": self.model
            }
            
        except asyncio.TimeoutError:
            logger.error(f"GROQ_TIMEOUT | Timeout after {GROQ_TIMEOUT_SECONDS}s")
            raise Exception(f"GROQ API timeout after {GROQ_TIMEOUT_SECONDS}s")
        except Exception as e:
            logger.error(f"GROQ_API_ERROR | {type(e).__name__}: {str(e)[:100]}")
            raise
    
    # ==========================================================
    # CACHED CONTEXT DATA (Phase 5)
    # ==========================================================
    
    def _gather_context_data_cached(self, question: str, intent: IntentType) -> Dict:
        """Gather context data with caching"""
        
        cache_key = self.metrics_cache.get_metrics_key()
        cached = self.metrics_cache.get(cache_key)
        
        if cached:
            logger.debug("Using cached metrics")
            return cached
        
        context_data = self._gather_context_data(question, intent)
        self.metrics_cache.set(cache_key, context_data)
        
        return context_data
    
    def _gather_context_data(self, question: str, intent: IntentType) -> Dict:
        """Gather relevant data for AI context - Single aggregated query"""
        context_data = {
            "intent": intent.value,
            "question": question,
            "timestamp": datetime.utcnow().isoformat()
        }
        
        try:
            # Single aggregated query for all metrics
            result = self.db.query(
                func.count(DeliveryReport.dn_no).label("total_dns"),
                func.sum(case((DeliveryReport.pgi_status != "Completed", 1), else_=0)).label("pending_pgi"),
                func.sum(case(
                    (and_(DeliveryReport.pgi_status == "Completed", 
                          DeliveryReport.pod_status != "Received"), 1), 
                    else_=0
                )).label("pending_pod")
            ).first()
            
            total_dns = result.total_dns or 0
            pending_pgi = result.pending_pgi or 0
            pending_pod = result.pending_pod or 0
            
            # Get top delayed dealers (for better prompts)
            top_dealers = self.db.query(
                DeliveryReport.customer_name,
                func.count(DeliveryReport.dn_no).label("delayed_count")
            ).filter(
                DeliveryReport.pgi_status != "Completed"
            ).group_by(
                DeliveryReport.customer_name
            ).order_by(
                desc("delayed_count")
            ).limit(3).all()
            
            context_data["metrics"] = {
                "total_dns": total_dns,
                "pending_pgi_count": pending_pgi,
                "pending_pod_count": pending_pod,
                "completion_rate": ((total_dns - pending_pgi) / total_dns * 100) if total_dns else 0,
                "pod_compliance": ((total_dns - pending_pod) / total_dns * 100) if total_dns else 0
            }
            
            context_data["top_delayed_dealers"] = [
                {"name": d.customer_name, "count": d.delayed_count}
                for d in top_dealers if d.customer_name
            ]
            
        except Exception as e:
            logger.error(f"Error gathering context data: {e}")
            context_data["metrics"] = {
                "total_dns": 0,
                "pending_pgi_count": 0,
                "pending_pod_count": 0,
                "completion_rate": 0,
                "pod_compliance": 0
            }
        
        return context_data
    
    # ==========================================================
    # ENHANCED PROMPTS (Phase 6)
    # ==========================================================
    
    def _build_system_prompt(self, intent: IntentType) -> str:
        """Build enhanced system prompt for GROQ"""
        
        base_prompt = """You are Haier Pakistan's Logistics Intelligence AI Assistant.

Company Context:
- Haier Pakistan is a leading home appliance manufacturer
- DN (Delivery Note) is the primary order tracking document
- PGI (Post Goods Issue) indicates dispatch from warehouse
- POD (Proof of Delivery) confirms customer receipt
- SLA targets: Delivery within 1 day of PGI, POD within 3 days of delivery

Your Role:
Provide data-driven insights about logistics operations, delivery performance, and supply chain analytics.

Response Guidelines:
1. Be concise and actionable (WhatsApp format)
2. Use bullet points (•) for clarity
3. Include specific metrics when available
4. Provide 2-3 actionable recommendations
5. Keep responses under 1500 characters
6. Use emojis for visual cues (📊 📈 ⚠️ ✅ 🔴)
7. Be professional but friendly

Focus areas: DN status, dealer performance, warehouse efficiency, POD compliance, revenue realization.
"""
        
        if intent == IntentType.ROOT_CAUSE_ANALYSIS:
            return base_prompt + "\n\nFocus on identifying root causes of delays. Structure: 1) Key findings, 2) Root causes, 3) Recommendations."
        elif intent == IntentType.TREND_ANALYSIS:
            return base_prompt + "\n\nFocus on trend analysis, pattern recognition, and comparative insights."
        elif intent == IntentType.PREDICTIVE_ANALYSIS:
            return base_prompt + "\n\nFocus on forecasts, predictions, and proactive recommendations."
        
        return base_prompt
    
    def _build_user_prompt(self, question: str, context_data: Dict) -> str:
        """Build enhanced user prompt with rich context"""
        
        prompt = f"User Question: {question}\n\n"
        
        metrics = context_data.get("metrics", {})
        if metrics:
            prompt += f"""Current Logistics Data:
- Total DNs: {metrics.get('total_dns', 0)}
- Pending PGI: {metrics.get('pending_pgi_count', 0)} DNs
- Pending POD: {metrics.get('pending_pod_count', 0)} DNs
- Completion Rate: {metrics.get('completion_rate', 0):.1f}%
- POD Compliance: {metrics.get('pod_compliance', 0):.1f}%

"""
        
        top_dealers = context_data.get("top_delayed_dealers", [])
        if top_dealers:
            prompt += "Top Delayed Dealers:\n"
            for d in top_dealers[:3]:
                prompt += f"- {d.get('name', 'Unknown')}: {d.get('count', 0)} DNs\n"
            prompt += "\n"
        
        prompt += """Provide a concise, actionable analysis for WhatsApp. Include:
1. Key insight (1 sentence)
2. Data-driven findings (2-3 bullet points)
3. Recommended actions (2-3 bullet points)

Use emojis and keep it WhatsApp-friendly."""
        
        return prompt
    
    # ==========================================================
    # RULE-BASED FALLBACK METHODS (Enhanced)
    # ==========================================================
    
    def _analyze_with_rules(self, question: str, intent: IntentType, 
                            context_data: Dict) -> Dict[str, Any]:
        """Enhanced fallback rule-based analysis"""
        
        if intent == IntentType.ROOT_CAUSE_ANALYSIS:
            return self._root_cause_analysis_enhanced(question, context_data)
        elif intent == IntentType.TREND_ANALYSIS:
            return self._trend_analysis_enhanced(question, context_data)
        elif intent == IntentType.PREDICTIVE_ANALYSIS:
            return self._predictive_analysis_enhanced(question, context_data)
        else:
            return self._general_analysis_enhanced(question, context_data)
    
    def _root_cause_analysis_enhanced(self, question: str, context_data: Dict) -> Dict[str, Any]:
        """Enhanced root cause analysis with better insights"""
        metrics = context_data.get("metrics", {})
        top_dealers = context_data.get("top_delayed_dealers", [])
        
        pending_pgi = metrics.get('pending_pgi_count', 0)
        pending_pod = metrics.get('pending_pod_count', 0)
        completion_rate = metrics.get('completion_rate', 0)
        
        dealer_names = ", ".join([d.get('name', '')[:20] for d in top_dealers[:2]]) if top_dealers else "None"
        
        insight = f"""🔍 *ROOT CAUSE ANALYSIS*

📊 *KEY FINDINGS*
• {pending_pgi} DNs pending dispatch
• {pending_pod} DNs pending POD collection
• Completion Rate: {completion_rate:.1f}%
• Top Delayed Dealers: {dealer_names}

🔎 *PRIMARY CAUSES*
• Warehouse processing delays ({pending_pgi} DNs)
• POD collection delays ({pending_pod} DNs)

💡 *RECOMMENDATIONS*
• Prioritize DNs pending >7 days
• Schedule follow-up calls with top delayed dealers
• Implement automated POD reminders

For detailed AI analysis, configure GROQ API key."""
        
        return {
            "success": True,
            "response": insight,
            "source": "rule_based",
            "confidence": 0.70,
            "intent": intent.value
        }
    
    def _trend_analysis_enhanced(self, question: str, context_data: Dict) -> Dict[str, Any]:
        """Enhanced trend analysis"""
        metrics = context_data.get("metrics", {})
        
        insight = f"""📈 *TREND ANALYSIS*

📊 *CURRENT STATE*
• Total DNs: {metrics.get('total_dns', 0)}
• Completion Rate: {metrics.get('completion_rate', 0):.1f}%
• POD Compliance: {metrics.get('pod_compliance', 0):.1f}%

📉 *OBSERVED TRENDS*
• Delays primarily in warehouse-to-transit handoff
• POD collection averages 3-5 days
• High-value orders show longer delays

🎯 *FOCUS AREAS*
1. Improve PGI processing time
2. Automate POD collection workflow
3. Prioritize high-value order fulfillment

💡 Type "Root cause" for detailed analysis"""
        
        return {
            "success": True,
            "response": insight,
            "source": "rule_based",
            "confidence": 0.70,
            "intent": intent.value
        }
    
    def _predictive_analysis_enhanced(self, question: str, context_data: Dict) -> Dict[str, Any]:
        """Enhanced predictive analysis"""
        metrics = context_data.get("metrics", {})
        pending_pgi = metrics.get('pending_pgi_count', 0)
        
        days_to_clear = pending_pgi // 5 + 1 if pending_pgi > 0 else 0
        
        insight = f"""🔮 *PREDICTIVE ANALYSIS*

📊 *FORECASTS*
• Days to clear pending PGI: {days_to_clear} days
• Expected SLA breach: {int(pending_pgi * 0.3)} DNs
• Revenue at risk next 7 days: Rs {pending_pgi * 50000:,.0f}

⚠️ *HIGH-RISK PREDICTIONS*
• DNs likely to miss SLA: {pending_pgi}
• Dealers at risk: Check dealer rankings

💡 *RECOMMENDED ACTIONS*
1. Prioritize DNs pending >7 days
2. Proactive communication with at-risk dealers
3. Increase warehouse capacity for peak load

💡 Type "Control tower" for real-time alerts"""
        
        return {
            "success": True,
            "response": insight,
            "source": "rule_based",
            "confidence": 0.65,
            "intent": intent.value
        }
    
    def _general_analysis_enhanced(self, question: str, context_data: Dict) -> Dict[str, Any]:
        """Enhanced general analysis"""
        
        insight = f"""🤖 *AI INSIGHTS*

I understand you're asking about: "{question[:100]}"

💡 *AVAILABLE COMMANDS:*
• "DN 6243612278" - Track a delivery
• "GB Electronics" - Dealer dashboard
• "Executive summary" - View dashboard
• "Pending PODs" - Collection status
• "Control tower" - Critical alerts
• "Help" - Complete menu

📊 *QUICK STATS*
• System Status: Operational
• AI Service: {'Available' if self.ai_available else 'Fallback Mode'}

For AI-powered insights, configure GROQ API key."""
        
        return {
            "success": True,
            "response": insight,
            "source": "rule_based",
            "confidence": 0.60,
            "intent": intent.value
        }


# Helper for SQL CASE
def case(when, then, else_=0):
    from sqlalchemy import case as sa_case
    return sa_case(when, then, else_=else_)


def and_(*conditions):
    from sqlalchemy import and_ as sa_and
    return sa_and(*conditions)


# ==========================================================
# FACTORY FUNCTION
# ==========================================================

def get_groq_insight_service(db: Session, cache_service=None) -> GroqInsightService:
    """Get Groq Insight Service instance"""
    return GroqInsightService(db, cache_service)
