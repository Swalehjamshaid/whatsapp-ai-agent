# ==========================================================
# FILE: app/services/whatsapp_service.py (ENTERPRISE v6.3)
# ==========================================================
# PURPOSE: WhatsApp Cloud API Communication Layer
# ARCHITECTURE: webhook.py → ai_query_service.py → ... → whatsapp_service.py → Meta API
#
# ENHANCEMENTS v6.3:
# - ✅ ADDED: Auto-start background services (self-healing)
# - ✅ ADDED: Direct send fallback (worker failure protection)
# - ✅ ADDED: Queue overflow protection
# - ✅ FIXED: get_message_trace() for dataclass objects
# - ✅ ADDED: Worker heartbeat monitoring
# - ✅ ADDED: Meta API response history (last 50)
# - ✅ ADDED: Worker auto-restart
# - ✅ ADDED: Startup self-test
# - ✅ ADDED: Diagnostic endpoint support
# - ✅ ADDED: Delivery tracking metrics (timestamps)
# - ✅ PRESERVED: All existing public APIs (100% backward compatible)
# ==========================================================

import re
import json
import time
import asyncio
import hashlib
import uuid
from enum import Enum
from typing import Dict, Any, Optional, List, Tuple
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from collections import deque
from threading import Lock
from loguru import logger
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from cachetools import TTLCache
from dataclasses import dataclass

from app.config import config


# ==========================================================
# CONSTANTS
# ==========================================================

MAX_MESSAGE_LENGTH = 4000
DEFAULT_TIMEOUT = 30
MAX_RETRIES = 5
RETRY_BACKOFF_FACTOR = 2
DEFAULT_API_VERSION = "v20.0"

# Rate limiting
MAX_MESSAGES_PER_MINUTE = 80
MAX_MESSAGES_PER_HOUR = 1000
MAX_MESSAGES_PER_DAY = 5000

# Circuit breaker
CIRCUIT_FAILURE_THRESHOLD = 3
CIRCUIT_RECOVERY_TIMEOUT = 60
CIRCUIT_HALF_OPEN_MAX_ATTEMPTS = 2

# Queue
QUEUE_MAX_SIZE = 10000
QUEUE_WORKER_INTERVAL = 0.1
DEAD_LETTER_MAX_SIZE = 5000

# Health monitor
HEALTH_CHECK_INTERVAL = 300  # 5 minutes
TOKEN_EXPIRY_WARNING_DAYS = 7

# Meta response history
META_HISTORY_MAX_SIZE = 50


# ==========================================================
# ENUMS
# ==========================================================

class MessagePriority(Enum):
    CRITICAL = 0   # System alerts, executive notifications
    HIGH = 1       # Time-sensitive responses
    NORMAL = 2     # Standard user responses
    LOW = 3        # Bulk messages, non-urgent


class MessageStatus(Enum):
    QUEUED = "queued"
    SENT = "sent"
    DELIVERED = "delivered"
    READ = "read"
    FAILED = "failed"
    DEAD_LETTER = "dead_letter"


class CircuitState(Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


# ==========================================================
# DATA CLASSES
# ==========================================================

@dataclass
class QueuedMessage:
    """Message in the queue"""
    id: str
    phone_number: str
    message: str
    priority: MessagePriority
    created_at: datetime
    retry_count: int = 0
    message_id: Optional[str] = None
    request_id: Optional[str] = None
    correlation_id: Optional[str] = None
    status: MessageStatus = MessageStatus.QUEUED
    scheduled_for: Optional[datetime] = None
    flow_trace: List[str] = field(default_factory=list)
    sent_at: Optional[datetime] = None  # PRIORITY 10: Delivery tracking
    delivered_at: Optional[datetime] = None  # PRIORITY 10: Delivery tracking
    read_at: Optional[datetime] = None  # PRIORITY 10: Delivery tracking


@dataclass
class DeliveryStatus:
    """Delivery status of a message"""
    message_id: str
    status: MessageStatus
    updated_at: datetime
    meta_status: Optional[str] = None
    error: Optional[str] = None
    sent_at: Optional[datetime] = None  # PRIORITY 10: Delivery tracking
    delivered_at: Optional[datetime] = None  # PRIORITY 10: Delivery tracking
    read_at: Optional[datetime] = None  # PRIORITY 10: Delivery tracking


@dataclass
class MessageAnalytics:
    """Message analytics"""
    total_sent: int = 0
    total_delivered: int = 0
    total_read: int = 0
    total_failed: int = 0
    total_queued: int = 0
    avg_send_time_ms: float = 0.0
    success_rate: float = 100.0
    last_hour_sent: int = 0
    last_minute_sent: int = 0
    avg_delivery_time_ms: float = 0.0  # PRIORITY 10: Delivery tracking
    avg_read_time_ms: float = 0.0  # PRIORITY 10: Delivery tracking


# ==========================================================
# MESSAGE TEMPLATES (ENHANCED)
# ==========================================================

class TemplateManager:
    """Centralized message template management"""
    
    TEMPLATES = {
        "help": """📋 *Available Commands*

• *Track DN* - Send any 10+ digit number
• *Dealer Performance* - "Show dealer ABC Traders"
• *Warehouse Status* - "Stock at Mumbai warehouse"
• *Pending PODs* - "Pending POD Lahore"
• *KPI Dashboard* - "Show me KPIs"
• *Control Tower* - "Control tower"

Need help? Reply with your question.""",
        
        "welcome": "👋 Welcome to Logistics AI!\n\nSend any 10+ digit number to track your delivery note.",
        
        "error": "⚠️ *System Notice*\n\nI encountered an issue processing your request. Please try again in a moment.\n\nType 'Help' for available commands.",
        
        "timeout": "⏳ *Processing*\n\nYour request is taking longer than expected. I'll continue processing and respond shortly.",
        
        "rate_limit": "⏱️ *Rate Limit*\n\nYou're sending messages too quickly. Please wait a moment before sending more messages.",
        
        "maintenance": "🔧 *Maintenance Mode*\n\nSystem is undergoing maintenance. Please try again in a few minutes.",
        
        "executive_summary": "📊 *Executive Summary*\n\n{summary}",
        
        "kpi_report": "📈 *KPI Report - {period}*\n\n{report}"
    }
    
    @classmethod
    def get(cls, template_name: str, **kwargs) -> str:
        """Get formatted template"""
        template = cls.TEMPLATES.get(template_name, cls.TEMPLATES["help"])
        try:
            return template.format(**kwargs)
        except KeyError:
            return template


# ==========================================================
# RATE LIMIT MANAGER
# ==========================================================

class RateLimitManager:
    """Manage rate limits per phone number and globally"""
    
    def __init__(self):
        self._phone_limits = TTLCache(maxsize=10000, ttl=86400)  # 24 hours
        self._global_minute_counter = deque(maxlen=MAX_MESSAGES_PER_MINUTE)
        self._global_hour_counter = deque(maxlen=MAX_MESSAGES_PER_HOUR)
        self._lock = Lock()
    
    def can_send(self, phone_number: str) -> Tuple[bool, Optional[str]]:
        """Check if message can be sent"""
        with self._lock:
            now = time.time()
            
            # Clean old entries
            while self._global_minute_counter and self._global_minute_counter[0] < now - 60:
                self._global_minute_counter.popleft()
            while self._global_hour_counter and self._global_hour_counter[0] < now - 3600:
                self._global_hour_counter.popleft()
            
            # Check global limits
            if len(self._global_minute_counter) >= MAX_MESSAGES_PER_MINUTE:
                return False, "Global minute rate limit exceeded"
            
            if len(self._global_hour_counter) >= MAX_MESSAGES_PER_HOUR:
                return False, "Global hour rate limit exceeded"
            
            # Check phone-specific limits
            phone_limits = self._phone_limits.get(phone_number, {"minute": deque(), "hour": deque()})
            
            while phone_limits["minute"] and phone_limits["minute"][0] < now - 60:
                phone_limits["minute"].popleft()
            while phone_limits["hour"] and phone_limits["hour"][0] < now - 3600:
                phone_limits["hour"].popleft()
            
            if len(phone_limits["minute"]) >= 5:  # 5 messages per minute per phone
                return False, "Phone minute rate limit exceeded"
            
            if len(phone_limits["hour"]) >= 50:  # 50 messages per hour per phone
                return False, "Phone hour rate limit exceeded"
            
            return True, None
    
    def record_send(self, phone_number: str):
        """Record a message send attempt"""
        with self._lock:
            now = time.time()
            self._global_minute_counter.append(now)
            self._global_hour_counter.append(now)
            
            phone_limits = self._phone_limits.get(phone_number, {"minute": deque(), "hour": deque()})
            phone_limits["minute"].append(now)
            phone_limits["hour"].append(now)
            self._phone_limits[phone_number] = phone_limits
    
    def get_stats(self) -> Dict[str, Any]:
        """Get rate limit statistics"""
        with self._lock:
            return {
                "global_minute_usage": len(self._global_minute_counter),
                "global_minute_limit": MAX_MESSAGES_PER_MINUTE,
                "global_hour_usage": len(self._global_hour_counter),
                "global_hour_limit": MAX_MESSAGES_PER_HOUR,
                "phones_tracked": len(self._phone_limits)
            }


# ==========================================================
# CIRCUIT BREAKER
# ==========================================================

class CircuitBreaker:
    """Prevent cascading failures when Meta API is down"""
    
    def __init__(self, name: str):
        self.name = name
        self.state = CircuitState.CLOSED
        self.failure_count = 0
        self.last_failure_time = None
        self.half_open_attempts = 0
        self._lock = Lock()
    
    def call(self, func, *args, **kwargs):
        """Execute function with circuit breaker protection"""
        with self._lock:
            if self.state == CircuitState.OPEN:
                if time.time() - self.last_failure_time > CIRCUIT_RECOVERY_TIMEOUT:
                    self.state = CircuitState.HALF_OPEN
                    self.half_open_attempts = 0
                    logger.info(f"[CircuitBreaker:{self.name}] Transitioned to HALF_OPEN")
                else:
                    raise Exception(f"Circuit {self.name} is OPEN")
        
        try:
            result = func(*args, **kwargs)
            
            with self._lock:
                if self.state == CircuitState.HALF_OPEN:
                    self.half_open_attempts += 1
                    if self.half_open_attempts >= CIRCUIT_HALF_OPEN_MAX_ATTEMPTS:
                        self.state = CircuitState.CLOSED
                        self.failure_count = 0
                        logger.info(f"[CircuitBreaker:{self.name}] Transitioned to CLOSED")
                return result
                
        except Exception as e:
            with self._lock:
                self.failure_count += 1
                self.last_failure_time = time.time()
                
                if self.failure_count >= CIRCUIT_FAILURE_THRESHOLD:
                    self.state = CircuitState.OPEN
                    logger.error(f"[CircuitBreaker:{self.name}] Transitioned to OPEN")
            raise
    
    def get_state(self) -> Dict[str, Any]:
        """Get circuit breaker state"""
        with self._lock:
            return {
                "name": self.name,
                "state": self.state.value,
                "failure_count": self.failure_count,
                "last_failure_time": self.last_failure_time
            }


# ==========================================================
# MESSAGE OPTIMIZER (Smart splitting, no truncation)
# ==========================================================

class MessageOptimizer:
    """Optimize messages for WhatsApp delivery"""
    
    @staticmethod
    def optimize(message: str) -> List[str]:
        """Split long messages intelligently"""
        if len(message) <= MAX_MESSAGE_LENGTH:
            return [message]
        
        chunks = []
        lines = message.split('\n')
        current_chunk = []
        current_length = 0
        
        for line in lines:
            line_length = len(line) + 1  # +1 for newline
            
            if current_length + line_length > MAX_MESSAGE_LENGTH:
                # Save current chunk
                if current_chunk:
                    chunks.append('\n'.join(current_chunk))
                # Start new chunk
                current_chunk = [line]
                current_length = line_length
            else:
                current_chunk.append(line)
                current_length += line_length
        
        # Add last chunk
        if current_chunk:
            chunks.append('\n'.join(current_chunk))
        
        # Add continuation markers
        if len(chunks) > 1:
            for i, chunk in enumerate(chunks, 1):
                chunks[i-1] = f"({i}/{len(chunks)})\n{chunk}"
        
        logger.info(f"Message optimized: {len(message)} chars → {len(chunks)} chunks")
        return chunks
    
    @staticmethod
    def summarize(message: str, max_length: int = 500) -> str:
        """Create a summary of long messages"""
        if len(message) <= max_length:
            return message
        
        # Try to find a good breaking point
        break_points = ['. ', '! ', '? ', '\n\n', '\n', ' ']
        for bp in break_points:
            pos = message[:max_length].rfind(bp)
            if pos > max_length * 0.7:
                return message[:pos + len(bp)] + "...\n\n[Message truncated]"
        
        return message[:max_length - 50] + "...\n\n[Message truncated]"


# ==========================================================
# DEAD LETTER QUEUE
# ==========================================================

class DeadLetterQueue:
    """Store failed messages for manual recovery"""
    
    def __init__(self):
        self._messages = TTLCache(maxsize=DEAD_LETTER_MAX_SIZE, ttl=86400 * 7)  # 7 days
        self._lock = Lock()
    
    def add(self, message: QueuedMessage, error: str):
        """Add failed message to dead letter queue"""
        with self._lock:
            key = f"{message.id}_{int(time.time())}"
            self._messages[key] = {
                "message": message,
                "error": error,
                "timestamp": datetime.now().isoformat()
            }
            logger.warning(f"💀 Message {message.id} added to dead letter queue: {error}")
    
    def get_all(self) -> List[Dict]:
        """Get all dead letter messages"""
        with self._lock:
            return [{"key": k, **v} for k, v in self._messages.items()]
    
    def retry(self, key: str) -> Optional[QueuedMessage]:
        """Retry a dead letter message"""
        with self._lock:
            if key in self._messages:
                message_data = self._messages.pop(key)
                return message_data["message"]
        return None
    
    def clear(self):
        """Clear dead letter queue"""
        with self._lock:
            count = len(self._messages)
            self._messages.clear()
            logger.info(f"Cleared {count} messages from dead letter queue")
            return count
    
    def get_stats(self) -> Dict[str, int]:
        """Get dead letter queue statistics"""
        return {
            "size": len(self._messages),
            "max_size": DEAD_LETTER_MAX_SIZE
        }


# ==========================================================
# MESSAGE QUEUE (Async processing)
# ==========================================================

class WhatsAppMessageQueue:
    """Async message queue for reliable delivery"""
    
    def __init__(self, send_func, dead_letter_queue: DeadLetterQueue):
        self._queue = deque(maxlen=QUEUE_MAX_SIZE)
        self._send_func = send_func
        self.dead_letter_queue = dead_letter_queue
        self._worker_task = None
        self._running = False
        self._lock = Lock()
        self._analytics = MessageAnalytics()
        self._last_hour_reset = time.time()
        self._last_minute_reset = time.time()
        
        # PRIORITY 5: Worker heartbeat
        self._last_worker_activity = time.time()
        self._heartbeat_interval = 30  # seconds
    
    def enqueue(self, message: QueuedMessage) -> bool:
        """Add message to queue with overflow protection"""
        # PRIORITY 3: Queue overflow protection
        with self._lock:
            if len(self._queue) >= QUEUE_MAX_SIZE:
                logger.critical(f"⚠️ Queue full! Cannot enqueue message {message.id}")
                return False
            
            # Check for duplicate
            for existing in self._queue:
                if (existing.correlation_id and existing.correlation_id == message.correlation_id and
                    existing.status != MessageStatus.FAILED):
                    logger.warning(f"Duplicate message rejected: {message.correlation_id}")
                    return False
            
            # Insert by priority
            inserted = False
            for i, existing in enumerate(self._queue):
                if message.priority.value < existing.priority.value:
                    self._queue.insert(i, message)
                    inserted = True
                    break
            
            if not inserted:
                self._queue.append(message)
            
            self._analytics.total_queued += 1
            message.flow_trace.append(f"QUEUED at {datetime.now().isoformat()}")
            
            logger.info(f"📥 Message queued: {message.id} (priority: {message.priority.name}, correlation: {message.correlation_id})")
            return True
    
    def dequeue(self) -> Optional[QueuedMessage]:
        """Get next message from queue"""
        with self._lock:
            if self._queue:
                return self._queue.popleft()
        return None
    
    def size(self) -> int:
        """Get queue size"""
        with self._lock:
            return len(self._queue)
    
    def is_full(self) -> bool:
        """Check if queue is full"""
        return self.size() >= QUEUE_MAX_SIZE
    
    async def start_worker(self):
        """Start background worker"""
        if self._running:
            logger.warning("Worker already running")
            return
        
        self._running = True
        self._worker_task = asyncio.create_task(self._worker_loop())
        logger.info("🚀 Message queue worker started")
    
    async def stop_worker(self):
        """Stop background worker"""
        self._running = False
        if self._worker_task:
            await self._worker_task
        logger.info("⏹️ Message queue worker stopped")
    
    def is_worker_alive(self) -> bool:
        """Check if worker is alive and responsive"""
        if not self._running:
            return False
        # PRIORITY 5: Check heartbeat
        time_since_activity = time.time() - self._last_worker_activity
        if time_since_activity > self._heartbeat_interval * 3:
            return False
        return True
    
    # PRIORITY 7: Worker auto-restart
    async def ensure_worker_running(self):
        """Ensure worker is running, restart if needed"""
        if not self.is_worker_alive():
            logger.warning("⚠️ Worker appears dead - restarting...")
            self._running = False
            if self._worker_task:
                self._worker_task.cancel()
                try:
                    await self._worker_task
                except:
                    pass
            await self.start_worker()
            return True
        return False
    
    # PRIORITY 8: Worker crash protection
    async def _worker_loop(self):
        """Background worker loop with crash protection"""
        logger.info("🔄 Worker loop started")
        
        while self._running:
            try:
                await self._worker_iteration()
                # PRIORITY 5: Update heartbeat
                self._last_worker_activity = time.time()
            except Exception as e:
                logger.exception(f"❌ Worker iteration failed: {e}")
                # Continue running - don't crash
                await asyncio.sleep(1)
    
    async def _worker_iteration(self):
        """Single worker iteration with error handling"""
        try:
            message = self.dequeue()
            if message:
                # Check if scheduled for future
                if message.scheduled_for and datetime.now() < message.scheduled_for:
                    # Re-queue with delay
                    self.enqueue(message)
                    await asyncio.sleep(1)
                    return
                
                message.flow_trace.append(f"WORKER_PICKED at {datetime.now().isoformat()}")
                
                logger.info(f"📤 Sending queued message: {message.id} to {message.phone_number} (correlation: {message.correlation_id})")
                start_time = time.time()
                
                loop = asyncio.get_running_loop()
                
                result = await loop.run_in_executor(
                    None,
                    lambda: self._send_func(
                        phone_number=message.phone_number,
                        message=message.message,
                        message_id=message.message_id,
                        request_id=message.request_id
                    )
                )
                
                duration_ms = (time.time() - start_time) * 1000
                self._update_analytics(result, duration_ms)
                
                if result.get("success"):
                    message.status = MessageStatus.SENT
                    message.sent_at = datetime.now()  # PRIORITY 10
                    message.flow_trace.append(f"META_RESPONSE_SUCCESS at {datetime.now().isoformat()}")
                    
                    logger.info(f"✅ Message {message.id} sent successfully in {duration_ms:.0f}ms")
                    
                    if result.get("message_id"):
                        message.message_id = result["message_id"]
                        logger.info(f"   Meta Message ID: {message.message_id}")
                    
                    message.flow_trace.append(f"DELIVERED at {datetime.now().isoformat()}")
                else:
                    message.status = MessageStatus.FAILED
                    error_msg = result.get("error", "Unknown error")
                    message.flow_trace.append(f"META_RESPONSE_FAILED at {datetime.now().isoformat()}: {error_msg}")
                    
                    logger.error(f"❌ Message {message.id} failed: {error_msg}")
                    
                    if message.retry_count < MAX_RETRIES:
                        message.retry_count += 1
                        delay = 2 ** message.retry_count
                        message.scheduled_for = datetime.now() + timedelta(seconds=delay)
                        self.enqueue(message)
                        message.flow_trace.append(f"RETRY_SCHEDULED at {datetime.now().isoformat()} (attempt {message.retry_count})")
                        logger.info(f"🔄 Message {message.id} scheduled for retry {message.retry_count} in {delay}s")
                    else:
                        logger.critical(f"💀 Worker retry exhausted for {message.id} - moving to dead letter")
                        self.dead_letter_queue.add(message, error_msg)
                        message.status = MessageStatus.DEAD_LETTER
                        message.flow_trace.append(f"DEAD_LETTER at {datetime.now().isoformat()}: {error_msg}")
                        logger.error(f"💀 Message {message.id} moved to dead letter queue after {MAX_RETRIES} retries")
                        
            else:
                await asyncio.sleep(QUEUE_WORKER_INTERVAL)
        except Exception as e:
            logger.error(f"Worker iteration error: {e}")
            raise
    
    def _update_analytics(self, result: Dict, duration_ms: float):
        """Update analytics"""
        now = time.time()
        
        if now - self._last_minute_reset > 60:
            self._analytics.last_minute_sent = 0
            self._last_minute_reset = now
        
        if now - self._last_hour_reset > 3600:
            self._analytics.last_hour_sent = 0
            self._last_hour_reset = now
        
        if result.get("success"):
            self._analytics.total_sent += 1
            self._analytics.last_minute_sent += 1
            self._analytics.last_hour_sent += 1
            
            total = self._analytics.avg_send_time_ms * (self._analytics.total_sent - 1)
            self._analytics.avg_send_time_ms = (total + duration_ms) / self._analytics.total_sent
        else:
            self._analytics.total_failed += 1
        
        total_attempts = self._analytics.total_sent + self._analytics.total_failed
        if total_attempts > 0:
            self._analytics.success_rate = (self._analytics.total_sent / total_attempts) * 100
    
    def get_analytics(self) -> Dict[str, Any]:
        """Get analytics"""
        return {
            "total_sent": self._analytics.total_sent,
            "total_delivered": self._analytics.total_delivered,
            "total_read": self._analytics.total_read,
            "total_failed": self._analytics.total_failed,
            "total_queued": self._analytics.total_queued,
            "avg_send_time_ms": round(self._analytics.avg_send_time_ms, 2),
            "success_rate": round(self._analytics.success_rate, 2),
            "last_hour_sent": self._analytics.last_hour_sent,
            "last_minute_sent": self._analytics.last_minute_sent,
            "queue_size": self.size(),
            "avg_delivery_time_ms": round(self._analytics.avg_delivery_time_ms, 2),
            "avg_read_time_ms": round(self._analytics.avg_read_time_ms, 2)
        }
    
    def get_worker_status(self) -> Dict[str, Any]:
        """Get worker status"""
        return {
            "running": self._running,
            "queue_size": self.size(),
            "is_full": self.is_full(),
            "alive": self.is_worker_alive(),
            "last_activity": datetime.fromtimestamp(self._last_worker_activity).isoformat() if self._last_worker_activity else None,
            "analytics": self.get_analytics()
        }


# ==========================================================
# META HEALTH MONITOR
# ==========================================================

class MetaHealthMonitor:
    """Automated Meta API health monitoring"""
    
    def __init__(self, whatsapp_service):
        self.service = whatsapp_service
        self._last_check = None
        self._health_status = {"status": "unknown", "last_success": None, "failures": 0}
        self._monitor_task = None
        self._running = False
    
    async def start_monitoring(self):
        """Start background health monitoring"""
        if self._running:
            logger.warning("Health monitor already running")
            return
        
        self._running = True
        self._monitor_task = asyncio.create_task(self._monitor_loop())
        logger.info("🩺 Meta Health Monitor started")
    
    async def stop_monitoring(self):
        """Stop background monitoring"""
        self._running = False
        if self._monitor_task:
            await self._monitor_task
        logger.info("⏹️ Meta Health Monitor stopped")
    
    async def _monitor_loop(self):
        """Background health check loop"""
        while self._running:
            try:
                await self._check_health()
                
                # PRIORITY 7: Check worker health
                if hasattr(self.service, '_message_queue'):
                    await self.service._message_queue.ensure_worker_running()
                
                await asyncio.sleep(HEALTH_CHECK_INTERVAL)
            except Exception as e:
                logger.error(f"Health monitor error: {e}")
                await asyncio.sleep(60)
    
    async def _check_health(self):
        """Perform health check"""
        try:
            loop = asyncio.get_running_loop()
            result = await loop.run_in_executor(None, self.service.health_check, True)
            
            self._last_check = datetime.now()
            
            if result.get("meta_api_status") == "healthy":
                self._health_status["status"] = "healthy"
                self._health_status["last_success"] = datetime.now()
                self._health_status["failures"] = 0
                logger.debug("✅ Meta API health check passed")
            else:
                self._health_status["failures"] += 1
                if self._health_status["failures"] >= 3:
                    self._health_status["status"] = "unhealthy"
                    logger.warning("⚠️ Meta API health check failed: degraded to UNHEALTHY")
                else:
                    self._health_status["status"] = "degraded"
                    logger.warning(f"⚠️ Meta API health check failed: degraded ({self._health_status['failures']}/3)")
        except Exception as e:
            self._health_status["failures"] += 1
            self._health_status["status"] = "unhealthy"
            logger.error(f"❌ Health check failed: {e}")
    
    def get_status(self) -> Dict[str, Any]:
        """Get current health status"""
        return {
            "status": self._health_status["status"],
            "last_check": self._last_check.isoformat() if self._last_check else None,
            "last_success": self._health_status["last_success"].isoformat() if self._health_status["last_success"] else None,
            "failures": self._health_status["failures"]
        }


# ==========================================================
# WHATSAPP SERVICE (MAIN - ENHANCED)
# ==========================================================

class WhatsAppService:
    """
    WhatsApp Cloud API Communication Layer
    Enterprise Grade v6.3
    """
    
    def __init__(self):
        self.access_token = config.WHATSAPP_ACCESS_TOKEN
        self.phone_number_id = config.WHATSAPP_PHONE_NUMBER_ID
        
        self.api_version = getattr(config, 'WHATSAPP_API_VERSION', DEFAULT_API_VERSION)
        self.base_url = f"https://graph.facebook.com/{self.api_version}/{self.phone_number_id}/messages"
        
        self.session = self._create_session()
        self.circuit_breaker = CircuitBreaker("whatsapp_api")
        self.rate_limiter = RateLimitManager()
        self.dead_letter = DeadLetterQueue()
        self.templates = TemplateManager()
        self.optimizer = MessageOptimizer()
        self.health_monitor = MetaHealthMonitor(self)
        
        # Delivery tracking
        self._delivery_status = TTLCache(maxsize=100000, ttl=86400 * 7)  # 7 days
        self._correlation_tracker = TTLCache(maxsize=100000, ttl=86400)  # 24 hours
        
        # PRIORITY 6: Meta API response history
        self._meta_history = deque(maxlen=META_HISTORY_MAX_SIZE)
        
        # Message queue
        self._message_queue = WhatsAppMessageQueue(self._send_sync, self.dead_letter)
        self._background_started = False
        self._startup_lock = asyncio.Lock()
        
        # PRIORITY 8: Startup self-test
        self._startup_verified = False
        self._startup_error = None
        
        # Mark initialization complete
        logger.info(f"✅ WhatsApp Service v6.3 initialized (API: {self.api_version}, Timeout: {DEFAULT_TIMEOUT}s)")
        logger.info("   Background services NOT started - call start_background_services() to start")
    
    # ==========================================================
    # PRIORITY 8: Startup Self-Test
    # ==========================================================
    
    async def startup_self_test(self) -> Dict[str, Any]:
        """Run startup self-test to verify everything works"""
        logger.info("🔍 Running startup self-test...")
        
        results = {
            "token_configured": bool(self.access_token),
            "phone_number_configured": bool(self.phone_number_id),
            "api_version": self.api_version,
            "meta_api_status": "unknown",
            "worker_status": "unknown",
            "passed": False,
            "errors": []
        }
        
        # Test 1: Token and phone number
        if not results["token_configured"]:
            results["errors"].append("Access token not configured")
        if not results["phone_number_configured"]:
            results["errors"].append("Phone number ID not configured")
        
        if results["errors"]:
            self._startup_error = "; ".join(results["errors"])
            self._startup_verified = False
            logger.error(f"❌ Startup self-test failed: {self._startup_error}")
            return results
        
        # Test 2: Meta API access
        try:
            loop = asyncio.get_running_loop()
            health = await loop.run_in_executor(None, self.health_check, True)
            results["meta_api_status"] = health.get("meta_api_status", "unknown")
            if results["meta_api_status"] != "healthy":
                results["errors"].append(f"Meta API not healthy: {results['meta_api_status']}")
        except Exception as e:
            results["errors"].append(f"Meta API check failed: {str(e)}")
            results["meta_api_status"] = "error"
        
        # Test 3: Worker status
        if self._background_started and self._message_queue._running:
            results["worker_status"] = "running"
        elif self._background_started:
            results["worker_status"] = "stopped"
            results["errors"].append("Worker not running")
        else:
            results["worker_status"] = "not_started"
            # This is not an error - worker can be started later
        
        results["passed"] = len(results["errors"]) == 0
        self._startup_verified = results["passed"]
        
        if results["passed"]:
            logger.info("✅ Startup self-test passed!")
        else:
            logger.warning(f"⚠️ Startup self-test had issues: {results['errors']}")
        
        return results
    
    # ==========================================================
    # BACKGROUND SERVICES
    # ==========================================================
    
    async def start_background_services(self):
        """Start background services (queue worker, health monitor)"""
        if self._background_started:
            logger.warning("Background services already started")
            return
        
        logger.info("🚀 Starting WhatsApp background services...")
        
        if not self._message_queue._running:
            await self._message_queue.start_worker()
        
        if not self.health_monitor._running:
            await self.health_monitor.start_monitoring()
        
        self._background_started = True
        
        # Run startup self-test
        await self.startup_self_test()
        
        logger.info("✅ WhatsApp background services started successfully")
    
    async def stop_background_services(self):
        """Stop background services"""
        logger.info("⏹️ Stopping WhatsApp background services...")
        
        await self._message_queue.stop_worker()
        await self.health_monitor.stop_monitoring()
        
        self._background_started = False
        logger.info("✅ WhatsApp background services stopped")
    
    # PRIORITY 1: Auto-start background services
    async def ensure_started(self):
        """Ensure background services are started (self-healing)"""
        async with self._startup_lock:
            if not self._background_started:
                logger.warning("⚠️ Background services not started - auto-starting...")
                await self.start_background_services()
                logger.info("✅ Background services auto-started")
            elif not self._message_queue._running:
                logger.warning("⚠️ Worker not running - restarting...")
                await self._message_queue.start_worker()
            elif not self.health_monitor._running:
                logger.warning("⚠️ Health monitor not running - restarting...")
                await self.health_monitor.start_monitoring()
            
            # PRIORITY 7: Worker auto-restart via health monitor
            if self._background_started and self._message_queue.is_worker_alive() is False:
                logger.warning("⚠️ Worker appears dead - restarting...")
                await self._message_queue.ensure_worker_running()
    
    def verify_startup(self) -> Dict[str, Any]:
        """Verify all services are properly started"""
        return {
            "service_initialized": True,
            "worker_running": self._message_queue._running,
            "worker_alive": self._message_queue.is_worker_alive(),
            "health_monitor_running": self.health_monitor._running,
            "background_started": self._background_started,
            "startup_verified": self._startup_verified,
            "startup_error": self._startup_error,
            "queue_size": self._message_queue.size(),
            "dead_letter_size": self.dead_letter.get_stats()["size"]
        }
    
    # PRIORITY 4: Queue health monitoring
    def queue_health(self) -> Dict[str, Any]:
        """Get queue health status"""
        return {
            "running": self._message_queue._running,
            "alive": self._message_queue.is_worker_alive(),
            "queue_size": self._message_queue.size(),
            "is_full": self._message_queue.is_full(),
            "background_started": self._background_started,
            "dead_letter": self.dead_letter.get_stats(),
            "analytics": self._message_queue.get_analytics()
        }
    
    # PRIORITY 9: Diagnostic endpoint support
    def diagnostics(self) -> Dict[str, Any]:
        """Get full diagnostics for the service"""
        return {
            "configured": bool(self.access_token and self.phone_number_id),
            "worker_running": self._message_queue._running,
            "worker_alive": self._message_queue.is_worker_alive(),
            "background_started": self._background_started,
            "startup_verified": self._startup_verified,
            "startup_error": self._startup_error,
            "queue_size": self._message_queue.size(),
            "is_full": self._message_queue.is_full(),
            "meta_url": self.base_url,
            "circuit_breaker": self.circuit_breaker.get_state(),
            "dead_letter": self.dead_letter.get_stats(),
            "analytics": self._message_queue.get_analytics(),
            "health_monitor": self.health_monitor.get_status(),
            "meta_history_size": len(self._meta_history)
        }
    
    def _create_session(self) -> requests.Session:
        """Create requests session with retry strategy"""
        session = requests.Session()
        retry_strategy = Retry(
            total=MAX_RETRIES,
            backoff_factor=RETRY_BACKOFF_FACTOR,
            status_forcelist=[429, 500, 502, 503, 504, 520, 524],
            allowed_methods=["GET", "POST"],
            raise_on_status=False
        )
        adapter = HTTPAdapter(
            max_retries=retry_strategy,
            pool_connections=20,
            pool_maxsize=40
        )
        session.mount("https://", adapter)
        session.mount("http://", adapter)
        return session
    
    def _get_headers(self) -> Dict[str, str]:
        return {
            "Authorization": f"Bearer {self.access_token}",
            "Content-Type": "application/json"
        }
    
    def _clean_phone_number(self, phone_number: str) -> str:
        """Clean and format phone number"""
        cleaned = re.sub(r'\D', '', phone_number)
        
        if cleaned.startswith('0'):
            cleaned = '92' + cleaned[1:]
        elif cleaned.startswith('92'):
            pass
        elif len(cleaned) == 10:
            cleaned = '92' + cleaned
        
        return cleaned
    
    def _validate_message(self, message: str) -> bool:
        """Validate message before sending"""
        if not message or not message.strip():
            return False
        return True
    
    def _generate_correlation_id(self, request_id: Optional[str] = None) -> str:
        """Generate correlation ID for tracing"""
        if request_id:
            return f"{request_id}_{int(time.time() * 1000)}"
        return str(uuid.uuid4())[:8]
    
    def _send_sync(self, phone_number: str, message: str, message_id: Optional[str] = None,
                   request_id: Optional[str] = None) -> Dict[str, Any]:
        """Synchronous send method (used by queue)"""
        return self.send_text_message_sync(
            phone_number=phone_number,
            message=message,
            message_id=message_id,
            request_id=request_id
        )
    
    # ==========================================================
    # PUBLIC API
    # ==========================================================
    
    def send_text_message(
        self, 
        phone_number: str, 
        message: str, 
        preview_url: bool = False, 
        message_id: Optional[str] = None,
        request_id: Optional[str] = None,
        priority: MessagePriority = MessagePriority.NORMAL,
        correlation_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Send a text message via WhatsApp Cloud API (with queue support)
        """
        # PRIORITY 2: Direct send fallback
        if not self._message_queue.is_worker_alive():
            logger.warning("⚠️ Worker not alive - using direct send fallback")
            return self.send_text_message_sync(
                phone_number=phone_number,
                message=message,
                preview_url=preview_url,
                message_id=message_id,
                request_id=request_id
            )
        
        # PRIORITY 1: Auto-start if not started
        if not self._background_started:
            logger.warning("⚠️ Background services not started - attempting auto-start")
            # Create task to start services
            import asyncio
            asyncio.create_task(self.ensure_started())
            # Still queue the message - it will be processed when worker starts
            # But we should warn the user
            logger.warning("⚠️ Message queued but worker starting - may experience delay")
        
        # Check rate limits
        can_send, limit_error = self.rate_limiter.can_send(phone_number)
        if not can_send:
            logger.warning(f"Rate limit exceeded for {phone_number}: {limit_error}")
            return {"success": False, "error": limit_error, "rate_limited": True}
        
        # Validate message
        if not self._validate_message(message):
            return {"success": False, "error": "Empty message"}
        
        # Optimize message
        message_chunks = self.optimizer.optimize(message)
        
        # Generate correlation ID
        corr_id = correlation_id or self._generate_correlation_id(request_id)
        
        # Queue first chunk
        queued_message = QueuedMessage(
            id=f"msg_{int(time.time() * 1000)}_{hashlib.md5(phone_number.encode()).hexdigest()[:8]}",
            phone_number=phone_number,
            message=message_chunks[0],
            priority=priority,
            created_at=datetime.now(),
            message_id=message_id,
            request_id=request_id,
            correlation_id=corr_id,
            flow_trace=[f"RECEIVED at {datetime.now().isoformat()}"]
        )
        
        # Queue additional chunks
        for i, chunk in enumerate(message_chunks[1:], 2):
            chunk_message = QueuedMessage(
                id=f"msg_{int(time.time() * 1000)}_{i}_{hashlib.md5(phone_number.encode()).hexdigest()[:8]}",
                phone_number=phone_number,
                message=chunk,
                priority=priority,
                created_at=datetime.now(),
                message_id=message_id,
                request_id=request_id,
                correlation_id=f"{corr_id}_{i}",
                flow_trace=[f"RECEIVED at {datetime.now().isoformat()}"]
            )
            self._message_queue.enqueue(chunk_message)
        
        # Queue first message
        success = self._message_queue.enqueue(queued_message)
        
        if success:
            logger.info(f"📨 Message {queued_message.id} queued for {phone_number} (chunks: {len(message_chunks)}, correlation: {corr_id})")
            return {
                "success": True,
                "queued": True,
                "correlation_id": corr_id,
                "chunks": len(message_chunks),
                "message_id": queued_message.id,
                "status": "queued"
            }
        else:
            logger.critical(f"⚠️ Queue full - message {queued_message.id} rejected")
            return {"success": False, "error": "Queue full"}
    
    def send_text_message_sync(
        self, 
        phone_number: str, 
        message: str, 
        preview_url: bool = False, 
        message_id: Optional[str] = None,
        request_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Synchronous send method (direct to Meta API, no queue)
        """
        req_id = request_id or "unknown"
        
        if not self.access_token or not self.phone_number_id:
            logger.error(f"[{req_id}] WhatsApp service not configured")
            return {"success": False, "error": "WhatsApp service not configured"}
        
        cleaned_number = self._clean_phone_number(phone_number)
        
        # Record for rate limiting
        self.rate_limiter.record_send(cleaned_number)
        
        logger.info(f"[{req_id}] 📤 Sending WhatsApp message to {cleaned_number}")
        logger.info(f"[{req_id}]    Meta URL: {self.base_url}")
        logger.info(f"[{req_id}]    Payload size: {len(message)} chars")
        logger.info(f"[{req_id}]    Request ID: {req_id}")
        
        try:
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
            
            if message_id:
                payload["context"] = {"message_id": message_id}
            
            logger.debug(f"[{req_id}]    Payload: {json.dumps(payload, ensure_ascii=False)[:500]}...")
            
            def _send():
                return self.session.post(
                    self.base_url, 
                    headers=self._get_headers(), 
                    json=payload, 
                    timeout=DEFAULT_TIMEOUT
                )
            
            logger.info(f"[{req_id}] 📡 Calling Meta API...")
            response = self.circuit_breaker.call(_send)
            
            logger.info(f"[{req_id}]    Meta Status Code: {response.status_code}")
            
            try:
                result = response.json()
                logger.info(f"[{req_id}]    Meta Response: {json.dumps(result, ensure_ascii=False)[:1000]}")
            except Exception as json_err:
                result = {"error": {"message": f"Invalid JSON: {response.text[:200]}"}}
                logger.error(f"[{req_id}]    Meta Response (invalid JSON): {response.text[:200]}")
            
            # PRIORITY 6: Store response history
            self._meta_history.append({
                "timestamp": datetime.now().isoformat(),
                "phone": cleaned_number,
                "status_code": response.status_code,
                "success": response.status_code in [200, 201],
                "response": result
            })
            
            if response.status_code in [200, 201]:
                response_message_id = result.get("messages", [{}])[0].get("id")
                
                logger.info(f"[{req_id}] ✅ Message sent successfully to Meta")
                if response_message_id:
                    logger.info(f"[{req_id}]    Meta Message ID: {response_message_id}")
                else:
                    logger.warning(f"[{req_id}]    No message ID in response")
                
                # Track delivery
                if response_message_id:
                    self._delivery_status[response_message_id] = DeliveryStatus(
                        message_id=response_message_id,
                        status=MessageStatus.SENT,
                        updated_at=datetime.now(),
                        sent_at=datetime.now()  # PRIORITY 10
                    )
                    
                    if request_id:
                        self._correlation_tracker[request_id] = {
                            "message_id": response_message_id,
                            "timestamp": datetime.now().isoformat()
                        }
                
                return {
                    "success": True, 
                    "status_code": response.status_code, 
                    "message_id": response_message_id
                }
            
            error_msg = result.get("error", {}).get("message", f"HTTP {response.status_code}")
            logger.error(f"[{req_id}] ❌ Meta API Error: {response.status_code} - {error_msg}")
            
            return {
                "success": False, 
                "status_code": response.status_code, 
                "error": error_msg
            }
            
        except requests.Timeout:
            logger.error(f"[{req_id}] ❌ Request timeout after {DEFAULT_TIMEOUT}s")
            return {"success": False, "error": f"Timeout after {DEFAULT_TIMEOUT}s"}
        
        except Exception as e:
            logger.exception(f"[{req_id}] ❌ Send failed: {e}")
            return {"success": False, "error": str(e)}
    
    def send_help_message(self, phone_number: str, request_id: Optional[str] = None) -> Dict[str, Any]:
        """Send help message"""
        return self.send_text_message(phone_number, self.templates.get("help"), request_id=request_id)
    
    def send_welcome_message(self, phone_number: str, request_id: Optional[str] = None) -> Dict[str, Any]:
        """Send welcome message"""
        return self.send_text_message(phone_number, self.templates.get("welcome"), request_id=request_id)
    
    def send_bulk_messages(self, phone_numbers: List[str], message: str, 
                           priority: MessagePriority = MessagePriority.LOW) -> List[Dict]:
        """Send same message to multiple recipients"""
        results = []
        for phone in phone_numbers:
            result = self.send_text_message(phone, message, priority=priority)
            results.append({"phone": phone, "result": result})
        return results
    
    def update_delivery_status(self, message_id: str, status: str, meta_status: Optional[str] = None):
        """Update delivery status from webhook"""
        now = datetime.now()
        
        if message_id in self._delivery_status:
            delivery = self._delivery_status[message_id]
            old_status = delivery.status
            delivery.status = MessageStatus(status)
            delivery.meta_status = meta_status
            delivery.updated_at = now
            
            # PRIORITY 10: Update timestamps
            if status == "delivered" and not delivery.delivered_at:
                delivery.delivered_at = now
            if status == "read" and not delivery.read_at:
                delivery.read_at = now
            
            self._delivery_status[message_id] = delivery
            
            # Calculate delivery metrics
            if delivery.sent_at and delivery.delivered_at:
                delivery_time = (delivery.delivered_at - delivery.sent_at).total_seconds() * 1000
                if self._message_queue._analytics.avg_delivery_time_ms == 0:
                    self._message_queue._analytics.avg_delivery_time_ms = delivery_time
                else:
                    avg = self._message_queue._analytics.avg_delivery_time_ms
                    total = self._message_queue._analytics.total_delivered
                    self._message_queue._analytics.avg_delivery_time_ms = (avg * total + delivery_time) / (total + 1)
            
            if delivery.delivered_at and delivery.read_at:
                read_time = (delivery.read_at - delivery.delivered_at).total_seconds() * 1000
                if self._message_queue._analytics.avg_read_time_ms == 0:
                    self._message_queue._analytics.avg_read_time_ms = read_time
                else:
                    avg = self._message_queue._analytics.avg_read_time_ms
                    total = self._message_queue._analytics.total_read
                    self._message_queue._analytics.avg_read_time_ms = (avg * total + read_time) / (total + 1)
        else:
            self._delivery_status[message_id] = DeliveryStatus(
                message_id=message_id,
                status=MessageStatus(status),
                updated_at=now,
                meta_status=meta_status,
                sent_at=now if status == "sent" else None,
                delivered_at=now if status == "delivered" else None,
                read_at=now if status == "read" else None
            )
    
    def get_delivery_status(self, message_id: str) -> Optional[Dict]:
        """Get delivery status of a message"""
        if message_id in self._delivery_status:
            delivery = self._delivery_status[message_id]
            return {
                "status": delivery.status.value,
                "updated_at": delivery.updated_at.isoformat(),
                "meta_status": delivery.meta_status,
                "error": delivery.error,
                "sent_at": delivery.sent_at.isoformat() if delivery.sent_at else None,
                "delivered_at": delivery.delivered_at.isoformat() if delivery.delivered_at else None,
                "read_at": delivery.read_at.isoformat() if delivery.read_at else None
            }
        return None
    
    def get_message_by_correlation(self, correlation_id: str) -> Optional[Dict]:
        """Get message by correlation ID"""
        return self._correlation_tracker.get(correlation_id)
    
    # PRIORITY 4: Fixed get_message_trace()
    def get_message_trace(self, correlation_id: str) -> Optional[Dict[str, Any]]:
        """Get the full flow trace for a message by correlation ID"""
        # Search in dead letter queue
        for item in self.dead_letter.get_all():
            msg = item.get("message")
            if msg and hasattr(msg, 'correlation_id') and msg.correlation_id == correlation_id:
                return {
                    "found": True,
                    "location": "dead_letter",
                    "trace": getattr(msg, 'flow_trace', []),
                    "error": item.get("error"),
                    "message_id": getattr(msg, 'id', None),
                    "phone": getattr(msg, 'phone_number', None)
                }
        
        # Search in delivery status
        for msg_id, status in self._delivery_status.items():
            if msg_id == correlation_id:
                return {
                    "found": True,
                    "location": "delivery_status",
                    "status": status.status.value,
                    "updated_at": status.updated_at.isoformat(),
                    "sent_at": status.sent_at.isoformat() if status.sent_at else None,
                    "delivered_at": status.delivered_at.isoformat() if status.delivered_at else None,
                    "read_at": status.read_at.isoformat() if status.read_at else None
                }
        
        # Search in correlation tracker
        if correlation_id in self._correlation_tracker:
            return {
                "found": True,
                "location": "correlation_tracker",
                "data": self._correlation_tracker[correlation_id]
            }
        
        return {"found": False, "message": f"Correlation ID '{correlation_id}' not found"}
    
    def health_check(self, verify_meta: bool = False) -> Dict[str, Any]:
        """Enhanced health check with token monitoring"""
        result = {
            "service": "whatsapp",
            "version": "6.3",
            "configured": bool(self.access_token and self.phone_number_id),
            "api_version": self.api_version,
            "circuit_breaker": self.circuit_breaker.get_state(),
            "rate_limiter": self.rate_limiter.get_stats(),
            "queue_size": self._message_queue.size(),
            "worker_alive": self._message_queue.is_worker_alive(),
            "dead_letter_size": self.dead_letter.get_stats()["size"],
            "delivery_tracking_size": len(self._delivery_status),
            "correlation_tracking_size": len(self._correlation_tracker),
            "background_started": self._background_started,
            "startup_verified": self._startup_verified,
            "timestamp": datetime.now().isoformat()
        }
        
        if hasattr(config, 'WHATSAPP_TOKEN_EXPIRY'):
            days_remaining = (config.WHATSAPP_TOKEN_EXPIRY - datetime.now()).days
            result["token_days_remaining"] = days_remaining
            if days_remaining < TOKEN_EXPIRY_WARNING_DAYS:
                result["token_warning"] = f"Token expires in {days_remaining} days"
        
        if verify_meta and result["configured"]:
            try:
                test_url = f"https://graph.facebook.com/{self.api_version}/{self.phone_number_id}"
                response = self.session.get(test_url, headers=self._get_headers(), timeout=10)
                
                if response.status_code == 200:
                    result["meta_api_status"] = "healthy"
                    result["meta_api_verified"] = True
                    logger.info("✅ Meta API health check passed")
                elif response.status_code == 401:
                    result["meta_api_status"] = "token_expired"
                    result["meta_api_error"] = "Token expired or invalid"
                    logger.error("❌ Meta API token expired or invalid")
                else:
                    result["meta_api_status"] = "unhealthy"
                    result["meta_api_error"] = f"HTTP {response.status_code}"
                    logger.warning(f"⚠️ Meta API health check failed: HTTP {response.status_code}")
            except Exception as e:
                result["meta_api_status"] = "error"
                result["meta_api_error"] = str(e)
                logger.error(f"❌ Meta API health check error: {e}")
        
        return result
    
    def get_metrics(self) -> Dict[str, Any]:
        """Get service metrics"""
        analytics = self._message_queue.get_analytics()
        
        return {
            "analytics": analytics,
            "rate_limiter": self.rate_limiter.get_stats(),
            "circuit_breaker": self.circuit_breaker.get_state(),
            "dead_letter": self.dead_letter.get_stats(),
            "delivery_tracking_size": len(self._delivery_status),
            "correlation_tracking_size": len(self._correlation_tracker),
            "queue_size": self._message_queue.size(),
            "background_started": self._background_started,
            "worker_alive": self._message_queue.is_worker_alive(),
            "meta_history_size": len(self._meta_history)
        }
    
    def clear_cache(self) -> Dict[str, Any]:
        """Clear tracking caches"""
        old_delivery = len(self._delivery_status)
        old_correlation = len(self._correlation_tracker)
        
        self._delivery_status.clear()
        self._correlation_tracker.clear()
        dead_letter_cleared = self.dead_letter.clear()
        
        return {
            "cleared_delivery_status": old_delivery,
            "cleared_correlation_tracking": old_correlation,
            "cleared_dead_letter": dead_letter_cleared
        }
    
    def retry_dead_letter(self, key: str) -> Dict[str, Any]:
        """Retry a dead letter message"""
        message = self.dead_letter.retry(key)
        if message:
            success = self._message_queue.enqueue(message)
            return {"success": success, "message_id": message.id}
        return {"success": False, "error": "Message not found"}
    
    # ==========================================================
    # DIAGNOSTIC METHODS
    # ==========================================================
    
    def test_meta_send(self) -> Dict[str, Any]:
        """Diagnostic: Test Meta API configuration"""
        return {
            "configured": bool(self.access_token and self.phone_number_id),
            "base_url": self.base_url,
            "api_version": self.api_version,
            "queue_running": self._message_queue._running,
            "worker_alive": self._message_queue.is_worker_alive(),
            "queue_size": self._message_queue.size(),
            "circuit_state": self.circuit_breaker.state.value,
            "background_started": self._background_started,
            "startup_verified": self._startup_verified
        }
    
    def get_worker_status(self) -> Dict[str, Any]:
        """Diagnostic: Get worker health status"""
        return {
            "worker_running": self._message_queue._running,
            "worker_alive": self._message_queue.is_worker_alive(),
            "queue_size": self._message_queue.size(),
            "is_full": self._message_queue.is_full(),
            "dead_letter": self.dead_letter.get_stats(),
            "analytics": self._message_queue.get_analytics(),
            "health_monitor": self.health_monitor.get_status()
        }
    
    # PRIORITY 6: Get meta history
    def get_meta_history(self) -> List[Dict]:
        """Get Meta API response history"""
        return list(self._meta_history)


# ==========================================================
# SINGLETON & COMPATIBILITY FUNCTIONS
# ==========================================================

_whatsapp_service = None
_dead_letter_queue = None


def get_whatsapp_service() -> WhatsAppService:
    """Get or create WhatsApp service singleton"""
    global _whatsapp_service
    if _whatsapp_service is None:
        _whatsapp_service = WhatsAppService()
    return _whatsapp_service


def send_text_message(
    phone_number: str, 
    message: str, 
    message_id: Optional[str] = None,
    request_id: Optional[str] = None
) -> Dict[str, Any]:
    """
    Compatibility function for webhook.py
    """
    service = get_whatsapp_service()
    return service.send_text_message(
        phone_number=phone_number, 
        message=message, 
        message_id=message_id,
        request_id=request_id
    )


def send_help_message(phone_number: str, request_id: Optional[str] = None) -> Dict[str, Any]:
    """Send help message"""
    service = get_whatsapp_service()
    return service.send_help_message(phone_number, request_id=request_id)


def send_welcome_message(phone_number: str, request_id: Optional[str] = None) -> Dict[str, Any]:
    """Send welcome message"""
    service = get_whatsapp_service()
    return service.send_welcome_message(phone_number, request_id=request_id)


def send_bulk_messages(phone_numbers: List[str], message: str) -> List[Dict]:
    """Send bulk messages"""
    service = get_whatsapp_service()
    return service.send_bulk_messages(phone_numbers, message)


def get_whatsapp_metrics() -> Dict[str, Any]:
    """Get WhatsApp service metrics"""
    service = get_whatsapp_service()
    return service.get_metrics()


def clear_whatsapp_cache() -> Dict[str, Any]:
    """Clear WhatsApp service caches"""
    service = get_whatsapp_service()
    return service.clear_cache()


def retry_dead_letter(key: str) -> Dict[str, Any]:
    """Retry a dead letter message"""
    service = get_whatsapp_service()
    return service.retry_dead_letter(key)


def get_dead_letter_stats() -> Dict[str, int]:
    """Get dead letter queue statistics"""
    service = get_whatsapp_service()
    return service.dead_letter.get_stats()


def get_whatsapp_diagnostics() -> Dict[str, Any]:
    """Get full WhatsApp service diagnostics"""
    service = get_whatsapp_service()
    return service.diagnostics()


def verify_whatsapp_startup() -> Dict[str, Any]:
    """Verify WhatsApp service startup status"""
    service = get_whatsapp_service()
    return service.verify_startup()


def get_message_trace(correlation_id: str) -> Optional[Dict[str, Any]]:
    """Get message flow trace by correlation ID"""
    service = get_whatsapp_service()
    return service.get_message_trace(correlation_id)


def get_meta_history() -> List[Dict]:
    """Get Meta API response history"""
    service = get_whatsapp_service()
    return service.get_meta_history()


# ==========================================================
# INITIALIZATION LOG
# ==========================================================

logger.info("=" * 60)
logger.info("📱 WhatsApp Service v6.3 - Module Loaded")
logger.info("   Features:")
logger.info("   ✅ Auto-start background services")
logger.info("   ✅ Direct send fallback")
logger.info("   ✅ Queue overflow protection")
logger.info("   ✅ Worker heartbeat monitoring")
logger.info("   ✅ Meta API response history")
logger.info("   ✅ Worker auto-restart")
logger.info("   ✅ Startup self-test")
logger.info("   ✅ Diagnostic endpoint support")
logger.info("   ✅ Delivery tracking metrics")
logger.info("=" * 60)
