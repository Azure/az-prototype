"""Built-in agents that ship with the extension."""

from azext_prototype.agents.builtin.advisor import AdvisorAgent
from azext_prototype.agents.builtin.app_developer import AppDeveloperAgent
from azext_prototype.agents.builtin.application_architect import ApplicationArchitectAgent
from azext_prototype.agents.builtin.bicep_agent import BicepAgent
from azext_prototype.agents.builtin.biz_analyst import BizAnalystAgent
from azext_prototype.agents.builtin.cloud_architect import CloudArchitectAgent
from azext_prototype.agents.builtin.cost_analyst import CostAnalystAgent
from azext_prototype.agents.builtin.csharp_developer import CSharpDeveloperAgent
from azext_prototype.agents.builtin.data_architect import DataArchitectAgent
from azext_prototype.agents.builtin.doc_agent import DocumentationAgent
from azext_prototype.agents.builtin.governor_agent import GovernorAgent
from azext_prototype.agents.builtin.infrastructure_architect import InfrastructureArchitectAgent
from azext_prototype.agents.builtin.monitoring_agent import MonitoringAgent
from azext_prototype.agents.builtin.project_manager import ProjectManagerAgent
from azext_prototype.agents.builtin.python_developer import PythonDeveloperAgent
from azext_prototype.agents.builtin.qa_engineer import QAEngineerAgent
from azext_prototype.agents.builtin.react_developer import ReactDeveloperAgent
from azext_prototype.agents.builtin.security_architect import SecurityArchitectAgent
from azext_prototype.agents.builtin.terraform_agent import TerraformAgent

ALL_BUILTIN_AGENTS = [
    # Architects
    CloudArchitectAgent,
    InfrastructureArchitectAgent,
    DataArchitectAgent,
    ApplicationArchitectAgent,
    SecurityArchitectAgent,
    # IaC agents
    TerraformAgent,
    BicepAgent,
    # Language-specific developers
    CSharpDeveloperAgent,
    PythonDeveloperAgent,
    ReactDeveloperAgent,
    AppDeveloperAgent,  # Generic fallback for unsupported languages (Java, Go, Rust, etc.)
    # Supporting agents
    DocumentationAgent,
    QAEngineerAgent,
    BizAnalystAgent,
    CostAnalystAgent,
    ProjectManagerAgent,
    MonitoringAgent,
    GovernorAgent,
    AdvisorAgent,
]


def register_all_builtin(registry):
    """Register all built-in agents into the registry."""
    for agent_cls in ALL_BUILTIN_AGENTS:
        registry.register_builtin(agent_cls())
