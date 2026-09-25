# -*- coding: utf-8 -*-
"""Current Search-history / Home-recent / Details-locale regression gate.

Historical filename retained for compatibility with existing release tooling.
The checks below intentionally follow the current R270 architecture rather than
pixel/asset assumptions from the original R160 implementation.
"""
from __future__ import print_function
import os, sys
ROOT=os.path.abspath(os.path.dirname(__file__))
def read(name):
    with open(os.path.join(ROOT,name),'r',encoding='utf-8') as h:return h.read()
def main():
    failures=[]
    search=read('ui_screens_search.py'); home=read('ui_screens_home.py')
    menu=read('ui_icon_menu.py'); authority=read('details_authority.py')

    # Search history is one modal inline overlay with explicit cleanup.
    for token in ('SettingsInlineChoiceOverlay','"history_inline_list"','action_priority=-20000',
                  'row_h=70, max_visible=5','adaptive_accent=self._history_green_accent',
                  'amap.execEnd()','keyboard.onClose.append(cleanup)'):
        if token not in search:failures.append('current Search history contract missing: '+token)

    # Home owns exactly nine recent slots: 3 Live + 3 Movies + 3 distinct Series.
    if 'self._recent_entries=[None]*9' not in home:
        failures.append('Home no longer initializes nine recent slots')
    if 'groups=[[],[],[]];seen_series=set()' not in home or 'if len(groups[group])<3:groups[group].append' not in home:
        failures.append('Home 3x3 recent grouping/dedup contract missing')
    if 'identity=self._recent_series_identity(item,mtype)' not in home or 'if identity and identity in seen_series:continue' not in home:
        failures.append('Home Series recent rows no longer deduplicate by parent series')
    if 'S%02d' not in home or 'E%02d' not in home:
        failures.append('Home Series recent metadata lost season/episode hierarchy')

    if 'status_w = 118 if portal_numbered else 72' not in menu:
        failures.append('Portal Manager selective status-lane width changed')

    # Current Details locale/synopsis authority is centralized and language-aware.
    if 'def details_overview(' not in authority or 'wanted_lang=="ar"' not in authority or '_has_arabic' not in authority:
        failures.append('current Details language/synopsis authority missing')
    if 'def resolve_metadata_fast(' not in authority or 'merged["_details_authority_ready"]=True' not in authority:
        failures.append('current fast Details metadata authority missing')

    if failures:
        print('R160/CURRENT GATE: FAIL')
        for f in failures:print(' - '+f)
        return 1
    print('R160/CURRENT GATE: PASS')
    print(' - modal five-row Search history with lifecycle cleanup')
    print(' - nine Home recent slots with parent-Series dedup and S/E hierarchy')
    print(' - Portal Manager selective status lane retained')
    print(' - current Details locale/synopsis authority retained')
    return 0
if __name__=='__main__':sys.exit(main())
