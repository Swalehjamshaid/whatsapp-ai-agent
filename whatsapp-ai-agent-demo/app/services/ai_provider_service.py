"""Enterprise AI Provider Service implementation for WhatsApp AI Agents.

Handles multi-LLM orchestration (Groq, DeepSeek, OpenAI) with automated fallback
chains, strict token/character budget enforcement for Meta's 4096 WhatsApp limit,
structured schema validation, database analytics synchronization, and connection pooling.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import sys
import time
import traceback
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Final, AsyncGenerator

import httpx
import orjson
from loguru import logger
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy import create_engine, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import sessionmaker, declarative_base
from tenacity import (
    AsyncRetrying,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential_jitter,
)

# -------------------------------------------------------------------------
# CONSTANTS & METRIC BOUNDS
# -------------------------------------------------------------------------
MAX_WHATSAPP_MESSAGE_LENGTH: Final[int] = 4096
DEFAULT_REQUEST_TIMEOUT: Final[float] = 25.0
MAX_DB_RETRIES: Final[int] = 3

# -------------------------------------------------------------------------
# EXCEPTIONS
# -------------------------------------------------------------------------
class AIProviderException(RuntimeError):
    """Base exception for all execution faults within the provider domain."""

class ProviderTimeoutException(AIProviderException):
    """Raised when an active LLM provider upstream exceeds SLA allocations."""

class ProviderCircuitBreakerException(AIProviderException):
    """Raised when an upstream provider endpoint is short-circuited."""

class DatabaseQueryException(AIProviderException):
    """Encapsulates downstream PostgreSQL execution or extraction failures."""

# -------------------------------------------------------------------------
# REUSABLE CONTEXT & SCHEMAS
# -------------------------------------------------------------------------
class ProviderMetrics(BaseModel):
    """Validatable data object for tracking engine performance indicators."""
    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True)

    request_id: str
    selected_provider: str
    fallback_provider: str | None = None
    database_query_time_ms: float = 0.0
    llm_execution_time_ms: float = 0.0
    total_execution_time_ms: float = 0.0
    errors_encountered: list[str] = Field(default_factory=list)

# -------------------------------------------------------------------------
# HTTP CLIENT POOL MANAGEMENT
# -------------------------------------------------------------------------
class AsyncHttpClientManager:
    """Manages thread-safe global async HTTP clients with optimized pooling properties."""
    _client: httpx.AsyncClient | None = None
    _lock: asyncio.Lock = asyncio.Lock()

    @classmethod
    async def get_client(cls) -> httpx.AsyncClient:
        """Retrieves or spins up the shared AsyncClient engine under lock isolation."""
        if cls._client is None or cls._client.is_closed:
            async with cls._lock:
                if cls._client is None or cls._client.is_closed:
                    limits = httpx.Limits(
                        max_connections=100,
                        max_keepalive_connections=50,
                        keepalive_expiry=30.0
                    )
                    cls._client = httpx.AsyncClient(
                        limits=limits,
                        timeout=httpx.Timeout(DEFAULT_REQUEST_TIMEOUT, connect=5.0)
                    )
        return cls._client

    @classmethod
    async def shutdown(cls) -> None:
        """Gracefully flushes keepalive arrays and terminates connections."""
        if cls._client and not cls._client.is_closed:
            async with cls._lock:
                if cls._client and not cls._client.is_closed:
                    await cls._client.aclose()
                    cls._client = None

# -------------------------------------------------------------------------
# POSTGRESQL ENGINE POOL RESILIENCY LAYER
# -------------------------------------------------------------------------
class DatabaseEnginePool:
    """Provides resilient connection pooling for analytics capture and dashboard verification."""
    _engine: Any = None
    _session_factory: Any = None
    _lock: asyncio.Lock = asyncio.Lock()

    @classmethod
    def _initialize(cls) -> None:
        """Extracts variables securely and provisions the SQLAlchemy engine infrastructure."""
        db_url = os.getenv("DATABASE_URL")
        if not db_url:
            # Fallback string construction if component keys are injected discretely
            user = os.getenv("POSTGRES_USER", "postgres")
            password = os.getenv("POSTGRES_PASSWORD", "")
            host = os.getenv("POSTGRES_HOST", "localhost")
            port = os.getenv("POSTGRES_PORT", "5432")
            db_name = os.getenv("POSTGRES_DB", "postgres")
            db_url = f"postgresql://{user}:{password}@{host}:{port}/{db_name}"

        # Cleans up system variables mismatching blueprint parameters
        if db_url.startswith("postgresql+psycopg2://"):
            pass
        elif db_url.startswith("postgres://"):
            db_url = db_url.replace("postgres://", "postgresql://", 1)

        pool_size = int(os.getenv("POSTGRES_POOL_SIZE", "10"))
        max_overflow = int(os.getenv("POSTGRES_MAX_OVERFLOW", "20"))
        pool_timeout = float(os.getenv("POSTGRES_POOL_TIMEOUT", "30.0"))

        cls._engine = create_engine(
            db_url,
            pool_size=pool_size,
            max_overflow=max_overflow,
            pool_timeout=pool_timeout,
            pool_pre_ping=True,
            json_serializer=lambda obj: orjson.dumps(obj).decode("utf-8")
        )
        cls._session_factory = sessionmaker(bind=cls._engine, expire_on_commit=False)

    @classmethod
    async def execute_query(cls, sql_statement: str, params: dict[str, Any]) -> list[dict[str, Any]]:
        """Executes database expressions off-loop inside thread containment blocks safely."""
        if cls._engine is None:
            async with cls._lock:
                if cls._engine is None:
                    cls._initialize()

        def _sync_execute() -> list[dict[str, Any]]:
            session = cls._session_factory()
            try:
                result = session.execute(text(sql_statement), params)
                if result.returns_rows:
                    return [dict(row._mapping) for row in result.all()]
                return []
            finally:
                session.close()

        loop = asyncio.get_running_loop()
        try:
            async for attempt in AsyncRetrying(
                stop=stop_after_attempt(MAX_DB_RETRIES),
                wait=wait_exponential_jitter(initial=0.5, max=3.0),
                retry=retry_if_exception_type(SQLAlchemyError),
                reraise=True
            ):
                with attempt:
                    return await loop.run_in_executor(None, _sync_execute)
        except Exception as exc:
            logger.error("Persistent failure executing PostgreSQL transaction: {}", str(exc))
            raise DatabaseQueryException("No matching data was found in PostgreSQL.") from exc

# -------------------------------------------------------------------------
# CORE IMPLEMENTATION
# -------------------------------------------------------------------------
class AIProviderService:
    """Core domain wrapper parsing operations, database extraction, and multi-LLM processing."""

    def __init__(self) -> None:
        self.primary_provider: Final[str] = os.getenv("PRIMARY_AI_PROVIDER", "groq").lower()
        self.secondary_provider: Final[str] = os.getenv("AI_PROVIDER", "deepseek").lower()
        self.enable_analytics: Final[bool] = os.getenv("ENABLE_ANALYTICS", "True").lower() == "true"
        
        # Runtime mapping definitions
        self.provider_chain: Final[list[str]] = [self.primary_provider, self.secondary_provider, "openai"]
        self._deduplicate_provider_chain()

    def _deduplicate_provider_chain(self) -> None:
        """Sanitizes order structures to guarantee no loops run concurrently on exact providers."""
        seen = set()
        deduped = []
        for p in self.provider_chain:
            clean_p = p.strip().lower()
            if clean_p and clean_p not in seen:
                seen.add(clean_p)
                deduped.append(clean_p)
        # Guarantee full list fallback representation
        for mandatory in ["groq", "deepseek", "openai"]:
            if mandatory not in seen:
                deduped.append(mandatory)
        object.__setattr__(self, "provider_chain", deduped)

    @staticmethod
    def split_whatsapp_message(text_content: str) -> list[str]:
        """Slices output streams down to fit within the 4096 Meta constraint limits safely."""
        if len(text_content) <= MAX_WHATSAPP_MESSAGE_LENGTH:
            return [text_content]

        chunks: list[str] = []
        current_chunk: list[str] = []
        current_length = 0

        # Attempt to slice cleanly via structural elements (paragraphs, double returns)
        lines = text_content.splitlines(keepends=True)
        for line in lines:
            if current_length + len(line) > MAX_WHATSAPP_MESSAGE_LENGTH:
                if current_chunk:
                    chunks.append("".join(current_chunk))
                    current_chunk = []
                    current_length = 0
                
                # If a single structural item breaches bounds, slice it byte-wise
                if len(line) > MAX_WHATSAPP_MESSAGE_LENGTH:
                    for i in range(0, len(line), MAX_WHATSAPP_MESSAGE_LENGTH):
                        chunks.append(line[i:i + MAX_WHATSAPP_MESSAGE_LENGTH])
                else:
                    current_chunk.append(line)
                    current_length = len(line)
            else:
                current_chunk.append(line)
                current_length += len(line)

        if current_chunk:
            chunks.append("".join(current_chunk))

        return chunks

    async def _dispatch_llm_call(self, provider: str, messages: list[dict[str, str]], timeout_secs: float) -> str:
        """Routes execution parameters targeting specialized client connectors directly."""
        client = await AsyncHttpClientManager.get_client()
        
        if provider == "groq":
            api_key = os.getenv("GROQ_API_KEY", "")
            model = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile")
            url = "https://api.groq.com/openai/v1/chat/completions"
        elif provider == "deepseek":
            api_key = os.getenv("DEEPSEEK_API_KEY", "")
            model = os.getenv("DEEPSEEK_MODEL", "deepseek-chat")
            url = "https://api.deepseek.com/v1/chat/completions"
        elif provider == "openai":
            api_key = os.getenv("OPENAI_API_KEY", "")
            model = "gpt-4o-mini"
            url = "https://api.openai.com/v1/chat/completions"
        else:
            raise AIProviderException(f"Unsupported LLM provider requested: {provider}")

        if not api_key:
            raise AIProviderException(f"API credential parameter missing for provider: {provider}")

        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json"
        }
        payload = {
            "model": model,
            "messages": messages,
            "temperature": 0.2
        }

        response = await client.post(url, json=payload, headers=headers, timeout=timeout_secs)
        if response.status_code != 200:
            raise AIProviderException(f"Upstream provider {provider} raised HTTP status error {response.status_code}: {response.text}")
        
        data = orjson.loads(response.content)
        return str(data["choices"][0]["message"]["content"])

    async def execute_llm_with_fallback(self, messages: list[dict[str, str]], request_id: str) -> tuple[str, str, str | None, list[str]]:
        """Tunnels calls down the chain array until a successful return parameter registers."""
        errors_logged: list[str] = []
        active_chain = list(self.provider_chain)
        
        for idx, provider in enumerate(active_chain):
            started_timer = time.perf_counter()
            fallback_partner = active_chain[idx + 1] if idx + 1 < len(active_chain) else None
            
            try:
                logger.info("Attempting LLM inference execution. ID: {} | Engine: {}", request_id, provider)
                output = await self._dispatch_llm_call(provider, messages, timeout_secs=18.0)
                return output, provider, fallback_partner, errors_logged
            except Exception as exc:
                err_msg = f"Inference engine execution error on target '{provider}': {str(exc)}"
                logger.warning(err_msg)
                errors_logged.append(err_msg)
                continue

        raise ProviderCircuitBreakerException("All configured upstream AI service layers are currently exhausted.")

    async def write_analytics_record(self, metrics: ProviderMetrics) -> None:
        """Pipes execution logs asynchronously downstream directly matching domain database configurations."""
        if not self.enable_analytics:
            return
            
        sql = """
            INSERT INTO ai_provider_analytics 
            (request_id, selected_provider, fallback_provider, database_query_time_ms, llm_execution_time_ms, total_execution_time_ms, errors, created_at)
            VALUES (:request_id, :selected_provider, :fallback_provider, :db_time, :llm_time, :total_time, :errors, :created_at)
        """
        params = {
            "request_id": metrics.request_id,
            "selected_provider": metrics.selected_provider,
            "fallback_provider": metrics.fallback_provider,
            "db_time": metrics.database_query_time_ms,
            "llm_time": metrics.llm_execution_time_ms,
            "total_time": metrics.total_execution_time_ms,
            "errors": json.dumps(metrics.errors_encountered),
            "created_at": datetime.now(timezone.utc)
        }
        try:
            # Fire-and-forget or loop isolation tracking block
            await DatabaseEnginePool.execute_query(sql, params)
        except Exception as exc:
            logger.error("Failed writing execution analytics payload packet: {}", str(exc))

    async def process_whatsapp_query(self, message: str, sender: str | None = None, **context: Any) -> str:
        """Processes structured intent calls passing dashboard lookups and domain validations down to data layers."""
        start_time = time.perf_counter()
        request_id = str(context.get("request_id") or uuid.uuid4())
        
        # Initialization steps tracking timing parameters
        db_ms = 0.0
        llm_ms = 0.0
        selected_provider = self.primary_provider
        fallback_provider = self.secondary_provider
        errors: list[str] = []

        try:
            normalized = " ".join(message.split()).lower()
            db_data: list[dict[str, Any]] = []
            db_query_start = time.perf_counter()

            # -----------------------------------------------------------------
            # DATA ENGINE DISPATCH & RESOLUTION ROUTING TABLE
            # -----------------------------------------------------------------
            if "dn_lookup" in normalized or "dn_dashboard" in normalized:
                match = re.search(r"\b\d{6,20}\b", normalized)
                if match:
                    sql = "SELECT * FROM delivery_notes WHERE dn_number = :dn LIMIT 1"
                    db_data = await DatabaseEnginePool.execute_query(sql, {"dn": match.group(0)})
                else:
                    return "A valid delivery note (DN) number was not supplied in your text context."

            elif "pending_dn" in normalized or "pending_dns" in normalized:
                sql = "SELECT id, dn_number, status, ETA FROM delivery_notes WHERE status = 'pending' ORDER BY created_at DESC LIMIT 5"
                db_data = await DatabaseEnginePool.execute_query(sql, {})

            elif "dealer_dashboard" in normalized:
                sql = "SELECT id, dealer_name, performance_score, active_orders FROM dealers ORDER BY performance_score DESC LIMIT 5"
                db_data = await DatabaseEnginePool.execute_query(sql, {})

            elif "warehouse_dashboard" in normalized:
                sql = "SELECT warehouse_name, current_capacity, threshold_breached FROM warehouses WHERE threshold_breached = TRUE LIMIT 5"
                db_data = await DatabaseEnginePool.execute_query(sql, {})

            elif "city_dashboard" in normalized:
                sql = "SELECT city_name, gross_revenue, volume_delivered FROM city_metrics ORDER BY gross_revenue DESC LIMIT 5"
                db_data = await DatabaseEnginePool.execute_query(sql, {})

            elif "product_dashboard" in normalized:
                sql = "SELECT sku, product_name, stock_level FROM products WHERE stock_level < reorder_point LIMIT 10"
                db_data = await DatabaseEnginePool.execute_query(sql, {})

            db_ms = (time.perf_counter() - db_query_start) * 1000.0

            # Structural checking to ensure no placeholder states leak
            data_context_str = ""
            if db_data:
                data_context_str = orjson.dumps(db_data).decode("utf-8")
            elif any(keyword in normalized for keyword in ["dn_lookup", "pending", "dashboard", "product"]):
                return "No matching data was found in PostgreSQL."

            # Construct execution payload strings for the upstream model infrastructure
            system_prompt = (
                "You are an enterprise AI customer service engine. Transform structured transactional database metrics "
                "into natural, crisp, business-oriented insights appropriate for real-time WhatsApp processing.\n"
                "Constraints: Never mention raw structural configurations or JSON arrays explicitly. Format using line spaces cleanly."
            )
            
            user_input_prompt = f"User Request: {message}\n"
            if data_context_str:
                user_input_prompt += f"PostgreSQL Context Records: {data_context_str}"

            messages = [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_input_prompt}
            ]

            llm_start_time = time.perf_counter()
            llm_output, selected_provider, fallback_provider, logged_errs = await self.execute_llm_with_fallback(messages, request_id)
            llm_ms = (time.perf_counter() - llm_start_time) * 1000.0
            errors.extend(logged_errs)

            # Safeguard text structure formats against length constraints
            final_responses = self.split_whatsapp_message(llm_output)
            total_ms = (time.perf_counter() - start_time) * 1000.0

            # Sync analytics metadata to tracking layer
            metrics_packet = ProviderMetrics(
                request_id=request_id,
                selected_provider=selected_provider,
                fallback_provider=fallback_provider,
                database_query_time_ms=db_ms,
                llm_execution_time_ms=llm_ms,
                total_execution_time_ms=total_ms,
                errors_encountered=errors
            )
            await self.write_analytics_record(metrics_packet)
            
            logger.info("Request completed successfully. RequestID: {} | TotalTime: {:.2f}ms", request_id, total_ms)
            return final_responses[0]

        except Exception as global_exc:
            total_ms = (time.perf_counter() - start_time) * 1000.0
            tb_str = "".join(traceback.format_exception(type(global_exc), global_exc, global_exc.__traceback__))
            logger.critical("Fatal execution cycle inside AI core framework. ID: {} | Stack: {}", request_id, tb_str)
            
            errors.append(f"Global exception hook captured fault: {str(global_exc)}")
            
            # Persist tracking parameters even inside complete structural collapse
            try:
                fail_packet = ProviderMetrics(
                    request_id=request_id,
                    selected_provider=selected_provider,
                    fallback_provider=fallback_provider,
                    database_query_time_ms=db_ms,
                    llm_execution_time_ms=llm_ms,
                    total_execution_time_ms=total_ms,
                    errors_encountered=errors
                )
                await self.write_analytics_record(fail_packet)
            except Exception:
                pass

            return "⚠️ AI service is currently unavailable. Please try again later."

    async def process_query(self, message: str, sender: str | None = None, **context: Any) -> str:
        """Alias handling system orchestration forwarding mechanics natively."""
        return await self.process_whatsapp_query(message, sender, **context)

    async def enhance_response(self, decision: Any, business_response: Any, message: str, request_id: str) -> str:
        """Enhances data outputs into stylized presentation parameters directly matching orchestrator signatures."""
        messages = [
            {
                "role": "system",
                "content": "You are a senior copywriter optimizing messaging layouts. Format raw context descriptions beautifully for WhatsApp deployment."
            },
            {
                "role": "user",
                "content": f"User prompt: {message}\nBusiness asset payload context: {str(business_response)}"
            }
        ]
        try:
            output, _, _, _ = await self.execute_llm_with_fallback(messages, request_id)
            return self.split_whatsapp_message(output)[0]
        except Exception as exc:
            logger.error("Failed generation enhancement pass. Utilizing raw business fallback text string: {}", str(exc))
            return str(business_response)
