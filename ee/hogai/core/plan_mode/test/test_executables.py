import pytest
from posthog.test.base import BaseTest
from unittest.mock import AsyncMock, patch

from langchain_core.runnables import RunnableConfig
from parameterized import parameterized

from posthog.schema import AgentMode, AssistantMessage, AssistantToolCall, AssistantToolCallMessage, HumanMessage

from ee.hogai.chat_agent.executables import SWITCH_TO_EXECUTION_MODE_PROMPT, ChatAgentPlanToolsExecutable
from ee.hogai.chat_agent.toolkit import ChatAgentToolkitManager
from ee.hogai.context import AssistantContextManager
from ee.hogai.utils.types import AssistantState, PartialAssistantState
from ee.hogai.utils.types.base import AssistantNodeName, NodePath


class TestPlanModeToolsExecutable(BaseTest):
    @pytest.mark.asyncio
    async def test_transitions_when_should_transition_true(self):
        config = RunnableConfig(configurable={})
        node_path = (NodePath(name=AssistantNodeName.ROOT, message_id="test_id", tool_call_id="test_tool_call_id"),)
        context_manager = AssistantContextManager(team=self.team, user=self.user, config=config)

        executable = ChatAgentPlanToolsExecutable(
            team=self.team,
            user=self.user,
            toolkit_manager_class=ChatAgentToolkitManager,
            node_path=node_path,
        )
        executable._config = config
        executable._context_manager = context_manager

        # Create state that will trigger switch_mode tool call
        state = AssistantState(
            messages=[
                HumanMessage(content="Test"),
                AssistantMessage(
                    content="Switching mode",
                    id="msg-1",
                    tool_calls=[
                        AssistantToolCall(
                            id="tool-1",
                            name="switch_mode",
                            args={"new_mode": "execution"},
                        )
                    ],
                ),
            ],
            supermode=AgentMode.PLAN,
            root_tool_call_id="tool-1",
        )

        # Patch AgentToolsExecutable.arun (the grandparent class)
        with patch(
            "ee.hogai.core.agent_modes.executables.AgentToolsExecutable.arun",
            new_callable=AsyncMock,
        ) as mock_super_arun:
            # Simulate the tool result that triggers transition
            mock_super_arun.return_value = PartialAssistantState(
                messages=[
                    AssistantToolCallMessage(
                        content="Switched",
                        id="tool-msg-1",
                        tool_call_id="tool-1",
                    )
                ],
                agent_mode=AgentMode.EXECUTION,
            )

            result = await executable.arun(state, config)

            self.assertIsInstance(result, PartialAssistantState)
            self.assertIsNone(result.supermode)  # ChatAgent exits plan mode entirely
            self.assertEqual(result.agent_mode, AgentMode.PRODUCT_ANALYTICS)
            last_message = result.messages[-1]
            assert isinstance(last_message, AssistantToolCallMessage)
            self.assertEqual(last_message.content, SWITCH_TO_EXECUTION_MODE_PROMPT)

    @pytest.mark.asyncio
    async def test_no_transition_when_should_transition_false(self):
        config = RunnableConfig(configurable={})
        node_path = (NodePath(name=AssistantNodeName.ROOT, message_id="test_id", tool_call_id="test_tool_call_id"),)
        context_manager = AssistantContextManager(team=self.team, user=self.user, config=config)

        executable = ChatAgentPlanToolsExecutable(
            team=self.team,
            user=self.user,
            toolkit_manager_class=ChatAgentToolkitManager,
            node_path=node_path,
        )
        executable._config = config
        executable._context_manager = context_manager

        state = AssistantState(
            messages=[
                HumanMessage(content="Test"),
                AssistantMessage(
                    content="Using search",
                    id="msg-1",
                    tool_calls=[
                        AssistantToolCall(
                            id="tool-1",
                            name="search",
                            args={"query": "test"},
                        )
                    ],
                ),
            ],
            supermode=AgentMode.PLAN,
            root_tool_call_id="tool-1",
        )

        # Patch AgentToolsExecutable.arun (the grandparent class)
        with patch(
            "ee.hogai.core.agent_modes.executables.AgentToolsExecutable.arun",
            new_callable=AsyncMock,
        ) as mock_super_arun:
            # Simulate a regular tool result (not transitioning)
            mock_super_arun.return_value = PartialAssistantState(
                messages=[
                    AssistantToolCallMessage(
                        content="Search results",
                        id="tool-msg-1",
                        tool_call_id="tool-1",
                    )
                ],
                agent_mode=AgentMode.PRODUCT_ANALYTICS,  # Not EXECUTION
            )

            result = await executable.arun(state, config)

            self.assertIsInstance(result, PartialAssistantState)
            # Result should be unchanged from super().arun()
            last_message = result.messages[-1]
            assert isinstance(last_message, AssistantToolCallMessage)
            self.assertEqual(last_message.content, "Search results")

    @pytest.mark.asyncio
    async def test_raises_value_error_if_last_message_not_tool_call(self):
        config = RunnableConfig(configurable={})
        node_path = (NodePath(name=AssistantNodeName.ROOT, message_id="test_id", tool_call_id="test_tool_call_id"),)
        context_manager = AssistantContextManager(team=self.team, user=self.user, config=config)

        executable = ChatAgentPlanToolsExecutable(
            team=self.team,
            user=self.user,
            toolkit_manager_class=ChatAgentToolkitManager,
            node_path=node_path,
        )
        executable._config = config
        executable._context_manager = context_manager

        state = AssistantState(
            messages=[
                HumanMessage(content="Test"),
                AssistantMessage(
                    content="Switching mode",
                    id="msg-1",
                    tool_calls=[
                        AssistantToolCall(
                            id="tool-1",
                            name="switch_mode",
                            args={"new_mode": "execution"},
                        )
                    ],
                ),
            ],
            supermode=AgentMode.PLAN,
            root_tool_call_id="tool-1",
        )

        # Patch AgentToolsExecutable.arun (the grandparent class)
        with patch(
            "ee.hogai.core.agent_modes.executables.AgentToolsExecutable.arun",
            new_callable=AsyncMock,
        ) as mock_super_arun:
            # Return a result where last message is NOT AssistantToolCallMessage
            mock_super_arun.return_value = PartialAssistantState(
                messages=[AssistantMessage(content="Not a tool call message")],
                agent_mode=AgentMode.EXECUTION,
            )

            with self.assertRaises(ValueError) as context:
                await executable.arun(state, config)

            self.assertIn("AssistantToolCallMessage", str(context.exception))


class TestChatAgentPlanToolsExecutableProperties(BaseTest):
    def test_transition_supermode_returns_none(self):
        node_path = (NodePath(name=AssistantNodeName.ROOT, message_id="test_id", tool_call_id="test_tool_call_id"),)

        executable = ChatAgentPlanToolsExecutable(
            team=self.team,
            user=self.user,
            toolkit_manager_class=ChatAgentToolkitManager,
            node_path=node_path,
        )

        self.assertIsNone(executable.transition_supermode)

    def test_transition_prompt_returns_expected_value(self):
        from ee.hogai.chat_agent.executables import SWITCH_TO_EXECUTION_MODE_PROMPT, ChatAgentPlanToolsExecutable
        from ee.hogai.chat_agent.toolkit import ChatAgentToolkitManager
        from ee.hogai.utils.types.base import AssistantNodeName, NodePath

        node_path = (NodePath(name=AssistantNodeName.ROOT, message_id="test_id", tool_call_id="test_tool_call_id"),)

        executable = ChatAgentPlanToolsExecutable(
            team=self.team,
            user=self.user,
            toolkit_manager_class=ChatAgentToolkitManager,
            node_path=node_path,
        )

        self.assertEqual(executable.transition_prompt, SWITCH_TO_EXECUTION_MODE_PROMPT)

    @parameterized.expand(
        [
            # (supermode, result_agent_mode, expected_should_transition)
            (AgentMode.PLAN, AgentMode.EXECUTION, True),
            (AgentMode.PLAN, AgentMode.PRODUCT_ANALYTICS, False),
            (None, AgentMode.EXECUTION, False),
            (AgentMode.PLAN, AgentMode.SQL, False),
        ]
    )
    def test_should_transition_logic(self, supermode, result_agent_mode, expected):
        node_path = (NodePath(name=AssistantNodeName.ROOT, message_id="test_id", tool_call_id="test_tool_call_id"),)

        executable = ChatAgentPlanToolsExecutable(
            team=self.team,
            user=self.user,
            toolkit_manager_class=ChatAgentToolkitManager,
            node_path=node_path,
        )

        state = AssistantState(
            messages=[HumanMessage(content="Test")],
            supermode=supermode,
        )

        result = PartialAssistantState(
            messages=[],
            agent_mode=result_agent_mode,
        )

        self.assertEqual(executable._should_transition(state, result), expected)
