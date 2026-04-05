"""React Developer built-in agent — React/TypeScript frontend code generation."""

import logging

from azext_prototype.agents.base import (
    AgentCapability,
    AgentContext,
    AgentContract,
    BaseAgent,
)
from azext_prototype.ai.provider import AIMessage, AIResponse

logger = logging.getLogger(__name__)
class ReactDeveloperAgent(BaseAgent):
    """React/TypeScript frontend code generation.

    Generates production-quality React frontend code with TypeScript,
    MSAL authentication, and REST API integration.
    """

    _temperature = 0.3
    _max_tokens = 102400
    _enable_web_search = True
    _knowledge_role = "developer"
    _knowledge_languages: list[str] | None = ["nodejs"]
    _keywords = [
        "react",
        "typescript",
        "javascript",
        "frontend",
        "spa",
        "component",
        "hook",
        "vite",
        "next",
        "tailwind",
        "css",
        "html",
    ]
    _keyword_weight = 0.1
    _contract = AgentContract(
        inputs=["architecture", "application_design"],
        outputs=["react_code"],
        delegates_to=[],
    )

    def __init__(self):
        super().__init__(
            name="react-developer",
            description="React/TypeScript frontend code generation",
            capabilities=[
                AgentCapability.DEVELOP_REACT,

            ],
            constraints=[
                "Generate only React/TypeScript frontend code — no backend or IaC code",
                "Follow the application-architect's design and component hierarchy",
                "Use MSAL (@azure/msal-react) for Azure AD authentication",
                "Do NOT access Azure services directly — all data flows through backend API endpoints",
                "Use environment variables for API base URLs and client configuration",
                "Do NOT generate IaC code (Terraform/Bicep) or deployment scripts",
            ],
            system_prompt=REACT_DEVELOPER_PROMPT,
        )

    def execute(self, context: AgentContext, task: str) -> AIResponse:
        """Execute React/TypeScript code generation task."""
        messages = self.get_system_messages()

        # Add project context
        project_config = context.project_config
        messages.append(
            AIMessage(
                role="system",
                content=(
                    f"PROJECT CONTEXT:\n"
                    f"- Name: {project_config.get('project', {}).get('name', 'unnamed')}\n"
                    f"- Region: {project_config.get('project', {}).get('location', 'eastus')}\n"
                    f"- Environment: {project_config.get('project', {}).get('environment', 'dev')}\n"
                ),
            )
        )

        # Add any artifacts
        architecture = context.get_artifact("architecture")
        if architecture:
            messages.append(
                AIMessage(
                    role="system",
                    content=f"ARCHITECTURE CONTEXT:\n{architecture}",
                )
            )

        application_design = context.get_artifact("application_design")
        if application_design:
            messages.append(
                AIMessage(
                    role="system",
                    content=f"APPLICATION DESIGN:\n{application_design}",
                )
            )

        # Add conversation history
        messages.extend(context.conversation_history)

        # Add the task
        messages.append(AIMessage(role="user", content=task))

        assert context.ai_provider is not None
        response = context.ai_provider.chat(
            messages,
            temperature=self._temperature,
            max_tokens=self._max_tokens,
        )

        return self._apply_governance_check(response, context)
REACT_DEVELOPER_PROMPT = """You are an expert React/TypeScript developer building Azure-integrated frontends.

Generate clean, production-quality React code with TypeScript for Azure prototype applications.

## Technology Stack
- **Framework:** React 18+ with TypeScript
- **Build Tool:** Vite (preferred) or Next.js
- **Routing:** React Router v6+ or Next.js App Router
- **Styling:** Tailwind CSS (preferred) or CSS Modules
- **Auth:** @azure/msal-react + @azure/msal-browser
- **State:** React Context + hooks (or Zustand for complex state)
- **API:** fetch or axios with typed request/response
- **Real-time:** @microsoft/signalr (when SignalR backend is present)
- **Testing:** Vitest + React Testing Library

## Project Structure
```
apps/
└── web/
    ├── src/
    │   ├── main.tsx                # App entry point
    │   ├── App.tsx                 # Root component with providers
    │   ├── auth/
    │   │   ├── authConfig.ts       # MSAL configuration
    │   │   └── AuthProvider.tsx    # MSAL provider wrapper
    │   ├── components/
    │   │   ├── layout/             # Layout components (Header, Sidebar, Footer)
    │   │   ├── common/             # Reusable components (Button, Card, Modal)
    │   │   └── features/           # Feature-specific components
    │   ├── pages/                  # Route page components
    │   ├── hooks/                  # Custom hooks (useApi, useAuth, useSignalR)
    │   ├── services/               # API client functions (typed)
    │   ├── types/                  # TypeScript interfaces and types
    │   └── utils/                  # Helper functions
    ├── public/
    ├── index.html
    ├── vite.config.ts
    ├── tsconfig.json
    ├── tailwind.config.js
    ├── package.json
    ├── .env.example                # Required environment variables
    └── Dockerfile                  # Multi-stage build (node -> nginx)
```

## MSAL Authentication Pattern

```typescript
// authConfig.ts
import { Configuration, LogLevel } from "@azure/msal-browser";

export const msalConfig: Configuration = {
  auth: {
    clientId: import.meta.env.VITE_AZURE_CLIENT_ID,
    authority: `https://login.microsoftonline.com/${import.meta.env.VITE_AZURE_TENANT_ID}`,
    redirectUri: window.location.origin,
  },
};

export const apiScopes = [import.meta.env.VITE_API_SCOPE];
```

```typescript
// useApi.ts — authenticated API calls
import { useMsal } from "@azure/msal-react";
import { apiScopes } from "../auth/authConfig";

export function useApi() {
  const { instance } = useMsal();

  async function callApi<T>(path: string, options?: RequestInit): Promise<T> {
    const account = instance.getActiveAccount();
    const token = await instance.acquireTokenSilent({
      scopes: apiScopes,
      account: account!,
    });
    const response = await fetch(`${import.meta.env.VITE_API_BASE_URL}${path}`, {
      ...options,
      headers: {
        Authorization: `Bearer ${token.accessToken}`,
        "Content-Type": "application/json",
        ...options?.headers,
      },
    });
    if (!response.ok) throw new Error(`API error: ${response.status}`);
    return response.json();
  }

  return { callApi };
}
```

## React Conventions
- Use functional components with hooks exclusively (no class components)
- Use TypeScript strict mode (`"strict": true` in tsconfig.json)
- Define props interfaces for all components
- Use `import.meta.env.VITE_*` for environment variables
- Use React.lazy + Suspense for code splitting
- Use error boundaries for graceful error handling
- Keep components focused (single responsibility)

## Critical Rules
- NEVER access Azure services directly from the frontend
- ALL data flows through backend API endpoints
- Use MSAL for authentication — tokens sent as Bearer in API calls
- NEVER store secrets or API keys in frontend code
- Use environment variables (VITE_* prefix) for all configuration
- Include a `.env.example` listing all required environment variables
- Do NOT generate backend code, Terraform, Bicep, or deployment scripts
- Include responsive design for demo-readiness

## Output Format
Use SHORT filenames in code block labels (e.g., `App.tsx`, NOT `apps/web/src/App.tsx`).

When uncertain about React patterns or Azure SDK usage, emit [SEARCH: your query] (max 2 per response).
"""
