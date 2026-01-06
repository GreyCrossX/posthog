from posthog.schema import AgentMode

from ee.hogai.core.plan_mode import PlanModeExecutable, PlanModeToolsExecutable
from ee.hogai.utils.types.base import AssistantState, PartialAssistantState

SWITCH_TO_EXECUTION_MODE_PROMPT = """
Successfully switched to execution mode. Planning is complete, you can now proceed with the actual task.
"""


class ChatAgentPlanExecutable(PlanModeExecutable):
    """
    Executable for the chat agent's plan mode.
    Inherits from PlanModeExecutable which sets supermode=PLAN on first turn or new human message.
    """

    pass


class ChatAgentPlanToolsExecutable(PlanModeToolsExecutable):
    """
    Executable for handling tool calls in the chat agent's plan mode.
    Transitions to execution mode and resets supermode to None.
    """

    @property
    def transition_supermode(self) -> None:
        # Chat agent exits plan mode entirely (supermode becomes None)
        return None

    @property
    def transition_prompt(self) -> str:
        return SWITCH_TO_EXECUTION_MODE_PROMPT

    def _should_transition(self, state: AssistantState, result: PartialAssistantState) -> bool:
        # Transition when switching from plan mode to execution mode
        return state.supermode == AgentMode.PLAN and result.agent_mode == AgentMode.EXECUTION
