"""Cost Analyst built-in agent — Azure cost estimation by t-shirt size.

Uses the Azure Retail Prices API (https://prices.azure.com/api/retail/prices)
combined with ARM resource metadata to estimate costs at three consumption
tiers: Small, Medium, and Large.

Invoked via: az prototype analyze --costs
"""

import json
import logging
from typing import Any

import requests

from azext_prototype.agents.base import BaseAgent, AgentCapability, AgentContext, AgentContract
from azext_prototype.ai.provider import AIMessage, AIResponse

logger = logging.getLogger(__name__)

# Azure Retail Prices REST endpoint (public, no auth required)
RETAIL_PRICES_API = "https://prices.azure.com/api/retail/prices"


class CostAnalystAgent(BaseAgent):
    """Estimate Azure costs for the current architecture at S/M/L tiers."""

    _temperature = 0.0
    _max_tokens = 8192
    _include_templates = False
    _include_standards = False
    _keywords = [
        "cost", "price", "pricing", "budget", "estimate",
        "spend", "expense", "sku", "t-shirt", "tshirt",
        "consumption", "billing", "retail",
    ]
    _keyword_weight = 0.12
    _contract = AgentContract(
        inputs=["architecture"],
        outputs=["cost_estimate"],
        delegates_to=[],
    )

    def __init__(self):
        super().__init__(
            name="cost-analyst",
            description=(
                "Analyze architecture to estimate Azure costs at Small, "
                "Medium, and Large t-shirt sizes using Azure Retail Prices API"
            ),
            capabilities=[AgentCapability.COST_ANALYSIS, AgentCapability.ANALYZE],
            constraints=[
                "Always cite the Azure Retail Prices API data used for estimates",
                "Clearly define what Small, Medium, and Large mean for each service",
                "Show monthly cost per component and a total",
                "Flag services where pricing model is complex (e.g., consumption-based)",
                "Include a disclaimer that these are estimates based on list prices",
            ],
            system_prompt=COST_ANALYST_PROMPT,
        )

    def execute(self, context: AgentContext, task: str) -> AIResponse:
        """Execute cost analysis.

        1. Ask the AI to extract Azure service components from the architecture.
        2. Query Azure Retail Prices API for each component.
        3. Feed pricing data back to the AI to produce the t-shirt size report.
        """
        messages = self.get_system_messages()
        messages.extend(context.conversation_history)

        # Step 1: Extract components from the architecture
        extraction_task = (
            "From the following architecture, list every Azure service that "
            "will be provisioned. For each service, provide:\n"
            "- serviceName (e.g., 'Azure App Service')\n"
            "- armResourceType (e.g., 'Microsoft.Web/sites')\n"
            "- skuSmall, skuMedium, skuLarge (appropriate SKU names)\n"
            "- meterName hint (for Retail Prices API filtering)\n"
            "- region\n\n"
            "Respond ONLY with a JSON array. No markdown, no explanation.\n\n"
            f"{task}"
        )
        messages.append(AIMessage(role="user", content=extraction_task))

        extraction_response = context.ai_provider.chat(
            messages, temperature=0.0, max_tokens=4096,
        )

        # Step 2: Parse components and query pricing
        components = self._parse_components(extraction_response.content)
        pricing_data = self._fetch_pricing(components, context)

        # Step 3: Generate the cost report
        report_messages = self.get_system_messages()
        report_messages.extend(context.conversation_history)
        report_messages.append(AIMessage(
            role="user",
            content=(
                "Generate a detailed cost estimation report using this pricing data.\n\n"
                f"## Architecture\n{task}\n\n"
                f"## Azure Retail Prices Data\n```json\n{json.dumps(pricing_data, indent=2)}\n```\n\n"
                "Create a table with columns: Service | Small | Medium | Large\n"
                "Show monthly costs. Include a total row.\n"
                "Define what Small/Medium/Large means for each service.\n"
                "Add notes about consumption-based services where exact costs depend on usage."
            ),
        ))

        response = context.ai_provider.chat(
            report_messages, temperature=0.0, max_tokens=8192,
        )

        # Post-response governance check
        warnings = self.validate_response(response.content)
        if warnings:
            for w in warnings:
                logger.warning("Governance: %s", w)
            block = "\n\n---\n⚠ **Governance warnings:**\n" + "\n".join(
                f"- {w}" for w in warnings
            )
            response = AIResponse(
                content=response.content + block,
                model=response.model,
                usage=response.usage,
                finish_reason=response.finish_reason,
            )
        return response

    def _parse_components(self, ai_output: str) -> list[dict]:
        """Parse the AI's JSON component list, tolerating markdown fences."""
        text = ai_output.strip()
        # Strip markdown code fences if present
        if text.startswith("```"):
            lines = text.split("\n")
            lines = [line for line in lines if not line.strip().startswith("```")]
            text = "\n".join(lines)

        try:
            components = json.loads(text)
            if isinstance(components, list):
                return components
        except json.JSONDecodeError:
            logger.warning("Could not parse component list from AI; using empty list.")

        return []

    def _fetch_pricing(
        self,
        components: list[dict],
        context: AgentContext,
    ) -> list[dict]:
        """Query Azure Retail Prices API for each component's SKUs."""
        region = context.project_config.get("project", {}).get("location", "eastus")
        pricing_results = []

        for component in components:
            service_name = component.get("serviceName", "unknown")
            arm_type = component.get("armResourceType", "")
            meter_hint = component.get("meterName", "")

            for size_label in ("Small", "Medium", "Large"):
                sku_key = f"sku{size_label}"
                sku = component.get(sku_key, "")
                if not sku:
                    continue

                price = self._query_retail_price(
                    arm_type=arm_type,
                    sku_name=sku,
                    meter_hint=meter_hint,
                    region=region,
                )

                pricing_results.append({
                    "service": service_name,
                    "size": size_label,
                    "sku": sku,
                    "region": region,
                    "retailPrice": price.get("retailPrice"),
                    "unitOfMeasure": price.get("unitOfMeasure", ""),
                    "meterName": price.get("meterName", ""),
                    "currencyCode": price.get("currencyCode", "USD"),
                })

        return pricing_results

    def _query_retail_price(
        self,
        arm_type: str,
        sku_name: str,
        meter_hint: str,
        region: str,
    ) -> dict[str, Any]:
        """Query a single price point from Azure Retail Prices API."""
        # Build OData filter
        filters = [
            f"armRegionName eq '{region}'",
            "priceType eq 'Consumption'",
        ]
        if arm_type:
            # Map ARM type to service family where possible
            filters.append(f"serviceFamily eq '{self._arm_to_family(arm_type)}'")
        if sku_name:
            filters.append(f"skuName eq '{sku_name}'")

        params: dict[str, str] = {
            "$filter": " and ".join(filters),
            "$top": "1",
        }

        try:
            resp = requests.get(RETAIL_PRICES_API, params=params, timeout=10)
            resp.raise_for_status()
            data = resp.json()
            items = data.get("Items", [])
            if items:
                return items[0]
        except Exception as e:
            logger.warning("Retail Prices API error for %s/%s: %s", arm_type, sku_name, e)

        return {"retailPrice": None, "unitOfMeasure": "N/A"}

    @staticmethod
    def _arm_to_family(arm_type: str) -> str:
        """Best-effort mapping from ARM resource type to service family."""
        mapping = {
            "Microsoft.Web": "Compute",
            "Microsoft.Compute": "Compute",
            "Microsoft.ContainerService": "Compute",
            "Microsoft.App": "Compute",
            "Microsoft.Sql": "Databases",
            "Microsoft.DBforPostgreSQL": "Databases",
            "Microsoft.DBforMySQL": "Databases",
            "Microsoft.DocumentDB": "Databases",
            "Microsoft.Cache": "Databases",
            "Microsoft.Storage": "Storage",
            "Microsoft.Network": "Networking",
            "Microsoft.Cdn": "Networking",
            "Microsoft.KeyVault": "Security",
            "Microsoft.CognitiveServices": "AI + Machine Learning",
            "Microsoft.Search": "AI + Machine Learning",
            "Microsoft.EventHub": "Integration",
            "Microsoft.ServiceBus": "Integration",
            "Microsoft.EventGrid": "Integration",
            "Microsoft.SignalRService": "Web",
            "Microsoft.Monitor": "Management and Governance",
            "Microsoft.Insights": "Management and Governance",
        }
        provider = arm_type.split("/")[0] if "/" in arm_type else arm_type
        return mapping.get(provider, "Compute")



COST_ANALYST_PROMPT = """You are an expert Azure cost analyst.

Your job is to analyze an Azure architecture and produce accurate cost estimates
at three t-shirt sizes: **Small**, **Medium**, and **Large**.

## T-Shirt Size Definitions

For each Azure service, define what Small/Medium/Large means in terms of:
- SKU / pricing tier
- Expected throughput / capacity
- DTU / vCore / RU (where applicable)
- Storage capacity

General guidance:
- **Small**: Dev/test, minimal traffic, lowest viable SKU
- **Medium**: Production-ready, moderate load, standard SKU
- **Large**: High-scale production, premium SKU, geo-redundancy

## Cost Estimation Rules

1. Use Azure Retail Prices API data when provided.
2. Show **monthly** costs (assume 730 hours/month for compute).
3. For consumption-based services (Functions, Logic Apps, Event Grid), estimate
   based on reasonable usage assumptions and state those assumptions.
4. Include data transfer costs where significant.
5. Show totals per t-shirt size.
6. Use USD unless the user specifies otherwise.

## Output Format

### Cost Summary

| Service | Small ($/mo) | Medium ($/mo) | Large ($/mo) |
|---------|-------------|---------------|--------------|
| ...     | ...         | ...           | ...          |
| **Total** | **$X** | **$Y** | **$Z** |

### Size Definitions
For each service, explain what each size means.

### Assumptions
List assumptions made for consumption-based estimates.

### Cost Optimization Tips
Suggest ways to reduce costs (reserved instances, spot VMs, etc.).

### Disclaimer
These are estimates based on Azure Retail Prices. Actual costs may vary
based on usage patterns, reserved instance commitments, enterprise
agreements, and regional pricing differences.
"""
