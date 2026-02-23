"""Command table registration for az prototype."""


def load_command_table(self, _):
    """Register all prototype commands."""

    with self.command_group("prototype", is_preview=True) as g:
        g.custom_command("init", "prototype_init")
        g.custom_command("design", "prototype_design")
        g.custom_command("build", "prototype_build")
        g.custom_command("deploy", "prototype_deploy")
        g.custom_command("status", "prototype_status")

    with self.command_group("prototype analyze", is_preview=True) as g:
        g.custom_command("error", "prototype_analyze_error")
        g.custom_command("costs", "prototype_analyze_costs")

    with self.command_group("prototype config", is_preview=True) as g:
        g.custom_command("init", "prototype_config_init")
        g.custom_command("show", "prototype_config_show")
        g.custom_command("get", "prototype_config_get")
        g.custom_command("set", "prototype_config_set")

    with self.command_group("prototype generate", is_preview=True) as g:
        g.custom_command("backlog", "prototype_generate_backlog")
        g.custom_command("docs", "prototype_generate_docs")
        g.custom_command("speckit", "prototype_generate_speckit")

    with self.command_group("prototype knowledge", is_preview=True) as g:
        g.custom_command("contribute", "prototype_knowledge_contribute")

    with self.command_group("prototype agent", is_preview=True) as g:
        g.custom_command("list", "prototype_agent_list")
        g.custom_command("add", "prototype_agent_add")
        g.custom_command("override", "prototype_agent_override")
        g.custom_command("show", "prototype_agent_show")
        g.custom_command("remove", "prototype_agent_remove")
        g.custom_command("update", "prototype_agent_update")
        g.custom_command("test", "prototype_agent_test")
        g.custom_command("export", "prototype_agent_export")
