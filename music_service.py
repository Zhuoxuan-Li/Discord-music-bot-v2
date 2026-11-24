import asyncio
import logging
from collections import deque

import discord
import yt_dlp


# ==========================================
# 1. 日志配置
# ==========================================

logger = logging.getLogger("music_service")


# ==========================================
# 2. yt-dlp 配置
# ==========================================

YDL_OPTIONS = {
    "format": "bestaudio/best",
    "noplaylist": True,
    "quiet": True,
    "no_warnings": True,
    "socket_timeout": 20,
    "retries": 3,
    "extractor_retries": 2
}


# ==========================================
# 3. FFmpeg 配置
# ==========================================

FFMPEG_BEFORE_OPTIONS = (
    "-nostdin "
    "-reconnect 1 "
    "-reconnect_streamed 1 "
    "-reconnect_delay_max 5"
)

FFMPEG_OPTIONS = "-vn"


# ==========================================
# 4. 提取歌曲信息
# ==========================================

def extract_song(url):

    logger.info("开始解析音乐链接：%s", url)

    with yt_dlp.YoutubeDL(YDL_OPTIONS) as music:

        info = music.extract_info(
            url,
            download=False
        )

    if not info:
        raise ValueError("yt-dlp 没有返回音乐信息")

    if not info.get("url"):
        raise ValueError("没有获取到有效的音频地址")

    song = {
        "title": info.get("title", "未知歌曲"),
        "url": info["url"],
        "webpage_url": info.get("webpage_url", url)
    }

    logger.info("音乐解析成功：%s", song["title"])

    return song


# ==========================================
# 5. 创建 FFmpeg PCM 音频源
# ==========================================

def create_audio_source(audio_url):

    logger.info("正在启动 FFmpeg PCM 音频源")

    source = discord.FFmpegPCMAudio(
        audio_url,
        before_options=FFMPEG_BEFORE_OPTIONS,
        options=FFMPEG_OPTIONS
    )

    logger.info("FFmpeg PCM 音频源创建成功")

    return source


# ==========================================
# 6. MusicPlayer 播放器类
# ==========================================

class MusicPlayer:

    def __init__(self, bot, guild):

        self.bot = bot
        self.guild = guild

        # 等待播放的歌曲
        self.queue = deque()

        # 当前播放的歌曲
        self.current = None

        # 接收播放提示的文字频道
        self.text_channel = None

        # 防止多个协程同时修改播放状态
        self.lock = asyncio.Lock()

        # 播放任务版本号
        self.generation = 0

    # --------------------------------------
    # 获取 Discord 语音连接
    # --------------------------------------

    @property
    def voice_client(self):

        return self.guild.voice_client

    # --------------------------------------
    # 向文字频道发送消息
    # --------------------------------------

    async def send_message(self, message):

        if self.text_channel is None:
            return

        try:
            await self.text_channel.send(
                message,
                allowed_mentions=discord.AllowedMentions.none()
            )

        except discord.HTTPException:
            logger.exception("发送 Discord 消息失败")

    # --------------------------------------
    # 加入语音频道
    # --------------------------------------

    async def connect(self, member):

        if member.voice is None:
            return "请先加入一个语音频道。"

        async with self.lock:

            channel = member.voice.channel
            voice = self.voice_client

            try:

                if voice is None:

                    logger.info(
                        "正在连接语音频道：%s",
                        channel.name
                    )

                    await channel.connect()

                    logger.info("语音频道连接成功")

                    return f"已加入语音频道：{channel.name}"

                if voice.channel != channel:

                    await voice.move_to(channel)

                    logger.info(
                        "已移动到语音频道：%s",
                        channel.name
                    )

                    return f"已移动到语音频道：{channel.name}"

                return "机器人已经在你的语音频道中了。"

            except Exception:

                logger.exception("连接语音频道失败")

                return "❌ 无法连接语音频道，请检查权限和网络。"

    # --------------------------------------
    # 检查用户语音状态
    # --------------------------------------

    async def check_channel(self, member):

        voice = self.voice_client

        if voice is None or not voice.is_connected():

            return "机器人目前不在语音频道。"

        if member.voice is None:

            return "请先加入机器人所在的语音频道。"

        if member.voice.channel != voice.channel:

            return "你需要与机器人位于同一个语音频道。"

        return None

    # ======================================
    # 7. 添加歌曲
    # ======================================

    async def add_song(
        self,
        member,
        url,
        text_channel
    ):

        if member.voice is None:

            return "请先加入一个语音频道。"

        # 先解析歌曲，避免阻塞 Discord 事件循环
        try:

            song = await asyncio.to_thread(
                extract_song,
                url
            )

        except Exception:

            logger.exception("歌曲解析失败")

            return "❌ 无法解析该音乐链接，请检查链接是否有效。"

        async with self.lock:

            # 再次检查用户的语音状态
            if member.voice is None:

                return "你的语音频道状态发生了变化，请重新加入。"

            voice = self.voice_client

            if voice is not None:

                if voice.channel != member.voice.channel:

                    return "请先加入机器人所在的语音频道。"

            else:

                try:

                    await member.voice.channel.connect()

                    logger.info("播放时自动加入语音频道成功")

                except Exception:

                    logger.exception("自动加入语音频道失败")

                    return "❌ 机器人加入语音频道失败。"

            self.text_channel = text_channel

            voice = self.voice_client

            # 如果正在播放或暂停，就加入队列
            if (
                self.current is not None
                or voice.is_playing()
                or voice.is_paused()
            ):

                self.queue.append(song)

                logger.info(
                    "歌曲加入队列：%s，当前队列长度：%s",
                    song["title"],
                    len(self.queue)
                )

                return f"➕ 已加入播放队列：{song['title']}"

            # 否则直接播放
            return await self._start_song(song)

    # ======================================
    # 8. 开始播放歌曲
    # ======================================

    async def _start_song(
        self,
        song,
        refresh=False
    ):

        voice = self.voice_client

        if voice is None or not voice.is_connected():

            logger.error("播放失败：语音连接不存在")

            return "❌ 机器人尚未连接语音频道。"

        source = None

        try:

            # 队列等待的歌曲重新获取音频地址
            if refresh:

                logger.info(
                    "重新解析队列歌曲：%s",
                    song["title"]
                )

                song = await asyncio.to_thread(
                    extract_song,
                    song["webpage_url"]
                )

            logger.info(
                "准备播放歌曲：%s",
                song["title"]
            )

            # 在独立线程启动 FFmpeg 子进程
            source = await asyncio.to_thread(
                create_audio_source,
                song["url"]
            )

            self.generation += 1

            generation = self.generation

            # 获取当前事件循环
            loop = asyncio.get_running_loop()

            self.current = song

            # ----------------------------------
            # 音频结束回调
            # ----------------------------------

            def after_playing(error):

                if error:

                    logger.error(
                        "音频播放线程出现错误：%s",
                        error
                    )

                else:

                    logger.info(
                        "歌曲播放结束：%s",
                        song["title"]
                    )

                try:

                    future = asyncio.run_coroutine_threadsafe(
                        self._on_song_end(
                            generation,
                            error
                        ),
                        loop
                    )

                    def report_error(f):

                        try:
                            f.result()

                        except Exception:

                            logger.exception(
                                "自动切换下一首失败"
                            )

                    future.add_done_callback(report_error)

                except RuntimeError:

                    logger.exception(
                        "无法向 Discord 事件循环提交播放回调"
                    )

            # ----------------------------------
            # 真正启动 Discord 音频播放
            # ----------------------------------

            voice.play(
                source,
                after=after_playing
            )

            logger.info(
                "Discord 音频播放已启动：%s",
                song["title"]
            )

            logger.info(
                "Voice connected: %s",
                voice.is_connected()
            )

            logger.info(
                "Voice playing: %s",
                voice.is_playing()
            )

            # 自动发送当前播放提示
            await self.send_message(
                f"🎵 当前播放：{song['title']}"
            )

            return f"开始播放：{song['title']}"

        except Exception as error:

            logger.exception(
                "歌曲启动失败：%s",
                song.get("title", "未知歌曲")
            )

            self.current = None

            if source is not None:

                try:
                    source.cleanup()

                except Exception:
                    logger.exception("音频资源清理失败")

            return (
                f"❌ 歌曲播放启动失败："
                f"{type(error).__name__}: {error}"
            )

    # ======================================
    # 9. 歌曲播放结束
    # ======================================

    async def _on_song_end(
        self,
        generation,
        error=None
    ):

        async with self.lock:

            # 忽略旧歌曲的回调
            if generation != self.generation:

                logger.info("忽略过期播放回调")

                return

            self.current = None

            if error:

                await self.send_message(
                    "⚠️ 上一首歌曲播放出现异常，尝试切换下一首。"
                )

            logger.info("准备播放下一首")

            await self._play_next_locked()

    # ======================================
    # 10. 自动播放下一首
    # ======================================

    async def _play_next_locked(self):

        voice = self.voice_client

        if voice is None or not voice.is_connected():

            self.current = None

            return

        while self.queue:

            song = self.queue.popleft()

            logger.info(
                "从播放队列取出歌曲：%s",
                song["title"]
            )

            result = await self._start_song(
                song,
                refresh=True
            )

            if self.current is not None:

                logger.info("下一首歌曲启动成功")

                return

            logger.warning(
                "歌曲启动失败，跳过：%s",
                result
            )

            await self.send_message(result)

        self.current = None

        logger.info("播放队列已经结束")

    # ======================================
    # 11. 暂停音乐
    # ======================================

    async def pause(self, member):

        error = await self.check_channel(member)

        if error:
            return error

        async with self.lock:

            voice = self.voice_client

            if voice.is_playing():

                voice.pause()

                logger.info("音乐已暂停")

                return "⏸️ 音乐已暂停。"

            return "目前没有正在播放的音乐。"

    # ======================================
    # 12. 恢复音乐
    # ======================================

    async def resume(self, member):

        error = await self.check_channel(member)

        if error:
            return error

        async with self.lock:

            voice = self.voice_client

            if voice.is_paused():

                voice.resume()

                logger.info("音乐已恢复")

                return "▶️ 音乐已继续播放。"

            return "目前没有暂停的音乐。"

    # ======================================
    # 13. 跳过当前歌曲
    # ======================================

    async def skip(self, member):

        error = await self.check_channel(member)

        if error:
            return error

        async with self.lock:

            voice = self.voice_client

            if voice.is_playing() or voice.is_paused():

                logger.info("用户请求跳过当前歌曲")

                voice.stop()

                return "⏭️ 已跳过当前歌曲。"

            return "目前没有正在播放的歌曲。"

    # ======================================
    # 14. 查看播放队列
    # ======================================

    async def show_queue(self):

        async with self.lock:

            if not self.queue:

                return "📭 播放队列目前为空。"

            lines = ["📋 当前等待播放队列："]

            for index, song in enumerate(
                list(self.queue)[:15],
                start=1
            ):

                lines.append(
                    f"{index}. {song['title']}"
                )

            if len(self.queue) > 15:

                lines.append(
                    f"还有 {len(self.queue) - 15} 首歌曲未显示。"
                )

            return "\n".join(lines)[:1900]

    # ======================================
    # 15. 查看当前歌曲
    # ======================================

    async def now_playing(self):

        async with self.lock:

            if self.current is None:

                return "目前没有正在播放的歌曲。"

            return (
                f"🎵 当前播放："
                f"{self.current['title']}"
            )

    # ======================================
    # 16. 清空播放队列
    # ======================================

    async def clear(self, member):

        error = await self.check_channel(member)

        if error:
            return error

        async with self.lock:

            self.queue.clear()

            logger.info("等待播放队列已清空")

            return "🧹 播放队列已清空。"

    # ======================================
    # 17. 删除指定歌曲
    # ======================================

    async def remove(
        self,
        member,
        position
    ):

        error = await self.check_channel(member)

        if error:
            return error

        async with self.lock:

            songs = list(self.queue)

            if position < 1 or position > len(songs):

                return "❌ 歌曲序号无效。"

            song = songs.pop(position - 1)

            self.queue = deque(songs)

            logger.info(
                "从队列中删除歌曲：%s",
                song["title"]
            )

            return f"🗑️ 已移除：{song['title']}"

    # ======================================
    # 18. 离开语音频道
    # ======================================

    async def leave(self, member):

        error = await self.check_channel(member)

        if error:
            return error

        async with self.lock:

            voice = self.voice_client

            # 让此前的播放回调失效
            self.generation += 1

            self.queue.clear()

            self.current = None

            self.text_channel = None

            try:

                if voice is not None:

                    if voice.is_playing() or voice.is_paused():

                        voice.stop()

                    await voice.disconnect(force=True)

                logger.info("已退出语音频道")

                return "👋 已离开语音频道，并清空播放队列。"

            except Exception:

                logger.exception("离开语音频道失败")

                return "❌ 离开语音频道时发生错误。"


# ==========================================
# 19. 每个 Discord 服务器独立播放器
# ==========================================

music_players = {}


def get_player(bot, guild):

    if guild.id not in music_players:

        music_players[guild.id] = MusicPlayer(
            bot,
            guild
        )

        logger.info(
            "创建新的音乐播放器，Guild ID：%s",
            guild.id
        )

    return music_players[guild.id]
