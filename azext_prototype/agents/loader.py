"""Agent loader — loads custom agents from YAML or Python files."""

import importlib.util
import logging
from pathlib import Path

import yaml
from knack.util import CLIError

from azext_prototype.agents.base import AgentCapability, AgentContext, BaseAgent
from azext_prototype.ai.provider import AIMessage, AIResponse

logger = logging.getLogger(__name__)


class YAMLAgent(BaseAgent):
    """Agent loaded from a YAML definition.

    Delegates execution to the AI provider using the system prompt
    and constraints defined in the YAML file.
    """

    def __init__(self, definition: dict):
        """Create an agent from a YAML definition dict.

        Args:
            definition: Parsed YAML with name, description, system_prompt, etc.
        """
        name = definition.get("name")
        if not name:
            raise CLIError("YAML agent definition must include 'name'.")

        # Parse capabilities
        raw_caps = definition.get("capabilities", [])
        capabilities = []
        for cap in raw_caps:
            try:
                capabilities.append(AgentCapability(cap))
            except ValueError:
                logger.warning("Unknown capability '%s' in agent '%s', skipping.", cap, name)

        super().__init__(
            name=name,
            description=definition.get("description", ""),
            capabilities=capabilities,
            constraints=definition.get("constraints", []),
            system_prompt=definition.get("system_prompt", ""),
        )

        self._is_builtin = False
        self._definition = definition

        # Additional YAML-specific config
        self.tools = definition.get("tools", [])
        self.role = definition.get("role", "general")
        self.examples = definition.get("examples", [])

    def execute(self, context: AgentContext, task: str) -> AIResponse:
        """Execute using the AI provider with this agent's system prompt."""
        messages = self.get_system_messages()

        # Add any examples as few-shot prompts
        for example in self.examples:
            if "user" in example:
                messages.append(AIMessage(role="user", content=example["user"]))
            if "assistant" in example:
                messages.append(AIMessage(role="assistant", content=example["assistant"]))

        # Add conversation history
        messages.extend(context.conversation_history)

        # Add the current task
        messages.append(AIMessage(role="user", content=task))

        assert context.ai_provider is not None
        return context.ai_provider.chat(messages)

    def can_handle(self, task_description: str) -> float:
        """Score task relevance based on keywords in description and role."""
        task_lower = task_description.lower()
        score = 0.3  # base score

        # Check if role keywords match
        if self.role and self.role.lower() in task_lower:
            score += 0.3

        # Check if name keywords match
        name_parts = self.name.lower().replace("-", " ").replace("_", " ").split()
        for part in name_parts:
            if part in task_lower:
                score += 0.15

        return min(score, 1.0)


def load_yaml_agent(file_path: str) -> BaseAgent:
    """Load an agent from a YAML file.

    Args:
        file_path: Path to the YAML agent definition.

    Returns:
        YAMLAgent instance.

    Raises:
        CLIError if file is invalid or missing required fields.
    """
    path = Path(file_path)

    if not path.exists():
        raise CLIError(f"Agent definition file not found: {file_path}")

    if path.suffix not in (".yaml", ".yml"):
        raise CLIError(f"Expected .yaml or .yml file, got: {path.suffix}")

    try:
        with open(path, "r", encoding="utf-8") as f:
            definition = yaml.safe_load(f)
    except yaml.YAMLError as e:
        raise CLIError(f"Invalid YAML in agent definition: {e}")

    if not isinstance(definition, dict):
        raise CLIError("Agent YAML must be a mapping (dict) at the top level.")

    return YAMLAgent(definition)


def load_python_agent(file_path: str) -> BaseAgent:
    """Load a custom agent from a Python file.

    The Python file must define a class that subclasses BaseAgent
    and a module-level variable `AGENT_CLASS` pointing to it,
    or contain exactly one BaseAgent subclass.

    Args:
        file_path: Path to the Python agent file.

    Returns:
        Instantiated BaseAgent subclass.

    Raises:
        CLIError if file is invalid or no agent class is found.
    """
    path = Path(file_path)

    if not path.exists():
        raise CLIError(f"Agent file not found: {file_path}")

    if path.suffix != ".py":
        raise CLIError(f"Expected .py file, got: {path.suffix}")

    try:
        spec = importlib.util.spec_from_file_location(f"custom_agent_{path.stem}", str(path))
        if spec is None or spec.loader is None:
            raise CLIError(f"Cannot load module spec from {file_path}")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)  # type: ignore[union-attr]
    except CLIError:
        raise
    except Exception as e:
        raise CLIError(f"Failed to load Python agent from {file_path}: {e}")

    # Check for explicit AGENT_CLASS
    if hasattr(module, "AGENT_CLASS"):
        agent_cls = module.AGENT_CLASS
        if isinstance(agent_cls, type) and issubclass(agent_cls, BaseAgent):
            return agent_cls()  # type: ignore[call-arg]  # concrete subclass provides defaults
        raise CLIError(f"AGENT_CLASS in {file_path} must be a BaseAgent subclass.")

    # Auto-discover BaseAgent subclass
    agent_classes = []
    for attr_name in dir(module):
        attr = getattr(module, attr_name)
        if isinstance(attr, type) and issubclass(attr, BaseAgent) and attr is not BaseAgent:
            agent_classes.append(attr)

    if len(agent_classes) == 0:
        raise CLIError(f"No BaseAgent subclass found in {file_path}. " "Define a class that extends BaseAgent.")

    if len(agent_classes) > 1:
        raise CLIError(
            f"Multiple BaseAgent subclasses found in {file_path}: "
            f"{[c.__name__ for c in agent_classes]}. "
            "Set AGENT_CLASS to specify which one to use."
        )

    return agent_classes[0]()  # type: ignore[call-arg]  # concrete subclass provides defaults


def load_agents_from_directory(directory: str) -> list[BaseAgent]:
    """Load all agent definitions from a directory.

    Supports both .yaml and .py files.

    Args:
        directory: Path to directory containing agent definitions.

    Returns:
        List of loaded agents.
    """
    path = Path(directory)
    if not path.is_dir():
        logger.debug("Agent directory does not exist: %s", directory)
        return []

    agents = []
    for file_path in sorted(path.iterdir()):
        try:
            if file_path.suffix in (".yaml", ".yml"):
                agents.append(load_yaml_agent(str(file_path)))
            elif file_path.suffix == ".py" and not file_path.name.startswith("_"):
                agents.append(load_python_agent(str(file_path)))
        except CLIError as e:
            logger.warning("Failed to load agent from %s: %s", file_path, e)

    logger.info("Loaded %d agents from %s", len(agents), directory)
    return agents
