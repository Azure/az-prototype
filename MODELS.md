# AI Models & Providers

> [!NOTE]
> This reference is part of the **prototype** extension for the Azure CLI. See [COMMANDS.md](COMMANDS.md) for the full command reference.

The `az prototype` extension uses AI models to power its agent-driven workflow (design → build → deploy). This document lists the available providers, their supported models, and guidance on choosing the right combination.

---

## Providers

The extension supports three AI providers. Set the active provider with:

```bash
az prototype config set --key ai.provider --value <provider>
az prototype config set --key ai.model --value <model-id>
```

| Provider | Config Value | Authentication | Best For |
|---|---|---|---|
| [GitHub Copilot](#github-copilot-copilot) | `copilot` | Copilot OAuth token (OS keychain, env vars, `gh` CLI) | **Recommended.** Broadest model selection, including Anthropic Claude. |
| [GitHub Models](#github-models-github-models) | `github-models` | GitHub PAT via `gh auth login` (`models:read` scope) | Experimentation with open-weight and frontier models. No Anthropic models. |
| [Azure OpenAI](#azure-openai-azure-openai) | `azure-openai` | Azure AD via `DefaultAzureCredential` (`az login` / managed identity) | Enterprise deployments with data residency, private networking, or compliance requirements. |

> [!IMPORTANT]
> Anthropic Claude models are **only** available through the `copilot` provider. They are not available on GitHub Models or Azure OpenAI.

---

## GitHub Copilot (`copilot`)

**Recommended provider.** Routes requests via direct HTTP calls to the GitHub Copilot completions API (`https://api.githubcopilot.com/chat/completions`), which exposes models from OpenAI, Anthropic, and Google under a single authentication flow.

The raw OAuth token (`gho_`, `ghu_`, `ghp_`) is sent directly as a `Bearer` header with editor-identification headers — no JWT exchange or SDK subprocess is required. This makes requests fast and lightweight.

**Prerequisites:** An active **GitHub Copilot Business** or **Enterprise** licence assigned to your GitHub account.

### Supported Models

| Model ID | Name | Provider | Context Window | Notes |
|---|---|---|---|---|
| `claude-sonnet-4.5` | Claude Sonnet 4.5 | Anthropic | 200K tokens | **Default.** Best balance of quality, speed, and cost for code generation. |
| `claude-sonnet-4` | Claude Sonnet 4 | Anthropic | 200K tokens | Strong coding model, slightly lower cost than 4.5. |
| `gpt-4o` | GPT-4o | OpenAI | 128K tokens | Strong all-rounder from OpenAI. |
| `gpt-4.1` | GPT-4.1 | OpenAI | 1M tokens | Massive context window. Useful for analyzing very large codebases. |
| `o3-mini` | o3-mini | OpenAI | 200K tokens | Fast reasoning model. |
| `gemini-2.5-pro` | Gemini 2.5 Pro | Google | 1M tokens | Google's flagship model. Large context window. |

> The model list above is curated from verified models. Additional models (e.g. `claude-opus-4.5`, `claude-haiku-4.5`, `gpt-5.2`) may be available as GitHub adds them to the Copilot API.

### Credential Resolution

The Copilot provider resolves a raw OAuth token from the following sources, in priority order (first match wins):

| Priority | Source | Details |
|---|---|---|
| 1 | `COPILOT_GITHUB_TOKEN` env var | Highest priority — set this to override all other sources |
| 2 | `GH_TOKEN` env var | GitHub CLI-compatible environment variable |
| 3 | Copilot CLI keychain | Windows Credential Manager / macOS Keychain, written by `copilot login` |
| 4 | Copilot SDK config files | `~/.config/github-copilot/hosts.json` or `apps.json` |
| 5 | `gh auth token` | Reads the active token from the GitHub CLI subprocess |
| 6 | `GITHUB_TOKEN` env var | Lowest priority fallback |

> [!IMPORTANT]
> The token must originate from an approved Copilot OAuth application (e.g. `copilot login` or the VS Code Copilot extension). Tokens created via `gh auth login` alone may return `403 Forbidden` if the OAuth app is not approved for Copilot access in your organisation. For EMU (Enterprise Managed User) accounts, `copilot login` is the recommended setup method.

### Setup

```bash
# Option A — Copilot CLI (recommended, especially for EMU accounts)
copilot login

# Option B — Environment variable
set COPILOT_GITHUB_TOKEN=gho_your_token_here

# Option C — GitHub CLI (may not work for all org policies)
gh auth login
```

---

## GitHub Models (`github-models`)

Routes requests through the GitHub Models inference API (`models.inference.ai.azure.com`). Uses the OpenAI SDK with model IDs in `publisher/model-name` format.

**Authentication:** Requires a GitHub Personal Access Token (PAT). Set via environment variable `GITHUB_TOKEN` or `gh auth token`.

> [!WARNING]
> Anthropic (Claude) models are **not available** on GitHub Models. If you need Claude, switch to the `copilot` provider.

### Supported Models

| Model ID | Name | Provider | Context Window | Notes |
|---|---|---|---|---|
| `openai/gpt-4o` | GPT-4o | OpenAI | 128K tokens | **Default for this provider.** Reliable general-purpose model. |
| `openai/gpt-4.1` | GPT-4.1 | OpenAI | 1M tokens | Massive context. Good for large repo analysis. |
| `openai/gpt-4o-mini` | GPT-4o Mini | OpenAI | 128K tokens | Lower cost, good for simpler tasks. |
| `openai/o3` | o3 | OpenAI | 200K tokens | Reasoning model. Strong for complex multi-step problems. |
| `openai/o3-mini` | o3 Mini | OpenAI | 200K tokens | Smaller reasoning model. Faster, lower cost. |
| `meta/meta-llama-3.1-405b-instruct` | Llama 3.1 405B | Meta | 128K tokens | Largest open-weight model available. |
| `deepseek/deepseek-r1` | DeepSeek R1 | DeepSeek | 128K tokens | Open-weight reasoning model. |

---

## Azure OpenAI (`azure-openai`)

Routes requests to your own Azure OpenAI Service deployment. You control the model version, region, and networking.

**Authentication:** Uses **Azure AD (Entra ID)** via `DefaultAzureCredential`. API keys are intentionally **not supported** — all credentials stay within your Azure tenant.

Supported credential flows (in `DefaultAzureCredential` priority order):

1. **Managed Identity** — Azure VMs, App Service, AKS, etc.
2. **Azure CLI** — `az login` on a developer workstation
3. **Visual Studio / VS Code** — signed-in Azure account
4. **Environment variables** — `AZURE_CLIENT_ID` / `AZURE_CLIENT_SECRET` / `AZURE_TENANT_ID`

**Prerequisites:**

- An Azure subscription with an **Azure OpenAI** resource provisioned.
- At least one model **deployed** in the resource.
- The `Cognitive Services OpenAI User` role assigned to your identity.
- The `azure-identity` Python package installed (`pip install azure-identity`).

```bash
az prototype config set --key ai.provider --value azure-openai
az prototype config set --key ai.azure_openai.endpoint --value https://<resource>.openai.azure.com/
az prototype config set --key ai.model --value <deployment-name>
```

### Supported Models

Models depend on what you deploy in your Azure OpenAI resource. Common deployments:

| Deployment Name | Typical Model | Context Window | Notes |
|---|---|---|---|
| `gpt-4o` | GPT-4o (2024-08-06+) | 128K tokens | Recommended general-purpose deployment. |
| `gpt-4.1` | GPT-4.1 | 1M tokens | Available in select regions. |
| `gpt-4o-mini` | GPT-4o Mini | 128K tokens | Lower cost option. |

> [!TIP]
> See [Azure OpenAI model availability](https://learn.microsoft.com/azure/ai-services/openai/concepts/models) for regional deployment options.

### Endpoint Validation

For security, only endpoints matching `https://<resource>.openai.azure.com/` are accepted. The following are **blocked**:

- `api.openai.com` (public OpenAI)
- `chat.openai.com` (ChatGPT)
- `platform.openai.com`
- Any non-Azure-hosted endpoint

---

## Usage Recommendations

### Default Setup (Recommended)

For most users, the quickest path to a working setup:

```bash
az prototype config set --key ai.provider --value copilot
az prototype config set --key ai.model --value claude-sonnet-4.5
```

This is the default configuration. Claude Sonnet 4.5 provides the best balance of code generation quality, architectural reasoning, and speed for the prototype workflow.

### By Use Case

| Use Case | Provider | Model | Why |
|---|---|---|---|
| **General prototyping** | `copilot` | `claude-sonnet-4.5` | Best code generation quality and architectural reasoning. |
| **Complex architecture design** | `copilot` | `claude-opus-4.5` | Most capable model for nuanced design trade-offs. Slower but higher quality. |
| **Fast iteration / cost-sensitive** | `copilot` | `claude-haiku-4.5` | Fastest response times. Good enough for straightforward generation tasks. |
| **Very large codebases** | `copilot` | `gpt-4.1` or `gemini-2.5-pro` | 1M token context windows let you feed entire repos. |
| **Enterprise / compliance** | `azure-openai` | `gpt-4o` | Data stays in your Azure tenant. Private endpoints, RBAC, audit logs. |
| **Open-weight models** | `github-models` | `meta/meta-llama-3.1-405b-instruct` | No vendor lock-in on the model itself. |
| **Reasoning-heavy tasks** | `github-models` | `openai/o3` | Chain-of-thought reasoning for multi-step deployment planning. |

### By Stage

Different stages benefit from different model characteristics:

| Stage | Recommended Model | Rationale |
|---|---|---|
| `init` | Any (minimal AI usage) | Initialization is mostly scaffold work. |
| `design` | `claude-sonnet-4.5` or `claude-opus-4.5` | Architecture design benefits from strong reasoning. Opus for complex multi-service designs. |
| `build` | `claude-sonnet-4.5` | Code generation is Sonnet's sweet spot — fast, high-quality Bicep/Terraform/app code. |
| `deploy` | `claude-sonnet-4.5` | Deployment troubleshooting needs good code understanding and Azure knowledge. |
| `analyze error` | `claude-sonnet-4.5` | Error diagnosis requires correlating logs, code, and Azure docs. |
| `analyze costs` | `claude-haiku-4.5` or `gpt-4o` | Cost estimation is structured output — faster models work fine. |
| `generate docs` | `claude-sonnet-4.5` | Documentation generation benefits from natural language fluency. |

> [!NOTE]
> The extension uses a single model across all stages. Per-stage model selection is planned for a future release.

### Switching Models

Change the model at any time:

```bash
# Switch to a different model
az prototype config set --key ai.model --value claude-opus-4.5

# Switch provider entirely
az prototype config set --key ai.provider --value github-models
az prototype config set --key ai.model --value openai/gpt-4o

# Check current configuration
az prototype config show
```

### Troubleshooting

| Problem | Solution |
|---|---|
| `No Copilot credentials found` | Run `copilot login`, or set `COPILOT_GITHUB_TOKEN` env var, or try a different provider (`--ai-provider github-models`). |
| `403 Forbidden` with copilot | Your token likely came from an unapproved OAuth app. Run `copilot login` to get a token from the approved Copilot CLI app. Common with EMU accounts using `gh auth login`. |
| `401 Unauthorized` with copilot | Ensure you have an active GitHub Copilot Business or Enterprise licence. The provider will retry once automatically. |
| `401 Unauthorized` with github-models | Check your GitHub token has `models:read` scope. Run `gh auth refresh --scopes models:read`. |
| Claude model on `github-models` | Claude is not available on GitHub Models. Switch to `copilot` provider. |
| `Invalid Azure OpenAI endpoint` | Endpoint must match `https://<resource>.openai.azure.com/`. Public OpenAI endpoints are blocked. |
| Slow responses | Try a smaller/faster model like `gpt-4o-mini`. The `copilot` provider uses direct HTTP (no SDK overhead). |
| Token limit exceeded | Switch to a model with a larger context window (`gpt-4.1`, `gemini-2.5-pro`). |
| Timeout on large prompts | Increase the timeout: `set COPILOT_TIMEOUT=300` (default is 120 seconds). |

---

## Provider Comparison

| Feature | Copilot | GitHub Models | Azure OpenAI |
|---|---|---|---|
| **Anthropic Claude** | Yes | No | No |
| **OpenAI GPT** | Yes | Yes | Yes |
| **Google Gemini** | Yes | No | No |
| **Open-weight models** | No | Yes (Meta, DeepSeek) | No |
| **Authentication** | Copilot OAuth token (`copilot login`) | GitHub PAT (`gh auth login`) | Azure AD (`az login` / managed identity) |
| **Data residency** | GitHub-managed | GitHub-managed | Your Azure tenant |
| **Private networking** | No | No | Yes (Private Endpoints) |
| **SLA** | Copilot SLA | Preview (no SLA) | Azure OpenAI SLA |
| **Cost** | Included in Copilot plan | Free tier + usage | Azure OpenAI pricing |

---

## Blocked Providers

The following provider names are explicitly blocked for security and policy compliance:

> `openai`, `chatgpt`, `public-openai`, `anthropic`, `cohere`, `google`, `aws-bedrock`, `huggingface`

Only Azure-hosted / Microsoft-approved AI services (`copilot`, `github-models`, `azure-openai`) are permitted. Attempting to configure a blocked provider will result in a `CLIError`.
