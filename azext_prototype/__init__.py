"""Azure CLI Extension: az prototype — Innovation Factory rapid prototyping."""

try:
    from azure.cli.core import AzCommandsLoader
except ImportError:
    # Azure CLI not installed — allow submodules (e.g. policy validator)
    # to be imported standalone without the full CLI runtime.
    AzCommandsLoader = None  # type: ignore[assignment,misc]

if AzCommandsLoader is not None:
    from azext_prototype._help import helps  # type: ignore[attr-defined]  # noqa: F401

    class PrototypeCommandsLoader(AzCommandsLoader):
        """Command loader for az prototype extension."""

        def __init__(self, cli_ctx=None):
            from azure.cli.core.commands import CliCommandType

            prototype_custom = CliCommandType(operations_tmpl="azext_prototype.custom#{}")
            super().__init__(cli_ctx=cli_ctx, custom_command_type=prototype_custom)

        def load_command_table(self, args):
            from azext_prototype.commands import load_command_table

            load_command_table(self, args)
            return self.command_table

        def load_arguments(self, command):
            from azext_prototype._params import load_arguments

            load_arguments(self, command)

    COMMAND_LOADER_CLS = PrototypeCommandsLoader
