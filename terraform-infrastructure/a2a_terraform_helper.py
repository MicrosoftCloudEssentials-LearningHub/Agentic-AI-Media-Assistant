"""
Terraform A2A Integration Helper

This script helps integrate the A2A automation framework with Terraform deployment.
It validates that all A2A components are ready and provides automation status.
"""
import os
import sys
import json
import subprocess
from pathlib import Path


def check_a2a_framework():
    """Check if A2A framework is properly deployed"""
    a2a_path = Path(__file__).parent.parent / "src" / "a2a"
    
    required_components = [
        "automation/process_manager.py",
        "automation/deployment_manager.py", 
        "automation/test_framework.py",
        "automation/monitoring_framework.py",
        "automated_main.py",
        "main.py",
        "config.py"
    ]
    
    status = {
        "a2a_path": str(a2a_path),
        "components_status": {},
        "missing_components": [],
        "ready": True
    }
    
    # Check if A2A directory exists
    if not a2a_path.exists():
        status["ready"] = False
        status["error"] = f"A2A framework directory not found: {a2a_path}"
        return status
    
    # Check each component
    for component in required_components:
        component_path = a2a_path / component
        exists = component_path.exists()
        status["components_status"][component] = exists
        
        if not exists:
            status["missing_components"].append(component)
            status["ready"] = False
    
    # Check if automation directories exist
    automation_dirs = ["automation", "server", "agent", "api"]
    for dir_name in automation_dirs:
        dir_path = a2a_path / dir_name
        if dir_path.exists():
            status["components_status"][f"{dir_name}/"] = True
        else:
            status["components_status"][f"{dir_name}/"] = False
            status["missing_components"].append(f"{dir_name}/")
            status["ready"] = False
    
    return status


def get_terraform_outputs():
    """Get relevant Terraform outputs for A2A integration"""
    try:
        # Get terraform output
        result = subprocess.run(
            ["terraform", "output", "-json"],
            capture_output=True,
            text=True,
            cwd=Path(__file__).parent
        )
        
        if result.returncode == 0:
            return json.loads(result.stdout)
        else:
            return {"error": f"Terraform output failed: {result.stderr}"}
            
    except Exception as e:
        return {"error": f"Could not get terraform outputs: {e}"}


def create_a2a_terraform_config():
    """Create A2A configuration from Terraform outputs"""
    tf_outputs = get_terraform_outputs()
    
    if "error" in tf_outputs:
        print(f"Warning: {tf_outputs['error']}")
        return
    
    config = {
        "# A2A Terraform Integration Configuration": "",
        "A2A_TERRAFORM_MANAGED": "true",
        "A2A_DEPLOYMENT_MODE": "terraform"
    }
    
    # Add relevant outputs if available
    if "web_app_url" in tf_outputs:
        config["BASE_APP_URL"] = tf_outputs["web_app_url"]["value"]
    
    if "application_insights_connection_string" in tf_outputs:
        config["APPLICATION_INSIGHTS_CONNECTION_STRING"] = tf_outputs["application_insights_connection_string"]["value"]
    
    if "resource_group_name" in tf_outputs:
        config["AZURE_RESOURCE_GROUP"] = tf_outputs["resource_group_name"]["value"]
    
    # Write configuration
    a2a_path = Path(__file__).parent.parent / "src" / "a2a"
    if a2a_path.exists():
        config_file = a2a_path / ".env_terraform"
        
        with open(config_file, "w") as f:
            for key, value in config.items():
                if key.startswith("#"):
                    f.write(f"{key}\n")
                else:
                    f.write(f"{key}={value}\n")
        
        print(f"A2A Terraform configuration created: {config_file}")
    else:
        print(f"Warning: A2A directory not found: {a2a_path}")


def main():
    """Main function for Terraform integration"""
    print("A2A Terraform Integration Helper")
    print("=" * 50)
    
    # Check A2A framework status
    status = check_a2a_framework()
    
    print(f"A2A Framework Path: {status['a2a_path']}")
    print(f"Framework Ready: {'YES' if status['ready'] else 'NO'}")
    
    if not status['ready']:
        print("\nMissing A2A Components:")
        for component in status['missing_components']:
            print(f"  - {component}")
        print("\nPlease ensure the A2A automation framework is fully deployed")
        sys.exit(1)
    
    print("\nA2A Framework Status:")
    for component, exists in status['components_status'].items():
        status_icon = "[OK]" if exists else "[MISSING]"
        print(f"  {status_icon} {component}")
    
    # Create Terraform integration config
    print("\nCreating A2A Terraform configuration...")
    create_a2a_terraform_config()
    
    # Output status for Terraform
    terraform_status = {
        "a2a_ready": status['ready'],
        "components_count": len([c for c in status['components_status'].values() if c]),
        "missing_count": len(status['missing_components'])
    }
    
    print(f"\nTerraform Integration Status:")
    print(json.dumps(terraform_status, indent=2))
    
    if status['ready']:
        print("\nA2A automation framework is ready for Terraform deployment!")
        print("Run 'terraform apply' to deploy the complete automated system")
    else:
        print("\nWARNING: A2A framework needs setup before Terraform deployment")
        print("See src/a2a/automation/README.md for setup instructions")


if __name__ == "__main__":
    main()