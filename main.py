import discord
import random

client = discord.Client()


@client.event
async def on_ready():
    print("login in {client.user}")
    await client.change_presence(status=discord.Status.dnd)


@client.event
async def on_message(message):
    if message.content.startswith('!컬쳐랜드'):
        th1 = random.randint(1000, 9900)
        th2 = random.randint(1000, 9999)
        th3 = random.randint(1000, 9999)
        th4 = random.randint(100000,999999)
        THembed = discord.Embed(title='컬쳐랜드', description=str(th1) + '-' + str(th2) + '-' + str(th3) + '-' + str(th4))
        await message.channel.send(embed=THembed)

#봇 토큰을 넣어주세요.
client.run('put token')
