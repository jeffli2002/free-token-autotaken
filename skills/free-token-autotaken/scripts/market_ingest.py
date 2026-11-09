#!/usr/bin/env python3
"""Safely import offers already verified from vendor-controlled sources."""
import argparse, json, tempfile
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
META = ROOT / 'market_meta.json'
ALLOWED = {'market_catalog.json', 'market_extra.json'}
REQUIRED = {'id','name','vendor','amount','cycle','scope','requirements','official_url','verified_on','verification_state'}

def validate(items):
    if not isinstance(items, list) or not items:
        raise ValueError('输入必须是非空 JSON 活动数组')
    seen=set()
    for item in items:
        missing=REQUIRED-set(item)
        if missing: raise ValueError(f"{item.get('id','?')} 缺字段: {sorted(missing)}")
        url=urlparse(item['official_url'])
        if url.scheme!='https' or not url.hostname or url.hostname=='aihot.news' or url.hostname.endswith('.aihot.news'):
            raise ValueError(f"{item['id']} 必须引用厂商 HTTPS 官方页面，不能引用 AIHOT")
        if item.get('verification_state')!='厂商官方已核实':
            raise ValueError(f"{item['id']} 尚未标注为厂商官方已核实")
        if item['id'] in seen: raise ValueError(f"重复活动 ID: {item['id']}")
        seen.add(item['id'])
    return items

def main():
    p=argparse.ArgumentParser()
    p.add_argument('--input',type=Path,required=True,help='已由厂商官方页面核实的 JSON 活动数组')
    p.add_argument('--target',choices=sorted(ALLOWED),default='market_extra.json')
    args=p.parse_args()
    items=validate(json.loads(args.input.read_text()))
    target=ROOT/args.target
    previous=json.loads(target.read_text()) if target.exists() else []
    by_id={x['id']:x for x in previous}
    by_id.update({x['id']:x for x in items})
    merged=list(by_id.values())
    with tempfile.NamedTemporaryFile('w',encoding='utf-8',dir=ROOT,delete=False) as f:
        json.dump(merged,f,ensure_ascii=False,indent=2); f.write('\n'); temp=Path(f.name)
    temp.replace(target)
    meta=json.loads(META.read_text()) if META.exists() else {}
    meta.update({'official_sources_updated_at':datetime.now(ZoneInfo('Asia/Shanghai')).isoformat(timespec='seconds'),'verified_offer_count':sum(x.get('verification_state')=='厂商官方已核实' for x in json.loads((ROOT/'market_catalog.json').read_text())+json.loads((ROOT/'market_extra.json').read_text())),'method':'直接核对厂商官方页面；AIHOT 仅作为采集方法参考'})
    META.write_text(json.dumps(meta,ensure_ascii=False,indent=2)+'\n')
    print(f'已合并 {len(items)} 项官方核实活动')

if __name__=='__main__': main()
