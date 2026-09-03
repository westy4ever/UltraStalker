# -*- coding: utf-8 -*-
import hashlib, json
from .database import DB
from ..identity import stable_content_identity

def content_key(profile,media_type,item,legacy_episode=False):
    item=item if isinstance(item,dict) else {}
    portal=str((profile or {}).get('portal') or '').rstrip('/').lower()
    mac=str((profile or {}).get('mac') or '').upper()
    mtype=str(media_type or '').lower()
    identity=stable_content_identity(mtype,item,legacy_episode=legacy_episode)
    raw=json.dumps([portal,mac,mtype,identity],ensure_ascii=False,sort_keys=True,separators=(',',':'))
    return hashlib.sha256(raw.encode('utf-8')).hexdigest()

def _episode_signature(item):
    item=item if isinstance(item,dict) else {}
    episode_id=item.get('episode_id')
    series_ref=item.get('series_id') or item.get('_series_id') or item.get('series') or item.get('movie_id') or item.get('series_uid') or item.get('parent_id')
    season=item.get('season') or item.get('season_id') or item.get('season_number')
    episode=item.get('episode') or item.get('number') or item.get('episode_num') or item.get('episode_number')
    if episode_id not in (None,''):
        return ('episode_id',str(episode_id),str(series_ref or ''),str(season or ''),str(episode or ''))
    if series_ref not in (None,'') and (season not in (None,'') or episode not in (None,'')):
        return ('series_tuple',str(series_ref),str(season or ''),str(episode or ''))
    return None

def _legacy_episode_states(profile, limit=5000):
    """Index old history payloads by structural episode identity.

    Us110 included the mutable episode title in its hash. If a portal changes
    translation at the same time as the upgrade, no hash-only fallback can find
    that bookmark. The stored SQLite payload still contains series/season/episode
    fields, so use them once as a migration bridge.
    """
    wanted_portal=str((profile or {}).get('portal') or '').rstrip('/').lower()
    wanted_mac=str((profile or {}).get('mac') or '').upper()
    out={}
    try:rows=DB.list_history(limit=limit)
    except Exception:return out
    for payload in rows:
        if not isinstance(payload,dict) or str(payload.get('media_type') or '').lower()!='episode':continue
        if str(payload.get('portal') or '').rstrip('/').lower()!=wanted_portal:continue
        if str(payload.get('mac') or '').upper()!=wanted_mac:continue
        sig=_episode_signature(payload.get('item') or {})
        if not sig or sig in out:continue
        out[sig]={'position':int(payload.get('_position') or 0),'duration':int(payload.get('_duration') or 0),'completed':bool(payload.get('_completed')),'content_key':payload.get('_content_key')}
    return out

class FavoritesRepository:
    def list(self,limit=500): return DB.list_payloads('favorites',limit)
    def toggle(self,profile,media_type,item):
        payload={'portal':profile.get('portal',''),'mac':profile.get('mac',''),'media_type':str(media_type or ''),'item':item}
        return DB.toggle_favorite(content_key(profile,media_type,item),payload['portal'],payload['media_type'],payload)
    def contains(self,profile,media_type,item): return DB.is_favorite(content_key(profile,media_type,item))
    def states(self,profile,media_type,items):
        items=list(items or [])
        keys=[content_key(profile,media_type,item) for item in items]
        legacy_sets=[]
        if str(media_type or '').lower()=='episode':
            legacy_sets=[[content_key(profile,media_type,item,legacy_episode=variant) for item in items] for variant in ('us110',True)]
        all_keys=list(keys)
        for group in legacy_sets:all_keys.extend(group)
        states=DB.content_states(all_keys)
        structural=_legacy_episode_states(profile) if legacy_sets else {}
        out=[]
        for idx,key in enumerate(keys):
            value=dict(states.get(key,{'favorite':False,'position':0,'duration':0,'completed':0}))
            for group in legacy_sets:
                old=states.get(group[idx],{})
                if not value.get('favorite') and old.get('favorite'):value['favorite']=True
                if not value.get('position') and old.get('position'):
                    value['position']=old.get('position',0);value['duration']=old.get('duration',0);value['completed']=old.get('completed',0)
            if legacy_sets and not (value.get('position') or value.get('completed')):
                old=structural.get(_episode_signature(items[idx]),{})
                if old:
                    value['position']=old.get('position',0);value['duration']=old.get('duration',0);value['completed']=old.get('completed',0)
            out.append(value)
        return out
class HistoryRepository:
    def list(self,limit=500): return DB.list_history(limit=limit)
    def continue_list(self,limit=500): return DB.list_history(limit=limit,continue_only=True)
    def incomplete_list(self,limit=500): return DB.list_history(limit=limit,include_completed=False)
    def save(self,profile,media_type,item,position=0,duration=0,completed=False,force=False):
        payload={'portal':profile.get('portal',''),'mac':profile.get('mac',''),'media_type':str(media_type or ''),'item':item}
        DB.add_history(content_key(profile,media_type,item),payload['portal'],payload['media_type'],payload,position,duration,completed,force=force)
    def touch(self,profile,media_type,item):
        payload={'portal':profile.get('portal',''),'mac':profile.get('mac',''),'media_type':str(media_type or ''),'item':item}
        DB.touch_history(content_key(profile,media_type,item),payload['portal'],payload['media_type'],payload)
    def progress(self,profile,media_type,item):
        key=content_key(profile,media_type,item);value=DB.progress(key)
        if str(media_type or '').lower()=='episode' and not (int(value.get('position') or 0) or int(value.get('completed') or 0)):
            for variant in ('us110',True):
                old_key=content_key(profile,media_type,item,legacy_episode=variant)
                if old_key==key:continue
                legacy=DB.progress(old_key)
                if int(legacy.get('position') or 0) or int(legacy.get('completed') or 0):
                    self.save(profile,media_type,item,legacy.get('position',0),legacy.get('duration',0),legacy.get('completed',False))
                    try:DB.remove_history(old_key)
                    except Exception:pass
                    return legacy
            # Last-resort us110 migration when the portal translated/renamed
            # the episode: compare the stored payload's structural IDs instead
            # of its obsolete title-bearing hash.
            structural=_legacy_episode_states(profile)
            legacy=structural.get(_episode_signature(item),{})
            if legacy and (int(legacy.get('position') or 0) or int(legacy.get('completed') or 0)):
                self.save(profile,media_type,item,legacy.get('position',0),legacy.get('duration',0),legacy.get('completed',False))
                old_key=legacy.get('content_key')
                if old_key and old_key!=key:
                    try:DB.remove_history(old_key)
                    except Exception:pass
                return {'position':legacy.get('position',0),'duration':legacy.get('duration',0),'completed':int(bool(legacy.get('completed')))}
        return value
    def mark_watched(self,profile,media_type,item,watched=True):
        self.touch(profile,media_type,item)
        DB.set_history_completed(content_key(profile,media_type,item),watched)
    def remove(self,profile,media_type,item): DB.remove_history(content_key(profile,media_type,item))
    def clear(self,profile=None): DB.clear_history((profile or {}).get('portal') if profile else None,(profile or {}).get('mac') if profile else None)
FAVORITES=FavoritesRepository(); HISTORY=HistoryRepository()
