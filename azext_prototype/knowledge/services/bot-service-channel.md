---
service_namespace: Microsoft.BotService/botServices/channels
display_name: Bot Service Channel
depends_on:
  - Microsoft.BotService/botServices
---

# Bot Service Channel

> Connects an Azure Bot to external messaging platforms (Microsoft Teams, Slack, Web Chat, Direct Line, etc.) for multi-channel bot experiences.

## When to Use
- Enable Microsoft Teams integration for enterprise bots
- Add Web Chat channel for website-embedded chat widgets
- Configure Direct Line channel for custom client applications
- Connect to Slack, Facebook Messenger, or other third-party platforms
- Every bot needs at least one channel to be reachable by users

## POC Defaults
- **Web Chat**: Enabled by default on bot creation
- **Direct Line**: Enabled for custom client apps and testing
- **Microsoft Teams**: Most common enterprise channel
- **Channel name convention**: `MsTeamsChannel`, `DirectLineChannel`, `WebChatChannel`

## Terraform Patterns

### Basic Resource
```hcl
resource "azapi_resource" "teams_channel" {
  type      = "Microsoft.BotService/botServices/channels@2022-09-15"
  name      = "MsTeamsChannel"
  parent_id = azapi_resource.bot_service.id
  location  = "global"

  body = {
    properties = {
      channelName = "MsTeamsChannel"
      properties = {
        isEnabled = true
        enableCalling = false
      }
    }
  }
}

resource "azapi_resource" "directline_channel" {
  type      = "Microsoft.BotService/botServices/channels@2022-09-15"
  name      = "DirectLineChannel"
  parent_id = azapi_resource.bot_service.id
  location  = "global"

  body = {
    properties = {
      channelName = "DirectLineChannel"
      properties = {
        sites = [
          {
            siteName  = "default"
            isEnabled = true
            isV1Enabled = false
            isV3Enabled = true
          }
        ]
      }
    }
  }
}
```

### RBAC Assignment
```hcl
# Channel management inherits from the Bot Service RBAC.
# No separate RBAC role exists for individual channels.
```

## Bicep Patterns

### Basic Resource
```bicep
resource teamsChannel 'Microsoft.BotService/botServices/channels@2022-09-15' = {
  parent: botService
  name: 'MsTeamsChannel'
  location: 'global'
  properties: {
    channelName: 'MsTeamsChannel'
    properties: {
      isEnabled: true
      enableCalling: false
    }
  }
}

resource directLineChannel 'Microsoft.BotService/botServices/channels@2022-09-15' = {
  parent: botService
  name: 'DirectLineChannel'
  location: 'global'
  properties: {
    channelName: 'DirectLineChannel'
    properties: {
      sites: [
        {
          siteName: 'default'
          isEnabled: true
          isV1Enabled: false
          isV3Enabled: true
        }
      ]
    }
  }
}
```

## Application Code

### Python
```python
from azure.identity import DefaultAzureCredential
from botbuilder.core import TurnContext
from botbuilder.schema import Activity

# Channel-specific logic in the bot handler
class MyBot:
    async def on_message_activity(self, turn_context: TurnContext):
        channel = turn_context.activity.channel_id  # "msteams", "directline", "webchat"
        if channel == "msteams":
            # Teams-specific adaptive card response
            await turn_context.send_activity(Activity(type="message", text="Hello from Teams!"))
        else:
            await turn_context.send_activity("Hello from Bot!")
```

### C#
```csharp
using Microsoft.Bot.Builder;
using Microsoft.Bot.Schema;

public class MyBot : ActivityHandler
{
    protected override async Task OnMessageActivityAsync(ITurnContext<IMessageActivity> turnContext, CancellationToken ct)
    {
        var channel = turnContext.Activity.ChannelId; // "msteams", "directline", "webchat"
        if (channel == "msteams")
            await turnContext.SendActivityAsync(MessageFactory.Text("Hello from Teams!"), ct);
        else
            await turnContext.SendActivityAsync(MessageFactory.Text("Hello from Bot!"), ct);
    }
}
```

### Node.js
```typescript
import { ActivityHandler, TurnContext } from "botbuilder";

class MyBot extends ActivityHandler {
  constructor() {
    super();
    this.onMessage(async (context: TurnContext) => {
      const channel = context.activity.channelId; // "msteams", "directline", "webchat"
      if (channel === "msteams") {
        await context.sendActivity("Hello from Teams!");
      } else {
        await context.sendActivity("Hello from Bot!");
      }
    });
  }
}
```

## Common Pitfalls
- **Channel name is the resource name**: The resource name must exactly match the channel identifier (e.g., `MsTeamsChannel`, `DirectLineChannel`). Arbitrary names fail.
- **Location must be 'global'**: Bot Service channels always use `global` as the location, regardless of the bot's region.
- **Teams app registration**: Enabling Teams channel is just the Azure side — you also need a Teams app manifest and deployment to the Teams app catalog.
- **Direct Line secrets rotation**: Direct Line channel secrets should be rotated regularly. The initial secrets are retrievable via the API.
- **Web Chat is auto-created**: The Web Chat channel is created automatically with the bot. Recreating it may cause conflicts.

## Production Backlog Items
- Teams app manifest and deployment to organization app catalog
- Direct Line secret rotation on a schedule
- Enhanced authentication for Direct Line (trusted origins)
- Channel-specific adaptive card templates
- Bot analytics and conversation telemetry per channel
