# -*- coding: utf-8 -*-
"""Current federated Search, Web manager and Home recent hierarchy gate."""
from __future__ import print_function
import os, sys
ROOT=os.path.abspath(os.path.dirname(__file__))
def read(name):
    with open(os.path.join(ROOT,name),'r',encoding='utf-8') as h:return h.read()
def main():
    failures=[]
    search=read('ui_screens_search.py'); home=read('ui_screens_home.py')
    skin=read('ui_skin_templates.py'); web=read('web/cleaner.html'); webpy=read('webcleaner.py')

    if 'source_mix={"portal":sum(1 for p in profiles' not in search:
        failures.append('Search source-family accounting missing')
    if 'min(6, jobs.qsize())' not in search or 'for pos, p in enumerate(profiles):' not in search:
        failures.append('Search is not scheduling source families in the same progressive worker wave')
    if 'tagged, err = search_profile(profiles[0]' in search:
        failures.append('legacy serial first-portal Search returned')
    if '_("%d sources")' not in search or 'search_portal_mix' not in search or 'search_xtream_mix' not in search:
        failures.append('Search federated source-count UI missing')

    # Current Home is three resident MultiContent panels, each with three rows.
    if 'self._recent_entries=[None]*9' not in home or 'groups=[[],[],[]];seen_series=set()' not in home:
        failures.append('Home nine-slot recent hierarchy missing')
    for name in ('recent_list0','recent_list1','recent_list2','recent_art0','recent_art1','recent_art2'):
        if ('name="%s"'%name) not in skin:
            failures.append('HOME_SKIN missing resident recent component: '+name)
    if 'for group in range(3):' not in home or 'for row in range(3):' not in home:
        failures.append('Home 3x3 resident recent-list binding missing')
    if 'S%02d' not in home or 'E%02d' not in home:
        failures.append('Home Series copy lost Season/Episode hierarchy')

    if 'MutationObserver' in webpy:
        failures.append('recursive Web DOM MutationObserver remains enabled')
    if 'o.value=JSON.stringify(r.key)' not in web or 'managerConnections.find(r=>JSON.stringify(r.key)===old)' not in web:
        failures.append('V7-proven Web manager saved-key identity flow missing')

    if failures:
        print('R164/CURRENT GATE: FAIL')
        for f in failures:print(' - '+f)
        return 1
    print('R164/CURRENT GATE: PASS')
    print(' - Portal + Xtream/M3U Search sources share one progressive worker wave')
    print(' - Home has 9 recent slots in three resident media panels with Series S/E hierarchy')
    print(' - Web manager uses stable saved-key selection with no recursive DOM observer')
    return 0
if __name__=='__main__':sys.exit(main())
