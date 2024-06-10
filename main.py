import discord
from discord.ext import commands
import config
from pathlib import Path
import traceback
import logging
import sends
import datetime
import pytz
import json
import asyncio
import os
from git import Repo
from HoYoLab import hoyolabstart

log_file = os.path.join(config.dir, 'data','logs', 'syslog.txt')
tz = pytz.timezone('Asia/Taipei')

intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix='/', intents=intents, help_command=None, owner_id=config.ownerid)

@bot.event
async def on_ready():
	logging.info(f'Discord.py version: {discord.__version__}')
	logging.info(f"Logged in as {bot.user.name} ({bot.user.id})")
	logging.info("------")
	activity = discord.Game(name='my dog')
	await bot.change_presence(activity=activity)

@bot.event
async def setup_hook():
	await load_cog()
	asyncio.create_task(hoyolabstart())

async def load_cog():
	for cog in [p.stem for p in Path(config.dir, "cog").glob("*.py")]:
		await bot.load_extension(f'cog.{cog}')
		logging.info(f'Loaded {cog}.')
	logging.info('Loaded Cog Done.')

@bot.tree.command(name='cog_load')
async def cog_load(ctx: discord.Interaction, mode: int, cog: str):
	if not await bot.is_owner(ctx.user):
		return
	await ctx.response.defer(ephemeral=True)
	if mode == 0:
		try:
			await bot.load_extension(f'cog.{cog}')
			await ctx.followup.send(f'{cog} loaded successfully.', ephemeral=True)
		except Exception as e:
			await ctx.followup.send(f'{cog} load Failed: {e}.', ephemeral=True)
	elif mode == 1:
		try:
			await bot.unload_extension(f'cog.{cog}')
			await ctx.followup.send(f'{cog} unloaded successfully.', ephemeral=True)
		except Exception as e:
			await ctx.followup.send(f'{cog} unload Failed: {e}.', ephemeral=True)
	elif mode == 2:
		try:
			await bot.reload_extension(f'cog.{cog}')
			await ctx.followup.send(f'{cog} reloaded successfully.', ephemeral=True)
		except Exception as e:
			await ctx.followup.send(f'{cog} reload Failed: {e}.', ephemeral=True)

@bot.tree.command(name="git")
async def git(ctx:discord.Interaction):
	await ctx.response.defer(ephemeral=True)
	local_dir = config.dir
	identity_file = config.git_identity_file
	os.environ['GIT_SSH_COMMAND'] = f'ssh -i {identity_file} -o UserKnownHostsFile=/dev/null -o StrictHostKeyChecking=no'

	repo = Repo(local_dir)
	assert not repo.bare
	repo.git.checkout('main')
	old_commit = repo.head.commit
	origin = repo.remote(name='origin')
	pull_info = origin.pull()
	new_commit = repo.head.commit

	for info in pull_info:
		detail = f"Pulled {info.ref} ({info.ref.name})\nCommit: {info.commit}\nFlags: {info.flags}\nNote: {info.note}"
	if old_commit != new_commit:
		diff_index = old_commit.diff(new_commit)
		detail += "\n"
		for diff in diff_index:
			change_type = diff.change_type
			a_path = diff.a_path
			b_path = diff.b_path

			if change_type == 'A':
				detail += f"Added: {b_path}\n"
			elif change_type == 'D':
				detail += f"Deleted: {a_path}\n"
			elif change_type == 'M':
				detail += f"Modified: {a_path}\n"
			elif change_type == 'R':
				detail += f"Renamed: {a_path} to {b_path}\n"
	await ctx.followup.send(detail)

@bot.event
async def on_error(event, *args, **kwargs):
	tb = traceback.format_exc()
	logging.error(tb)
	message = f"{event}\n```\n{tb}\n```"
	embed = discord.Embed(title="An Error Occurred", description=message, color=0xFF0000)
	embed.set_footer(text='錯誤發生時間')
	embed.timestamp = embed.timestamp = datetime.datetime.now(tz)
	message_send = {'embeds': [embed.to_dict()]}
	try: await sends.by_bot(config.bot_event, message_send)
	except: pass

@bot.tree.error
async def on_command_error(interaction:discord.Interaction, error:discord.app_commands.AppCommandError):
	tb = traceback.format_exc()
	if isinstance(error, discord.app_commands.errors.CommandInvokeError):
		if isinstance(error.original, discord.errors.NotFound):
			return
	if isinstance(error, discord.app_commands.errors.CommandOnCooldown):
		await interaction.response.defer(ephemeral=True)
		await interaction.followup.send(error)
	else:
		try:
			if not interaction.response.is_done():
				await interaction.response.defer(ephemeral=True)
			await interaction.followup.send(f"糟糕 好像發生問題了 :skull_crossbones: Reason: {error.__class__.__name__}: {error}",ephemeral=True)
		finally:
			message = f"{interaction.command.name}\n```\n{error}\n```\n```\n{tb}\n```"
			logging.error(f"A Command Error Occurred {message}")
			embed = discord.Embed(title="A Command Error Occurred", description=message, color=0xFF0000)
			embed.set_footer(text='錯誤發生時間')
			embed.timestamp = embed.timestamp = datetime.datetime.now(tz)
			message_send = {'embeds': [embed.to_dict()]}
			try: await sends.by_bot(config.bot_event, message_send)
			except: pass

@bot.event
async def on_socket_event_type(event_type):
	with open(os.path.join(config.dir, 'data','logs', 'statistic.json'), 'r+') as f:
		data = json.load(f)
		data.setdefault(event_type, 0)
		count = data[event_type]
		count += 1
		data[event_type] = count
		f.seek(0)
		json.dump(data, f, indent=2, sort_keys=True)
		f.truncate()

class LogFilter(logging.Filter):
	def filter(record:logging.LogRecord) -> bool:
		if record.name == "webapp":
			return False
		else:
			return True

handler = config.handler
handler.addFilter(LogFilter)

if __name__ == "__main__":
	bot.run(token=config.token,log_handler=handler,log_formatter=logging.Formatter('%(asctime)s %(levelname)s: %(message)s', datefmt='%Y-%m-%d %H:%M:%S') ,log_level=logging.INFO,root_logger=True)