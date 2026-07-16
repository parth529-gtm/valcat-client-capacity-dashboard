#!/usr/bin/env python3
"""Generate and optionally publish Valcat client capacity dashboard.

Source of truth: compact Valcat state files under /opt/data/home/workspace/valcat/state/clients
and /opt/data/home/workspace/valcat/state/agents/otto.md. This intentionally avoids live paid/API calls;
other Valcat agents update the state files, and this dashboard reflects them every run.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path('/opt/data/home/workspace/valcat')
OUT = ROOT / 'artifacts' / 'client-capacity-dashboard'
STATE = ROOT / 'state'

CLIENTS = [
    ('Achievee', 'Zara', STATE / 'clients' / 'achievee.md'),
    ('Bits In Glass', 'Morgan', STATE / 'clients' / 'bits-in-glass.md'),
    ('Disprz', 'Dev', STATE / 'clients' / 'disprz.md'),
    ('Klaar', 'Kira', STATE / 'clients' / 'klaar.md'),
    ('Pictory', 'Piper', STATE / 'clients' / 'pictory.md'),
    ('Matters.ai', 'Mateo', STATE / 'clients' / 'matters-ai.md'),
    ('Valcat', 'Avery/Sage', STATE / 'clients' / 'valcat.md'),
    ('Teqtivity', 'Avery', STATE / 'clients' / 'teqtivity.md'),
]

SECTION_RE = re.compile(r'^##\s+(.+?)\s*$', re.M)


def read(path: Path) -> str:
    return path.read_text(encoding='utf-8', errors='replace') if path.exists() else ''


def section(text: str, name: str) -> str:
    matches = list(SECTION_RE.finditer(text))
    for i, m in enumerate(matches):
        if m.group(1).strip().lower() == name.lower():
            start = m.end()
            end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
            return text[start:end].strip()
    return ''


def clean(md: str) -> str:
    md = re.sub(r'`([^`]+)`', r'\1', md)
    md = re.sub(r'\[(.*?)\]\((.*?)\)', r'\1', md)
    md = re.sub(r'^\s*[-*]\s*', '', md, flags=re.M)
    md = re.sub(r'\s+', ' ', md).strip()
    return md


def sentences(text: str) -> list[str]:
    s = clean(text)
    # State files mostly use semicolon-heavy bullets; split conservatively.
    parts = re.split(r'(?<=[.!?])\s+(?=(?:[A-Z0-9`]|SalesRobot|Instantly|VAL|Owned|No|Current))', s)
    out = []
    for p in parts:
        p = p.strip(' -')
        if 18 <= len(p) <= 520:
            out.append(p)
    return out


def first_matching(parts: list[str], patterns: list[str], fallback: str = 'Not found in current state.') -> str:
    # State files are maintained newest-first. Preserve that ordering instead of
    # choosing the longest/most numeric historical line, so the dashboard tracks
    # the latest operator state.
    for pat in patterns:
        rx = re.compile(pat, re.I)
        for p in parts:
            if rx.search(p):
                return p
    return fallback


def status_for(text: str, client: str) -> str:
    cur = section(text, 'Current Status').lower()
    blockers = section(text, 'Blockers / Risks').lower()
    combo = cur + ' ' + blockers
    if client == 'Teqtivity' or 'onboarding' in combo and 'not launched' in combo:
        return 'ONBOARDING'
    critical_terms = ['0 not-contacted', '0 nc', '0.0d', 'critically', 'severely', 'under-buffered', 'under-buffer', 'dry', '<1 day', '<1d', 'less than 1 day']
    if any(t in combo for t in critical_terms):
        return 'CRITICAL'
    high_terms = ['below target', 'short', 'gap', 'blocked', 'empty', 'under half a day']
    if any(t in combo for t in high_terms):
        return 'HIGH'
    return 'WATCH'


def channel_for(text: str, client: str) -> str:
    cur = section(text, 'Current Status')
    c = cur.lower()
    if client == 'Teqtivity':
        return 'Not launched'
    has_inst = 'instantly' in c or 'email' in c
    has_sr = 'salesrobot' in c or 'linkedin' in c
    if has_inst and has_sr:
        if (
            'salesrobot is out of scope' in c
            or 'salesrobot still has 0 active' in c
            or 'salesrobot has 0 active' in c
            or 'salesrobot is not configured' in c
            or 'salesrobot is not achievee-configured' in c
            or 'salesrobot is not configured for' in c
        ):
            return 'Instantly/email; SalesRobot inactive or out of scope'
        return 'Instantly + SalesRobot'
    if has_inst:
        return 'Instantly/email'
    if has_sr:
        return 'SalesRobot/LinkedIn'
    return 'TBD'


def client_record(name: str, owner: str, path: Path) -> dict:
    text = read(path)
    cur = section(text, 'Current Status')
    blocks = section(text, 'Blockers / Risks')
    next_action = section(text, 'Next Action')
    artifacts = section(text, 'Artifacts')
    last = section(text, 'Last Updated')
    parts = sentences(cur)

    capacity = first_matching(parts, [r'capacity|daily[_ -]?limit|/day|caps?'])
    utilization = first_matching(parts, [r'utili[sz]ation|sent on|sends? on|demand|%'])
    runway = first_matching(parts, [r'runway|not[- ]contacted|\bNC\b|lead gap|\bgap\b|dry|0\.0d'])

    if name == 'Teqtivity':
        capacity = 'TBD — onboarding; no outbound campaign capacity has been set yet.'
        utilization = 'No campaign execution yet.'
        runway = 'Not applicable until Hiren provides data/access and campaigns are built.'
    elif utilization == 'Not found in current state.' and re.search(r'under-buffer|dry|0\.\d+d|not-contacted|gap', cur, re.I):
        utilization = 'No separate utilization number in latest state; latest status says active capacity is under-buffered / campaigns are dry.'

    rec = {
        'client': name,
        'owner': owner,
        'status': status_for(text, name),
        'channel': channel_for(text, name),
        'daily_capacity': capacity,
        'utilization': utilization,
        'runway': runway,
        'blockers': clean(blocks)[:850] or 'None listed.',
        'next': clean(next_action)[:550] or 'No next action listed.',
        'current_status': clean(cur)[:1200],
        'last_updated_state': clean(last),
        'state_file': str(path),
        'artifact_hint': clean(artifacts)[:650],
    }
    return rec


def build_data() -> dict:
    clients = [client_record(n, o, p) for n, o, p in CLIENTS]
    summary = {
        'updated': datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC'),
        'source': 'Valcat state files on Hermes VPS; auto-regenerated by GitHub Pages publisher cron',
        'critical_count': sum(c['status'] == 'CRITICAL' for c in clients),
        'high_count': sum(c['status'] == 'HIGH' for c in clients),
        'watch_count': sum(c['status'] == 'WATCH' for c in clients),
        'onboarding_count': sum(c['status'] == 'ONBOARDING' for c in clients),
    }
    return {'summary': summary, 'clients': clients}


HTML = r'''<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Valcat Client Capacity Dashboard</title>
<style>:root{--muted:#8ca3c7;--text:#eef5ff;--line:#203450;--blue:#5fa8ff}*{box-sizing:border-box}html,body{max-width:100%;overflow-x:hidden}body{margin:0;font-family:Inter,ui-sans-serif,system-ui,-apple-system,Segoe UI,Arial;background:radial-gradient(circle at top left,#17365d 0,#07111f 36%,#050a12 100%);color:var(--text)}.wrap{width:100%;max-width:100vw;margin:0;padding:28px 14px 54px;overflow-x:hidden}.top{display:flex;gap:22px;justify-content:space-between;align-items:flex-start;flex-wrap:wrap}.eyebrow{letter-spacing:.12em;text-transform:uppercase;color:var(--blue);font-size:12px;font-weight:700}.title{font-size:42px;line-height:1.05;margin:8px 0 10px}.sub{color:var(--muted);max-width:820px;font-size:16px;line-height:1.5}.stamp{background:rgba(255,255,255,.06);border:1px solid var(--line);border-radius:16px;padding:14px 16px;color:var(--muted);min-width:0;max-width:100%;overflow-wrap:anywhere}.metrics{display:grid;grid-template-columns:repeat(auto-fit,minmax(140px,1fr));gap:14px;margin:28px 0}.metric{background:rgba(15,27,46,.78);border:1px solid var(--line);border-radius:18px;padding:18px;box-shadow:0 18px 50px rgba(0,0,0,.22)}.metric .num{font-size:32px;font-weight:800}.metric .lbl{color:var(--muted);font-size:13px;margin-top:5px}.toolbar{display:flex;gap:10px;align-items:center;margin:18px 0;flex-wrap:wrap}.search{flex:1;min-width:0;width:100%;background:#091424;border:1px solid var(--line);color:var(--text);border-radius:14px;padding:13px 14px;font-size:14px}.pill{border:1px solid var(--line);border-radius:999px;background:#0d1829;color:var(--muted);padding:10px 13px;cursor:pointer}.pill.active{background:#173a62;color:#fff;border-color:#316fae}.grid{display:grid;grid-template-columns:1fr;gap:16px}.card{background:linear-gradient(180deg,rgba(18,33,55,.96),rgba(10,19,33,.96));border:1px solid var(--line);border-radius:22px;padding:18px;box-shadow:0 20px 60px rgba(0,0,0,.25)}.head{display:flex;justify-content:space-between;gap:12px;align-items:flex-start}.client{font-size:24px;font-weight:800}.owner{color:var(--muted);font-size:13px;margin-top:3px}.badge{font-size:12px;font-weight:800;letter-spacing:.04em;border-radius:999px;padding:7px 10px;white-space:nowrap}.CRITICAL{background:rgba(255,93,108,.14);color:#ff8b96;border:1px solid rgba(255,93,108,.35)}.HIGH{background:rgba(255,191,77,.14);color:#ffd17e;border:1px solid rgba(255,191,77,.35)}.WATCH{background:rgba(71,209,140,.14);color:#91e4ba;border:1px solid rgba(71,209,140,.35)}.ONBOARDING{background:rgba(95,168,255,.14);color:#9bc9ff;border:1px solid rgba(95,168,255,.35)}.rows{display:grid;gap:11px;margin-top:16px}.row{border-top:1px solid rgba(255,255,255,.08);padding-top:11px}.k{color:var(--muted);font-size:12px;text-transform:uppercase;letter-spacing:.08em;font-weight:700}.v{margin-top:4px;line-height:1.42;overflow-wrap:anywhere;word-break:normal}.next{margin-top:14px;padding:13px;border-radius:14px;background:rgba(95,168,255,.08);border:1px solid rgba(95,168,255,.18)}details{margin-top:12px;color:#c9d8ee}summary{cursor:pointer;color:#9bc9ff}code{color:#a9c7ff;font-size:12px;word-break:break-all}footer{color:var(--muted);font-size:12px;margin-top:26px}@media(max-width:900px){.metrics{grid-template-columns:repeat(2,1fr)}.grid{grid-template-columns:1fr}.title{font-size:34px}}@media(max-width:520px){.metrics{grid-template-columns:1fr}}</style></head><body><div class="wrap"><div class="top"><div><div class="eyebrow">Valcat internal</div><h1 class="title">Client Capacity Dashboard</h1><div class="sub">Daily capacity, runway, utilization, blockers, source state file, and next owner action across all active/onboarding clients. Auto-refreshes from Hermes state every 2 hours, Monday-Friday.</div></div><div class="stamp"><b>Last updated</b><br><span id="updated"></span><br><br><b>Source</b><br><span id="source"></span></div></div><div class="metrics"><div class="metric"><div class="num" id="total"></div><div class="lbl">clients tracked</div></div><div class="metric"><div class="num" id="critical"></div><div class="lbl">critical</div></div><div class="metric"><div class="num" id="high"></div><div class="lbl">high</div></div><div class="metric"><div class="num" id="watch"></div><div class="lbl">watch</div></div><div class="metric"><div class="num" id="onboarding"></div><div class="lbl">onboarding</div></div></div><div class="toolbar"><input class="search" id="q" placeholder="Search client, blocker, owner…"><button class="pill active" data-filter="ALL">All</button><button class="pill" data-filter="CRITICAL">Critical</button><button class="pill" data-filter="HIGH">High</button><button class="pill" data-filter="WATCH">Watch</button><button class="pill" data-filter="ONBOARDING">Onboarding</button></div><div class="grid" id="cards"></div><footer>Confidential Valcat operating dashboard. Public GitHub Pages link for Parth QA; do not share outside approved internal workflow.</footer></div><script>async function boot(){const r=await fetch('./data.json',{cache:'no-store'});const d=await r.json();window.DATA=d;updated.textContent=d.summary.updated;source.textContent=d.summary.source;total.textContent=d.clients.length;critical.textContent=d.summary.critical_count;high.textContent=d.summary.high_count;watch.textContent=d.summary.watch_count;onboarding.textContent=d.summary.onboarding_count;render()}let filt='ALL';function esc(s){return String(s||'').replace(/[&<>]/g,m=>({'&':'&amp;','<':'&lt;','>':'&gt;'}[m]))}function row(k,v){return `<div class="row"><div class="k">${k}</div><div class="v">${esc(v)}</div></div>`}function render(){const q=document.getElementById('q').value.toLowerCase();const cards=document.getElementById('cards');cards.innerHTML='';DATA.clients.filter(c=>(filt==='ALL'||c.status===filt)&&JSON.stringify(c).toLowerCase().includes(q)).forEach(c=>{const el=document.createElement('article');el.className='card';el.innerHTML=`<div class="head"><div><div class="client">${esc(c.client)}</div><div class="owner">Owner: ${esc(c.owner)} · ${esc(c.channel)}</div></div><div class="badge ${c.status}">${c.status}</div></div><div class="rows">${row('Daily capacity',c.daily_capacity)}${row('Utilization',c.utilization)}${row('Runway / gap',c.runway)}${row('Critical blockers',c.blockers)}</div><div class="next"><div class="k">Next action</div><div class="v">${esc(c.next)}</div></div><details><summary>QA source evidence</summary><p>${esc(c.current_status)}</p><p><b>State file:</b><br><code>${esc(c.state_file)}</code></p><p><b>Last state update:</b> ${esc(c.last_updated_state)}</p></details>`;cards.appendChild(el)})}document.addEventListener('click',e=>{if(e.target.matches('.pill')){document.querySelectorAll('.pill').forEach(b=>b.classList.remove('active'));e.target.classList.add('active');filt=e.target.dataset.filter;render()}});document.getElementById('q').addEventListener('input',render);boot();</script></body></html>'''


def write_dashboard() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    data = build_data()
    (OUT / 'data.json').write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding='utf-8')
    (OUT / 'index.html').write_text(HTML, encoding='utf-8')
    (OUT / 'README.md').write_text('# Valcat Client Capacity Dashboard\n\nStatic GitHub Pages dashboard generated from Valcat Hermes state files.\n', encoding='utf-8')


def run(cmd: list[str], cwd: Path = OUT, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, cwd=str(cwd), text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, check=check)


def publish() -> None:
    token = os.environ.get('GITHUB_TOKEN')
    if token:
        subprocess.run(['gh', 'auth', 'login', '--with-token'], input=token, text=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
        subprocess.run(['gh', 'auth', 'setup-git'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
    run(['git', 'init', '-b', 'main'], check=False)
    run(['git', 'config', 'user.email', 'parth@valcat.co'])
    run(['git', 'config', 'user.name', 'Parth / Hermes'])
    owner = run(['gh', 'api', 'user', '--jq', '.login']).stdout.strip()
    repo = 'valcat-client-capacity-dashboard'
    full = f'{owner}/{repo}'
    if run(['gh', 'repo', 'view', full], check=False).returncode != 0:
        run(['gh', 'repo', 'create', repo, '--public', '--description', 'Valcat client capacity dashboard'])
    if run(['git', 'remote', 'get-url', 'origin'], check=False).returncode != 0:
        run(['git', 'remote', 'add', 'origin', f'https://github.com/{full}.git'])
    run(['git', 'add', 'index.html', 'data.json', 'README.md', 'generate_dashboard.py'])
    diff = run(['git', 'diff', '--cached', '--quiet'], check=False)
    if diff.returncode != 0:
        run(['git', 'commit', '-m', 'Update Valcat client capacity dashboard'])
    run(['git', 'push', '-u', 'origin', 'main'])
    if run(['gh', 'api', f'repos/{full}/pages'], check=False).returncode != 0:
        run(['gh', 'api', '--method', 'POST', f'repos/{full}/pages', '-F', 'source[branch]=main', '-F', 'source[path]=/'])
    else:
        run(['gh', 'api', '--method', 'PUT', f'repos/{full}/pages', '-F', 'source[branch]=main', '-F', 'source[path]=/'], check=False)
    url = run(['gh', 'api', f'repos/{full}/pages', '--jq', '.html_url']).stdout.strip()
    print(json.dumps({'repo': f'https://github.com/{full}', 'pages': url}, indent=2))


def qa() -> None:
    data = json.loads((OUT / 'data.json').read_text(encoding='utf-8'))
    assert len(data['clients']) == 8, data['clients']
    assert data['summary']['critical_count'] >= 4, data['summary']
    for c in data['clients']:
        for key in ['client','owner','status','channel','daily_capacity','utilization','runway','blockers','next','state_file']:
            assert c.get(key), (c['client'], key)
        assert Path(c['state_file']).exists(), c['state_file']
    assert 'Client Capacity Dashboard' in (OUT / 'index.html').read_text(encoding='utf-8')
    print('QA_PASS local: 8 clients, required fields, source files, HTML marker')


def main(argv: list[str]) -> None:
    write_dashboard()
    qa()
    if '--publish' in argv:
        publish()

if __name__ == '__main__':
    main(sys.argv[1:])
