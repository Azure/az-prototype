# React/TypeScript Language Patterns for Azure Prototypes

Reference patterns for React/TypeScript-based Azure prototype frontends. Agents should use these patterns when generating React frontend code.

## Project Structure

```
apps/
└── web/
    ├── src/
    │   ├── main.tsx                  # App entry point (React.createRoot)
    │   ├── App.tsx                   # Root component — providers, router
    │   ├── vite-env.d.ts             # Vite environment type declarations
    │   ├── auth/
    │   │   ├── authConfig.ts         # MSAL configuration (clientId, authority, scopes)
    │   │   └── AuthProvider.tsx      # MsalProvider wrapper
    │   ├── components/
    │   │   ├── layout/               # Layout shell (Header, Sidebar, Footer, PageLayout)
    │   │   ├── common/               # Reusable UI (Button, Card, Modal, LoadingSpinner, ErrorBanner)
    │   │   └── features/             # Feature-specific components grouped by domain
    │   ├── pages/                    # Route-level page components (one per route)
    │   ├── hooks/                    # Custom hooks (useApi, useAuth, useSignalR, useDebounce)
    │   ├── services/                 # API client functions — typed request/response
    │   ├── types/                    # Shared TypeScript interfaces and type definitions
    │   └── utils/                    # Pure helper functions (formatting, validation)
    ├── public/
    │   └── favicon.svg
    ├── index.html                    # Vite HTML entry point
    ├── vite.config.ts                # Vite configuration
    ├── tsconfig.json                 # TypeScript strict configuration
    ├── tailwind.config.js            # Tailwind CSS configuration
    ├── postcss.config.js             # PostCSS for Tailwind
    ├── package.json
    ├── .env.example                  # Required environment variables
    └── Dockerfile                    # Multi-stage build (node build -> nginx serve)
```

### Folder conventions
- `components/` contains reusable pieces; `pages/` contains route-level compositions
- `hooks/` contains only custom React hooks (prefixed with `use`)
- `services/` contains API call functions, never React components or hooks
- `types/` contains shared interfaces; component-specific types live next to their component
- One component per file; filename matches the default export name

## Vite Build Tooling

### vite.config.ts
```typescript
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  server: {
    port: 3000,
    proxy: {
      "/api": {
        target: "http://localhost:8080",
        changeOrigin: true,
      },
    },
  },
  build: {
    outDir: "dist",
    sourcemap: true,
  },
});
```

### Environment Variables

All frontend environment variables MUST use the `VITE_` prefix. They are embedded at build time, not runtime secrets.

```bash
# .env.example
VITE_API_BASE_URL=http://localhost:8080
VITE_AZURE_CLIENT_ID=<app-registration-client-id>
VITE_AZURE_TENANT_ID=<azure-ad-tenant-id>
VITE_API_SCOPE=api://<backend-app-id>/access_as_user
VITE_SIGNALR_URL=http://localhost:8080/hub
```

Access in code via `import.meta.env`:

```typescript
const apiBaseUrl = import.meta.env.VITE_API_BASE_URL;
const clientId = import.meta.env.VITE_AZURE_CLIENT_ID;
```

### vite-env.d.ts
```typescript
/// <reference types="vite/client" />

interface ImportMetaEnv {
  readonly VITE_API_BASE_URL: string;
  readonly VITE_AZURE_CLIENT_ID: string;
  readonly VITE_AZURE_TENANT_ID: string;
  readonly VITE_API_SCOPE: string;
  readonly VITE_SIGNALR_URL?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
```

## MSAL React Authentication

### authConfig.ts
```typescript
import { Configuration, LogLevel } from "@azure/msal-browser";

export const msalConfig: Configuration = {
  auth: {
    clientId: import.meta.env.VITE_AZURE_CLIENT_ID,
    authority: `https://login.microsoftonline.com/${import.meta.env.VITE_AZURE_TENANT_ID}`,
    redirectUri: window.location.origin,
    postLogoutRedirectUri: window.location.origin,
  },
  cache: {
    cacheLocation: "sessionStorage",
    storeAuthStateInCookie: false,
  },
  system: {
    loggerOptions: {
      logLevel: LogLevel.Warning,
      loggerCallback: (level, message) => {
        if (level === LogLevel.Error) console.error(message);
      },
    },
  },
};

export const loginRequest = {
  scopes: [import.meta.env.VITE_API_SCOPE],
};

export const apiScopes = [import.meta.env.VITE_API_SCOPE];
```

### AuthProvider.tsx
```typescript
import { MsalProvider } from "@azure/msal-react";
import { PublicClientApplication, EventType, EventMessage, AuthenticationResult } from "@azure/msal-browser";
import { msalConfig } from "./authConfig";

const msalInstance = new PublicClientApplication(msalConfig);

// Set the first account as active on login
msalInstance.addEventCallback((event: EventMessage) => {
  if (event.eventType === EventType.LOGIN_SUCCESS && event.payload) {
    const result = event.payload as AuthenticationResult;
    msalInstance.setActiveAccount(result.account);
  }
});

interface AuthProviderProps {
  children: React.ReactNode;
}

export function AuthProvider({ children }: AuthProviderProps) {
  return <MsalProvider instance={msalInstance}>{children}</MsalProvider>;
}
```

### App.tsx with authentication
```typescript
import { BrowserRouter, Routes, Route } from "react-router-dom";
import { AuthenticatedTemplate, UnauthenticatedTemplate } from "@azure/msal-react";
import { AuthProvider } from "./auth/AuthProvider";
import { PageLayout } from "./components/layout/PageLayout";
import { LoginPage } from "./pages/LoginPage";
import { HomePage } from "./pages/HomePage";
import { DashboardPage } from "./pages/DashboardPage";

export default function App() {
  return (
    <AuthProvider>
      <BrowserRouter>
        <UnauthenticatedTemplate>
          <LoginPage />
        </UnauthenticatedTemplate>
        <AuthenticatedTemplate>
          <PageLayout>
            <Routes>
              <Route path="/" element={<HomePage />} />
              <Route path="/dashboard" element={<DashboardPage />} />
            </Routes>
          </PageLayout>
        </AuthenticatedTemplate>
      </BrowserRouter>
    </AuthProvider>
  );
}
```

## React Router Navigation

```typescript
// pages/ — one component per route
import { useNavigate, useParams } from "react-router-dom";

export function OrderDetailPage() {
  const { orderId } = useParams<{ orderId: string }>();
  const navigate = useNavigate();

  const handleBack = () => navigate("/orders");

  return (
    <div>
      <button onClick={handleBack}>Back to Orders</button>
      <h1>Order: {orderId}</h1>
      {/* ... */}
    </div>
  );
}
```

Route definitions in `App.tsx`:
```typescript
<Routes>
  <Route path="/" element={<HomePage />} />
  <Route path="/orders" element={<OrderListPage />} />
  <Route path="/orders/:orderId" element={<OrderDetailPage />} />
  <Route path="*" element={<NotFoundPage />} />
</Routes>
```

## API Client Pattern

The frontend NEVER accesses Azure services directly. All data flows through backend API endpoints.

### useApi hook (authenticated fetch)
```typescript
// hooks/useApi.ts
import { useMsal } from "@azure/msal-react";
import { apiScopes } from "../auth/authConfig";

const API_BASE = import.meta.env.VITE_API_BASE_URL;

export function useApi() {
  const { instance } = useMsal();

  async function callApi<T>(
    path: string,
    options: RequestInit = {}
  ): Promise<T> {
    const account = instance.getActiveAccount();
    if (!account) throw new Error("No active account. User must sign in.");

    const tokenResponse = await instance.acquireTokenSilent({
      scopes: apiScopes,
      account,
    });

    const response = await fetch(`${API_BASE}${path}`, {
      ...options,
      headers: {
        Authorization: `Bearer ${tokenResponse.accessToken}`,
        "Content-Type": "application/json",
        ...options.headers,
      },
    });

    if (!response.ok) {
      const errorBody = await response.json().catch(() => ({}));
      throw new ApiError(response.status, errorBody.error || response.statusText);
    }

    return response.json();
  }

  return { callApi };
}

export class ApiError extends Error {
  constructor(
    public status: number,
    message: string
  ) {
    super(message);
    this.name = "ApiError";
  }
}
```

### Typed service functions
```typescript
// services/orderService.ts
import type { Order, CreateOrderRequest } from "../types";

export function createOrderService(callApi: <T>(path: string, options?: RequestInit) => Promise<T>) {
  return {
    async listOrders(): Promise<Order[]> {
      return callApi<Order[]>("/api/v1/orders");
    },

    async getOrder(id: string): Promise<Order> {
      return callApi<Order>(`/api/v1/orders/${id}`);
    },

    async createOrder(data: CreateOrderRequest): Promise<Order> {
      return callApi<Order>("/api/v1/orders", {
        method: "POST",
        body: JSON.stringify(data),
      });
    },

    async deleteOrder(id: string): Promise<void> {
      await callApi<void>(`/api/v1/orders/${id}`, { method: "DELETE" });
    },
  };
}
```

### Usage in a component
```typescript
import { useState, useEffect } from "react";
import { useApi } from "../hooks/useApi";
import { createOrderService } from "../services/orderService";
import type { Order } from "../types";

export function OrderListPage() {
  const { callApi } = useApi();
  const orderService = createOrderService(callApi);
  const [orders, setOrders] = useState<Order[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    orderService
      .listOrders()
      .then(setOrders)
      .catch((err) => setError(err.message))
      .finally(() => setLoading(false));
  }, []);

  if (loading) return <LoadingSpinner />;
  if (error) return <ErrorBanner message={error} />;

  return (
    <div>
      <h1>Orders</h1>
      {orders.map((order) => (
        <OrderCard key={order.id} order={order} />
      ))}
    </div>
  );
}
```

## SignalR Real-Time Updates

When the backend uses Azure SignalR Service:

```typescript
// hooks/useSignalR.ts
import { useEffect, useRef, useCallback } from "react";
import { HubConnectionBuilder, HubConnection, LogLevel } from "@microsoft/signalr";
import { useMsal } from "@azure/msal-react";
import { apiScopes } from "../auth/authConfig";

const SIGNALR_URL = import.meta.env.VITE_SIGNALR_URL;

export function useSignalR(hubPath: string = "/hub") {
  const { instance } = useMsal();
  const connectionRef = useRef<HubConnection | null>(null);

  useEffect(() => {
    if (!SIGNALR_URL) return;

    const connection = new HubConnectionBuilder()
      .withUrl(`${SIGNALR_URL}${hubPath}`, {
        accessTokenFactory: async () => {
          const account = instance.getActiveAccount();
          if (!account) return "";
          const token = await instance.acquireTokenSilent({
            scopes: apiScopes,
            account,
          });
          return token.accessToken;
        },
      })
      .withAutomaticReconnect()
      .configureLogging(LogLevel.Warning)
      .build();

    connection.start().catch((err) => console.error("SignalR connection failed:", err));
    connectionRef.current = connection;

    return () => {
      connection.stop();
    };
  }, [hubPath, instance]);

  const on = useCallback(
    (eventName: string, callback: (...args: unknown[]) => void) => {
      connectionRef.current?.on(eventName, callback);
      return () => connectionRef.current?.off(eventName, callback);
    },
    []
  );

  return { on, connection: connectionRef.current };
}
```

Usage:
```typescript
export function NotificationPanel() {
  const { on } = useSignalR();
  const [notifications, setNotifications] = useState<string[]>([]);

  useEffect(() => {
    return on("NewNotification", (message: unknown) => {
      setNotifications((prev) => [String(message), ...prev]);
    });
  }, [on]);

  return (
    <ul>
      {notifications.map((msg, i) => (
        <li key={i}>{msg}</li>
      ))}
    </ul>
  );
}
```

## TypeScript Configuration

```json
{
  "compilerOptions": {
    "target": "ES2020",
    "lib": ["ES2020", "DOM", "DOM.Iterable"],
    "module": "ESNext",
    "moduleResolution": "bundler",
    "jsx": "react-jsx",
    "strict": true,
    "noUnusedLocals": true,
    "noUnusedParameters": true,
    "noFallthroughCasesInSwitch": true,
    "skipLibCheck": true,
    "esModuleInterop": true,
    "resolveJsonModule": true,
    "isolatedModules": true,
    "forceConsistentCasingInFileNames": true,
    "allowImportingTsExtensions": true,
    "noEmit": true
  },
  "include": ["src"],
  "references": [{ "path": "./tsconfig.node.json" }]
}
```

## Component Patterns

### Props interfaces
```typescript
// Always define a Props interface for every component
interface OrderCardProps {
  order: Order;
  onSelect?: (orderId: string) => void;
  className?: string;
}

export function OrderCard({ order, onSelect, className }: OrderCardProps) {
  return (
    <div className={`rounded-lg border p-4 ${className ?? ""}`} onClick={() => onSelect?.(order.id)}>
      <h3 className="font-semibold">{order.title}</h3>
      <p className="text-sm text-gray-600">{order.status}</p>
    </div>
  );
}
```

### Error boundaries
```typescript
// components/common/ErrorBoundary.tsx
import { Component, type ErrorInfo, type ReactNode } from "react";

interface ErrorBoundaryProps {
  children: ReactNode;
  fallback?: ReactNode;
}

interface ErrorBoundaryState {
  hasError: boolean;
  error: Error | null;
}

export class ErrorBoundary extends Component<ErrorBoundaryProps, ErrorBoundaryState> {
  state: ErrorBoundaryState = { hasError: false, error: null };

  static getDerivedStateFromError(error: Error) {
    return { hasError: true, error };
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    console.error("ErrorBoundary caught:", error, info);
  }

  render() {
    if (this.state.hasError) {
      return (
        this.props.fallback ?? (
          <div className="p-4 text-center">
            <h2 className="text-lg font-semibold text-red-600">Something went wrong</h2>
            <p className="text-sm text-gray-600">{this.state.error?.message}</p>
          </div>
        )
      );
    }
    return this.props.children;
  }
}
```

### Code splitting with React.lazy
```typescript
import { lazy, Suspense } from "react";
import { LoadingSpinner } from "./components/common/LoadingSpinner";

const DashboardPage = lazy(() => import("./pages/DashboardPage"));
const SettingsPage = lazy(() => import("./pages/SettingsPage"));

// In routes:
<Suspense fallback={<LoadingSpinner />}>
  <Routes>
    <Route path="/dashboard" element={<DashboardPage />} />
    <Route path="/settings" element={<SettingsPage />} />
  </Routes>
</Suspense>
```

## Dockerfile (Multi-Stage: Node Build + Nginx Serve)

```dockerfile
# Stage 1: Build
FROM node:20-alpine AS builder

WORKDIR /build
COPY package.json package-lock.json ./
RUN npm ci
COPY . .

# Build-time environment variables (baked into the bundle)
ARG VITE_API_BASE_URL
ARG VITE_AZURE_CLIENT_ID
ARG VITE_AZURE_TENANT_ID
ARG VITE_API_SCOPE

RUN npm run build

# Stage 2: Serve with nginx
FROM nginx:1.27-alpine AS runtime

# Remove default nginx config
RUN rm /etc/nginx/conf.d/default.conf

# Copy custom nginx config for SPA routing
COPY nginx.conf /etc/nginx/conf.d/default.conf

# Copy built assets
COPY --from=builder /build/dist /usr/share/nginx/html

# Non-root user (nginx alpine image supports this)
RUN chown -R nginx:nginx /usr/share/nginx/html

EXPOSE 80

HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
    CMD wget --no-verbose --tries=1 --spider http://localhost/health || exit 1

CMD ["nginx", "-g", "daemon off;"]
```

### nginx.conf (SPA routing)
```nginx
server {
    listen 80;
    server_name _;
    root /usr/share/nginx/html;
    index index.html;

    # SPA routing — serve index.html for all non-file routes
    location / {
        try_files $uri $uri/ /index.html;
    }

    # Health check endpoint
    location /health {
        access_log off;
        return 200 '{"status":"healthy"}';
        add_header Content-Type application/json;
    }

    # Cache static assets aggressively
    location /assets/ {
        expires 1y;
        add_header Cache-Control "public, immutable";
    }

    # Security headers
    add_header X-Content-Type-Options "nosniff" always;
    add_header X-Frame-Options "DENY" always;
    add_header Referrer-Policy "strict-origin-when-cross-origin" always;
}
```

## Health / Readiness

Health checks are not applicable to the React frontend in the traditional sense (it is a static SPA served by nginx). However:

- The nginx container exposes `/health` returning `{"status":"healthy"}` for container orchestrator liveness probes
- The Dockerfile includes a `HEALTHCHECK` instruction
- The frontend itself does NOT expose health endpoints -- it is a static asset bundle

## package.json

```json
{
  "name": "prototype-web",
  "private": true,
  "version": "0.1.0",
  "type": "module",
  "scripts": {
    "dev": "vite",
    "build": "tsc && vite build",
    "preview": "vite preview",
    "lint": "eslint src/",
    "lint:fix": "eslint src/ --fix",
    "test": "vitest run",
    "test:watch": "vitest"
  },
  "dependencies": {
    "react": "^18.3.0",
    "react-dom": "^18.3.0",
    "react-router-dom": "^6.28.0",

    "@azure/msal-browser": "^3.27.0",
    "@azure/msal-react": "^2.1.0",

    "@microsoft/signalr": "^8.0.0"
  },
  "devDependencies": {
    "@types/react": "^18.3.0",
    "@types/react-dom": "^18.3.0",
    "@vitejs/plugin-react": "^4.3.0",
    "typescript": "^5.7.0",
    "vite": "^6.0.0",

    "tailwindcss": "^3.4.0",
    "postcss": "^8.4.0",
    "autoprefixer": "^10.4.0",

    "vitest": "^2.1.0",
    "@testing-library/react": "^16.1.0",
    "@testing-library/jest-dom": "^6.6.0",
    "@testing-library/user-event": "^14.5.0",

    "eslint": "^9.16.0",
    "@typescript-eslint/eslint-plugin": "^8.18.0",
    "@typescript-eslint/parser": "^8.18.0",
    "eslint-plugin-react-hooks": "^5.0.0"
  }
}
```

## Testing Patterns (Vitest + React Testing Library)

### vitest.config.ts
```typescript
import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  test: {
    globals: true,
    environment: "jsdom",
    setupFiles: ["./src/test/setup.ts"],
    css: false,
  },
});
```

### Test setup
```typescript
// src/test/setup.ts
import "@testing-library/jest-dom/vitest";
import { vi } from "vitest";

// Mock MSAL
vi.mock("@azure/msal-react", () => ({
  useMsal: () => ({
    instance: {
      getActiveAccount: () => ({ username: "test@example.com" }),
      acquireTokenSilent: vi.fn().mockResolvedValue({ accessToken: "mock-token" }),
    },
    accounts: [{ username: "test@example.com" }],
  }),
  MsalProvider: ({ children }: { children: React.ReactNode }) => children,
  AuthenticatedTemplate: ({ children }: { children: React.ReactNode }) => children,
  UnauthenticatedTemplate: () => null,
}));

// Mock import.meta.env
vi.stubEnv("VITE_API_BASE_URL", "http://localhost:8080");
vi.stubEnv("VITE_AZURE_CLIENT_ID", "test-client-id");
vi.stubEnv("VITE_AZURE_TENANT_ID", "test-tenant-id");
vi.stubEnv("VITE_API_SCOPE", "api://test/access_as_user");
```

### Component test
```typescript
// src/components/features/__tests__/OrderCard.test.tsx
import { render, screen, fireEvent } from "@testing-library/react";
import { describe, it, expect, vi } from "vitest";
import { OrderCard } from "../OrderCard";

describe("OrderCard", () => {
  const mockOrder = {
    id: "order-1",
    title: "Test Order",
    status: "Pending",
  };

  it("renders order details", () => {
    render(<OrderCard order={mockOrder} />);
    expect(screen.getByText("Test Order")).toBeInTheDocument();
    expect(screen.getByText("Pending")).toBeInTheDocument();
  });

  it("calls onSelect when clicked", () => {
    const onSelect = vi.fn();
    render(<OrderCard order={mockOrder} onSelect={onSelect} />);
    fireEvent.click(screen.getByText("Test Order"));
    expect(onSelect).toHaveBeenCalledWith("order-1");
  });
});
```

### API hook test
```typescript
// src/hooks/__tests__/useApi.test.tsx
import { renderHook } from "@testing-library/react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { useApi } from "../useApi";

describe("useApi", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it("attaches bearer token to requests", async () => {
    const fetchSpy = vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify({ id: "1" }), { status: 200 })
    );

    const { result } = renderHook(() => useApi());
    await result.current.callApi("/api/v1/items");

    expect(fetchSpy).toHaveBeenCalledWith(
      expect.stringContaining("/api/v1/items"),
      expect.objectContaining({
        headers: expect.objectContaining({
          Authorization: "Bearer mock-token",
        }),
      })
    );
  });
});
```

## Common Pitfalls

### NEVER use `require()` in test files — Vitest uses ESM
```typescript
// WRONG — require() bypasses Vitest module mocks
const { reducer } = require("./boardReducer");

// CORRECT — use import (ESM)
import { reducer } from "./boardReducer";
```

### NEVER use dynamic `import()` inside test bodies
```typescript
// WRONG — dynamic import bypasses Vitest mock cache
it("should work", async () => {
  const { useApi } = await import("../hooks/useApi");  // mock not applied
});

// CORRECT — import at top level, mock at module level
import { useApi } from "../hooks/useApi";
vi.mock("../hooks/useApi");
```

### Use ConnectionString, NOT InstrumentationKey
```typescript
// WRONG — InstrumentationKey is deprecated
VITE_APPLICATIONINSIGHTS_KEY=00000000-0000-0000-0000-000000000000

// CORRECT — use ConnectionString
VITE_APPLICATIONINSIGHTS_CONNECTION_STRING=InstrumentationKey=...;IngestionEndpoint=...
```

### Always mock MSAL at the module level
```typescript
// In test setup (src/test/setup.ts), NOT in individual tests
vi.mock("@azure/msal-react", () => ({
  useMsal: () => ({
    instance: { getActiveAccount: () => ({ username: "test@test.com" }) },
  }),
}));
```

## Critical Rules

1. **NEVER access Azure services directly** -- no Azure SDK imports in frontend code. All data flows through backend API endpoints with authentication.
2. **Use MSAL for authentication** -- `@azure/msal-react` and `@azure/msal-browser`. Tokens are sent as `Bearer` in API calls.
3. **No secrets in frontend code** -- environment variables are baked into the build and are publicly visible. Only store client IDs, tenant IDs, API scopes, and endpoint URLs.
4. **Use `import.meta.env.VITE_*`** for all configuration -- never hardcode URLs, client IDs, or API paths.
5. **TypeScript strict mode** -- `"strict": true` in `tsconfig.json`. Define interfaces for all props, API responses, and state.
6. **Functional components only** -- no class components except for Error Boundaries (React limitation).
7. **Do NOT generate backend, IaC, or deployment scripts** -- this language pattern is frontend only.
