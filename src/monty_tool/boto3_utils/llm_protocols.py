"""
Bedrock Converse types and client protocol.

Reference: https://docs.aws.amazon.com/boto3/latest/reference/services/bedrock-runtime/client/converse.html
"""

# Imports

from typing import Literal, NotRequired, Protocol, TypedDict


# JSON data

type JSONValue = (
    str | int | float | bool | None | list[JSONValue] | dict[str, JSONValue]
)


# Conversation content

class SystemContentBlock(TypedDict):
    """
    A system prompt.
    """
    text: str


class ToolUseBlock(TypedDict):
    """
    A tool call requested by the model.
    """
    toolUseId: str
    name: str
    input: JSONValue


class ToolResultContentBlock(TypedDict, total=False):
    """
    Tool output as JSON or text.
    """
    json: JSONValue
    text: str


class ToolResultBlock(TypedDict):
    """
    A tool result matched to a call by toolUseId.
    """
    toolUseId: str
    content: list[ToolResultContentBlock]
    status: NotRequired[Literal['success', 'error']]


class ContentBlock(TypedDict, total=False):
    """
    Text, a tool call, or a tool result within a message.
    """
    text: str
    toolUse: ToolUseBlock
    toolResult: ToolResultBlock


class ConverseMessage(TypedDict):
    """
    A user or assistant message.
    """
    role: Literal['user', 'assistant']
    content: list[ContentBlock]


# Tool definitions and generation settings

class ToolInputSchema(TypedDict):
    """
    JSON Schema for tool arguments.
    """
    json: JSONValue


class ToolSpecification(TypedDict):
    """
    A tool's definition.
    """
    name: str
    description: NotRequired[str]
    inputSchema: ToolInputSchema


class Tool(TypedDict):
    """
    A tool supplied to Converse.
    """
    toolSpec: ToolSpecification


class ToolName(TypedDict):
    """
    A named tool choice.
    """
    name: str


class EmptyToolChoice(TypedDict):
    """
    The empty object used for auto and any tool choices.
    """


class ToolChoice(TypedDict, total=False):
    """
    Automatic, required, or named tool selection.
    """
    auto: EmptyToolChoice
    any: EmptyToolChoice
    tool: ToolName


class ToolConfiguration(TypedDict):
    """
    Tools and their selection settings.
    """
    tools: list[Tool]
    toolChoice: NotRequired[ToolChoice]


class InferenceConfiguration(TypedDict, total=False):
    """
    Token limits, sampling settings, and stop sequences.
    """
    maxTokens: int
    temperature: float
    topP: float
    stopSequences: list[str]


class ConverseRequest(TypedDict):
    """
    Arguments for a Converse call.
    """
    modelId: str
    messages: list[ConverseMessage]
    system: NotRequired[list[SystemContentBlock]]
    inferenceConfig: NotRequired[InferenceConfiguration]
    toolConfig: NotRequired[ToolConfiguration]


# Response data

type StopReason = Literal[
    'end_turn',
    'tool_use',
    'max_tokens',
    'stop_sequence',
    'guardrail_intervened',
    'content_filtered',
    'malformed_model_output',
    'malformed_tool_use',
    'model_context_window_exceeded',
]


class ConverseOutput(TypedDict):
    """
    The assistant message returned by Converse.
    """
    message: ConverseMessage


class TokenUsage(TypedDict):
    """
    Token counts for a request and response.
    """
    inputTokens: int
    outputTokens: int
    totalTokens: int
    cacheReadInputTokens: NotRequired[int]
    cacheWriteInputTokens: NotRequired[int]


class ConverseMetrics(TypedDict):
    """
    Inference latency in milliseconds.
    """
    latencyMs: int


class ConverseResponse(TypedDict):
    """
    The result of a Converse call.
    """
    output: ConverseOutput
    stopReason: StopReason
    usage: TokenUsage
    metrics: ConverseMetrics


# Client protocol

class BedrockRuntimeClient(Protocol):
    """
    A Bedrock Runtime client with Converse support.
    """
    def converse(
        self,
        *,
        modelId: str,
        messages: list[ConverseMessage],
        system: list[SystemContentBlock] = ...,
        inferenceConfig: InferenceConfiguration = ...,
        toolConfig: ToolConfiguration = ...,
    ) -> ConverseResponse:
        """
        Generate an assistant message.
        """
        ...
