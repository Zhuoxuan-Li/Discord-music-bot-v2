
import os
import asyncio
import logging
from collections import deque
import logging
from openai import OpenAIError
from llm_service import chat_with_deepseek

import discord
import yt_dlp
from discord.ext import commands
from dotenv import load_dotenv


# ==============================
# 1. 基础配置
# ==============================F

load_dotenv()

TOKEN = os.getenv("DISCORD_TOKEN")

if not TOKEN:
    raise RuntimeError("未找到 DISCORD_TOKEN，请检查 .env 文件。")

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("music_bot")

intents = discord.Intents.default()
intents.message_content = True

bot = commands.Bot(
    command_prefix="!",
    intents=intents,
    help_command=None
)


# ==============================
# 2. 音乐播放器状态
# ==============================

# 每个 Discord 服务器独立保存播放状态
music_states = {}

YDL_OPTIONS = {
    "format": "bestaudio/best",
    "noplaylist": True,
    "quiet": True,
    "no_warnings": True
}

FFMPEG_OPTIONS = {
    "before_options": (
        "-reconnect 1 "
        "-reconnect_streamed 1 "
        "-reconnect_delay_max 5"
    ),
    "options": "-vn"
}


def get_state(guild_id):
    if guild_id not in music_states:
        music_states[guild_id] = {
            "queue": deque(),
            "current": None,
            "channel": None,
            "lock": asyncio.Lock(),
            "generation": 0
        }

    return music_states[guild_id]


def create_source(song):
    return discord.FFmpegPCMAudio(
        song["url"],
        **FFMPEG_OPTIONS
    )


# ==============================
# 3. 音乐信息提取
# ==============================

def extract_song(url):
    with yt_dlp.YoutubeDL(YDL_OPTIONS) as music:
        info = music.extract_info(url, download=False)

    if not info or not info.get("url"):
        raise ValueError("无法获取该链接的音频地址。")

    return {
        "title": info.get("title", "未知歌曲"),
        "url": info["url"],
        "webpage_url": info.get("webpage_url", url)
    }


# ==============================
# 4. 自动播放系统
# ==============================

async def start_song(guild, state, song):
    voice_client = guild.voice_client

    if voice_client is None or not voice_client.is_connected():
        return

    try:
        # 重新解析原始链接，避免队列等待期间音频地址过期
        song = await asyncio.to_thread(
            extract_song,
            song["webpage_url"]
        )

        source = await asyncio.to_thread(
            create_source,
            song
        )

        state["current"] = song
        state["generation"] += 1
        generation = state["generation"]

        def after_playing(error):
            if error:
                logger.error("播放出现错误：%s", error)

            future = asyncio.run_coroutine_threadsafe(
                finish_song(guild, generation),
                bot.loop
            )

            def log_callback(f):
                try:
                    f.result()
                except Exception:
                    logger.exception("自动切歌失败")

            future.add_done_callback(log_callback)

        voice_client.play(
            source,
            after=after_playing
        )

        if state["channel"]:
            await state["channel"].send(
                f"🎵 当前播放：{song['title']}"
            )

    except Exception:
        logger.exception("歌曲播放失败")

        state["current"] = None

        if state["channel"]:
            await state["channel"].send(
                "❌ 歌曲播放失败，尝试下一首。"
            )

        await play_next(guild, state)


async def play_next(guild, state):
    voice_client = guild.voice_client

    if voice_client is None or not voice_client.is_connected():
        state["current"] = None
        return

    if not state["queue"]:
        state["current"] = None
        return

    song = state["queue"].popleft()

    await start_song(guild, state, song)


async def finish_song(guild, generation):
    state = get_state(guild.id)

    async with state["lock"]:
        if generation != state["generation"]:
            return

        state["current"] = None
        await play_next(guild, state)


# ==============================
# 5. Bot 上线
# ==============================

@bot.event
async def on_ready():
    logger.info("%s is online!", bot.user)


# ==============================
# 6. 加入语音频道
# ==============================

@bot.command()
@commands.guild_only()
async def join(ctx):

    if ctx.author.voice is None:
        await ctx.send("你需要先加入一个语音频道。")
        return

    channel = ctx.author.voice.channel
    voice_client = ctx.voice_client

    if voice_client is None:
        await channel.connect()

    elif voice_client.channel != channel:
        await voice_client.move_to(channel)

    else:
        await ctx.send("机器人已经在你的语音频道中了。")
        return

    await ctx.send(f"✅ 已加入语音频道：{channel.name}")


# ==============================
# 7. 离开语音频道
# ==============================

@bot.command()
@commands.guild_only()
async def leave(ctx):

    if ctx.voice_client is None:
        await ctx.send("机器人目前不在语音频道。")
        return

    state = get_state(ctx.guild.id)

    async with state["lock"]:
        state["generation"] += 1
        state["queue"].clear()
        state["current"] = None
        state["channel"] = None

        await ctx.voice_client.disconnect()

    await ctx.send("👋 已离开语音频道，并清空播放队列。")


# ==============================
# 8. 播放音乐
# ==============================

@bot.command()
@commands.guild_only()
async def play(ctx, url: str = None):

    if url is None:
        await ctx.send("请输入音乐链接，例如：!play URL")
        return

    if ctx.author.voice is None:
        await ctx.send("你需要先加入一个语音频道。")
        return

    if ctx.voice_client is None:
        await ctx.author.voice.channel.connect()

    elif ctx.voice_client.channel != ctx.author.voice.channel:
        await ctx.send("请先加入机器人所在的语音频道。")
        return

    await ctx.send("🔎 正在解析音乐链接...")

    try:
        song = await asyncio.to_thread(extract_song, url)

    except Exception:
        logger.exception("音乐链接解析失败")
        await ctx.send("❌ 无法解析该音乐链接，请检查链接。")
        return

    state = get_state(ctx.guild.id)

    async with state["lock"]:
        state["channel"] = ctx.channel

        voice_client = ctx.voice_client

        if (
            state["current"] is not None
            or voice_client.is_playing()
            or voice_client.is_paused()
        ):
            state["queue"].append(song)

            await ctx.send(
                f"➕ 已加入播放队列：{song['title']}"
            )
            return

        await start_song(ctx.guild, state, song)


# ==============================
# 9. 暂停音乐
# ==============================

@bot.command()
@commands.guild_only()
async def pause(ctx):

    voice_client = ctx.voice_client

    if voice_client is None:
        await ctx.send("机器人目前不在语音频道。")
        return

    if voice_client.is_playing():
        voice_client.pause()
        await ctx.send("⏸️ 音乐已暂停。")

    else:
        await ctx.send("目前没有正在播放的音乐。")


# ==============================
# 10. 恢复音乐
# ==============================

@bot.command()
@commands.guild_only()
async def resume(ctx):

    voice_client = ctx.voice_client

    if voice_client is None:
        await ctx.send("机器人目前不在语音频道。")
        return

    if voice_client.is_paused():
        voice_client.resume()
        await ctx.send("▶️ 音乐已继续播放。")

    else:
        await ctx.send("目前没有暂停的音乐。")


# ==============================
# 11. 跳过歌曲
# ==============================

@bot.command()
@commands.guild_only()
async def skip(ctx):

    voice_client = ctx.voice_client

    if voice_client is None:
        await ctx.send("机器人目前不在语音频道。")
        return

    if voice_client.is_playing() or voice_client.is_paused():
        voice_client.stop()
        await ctx.send("⏭️ 已跳过当前歌曲。")

    else:
        await ctx.send("目前没有正在播放的歌曲。")


# ==============================
# 12. 查看播放队列
# ==============================

@bot.command()
@commands.guild_only()
async def queue(ctx):

    state = get_state(ctx.guild.id)
    songs = list(state["queue"])

    if not songs:
        await ctx.send("📭 播放队列目前为空。")
        return

    lines = ["📋 当前播放队列："]

    for index, song in enumerate(songs[:15], start=1):
        lines.append(f"{index}. {song['title']}")

    if len(songs) > 15:
        lines.append(f"还有 {len(songs) - 15} 首歌曲未显示。")

    await ctx.send("\n".join(lines)[:1900])


# ==============================
# 13. 当前播放歌曲
# ==============================

@bot.command()
@commands.guild_only()
async def nowplaying(ctx):

    state = get_state(ctx.guild.id)
    song = state["current"]

    if song is None:
        await ctx.send("目前没有正在播放的歌曲。")
        return

    await ctx.send(f"🎵 当前播放：{song['title']}")


# ==============================
# 14. 删除队列中的歌曲
# ==============================

@bot.command()
@commands.guild_only()
async def remove(ctx, position: int = None):

    if position is None:
        await ctx.send("请输入歌曲序号，例如：!remove 2")
        return

    state = get_state(ctx.guild.id)

    async with state["lock"]:
        songs = list(state["queue"])

        if position < 1 or position > len(songs):
            await ctx.send("❌ 歌曲序号无效。")
            return

        song = songs.pop(position - 1)
        state["queue"] = deque(songs)

    await ctx.send(f"🗑️ 已移除：{song['title']}")


# ==============================
# 15. 清空播放队列
# ==============================

@bot.command()
@commands.guild_only()
async def clear(ctx):

    state = get_state(ctx.guild.id)

    async with state["lock"]:
        if not state["queue"]:
            await ctx.send("播放队列已经是空的。")
            return

        state["queue"].clear()

    await ctx.send("🧹 播放队列已清空。")


# ==============================
# 16. 帮助菜单
# ==============================

@bot.command(name="help")
async def help_command(ctx):

    help_text = (
        "🎵 Discord Music Bot 命令列表\n\n"
        "!join - 加入语音频道\n"
        "!play URL - 播放音乐\n"
        "!pause - 暂停音乐\n"
        "!resume - 恢复播放\n"
        "!skip - 跳过歌曲\n"
        "!queue - 查看等待队列\n"
        "!nowplaying - 查看当前歌曲\n"
        "!remove 2 - 删除队列第2首\n"
        "!clear - 清空等待队列\n"
        "!leave - 退出语音频道\n"
        "!help - 显示帮助菜单"
    )

    await ctx.send(help_text)


# ==============================
# 17. 命令错误处理
# ==============================

@bot.event
async def on_command_error(ctx, error):

    if isinstance(error, commands.CommandNotFound):
        return

    if isinstance(error, commands.NoPrivateMessage):
        await ctx.send("该命令只能在 Discord 服务器中使用。")
        return

    if isinstance(error, commands.BadArgument):
        await ctx.send("❌ 命令参数错误，请使用 !help 查看说明。")
        return

    logger.error(
        "命令执行失败：%s",
        error,
        exc_info=(type(error), error, error.__traceback__)
    )

    await ctx.send("❌ 命令执行时出现错误，请查看终端日志。")


@bot.command()
async def chat(ctx, *, message: str = None):

    if not message:
        await ctx.send("请输入问题，例如：!chat 你好")
        return

    async with ctx.typing():
        try:
            reply = await chat_with_deepseek(message)

        except OpenAIError:
            logging.exception("DeepSeek API 调用失败")
            await ctx.send(
                "❌ AI 服务暂时不可用，请稍后再试。"
            )
            return

        except Exception:
            logging.exception("聊天命令发生异常")
            await ctx.send(
                "❌ 处理消息时出现错误。"
            )
            return

    # Discord 单条普通消息有长度限制
    # 分段发送，避免过长回复失败
    for start in range(0, len(reply), 1900):
        await ctx.send(
            reply[start:start + 1900],
            allowed_mentions=discord.AllowedMentions.none()
        )


# ==============================
# 18. 启动 Bot
# ==============================

bot.run(TOKEN)
