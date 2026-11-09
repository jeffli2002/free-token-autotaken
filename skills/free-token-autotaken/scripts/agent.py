#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Local-only free quota dashboard and claim coordinator (macOS)."""
import argparse
import fcntl
import html
import json
import os
import plistlib
import re
import secrets
import shutil
import sqlite3
import subprocess
import sys
import threading
import tomllib
from datetime import datetime, date
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlsplit
from zoneinfo import ZoneInfo

SCRIPT_DIR = Path(__file__).resolve().parent
ROOT = SCRIPT_DIR.parent
BANNER_FILE = ROOT / 'assets' / 'agent-auto-claim-banner.png'
DATA = Path.home() / 'Library/Application Support/FreeTokenAgent'
STATE_FILE = DATA / 'state.json'
BATCH_LOCK = threading.Lock()
BATCH_RUNS = {}
SCHEDULE_LOCK = threading.Lock()
def catalog():
    market = json.loads((ROOT / 'market_catalog.json').read_text())
    extra = json.loads((ROOT / 'market_extra.json').read_text())
    seen = {x['id'] for x in market}
    today = datetime.now(TZ).date()
    offers = market + [x for x in extra if x['id'] not in seen]
    # Render only offers with direct official verification; automatically hide stale or expired evidence.
    return [x for x in offers if x.get('verification_state') == '厂商官方已核实'
            and (not x.get('active_until') or today <= date.fromisoformat(x['active_until']))
            and (today - date.fromisoformat(x['verified_on'])).days <= int(x.get('verification_valid_days', 14))]
TZ = ZoneInfo('Asia/Shanghai')
KNOWN = {
    'com.openai.codex': 'Codex', 'com.todesktop.230313mzl4w4u92': 'Cursor',
    'com.work.pc.doubao': '豆包工作', 'com.tencent.workbuddy.mac': 'WorkBuddy',
    'com.moonshot.kimichat': 'Kimi', 'com.deepseek.dsh': 'DeepSeek Harness'
}
CLAIM_BUNDLES = {'doubao-daily': 'com.work.pc.doubao', 'workbuddy-daily': 'com.tencent.workbuddy.mac'}
PRODUCT_TYPES = [('llm','LLM 与模型 API'), ('coding_agent','Coding Agent'),
                 ('agent','通用 Agent'), ('creative','创作与多模态'),
                 ('developer_platform','开发平台')]
TYPE_DESCRIPTIONS = {
    'llm':'模型调用、开发平台额度与 API 代金券',
    'coding_agent':'编程助手、IDE 与代码 Agent 的产品内额度',
    'agent':'面向日常任务的 Agent 活动',
    'creative':'语音、图像、视频等创作额度',
    'developer_platform':'应用开发和部署平台的 AI 权益',
}

def product_key(offer): return offer['product_id']

def now(): return datetime.now(TZ).isoformat(timespec='seconds')
def period(offer):
    today = datetime.now(TZ).date()
    return today.isoformat() if offer['cycle'] == 'daily' else (today.strftime('%Y-%m') if offer['cycle'] == 'monthly' else 'once')
def load_state():
    try: state = json.loads(STATE_FILE.read_text())
    except FileNotFoundError: state = {}
    state.setdefault('claims', {})
    state.setdefault('dismissed', [])
    state.setdefault('suppressed_products', [])
    state.setdefault('schedules', {})
    state.setdefault('notices', {})
    return state
def save_state(state):
    DATA.mkdir(parents=True, exist_ok=True)
    temp = STATE_FILE.with_suffix('.tmp')
    temp.write_text(json.dumps(state, ensure_ascii=False, indent=2))
    os.replace(temp, STATE_FILE)

def auto_adapter_available(offer):
    return (offer.get('cycle') in ('daily', 'monthly')
            and offer.get('claim_mode') == 'desktop'
            and offer.get('id') in CLAIM_BUNDLES
            and bool(offer.get('bundle_ids')))

def skill_path(offer_id):
    slug = re.sub(r'[^a-z0-9-]+', '-', offer_id.lower()).strip('-')
    return Path.home() / '.agents' / 'skills' / f'free-token-{slug}' / 'SKILL.md'

def scheduler_loaded(label='org.free-token-autotaken.agent'):
    try:
        result=subprocess.run(['/bin/launchctl','print',f'gui/{os.getuid()}/{label}'],
                              capture_output=True,text=True,timeout=5)
        return result.returncode==0
    except (OSError,subprocess.SubprocessError):
        return False

def matches_activity(text, offer):
    haystack=text.casefold()
    if offer['id'].casefold() in haystack or offer['name'].casefold() in haystack:
        return True
    terms=offer.get('duplicate_terms',[])
    return bool(terms) and all(term.casefold() in haystack for term in terms)

def existing_claim_skill(offer):
    canonical=skill_path(offer['id'])
    candidates=[canonical]
    for root in (Path.home()/'.agents/skills',Path.home()/'.codex/skills',Path.home()/'.codebuddy/skills',
                 Path.home()/'.workbuddy/skills',Path.home()/'.cursor/skills',Path.home()/'.cursor/skills-cursor',
                 Path.home()/'.kimi-code/skills',Path.home()/'.deepseek/skills'):
        if root.exists(): candidates.extend(root.glob('*/SKILL.md'))
    seen=set()
    for path in candidates:
        if path in seen or not path.is_file(): continue
        seen.add(path)
        try: content=path.read_text(encoding='utf-8')[:65536]
        except OSError: continue
        if matches_activity(content,offer):
            return path
    return None

def cross_agent_task(offer):
    database=Path.home()/'.workbuddy/workbuddy.db'
    if database.is_file():
        try:
            with sqlite3.connect(f'file:{database}?mode=ro',uri=True,timeout=1) as db:
                rows=db.execute("SELECT name,prompt,skills_json FROM automations WHERE deleted_at IS NULL AND upper(status) IN ('ACTIVE','ENABLED') AND schedule_type='recurring'")
                for name,prompt,skills in rows:
                    if matches_activity(' '.join((name or '',prompt or '',skills or '')),offer):
                        return {'agent':'WorkBuddy','name':name,'source':'workbuddy-automation'}
        except (OSError,sqlite3.Error): pass
    codex_automations=Path.home()/'.codex/automations'
    if codex_automations.exists():
        for path in codex_automations.glob('*/automation.toml'):
            try: data=tomllib.loads(path.read_text(encoding='utf-8'))
            except (OSError,ValueError): continue
            if str(data.get('status','')).upper() not in ('ACTIVE','ENABLED'): continue
            if matches_activity(' '.join(str(data.get(key,'')) for key in ('title','name','prompt')),offer):
                return {'agent':'Codex','name':str(data.get('title') or data.get('name') or path.parent.name),'source':'codex-automation'}
    try:
        cron=subprocess.run(['/usr/bin/crontab','-l'],capture_output=True,text=True,timeout=5)
        if cron.returncode==0:
            for line in cron.stdout.splitlines():
                if line.strip() and not line.lstrip().startswith('#') and matches_activity(line,offer):
                    return {'agent':'macOS','name':'crontab 定时任务','source':'cron'}
    except (OSError,subprocess.SubprocessError): pass
    return None

def external_claim_task(offer):
    task=cross_agent_task(offer)
    if task: return task
    root=Path.home()/'Library/LaunchAgents'
    if not root.exists(): return None
    for path in root.glob('*.plist'):
        try:
            with path.open('rb') as stream: item=plistlib.load(stream)
        except (OSError,ValueError): continue
        label=item.get('Label','')
        if label=='org.free-token-autotaken.agent': continue
        args=' '.join(map(str,item.get('ProgramArguments',[])))
        if matches_activity(label+' '+args,offer) or str(skill_path(offer['id'])) in args:
            if scheduler_loaded(label): return {'agent':'macOS','name':label,'source':'launch-agent'}
    return None

def claim_automation_status(offer, state):
    schedule=state['schedules'].get(offer['id'],{})
    skill=existing_claim_skill(offer)
    task=external_claim_task(offer)
    if task: return 'external_running',task
    if schedule.get('enabled') and skill and scheduler_loaded(): return 'running',skill
    if schedule.get('enabled'): return 'configured',skill
    if skill: return 'skill_only',skill
    return 'absent',None

def write_claim_skill(offer):
    target = skill_path(offer['id'])
    target.parent.mkdir(parents=True, exist_ok=True)
    frequency = '每天' if offer['cycle'] == 'daily' else '每月'
    skill_name = 'free-token-' + offer['id']
    description = f"按厂商官方规则检查并领取{offer['vendor']}的免费额度。"
    text = f'''---
name: {json.dumps(skill_name, ensure_ascii=False)}
description: {json.dumps(description, ensure_ascii=False)}
---

# {offer['name']}

<!-- free-token-offer-id: {offer['id']} -->

仅在用户已启用本机每日任务时运行。适用范围：{offer['scope']}

领取规则：{offer['requirements']}

厂商官方来源：{offer['official_url']}
核验日期：{offer['verified_on']}

## 执行要求

- 本机连接器会按{frequency}计划调用适配器 `{offer['id']}`；只在客户端已运行且电脑空闲时尝试。
- 仅点击已核验的领取入口；登录失效、验证码、实名审核、入口变化或结果不明确时立即停止并报告，不能绕过验证。
- 只有客户端明确显示领取成功时才记录成功；同一领取周期不重复领取。
- 若活动过期或官方规则发生变化，停止操作并要求重新核验。
'''
    if target.exists(): raise FileExistsError('同名 Skill 已存在，未覆盖。')
    target.write_text(text, encoding='utf-8')
    return target

def install_scheduler():
    DATA.mkdir(parents=True, exist_ok=True)
    binary = SCRIPT_DIR / 'claim'
    if not binary.exists():
        result = subprocess.run(['/usr/bin/swiftc', str(SCRIPT_DIR / 'claim.swift'), '-o', str(binary)],
                                capture_output=True, text=True, timeout=120)
        if result.returncode:
            raise RuntimeError('无法准备本机领取连接器：' + (result.stderr.strip()[-500:] or 'Swift 编译失败'))
        binary.chmod(0o700)
    launch_agents = Path.home() / 'Library' / 'LaunchAgents'
    launch_agents.mkdir(parents=True, exist_ok=True)
    plist_path = launch_agents / 'org.free-token-autotaken.agent.plist'
    if scheduler_loaded(): return
    payload = {
        'Label': 'org.free-token-autotaken.agent',
        'ProgramArguments': [sys.executable, str(SCRIPT_DIR / 'agent.py'), 'run-due'],
        'StartCalendarInterval': {'Minute': 5},
        'StandardOutPath': str(DATA / 'agent.log'),
        'StandardErrorPath': str(DATA / 'agent-error.log'),
    }
    with plist_path.open('wb') as f: plistlib.dump(payload, f)
    domain = f'gui/{os.getuid()}'
    subprocess.run(['/bin/launchctl', 'bootout', f'{domain}/org.free-token-autotaken.agent'],
                   capture_output=True, text=True, timeout=10)
    loaded = subprocess.run(['/bin/launchctl', 'bootstrap', domain, str(plist_path)],
                            capture_output=True, text=True, timeout=15)
    if loaded.returncode:
        raise RuntimeError('本机定时任务启动失败：' + (loaded.stderr.strip()[-500:] or 'launchctl 未能加载任务'))

def schedule_offer(offer_id):
    with SCHEDULE_LOCK:
        return _schedule_offer(offer_id)

def _schedule_offer(offer_id):
    offer = next((x for x in catalog() if x['id'] == offer_id), None)
    if offer and product_key(offer) in load_state()['suppressed_products']:
        return {'status':'hidden','message':'该产品已设为不再显示。'}
    if not offer or not auto_adapter_available(offer):
        return {'status':'unsupported','message':'该活动尚无经核验的客户端自动领取适配器；可先用当期领取按钮手动操作。'}
    if not any(bundle_id in scan() for bundle_id in offer.get('bundle_ids', [])):
        message='本机未检测到该活动对应的客户端，未创建定时任务。'
        state=load_state(); state['notices'][offer_id]=message; save_state(state)
        return {'status':'not_installed','message':message}
    state=load_state()
    existing_status,existing_skill=claim_automation_status(offer,state)
    if existing_status in ('running','external_running'):
        message='本助手已有本机 Skill 和定时任务在代领此活动，未重复创建。' if existing_status=='running' else f'检测到 {existing_skill["agent"]} 已启用“{existing_skill["name"]}”，本助手未重复创建。'
        state['notices'][offer_id]=message; save_state(state)
        return {'status':'already_running','message':message}
    if existing_status=='skill_only':
        message='已发现这个领取活动的 Skill；无法确认它是否另有定时任务，未重复创建。'
        state['notices'][offer_id]=message; save_state(state)
        return {'status':'existing_skill','message':message}
    if skill_path(offer_id).exists() and not existing_skill:
        message='发现同名 Skill，但内容无法确认属于本活动；未覆盖，也未创建定时任务。'
        state['notices'][offer_id]=message; save_state(state)
        return {'status':'skill_conflict','message':message}
    try:
        path = existing_skill or write_claim_skill(offer)
        install_scheduler()
    except (OSError, subprocess.SubprocessError, RuntimeError) as exc:
        message='定时领取未启用：' + str(exc)
        state=load_state(); state['notices'][offer_id]=message; save_state(state)
        return {'status':'not_ready','message':message}
    state = load_state()
    state['schedules'][offer_id] = {'enabled':True,'cycle':offer['cycle'],
                                    'created_at':state['schedules'].get(offer_id,{}).get('created_at',now()),
                                    'skill_path':str(path),'announcement_seen':False}
    reused=existing_skill is not None
    state['notices'][offer_id] = ('已复用本机现有 Skill，启用每日定时任务；未创建重复 Skill。' if reused else '已创建本机 Skill 和定时任务；电脑空闲且客户端运行时自动尝试，成功后本周期不再重复。')
    save_state(state)
    return {'status':'reused_skill' if reused else 'scheduled','message':state['notices'][offer_id]}

def unschedule_offer(offer_id):
    state = load_state()
    schedule = state['schedules'].get(offer_id)
    if not schedule or not schedule.get('enabled'):
        return {'status':'not_managed','message':'本助手没有管理此活动的运行任务，未操作其他应用的 Skill 或定时任务。'}
    schedule['enabled'] = False
    schedule['disabled_at'] = now()
    state['notices'][offer_id] = '已暂停本助手对该活动的本机代领任务；WorkBuddy 应用内部的 Skill 未被操作。'
    save_state(state)
    return {'status':'disabled','message':state['notices'][offer_id]}
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
    if offer and product_key(offer) in load_state()['suppressed_products']:
        return {'status':'hidden','message':'该产品已设为不再显示。'}
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
    changed=False
    for offer in market:
        schedule=state['schedules'].get(offer['id'],{})
        if schedule.get('enabled'):
            duplicate=external_claim_task(offer)
            if duplicate:
                schedule['enabled']=False
                schedule['disabled_at']=now()
                state['notices'][offer['id']]=f'发现 {duplicate["agent"]} 已启用“{duplicate["name"]}”；本助手的重复代领任务已暂停。'
                changed=True
    if changed: save_state(state)
    # This generic WorkBuddy activity is shown only when this device has a
    # concrete prior client success or an explicitly created daily schedule.
    if not any(key.startswith('workbuddy-daily:') for key in state['claims']) and 'workbuddy-daily' not in state['schedules']:
        market=[offer for offer in market if offer['id']!='workbuddy-daily']
    hidden=set(state['suppressed_products'])
    hidden.update(product_key(offer) for offer in market if offer['id'] in state['dismissed'])
    for offer in market:
        if product_key(offer) in hidden: continue
        on_device = any(x in installed for x in offer['bundle_ids'])
        group = 'installed' if on_device else ('other' if offer.get('requires_install') else 'web')
        record = state['claims'].get(offer['id'] + ':' + period(offer), {})
        auto_status,auto_detail = claim_automation_status(offer,state) if on_device and auto_adapter_available(offer) else ('absent',None)
        entries.append({**offer,'group':group,'record':record,
                        'schedule':state['schedules'].get(offer['id'],{}),
                        'automation_status':auto_status,
                        'automation_task':auto_detail if isinstance(auto_detail,dict) else None,
                        'notice':state['notices'].get(offer['id'],''),
                        'auto_supported':auto_adapter_available(offer),
                        'dismissed':False})
    return {'apps':list(installed.values()),'offers':entries,'time':now(),'market':meta}

def batch_candidates(kind, data):
    offers = data['offers']
    if kind == 'once':
        return [offer for offer in offers if offer['group'] == 'installed'
                and offer['cycle'] == 'once' and offer['claim_mode'] != 'included'
                and offer['record'].get('status') not in ('claimed', 'already_claimed')]
    if kind == 'daily':
        return [offer for offer in offers if offer['group'] == 'installed'
                and offer['cycle'] == 'daily' and offer['auto_supported']
                and offer['automation_status'] in ('absent', 'configured')]
    return []

def start_batch(kind):
    candidates = batch_candidates(kind, dashboard())
    if len(candidates) < 2:
        return None
    with BATCH_LOCK:
        if any(not run['done'] for run in BATCH_RUNS.values()):
            return None
        for old_id in list(BATCH_RUNS)[:-10]:
            del BATCH_RUNS[old_id]
        run_id = secrets.token_urlsafe(12)
        BATCH_RUNS[run_id] = {'kind': kind, 'done': False, 'items': [
            {'id': offer['id'], 'name': offer['name'], 'status': 'queued', 'message': '',
             'official_url': offer['official_url'], 'claim_mode': offer['claim_mode']}
            for offer in candidates]}
    threading.Thread(target=run_batch, args=(run_id,), daemon=True).start()
    return run_id

def run_batch(run_id):
    with BATCH_LOCK:
        kind = BATCH_RUNS[run_id]['kind']
        items = [(item['id'], item['claim_mode']) for item in BATCH_RUNS[run_id]['items']]
    try:
        for index, (offer_id, claim_mode) in enumerate(items):
            with BATCH_LOCK:
                BATCH_RUNS[run_id]['items'][index]['status'] = 'running'
            try:
                if kind == 'once' and (claim_mode != 'desktop' or offer_id not in CLAIM_BUNDLES):
                    result = {'status':'manual_required', 'message':'此活动需要在官方页面完成领取。'}
                else:
                    result = claim(offer_id) if kind == 'once' else schedule_offer(offer_id)
            except Exception as exc:
                result = {'status': 'error', 'message': f'处理失败：{type(exc).__name__}'}
            with BATCH_LOCK:
                item = BATCH_RUNS[run_id]['items'][index]
                item['status'] = result['status']
                item['message'] = result.get('message', '')
    finally:
        with BATCH_LOCK:
            BATCH_RUNS[run_id]['done'] = True

def render(token, flash=None):
    data = dashboard(); esc=html.escape
    def button(action, oid, label, css=''):
        return f'<form method="post" action="/{action}"><input type="hidden" name="csrf" value="{token}"><input type="hidden" name="id" value="{esc(oid)}"><button class="{css}">{label}</button></form>'
    def claimed_button():
        return '<button class="claimed" type="button" disabled>已领取</button>'
    def claim_history():
        rows=[]
        for key, record in sorted(load_state()['claims'].items(), reverse=True):
            if record.get('status') not in ('claimed','already_claimed') or ':' not in key: continue
            offer_id, claim_period = key.rsplit(':', 1)
            bundle = CLAIM_BUNDLES.get(offer_id)
            offer = next((item for item in data['offers'] if item['id'] == offer_id), None)
            if offer and offer['group']=='installed': continue
            name = offer['name'] if offer else KNOWN.get(bundle, offer_id)
            amount = record.get('claimed_amount') or record.get('amount')
            recorded_at = record.get('time', claim_period)
            amount_html=f'<div class="history-amount">{esc(str(amount))}</div>' if amount else ''
            rows.append(f'<article class="history-item"><div><strong>{esc(name)}</strong><p>{esc(claim_period)} · 本机客户端成功记录</p></div>{amount_html}{claimed_button()}<small>记录时间：{esc(recorded_at)}</small></article>')
        return '<div class="claim-history"><h3 class="history-title">历史领取</h3>' + ''.join(rows) + '</div>' if rows else ''
    def cards(group, product_type=None):
        rows=[]
        for o in data['offers']:
            if group=='more':
                if (o['group']=='installed' or o['cycle']!=product_type['cycle']
                        or o['product_type']!=product_type['type']): continue
            elif o['group'] != group or (product_type and o['cycle'] != product_type): continue
            status=o['record'].get('status','')
            if group=='installed' and (status in ('deferred','needs_review','needs_accessibility') or (o['claim_mode']=='included' and status not in ('claimed','already_claimed'))): continue
            label={'claimed':'已领取','already_claimed':'已领取'}.get(status,'待领取' if group=='installed' else ('未安装' if o['group']=='other' else ''))
            cycle=o.get('cycle','once')
            cycle_label={'daily':'每日','monthly':'每月','once':'一次性'}.get(cycle,cycle)
            manual_label=('今日领取' if cycle=='daily' else ('本期领取' if cycle=='monthly' else '立即领取')) if group=='installed' else '立刻领取'
            if cycle in ('daily','monthly'):
                schedule_label='开启 Agent 每日自动领取' if cycle=='daily' else '开启 Agent 每月自动领取'
                automation_status=o.get('automation_status','absent')
                scheduled=automation_status=='running'
            else:
                schedule_label=''
                automation_status='absent'
                scheduled=False
            if group=='installed' and status in ('claimed','already_claimed'):
                manual_action=claimed_button()
            elif group=='installed' and o['claim_mode']=='included':
                manual_action=''
            elif group=='installed' and o['claim_mode']=='desktop' and o['id'] in CLAIM_BUNDLES:
                manual_action=button('claim',o['id'],manual_label,'primary')
            else:
                manual_action=f'<a class="button primary" href="{esc(o["official_url"])}" target="_blank" rel="noopener">{manual_label}</a>'
            if cycle in ('daily','monthly') and group=='installed' and o['claim_mode']!='included':
                if o['auto_supported']:
                    if automation_status=='external_running':
                        schedule_action=''
                    elif automation_status=='skill_only':
                        schedule_action='<button class="claimed" type="button" disabled>已有同活动 Skill</button>'
                    elif scheduled:
                        schedule_action=button('unschedule',o['id'],'暂停本助手代领','primary')
                    else:
                        text={'configured':'修复定时任务'}.get(automation_status,schedule_label)
                        schedule_action=button('schedule',o['id'],text,'primary')
                else:
                    schedule_action=''
                action=manual_action+schedule_action
            else:
                action=manual_action
            if group!='installed': action += button('dismiss',product_key(o),'× 不再显示','quiet dismiss')
            messages=[]
            if o.get('notice'): messages.append(o['notice'])
            if status: messages.append(o['record'].get('message',''))
            if o.get('schedule',{}).get('enabled'): messages.append('已启用本机定时领取；电脑空闲且客户端运行时检查。')
            if o.get('schedule',{}).get('disabled_at'): messages.append('本机定时领取已暂停。')
            if o['auto_supported'] and cycle in ('daily','monthly') and automation_status in ('absent','configured'):
                messages.append(f'启用后会创建本机 Skill 和{cycle_label}定时任务。')
            note = '<p class="note">' + esc(' '.join(x for x in messages if x)) + '</p>' if messages else ''
            task_text={'running':'本助手的本机 Skill 与定时任务正在代领','skill_only':'已发现同活动 Skill；无法确认是否已有调度，未重复创建','configured':'本助手已配置代领任务，后台调度需要修复'}.get(automation_status,'')
            if automation_status=='external_running' and o.get('automation_task'):
                task=o['automation_task']
                task_text=f'{task["agent"]} 已启用“{task["name"]}”；本助手不会重复创建或暂停它'
            task_status=f'<div class="task-status">{task_text}</div>' if group=='installed' and task_text else ''
            claim_mode_label=''
            if group=='installed' and o['claim_mode']!='included':
                if automation_status in ('running','external_running'):
                    claim_mode_label='<span class="claim-mode-badge auto">Agent 自动领取中</span>'
                elif cycle in ('daily','monthly') and o['auto_supported']:
                    claim_mode_label='<span class="claim-mode-badge">支持 Agent 自动领取</span>'
                else:
                    claim_mode_label='<span class="claim-mode-badge manual">手动领取</span>'
            vendor=esc(o['vendor']); name=esc(o['name']); amount=esc(o['amount'])
            requirements=esc(o['requirements']); scope=esc(o['scope'])
            verification=esc(o.get('verification_state', '官方待核实')); verified_on=esc(o['verified_on'])
            source_url=esc(o['source_url']); official_url=esc(o['official_url'])
            show_pill=bool(label and status not in ('claimed','already_claimed'))
            pill=f'<span class="pill">{label}</span>' if show_pill else ''
            card_class='offer' if show_pill else 'offer no-pill'
            rows.append(f'<article class="{card_class}"><div class="offer-identity"><h3>{vendor}</h3><p>{name}</p>{claim_mode_label}</div><div class="amount"><small>活动额度 · {esc(cycle_label)}</small><strong>{amount}</strong></div>{pill}<div class="actions">{action}</div>{task_status}<details><summary>领取条件与适用范围</summary><p>{requirements}</p><p>适用范围：{scope}</p><p>来源状态：{verification} · 标注日期：{verified_on}</p><a href="{source_url}" target="_blank" rel="noopener">活动来源 ↗</a> · <a href="{official_url}" target="_blank" rel="noopener">厂商页面 ↗</a>{note}</details></article>')
        return ''.join(rows) or ('' if group=='installed' else '<p class="empty">暂无已核实活动。</p>')
    def installed_sections():
        sections=[]
        for cycle, title, description, batch_kind, batch_label in (
            ('once','一次性活动','每项只领取一次，可单独操作或批量领取。','once','一键领取全部一次性额度'),
            ('daily','每日活动','查看今日状态，按需开启 Agent 每日自动领取。','daily','一键开启全部每日自动领取'),
            ('monthly','每月活动','按本期活动规则领取。',None,None),
        ):
            cards_html=cards('installed',cycle)
            if not cards_html: continue
            count=len(batch_candidates(batch_kind,data)) if batch_kind else 0
            batch_html=(f'<div class="batch-bar"><span>{count} 项可批量处理，逐项显示结果。</span>'
                        f'<button class="primary" type="button" data-batch="{batch_kind}">{batch_label}</button></div>') if count>1 else ''
            sections.append(f'<section class="claim-group"><div class="section-heading"><h2>{title}</h2>'
                            f'<p>{description}</p></div>{cards_html}{batch_html}</section>')
        return ''.join(sections)
    def more_sections():
        sections=[]
        for cycle, cycle_title, cycle_description in (
            ('once','一次性活动','一次领取的免费额度与新用户权益。'),
            ('daily','每日活动','每日领取、每日登录额度与周期性免费用量。'),
            ('monthly','每月活动','按月更新或每月可领取的活动。'),
        ):
            subsections=[]
            cycle_count=0
            for type_id, type_title in PRODUCT_TYPES:
                count=sum(o['group']!='installed' and o['cycle']==cycle and o['product_type']==type_id for o in data['offers'])
                if count:
                    cycle_count+=count
                    subsections.append(f'<section class="offer-subgroup"><div class="group-heading"><h4>{type_title}</h4><span>{count} 项</span></div>{cards("more",{"cycle":cycle,"type":type_id})}</section>')
            if subsections:
                sections.append(f'<section class="offer-group"><div class="group-heading"><h3 class="group-title">{cycle_title}</h3><span>{cycle_count} 项活动</span></div><p class="group-intro">{cycle_description}</p>{"".join(subsections)}</section>')
        return ''.join(sections) or '<p class="empty">暂无更多已核实活动。</p>'
    state=load_state()
    today=datetime.now(TZ).date().isoformat()
    confirmed=sum(1 for key,record in state['claims'].items() if key.endswith(':'+today) and record.get('status') in ('claimed','already_claimed'))
    body=f'''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>Free Token Autotaken</title><link rel="stylesheet" href="/style.css"><div class="window"><header><span class="dots">● ● ●</span><strong>Free Token Autotaken</strong><small>本机运行 · 数据仅存本地</small></header><div class="layout"><nav><a href="#installed">我的活动</a><a href="#other">发现更多</a></nav><main><h1>免费薅Token</h1><p class="lede">先从模型和 Agent 厂商官方页面收集活动，再匹配本机软件。当前目录 {len(data['offers'])} 项；更新时间 {esc(data['market'].get('official_sources_updated_at', '尚未更新'))}。</p><img class="hero-banner" src="/banner.png" alt="Agent 自动领取免费 Token 和 Credits" width="2172" height="724"><div class="progress">本机今日已确认领取 <b>{confirmed}</b> 项</div><div class="installed-section" id="installed">{installed_sections()}{claim_history()}</div><section class="discovery-section" id="other"><div class="section-heading"><h2>发现更多</h2><p>按产品用途浏览官方已核实的活动</p></div>{more_sections()}</section></main></div></div></html>'''
    if flash:
        title={'scheduled':'每日代领任务已创建','reused_skill':'已复用现有 Skill','already_running':'本助手已有代领任务','disabled':'本助手代领任务已暂停'}.get(flash['status'],'操作未完成')
        detail='Skill 已写入本机；后台任务每小时第 5 分钟检查客户端。当天确认领取成功后不会重复操作。' if flash['status']=='scheduled' else flash['message']
        dialog=f'<div class="notice-overlay" role="alertdialog" aria-modal="true" aria-labelledby="notice-title"><div class="notice-dialog"><h2 id="notice-title">{esc(title)}</h2><p>{esc(detail)}</p><button class="primary" type="button" onclick="this.closest(\'.notice-overlay\').remove()">知道了</button></div></div><script>history.replaceState(null,"","/")</script>'
        body=body.replace('<div class="window">',dialog+'<div class="window">',1)
    body=body.replace('</main>', BATCH_DIALOG + BATCH_SCRIPT.replace('__CSRF__', token) + '</main>', 1)
    if load_state()['suppressed_products'] or load_state()['dismissed']:
        body = body.replace('<h2 id="apps">', button('restore','all','恢复全部已隐藏产品','quiet') + '<h2 id="apps">')
    return body.encode()

CSS=b'''*{box-sizing:border-box}html{scroll-behavior:smooth}body{margin:0;background:#f8f7f3;color:#172322;font-family:-apple-system,BlinkMacSystemFont,"PingFang SC",sans-serif}.window{max-width:1500px;margin:auto;background:#fcfbf8;min-height:100vh}header{height:76px;border-bottom:1px solid #e7e5e0;display:flex;align-items:center;gap:24px;padding:0 36px;font-size:22px}.dots{font-size:17px;letter-spacing:3px;color:#28b853}.dots:first-letter{color:#ed6056}header small{margin-left:auto;font-size:14px;color:#6c7773}.layout{display:grid;grid-template-columns:220px 1fr}nav{border-right:1px solid #e7e5e0;padding:40px 20px;display:flex;flex-direction:column;gap:10px}nav a{padding:13px 18px;text-decoration:none;color:#53605c;border-radius:13px}nav a:first-child,nav a:hover{background:#dfebe7;color:#096c60}main{padding:48px 56px;max-width:1250px}h1{font-family:Georgia,"Songti SC",serif;font-size:52px;margin:0 0 10px}h2{font-family:Georgia,"Songti SC",serif;font-size:36px;margin:50px 0 22px}.lede{font-size:18px;color:#6c7773;margin:0 0 34px}.progress{background:#e5f0ec;padding:20px 28px;border-radius:16px;margin-bottom:26px;font-size:18px}.progress b{font-size:26px;color:#087b68}.progress span{float:right;color:#648077;font-size:14px;padding-top:9px}.offer{display:grid;grid-template-columns:minmax(180px,1.2fr) minmax(150px,1fr) 100px minmax(210px,auto);align-items:center;gap:20px;border:1px solid #e3e2dd;border-radius:16px;padding:18px 24px;margin:12px 0;background:#fff}.offer h3{font-size:22px;margin:0 0 4px}.offer p{margin:0;color:#68736f}.amount{font-size:19px;font-weight:600}.pill{background:#e1f0e9;color:#0b7668;border-radius:30px;padding:8px 10px;text-align:center;font-size:14px}.actions{display:flex;gap:8px;justify-content:flex-end;flex-wrap:wrap}.actions form{margin:0}.done{color:#087b68;font-size:14px}button,.button{border:1px solid #087b68;background:white;color:#087b68;border-radius:10px;padding:10px 16px;font-size:14px;cursor:pointer;text-decoration:none;white-space:nowrap}button.primary,.button.primary{background:#087b68;color:white}.disabled{border-color:#d9dedb;color:#9aa39f;background:#f4f5f3;cursor:not-allowed}.quiet{border:0;color:#7f8a86}.note{margin-top:10px!important;color:#59766f!important}.offer details{grid-column:1/-1;border-top:1px solid #eee;padding-top:12px;color:#60706b}.offer details p{margin:8px 0}.offer a{color:#087b68}.apps,.foot{color:#68736f}.empty{padding:24px;color:#7b8580}@media(max-width:800px){header{padding:0 18px}.layout{display:block}nav{border:0;flex-direction:row;padding:12px;overflow:auto}main{padding:30px 18px}h1{font-size:36px}h2{font-size:29px}.progress span{float:none;display:block}.offer{grid-template-columns:1fr 1fr;gap:10px}.amount{grid-column:1}.pill{grid-column:2}.actions{grid-column:1/-1;justify-content:flex-start}}'''
CSS+=b'''.history-title{font-size:28px;margin:34px 0 6px}.history-help{color:#68736f;margin:0 0 14px}.history-item{display:grid;grid-template-columns:minmax(180px,1fr) minmax(170px,1fr) auto;gap:12px;align-items:center;background:#fff;border:1px solid #dce8e2;border-radius:14px;padding:16px 22px;margin:10px 0}.history-item strong{font-size:18px}.history-item p{margin:5px 0 0;color:#6c7773;font-size:13px}.history-amount{font-weight:600;color:#386258}.history-item small{grid-column:1/-1;color:#79847f}.claimed{background:#e1f0e9;color:#0b7668;border-color:#c8e2d7;cursor:default}.claimed:disabled{opacity:1}@media(max-width:800px){.history-item{grid-template-columns:1fr auto}.history-amount{grid-column:1}.history-item button{grid-column:2;grid-row:1/3}}'''
CSS+=b'''.history-item .claimed{grid-column:3}@media(max-width:800px){.history-item .claimed{grid-column:2}}'''
CSS+=b'''.offer.no-pill{grid-template-columns:minmax(220px,1.2fr) minmax(180px,1fr) minmax(300px,auto)}.offer.no-pill .actions{grid-column:3;flex-wrap:nowrap;align-items:center;justify-content:flex-end}.offer-group{margin-top:28px}.offer-group .group-title{font-size:24px;margin:22px 0 12px}.dismiss{font-size:13px}.dismiss:hover{color:#a43b36}@media(max-width:800px){.offer.no-pill{grid-template-columns:1fr 1fr}.offer.no-pill .actions{grid-column:1/-1;flex-wrap:wrap;justify-content:flex-start}}'''
CSS+=b'''.section-heading{display:flex;align-items:end;justify-content:space-between;gap:20px;border-bottom:1px solid #e5e9e5;padding-bottom:12px;margin:48px 0 18px}.section-heading h2{margin:0}.section-heading p{margin:0 0 4px;color:#6c7773;font-size:14px}.group-heading{display:flex;align-items:baseline;gap:12px}.group-heading span{color:#77837e;font-size:13px}.offer-group .group-title{margin:0}.group-intro{color:#77837e;font-size:14px;margin:5px 0 14px}.offer-identity h3{margin:0 0 5px}.offer-identity p{font-size:14px;line-height:1.5}.offer .amount{display:flex;flex-direction:column;gap:6px}.offer .amount small{font-size:12px;font-weight:500;color:#7c8883}.offer .amount strong{font-size:18px;line-height:1.4}.installed-section .offer{border-color:#cfe3d9}.history-title{font-family:Georgia,"Songti SC",serif;font-size:25px;margin:32px 0 12px}@media(max-width:800px){.section-heading{display:block;margin-top:36px}.section-heading p{margin-top:7px}.offer .amount{grid-column:1/-1}.offer.no-pill .actions{grid-column:1/-1}}'''
CSS+=b'''.task-status{grid-column:1/-1;color:#087b68;background:#edf7f1;border-radius:8px;padding:9px 13px;font-size:13px}.notice-overlay{position:fixed;inset:0;background:rgba(18,38,33,.38);display:flex;align-items:center;justify-content:center;z-index:1000;padding:20px}.notice-dialog{width:min(460px,100%);background:#fff;border-radius:18px;box-shadow:0 20px 60px rgba(0,0,0,.18);padding:28px}.notice-dialog h2{font-family:-apple-system,BlinkMacSystemFont,"PingFang SC",sans-serif;font-size:24px;margin:0 0 12px}.notice-dialog p{color:#53625c;line-height:1.7;margin:0 0 22px}.notice-dialog button{min-width:96px}'''
CSS+=b'''h1,h2,.history-title{font-family:-apple-system,BlinkMacSystemFont,"PingFang SC",sans-serif;font-weight:700}'''
CSS+=b'''.hero-banner{display:block;width:100%;height:auto;aspect-ratio:3/1;object-fit:cover;border-radius:18px;margin:0 0 28px;border:1px solid #dce9e2}@media(max-width:800px){.hero-banner{aspect-ratio:2/1;object-position:left center}}'''
CSS+=b'''.claim-mode-badge{display:inline-block;margin-top:10px;padding:5px 9px;border-radius:7px;background:#eef5f1;color:#376a5c;font-size:12px;font-weight:600}.claim-mode-badge.auto{background:#dcefe5;color:#087b68}.claim-mode-badge.manual{background:#f2f2ef;color:#64716a}'''
CSS+=b'''.offer-subgroup{margin:18px 0 24px}.offer-subgroup h4{font-size:17px;margin:0}.offer-subgroup .group-heading{margin-bottom:8px}'''
CSS+=b'''.claim-group{margin-top:42px}.claim-group .section-heading{margin-top:0}.batch-bar{display:flex;align-items:center;justify-content:space-between;gap:16px;padding:17px 20px;margin-top:14px;border:1px solid #dce9df;border-radius:14px;background:#edf5ef;color:#527264;font-size:14px}.batch-dialog{border:0;border-radius:18px;padding:0;width:min(580px,calc(100% - 28px));box-shadow:0 24px 80px #1b453d3b;color:#172322}.batch-dialog::backdrop{background:#14282080}.batch-dialog-header,.batch-dialog-body{padding:22px 26px}.batch-dialog-header{display:flex;justify-content:space-between;gap:12px;border-bottom:1px solid #e9efeb}.batch-dialog-header h2{font-size:23px;margin:0}.batch-dialog-header button{border:0;background:#f0f4f1;color:#53695e;padding:3px 11px}.batch-summary{margin:0 0 12px;color:#386258}.batch-track{height:5px;background:#e7efea;border-radius:9px;overflow:hidden}.batch-fill{height:100%;width:0;background:#087b68;transition:width .3s}.batch-result{display:flex;justify-content:space-between;gap:14px;padding:14px 0;border-bottom:1px solid #edf0ed;font-size:14px}.batch-result small{display:block;color:#78867e;margin-top:4px}.batch-result a{display:inline-block;margin-top:5px;color:#087b68}.batch-state{white-space:nowrap;color:#68766e}.batch-state.done{color:#087b68;font-weight:700}.batch-state.attention{color:#9b6118}.batch-state.running::before{content:"";display:inline-block;width:9px;height:9px;margin-right:6px;border:2px solid #8ebfa8;border-top-color:#087b68;border-radius:50%;animation:batch-spin .7s linear infinite}@keyframes batch-spin{to{transform:rotate(360deg)}}@media(max-width:800px){.batch-bar{display:block}.batch-bar button{width:100%;margin-top:12px;white-space:normal}.batch-dialog-header,.batch-dialog-body{padding:18px}}@media(prefers-reduced-motion:reduce){.batch-state.running::before{animation:none}.batch-fill{transition:none}}'''

BATCH_DIALOG='''<dialog class="batch-dialog" id="batch-dialog" aria-labelledby="batch-title"><div class="batch-dialog-header"><h2 id="batch-title">批量处理中</h2><button type="button" id="batch-close" aria-label="关闭">×</button></div><div class="batch-dialog-body"><p class="batch-summary" id="batch-summary" role="status" aria-live="polite"></p><div class="batch-track"><div class="batch-fill" id="batch-fill"></div></div><div id="batch-results"></div></div></dialog>'''
BATCH_SCRIPT='''<script>
(() => {
  const csrf = '__CSRF__';
  const dialog = document.getElementById('batch-dialog');
  const title = document.getElementById('batch-title');
  const summary = document.getElementById('batch-summary');
  const fill = document.getElementById('batch-fill');
  const results = document.getElementById('batch-results');
  let currentRun = null;
  let complete = false;
  const success = new Set(['claimed','already_claimed','scheduled','reused_skill','already_running']);
  const labels = {claimed:'✓ 已确认领取',already_claimed:'✓ 已领取过',scheduled:'✓ 已开启',reused_skill:'✓ 已开启',already_running:'✓ 已有任务',manual_required:'需手动领取',queued:'排队中',running:'处理中'};
  function showRun(run) {
    results.replaceChildren();
    let finished = 0;
    for (const item of run.items) {
      const row = document.createElement('div');
      row.className = 'batch-result';
      const detail = document.createElement('div');
      const name = document.createElement('strong');
      name.textContent = item.name;
      const note = document.createElement('small');
      note.textContent = item.message || (item.status === 'running' ? '正在处理…' : '等待处理');
      detail.append(name,note);
      if (item.status === 'manual_required' && item.official_url) {
        const link = document.createElement('a');
        link.href = item.official_url;
        link.target = '_blank';
        link.rel = 'noopener';
        link.textContent = '前往官方领取 ↗';
        detail.append(link);
      }
      const state = document.createElement('span');
      state.className = 'batch-state ' + (success.has(item.status) ? 'done' : item.status === 'running' ? 'running' : item.status === 'queued' ? '' : 'attention');
      state.textContent = labels[item.status] || '需要你处理';
      row.append(detail,state);
      results.append(row);
      if (item.status !== 'queued' && item.status !== 'running') finished++;
    }
    summary.textContent = run.done ? `已处理 ${finished}/${run.items.length} 项` : `处理中 ${finished}/${run.items.length} 项`;
    fill.style.width = `${finished / run.items.length * 100}%`;
    complete = run.done;
  }
  async function poll(runId) {
    while (currentRun === runId) {
      const response = await fetch(`/api/batch-status?run=${encodeURIComponent(runId)}&csrf=${encodeURIComponent(csrf)}`);
      if (!response.ok) { summary.textContent = '状态读取失败，请刷新页面查看记录。'; return; }
      const run = await response.json();
      showRun(run);
      if (run.done) return;
      await new Promise(resolve => setTimeout(resolve, 700));
    }
  }
  for (const button of document.querySelectorAll('[data-batch]')) {
    button.addEventListener('click', async () => {
      const kind = button.dataset.batch;
      title.textContent = kind === 'once' ? '一次性额度 · 领取进度' : '每日自动领取 · 开启进度';
      summary.textContent = '正在核对可处理活动…';
      results.replaceChildren();
      fill.style.width = '0%';
      complete = false;
      dialog.showModal();
      try {
        const response = await fetch('/api/batch', {method:'POST',headers:{'Content-Type':'application/x-www-form-urlencoded'},body:new URLSearchParams({kind,csrf})});
        const data = await response.json();
        if (!response.ok) { summary.textContent = data.error || '批量任务未启动'; return; }
        currentRun = data.run;
        await poll(currentRun);
      } catch { summary.textContent = '本机连接失败，请刷新页面后重试。'; }
    });
  }
  document.getElementById('batch-close').addEventListener('click', () => dialog.close());
  dialog.addEventListener('close', () => { if (complete) location.reload(); });
})();
</script>'''

class Handler(BaseHTTPRequestHandler):
    token = secrets.token_urlsafe(24)
    def send_json(self, value, status=200):
        content = json.dumps(value, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Cache-Control', 'no-store')
        self.send_header('Content-Length', str(len(content)))
        self.end_headers()
        self.wfile.write(content)
    def do_GET(self):
        parsed=urlsplit(self.path)
        if parsed.path == '/api/batch-status':
            query=parse_qs(parsed.query)
            if query.get('csrf',[''])[0] != self.token: self.send_error(403); return
            with BATCH_LOCK:
                run=BATCH_RUNS.get(query.get('run',[''])[0])
                snapshot=json.loads(json.dumps(run, ensure_ascii=False)) if run else None
            self.send_json(snapshot if snapshot else {'error':'未找到本次任务'}, 200 if snapshot else 404)
            return
        if parsed.path not in ('/','/style.css','/banner.png'): self.send_error(404); return
        flash=None
        if parsed.path=='/':
            state=load_state()
            query=parse_qs(parsed.query)
            oid=query.get('notice',[''])[0]
            status=query.get('result',[''])[0]
            message=state['notices'].get(oid)
            if message and status in ('scheduled','reused_skill','already_running','existing_skill','disabled','not_managed','not_ready','not_installed','unsupported','skill_conflict'):
                flash={'status':status,'message':message}
            if not flash:
                oid=next((key for key,schedule in state['schedules'].items() if schedule.get('enabled') and not schedule.get('announcement_seen')),None)
                if oid: flash={'status':'already_running','message':'本助手的本机 Skill 与定时任务已在代领这个活动。'}
            if flash and flash['status'] in ('scheduled','reused_skill','already_running') and oid in state['schedules']:
                state['schedules'][oid]['announcement_seen']=True
                save_state(state)
        content = (CSS if parsed.path == '/style.css' else
                   BANNER_FILE.read_bytes() if parsed.path == '/banner.png' else
                   render(self.token, flash))
        content_type = {'/style.css':'text/css', '/banner.png':'image/png'}.get(parsed.path, 'text/html; charset=utf-8')
        self.send_response(200)
        self.send_header('Content-Type', content_type)
        self.send_header('Cache-Control', 'no-store')
        self.send_header('Content-Length', str(len(content)))
        self.end_headers()
        self.wfile.write(content)
    def do_POST(self):
        length=int(self.headers.get('Content-Length','0'))
        if length>4096: self.send_error(413); return
        form=parse_qs(self.rfile.read(length).decode())
        if form.get('csrf',[''])[0] != self.token: self.send_error(403); return
        oid=form.get('id',[''])[0]
        if self.path=='/api/batch':
            run_id=start_batch(form.get('kind',[''])[0])
            self.send_json({'run':run_id} if run_id else {'error':'当前不足两项可批量处理，或已有批量任务正在运行'}, 200 if run_id else 409)
            return
        if self.path=='/claim': claim(oid)
        elif self.path=='/schedule': result=schedule_offer(oid)
        elif self.path=='/unschedule': result=unschedule_offer(oid)
        elif self.path=='/dismiss':
            if oid not in {product_key(offer) for offer in catalog()}: self.send_error(400); return
            state=load_state()
            state['suppressed_products']=sorted(set(state['suppressed_products'])|{oid})
            for offer in catalog():
                if product_key(offer)==oid and offer['id'] in state['schedules']:
                    state['schedules'][offer['id']]['enabled']=False
            save_state(state)
        elif self.path=='/restore':
            state=load_state(); state['dismissed']=[]; state['suppressed_products']=[]; save_state(state)
        else: self.send_error(404); return
        location='/?'+urlencode({'notice':oid,'result':result['status']}) if self.path in ('/schedule','/unschedule') else '/'
        self.send_response(303); self.send_header('Location',location); self.end_headers()

def main():
    p=argparse.ArgumentParser(); p.add_argument('command',choices=['scan','status','claim','run-due','serve']); p.add_argument('id',nargs='?'); p.add_argument('--port',type=int,default=8765); args=p.parse_args()
    if args.command=='scan': print(json.dumps(scan(),ensure_ascii=False,indent=2))
    elif args.command=='status': print(json.dumps(dashboard(),ensure_ascii=False,indent=2))
    elif args.command=='claim': print(json.dumps(claim(args.id),ensure_ascii=False))
    elif args.command=='run-due':
        state=load_state()
        available={x['id']:x for x in catalog()}
        due=[]
        for oid,schedule in state['schedules'].items():
            if not schedule.get('enabled'): continue
            offer=available.get(oid)
            if not offer or product_key(offer) in state['suppressed_products'] or not auto_adapter_available(offer):
                schedule['enabled']=False
                schedule['disabled_at']=now()
                state['notices'][oid]='活动已过期、核验失效或自动领取适配器不可用，已暂停定时领取。'
                continue
            duplicate=external_claim_task(offer)
            if duplicate:
                schedule['enabled']=False
                schedule['disabled_at']=now()
                state['notices'][oid]=f'发现 {duplicate["agent"]} 已启用“{duplicate["name"]}”；本助手的重复代领任务已暂停。'
                continue
            due.append(oid)
        save_state(state)
        for oid in due: print(oid,json.dumps(claim(oid,True),ensure_ascii=False))
    elif args.command=='serve':
        with ThreadingHTTPServer(('127.0.0.1',args.port),Handler) as server: server.serve_forever()
if __name__=='__main__': main()
