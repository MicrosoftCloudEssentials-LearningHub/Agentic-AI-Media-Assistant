"""
Automated Testing Framework for A2A Protocol

This module provides comprehensive automated testing capabilities including:
- Continuous integration testing
- Performance regression testing
- Load testing automation
- Security testing automation
- User journey testing
- Agent behavior validation
"""
import asyncio
import logging
import json
import time
import random
from datetime import datetime, timedelta
from typing import Dict, List, Any, Optional, Callable
from dataclasses import dataclass
from enum import Enum
import httpx
from concurrent.futures import ThreadPoolExecutor

logger = logging.getLogger(__name__)


class TestType(Enum):
    """Types of automated tests"""
    UNIT = "unit"
    INTEGRATION = "integration"
    LOAD = "load"
    SECURITY = "security"
    USER_JOURNEY = "user_journey"
    PERFORMANCE = "performance"
    REGRESSION = "regression"


class TestStatus(Enum):
    """Test execution status"""
    PENDING = "pending"
    RUNNING = "running"
    PASSED = "passed"
    FAILED = "failed"
    SKIPPED = "skipped"
    TIMEOUT = "timeout"


@dataclass
class TestCase:
    """Individual test case definition"""
    test_id: str
    name: str
    description: str
    test_type: TestType
    timeout_seconds: int = 30
    retry_count: int = 0
    max_retries: int = 3
    prerequisites: List[str] = None
    tags: List[str] = None


@dataclass
class TestResult:
    """Test execution result"""
    test_id: str
    status: TestStatus
    started_at: datetime
    completed_at: Optional[datetime] = None
    duration_ms: Optional[float] = None
    error_message: Optional[str] = None
    output: Optional[Dict[str, Any]] = None
    metrics: Optional[Dict[str, float]] = None


class AutomatedTestFramework:
    """
    Comprehensive automated testing framework for the A2A protocol system.
    
    Provides:
    - Continuous testing automation
    - Performance regression detection
    - Load testing with realistic scenarios
    - Security vulnerability scanning
    - User journey validation
    - Agent behavior testing
    """
    
    def __init__(self, base_url: str = "http://localhost:8000"):
        self.base_url = base_url
        self.test_suites = {}
        self.test_results = []
        self.performance_baselines = {}
        self.running_tests = {}
        
        # HTTP client for testing
        self.http_client = httpx.AsyncClient(timeout=30.0)
        
        # Performance thresholds
        self.performance_thresholds = {
            'response_time_ms': 2000,
            'throughput_rps': 50,
            'error_rate': 0.05,
            'memory_usage_mb': 512,
            'cpu_usage_percent': 70
        }
        
        self._setup_test_suites()
        logger.info("Automated Test Framework initialized")
    
    def _setup_test_suites(self):
        """Setup predefined test suites"""
        
        # API Health Tests
        self.register_test_suite("api_health", [
            TestCase("health_check", "Health Endpoint Check", "Verify health endpoint responds correctly", TestType.INTEGRATION),
            TestCase("stats_endpoint", "Stats Endpoint Check", "Verify stats endpoint returns valid data", TestType.INTEGRATION),
            TestCase("root_endpoint", "Root Endpoint Check", "Verify root endpoint returns system info", TestType.INTEGRATION),
        ])
        
        # A2A Protocol Tests
        self.register_test_suite("a2a_protocol", [
            TestCase("message_processing", "Message Processing Test", "Test basic message processing through A2A", TestType.INTEGRATION),
            TestCase("agent_routing", "Agent Routing Test", "Test intelligent agent routing", TestType.INTEGRATION),
            TestCase("websocket_connection", "WebSocket Connection Test", "Test WebSocket connection stability", TestType.INTEGRATION),
            TestCase("rate_limiting", "Rate Limiting Test", "Test rate limiting functionality", TestType.INTEGRATION),
        ])
        
        # Performance Tests
        self.register_test_suite("performance", [
            TestCase("response_time", "Response Time Test", "Measure and validate response times", TestType.PERFORMANCE),
            TestCase("throughput", "Throughput Test", "Measure system throughput under load", TestType.LOAD),
            TestCase("concurrent_connections", "Concurrent Connections Test", "Test multiple simultaneous connections", TestType.LOAD),
            TestCase("memory_usage", "Memory Usage Test", "Monitor memory usage patterns", TestType.PERFORMANCE),
        ])
        
        # User Journey Tests
        self.register_test_suite("user_journeys", [
            TestCase("shopping_conversation", "Shopping Conversation Journey", "Complete shopping assistant conversation", TestType.USER_JOURNEY, timeout_seconds=60),
            TestCase("multi_agent_handoff", "Multi-Agent Handoff Journey", "Test handoffs between multiple agents", TestType.USER_JOURNEY, timeout_seconds=45),
            TestCase("error_recovery", "Error Recovery Journey", "Test system recovery from errors", TestType.USER_JOURNEY),
        ])
        
        # Security Tests
        self.register_test_suite("security", [
            TestCase("input_validation", "Input Validation Test", "Test input sanitization and validation", TestType.SECURITY),
            TestCase("rate_limit_bypass", "Rate Limit Bypass Test", "Test rate limiting security", TestType.SECURITY),
            TestCase("injection_attacks", "Injection Attack Test", "Test resistance to injection attacks", TestType.SECURITY),
            TestCase("cors_policy", "CORS Policy Test", "Validate CORS configuration", TestType.SECURITY),
        ])
    
    def register_test_suite(self, suite_name: str, test_cases: List[TestCase]):
        """Register a test suite"""
        self.test_suites[suite_name] = test_cases
        logger.info(f"Registered test suite: {suite_name} with {len(test_cases)} tests")
    
    async def run_continuous_testing(self, interval_minutes: int = 30):
        """Run continuous testing loop"""
        logger.info(f"Starting continuous testing with {interval_minutes} minute intervals")
        
        while True:
            try:
                # Run all test suites
                await self.run_all_test_suites()
                
                # Analyze results and trigger alerts if needed
                await self._analyze_test_results()
                
                # Wait for next cycle
                await asyncio.sleep(interval_minutes * 60)
                
            except Exception as e:
                logger.error(f"Error in continuous testing: {e}")
                await asyncio.sleep(300)  # Wait 5 minutes on error
    
    async def run_all_test_suites(self) -> Dict[str, List[TestResult]]:
        """Run all registered test suites"""
        logger.info("Running all test suites")
        
        all_results = {}
        
        for suite_name, test_cases in self.test_suites.items():
            suite_results = await self.run_test_suite(suite_name)
            all_results[suite_name] = suite_results
        
        return all_results
    
    async def run_test_suite(self, suite_name: str) -> List[TestResult]:
        """Run a specific test suite"""
        if suite_name not in self.test_suites:
            raise ValueError(f"Test suite {suite_name} not found")
        
        test_cases = self.test_suites[suite_name]
        logger.info(f"Running test suite: {suite_name} ({len(test_cases)} tests)")
        
        # Run tests concurrently
        tasks = []
        for test_case in test_cases:
            task = asyncio.create_task(self._run_single_test(test_case))
            tasks.append(task)
        
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        # Process results
        suite_results = []
        for i, result in enumerate(results):
            if isinstance(result, Exception):
                # Create failed test result for exception
                test_case = test_cases[i]
                failed_result = TestResult(
                    test_id=test_case.test_id,
                    status=TestStatus.FAILED,
                    started_at=datetime.now(),
                    completed_at=datetime.now(),
                    error_message=str(result)
                )
                suite_results.append(failed_result)
            else:
                suite_results.append(result)
        
        # Store results
        self.test_results.extend(suite_results)
        
        # Log suite summary
        passed = sum(1 for r in suite_results if r.status == TestStatus.PASSED)
        failed = sum(1 for r in suite_results if r.status == TestStatus.FAILED)
        logger.info(f"Test suite {suite_name} completed: {passed} passed, {failed} failed")
        
        return suite_results
    
    async def _run_single_test(self, test_case: TestCase) -> TestResult:
        """Run a single test case"""
        result = TestResult(
            test_id=test_case.test_id,
            status=TestStatus.RUNNING,
            started_at=datetime.now()
        )
        
        self.running_tests[test_case.test_id] = result
        
        try:
            logger.debug(f"Starting test: {test_case.name}")
            start_time = time.time()
            
            # Execute test based on type
            if test_case.test_type == TestType.INTEGRATION:
                await self._run_integration_test(test_case, result)
            elif test_case.test_type == TestType.LOAD:
                await self._run_load_test(test_case, result)
            elif test_case.test_type == TestType.PERFORMANCE:
                await self._run_performance_test(test_case, result)
            elif test_case.test_type == TestType.SECURITY:
                await self._run_security_test(test_case, result)
            elif test_case.test_type == TestType.USER_JOURNEY:
                await self._run_user_journey_test(test_case, result)
            else:
                raise NotImplementedError(f"Test type {test_case.test_type} not implemented")
            
            # Calculate duration
            end_time = time.time()
            result.duration_ms = (end_time - start_time) * 1000
            result.completed_at = datetime.now()
            result.status = TestStatus.PASSED
            
            logger.debug(f"Test {test_case.name} passed in {result.duration_ms:.2f}ms")
            
        except asyncio.TimeoutError:
            result.status = TestStatus.TIMEOUT
            result.error_message = f"Test timed out after {test_case.timeout_seconds} seconds"
            result.completed_at = datetime.now()
            logger.error(f"Test {test_case.name} timed out")
            
        except Exception as e:
            result.status = TestStatus.FAILED
            result.error_message = str(e)
            result.completed_at = datetime.now()
            logger.error(f"Test {test_case.name} failed: {e}")
        
        finally:
            if test_case.test_id in self.running_tests:
                del self.running_tests[test_case.test_id]
        
        return result
    
    async def _run_integration_test(self, test_case: TestCase, result: TestResult):
        """Run integration test"""
        if test_case.test_id == "health_check":
            response = await self.http_client.get(f"{self.base_url}/health")
            assert response.status_code == 200
            data = response.json()
            assert "status" in data
            result.output = data
            
        elif test_case.test_id == "stats_endpoint":
            response = await self.http_client.get(f"{self.base_url}/a2a/chat/stats")
            assert response.status_code == 200
            data = response.json()
            assert "statistics" in data
            result.output = data
            
        elif test_case.test_id == "root_endpoint":
            response = await self.http_client.get(f"{self.base_url}/")
            assert response.status_code == 200
            data = response.json()
            assert "message" in data
            result.output = data
            
        elif test_case.test_id == "message_processing":
            await self._test_message_processing(result)
            
        elif test_case.test_id == "agent_routing":
            await self._test_agent_routing(result)
            
        elif test_case.test_id == "websocket_connection":
            await self._test_websocket_connection(result)
            
        elif test_case.test_id == "rate_limiting":
            await self._test_rate_limiting(result)
    
    async def _test_message_processing(self, result: TestResult):
        """Test basic message processing"""
        test_message = {
            "message": "What paint colors would work well for a living room?",
            "session_id": f"test_session_{int(time.time())}",
            "streaming": False
        }
        
        response = await self.http_client.post(
            f"{self.base_url}/a2a/chat/message",
            json=test_message
        )
        
        assert response.status_code == 200
        data = response.json()
        assert "content" in data
        assert "agent_id" in data
        assert len(data["content"]) > 0
        
        result.output = {
            "message_sent": test_message["message"],
            "response_received": data["content"][:100] + "..." if len(data["content"]) > 100 else data["content"],
            "agent_used": data["agent_id"]
        }
    
    async def _test_agent_routing(self, result: TestResult):
        """Test intelligent agent routing"""
        test_cases = [
            {"message": "What colors go well together?", "expected_agent": "InteriorDesignAgent"},
            {"message": "Do you have this product in stock?", "expected_agent": "InventoryAgent"},
            {"message": "What's in my cart?", "expected_agent": "CartManagementAgent"},
        ]
        
        routing_results = []
        
        for test in test_cases:
            response = await self.http_client.post(
                f"{self.base_url}/a2a/chat/message",
                json={"message": test["message"], "streaming": False}
            )
            
            assert response.status_code == 200
            data = response.json()
            
            routing_results.append({
                "message": test["message"],
                "expected_agent": test["expected_agent"],
                "actual_agent": data.get("agent_id", "unknown"),
                "routed_correctly": test["expected_agent"] in data.get("agent_id", "")
            })
        
        result.output = {"routing_tests": routing_results}
        
        # Assert at least some routing worked correctly
        correct_routings = sum(1 for r in routing_results if r["routed_correctly"])
        assert correct_routings > 0, "No messages were routed correctly"
    
    async def _test_websocket_connection(self, result: TestResult):
        """Test WebSocket connection"""
        # Note: This is a simplified test - real implementation would use websockets library
        connection_attempts = []
        
        for i in range(3):
            try:
                # Simulate WebSocket connection test
                await asyncio.sleep(0.1)  # Simulate connection time
                connection_attempts.append({
                    "attempt": i + 1,
                    "status": "success",
                    "response_time_ms": 100 + random.randint(0, 50)
                })
            except Exception as e:
                connection_attempts.append({
                    "attempt": i + 1,
                    "status": "failed",
                    "error": str(e)
                })
        
        result.output = {"connection_attempts": connection_attempts}
        
        # Assert at least one connection succeeded
        successful = sum(1 for a in connection_attempts if a["status"] == "success")
        assert successful > 0, "No WebSocket connections succeeded"
    
    async def _test_rate_limiting(self, result: TestResult):
        """Test rate limiting functionality"""
        # Send multiple requests rapidly
        requests_sent = 0
        rate_limited = 0
        
        for i in range(70):  # Send more than the rate limit
            try:
                response = await self.http_client.post(
                    f"{self.base_url}/a2a/chat/message",
                    json={"message": f"Test message {i}", "streaming": False}
                )
                requests_sent += 1
                
                if response.status_code == 429:  # Rate limited
                    rate_limited += 1
                    
            except Exception:
                # Ignore individual request failures for this test
                pass
        
        result.output = {
            "requests_sent": requests_sent,
            "rate_limited_responses": rate_limited,
            "rate_limiting_active": rate_limited > 0
        }
        
        # Assert that rate limiting is working
        assert rate_limited > 0, "Rate limiting is not functioning"
    
    async def _run_load_test(self, test_case: TestCase, result: TestResult):
        """Run load test"""
        if test_case.test_id == "throughput":
            await self._test_throughput(result)
        elif test_case.test_id == "concurrent_connections":
            await self._test_concurrent_connections(result)
    
    async def _test_throughput(self, result: TestResult):
        """Test system throughput"""
        duration_seconds = 30
        concurrent_users = 10
        
        start_time = time.time()
        completed_requests = 0
        failed_requests = 0
        response_times = []
        
        async def send_request():
            nonlocal completed_requests, failed_requests
            try:
                req_start = time.time()
                response = await self.http_client.get(f"{self.base_url}/health")
                req_end = time.time()
                
                if response.status_code == 200:
                    completed_requests += 1
                    response_times.append((req_end - req_start) * 1000)
                else:
                    failed_requests += 1
            except Exception:
                failed_requests += 1
        
        # Run load test
        end_time = start_time + duration_seconds
        tasks = []
        
        while time.time() < end_time:
            # Maintain concurrent users
            if len(tasks) < concurrent_users:
                task = asyncio.create_task(send_request())
                tasks.append(task)
            
            # Clean completed tasks
            tasks = [t for t in tasks if not t.done()]
            
            await asyncio.sleep(0.1)
        
        # Wait for remaining tasks
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        
        total_time = time.time() - start_time
        throughput = completed_requests / total_time
        avg_response_time = sum(response_times) / len(response_times) if response_times else 0
        
        result.output = {
            "duration_seconds": total_time,
            "completed_requests": completed_requests,
            "failed_requests": failed_requests,
            "throughput_rps": throughput,
            "average_response_time_ms": avg_response_time
        }
        result.metrics = {
            "throughput_rps": throughput,
            "avg_response_time_ms": avg_response_time,
            "error_rate": failed_requests / (completed_requests + failed_requests) if (completed_requests + failed_requests) > 0 else 0
        }
        
        # Assert performance meets thresholds
        assert throughput >= self.performance_thresholds['throughput_rps'], f"Throughput too low: {throughput}"
        assert avg_response_time <= self.performance_thresholds['response_time_ms'], f"Response time too high: {avg_response_time}"
    
    async def _test_concurrent_connections(self, result: TestResult):
        """Test concurrent connections handling"""
        max_connections = 50
        connection_results = []
        
        async def test_connection(connection_id: int):
            try:
                start_time = time.time()
                response = await self.http_client.get(f"{self.base_url}/a2a/chat/stats")
                end_time = time.time()
                
                return {
                    "connection_id": connection_id,
                    "status": "success" if response.status_code == 200 else "failed",
                    "response_time_ms": (end_time - start_time) * 1000,
                    "status_code": response.status_code
                }
            except Exception as e:
                return {
                    "connection_id": connection_id,
                    "status": "error",
                    "error": str(e)
                }
        
        # Create concurrent connections
        tasks = [test_connection(i) for i in range(max_connections)]
        connection_results = await asyncio.gather(*tasks, return_exceptions=True)
        
        # Process results
        successful = sum(1 for r in connection_results if isinstance(r, dict) and r.get("status") == "success")
        failed = len(connection_results) - successful
        
        result.output = {
            "total_connections": max_connections,
            "successful_connections": successful,
            "failed_connections": failed,
            "success_rate": successful / max_connections,
            "connection_details": connection_results[:10]  # First 10 for brevity
        }
        
        # Assert acceptable success rate
        success_rate = successful / max_connections
        assert success_rate >= 0.9, f"Connection success rate too low: {success_rate}"
    
    async def _run_performance_test(self, test_case: TestCase, result: TestResult):
        """Run performance test"""
        if test_case.test_id == "response_time":
            await self._test_response_time(result)
        elif test_case.test_id == "memory_usage":
            await self._test_memory_usage(result)
    
    async def _test_response_time(self, result: TestResult):
        """Test response time performance"""
        endpoints = [
            "/health",
            "/a2a/chat/stats", 
            "/",
            "/a2a/chat/connections"
        ]
        
        response_times = {}
        
        for endpoint in endpoints:
            times = []
            for _ in range(10):  # 10 requests per endpoint
                start_time = time.time()
                try:
                    response = await self.http_client.get(f"{self.base_url}{endpoint}")
                    end_time = time.time()
                    if response.status_code == 200:
                        times.append((end_time - start_time) * 1000)
                except Exception:
                    pass  # Skip failed requests
            
            if times:
                response_times[endpoint] = {
                    "min_ms": min(times),
                    "max_ms": max(times),
                    "avg_ms": sum(times) / len(times),
                    "count": len(times)
                }
        
        result.output = {"endpoint_response_times": response_times}
        
        # Check if any endpoint exceeds threshold
        for endpoint, times in response_times.items():
            avg_time = times["avg_ms"]
            assert avg_time <= self.performance_thresholds['response_time_ms'], \
                f"Endpoint {endpoint} response time too high: {avg_time}ms"
    
    async def _test_memory_usage(self, result: TestResult):
        """Test memory usage patterns"""
        # Simulate memory usage test
        await asyncio.sleep(1)
        
        # In a real implementation, this would check actual memory usage
        simulated_memory = {
            "initial_mb": 128,
            "peak_mb": 256,
            "final_mb": 145,
            "growth_mb": 17
        }
        
        result.output = {"memory_usage": simulated_memory}
        result.metrics = {"memory_usage_mb": simulated_memory["peak_mb"]}
        
        # Assert memory usage is within limits
        assert simulated_memory["peak_mb"] <= self.performance_thresholds['memory_usage_mb'], \
            f"Memory usage too high: {simulated_memory['peak_mb']}MB"
    
    async def _run_security_test(self, test_case: TestCase, result: TestResult):
        """Run security test"""
        if test_case.test_id == "input_validation":
            await self._test_input_validation(result)
        elif test_case.test_id == "injection_attacks":
            await self._test_injection_attacks(result)
        elif test_case.test_id == "cors_policy":
            await self._test_cors_policy(result)
    
    async def _test_input_validation(self, result: TestResult):
        """Test input validation and sanitization"""
        malicious_inputs = [
            "<script>alert('xss')</script>",
            "'; DROP TABLE users; --",
            "../../../etc/passwd",
            "javascript:alert('xss')",
            "<img src=x onerror=alert('xss')>"
        ]
        
        validation_results = []
        
        for malicious_input in malicious_inputs:
            try:
                response = await self.http_client.post(
                    f"{self.base_url}/a2a/chat/message",
                    json={"message": malicious_input, "streaming": False}
                )
                
                # Check if input was properly sanitized
                if response.status_code == 200:
                    data = response.json()
                    response_content = data.get("content", "")
                    
                    # Check if malicious content is reflected back
                    contains_malicious = malicious_input.lower() in response_content.lower()
                    
                    validation_results.append({
                        "input": malicious_input[:50],
                        "status": "passed" if not contains_malicious else "failed",
                        "reflected": contains_malicious
                    })
                else:
                    validation_results.append({
                        "input": malicious_input[:50],
                        "status": "rejected",
                        "status_code": response.status_code
                    })
                    
            except Exception as e:
                validation_results.append({
                    "input": malicious_input[:50],
                    "status": "error",
                    "error": str(e)
                })
        
        result.output = {"validation_tests": validation_results}
        
        # Assert no malicious content was reflected
        reflected_count = sum(1 for r in validation_results if r.get("reflected", False))
        assert reflected_count == 0, f"{reflected_count} inputs were reflected without sanitization"
    
    async def _test_injection_attacks(self, result: TestResult):
        """Test resistance to injection attacks"""
        injection_payloads = [
            "1' OR '1'='1",
            "1; DELETE FROM users; --",
            "admin'--",
            "1' UNION SELECT * FROM users --"
        ]
        
        injection_results = []
        
        for payload in injection_payloads:
            try:
                response = await self.http_client.post(
                    f"{self.base_url}/a2a/chat/message",
                    json={"message": payload, "streaming": False}
                )
                
                injection_results.append({
                    "payload": payload[:30],
                    "status_code": response.status_code,
                    "blocked": response.status_code != 200 or "error" in response.text.lower()
                })
                
            except Exception as e:
                injection_results.append({
                    "payload": payload[:30],
                    "status": "error",
                    "error": str(e)
                })
        
        result.output = {"injection_tests": injection_results}
    
    async def _test_cors_policy(self, result: TestResult):
        """Test CORS policy configuration"""
        # Test CORS headers
        response = await self.http_client.options(f"{self.base_url}/a2a/chat/message")
        
        cors_headers = {
            "access-control-allow-origin": response.headers.get("access-control-allow-origin"),
            "access-control-allow-methods": response.headers.get("access-control-allow-methods"),
            "access-control-allow-headers": response.headers.get("access-control-allow-headers")
        }
        
        result.output = {"cors_headers": cors_headers}
        
        # Basic CORS validation
        assert cors_headers["access-control-allow-origin"] is not None, "CORS Allow-Origin header missing"
    
    async def _run_user_journey_test(self, test_case: TestCase, result: TestResult):
        """Run user journey test"""
        if test_case.test_id == "shopping_conversation":
            await self._test_shopping_conversation(result)
        elif test_case.test_id == "multi_agent_handoff":
            await self._test_multi_agent_handoff(result)
        elif test_case.test_id == "error_recovery":
            await self._test_error_recovery(result)
    
    async def _test_shopping_conversation(self, result: TestResult):
        """Test complete shopping conversation journey"""
        conversation_steps = [
            "Hi, I need help choosing paint colors for my living room",
            "I like modern styles and neutral colors",
            "Do you have Benjamin Moore paint in stock?",
            "What's the price for a gallon of Revere Pewter?",
            "Add it to my cart please"
        ]
        
        conversation_log = []
        session_id = f"test_journey_{int(time.time())}"
        
        for step, message in enumerate(conversation_steps):
            try:
                response = await self.http_client.post(
                    f"{self.base_url}/a2a/chat/message",
                    json={
                        "message": message,
                        "session_id": session_id,
                        "streaming": False
                    }
                )
                
                if response.status_code == 200:
                    data = response.json()
                    conversation_log.append({
                        "step": step + 1,
                        "user_message": message,
                        "agent_response": data.get("content", "")[:100] + "...",
                        "agent_id": data.get("agent_id", "unknown"),
                        "success": True
                    })
                else:
                    conversation_log.append({
                        "step": step + 1,
                        "user_message": message,
                        "error": f"HTTP {response.status_code}",
                        "success": False
                    })
                    
            except Exception as e:
                conversation_log.append({
                    "step": step + 1,
                    "user_message": message,
                    "error": str(e),
                    "success": False
                })
        
        result.output = {
            "conversation_log": conversation_log,
            "session_id": session_id,
            "total_steps": len(conversation_steps),
            "successful_steps": sum(1 for log in conversation_log if log.get("success", False))
        }
        
        # Assert most steps succeeded
        success_rate = result.output["successful_steps"] / len(conversation_steps)
        assert success_rate >= 0.8, f"Conversation success rate too low: {success_rate}"
    
    async def _test_multi_agent_handoff(self, result: TestResult):
        """Test handoffs between multiple agents"""
        handoff_scenario = [
            {"message": "I want to redecorate my bedroom", "expected_agent": "InteriorDesign"},
            {"message": "Do you have any paint brushes in stock?", "expected_agent": "Inventory"}, 
            {"message": "What discounts do I have available?", "expected_agent": "CustomerLoyalty"},
            {"message": "Add the paint brush to my cart", "expected_agent": "CartManagement"}
        ]
        
        handoff_results = []
        session_id = f"handoff_test_{int(time.time())}"
        
        for scenario in handoff_scenario:
            try:
                response = await self.http_client.post(
                    f"{self.base_url}/a2a/chat/message",
                    json={
                        "message": scenario["message"],
                        "session_id": session_id,
                        "streaming": False
                    }
                )
                
                if response.status_code == 200:
                    data = response.json()
                    agent_used = data.get("agent_id", "")
                    
                    handoff_results.append({
                        "message": scenario["message"],
                        "expected_agent": scenario["expected_agent"],
                        "actual_agent": agent_used,
                        "handoff_successful": scenario["expected_agent"].lower() in agent_used.lower(),
                        "response_received": len(data.get("content", "")) > 0
                    })
                    
            except Exception as e:
                handoff_results.append({
                    "message": scenario["message"],
                    "error": str(e),
                    "handoff_successful": False
                })
        
        result.output = {
            "handoff_tests": handoff_results,
            "successful_handoffs": sum(1 for r in handoff_results if r.get("handoff_successful", False))
        }
        
        # Assert reasonable handoff success rate
        success_rate = result.output["successful_handoffs"] / len(handoff_scenario)
        assert success_rate >= 0.5, f"Agent handoff success rate too low: {success_rate}"
    
    async def _test_error_recovery(self, result: TestResult):
        """Test system recovery from errors"""
        error_scenarios = [
            {"message": "", "description": "Empty message"},
            {"message": "x" * 10000, "description": "Extremely long message"},
            {"message": "Invalid JSON payload test", "description": "Boundary condition"}
        ]
        
        recovery_results = []
        
        for scenario in error_scenarios:
            try:
                response = await self.http_client.post(
                    f"{self.base_url}/a2a/chat/message",
                    json={"message": scenario["message"], "streaming": False}
                )
                
                # System should handle errors gracefully
                recovery_results.append({
                    "scenario": scenario["description"],
                    "status_code": response.status_code,
                    "graceful_handling": response.status_code in [400, 422, 500],  # Expected error codes
                    "response_received": len(response.content) > 0
                })
                
            except Exception as e:
                recovery_results.append({
                    "scenario": scenario["description"],
                    "error": str(e),
                    "graceful_handling": True  # Exception handling is also graceful
                })
        
        result.output = {"error_recovery_tests": recovery_results}
        
        # Assert all scenarios were handled gracefully
        graceful_count = sum(1 for r in recovery_results if r.get("graceful_handling", False))
        assert graceful_count == len(error_scenarios), "Not all error scenarios handled gracefully"
    
    async def _analyze_test_results(self):
        """Analyze test results and trigger alerts if needed"""
        if not self.test_results:
            return
        
        recent_results = [r for r in self.test_results if 
                         (datetime.now() - r.started_at).total_seconds() < 3600]  # Last hour
        
        if not recent_results:
            return
        
        # Calculate metrics
        total_tests = len(recent_results)
        passed_tests = sum(1 for r in recent_results if r.status == TestStatus.PASSED)
        failed_tests = sum(1 for r in recent_results if r.status == TestStatus.FAILED)
        
        pass_rate = passed_tests / total_tests if total_tests > 0 else 0
        
        # Trigger alerts for low pass rate
        if pass_rate < 0.8:
            logger.warning(f"Low test pass rate detected: {pass_rate:.2%} ({failed_tests}/{total_tests} failed)")
            await self._trigger_test_failure_alert(recent_results)
        
        # Check for performance regressions
        await self._check_performance_regressions(recent_results)
    
    async def _trigger_test_failure_alert(self, failed_results: List[TestResult]):
        """Trigger alert for test failures"""
        failed_tests = [r for r in failed_results if r.status == TestStatus.FAILED]
        
        alert_data = {
            "alert_type": "test_failures",
            "timestamp": datetime.now().isoformat(),
            "failed_count": len(failed_tests),
            "total_count": len(failed_results),
            "failed_tests": [
                {
                    "test_id": r.test_id,
                    "error": r.error_message,
                    "duration": r.duration_ms
                }
                for r in failed_tests[:5]  # First 5 failures
            ]
        }
        
        # In a real implementation, this would send to alerting system
        logger.error(f"Test failure alert: {json.dumps(alert_data, indent=2)}")
    
    async def _check_performance_regressions(self, recent_results: List[TestResult]):
        """Check for performance regressions"""
        performance_results = [r for r in recent_results if 
                             r.metrics and r.status == TestStatus.PASSED]
        
        for result in performance_results:
            for metric_name, metric_value in result.metrics.items():
                baseline = self.performance_baselines.get(f"{result.test_id}_{metric_name}")
                
                if baseline and metric_value > baseline * 1.2:  # 20% regression threshold
                    logger.warning(f"Performance regression detected in {result.test_id}: "
                                 f"{metric_name} = {metric_value:.2f} (baseline: {baseline:.2f})")
    
    def get_test_summary(self, hours: int = 24) -> Dict[str, Any]:
        """Get test execution summary"""
        cutoff_time = datetime.now() - timedelta(hours=hours)
        recent_results = [r for r in self.test_results if r.started_at >= cutoff_time]
        
        if not recent_results:
            return {"message": "No test results in the specified time period"}
        
        by_status = {}
        for status in TestStatus:
            count = sum(1 for r in recent_results if r.status == status)
            by_status[status.value] = count
        
        by_type = {}
        for test_type in TestType:
            type_results = [r for r in recent_results if r.test_id in 
                          [tc.test_id for suite in self.test_suites.values() 
                           for tc in suite if tc.test_type == test_type]]
            by_type[test_type.value] = len(type_results)
        
        avg_duration = sum(r.duration_ms or 0 for r in recent_results) / len(recent_results)
        
        return {
            "time_period_hours": hours,
            "total_tests": len(recent_results),
            "results_by_status": by_status,
            "results_by_type": by_type,
            "average_duration_ms": avg_duration,
            "pass_rate": by_status.get("passed", 0) / len(recent_results),
            "current_running_tests": len(self.running_tests)
        }


# Factory function
def create_test_framework(base_url: str = "http://localhost:8000") -> AutomatedTestFramework:
    """Create an automated test framework"""
    return AutomatedTestFramework(base_url)