import os
import yt_dlp

import discord
from discord.ext import commands
from dotenv import load_dotenv


load_dotenv()

TOKEN = os.getenv("DISCORD_TOKEN")

intents = discord.Intents.default()
intents.message_content = True

bot = commands.Bot(
    command_prefix="!",
    intents=intents
)

music_queue = []

@bot.event
async def on_ready():
    print(f"{bot.user} is online!")


@bot.command()
async def join(ctx):

    if ctx.author.voice is None:
        await ctx.send("你需要先加入一个语音频道。")
        return

    channel = ctx.author.voice.channel

    await channel.connect()

    await ctx.send(f"已加入语音频道：{channel.name}")

@bot.command()
async def leave(ctx):

    if ctx.voice_client is None:
        await ctx.send("机器人目前不在语音频道。")
        return

    await ctx.voice_client.disconnect()

    await ctx.send("已离开语音频道。")

def play_next(ctx):

    if len(music_queue) == 0:
        return

    song = music_queue.pop(0)

    source = discord.FFmpegPCMAudio(
        song["url"],
        before_options=(
            "-reconnect 1 "
            "-reconnect_streamed 1 "
            "-reconnect_delay_max 5"
        ),
        options="-vn"
    )

    ctx.voice_client.play(
        source,
        after=lambda error: play_next(ctx)
    )

    bot.loop.create_task(
        ctx.send(f"🎵当前播放：{song['title']}")
    )

@bot.command()
async def play(ctx, url):

    if ctx.author.voice is None:
        await ctx.send("你需要先加入一个语音频道。")
        return

    if ctx.voice_client is None:
        channel = ctx.author.voice.channel
        await channel.connect()

    ydl_options = {
        "format": "bestaudio/best",
        "noplaylist": True
    }

    with yt_dlp.YoutubeDL(ydl_options) as music:
        info = music.extract_info(url, download=False)
    
    song = {
        "title": info["title"],
        "url": info["url"]
    }
    
    if ctx.voice_client.is_playing() or ctx.voice_client.is_paused():

        music_queue.append(song)

        return

    source = discord.FFmpegPCMAudio(
        song["url"],
        before_options=(
         "-reconnect 1 "
        "-reconnect_streamed 1 "
        "-reconnect_delay_max 5"
        ),
        options="-vn"
        
    )

    ctx.voice_client.play(
        source,
        after=lambda error: play_next(ctx)
    )

    await ctx.send(f"正在播放：{song['title']}")

@bot.command()
async def pause(ctx):

    if ctx.voice_client is None:
        await ctx.send("机器人目前不在语音频道。")
        return

    if ctx.voice_client.is_playing():
        ctx.voice_client.pause()
        await ctx.send("音乐已暂停。")
    else:
        await ctx.send("目前没有正在播放的音乐。")

@bot.command()
async def resume(ctx):

    if ctx.voice_client is None:
        await ctx.send("机器人目前不在语音频道。")
        return

    if ctx.voice_client.is_paused():
        ctx.voice_client.resume()
        await ctx.send("音乐已继续播放。")
    else:
        await ctx.send("目前没有暂停的音乐。")

@bot.command()
async def skip(ctx):

    if ctx.voice_client is None:
        await ctx.send("机器人目前不在语音频道。")
        return

    if ctx.voice_client.is_playing() or ctx.voice_client.is_paused():
        ctx.voice_client.stop()
        await ctx.send("音乐已停止。")
    else:
        await ctx.send("目前没有正在播放的音乐。")

@bot.command()
async def queue(ctx):

    if len(music_queue) == 0:
        await ctx.send("播放队列目前为空。")
        return

    queue_text = "当前播放队列：\n"

    for index, song in enumerate(music_queue, start=1):
        queue_text += f"{index}. {song['title']}\n"

    await ctx.send(queue_text)




bot.run(TOKEN)