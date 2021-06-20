import os
import argparse
import asyncio
import time
from datetime import datetime, timezone, timedelta

import logging, logging.handlers
_LOGGER = logging.getLogger(__name__)

import discord

EMOJI_ASTRAY = "\N{ANGRY FACE}"
EMOJI_ACTIVE = "\N{FACE WITH MONOCLE}"
EMOJI_FINISHED = "\N{SHRUG}\N{ZWJ}\N{FEMALE SIGN}\uFE0F"
EMOJI_IGNORED = "\N{POUTING FACE}"
EMOJI_SPECTRUM = frozenset({
    EMOJI_ASTRAY,
    EMOJI_ACTIVE,
    EMOJI_FINISHED,
    EMOJI_IGNORED,
})

QUEUE_RULES_PREFIX = """
В этом чате установлены следующие правила:
""".strip('\n')
QUEUE_RULES_PREFIX_OTHER = """
В чате очереди установлены следующие правила:
""".strip('\n')
QUEUE_RULES = f"""
\N{HEAVY CHECK MARK} в очередь нужно записаться, указав задачи, которые вы хотите сдать;
{EMOJI_IGNORED} нельзя писать несколько сообщений; можно редактировать или удалять старое;
{EMOJI_ASTRAY} записавшись, нужно подключиться к голосовому каналу очереди (с выключенным микрофоном);
{EMOJI_ACTIVE} свободный преподаватель переключит вас в свой канал для разговора;
{EMOJI_FINISHED} поcле разговора сообщение из очереди нужно удалить.
""".strip('\n')

QUEUE_ALGORITHM = f"""
Я буду отмечать сообщения учеников в текстовом чате очереди одной из реакций {EMOJI_IGNORED}, {EMOJI_ASTRAY}, {EMOJI_ACTIVE}, {EMOJI_FINISHED} (или ни одной из них). Наличие реакции означает, что сообщение нерелевантно по той или иной причине.

{EMOJI_IGNORED} это не первое сообщение ученика в очереди;
{EMOJI_ASTRAY} ученик не подключен ни к какому голосовому каналу;
{EMOJI_ACTIVE} ученик подключен (или его подключили) к некоторому голосовому каналу, кроме канала очереди;
{EMOJI_FINISHED} ученик подключался (или его подключали) к некоторому голосовому каналу, кроме канала очереди (если у ученика есть несколько сообщений в разных чатах очереди, будет отмечено только одно из них — по умолчанию в первом канале по списку).

Самостоятельно выйти из «прослушанного» состояния ({EMOJI_FINISHED}) ученик может, только удалив свое сообщение. Если преподаватель видит, что это состояние наступило некорректно (например, ученик случайно зашёл не в тот канал, или хотел сдавать определенному преподавателю), он может стереть все реакции у сообщения, вернув его к изначальному состоянию.

Сообщения от преподавателей игнорируются (кроме команд).

(Определения.
_Преподаватель_ — это участник сервера, у которого есть роль, название которой начинается с «препод» в любом регистре.
_Текстовый чат очереди_ — это любой текстовый чат, название которого начинается с «очередь» в любом регистре, либо находящийся в категории с подобным названием.
_Голосовой канал очереди_ — это любой голосовой канал, название которого начинается с «очередь» в любом регистре, либо находящийся в категории с подобным названием.)
""".strip('\n')

QUEUE_COMMANDS_SHORT = """
Команды: «команды», «правила», «алгоритм», «следующий».
""".strip('\n')

QUEUE_COMMANDS = """
Команды:
«команды» — список команд с описанием;
«правила» — краткие правила очереди;
«алгоритм» — более полное описание алгоритма;
«следующий» — в чате очереди, переместить первого ученика в очереди в голосовой канал к преподавателю.
""".strip('\n')

EDIT_RULES_ON_STARTUP = True

QUEUE_PREFIX = "очередь"
TEXT_QUEUE_PREFIX = QUEUE_PREFIX
VOICE_QUEUE_PREFIX = QUEUE_PREFIX
TEACHER_ROLE_PREFIX = "препод"

TIME_LIMIT_CLEAN  = 60 * 60 * 24 * 7
TIME_LIMIT_ACTIVE = 60 * 60
TIME_EPSILON = 10

class QueueBot(discord.Client):

    def __init__(self, *args, **kwargs):
        kwargs["intents"] = discord.Intents(
            guilds=True, members=True,
            messages=True, voice_states=True, reactions=True )
        super().__init__(*args, **kwargs)
        self.queue_states = dict()
        self.dict_lock = asyncio.Lock()
        self.student_activity = self.StudentActivity(self.loop)
        self.student_activity.start_monitor()

    async def on_error(self, event, *args, **kwargs):
        _LOGGER.exception( f"Exception in {event}",
            exc_info=True )

    class QueueState:

        def __init__(self):
            self.messages = dict()
            self.finished = set()
            self.update()
            self.lock = asyncio.Lock()

        def update(self):
            self.mtime = time.monotonic()

    async def queue_state(self, guild, member):
        if not isinstance(member, int):
            if guild is None:
                guild = member.guild
            member = member.id
        if not isinstance(guild, int):
            guild = guild.id
        async with self.dict_lock:
            try:
                guild_states = self.queue_states[guild]
            except KeyError:
                guild_states = self.queue_states[guild] = dict()
            try:
                state = guild_states[member]
            except KeyError:
                state = guild_states[member] = self.QueueState()
                self.loop.create_task(
                    self.clean_queue_state(guild, member, state) )
            return state

    async def clean_queue_state(self, guild_id, member_id, queue_state):
        while True:
            delta = queue_state.mtime + TIME_LIMIT_CLEAN - time.monotonic()
            if delta > 0:
                await asyncio.sleep(delta + TIME_EPSILON)
                continue
            break
        async with self.dict_lock:
            if guild_id not in self.queue_states:
                return
            guild_states = self.queue_states[guild_id]
            if member_id not in guild_states:
                return
            if guild_states[member_id] is not queue_state:
                return
            del guild_states[member_id]
            if not guild_states:
                del self.queue_states[guild_id]
        await self.vandalize_queue_state( guild_id, member_id,
            queue_state )

    async def vandalize_queue_state(self, guild_id, member_id, queue_state):
        async with queue_state.lock:
            guild = self.get_guild(guild_id)
            if guild is None:
                return
            member = guild.get_member(member_id)
            if member is None:
                return
            for channel_id, message_id in queue_state.messages.items():
                channel = guild.get_channel(channel_id)
                if channel is None:
                    continue
                try:
                    message = await channel.fetch_message(message_id)
                except (discord.NotFound, discord.Forbidden):
                    continue
                await self.message_add_reactions(message, {EMOJI_IGNORED})
            queue_state.messages.clear()
            queue_state.finished.clear()

    class StudentActivity(dict):

        def __init__(self, loop):
            self.loop = loop
            self.awaken = asyncio.Event()
            self.wake_task = None
            self.wake_time = None

        def start_monitor(self):
            self.loop.create_task(self.monitor())

        async def monitor(self):
            while True:
                await self.awaken.wait()
                self.awaken.clear()
                self.wake_task = self.wake_time = None
                now = time.monotonic()
                gone_inactive = set()
                deltas = list()
                for guild, atime in self.items():
                    delta = atime + TIME_LIMIT_ACTIVE - now
                    if delta <= 0:
                        gone_inactive.add(guild)
                    else:
                        deltas.append(delta)
                for guild in gone_inactive:
                    del self[guild]
                    self.report_inactive(guild)
                if deltas:
                    self.delay_wake(duration=min(deltas) + TIME_EPSILON)
                else:
                    self.report_silence()

        def report_silence(self):
            _LOGGER.info(
                f"All servers have gone inactive" )

        def report_active(self, guild):
            _LOGGER.info(
                f"Server “{guild.name}” (id={guild.id}) has become active" )

        def report_inactive(self, guild):
            guild_name = guild.name
            if guild_name is None:
                guild_name = "<unavailable>"
            _LOGGER.info(
                f"Server “{guild_name}” (id={guild.id}) has gone inactive" )

        def note_guild(self, guild, mtime=None):
            fresh = guild not in self
            now = time.monotonic()
            if mtime is not None:
                age = ( datetime.now(timezone.utc).replace(tzinfo=None)
                    - mtime ).total_seconds()
                if age >= TIME_LIMIT_ACTIVE:
                    return
                now -= age
            if fresh:
                self.report_active(guild)
            self[guild] = now
            if fresh:
                self.delay_wake( now=now,
                    duration=TIME_LIMIT_ACTIVE + TIME_EPSILON )

        def clear_guild(self, guild):
            if guild in self:
                del self[guild]
                self.report_inactive(guild)

        def delay_wake(self, *, now=None, duration):
            if now is None:
                now = time.monotonic()
            wake_time = now + duration
            if self.wake_task is not None:
                if self.wake_time <= wake_time:
                    return
                self.wake_task.cancel()
                self.wake_task = self.wake_time = None
            self.wake_task = self.loop.create_task(
                self.wait_and_wake(duration) )
            self.wake_time = wake_time

        async def wait_and_wake(self, duration):
            await asyncio.sleep(duration)
            self.awaken.set()

    def member_is_teacher(self, member):
        return any(
            role.name.lower().startswith(TEACHER_ROLE_PREFIX)
            for role in member.roles )

    def text_channel_is_queue(self, text_channel):
        if text_channel.name.lower().startswith(TEXT_QUEUE_PREFIX):
            return True
        category = text_channel.category
        if category is not None and \
                category.name.lower().startswith(QUEUE_PREFIX):
            return True
        return False

    def voice_channel_is_queue(self, voice_channel):
        if voice_channel.name.lower().startswith(VOICE_QUEUE_PREFIX):
            return True
        category = voice_channel.category
        if category is not None and \
                category.name.lower().startswith(QUEUE_PREFIX):
            return True
        return False

    def queue_channels(self, guild):
        for text_channel in guild.text_channels:
            if self.text_channel_is_queue(text_channel):
                yield text_channel

    async def consider_message( self, message, *,
        guild=None, channel=None, member=None,
        queue_state=None, lock_acquired=False,
        historical=False,
    ):
        # return whether something has changed
        if member is None:
            member = message.author
            if not isinstance(member, discord.Member):
                return False
            if member == self.user:
                return False
            if self.member_is_teacher(member):
                return False
        if channel is None:
            channel = message.channel
            if not isinstance(channel, discord.TextChannel):
                return False
            if not self.text_channel_is_queue(channel):
                return False
        if guild is None:
            guild = member.guild
            assert guild == channel.guild
        if queue_state is None:
            if lock_acquired:
                raise RuntimeError("escaping potential deadlock")
            queue_state = await self.queue_state(guild, member)
        if not lock_acquired:
            await queue_state.lock.acquire()
        try:
            messages = queue_state.messages
            finished = queue_state.finished
            emoji = set( reaction.emoji
                for reaction in message.reactions
                if reaction.emoji in EMOJI_SPECTRUM )
            if EMOJI_IGNORED in emoji:
                if channel.id in messages and \
                        messages[channel.id] == message.id:
                    del messages[channel.id]
                    finished.discard(message.id)
                    queue_state.update()
                    return True
                return False
            if channel.id in messages:
                if messages[channel.id] == message.id:
                    if EMOJI_ACTIVE not in emoji:
                        if EMOJI_FINISHED in emoji:
                            if message.id not in finished:
                                finished.add(message.id)
                                queue_state.update()
                                self.student_activity.note_guild(guild)
                                return True
                        else:
                            if message.id in finished:
                                finished.discard(message.id)
                                queue_state.update()
                                self.student_activity.note_guild(guild)
                                return True
                    return False
                old_message_id = messages[channel.id]
                try:
                    old_message = await channel.fetch_message(old_message_id)
                except (discord.NotFound, discord.Forbidden):
                    old_message = None
                if old_message is None:
                    del messages[channel.id]
                    finished.discard(old_message_id)
                elif old_message_id in finished:
                    del messages[channel.id]
                    finished.discard(old_message_id)
                    await old_message.add_reaction(EMOJI_IGNORED)
                else:
                    await self.message_add_reactions(message, {EMOJI_IGNORED})
                    return False
            messages[channel.id] = message.id
            if EMOJI_FINISHED in emoji:
                finished.add(message.id)
            queue_state.update()
            if not historical:
                self.student_activity.note_guild(guild)
            else:
                self.student_activity.note_guild( guild,
                    mtime=message.created_at )
            return True
        finally:
            if not lock_acquired:
                queue_state.lock.release()

    async def update_student( self, member, *,
        guild=None, voice=None, voice_arg=False, allow_finish=False,
        queue_state=None, lock_acquired=False,
    ):
        if guild is None:
            guild = member.guild
        if queue_state is None:
            if lock_acquired:
                raise RuntimeError("escaping potential deadlock")
            queue_state = await self.queue_state(guild, member)
        if not lock_acquired:
            await queue_state.lock.acquire()
        try:
            messages = queue_state.messages
            finished = queue_state.finished
            if not voice_arg:
                voice = member.voice
            if voice is None or voice.channel is None:
                status = "astray"
            elif self.voice_channel_is_queue(voice.channel):
                status = "normal"
            else:
                status = "active"
            prospective_finished = set()
            if status == "active":
                if not finished and allow_finish:
                    for channel in self.queue_channels(guild):
                        if channel.id not in messages:
                            continue
                        prospective_finished.add(messages[channel.id])
                        break
                self.student_activity.note_guild(guild)
            garbage = list()
            for channel_id, message_id in messages.items():
                channel = guild.get_channel(channel_id)
                if channel is None or not self.text_channel_is_queue(channel):
                    garbage.append((channel_id, message_id))
                    continue
                try:
                    message = await channel.fetch_message(message_id)
                except (discord.NotFound, discord.Forbidden):
                    garbage.append((channel_id, message_id))
                    continue
                emoji = set()
                if message_id in prospective_finished:
                    finished.add(message_id)
                if status == "active":
                    emoji.add(EMOJI_ACTIVE)
                elif message_id in finished:
                    emoji.add(EMOJI_FINISHED)
                elif status == "astray":
                    emoji.add(EMOJI_ASTRAY)
                await self.message_add_reactions(message, emoji)
            for channel_id, message_id in garbage:
                del messages[channel_id]
                finished.discard(message_id)
            queue_state.update()
        finally:
            if not lock_acquired:
                queue_state.lock.release()

    async def on_ready(self):
        _LOGGER.info(
            f"{self.user} has connected to Discord, "
            f"and manages queues on {len(self.guilds)} servers" )

    async def on_guild_available(self, guild):
        me = guild.me
        _LOGGER.info(
            f"Server “{guild.name}” (id={guild.id}) is available, and "
            f"I am “{me.nick if me.nick is not None else me.name}”" )
        await self.reconsider_guild(guild)

    on_guild_join = on_guild_available

    async def on_guild_unavailable(self, guild):
        guild_name = guild.name
        if guild_name is None:
            guild_name = "<unavailable>"
        _LOGGER.info(
            f"Server “{guild_name}” (id={guild.id}) is unavailable" )
        async with self.dict_lock:
            if guild.id in self.queue_states:
                del self.queue_states[guild.id]
        self.student_activity.clear_guild(guild)

    async def reconsider_guild(self, guild):
        for channel in self.queue_channels(guild):
            await self.reconsider_channel(channel, guild=guild)
        async with self.dict_lock:
            if guild.id not in self.queue_states:
                return
            for member_id, queue_state in self.queue_states[guild.id].items():
                member = guild.get_member(member_id)
                await self.update_student( member, guild=guild,
                    queue_state=queue_state )

    async def reconsider_channel(self, channel, *, guild):
        try:
            prehistoric = ( datetime.now(timezone.utc).replace(tzinfo=None) -
                timedelta(TIME_LIMIT_CLEAN) )
            prehistoric_limit = 7
            async for message in channel.history(limit=None):
                if EDIT_RULES_ON_STARTUP and message.author == self.user:
                    content = message.content
                    if not content.startswith(QUEUE_RULES_PREFIX):
                        continue
                    if content == QUEUE_RULES_PREFIX + "\n" + QUEUE_RULES:
                        continue
                    _LOGGER.info("editing previously posted rules")
                    await message.edit(
                        content=QUEUE_RULES_PREFIX + "\n" + QUEUE_RULES )
                    prehistoric_limit = 0
                    continue
                if message.created_at <= prehistoric:
                    prehistoric_limit -= 1
                    if prehistoric_limit < 0:
                        break
                await self.consider_message( message,
                    guild=guild, channel=channel,
                    historical=True )
        except discord.Forbidden:
            pass

    async def on_message(self, message):
        member = message.author
        channel = message.channel
        if member == self.user:
            return
        if not isinstance(channel, discord.TextChannel):
            return
        if self.user in message.mentions:
            if await self.on_command(message):
                return
        if self.member_is_teacher(member):
            return
        if not self.text_channel_is_queue(channel):
            return
        queue_state = await self.queue_state(None, member)
        async with queue_state.lock:
            changed = await self.consider_message( message,
                member=member, channel=channel,
                queue_state=queue_state, lock_acquired=True )
            if changed:
                await self.update_student( member,
                    queue_state=queue_state, lock_acquired=True )

    async def on_voice_state_update(self, member, before, after):
        if self.member_is_teacher(member):
            return
        if before.channel == after.channel:
            return
        await self.update_student(member, voice=after, allow_finish=True)

    async def on_raw_reaction_add(self, payload):
        if payload.user_id == self.user.id:
            return
        if payload.guild_id is None:
            return
        guild = self.get_guild(payload.guild_id)
        if guild is None:
            return
        channel = guild.get_channel(payload.channel_id)
        if channel is None or not isinstance(channel, discord.TextChannel):
            return
        if not self.text_channel_is_queue(channel):
            return
        try:
            message = await channel.fetch_message(payload.message_id)
        except (discord.NotFound, discord.Forbidden):
            return
        member = message.author
        if member == self.user:
            return
        if self.member_is_teacher(member):
            return
        queue_state = await self.queue_state(guild, member)
        async with queue_state.lock:
            changed = await self.consider_message( message,
                member=member, channel=channel,
                queue_state=queue_state, lock_acquired=True )
            if changed:
                await self.update_student( member,
                    queue_state=queue_state, lock_acquired=True )

    async def on_raw_reaction_remove(self, payload):
        if payload.guild_id is None:
            return
        guild = self.get_guild(payload.guild_id)
        if guild is None:
            return
        channel = guild.get_channel(payload.channel_id)
        if channel is None or not isinstance(channel, discord.TextChannel):
            return
        if not self.text_channel_is_queue(channel):
            return
        try:
            message = await channel.fetch_message(payload.message_id)
        except (discord.NotFound, discord.Forbidden):
            return
        member = message.author
        if member == self.user:
            return
        if self.member_is_teacher(member):
            return
        queue_state = await self.queue_state(guild, member)
        async with queue_state.lock:
            changed = await self.consider_message( message,
                member=member, channel=channel,
                queue_state=queue_state, lock_acquired=True )
            if changed:
                await self.update_student( member,
                    queue_state=queue_state, lock_acquired=True )

    on_raw_reaction_clear_emoji = on_raw_reaction_remove
    on_raw_reaction_clear = on_raw_reaction_remove

    async def message_add_reactions(self, message, emoji_set):
        try:
            if not emoji_set <= EMOJI_SPECTRUM:
                unknown_emoji = next(emoji_set - EMOJI_SPECTRUM)
                raise RuntimeError(
                    f"Unrecognised emoji {unknown_emoji} "
                    f"(code {''.join(hex(ord(e)) for ee in unknown_emoji)})" )
            me = self.user
            emoji_add = set(emoji_set)
            reactions_remove = list()
            for reaction in message.reactions:
                if reaction.emoji not in EMOJI_SPECTRUM:
                    continue
                if reaction.emoji in emoji_set:
                    async for user in reaction.users():
                        if user == me:
                            emoji_add.discard(reaction.emoji)
                            break
                else:
                    reactions_remove.append(reaction)
            for emoji in emoji_add:
                await message.add_reaction(emoji)
            for reaction in reactions_remove:
                await reaction.clear()
        except (discord.NotFound, discord.Forbidden):
            return

    async def on_command(self, message):
        # return whether it really should be considered a command
        if not self.member_is_teacher(message.author):
            return False
        channel = message.channel
        if not isinstance(channel, discord.TextChannel):
            return False
        mention_prefix = f"<@!{self.user.id}> "
        if message.content.startswith(mention_prefix):
            command = message.content[len(mention_prefix):]
        else:
            await self.send_help( channel,
                reply_to=message.author, error="Команда не распознана." )
            return True
        if command.lower() == "команды":
            await self.send_help(channel, short=False)
        elif command.lower() == "правила":
            await self.on_command_rules( channel,
                is_queue=self.text_channel_is_queue(channel) )
        elif command.lower() == "алгоритм":
            await self.on_command_algorithm(channel)
        elif command.lower() == "следующий":
            if not self.text_channel_is_queue(channel):
                await channel.send( f"<@!{message.author.id}> "
                    "Команду «следующий» можно запускать "
                    "только в чате очереди.",
                    allowed_mentions=discord.AllowedMentions(
                        everyone=False, roles=False,
                        users=[message.author] )
                )
                return True
            await self.on_command_next(channel, message.author)
            await message.delete()
        else:
            await self.send_help( channel,
                reply_to=message.author,
                error=f"Команда «{command}» не распознана" )
        return True

    async def on_command_rules(self, channel, *, is_queue=False):
        #nick = channel.guild.me.nick
        prefix = QUEUE_RULES_PREFIX if is_queue else QUEUE_RULES_PREFIX_OTHER
        await channel.send( prefix + "\n" + QUEUE_RULES,
            allowed_mentions=discord.AllowedMentions(
                everyone=False, roles=False, users=False )
        )

    async def on_command_algorithm(self, channel):
        await channel.send( QUEUE_ALGORITHM,
            allowed_mentions=discord.AllowedMentions(
                everyone=False, roles=False, users=False )
        )

    async def on_command_next(self, channel, teacher):
        guild = channel.guild
        async for message in channel.history(oldest_first=True):
            member = message.author
            if not isinstance(member, discord.Member):
                continue
            if member == self.user:
                continue
            if self.member_is_teacher(member):
                continue
            queue_state = await self.queue_state(guild, member)
            if channel.id not in queue_state.messages:
                continue
            if queue_state.messages[channel.id] != message.id:
                continue
            if message.id in queue_state.finished:
                continue
            if member.voice is None or member.voice.channel is None:
                return
            break
        else:
            return
        async with queue_state.lock:
            if teacher.voice is None or teacher.voice.channel is None:
                return
            voice_channel = teacher.voice.channel
            if self.voice_channel_is_queue(voice_channel):
                return
            await member.move_to(voice_channel, reason="queue")
            queue_state.finished.add(message.id)

    async def send_help( self, channel,
        *, reply_to=None, error=None, short=True,
    ):
        sentences = []
        if reply_to is not None:
            sentences.append(f"<@!{reply_to.id}>")
        if error is not None:
            sentences.append(error)
        sentences.append(
            f"Формат команд: `@{channel.guild.me.name} <команда>`." )
        sentences.append(QUEUE_COMMANDS_SHORT if short else QUEUE_COMMANDS)
        await channel.send( " ".join(sentences),
            allowed_mentions=discord.AllowedMentions(
                everyone=False, roles=False,
                users=[reply_to] if reply_to is not None else False )
        )


if __name__ == '__main__':
    parser = argparse.ArgumentParser("QueueBot for Discord")
    parser.add_argument("--log-level", choices=["DEBUG", "INFO", "WARNING"], default="INFO")
    parser.add_argument("--log-file")
    args = parser.parse_args()
    _LOGGER.setLevel(args.log_level)
    log_formatter = logging.Formatter(
        "%(asctime)s %(message)s",
        "%Y-%m-%d %H:%M:%S" )
    if args.log_file is None:
        log_stream_handler = logging.StreamHandler()
        _LOGGER.addHandler(log_stream_handler)
        log_handler.setFormatter(log_formatter)
    else:
        log_stream_handler = logging.StreamHandler()
        log_stream_handler.setLevel("ERROR")
        log_stream_handler.setFormatter(log_formatter)
        _LOGGER.addHandler(log_stream_handler)
        log_file_handler = logging.handlers.RotatingFileHandler(
            args.log_file,
            maxBytes=(1 << 18), backupCount=1 )
        def antirotator(source, dest):
            with open(source, "wb"):
                pass
        log_file_handler.rotator = antirotator
        log_file_handler.setFormatter(log_formatter)
        _LOGGER.addHandler(log_file_handler)
    _LOGGER.info("starting up")
    _LOGGER.debug("debug output enabled")
    client = QueueBot()
    client.run(os.getenv('DISCORD_TOKEN'))
    _LOGGER.info("shutting down")

# vim: set wrap lbr :
