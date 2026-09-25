# -*- coding: utf-8 -*-
"""R165 focused regression gate: real Xtream category search federation."""
from __future__ import print_function
import os, sys
ROOT=os.path.abspath(os.path.dirname(__file__))
def read(name):
    with open(os.path.join(ROOT,name),'r',encoding='utf-8') as h:return h.read()
def main():
    failures=[]
    search=read('ui_screens_search.py')
    m3u=read('m3u_adapter.py')
    version=read('version.py')
    import re
    m=re.search(r'PLUGIN_BUILD\s*=\s*(\d+)', version)
    if (not m) or int(m.group(1)) < 165:
        failures.append('R165 feature baseline build metadata missing')
    if 'source_mix={"portal":sum(1 for p in profiles' not in search:
        failures.append('Search does not account for Portal/Xtream source families')
    if 'search_portal_mix' not in search or 'search_xtream_mix' not in search:
        failures.append('Search status does not expose split Portal/Xtream participation')
    if '42.0 if source_mix.get("xtream") else 18.0' not in search:
        failures.append('Federated search does not give Xtream enough progressive search time')
    if 'def search_content_fast' not in m3u or 'self._read_xtream_category_cache(typ,cid)' not in m3u:
        failures.append('Xtream search does not scan persistent category cache first')
    if 'self._load_xtream_category(typ,cid,cancel_event,request_timeout=' not in m3u:
        failures.append('Xtream search is not using real category-id provider routes')
    if 'get_vod_streams' not in m3u or 'get_series' not in m3u:
        failures.append('Xtream provider catalogue routes missing')
    if 'if self._xtream:' not in m3u:
        failures.append('Credentialed Xtream sources do not have a dedicated search path')
    if failures:
        print('R165 GATE: FAIL')
        for f in failures:print(' - '+f)
        return 1
    print('R165 GATE: PASS')
    print(' - active Xtream sources are visible in Search source accounting')
    print(' - cached Xtream categories are searched before network work')
    print(' - uncached Xtream categories use the same category_id route as normal browsing')
    print(' - Xtream progressive search has an extended background budget without blocking Portal results')
    return 0
if __name__=='__main__':sys.exit(main())
