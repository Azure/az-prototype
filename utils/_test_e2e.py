"""End-to-end test of the updated CopilotProvider against the enterprise endpoint."""

from azext_prototype.ai.copilot_provider import CopilotProvider
from azext_prototype.ai.provider import AIMessage

p = CopilotProvider()

# Test 1: list_models (dynamic from /models endpoint)
models = p.list_models()
print(f"Models returned: {len(models)}")
for m in models[:5]:
    print(f"  {m['id']}")
if len(models) > 5:
    print(f"  ... and {len(models) - 5} more")

print()

# Test 2: chat completion with Claude (default model)
print(f"Using model: {p._model}")
msg = AIMessage(role="user", content="Reply with exactly: ENTERPRISE_OK")
resp = p.chat([msg])
print(f"Chat response type: {type(resp).__name__}")
print(f"Chat content: {resp.content[:120] if hasattr(resp, 'content') else repr(resp)}")
