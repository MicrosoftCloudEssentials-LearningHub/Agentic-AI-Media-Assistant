# Troubleshooting Guide - Overview

Costa Rica

[![GitHub](https://img.shields.io/badge/--181717?logo=github&logoColor=ffffff)](https://github.com/)
[brown9804](https://github.com/brown9804)

Last updated: 2025-12-11

----------

> This guide covers common issues you may encounter when deploying and running this Azure AI Media Assistantcls demo application.

<details>
<summary><b>Table of Content</b> (Click to expand)</summary>

- [Python Not Found](#python-not-found)
- [Virtual Environment Creation Failed](#virtual-environment-creation-failed)
- [Package Installation Failed](#package-installation-failed)
- [Not Logged into Azure CLI](#not-logged-into-azure-cli)
- [AAD Authentication Failed](#aad-authentication-failed)
- [Local Authorization Disabled Error](#local-authorization-disabled-error)
- [Connection Timeout](#connection-timeout)
- [CSV File Not Found](#csv-file-not-found)
- [CSV Parsing Error](#csv-parsing-error)
- [Environment File Missing](#environment-file-missing)
- [Failed to Authenticate to Cosmos DB](#failed-to-authenticate-to-cosmos-db)
- [Resource Already Exists](#resource-already-exists)
- [Insufficient Permissions](#insufficient-permissions)
- [Provider Configuration Error](#provider-configuration-error)
- [State Lock Error](#state-lock-error)
- [Enable Verbose Logging](#enable-verbose-logging)
- [Check Azure Service Health](#check-azure-service-health)
- [Clean Up and Retry](#clean-up-and-retry)
- [Still Having Issues?](#still-having-issues)

</details>

## Python Not Found

```
ERROR: Python is not installed or not in PATH
```

**Solution**: 

- Install Python 3.8+ from <https://www.python.org/downloads/>
- Ensure Python is added to your system PATH during installation
- Verify installation: `python --version`

## Virtual Environment Creation Failed

```
ERROR: Failed to create virtual environment
```

**Solution**: 

- Ensure you have write permissions to the `src` directory
- Try deleting existing `venv` folder: `Remove-Item -Recurse -Force venv`
- Check if `python -m venv` works manually: `python -m venv test_venv`
- On Windows, ensure your execution policy allows script execution

## Package Installation Failed

```
ERROR: Could not install packages due to an OSError
```

**Solution**:

- Update pip: `python -m pip install --upgrade pip`
- Clear pip cache: `pip cache purge`
- Try installing with `--no-cache-dir`: `pip install --no-cache-dir -r requirements.txt`
- For Windows + pandas issues, use pre-built wheels by ensuring `pandas>=2.2.2` in requirements.txt

## Not Logged into Azure CLI

```
ERROR: Please run 'az login' to setup account
```

**Solution**:

```powershell
# Login to Azure CLI
az login

# Verify you're logged in with the correct account
az account show

# If needed, set the correct subscription
az account set --subscription <subscription-id>
```

## AAD Authentication Failed

```
DefaultAzureCredential failed to retrieve a token
```

**Solution**:

1. Ensure you're logged into Azure CLI: `az login`
2. Check your account has proper permissions
3. Verify the resource exists and you have access
4. Try clearing Azure credentials cache: `az account clear` then `az login` again

## Local Authorization Disabled Error

```
ERROR: Local Authorization is disabled. Use an AAD token to authorize all requests.
```

> This error occurs when Cosmos DB requires Azure Active Directory (AAD) authentication instead of key-based authentication.

**Common Causes and Solutions**:

- Not logged into Azure CLI

```powershell
# Login to Azure CLI
az login

# Verify you're logged in with the correct account
az account show

# If needed, set the correct subscription
az account set --subscription <subscription-id>
```

> After logging in, try running the script again.

- Public Network Access Disabled

> If your Cosmos DB has public network access disabled, your local machine or Codespace VM cannot connect.

**Solution via Azure Portal**:

- Navigate to your Cosmos DB account in the Azure portal
- Select **Networking** from the Settings menu
- Ensure **Public network access** is set to **All networks**
- Click **Save**
- Wait a few minutes for the change to propagate
- Try running the script again

**Solution via Azure CLI**:

```powershell
az cosmosdb update \
  --name <cosmos-account-name> \
  --resource-group <resource-group-name> \
  --enable-public-network true
```

- Insufficient Permissions: Your Azure account needs appropriate role assignments on the Cosmos DB account.

**Required roles**:

- `Cosmos DB Built-in Data Contributor` (for read/write access)
- Or `Contributor` at the resource group level

**Solution via Azure CLI**:

```powershell
# Get your user object ID
$userId = (az ad signed-in-user show --query id -o tsv)

# Assign Cosmos DB Data Contributor role
az cosmosdb sql role assignment create \
  --account-name <cosmos-account-name> \
  --resource-group <resource-group-name> \
  --role-definition-id 00000000-0000-0000-0000-000000000002 \
  --principal-id $userId \
  --scope "/"
```

## Connection Timeout

```
ERROR: Request timeout
```

**Solution**:

- Check your network connection
- Verify Cosmos DB firewall settings allow your IP address
- Ensure public network access is enabled (see above)
- Check if Azure services are experiencing outages: <https://status.azure.com/>

## CSV File Not Found

```
WARNING: CSV data file not found at data/updated_product_catalog(in).csv
```

**Solution**: Download or place the product catalog CSV file in the `src/data/` directory:

```bash
curl -o src/data/updated_product_catalog(in).csv https://raw.githubusercontent.com/microsoft/TechWorkshop-L300-AI-Apps-and-agents/main/src/data/updated_product_catalog(in).csv
```

## CSV Parsing Error

```
ERROR: Error tokenizing data. C error: Expected X fields, saw Y
```

**Solution**:

- Ensure CSV fields with commas are properly quoted
- Check for special characters or encoding issues
- Verify the CSV has the correct number of columns (6): ProductID, ProductName, ProductCategory, ProductDescription, Price, ImageUrl
- Try opening the CSV in a text editor to check for formatting issues

## Environment File Missing

```
ERROR: .env file not found
```

**Solution**:

```bash
# Run Terraform to generate the .env file
cd terraform-infrastructure
terraform apply -auto-approve
```

## Failed to Authenticate to Cosmos DB

```
ERROR: Failed to authenticate to Cosmos DB using DefaultAzureCredential and no valid COSMOS_DB_KEY was provided
```

**Solution**: 

- Ensure your `.env` file is properly generated with correct keys
- Run `terraform apply` again if needed
- Check that `COSMOS_DB_ENDPOINT` and `COSMOS_DB_KEY` are set correctly in `.env`
- The script will automatically try AAD authentication first, then fall back to key-based auth

## Resource Already Exists

```
ERROR: A resource with the ID already exists
```

**Solution**:

- Import the existing resource: `terraform import <resource_type>.<name> <azure_resource_id>`
- Or destroy and recreate: `terraform destroy` then `terraform apply`
- Check for resources in other resource groups with the same name

## Insufficient Permissions

```
ERROR: The client does not have authorization to perform action
```

**Solution**:

- Ensure your Azure account has `Contributor` or `Owner` role on the subscription or resource group
- Check if specific Azure policies are blocking resource creation
- Contact your Azure administrator to grant necessary permissions

## Provider Configuration Error

```
ERROR: Error configuring the backend "azurerm"
```

**Solution**:

- Verify your Azure credentials are configured: `az login`
- Check that the specified subscription exists and you have access
- Ensure the backend storage account and container exist (if using remote state)

## State Lock Error

```
ERROR: Error acquiring the state lock
```

**Solution**:

```bash
# Force unlock (use with caution)
terraform force-unlock <lock-id>
```

> Only force-unlock if you're sure no other Terraform process is running.

## Enable Verbose Logging

> For more detailed error information:

**Azure CLI**:

```powershell
az <command> --debug
```

**Python Scripts**: Set environment variable before running.

```powershell
$env:AZURE_LOG_LEVEL = "DEBUG"
python pipelines/script.py
```

**Terraform**:

```bash
export TF_LOG=DEBUG
terraform apply
```

## Check Azure Service Health

> If experiencing unexpected issues, check [Azure service status](https://status.azure.com/)

## Clean Up and Retry

> Sometimes a clean slate helps:

```bash
# Clean Python environment
Remove-Item -Recurse -Force venv
python -m venv venv

# Clean Terraform state (use with caution)
terraform destroy
Remove-Item -Recurse -Force .terraform
terraform init
terraform apply
```

## Still Having Issues?

> If you continue experiencing problems:

1. Check the [GitHub repository issues](https://github.com/MicrosoftCloudEssentials-LearningHub/Agentic-DevOps-AI-Shopping/issues)
2. Review Azure documentation for specific services
3. Enable detailed logging as described above
4. Collect error messages, logs, and configuration details
5. Create a new issue with detailed information about your problem

<!-- START BADGE -->
<div align="center">
  <img src="https://img.shields.io/badge/Total%20views-1696-limegreen" alt="Total views">
  <p>Refresh Date: 2025-12-04</p>
</div>
<!-- END BADGE -->
