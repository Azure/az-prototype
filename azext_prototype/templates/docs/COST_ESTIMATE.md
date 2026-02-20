# [PROJECT_NAME] - Azure Cost Estimate

## Instructions for Claude Code

This template provides cost estimates for the Azure infrastructure. Populate each section by analyzing:
- The architecture document (`concept/docs/ARCHITECTURE.md`)
- Infrastructure code in `concept/infra/`
- The `concept/AZURE_CONFIG.json` for resource configurations
- Azure pricing calculator or pricing documentation

Replace all `[PLACEHOLDER]` values with actual information. Adjust scenarios based on customer's expected usage patterns. Use current Azure pricing for the deployment region.

**Key Principles:**
- Provide realistic cost ranges, not single-point estimates
- Include all resources, even small ones
- Document assumptions clearly
- Offer cost optimization recommendations

---

## Current Architecture SKUs

Based on the infrastructure definitions:

### Core Services

| Resource | SKU | Configuration |
|----------|-----|---------------|
| [SERVICE_1] | [SKU] | [CONFIG] |
| [SERVICE_2] | [SKU] | [CONFIG] |
| [SERVICE_3] | [SKU] | [CONFIG] |
| [SERVICE_4] | [SKU] | [CONFIG] |
| [SERVICE_5] | [SKU] | [CONFIG] |

### Ancillary Services

| Resource | SKU | Configuration |
|----------|-----|---------------|
| [SERVICE_1] | [SKU] | [CONFIG] |
| [SERVICE_2] | [SKU] | [CONFIG] |
| [SERVICE_3] | [SKU] | [CONFIG] |
| [SERVICE_4] | [SKU] | [CONFIG] |

---

## Cost Scenarios

### Scenario 1: Development/POC (Low Usage)

**Assumptions:**
- [USAGE_METRIC_1]: [VALUE]
- [USAGE_METRIC_2]: [VALUE]
- [USAGE_METRIC_3]: [VALUE]
- [USAGE_METRIC_4]: [VALUE]

**Limitations:**
- [LIMITATION_1]
- [LIMITATION_2]
- [LIMITATION_3]
- [LIMITATION_4]

| Resource | Monthly Cost | Notes |
|----------|-------------|-------|
| **[SERVICE_1]** | $[AMOUNT] | [NOTES] |
| **[SERVICE_2]** | $[AMOUNT] | [NOTES] |
| **[SERVICE_3]** | $[AMOUNT] | [NOTES] |
| **[SERVICE_4]** | $[AMOUNT] | [NOTES] |
| **[SERVICE_5]** | $[AMOUNT] | [NOTES] |
| **[SERVICE_6]** | $[AMOUNT] | [NOTES] |
| **[SERVICE_7]** | $[AMOUNT] | [NOTES] |
| **[SERVICE_8]** | $[AMOUNT] | [NOTES] |

**POC Monthly Total: ~$[LOW_ESTIMATE] - $[HIGH_ESTIMATE]**

---

### Scenario 2: Pilot (Medium Usage)

**Assumptions:**
- [USAGE_METRIC_1]: [VALUE]
- [USAGE_METRIC_2]: [VALUE]
- [USAGE_METRIC_3]: [VALUE]
- [USAGE_METRIC_4]: [VALUE]

**Limitations:**
- [LIMITATION_1]
- [LIMITATION_2]
- [LIMITATION_3]
- [LIMITATION_4]

| Resource | Monthly Cost | Notes |
|----------|-------------|-------|
| **[SERVICE_1]** | $[AMOUNT] | [NOTES] |
| **[SERVICE_2]** | $[AMOUNT] | [NOTES] |
| **[SERVICE_3]** | $[AMOUNT] | [NOTES] |
| **[SERVICE_4]** | $[AMOUNT] | [NOTES] |
| **[SERVICE_5]** | $[AMOUNT] | [NOTES] |
| **[SERVICE_6]** | $[AMOUNT] | [NOTES] |
| **[SERVICE_7]** | $[AMOUNT] | [NOTES] |
| **[SERVICE_8]** | $[AMOUNT] | [NOTES] |

**Pilot Monthly Total: ~$[LOW_ESTIMATE] - $[HIGH_ESTIMATE]**

---

### Scenario 3: Production (High Usage)

**Assumptions:**
- [USAGE_METRIC_1]: [VALUE]
- [USAGE_METRIC_2]: [VALUE]
- [USAGE_METRIC_3]: [VALUE]
- [USAGE_METRIC_4]: [VALUE]

**Limitations:**
- [LIMITATION_1]
- [LIMITATION_2]
- [LIMITATION_3]
- [LIMITATION_4]

| Resource | Monthly Cost | Notes |
|----------|-------------|-------|
| **[SERVICE_1]** | $[AMOUNT] | [NOTES] |
| **[SERVICE_2]** | $[AMOUNT] | [NOTES] |
| **[SERVICE_3]** | $[AMOUNT] | [NOTES] |
| **[SERVICE_4]** | $[AMOUNT] | [NOTES] |
| **[SERVICE_5]** | $[AMOUNT] | [NOTES] |
| **[SERVICE_6]** | $[AMOUNT] | [NOTES] |
| **[SERVICE_7]** | $[AMOUNT] | [NOTES] |
| **[SERVICE_8]** | $[AMOUNT] | [NOTES] |
| **[PRODUCTION_ONLY_SERVICE_1]** | $[AMOUNT] | [NOTES] |
| **[PRODUCTION_ONLY_SERVICE_2]** | $[AMOUNT] | [NOTES] |

**Production Monthly Total: ~$[LOW_ESTIMATE] - $[HIGH_ESTIMATE]**

---

## Cost Breakdown by Category

### [CATEGORY_1] ([PERCENTAGE]% of total)
- [SERVICE]: [DESCRIPTION]
- [SERVICE]: [DESCRIPTION]

### [CATEGORY_2] ([PERCENTAGE]% of total)
- [SERVICE]: [DESCRIPTION]
- [SERVICE]: [DESCRIPTION]

### [CATEGORY_3] ([PERCENTAGE]% of total)
- [SERVICE]: [DESCRIPTION]

### [CATEGORY_4] ([PERCENTAGE]% of total)
- [SERVICE]: [DESCRIPTION]

---

## Cost Optimization Recommendations

### Immediate Savings

1. **[OPTIMIZATION_1]**
   - Current: [CURRENT_STATE]
   - Recommended: [RECOMMENDED_STATE]
   - Estimated savings: **$[AMOUNT]/month**
   - Trade-off: [TRADE_OFF]

2. **[OPTIMIZATION_2]**
   - Current: [CURRENT_STATE]
   - Recommended: [RECOMMENDED_STATE]
   - Estimated savings: **$[AMOUNT]/month**
   - Trade-off: [TRADE_OFF]

3. **[OPTIMIZATION_3]**
   - Current: [CURRENT_STATE]
   - Recommended: [RECOMMENDED_STATE]
   - Estimated savings: **[PERCENTAGE]% on [SERVICE] costs**

### Production Optimizations

1. **[PRODUCTION_OPTIMIZATION_1]**
   - [OPTION_A]: [DESCRIPTION]
   - [OPTION_B]: [DESCRIPTION]
   - Break-even: [BREAK_EVEN_POINT]

2. **Reserved Capacity**
   - 1-year reserved [SERVICE]: [PERCENTAGE]% savings
   - 3-year reserved: [PERCENTAGE]% savings

3. **[CACHING_OR_EFFICIENCY_OPTIMIZATION]**
   - [STRATEGY_1]: [DESCRIPTION]
   - [STRATEGY_2]: [DESCRIPTION]
   - Estimated savings: **[PERCENTAGE]% on [SERVICE] costs**

---

## [PRIMARY_COST_DRIVER] Detailed Costs

[If AI services, compute, or another service is the primary cost driver, provide detailed breakdown here.]

### Pricing ([PRICING_MODEL])

| [UNIT_TYPE] | [METRIC_1] | [METRIC_2] |
|-------------|------------|------------|
| [ITEM_1] | $[PRICE] | $[PRICE] |
| [ITEM_2] | $[PRICE] | $[PRICE] |
| [ITEM_3] | $[PRICE] | $[PRICE] |

### Estimated Usage per [TRANSACTION_TYPE]

| Component | [UNIT_TYPE] | [METRIC_1] | [METRIC_2] |
|-----------|-------------|------------|------------|
| [COMPONENT_1] | [TYPE] | [VALUE] | [VALUE] |
| [COMPONENT_2] | [TYPE] | [VALUE] | [VALUE] |
| [COMPONENT_3] | [TYPE] | [VALUE] | [VALUE] |
| [COMPONENT_4] | [TYPE] | [VALUE] | [VALUE] |

**Total per [TRANSACTION]: ~[VALUE] [UNIT_1] + ~[VALUE] [UNIT_2]**

**Cost per [TRANSACTION]:**
- Standard path: ~$[LOW] - $[HIGH]
- With optimization ([OPTIMIZATION]): ~$[LOW] - $[HIGH]

---

## Monthly Cost Summary Table

| Usage Level | [PRIMARY_METRIC]/Month | Estimated Monthly Cost |
|-------------|------------------------|------------------------|
| POC | [VALUE] | $[LOW] - $[HIGH] |
| Pilot | [VALUE] | $[LOW] - $[HIGH] |
| Production (Low) | [VALUE] | $[LOW] - $[HIGH] |
| Production (Medium) | [VALUE] | $[LOW] - $[HIGH] |
| Production (High) | [VALUE] | $[LOW] - $[HIGH] |

---

## Cost Monitoring Recommendations

1. **Set up Azure Cost Alerts**
   - Budget alerts at 50%, 75%, 90%
   - Anomaly detection enabled

2. **Tag Resources**
   - Environment: dev/staging/prod
   - Component: [COMPONENT_TAGS]
   - Owner: [TEAM]

3. **Review Weekly**
   - Azure Advisor cost recommendations
   - Unused resources
   - Right-sizing opportunities

---

## Alternative Architectures (Cost Reduction)

### Option A: [ALTERNATIVE_1]
- Change: [DESCRIPTION]
- Saves: ~$[AMOUNT]/month
- Trade-off: [TRADE_OFF]

### Option B: [ALTERNATIVE_2]
- Change: [DESCRIPTION]
- Saves: Variable, potentially $[LOW]-$[HIGH]/month at [USAGE_LEVEL]
- Trade-off: [TRADE_OFF]

### Option C: [ALTERNATIVE_3]
- Change: [DESCRIPTION]
- Saves: [PERCENTAGE]% on [SERVICE] costs
- Trade-off: [TRADE_OFF]

---

## Assumptions & Disclaimers

1. All prices are based on [REGION] region pricing as of [DATE]
2. Prices are subject to change; verify with Azure Pricing Calculator
3. Actual costs may vary based on usage patterns
4. Reserved instance and commitment discounts not included unless specified
5. Data transfer costs estimated; actual may vary based on architecture
6. [ADDITIONAL_ASSUMPTION]

---

*Last updated: [DATE]*
*Prices based on [REGION] region, subject to change*
