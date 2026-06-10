# ==========================================================
# FILE: app/services/ai_provider_service.py
# VERSION: 3.0
# PURPOSE: Groq AI Layer - Summaries, Insights, Root Cause Analysis, Recommendations
# ARCHITECTURE: ai_query_service → ai_provider_service → (Groq API | DeepSeek | OpenAI)
# ==========================================================

import os
import json
import asyncio
from typing import Dict, Any, Optional, List, Tuple
from datetime import datetime, timedelta
from loguru import logger

# ==========================================================
# AI PROVIDER IMPORTS (Conditional)
# ==========================================================

GROQ_AVAILABLE = False
DEEPSEEK_AVAILABLE = False
OPENAI_AVAILABLE = False

try:
    from groq import Groq
    GROQ_AVAILABLE = True
    logger.info("✅ Groq SDK loaded")
except ImportError:
    logger.warning("⚠️ Groq SDK not installed")

try:
    from openai import OpenAI
    OPENAI_AVAILABLE = True
    logger.info("✅ OpenAI SDK loaded")
except ImportError:
    logger.warning("⚠️ OpenAI SDK not installed")

# ==========================================================
# AI PROVIDER CONFIGURATION
# ==========================================================

class AIProviderConfig:
    """Configuration for AI providers"""
    
    # Groq Models
    GROQ_MODELS = {
        "mixtral": "mixtral-8x7b-32768",
        "llama3_70b": "llama-3.1-70b-versatile",
        "llama3_8b": "llama-3.1-8b-instant",
        "gemma2": "gemma2-9b-it"
    }
    
    # OpenAI Models
    OPENAI_MODELS = {
        "gpt4": "gpt-4-turbo-preview",
        "gpt35": "gpt-3.5-turbo"
    }
    
    # DeepSeek Models
    DEEPSEEK_MODELS = {
        "deepseek": "deepseek-chat"
    }
    
    # Default configuration
    DEFAULT_MODEL = "mixtral"
    DEFAULT_TEMPERATURE = 0.3
    DEFAULT_MAX_TOKENS = 1000
    DEFAULT_TIMEOUT = 30
    
    # System Prompts
    SYSTEM_PROMPTS = {
        "default": """You are an AI Logistics Assistant for a supply chain management system. 
        Your role is to help users understand logistics data, KPIs, performance metrics, and provide actionable insights.
        Be concise, professional, and data-driven. Use bullet points for clarity when appropriate.
        Always focus on providing value and actionable recommendations.""",
        
        "executive": """You are an Executive Logistics Analyst. Provide high-level strategic insights,
        focus on business impact, ROI, and key decision-making factors. Be concise and professional.""",
        
        "technical": """You are a Technical Logistics Expert. Provide detailed analysis including root cause,
        system-level insights, and technical recommendations. Use data to support your conclusions.""",
        
        "summary": """You are a Report Summarizer. Create concise, clear summaries of logistics data.
        Focus on key metrics, trends, and actionable takeaways. Use bullet points for clarity."""
    }


# ==========================================================
# MAIN AI PROVIDER SERVICE
# ==========================================================

class AIProviderService:
    """
    Groq AI Layer - Summaries, Insights, Root Cause Analysis, Recommendations
    Supports multiple AI providers with automatic fallback
    """
    
    def __init__(self):
        self.config = AIProviderConfig()
        self.client = None
        self.provider = None
        self.model = None
        
        # Initialize available provider
        self._initialize_provider()
        
        # Conversation history cache (in-memory, could be replaced with Redis)
        self.conversation_history = {}
        self.max_history = 10
        
        logger.info(f"AI Provider Service initialized with {self.provider or 'no provider'}")
    
    def _initialize_provider(self):
        """Initialize the first available AI provider"""
        
        # Try Groq first (primary)
        if GROQ_AVAILABLE and os.getenv("GROQ_API_KEY"):
            try:
                self.client = Groq(api_key=os.getenv("GROQ_API_KEY"))
                self.provider = "groq"
                self.model = self.config.GROQ_MODELS.get(
                    os.getenv("GROQ_MODEL", "mixtral"),
                    self.config.GROQ_MODELS["mixtral"]
                )
                logger.info(f"✅ Groq AI initialized with model: {self.model}")
                return
            except Exception as e:
                logger.error(f"Failed to initialize Groq: {e}")
        
        # Try OpenAI as fallback
        if OPENAI_AVAILABLE and os.getenv("OPENAI_API_KEY"):
            try:
                self.client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
                self.provider = "openai"
                self.model = self.config.OPENAI_MODELS.get(
                    os.getenv("OPENAI_MODEL", "gpt35"),
                    self.config.OPENAI_MODELS["gpt35"]
                )
                logger.info(f"✅ OpenAI initialized with model: {self.model}")
                return
            except Exception as e:
                logger.error(f"Failed to initialize OpenAI: {e}")
        
        # Try DeepSeek as fallback
        if DEEPSEEK_AVAILABLE and os.getenv("DEEPSEEK_API_KEY"):
            try:
                # DeepSeek uses OpenAI-compatible API
                self.client = OpenAI(
                    api_key=os.getenv("DEEPSEEK_API_KEY"),
                    base_url="https://api.deepseek.com/v1"
                )
                self.provider = "deepseek"
                self.model = self.config.DEEPSEEK_MODELS["deepseek"]
                logger.info(f"✅ DeepSeek initialized with model: {self.model}")
                return
            except Exception as e:
                logger.error(f"Failed to initialize DeepSeek: {e}")
        
        # No provider available
        self.client = None
        self.provider = None
        self.model = None
        logger.error("❌ No AI provider available! Please set GROQ_API_KEY, OPENAI_API_KEY, or DEEPSEEK_API_KEY")
    
    def _get_conversation_context(self, user_id: str) -> List[Dict]:
        """Get conversation history for a user"""
        if user_id not in self.conversation_history:
            self.conversation_history[user_id] = []
        return self.conversation_history[user_id]
    
    def _add_to_history(self, user_id: str, role: str, content: str):
        """Add message to conversation history"""
        history = self._get_conversation_context(user_id)
        history.append({"role": role, "content": content})
        
        # Trim history if too long
        if len(history) > self.max_history * 2:
            self.conversation_history[user_id] = history[-self.max_history * 2:]
    
    def _call_ai(self, messages: List[Dict], temperature: float = None, max_tokens: int = None) -> str:
        """
        Call AI provider with messages
        
        Args:
            messages: List of message dictionaries
            temperature: Temperature for response (0-1)
            max_tokens: Maximum tokens in response
        
        Returns:
            AI response text
        """
        if not self.client:
            return self._fallback_response("AI service is not configured")
        
        temp = temperature or self.config.DEFAULT_TEMPERATURE
        tokens = max_tokens or self.config.DEFAULT_MAX_TOKENS
        
        try:
            if self.provider == "groq":
                response = self.client.chat.completions.create(
                    model=self.model,
                    messages=messages,
                    temperature=temp,
                    max_tokens=tokens
                )
                return response.choices[0].message.content
            
            elif self.provider in ["openai", "deepseek"]:
                response = self.client.chat.completions.create(
                    model=self.model,
                    messages=messages,
                    temperature=temp,
                    max_tokens=tokens
                )
                return response.choices[0].message.content
            
            else:
                return self._fallback_response(f"Unknown provider: {self.provider}")
                
        except Exception as e:
            logger.error(f"AI call failed: {e}")
            return self._fallback_response(f"AI service error: {str(e)[:100]}")
    
    def _fallback_response(self, error_msg: str = None) -> str:
        """Fallback response when AI is unavailable"""
        if error_msg:
            logger.warning(f"Using fallback response due to: {error_msg}")
        
        return """I'm here to help with logistics insights! Currently, I can help you with:

• DN Status - Send any 10+ digit number to track
• Dealer Performance - Ask about any dealer's performance
• Warehouse Status - Check stock and capacity
• Pending Deliveries - Get list of pending PODs
• KPI Metrics - View performance dashboards
• Regional Analysis - Compare region performance

What would you like to know about your logistics operations?"""
    
    # ==========================================================
    # PUBLIC METHODS - Called by ai_query_service.py
    # ==========================================================
    
    def chat(self, message: str, user_id: str = "guest") -> str:
        """
        Main chat method for general AI conversations
        
        Args:
            message: User message
            user_id: User identifier for context
        
        Returns:
            AI response
        """
        logger.info(f"AI Chat - User: {user_id}, Message: {message[:100]}")
        
        # Get conversation history
        history = self._get_conversation_context(user_id)
        
        # Build messages
        messages = [
            {"role": "system", "content": self.config.SYSTEM_PROMPTS["default"]}
        ]
        
        # Add conversation history (last 5 exchanges)
        for msg in history[-10:]:
            messages.append(msg)
        
        # Add current message
        messages.append({"role": "user", "content": message})
        
        # Get response
        response = self._call_ai(messages)
        
        # Save to history
        self._add_to_history(user_id, "user", message)
        self._add_to_history(user_id, "assistant", response)
        
        return response
    
    # ==========================================================
    # SUMMARY METHODS
    # ==========================================================
    
    def generate_executive_summary(self, data: Dict[str, Any]) -> str:
        """
        Generate executive summary from dashboard data
        
        Args:
            data: Dashboard/KPI data
        
        Returns:
            Executive summary text
        """
        logger.info("Generating executive summary")
        
        prompt = f"""Based on the following logistics performance data, create a concise executive summary (2-3 paragraphs):

Performance Data:
{json.dumps(data, indent=2, default=str)}

Focus on:
1. Overall performance score and trend
2. Top 2-3 key achievements
3. Top 2-3 areas needing attention
4. Strategic recommendations

Format: Professional, business-focused, actionable."""
        
        messages = [
            {"role": "system", "content": self.config.SYSTEM_PROMPTS["executive"]},
            {"role": "user", "content": prompt}
        ]
        
        return self._call_ai(messages, temperature=0.3, max_tokens=800)
    
    def generate_dn_summary(self, dn_data: Dict[str, Any]) -> str:
        """
        Generate summary for a DN (Delivery Note)
        
        Args:
            dn_data: DN intelligence data
        
        Returns:
            DN summary text
        """
        logger.info(f"Generating DN summary for {dn_data.get('dn_number')}")
        
        prompt = f"""Create a clear summary for this Delivery Note:

DN Details:
{json.dumps(dn_data, indent=2, default=str)}

Provide:
1. Current status (in simple terms)
2. Key issues (if any)
3. Aging status
4. Recommended action

Keep it concise (3-4 sentences)."""
        
        messages = [
            {"role": "system", "content": self.config.SYSTEM_PROMPTS["summary"]},
            {"role": "user", "content": prompt}
        ]
        
        return self._call_ai(messages, temperature=0.4, max_tokens=300)
    
    def generate_dealer_summary(self, dealer_data: Dict[str, Any]) -> str:
        """
        Generate summary for dealer performance
        
        Args:
            dealer_data: Dealer performance data
        
        Returns:
            Dealer summary text
        """
        logger.info(f"Generating dealer summary for {dealer_data.get('dealer_name')}")
        
        prompt = f"""Summarize this dealer's performance:

Dealer Data:
{json.dumps(dealer_data, indent=2, default=str)}

Include:
1. Overall performance rating (Excellent/Good/Needs Improvement/Critical)
2. Key strengths
3. Key areas for improvement
4. Actionable recommendation

Be specific and data-driven."""
        
        messages = [
            {"role": "system", "content": self.config.SYSTEM_PROMPTS["default"]},
            {"role": "user", "content": prompt}
        ]
        
        return self._call_ai(messages, temperature=0.4, max_tokens=400)
    
    def generate_warehouse_summary(self, warehouse_data: Dict[str, Any]) -> str:
        """
        Generate summary for warehouse status
        
        Args:
            warehouse_data: Warehouse performance data
        
        Returns:
            Warehouse summary text
        """
        logger.info(f"Generating warehouse summary for {warehouse_data.get('warehouse_name')}")
        
        prompt = f"""Summarize this warehouse's performance:

Warehouse Data:
{json.dumps(warehouse_data, indent=2, default=str)}

Include:
1. Capacity status (Utilization percentage)
2. Processing efficiency
3. Pending workload
4. Recommendation for optimization

Keep it actionable."""
        
        messages = [
            {"role": "system", "content": self.config.SYSTEM_PROMPTS["technical"]},
            {"role": "user", "content": prompt}
        ]
        
        return self._call_ai(messages, temperature=0.4, max_tokens=400)
    
    def generate_region_summary(self, region_data: Dict[str, Any]) -> str:
        """
        Generate summary for region performance
        
        Args:
            region_data: Region performance data
        
        Returns:
            Region summary text
        """
        logger.info(f"Generating region summary for {region_data.get('region_name')}")
        
        prompt = f"""Summarize this region's performance:

Region Data:
{json.dumps(region_data, indent=2, default=str)}

Include:
1. Overall ranking among regions
2. Key metrics (success rate, delivery time, active dealers)
3. Comparison to average
4. Specific improvement actions"""
        
        messages = [
            {"role": "system", "content": self.config.SYSTEM_PROMPTS["default"]},
            {"role": "user", "content": prompt}
        ]
        
        return self._call_ai(messages, temperature=0.4, max_tokens=400)
    
    # ==========================================================
    # INSIGHTS METHODS
    # ==========================================================
    
    def generate_root_cause_analysis(self, metric: str, data: Dict[str, Any]) -> str:
        """
        Generate root cause analysis for performance issues
        
        Args:
            metric: The metric with issues (e.g., "POD performance")
            data: Relevant data for analysis
        
        Returns:
            Root cause analysis
        """
        logger.info(f"Generating root cause analysis for {metric}")
        
        prompt = f"""Perform root cause analysis for {metric} issue:

Data:
{json.dumps(data, indent=2, default=str)}

Identify:
1. Primary root causes (2-3)
2. Contributing factors
3. Why these issues are occurring
4. Which regions/branches/dealers are most affected

Be specific and data-driven. Format as bullet points."""
        
        messages = [
            {"role": "system", "content": self.config.SYSTEM_PROMPTS["technical"]},
            {"role": "user", "content": prompt}
        ]
        
        return self._call_ai(messages, temperature=0.5, max_tokens=600)
    
    def generate_recommendations(self, issues: List[str], data: Dict[str, Any]) -> str:
        """
        Generate actionable recommendations based on issues
        
        Args:
            issues: List of identified issues
            data: Supporting data
        
        Returns:
            Actionable recommendations
        """
        logger.info(f"Generating recommendations for {len(issues)} issues")
        
        prompt = f"""Based on these identified issues:

Issues:
{json.dumps(issues, indent=2)}

Supporting Data:
{json.dumps(data, indent=2, default=str)}

Provide actionable recommendations:
1. Immediate actions (next 24-48 hours)
2. Short-term improvements (next 2 weeks)
3. Long-term strategic changes
4. Success metrics to track

Prioritize by impact and ease of implementation."""
        
        messages = [
            {"role": "system", "content": self.config.SYSTEM_PROMPTS["executive"]},
            {"role": "user", "content": prompt}
        ]
        
        return self._call_ai(messages, temperature=0.5, max_tokens=800)
    
    def generate_risk_analysis(self, alerts: List[Dict[str, Any]], kpis: Dict[str, Any]) -> str:
        """
        Generate risk analysis based on alerts and KPIs
        
        Args:
            alerts: List of risk alerts
            kpis: Current KPI data
        
        Returns:
            Risk analysis report
        """
        logger.info(f"Generating risk analysis for {len(alerts)} alerts")
        
        prompt = f"""Perform risk analysis based on:

Active Alerts:
{json.dumps(alerts, indent=2)}

Current KPIs:
{json.dumps(kpis, indent=2, default=str)}

Provide:
1. Overall risk assessment (Low/Medium/High/Critical)
2. Highest priority risks to address
3. Potential business impact if not addressed
4. Mitigation strategies
5. Early warning signs to monitor

Be specific and actionable."""
        
        messages = [
            {"role": "system", "content": self.config.SYSTEM_PROMPTS["executive"]},
            {"role": "user", "content": prompt}
        ]
        
        return self._call_ai(messages, temperature=0.4, max_tokens=700)
    
    def generate_trend_explanation(self, trend_data: Dict[str, Any]) -> str:
        """
        Explain trends in performance data
        
        Args:
            trend_data: Trend analysis data
        
        Returns:
            Trend explanation
        """
        logger.info("Generating trend explanation")
        
        prompt = f"""Explain these performance trends:

Trend Data:
{json.dumps(trend_data, indent=2, default=str)}

Explain:
1. What the trends indicate (improving, declining, stable)
2. Possible reasons for the trends
3. Which regions/segments are driving the trends
4. What to watch for in the coming weeks

Keep it insightful and actionable."""
        
        messages = [
            {"role": "system", "content": self.config.SYSTEM_PROMPTS["default"]},
            {"role": "user", "content": prompt}
        ]
        
        return self._call_ai(messages, temperature=0.4, max_tokens=500)
    
    # ==========================================================
    # QUESTION ANSWERING METHODS
    # ==========================================================
    
    def answer_question(self, question: str, context: Dict[str, Any]) -> str:
        """
        Answer specific questions with context
        
        Args:
            question: User's question
            context: Relevant data context
        
        Returns:
            Answer to the question
        """
        logger.info(f"Answering question: {question[:100]}")
        
        prompt = f"""Answer the following question based on the provided data:

Question: {question}

Data Context:
{json.dumps(context, indent=2, default=str)}

Guidelines:
1. Be specific and data-driven
2. Include numbers and metrics where relevant
3. If data doesn't answer the question, say so clearly
4. Keep answer concise (2-3 sentences)"""
        
        messages = [
            {"role": "system", "content": self.config.SYSTEM_PROMPTS["default"]},
            {"role": "user", "content": prompt}
        ]
        
        return self._call_ai(messages, temperature=0.3, max_tokens=400)
    
    def explain_kpi(self, kpi_name: str, current_value: float, target: float, trend: str) -> str:
        """
        Explain a KPI and its significance
        
        Args:
            kpi_name: Name of the KPI
            current_value: Current value
            target: Target value
            trend: Trend direction
        
        Returns:
            KPI explanation
        """
        logger.info(f"Explaining KPI: {kpi_name}")
        
        prompt = f"""Explain this KPI:

KPI Name: {kpi_name}
Current Value: {current_value}
Target: {target}
Trend: {trend}

Explain:
1. What this KPI measures and why it matters
2. How current performance compares to target
3. What the trend indicates
4. How to improve this KPI (if needed)

Keep it clear and educational."""
        
        messages = [
            {"role": "system", "content": self.config.SYSTEM_PROMPTS["default"]},
            {"role": "user", "content": prompt}
        ]
        
        return self._call_ai(messages, temperature=0.3, max_tokens=500)
    
    def explain_performance(self, metric: str, actual: float, target: float, 
                           benchmark: float = None, region: str = None) -> str:
        """
        Explain performance of a specific metric
        
        Args:
            metric: Metric name
            actual: Actual value
            target: Target value
            benchmark: Benchmark value (optional)
            region: Region name (optional)
        
        Returns:
            Performance explanation
        """
        logger.info(f"Explaining performance for {metric}")
        
        benchmark_text = f"Benchmark: {benchmark}" if benchmark else "No benchmark available"
        region_text = f"Region: {region}" if region else "Overall"
        
        prompt = f"""Explain this performance:

Metric: {metric}
{region_text}
Actual: {actual}
Target: {target}
{benchmark_text}

Provide:
1. Performance rating (Exceeding/Meeting/Below/Well Below target)
2. Gap analysis (how far from target)
3. Potential reasons
4. Recommended focus areas"""
        
        messages = [
            {"role": "system", "content": self.config.SYSTEM_PROMPTS["default"]},
            {"role": "user", "content": prompt}
        ]
        
        return self._call_ai(messages, temperature=0.3, max_tokens=500)
    
    # ==========================================================
    # COMPREHENSIVE ANALYSIS METHODS
    # ==========================================================
    
    def generate_comprehensive_insights(self, dashboard_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Generate comprehensive insights from dashboard data
        
        Args:
            dashboard_data: Complete dashboard data
        
        Returns:
            Dictionary with multiple insight types
        """
        logger.info("Generating comprehensive insights")
        
        insights = {
            "executive_summary": self.generate_executive_summary(dashboard_data),
            "key_findings": [],
            "risk_factors": [],
            "recommendations": [],
            "trend_analysis": ""
        }
        
        # Extract key findings
        pod_score = dashboard_data.get('executive_summary', {}).get('pod_score', 0)
        if pod_score < 85:
            insights["key_findings"].append(f"POD performance at {pod_score}% - below target")
        
        # Generate recommendations based on scores
        if pod_score < 90:
            insights["recommendations"].append("Focus on improving POD collection process")
        
        # Add trend analysis
        insights["trend_analysis"] = self.generate_trend_explanation(
            dashboard_data.get('target_analysis', {}).get('monthly_trend', [])
        )
        
        return insights
    
    # ==========================================================
    # STREAMING METHODS (for real-time responses)
    # ==========================================================
    
    async def stream_chat(self, message: str, user_id: str = "guest"):
        """
        Stream chat response token by token
        
        Args:
            message: User message
            user_id: User identifier
        
        Yields:
            Response tokens
        """
        if not self.client or self.provider != "groq":
            yield self._fallback_response()
            return
        
        history = self._get_conversation_context(user_id)
        
        messages = [
            {"role": "system", "content": self.config.SYSTEM_PROMPTS["default"]}
        ]
        
        for msg in history[-10:]:
            messages.append(msg)
        
        messages.append({"role": "user", "content": message})
        
        try:
            stream = self.client.chat.completions.create(
                model=self.model,
                messages=messages,
                temperature=self.config.DEFAULT_TEMPERATURE,
                max_tokens=self.config.DEFAULT_MAX_TOKENS,
                stream=True
            )
            
            full_response = ""
            for chunk in stream:
                if chunk.choices[0].delta.content:
                    token = chunk.choices[0].delta.content
                    full_response += token
                    yield token
            
            # Save to history
            self._add_to_history(user_id, "user", message)
            self._add_to_history(user_id, "assistant", full_response)
            
        except Exception as e:
            logger.error(f"Streaming error: {e}")
            yield f"Error: {str(e)}"
    
    # ==========================================================
    # HEALTH CHECK
    # ==========================================================
    
    def health_check(self) -> Dict[str, Any]:
        """Check AI provider health and configuration"""
        return {
            "service": "ai_provider",
            "version": "3.0",
            "provider": self.provider,
            "model": self.model,
            "configured": self.client is not None,
            "groq_available": GROQ_AVAILABLE,
            "openai_available": OPENAI_AVAILABLE,
            "deepseek_available": DEEPSEEK_AVAILABLE,
            "api_key_configured": bool(
                os.getenv("GROQ_API_KEY") or 
                os.getenv("OPENAI_API_KEY") or 
                os.getenv("DEEPSEEK_API_KEY")
            )
        }
    
    def clear_history(self, user_id: str):
        """Clear conversation history for a user"""
        if user_id in self.conversation_history:
            del self.conversation_history[user_id]
            logger.info(f"Cleared history for user: {user_id}")


# ==========================================================
# COMPATIBILITY FUNCTIONS (Called by ai_query_service.py)
# ==========================================================

# Singleton instance
_ai_provider = None


def get_ai_provider() -> AIProviderService:
    """Get or create AI provider singleton"""
    global _ai_provider
    if _ai_provider is None:
        _ai_provider = AIProviderService()
    return _ai_provider


def chat(message: str, user_id: str = "guest") -> str:
    """Compatibility function for chat"""
    provider = get_ai_provider()
    return provider.chat(message, user_id)


def generate_summary(data: Dict[str, Any], summary_type: str = "executive") -> str:
    """Compatibility function for generating summaries"""
    provider = get_ai_provider()
    
    if summary_type == "dn":
        return provider.generate_dn_summary(data)
    elif summary_type == "dealer":
        return provider.generate_dealer_summary(data)
    elif summary_type == "warehouse":
        return provider.generate_warehouse_summary(data)
    elif summary_type == "region":
        return provider.generate_region_summary(data)
    else:
        return provider.generate_executive_summary(data)


def generate_root_cause(metric: str, data: Dict[str, Any]) -> str:
    """Compatibility function for root cause analysis"""
    provider = get_ai_provider()
    return provider.generate_root_cause_analysis(metric, data)


def generate_recommendations(issues: List[str], data: Dict[str, Any]) -> str:
    """Compatibility function for recommendations"""
    provider = get_ai_provider()
    return provider.generate_recommendations(issues, data)


def generate_risk_analysis(alerts: List[Dict], kpis: Dict[str, Any]) -> str:
    """Compatibility function for risk analysis"""
    provider = get_ai_provider()
    return provider.generate_risk_analysis(alerts, kpis)


def answer_question(question: str, context: Dict[str, Any]) -> str:
    """Compatibility function for answering questions"""
    provider = get_ai_provider()
    return provider.answer_question(question, context)


# ==========================================================
# INITIALIZATION LOG
# ==========================================================

logger.info("=" * 60)
logger.info("🤖 AI Provider Service v3.0 Loaded - Groq AI Layer")
logger.info(f"   Provider: {get_ai_provider().provider or 'None'}")
logger.info(f"   Model: {get_ai_provider().model or 'N/A'}")
logger.info("   Features: Summaries | Insights | Root Cause | Recommendations | Risk Analysis")
logger.info("=" * 60)
