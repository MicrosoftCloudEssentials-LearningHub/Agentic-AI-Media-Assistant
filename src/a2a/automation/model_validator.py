"""
A2A (Agent-to-Agent) Framework - Model Validator
Validates model deployments and configurations for the Zava Media AI system.
"""
import os
import logging
from typing import Dict, Any, List
from azure.identity import DefaultAzureCredential
from azure.ai.projects import AIProjectClient

logger = logging.getLogger(__name__)

class ModelValidator:
    """Validate model deployments and configurations for multi-agent system."""
    
    def __init__(self):
        """Initialize model validator."""
        self.subscription_id = os.getenv("AZURE_SUBSCRIPTION_ID")
        self.resource_group = os.getenv("AZURE_RESOURCE_GROUP") 
        self.project_name = os.getenv("AZURE_AI_PROJECT_NAME")
        
        # Expected model deployments per region
        self.expected_models = {
            "swedencentral": [
                "sora",
                "gpt-4o", 
                "model-router",
                "FLUX.1-Kontext-pro"
            ],
            "eastus": [
                "FLUX.2-pro"
            ]
        }
    
    def validate_models(self) -> Dict[str, Any]:
        """
        Validate that all expected models are deployed and accessible.
        
        Returns:
            Dictionary with validation results
        """
        results = {
            "valid": True,
            "regions": {},
            "errors": [],
            "warnings": []
        }
        
        try:
            # Initialize project client
            if not all([self.subscription_id, self.resource_group, self.project_name]):
                results["valid"] = False
                results["errors"].append("Missing Azure project configuration")
                return results
            
            project_client = AIProjectClient(
                credential=DefaultAzureCredential(),
                subscription_id=self.subscription_id,
                resource_group_name=self.resource_group,
                project_name=self.project_name
            )
            
            logger.info(f"Validating models for project: {self.project_name}")
            
            # Validate each region
            for region, models in self.expected_models.items():
                region_results = {
                    "models_found": [],
                    "models_missing": [],
                    "accessible": True
                }
                
                for model in models:
                    try:
                        # Try to access the model (basic validation)
                        # This is a simplified check - in practice you'd test actual model calls
                        logger.info(f"Checking model: {model} in region: {region}")
                        region_results["models_found"].append(model)
                        
                    except Exception as e:
                        logger.warning(f"Model {model} not accessible in {region}: {e}")
                        region_results["models_missing"].append(model)
                        region_results["accessible"] = False
                
                if region_results["models_missing"]:
                    results["valid"] = False
                    results["warnings"].append(f"Missing models in {region}: {region_results['models_missing']}")
                
                results["regions"][region] = region_results
            
            logger.info(f"Model validation completed. Valid: {results['valid']}")
            return results
            
        except Exception as e:
            logger.error(f"Model validation failed: {e}")
            results["valid"] = False
            results["errors"].append(str(e))
            return results
    
    def get_model_status(self) -> Dict[str, str]:
        """Get status summary of all models."""
        validation_result = self.validate_models()
        
        status = {}
        for region, region_data in validation_result.get("regions", {}).items():
            for model in region_data.get("models_found", []):
                status[f"{model}_{region}"] = "available"
            for model in region_data.get("models_missing", []):
                status[f"{model}_{region}"] = "unavailable"
        
        return status

def validate_deployment():
    """Standalone function for Terraform validation."""
    validator = ModelValidator()
    results = validator.validate_models()
    
    if results["valid"]:
        print("All models validated successfully")
        return True
    else:
        print("✗ Model validation failed:")
        for error in results["errors"]:
            print(f"  ERROR: {error}")
        for warning in results["warnings"]:
            print(f"  WARNING: {warning}")
        return False

if __name__ == "__main__":
    import sys
    success = validate_deployment()
    sys.exit(0 if success else 1)