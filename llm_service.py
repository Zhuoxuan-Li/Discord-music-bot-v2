
import asyncio
import json
import logging
from collections import defaultdict

from openai import AsyncOpenAI

from config import (
    DEEPSEEK_API_KEY,
    DEEPSEEK_BASE_URL,
    DEEPSEEK_MODEL,
    MAX_HISTORY,
    MAX_TOOL_ROUNDS
)

logger = logging.getLogger("llm_service")

client = AsyncOpenAI(
    api_key=DEEPSEEK_API_KEY,
    base_url=DEEPSEEK_BASE_URL,
    timeout=60.0
)


SYSTEM_PROMPT = """
你是一个运行在 Discord 服务器中的
LLM-Agent 智能音乐助手。

你可以：
1. 与用户进行自然语言对话。
2. 结合历史消息理解上下文。
3. 使用提供的工具控制音乐播放器。
4. 查询当前歌曲与播放队列。

规则：
- 默认使用中文回答。
- 需要操作音乐时，优先调用对应工具。
- 不要假装已经执行了未执行的操作。
- 工具执行失败时，应明确告诉用户。
- 播放歌曲必须提供可用的媒体链接。
- 不要编造歌曲链接。
- 不要把普通聊天当作工具指令。
- 不要声称拥有未提供的系统能力。
"""


# ==============================
# Function Calling 工具定义
# ==============================

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "play_music",
            "description": (
                "播放用户提供的音乐链接。"
                "如果正在播放，则加入等待队列。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {
                        "type": "string",
                        "description": "用户提供的完整音乐链接"
                    }
                },
                "required": ["url"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "pause_music",
            "description": "暂停当前正在播放的音乐",
            "parameters": {
                "type": "object",
                "properties": {}
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "resume_music",
            "description": "恢复已暂停的音乐",
            "parameters": {
                "type": "object",
                "properties": {}
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "skip_music",
            "description": "跳过当前歌曲，播放下一首",
            "parameters": {
                "type": "object",
                "properties": {}
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "show_queue",
            "description": "查询当前等待播放的歌曲队列",
            "parameters": {
                "type": "object",
                "properties": {}
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "now_playing",
            "description": "查询当前正在播放的歌曲",
            "parameters": {
                "type": "object",
                "properties": {}
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "clear_queue",
            "description": "清空等待播放的歌曲队列",
            "parameters": {
                "type": "object",
                "properties": {}
            }
        }
    }
]


conversation_history = defaultdict(list)
conversation_locks = defaultdict(asyncio.Lock)


# ==============================
# AI 对话与工具调用
# ==============================

async def chat_with_deepseek(
    message,
    user_id,
    guild_id,
    tool_executor
):
    conversation_id = (guild_id, user_id)

    async with conversation_locks[conversation_id]:

        history = conversation_history[conversation_id]

        messages = [
            {
                "role": "system",
                "content": SYSTEM_PROMPT
            },
            *history,
            {
                "role": "user",
                "content": message
            }
        ]

        # 一次对话最多执行指定轮数的工具调用
        for _ in range(MAX_TOOL_ROUNDS):

            response = await client.chat.completions.create(
                model=DEEPSEEK_MODEL,
                messages=messages,
                tools=TOOLS,
                tool_choice="auto",
                max_tokens=1200
            )

            assistant_message = response.choices[0].message

            if not assistant_message.tool_calls:
                reply = (
                    assistant_message.content
                    or "模型没有返回文本内容。"
                )

                # 只保存已完成的对话轮次
                conversation_history[conversation_id] = (
                    history + [
                        {
                            "role": "user",
                            "content": message
                        },
                        {
                            "role": "assistant",
                            "content": reply
                        }
                    ]
                )[-MAX_HISTORY:]

                return reply

            # 必须保留 tool_calls 信息，
            # 以便后续工具结果与调用 ID 对应
            messages.append(
                assistant_message.model_dump(
                    exclude_none=True
                )
            )

            for tool_call in assistant_message.tool_calls:

                name = tool_call.function.name

                try:
                    arguments = json.loads(
                        tool_call.function.arguments
                    )

                    if not isinstance(arguments, dict):
                        raise ValueError("工具参数必须是对象")

                    result = await tool_executor(
                        name,
                        arguments
                    )

                except Exception:
                    logger.exception(
                        "工具执行失败：%s",
                        name
                    )
                    result = "工具执行失败。"

                messages.append({
                    "role": "tool",
                    "tool_call_id": tool_call.id,
                    "content": str(result)
                })

        # 达到工具调用上限
        reply = "本次操作已达到工具调用次数上限，请重新发送指令。"

        conversation_history[conversation_id] = (
            history + [
                {
                    "role": "user",
                    "content": message
                },
                {
                    "role": "assistant",
                    "content": reply
                }
            ]
        )[-MAX_HISTORY:]

        return reply


async def clear_conversation(user_id, guild_id):
    conversation_id = (guild_id, user_id)

    async with conversation_locks[conversation_id]:
        conversation_history.pop(
            conversation_id,
            None
        )
