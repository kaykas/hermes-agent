import json, time
from datetime import datetime
from pathlib import Path
from gateway.control_socket import reload_gateway_plugins, request_unserve_profile, request_serve_profile_hot
home=Path('/Users/jkw/.hermes')
profiles=['carlton','cedar','halo','heath','hendrick','icculus','jeter','nova2','pippin','verve']
started=datetime.now()
results=[]
for name in profiles:
    profile=home/'profiles'/name
    reload_result=reload_gateway_plugins(home,profile_home=profile)
    unserve=request_unserve_profile(home,name)
    serve=request_serve_profile_hot(home,name)
    results.append({'profile':name,'reloaded':bool((reload_result or {}).get('reloaded')),'unserved':(unserve or {}).get('unserved')==name,'serve_pending':bool((serve or {}).get('pending'))})
time.sleep(15)
for item in results:
    path=home/'profiles'/item['profile']/'logs'/'gateway.log'
    connected=None; errors=[]
    if path.exists():
        lines=path.read_text(errors='replace').splitlines()[-400:]
        for line in lines:
            if 'Socket Mode connected' in line: connected=line
            if 'Unhandled request' in line or 'operator.link' in line and '404' in line: errors.append(line)
    fresh=False
    if connected:
        try: fresh=datetime.strptime(connected[:19],'%Y-%m-%d %H:%M:%S') >= started
        except ValueError: pass
    item['socket_connected_fresh']=fresh
    item['latest_socket_log']=connected[:80] if connected else None
    item['new_unhandled_errors']=len(errors)
out={'started':started.isoformat(timespec='seconds'),'profiles':results,'all_verified':all(x['reloaded'] and x['unserved'] and x['serve_pending'] and x['socket_connected_fresh'] and x['new_unhandled_errors']==0 for x in results)}
Path('/Users/jkw/.hermes/artifacts/json-render/slack-fleet-rollout.json').write_text(json.dumps(out,indent=2))
print(json.dumps({'all_verified':out['all_verified'],'profiles':[{k:x[k] for k in ('profile','reloaded','socket_connected_fresh','new_unhandled_errors')} for x in results]}))
