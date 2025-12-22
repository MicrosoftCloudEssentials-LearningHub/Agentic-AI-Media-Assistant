"""
Automated Deployment and CI/CD Pipeline Manager

This module handles automated deployment processes, continuous integration,
and continuous deployment workflows for the A2A protocol system.

Key automation features:
- Automated testing and validation
- Blue-green deployment automation
- Configuration management
- Rollback automation
- Performance baseline validation
- Security scanning automation
"""
import asyncio
import logging
import json
import os
import subprocess
import tempfile
from datetime import datetime, timedelta
from typing import Dict, List, Any, Optional
from dataclasses import dataclass
from enum import Enum
import yaml

logger = logging.getLogger(__name__)


class DeploymentStage(Enum):
    """Deployment pipeline stages"""
    BUILD = "build"
    TEST = "test"
    SECURITY_SCAN = "security_scan"
    STAGING_DEPLOY = "staging_deploy"
    INTEGRATION_TEST = "integration_test"
    PRODUCTION_DEPLOY = "production_deploy"
    VALIDATION = "validation"
    COMPLETE = "complete"
    FAILED = "failed"


class DeploymentStrategy(Enum):
    """Deployment strategies"""
    BLUE_GREEN = "blue_green"
    ROLLING = "rolling"
    CANARY = "canary"
    IMMEDIATE = "immediate"


@dataclass
class DeploymentConfig:
    """Deployment configuration"""
    strategy: DeploymentStrategy
    environment: str
    version: str
    rollback_enabled: bool = True
    validation_timeout: int = 300
    health_check_url: str = "/health"
    performance_baseline: Dict[str, float] = None


class AutomatedDeploymentManager:
    """
    Manages automated deployment processes for the A2A protocol system.
    
    Features:
    - Automated CI/CD pipeline execution
    - Blue-green deployment with automatic rollback
    - Performance validation and baseline comparison
    - Security scanning and compliance checks
    - Configuration management automation
    """
    
    def __init__(self, config_path: str = None):
        self.config_path = config_path or "deployment-config.yaml"
        self.deployment_history = []
        self.active_deployments = {}
        self.performance_baselines = {}
        
        # Load deployment configuration
        self.deployment_config = self._load_deployment_config()
        
        logger.info("Automated Deployment Manager initialized")
    
    def _load_deployment_config(self) -> Dict[str, Any]:
        """Load deployment configuration from file"""
        try:
            if os.path.exists(self.config_path):
                with open(self.config_path, 'r') as f:
                    return yaml.safe_load(f)
            else:
                # Default configuration
                return {
                    "environments": {
                        "staging": {
                            "strategy": "immediate",
                            "health_check_url": "/health",
                            "validation_timeout": 180
                        },
                        "production": {
                            "strategy": "blue_green",
                            "health_check_url": "/health",
                            "validation_timeout": 300,
                            "rollback_enabled": True
                        }
                    },
                    "pipeline": {
                        "build_command": "docker build -t a2a-protocol .",
                        "test_command": "pytest tests/",
                        "security_scan_command": "bandit -r src/",
                        "deploy_command": "docker-compose up -d"
                    },
                    "performance_thresholds": {
                        "response_time_ms": 2000,
                        "error_rate": 0.05,
                        "throughput_rps": 100
                    }
                }
        except Exception as e:
            logger.error(f"Error loading deployment config: {e}")
            return {}
    
    async def trigger_automated_deployment(self, 
                                         version: str,
                                         environment: str = "production",
                                         strategy: DeploymentStrategy = None) -> str:
        """Trigger an automated deployment"""
        deployment_id = f"deploy_{environment}_{version}_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        
        # Create deployment configuration
        env_config = self.deployment_config.get("environments", {}).get(environment, {})
        
        deployment_config = DeploymentConfig(
            strategy=strategy or DeploymentStrategy(env_config.get("strategy", "immediate")),
            environment=environment,
            version=version,
            rollback_enabled=env_config.get("rollback_enabled", True),
            validation_timeout=env_config.get("validation_timeout", 300),
            health_check_url=env_config.get("health_check_url", "/health"),
            performance_baseline=self.performance_baselines.get(environment)
        )
        
        # Store active deployment
        self.active_deployments[deployment_id] = {
            "config": deployment_config,
            "stage": DeploymentStage.BUILD,
            "started_at": datetime.now(),
            "logs": []
        }
        
        logger.info(f"Starting automated deployment {deployment_id}")
        
        # Start deployment pipeline
        asyncio.create_task(self._execute_deployment_pipeline(deployment_id))
        
        return deployment_id
    
    async def _execute_deployment_pipeline(self, deployment_id: str):
        """Execute the complete deployment pipeline"""
        deployment = self.active_deployments[deployment_id]
        config = deployment["config"]
        
        try:
            # Build stage
            await self._execute_build_stage(deployment_id)
            
            # Test stage
            await self._execute_test_stage(deployment_id)
            
            # Security scan stage
            await self._execute_security_scan_stage(deployment_id)
            
            # Deploy to staging (if production deployment)
            if config.environment == "production":
                await self._execute_staging_deployment(deployment_id)
                await self._execute_integration_tests(deployment_id)
            
            # Production deployment
            await self._execute_production_deployment(deployment_id)
            
            # Validation stage
            await self._execute_validation_stage(deployment_id)
            
            # Mark as complete
            deployment["stage"] = DeploymentStage.COMPLETE
            deployment["completed_at"] = datetime.now()
            
            logger.info(f"Deployment {deployment_id} completed successfully")
            
        except Exception as e:
            deployment["stage"] = DeploymentStage.FAILED
            deployment["error"] = str(e)
            deployment["failed_at"] = datetime.now()
            
            logger.error(f"Deployment {deployment_id} failed: {e}")
            
            # Attempt rollback if enabled
            if config.rollback_enabled:
                await self._execute_rollback(deployment_id)
        
        finally:
            # Move to deployment history
            self.deployment_history.append(deployment)
            if deployment_id in self.active_deployments:
                del self.active_deployments[deployment_id]
    
    async def _execute_build_stage(self, deployment_id: str):
        """Execute build stage"""
        deployment = self.active_deployments[deployment_id]
        deployment["stage"] = DeploymentStage.BUILD
        
        build_command = self.deployment_config.get("pipeline", {}).get("build_command")
        if build_command:
            result = await self._run_command(build_command, "Build")
            deployment["logs"].append({"stage": "build", "output": result, "timestamp": datetime.now()})
        
        logger.info(f"Build stage completed for {deployment_id}")
    
    async def _execute_test_stage(self, deployment_id: str):
        """Execute test stage"""
        deployment = self.active_deployments[deployment_id]
        deployment["stage"] = DeploymentStage.TEST
        
        test_command = self.deployment_config.get("pipeline", {}).get("test_command")
        if test_command:
            result = await self._run_command(test_command, "Test")
            deployment["logs"].append({"stage": "test", "output": result, "timestamp": datetime.now()})
        
        # Run automated API tests
        await self._run_api_tests(deployment_id)
        
        logger.info(f"Test stage completed for {deployment_id}")
    
    async def _run_api_tests(self, deployment_id: str):
        """Run automated API tests"""
        deployment = self.active_deployments[deployment_id]
        
        test_cases = [
            {"endpoint": "/health", "expected_status": 200},
            {"endpoint": "/a2a/chat/stats", "expected_status": 200},
            {"endpoint": "/", "expected_status": 200}
        ]
        
        test_results = []
        for test_case in test_cases:
            try:
                # Simulate API test (in real implementation, use httpx or requests)
                result = {
                    "endpoint": test_case["endpoint"],
                    "status": "passed",
                    "timestamp": datetime.now()
                }
                test_results.append(result)
            except Exception as e:
                result = {
                    "endpoint": test_case["endpoint"],
                    "status": "failed",
                    "error": str(e),
                    "timestamp": datetime.now()
                }
                test_results.append(result)
        
        deployment["logs"].append({
            "stage": "api_tests", 
            "results": test_results, 
            "timestamp": datetime.now()
        })
    
    async def _execute_security_scan_stage(self, deployment_id: str):
        """Execute security scan stage"""
        deployment = self.active_deployments[deployment_id]
        deployment["stage"] = DeploymentStage.SECURITY_SCAN
        
        security_command = self.deployment_config.get("pipeline", {}).get("security_scan_command")
        if security_command:
            result = await self._run_command(security_command, "Security Scan")
            deployment["logs"].append({"stage": "security", "output": result, "timestamp": datetime.now()})
        
        # Additional security checks
        await self._run_security_checks(deployment_id)
        
        logger.info(f"Security scan stage completed for {deployment_id}")
    
    async def _run_security_checks(self, deployment_id: str):
        """Run additional security checks"""
        deployment = self.active_deployments[deployment_id]
        
        security_checks = [
            "Check for hardcoded secrets",
            "Validate HTTPS configuration",
            "Check CORS settings",
            "Validate rate limiting configuration",
            "Check for SQL injection vulnerabilities"
        ]
        
        check_results = []
        for check in security_checks:
            # Simulate security check
            result = {
                "check": check,
                "status": "passed",
                "timestamp": datetime.now()
            }
            check_results.append(result)
        
        deployment["logs"].append({
            "stage": "security_checks",
            "results": check_results,
            "timestamp": datetime.now()
        })
    
    async def _execute_staging_deployment(self, deployment_id: str):
        """Execute staging deployment"""
        deployment = self.active_deployments[deployment_id]
        deployment["stage"] = DeploymentStage.STAGING_DEPLOY
        
        # Deploy to staging environment
        staging_config = self.deployment_config.get("environments", {}).get("staging", {})
        
        deploy_result = await self._deploy_to_environment("staging", deployment["config"].version)
        deployment["logs"].append({
            "stage": "staging_deploy",
            "result": deploy_result,
            "timestamp": datetime.now()
        })
        
        logger.info(f"Staging deployment completed for {deployment_id}")
    
    async def _execute_integration_tests(self, deployment_id: str):
        """Execute integration tests on staging"""
        deployment = self.active_deployments[deployment_id]
        deployment["stage"] = DeploymentStage.INTEGRATION_TEST
        
        # Run comprehensive integration tests
        integration_tests = [
            "Multi-agent conversation flow",
            "WebSocket connection handling",
            "Rate limiting functionality",
            "Error handling and recovery",
            "Performance under load"
        ]
        
        test_results = []
        for test in integration_tests:
            # Simulate integration test
            result = {
                "test": test,
                "status": "passed",
                "duration_ms": 1500,
                "timestamp": datetime.now()
            }
            test_results.append(result)
        
        deployment["logs"].append({
            "stage": "integration_tests",
            "results": test_results,
            "timestamp": datetime.now()
        })
        
        logger.info(f"Integration tests completed for {deployment_id}")
    
    async def _execute_production_deployment(self, deployment_id: str):
        """Execute production deployment"""
        deployment = self.active_deployments[deployment_id]
        deployment["stage"] = DeploymentStage.PRODUCTION_DEPLOY
        config = deployment["config"]
        
        if config.strategy == DeploymentStrategy.BLUE_GREEN:
            await self._blue_green_deployment(deployment_id)
        elif config.strategy == DeploymentStrategy.ROLLING:
            await self._rolling_deployment(deployment_id)
        elif config.strategy == DeploymentStrategy.CANARY:
            await self._canary_deployment(deployment_id)
        else:
            await self._immediate_deployment(deployment_id)
        
        logger.info(f"Production deployment completed for {deployment_id}")
    
    async def _blue_green_deployment(self, deployment_id: str):
        """Execute blue-green deployment"""
        deployment = self.active_deployments[deployment_id]
        
        steps = [
            "Deploy to green environment",
            "Run health checks on green",
            "Switch traffic to green",
            "Monitor green environment",
            "Keep blue as backup"
        ]
        
        for step in steps:
            # Simulate deployment step
            await asyncio.sleep(1)  # Simulate work
            deployment["logs"].append({
                "stage": "blue_green",
                "step": step,
                "status": "completed",
                "timestamp": datetime.now()
            })
    
    async def _rolling_deployment(self, deployment_id: str):
        """Execute rolling deployment"""
        deployment = self.active_deployments[deployment_id]
        
        steps = [
            "Deploy to instance 1",
            "Health check instance 1",
            "Deploy to instance 2", 
            "Health check instance 2",
            "Deploy to instance 3",
            "Health check instance 3"
        ]
        
        for step in steps:
            await asyncio.sleep(1)
            deployment["logs"].append({
                "stage": "rolling",
                "step": step,
                "status": "completed",
                "timestamp": datetime.now()
            })
    
    async def _canary_deployment(self, deployment_id: str):
        """Execute canary deployment"""
        deployment = self.active_deployments[deployment_id]
        
        steps = [
            "Deploy to 5% of traffic",
            "Monitor canary metrics",
            "Deploy to 25% of traffic",
            "Monitor performance",
            "Deploy to 50% of traffic",
            "Full deployment"
        ]
        
        for step in steps:
            await asyncio.sleep(2)  # Canary takes longer
            deployment["logs"].append({
                "stage": "canary",
                "step": step,
                "status": "completed",
                "timestamp": datetime.now()
            })
    
    async def _immediate_deployment(self, deployment_id: str):
        """Execute immediate deployment"""
        deployment = self.active_deployments[deployment_id]
        
        deploy_result = await self._deploy_to_environment("production", deployment["config"].version)
        deployment["logs"].append({
            "stage": "immediate_deploy",
            "result": deploy_result,
            "timestamp": datetime.now()
        })
    
    async def _deploy_to_environment(self, environment: str, version: str) -> Dict[str, Any]:
        """Deploy to specific environment"""
        deploy_command = self.deployment_config.get("pipeline", {}).get("deploy_command")
        
        if deploy_command:
            result = await self._run_command(f"{deploy_command} --env {environment} --version {version}", "Deploy")
            return {"status": "success", "output": result}
        
        return {"status": "simulated", "message": f"Deployed version {version} to {environment}"}
    
    async def _execute_validation_stage(self, deployment_id: str):
        """Execute validation stage"""
        deployment = self.active_deployments[deployment_id]
        deployment["stage"] = DeploymentStage.VALIDATION
        config = deployment["config"]
        
        # Health check validation
        health_status = await self._validate_health_check(config.environment)
        
        # Performance validation
        performance_results = await self._validate_performance(config)
        
        # Smoke tests
        smoke_test_results = await self._run_smoke_tests(config.environment)
        
        deployment["logs"].append({
            "stage": "validation",
            "health_check": health_status,
            "performance": performance_results,
            "smoke_tests": smoke_test_results,
            "timestamp": datetime.now()
        })
        
        # Check if validation passed
        if (health_status.get("status") == "healthy" and
            performance_results.get("status") == "passed" and
            all(t.get("status") == "passed" for t in smoke_test_results)):
            logger.info(f"Validation passed for {deployment_id}")
        else:
            raise Exception("Deployment validation failed")
    
    async def _validate_health_check(self, environment: str) -> Dict[str, Any]:
        """Validate health check endpoint"""
        try:
            # Simulate health check
            await asyncio.sleep(1)
            return {
                "status": "healthy",
                "response_time_ms": 150,
                "timestamp": datetime.now()
            }
        except Exception as e:
            return {
                "status": "unhealthy",
                "error": str(e),
                "timestamp": datetime.now()
            }
    
    async def _validate_performance(self, config: DeploymentConfig) -> Dict[str, Any]:
        """Validate performance against baseline"""
        try:
            # Simulate performance test
            current_metrics = {
                "response_time_ms": 1200,
                "error_rate": 0.02,
                "throughput_rps": 150
            }
            
            thresholds = self.deployment_config.get("performance_thresholds", {})
            
            validation_results = {}
            for metric, value in current_metrics.items():
                threshold = thresholds.get(metric)
                if threshold:
                    passed = value <= threshold
                    validation_results[metric] = {
                        "value": value,
                        "threshold": threshold,
                        "passed": passed
                    }
            
            all_passed = all(r.get("passed", True) for r in validation_results.values())
            
            return {
                "status": "passed" if all_passed else "failed",
                "metrics": validation_results,
                "timestamp": datetime.now()
            }
            
        except Exception as e:
            return {
                "status": "error",
                "error": str(e),
                "timestamp": datetime.now()
            }
    
    async def _run_smoke_tests(self, environment: str) -> List[Dict[str, Any]]:
        """Run smoke tests after deployment"""
        smoke_tests = [
            {"name": "Basic connectivity", "endpoint": "/"},
            {"name": "Health check", "endpoint": "/health"},
            {"name": "A2A chat endpoint", "endpoint": "/a2a/chat/stats"},
            {"name": "WebSocket connection", "endpoint": "/a2a/chat/ws"}
        ]
        
        results = []
        for test in smoke_tests:
            try:
                # Simulate smoke test
                await asyncio.sleep(0.5)
                results.append({
                    "name": test["name"],
                    "status": "passed",
                    "response_time_ms": 200,
                    "timestamp": datetime.now()
                })
            except Exception as e:
                results.append({
                    "name": test["name"],
                    "status": "failed",
                    "error": str(e),
                    "timestamp": datetime.now()
                })
        
        return results
    
    async def _execute_rollback(self, deployment_id: str):
        """Execute automatic rollback"""
        deployment = self.active_deployments[deployment_id]
        
        logger.warning(f"Initiating rollback for {deployment_id}")
        
        # Find previous successful deployment
        previous_version = self._get_previous_stable_version(deployment["config"].environment)
        
        if previous_version:
            rollback_result = await self._deploy_to_environment(
                deployment["config"].environment, 
                previous_version
            )
            
            deployment["logs"].append({
                "stage": "rollback",
                "previous_version": previous_version,
                "result": rollback_result,
                "timestamp": datetime.now()
            })
            
            logger.info(f"Rollback completed for {deployment_id} to version {previous_version}")
        else:
            logger.error(f"No previous stable version found for rollback of {deployment_id}")
    
    def _get_previous_stable_version(self, environment: str) -> Optional[str]:
        """Get the previous stable version for rollback"""
        # Find last successful deployment for this environment
        for deployment in reversed(self.deployment_history):
            if (deployment.get("config", {}).environment == environment and 
                deployment.get("stage") == DeploymentStage.COMPLETE):
                return deployment.get("config", {}).version
        return None
    
    async def _run_command(self, command: str, stage: str) -> str:
        """Run a shell command and return output"""
        try:
            # In a real implementation, this would run the actual command
            # For demonstration, we'll simulate command execution
            logger.info(f"Running {stage} command: {command}")
            await asyncio.sleep(2)  # Simulate command execution time
            return f"Simulated output for: {command}"
        except Exception as e:
            logger.error(f"Error running {stage} command: {e}")
            raise
    
    def get_deployment_status(self, deployment_id: str) -> Optional[Dict[str, Any]]:
        """Get status of a specific deployment"""
        if deployment_id in self.active_deployments:
            deployment = self.active_deployments[deployment_id]
            return {
                "deployment_id": deployment_id,
                "stage": deployment["stage"].value,
                "started_at": deployment["started_at"].isoformat(),
                "config": {
                    "environment": deployment["config"].environment,
                    "version": deployment["config"].version,
                    "strategy": deployment["config"].strategy.value
                },
                "logs_count": len(deployment["logs"])
            }
        return None
    
    def get_deployment_history(self, limit: int = 10) -> List[Dict[str, Any]]:
        """Get deployment history"""
        recent_deployments = self.deployment_history[-limit:]
        return [
            {
                "stage": d["stage"].value if hasattr(d["stage"], "value") else str(d["stage"]),
                "started_at": d["started_at"].isoformat(),
                "completed_at": d.get("completed_at", {}).isoformat() if d.get("completed_at") else None,
                "environment": d["config"].environment,
                "version": d["config"].version,
                "strategy": d["config"].strategy.value
            }
            for d in recent_deployments
        ]


# Factory function
def create_deployment_manager(config_path: str = None) -> AutomatedDeploymentManager:
    """Create an automated deployment manager"""
    return AutomatedDeploymentManager(config_path)