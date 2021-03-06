# Installing the bot

```
python3 -m virtualenv venv
venv/bin/python3 -m pip install -r requirements.txt
```

# Running the bot

```
DISCORD_TOKEN=<secret> venv/bin/python3 queue_bot.py
```

# Adding the bot to a Discord server

(В инструкции в ссылке `APPLICATONID` нужно заменить на ID бота. Получить его можно на странице бота, найдя её на https://discord.com/developers/applications/)

Наверное, проще всего выдать мне права «Администратор», и я добавлю бота и настрою, но вот зачем-то инструкция:
1. Нужно открыть ссылку и добавить бота на сервер:
`https://discord.com/api/oauth2/authorize?client_id=APPLICATONID&permissions=16854080&scope=bot`
2. Нужно убедиться, что есть текстовый чат и голосовой канал с названиями, начинающимися на «очередь» в любом регистре (или категория с подобным названием — тогда в качестве очереди будут работать все чаты и каналы в ней).
3. В текстовом канале или категории очереди необходимо запретить ученикам «Добавлять реакции», а боту разрешить; и разрешить боту «Управлять сообщениями» (чтобы он мог удалять реакции). Кроме того, боту нужно выдать обычные разрешения, такие как «Просмотр канала», «Читать историю сообщений» и «Отправлять сообщения».
4. Роль преподавателей нужно назвать так, чтобы она начиналась с «препод» в любом регистре; иначе бот не будет их уважать. Кроме того, им стоит выдать как минимум те же разрешения «Добавлять реакции» и «Управлять сообщениями» в очереди.
5. Боту также нужно выдать разрешение «Просмотр канала» для голосовых каналов, в которых идёт сдача задач, чтобы он мог видеть, кто сдаёт задачи.
6. Все голосовые каналы, которые не являются очередью, бот интерпретирует как сдачу задач; поэтому все прочие каналы (не очередь и не сдача задач), если они есть, следует сделать невидимыми для бота (т. е. отключить для него «Просмотр канала» или «Просматривать каналы», если в категории).
7. По команде `@QueueBot правила` бот напечатает краткие правила очереди. Рекомендуется использовать прямо в чате очереди. Команды воспринимаются только от преподавателей.
8. Можно переименовать бота во что-нибудь весёлое, «котик» или «фея», например.
