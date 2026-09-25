"""One canonical metadata authority shared by Details, Cinematic and Backdrop Grid."""
import os
import re
import time
from .artwork_v2 import ArtworkV2, save_manifest
from .persistent_cache import load_detail_snapshot, load_detail_snapshot_by_tmdb, save_detail_snapshot
from .storage import load_settings
from .log import optional_failure
from .language_catalog import normalize_description_choice, effective_description_language
from .age_rating import certification_from_tmdb, rating_endpoint


# Fields whose visible value is owned by the raw verified TMDb information
# snapshot.  Provider/artwork aliases must not leak back in after authority is
# ready, even when TMDb legitimately returns an empty field.

_DETAILS_METADATA_SCHEMA = 5
_DESCRIPTION_OVERLAY_KEYS = (
    "_details_description_language", "_details_description_checked_at",
    "_details_description_fallback", "_replace_description_only",
    "_description_clear_overview",
)

_RAW_METADATA_FIELDS=('overview','overview_language','description','plot','descr','genres','genre','cast','actors','actor','crew','directors','director','writers','writer','runtime','duration','time','length','number_of_seasons','seasons_count','season_count','number_of_episodes','episodes_count','episode_count','release_date','first_air_date','year','release_year','releaseDate','countries','country','country_code','production_country','production_countries','origin_country','rating','vote_count','original_language','certification','age_rating')


def _cancelled(ev):
    try:return bool(ev is not None and ev.is_set())
    except Exception:return False


def _tmdb_certification(details, media_type, supplemental=None):
    return certification_from_tmdb(details,media_type,supplemental=supplemental)


def _backdrop_ok(path):
    try:
        from .artwork_v2 import _valid_backdrop
        return bool(_valid_backdrop(str(path or "")))
    except Exception:
        try:return bool(path and os.path.isfile(str(path)) and os.path.getsize(str(path))>100)
        except Exception:return False


def _merge(a,b):
    out=dict(a or {})
    if isinstance(b,dict):
        if b.get("_replace_description_only") and b.get("_description_clear_overview"):
            out.pop("overview",None);out.pop("overview_language",None)
        if b.get("_replace_localized_metadata"):
            # Raw TMDb locale snapshots are replacements, never a union of old
            # languages.  Clearing first also lets a genuinely unavailable field
            # stay unavailable instead of resurrecting stale Arabic/English text.
            for key in _RAW_METADATA_FIELDS:
                out.pop(key,None)
        for k,v in b.items():
            if k=="_description_clear_overview":continue
            if v not in (None,"",[],{}):out[k]=v
    return out


def _metadata_language(value):
    """Return the requested TMDb locale without collapsing it to AR/EN.

    Stage 3 treats metadata as TMDb-owned data.  Ultra only normalizes locale
    spelling for cache/readiness keys; it does not infer a different language
    from the text itself.
    """
    raw=str(value or "en-US").strip().replace("_","-") or "en-US"
    bits=[x for x in raw.split("-") if x]
    if not bits:return "en-US"
    language=bits[0].lower()
    if len(bits)==1:return language
    return "%s-%s"%(language,bits[1].upper())


def _has_arabic(value):
    return bool(re.search(r"[\u0600-\u06ff]",str(value or "")))


def _base_metadata_target(cfg):
    """The selected information language, with no Ultra language policy.

    The user-facing Description Language selector already exposes every TMDb
    locale supported by Ultra.  Stage 3 makes that selection the text-metadata
    language for overview/genres/credits as well.  ``current`` keeps the legacy
    internal TMDb locale for backwards compatibility.  Artwork/title-logo
    language remains separate and is not changed here.
    """
    cfg=cfg or {}
    choice=normalize_description_choice(cfg.get("description_language","ar-en"))
    if choice=="current":
        return _metadata_language(cfg.get("tmdb_language","ar-EG"))
    wanted=effective_description_language(choice,cfg.get("plugin_language","en"))
    return _metadata_language(wanted or cfg.get("tmdb_language","ar-EG"))


def _base_metadata_language_ready(row,settings=None):
    """Ready means the exact selected TMDb locale has already been requested.

    No script inspection, Arabic-vs-English heuristics, genre checks or text
    rejection are allowed.  A selected-language request may legitimately fall
    back field-by-field to English; the locale stamp still records which request
    policy produced the snapshot so it is not re-fetched forever.
    """
    row=row if isinstance(row,dict) else {}
    if not (row.get("_details_authority_ready") and row.get("tmdb_id")):
        return False
    # Metadata policy schema v5: older rows are refreshed once so the
    # selected-language overview and the country-aware certification lane can populate
    # Cinematic, BG1, BG2 and Details instead of preserving an old blank value.
    try:
        if int(row.get("_details_metadata_schema") or 0) < _DETAILS_METADATA_SCHEMA:return False
    except Exception:return False
    cfg=dict(settings or load_settings() or {})
    wanted=_base_metadata_target(cfg)
    stamped=str(row.get("_details_metadata_language") or "").strip()
    return bool(stamped and _metadata_language(stamped)==wanted)


def _description_choice(cfg):
    return normalize_description_choice((cfg or {}).get("description_language","current"))


def _description_language(cfg):
    return effective_description_language(_description_choice(cfg),(cfg or {}).get("plugin_language","en"))


def _base_metadata_view(row):
    """Project a description-overlay row back to its untouched base synopsis."""
    src=dict(row or {})
    if src.get("_details_description_language"):
        base=src.get("_details_base_description")
        if isinstance(base,dict):
            text=str(base.get("overview") or "")
            lang=str(base.get("overview_language") or "")
            if text:src["overview"]=text
            else:src.pop("overview",None)
            if lang:src["overview_language"]=lang
            else:src.pop("overview_language",None)
    return src


def metadata_language_ready(row,settings=None):
    """Return raw-TMDb metadata readiness for the selected locale."""
    return _base_metadata_language_ready(row,settings)


def _localized_metadata(profile,media_type,item,current,cfg,cancel_event=None):
    """Fetch one raw TMDb metadata snapshot by verified id.

    Contract: selected locale first, then English only for fields TMDb leaves
    empty.  Ultra does not inspect scripts, force Arabic/English, transliterate,
    prefer Latin person names, or preserve an older language over the new one.
    Artwork and title-logo bytes are never touched here.
    """
    if _cancelled(cancel_event):return {}
    tmdb_id=(current or {}).get("tmdb_id")
    credential=str((cfg or {}).get("tmdb_credential") or "").strip()
    if not (tmdb_id and credential and (cfg or {}).get("tmdb_enabled",True)):
        return {}
    try:
        from .tmdb import TMDBClient
        wanted=_base_metadata_target(cfg or {})
        mt=str((current or {}).get("media_type") or ("tv" if str(media_type).lower() in ("series","tv") else "movie")).lower()
        if mt in ("series","show"):mt="tv"
        if mt not in ("movie","tv"):mt="movie"
        timeout=min(7,max(3,int((cfg or {}).get("timeout",10) or 10)))
        client=TMDBClient(credential,wanted,timeout)
        _rating_append="release_dates" if mt=="movie" else "content_ratings"
        params={"language":wanted,"append_to_response":"credits,external_ids,translations,%s"%_rating_append}
        details=client._get("/%s/%s"%(mt,int(tmdb_id)),params) or {}
        if _cancelled(cancel_event) or not isinstance(details,dict) or not details.get("id"):
            return {}

        def _credit_parts(payload):
            credits=payload.get("credits") if isinstance(payload.get("credits"),dict) else {}
            cast=[str(p.get("name")) for p in (credits.get("cast") or [])[:6] if isinstance(p,dict) and p.get("name")]
            directors=[];writers=[];crew=[]
            for person in (credits.get("crew") or []):
                if not isinstance(person,dict) or not person.get("name"):continue
                name=str(person.get("name"));job=str(person.get("job") or "");dept=str(person.get("department") or "")
                if job=="Director" and name not in directors:directors.append(name)
                if job in ("Writer","Screenplay","Story","Teleplay","Original Story","Novel") or dept=="Writing":
                    if name not in writers:writers.append(name)
                if job in ("Director","Creator","Executive Producer") and name not in crew:crew.append(name)
            if mt=="tv":
                for person in (payload.get("created_by") or []):
                    if isinstance(person,dict) and person.get("name"):
                        name=str(person.get("name"))
                        if name not in writers:writers.insert(0,name)
                        if name not in crew:crew.insert(0,name)
            return cast,crew[:4],directors[:3],writers[:4]

        selected_genres=[str(x.get("name")) for x in (details.get("genres") or []) if isinstance(x,dict) and x.get("name")]
        selected_cast,selected_crew,selected_directors,selected_writers=_credit_parts(details)

        # TMDb's localized details endpoint may silently return its default/English
        # overview when the requested translation is absent.  Prefer the explicitly
        # tagged translation bundled in append_to_response.  Only an original work
        # whose original_language matches the requested language may use the normal
        # details overview without a translation tag.  Everything else falls back
        # to English field-by-field below.
        wanted_lang=_description_locale_parts(wanted)[0]
        original_lang=str(details.get("original_language") or "").strip().lower().split("-")[0]
        translations=details.get("translations") if isinstance(details.get("translations"),dict) else {}
        if wanted_lang=="en":
            overview=str(details.get("overview") or "").strip()
        else:
            overview=_translation_overview(translations,wanted)
            if not overview and original_lang==wanted_lang:
                overview=str(details.get("overview") or "").strip()

        fallback={}
        need_english=(wanted.lower()!="en-us" and (not overview or not selected_genres or not selected_cast or not selected_directors or not selected_writers))
        if need_english:
            try:fallback=client._get("/%s/%s"%(mt,int(tmdb_id)),{"language":"en-US","append_to_response":"credits,external_ids"}) or {}
            except Exception as exc:optional_failure("details_authority.raw_english_fallback",exc);fallback={}
        fallback_genres=[str(x.get("name")) for x in (fallback.get("genres") or []) if isinstance(x,dict) and x.get("name")]
        fallback_cast,fallback_crew,fallback_directors,fallback_writers=_credit_parts(fallback or {})

        fallback_fields=[]
        if not overview:
            overview=str((fallback or {}).get("overview") or "").strip()
            if overview:fallback_fields.append("overview")
        genres=selected_genres or fallback_genres
        if genres and not selected_genres:fallback_fields.append("genres")
        cast=selected_cast or fallback_cast
        if cast and not selected_cast:fallback_fields.append("cast")
        crew=selected_crew or fallback_crew
        if crew and not selected_crew:fallback_fields.append("crew")
        directors=selected_directors or fallback_directors
        if directors and not selected_directors:fallback_fields.append("directors")
        writers=selected_writers or fallback_writers
        if writers and not selected_writers:fallback_fields.append("writers")

        runtime=details.get("runtime")
        if mt=="tv" and not runtime:
            runtimes=details.get("episode_run_time") or []
            runtime=runtimes[0] if isinstance(runtimes,list) and runtimes else None
        date=details.get("release_date") if mt=="movie" else details.get("first_air_date")
        year=""
        try:year=int(str(date)[:4]) if str(date or "")[:4].isdigit() else ""
        except Exception:year=""
        countries=[]
        for entry in (details.get("production_countries") or details.get("origin_country") or []):
            value=(entry.get("iso_3166_1") or entry.get("name")) if isinstance(entry,dict) else entry
            if value and str(value) not in countries:countries.append(str(value))
        external=details.get("external_ids") if isinstance(details.get("external_ids"),dict) else {}
        if not external and isinstance((fallback or {}).get("external_ids"),dict):external=fallback.get("external_ids") or {}
        certification=_tmdb_certification(details,mt)
        # Some TMDb responses/receiver proxies omit appended certification
        # blocks even when the dedicated endpoint has them.  Only when the
        # appended payload produced no usable value, ask that tiny exact-id
        # endpoint once.  This stays inside Details Authority and therefore is
        # cached for Cinematic/BG1/BG2/Details together.
        if not certification and not _cancelled(cancel_event):
            try:
                rating_payload=client._get(rating_endpoint(mt,tmdb_id)) or {}
            except Exception as exc:
                optional_failure("details_authority.age_rating_fallback",exc);rating_payload={}
            certification=_tmdb_certification(details,mt,supplemental=rating_payload)

        out={
            "tmdb_id":int(tmdb_id),"media_type":mt,
            "_details_metadata_language":wanted,
            "_details_metadata_schema":_DETAILS_METADATA_SCHEMA,
            "_details_metadata_fallback_fields":fallback_fields,
            "_replace_localized_metadata":True,
            "overview":overview,
            "overview_language":("en-US" if "overview" in fallback_fields else wanted) if overview else "",
            "genres":genres,
            "cast":cast,"crew":crew,"directors":directors,"writers":writers,
            "runtime":runtime,
            "number_of_seasons":details.get("number_of_seasons") or 0,
            "number_of_episodes":details.get("number_of_episodes") or 0,
            "release_date":date or "","year":year,
            "rating":details.get("vote_average"),"vote_count":details.get("vote_count"),
            "certification":certification,"age_rating":certification,
            "_age_rating_checked":True,
            "countries":countries,
            "original_language":str(details.get("original_language") or "").strip().lower(),
            "imdb_id":str(external.get("imdb_id") or (current or {}).get("imdb_id") or "").strip(),
        }
        # Empty values are intentionally present in this complete language snapshot.
        # media_library._merge_metadata clears stale previous-language fields before
        # applying the meaningful values above.
        return out
    except Exception as exc:
        optional_failure("details_authority.raw_metadata",exc);return {}


def _description_locale_parts(value):
    raw=str(value or "en-US").replace("_","-").strip()
    bits=[x for x in raw.split("-") if x]
    language=(bits[0].lower() if bits else "en")
    region=(bits[1].upper() if len(bits)>1 else "")
    return language,region


def _translation_overview(payload,wanted):
    """Return only an overview explicitly tagged with the requested language.

    Region is preferred when TMDb exposes more than one translation for the
    same language (for example pt-BR vs pt-PT or zh-CN vs zh-TW). Falling back
    to another region of the same language is allowed; falling back to a
    different language is never allowed here.
    """
    language,region=_description_locale_parts(wanted)
    rows=(payload or {}).get("translations") or []
    same=[]
    for entry in rows:
        if not isinstance(entry,dict):continue
        if str(entry.get("iso_639_1") or "").strip().lower()!=language:continue
        text=str((entry.get("data") or {}).get("overview") or "").strip()
        if not text:continue
        entry_region=str(entry.get("iso_3166_1") or "").strip().upper()
        same.append((entry_region,text))
    if region:
        for entry_region,text in same:
            if entry_region==region:return text
    return same[0][1] if same else ""
def _base_description_snapshot(row, force=False):
    """Return the untouched AR+EN / English synopsis snapshot.

    The snapshot belongs to the base TMDb metadata mode, never to a generic
    description overlay.  This lets Description Language switch freely and
    return to Current without another network request.
    """
    row=row if isinstance(row,dict) else {}
    existing=row.get("_details_base_description")
    current_meta=str(row.get("_details_metadata_language") or "")
    if isinstance(existing,dict) and not force:
        if str(existing.get("metadata_language") or "")==current_meta:
            return dict(existing)
    return {
        "overview":str(row.get("overview") or ""),
        "overview_language":str(row.get("overview_language") or ""),
        "metadata_language":current_meta,
    }


def _restore_base_description(row):
    """Remove only the description overlay and restore the base synopsis."""
    src=dict(row or {})
    base=src.get("_details_base_description")
    if not isinstance(base,dict):
        return {}, False
    text=str(base.get("overview") or "")
    language=str(base.get("overview_language") or "")
    if text:
        src["overview"]=text
    else:
        src.pop("overview",None)
    if language:
        src["overview_language"]=language
    else:
        src.pop("overview_language",None)
    for key in _DESCRIPTION_OVERLAY_KEYS:
        src.pop(key,None)
    return src, True



def _localized_overview(profile,media_type,item,current,cfg,cancel_event=None):
    """Resolve only ``overview`` for the selected description language.

    The language contract is deliberately strict: requested language first,
    then English, then no description. No provider/base/previous-language text
    is allowed to masquerade as the selected language. The request is locked to
    the already verified TMDb id and never returns title, genre, cast, season or
    artwork fields.
    """
    if _cancelled(cancel_event):return {}
    choice=_description_choice(cfg)
    if choice=="current":return {}
    wanted=_description_language(cfg)
    tmdb_id=(current or {}).get("tmdb_id")
    credential=str((cfg or {}).get("tmdb_credential") or "").strip()
    if not (wanted and tmdb_id and credential and (cfg or {}).get("tmdb_enabled",True)):
        return {}

    cached=(current or {}).get("_description_overviews") or {}
    cached_row=cached.get(wanted) if isinstance(cached,dict) else None
    if isinstance(cached_row,dict) and "overview" in cached_row:
        out={
            "tmdb_id":int(tmdb_id),
            "_details_description_language":wanted,
            "_replace_description_only":True,
            "_description_overviews":cached,
            "_details_base_description":_base_description_snapshot(current),
        }
        text=str(cached_row.get("overview") or "").strip()
        if text:
            out["overview"]=text
            out["overview_language"]=str(cached_row.get("overview_language") or wanted)
        else:
            # The cached result means both selected language and English were
            # checked and absent. Never leak the base/previous language here.
            out["_description_clear_overview"]=True
        out["_details_description_fallback"]=bool(cached_row.get("fallback"))
        out["_details_description_checked_at"]=int(cached_row.get("checked_at") or time.time())
        return out

    try:
        from .tmdb import TMDBClient
        mt=str((current or {}).get("media_type") or ("tv" if str(media_type).lower() in ("series","tv") else "movie")).lower()
        if mt in ("series","show"):mt="tv"
        if mt not in ("movie","tv"):mt="movie"
        client=TMDBClient(credential,wanted,min(7,max(3,int((cfg or {}).get("timeout",10) or 10))))
        wanted_lang,_wanted_region=_description_locale_parts(wanted)

        overview="";overview_language="";fallback_used=False
        translations={}

        if wanted_lang=="en":
            details=client._get("/%s/%s"%(mt,int(tmdb_id)),{"language":"en-US"}) or {}
            if _cancelled(cancel_event) or not isinstance(details,dict) or not details.get("id"):
                return {}
            overview=str(details.get("overview") or "").strip()
            if overview:overview_language="en-US"
        else:
            # TMDb localized details can itself fall back to another language.
            # Use the translations endpoint as the authority so a Spanish choice
            # can only display a Spanish translation, a German choice German, etc.
            try:
                translations=client._get("/%s/%s/translations"%(mt,int(tmdb_id)),{}) or {}
            except Exception as exc:
                optional_failure("details_authority.description_translations",exc);translations={}
            overview=_translation_overview(translations,wanted)
            if overview:
                overview_language=wanted
            else:
                # If the work's original language is exactly the requested one,
                # the normal localized endpoint is authoritative even when TMDb
                # does not duplicate the original text in /translations.
                try:
                    details=client._get("/%s/%s"%(mt,int(tmdb_id)),{"language":wanted}) or {}
                except Exception as exc:
                    optional_failure("details_authority.description_selected",exc);details={}
                original_language=str((details or {}).get("original_language") or "").strip().lower()
                if isinstance(details,dict) and details.get("id") and original_language==wanted_lang:
                    overview=str(details.get("overview") or "").strip()
                    if overview:overview_language=wanted

            if not overview:
                # The only cross-language fallback permitted by Ultra is English.
                english=_translation_overview(translations,"en-US")
                if not english:
                    try:
                        fallback=client._get("/%s/%s"%(mt,int(tmdb_id)),{"language":"en-US"}) or {}
                    except Exception as exc:
                        optional_failure("details_authority.description_english_fallback",exc);fallback={}
                    english=str((fallback or {}).get("overview") or "").strip()
                if english:
                    overview=english;overview_language="en-US";fallback_used=True

        checked=int(time.time())
        cache=dict(cached) if isinstance(cached,dict) else {}
        cache[wanted]={
            "overview":overview,
            "overview_language":overview_language or wanted,
            "fallback":bool(fallback_used),
            "checked_at":checked,
        }
        out={
            "tmdb_id":int(tmdb_id),"media_type":mt,
            "_details_description_language":wanted,
            "_details_description_checked_at":checked,
            "_details_description_fallback":bool(fallback_used),
            "_description_overviews":cache,
            "_details_base_description":_base_description_snapshot(current),
            "_replace_description_only":True,
        }
        if overview:
            out["overview"]=overview;out["overview_language"]=overview_language or wanted
        else:
            out["_description_clear_overview"]=True
        return out
    except Exception as exc:
        optional_failure("details_authority.localized_overview",exc);return {}


def load_canonical(profile,media_type,item):
    try:
        row=load_detail_snapshot(profile,media_type,item) or {}
        if row.get("tmdb_id"):
            direct=load_detail_snapshot_by_tmdb(row.get("media_type") or media_type,row.get("tmdb_id")) or {}
            row=_merge(row,direct)
        return row
    except Exception as exc:
        optional_failure("details_authority.load",exc);return {}



def _visible_provider_overview(src):
    """Return the exact provider synopsis fields Native Details paints first."""
    if not isinstance(src,dict):return ""
    direct=str(src.get("_details_provider_overview") or "").strip()
    if direct:return direct
    for key in ("description","descr","plot","overview"):
        value=str(src.get(key) or "").strip()
        if value:return value
    overlay=src.get("item_overlay")
    if isinstance(overlay,dict):
        for key in ("description","descr","plot","overview"):
            value=str(overlay.get(key) or "").strip()
            if value:return value
    return ""


def _enrich_provider_overview(provider_client,item,media_type,cancel_event=None):
    """Mirror Details' Xtream provider-info fallback without polluting TMDb HDD data."""
    if _cancelled(cancel_event) or not isinstance(item,dict):return ""
    if not item.get("_xtream") or provider_client is None or not hasattr(provider_client,"enrich_provider_artwork"):
        return ""
    try:
        from .core.call_compat import call_compatible
        enriched=call_compatible(
            provider_client.enrich_provider_artwork,
            (((dict(item),media_type,cancel_event), {"force":True}),
             ((dict(item),media_type,cancel_event), {})),
        ) or {}
        if _cancelled(cancel_event):return ""
        return _visible_provider_overview(enriched)
    except Exception as exc:
        optional_failure("details_authority.provider_overview_enrich",exc);return ""

def details_overview(profile,media_type,item,current=None):
    """Return one stable visible synopsis for Details/Cinematic/BG1/BG2.

    R262 language lock: a cached row from another information language is never
    allowed to paint the box while the selected locale is resolving.  When the
    selected information language is Arabic, Arabic TMDb text wins first; if
    TMDb has no Arabic overview but this exact provider item already supplies an
    Arabic synopsis, keep that Arabic text; English is the final fallback only
    when no Arabic synopsis is available at all.
    """
    try:
        item=item if isinstance(item,dict) else {}
        current=current if isinstance(current,dict) else {}
        cfg=dict(load_settings() or {})
        wanted=_base_metadata_target(cfg)
        wanted_lang=_description_locale_parts(wanted)[0]

        row=load_canonical(profile,media_type,item) or {}
        if current.get("_details_authority_ready"):
            row=_merge(row,current)
        tmdb_id=(row.get("tmdb_id") or current.get("tmdb_id") or item.get("_locked_tmdb_id"))
        if tmdb_id:
            mt=(row.get("media_type") or current.get("media_type") or item.get("_locked_tmdb_type") or media_type)
            direct=load_detail_snapshot_by_tmdb(mt,tmdb_id) or {}
            if direct:row=_merge(row,direct)

        ready=_base_metadata_language_ready(row,cfg)
        canonical=str(row.get("overview") or "").strip() if ready else ""
        canonical_lang=str(row.get("overview_language") or "").strip().lower() if ready else ""

        def provider_values(source):
            source=source if isinstance(source,dict) else {}
            values=[]
            for key in ("_details_provider_overview","description","descr","plot"):
                value=str(source.get(key) or "").strip()
                if value and value not in values:values.append(value)
            # A raw catalogue row may legitimately call its provider synopsis
            # 'overview'.  A completed Details row may not: that field is TMDb.
            if not source.get("_details_authority_ready"):
                value=str(source.get("overview") or "").strip()
                if value and value not in values:values.append(value)
            return values

        provider=[]
        for source in (item,current):
            for value in provider_values(source):
                if value not in provider:provider.append(value)

        if wanted_lang=="ar":
            if canonical and (canonical_lang.startswith("ar") or _has_arabic(canonical)):
                return canonical,True
            for value in provider:
                if _has_arabic(value):
                    return value,True
            if canonical:
                # Exact ar request completed and TMDb proved Arabic absent.
                # English is now the permitted final fallback.
                return canonical,True
            if not ready:
                # While Arabic is still resolving, keep same-item provider text
                # rather than flashing a stale cached English TMDb snapshot.
                if provider:return provider[0],True
                return "",False
        else:
            if canonical:return canonical,True
            if not ready and provider:return provider[0],True

        if provider:return provider[0],True
        if ready:return "",True
        return "",False
    except Exception as exc:
        optional_failure("details_authority.overview",exc);return "",False


def resolve_metadata_fast(profile,media_type,item,current=None,cancel_event=None,settings=None,provider_client=None):
    """Fast Details-Authority text hydration for Cinematic/BG1/BG2.

    This lane is exact-id and metadata-only: no title search, artwork download,
    backdrop preparation, Pillow work or title-logo work.  It exists so the
    external information boxes can obtain the same Details record without the
    user having to open Details first.
    """
    if _cancelled(cancel_event):return dict(current or {})
    cfg=dict(settings or load_settings() or {})
    row=dict(current or load_canonical(profile,media_type,item) or {})
    if metadata_language_ready(row,cfg):
        # If TMDb legitimately has no overview, Native Details can still show the
        # provider synopsis. Catalogue rows usually already contain it; Xtream may
        # require the same provider-info enrichment Details performs on entry.
        if not str(row.get("overview") or "").strip() and not _visible_provider_overview(item) and not _visible_provider_overview(row):
            provider_text=_enrich_provider_overview(provider_client,item,media_type,cancel_event=cancel_event)
            if provider_text:row["_details_provider_overview"]=provider_text
        return row
    if not row.get("tmdb_id") and isinstance(item,dict):
        _item_tid=item.get("tmdb_id") or item.get("_locked_tmdb_id")
        if _item_tid:
            row["tmdb_id"]=_item_tid
            row["media_type"]=item.get("media_type") or item.get("_locked_tmdb_type") or ("tv" if str(media_type).lower() in ("series","tv") else "movie")
            row["identity_verified"]=bool(item.get("identity_verified") or item.get("identity_pointer_verified") or item.get("_locked_tmdb_id"))

    # Stage 2: the outside information box must not depend on Details having
    # already been opened.  When the catalogue row has no TMDb id, resolve only
    # the canonical identity here, then fetch text metadata by that exact id.
    # ArtworkV2._resolve_identity performs identity lookup only; it does not
    # download poster/backdrop/title-logo bytes, so this lane stays metadata-only.
    if not row.get("tmdb_id"):
        credential=str(cfg.get("tmdb_credential") or "").strip()
        if credential and cfg.get("tmdb_enabled",True) and isinstance(item,dict):
            try:
                resolver=ArtworkV2(credential,cfg.get("tmdb_language","ar-EG"),min(7,max(3,int(cfg.get("timeout",10) or 10))))
                resolved_type,resolved_id,confidence,identity_source,_seed=resolver._resolve_identity(media_type,item,hot=row)
                if _cancelled(cancel_event):return row
                if resolved_id:
                    row["tmdb_id"]=int(resolved_id)
                    row["media_type"]=str(resolved_type or ("tv" if str(media_type).lower() in ("series","tv") else "movie"))
                    row["identity_verified"]=True
                    row["identity_source"]=str(identity_source or "metadata_identity")
                    try:row["identity_confidence"]=float(confidence or 0.0)
                    except Exception:pass
            except Exception as exc:
                optional_failure("details_authority.fast_identity",exc)
    if not row.get("tmdb_id"):
        if not _visible_provider_overview(item) and not _visible_provider_overview(row):
            provider_text=_enrich_provider_overview(provider_client,item,media_type,cancel_event=cancel_event)
            if provider_text:row["_details_provider_overview"]=provider_text
        return row
    refreshed=_localized_metadata(profile,media_type,item,row,cfg,cancel_event=cancel_event) or {}
    if not refreshed:
        if not _visible_provider_overview(item) and not _visible_provider_overview(row):
            provider_text=_enrich_provider_overview(provider_client,item,media_type,cancel_event=cancel_event)
            if provider_text:row["_details_provider_overview"]=provider_text
        return row
    merged=_merge(row,refreshed)
    merged["matched"]=bool(merged.get("matched",True))
    merged["identity_verified"]=bool(merged.get("identity_verified") or merged.get("identity_pointer_verified") or merged.get("tmdb_id"))
    merged["_details_authority_ready"]=True
    merged["_details_authority_version"]=4
    for key in _DESCRIPTION_OVERLAY_KEYS+("_details_base_description","_description_overviews","_details_description_restore_needed","_details_visible_overview","_details_visible_overview_language","_details_visible_overview_source","_details_visible_overview_updated_at"):
        merged.pop(key,None)
    if _cancelled(cancel_event):return row
    try:save_detail_snapshot(profile,media_type,item,merged)
    except Exception as exc:optional_failure("details_authority.fast_snapshot",exc)
    try:save_manifest(profile,media_type,item,merged)
    except Exception as exc:optional_failure("details_authority.fast_manifest",exc)
    # Ephemeral provider fallback is attached only to the selected-page result.
    # Never write it into the global TMDb snapshot, otherwise one provider could
    # leak its synopsis into another provider that shares the same TMDb id.
    if not str(merged.get("overview") or "").strip() and not _visible_provider_overview(item) and not _visible_provider_overview(merged):
        provider_text=_enrich_provider_overview(provider_client,item,media_type,cancel_event=cancel_event)
        if provider_text:merged["_details_provider_overview"]=provider_text
    return merged


def resolve_canonical(profile,media_type,item,cancel_event=None,settings=None,provider_client=None,provider_downloader=None,component_callback=None):
    """Resolve one stable-focus item and persist the single Details record."""
    if _cancelled(cancel_event):return {}
    cfg=dict(settings or load_settings() or {})
    current=load_canonical(profile,media_type,item)
    # Stage 3: there is no separate description overlay or AR+EN text policy.
    # The selected TMDb information locale owns every descriptive metadata field
    # and may fall back field-by-field to English upstream.
    metadata_ready=metadata_language_ready(current,cfg)
    backdrop_ready=_backdrop_ok(current.get("backdrop_local"))
    if metadata_ready and backdrop_ready:
        return current

    credential=str(cfg.get("tmdb_credential") or "").strip()
    # R269: no provider/server artwork bootstrap. Bundled TMDb is the default
    # authority; if TMDb is explicitly disabled/unavailable, keep local data only.
    if not credential:return current
    if not cfg.get("tmdb_enabled",True):return current

    src=dict(item or {}) if isinstance(item,dict) else {}

    # R131: metadata completeness must never suppress artwork recovery.  The
    # exact failure seen on receiver had a verified TMDB id, correct overview,
    # genres and rating, but no local backdrop.  R130 returned above solely on
    # metadata readiness, so resolve_backdrop_only() was unreachable.  When the
    # identity is already verified, repair only the missing landscape lane by
    # exact TMDB id; no title search and no metadata churn.
    if metadata_ready and current.get("tmdb_id") and not backdrop_ready:
        try:
            retry_at=int(current.get("next_retry_at") or current.get("backdrop_next_retry_at") or 0)
        except Exception:
            retry_at=0
        if not retry_at or int(time.time())>=retry_at:
            try:
                resolver=ArtworkV2(credential,cfg.get("tmdb_language","ar-EG"),min(7,max(3,int(cfg.get("timeout",10) or 10))))
                locked=dict(src)
                locked["_locked_tmdb_id"]=current.get("tmdb_id")
                locked["_locked_tmdb_type"]=current.get("media_type") or ("tv" if str(media_type)=="series" else "movie")
                rescued=resolver.resolve_backdrop_only(profile,media_type,locked,cancel_event=cancel_event) or {}
                if rescued:
                    row=_merge(current,rescued)
                    row["_details_authority_ready"]=True
                    row["_details_authority_version"]=2
                    if _cancelled(cancel_event):return current
                    save_detail_snapshot(profile,media_type,src,row)
                    try:save_manifest(profile,media_type,src,row)
                    except Exception as exc:optional_failure("details_authority.backdrop_manifest",exc)
                    current=row
                    backdrop_ready=_backdrop_ok(row.get("backdrop_local"))
            except Exception as exc:
                optional_failure("details_authority.backdrop_repair",exc)
        if backdrop_ready:
            return current
        # Metadata is already complete for the requested language.  If the
        # exact-ID backdrop repair could not produce a file (or its retry window
        # is still active), keep the canonical row and stop here.  Do not fall
        # through into a full title/artwork resolver just because one visual lane
        # is missing; a later stable focus/Cache Artwork retry can repair it.
        return current

    # A language repair must never fall back to title search. Reuse the already
    # verified canonical identity and request raw metadata only for that exact id.
    if current.get("tmdb_id"):
        src["_locked_tmdb_id"]=current.get("tmdb_id")
        src["_locked_tmdb_type"]=current.get("media_type") or ("tv" if str(media_type)=="series" else "movie")
        if current.get("_details_authority_ready") and not metadata_ready:
            repaired=_localized_metadata(profile,media_type,src,current,cfg,cancel_event=cancel_event)
            if repaired:
                row=_merge(current,repaired)
                row["_details_authority_ready"]=True;row["_details_authority_version"]=4
                row["_details_metadata_language"]=_base_metadata_target(cfg)
                for _key in _DESCRIPTION_OVERLAY_KEYS+("_details_base_description","_description_overviews","_details_description_restore_needed","_details_visible_overview","_details_visible_overview_language","_details_visible_overview_source","_details_visible_overview_updated_at"):
                    row.pop(_key,None)
                if _cancelled(cancel_event):return current
                save_detail_snapshot(profile,media_type,src,row)
                try:save_manifest(profile,media_type,src,row)
                except Exception as exc:optional_failure("details_authority.language_manifest",exc)
                return row
            # Keep the old visible metadata on transient failure. It remains
            # unstamped for the selected locale so a later stable focus retries.
            return current

    try:
        resolver=ArtworkV2(credential,cfg.get("tmdb_language","ar-EG"),min(7,max(3,int(cfg.get("timeout",10) or 10))))
        # R173 Parallel First Paint: a cold stable-focus title must not finish a
        # poster-only network pass before the backdrop/logo-capable full resolve
        # is even allowed to start. The full resolver already owns identity, the
        # same TMDB details payload and the same persistent canonical HDD paths.
        # Start there directly; cached titles still return through the resolver's
        # HDD hot path without network work.
        row=dict(current or {})
        full=resolver.resolve(profile,media_type,src,full=True,cancel_event=cancel_event,component_callback=component_callback) or {}
        if _cancelled(cancel_event):return current
        if isinstance(full,dict) and full.get("matched"):row=_merge(row,full)
        if row.get("tmdb_id") and not _backdrop_ok(row.get("backdrop_local")):
            locked=dict(src);locked["_locked_tmdb_id"]=row.get("tmdb_id");locked["_locked_tmdb_type"]=row.get("media_type") or ("tv" if str(media_type)=="series" else "movie")
            row=_merge(row,resolver.resolve_backdrop_only(profile,media_type,locked,cancel_event=cancel_event) or {})
        if _cancelled(cancel_event) or not row.get("tmdb_id"):return row or current

        # Final descriptive metadata always comes from the raw exact-id TMDb
        # request for the selected information locale, with English fallback only
        # for fields TMDb left empty. Artwork/title-logo output above is untouched.
        localized=_localized_metadata(profile,media_type,src,row,cfg,cancel_event=cancel_event) or {}
        if localized:row=_merge(row,localized)
        if _cancelled(cancel_event):return current

        row["matched"]=bool(row.get("matched",True));row["identity_verified"]=bool(row.get("identity_verified") or row.get("identity_pointer_verified") or row.get("tmdb_id"))
        row["_details_authority_ready"]=True;row["_details_authority_version"]=4
        if localized:row["_details_metadata_language"]=_base_metadata_target(cfg)
        for _key in _DESCRIPTION_OVERLAY_KEYS+("_details_base_description","_description_overviews","_details_description_restore_needed","_details_visible_overview","_details_visible_overview_language","_details_visible_overview_source","_details_visible_overview_updated_at"):
            row.pop(_key,None)
        if _cancelled(cancel_event):return current
        save_detail_snapshot(profile,media_type,src,row)
        try:save_manifest(profile,media_type,src,row)
        except Exception as exc:optional_failure("details_authority.manifest",exc)
        return row
    except Exception as exc:
        optional_failure("details_authority.resolve",exc);return current
