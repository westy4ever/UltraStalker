# -*- coding: utf-8 -*-
"""Ultra Stalker single global TMDB media library (Beta55+).

One verified TMDB identity owns one persistent record under /media/hdd/UltraStalker/library.
Portal/profile/category placement stores pointers only. Quality remains stream-local.
"""
from __future__ import absolute_import
import hashlib,json,os,re,threading,time
from contextlib import contextmanager
try:
    from PIL import Image as _PILImage, ImageOps as _PILImageOps
except Exception:
    _PILImage=None;_PILImageOps=None
from .persistent_cache import ROOT,POSTERS,BACKDROPS,INDEX,LOOKUP,hdd_ready,hdd_read_ready,ensure_persistent_dirs,persistent_write_gate
from .title_clean import catalogue_title
from .log import diagnostic_failure
from .core.image_budget import image_budgeted

SCHEMA=62

_ITEM_LOCKS={}
_ITEM_LOCKS_GUARD=threading.RLock()
_ADAPTIVE_READ_CACHE={}
_ADAPTIVE_READ_CACHE_LOCK=threading.RLock()
_ADAPTIVE_READ_CACHE_LIMIT=512

@contextmanager
def item_write_lock(kind,tmdb_id):
    """Serialize all writes for one global TMDB identity without retaining idle locks."""
    key=(media_type(kind),int(tmdb_id))
    with _ITEM_LOCKS_GUARD:
        entry=_ITEM_LOCKS.get(key)
        if entry is None:
            entry=[threading.RLock(),0];_ITEM_LOCKS[key]=entry
        entry[1]+=1;lock=entry[0]
    lock.acquire()
    try:
        yield
    finally:
        lock.release()
        with _ITEM_LOCKS_GUARD:
            entry=_ITEM_LOCKS.get(key)
            if entry is not None:
                entry[1]-=1
                if entry[1]<=0:_ITEM_LOCKS.pop(key,None)

def _fsync_parent(path):
    try:
        fd=os.open(os.path.dirname(path) or ".",os.O_RDONLY)
        try:os.fsync(fd)
        finally:os.close(fd)
    except Exception as exc:
        diagnostic_failure("media_library.fsync_parent",exc)

_FINAL_FIELDS=("overview","cast","directors","writers","genres","runtime","country","year")

def media_type(value):return "tv" if str(value or "").strip().lower() in ("tv","series","show","episode") else "movie"
def _item_stem(kind,tmdb_id):return "%s_%s"%(media_type(kind),int(tmdb_id))
def item_dir(kind,tmdb_id):return ROOT
def item_paths(kind,tmdb_id):
    stem=_item_stem(kind,tmdb_id)
    return {
        "dir":ROOT,
        "poster":os.path.join(POSTERS,stem+".jpg"),
        "backdrop":os.path.join(BACKDROPS,stem+".jpg"),
        "metadata":os.path.join(INDEX,stem+".json"),
        "manifest":os.path.join(INDEX,stem+".json"),
        "adaptive":os.path.join(INDEX,stem+"_adaptive.json"),
    }

def _legacy_item_paths(kind,tmdb_id):
    mt=media_type(kind);tid=int(tmdb_id);base=os.path.join(ROOT,"library","%s-%s"%(mt,tid))
    return {"dir":base,"poster":os.path.join(base,"poster.jpg"),"backdrop":os.path.join(base,"backdrop.jpg"),"metadata":os.path.join(base,"metadata.json"),"adaptive":os.path.join(base,"adaptive.json")}

def _promote_legacy_file(source,target):
    """Adopt one old per-title file into the fixed flat store without making a new folder."""
    if not source or not target or not os.path.isfile(source):return ""
    if os.path.isfile(target):return target
    if not hdd_ready():return source
    try:
        ensure_persistent_dirs(os.path.dirname(target))
        try:os.replace(source,target)
        except Exception:
            import shutil
            shutil.copy2(source,target)
            try:os.unlink(source)
            except Exception:pass
        try:
            parent=os.path.dirname(source)
            if parent and os.path.isdir(parent) and not os.listdir(parent):os.rmdir(parent)
        except Exception:pass
        return target if os.path.isfile(target) else source
    except Exception:return source

def _resolved_item_paths(kind,tmdb_id):
    paths=item_paths(kind,tmdb_id);legacy=_legacy_item_paths(kind,tmdb_id)
    for role in ("poster","backdrop","metadata","adaptive"):
        if not os.path.isfile(paths[role]) and os.path.isfile(legacy[role]):
            adopted=_promote_legacy_file(legacy[role],paths[role])
            if adopted and os.path.isfile(adopted) and adopted!=paths[role] and not os.path.isfile(paths[role]):
                paths[role]=adopted
    return paths

def _atomic_json(path,payload):
    if not hdd_ready() or not ensure_persistent_dirs(os.path.dirname(path)):return False
    temp="%s.tmp.%d.%d"%(path,os.getpid(),threading.get_ident())
    try:
        if not persistent_write_gate(temp):return False
        with open(temp,"w",encoding="utf-8") as h:json.dump(payload,h,ensure_ascii=False,separators=(",",":"));h.flush();os.fsync(h.fileno())
        if not persistent_write_gate(path):return False
        os.replace(temp,path);_fsync_parent(path);return True
    except Exception as exc:
        diagnostic_failure("media_library.atomic_json",exc)
        try:
            if os.path.exists(temp) and persistent_write_gate(temp):os.unlink(temp)
        except Exception as cleanup_exc:diagnostic_failure("media_library.atomic_json_cleanup",cleanup_exc)
        return False

def load(kind,tmdb_id):
    if not tmdb_id or not hdd_read_ready():return {}
    paths=_resolved_item_paths(kind,tmdb_id);out={}
    for name in ("metadata","adaptive"):
        try:
            with open(paths[name],"r",encoding="utf-8") as h:row=json.load(h)
            if isinstance(row,dict):out.update(row)
        except (OSError,ValueError,TypeError):pass
    for _cast_key in ("cast_profiles","cast_art_ready","cast_art_unavailable"):
        out.pop(_cast_key,None)
    out.update({"schema":SCHEMA,"media_type":media_type(kind),"tmdb_id":int(tmdb_id),"library_dir":paths["dir"]})
    out["poster_local"]=paths["poster"] if os.path.isfile(paths["poster"]) and os.path.getsize(paths["poster"])>1024 else None
    out["backdrop_local"]=paths["backdrop"] if os.path.isfile(paths["backdrop"]) and os.path.getsize(paths["backdrop"])>1024 else None
    return out

def _meaningful(value):return value not in (None,"",{},[])
def _arabic(value):return any("\u0600"<=ch<="\u06ff" for ch in str(value or ""))
def _prefer_scalar(key,current,value,new):
    # Stage 3: descriptive TMDb metadata is never chosen by script/length.
    # Title/name identity remains separate from the user-visible Clean Names
    # setting, but overview must accept the latest verified raw TMDb value.
    if key=="overview":
        return value if (new.get("_metadata_complete") or new.get("identity_verified") or new.get("_replace_localized_metadata")) else current
    if key in ("title","name"):
        if len(str(value or ""))>len(str(current or "")):return value
        return current
    return value if (new.get("_metadata_complete") or new.get("identity_verified")) else current

def _merge_metadata(old,new):
    old=old if isinstance(old,dict) else {};new=new if isinstance(new,dict) else {};merged=dict(old)
    replace_localized=bool(new.get("_replace_localized_metadata"))
    replace_description=bool(new.get("_replace_description_only"))
    description_clear_overview=bool(new.get("_description_clear_overview"))
    clear_description=bool(new.get("_clear_description_overlay"))
    if replace_description and description_clear_overview:
        merged.pop("overview",None);merged.pop("overview_language",None)
    if clear_description:
        # Drop the visible overlay first. The incoming base snapshot below may
        # then repopulate overview/overview_language, or intentionally leave
        # them absent when the base provider had no synopsis.
        merged.pop("overview",None)
        merged.pop("overview_language",None)
        for key in ("_details_description_language","_details_description_checked_at","_details_description_fallback"):
            merged.pop(key,None)
    if replace_localized:
        # Stage 3 raw-language snapshot: clear the previous language's descriptive
        # fields first, then install exactly what the selected-locale/English
        # fallback request returned.  Never union Arabic/English/old-language data.
        for key in ('overview','overview_language','description','plot','descr','genres','genre','cast','actors','actor','crew','directors','director','writers','writer','runtime','duration','time','length','number_of_seasons','seasons_count','season_count','number_of_episodes','episodes_count','episode_count','release_date','first_air_date','year','release_year','releaseDate','countries','country','country_code','production_country','production_countries','origin_country','rating','vote_count','original_language','certification','age_rating'):
            merged.pop(key,None)
    for key,value in new.items():
        if key in ("poster_local","backdrop_local","library_dir","schema","updated_at","_replace_localized_metadata","_replace_description_only","_description_clear_overview","_clear_description_overlay"):continue
        # Description Language is an overlay only.  It must be able to replace
        # the visible overview even when the previous scalar was Arabic, while
        # leaving title/genre/cast/season/artwork metadata untouched.
        if replace_description and key in ("overview","overview_language","_details_description_language","_details_description_checked_at","_details_description_fallback"):
            if _meaningful(value) or key=="_details_description_fallback":merged[key]=value
            continue
        # A verified language refresh is a replacement for localized text, not
        # a union with the previous language. Without this, Arabic genres were
        # appended after cached English genres and the UI kept showing English.
        if replace_localized and key in ("overview","overview_language","genres","genre","cast","crew","directors","writers","_details_metadata_language","_details_metadata_fallback_fields"):
            if _meaningful(value):merged[key]=value
            continue
        # Final-state booleans are meaningful even when False is not.
        if key.endswith("_unavailable"):
            if value:merged[key]=True
            continue
        if not _meaningful(value):continue
        current=merged.get(key)
        if not _meaningful(current):merged[key]=value;continue
        if isinstance(current,list) and isinstance(value,list):
            if key=="cast_profiles":
                # release: cast portraits/profile paths are retired. Cast names live
                # in the normal ``cast`` field only. Never merge portrait records.
                continue
            out=list(current)
            for row in value:
                if row not in out:out.append(row)
            merged[key]=out;continue
        if isinstance(current,dict) and isinstance(value,dict):
            row=dict(current)
            for subk,subv in value.items():
                if _meaningful(subv):row[subk]=subv
            merged[key]=row;continue
        merged[key]=_prefer_scalar(key,current,value,new)
    return merged

def _field_present(row,field):
    if field=="overview":return bool(str(row.get("overview") or "").strip())
    if field=="cast":return bool(row.get("cast"))
    if field=="directors":return bool(row.get("directors") or row.get("director"))
    if field=="writers":return bool(row.get("writers") or row.get("writer"))
    if field=="genres":return bool(row.get("genres") or row.get("genre"))
    if field=="runtime":return row.get("runtime") not in (None,"")
    if field=="country":return bool(row.get("countries") or row.get("country") or row.get("production_countries"))
    if field=="year":return bool(row.get("year") or row.get("release_date") or row.get("first_air_date"))
    return False

def _stamp_final_states(payload,full_pass=False):
    if not full_pass:return payload
    out=dict(payload)
    for field in _FINAL_FIELDS:
        if not _field_present(out,field):out["%s_unavailable"%field]=True
    return out

def _file_fingerprint(path):
    try:
        h=hashlib.sha1()
        with open(path,"rb") as f:
            while True:
                b=f.read(262144)
                if not b:break
                h.update(b)
        return h.hexdigest()
    except Exception:return ""

def _parse_hex(value):
    value=str(value or "").strip().lstrip("#")
    if len(value)!=6:return None
    try:return tuple(int(value[i:i+2],16) for i in (0,2,4))
    except Exception:return None

def adaptive_palette_for_path(source_path):
    try:
        source=os.path.realpath(str(source_path or ""))
        roots=(os.path.realpath(POSTERS),os.path.realpath(BACKDROPS))
        if not any(source.startswith(root+os.sep) for root in roots):return None
        stem=os.path.splitext(os.path.basename(source))[0]
        index_path=os.path.join(INDEX,stem+"_adaptive.json")
        st=os.stat(index_path)
        sig=(index_path,int(getattr(st,"st_mtime_ns",int(st.st_mtime*1000000000))),int(st.st_size))
        with _ADAPTIVE_READ_CACHE_LOCK:
            cached=_ADAPTIVE_READ_CACHE.get(index_path)
            if cached is not None and cached[0]==sig:
                return cached[1]
        with open(index_path,"r",encoding="utf-8") as h:row=json.load(h)
        if not isinstance(row,dict):result=None
        else:
            primary=_parse_hex(row.get("adaptive_primary"));secondary=_parse_hex(row.get("adaptive_secondary")) or _parse_hex(row.get("adaptive_dark"))
            result=(primary,secondary) if primary and secondary else None
        with _ADAPTIVE_READ_CACHE_LOCK:
            if len(_ADAPTIVE_READ_CACHE)>=_ADAPTIVE_READ_CACHE_LIMIT:
                try:_ADAPTIVE_READ_CACHE.pop(next(iter(_ADAPTIVE_READ_CACHE)))
                except Exception:_ADAPTIVE_READ_CACHE.clear()
            _ADAPTIVE_READ_CACHE[index_path]=(sig,result)
        return result
    except Exception:return None

def readiness(data):
    row=data if isinstance(data,dict) else {}
    poster=bool(str(row.get("poster_local") or "") and os.path.isfile(str(row.get("poster_local") or "")))
    backdrop=bool(str(row.get("backdrop_local") or "") and os.path.isfile(str(row.get("backdrop_local") or "")))
    states={"poster":bool(poster or row.get("poster_unavailable")),"backdrop":bool(backdrop or row.get("backdrop_unavailable"))}
    for field in _FINAL_FIELDS:states[field]=bool(_field_present(row,field) or row.get("%s_unavailable"%field))
    adaptive=bool((row.get("adaptive_primary") and row.get("adaptive_dark")) or row.get("adaptive_unavailable"))
    states["adaptive"]=adaptive
    # release: actor portraits are not part of the product or cache contract.
    states["complete"]=bool(row.get("_metadata_complete") and all(states.get(k) for k in ("poster","backdrop","adaptive")+_FINAL_FIELDS))
    return states

def save(kind,tmdb_id,data):
    if not tmdb_id:return False
    with item_write_lock(kind,tmdb_id):
        paths=item_paths(kind,tmdb_id)
        if not ensure_persistent_dirs(POSTERS,BACKDROPS,INDEX):return False
        old=load(kind,tmdb_id) if hdd_read_ready() else {}
        incoming=dict(data or {});incoming=_stamp_final_states(incoming,bool(incoming.get("_metadata_complete")))
        payload=_merge_metadata(old,incoming)
        # release hard retirement: scrub any portrait/profile payload inherited
        # from older cache records while preserving the cast names themselves.
        for _cast_key in ("cast_profiles","cast_art_ready","cast_art_unavailable"):
            payload.pop(_cast_key,None)
        # A previous final-unavailable state must never survive once a later
        # resolver pass provides real data. Keep diagnostics/state monotonic.
        for field in _FINAL_FIELDS:
            if _field_present(payload,field):payload.pop("%s_unavailable"%field,None)
        payload.update({"schema":SCHEMA,"media_type":media_type(kind),"tmdb_id":int(tmdb_id),"library_dir":paths["dir"],"updated_at":int(time.time())})
        poster_ok=os.path.isfile(paths["poster"]) and os.path.getsize(paths["poster"])>1024
        backdrop_ok=os.path.isfile(paths["backdrop"]) and os.path.getsize(paths["backdrop"])>1024
        payload["poster_local"]=paths["poster"] if poster_ok else None;payload["backdrop_local"]=paths["backdrop"] if backdrop_ok else None
        if poster_ok:payload.pop("poster_unavailable",None)
        if backdrop_ok:payload.pop("backdrop_unavailable",None)
        if incoming.get("_metadata_complete"):
            # Network/download failure is retryable.  Final unavailable is stamped
            # only when the resolver explicitly proved TMDB has no candidate.
            if not poster_ok and incoming.get("poster_candidate_unavailable"):payload["poster_unavailable"]=True
            if not backdrop_ok and incoming.get("backdrop_candidate_unavailable"):payload["backdrop_unavailable"]=True
            if ((poster_ok or payload.get("poster_unavailable")) and (backdrop_ok or payload.get("backdrop_unavailable")) and not poster_ok and not backdrop_ok):payload["adaptive_unavailable"]=True
        ok=_atomic_json(paths["metadata"],payload)
        adaptive={k:payload.get(k) for k in ("adaptive_primary","adaptive_secondary","adaptive_dark","adaptive_accent","adaptive_source","adaptive_fingerprint","adaptive_unavailable") if payload.get(k) not in (None,"")}
        if adaptive:_atomic_json(paths["adaptive"],adaptive)
        return ok

def _hex(rgb):return "#%02x%02x%02x"%tuple(max(0,min(255,int(x))) for x in rgb)
@image_budgeted
def ensure_adaptive(data):
    row=dict(data or {});source=str(row.get("backdrop_local") or row.get("poster_local") or "")
    if not source or not os.path.isfile(source) or _PILImage is None:
        if row.get("_metadata_complete") and not source:row["adaptive_unavailable"]=True
        return row
    fingerprint=_file_fingerprint(source)
    if row.get("adaptive_primary") and row.get("adaptive_dark") and row.get("adaptive_fingerprint")==fingerprint:return row
    try:
        with _PILImage.open(source) as im:
            try:
                if _PILImageOps is not None:im=_PILImageOps.exif_transpose(im)
            except Exception as exc:diagnostic_failure("media_library.adaptive_exif",exc)
            im=im.convert("RGB");im.thumbnail((96,54));pixels=list(im.getdata())
        useful=[(r,g,b) for r,g,b in pixels if max(r,g,b)>=28 and min(r,g,b)<=225 and max(r,g,b)-min(r,g,b)>=18] or pixels or [(28,70,96)]
        r=sum(x[0] for x in useful)//len(useful);g=sum(x[1] for x in useful)//len(useful);b=sum(x[2] for x in useful)//len(useful)
        row.update({"adaptive_primary":_hex((r,g,b)),"adaptive_secondary":_hex((r*.72+25,g*.72+25,b*.72+25)),"adaptive_dark":_hex((max(2,r*.20),max(5,g*.20),max(8,b*.20))),"adaptive_accent":_hex((r*1.28+18,g*1.28+18,b*1.28+18)),"adaptive_source":source,"adaptive_fingerprint":fingerprint})
        row.pop("adaptive_unavailable",None)
    except Exception as exc:diagnostic_failure("media_library.adaptive",exc)
    return row

def metadata_complete(data):return bool(readiness(data).get("complete"))
def _norm_title(value):return re.sub(r"\s+"," ",catalogue_title(str(value or "")).casefold().strip())
def alias_key(kind,title,year=None):
    title=_norm_title(title)
    if not title:return ""
    return hashlib.sha1(("%s|%s|%s"%(media_type(kind),title,str(year or "")[:4])).encode("utf-8","ignore")).hexdigest()
def alias_path(kind,title,year=None):
    key=alias_key(kind,title,year);return os.path.join(LOOKUP,"alias_%s.json"%key) if key else ""

def load_alias(kind,title,year=None):
    if not hdd_read_ready():return {}
    requested=str(year or "")[:4]
    for y in ((year,None) if year else (None,)):
        path=alias_path(kind,title,y)
        if not path:continue
        try:
            with open(path,"r",encoding="utf-8") as h:row=json.load(h)
            if not isinstance(row,dict) or row.get("ambiguous") or not row.get("tmdb_id"):continue
            if requested and str(row.get("year") or "")[:4] and str(row.get("year"))[:4]!=requested:continue
            data=load(row.get("media_type") or kind,row.get("tmdb_id"))
            if data:
                # The alias file itself is independent identity evidence: it was
                # created from the provider/catalogue title that first resolved
                # this TMDB record.  Stamp that title onto the returned global
                # row so callers do not reject the same work merely because
                # TMDB stores a different localized/original title.  This is
                # what lets Portal, Xtream and M3U reuse one HDD record.
                data=dict(data)
                data["identity_catalogue_title"]=str(row.get("title") or title or "")
                data["identity_pointer_verified"]=True
                if row.get("year") not in (None,"") and not data.get("identity_catalogue_year"):
                    data["identity_catalogue_year"]=str(row.get("year"))[:4]
                return data
        except (OSError,ValueError,TypeError):continue
    return {}

def save_alias(kind,title,year,tmdb_id):
    if not tmdb_id:return False
    payload={"schema":SCHEMA,"media_type":media_type(kind),"tmdb_id":int(tmdb_id),"title":str(title or ""),"year":str(year or "")[:4],"updated_at":int(time.time())};ok=False
    for y in (year,None):
        path=alias_path(kind,title,y)
        if not path:continue
        row=dict(payload)
        if y is None and os.path.isfile(path):
            try:
                with open(path,"r",encoding="utf-8") as h:old=json.load(h)
                if isinstance(old,dict) and old.get("tmdb_id") and int(old.get("tmdb_id"))!=int(tmdb_id):
                    row={"schema":SCHEMA,"media_type":media_type(kind),"title":str(title or ""),"ambiguous":True,"candidates":sorted(set([int(old.get("tmdb_id")),int(tmdb_id)])),"updated_at":int(time.time())}
            except Exception:pass
        ok=_atomic_json(path,row) or ok
    return ok
