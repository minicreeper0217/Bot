import aiohttp
import json
import asyncio
import config
import os
import datetime
import pytz
import sends
import logging
import random

tz = pytz.timezone('Asia/Taipei')

# post  https://sg-hk4e-api.hoyolab.com/event/sol/sign?lang=zh-tw
# get   https://sg-hk4e-api.hoyolab.com/event/sol/info?act_id=e202102251931481&lang=zh-tw
# get   https://sg-hk4e-api.hoyolab.com/event/sol/resign_info?act_id=e202102251931481&lang=zh-tw

# {"retcode":0,"message":"OK","data":{"code":"ok","first_bind":false,"gt_result":{"risk_code":0,"gt":"","challenge":"","success":0,"is_risk":false}}}
# {"data":null,"message":"Traveler, you've already checked in today~","retcode":-5003}
# {"data":null,"message":"Something went wrong...please retry later","retcode":-502}
# {"retcode":0,"message":"OK","data":{"total_sign_day":1,"today":"2023-05-10","is_sign":true,"first_bind":false,"is_sub":false,"region":"","month_last_day":false}}
# {"retcode":0,"message":"OK","data":{"resign_cnt_daily":0,"resign_cnt_monthly":0,"resign_limit_daily":1,"resign_limit_monthly":3,"sign_cnt_missed":9,"quality_cnt":0,"signed":true,"sign_cnt":1,"cost":1,"month_quality_cnt":0}}

async def Genshinsignin() -> bool :
	with open(os.path.join(config.dir, 'data', 'hoyolab.json'), 'r') as f:
		cookies = json.load(f)
	now = datetime.datetime.now(tz).strftime("%Y-%m-%d")
	nowtime = datetime.datetime.now(tz).time()
	target = datetime.time(hour=6, minute=0, tzinfo=tz)
	if now == cookies['lastsign']['genshin'] or nowtime < target:
		return True
	data = {"act_id":"e202102251931481"}
	header = {
		"Cookie": cookies['cookies'],
		"Content-type" : "application/json",
		"User-Agent":"Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/114.0.0.0 Safari/537.36"
	}
	url = "https://sg-hk4e-api.hoyolab.com/event/sol/sign?lang=zh-tw"

	try:
		async with aiohttp.ClientSession() as session:
			async with session.post(url ,headers=header, data=json.dumps(data)) as response:
				t = await response.json()
				if t.get('data') and t['data']['gt_result']['is_risk']:
					raise Exception("Genshin now risking!!!")
				response.raise_for_status()
	except Exception as e:
		logging.warning(f"Auto genshin signin fall!! {e}")
		return False
	finally:
		cookies['lastsign']['genshin'] = now
		with open(os.path.join(config.dir, 'data', 'hoyolab.json'), 'w') as f:
			json.dump(cookies, f, indent=2)

	url = "https://sg-hk4e-api.hoyolab.com/event/sol/info?act_id=e202102251931481&lang=zh-tw"
	header = {
		"Cookie": cookies['cookies'],
		"User-Agent":"Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/114.0.0.0 Safari/537.36"
	}
	try:
		async with aiohttp.ClientSession() as session:
			async with session.get(url ,headers=header) as response:
				response.raise_for_status()
				r_text = await response.text()
				r = json.loads(r_text)
	except Exception as e:
		logging.warning(f"Check genshin sign info fall!! {e}")
		return False
	if r.get("data") and r['data'].get('is_sign') == True and int(r['data']['today'].replace("-", "")) <= int(now.replace("-", "")):
		return True
	else:
		logging.warning(f"Check genshin sign info fall!! {r}")
		return False

async def starrailsignin() -> bool :
	with open(os.path.join(config.dir, 'data', 'hoyolab.json'), 'r') as f:
		cookies = json.load(f)
	now = datetime.datetime.now(tz).strftime("%Y-%m-%d")
	nowtime = datetime.datetime.now(tz).time()
	target = datetime.time(hour=6, minute=0, tzinfo=tz)
	if now == cookies['lastsign']['star-rail'] or nowtime < target:
		return True
	data = {"act_id":"e202303301540311","lang":"zh-tw"}
	header = {
		"Cookie": cookies['cookies'],
		"Content-type" : "application/json",
		"User-Agent":"Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/114.0.0.0 Safari/537.36"
	}
	url = "https://sg-public-api.hoyolab.com/event/luna/os/sign"

	try:
		async with aiohttp.ClientSession() as session:
			async with session.post(url ,headers=header, data=json.dumps(data)) as response:
				t = await response.json()
				if t.get('data') and t['data']['is_risk']:
					raise Exception("Star-Rail now risking!!!")
				response.raise_for_status()
	except Exception as e:
		logging.warning(f"Auto star-rail signin fall!! {e}")
		return False
	finally:
		cookies['lastsign']['star-rail'] = now
		with open(os.path.join(config.dir, 'data', 'hoyolab.json'), 'w') as f:
			json.dump(cookies, f, indent=2)

	url = "https://sg-public-api.hoyolab.com/event/luna/os/info?lang=zh-tw&act_id=e202303301540311"
	header = {
		"Cookie": cookies['cookies'],
		"User-Agent":"Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/114.0.0.0 Safari/537.36"
	}
	try:
		async with aiohttp.ClientSession() as session:
			async with session.get(url ,headers=header) as response:
				response.raise_for_status()
				r_text = await response.text()
				r = json.loads(r_text)
	except Exception as e:
		logging.warning(f"Check star-rail sign info fall!! {e}")
		return False
	if r.get("data") and r['data'].get('is_sign') == True and int(r['data']['today'].replace("-", "")) <= int(now.replace("-", "")):
		return True
	else:
		logging.warning(f"Check star-rail sign info fall!! {r}")
		return False

async def hoyolabstart():
	while True:
		r = await Genshinsignin()
		if not r:
			message_send = {
				'content': "原神每日自動簽到失敗"
			}
			try:
				await sends.by_bot(1097782780001275964, message_send)
			except:
				pass
		s = await starrailsignin()
		if not s:
			message_send = {
				'content': "星穹鐵道每日自動簽到失敗"
			}
			try:
				await sends.by_bot(1097782780001275964, message_send)
			except:
				pass
		await asyncio.sleep(random.uniform(1800, 7200))