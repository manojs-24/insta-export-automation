from web_automation.config.schema import AutomationConfig
from web_automation.engine.context import ExecutionContext
from web_automation.engine.executor import ExecutionResult, Executor


def run_config(config: AutomationConfig, ctx: ExecutionContext) -> ExecutionResult:
    """Execute a validated config: its timeout and failure policy apply to every step."""
    ctx.timeout_ms = config.execution.timeout
    return Executor(ctx).run(
        config.to_steps(), name=config.name, on_failure=config.execution.on_failure
    )
