# ==========================================================
# FILE: app/services/ai_provider_service.py
# VERSION: 4.0
# PURPOSE: Groq AI Layer - Summaries, Insights, Root Cause Analysis, Recommendations
# ARCHITECTURE: ai_query_service → ai_provider_service → (Groq API | DeepSeek | OpenAI)
# ==========================================================

import os
import json
import asyncio
from typing import Dict, Any, Optional, List, Tuple, Union
from datetime import datetime, timedelta
from loguru import logger

# ==========================================================
# AI PROVIDER IMPORTS (Conditional with Better Error Handling)
# ==========================================================

GROQ_AVAILABLE = False
DEEPSEEK_AVAILABLE = False
OPENAI_AVAILABLE = False
GROQ_CLIENT = None

# Log initialization start
logger.info("=" * 60)
logger.info("AI PROVIDER SERVICE - INITIALIZATION STARTING")
logger.info("=" * 60)

# Check environment variables
logger.info(f"GROQ_API_KEY set: {bool(os.getenv('GROQ_API_KEY'))}")
logger.info(f"OPENAI_API_KEY set: {bool(os.getenv('OPENAI_API_KEY'))}")
logger.info(f"DEEPSEEK_API_KEY set: {bool(os.getenv('DEEPSEEK_API_KEY'))}")

# Try Groq import
try:
    from groq import Groq
    GROQ_AVAILABLE = True
    logger.info("✅ Groq SDK loaded successfully")
except ImportError as e:
    logger.warning(f"⚠️ Groq SDK not installed: {e}")
except Exception as e:
    logger.warning(f"⚠️ Groq import error: {e}")

# Try OpenAI import
try:
    from openai import OpenAI
    OPENAI_AVAILABLE = True
    logger.info("✅ OpenAI SDK loaded successfully")
except ImportError as e:
    logger.warning(f"⚠️ OpenAI SDK not installed: {e}")
except Exception as e:
    logger.warning(f"⚠️ OpenAI import error: {e}")

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
# MAIN AI PROVIDER SERVICE (Enhanced Version)
# ==========================================================

class AIProviderService:
    """
    Groq AI Layer - Summaries, Insights, Root Cause Analysis, Recommendations
    Supports multiple AI providers with automatic fallback
    
    ENHANCEMENTS v4.0:
    - Better error handling (never crashes)
    - Graceful degradation when AI unavailable
    - Improved initialization logging
    - Async support
    - Rate limit handling
    """
    
    def __init__(self):
        self.config = AIProviderConfig()
        self.client = None
        self.provider = None
        self.model = None
        self._initialization_error = None
        
        # Initialize available provider
        self._initialize_provider()
        
        # Conversation history cache (in-memory, could be replaced with Redis)
        self.conversation_history = {}
        self.max_history = 10
        
        # Request tracking for rate limiting
        self._request_count = 0
        self._last_request_time = None
        
        # Log final status
        if self.provider:
            logger.info(f"✅ AI Provider Service initialized with {self.provider}")
            logger.info(f"   Model: {self.model}")
        else:
            logger.warning("⚠️ AI Provider Service initialized with NO provider (fallback mode only)")
            if self._initialization_error:
                logger.warning(f"   Error: {self._initialization_error}")
    
    def _initialize_provider(self):
        """Initialize the first available AI provider with better error handling"""
        
        # Try Groq first (primary)
        if GROQ_AVAILABLE:
            api_key = os.getenv("GROQ_API_KEY")
            if api_key:
                try:
                    self.client = Groq(api_key=api_key)
                    self.provider = "groq"
                    self.model = self.config.GROQ_MODELS.get(
                        os.getenv("GROQ_MODEL", "mixtral"),
                        self.config.GROQ_MODELS["mixtral"]
                    )
                    logger.info(f"✅ Groq AI initialized with model: {self.model}")
                    return
                except Exception as e:
                    logger.error(f"Failed to initialize Groq: {e}")
                    self._initialization_error = f"Groq: {str(e)[:100]}"
            else:
                logger.debug("GROQ_API_KEY not set, skipping Groq")
        
        # Try OpenAI as fallback
        if OPENAI_AVAILABLE:
            api_key = os.getenv("OPENAI_API_KEY")
            if api_key:
                try:
                    self.client = OpenAI(api_key=api_key)
                    self.provider = "openai"
                    self.model = self.config.OPENAI_MODELS.get(
                        os.getenv("OPENAI_MODEL", "gpt35"),
                        self.config.OPENAI_MODELS["gpt35"]
                    )
                    logger.info(f"✅ OpenAI initialized with model: {self.model}")
                    return
                except Exception as e:
                    logger.error(f"Failed to initialize OpenAI: {e}")
                    if not self._initialization_error:
                        self._initialization_error = f"OpenAI: {str(e)[:100]}"
            else:
                logger.debug("OPENAI_API_KEY not set, skipping OpenAI")
        
        # Try DeepSeek as fallback
        if OPENAI_AVAILABLE:  # DeepSeek uses OpenAI-compatible API
            api_key = os.getenv("DEEPSEEK_API_KEY")
            if api_key:
                try:
                    self.client = OpenAI(
                        api_key=api_key,
                        base_url="https://api.deepseek.com/v1"
                    )
                    self.provider = "deepseek"
                    self.model = self.config.DEEPSEEK_MODELS["deepseek"]
                    logger.info(f"✅ DeepSeek initialized with model: {self.model}")
                    return
                except Exception as e:
                    logger.error(f"Failed to initialize DeepSeek: {e}")
                    if not self._initialization_error:
                        self._initialization_error = f"DeepSeek: {str(e)[:100]}"
            else:
                logger.debug("DEEPSEEK_API_KEY not set, skipping DeepSeek")
        
        # No provider available - service will use fallback responses
        self.client = None
        self.provider = None
        self.model = None
        logger.warning("❌ No AI provider available! Service will use fallback responses.")
    
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
    
    def _check_rate_limit(self) -> bool:
        """Simple rate limiting to avoid hitting API limits"""
        import time
        
        current_time = time.time()
        
        # Reset counter if more than 60 seconds have passed
        if self._last_request_time and (current_time - self._last_request_time) > 60:
            self._request_count = 0
        
        # Check if over limit (30 requests per minute max)
        if self._request_count >= 30:
            logger.warning("Rate limit reached, using fallback response")
            return False
        
        self._request_count += 1
        self._last_request_time = current_time
        return True
    
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
        # Check if AI is available
        if not self.client or not self.provider:
            return self._fallback_response("AI service is not configured")
        
        # Check rate limit
        if not self._check_rate_limit():
            return self._fallback_response("Rate limit reached")
        
        temp = temperature or self.config.DEFAULT_TEMPERATURE
        tokens = max_tokens or self.config.DEFAULT_MAX_TOKENS
        
        try:
            if self.provider == "groq":
                response = self.client.chat.completions.create(
                    model=self.model,
                    messages=messages,
                    temperature=temp,
                    max_tokens=tokens,
                    timeout=self.config.DEFAULT_TIMEOUT
                )
                return response.choices[0].message.content
            
            elif self.provider in ["openai", "deepseek"]:
                response = self.client.chat.completions.create(
                    model=self.model,
                    messages=messages,
                    temperature=temp,
                    max_tokens=tokens,
                    timeout=self.config.DEFAULT_TIMEOUT
                )
                return response.choices[0].message.content
            
            else:
                return self._fallback_response(f"Unknown provider: {self.provider}")
                
        except Exception as e:
            logger.error(f"AI call failed: {e}")
            return self._fallback_response(f"AI service error")
    
    def _fallback_response(self, error_msg: str = None) -> str:
        """Fallback response when AI is unavailable"""
        if error_msg:
            logger.debug(f"Using fallback response due to: {error_msg}")
        
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
        
        # If no AI, provide smart fallback
        if not self.client:
            return self._generate_smart_executive_summary(data)
        
        prompt = f"""Based on the following logistics performance data, create a concise executive summary (2-3 paragraphs):

Performance Data:
{json.dumps(data, indent=2, default=str)[:2000]}

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
    
    def _generate_smart_executive_summary(self, data: Dict) -> str:
        """Generate smart fallback executive summary without AI"""
        summary_parts = []
        
        # Extract key metrics if available
        if isinstance(data, dict):
            overall_score = data.get('overall_score', data.get('executive_summary', {}).get('overall_score', 'N/A'))
            pod_score = data.get('pod_score', data.get('pod_performance', {}).get('overall_score', 'N/A'))
            
            if overall_score != 'N/A':
                summary_parts.append(f"📊 Overall Performance Score: {overall_score}%")
            if pod_score != 'N/A':
                summary_parts.append(f"📋 POD Performance: {pod_score}%")
        
        if summary_parts:
            return "📈 *Executive Summary*\n━━━━━━━━━━━━━━━━━━━━━\n\n" + "\n".join(summary_parts) + "\n\nAnalysis available with AI service enabled."
        
        return "Executive summary data is being prepared. Please check back shortly or enable AI service for detailed insights."
    
    def generate_dn_summary(self, dn_data: Dict[str, Any]) -> str:
        """Generate summary for a DN (Delivery Note)"""
        logger.info(f"Generating DN summary for {dn_data.get('dn_number')}")
        
        if not self.client:
            # Smart fallback
            dn_number = dn_data.get('dn_number', 'Unknown')
            status = dn_data.get('status', 'Unknown')
            aging = dn_data.get('aging_days', 0)
            
            return f"📦 *DN {dn_number}*\n━━━━━━━━━━━━━━━━━━━━━\n\nStatus: {status}\nAging: {aging} days\n\nFor detailed analysis, AI service is being configured."
        
        prompt = f"""Create a clear summary for this Delivery Note:

DN Details:
{json.dumps(dn_data, indent=2, default=str)[:1500]}

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
        """Generate summary for dealer performance"""
        logger.info(f"Generating dealer summary for {dealer_data.get('dealer_name')}")
        
        if not self.client:
            dealer_name = dealer_data.get('dealer_name', 'Unknown')
            total_dns = dealer_data.get('total_dns', 0)
            pending = dealer_data.get('pending_count', 0)
            
            return f"🏪 *Dealer: {dealer_name}*\n━━━━━━━━━━━━━━━━━━━━━\n\nTotal DNs: {total_dns}\nPending: {pending}\n\nDetailed analysis available with AI service."
        
        prompt = f"""Summarize this dealer's performance:

Dealer Data:
{json.dumps(dealer_data, indent=2, default=str)[:1500]}

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
        """Generate summary for warehouse status"""
        logger.info(f"Generating warehouse summary for {warehouse_data.get('warehouse_name')}")
        
        if not self.client:
            warehouse_name = warehouse_data.get('warehouse_name', 'Unknown')
            capacity = warehouse_data.get('capacity_percentage', 0)
            
            return f"🏭 *Warehouse: {warehouse_name}*\n━━━━━━━━━━━━━━━━━━━━━\n\nCapacity Utilization: {capacity}%\n\nDetailed analysis available with AI service."
        
        prompt = f"""Summarize this warehouse's performance:

Warehouse Data:
{json.dumps(warehouse_data, indent=2, default=str)[:1500]}

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
        """Generate summary for region performance"""
        logger.info(f"Generating region summary for {region_data.get('region_name')}")
        
        prompt = f"""Summarize this region's performance:

Region Data:
{json.dumps(region_data, indent=2, default=str)[:1500]}

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
        
        if not self.client:
            return f"📊 *Root Cause Analysis: {metric}*\n━━━━━━━━━━━━━━━━━━━━━\n\nAI service is being configured. For detailed root cause analysis, please check back shortly."
        
        prompt = f"""Perform root cause analysis for {metric} issue:

Data:
{json.dumps(data, indent=2, default=str)[:1500]}

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
        
        if not self.client:
            return "💡 *Recommendations*\n━━━━━━━━━━━━━━━━━━━━━\n\n1. Review pending deliveries\n2. Follow up on aging PODs\n3. Optimize warehouse dispatch\n\nDetailed recommendations available with AI service."
        
        prompt = f"""Based on these identified issues:

Issues:
{json.dumps(issues, indent=2)[:500]}

Supporting Data:
{json.dumps(data, indent=2, default=str)[:1000]}

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
        """Generate risk analysis based on alerts and KPIs"""
        logger.info(f"Generating risk analysis for {len(alerts)} alerts")
        
        if not self.client:
            return "⚠️ *Risk Analysis*\n━━━━━━━━━━━━━━━━━━━━━\n\nActive alerts detected. AI service is being configured for detailed risk assessment."
        
        prompt = f"""Perform risk analysis based on:

Active Alerts:
{json.dumps(alerts, indent=2)[:1000]}

Current KPIs:
{json.dumps(kpis, indent=2, default=str)[:1000]}

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
        """Explain trends in performance data"""
        logger.info("Generating trend explanation")
        
        prompt = f"""Explain these performance trends:

Trend Data:
{json.dumps(trend_data, indent=2, default=str)[:1500]}

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
    
    def generate_predictive_analysis(self, question: str, context: Dict[str, Any]) -> str:
        """Generate predictive analysis for future outcomes"""
        logger.info("Generating predictive analysis")
        
        if not self.client:
            return "🔮 *Predictive Analysis*\n━━━━━━━━━━━━━━━━━━━━━\n\nAI service is being configured for predictive analytics. Please check back shortly."
        
        prompt = f"""Based on the following context, provide a predictive analysis:

Question: {question}

Context:
{json.dumps(context, indent=2, default=str)[:1500]}

Provide:
1. Short-term forecast (next 7 days)
2. Medium-term forecast (next 30 days)
3. Key risk factors
4. Recommended actions

Be specific and data-driven."""
        
        messages = [
            {"role": "system", "content": self.config.SYSTEM_PROMPTS["technical"]},
            {"role": "user", "content": prompt}
        ]
        
        return self._call_ai(messages, temperature=0.5, max_tokens=700)
    
    # ==========================================================
    # QUESTION ANSWERING METHODS
    # ==========================================================
    
    def answer_question(self, question: str, context: Dict[str, Any]) -> str:
        """Answer specific questions with context"""
        logger.info(f"Answering question: {question[:100]}")
        
        prompt = f"""Answer the following question based on the provided data:

Question: {question}

Data Context:
{json.dumps(context, indent=2, default=str)[:1500]}

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
        """Explain a KPI and its significance"""
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
        """Explain performance of a specific metric"""
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
        """Generate comprehensive insights from dashboard data"""
        logger.info("Generating comprehensive insights")
        
        insights = {
            "executive_summary": self.generate_executive_summary(dashboard_data),
            "key_findings": [],
            "risk_factors": [],
            "recommendations": [],
            "trend_analysis": ""
        }
        
        # Extract key findings from data if available
        if isinstance(dashboard_data, dict):
            pod_score = dashboard_data.get('executive_summary', {}).get('pod_score', 100)
            if pod_score < 85:
                insights["key_findings"].append(f"POD performance at {pod_score}% - below target")
        
        # Add trend analysis
        insights["trend_analysis"] = self.generate_trend_explanation(
            dashboard_data.get('target_analysis', {}).get('monthly_trend', [])
        )
        
        return insights
    
    # ==========================================================
    # STREAMING METHODS (for real-time responses)
    # ==========================================================
    
    async def stream_chat(self, message: str, user_id: str = "guest"):
        """Stream chat response token by token"""
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
                stream=True,
                timeout=self.config.DEFAULT_TIMEOUT
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
            "version": "4.0",
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
            ),
            "initialization_error": self._initialization_error,
            "rate_limit_remaining": 30 - self._request_count
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

# Get provider instance for logging
try:
    _test_provider = get_ai_provider()
    logger.info("=" * 60)
    logger.info("🤖 AI Provider Service v4.0 Loaded")
    logger.info(f"   Provider: {_test_provider.provider or 'None (Fallback Mode)'}")
    logger.info(f"   Model: {_test_provider.model or 'N/A'}")
    logger.info(f"   Configured: {_test_provider.client is not None}")
    logger.info("   Features: Summaries | Insights | Root Cause | Recommendations | Risk Analysis")
    logger.info("   Fallback: Smart responses when AI unavailable")
    logger.info("=" * 60)
except Exception as e:
    logger.warning(f"AI Provider Service loaded with issues: {e}")
    logger.info("=" * 60)
    logger.info("🤖 AI Provider Service v4.0 Loaded (Fallback Mode Only)")
    logger.info("   No AI provider configured - using smart fallback responses")
    logger.info("   Set GROQ_API_KEY, OPENAI_API_KEY, or DEEPSEEK_API_KEY to enable AI")
    logger.info("=" * 60)
