import discord
from discord.ext import commands
from discord import app_commands
import uuid
import config
import json
import aiohttp
import asyncio
import os
import tiktoken
import io
import sqlite3
from datetime import datetime

class ChatGPT(commands.Cog):
	chatgptcom = app_commands.Group(name='chatgpt',description="chat with chatgpt")

	def __init__(self, bot):
		self.bot:commands.Bot = bot
		self.model_3 = "gpt-3.5-turbo"
		self.model_4 = "gpt-4o"
		self.chatgptchannel = config.chatgpt_channel
		self.chatgptdb = sqlite3.connect(os.path.join(config.dir, 'database', 'chatgpt.db'), isolation_level=None)

	def num_tokens_from_messages(self, messages:list, model:str):
		"""Return the number of tokens used by a list of messages."""
		try:
			encoding = tiktoken.encoding_for_model(model)
		except KeyError:
			encoding = tiktoken.get_encoding("cl100k_base")
		num_tokens = 0
		for message in messages:
			num_tokens += 3
			for key, value in message.items():
				num_tokens += len(encoding.encode(value))
				if key == "name":
					num_tokens += 1
		num_tokens += 3
		return num_tokens

	async def chat(self, message:dict, max_tokens:int, chatid:str | int, model:str) -> tuple[dict, str]:
		data = {
			"model": model,
			"messages": message,
			"max_tokens": max_tokens,
			"user": self.chatgptdb.execute('SELECT uuid FROM list WHERE id = ?', (int(chatid),)).fetchone()[0]
		}
		headers = {
			"Content-Type": "application/json",
			"Authorization": f"Bearer {config.chatgpt}"
		}
		async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=90)) as s:
			async with s.post("https://api.openai.com/v1/chat/completions", data=json.dumps(data), headers=headers) as r:
				r.raise_for_status()
				response = await r.json()
				processing_ms = r.headers['openai-processing-ms']
				return response, processing_ms

	async def moderation(self, text:str) -> str:
		headers = {
			"Content-Type": "application/json",
			"Authorization": f"Bearer {config.chatgpt}"
		}
		data = {
			"input": text
		}
		async with aiohttp.ClientSession() as s:
			async with s.post("https://api.openai.com/v1/moderations", data=json.dumps(data), headers=headers) as r:
				r.raise_for_status()
				mod = await r.json()
				modfoo = ""
				for mod_type, mod_bool in mod["results"][0]["categories"].items():
					if mod_bool:
						modfoo += f" | {mod_type}: {round(mod['results'][0]['category_scores'][mod_type], 4)}"
				return modfoo

	def ischat(self, chatid:str | int) -> bool:
		cursor = self.chatgptdb.execute('SELECT * FROM list WHERE id = ?', (int(chatid),)).fetchone()
		if cursor is None:
			return False
		elif not os.path.isdir(os.path.join(config.dir, 'data', 'chatgpt', str(chatid))):
			return False
		else:
			return True

	def token_limit(self) -> tuple[bool, int | None]:
		limit = 210000
		last_time = self.chatgptdb.execute('SELECT value FROM memo WHERE key = ?', ("token_reset",)).fetchone()[0]
		tokens = self.chatgptdb.execute('SELECT value FROM memo WHERE key = ?', ("token_limit",)).fetchone()[0]
		now = int(datetime.now().timestamp())
		if now - last_time > 3600:
			if tokens < limit:
				self.chatgptdb.execute('UPDATE memo SET value = ? WHERE key = ?', (limit, "token_limit"))
			self.chatgptdb.execute('UPDATE memo SET value = ? WHERE key = ?', (now, "token_reset"))
			self.chatgptdb.commit()
			return True, None
		else:
			if tokens <= 0:
				return False, last_time + 3600
			else:
				return True, None

	def token_set(self, tokens_usage:dict, token_punish:int) -> int:
		tokens = self.chatgptdb.execute('SELECT value FROM memo WHERE key = ?', ("token_limit",)).fetchone()[0]
		total_tokens = tokens_usage["prompt_tokens"] + (tokens_usage["completion_tokens"] * 3)
		remain = tokens - (total_tokens * token_punish) if tokens - (total_tokens * token_punish) > 0 else 0
		self.chatgptdb.execute('UPDATE memo SET value = ? WHERE key = ?', (remain, "token_limit"))
		self.chatgptdb.commit()
		return remain

	def get_chat_data(self, chatid:str | int) -> tuple[list, int]:
		msgid = self.chatgptdb.execute('SELECT msgid FROM list WHERE id = ?', (int(chatid),)).fetchone()[0]
		with open(os.path.join(config.dir, 'data', 'chatgpt', str(chatid), "chat.json"), 'r') as f:
			chat_log:list = json.load(f)
			chat_data = [
				{
					"role": "system",
					"content": "Please always respond in Traditional Chinese"
				}
			]
			for mess in chat_log:
				mes = {
					"role": "user",
					"content": mess["user"]
				}
				chat_data.append(mes)
				mes = {
					"role": "assistant",
					"content": mess["assistant"]
				}
				chat_data.append(mes)
		return chat_data, msgid

	@chatgptcom.command(name="model")
	async def GPT_model(self, ctx:discord.Interaction, model:int):
		await ctx.response.defer(ephemeral=True)
		self.chatgptdb.execute('UPDATE memo SET value = ? WHERE key = ?', (model, "gpt4"))
		self.chatgptdb.commit()
		await ctx.followup.send("successfully", ephemeral=True)

	@chatgptcom.command(name="tokens")
	async def tokens(self, ctx:discord.Interaction):
		await ctx.response.defer(ephemeral=True)
		self.token_limit()
		last_time = self.chatgptdb.execute('SELECT value FROM memo WHERE key = ?', ("token_reset",)).fetchone()[0]
		tokens = self.chatgptdb.execute('SELECT value FROM memo WHERE key = ?', ("token_limit",)).fetchone()[0]
		last_time += 3600
		text = f"**Remain:**\n`{tokens}`\n**Reset:**\n<t:{last_time}:f>\n<t:{last_time}:R>"
		embed = discord.Embed(color=discord.Colour(196287), title="Token limit", description=text)
		await ctx.followup.send(embed=embed)

	@chatgptcom.command(name="generate")
	async def generate(self, ctx:discord.Interaction, text:str, chatid:int = None, max_tokens:int = 1024):
		await ctx.response.defer(ephemeral=True)
		if ctx.channel.id != self.chatgptchannel:
			await ctx.followup.send("You can't use this command at this channel!", ephemeral=True)
			return
		if max_tokens % 4 > 0:
			await ctx.followup.send("Invalid max tokens number", ephemeral=True)
			return
		if chatid is None:
			chatid = self.chatgptdb.execute('SELECT value FROM memo WHERE key = ?', ("last",)).fetchone()[0]
		if not self.ischat(chatid):
			await ctx.followup.send("Chat not found!", ephemeral=True)
			return
		token_limit, token_reset = self.token_limit()
		if not token_limit:
			await ctx.followup.send(f"Token limit reached! | Reset: <t:{token_reset}:R>", ephemeral=True)
			return
		await ctx.followup.send("Please wait...")
		chat_data, msgid = self.get_chat_data(chatid)
		msg = {
			"role": "user",
			"content": text
		}
		chat_data.append(msg)
		if self.chatgptdb.execute('SELECT value FROM memo WHERE key = ?', ("gpt4",)).fetchone()[0]:
			model = self.model_4
			model_max_token = 128000
			token_punish = 10
		else:
			model = self.model_3
			model_max_token = 16385
			token_punish = 1
		prompt_tokens = self.num_tokens_from_messages(chat_data, model)
		if prompt_tokens + max_tokens > model_max_token:
			await ctx.followup.send(f"Tokens limit reached! | prompt: {prompt_tokens} | all: {prompt_tokens + max_tokens}", ephemeral=True)
			return
		try:
			chat_json, processing_ms = await self.chat(message=chat_data, max_tokens=max_tokens, chatid=chatid, model=model)
			user_modfoo = await self.moderation(text)
			assistant_modfoo = await self.moderation(chat_json["choices"][0]["message"]["content"])
			msgid += 1
			self.chatgptdb.execute('UPDATE memo SET value = ? WHERE key = ?', (chatid, "last"))
			self.chatgptdb.execute('UPDATE list SET msgid = ? WHERE id = ?', (msgid, int(chatid)))
			self.chatgptdb.commit()
		except aiohttp.ClientResponseError as e:
			await ctx.followup.send(f"Request failed! {e.status}", ephemeral=True)
			return
		except asyncio.TimeoutError:
			await ctx.followup.send(f"Request failed! Connection time out", ephemeral=True)
			return

		embed_list = []
		embed = discord.Embed(color=discord.Colour(196287), description=text)
		embed.set_author(name=ctx.user.display_name, icon_url=ctx.user.avatar.url)
		embed.set_footer(text=f"ChatID: {chatid} | MessageID: {str(msgid)}{user_modfoo}")
		embed_list.append(embed)

		embed = discord.Embed(color=discord.Colour(196193), description=chat_json["choices"][0]["message"]["content"])
		embed.set_author(name="ChatGPT")
		embed.set_footer(text=f'{chat_json["model"]} | {chat_json["id"]} | tokens: {chat_json["usage"]["total_tokens"]}({chat_json["usage"]["prompt_tokens"]}/{chat_json["usage"]["completion_tokens"]})/{self.token_set(chat_json["usage"], token_punish)} | {processing_ms}ms{assistant_modfoo}')
		embed_list.append(embed)
		await ctx.followup.send(embeds=embed_list)

		with open(os.path.join(config.dir, 'data', 'chatgpt', str(chatid), "chat.json"), 'r+') as f:
			chat_log = json.load(f)
			msg = {
				"id": msgid,
				"user": text,
				"assistant": chat_json["choices"][0]["message"]["content"],
				"model": model
			}
			chat_log.append(msg)
			f.seek(0)
			json.dump(chat_log, f, ensure_ascii=False, indent=2)
			f.truncate()
		with open(os.path.join(config.dir, 'data', 'chatgpt', str(chatid), "all.json"), 'r+') as f:
			all_log = json.load(f)
			msg = {
				"role": "user",
				"content": text
			}
			all_log.append(msg)
			all_log.append(chat_json["choices"][0]["message"])
			f.seek(0)
			json.dump(all_log, f, ensure_ascii=False, indent=2)
			f.truncate()

	@chatgptcom.command(name="regenerate")
	async def regenerate(self, ctx:discord.Interaction, chatid:int = None, max_tokens:int = 1024):
		await ctx.response.defer(ephemeral=True)
		if ctx.channel.id != self.chatgptchannel:
			await ctx.followup.send("You can't use this command at this channel!", ephemeral=True)
			return
		if max_tokens % 4 > 0:
			await ctx.followup.send("Invalid max tokens number", ephemeral=True)
			return
		if chatid is None:
			chatid = self.chatgptdb.execute('SELECT value FROM memo WHERE key = ?', ("last",)).fetchone()[0]
		if not self.ischat(chatid):
			await ctx.followup.send("Chat not found!", ephemeral=True)
			return
		token_limit, token_reset = self.token_limit()
		if not token_limit:
			await ctx.followup.send(f"Token limit reached! | Reset: <t:{token_reset}:R>", ephemeral=True)
			return
		await ctx.followup.send("Please wait...")
		chat_data, msgid = self.get_chat_data(chatid)
		log_len = len(chat_data)
		if log_len == 2:
			chat_data = chat_data[:1]
			text = chat_data[0]["content"]
		elif log_len > 2:
			log_len -= 1
			chat_data = chat_data[:log_len]
			text = chat_data[log_len-1]["content"]
		else:
			await ctx.followup.send("Cut chat log failed", ephemeral=True)
			return
		if self.chatgptdb.execute('SELECT value FROM memo WHERE key = ?', ("gpt4",)).fetchone()[0]:
			model = self.model_4
			model_max_token = 128000
			token_punish = 10
		else:
			model = self.model_3
			model_max_token = 16385
			token_punish = 1
		prompt_tokens = self.num_tokens_from_messages(chat_data, model)
		if prompt_tokens + max_tokens > model_max_token:
			await ctx.followup.send(f"Tokens limit reached! | prompt: {prompt_tokens} | all: {prompt_tokens + max_tokens}", ephemeral=True)
			return
		try:
			chat_json, processing_ms = await self.chat(message=chat_data, max_tokens=max_tokens, chatid=chatid, model=model)
			assistant_modfoo = await self.moderation(chat_json["choices"][0]["message"]["content"])
			self.chatgptdb.execute('UPDATE memo SET value = ? WHERE key = ?', (chatid, "last"))
			self.chatgptdb.commit()
		except aiohttp.ClientResponseError as e:
			await ctx.followup.send(f"Request failed! {e.status}", ephemeral=True)
			return
		except asyncio.TimeoutError:
			await ctx.followup.send(f"Request failed! Connection time out", ephemeral=True)
			return

		embed = discord.Embed(color=discord.Colour(196193), description=chat_json["choices"][0]["message"]["content"])
		embed.set_author(name="ChatGPT")
		embed.set_footer(text=f'{chat_json["model"]} | {chat_json["id"]} | tokens: {chat_json["usage"]["total_tokens"]}({chat_json["usage"]["prompt_tokens"]}/{chat_json["usage"]["completion_tokens"]})/{self.token_set(chat_json["usage"], token_punish)} | {processing_ms}ms{assistant_modfoo}')
		await ctx.followup.send(embed=embed)

		with open(os.path.join(config.dir, 'data', 'chatgpt', str(chatid), "chat.json"), 'r+') as f:
			chat_log = json.load(f)
			len_chat = len(chat_log) - 1
			chat_log = chat_log[:len_chat]
			msg = {
				"id": msgid,
				"user": text,
				"assistant": chat_json["choices"][0]["message"]["content"],
				"model": model
			}
			chat_log.append(msg)
			f.seek(0)
			json.dump(chat_log, f, ensure_ascii=False, indent=2)
			f.truncate()
		with open(os.path.join(config.dir, 'data', 'chatgpt', str(chatid), "all.json"), 'r+') as f:
			all_log = json.load(f)
			all_log.append(chat_json["choices"][0]["message"])
			f.seek(0)
			json.dump(all_log, f, ensure_ascii=False, indent=2)
			f.truncate()

	@chatgptcom.command(name="create")
	async def create(self, ctx:discord.Interaction, name:str):
		await ctx.response.defer(ephemeral=True)
		chat_uuid = str(uuid.uuid4()).replace("-", "")
		count = self.chatgptdb.execute('SELECT value FROM memo WHERE key = ?', ("count",)).fetchone()[0]
		count += 1
		self.chatgptdb.execute('UPDATE memo SET value = ? WHERE key = ?', (count, "count"))
		self.chatgptdb.execute('UPDATE memo SET value = ? WHERE key = ?', (count, "last"))
		self.chatgptdb.execute('INSERT INTO list VALUES (?, ?, ?, ?)', (count, name, chat_uuid, 0))
		self.chatgptdb.commit()
		os.mkdir(os.path.join(config.dir, 'data', 'chatgpt', str(count)))
		with open(os.path.join(config.dir, 'data', 'chatgpt', str(count), "all.json"), 'w') as f:
			json.dump([], f, ensure_ascii=False, indent=2)
		with open(os.path.join(config.dir, 'data', 'chatgpt', str(count), "chat.json"), 'w') as f:
			json.dump([], f, ensure_ascii=False, indent=2)
		await ctx.followup.send(f"successfully | ChatID: {count}")

	@chatgptcom.command(name="export")
	async def export(self, ctx:discord.Interaction, chatid:int, type:int):
		await ctx.response.defer(ephemeral=True)
		# json = 0, txt = 1
		if not self.ischat(chatid):
			await ctx.followup.send("Chat not found!")
			return
		chat_name, chat_uuid = self.chatgptdb.execute('SELECT name, uuid FROM list WHERE id = ?', (chatid,)).fetchone()
		if type == 0:
			file = discord.File(fp=os.path.join(config.dir, 'data', 'chatgpt', str(chatid), 'all.json'), filename=f"{chat_name}.json")
			await ctx.followup.send(file=file)
		elif type == 1:
			with open(os.path.join(config.dir, 'data', 'chatgpt', str(chatid), 'all.json'), 'r') as f:
				data = json.load(f)
			a = 0
			chat = io.BytesIO()
			chat.write(bytes(f"{chat_name} ({chat_uuid})\n\n", "UTF-8"))
			for msg in data:
				if msg["role"] == "user":
					a += 1
					chat.write(bytes(f"{a} ------------------\n\n", "UTF-8"))
					chat.write(bytes("[user]\n", "UTF-8"))
					chat.write(bytes(f"{msg['content']}\n\n", "UTF-8"))
				elif msg["role"] == "assistant":
					chat.write(bytes("[assistant]\n", "UTF-8"))
					chat.write(bytes(f"{msg['content']}\n\n", "UTF-8"))
			chat.seek(0)
			file = discord.File(fp=chat, filename=f"{chat_uuid}.txt")
			await ctx.followup.send(file=file)

	@chatgptcom.command(name="rename")
	async def rename(self, ctx:discord.Interaction, chatid:int, name:str):
		await ctx.response.defer(ephemeral=True)
		pass

	@chatgptcom.command(name="delete")
	async def delete(self, ctx:discord.Interaction, chatid:int):
		await ctx.response.defer(ephemeral=True)
		pass

async def setup(bot):
	chatgptcog = ChatGPT(bot)
	await bot.add_cog(chatgptcog)