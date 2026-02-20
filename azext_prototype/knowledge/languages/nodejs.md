# Node.js / TypeScript Language Patterns for Azure Prototypes

Reference patterns for Node.js/TypeScript-based Azure prototype applications. Agents should use these patterns when generating Node.js application code.

## Express Application Structure (Recommended for Quick Prototypes)

```
src/
  index.ts              # Application entry point
  app.ts                # Express app setup + middleware
  config.ts             # Configuration loading
  routes/
    index.ts            # Route registration
    health.ts           # Health check endpoints
    api.ts              # Business logic endpoints
  services/
    azure-clients.ts    # Azure SDK client factories
  middleware/
    error-handler.ts    # Global error handling middleware
    request-logger.ts   # Request logging middleware
  types/
    index.ts            # Shared TypeScript types
tests/
  setup.ts              # Test setup
  health.test.ts
  api.test.ts
Dockerfile
package.json
tsconfig.json
.env.example
```

### index.ts
```typescript
import { app } from "./app";
import { config } from "./config";
import { initAzureClients, closeAzureClients } from "./services/azure-clients";
import { logger } from "./logger";

async function main(): Promise<void> {
  logger.info("Starting application: %s", config.appName);

  await initAzureClients();

  const server = app.listen(config.port, config.host, () => {
    logger.info("Server listening on %s:%d", config.host, config.port);
  });

  // Graceful shutdown
  const shutdown = async (signal: string): Promise<void> => {
    logger.info("Received %s, shutting down gracefully", signal);
    server.close(async () => {
      await closeAzureClients();
      logger.info("Shutdown complete");
      process.exit(0);
    });
    // Force exit after 10 seconds
    setTimeout(() => process.exit(1), 10_000);
  };

  process.on("SIGTERM", () => shutdown("SIGTERM"));
  process.on("SIGINT", () => shutdown("SIGINT"));
}

main().catch((err) => {
  logger.error("Failed to start application:", err);
  process.exit(1);
});
```

### app.ts
```typescript
import express from "express";
import { healthRouter } from "./routes/health";
import { apiRouter } from "./routes/api";
import { errorHandler } from "./middleware/error-handler";
import { requestLogger } from "./middleware/request-logger";

export const app = express();

// Middleware
app.use(express.json());
app.use(requestLogger);

// Routes
app.use(healthRouter);
app.use("/api/v1", apiRouter);

// Error handling (must be last)
app.use(errorHandler);
```

### config.ts
```typescript
import dotenv from "dotenv";

dotenv.config();

export const config = {
  // Application
  appName: process.env.APP_NAME || "prototype-api",
  appVersion: process.env.APP_VERSION || "0.1.0",
  nodeEnv: process.env.NODE_ENV || "development",

  // Server
  host: process.env.HOST || "0.0.0.0",
  port: parseInt(process.env.PORT || "8000", 10),
  logLevel: process.env.LOG_LEVEL || "info",

  // Azure Identity
  azureClientId: process.env.AZURE_CLIENT_ID || "",

  // Azure Service Endpoints
  azureStorageEndpoint: process.env.AZURE_STORAGE_ENDPOINT || "",
  azureKeyvaultEndpoint: process.env.AZURE_KEYVAULT_ENDPOINT || "",
  azureCosmosEndpoint: process.env.AZURE_COSMOS_ENDPOINT || "",
  azureServiceBusNamespace: process.env.AZURE_SERVICEBUS_NAMESPACE || "",
  azureOpenaiEndpoint: process.env.AZURE_OPENAI_ENDPOINT || "",
  azureOpenaiDeployment: process.env.AZURE_OPENAI_DEPLOYMENT || "",
} as const;
```

## Fastify Application Structure (Alternative)

```typescript
// src/app.ts
import Fastify from "fastify";
import { config } from "./config";
import { healthRoutes } from "./routes/health";
import { apiRoutes } from "./routes/api";
import { initAzureClients, closeAzureClients } from "./services/azure-clients";
import { logger } from "./logger";

export async function buildApp() {
  const app = Fastify({
    logger: {
      level: config.logLevel,
      transport:
        config.nodeEnv === "development"
          ? { target: "pino-pretty", options: { colorize: true } }
          : undefined,
    },
  });

  // Lifecycle hooks
  app.addHook("onReady", async () => {
    await initAzureClients();
  });

  app.addHook("onClose", async () => {
    await closeAzureClients();
  });

  // Error handler
  app.setErrorHandler(async (error, request, reply) => {
    app.log.error(error, "Request error on %s %s", request.method, request.url);
    return reply.status(error.statusCode || 500).send({
      error: error.statusCode && error.statusCode < 500 ? error.message : "Internal server error",
    });
  });

  // Routes
  await app.register(healthRoutes);
  await app.register(apiRoutes, { prefix: "/api/v1" });

  return app;
}
```

```typescript
// src/index.ts
import { buildApp } from "./app";
import { config } from "./config";

async function main() {
  const app = await buildApp();
  await app.listen({ host: config.host, port: config.port });
}

main().catch((err) => {
  console.error("Failed to start:", err);
  process.exit(1);
});
```

## Azure SDK Initialization with DefaultAzureCredential

```typescript
// src/services/azure-clients.ts
import {
  TokenCredential,
  DefaultAzureCredential,
  ManagedIdentityCredential,
} from "@azure/identity";
import { BlobServiceClient } from "@azure/storage-blob";
import { SecretClient } from "@azure/keyvault-secrets";
import { CosmosClient } from "@azure/cosmos";
import { ServiceBusClient } from "@azure/service-bus";
import { config } from "../config";
import { logger } from "../logger";

let credential: TokenCredential | null = null;
let blobClient: BlobServiceClient | null = null;
let secretClient: SecretClient | null = null;
let cosmosClient: CosmosClient | null = null;
let serviceBusClient: ServiceBusClient | null = null;

export async function initAzureClients(): Promise<void> {
  // Shared credential
  credential = config.azureClientId
    ? new ManagedIdentityCredential(config.azureClientId)
    : new DefaultAzureCredential();

  // Storage
  if (config.azureStorageEndpoint) {
    blobClient = new BlobServiceClient(config.azureStorageEndpoint, credential);
    logger.info("Initialized Azure Storage client");
  }

  // Key Vault
  if (config.azureKeyvaultEndpoint) {
    secretClient = new SecretClient(config.azureKeyvaultEndpoint, credential);
    logger.info("Initialized Key Vault client");
  }

  // Cosmos DB
  if (config.azureCosmosEndpoint) {
    cosmosClient = new CosmosClient({
      endpoint: config.azureCosmosEndpoint,
      aadCredentials: credential,
    });
    logger.info("Initialized Cosmos DB client");
  }

  // Service Bus
  if (config.azureServiceBusNamespace) {
    serviceBusClient = new ServiceBusClient(config.azureServiceBusNamespace, credential);
    logger.info("Initialized Service Bus client");
  }
}

export async function closeAzureClients(): Promise<void> {
  if (serviceBusClient) {
    await serviceBusClient.close();
  }
  logger.info("Azure clients closed");
}

export function getBlobClient(): BlobServiceClient {
  if (!blobClient) throw new Error("Blob client not initialized");
  return blobClient;
}

export function getSecretClient(): SecretClient {
  if (!secretClient) throw new Error("Key Vault client not initialized");
  return secretClient;
}

export function getCosmosClient(): CosmosClient {
  if (!cosmosClient) throw new Error("Cosmos client not initialized");
  return cosmosClient;
}

export function getServiceBusClient(): ServiceBusClient {
  if (!serviceBusClient) throw new Error("Service Bus client not initialized");
  return serviceBusClient;
}
```

## Dockerfile Pattern (Multi-Stage Build)

```dockerfile
# Stage 1: Build
FROM node:20-alpine AS builder

WORKDIR /build

COPY package.json package-lock.json tsconfig.json ./
RUN npm ci

COPY src/ ./src/
RUN npm run build

# Stage 2: Production dependencies
FROM node:20-alpine AS deps

WORKDIR /deps
COPY package.json package-lock.json ./
RUN npm ci --omit=dev

# Stage 3: Runtime
FROM node:20-alpine AS runtime

# Security: non-root user
RUN addgroup -S appgroup && adduser -S appuser -G appgroup

WORKDIR /app

# Copy built output and production dependencies
COPY --from=deps /deps/node_modules ./node_modules
COPY --from=builder /build/dist ./dist
COPY package.json ./

RUN chown -R appuser:appgroup /app
USER appuser

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD wget --no-verbose --tries=1 --spider http://localhost:8000/health || exit 1

CMD ["node", "dist/index.js"]
```

## Health Check Endpoints

### Express
```typescript
// src/routes/health.ts
import { Router, Request, Response } from "express";
import { config } from "../config";
import { logger } from "../logger";

export const healthRouter = Router();

const startTime = Date.now();

healthRouter.get("/health", (_req: Request, res: Response) => {
  res.json({ status: "healthy" });
});

healthRouter.get("/healthz", (_req: Request, res: Response) => {
  res.sendStatus(200);
});

healthRouter.get("/readyz", async (_req: Request, res: Response) => {
  const checks: Record<string, string> = {};
  let overallHealthy = true;

  // Check Azure Storage
  if (config.azureStorageEndpoint) {
    try {
      const { getBlobClient } = await import("../services/azure-clients");
      const client = getBlobClient();
      await client.getAccountInfo();
      checks.azure_storage = "healthy";
    } catch (err) {
      logger.warn("Storage health check failed: %s", (err as Error).message);
      checks.azure_storage = "unhealthy";
      overallHealthy = false;
    }
  }

  const uptimeSeconds = Math.round((Date.now() - startTime) / 1000);

  res.status(overallHealthy ? 200 : 503).json({
    status: overallHealthy ? "healthy" : "degraded",
    uptime_seconds: uptimeSeconds,
    version: config.appVersion,
    checks,
  });
});
```

### Fastify
```typescript
// src/routes/health.ts
import { FastifyPluginAsync } from "fastify";
import { config } from "../config";

const startTime = Date.now();

export const healthRoutes: FastifyPluginAsync = async (app) => {
  app.get("/health", async () => {
    return { status: "healthy" };
  });

  app.get("/healthz", async (_request, reply) => {
    return reply.status(200).send();
  });

  app.get("/readyz", async (_request, reply) => {
    const checks: Record<string, string> = {};
    let overallHealthy = true;

    if (config.azureStorageEndpoint) {
      try {
        const { getBlobClient } = await import("../services/azure-clients");
        await getBlobClient().getAccountInfo();
        checks.azure_storage = "healthy";
      } catch {
        checks.azure_storage = "unhealthy";
        overallHealthy = false;
      }
    }

    return reply.status(overallHealthy ? 200 : 503).send({
      status: overallHealthy ? "healthy" : "degraded",
      uptime_seconds: Math.round((Date.now() - startTime) / 1000),
      version: config.appVersion,
      checks,
    });
  });
};
```

## package.json Management

```json
{
  "name": "prototype-api",
  "version": "0.1.0",
  "private": true,
  "type": "module",
  "scripts": {
    "dev": "tsx watch src/index.ts",
    "build": "tsc",
    "start": "node dist/index.js",
    "lint": "eslint src/",
    "lint:fix": "eslint src/ --fix",
    "test": "vitest run",
    "test:watch": "vitest",
    "test:coverage": "vitest run --coverage"
  },
  "dependencies": {
    "express": "^4.21.0",
    "dotenv": "^16.4.0",

    "@azure/identity": "^4.5.0",
    "@azure/storage-blob": "^12.26.0",
    "@azure/keyvault-secrets": "^4.9.0",
    "@azure/cosmos": "^4.2.0",
    "@azure/service-bus": "^7.10.0",
    "openai": "^4.77.0",

    "winston": "^3.17.0"
  },
  "devDependencies": {
    "@types/express": "^5.0.0",
    "@types/node": "^22.10.0",
    "typescript": "^5.7.0",
    "tsx": "^4.19.0",

    "vitest": "^2.1.0",
    "supertest": "^7.0.0",
    "@types/supertest": "^6.0.0",

    "eslint": "^9.16.0",
    "@typescript-eslint/eslint-plugin": "^8.18.0",
    "@typescript-eslint/parser": "^8.18.0"
  }
}
```

## .env.example Pattern

```bash
# Application
APP_NAME=prototype-api
APP_VERSION=0.1.0
NODE_ENV=development

# Server
HOST=0.0.0.0
PORT=8000
LOG_LEVEL=info

# Azure Identity (leave empty for DefaultAzureCredential chain)
AZURE_CLIENT_ID=

# Azure Service Endpoints (no secrets - just URLs)
AZURE_STORAGE_ENDPOINT=https://<storage-account>.blob.core.windows.net
AZURE_KEYVAULT_ENDPOINT=https://<keyvault-name>.vault.azure.net
AZURE_COSMOS_ENDPOINT=https://<cosmos-account>.documents.azure.com:443
AZURE_SERVICEBUS_NAMESPACE=<namespace>.servicebus.windows.net
AZURE_OPENAI_ENDPOINT=https://<openai-resource>.openai.azure.com
AZURE_OPENAI_DEPLOYMENT=gpt-4o
```

## Logging (Winston)

```typescript
// src/logger.ts
import winston from "winston";
import { config } from "./config";

const devFormat = winston.format.combine(
  winston.format.colorize(),
  winston.format.timestamp({ format: "HH:mm:ss" }),
  winston.format.printf(({ timestamp, level, message, ...meta }) => {
    const metaStr = Object.keys(meta).length ? ` ${JSON.stringify(meta)}` : "";
    return `${timestamp} ${level}: ${message}${metaStr}`;
  })
);

const prodFormat = winston.format.combine(
  winston.format.timestamp({ format: "YYYY-MM-DDTHH:mm:ssZ" }),
  winston.format.json()
);

export const logger = winston.createLogger({
  level: config.logLevel,
  format: config.nodeEnv === "production" ? prodFormat : devFormat,
  transports: [new winston.transports.Console()],
  // Suppress Azure SDK noise
  silent: false,
});
```

### Logging with Pino (Alternative)

```typescript
// src/logger.ts
import pino from "pino";
import { config } from "./config";

export const logger = pino({
  level: config.logLevel,
  transport:
    config.nodeEnv === "development"
      ? { target: "pino-pretty", options: { colorize: true } }
      : undefined,
  serializers: pino.stdSerializers,
  base: { app: config.appName, version: config.appVersion },
});
```

## Error Handling Middleware

```typescript
// src/middleware/error-handler.ts
import { Request, Response, NextFunction } from "express";
import { RestError } from "@azure/core-rest-pipeline";
import { logger } from "../logger";

export interface AppError extends Error {
  statusCode?: number;
  detail?: string;
}

export class NotFoundError extends Error implements AppError {
  statusCode = 404;
  constructor(resource: string, identifier: string) {
    super(`${resource} not found: ${identifier}`);
    this.name = "NotFoundError";
  }
}

export class ValidationError extends Error implements AppError {
  statusCode = 422;
  detail?: string;
  constructor(message: string, detail?: string) {
    super(message);
    this.name = "ValidationError";
    this.detail = detail;
  }
}

export function errorHandler(
  err: Error,
  req: Request,
  res: Response,
  _next: NextFunction
): void {
  // Azure SDK errors
  if (err instanceof RestError) {
    const status = err.statusCode || 502;
    if (status === 401) {
      logger.error("Azure authentication failed: %s", err.message);
      res.status(401).json({ error: "Authentication failed" });
      return;
    }
    if (status === 403) {
      logger.error("Azure authorization failed: %s", err.message);
      res.status(403).json({ error: "Authorization failed" });
      return;
    }
    if (status === 404) {
      logger.warn("Azure resource not found: %s", err.message);
      res.status(404).json({ error: "Resource not found" });
      return;
    }
    logger.error("Azure SDK error (status=%d): %s", status, err.message);
    res.status(502).json({ error: "Azure service error" });
    return;
  }

  // Application errors
  const appErr = err as AppError;
  if (appErr.statusCode && appErr.statusCode < 500) {
    logger.warn("Application error: %s (status=%d)", err.message, appErr.statusCode);
    const body: Record<string, string> = { error: err.message };
    if (appErr.detail) body.detail = appErr.detail;
    res.status(appErr.statusCode).json(body);
    return;
  }

  // Unhandled errors
  logger.error(err, "Unhandled exception on %s %s", req.method, req.path);
  res.status(500).json({ error: "Internal server error" });
}
```

### Request Logger Middleware

```typescript
// src/middleware/request-logger.ts
import { Request, Response, NextFunction } from "express";
import { logger } from "../logger";

export function requestLogger(req: Request, res: Response, next: NextFunction): void {
  const start = Date.now();

  res.on("finish", () => {
    const duration = Date.now() - start;
    const level = res.statusCode >= 400 ? "warn" : "info";
    logger[level]("%s %s %d %dms", req.method, req.path, res.statusCode, duration);
  });

  next();
}
```

## TypeScript Configuration

```json
{
  "compilerOptions": {
    "target": "ES2022",
    "module": "NodeNext",
    "moduleResolution": "NodeNext",
    "lib": ["ES2022"],
    "outDir": "dist",
    "rootDir": "src",
    "strict": true,
    "esModuleInterop": true,
    "skipLibCheck": true,
    "forceConsistentCasingInFileNames": true,
    "resolveJsonModule": true,
    "declaration": true,
    "declarationMap": true,
    "sourceMap": true,
    "noUnusedLocals": true,
    "noUnusedParameters": true,
    "noImplicitReturns": true,
    "noFallthroughCasesInSwitch": true
  },
  "include": ["src/**/*"],
  "exclude": ["node_modules", "dist", "tests"]
}
```

## Testing Patterns (Vitest)

### Test Setup
```typescript
// tests/setup.ts
import { vi } from "vitest";

// Mock Azure clients globally
vi.mock("../src/services/azure-clients", () => ({
  initAzureClients: vi.fn().mockResolvedValue(undefined),
  closeAzureClients: vi.fn().mockResolvedValue(undefined),
  getBlobClient: vi.fn(),
  getSecretClient: vi.fn(),
  getCosmosClient: vi.fn(),
  getServiceBusClient: vi.fn(),
}));

// Mock environment
process.env.APP_NAME = "test-api";
process.env.NODE_ENV = "test";
process.env.LOG_LEVEL = "silent";
```

### vitest.config.ts
```typescript
import { defineConfig } from "vitest/config";

export default defineConfig({
  test: {
    globals: true,
    environment: "node",
    setupFiles: ["./tests/setup.ts"],
    coverage: {
      provider: "v8",
      include: ["src/**/*.ts"],
      exclude: ["src/index.ts"],
    },
  },
});
```

### Health Check Tests
```typescript
// tests/health.test.ts
import { describe, it, expect, vi } from "vitest";
import supertest from "supertest";
import { app } from "../src/app";

describe("Health endpoints", () => {
  const request = supertest(app);

  it("GET /health returns 200 with status healthy", async () => {
    const res = await request.get("/health");
    expect(res.status).toBe(200);
    expect(res.body).toEqual({ status: "healthy" });
  });

  it("GET /healthz returns 200", async () => {
    const res = await request.get("/healthz");
    expect(res.status).toBe(200);
  });

  it("GET /readyz returns degraded when storage is down", async () => {
    const { getBlobClient } = await import("../src/services/azure-clients");
    const mockClient = {
      getAccountInfo: vi.fn().mockRejectedValue(new Error("Connection refused")),
    };
    vi.mocked(getBlobClient).mockReturnValue(mockClient as any);

    // Set the endpoint so the check runs
    process.env.AZURE_STORAGE_ENDPOINT = "https://test.blob.core.windows.net";

    const res = await request.get("/readyz");
    expect(res.status).toBe(503);
    expect(res.body.status).toBe("degraded");
    expect(res.body.checks.azure_storage).toBe("unhealthy");

    // Cleanup
    delete process.env.AZURE_STORAGE_ENDPOINT;
  });
});
```

### API Tests
```typescript
// tests/api.test.ts
import { describe, it, expect, vi, beforeEach } from "vitest";
import supertest from "supertest";
import { app } from "../src/app";

describe("API endpoints", () => {
  const request = supertest(app);

  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("GET /api/v1/items returns list of items", async () => {
    const { getBlobClient } = await import("../src/services/azure-clients");

    const mockBlobItems = [{ name: "item1.json" }, { name: "item2.json" }];
    const mockContainer = {
      listBlobsFlat: vi.fn().mockReturnValue({
        [Symbol.asyncIterator]: async function* () {
          for (const item of mockBlobItems) {
            yield item;
          }
        },
      }),
    };
    const mockClient = {
      getContainerClient: vi.fn().mockReturnValue(mockContainer),
    };
    vi.mocked(getBlobClient).mockReturnValue(mockClient as any);

    const res = await request.get("/api/v1/items");
    expect(res.status).toBe(200);
    expect(res.body).toHaveLength(2);
  });

  it("POST /api/v1/items creates an item", async () => {
    const { getBlobClient } = await import("../src/services/azure-clients");

    const mockBlockBlobClient = {
      upload: vi.fn().mockResolvedValue({}),
    };
    const mockContainer = {
      createIfNotExists: vi.fn().mockResolvedValue({}),
      getBlockBlobClient: vi.fn().mockReturnValue(mockBlockBlobClient),
    };
    const mockClient = {
      getContainerClient: vi.fn().mockReturnValue(mockContainer),
    };
    vi.mocked(getBlobClient).mockReturnValue(mockClient as any);

    const res = await request
      .post("/api/v1/items")
      .send({ name: "Test Item", description: "A test" });

    expect(res.status).toBe(201);
    expect(res.body).toHaveProperty("id");
  });

  it("returns 500 for unhandled errors", async () => {
    const { getBlobClient } = await import("../src/services/azure-clients");
    vi.mocked(getBlobClient).mockImplementation(() => {
      throw new Error("Unexpected failure");
    });

    const res = await request.get("/api/v1/items");
    expect(res.status).toBe(500);
    expect(res.body).toEqual({ error: "Internal server error" });
  });
});
```

### Testing with Jest (Alternative)

```typescript
// jest.config.ts
import type { Config } from "jest";

const config: Config = {
  preset: "ts-jest/presets/default-esm",
  testEnvironment: "node",
  roots: ["<rootDir>/tests"],
  moduleNameMapper: {
    "^(\\.{1,2}/.*)\\.js$": "$1",
  },
  transform: {
    "^.+\\.tsx?$": ["ts-jest", { useESM: true }],
  },
  setupFilesAfterSetup: ["./tests/setup.ts"],
  collectCoverageFrom: ["src/**/*.ts", "!src/index.ts"],
};

export default config;
```
