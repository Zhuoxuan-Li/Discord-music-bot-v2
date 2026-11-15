
import logging
from urllib.parse import urlparse

import discord
from discord.ext import commands
from openai import OpenAIError

from config import DISCORD_TOKEN
from music_service import get_player
from llm_service import (
    chat_with_deepseek,
    clear_conversation
)


# ==============================
# 1. 基础配置
# ==============================

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("discord_bot")

intents = discord.Intents.default()
intents.message_content = True

bot = commands.Bot(
    command_prefix="!",
    intents=intents,
    help_command=None
)


@bot.event
async def on_ready():
    logger.info("%s is online!", bot.user)


# ==============================
# 2. 音乐命令
# ==============================

@bot.command()
@commands.guild_only()
async def join(ctx):
    player = get_player(bot, ctx.guild)

    result = await player.connect(ctx.author)
    await ctx.send(result)


@bot.command()
@commands.guild_only()
async def leave(ctx):
    player = get_player(bot, ctx.guild)

    result = await player.leave(ctx.author)
    await ctx.send(result)


@bot.command()
@commands.guild_only()
async def play(ctx, url: str = None):

    if not url:
        await ctx.send("请输入音乐链接，例如：!play URL")
        return

    player = get_player(bot, ctx.guild)

    async with ctx.typing():
        result = await player.add_song(
            ctx.author,
            url,
            ctx.channel
        )

    # 成功开始播放时，播放器已经自动发送提示
    if not result.startswith("开始播放："):
        await ctx.send(result)


@bot.command()
@commands.guild_only()
async def pause(ctx):
    player = get_player(bot, ctx.guild)

    result = await player.pause(ctx.author)
    await ctx.send(result)


@bot.command()
@commands.guild_only()
async def resume(ctx):
    player = get_player(bot, ctx.guild)

    result = await player.resume(ctx.author)
    await ctx.send(result)


@bot.command()
@commands.guild_only()
async def skip(ctx):
    player = get_player(bot, ctx.guild)

    result = await player.skip(ctx.author)
    await ctx.send(result)


@bot.command()
@commands.guild_only()
async def queue(ctx):
    player = get_player(bot, ctx.guild)

    result = await player.show_queue()
    await ctx.send(result)


@bot.command()
@commands.guild_only()
async def nowplaying(ctx):
    player = get_player(bot, ctx.guild)

    result = await player.now_playing()
    await ctx.send(result)


@bot.command()
@commands.guild_only()
async def remove(ctx, position: int = None):

    if position is None:
        await ctx.send("请输入序号，例如：!remove 2")
        return

    player = get_player(bot, ctx.guild)

    result = await player.remove(
        ctx.author,
        position
    )

    await ctx.send(result)


@bot.command()
@commands.guild_only()
async def clear(ctx):
    player = get_player(bot, ctx.guild)

    result = await player.clear(ctx.author)
    await ctx.send(result)


# ==============================
# 3. Agent 工具执行器
# ==============================

def valid_music_url(url):
    if not isinstance(url, str):
        return False

    parsed = urlparse(url)

    return (
        parsed.scheme in ("http", "https")
        and bool(parsed.hostname)
        and not parsed.username
        and not parsed.password
    )


async def execute_music_tool(
    ctx,
    tool_name,
    arguments
):
    """
    DeepSeek 选择工具后，
    由这个函数执行真实的音乐操作。
    """

    player = get_player(bot, ctx.guild)

    # 读取类工具不修改播放器状态
    if tool_name == "show_queue":
        return await player.show_queue()

    if tool_name == "now_playing":
        return await player.now_playing()

    # 播放工具
    if tool_name == "play_music":

        url = arguments.get("url")

        if not valid_music_url(url):
            return "请提供有效的 HTTP/HTTPS 音乐链接。"

        return await player.add_song(
            ctx.author,
            url,
            ctx.channel
        )

    if tool_name == "pause_music":
        return await player.pause(ctx.author)

    if tool_name == "resume_music":
        return await player.resume(ctx.author)

    if tool_name == "skip_music":
        return await player.skip(ctx.author)

    if tool_name == "clear_queue":
        return await player.clear(ctx.author)

    return f"不支持的工具：{tool_name}"


# ==============================
# 4. DeepSeek AI 聊天
# ==============================

@bot.command()
@commands.guild_only()
async def chat(ctx, *, message: str = None):

    if not message:
        await ctx.send(
            "请输入问题，例如：!chat 帮我暂停音乐"
        )
        return

    async def tool_executor(name, arguments):
        logger.info(
            "用户 %s 调用工具：%s",
            ctx.author.id,
            name
        )

        return await execute_music_tool(
            ctx,
            name,
            arguments
        )

    async with ctx.typing():
        try:
            reply = await chat_with_deepseek(
                message=message,
                user_id=ctx.author.id,
                guild_id=ctx.guild.id,
                tool_executor=tool_executor
            )

        except OpenAIError:
            logger.exception("DeepSeek API 调用失败")
            await ctx.send(
                "❌ DeepSeek API 调用失败，请检查密钥、"
                "模型名称、账户额度或网络连接。"
            )
            return

        except Exception:
            logger.exception("AI 聊天失败")
            await ctx.send(
                "❌ AI 请求处理失败，请查看终端日志。"
            )
            return

    for start in range(0, len(reply), 1900):
        await ctx.send(
            reply[start:start + 1900],
            allowed_mentions=discord.AllowedMentions.none()
        )


# ==============================
# 5. 清空 AI 对话记忆
# ==============================

@bot.command()
@commands.guild_only()
async def reset(ctx):

    await clear_conversation(
        user_id=ctx.author.id,
        guild_id=ctx.guild.id
    )

    await ctx.send("🧹 AI 对话历史已清空。")


# ==============================
# 6. 帮助菜单
# ==============================

@bot.command(name="help")
async def help_command(ctx):

    help_text = """
🎵 Discord Music & LLM-Agent Bot

【音乐播放器】

!join - 加入语音频道
!play URL - 播放音乐
!pause - 暂停音乐
!resume - 恢复播放
!skip - 跳过歌曲
!queue - 查看等待队列
!nowplaying - 查看当前歌曲
!remove 2 - 删除队列第2首
!clear - 清空等待队列
!leave - 退出语音频道

【DeepSeek AI】

!chat 问题 - AI 智能聊天
!reset - 清空 AI 对话历史

【自然语言控制示例】

!chat 帮我暂停音乐
!chat 继续播放
!chat 下一首
!chat 现在播放什么歌
!chat 队列里还有什么歌
!chat 清空播放队列
!chat 播放这首歌 https://example.com/music
"""

    await ctx.send(help_text)


# ==============================
# 7. 错误处理
# ==============================

@bot.event
async def on_command_error(ctx, error):

    if isinstance(error, commands.CommandNotFound):
        return

    if isinstance(error, commands.NoPrivateMessage):
        await ctx.send(
            "该命令只能在 Discord 服务器中使用。"
        )
        return

    if isinstance(error, commands.BadArgument):
        await ctx.send(
            "❌ 命令参数错误，请使用 !help 查看说明。"
        )
        return

    logger.error(
        "命令执行失败：%s",
        error,
        exc_info=(
            type(error),
            error,
            error.__traceback__
        )
    )

    await ctx.send(
        "❌ 命令执行时出现错误，请查看终端日志。"
    )


# ==============================
# 8. 启动机器人
# ==============================

bot.run(DISCORD_TOKEN)
