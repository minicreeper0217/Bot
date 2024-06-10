import discord
from discord.ext import commands
import config
import asyncio
import pytz
import datetime
import timecount
import os
import json
import aiohttp
import hashlib
import random
import hmac
import sqlite3
import logging
import schedule

tz = pytz.timezone('Asia/Taipei')
autocog = None

class Automatic(commands.Cog):
	def __init__(self, bot):
		self.bot:commands.Bot = bot
		asyncio.create_task(Automatic.start(self))

	async def start(self):
		await self.bot.wait_until_ready()
		schedule.every(1).day.at("02:00", tz=tz).do(lambda: asyncio.create_task(self.check_log())).tag("automatic")
		schedule.every(1).day.at("06:00", tz=tz).do(lambda: asyncio.create_task(self.check_ytsubscribe())).tag("automatic")
		schedule.every(1).day.at("14:00", tz=tz).do(lambda: asyncio.create_task(self.check_ytsubscribe())).tag("automatic")
		schedule.every(1).day.at("22:00", tz=tz).do(lambda: asyncio.create_task(self.check_ytsubscribe())).tag("automatic")
		schedule.every(120).seconds.do(lambda: asyncio.create_task(self.auto_clean_messages())).tag("automatic")
		self.tasks = [
			asyncio.create_task(self.check_schedule()),
		]

	async def check_schedule(self):
		while True:
			schedule.run_pending()
			await asyncio.sleep(1)

	async def cleanup_messages(self):
		for channel_id, keywords in config.target_word.items():
			channel = self.bot.get_channel(channel_id)
			async for message in channel.history():
				now = datetime.datetime.now().timestamp()
				if (now - message.created_at.timestamp()) > 1209000:
					return
				if message.author.bot and message.channel.id == channel_id:
					for keyword in keywords:
						if keyword.lower() in message.content.lower():
							try:
								await message.delete()
								await asyncio.sleep(2)
								continue
							except (discord.errors.HTTPException, discord.errors.DiscordServerError):
								await asyncio.sleep(3)
								continue
							except discord.errors.RateLimited as e:
								await asyncio.sleep(e.retry_after)
								continue
							except:
								continue

						for embed in message.embeds:
							if embed.description is not None and keyword.lower() in embed.description.lower() or embed.title is not None and keyword.lower() in embed.title.lower():
								try:
									await message.delete()
									await asyncio.sleep(2)
									continue
								except (discord.errors.HTTPException, discord.errors.DiscordServerError):
									await asyncio.sleep(3)
									continue
								except discord.errors.RateLimited as e:
									await asyncio.sleep(e.retry_after)
									continue
								except:
									continue

	async def command_channel_message(self):
		for channel_id in config.command_channel.keys():
			channel = self.bot.get_channel(channel_id)
			async for message in channel.history():
				now = datetime.datetime.now().timestamp()
				if (now - message.created_at.timestamp()) > 1209000:
					return
				if not message.author.bot and message.channel.id in config.command_channel and channel.id in config.command_channel and config.command_channel[channel.id] != message.type:
					try:
						await message.delete()
						await asyncio.sleep(2)
						continue
					except (discord.errors.HTTPException, discord.errors.DiscordServerError):
						await asyncio.sleep(3)
						continue
					except discord.errors.RateLimited as e:
						await asyncio.sleep(e.retry_after)
						continue
					except:
						continue

	@timecount.timer
	async def auto_clean_messages(self):
		tasks = [
			asyncio.create_task(Automatic.cleanup_messages(self)),
			asyncio.create_task(Automatic.command_channel_message(self))
		]
		await asyncio.wait(tasks)
		return

	async def check_log(self):
		today = datetime.datetime.now(tz).strftime("%Y-%m-%d")
		log_file = os.path.join(config.dir, 'data','logs', 'syslog.txt.2')
		applog_file = os.path.join(config.dir, 'data','logs', 'webapplog.txt.2')
		nginx = '/var/log/nginx/access.log.1'
		if os.path.isfile(nginx):
			with open(os.path.join(config.dir, 'data',"nginx_log.json"),"r") as f:
				data:dict = json.load(f)
			if data['date'] != today:
				hash_sha256 = hashlib.sha256()
				with open(nginx,"rb") as f:
					for chunk in iter(lambda: f.read(4096), b''):
						hash_sha256.update(chunk)
				if hash_sha256.hexdigest() != data['hash']:
					channel = self.bot.get_channel(config.bot_event)
					file = discord.File(nginx,filename=f"nginx-access-log-{data['date']}.txt")
					await channel.send(content="Here is a nginx access log =>",file=file)
					data['date'] = today
					data['hash'] = hash_sha256.hexdigest()
					with open(os.path.join(config.dir, 'data',"nginx_log.json"),"w") as f:
						json.dump(data, f, indent=2)
		if os.path.isfile(log_file):
			channel = self.bot.get_channel(config.bot_event)
			file = discord.File(log_file,filename=f"syslog ({today}).txt")
			await channel.send(content="Here is a backup log =>",file=file)
			os.remove(log_file)
		if os.path.isfile(applog_file):
			channel = self.bot.get_channel(config.bot_event)
			file = discord.File(log_file,filename=f"applog ({today}).txt")
			await channel.send(content="Here is a backup log =>",file=file)
			os.remove(applog_file)

	async def check_ytsubscribe(self):
		now = int(datetime.datetime.now().timestamp())
		with sqlite3.connect(os.path.join(config.dir, 'database', 'youtube.db')) as db:
			cursor = db.execute('SELECT id FROM subscribe WHERE time < ?', (now + 100000,)).fetchall()
			if len(cursor) > 0:
				async with aiohttp.ClientSession() as session:
					for idtup in cursor:
						id = idtup[0]
						secret = hashlib.sha256(random.randbytes(1024)).hexdigest()
						verify_token = hmac.new(bytes(config.secret,"utf-8"), bytes(secret,"utf-8"), hashlib.sha256).hexdigest()
						db.execute('UPDATE subscribe SET secret = ? WHERE id = ?', (secret, id))
						db.commit()
						url = f"https://pubsubhubbub.appspot.com/subscribe?hub.callback=https%3A%2F%2F{config.domain}%2Fwebhook%2Fyoutube%2F{id}&hub.topic=https%3A%2F%2Fwww.youtube.com%2Fxml%2Ffeeds%2Fvideos.xml%3Fchannel_id%3D{id}&hub.verify=async&hub.mode=subscribe&hub.verify_token={verify_token}&hub.secret={secret}&hub.lease_numbers=432000"
						try:
							async with session.post(url) as r:
								r.raise_for_status()
								db.execute('UPDATE subscribe SET time = ? WHERE id = ?', (now + 432000, id))
						except:
							logging.exception("Update youtube subscribe failed!")
							break
				db.commit()

async def setup(bot):
	global autocog
	autocog = Automatic(bot)
	await bot.add_cog(autocog)

async def teardown(bot):
	global autocog
	if autocog is not None:
		tasks = autocog.tasks
		for task in tasks:
			task.cancel()
		schedule.clear("automatic")
		autocog = None