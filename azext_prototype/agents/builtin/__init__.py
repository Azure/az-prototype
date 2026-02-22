"""Built-in agents that ship with the extension."""

from azext_prototype.agents.builtin.app_developer import AppDeveloperAgent
from azext_prototype.agents.builtin.bicep_agent import BicepAgent
from azext_prototype.agents.builtin.biz_analyst import BizAnalystAgent
from azext_prototype.agents.builtin.cloud_architect import CloudArchitectAgent
from azext_prototype.agents.builtin.cost_analyst import CostAnalystAgent
from azext_prototype.agents.builtin.doc_agent import DocumentationAgent
from azext_prototype.agents.builtin.monitoring_agent import MonitoringAgent
from azext_prototype.agents.builtin.project_manager import ProjectManagerAgent
from azext_prototype.agents.builtin.qa_engineer import QAEngineerAgent
from azext_prototype.agents.builtin.security_reviewer import SecurityReviewerAgent
from azext_prototype.agents.builtin.terraform_agent import TerraformAgent

ALL_BUILTIN_AGENTS = [
    CloudArchitectAgent,
    TerraformAgent,
    BicepAgent,
    AppDeveloperAgent,
    DocumentationAgent,
    QAEngineerAgent,
    BizAnalystAgent,
    CostAnalystAgent,
    ProjectManagerAgent,
    SecurityReviewerAgent,
    MonitoringAgent,
]


def register_all_builtin(registry):
    """Register all built-in agents into the registry."""
    for agent_cls in ALL_BUILTIN_AGENTS:
        registry.register_builtin(agent_cls())
