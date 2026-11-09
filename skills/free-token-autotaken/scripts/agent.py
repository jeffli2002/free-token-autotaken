#!/usr/bin/env python3
"""Local-only free quota dashboard and claim coordinator (macOS)."""
import argparse
import fcntl
import html
import json
import os
import plistlib
import secrets
import shutil
import subprocess
import sys
from datetime import datetime, date
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs
from zoneinfo import ZoneInfo

SCRIPT_DIR = Path(__file__).resolve().parent
ROOT = SCRIPT_DIR.parent
DATA = Path.home() / 'Library/Application Support/FreeTokenAgent'
STATE_FILE = DATA / 'state.json'
def catalog():
    market = json.loads((ROOT / 'market_catalog.json').read_text())
    extra = json.loads((ROOT / 'market_extra.json').read_text())
    seen = {x['id'] for x in market}
    return market + [x for x in extra if x['id'] not in seen]
TZ = ZoneInfo('Asia/Shanghai')
KNOWN = {
    'com.openai.codex': 'Codex', 'com.todesktop.230313mzl4w4u92': 'Cursor',
    'com.work.pc.doubao': '豆包工作', 'com.tencent.workbuddy.mac': 'WorkBuddy',
    'com.moonshot.kimichat': 'Kimi', 'com.deepseek.dsh': 'DeepSeek Harness'
}
CLAIM_BUNDLES = {'doubao-daily': 'com.work.pc.doubao', 'workbuddy-daily': 'com.tencent.workbuddy.mac'}

def now(): return datetime.now(TZ).isoformat(timespec='seconds')
def period(offer):
    today = datetime.now(TZ).date()
    return today.isoformat() if offer['cycle'] == 'daily' else (today.strftime('%Y-%m') if offer['cycle'] == 'monthly' else 'once')
def load_state():
    try: return json.loads(STATE_FILE.read_text())
    except FileNotFoundError: return {'claims': {}, 'dismissed': [], 'enabled': list(CLAIM_BUNDLES)}
def save_state(state):
    DATA.mkdir(parents=True, exist_ok=True)
    temp = STATE_FILE.with_suffix('.tmp')
    temp.write_text(json.dumps(state, ensure_ascii=False, indent=2))
    os.replace(temp, STATE_FILE)
def bundle_info(path):
    try:
        with (path / 'Contents/Info.plist').open('rb') as f: p = plistlib.load(f)
        return {'name': KNOWN.get(p.get('CFBundleIdentifier'), p.get('CFBundleDisplayName') or p.get('CFBundleName') or path.stem),
                'bundle_id': p.get('CFBundleIdentifier',''), 'version': p.get('CFBundleShortVersionString',''), 'path': str(path)}
    except (OSError, ValueError): return None
def scan():
    found = {}
    for base in [Path('/Applications'), Path.home() / 'Applications']:
        if not base.exists(): continue
        for path in list(base.glob('*.app')) + list(base.glob('*/*.app')):
            info = bundle_info(path)
            if info and info['bundle_id']: found[info['bundle_id']] = info
    kimi = Path.home() / '.kimi-code/bin/kimi'
    if kimi.exists(): found['cli.kimi-code'] = {'name':'Kimi Code CLI', 'bundle_id':'cli.kimi-code', 'version':'', 'path':str(kimi)}
    return found
def claim(offer_id, background=False):
    DATA.mkdir(parents=True, exist_ok=True)
    with (DATA / 'claim.lock').open('w') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        return _claim_locked(offer_id, background)

def _claim_locked(offer_id, background=False):
    offer = next((x for x in catalog() if x['id'] == offer_id), None)
    if not offer or offer_id not in CLAIM_BUNDLES: return {'status':'unsupported','message':'该活动尚无自动领取适配器'}
    state = load_state(); key = offer_id + ':' + period(offer)
    if state['claims'].get(key,{}).get('status') in ('claimed','already_claimed'):
        return {'status':'already_claimed','message':'本周期已有确认记录，未再次操作'}
    if (datetime.now(TZ).date() - date.fromisoformat(offer['verified_on'])).days > 14:
        return {'status':'needs_review','message':'活动规则超过 14 天未核实，已停止自动领取'}
    if not any(x in scan() for x in offer['bundle_ids']): return {'status':'not_installed','message':'本机未安装对应客户端'}
    binary = SCRIPT_DIR / 'claim'
    if not binary.exists(): return {'status':'not_ready','message':'本机辅助功能程序尚未编译；运行 install.command'}
    try:
        result = subprocess.run([str(binary), offer_id] + ([] if background else ['--manual']), capture_output=True, text=True, timeout=25)
        output = json.loads(result.stdout.strip().splitlines()[-1])
    except (subprocess.TimeoutExpired, ValueError, IndexError) as exc:
        output = {'status':'needs_review','message':'领取结果不确定：' + str(exc)}
    state['claims'][key] = {**output, 'time':now()}
    save_state(state)
    return output
def dashboard():
    market = catalog()
    meta = json.loads((ROOT / 'market_meta.json').read_text())
    installed = scan(); state = load_state(); entries=[]
    for offer in market:
        on_device = any(x in installed for x in offer['bundle_ids'])
        group = 'installed' if on_device else ('other' if offer.get('requires_install') else 'web')
        record = state['claims'].get(offer['id'] + ':' + period(offer), {})
        entries.append({**offer,'group':group,'record':record,'enabled':offer['id'] in state['enabled'],'dismissed':offer['id'] in state['dismissed']})
    return {'apps':list(installed.values()),'offers':entries,'time':now(),'market':meta}

def render(token):
    data = dashboard(); esc=html.escape
    def button(action, oid, label, css=''):
        return f'<form method="post" action="/{action}"><input type="hidden" name="csrf" value="{token}"><input type="hidden" name="id" value="{esc(oid)}"><button class="{css}">{label}</button></form>'
    def cards(group):
        rows=[]
        for o in data['offers']:
            if o['group'] != group or o['dismissed']: continue
            status=o['record'].get('status','')
            label={'claimed':'已领取','already_claimed':'已领取','deferred':'已延后','needs_review':'需核对','needs_accessibility':'需授权'}.get(status,('待核对' if o['claim_mode']=='guide' and group=='installed' else '待领取') if group=='installed' else ('无需安装' if group=='web' else '未安装'))
            action=(f'<span class="done">本周期已领取</span>' if status in ('claimed','already_claimed') else button('claim',o['id'],'立即领取','primary')) if o['claim_mode']=='desktop' and group=='installed' else f'<a class="button" href="{esc(o["download_url"] if group=="other" else o["official_url"])}" target="_blank" rel="noopener">查看官网</a>'
            if group!='installed': action += button('dismiss',o['id'],'暂不需要','quiet')
            rows.append(f'<article class="offer"><div><h3>{esc(o["vendor"])}</h3><p>{esc(o["name"])}</p></div><div class="amount">{esc(o["amount"])}</div><span class="pill">{label}</span><div class="actions">{action}</div><details><summary>条件与使用范围</summary><p>{esc(o["requirements"])}</p><p>适用范围：{esc(o["scope"])}</p><p>来源状态：{esc(o.get("verification_state", "AIHOT 收录；以官方页面为准"))} · 标注日期：{esc(o["verified_on"])}</p><a href="{esc(o["source_url"])}" target="_blank" rel="noopener">活动来源 ↗</a> · <a href="{esc(o["official_url"])}" target="_blank" rel="noopener">厂商页面 ↗</a>{'<p class="note">'+esc(o['record'].get('message',''))+'</p>' if status else ''}</details></article>')
        return ''.join(rows) or '<p class="empty">暂无已核实活动。请将厂商官方页面核实后的项目加入市场目录。</p>'
    confirmed=sum(1 for o in data['offers'] if o['record'].get('status') in ('claimed','already_claimed'))
    unknown=[a['name'] for a in data['apps'] if a['bundle_id'] in KNOWN or a['bundle_id']=='cli.kimi-code']
    body=f'''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>Free Token Autotaken</title><link rel="stylesheet" href="/style.css"><div class="window"><header><span class="dots">● ● ●</span><strong>Free Token Autotaken</strong><small>本机运行 · 数据仅存本地</small></header><div class="layout"><nav><a href="#installed">概览</a><a href="#other">更多活动</a><a href="#apps">本机软件</a></nav><main><h1 id="installed">已安装，Agent 帮我领</h1><p class="lede">先从模型和 Agent 厂商官方页面收集活动，再匹配本机软件。当前目录 {len(data['offers'])} 项；更新时间 {esc(data['market'].get('official_sources_updated_at', '尚未更新'))}。</p><div class="progress">本周期已确认领取 <b>{confirmed}</b> 项 <span>后台任务只在电脑空闲时尝试</span></div>{button('run','all','立即检查／领取已支持活动','primary')}{cards('installed')}<h2 id="other">更多活动</h2><h3 class="group-title">未安装客户端活动</h3>{cards('other')}<h3 class="group-title">无需安装</h3>{cards('web')}<h2 id="apps">本机识别</h2><p class="apps">{' · '.join(map(esc, unknown))}</p><p class="foot">活动来源为厂商官方页面。AIHOT 仅作为信息采集与核验方法的参考，不导入其活动数据。</p><p class="foot">本机扫描时间：{esc(data['time'])}</p></main></div></div></html>'''
    if load_state()['dismissed']:
        body = body.replace('<h2 id="apps">', button('restore','all','显示隐藏活动','quiet') + '<h2 id="apps">')
    return body.encode()

CSS=b'''*{box-sizing:border-box}html{scroll-behavior:smooth}body{margin:0;background:#f8f7f3;color:#172322;font-family:-apple-system,BlinkMacSystemFont,"PingFang SC",sans-serif}.window{max-width:1500px;margin:auto;background:#fcfbf8;min-height:100vh}header{height:76px;border-bottom:1px solid #e7e5e0;display:flex;align-items:center;gap:24px;padding:0 36px;font-size:22px}.dots{font-size:17px;letter-spacing:3px;color:#28b853}.dots:first-letter{color:#ed6056}header small{margin-left:auto;font-size:14px;color:#6c7773}.layout{display:grid;grid-template-columns:220px 1fr}nav{border-right:1px solid #e7e5e0;padding:40px 20px;display:flex;flex-direction:column;gap:10px}nav a{padding:13px 18px;text-decoration:none;color:#53605c;border-radius:13px}nav a:first-child,nav a:hover{background:#dfebe7;color:#096c60}main{padding:48px 56px;max-width:1250px}h1{font-family:Georgia,"Songti SC",serif;font-size:52px;margin:0 0 10px}h2{font-family:Georgia,"Songti SC",serif;font-size:36px;margin:50px 0 22px}.lede{font-size:18px;color:#6c7773;margin:0 0 34px}.progress{background:#e5f0ec;padding:20px 28px;border-radius:16px;margin-bottom:26px;font-size:18px}.progress b{font-size:26px;color:#087b68}.progress span{float:right;color:#648077;font-size:14px;padding-top:9px}.offer{display:grid;grid-template-columns:minmax(180px,1.2fr) minmax(150px,1fr) 100px 180px;align-items:center;gap:20px;border:1px solid #e3e2dd;border-radius:16px;padding:18px 24px;margin:12px 0;background:#fff}.offer h3{font-size:22px;margin:0 0 4px}.offer p{margin:0;color:#68736f}.amount{font-size:19px;font-weight:600}.pill{background:#e1f0e9;color:#0b7668;border-radius:30px;padding:8px 10px;text-align:center;font-size:14px}.actions{display:flex;gap:8px;justify-content:flex-end;flex-wrap:wrap}.actions form{margin:0}.done{color:#087b68;font-size:14px}button,.button{border:1px solid #087b68;background:white;color:#087b68;border-radius:10px;padding:10px 16px;font-size:14px;cursor:pointer;text-decoration:none;white-space:nowrap}button.primary{background:#087b68;color:white}.quiet{border:0;color:#7f8a86}.offer details{grid-column:1/-1;border-top:1px solid #eee;padding-top:12px;color:#60706b}.offer details p{margin:8px 0}.offer a{color:#087b68}.apps,.foot{color:#68736f}.empty{padding:24px;color:#7b8580}@media(max-width:800px){header{padding:0 18px}.layout{display:block}nav{border:0;flex-direction:row;padding:12px;overflow:auto}main{padding:30px 18px}h1{font-size:36px}h2{font-size:29px}.progress span{float:none;display:block}.offer{grid-template-columns:1fr 1fr;gap:10px}.amount{grid-column:1}.pill{grid-column:2}.actions{grid-column:1/-1;justify-content:flex-start}}'''

class Handler(BaseHTTPRequestHandler):
    token = secrets.token_urlsafe(24)
    def do_GET(self):
        if self.path not in ('/','/style.css'): self.send_error(404); return
        content = CSS if self.path == '/style.css' else render(self.token)
        self.send_response(200); self.send_header('Content-Type','text/css' if self.path=='/style.css' else 'text/html; charset=utf-8'); self.send_header('Content-Length',str(len(content))); self.end_headers(); self.wfile.write(content)
    def do_POST(self):
        length=int(self.headers.get('Content-Length','0'))
        if length>4096: self.send_error(413); return
        form=parse_qs(self.rfile.read(length).decode())
        if form.get('csrf',[''])[0] != self.token: self.send_error(403); return
        oid=form.get('id',[''])[0]
        if self.path=='/claim': claim(oid)
        elif self.path=='/run':
            for target in CLAIM_BUNDLES: claim(target)
        elif self.path=='/dismiss':
            state=load_state(); state['dismissed']=list(set(state['dismissed'])|{oid}); save_state(state)
        elif self.path=='/restore':
            state=load_state(); state['dismissed']=[]; save_state(state)
        else: self.send_error(404); return
        self.send_response(303); self.send_header('Location','/'); self.end_headers()

def main():
    p=argparse.ArgumentParser(); p.add_argument('command',choices=['scan','status','claim','run-due','serve']); p.add_argument('id',nargs='?'); p.add_argument('--port',type=int,default=8765); args=p.parse_args()
    if args.command=='scan': print(json.dumps(scan(),ensure_ascii=False,indent=2))
    elif args.command=='status': print(json.dumps(dashboard(),ensure_ascii=False,indent=2))
    elif args.command=='claim': print(json.dumps(claim(args.id),ensure_ascii=False))
    elif args.command=='run-due':
        for oid in load_state()['enabled']: print(oid,json.dumps(claim(oid,True),ensure_ascii=False))
    elif args.command=='serve':
        with ThreadingHTTPServer(('127.0.0.1',args.port),Handler) as server: server.serve_forever()
if __name__=='__main__': main()
