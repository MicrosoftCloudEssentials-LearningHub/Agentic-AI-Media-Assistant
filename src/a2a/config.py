"""
Configuration Management for A2A Protocol Implementation

This module provides configuration management for the enhanced A2A shopping assistant,
including environment variable handling, server setup, and integration options.

Key frameworks and libraries used:
- Pydantic: Data validation and settings management library with built-in support for
  environment variables, type validation, and configuration file parsing
- Python OS: Built-in operating system interface for accessing environment variables
- Python Logging: Built-in logging framework for application monitoring and debugging
- Python Enums: Enumeration support for defining configuration choices and modes
- Type Hints: Python typing system for better IDE support and code documentation
"""
import os
import logging
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field, validator
from enum import Enum


class LogLevel(str, Enum):
    """Logging levels"""
    DEBUG = "DEBUG"
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"


class ServerMode(str, Enum):
    """Server operation modes"""
    LEGACY = "legacy"  # Original multi-agent implementation
    A2A = "a2a"       # A2A protocol implementation
    HYBRID = "hybrid"  # Both legacy and A2A endpoints


class A2AConfig(BaseModel):
    """Configuration for A2A protocol server"""
    
    # Server Configuration
    host: str = Field(default="localhost", env="A2A_HOST")
    port: int = Field(default=8001, env="A2A_PORT")
    debug: bool = Field(default=False, env="A2A_DEBUG")
    
    # Server Mode
    mode: ServerMode = Field(default=ServerMode.HYBRID, env="A2A_MODE")
    
    # CORS Configuration
    cors_enabled: bool = Field(default=True, env="A2A_CORS_ENABLED")
    allowed_origins: List[str] = Field(default=["*"], env="A2A_ALLOWED_ORIGINS")
    
    # Logging Configuration
    log_level: LogLevel = Field(default=LogLevel.INFO, env="A2A_LOG_LEVEL")
    log_file: Optional[str] = Field(default=None, env="A2A_LOG_FILE")
    
    # Event Queue Configuration
    event_queue_size: int = Field(default=1000, env="A2A_EVENT_QUEUE_SIZE")
    event_ttl_seconds: int = Field(default=3600, env="A2A_EVENT_TTL_SECONDS")
    
    # Task Configuration
    max_concurrent_tasks: int = Field(default=100, env="A2A_MAX_CONCURRENT_TASKS")
    task_timeout_seconds: int = Field(default=300, env="A2A_TASK_TIMEOUT_SECONDS")
    
    # Agent Configuration
    enable_all_agents: bool = Field(default=True, env="A2A_ENABLE_ALL_AGENTS")
    enabled_agents: List[str] = Field(
        default=["interior_design", "inventory", "customer_loyalty", "cart_management", "cora"],
        env="A2A_ENABLED_AGENTS"
    )
    
    # Integration Configuration
    integrate_with_legacy: bool = Field(default=True, env="A2A_INTEGRATE_LEGACY")
    legacy_websocket_path: str = Field(default="/ws", env="A2A_LEGACY_WS_PATH")
    
    # Static Files
    static_files_enabled: bool = Field(default=True, env="A2A_STATIC_FILES_ENABLED")
    static_files_dir: str = Field(default="static", env="A2A_STATIC_FILES_DIR")
    
    # Health Check Configuration
    health_check_enabled: bool = Field(default=True, env="A2A_HEALTH_CHECK_ENABLED")
    health_check_path: str = Field(default="/health", env="A2A_HEALTH_CHECK_PATH")
    
    @validator('enabled_agents', pre=True)
    def parse_enabled_agents(cls, v):
        """Parse enabled agents from string or list"""
        if isinstance(v, str):
            return [agent.strip() for agent in v.split(",") if agent.strip()]
        return v
    
    @validator('allowed_origins', pre=True)
    def parse_allowed_origins(cls, v):
        """Parse allowed origins from string or list"""
        if isinstance(v, str):
            return [origin.strip() for origin in v.split(",") if origin.strip()]
        return v
    
    @classmethod
    def from_env(cls) -> "A2AConfig":
        """Create configuration from environment variables"""
        return cls()
    
    def get_static_files_path(self) -> str:
        """Get absolute path to static files directory"""
        if os.path.isabs(self.static_files_dir):
            return self.static_files_dir
        
        # Relative to a2a directory
        a2a_dir = os.path.dirname(os.path.abspath(__file__))
        return os.path.join(a2a_dir, self.static_files_dir)


class ZavaConfig(BaseModel):
    """Configuration for existing Zava application"""
    
    # Azure OpenAI Configuration
    gpt_endpoint: Optional[str] = Field(default=None, env="gpt_endpoint")
    gpt_deployment: Optional[str] = Field(default=None, env="gpt_deployment")
    gpt_api_version: Optional[str] = Field(default=None, env="gpt_api_version")
    gpt_api_key: Optional[str] = Field(default=None, env="gpt_api_key")
    
    # Agent Configuration
    interior_designer: Optional[str] = Field(default=None, env="interior_designer")
    inventory_agent: Optional[str] = Field(default=None, env="inventory_agent")
    customer_loyalty: Optional[str] = Field(default=None, env="customer_loyalty")
    cart_manager: Optional[str] = Field(default=None, env="cart_manager")
    cora: Optional[str] = Field(default=None, env="cora")
    
    # Azure AI Configuration
    azure_ai_agent_endpoint: Optional[str] = Field(default=None, env="AZURE_AI_AGENT_ENDPOINT")
    azure_ai_project_endpoint: Optional[str] = Field(default=None, env="AZURE_AI_PROJECT_ENDPOINT")
    
    # Feature Flags
    use_multi_agent: bool = Field(default=True, env="USE_MULTI_AGENT")
    
    # Customer Configuration
    customer_id: str = Field(default="CUST001", env="CUSTOMER_ID")
    
    @classmethod
    def from_env(cls) -> "ZavaConfig":
        """Create configuration from environment variables"""
        return cls()
    
    def has_remote_agents(self) -> bool:
        """Check if remote agent configuration is available"""
        return bool(
            self.azure_ai_agent_endpoint and
            any([
                self.interior_designer,
                self.inventory_agent,
                self.customer_loyalty,
                self.cart_manager,
                self.cora
            ])
        )
    
    def has_gpt_config(self) -> bool:
        """Check if GPT configuration is available"""
        return bool(
            self.gpt_endpoint and
            self.gpt_deployment and
            self.gpt_api_version
        )


class IntegratedConfig(BaseModel):
    """Integrated configuration for the enhanced shopping assistant"""
    
    a2a: A2AConfig
    zava: ZavaConfig
    
    @classmethod
    def from_env(cls) -> "IntegratedConfig":
        """Create integrated configuration from environment variables"""
        return cls(
            a2a=A2AConfig.from_env(),
            zava=ZavaConfig.from_env()
        )
    
    def validate_configuration(self) -> List[str]:
        """Validate configuration and return list of warnings/errors"""
        warnings = []
        
        # Check Zava configuration
        if not self.zava.has_gpt_config():
            warnings.append("GPT configuration incomplete - handoff service may not work")
        
        if not self.zava.has_remote_agents():
            warnings.append("Remote agent configuration incomplete - using local agents only")
        
        # Check A2A configuration
        if self.a2a.mode == ServerMode.A2A and not self.a2a.enable_all_agents:
            warnings.append("A2A mode enabled but not all agents are enabled")
        
        if self.a2a.static_files_enabled and not os.path.exists(self.a2a.get_static_files_path()):
            warnings.append(f"Static files directory not found: {self.a2a.get_static_files_path()}")
        
        return warnings
    
    def get_effective_agents(self) -> List[str]:
        """Get list of effectively enabled agents"""
        if not self.a2a.enable_all_agents:
            return self.a2a.enabled_agents
        
        # Return all available agents
        available_agents = []
        agent_env_vars = {
            "interior_design": self.zava.interior_designer,
            "inventory": self.zava.inventory_agent,
            "customer_loyalty": self.zava.customer_loyalty,
            "cart_management": self.zava.cart_manager,
            "cora": self.zava.cora
        }
        
        for agent, env_var in agent_env_vars.items():
            if env_var:  # Agent is configured
                available_agents.append(agent)
        
        return available_agents or ["cora"]  # Fallback to cora


def setup_logging(config: A2AConfig) -> None:
    """Setup logging configuration"""
    log_format = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    
    logging_config = {
        "level": getattr(logging, config.log_level.value),
        "format": log_format
    }
    
    if config.log_file:
        logging_config["filename"] = config.log_file
        logging_config["filemode"] = "a"
    
    logging.basicConfig(**logging_config)
    
    # Set specific logger levels
    if config.debug:
        logging.getLogger("a2a").setLevel(logging.DEBUG)
        logging.getLogger("app").setLevel(logging.DEBUG)
        logging.getLogger("services").setLevel(logging.DEBUG)
    else:
        # Reduce noise from external libraries
        logging.getLogger("httpx").setLevel(logging.WARNING)
        logging.getLogger("azure").setLevel(logging.WARNING)
        logging.getLogger("openai").setLevel(logging.WARNING)


def load_env_file(env_file: str = ".env") -> bool:
    """Load environment variables from file"""
    try:
        from dotenv import load_dotenv
        return load_dotenv(env_file)
    except ImportError:
        logging.warning("python-dotenv not available, skipping .env file loading")
        return False


def get_configuration(env_file: Optional[str] = None) -> IntegratedConfig:
    """
    Load and return the integrated configuration.
    
    Args:
        env_file: Path to .env file (optional)
    
    Returns:
        IntegratedConfig: Loaded configuration
    """
    # Load environment file if specified
    if env_file and os.path.exists(env_file):
        load_env_file(env_file)
    elif os.path.exists(".env"):
        load_env_file(".env")
    
    # Create configuration
    config = IntegratedConfig.from_env()
    
    # Setup logging
    setup_logging(config.a2a)
    
    # Validate and log warnings
    warnings = config.validate_configuration()
    if warnings:
        logger = logging.getLogger(__name__)
        logger.warning("Configuration validation warnings:")
        for warning in warnings:
            logger.warning(f"  - {warning}")
    
    return config


# Global configuration instance
_config: Optional[IntegratedConfig] = None


def get_global_config() -> IntegratedConfig:
    """Get or create global configuration instance"""
    global _config
    if _config is None:
        _config = get_configuration()
    return _config


def set_global_config(config: IntegratedConfig) -> None:
    """Set global configuration instance"""
    global _config
    _config = config