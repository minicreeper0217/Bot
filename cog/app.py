import discord
from discord.ext import commands
from aiohttp import web
import aiohttp
from aiohttp import web_exceptions
import asyncio
import logging
import logging.handlers
from urllib.parse import urlparse, parse_qs
import config
import os
import json
import datetime
import hmac
import hashlib
import youtube.youtube as yt
import youtube.twitch as twitch
from bs4 import BeautifulSoup
import uuid
import jwt as pyjwt
import sqlite3
import random
import re
from functools import wraps

webapp = None

class Route():
	def __init__(self) -> None:
		self.route_list = []

	def route(self, path:str, method:str):
		def decorator(func):
			@wraps(func)
			async def wrapper(*args, **kwargs):
				result = await func(*args, **kwargs)
				return result
			self.route_list.append({"path":path, "callback":func.__name__, "method":method.upper()})
			return wrapper
		return decorator

	def get_list(self, class_obj) -> list[web.RouteDef]:
		web_list = []
		for path in self.route_list:
			handler = getattr(class_obj, path['callback'])
			web_list.append(web.route(path=path['path'], handler=handler, method=path['method']))
		return web_list

class APP(commands.Cog):
	routes = Route()

	def __init__(self, bot:commands.Bot) -> None:
		self.bot = bot
		self.iddb = sqlite3.connect(os.path.join(config.dir, 'database', 'idata.db'), isolation_level=None)
		self.webappdb = sqlite3.connect(os.path.join(config.dir, 'database', 'webapp.db'))
		self.youtubedb = sqlite3.connect(os.path.join(config.dir, 'database', 'youtube.db'))
		self.chatgptdb = sqlite3.connect(os.path.join(config.dir, 'database', 'chatgpt.db'), isolation_level=None)
		self.youtubedb.execute('PRAGMA auto_vacuum = FULL')
		self.youtubedb.execute('VACUUM')
		handler = logging.handlers.RotatingFileHandler(filename=os.path.join(config.dir, 'data','logs', 'webapplog.txt'),maxBytes=1048576,backupCount=2,encoding="UTF-8")
		handler.setFormatter(logging.Formatter('%(asctime)s %(levelname)s: %(message)s', datefmt='%Y-%m-%d %H:%M:%S'))
		self.logger = logging.getLogger("webapp")
		self.logger.addHandler(handler)
		self.logger.setLevel(logging.INFO)
		self.task = None

	@routes.route(path="/webhook/youtube/{pathid}", method="POST")
	async def ytpost(self, request:web.Request):
		if request.headers.get("X-Hub-Signature") and request.headers['User-Agent'] == "FeedFetcher-Google; (+http://www.google.com/feedfetcher.html)":
			try:
				with sqlite3.connect(os.path.join(config.dir, 'database', 'youtube.db')) as db:
					secret = db.execute('SELECT secret FROM subscribe WHERE id = ?', (request.match_info['pathid'],)).fetchone()
					if secret is None:
						return web.Response(status=404,text="")
				msg = await request.text()
				bkey = bytes(secret[0],"utf-8")
				bdata = bytes(msg,"utf-8")
				hash = f"sha1={hmac.new(bkey, bdata, hashlib.sha1).hexdigest()}"
				signature = request.headers['X-Hub-Signature']
				if hmac.compare_digest(signature, hash):
					soup = BeautifulSoup(msg, 'xml')
					if soup.find('yt:videoId') is not None:
						id = soup.find('yt:videoId').text
						channel_id = soup.find('yt:channelId').text
						asyncio.create_task(yt.youtube(channel_id=channel_id, video_id=id, youtubedb=self.youtubedb))
					elif soup.find('at:deleted-entry') is not None:
						id = soup.find('at:deleted-entry')['ref'].replace("yt:video:", "")
						asyncio.create_task(yt.youtube_delete(id, self.youtubedb))
			except Exception as e:
				logging.warning(f"Process message failed! Reason: {e.__class__.__name__}: {e}\nHeaders: {dict(request.headers)}\ndata: {msg}")
			finally:
				headers = {'Cache-Control': 'no-cache'}
				return web.Response(status=204,text="", headers=headers)
		else:
			return web.Response(status=404,text="")

	@routes.route(path="/webhook/youtube/{pathid}", method="GET")
	async def ytfetch(self, request:web.Request):
		if request.query.get('hub.challenge') and request.query.get("hub.verify_token"):
			parsed_url = urlparse(request.query['hub.topic'])
			if parsed_url.netloc != "www.youtube.com" or "/xml/feeds/videos.xml" not in parsed_url.path:
				return web.Response(status=404, text="")
			query_params = parse_qs(parsed_url.query)
			channel_id = query_params['channel_id'][0]
			if request.match_info['pathid'] != channel_id:
				return web.Response(status=400, text="")
			with sqlite3.connect(os.path.join(config.dir, 'database', 'youtube.db')) as db:
				secret = db.execute('SELECT secret FROM subscribe WHERE id = ?', (channel_id,)).fetchone()
				if secret is None:
					return web.Response(status=404,text="")
				verify_token = hmac.new(bytes(config.secret,"utf-8"), bytes(secret[0],"utf-8"), hashlib.sha256).hexdigest()
				if request.query.get("hub.verify_token") != verify_token:
					return web.Response(status=401, text="")
			challenge = request.query['hub.challenge']
			headers = {'Cache-Control': 'no-cache'}
			return web.Response(status=200,text=challenge, headers=headers)
		else:
			return web.Response(status=404)

	@routes.route(path="/test/youtube", method="POST")
	async def yttest(self, request:web.Request):
		if request.headers.get("Authorization") == config.secret:
			video = request.query.get('video_id')
			if not video:
				return web.Response(status=400,text="No video id")
			asyncio.create_task(yt.youtube(video_id=video, test=True, youtubedb=self.youtubedb))
			headers = {'Cache-Control': 'no-cache'}
			return web.Response(status=202,text="", headers=headers)
		else:
			return web.Response(status=404,text="")

	@routes.route(path="/webhook/twitch/{pathid}", method="POST")
	async def twitchpost(self, request:web.Request):
		if request.headers.get('Twitch-Eventsub-Message-Type') and request.headers.get('Twitch-Eventsub-Message-Signature') and request.headers.get('Twitch-Eventsub-Message-Id') and request.headers.get('Twitch-Eventsub-Message-Timestamp') and request.headers.get('Twitch-Eventsub-Subscription-Type'):
			text = await request.text()
			data = f"{request.headers['Twitch-Eventsub-Message-Id']}{request.headers['Twitch-Eventsub-Message-Timestamp']}{text}"
			bkey = bytes(config.secret,"utf-8")
			bdata = bytes(data,"utf-8")
			hash = f"sha256={hmac.new(bkey, bdata, hashlib.sha256).hexdigest()}"
			signature = request.headers['Twitch-Eventsub-Message-Signature']
			if hmac.compare_digest(signature, hash):
				jsondata = json.loads(text)
				userid = jsondata['subscription']['condition']['broadcaster_user_id']
				headers = {'Cache-Control': 'no-cache'}
				if request.match_info['pathid'] != userid:
					return web.Response(status=400, text="")
				if request.headers['Twitch-Eventsub-Message-Type'] == "webhook_callback_verification":
					if request.headers['Twitch-Eventsub-Subscription-Type'] not in ["stream.online", 'stream.offline']:
						return web.Response(status=404,text="")
					challenge = jsondata['challenge']
					return web.Response(status=200,text=challenge, headers=headers)
				elif request.headers['Twitch-Eventsub-Message-Type'] == "notification":
					if request.headers['Twitch-Eventsub-Subscription-Type'] == "stream.online":
						asyncio.create_task(twitch.notification(jsondata, request.headers['Twitch-Eventsub-Message-Id']))
						return web.Response(status=204,text="", headers=headers)
					elif request.headers['Twitch-Eventsub-Subscription-Type'] == "stream.offline":
						asyncio.create_task(twitch.offline(jsondata, request.headers['Twitch-Eventsub-Message-Id']))
						return web.Response(status=204,text="", headers=headers)
					else:
						return web.Response(status=404,text="")
				elif request.headers['Twitch-Eventsub-Message-Type'] == "revocation":
					asyncio.create_task(twitch.revocation(jsondata))
					return web.Response(status=204,text="", headers=headers)
				else:
					return web.Response(status=202,text="", headers=headers)
			else:
				return web.Response(status=401, text="")
		else:
			return web.Response(status=404, text="")

	@routes.route(path="/robots.txt", method="GET")
	async def robots(self, request:web.Request):
		text = 'User-agent: *\nDisallow: /'
		return web.Response(status=200,text=text)

	@routes.route(path="/status", method="GET")
	async def status(self, request:web.Request):
		if request.headers.get('Authorization') == config.secret:
			now = datetime.datetime.now().timestamp()
			signature = hmac.new(bytes(config.token,"utf-8"), bytes(str(now),"utf-8"), hashlib.sha256).hexdigest()
			header = {
				"X-Time": str(now),
				"X-Signature": signature
			}
			return web.Response(status=204,text="",headers=header)
		else:
			return web.Response(status=404, text="")

	@web.middleware
	async def rdns(self, request:web.Request, handler):
		if not request.headers.get('X-Real-IP') or not request.headers.get('User-Agent'):
			return web.Response(status=400,text="")
		if request.headers.get('Host') == config.onion_domain:
			if request.path != "/":
				return web.Response(status=404,text="")
		try:
			response = await handler(request)
			return response
		except web_exceptions.HTTPNotFound:
			return web.Response(status=404,text="")
		except:
			ex = {"Code": 500, "Message": "Internal_Server_Error"}
			logging.exception(f"An error occurred while handling request!")
			return web.Response(status=500,text=json.dumps(ex), content_type="application/json")

	#---------------------------------------------------------------------

	@routes.route(path="/api/{path:.*}", method="GET")
	@routes.route(path="/api/{path:.*}", method="POST")
	async def api(self, request:web.Request):
			if re.search(r'^codis$', request.match_info['path']):
				return await self.codis(request)
			if await self.site_verify(request=request):
				if re.search(r'^log/', request.match_info['path']):
					return await self.log(request)
				elif re.search(r'^chat/', request.match_info['path']):
						return await self.chat_post(request)
				elif re.search(r'^chatlog/', request.match_info['path']):
					return await self.chat_log(request)
				elif re.search(r'^subscription-list', request.match_info['path']):
					subscription_list = {}
					with sqlite3.connect(os.path.join(config.dir, 'database', 'youtube.db')) as db:
						yt_db_list = db.execute('SELECT id, name FROM subscribe').fetchall()
					yt_list = []
					for cursor in yt_db_list:
						yt_list.append({"id":cursor[0], "name":cursor[1]})
					subscription_list["Youtube"] = yt_list
					return web.Response(status=200,body=json.dumps(subscription_list, ensure_ascii=False), content_type="application/json", charset="UTF-8")
				elif re.search(r'^subscription/', request.match_info['path']):
					platform = request.match_info['path'].split("/")[-2]
					if platform == "youtube":
						return await self.ytsubscription(request)
					else:
						return web.Response(status=404, text="")
				else:
					return web.Response(status=404, text="")
			else:
				data = {
					"Code": 401,
					"Message": "Unauthorized"
				}
				header = {
					"Www-Authenticate": 'Basic realm="Restricted Area"'
				}
				return web.Response(status=401,text=json.dumps(data), content_type="application/json", headers=header)

	@routes.route(path="/entry", method="GET")
	async def main(self, request:web.Request):
		state_uuid = request.query.get("code1")
		state_hmac = request.query.get("code2")
		state_time = request.query.get("time")
		if not state_uuid or not state_hmac or not state_time:
			return web.Response(status=400, text="")
		state_hmac_new = hmac.new(bytes(config.bot_public_key, "UTF-8"), bytes(f"{state_uuid}-{state_time}", "UTF-8"), hashlib.sha256).hexdigest()
		if not hmac.compare_digest(state_hmac, state_hmac_new):
			return web.Response(status=400, text="")
		state = hmac.new(bytes(state_uuid, "UTF-8"), bytes(state_hmac, "UTF-8"), hashlib.sha256).hexdigest()
		self.webappdb.execute('INSERT INTO state VALUES (?, ?, ?, ?)', (state, request.headers.get('X-Real-IP'), request.headers.get('User-Agent'), int(datetime.datetime.now().timestamp() + 1800)))
		self.webappdb.commit()
		headers = {"Location": f"https://discord.com/oauth2/authorize?response_type=code&client_id={config.applications_id}&scope=identify&state={state}"}
		return web.Response(status=302, headers=headers)

	@routes.route(path="/home/{path:.*}", method="GET")
	async def home(self, request:web.Request):
		if not await self.site_verify(request):
			headers = {"Location": "/", "Set-Cookie": 'Authorization=; path=/; Secure; HttpOnly; SameSite=Lax; Max-Age=0;'}
			return web.Response(status=302, headers=headers)
		if request.match_info['path'] == "main":
			with open(os.path.join(config.dir, 'html',"home.html"), "r") as d:
				c = d.read()
			return web.Response(status=200,body=c, content_type="text/html")
		elif request.match_info['path'] == "syslog":
			with open(os.path.join(config.dir, 'html',"syslog.html"), "r") as d:
				c = d.read()
			return web.Response(status=200,body=c, content_type="text/html")
		elif request.match_info['path'] == "backuplog":
			with open(os.path.join(config.dir, 'html',"syslog_backup.html"), "r") as d:
				c = d.read()
			return web.Response(status=200,body=c, content_type="text/html")
		elif request.match_info['path'] == "applog":
			with open(os.path.join(config.dir, 'html',"applog.html"), "r") as d:
				c = d.read()
			return web.Response(status=200,body=c, content_type="text/html")
		elif request.match_info['path'] == "backupapplog":
			with open(os.path.join(config.dir, 'html',"applog_backup.html"), "r") as d:
				c = d.read()
			return web.Response(status=200,body=c, content_type="text/html")
		elif request.match_info['path'] == "misskey":
			with open(os.path.join(config.dir, 'html',"misskey_statistics.html"), "r") as d:
				c = d.read()
			return web.Response(status=200,body=c, content_type="text/html")
		elif request.match_info['path'] == "nginx":
			with open(os.path.join(config.dir, 'html',"nginx.html"), "r") as d:
				c = d.read()
			return web.Response(status=200,body=c, content_type="text/html")
		else:
			return web.Response(status=404,text="")

	async def log(self, request:web.Request):
		if request.match_info['path'].split("/")[-1] == "syslog":
			with open(os.path.join(config.dir, 'data', 'logs',"syslog.txt"), "r") as d:
				c = d.read()
			return web.Response(status=200,text=c)
		elif request.match_info['path'].split("/")[-1] == "backuplog":
			if os.path.isfile(os.path.join(config.dir, 'data', 'logs',"syslog.txt.1")):
				with open(os.path.join(config.dir, 'data', 'logs',"syslog.txt.1"), "r") as d:
					c = d.read()
				return web.Response(status=200,text=c)
			else:
				return web.Response(status=404,text="")
		elif request.match_info['path'].split("/")[-1] == "applog":
			with open(os.path.join(config.dir, 'data', 'logs',"webapplog.txt"), "r") as d:
				c = d.read()
			return web.Response(status=200,text=c)
		elif request.match_info['path'].split("/")[-1] == "backupapplog":
			if os.path.isfile(os.path.join(config.dir, 'data', 'logs',"applog.txt.1")):
				with open(os.path.join(config.dir, 'data', 'logs',"webapplog.txt.1"), "r") as d:
					c = d.read()
				return web.Response(status=200,text=c)
			else:
				return web.Response(status=404,text="")
		elif request.match_info['path'].split("/")[-1] == "misskey":
			with open(os.path.join(config.dir, 'data', 'misskey',"statistic.json"), "r") as d:
				c = d.read()
			return web.Response(status=200,body=c, content_type="application/json", charset="UTF-8")
		elif request.match_info['path'].split("/")[-1] == "chatlist":
			cursor = self.chatgptdb.execute('SELECT * FROM list').fetchall()
			c = {}
			for data in cursor:
				e = {
					"name": data[1],
					"uuid": data[2]
				}
				c[str(data[0])] = e
			return web.Response(status=200,body=json.dumps(c, ensure_ascii=False), content_type="application/json", charset="UTF-8")
		elif request.match_info['path'].split("/")[-1] == "nginx":
			with open("/var/log/nginx/access.log", "r") as d:
				c = d.read()
				matches = re.findall(r'\[(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\+\d{2}:\d{2})\]', c)
				if matches:
					for match in matches:
						log_time = datetime.datetime.strptime(match, "%Y-%m-%dT%H:%M:%S%z").strftime("%Y-%m-%d %H:%M:%S")
						c = c.replace(match, log_time)
			return web.Response(status=200,text=c)
		else:
			return web.Response(status=404,text="")

	def jwt_create(self, scope:str, exp_offset:int=0, key:str=None, jti:str=None):
		now = int(datetime.datetime.now().timestamp())
		payload = {
			"scope": scope,
			"iat": now,
			"exp": now + 3153600,
			"jti": jti or str(uuid.uuid4()).replace("-", "")
		}
		if exp_offset:
			payload['exp'] = int(datetime.datetime.now().timestamp()) + exp_offset
		if key is None:
			key = config.bot_public_key
		return pyjwt.encode(payload=payload, key=key, algorithm='HS256')

	async def jwt_verify(self, scope:str, jwt:str, key:str=None):
		if key is None:
			key = config.bot_public_key
		try:
			decoded_token = pyjwt.decode(jwt, key, algorithms=['HS256'])
		except:
			return False
		if decoded_token.get("scope") == scope:
			if scope == 'app.access.token':
				session_id = decoded_token.get('jti')
				if session_id is None:
					return False
				session_time = self.webappdb.execute('SELECT expires FROM session WHERE id = ?', (session_id,)).fetchone()
				if session_time is None:
					return False
				elif session_time[0] < datetime.datetime.now().timestamp() - 300:
					refresh = await self.discord_refresh(session_id)
					return refresh
			return True
		else:
			return False

	async def site_verify(self, request:web.Request):
		if request.headers.get('Cookie'):
			Cookies = request.headers['Cookie']
			if not "Authorization=" in Cookies:
				return False
			for Cookie in Cookies.split("; "):
				if "Authorization=" in Cookie:
					verify = await self.jwt_verify('app.access.token', Cookie.replace("Authorization=", ""))
					if not verify:
						return False
		else:
			return False
		return True

	@routes.route(path="/auth", method="GET")
	async def discord_auth(self, request:web.Request):
		code = request.query.get('code')
		state = request.query.get('state')
		if request.query.get('error') == 'access_denied':
			return web.Response(status=403, text='Sorry, You must login from discord to enter this site')
		if not code or not state:
			return web.Response(status=400, text='')
		state_data = self.webappdb.execute('SELECT * FROM state WHERE id = ?', (state,)).fetchone()
		if state_data is None:
			return web.Response(status=400, text='')
		state, userip, useragant, expire = state_data
		self.webappdb.execute('DELETE FROM state WHERE id = ?', (state,))
		self.webappdb.commit()
		if userip != request.headers.get('X-Real-IP') or useragant != request.headers.get('User-Agent') or datetime.datetime.now().timestamp() > expire:
			return web.Response(status=400, text='')
		data = {
			'grant_type': 'authorization_code',
			'code': code,
			'redirect_uri': f'https://{config.domain}/auth'
		}
		headers = {'Content-Type': 'application/x-www-form-urlencoded'}
		try:
			async with aiohttp.ClientSession() as s:
				async with s.post("https://discord.com/api/v10/oauth2/token", headers=headers, data=data, auth=aiohttp.BasicAuth(str(config.applications_id), config.bot_auth_secret)) as r:
					d = await r.json()
					if r.status == 400 and d.get("error") == "invalid_grant":
						return web.Response(status=400, text='')
					r.raise_for_status()
					access_token = d['access_token']
					refresh_token = d['refresh_token']
					expires = int(datetime.datetime.now().timestamp()) + d['expires_in']
				headers = {'Authorization': f'Bearer {access_token}'}
				async with s.get("https://discord.com/api/v10/users/@me", headers=headers) as r:
					r.raise_for_status()
					d = await r.json()
					userid = d['id']
					if userid != str(config.ownerid):
						logging.warning(f"A unknown user want login from Discord\n{d}")
						return web.Response(status=403, text="Sorry, You don't have permission to enter this site")
					session_id = str(uuid.uuid4()).replace("-", "")
					jwt = self.jwt_create(scope='app.access.token', jti=session_id)
					self.webappdb.execute('INSERT INTO session VALUES (?, ?, ?, ?)', (session_id, access_token, refresh_token, expires))
					self.webappdb.commit()
					headers = {"Set-Cookie": f'Authorization={jwt}; path=/; Secure; HttpOnly; SameSite=Lax; Max-Age=31536000', "Location": "/home/main"}
					return web.Response(status=302, headers=headers)
		except Exception as e:
			logging.error(f"Get discord auth token failed!! | Reason: {e}")
			return web.Response(status=500, text=f'')

	async def discord_refresh(self, session_id:str):
		refresh_token = self.webappdb.execute('SELECT refresh_token FROM session WHERE id = ?', (session_id,)).fetchone()[0]
		data = {
			'grant_type': 'refresh_token',
			'refresh_token': refresh_token
		}
		headers = {'Content-Type': 'application/x-www-form-urlencoded'}
		try:
			async with aiohttp.ClientSession() as s:
				async with s.post("https://discord.com/api/v10/oauth2/token", headers=headers, data=data, auth=aiohttp.BasicAuth(str(config.applications_id), config.bot_auth_secret)) as r:
					if r.status == 400:
						d = await r.json()
						if d.get('error') == 'invalid_grant':
							self.webappdb.execute('DELETE FROM session WHERE refresh_token = ?', (refresh_token,))
							self.webappdb.commit()
							return False
					r.raise_for_status()
					d = await r.json()
					new_access_token = d['access_token']
					new_refresh_token = d['refresh_token']
					expires = int(datetime.datetime.now().timestamp()) + d['expires_in']
				headers = {'Authorization': f'Bearer {new_access_token}'}
				async with s.get("https://discord.com/api/v10/users/@me", headers=headers) as r:
					r.raise_for_status()
					d = await r.json()
					userid = d['id']
					if userid != str(config.ownerid):
						self.webappdb.execute('DELETE FROM session WHERE refresh_token = ?', (refresh_token,))
						self.webappdb.commit()
						return False
					self.webappdb.execute('UPDATE session SET access_token = ?, refresh_token = ?, expires = ? WHERE refresh_token = ?', (new_access_token, new_refresh_token, expires, refresh_token))
					self.webappdb.commit()
					return True
		except Exception as e:
			logging.error(f"Refesh discord auth token failed!! | Reason: {e}")
			raise

	@routes.route(path="/logout", method="GET")
	async def logout(self, request:web.Request):
		if request.headers.get('Cookie'):
			Cookies = request.headers['Cookie']
			if not "Authorization=" in Cookies:
				headers = {"Location": "/"}
				return web.Response(status=302, headers=headers)
			for Cookie in Cookies.split("; "):
				if "Authorization=" in Cookie:
					try:
						decoded_token = pyjwt.decode(jwt=Cookie.replace("Authorization=", ""), key=config.bot_public_key, algorithms=['HS256'])
					except:
						headers = {"Set-Cookie": 'Authorization=; Max-Age=0;', "Location": "/"}
						return web.Response(status=302, headers=headers)
		else:
			headers = {"Location": "/"}
			return web.Response(status=302, headers=headers)
		session_id = decoded_token['jti']
		access_token = self.webappdb.execute('SELECT access_token FROM session WHERE id = ?', (session_id,)).fetchone()
		if access_token is None:
			headers = {"Set-Cookie": f'Authorization=; Max-Age=0;', "Location": "/"}
			return web.Response(status=302, headers=headers)
		data = {
			'token': access_token[0],
			'token_type_hint': 'access_token'
		}
		headers = {'Content-Type': 'application/x-www-form-urlencoded'}
		try:
			async with aiohttp.ClientSession() as s:
				async with s.post("https://discord.com/api/v10/oauth2/token/revoke", headers=headers, data=data, auth=aiohttp.BasicAuth(str(config.applications_id), config.bot_auth_secret)) as r:
					r.raise_for_status()
					self.webappdb.execute('DELETE FROM session WHERE access_token = ?', access_token)
					self.webappdb.commit()
					headers = {"Set-Cookie": f'Authorization=; Max-Age=0;', "Location": "/"}
					return web.Response(status=302, headers=headers)
		except Exception as e:
			logging.error(f"Revoke discord auth token failed!! | Reason: {e}")
			return web.Response(status=500, text='')

	@routes.route(path="/chat/{chatid:.*}", method="GET")
	async def chat_site(self, request:web.Request):
		if not await self.site_verify(request):
			return web.Response(status=401, text="")
		chatid = request.match_info['chatid']
		cursor = self.chatgptdb.execute('SELECT name, uuid FROM list WHERE id = ?', (chatid,)).fetchone()
		if cursor is None or not os.path.isdir(os.path.join(config.dir, 'data', 'chatgpt', chatid)):
			return web.Response(status=404, text="")
		with open(os.path.join(config.dir, 'html',"chat.html"), "r") as d:
			c = d.read()
		return web.Response(status=200,body=c, content_type="text/html")

	async def chat_log(self, request:web.Request):
		chatid = request.match_info['path'].split("/")[-1]
		cursor = self.chatgptdb.execute('SELECT name, uuid FROM list WHERE id = ?', (chatid,)).fetchone()
		if cursor is None or not os.path.isdir(os.path.join(config.dir, 'data', 'chatgpt', chatid)):
			return web.Response(status=404, text="")
		with open(os.path.join(config.dir, 'data', 'chatgpt', str(chatid), "chat.json"), 'r') as d:
			c = d.read()
		return web.Response(status=200,body=c, content_type="application/json")

	async def chat_post(self, request:web.Request):
		chatid = request.match_info['path'].split("/")[-1]
		if chatid == "new":
			body = await request.json()
			chat_uuid = str(uuid.uuid4()).replace("-", "")
			count = self.chatgptdb.execute('SELECT value FROM memo WHERE key = ?', ("count",)).fetchone()[0]
			count += 1
			self.chatgptdb.execute('UPDATE memo SET value = ? WHERE key = ?', (count, "count"))
			self.chatgptdb.execute('INSERT INTO list VALUES (?, ?, ?, ?)', (count, body["name"], chat_uuid, body["lastid"]))
			self.chatgptdb.commit()
			os.mkdir(os.path.join(config.dir, 'data', 'chatgpt', str(count)))
			with open(os.path.join(config.dir, 'data', 'chatgpt', str(count), "all.json"), 'w') as f:
				json.dump([], f, ensure_ascii=False, indent=2)
			with open(os.path.join(config.dir, 'data', 'chatgpt', str(count), "chat.json"), 'w') as f:
				json.dump(body["message"], f, ensure_ascii=False, indent=2)
			return web.Response(status=204,text="")
		cursor = self.chatgptdb.execute('SELECT name, uuid FROM list WHERE id = ?', (chatid,)).fetchone()
		if cursor is None or not os.path.isdir(os.path.join(config.dir, 'data', 'chatgpt', chatid)):
			return web.Response(status=404, text="")
		with open(os.path.join(config.dir, 'data', 'chatgpt', str(chatid), "chat.json"), 'w') as d:
			body = await request.json()
			json.dump(body, d, indent=2, ensure_ascii=False)
		return web.Response(status=204,text="")

	@routes.route(path="/chatlist", method="GET")
	async def chat_list(self, request:web.Request):
		if not await self.site_verify(request):
			return web.Response(status=401, text="")
		with open(os.path.join(config.dir, 'html',"chatlist.html"), "r") as d:
			c = d.read()
		return web.Response(status=200,body=c, content_type="text/html")

	@routes.route(path="/subscription/{path:.*}", method="GET")
	async def subscription(self, request:web.Request):
		if not await self.site_verify(request):
			return web.Response(status=401, text="")
		with open(os.path.join(config.dir, 'html',"subscription.html"), "r") as d:
			c = d.read()
		return web.Response(status=200,body=c, content_type="text/html")

	@routes.route(path="/subscription", method="GET")
	async def subscription_list(self, request:web.Request):
		if not await self.site_verify(request):
			return web.Response(status=401, text="")
		with open(os.path.join(config.dir, 'html',"subscription_list.html"), "r") as d:
			c = d.read()
		return web.Response(status=200,body=c, content_type="text/html")

	async def ytsubscription(self, request:web.Request):
		if not await self.site_verify(request):
			return web.Response(status=401, text="")
		channel_id = request.match_info['path'].split("/")[-1]
		with sqlite3.connect(os.path.join(config.dir, 'database', 'youtube.db')) as db:
			secret = db.execute('SELECT secret FROM subscribe WHERE id = ?', (channel_id,)).fetchone()
			if secret is None:
				return web.Response(status=404,text="")
			channel_name = db.execute('SELECT name FROM subscribe WHERE id = ?', (channel_id,)).fetchone()[0]
		async with aiohttp.ClientSession() as s:
			async with s.get(f"https://pubsubhubbub.appspot.com/subscription-details?hub.callback=https%3A%2F%2F{config.domain}%2Fwebhook%2Fyoutube%2F{channel_id}&hub.topic=https%3A%2F%2Fwww.youtube.com%2Fxml%2Ffeeds%2Fvideos.xml%3Fchannel_id%3D{channel_id}&hub.secret={secret[0]}") as r:
				r.raise_for_status()
				html_content = await r.read()
				s = BeautifulSoup(html_content, 'html.parser')
				soup = s.find('body')
				subscription_details = {}
				subscription_details["Name"] = channel_name
				subscription_details["Topic URL"] = soup.find('p', class_='lead').string.strip()
				subscription_dl = soup.find('dl', class_='glue-body glue-body--large')
				if subscription_dl:
					dt_elements = subscription_dl.find_all('dt')
					dd_elements = subscription_dl.find_all('dd')
					for dt, dd in zip(dt_elements, dd_elements):
						subscription_details[dt.string.strip()] = dd.string.strip()

				last_item_dl = soup.find_all('dl', class_='glue-body glue-body--large')[-1]
				subscription_details["Last content received"] = last_item_dl.find('dt', string="Content received").find_next_sibling('dd').string.strip()
				subscription_details["Last content delivered"] = last_item_dl.find('dt', string="Content delivered").find_next_sibling('dd').string.strip()

				for title, subscription_time in subscription_details.items():
					match = re.search(r"([a-zA-Z]+, \d+ [a-zA-Z]+ \d+ \d+:\d+:\d+ \+\d+)", subscription_time)
					if match:
						datetime_format = "%a, %d %b %Y %H:%M:%S %z"
						format_dt = datetime.datetime.strptime(match.group(1), datetime_format)
						subscription_details[title] = subscription_time.replace(match.group(1), format_dt.strftime("%Y-%m-%d %H:%M:%S"))
				return web.Response(status=200,body=json.dumps(subscription_details, ensure_ascii=False), content_type="application/json")
			
	async def codis(self, request:web.Request):
		authorization = request.headers.get("authorization")
		if authorization != config.secret:
			return web.Response(status=401, text="")
		date_query = request.query.get("date")
		if date_query is None:
			date_query_int = 10
		else:
			try:
				date_query_int = int(date_query)
				if date_query_int < 1 or date_query_int > 30:
					date_query_int = 10
			except:
				date_query_int = 10 
		date_now = datetime.datetime.now()
		date_now_str = date_now.strftime("%Y-%m-%d")
		date_last = datetime.datetime.fromtimestamp((date_now.timestamp() - (86400 * date_query_int))).strftime("%Y-%m-%d")
		data_dict = {
			"date": f"{date_last}T00:00:00.000+08:00",
			"type": "report_month",
			"stn_ID":"466920",
			"stn_type": "cwb",
			"start": f"{date_last}T00:00:00",
			"end": f"{date_now_str}T00:00:00"
		}
		header = {"Content-Type": "application/x-www-form-urlencoded"}
		async with aiohttp.ClientSession() as s:
			async with s.post(url="https://codis.cwa.gov.tw/api/station?", data=data_dict, headers=header) as r:
				dt_json = json.loads(await r.text())
				dt_dump = {}
				for dt in dt_json["data"][0]["dts"]:
					dt_date = datetime.datetime.strptime(dt["DataDate"], "%Y-%m-%dT%H:%M:%S").strftime("%Y-%m-%d")
					dt_dump[dt_date] = {
						"T": dt["AirTemperature"]["Mean"],
						"H": dt["AirTemperature"]["Maximum"],
						"L": dt["AirTemperature"]["Minimum"]
					}
		return web.Response(status=200, body=json.dumps(dt_dump, sort_keys=True), headers={"Content-Type": "application/json"})

	async def run(self):
		app = web.Application(middlewares=[self.rdns])
		app.router.add_routes(self.routes.get_list(class_obj=self))
		self.runner = web.AppRunner(app,access_log_format='%{X-Real-IP}i "%{X-Method}i" %s %{Content-Length}i "%{User-Agent}i" (%D)',access_log = self.logger)
		await self.runner.setup()
		site = web.TCPSite(self.runner, host='localhost',port=3000)
		await site.start()
		while True:
			now = int(datetime.datetime.now().timestamp())
			cursor = self.webappdb.execute('SELECT ip FROM block WHERE expires < ?', (now,)).fetchall()
			if len(cursor) > 0:
				for ip in cursor:
					self.webappdb.execute('DELETE FROM block WHERE ip = ?', ip)
				self.webappdb.commit()
			state = self.webappdb.execute('SELECT id FROM state WHERE expires < ?', (now,)).fetchall()
			if len(state) > 0:
				for id in state:
					self.webappdb.execute('DELETE FROM state WHERE id = ?', id)
				self.webappdb.commit()
			repost = self.webappdb.execute('SELECT * FROM repost').fetchall()
			if len(repost) > 0:
				for post in repost:
					post_id, post_type, post_data, repost_time = post
					self.webappdb.execute('DELETE FROM repost WHERE id = ?', (post_id,))
					self.webappdb.commit()
					if repost_time >= 5:
						continue
					logging.info(f"Try repost {post_type} {post_id}")
					if post_type == "youtube":
						asyncio.create_task(yt.youtube(video_id=post_id, youtubedb=self.youtubedb, repost_time=repost_time))
					elif post_type == "twitch":
						postdata = json.loads(post_data)
						if postdata['subscription']['type'] == "stream.online":
							asyncio.create_task(twitch.notification(postdata, post_id, repost_time))
						elif postdata['subscription']['type'] == "stream.offline":
							asyncio.create_task(twitch.offline(postdata, post_id, repost_time))
			await asyncio.sleep(600)

async def setup(bot):
	global webapp
	webapp = APP(bot)
	await bot.add_cog(webapp)
	webapp.task = asyncio.create_task(webapp.run())

async def teardown(bot):
	global webapp
	if webapp is not None:
		await webapp.runner.shutdown()
		await webapp.runner.cleanup()
		webapp.task.cancel()
		webapp.youtubedb.close()
		webapp.iddb.close()
		webapp.webappdb.close()
		for handler in webapp.logger.handlers:
			handler.close()
			webapp.logger.removeHandler(handler)
		webapp = None