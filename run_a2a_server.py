"""
A2A Protocol Test Runner

This script provides an easy way to test and run the A2A (Agent-to-Agent) protocol
server for development and validation purposes.

Key frameworks and technologies used:
- Uvicorn: ASGI web server implementation with support for HTTP/1.1 and WebSockets,
  designed for high-performance async Python web applications
- AsyncIO: Python's asynchronous I/O framework enabling concurrent execution
  without threading, perfect for handling multiple agent communications
- Python Logging: Built-in logging system for monitoring server startup and operations
- Pathlib: Modern object-oriented filesystem path handling library
"""
import uvicorn
import asyncio
import logging
from pathlib import Path

from src.a2a.config import load_config, A2AMode
from src.a2a.main import create_app

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

async def test_a2a_protocol():
    """Test the A2A protocol implementation"""
    try:
        # Load configuration
        config = load_config()
        logger.info(f"Loaded configuration with mode: {config.a2a.mode}")
        
        # Create the application
        app = create_app()
        logger.info("Application created successfully")
        
        # Test agent initialization
        if hasattr(app.state, 'coordinator'):
            coordinator = app.state.coordinator
            agents = coordinator.get_available_agents()
            logger.info(f" Available agents: {list(agents.keys())}")
            
            # Test each agent
            for agent_id, agent_info in agents.items():
                logger.info(f"  - {agent_id}: {agent_info.get('description', 'No description')}")
        
        # Start server for testing
        config_obj = uvicorn.Config(
            app=app,
            host="localhost",
            port=8000,
            log_level="info",
            reload=False
        )
        server = uvicorn.Server(config_obj)
        
        logger.info(" Starting A2A protocol server on http://localhost:8000")
        logger.info("   - A2A Chat: http://localhost:8000/a2a/chat")
        logger.info("   - A2A API: http://localhost:8000/a2a/api/docs")
        logger.info("   - Legacy Chat: http://localhost:8000/chat")
        logger.info("   - Health: http://localhost:8000/health")
        
        await server.serve()
        
    except Exception as e:
        logger.error(f"❌ Error testing A2A protocol: {e}")
        import traceback
        logger.error(traceback.format_exc())
        raise

if __name__ == "__main__":
    asyncio.run(test_a2a_protocol())
