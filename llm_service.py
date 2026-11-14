
from openai import AsyncOpenAI

from config import (
    DEEPSEEK_API_KEY,
    DEEPSEEK_BASE_URL,
    DEEPSEEK_MODEL
)


client = AsyncOpenAI(
    api_key=DEEPSEEK_API_KEY,
    base_url=DEEPSEEK_BASE_URL,
    timeout=30.0
)


SYSTEM_PROMPT = """
你是一个运行在 Discord 服务器中的智能助手。

你的职责：
1. 回答用户的问题。
2. 使用自然、友好的语言交流。
3. 优先使用中文回答。
4. 对不确定的信息明确说明。
5. 不要声称自己已经执行了实际未执行的操作。
"""


async def chat_with_deepseek(message: str) -> str:

    response = await client.chat.completions.create(
        model=DEEPSEEK_MODEL,
        messages=[
            {
                "role": "system",
                "content": SYSTEM_PROMPT
            },
            {
                "role": "user",
                "content": message
            }
        ],
        extra_body={
            "thinking": {"type": "disabled"}
        },
        max_tokens=1000
    )

    return (
        response.choices[0].message.content
        or "模型没有返回文本内容。"
    )
