"""
Automated Monitoring and Observability Framework

This module provides comprehensive monitoring and observability capabilities including:
- Real-time metrics collection and aggregation
- Custom dashboard generation
- Automated alerting and notifications
- Performance anomaly detection
- Health check automation
- Log aggregation and analysis
"""
import asyncio
import logging
import json
import time
import statistics
from datetime import datetime, timedelta
from typing import Dict, List, Any, Optional, Callable
from dataclasses import dataclass
from enum import Enum
import aiofiles
from collections import defaultdict, deque

logger = logging.getLogger(__name__)


class MetricType(Enum):
    """Types of metrics collected"""
    COUNTER = "counter"
    GAUGE = "gauge"
    HISTOGRAM = "histogram"
    TIMER = "timer"


class AlertSeverity(Enum):
    """Alert severity levels"""
    INFO = "info"
    WARNING = "warning"
    ERROR = "error" 
    CRITICAL = "critical"


@dataclass
class Metric:
    """Individual metric data point"""
    name: str
    value: float
    metric_type: MetricType
    timestamp: datetime
    labels: Dict[str, str] = None
    unit: str = None


@dataclass
class Alert:
    """Alert definition and current state"""
    alert_id: str
    name: str
    description: str
    condition: str
    severity: AlertSeverity
    threshold: float
    is_active: bool = False
    last_triggered: Optional[datetime] = None
    trigger_count: int = 0


@dataclass
class HealthCheck:
    """Health check configuration and result"""
    check_id: str
    name: str
    description: str
    endpoint: str
    timeout_seconds: int = 30
    expected_status: int = 200
    interval_seconds: int = 60
    failure_threshold: int = 3
    last_check: Optional[datetime] = None
    consecutive_failures: int = 0
    is_healthy: bool = True


class AutomatedMonitoringFramework:
    """
    Comprehensive monitoring and observability framework for the A2A protocol system.
    
    Provides:
    - Real-time metrics collection and storage
    - Custom dashboard generation
    - Intelligent alerting based on thresholds and patterns
    - Automated health checks
    - Performance anomaly detection
    - Log aggregation and analysis
    """
    
    def __init__(self, storage_path: str = "./monitoring_data"):
        self.storage_path = storage_path
        
        # Metrics storage
        self.metrics = defaultdict(lambda: deque(maxlen=10000))  # Keep last 10k points per metric
        self.metric_definitions = {}
        
        # Alerting
        self.alerts = {}
        self.alert_history = deque(maxlen=1000)
        self.alert_handlers = []
        
        # Health checks
        self.health_checks = {}
        self.health_history = deque(maxlen=1000)
        
        # Dashboards
        self.dashboards = {}
        
        # Anomaly detection
        self.anomaly_detectors = {}
        
        # Background tasks
        self.collection_tasks = []
        self.is_running = False
        
        self._setup_default_metrics()
        self._setup_default_alerts()
        self._setup_default_health_checks()
        self._setup_default_dashboards()
        
        logger.info("Automated Monitoring Framework initialized")
    
    def _setup_default_metrics(self):
        """Setup default metrics to collect"""
        
        # System metrics
        self.register_metric("system_cpu_usage", MetricType.GAUGE, "percentage")
        self.register_metric("system_memory_usage", MetricType.GAUGE, "bytes")
        self.register_metric("system_disk_usage", MetricType.GAUGE, "percentage")
        
        # Application metrics
        self.register_metric("a2a_requests_total", MetricType.COUNTER, "count")
        self.register_metric("a2a_request_duration", MetricType.HISTOGRAM, "milliseconds")
        self.register_metric("a2a_active_connections", MetricType.GAUGE, "count")
        self.register_metric("a2a_message_processing_rate", MetricType.GAUGE, "messages/second")
        self.register_metric("a2a_agent_response_time", MetricType.HISTOGRAM, "milliseconds")
        
        # Error metrics
        self.register_metric("a2a_errors_total", MetricType.COUNTER, "count")
        self.register_metric("a2a_error_rate", MetricType.GAUGE, "percentage")
        self.register_metric("a2a_timeout_rate", MetricType.GAUGE, "percentage")
        
        # Business metrics
        self.register_metric("shopping_sessions_started", MetricType.COUNTER, "count")
        self.register_metric("shopping_sessions_completed", MetricType.COUNTER, "count")
        self.register_metric("agent_handoffs_total", MetricType.COUNTER, "count")
        self.register_metric("user_satisfaction_score", MetricType.GAUGE, "score")
    
    def _setup_default_alerts(self):
        """Setup default alert conditions"""
        
        self.register_alert(
            "high_cpu_usage",
            "High CPU Usage",
            "System CPU usage is above threshold",
            "system_cpu_usage > 80",
            AlertSeverity.WARNING,
            80.0
        )
        
        self.register_alert(
            "high_memory_usage", 
            "High Memory Usage",
            "System memory usage is above threshold",
            "system_memory_usage > 1073741824",  # 1GB
            AlertSeverity.WARNING,
            1073741824
        )
        
        self.register_alert(
            "high_error_rate",
            "High Error Rate",
            "Application error rate is above acceptable threshold",
            "a2a_error_rate > 5",
            AlertSeverity.ERROR,
            5.0
        )
        
        self.register_alert(
            "slow_response_time",
            "Slow Response Time",
            "Average response time is above threshold",
            "a2a_request_duration > 2000",
            AlertSeverity.WARNING,
            2000.0
        )
        
        self.register_alert(
            "connection_limit",
            "High Connection Count",
            "Active connections approaching system limits",
            "a2a_active_connections > 100",
            AlertSeverity.WARNING,
            100.0
        )
    
    def _setup_default_health_checks(self):
        """Setup default health check endpoints"""
        
        self.register_health_check(
            "api_health",
            "API Health Check",
            "Verify main API endpoint is responding",
            "/health"
        )
        
        self.register_health_check(
            "a2a_chat_health",
            "A2A Chat Health",
            "Verify A2A chat endpoint is responding",
            "/a2a/chat/stats"
        )
        
        self.register_health_check(
            "websocket_health",
            "WebSocket Health",
            "Verify WebSocket endpoint is accessible",
            "/a2a/chat/ws"
        )
    
    def _setup_default_dashboards(self):
        """Setup default monitoring dashboards"""
        
        # System Overview Dashboard
        self.register_dashboard("system_overview", {
            "title": "System Overview",
            "description": "High-level system health and performance metrics",
            "refresh_interval": 30,
            "panels": [
                {
                    "title": "CPU Usage",
                    "type": "gauge",
                    "metric": "system_cpu_usage",
                    "unit": "%",
                    "thresholds": {"warning": 70, "critical": 90}
                },
                {
                    "title": "Memory Usage", 
                    "type": "gauge",
                    "metric": "system_memory_usage",
                    "unit": "MB",
                    "thresholds": {"warning": 800, "critical": 1000}
                },
                {
                    "title": "Active Connections",
                    "type": "line",
                    "metric": "a2a_active_connections",
                    "time_range": "1h"
                },
                {
                    "title": "Request Rate",
                    "type": "line",
                    "metric": "a2a_message_processing_rate",
                    "unit": "req/s",
                    "time_range": "1h"
                }
            ]
        })
        
        # Performance Dashboard
        self.register_dashboard("performance", {
            "title": "Performance Metrics",
            "description": "Detailed performance and latency metrics",
            "refresh_interval": 15,
            "panels": [
                {
                    "title": "Response Time Distribution",
                    "type": "histogram",
                    "metric": "a2a_request_duration",
                    "unit": "ms",
                    "time_range": "1h"
                },
                {
                    "title": "Agent Response Times",
                    "type": "line",
                    "metric": "a2a_agent_response_time",
                    "unit": "ms",
                    "time_range": "1h",
                    "group_by": "agent_id"
                },
                {
                    "title": "Error Rate",
                    "type": "line",
                    "metric": "a2a_error_rate",
                    "unit": "%",
                    "time_range": "24h"
                }
            ]
        })
        
        # Business Metrics Dashboard
        self.register_dashboard("business", {
            "title": "Business Metrics",
            "description": "Shopping experience and business KPIs",
            "refresh_interval": 60,
            "panels": [
                {
                    "title": "Shopping Sessions",
                    "type": "stat",
                    "metric": "shopping_sessions_started",
                    "time_range": "24h"
                },
                {
                    "title": "Completion Rate",
                    "type": "stat", 
                    "derived_metric": "shopping_sessions_completed / shopping_sessions_started * 100",
                    "unit": "%"
                },
                {
                    "title": "Agent Handoffs",
                    "type": "line",
                    "metric": "agent_handoffs_total",
                    "time_range": "24h"
                },
                {
                    "title": "User Satisfaction",
                    "type": "gauge",
                    "metric": "user_satisfaction_score",
                    "unit": "/10",
                    "thresholds": {"warning": 7, "critical": 5}
                }
            ]
        })
    
    def register_metric(self, name: str, metric_type: MetricType, unit: str = None):
        """Register a new metric for collection"""
        self.metric_definitions[name] = {
            "type": metric_type,
            "unit": unit,
            "created_at": datetime.now()
        }
        logger.debug(f"Registered metric: {name} ({metric_type.value})")
    
    def register_alert(self, alert_id: str, name: str, description: str, 
                      condition: str, severity: AlertSeverity, threshold: float):
        """Register a new alert condition"""
        alert = Alert(
            alert_id=alert_id,
            name=name,
            description=description,
            condition=condition,
            severity=severity,
            threshold=threshold
        )
        self.alerts[alert_id] = alert
        logger.debug(f"Registered alert: {name}")
    
    def register_health_check(self, check_id: str, name: str, description: str, endpoint: str,
                            timeout_seconds: int = 30, expected_status: int = 200,
                            interval_seconds: int = 60, failure_threshold: int = 3):
        """Register a new health check"""
        health_check = HealthCheck(
            check_id=check_id,
            name=name,
            description=description,
            endpoint=endpoint,
            timeout_seconds=timeout_seconds,
            expected_status=expected_status,
            interval_seconds=interval_seconds,
            failure_threshold=failure_threshold
        )
        self.health_checks[check_id] = health_check
        logger.debug(f"Registered health check: {name}")
    
    def register_dashboard(self, dashboard_id: str, definition: Dict[str, Any]):
        """Register a new dashboard"""
        self.dashboards[dashboard_id] = definition
        logger.debug(f"Registered dashboard: {definition['title']}")
    
    def record_metric(self, name: str, value: float, labels: Dict[str, str] = None):
        """Record a metric value"""
        if name not in self.metric_definitions:
            logger.warning(f"Recording undefined metric: {name}")
            return
        
        metric = Metric(
            name=name,
            value=value,
            metric_type=self.metric_definitions[name]["type"],
            timestamp=datetime.now(),
            labels=labels or {},
            unit=self.metric_definitions[name]["unit"]
        )
        
        self.metrics[name].append(metric)
        
        # Check alerts for this metric
        asyncio.create_task(self._check_alerts_for_metric(name, value))
    
    async def start_monitoring(self):
        """Start the monitoring framework"""
        if self.is_running:
            logger.warning("Monitoring framework already running")
            return
        
        self.is_running = True
        logger.info("Starting automated monitoring framework")
        
        # Start metric collection tasks
        self.collection_tasks = [
            asyncio.create_task(self._collect_system_metrics()),
            asyncio.create_task(self._collect_application_metrics()),
            asyncio.create_task(self._run_health_checks()),
            asyncio.create_task(self._detect_anomalies()),
            asyncio.create_task(self._cleanup_old_data())
        ]
        
        logger.info("Monitoring framework started")
    
    async def stop_monitoring(self):
        """Stop the monitoring framework"""
        self.is_running = False
        
        # Cancel all tasks
        for task in self.collection_tasks:
            task.cancel()
        
        await asyncio.gather(*self.collection_tasks, return_exceptions=True)
        self.collection_tasks.clear()
        
        logger.info("Monitoring framework stopped")
    
    async def _collect_system_metrics(self):
        """Collect system-level metrics"""
        while self.is_running:
            try:
                # Simulate system metrics collection
                # In a real implementation, this would use psutil or similar
                
                # CPU usage (simulated)
                cpu_usage = 20 + (time.time() % 60) / 2  # Oscillates between 20-50%
                self.record_metric("system_cpu_usage", cpu_usage)
                
                # Memory usage (simulated)
                memory_usage = 512 * 1024 * 1024 + (time.time() % 30) * 10 * 1024 * 1024  # ~512-812MB
                self.record_metric("system_memory_usage", memory_usage)
                
                # Disk usage (simulated)
                disk_usage = 45.5  # 45.5%
                self.record_metric("system_disk_usage", disk_usage)
                
                await asyncio.sleep(30)  # Collect every 30 seconds
                
            except Exception as e:
                logger.error(f"Error collecting system metrics: {e}")
                await asyncio.sleep(30)
    
    async def _collect_application_metrics(self):
        """Collect application-specific metrics"""
        while self.is_running:
            try:
                # Simulate application metrics
                # In a real implementation, these would come from the actual A2A system
                
                current_time = time.time()
                
                # Request metrics
                request_rate = 10 + 5 * (0.5 + 0.5 * (current_time % 300) / 300)  # 10-15 req/s
                self.record_metric("a2a_message_processing_rate", request_rate)
                
                # Connection metrics
                active_connections = int(25 + 15 * (0.5 + 0.5 * (current_time % 180) / 180))  # 25-40 connections
                self.record_metric("a2a_active_connections", active_connections)
                
                # Response time (simulated with realistic variation)
                base_response_time = 150 + 50 * (current_time % 120) / 120  # 150-200ms base
                response_time = base_response_time + 20 * ((current_time % 10) - 5)  # Add variation
                self.record_metric("a2a_request_duration", max(50, response_time))
                
                # Error rate (usually low, occasional spikes)
                error_rate = 1.0 if (current_time % 600) < 30 else 0.2  # Spike every 10 minutes
                self.record_metric("a2a_error_rate", error_rate)
                
                # Business metrics
                if current_time % 300 < 15:  # Every 5 minutes, simulate some activity
                    self.record_metric("shopping_sessions_started", 1)
                    if (current_time % 600) < 100:  # 80% completion rate simulation
                        self.record_metric("shopping_sessions_completed", 1)
                
                # User satisfaction (simulated)
                satisfaction = 8.5 + 1.0 * ((current_time % 100) - 50) / 50  # 7.5-9.5 range
                self.record_metric("user_satisfaction_score", max(1, min(10, satisfaction)))
                
                await asyncio.sleep(15)  # Collect every 15 seconds
                
            except Exception as e:
                logger.error(f"Error collecting application metrics: {e}")
                await asyncio.sleep(15)
    
    async def _run_health_checks(self):
        """Run automated health checks"""
        while self.is_running:
            try:
                for check_id, health_check in self.health_checks.items():
                    if (not health_check.last_check or 
                        (datetime.now() - health_check.last_check).total_seconds() >= health_check.interval_seconds):
                        
                        await self._perform_health_check(health_check)
                
                await asyncio.sleep(10)  # Check every 10 seconds for due health checks
                
            except Exception as e:
                logger.error(f"Error in health check loop: {e}")
                await asyncio.sleep(10)
    
    async def _perform_health_check(self, health_check: HealthCheck):
        """Perform individual health check"""
        try:
            # Simulate health check
            # In a real implementation, this would make actual HTTP requests
            
            health_check.last_check = datetime.now()
            
            # Simulate occasional failures
            simulated_success = (time.time() % 300) > 10  # Fail for 10 seconds every 5 minutes
            
            if simulated_success:
                health_check.consecutive_failures = 0
                if not health_check.is_healthy:
                    health_check.is_healthy = True
                    await self._trigger_health_recovery_alert(health_check)
            else:
                health_check.consecutive_failures += 1
                if health_check.consecutive_failures >= health_check.failure_threshold and health_check.is_healthy:
                    health_check.is_healthy = False
                    await self._trigger_health_failure_alert(health_check)
            
            # Record health check result
            self.health_history.append({
                "check_id": health_check.check_id,
                "timestamp": datetime.now().isoformat(),
                "is_healthy": health_check.is_healthy,
                "consecutive_failures": health_check.consecutive_failures
            })
            
        except Exception as e:
            logger.error(f"Error performing health check {health_check.name}: {e}")
            health_check.consecutive_failures += 1
    
    async def _trigger_health_failure_alert(self, health_check: HealthCheck):
        """Trigger alert for health check failure"""
        alert_data = {
            "type": "health_check_failure",
            "check_id": health_check.check_id,
            "check_name": health_check.name,
            "description": health_check.description,
            "consecutive_failures": health_check.consecutive_failures,
            "timestamp": datetime.now().isoformat()
        }
        
        logger.error(f"Health check failed: {health_check.name}")
        await self._send_alert(AlertSeverity.ERROR, f"Health Check Failed: {health_check.name}", alert_data)
    
    async def _trigger_health_recovery_alert(self, health_check: HealthCheck):
        """Trigger alert for health check recovery"""
        alert_data = {
            "type": "health_check_recovery",
            "check_id": health_check.check_id,
            "check_name": health_check.name,
            "timestamp": datetime.now().isoformat()
        }
        
        logger.info(f"Health check recovered: {health_check.name}")
        await self._send_alert(AlertSeverity.INFO, f"Health Check Recovered: {health_check.name}", alert_data)
    
    async def _check_alerts_for_metric(self, metric_name: str, value: float):
        """Check if any alerts should be triggered for a metric"""
        for alert_id, alert in self.alerts.items():
            if metric_name in alert.condition:
                should_trigger = await self._evaluate_alert_condition(alert, metric_name, value)
                
                if should_trigger and not alert.is_active:
                    alert.is_active = True
                    alert.last_triggered = datetime.now()
                    alert.trigger_count += 1
                    await self._trigger_alert(alert, value)
                    
                elif not should_trigger and alert.is_active:
                    alert.is_active = False
                    await self._resolve_alert(alert)
    
    async def _evaluate_alert_condition(self, alert: Alert, metric_name: str, value: float) -> bool:
        """Evaluate if alert condition is met"""
        # Simple threshold evaluation
        # In a real implementation, this would support complex expressions
        
        if ">" in alert.condition:
            return value > alert.threshold
        elif "<" in alert.condition:
            return value < alert.threshold
        elif "==" in alert.condition:
            return abs(value - alert.threshold) < 0.001
        
        return False
    
    async def _trigger_alert(self, alert: Alert, value: float):
        """Trigger an alert"""
        alert_data = {
            "alert_id": alert.alert_id,
            "alert_name": alert.name,
            "description": alert.description,
            "severity": alert.severity.value,
            "condition": alert.condition,
            "threshold": alert.threshold,
            "current_value": value,
            "trigger_count": alert.trigger_count,
            "timestamp": datetime.now().isoformat()
        }
        
        self.alert_history.append(alert_data)
        
        logger.warning(f"Alert triggered: {alert.name} (value: {value}, threshold: {alert.threshold})")
        await self._send_alert(alert.severity, alert.name, alert_data)
    
    async def _resolve_alert(self, alert: Alert):
        """Resolve an active alert"""
        resolution_data = {
            "alert_id": alert.alert_id,
            "alert_name": alert.name,
            "resolved_at": datetime.now().isoformat(),
            "total_triggers": alert.trigger_count
        }
        
        logger.info(f"Alert resolved: {alert.name}")
        await self._send_alert(AlertSeverity.INFO, f"Alert Resolved: {alert.name}", resolution_data)
    
    async def _send_alert(self, severity: AlertSeverity, title: str, data: Dict[str, Any]):
        """Send alert to configured handlers"""
        # In a real implementation, this would send to Slack, email, PagerDuty, etc.
        alert_message = {
            "severity": severity.value,
            "title": title,
            "data": data,
            "timestamp": datetime.now().isoformat()
        }
        
        # Log the alert
        log_level = {
            AlertSeverity.INFO: logging.INFO,
            AlertSeverity.WARNING: logging.WARNING,
            AlertSeverity.ERROR: logging.ERROR,
            AlertSeverity.CRITICAL: logging.CRITICAL
        }[severity]
        
        logger.log(log_level, f"ALERT: {title}")
        
        # Save to file for persistence
        await self._save_alert_to_file(alert_message)
    
    async def _save_alert_to_file(self, alert_data: Dict[str, Any]):
        """Save alert to file for persistence"""
        try:
            import os
            os.makedirs(self.storage_path, exist_ok=True)
            
            alert_file = f"{self.storage_path}/alerts.jsonl"
            async with aiofiles.open(alert_file, "a") as f:
                await f.write(json.dumps(alert_data) + "\n")
                
        except Exception as e:
            logger.error(f"Error saving alert to file: {e}")
    
    async def _detect_anomalies(self):
        """Detect anomalies in metrics using statistical methods"""
        while self.is_running:
            try:
                for metric_name, metric_points in self.metrics.items():
                    if len(metric_points) >= 30:  # Need enough data points
                        await self._check_metric_for_anomalies(metric_name, metric_points)
                
                await asyncio.sleep(300)  # Check every 5 minutes
                
            except Exception as e:
                logger.error(f"Error in anomaly detection: {e}")
                await asyncio.sleep(300)
    
    async def _check_metric_for_anomalies(self, metric_name: str, metric_points: deque):
        """Check specific metric for anomalies"""
        try:
            # Get recent values
            recent_values = [point.value for point in list(metric_points)[-30:]]
            current_value = recent_values[-1]
            historical_values = recent_values[:-1]
            
            # Calculate statistical properties
            mean = statistics.mean(historical_values)
            stdev = statistics.stdev(historical_values) if len(historical_values) > 1 else 0
            
            # Anomaly detection using standard deviation
            if stdev > 0:
                z_score = abs(current_value - mean) / stdev
                
                # Trigger anomaly alert if z-score > 3 (highly unusual)
                if z_score > 3:
                    await self._trigger_anomaly_alert(metric_name, current_value, mean, z_score)
                    
        except Exception as e:
            logger.error(f"Error checking anomalies for {metric_name}: {e}")
    
    async def _trigger_anomaly_alert(self, metric_name: str, current_value: float, 
                                   expected_value: float, z_score: float):
        """Trigger alert for detected anomaly"""
        anomaly_data = {
            "type": "anomaly_detection",
            "metric_name": metric_name,
            "current_value": current_value,
            "expected_value": expected_value,
            "z_score": z_score,
            "deviation_percent": abs(current_value - expected_value) / expected_value * 100,
            "timestamp": datetime.now().isoformat()
        }
        
        severity = AlertSeverity.WARNING if z_score < 4 else AlertSeverity.ERROR
        
        logger.warning(f"Anomaly detected in {metric_name}: {current_value} (expected ~{expected_value:.2f}, z-score: {z_score:.2f})")
        await self._send_alert(severity, f"Anomaly Detected: {metric_name}", anomaly_data)
    
    async def _cleanup_old_data(self):
        """Clean up old monitoring data"""
        while self.is_running:
            try:
                # Clean up old alert history
                cutoff_time = datetime.now() - timedelta(days=7)
                
                # Keep only recent alerts in memory
                recent_alerts = []
                for alert_data in self.alert_history:
                    if isinstance(alert_data, dict) and "timestamp" in alert_data:
                        alert_time = datetime.fromisoformat(alert_data["timestamp"].replace("Z", "+00:00"))
                        if alert_time >= cutoff_time:
                            recent_alerts.append(alert_data)
                
                self.alert_history.clear()
                self.alert_history.extend(recent_alerts)
                
                # Clean up health check history
                recent_health = []
                for health_data in self.health_history:
                    if isinstance(health_data, dict) and "timestamp" in health_data:
                        health_time = datetime.fromisoformat(health_data["timestamp"].replace("Z", "+00:00"))
                        if health_time >= cutoff_time:
                            recent_health.append(health_data)
                
                self.health_history.clear()
                self.health_history.extend(recent_health)
                
                logger.debug("Completed monitoring data cleanup")
                
                await asyncio.sleep(3600)  # Clean up every hour
                
            except Exception as e:
                logger.error(f"Error in monitoring data cleanup: {e}")
                await asyncio.sleep(3600)
    
    def get_metric_summary(self, metric_name: str, time_range_minutes: int = 60) -> Dict[str, Any]:
        """Get summary statistics for a metric"""
        if metric_name not in self.metrics:
            return {"error": f"Metric {metric_name} not found"}
        
        cutoff_time = datetime.now() - timedelta(minutes=time_range_minutes)
        recent_points = [
            point for point in self.metrics[metric_name]
            if point.timestamp >= cutoff_time
        ]
        
        if not recent_points:
            return {"error": "No data points in time range"}
        
        values = [point.value for point in recent_points]
        
        return {
            "metric_name": metric_name,
            "time_range_minutes": time_range_minutes,
            "data_points": len(values),
            "current_value": values[-1],
            "min_value": min(values),
            "max_value": max(values),
            "average_value": sum(values) / len(values),
            "median_value": statistics.median(values),
            "std_deviation": statistics.stdev(values) if len(values) > 1 else 0,
            "first_timestamp": recent_points[0].timestamp.isoformat(),
            "last_timestamp": recent_points[-1].timestamp.isoformat()
        }
    
    def get_dashboard_data(self, dashboard_id: str) -> Dict[str, Any]:
        """Get data for a dashboard"""
        if dashboard_id not in self.dashboards:
            return {"error": f"Dashboard {dashboard_id} not found"}
        
        dashboard = self.dashboards[dashboard_id]
        dashboard_data = {
            "title": dashboard["title"],
            "description": dashboard["description"],
            "refresh_interval": dashboard["refresh_interval"],
            "generated_at": datetime.now().isoformat(),
            "panels": []
        }
        
        for panel in dashboard["panels"]:
            panel_data = {
                "title": panel["title"],
                "type": panel["type"],
                "unit": panel.get("unit", ""),
                "data": {}
            }
            
            # Get metric data for panel
            metric_name = panel.get("metric")
            if metric_name:
                time_range_str = panel.get("time_range", "1h")
                time_range_minutes = self._parse_time_range(time_range_str)
                panel_data["data"] = self.get_metric_summary(metric_name, time_range_minutes)
            
            dashboard_data["panels"].append(panel_data)
        
        return dashboard_data
    
    def _parse_time_range(self, time_range_str: str) -> int:
        """Parse time range string to minutes"""
        if time_range_str.endswith("m"):
            return int(time_range_str[:-1])
        elif time_range_str.endswith("h"):
            return int(time_range_str[:-1]) * 60
        elif time_range_str.endswith("d"):
            return int(time_range_str[:-1]) * 24 * 60
        else:
            return 60  # Default 1 hour
    
    def get_system_status(self) -> Dict[str, Any]:
        """Get overall system status"""
        active_alerts = [alert for alert in self.alerts.values() if alert.is_active]
        unhealthy_checks = [check for check in self.health_checks.values() if not check.is_healthy]
        
        # Overall status determination
        if any(alert.severity in [AlertSeverity.CRITICAL, AlertSeverity.ERROR] for alert in active_alerts):
            overall_status = "critical"
        elif unhealthy_checks or any(alert.severity == AlertSeverity.WARNING for alert in active_alerts):
            overall_status = "warning"
        else:
            overall_status = "healthy"
        
        return {
            "overall_status": overall_status,
            "monitoring_active": self.is_running,
            "total_metrics": len(self.metric_definitions),
            "active_alerts": len(active_alerts),
            "unhealthy_checks": len(unhealthy_checks),
            "alert_details": [
                {
                    "name": alert.name,
                    "severity": alert.severity.value,
                    "last_triggered": alert.last_triggered.isoformat() if alert.last_triggered else None
                }
                for alert in active_alerts
            ],
            "health_check_details": [
                {
                    "name": check.name,
                    "consecutive_failures": check.consecutive_failures,
                    "last_check": check.last_check.isoformat() if check.last_check else None
                }
                for check in unhealthy_checks
            ],
            "last_updated": datetime.now().isoformat()
        }


# Factory function
def create_monitoring_framework(storage_path: str = "./monitoring_data") -> AutomatedMonitoringFramework:
    """Create an automated monitoring framework"""
    return AutomatedMonitoringFramework(storage_path)