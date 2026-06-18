# ==========================================================
# FILE: tests/test_webhook_diagnostic.py
# ==========================================================
# PURPOSE: Complete Diagnostic Test Suite for webhook.py
# VERSION: 1.0 - Production Diagnostic Tool
#
# This test suite diagnoses:
# 1. Webhook Verification (GET /webhook)
# 2. Webhook Receiving (POST /webhook)
# 3. 422 Error Detection
# 4. Pydantic Validation Issues
# 5. Payload Parsing Issues
# 6. Response Times
# 7. Error Handling
# ==========================================================

import json
import hmac
import hashlib
import time
import uuid
from datetime import datetime
from typing import Dict, Any, Optional
import httpx
import asyncio
from dataclasses import dataclass, field
from enum import Enum

# ==========================================================
# TEST CONFIGURATION
# ==========================================================

class TestSeverity(Enum):
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"
    CRITICAL = "CRITICAL"
    SUCCESS = "SUCCESS"

@dataclass
class TestResult:
    name: str
    status: bool
    severity: TestSeverity
    message: str
    details: Dict[str, Any] = field(default_factory=dict)
    duration_ms: float = 0.0

@dataclass
class TestSuite:
    name: str
    results: list = field(default_factory=list)
    total_tests: int = 0
    passed: int = 0
    failed: int = 0
    errors: int = 0
    start_time: float = field(default_factory=time.time)
    
    def add_result(self, result: TestResult):
        self.results.append(result)
        self.total_tests += 1
        if result.status:
            self.passed += 1
        else:
            self.failed += 1
            if result.severity == TestSeverity.ERROR or result.severity == TestSeverity.CRITICAL:
                self.errors += 1
    
    def print_summary(self):
        duration = (time.time() - self.start_time) * 1000
        print("\n" + "=" * 70)
        print(f"📊 TEST SUMMARY: {self.name}")
        print("=" * 70)
        print(f"  ✅ Passed: {self.passed}")
        print(f"  ❌ Failed: {self.failed}")
        print(f"  ⚠️  Errors: {self.errors}")
        print(f"  📝 Total: {self.total_tests}")
        print(f"  ⏱️  Duration: {duration:.2f}ms")
        print("=" * 70)
        
        if self.failed > 0 or self.errors > 0:
            print("\n❌ FAILED TESTS:")
            for r in self.results:
                if not r.status:
                    print(f"  [{r.severity.value}] {r.name}")
                    print(f"      {r.message}")
                    if r.details:
                        print(f"      Details: {r.details}")
            print("=" * 70)
        else:
            print("\n✅ ALL TESTS PASSED! Webhook is working correctly.")
            print("=" * 70)
        
        print(f"\n📋 DETAILED RESULTS:")
        for r in self.results:
            status_icon = "✅" if r.status else "❌"
            print(f"  {status_icon} [{r.severity.value}] {r.name} - {r.duration_ms:.2f}ms")
            if r.message:
                print(f"      {r.message}")


# ==========================================================
# WEBHOOK DIAGNOSTIC TESTER
# ==========================================================

class WebhookDiagnosticTester:
    """
    Complete diagnostic test suite for webhook.py.
    Tests all endpoints and identifies issues.
    """
    
    def __init__(self, base_url: str = "http://localhost:8000", verify_token: str = "test_token"):
        self.base_url = base_url
        self.verify_token = verify_token
        self.webhook_url = f"{base_url}/webhook"
        self.health_url = f"{base_url}/webhook/health"
        self.ping_url = f"{base_url}/webhook/ping"
        self.client = httpx.Client(timeout=30.0)
        self.test_suite = TestSuite("Webhook Diagnostic Suite")
    
    # ==========================================================
    # TEST 1: Ping Endpoint
    # ==========================================================
    
    def test_ping(self) -> TestResult:
        """Test if webhook is alive."""
        start = time.time()
        try:
            response = self.client.get(self.ping_url)
            duration = (time.time() - start) * 1000
            
            if response.status_code == 200:
                data = response.json()
                return TestResult(
                    name="Ping Endpoint",
                    status=True,
                    severity=TestSeverity.SUCCESS,
                    message="Webhook is alive and responding",
                    details={"status_code": response.status_code, "response": data},
                    duration_ms=duration
                )
            else:
                return TestResult(
                    name="Ping Endpoint",
                    status=False,
                    severity=TestSeverity.ERROR,
                    message=f"Ping failed with status {response.status_code}",
                    details={"status_code": response.status_code, "response": response.text},
                    duration_ms=duration
                )
        except Exception as e:
            return TestResult(
                name="Ping Endpoint",
                status=False,
                severity=TestSeverity.CRITICAL,
                message=f"Ping error: {str(e)}",
                details={"error": str(e)},
                duration_ms=(time.time() - start) * 1000
            )
    
    # ==========================================================
    # TEST 2: Health Endpoint
    # ==========================================================
    
    def test_health(self) -> TestResult:
        """Test health endpoint for service status."""
        start = time.time()
        try:
            response = self.client.get(self.health_url)
            duration = (time.time() - start) * 1000
            
            if response.status_code == 200:
                data = response.json()
                status = data.get("status", "unknown")
                
                # Check if all services are healthy
                components = data.get("components", {})
                webhook_healthy = components.get("webhook") == "healthy"
                database_healthy = components.get("database") == "healthy"
                ai_healthy = components.get("ai_provider") == "available"
                
                all_healthy = webhook_healthy and database_healthy and ai_healthy
                
                return TestResult(
                    name="Health Endpoint",
                    status=all_healthy,
                    severity=TestSeverity.SUCCESS if all_healthy else TestSeverity.WARNING,
                    message=f"Health status: {status}",
                    details={
                        "status": status,
                        "components": components,
                        "webhook_healthy": webhook_healthy,
                        "database_healthy": database_healthy,
                        "ai_healthy": ai_healthy
                    },
                    duration_ms=duration
                )
            else:
                return TestResult(
                    name="Health Endpoint",
                    status=False,
                    severity=TestSeverity.ERROR,
                    message=f"Health check failed with status {response.status_code}",
                    details={"status_code": response.status_code},
                    duration_ms=duration
                )
        except Exception as e:
            return TestResult(
                name="Health Endpoint",
                status=False,
                severity=TestSeverity.CRITICAL,
                message=f"Health check error: {str(e)}",
                details={"error": str(e)},
                duration_ms=(time.time() - start) * 1000
            )
    
    # ==========================================================
    # TEST 3: Webhook Verification (GET)
    # ==========================================================
    
    def test_verification(self) -> TestResult:
        """Test webhook verification endpoint."""
        start = time.time()
        challenge = "test_challenge_12345"
        
        try:
            response = self.client.get(
                self.webhook_url,
                params={
                    "hub.mode": "subscribe",
                    "hub.verify_token": self.verify_token,
                    "hub.challenge": challenge
                }
            )
            duration = (time.time() - start) * 1000
            
            if response.status_code == 200:
                # Check if response matches challenge
                if response.text == challenge:
                    return TestResult(
                        name="Webhook Verification",
                        status=True,
                        severity=TestSeverity.SUCCESS,
                        message="Verification successful - Meta challenge matched",
                        details={"challenge": challenge, "response": response.text},
                        duration_ms=duration
                    )
                else:
                    return TestResult(
                        name="Webhook Verification",
                        status=False,
                        severity=TestSeverity.ERROR,
                        message=f"Challenge mismatch: expected '{challenge}', got '{response.text}'",
                        details={"expected": challenge, "got": response.text},
                        duration_ms=duration
                    )
            else:
                return TestResult(
                    name="Webhook Verification",
                    status=False,
                    severity=TestSeverity.ERROR,
                    message=f"Verification failed with status {response.status_code}",
                    details={"status_code": response.status_code, "response": response.text},
                    duration_ms=duration
                )
        except Exception as e:
            return TestResult(
                name="Webhook Verification",
                status=False,
                severity=TestSeverity.CRITICAL,
                message=f"Verification error: {str(e)}",
                details={"error": str(e)},
                duration_ms=(time.time() - start) * 1000
            )
    
    # ==========================================================
    # TEST 4: Webhook Receiving (POST) - Valid Payload
    # ==========================================================
    
    def test_valid_payload(self) -> TestResult:
        """Test webhook with valid WhatsApp payload."""
        start = time.time()
        
        # Create valid WhatsApp webhook payload
        payload = {
            "object": "whatsapp_business_account",
            "entry": [{
                "id": "123456789",
                "changes": [{
                    "value": {
                        "messaging_product": "whatsapp",
                        "metadata": {
                            "display_phone_number": "1234567890",
                            "phone_number_id": "1234567890"
                        },
                        "contacts": [{
                            "profile": {"name": "Test User"},
                            "wa_id": "923001234567"
                        }],
                        "messages": [{
                            "from": "923001234567",
                            "id": f"wamid.test_{uuid.uuid4().hex[:8]}",
                            "timestamp": str(int(time.time())),
                            "text": {"body": "Hello, this is a test message"},
                            "type": "text"
                        }]
                    }
                }]
            }]
        }
        
        try:
            response = self.client.post(
                self.webhook_url,
                json=payload,
                headers={"Content-Type": "application/json"}
            )
            duration = (time.time() - start) * 1000
            
            # Webhook should ALWAYS return 200 OK
            if response.status_code == 200:
                data = response.json()
                if data.get("status") == "ok":
                    return TestResult(
                        name="Valid Payload - 200 OK",
                        status=True,
                        severity=TestSeverity.SUCCESS,
                        message=f"Webhook accepted payload with 200 OK in {duration:.2f}ms",
                        details={"status_code": response.status_code, "response": data},
                        duration_ms=duration
                    )
                else:
                    return TestResult(
                        name="Valid Payload - 200 OK",
                        status=False,
                        severity=TestSeverity.ERROR,
                        message=f"Webhook returned 200 but status not 'ok'",
                        details={"status_code": response.status_code, "response": data},
                        duration_ms=duration
                    )
            else:
                return TestResult(
                    name="Valid Payload - 200 OK",
                    status=False,
                    severity=TestSeverity.CRITICAL,
                    message=f"Webhook returned {response.status_code} instead of 200 OK!",
                    details={
                        "status_code": response.status_code,
                        "response": response.text[:500],
                        "response_headers": dict(response.headers)
                    },
                    duration_ms=duration
                )
        except Exception as e:
            return TestResult(
                name="Valid Payload - 200 OK",
                status=False,
                severity=TestSeverity.CRITICAL,
                message=f"Request error: {str(e)}",
                details={"error": str(e)},
                duration_ms=(time.time() - start) * 1000
            )
    
    # ==========================================================
    # TEST 5: Webhook Receiving - Malformed JSON
    # ==========================================================
    
    def test_malformed_json(self) -> TestResult:
        """Test webhook with malformed JSON payload."""
        start = time.time()
        
        malformed_json = '{"object": "whatsapp_business_account", "entry": [{"changes": [{"value": {"messages": [{"from": "923001234567", "id": "test", "text": {"body": "test"}}]}}]}]'  # Missing closing brace
        
        try:
            response = self.client.post(
                self.webhook_url,
                content=malformed_json,
                headers={"Content-Type": "application/json"}
            )
            duration = (time.time() - start) * 1000
            
            # Webhook should ALWAYS return 200 OK even with malformed JSON
            if response.status_code == 200:
                return TestResult(
                    name="Malformed JSON - Graceful Handling",
                    status=True,
                    severity=TestSeverity.SUCCESS,
                    message=f"Webhook gracefully handled malformed JSON with 200 OK",
                    details={"status_code": response.status_code},
                    duration_ms=duration
                )
            else:
                return TestResult(
                    name="Malformed JSON - Graceful Handling",
                    status=False,
                    severity=TestSeverity.ERROR,
                    message=f"Webhook returned {response.status_code} instead of 200 OK",
                    details={"status_code": response.status_code, "response": response.text[:200]},
                    duration_ms=duration
                )
        except Exception as e:
            return TestResult(
                name="Malformed JSON - Graceful Handling",
                status=False,
                severity=TestSeverity.CRITICAL,
                message=f"Request error: {str(e)}",
                details={"error": str(e)},
                duration_ms=(time.time() - start) * 1000
            )
    
    # ==========================================================
    # TEST 6: Webhook Receiving - Empty Payload
    # ==========================================================
    
    def test_empty_payload(self) -> TestResult:
        """Test webhook with empty payload."""
        start = time.time()
        
        try:
            response = self.client.post(
                self.webhook_url,
                json={},
                headers={"Content-Type": "application/json"}
            )
            duration = (time.time() - start) * 1000
            
            if response.status_code == 200:
                return TestResult(
                    name="Empty Payload - Graceful Handling",
                    status=True,
                    severity=TestSeverity.SUCCESS,
                    message=f"Webhook gracefully handled empty payload with 200 OK",
                    details={"status_code": response.status_code},
                    duration_ms=duration
                )
            else:
                return TestResult(
                    name="Empty Payload - Graceful Handling",
                    status=False,
                    severity=TestSeverity.ERROR,
                    message=f"Webhook returned {response.status_code} instead of 200 OK",
                    details={"status_code": response.status_code},
                    duration_ms=duration
                )
        except Exception as e:
            return TestResult(
                name="Empty Payload - Graceful Handling",
                status=False,
                severity=TestSeverity.CRITICAL,
                message=f"Request error: {str(e)}",
                details={"error": str(e)},
                duration_ms=(time.time() - start) * 1000
            )
    
    # ==========================================================
    # TEST 7: Signature Verification
    # ==========================================================
    
    def test_signature_verification(self) -> TestResult:
        """Test signature verification (if enabled)."""
        start = time.time()
        
        # Create payload
        payload = {"object": "whatsapp_business_account", "entry": []}
        payload_json = json.dumps(payload)
        
        # Generate signature (using test secret)
        secret = "test_secret"
        expected_signature = hmac.new(
            secret.encode('utf-8'),
            payload_json.encode('utf-8'),
            hashlib.sha256
        ).hexdigest()
        
        try:
            response = self.client.post(
                self.webhook_url,
                json=payload,
                headers={
                    "Content-Type": "application/json",
                    "X-Hub-Signature-256": f"sha256={expected_signature}"
                }
            )
            duration = (time.time() - start) * 1000
            
            # If signature required, this might fail - but should still return 200
            if response.status_code == 200:
                return TestResult(
                    name="Signature Verification",
                    status=True,
                    severity=TestSeverity.INFO,
                    message=f"Signature verification processed (may be disabled)",
                    details={"status_code": response.status_code},
                    duration_ms=duration
                )
            else:
                return TestResult(
                    name="Signature Verification",
                    status=False,
                    severity=TestSeverity.ERROR,
                    message=f"Signature verification failed with {response.status_code}",
                    details={"status_code": response.status_code},
                    duration_ms=duration
                )
        except Exception as e:
            return TestResult(
                name="Signature Verification",
                status=False,
                severity=TestSeverity.CRITICAL,
                message=f"Signature test error: {str(e)}",
                details={"error": str(e)},
                duration_ms=(time.time() - start) * 1000
            )
    
    # ==========================================================
    # TEST 8: Performance - Response Time
    # ==========================================================
    
    def test_performance(self) -> TestResult:
        """Test webhook response time."""
        start = time.time()
        
        payload = {
            "object": "whatsapp_business_account",
            "entry": [{
                "changes": [{
                    "value": {
                        "messages": [{
                            "from": "923001234567",
                            "id": f"wamid.test_{uuid.uuid4().hex[:8]}",
                            "text": {"body": "test"},
                            "type": "text"
                        }]
                    }
                }]
            }]
        }
        
        try:
            response = self.client.post(self.webhook_url, json=payload)
            duration = (time.time() - start) * 1000
            
            # Webhook should respond in < 100ms
            if duration < 100:
                status = True
                severity = TestSeverity.SUCCESS
                message = f"Excellent performance: {duration:.2f}ms (< 100ms)"
            elif duration < 500:
                status = True
                severity = TestSeverity.INFO
                message = f"Acceptable performance: {duration:.2f}ms (< 500ms)"
            else:
                status = False
                severity = TestSeverity.WARNING
                message = f"Slow response: {duration:.2f}ms (> 500ms)"
            
            return TestResult(
                name="Performance - Response Time",
                status=status,
                severity=severity,
                message=message,
                details={
                    "duration_ms": duration,
                    "target_ms": 100,
                    "max_ms": 500
                },
                duration_ms=duration
            )
        except Exception as e:
            return TestResult(
                name="Performance - Response Time",
                status=False,
                severity=TestSeverity.CRITICAL,
                message=f"Performance test error: {str(e)}",
                details={"error": str(e)},
                duration_ms=(time.time() - start) * 1000
            )
    
    # ==========================================================
    # TEST 9: Code Analysis - Pydantic Detection
    # ==========================================================
    
    def test_pydantic_detection(self) -> TestResult:
        """Check if webhook.py contains Pydantic (would cause 422)."""
        start = time.time()
        
        try:
            # Read webhook.py file
            import os
            import re
            
            webhook_path = "app/routes/webhook.py"
            if not os.path.exists(webhook_path):
                return TestResult(
                    name="Pydantic Detection",
                    status=False,
                    severity=TestSeverity.WARNING,
                    message=f"webhook.py not found at {webhook_path}",
                    details={"path": webhook_path},
                    duration_ms=(time.time() - start) * 1000
                )
            
            with open(webhook_path, 'r') as f:
                content = f.read()
            
            # Check for Pydantic imports
            pydantic_imports = re.findall(r'from pydantic import|import pydantic', content)
            base_model = re.findall(r'BaseModel', content)
            webhook_payload = re.findall(r'WebhookPayload', content)
            
            has_pydantic = bool(pydantic_imports) or bool(base_model) or bool(webhook_payload)
            
            if has_pydantic:
                return TestResult(
                    name="Pydantic Detection",
                    status=False,
                    severity=TestSeverity.CRITICAL,
                    message="❌ Pydantic found in webhook.py - This WILL cause 422 errors!",
                    details={
                        "pydantic_imports": pydantic_imports,
                        "base_model_found": bool(base_model),
                        "webhook_payload_found": bool(webhook_payload)
                    },
                    duration_ms=(time.time() - start) * 1000
                )
            else:
                return TestResult(
                    name="Pydantic Detection",
                    status=True,
                    severity=TestSeverity.SUCCESS,
                    message="✅ No Pydantic found in webhook.py - 422 error should be fixed",
                    duration_ms=(time.time() - start) * 1000
                )
        except Exception as e:
            return TestResult(
                name="Pydantic Detection",
                status=False,
                severity=TestSeverity.ERROR,
                message=f"Code analysis error: {str(e)}",
                details={"error": str(e)},
                duration_ms=(time.time() - start) * 1000
            )
    
    # ==========================================================
    # TEST 10: Handler Signature Check
    # ==========================================================
    
    def test_handler_signature(self) -> TestResult:
        """Check if webhook handler uses correct signature (Request object)."""
        start = time.time()
        
        try:
            import os
            import re
            
            webhook_path = "app/routes/webhook.py"
            if not os.path.exists(webhook_path):
                return TestResult(
                    name="Handler Signature",
                    status=False,
                    severity=TestSeverity.WARNING,
                    message=f"webhook.py not found at {webhook_path}",
                    duration_ms=(time.time() - start) * 1000
                )
            
            with open(webhook_path, 'r') as f:
                content = f.read()
            
            # Find handler definition
            handler_pattern = r'async def handle_webhook\(([^)]+)\)'
            match = re.search(handler_pattern, content)
            
            if not match:
                return TestResult(
                    name="Handler Signature",
                    status=False,
                    severity=TestSeverity.ERROR,
                    message="❌ handler_webhook function not found!",
                    duration_ms=(time.time() - start) * 1000
                )
            
            signature = match.group(1)
            
            # Check if using Request object
            if 'request: Request' in signature and 'background_tasks: BackgroundTasks' in signature:
                return TestResult(
                    name="Handler Signature",
                    status=True,
                    severity=TestSeverity.SUCCESS,
                    message="✅ Handler uses correct signature: Request + BackgroundTasks",
                    details={"signature": signature},
                    duration_ms=(time.time() - start) * 1000
                )
            elif 'request: Request' in signature:
                return TestResult(
                    name="Handler Signature",
                    status=True,
                    severity=TestSeverity.INFO,
                    message="Handler uses Request but missing BackgroundTasks",
                    details={"signature": signature},
                    duration_ms=(time.time() - start) * 1000
                )
            else:
                return TestResult(
                    name="Handler Signature",
                    status=False,
                    severity=TestSeverity.CRITICAL,
                    message=f"❌ Handler using: {signature} - Should use Request object!",
                    details={"signature": signature},
                    duration_ms=(time.time() - start) * 1000
                )
        except Exception as e:
            return TestResult(
                name="Handler Signature",
                status=False,
                severity=TestSeverity.ERROR,
                message=f"Code analysis error: {str(e)}",
                details={"error": str(e)},
                duration_ms=(time.time() - start) * 1000
            )
    
    # ==========================================================
    # RUN ALL TESTS
    # ==========================================================
    
    def run_all_tests(self):
        """Run all diagnostic tests."""
        print("\n" + "=" * 70)
        print("🔧 WEBHOOK DIAGNOSTIC TEST SUITE")
        print("=" * 70)
        print(f"  Base URL: {self.base_url}")
        print(f"  Verify Token: {self.verify_token}")
        print(f"  Time: {datetime.now().isoformat()}")
        print("=" * 70)
        print("\n🔄 Running tests...\n")
        
        # Run all tests
        self.test_suite.add_result(self.test_ping())
        self.test_suite.add_result(self.test_health())
        self.test_suite.add_result(self.test_verification())
        self.test_suite.add_result(self.test_valid_payload())
        self.test_suite.add_result(self.test_malformed_json())
        self.test_suite.add_result(self.test_empty_payload())
        self.test_suite.add_result(self.test_signature_verification())
        self.test_suite.add_result(self.test_performance())
        self.test_suite.add_result(self.test_pydantic_detection())
        self.test_suite.add_result(self.test_handler_signature())
        
        # Print summary
        self.test_suite.print_summary()
        
        # Return final status
        return self.test_suite.failed == 0 and self.test_suite.errors == 0
    
    # ==========================================================
    # DIAGNOSTIC RECOMMENDATIONS
    # ==========================================================
    
    def get_recommendations(self) -> List[str]:
        """Get recommendations based on test results."""
        recommendations = []
        
        for result in self.test_suite.results:
            if not result.status:
                if "422" in result.message or "Pydantic" in result.message:
                    recommendations.append(
                        "🚨 422 Error Detected: Remove all Pydantic models from webhook.py"
                    )
                    recommendations.append(
                        "   ✅ Use: async def handle_webhook(request: Request, background_tasks: BackgroundTasks)"
                    )
                    recommendations.append(
                        "   ✅ Parse JSON manually: data = json.loads(raw_body)"
                    )
                elif "401" in result.message:
                    recommendations.append(
                        "🔑 Verify Token Mismatch: Check WHATSAPP_VERIFY_TOKEN in config"
                    )
                elif "timeout" in result.message.lower():
                    recommendations.append(
                        "⏱️ Timeout Detected: Check if service is running at " + self.base_url
                    )
        
        if not recommendations:
            recommendations.append("✅ All tests passed! Webhook is working correctly.")
        
        return recommendations


# ==========================================================
# MAIN EXECUTION
# ==========================================================

async def run_diagnostics():
    """Run full diagnostic suite."""
    import sys
    
    # Parse command line arguments
    base_url = "http://localhost:8000"
    verify_token = "test_token"
    
    if len(sys.argv) > 1:
        base_url = sys.argv[1]
    if len(sys.argv) > 2:
        verify_token = sys.argv[2]
    
    # Run tests
    tester = WebhookDiagnosticTester(base_url=base_url, verify_token=verify_token)
    success = tester.run_all_tests()
    
    # Print recommendations
    print("\n💡 RECOMMENDATIONS:")
    print("=" * 70)
    for rec in tester.get_recommendations():
        print(f"  {rec}")
    print("=" * 70)
    
    # Exit with appropriate code
    sys.exit(0 if success else 1)


# ==========================================================
# QUICK TEST FUNCTION
# ==========================================================

def quick_test():
    """Quick test for immediate diagnosis."""
    print("\n" + "=" * 70)
    print("⚡ QUICK WEBHOOK DIAGNOSTIC")
    print("=" * 70)
    
    # Test 1: Check if webhook is running
    try:
        import httpx
        response = httpx.get("http://localhost:8000/webhook/ping", timeout=5.0)
        if response.status_code == 200:
            print("✅ Webhook is running")
        else:
            print(f"❌ Webhook returned {response.status_code}")
    except Exception as e:
        print(f"❌ Webhook not reachable: {e}")
        return
    
    # Test 2: Check for 422 error
    try:
        payload = {"test": "data"}
        response = httpx.post(
            "http://localhost:8000/webhook",
            json=payload,
            timeout=5.0
        )
        if response.status_code == 200:
            print("✅ No 422 error - webhook accepts payloads")
        elif response.status_code == 422:
            print("❌ 422 ERROR DETECTED! Pydantic validation is active.")
            print("   Fix: Remove all Pydantic from webhook.py")
        else:
            print(f"⚠️ Webhook returned {response.status_code}")
    except Exception as e:
        print(f"❌ Request failed: {e}")
    
    print("=" * 70)


if __name__ == "__main__":
    import asyncio
    
    # Check for quick mode
    import sys
    if "--quick" in sys.argv:
        quick_test()
    else:
        asyncio.run(run_diagnostics())
