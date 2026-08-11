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
        info = ydl.extract_info(url, download=False)
        audio_url = info["url"]

    source = discord.FFmpegPCMAudio(audio_url)

    ctx.voice_client.play(source)

    await ctx.send(f"正在播放：{info['title']}")




bot.run(TOKEN)