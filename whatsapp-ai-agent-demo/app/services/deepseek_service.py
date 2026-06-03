# ==========================================================
# FILE: app/services/deepseek_service.py
# ==========================================================

from typing import Dict, Any, List, Optional
from openai import OpenAI
from loguru import logger

from app.core.config import settings


class DeepSeekService:
    """
    DeepSeek AI Service

    Responsibilities:
    - Dealer Analysis
    - Executive Analysis
    - Product Analysis
    - Warehouse Analysis
    - General Logistics Q&A
    """

    def __init__(self):

        self.client = OpenAI(
            api_key=settings.DEEPSEEK_API_KEY,
            base_url="https://api.deepseek.com"
        )

        self.model = getattr(
            settings,
            "DEEPSEEK_MODEL",
            "deepseek-chat"
        )

    # ======================================================
    # CORE CHAT COMPLETION
    # ======================================================

    def generate_response(
        self,
        prompt: str,
        temperature: float = 0.3,
        max_tokens: int = 2500
    ) -> str:

        try:

            response = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "You are a senior logistics analyst. "
                            "Analyze delivery, POD, warehouse, "
                            "dealer, city and product performance. "
                            "Always provide concise business insights."
                        )
                    },
                    {
                        "role": "user",
                        "content": prompt
                    }
                ],
                temperature=temperature,
                max_tokens=max_tokens
            )

            return (
                response
                .choices[0]
                .message
                .content
            )

        except Exception as e:

            logger.exception(
                f"DeepSeek Error: {str(e)}"
            )

            return (
                "Unable to generate analysis "
                "at this time."
            )

    # ======================================================
    # DEALER ANALYSIS
    # ======================================================

    def analyze_dealer(
        self,
        dealer_context: Dict[str, Any]
    ) -> str:

        prompt = f"""
Analyze this dealer.

Dealer Data:

{dealer_context}

Provide:

1. Executive Summary
2. Key Risks
3. Opportunities
4. Recommendations
5. Priority Actions

Keep response concise.
"""

        return self.generate_response(
            prompt
        )

    # ======================================================
    # EXECUTIVE ANALYSIS
    # ======================================================

    def analyze_executive(
        self,
        executive_context: Dict[str, Any]
    ) -> str:

        prompt = f"""
Analyze this logistics operation.

Data:

{executive_context}

Provide:

1. Executive Summary
2. Top Risks
3. Top Performing Areas
4. Immediate Attention Items
5. Recommended Actions

Focus on business impact.
"""

        return self.generate_response(
            prompt
        )

    # ======================================================
    # PRODUCT ANALYSIS
    # ======================================================

    def analyze_product(
        self,
        product_context: Dict[str, Any]
    ) -> str:

        prompt = f"""
Analyze product performance.

Data:

{product_context}

Provide:

1. Product Performance
2. Demand Trends
3. Risk Areas
4. Recommendations
"""

        return self.generate_response(
            prompt
        )

    # ======================================================
    # WAREHOUSE ANALYSIS
    # ======================================================

    def analyze_warehouse(
        self,
        warehouse_context: Dict[str, Any]
    ) -> str:

        prompt = f"""
Analyze warehouse operations.

Data:

{warehouse_context}

Provide:

1. Performance Summary
2. Bottlenecks
3. Risks
4. Recommendations
"""

        return self.generate_response(
            prompt
        )

    # ======================================================
    # CITY ANALYSIS
    # ======================================================

    def analyze_city(
        self,
        city_context: Dict[str, Any]
    ) -> str:

        prompt = f"""
Analyze city logistics performance.

Data:

{city_context}

Provide:

1. Summary
2. Risks
3. Delivery Challenges
4. Recommendations
"""

        return self.generate_response(
            prompt
        )

    # ======================================================
    # DN ANALYSIS
    # ======================================================

    def analyze_dn(
        self,
        dn_context: Dict[str, Any]
    ) -> str:

        prompt = f"""
Analyze this Delivery Note.

Data:

{dn_context}

Provide:

1. DN Summary
2. Product Summary
3. Delivery Status
4. Risks
5. Recommendations
"""

        return self.generate_response(
            prompt
        )

    # ======================================================
    # COMPARE ENTITIES
    # ======================================================

    def compare_entities(
        self,
        comparison_context: Dict[str, Any]
    ) -> str:

        prompt = f"""
Compare these logistics entities.

Data:

{comparison_context}

Provide:

1. Comparison Summary
2. Strengths
3. Weaknesses
4. Winner
5. Recommendations
"""

        return self.generate_response(
            prompt
        )

    # ======================================================
    # ACTION PLAN ANALYSIS
    # ======================================================

    def analyze_action_plan(
        self,
        action_plan: List[Dict]
    ) -> str:

        prompt = f"""
Review this logistics action plan.

Actions:

{action_plan}

Provide:

1. Overall Assessment
2. Highest Priority Items
3. Risks
4. Recommended Execution Sequence
"""

        return self.generate_response(
            prompt
        )

    # ======================================================
    # FREE FORM LOGISTICS QUESTION
    # ======================================================

    def answer_question(
        self,
        question: str,
        context: Dict[str, Any]
    ) -> str:

        prompt = f"""
Question:

{question}

Business Data:

{context}

Answer the question using only the provided data.

If information is unavailable,
clearly state that.

Provide a concise business answer.
"""

        return self.generate_response(
            prompt
        )


deepseek_service = DeepSeekService()
