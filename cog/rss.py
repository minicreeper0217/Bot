import discord
from discord.ext import commands
import asyncio
import config
import json
from datetime import datetime
import timecount
import sends
import os
import pytz
import logging
from bs4 import BeautifulSoup
import random
import re
from selenium.webdriver.chrome.options import Options
from selenium.common import exceptions as selenium_exceptions
from selenium import webdriver
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.common.by import By
import sqlite3

tz = pytz.timezone('Asia/Taipei')
rsscog = None

class RSS(commands.Cog):
	def __init__(self, bot):
		self.bot:commands.Bot = bot
		self.fanbotiadb = sqlite3.connect(os.path.join(config.dir, 'database', 'fanbotia.db'), isolation_level=None)
		self.iddb = sqlite3.connect(os.path.join(config.dir, 'database', 'idata.db'), isolation_level=None)
		self.fanbotiadb.execute('PRAGMA auto_vacuum = FULL')
		self.fanbotiadb.execute('VACUUM')
		self.tasks = [
			asyncio.create_task(RSS.auto_fanbotia(self)),
		]

	@timecount.timer
	async def fantia(self):

		async def fantia_send(name, avatar, fanclublink, postlink, title, text, image, time):
			embed = discord.Embed(title=title, description=text, url=postlink,color=0xEA4C89)
			embed.set_author(name=name, url=fanclublink, icon_url=avatar)
			embed.set_footer(text='發佈時間')
			embed.timestamp = tz.localize(datetime.strptime(time,"%Y-%m-%d %H:%M"))
			if image:
				embed.set_image(url=image)
			message_send = {
				'embeds': [embed.to_dict()],
				'username': name,
				'avatar_url': avatar,
				"allowed_mentions": {
						"parse": []
				}
			}
			try:
				await sends.by_webhook(config.fantia_webhook, message_send)
				return True
			except:
				return False

		clubid = self.iddb.execute('SELECT id FROM fantia').fetchall()
		count = 0

		options = Options()
		options.add_argument("--headless")
		options.add_argument("--disable-notifications")
		options.add_argument("start-maximized")
		options.add_argument("disable-infobars")
		options.add_argument("--disable-extensions")
		options.add_argument("--disable-dev-shm-usage")
		options.add_argument("--no-sandbox")
		driver = webdriver.Chrome(options=options)

		with open(os.path.join(config.dir, "data", "fantia.json"), "r") as f:
			cookie = json.load(f)
		driver.get(f"https://fantia.jp")
		driver.delete_all_cookies()
		for x in cookie:
			driver.add_cookie(x)

		for fanclubid in clubid:
			fanclubid = fanclubid[0]
			driver.get(f"https://fantia.jp/fanclubs/{fanclubid}/posts?locale=ja&amp;q%5Bs%5D=newer")
			try:
				wait = WebDriverWait(driver, 20)
				wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, ".module.post.post-md-square")))
				error_msg = "Loging to fantia Failed!!"
				assert BeautifulSoup(driver.page_source, 'html.parser').find("span", class_="avatar"), error_msg
				page_height = driver.execute_script("return document.body.scrollHeight")
				scroll_step = 80
				current_scroll = 0
				while current_scroll < page_height:
					driver.execute_script(f"window.scrollTo(0, {current_scroll});")
					await asyncio.sleep(0.02)
					current_scroll += scroll_step
					driver.execute_script("window.scrollTo(0, 0);")
				page_height = driver.execute_script("return document.body.scrollHeight")
				current_scroll = 0
				while current_scroll < page_height:
					driver.execute_script(f"window.scrollTo(0, {current_scroll});")
					await asyncio.sleep(0.02)
					current_scroll += scroll_step
				await asyncio.sleep(0.5)
			except:
				logging.exception(f"Check fantia fanclub Failed!!")
				break

			new = False
			soup = BeautifulSoup(driver.page_source, 'html.parser')
			fanclub = soup.find('div',class_="module fanclub fanclub-sm")
			fl = (fanclub.find('a'))['href']
			fanclublink = f"https://fantia.jp{fl}"
			name = (fanclub.find('source'))['alt']
			avatar = (fanclub.find('source'))['data-srcset']

			if self.fanbotiadb.execute('SELECT * FROM fantia_restart WHERE id = ?', (fanclubid,)).fetchone() is None:
				new = True

			post = soup.find_all('div',class_="module post post-md-square")
			for p in post:
				try:
					post_title = (p.find('h3')).string
					l = (p.find("a"))['href']
					postlink = f"https://fantia.jp{l}"
					post_id = l.split("/")[-1]
					post_text = (p.find("div",class_="post-body")).find("div",class_="post-text")
					if post_text is not None:
						post_text = post_text.string
					try:
						post_image = (p.find("source"))['data-srcset']
						pattern = r"medium_webp_([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12})\.webp"
						match = re.search(pattern, post_image)
						if match:
							post_image = post_image.replace(match.group(0), f"{match.group(1)}.jpg")
					except:
						post_image = ""
					post_time = p.find("span", class_="post-date")
					if post_time is None:
						post_time = p.find("span", class_="post-date recently")
					if post_time.string is None:
						post_time = p.find("span", class_="mr-5")
					post_time = post_time.string

					if not new and self.fanbotiadb.execute('SELECT * FROM fantia_post WHERE id = ?', (post_id,)).fetchone() is None:
						a = await fantia_send(name, avatar, fanclublink, postlink, post_title, post_text, post_image, post_time)
						if not a:
							continue
						self.fanbotiadb.execute('INSERT INTO fantia_post VALUES (?)', (post_id,))
					elif new:
						try:
							self.fanbotiadb.execute('INSERT INTO fantia_post VALUES (?)', (post_id,))
						except sqlite3.IntegrityError:
							pass
				except:
						logging.exception(f"Pending fantia post failed!! | {fanclubid}")
						return

			post = soup.find_all('div',class_="module post post-md-square is-tipping")
			for p in post:
				try:
					post_title = (p.find('h3')).string
					l = (p.find("a"))['href']
					postlink = f"https://fantia.jp{l}"
					post_id = l.split("/")[-1]
					post_text = (p.find("div",class_="post-body")).find("div",class_="post-text")
					if post_text is not None:
						post_text = post_text.string
					try:
						post_image = (p.find("source"))['data-srcset']
						pattern = r"medium_webp_([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12})\.webp"
						match = re.search(pattern, post_image)
						if match:
							post_image = post_image.replace(match.group(0), f"{match.group(1)}.jpg")
					except:
						post_image = ""
					post_time = p.find("span", class_="post-date")
					if post_time is None:
						post_time = p.find("span", class_="post-date recently")
					if post_time.string is None:
						post_time = p.find("span", class_="mr-5")
					post_time = post_time.string

					if not new and self.fanbotiadb.execute('SELECT * FROM fantia_post WHERE id = ?', (post_id,)).fetchone() is None:
						a = await fantia_send(name, avatar, fanclublink, postlink, post_title, post_text, post_image, post_time)
						if not a:
							continue
						self.fanbotiadb.execute('INSERT INTO fantia_post VALUES (?)', (post_id,))
					elif new:
						try:
							self.fanbotiadb.execute('INSERT INTO fantia_post VALUES (?)', (post_id,))
						except sqlite3.IntegrityError:
							pass
				except:
						logging.exception(f"Pending fantia post failed!! | {fanclubid}")
						return

			count += 1
			if count < len(clubid):
				rd = random.uniform(4,7)
				await asyncio.sleep(rd)

		driver.quit()
		self.fanbotiadb.execute('DELETE FROM fantia_restart')
		for fanclubid in clubid:
			self.fanbotiadb.execute('INSERT INTO fantia_restart VALUES (?)', (fanclubid[0],))
		self.fanbotiadb.commit()

	@timecount.timer
	async def fanbox(self):
		ids = self.iddb.execute('SELECT id FROM fanbox').fetchall()
		options = Options()
		options.add_argument("--headless")
		options.add_argument("--disable-notifications")
		options.add_argument("start-maximized")
		options.add_argument("disable-infobars")
		options.add_argument("--disable-extensions")
		options.add_argument("--disable-dev-shm-usage")
		options.add_argument("--no-sandbox")

		driver = webdriver.Chrome(options=options)

		for id in ids:
			id = id[0]
			driver.get(f"https://www.fanbox.cc/@{id}/posts")
			try:
				wait = WebDriverWait(driver, 20)
				wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, ".CreatorPostList__Wrapper-sc-1gerkjf-0.jZJlYv")))
			except selenium_exceptions.TimeoutException:
				logging.error(f"Can't connect to fanbox!! | {id}")
				return
			await asyncio.sleep(0.5)
			try:
				button = driver.find_element(by=By.CSS_SELECTOR, value=".ButtonBase-sc-1pize7g-0.CommonButton__CommonButtonOuter-sc-1s35wwu-0.iorEfw.dhrsDw")
				button.click()
			except selenium_exceptions.NoSuchElementException:
				pass
			page_height = driver.execute_script("return document.body.scrollHeight")
			scroll_step = 80
			current_scroll = 0
			while current_scroll < page_height:
				driver.execute_script(f"window.scrollTo(0, {current_scroll});")
				await asyncio.sleep(0.02)
				current_scroll += scroll_step
			driver.execute_script("window.scrollTo(0, 0);")
			page_height = driver.execute_script("return document.body.scrollHeight")
			current_scroll = 0
			while current_scroll < page_height:
				driver.execute_script(f"window.scrollTo(0, {current_scroll});")
				await asyncio.sleep(0.02)
				current_scroll += scroll_step
			await asyncio.sleep(0.5)

			html_content = driver.page_source
			soup = BeautifulSoup(html_content, 'html.parser')
			try:
				name = soup.find('a',class_="styled__UserNameText-sc-1upaq18-14 lgKEyE").string
				icon = soup.find('div',class_="LazyImage__BgImage-sc-14k46gk-3 pVmiQ UserIcon__Icon-sc-dzfsti-1 fGNywG styled__StyledUserIcon-sc-1upaq18-10 heHjIG")['style']
				pattern = r'"([^"]*)"'
				match = re.search(pattern, icon)
				if match:
					icon = match.group(1)
				posts = soup.find_all('a',class_="CardPostItem__Wrapper-sc-1bjj922-0 eGwQXQ")
			except:
				try:
					name = soup.find('a',class_="styled__UserNameText-sc-1upaq18-14 lgKEyE").string
					icon = soup.find('div',class_="sc-14k46gk-3 dMigcK UserIcon__Icon-sc-dzfsti-1 fGNywG styled__StyledUserIcon-sc-1upaq18-10 heHjIG")['style']
					pattern = r'"([^"]*)"'
					match = re.search(pattern, icon)
					if match:
						icon = match.group(1)
					posts = soup.find_all('a',class_="CardPostItem__Wrapper-sc-1bjj922-0 eGwQXQ")
				except:
					logging.exception(f"Find fanbox element failed!! | {id}")
					return

			for p in posts:
				link = p['href']
				post_id = link.replace("/posts/", "")
				if self.fanbotiadb.execute('SELECT * FROM fanbox_post WHERE id = ?', (post_id,)).fetchone() is None:
					try:
						title = p.find('div',class_="styled__Title-sc-ar8j54-4 kaPnNn").string
						post_time = p.find('div',class_="styled__PublishedDatetime-sc-ar8j54-2 goQPIE").string
						image = p.find('img', class_="LazyImage__Image-sc-14k46gk-1 KHtJH")
						if image is not None:
							image = image['src']
						price =  p.find('div',class_="FeeLabel__Wrapper-sc-9yyo9a-0 cyOqa-d").string
						if price == "全体公開":
							price = "對所有人公開"
						description = p.find('div',class_="CardPostItem__CardExcerpt-sc-1bjj922-5 jmQxOb")
						if description is not None:
							description = description.string
					except:
						try:
							title = p.find('div',class_="sc-ar8j54-4 gbwxEp").string
							post_time = p.find('div',class_="sc-ar8j54-2 fuDDXn").string
							image = p.find('img', class_="sc-14k46gk-1 ORPUg")
							if image is not None:
								image = image['src']
							price =  p.find('div',class_="FeeLabel__Wrapper-sc-9yyo9a-0 cyOqa-d").string
							if price == "全体公開":
								price = "對所有人公開"
							description = p.find('div',class_="sc-ar8j54-5 jJozUU")
							if description is not None:
								description = description.string
						except:
							logging.exception(f"Find fanbox element failed!! | {id} | {post_id}")
							return

					embed = discord.Embed(title=title, description=description, url=f"https://www.fanbox.cc/@{id}{link}", color=0xFAF18A)
					embed.set_author(name=name, icon_url=icon, url=f"https://www.fanbox.cc/@{id}")
					embed.set_footer(text=f"{price} | 發布時間")
					embed.timestamp = tz.localize(datetime.strptime(post_time, "%Y年%m月%d日 %H:%M"))
					if image is not None:
						embed.set_image(url=image)
					message_send = {
						'embeds': [embed.to_dict()],
						'username': name,
						'avatar_url': icon,
						"allowed_mentions": {
							"parse": []
						}
					}
					if self.fanbotiadb.execute('SELECT * FROM fanbox_restart WHERE id = ?', (id,)).fetchone() is not None:
						try:
							await sends.by_webhook(config.fantia_webhook, message_send)
							self.fanbotiadb.execute('INSERT INTO fanbox_post VALUES (?)', (post_id,))
						except:
							continue
					else:
						try:
							self.fanbotiadb.execute('INSERT INTO fanbox_post VALUES (?)', (post_id,))
						except sqlite3.IntegrityError:
							pass

		driver.quit()
		self.fanbotiadb.execute('DELETE FROM fanbox_restart')
		for id in ids:
			self.fanbotiadb.execute('INSERT INTO fanbox_restart VALUES (?)', (id[0],))
		self.fanbotiadb.commit()

	async def auto_fanbotia(self):
		while True:
			tasks = [asyncio.create_task(self.fanbox())]
			await asyncio.wait(tasks)
			rd = random.uniform(180, 360)
			await asyncio.sleep(rd)
			tasks = [asyncio.create_task(self.fantia())]
			await asyncio.wait(tasks)
			rd = random.uniform(1800, 3600)
			await asyncio.sleep(rd)

async def setup(bot):
	global rsscog
	rsscog = RSS(bot)
	await bot.add_cog(rsscog)

async def teardown(bot):
	global rsscog
	if rsscog is not None:
		tasks = rsscog.tasks
		for task in tasks:
			task.cancel()
		rsscog.fanbotiadb.close()
		rsscog.iddb.close()
		rsscog = None