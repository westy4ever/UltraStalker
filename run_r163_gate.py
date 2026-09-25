# -*- coding: utf-8 -*-
"""Current Search-history visibility + Web manager identity regression gate."""
from __future__ import print_function
import os, sys
ROOT=os.path.abspath(os.path.dirname(__file__))
def read(name):
    with open(os.path.join(ROOT,name),'r',encoding='utf-8') as h:return h.read()
def main():
    failures=[]
    skin=read('ui_skin_templates.py'); search=read('ui_screens_search.py')
    web=read('web/cleaner.html'); webpy=read('webcleaner.py')
    if 'name="history_inline_list"' not in skin:
        failures.append('Search history list widget is not declared in SEARCH_SKIN')
    if 'SettingsInlineChoiceOverlay' not in search or 'history_inline_list' not in search or 'action_priority=-20000' not in search:
        failures.append('Search history modal overlay wiring missing')

    # R164 deliberately restored the V7-proven saved-key identity flow.  Keep
    # the serialized key in the in-memory <select> value so reorder/rename does
    # not accidentally target a different connection by numeric position.
    if 'o.value=JSON.stringify(r.key)' not in web:
        failures.append('Web editor saved-key option identity missing')
    if 'managerConnections.find(r=>JSON.stringify(r.key)===old)' not in web:
        failures.append('Web editor saved-key restore lookup missing')
    if "managerConnections.find(r=>JSON.stringify(r.key)===$('managerSelect').value)" not in web:
        failures.append('Web editor current selection is not resolved by saved key')
    if 'MutationObserver' in webpy:
        failures.append('recursive Web DOM MutationObserver returned')
    if failures:
        print('R163/CURRENT GATE: FAIL')
        for f in failures:print(' - '+f)
        return 1
    print('R163/CURRENT GATE: PASS')
    print(' - Search history has a real skinned modal list')
    print(' - Web editor uses stable saved-key identity, not positional identity')
    print(' - recursive DOM observer remains disabled')
    return 0
if __name__=='__main__':sys.exit(main())
