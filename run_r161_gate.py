# -*- coding: utf-8 -*-
"""Current Player-style Search-history overlay regression gate.

Historical filename retained so older packaging scripts keep working.
"""
from __future__ import print_function
import os, sys
ROOT=os.path.abspath(os.path.dirname(__file__))
def read(name):
    with open(os.path.join(ROOT,name),'r',encoding='utf-8') as h:return h.read()
def main():
    failures=[]
    search=read('ui_screens_search.py'); inline=read('ui_settings_inline_choice.py'); skin=read('ui_skin_templates.py')
    required=(
        'SettingsInlineChoiceOverlay',
        'left_x=self._history_status_left()',
        'anchor_bottom=654',
        'min_card_w=180, max_card_w=520, padding=38',
        'visual_style="category", row_h=70, max_visible=5',
        'adaptive_accent=self._history_green_accent',
        'self._history_green_accent = "#32D57B"',
        '560 + max(0, width) + 15',
        'fixed_master_r63", "utility_row_selected.png"',
    )
    for token in required:
        if token not in search:failures.append('missing current Search history contract: '+token)
    if 'left_x=None' not in inline or 'if left_x is not None:' not in inline:
        failures.append('shared inline overlay lost exact left-edge anchoring')
    if 'name="history_inline_list"' not in skin:
        failures.append('Search history list widget is not declared in SEARCH_SKIN')
    if 'search_history_green_300x40.png' in search or 'search_history_green_selected_300x40.png' in search:
        failures.append('obsolete bespoke Search-history surfaces returned')
    if 'amap.execEnd()' not in search or 'keyboard.onClose.append(cleanup)' not in search:
        failures.append('keyboard MENU lifecycle cleanup lost')
    if failures:
        print('R161/CURRENT GATE: FAIL')
        for f in failures:print(' - '+f)
        return 1
    print('R161/CURRENT GATE: PASS')
    print(' - Search history uses shared inline renderer, five 70px rows and fixed green master')
    print(' - overlay follows rendered Search status with the current 15px gap')
    print(' - modal/widget lifecycle cleanup retained')
    return 0
if __name__=='__main__':sys.exit(main())
