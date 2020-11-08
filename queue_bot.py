# Execution:
# DISCORD_TOKEN=<secret> python3 queue_bot.py

import os
import traceback
import asyncio
import time

import discord

QUEUE_RULES_PREFIX = """
В этом чате установлены следующие правила:
""".strip()
QUEUE_RULES_PREFIX_OTHER = """
В чате очереди установлены следующие правила:
""".strip()
QUEUE_RULES = """
:ok_hand: в очередь нужно записаться, указав задачи, которые вы хотите сдать;
:rage: нельзя писать несколько сообщений; можно редактировать или удалять старое;
:angry: записавшись, нужно подключиться к голосовому каналу очереди (с выключенным микрофоном);
:face_with_monocle: свободный преподаватель переключит вас в свой канал для разговора;
:woman_shrugging: поcле разговора сообщение из очереди нужно удалить.
""".strip()

QUEUE_ALGORITHM = """
Я буду отмечать сообщения учеников в текстовом чате очереди одной из реакций :rage:, :angry:, :face_with_monocle:, :woman_shrugging: (или ни одной из них). Наличие реакции означает, что сообщение нерелевантно по той или иной причине.

:rage: это не первое сообщение ученика в очереди;
:angry: ученик не подключен ни к какому голосовому каналу;
:face_with_monocle: ученик подключен (или его подключили) к некоторому голосовому каналу, кроме канала очереди;
:woman_shrugging: ученик подключался (или его подключали) к некоторому голосовому каналу, кроме канала очереди (если у ученика есть несколько сообщений в разных чатах очереди, будет отмечено только одно из них — по умолчанию в первом канале по списку).

Самостоятельно выйти из «прослушанного» состояния (:woman_shrugging:) ученик может, только удалив свое сообщение. Если преподаватель видит, что это состояние наступило некорректно (например, ученик случайно зашёл не в тот канал, или хотел сдавать определенному преподавателю), он можете стереть все реакции у сообщения, вернув его к изначальному состоянию.

Сообщения от преподавателей игнорируются (кроме команд).

(Определения.
_Преподаватель_ — это участник сервера, у которого есть роль, название которой начинается с «препод» в любом регистре.
_Текстовый чат очереди_ — это любой текстовый чат, название которого начинается с «очередь» в любом регистре, либо находящийся в категории с подобным названием.
_Голосовой канал очереди_ — это любой голосовой канал, название которого начинается с «очередь» в любом регистре, либо находящийся в категории с подобным названием.)
""".strip()

QUEUE_COMMANDS_SHORT = """
Команды: «команды», «правила», «алгоритм», «следующий».
""".strip()

QUEUE_COMMANDS = """
Команды:
«команды» — список команд с описанием;
«правила» — краткие правила очереди;
«алгоритм» — более полное описание алгоритма;
«следующий» — в чате очереди, переместить первого ученика в очереди в голосовой канал к преподавателю.
""".strip()

QUEUE_PREFIX = "очередь"
TEXT_QUEUE_PREFIX = QUEUE_PREFIX
VOICE_QUEUE_PREFIX = QUEUE_PREFIX
TEACHER_ROLE_PREFIX = "препод"

EMOJI_ASTRAY = "\N{ANGRY FACE}"
EMOJI_ACTIVE = "\N{FACE WITH MONOCLE}"
EMOJI_FINISHED = "\N{SHRUG}\N{ZWJ}\N{FEMALE SIGN}\uFE0F"
EMOJI_DUPLICATE = "\N{POUTING FACE}"
EMOJI_SPECTRUM = frozenset({
    EMOJI_ASTRAY,
    EMOJI_ACTIVE,
    EMOJI_FINISHED,
    EMOJI_DUPLICATE,
})

class QueueBot(discord.Client):

    def __init__(self, *args, **kwargs):
        self.queue_states = dict()
        self.dict_lock = asyncio.Lock()
        kwargs["intents"] = discord.Intents(
            guilds=True, members=True,
            messages=True, voice_states=True, reactions=True )
        super().__init__(*args, **kwargs)

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
        try:
            state = self.queue_states[guild, member]
        except KeyError:
            async with self.dict_lock:
                state = self.queue_states[guild, member] = self.QueueState()
            self.loop.create_task(
                self.clean_queue_state(guild, member, state) )
        return state

    async def clean_queue_state(self, guild_id, member_id, queue_state):
        while True:
            delta = queue_state.mtime + 1_000_000 - time.monotonic()
            if delta > 10:
                await asyncio.sleep(delta)
                continue
            break
        async with self.dict_lock:
            if self.queue_states.get((guild_id, member_id)) \
                    is queue_state:
                del self.queue_states[guild_id, member_id]
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
            print(queue_state.messages)
            for channel_id, message_id in queue_state.messages.items():
                channel = guild.get_channel(channel_id)
                if channel is None:
                    continue
                try:
                    message = await channel.fetch_message(message_id)
                except (discord.NotFound, discord.Forbidden):
                    continue
                await self.message_add_reactions(message, {EMOJI_DUPLICATE})
            queue_state.messages.clear()
            queue_state.finished.clear()

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
            queue_state = await self.queue_state(guild, member)
        if not lock_acquired:
            await queue_state.lock.acquire()
        try:
            messages = queue_state.messages
            finished = queue_state.finished
            emoji = set( reaction.emoji
                for reaction in message.reactions
                if reaction.emoji in EMOJI_SPECTRUM )
            if EMOJI_DUPLICATE in emoji:
                if channel.id in messages and \
                        messages[channel.id] == message.id:
                    del messages[channel.id]
                    finished.discard(message.id)
                    queue_state.update()
                    return True
                return False
            if channel.id in messages:
                if messages[channel.id] == message.id:
                    if EMOJI_FINISHED in emoji:
                        if message.id not in finished:
                            finished.add(message.id)
                            queue_state.update()
                            return True
                    else:
                        if message.id in finished:
                            finished.discard(message.id)
                            queue_state.update()
                            return True
                    return False
                old_message_id = messages[channel.id]
                try:
                    old_message = await channel.fetch_message(old_message_id)
                except (discord.NotFound, discord.Forbidden):
                    old_message = None
                if old_message is not None:
                    await self.message_add_reactions(message, {EMOJI_DUPLICATE})
                    return False
                else:
                    del messages[channel.id]
                    finished.discard(old_message_id)
            messages[channel.id] = message.id
            if EMOJI_FINISHED in emoji:
                finished.add(message.id)
            queue_state.update()
            return True
        finally:
            if not lock_acquired:
                queue_state.lock.release()

    async def consider_member( self, member, *,
        guild=None, voice=None, voice_arg=False, allow_finish=False,
        queue_state=None, lock_acquired=False,
    ):
        if guild is None:
            guild = member.guild
        if queue_state is None:
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
                if status == "astray":
                    emoji.add(EMOJI_ASTRAY)
                elif status == "active":
                    emoji.add(EMOJI_ACTIVE)
                if message_id in finished:
                    emoji.add(EMOJI_FINISHED)
                    emoji.discard(EMOJI_ASTRAY)
                if message_id in prospective_finished:
                    emoji.add(EMOJI_FINISHED)
                    finished.add(message_id)
                    emoji.discard(EMOJI_ASTRAY)
                await self.message_add_reactions(message, emoji)
            for (channel_id, message_id) in garbage:
                del messages[channel_id]
                finished.discard(message_id)
            queue_state.update()
        finally:
            if not lock_acquired:
                queue_state.lock.release()

    async def on_ready(self):
        print(f"{self.user} has connected to Discord")
        for guild in self.guilds:
            print( f"{guild.name} (id={guild.id}): "
                f"{guild.me.name}" )
            try:
                for channel in self.queue_channels(guild):
                    async for message in channel.history(oldest_first=True):
                        #if message.author == self.user:
                        #    if message.content.startswith(QUEUE_RULES_PREFIX):
                        #        await message.edit(
                        #            content=QUEUE_RULES_PREFIX + "\n" + QUEUE_RULES )
                        #    continue
                        await self.consider_message( message,
                            guild=guild, channel=channel )
            except Exception:
                print(f"{guild.name} (id={guild.id}): error occured")
                traceback.print_exc()
        async with self.dict_lock:
            for (guild_id, member_id), queue_state in \
                    self.queue_states.items():
                try:
                    guild = self.get_guild(guild_id)
                    member = guild.get_member(member_id)
                    await self.consider_member( member, guild=guild,
                        queue_state=queue_state )
                except Exception:
                    print(f"{guild.name} (id={guild.id}): error occured")
                    traceback.print_exc()

    async def on_message(self, message):
        member = message.author
        if member == self.user:
            return
        if self.user.mentioned_in(message):
            if await self.on_command(message):
                return
        if self.member_is_teacher(member):
            return
        channel = message.channel
        if not isinstance(channel, discord.TextChannel):
            return
        if not self.text_channel_is_queue(channel):
            return
        if await self.consider_message(message, member=member, channel=channel):
            await self.consider_member(member)

    async def on_voice_state_update(self, member, before, after):
        if self.member_is_teacher(member):
            return
        if before.channel == after.channel:
            return
        await self.consider_member(member, voice=after, allow_finish=True)

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
            if await self.consider_message( message,
                member=member, channel=channel,
                queue_state=queue_state, lock_acquired=True,
            ):
                await self.consider_member( member,
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
            if await self.consider_message( message,
                member=member, channel=channel,
                queue_state=queue_state, lock_acquired=True,
            ):
                await self.consider_member( member,
                    queue_state=queue_state, lock_acquired=True )

    on_raw_reaction_clear_emoji = on_raw_reaction_remove
    on_raw_reaction_clear = on_raw_reaction_remove

    async def message_add_reactions(self, message, emoji_set):
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
                    "только в чате очереди." )
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
        await channel.send(prefix + "\n" + QUEUE_RULES)

    async def on_command_algorithm(self, channel):
        await channel.send(QUEUE_ALGORITHM)

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
        await channel.send(" ".join(sentences))


if __name__ == '__main__':
    client = QueueBot()
    client.run(os.getenv('DISCORD_TOKEN'))

# vim: set wrap lbr :
