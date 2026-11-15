
import asyncio
import logging
from collections import deque

import discord
import yt_dlp

logger = logging.getLogger("music_service")

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


def extract_song(url):
    with yt_dlp.YoutubeDL(YDL_OPTIONS) as music:
        info = music.extract_info(
            url,
            download=False
        )

    if not info or not info.get("url"):
        raise ValueError("无法解析音频地址")

    return {
        "title": info.get("title", "未知歌曲"),
        "url": info["url"],
        "webpage_url": info.get(
            "webpage_url",
            url
        )
    }


class MusicPlayer:

    def __init__(self, bot, guild):
        self.bot = bot
        self.guild = guild

        self.queue = deque()
        self.current = None
        self.text_channel = None

        self.lock = asyncio.Lock()
        self.generation = 0

    @property
    def voice_client(self):
        return self.guild.voice_client

    async def connect(self, member):
        if not member.voice:
            return "请先加入一个语音频道。"

        channel = member.voice.channel
        voice = self.voice_client

        if voice is None:
            await channel.connect()
            return f"已加入语音频道：{channel.name}"

        if voice.channel != channel:
            await voice.move_to(channel)
            return f"已移动到语音频道：{channel.name}"

        return "机器人已经在你的语音频道中。"

    async def check_channel(self, member):
        voice = self.voice_client

        if voice is None or not voice.is_connected():
            return "机器人目前不在语音频道。"

        if not member.voice:
            return "请先加入机器人所在的语音频道。"

        if member.voice.channel != voice.channel:
            return "你需要与机器人位于同一语音频道。"

        return None

    async def add_song(self, member, url, text_channel):
        if not member.voice:
            return "请先加入一个语音频道。"

        voice = self.voice_client

        if voice is not None:
            if voice.channel != member.voice.channel:
                return "请先加入机器人所在的语音频道。"

        # 在事件循环之外解析链接
        try:
            song = await asyncio.to_thread(
                extract_song,
                url
            )
        except Exception:
            logger.exception("音乐解析失败")
            return "无法解析该音乐链接。"

        async with self.lock:
            voice = self.voice_client

            if voice is None:
                try:
                    await member.voice.channel.connect()
                except Exception:
                    logger.exception("连接语音频道失败")
                    return "无法连接语音频道。"

            elif voice.channel != member.voice.channel:
                return "请先加入机器人所在的语音频道。"

            self.text_channel = text_channel

            voice = self.voice_client

            if (
                self.current is not None
                or voice.is_playing()
                or voice.is_paused()
            ):
                self.queue.append(song)
                return f"➕ 已加入播放队列：{song['title']}"

            return await self._start_song(song)

    async def _start_song(self, song):
        voice = self.voice_client

        if voice is None or not voice.is_connected():
            self.current = None
            return "机器人未连接语音频道。"

        try:
            # 重新获取音频流地址
            fresh_song = await asyncio.to_thread(
                extract_song,
                song["webpage_url"]
            )

            source = await asyncio.to_thread(
                discord.FFmpegPCMAudio,
                fresh_song["url"],
                **FFMPEG_OPTIONS
            )

            self.generation += 1
            generation = self.generation

            self.current = fresh_song

            def after_playing(error):
                if error:
                    logger.error(
                        "音频播放错误：%s",
                        error
                    )

                future = asyncio.run_coroutine_threadsafe(
                    self._on_song_end(generation),
                    self.bot.loop
                )

                def report_error(f):
                    try:
                        f.result()
                    except Exception:
                        logger.exception(
                            "自动播放下一首失败"
                        )

                future.add_done_callback(report_error)

            voice.play(
                source,
                after=after_playing
            )

            if self.text_channel:
                try:
                    await self.text_channel.send(
                        f"🎵 当前播放：{fresh_song['title']}",
                        allowed_mentions=discord.AllowedMentions.none()
                    )
                except discord.HTTPException:
                    logger.warning("无法发送播放提示")

            return f"开始播放：{fresh_song['title']}"

        except Exception:
            logger.exception("启动歌曲失败")
            self.current = None
            return "歌曲播放失败。"

    async def _on_song_end(self, generation):
        async with self.lock:
            # 忽略旧播放任务的回调
            if generation != self.generation:
                return

            self.current = None
            await self._play_next()

    async def _play_next(self):
        while self.queue:
            song = self.queue.popleft()

            result = await self._start_song(song)

            if self.current is not None:
                return

            logger.warning(
                "跳过无法播放的歌曲：%s",
                result
            )

        self.current = None

    async def pause(self, member):
        error = await self.check_channel(member)
        if error:
            return error

        async with self.lock:
            voice = self.voice_client

            if voice.is_playing():
                voice.pause()
                return "⏸️ 音乐已暂停。"

            return "目前没有正在播放的音乐。"

    async def resume(self, member):
        error = await self.check_channel(member)
        if error:
            return error

        async with self.lock:
            voice = self.voice_client

            if voice.is_paused():
                voice.resume()
                return "▶️ 音乐已继续播放。"

            return "目前没有暂停的音乐。"

    async def skip(self, member):
        error = await self.check_channel(member)
        if error:
            return error

        async with self.lock:
            voice = self.voice_client

            if voice.is_playing() or voice.is_paused():
                voice.stop()
                return "⏭️ 已跳过当前歌曲。"

            return "目前没有正在播放的歌曲。"

    async def show_queue(self):
        async with self.lock:
            if not self.queue:
                return "📭 播放队列目前为空。"

            lines = ["📋 当前等待队列："]

            for index, song in enumerate(
                list(self.queue)[:15],
                start=1
            ):
                lines.append(
                    f"{index}. {song['title']}"
                )

            if len(self.queue) > 15:
                lines.append(
                    f"还有 {len(self.queue) - 15} 首未显示。"
                )

            return "\n".join(lines)[:1800]

    async def now_playing(self):
        async with self.lock:
            if self.current is None:
                return "目前没有正在播放的歌曲。"

            return (
                f"🎵 当前播放："
                f"{self.current['title']}"
            )

    async def clear(self, member):
        error = await self.check_channel(member)
        if error:
            return error

        async with self.lock:
            self.queue.clear()
            return "🧹 等待队列已清空。"

    async def remove(self, member, position):
        error = await self.check_channel(member)
        if error:
            return error

        async with self.lock:
            if position < 1 or position > len(self.queue):
                return "歌曲序号无效。"

            songs = list(self.queue)
            song = songs.pop(position - 1)

            self.queue = deque(songs)

            return f"🗑️ 已移除：{song['title']}"

    async def leave(self, member):
        error = await self.check_channel(member)
        if error:
            return error

        async with self.lock:
            self.generation += 1
            self.queue.clear()
            self.current = None
            self.text_channel = None

            voice = self.voice_client

            if voice:
                await voice.disconnect(force=True)

            return "👋 已离开语音频道，并清空队列。"


# 每个服务器一个播放器
music_players = {}


def get_player(bot, guild):
    if guild.id not in music_players:
        music_players[guild.id] = MusicPlayer(
            bot,
            guild
        )

    return music_players[guild.id]
