"""Icon menu list extracted from ui.py without changing class behavior."""

from Components.MenuList import MenuList
from . import _
from .storage import load_settings

_FONT_SCALE_FACTORS = {"normal":1.0,"large":1.12,"larger":1.22,"xlarge":1.32}

def _scaled_ui_font(size):
    """Scale list fonts from the one global user setting without changing row geometry."""
    base=max(1,int(size or 1))
    if base < 12:return base
    try:mode=str(load_settings().get("font_scale") or "normal").lower()
    except Exception:mode="normal"
    factor=float(_FONT_SCALE_FACTORS.get(mode,1.0))
    # Bound growth so large modes remain usable on long provider/category names.
    return max(base,min(base+10,int(round(base*factor))))


def configure_icon_menu(**deps):
    globals().update(deps)

class IconMenuList(MenuList):
    """OpenBH-safe MultiContent list with dedicated row styles.

    Important: Enigma2 clips pixmaps and does not scale them. The renderer
    therefore selects a real on-disk icon matching the requested row size.
    """
    def __init__(self, rows=None, width=975, item_height=78, icon_size=60,
                 primary_font=36, secondary_font=23, row_style="double"):
        self.row_width = int(width)
        self.row_height = int(item_height)
        self.icon_size = int(icon_size)
        self.primary_font = _scaled_ui_font(primary_font)
        self.secondary_font = _scaled_ui_font(secondary_font)
        self.row_style = str(row_style or "double")
        MenuList.__init__(self, rows or [], enableWrapAround=True, content=eListboxPythonMultiContent)
        self.l.setFont(0, gFont("Regular", self.primary_font))
        self.l.setFont(1, gFont("Regular", max(self.secondary_font, 16)))
        self.l.setFont(2, gFont("Regular", max(self.secondary_font, 15)))
        self.l.setFont(3, gFont("Regular", max(17, self.primary_font - 2)))
        self.l.setFont(4, gFont("Regular", max(15, self.primary_font - 4)))
        self.l.setFont(5, gFont("Regular", max(14, self.primary_font - 6)))
        self.l.setFont(6, gFont("Regular", max(13, self.primary_font - 8)))
        self.l.setFont(7, gFont("Regular", _scaled_ui_font(22)))
        self.l.setFont(8, gFont("Regular", _scaled_ui_font(36)))
        self.l.setFont(9, gFont("Regular", _scaled_ui_font(33)))
        self.l.setFont(10, gFont("Regular", _scaled_ui_font(30)))
        self.l.setFont(11, gFont("Regular", _scaled_ui_font(26)))
        self.l.setFont(12, gFont("Regular", _scaled_ui_font(max(self.secondary_font + 2, 18))))
        # Portal source identity: MAC stays at the proven compact 14px.
        # M3U stays 16px and is centered in the same identity lane.
        self.l.setFont(13, gFont("Regular", _scaled_ui_font(14)))
        self.l.setFont(14, gFont("Regular", _scaled_ui_font(16)))
        self.l.setItemHeight(self.row_height)
        # Stage2: keep a cheap semantic snapshot of the currently rendered rows.
        # This lets repeated refresh paths update only rows whose visible content
        # or payload actually changed instead of rebuilding the whole eListbox.
        self._render_row_signatures = None
        self._render_layout_signature = self._layout_signature()

    def postWidgetCreate(self, instance):
        # Ultra Stalker never exposes Enigma2's native white scrollbar. Every
        # screen using IconMenuList owns paging/navigation visually instead.
        MenuList.postWidgetCreate(self, instance)
        try:instance.setScrollbarMode(2)
        except Exception:pass

    def _layout_signature(self):
        return (
            int(getattr(self, "row_width", 0) or 0),
            int(getattr(self, "row_height", 0) or 0),
            int(getattr(self, "icon_size", 0) or 0),
            int(getattr(self, "primary_font", 0) or 0),
            int(getattr(self, "secondary_font", 0) or 0),
            str(getattr(self, "row_style", "") or ""),
        )

    def set_layout(self, item_height, icon_size, primary_font, secondary_font, row_style="single"):
        next_height = int(item_height)
        next_icon = int(icon_size)
        next_primary = _scaled_ui_font(primary_font)
        next_secondary = _scaled_ui_font(secondary_font)
        next_style = str(row_style or "single")
        next_signature = (int(getattr(self, "row_width", 0) or 0), next_height, next_icon, next_primary, next_secondary, next_style)
        if next_signature == getattr(self, "_render_layout_signature", None):
            return False
        self.row_height = next_height
        self.icon_size = next_icon
        self.primary_font = next_primary
        self.secondary_font = next_secondary
        self.row_style = next_style
        self.l.setFont(0, gFont("Regular", self.primary_font))
        self.l.setFont(1, gFont("Regular", max(self.secondary_font, 16)))
        self.l.setFont(2, gFont("Regular", max(self.secondary_font, 15)))
        self.l.setFont(3, gFont("Regular", max(17, self.primary_font - 2)))
        self.l.setFont(4, gFont("Regular", max(15, self.primary_font - 4)))
        self.l.setFont(5, gFont("Regular", max(14, self.primary_font - 6)))
        self.l.setFont(6, gFont("Regular", max(13, self.primary_font - 8)))
        self.l.setFont(7, gFont("Regular", _scaled_ui_font(22)))
        self.l.setFont(8, gFont("Regular", _scaled_ui_font(36)))
        self.l.setFont(9, gFont("Regular", _scaled_ui_font(33)))
        self.l.setFont(10, gFont("Regular", _scaled_ui_font(30)))
        self.l.setFont(11, gFont("Regular", _scaled_ui_font(26)))
        self.l.setFont(12, gFont("Regular", _scaled_ui_font(max(self.secondary_font + 2, 18))))
        self.l.setFont(13, gFont("Regular", _scaled_ui_font(max(self.secondary_font + 1, 16))))
        self.l.setItemHeight(self.row_height)
        self._render_layout_signature = self._layout_signature()
        # Existing rows were authored for the previous geometry and must be
        # rebuilt the next time set_icon_rows() is called.
        self._render_row_signatures = None
        return True

    def _icon_candidate(self, icon_path):
        # Legacy list_icons/list_icons_32/list_icons_48 were three duplicate
        # copies of the same obsolete icon family. Current rows either own
        # their visual identity or may use the original semantic asset path.
        return icon_path

    def make_row(self, text, icon_path, data=None, secondary=""):
        title = _clean_display_text(text, 150)
        if not title and self.row_style != "home_recent":
            title = "Untitled"
        channel_meta = secondary if isinstance(secondary, dict) else None
        meta = _clean_display_text(secondary, 42) if channel_meta is None else ""
        png = None
        # R52: category_floating owns its icon layer below. The old generic path
        # decoded/looked up the same folder pixmap once here and then a second
        # time inside the category renderer for every row. On 911 Live categories
        # that meant ~1822 redundant cached_png calls before first paint.
        if self.row_style not in ("category_floating", "home_recent"):
            try:
                png = cached_png(self._icon_candidate(icon_path))
            except Exception as exc:
                optional_failure("ui", exc)
        row = [data if data is not None else title]
        iy = max(1, (self.row_height - self.icon_size) // 2)
        # Custom row renderers own their icon layer.  Drawing the generic icon
        # here as well produced the doubled/shadowed season/episode icon seen on
        # receiver screenshots.
        if png is not None and self.row_style not in (
            "portal_card", "episode_card", "series_floating",
            "settings_portal", "category_portal", "category_floating", "settings_episode", "favorite_live", "home_recent"
        ):
            row.append(MultiContentEntryPixmapAlphaTest(
                pos=(10, iy), size=(self.icon_size, self.icon_size), png=png
            ))

        tx = self.icon_size + 26
        usable = max(80, self.row_width - tx - 22)

        if self.row_style == "settings_portal":
            details=channel_meta or {}
            selected=bool(details.get("selected"))
            frame_path=details.get("row_selected_asset") if selected else details.get("row_asset")
            frame=None
            try:
                if frame_path and os.path.isfile(str(frame_path)):
                    frame=cached_png(str(frame_path))
            except Exception as exc:
                optional_failure("ui.settings_glass_frame",exc)

            # Categories grammar: floating icon + independent rounded glass card.
            card_x,card_y,card_h=100,4,max(40,self.row_height-8)
            card_w=max(300,self.row_width-card_x-6)
            if frame is not None:
                row.append(MultiContentEntryPixmapAlphaBlend(
                    pos=(card_x,card_y),size=(card_w,card_h),png=frame))
            icon=cached_png(self._icon_candidate(icon_path)) if icon_path else None
            if icon is not None:
                # Keep the floating icon fully inside the list canvas and above
                # the glass card. x=18 leaves safe pixels on both sides.
                row.append(MultiContentEntryPixmapAlphaBlend(
                    pos=(2,8),size=(38,38),png=icon))

            value=_clean_display_text(details.get("value"),36)
            value_w=205 if value else 0
            row.append(MultiContentEntryText(
                pos=(122,3),size=(max(160,self.row_width-142-value_w),self.row_height-6),
                font=0,flags=RT_HALIGN_LEFT|RT_VALIGN_CENTER,text=title))
            if value:
                row.append(MultiContentEntryText(
                    pos=(self.row_width-value_w-18,3),size=(value_w,self.row_height-6),
                    font=1,flags=RT_HALIGN_RIGHT|RT_VALIGN_CENTER,text=value))

        elif self.row_style == "settings_dialog":
            details=channel_meta or {}
            selected=bool(details.get("selected"))
            frame_path=details.get("row_selected_asset") if selected else details.get("row_asset")
            frame=None
            try:
                if frame_path and os.path.isfile(str(frame_path)):
                    frame=cached_png(str(frame_path))
            except Exception as exc:
                optional_failure("ui.settings_dialog_frame",exc)

            card_x=4
            card_y=4
            card_h=max(48,self.row_height-8)
            card_w=max(300,self.row_width-8)
            if frame is not None:
                row.append(MultiContentEntryPixmapAlphaBlend(
                    pos=(card_x,card_y),size=(card_w,card_h),png=frame))

            meta_text=_clean_display_text(details.get("meta") if isinstance(details,dict) else "",40)
            meta_w=220 if meta_text else 0
            row.append(MultiContentEntryText(
                pos=(22,4),size=(max(150,self.row_width-44-meta_w),self.row_height-8),
                font=0,flags=RT_HALIGN_LEFT|RT_VALIGN_CENTER,text=title))
            if meta_text:
                row.append(MultiContentEntryText(
                    pos=(self.row_width-meta_w-18,4),size=(meta_w,self.row_height-8),
                    font=1,flags=RT_HALIGN_RIGHT|RT_VALIGN_CENTER,text=meta_text))

        elif self.row_style == "home_recent":
            # R181: one eListbox row replaces four native Home widgets.  The
            # frame is the exact bundled 305x80 fixed-green row; title/meta and
            # the tiny progress bar are drawn by MultiContent in the same list.
            details=channel_meta or {}
            selected=bool(details.get("selected"))
            frame_path=details.get("row_selected_asset") if selected else details.get("row_asset")
            frame=None
            try:
                if frame_path and os.path.isfile(str(frame_path)):
                    frame=cached_png(str(frame_path))
            except Exception as exc:
                optional_failure("ui.home_recent_frame",exc)
            if frame is not None:
                row.append(MultiContentEntryPixmapAlphaBlend(
                    pos=(0,0),size=(min(305,self.row_width),80),png=frame))
            meta_text=_clean_display_text(details.get("meta") or "",46)
            # R201 Home Recent hierarchy: keep the title face clean pure white
            # with no dark duplicate pass, and use one shared bright mint for
            # every secondary line plus Portal Connected / Expires.
            # R204: the title was already numeric pure white, but OpenBH font
            # antialiasing made it read grey against the dark recent-card glass.
            # Draw the exact same glyph run twice in the exact same geometry.
            # This raises edge opacity/clarity without a glow, font change, asset,
            # timer, or any extra image work on navigation.
            row.append(MultiContentEntryText(
                pos=(12,8),size=(max(120,self.row_width-24),31),
                font=0,flags=RT_HALIGN_CENTER|RT_VALIGN_CENTER,text=title,
                color=0x00FFFFFF,color_sel=0x00FFFFFF))
            row.append(MultiContentEntryText(
                pos=(12,8),size=(max(120,self.row_width-24),31),
                font=0,flags=RT_HALIGN_CENTER|RT_VALIGN_CENTER,text=title,
                color=0x00FFFFFF,color_sel=0x00FFFFFF))
            if meta_text:
                row.append(MultiContentEntryText(
                    pos=(12,39),size=(max(120,self.row_width-24),22),
                    font=6,flags=RT_HALIGN_CENTER|RT_VALIGN_CENTER,text=meta_text,
                    color=0x00C8F5DE,color_sel=0x00C8F5DE))
            try:pct=max(0,min(100,int(details.get("progress") or 0)))
            except Exception:pct=0
            laser_path=str(details.get("progress_laser") or "")
            if bool(details.get("show_progress")) and pct>0 and laser_path:
                try:laser_png=cached_png(laser_path) if os.path.isfile(laser_path) else None
                except Exception as exc:
                    optional_failure("ui.home_recent_laser",exc);laser_png=None
                if laser_png is not None:
                    # Exact Cinematic laser frame.  The 34px cached frame is
                    # clipped to the row's lower 29px; its luminous beam stays
                    # centered at y=68 without any runtime scaling/animation.
                    row.append(MultiContentEntryPixmapAlphaBlend(
                        pos=(14,51),size=(277,29),png=laser_png))

        elif self.row_style == "favorite_live":
            # Favorites / Live: static text+picon rows, no glass/adaptive work.
            details=channel_meta or {};selected=bool(details.get("selected"))
            icon=None
            try:icon=cached_png(str(icon_path)) if icon_path else None
            except Exception as exc:optional_failure("ui.favorite_live_icon",exc)
            if icon is not None:
                if selected:row.append(MultiContentEntryPixmapAlphaBlend(pos=(10,9),size=(72,40),png=icon))
                else:row.append(MultiContentEntryPixmapAlphaBlend(pos=(14,11),size=(64,36),png=icon))
            row.append(MultiContentEntryText(
                pos=(96 if selected else 90,1),size=(max(180,self.row_width-112),self.row_height-2),
                font=(11 if selected else 0),flags=RT_HALIGN_LEFT|RT_VALIGN_CENTER,text=title,
                color=(0x00E676 if selected else 0xFFFFFF)))
        elif self.row_style == "category_menu_text":
            # Movies Categories MENU overlay only: pure floating text.
            # No card, no glass, no icon and no adaptive/dynamic artwork.
            details=channel_meta or {}
            selected=bool(details.get("selected"))
            font_idx=11 if selected else 0
            color=0x00E676 if selected else 0xFFFFFF
            # Keep the selection emphasis, but at a calmer size and with a wider
            # text runway so long menu labels remain fully visible.
            x=10 if selected else 18
            row.append(MultiContentEntryText(
                pos=(x,2),size=(max(200,self.row_width-x-12),self.row_height-4),
                font=font_idx,flags=RT_HALIGN_LEFT|RT_VALIGN_CENTER,text=title,color=color))
        elif self.row_style == "utility_glass":
            details=channel_meta or {}
            selected=bool(details.get("selected"))
            frame_path=details.get("row_selected_asset") if selected else details.get("row_asset")
            frame=None
            try:
                if frame_path and os.path.isfile(str(frame_path)):
                    frame=cached_png(str(frame_path))
            except Exception as exc:
                optional_failure("ui.utility_glass_frame",exc)
            card_x,card_y,card_h=62,4,max(40,self.row_height-8)
            card_w=max(300,self.row_width-card_x-6)
            if frame is not None:
                row.append(MultiContentEntryPixmapAlphaBlend(
                    pos=(card_x,card_y),size=(card_w,card_h),png=frame))
            icon=cached_png(self._icon_candidate(icon_path)) if icon_path else None
            if icon is not None:
                # Same safe floating-icon placement used by Settings.
                row.append(MultiContentEntryPixmapAlphaBlend(
                    pos=(2,8),size=(38,38),png=icon))

            meta_text=_clean_display_text(details.get("meta") if isinstance(details,dict) else "",40)
            meta_w=220 if meta_text else 0
            row.append(MultiContentEntryText(
                pos=(84,3),size=(max(150,self.row_width-104-meta_w),self.row_height-6),
                font=0,flags=RT_HALIGN_LEFT|RT_VALIGN_CENTER,text=title))
            if meta_text:
                row.append(MultiContentEntryText(
                    pos=(self.row_width-meta_w-18,3),size=(meta_w,self.row_height-6),
                    font=1,flags=RT_HALIGN_RIGHT|RT_VALIGN_CENTER,text=meta_text))
        elif self.row_style == "category_floating":
            # Lab25 Movies Categories: focus is expressed only by scale.  No
            # pointer, card, glow or animation is drawn.  The selected row uses
            # one prebuilt larger folder pixmap plus a larger fixed font; both
            # assets are cached and only the old/new rows are invalidated.
            details = channel_meta or {}
            selected = bool(details.get("selected"))
            # Movies Categories may use a wider transparent rail so long provider
            # names remain fully visible over the static background.
            try:card_w=max(426,min(1180,int(details.get("card_w") or (self.row_width-8))))
            except Exception:card_w=max(426,min(1180,self.row_width-8))
            # Keep the exact Lab25 folder artwork/geometry; selected state is the
            # same larger Lab25 yellow icon, with no card/glow/pointer.
            folder_path = asset("us89_folder_yellow_selected_54.png") if selected else str(icon_path or "")
            folder = None
            # R52: both category folder pixmaps are immutable receiver-local assets.
            # Cache the decoded ePixmap objects on the list instance and reuse them
            # for every row/selection update instead of calling cached_png per row.
            try:
                _cache_attr="_r52_category_folder_selected" if selected else "_r52_category_folder_normal"
                _path_attr=_cache_attr+"_path"
                if getattr(self,_path_attr,None)==folder_path:
                    folder=getattr(self,_cache_attr,None)
                if folder is None and folder_path:
                    folder=cached_png(folder_path)
                    setattr(self,_cache_attr,folder)
                    setattr(self,_path_attr,folder_path)
            except Exception as exc:optional_failure("ui.category_floating_icon",exc)
            if folder is None:
                try:
                    _fallback_path=asset("us89_folder_yellow_42.png")
                    if getattr(self,"_r52_category_folder_normal_path",None)==_fallback_path:
                        folder=getattr(self,"_r52_category_folder_normal",None)
                    if folder is None:
                        folder=cached_png(_fallback_path)
                        self._r52_category_folder_normal=folder
                        self._r52_category_folder_normal_path=_fallback_path
                except Exception:
                    folder=None
            if folder is not None:
                if selected:
                    row.append(MultiContentEntryPixmapAlphaBlend(pos=(12,6), size=(46,46), png=folder))
                else:
                    row.append(MultiContentEntryPixmapAlphaBlend(pos=(16,11), size=(34,34), png=folder))
            try:title_font=int(details.get("title_font",0) or 0)
            except Exception:title_font=0
            checked=bool(details.get("checked"))
            if selected:
                # Selected row should feel emphasized, but still compact enough
                # to keep the full category name visible while fitting 18 rows.
                try:selected_font=int(details.get("selected_title_font", {3:11,4:3,5:4,6:5}.get(title_font,11)) or {3:11,4:3,5:4,6:5}.get(title_font,11))
                except Exception:selected_font={3:11,4:3,5:4,6:5}.get(title_font,11)
                row.append(MultiContentEntryText(
                    pos=(72,1), size=(max(120,card_w-118),54), font=selected_font,
                    flags=RT_HALIGN_LEFT|RT_VALIGN_CENTER, text=title, color=0x00E676
                ))
            else:
                row.append(MultiContentEntryText(
                    pos=(64,2), size=(max(120,card_w-108),52), font=title_font,
                    flags=RT_HALIGN_LEFT|RT_VALIGN_CENTER, text=title, color=0xFFFFFF
                ))
            if checked:
                row.append(MultiContentEntryText(
                    pos=(card_w-54,3), size=(42,58), font=7,
                    flags=RT_HALIGN_CENTER|RT_VALIGN_CENTER, text="✓", color=0x70E4B0
                ))

        elif self.row_style == "category_portal":
            details = channel_meta or {}
            selected = bool(details.get("selected"))
            frame_path = details.get("row_selected_asset") if selected else details.get("row_asset")
            frame = None
            try:
                if frame_path and os.path.isfile(str(frame_path)):
                    frame = cached_png(str(frame_path))
            except Exception as exc:
                optional_failure("ui.category_portal_frame", exc)
            # Categories now use the Settings grammar: one self-contained floating
            # glass row, with the folder icon *inside* the card rather than hanging
            # outside it.  card_w is global for the whole category list.
            try:card_w=max(426,min(860,int(details.get("card_w") or (self.row_width-8))))
            except Exception:card_w=max(426,min(860,self.row_width-8))
            card_x,card_y,card_h=0,2,62
            if frame is not None:
                row.append(MultiContentEntryPixmapAlphaBlend(pos=(card_x,card_y), size=(card_w,card_h), png=frame))
            folder = None
            try:folder=cached_png(str(icon_path)) if icon_path else None
            except Exception as exc:optional_failure("ui.category_portal_icon",exc)
            if folder is None:
                folder=cached_png(asset("us89_folder_yellow_42.png"))
            if folder is not None:
                row.append(MultiContentEntryPixmapAlphaBlend(pos=(18,12), size=(40,40), png=folder))
            try:title_font=int(details.get("title_font",0) or 0)
            except Exception:title_font=0
            checked=bool(details.get("checked"))
            # Reserve a permanent right-side selection column. This keeps the
            # mark independent from Arabic/Latin provider text and prevents the
            # old check icon from replacing the yellow folder identity.
            row.append(MultiContentEntryText(
                pos=(76,3), size=(max(120,card_w-156),58), font=title_font,
                flags=RT_HALIGN_LEFT|RT_VALIGN_CENTER, text=title, color=0xFFFFFF
            ))
            if checked:
                row.append(MultiContentEntryText(
                    pos=(card_w-64,3), size=(42,58), font=7,
                    flags=RT_HALIGN_CENTER|RT_VALIGN_CENTER, text="✓", color=0x70E4B0
                ))
        elif self.row_style == "settings_episode":
            # Settings rows deliberately reuse the exact visual grammar of the
            # approved Series episode rail: 426x72 adaptive glass, semantic icon,
            # title on top and current value directly below it. Portal pages can
            # optionally split that second line into independently-colored fields
            # without changing the Settings geometry.
            details = channel_meta or {}
            selected = bool(details.get("selected"))
            frame_path = details.get("row_selected_asset") if selected else details.get("row_asset")
            frame = None
            try:
                if frame_path and os.path.isfile(str(frame_path)):
                    frame = cached_png(str(frame_path))
            except Exception as exc:
                optional_failure("ui.settings_episode_frame", exc)
            if frame is None:
                frame = cached_png(asset("series_floating_row_selected.png" if selected else "series_floating_row.png"))
            if frame is not None:
                compact = bool(details.get("compact_row"))
                row.append(MultiContentEntryPixmapAlphaBlend(pos=(0,0 if compact else 4), size=(426,72), png=frame))

            hide_icon = bool(details.get("hide_icon"))
            icon = None
            if not hide_icon:
                try:
                    icon = cached_png(str(icon_path)) if icon_path else None
                except Exception as exc:
                    optional_failure("ui.settings_episode_icon", exc)
            source_number = details.get("source_number")
            portal_numbered = source_number is not None
            if portal_numbered:
                try:
                    source_number_int = int(source_number)
                    number_text = str(source_number_int)
                except Exception:
                    source_number_int = 0
                    number_text = str(source_number or "")[:4]
                # Keep the proven large number below 1000.  Four-digit source
                # numbers need only a small reduction so 1000+ remains fully
                # visible without changing the look of 1..999.
                four_digits = source_number_int >= 1000
                # 33pt was still slightly tight on the receiver. Keep 1..999 at
                # the proven 36pt size, and reduce only 1000+ to 30pt.
                number_font = 10 if four_digits else 8
                number_pos = (7,8) if four_digits else (12,8)
                number_size = (68,56) if four_digits else (58,56)
                row.append(MultiContentEntryText(
                    pos=number_pos, size=number_size, font=number_font,
                    flags=RT_HALIGN_CENTER|RT_VALIGN_CENTER, text=number_text, color=0xFFFFFF
                ))
            if icon is not None:
                kind = str(details.get("kind") or "").strip().lower()
                season_icon = (kind == "season")
                icon_size = 32 if season_icon else 40
                icon_x = (82 if portal_numbered else 26) if season_icon else (78 if portal_numbered else 22)
                icon_y = 23 if season_icon else 19
                row.append(MultiContentEntryPixmapAlphaBlend(pos=(icon_x,icon_y), size=(icon_size,icon_size), png=icon))

            value = _clean_display_text(details.get("value"), 42)
            status_text = _clean_display_text(details.get("status_text"), 18)
            identity_text = _clean_display_text(details.get("identity_text"), 22)
            server_text = _clean_display_text(details.get("server_text"), 14)
            if (not server_text and bool(details.get("show_online_connection_fallback")) and
                    str(status_text or "").strip().upper() == "ONLINE"):
                server_text = "0/1"
            center_title = bool(details.get("center_title")) and not any((value,status_text,identity_text,server_text))
            # Keep Settings/Episodes unchanged by default. Cinematic Beta81
            # opts into a wider adaptive range so long catalogue names fit.
            title_len = len(str(title or ""))
            if bool(details.get("adaptive_title")):
                title_font = 0 if title_len <= 17 else (3 if title_len <= 25 else (4 if title_len <= 33 else (5 if title_len <= 43 else 6)))
            else:
                title_font = 0 if title_len <= 22 else (3 if title_len <= 30 else 4)
            if portal_numbered:
                # Keep the large source number and type icon independent from text.
                # The title starts farther right than the status line so it never
                # visually falls into ONLINE/MAC on compact receiver fonts.
                text_x = 132
                text_w = 280
            else:
                text_x = 18 if hide_icon else 80
                text_w = 390 if hide_icon else 304
            value_color = details.get("value_color")
            status_color = details.get("status_color")
            server_color = details.get("server_color")
            try:
                value_color = int(value_color) if value_color is not None else None
            except Exception:
                value_color = None
            try:
                status_color = int(status_color) if status_color is not None else value_color
            except Exception:
                status_color = value_color
            try:
                server_color = int(server_color) if server_color is not None else value_color
            except Exception:
                server_color = value_color
            utility_accent_strong = bool(details.get("utility_accent_strong"))
            accent_font = 12 if utility_accent_strong else 1
            # R69: every portal source identity owns the same stable lane. MAC
            # is compact/right-aligned; short M3U is centered in that lane.
            # ONLINE is deliberately left in its original position/font.
            portal_mac_compact = bool(details.get("portal_mac_compact"))
            portal_identity_right = bool(details.get("portal_identity_right"))
            portal_identity_source = bool(details.get("portal_identity_source"))
            identity_font = (14 if portal_identity_source else 13) if portal_identity_right else (12 if utility_accent_strong else 2)
            accent_shadow = 0x001015

            # Portal list only: draw the exact Cinematic green/yellow remote
            # button beside the focused 426px card.  No look-alike asset and no
            # synthetic numbers are accepted by the caller.
            connection_badge_text = _clean_display_text(details.get("connection_badge_text"), 18)
            connection_badge_asset = str(details.get("connection_badge_asset") or "")
            if connection_badge_text and connection_badge_asset:
                badge_png = None
                try:
                    if os.path.isfile(connection_badge_asset):
                        badge_png = cached_png(connection_badge_asset)
                except Exception as exc:
                    optional_failure("ui.settings_episode_connection_badge", exc)
                if badge_png is not None:
                    row.append(MultiContentEntryPixmapAlphaBlend(
                        pos=(444,19), size=(170,42), png=badge_png))
                    row.append(MultiContentEntryText(
                        pos=(452,19), size=(154,42), font=7,
                        flags=RT_HALIGN_CENTER|RT_VALIGN_CENTER,
                        text=connection_badge_text, color=0xFFFFFF))

            if center_title:
                row.append(MultiContentEntryText(
                    pos=(text_x,4), size=(text_w,72), font=title_font,
                    flags=RT_HALIGN_LEFT|RT_VALIGN_CENTER, text=title, color=0xFFFFFF
                ))
            else:
                # Seasons/Episodes use the same 73px glass as Cinematic.  Give the
                # title a little more lower breathing-room when a laser occupies
                # the second line; this avoids the old top-heavy/cropped look.
                title_y = 10 if bool(details.get("series_title_polish")) else 7
                title_h = 31 if bool(details.get("series_title_polish")) else 36
                row.append(MultiContentEntryText(
                    pos=(text_x,title_y), size=(text_w,title_h), font=title_font,
                    flags=RT_HALIGN_LEFT|RT_VALIGN_CENTER, text=title, color=0xFFFFFF
                ))
                if status_text or identity_text or server_text:
                    x = 132 if portal_numbered else 80
                    if status_text:
                        # Portal Manager needs enough runway for NOT CHECKED /
                        # DISABLED and their translated equivalents. Keep every
                        # other Settings/Episode row on the proven 72px lane.
                        status_w = 118 if portal_numbered else 72
                        if utility_accent_strong:
                            row.append(MultiContentEntryText(
                                pos=(x+1,40), size=(status_w,26), font=accent_font,
                                flags=RT_HALIGN_LEFT|RT_VALIGN_CENTER, text=status_text, color=accent_shadow))
                        kwargs = dict(pos=(x,39), size=(status_w,26), font=accent_font,
                                      flags=RT_HALIGN_LEFT|RT_VALIGN_CENTER, text=status_text)
                        if status_color is not None:
                            kwargs["color"] = status_color
                        row.append(MultiContentEntryText(**kwargs))
                        # Keep MAC/M3U in its own right-aligned lane after the
                        # wider status text, without changing the 426px card.
                        x = (252 if (portal_numbered and portal_identity_right) else (252 if portal_numbered else 152))
                    if identity_text:
                        identity_w = ((154 if (portal_numbered and portal_identity_right) else (190 if portal_numbered else 128)) if server_text else (154 if (portal_numbered and portal_identity_right) else (200 if portal_numbered else 232)))
                        identity_color = details.get("identity_color") if portal_numbered else None
                        try:
                            identity_color = int(identity_color) if identity_color is not None else value_color
                        except Exception:
                            identity_color = value_color
                        # Portal MAC readability: keep the original compact font size,
                        # then add one cheap 2px dark shadow pass so the red text stays
                        # crisp over bright or busy backdrops without changing row layout.
                        if portal_identity_source:
                            identity_flags=RT_HALIGN_CENTER|RT_VALIGN_CENTER
                        else:
                            identity_flags=(RT_HALIGN_RIGHT|RT_VALIGN_CENTER) if portal_identity_right else (RT_HALIGN_LEFT|RT_VALIGN_CENTER)
                        if portal_numbered or utility_accent_strong:
                            row.append(MultiContentEntryText(
                                pos=(x + 1,40), size=(identity_w,26), font=identity_font,
                                flags=identity_flags, text=identity_text, color=accent_shadow
                            ))
                        kwargs = dict(pos=(x,39), size=(identity_w,26), font=identity_font,
                                      flags=identity_flags, text=identity_text)
                        if identity_color is not None:
                            kwargs["color"] = identity_color
                        row.append(MultiContentEntryText(**kwargs))
                    if server_text:
                        # Numbers only. No dot, no green decoration, no status
                        # colour coupling. Keep the count crisp and neutral.
                        row.append(MultiContentEntryText(
                            pos=(286,38), size=(120,26), font=1,
                            flags=RT_HALIGN_LEFT|RT_VALIGN_CENTER, text=server_text, color=0xEEF6FB
                        ))
                else:
                    progress_pct=0
                    try:progress_pct=max(0,min(100,int(details.get("watch_progress") or 0)))
                    except Exception:progress_pct=0
                    full_progress_line=bool(details.get("watch_progress_full_line"))
                    if value and not full_progress_line:
                        value_w=132 if progress_pct>0 else text_w
                        if utility_accent_strong:
                            row.append(MultiContentEntryText(
                                pos=(text_x+1,40), size=(value_w,26), font=accent_font,
                                flags=RT_HALIGN_LEFT|RT_VALIGN_CENTER, text=value, color=accent_shadow))
                        kwargs = dict(pos=(text_x,39), size=(value_w,26), font=accent_font,
                                      flags=RT_HALIGN_LEFT|RT_VALIGN_CENTER, text=value)
                        if value_color is not None:kwargs["color"] = value_color
                        row.append(MultiContentEntryText(**kwargs))
                    if progress_pct>0:
                        neon_path=str(details.get("watch_progress_neon") or "");neon=None
                        try:
                            if neon_path and os.path.isfile(neon_path):neon=cached_png(neon_path)
                        except Exception as exc:optional_failure("ui.settings_episode_progress_neon",exc)
                        below_title=bool(details.get("watch_progress_below_title"))
                        native_w=300
                        try:native_w=max(80,min(600,int(details.get("watch_progress_native_width") or 300)))
                        except Exception:native_w=300
                        pct_color=value_color if value_color is not None else 0xEEF6FB
                        if not below_title:
                            # release: draw the percentage first, then the full cinematic
                            # laser. At 100% the luminous endpoint therefore remains the
                            # top-most visual instead of being clipped/covered by "100%".
                            row.append(MultiContentEntryText(pos=(362,39),size=(48,26),font=1,
                                flags=RT_HALIGN_RIGHT|RT_VALIGN_CENTER,text=("%d%%"%progress_pct),color=pct_color))
                        if neon is not None:
                            if full_progress_line:
                                if below_title:
                                    # Episode rail: play icon stays fully visible at far left;
                                    # the exact Player laser begins under the *episode name*.
                                    row.append(MultiContentEntryPixmapAlphaBlend(pos=(80,38),size=(native_w,34),png=neon))
                                else:
                                    # Cinematic catalogue keeps its proven native geometry.
                                    row.append(MultiContentEntryPixmapAlphaBlend(pos=(46,38),size=(300,34),png=neon))
                            else:
                                row.append(MultiContentEntryPixmapAlphaBlend(pos=(150,43),size=(188,18),png=neon))
                        if below_title:
                            row.append(MultiContentEntryText(pos=(354,39),size=(56,26),font=1,
                                flags=RT_HALIGN_RIGHT|RT_VALIGN_CENTER,text=("%d%%"%progress_pct),color=pct_color))
        elif self.row_style == "cinematic_catalog":
            # Test18 Classic Fusion: catalogue titles deliberately reuse the
            # approved episode/settings floating-row grammar, but without an icon.
            # Primary title sits on top and a restrained quality/year line sits below.
            details = channel_meta or {}
            selected = bool(details.get("selected"))
            frame_path = details.get("row_selected_asset") if selected else details.get("row_asset")
            frame = None
            try:
                if frame_path and os.path.isfile(str(frame_path)):
                    frame = cached_png(str(frame_path))
            except Exception as exc:
                optional_failure("ui.cinematic_catalog_frame", exc)
            if frame is None:
                frame = cached_png(asset("series_floating_row_selected.png" if selected else "series_floating_row.png"))
            if frame is not None:
                # Exact approved episode/settings card geometry.
                row.append(MultiContentEntryPixmapAlphaBlend(pos=(0,4), size=(426,72), png=frame))
            sub = _clean_display_text(details.get("secondary"), 48)
            title_len = len(str(title or ""))
            title_font = 0 if title_len <= 28 else (3 if title_len <= 38 else 4)
            row.append(MultiContentEntryText(
                pos=(22,7), size=(382,36), font=title_font,
                flags=RT_HALIGN_LEFT|RT_VALIGN_CENTER, text=title, color=0xFFFFFF
            ))
            if sub:
                row.append(MultiContentEntryText(
                    pos=(22,39), size=(382,26), font=1,
                    flags=RT_HALIGN_LEFT|RT_VALIGN_CENTER, text=sub
                ))
        elif self.row_style == "series_floating":
            details = channel_meta or {}
            selected = bool(details.get("selected"))
            kind = str(details.get("kind") or "episode")
            hide_icon = bool(details.get("hide_icon"))
            frame_path = details.get("row_selected_asset") if selected else details.get("row_asset")
            frame = None
            try:
                if frame_path and os.path.isfile(str(frame_path)):
                    frame = cached_png(str(frame_path))
            except Exception as exc:
                optional_failure("ui.series_floating_frame", exc)
            if frame is None:
                fallback_name = "series_floating_row_selected.png" if selected else "series_floating_row.png"
                frame = cached_png(asset(fallback_name))
            if frame is not None:
                row.append(MultiContentEntryPixmapAlphaBlend(pos=(0,4), size=(426,72), png=frame))
            # Cinematic catalogue reuses this exact native Series row renderer.
            # The only deliberate difference is hiding the semantic season/episode
            # icon, exactly as requested; geometry/glass/selected treatment stay
            # byte-for-byte in the same code path used by the classic hierarchy.
            if not hide_icon:
                if kind == "season":
                    icon_name = "us166_details_folder_yellow_32.png"
                else:
                    icon_name = "us86_episode_40.png"
                icon = cached_png(asset(icon_name))
                if icon is not None:
                    row.append(MultiContentEntryPixmapAlphaBlend(pos=((26,23) if kind == "season" else (22,19)), size=((32,32) if kind == "season" else (40,40)), png=icon))
            right = _clean_display_text(details.get("right"), 48)
            badge_text = _clean_display_text(details.get("badge"), 18)
            text_x = 22 if hide_icon else 80
            text_w = 382 if hide_icon else 304
            row.append(MultiContentEntryText(pos=(text_x,7), size=(text_w,36), font=0, flags=RT_HALIGN_LEFT|RT_VALIGN_CENTER, text=title, color=0xFFFFFF))
            sub = right or badge_text
            if sub:
                row.append(MultiContentEntryText(pos=(text_x,39), size=(text_w,26), font=1, flags=RT_HALIGN_LEFT|RT_VALIGN_CENTER, text=sub, color=0xFFFFFF))
        elif self.row_style == "series_compact":
            details = channel_meta or {}
            selected = bool(details.get("selected"))
            kind = str(details.get("kind") or "episode")
            bg = "#102637" if selected else "#07131d"
            accent = "#55d8ff" if selected else "#17384a"
            w = max(220, self.row_width - 4)
            row.append(MultiContentEntryText(pos=(0,3), size=(w,56), font=2, flags=RT_HALIGN_LEFT|RT_VALIGN_CENTER, text="", backcolor=int(bg[1:],16)))
            row.append(MultiContentEntryText(pos=(0,3), size=(5,56), font=2, flags=RT_HALIGN_LEFT|RT_VALIGN_CENTER, text="", backcolor=int(accent[1:],16)))
            icon_name = "us166_details_folder_yellow_32.png" if kind == "season" else "us86_episode_40.png"
            icon = cached_png(asset(icon_name))
            row.append(MultiContentEntryPixmapAlphaTest(pos=((16,15) if kind == "season" else (12,11)), size=((32,32) if kind == "season" else (40,40)), png=icon))
            right = _clean_display_text(details.get("right"), 24)
            badge_text = _clean_display_text(details.get("badge"), 12)
            row.append(MultiContentEntryText(pos=(62,4), size=(max(120,w-76),30), font=0, flags=RT_HALIGN_LEFT|RT_VALIGN_CENTER, text=title))
            sub = right or badge_text
            if sub:
                row.append(MultiContentEntryText(pos=(62,31), size=(max(120,w-76),22), font=1, flags=RT_HALIGN_LEFT|RT_VALIGN_CENTER, text=sub))
        elif self.row_style == "series_luxe":
            details = channel_meta or {}
            selected = bool(details.get("selected"))
            kind = str(details.get("kind") or "episode")
            # Us42: spacious streaming-style card. Selection is expressed by a
            # bright cyan rail and a filled surface rather than a thin legacy box.
            bg = "#102c3d" if selected else "#071722"
            accent = "#43d7ff" if selected else "#173b50"
            row.append(MultiContentEntryText(pos=(0,4), size=(972,72), font=2, flags=RT_HALIGN_LEFT|RT_VALIGN_CENTER, text="", backcolor=int(bg[1:],16)))
            row.append(MultiContentEntryText(pos=(0,4), size=(6,72), font=2, flags=RT_HALIGN_LEFT|RT_VALIGN_CENTER, text="", backcolor=int(accent[1:],16)))
            icon_name = "us166_details_folder_yellow_32.png" if kind == "season" else "us86_episode_40.png"
            icon = cached_png(asset(icon_name))
            row.append(MultiContentEntryPixmapAlphaTest(pos=(22,13), size=(54,54), png=icon))
            right = _clean_display_text(details.get("right"), 30)
            badge_text = _clean_display_text(details.get("badge"), 16)
            row.append(MultiContentEntryText(pos=(96,7), size=(560,42), font=0, flags=RT_HALIGN_LEFT|RT_VALIGN_CENTER, text=title))
            if right:
                row.append(MultiContentEntryText(pos=(96,45), size=(560,24), font=1, flags=RT_HALIGN_LEFT|RT_VALIGN_CENTER, text=right))
            if badge_text:
                row.append(MultiContentEntryText(pos=(730,22), size=(205,36), font=2, flags=RT_HALIGN_CENTER|RT_VALIGN_CENTER, text=badge_text))
        elif self.row_style == "episode_card":
            details = channel_meta or {}
            selected = bool(details.get("selected"))
            kind = str(details.get("kind") or "episode")
            frame = cached_png(asset("us25_episode_card_selected.png" if selected else "us25_episode_card.png"))
            row.append(MultiContentEntryPixmapAlphaTest(pos=(0, 2), size=(1070,60), png=frame))
            if kind == "season":
                icon_name = "us166_details_folder_yellow_32.png"
            else:
                icon_name = "us86_episode_40.png"
            icon = cached_png(asset(icon_name))
            row.append(MultiContentEntryPixmapAlphaTest(pos=(16, 9), size=(46,46), png=icon))
            right = _clean_display_text(details.get("right"), 30)
            badge_text = _clean_display_text(details.get("badge"), 14)
            row.append(MultiContentEntryText(pos=(82, 5), size=(560, 52), font=0, flags=RT_HALIGN_LEFT | RT_VALIGN_CENTER, text=title))
            row.append(MultiContentEntryText(pos=(650, 5), size=(235, 52), font=1, flags=RT_HALIGN_RIGHT | RT_VALIGN_CENTER, text=right))
            if badge_text:
                pill_name = "us66_neutral_pill_quality.png"
                pill_img = cached_png(asset(pill_name))
                row.append(MultiContentEntryPixmapAlphaTest(pos=(918, 17), size=(126,30), png=pill_img))
                row.append(MultiContentEntryText(pos=(921, 16), size=(120,30), font=2, flags=RT_HALIGN_CENTER | RT_VALIGN_CENTER, text=badge_text))
        elif self.row_style == "download_glass":
            details = channel_meta or {}
            selected = bool(details.get("selected"))
            frame_path = details.get("row_selected_asset") if selected else details.get("row_asset")
            frame = None
            try:
                if frame_path and os.path.isfile(str(frame_path)):
                    frame = cached_png(str(frame_path))
            except Exception as exc:
                optional_failure("ui.download_glass_frame", exc)
            if frame is not None:
                row.append(MultiContentEntryPixmapAlphaBlend(pos=(0,4), size=(1090,72), png=frame))
            status = _clean_display_text(details.get("status"), 14)
            if status == "COMPLETED":
                icon = cached_png(asset("settings_icons_40/progress_safe.png"))
                if icon is not None:
                    row.append(MultiContentEntryPixmapAlphaBlend(pos=(12,11), size=(58,58), png=icon))
            progress = _clean_display_text(details.get("progress"), 12)
            row.append(MultiContentEntryText(pos=(84,7), size=(820,34), font=0, flags=RT_HALIGN_LEFT|RT_VALIGN_CENTER, text=title))
            sub = status.strip()
            if sub:
                row.append(MultiContentEntryText(pos=(84,39), size=(820,26), font=1, flags=RT_HALIGN_LEFT|RT_VALIGN_CENTER, text=sub))
            if progress:
                row.append(MultiContentEntryText(pos=(930,6), size=(125,58), font=1, flags=RT_HALIGN_CENTER|RT_VALIGN_CENTER, text=progress))
        elif self.row_style == "channel":
            details = channel_meta or {}
            number = _clean_display_text(details.get("number"), 5)
            now_title = _clean_display_text(details.get("now"), 75) or _("No EPG information")
            next_title = _clean_display_text(details.get("next"), 48)
            badges = _clean_display_text(details.get("badges"), 22)
            number_w = 55 if number else 8
            title_x = tx + number_w
            right_w = 175
            if number:
                row.append(MultiContentEntryText(pos=(tx,0), size=(48,self.row_height), font=1,
                    flags=RT_HALIGN_RIGHT | RT_VALIGN_CENTER, text=number))
            row.append(MultiContentEntryText(pos=(title_x,1), size=(usable-number_w-right_w,29), font=0,
                flags=RT_HALIGN_LEFT | RT_VALIGN_CENTER, text=title))
            lower = (_("NOW") + "  " + now_title) if now_title else ""
            row.append(MultiContentEntryText(pos=(title_x,29), size=(usable-number_w-right_w,25), font=1,
                flags=RT_HALIGN_LEFT | RT_VALIGN_CENTER, text=lower))
            if badges:
                row.append(MultiContentEntryText(pos=(self.row_width-right_w-12,0), size=(right_w,self.row_height), font=1,
                    flags=RT_HALIGN_RIGHT | RT_VALIGN_CENTER, text=badges))
        elif self.row_style == "double" and meta:
            primary_h = max(30, int(self.row_height * 0.58))
            secondary_y = primary_h - 1
            secondary_h = max(18, self.row_height - secondary_y)
            row.append(MultiContentEntryText(
                pos=(tx, 0), size=(usable, primary_h), font=0,
                flags=RT_HALIGN_LEFT | RT_VALIGN_CENTER, text=title
            ))
            row.append(MultiContentEntryText(
                pos=(tx, secondary_y), size=(usable, secondary_h), font=1,
                flags=RT_HALIGN_LEFT | RT_VALIGN_CENTER, text=meta
            ))
        elif self.row_style == "compact_meta" and meta:
            # One physical line: title left, short metadata right.
            meta_w = min(230, max(110, int(usable * 0.28)))
            gap = 18
            title_w = max(120, usable - meta_w - gap)
            row.append(MultiContentEntryText(
                pos=(tx, 0), size=(title_w, self.row_height), font=0,
                flags=RT_HALIGN_LEFT | RT_VALIGN_CENTER, text=title
            ))
            row.append(MultiContentEntryText(
                pos=(tx + title_w + gap, 0), size=(meta_w, self.row_height), font=1,
                flags=RT_HALIGN_RIGHT | RT_VALIGN_CENTER, text=meta
            ))
        else:
            row.append(MultiContentEntryText(
                pos=(tx, 0), size=(usable, self.row_height), font=0,
                flags=RT_HALIGN_LEFT | RT_VALIGN_CENTER, text=title
            ))
        return row

    def wrap_up(self):
        """Move up with an explicit last->first wrap on every Enigma2 build."""
        try:
            total=len(self.list or [])
            if total <= 0:return
            idx=int(self.getSelectedIndex() or 0)
            if idx <= 0:self.moveToIndex(total-1)
            else:self.up()
        except Exception:
            try:self.up()
            except Exception as exc:optional_failure("ui.silent_guard",exc)

    def wrap_down(self):
        """Move down with an explicit first<-last wrap on every Enigma2 build."""
        try:
            total=len(self.list or [])
            if total <= 0:return
            idx=int(self.getSelectedIndex() or 0)
            if idx >= total-1:self.moveToIndex(0)
            else:self.down()
        except Exception:
            try:self.down()
            except Exception as exc:optional_failure("ui.silent_guard",exc)

    def _visible_page_rows(self):
        """Best-effort number of fully visible rows in the current list widget."""
        try:
            if self.instance is not None:
                height=int(self.instance.size().height() or 0)
                if height > 0 and int(self.row_height or 0) > 0:
                    return max(1,height//int(self.row_height))
        except Exception as exc:
            optional_failure("ui.list_page_rows",exc)
        return max(1,min(15,len(self.list or []) or 1))

    def page_shift(self, delta):
        """LEFT/RIGHT paging with the same row preserved when possible.

        At the outer edge, LEFT from anywhere on the first page goes to item 1;
        RIGHT on the final page goes to the final item. UP/DOWN remain global
        wrap through wrap_up()/wrap_down().
        """
        try:
            total=len(self.list or [])
            if total <= 0:return
            idx=max(0,min(total-1,int(self.getSelectedIndex() or 0)))
            rows=self._visible_page_rows()
            page=idx//rows
            row=idx%rows
            last_page=max(0,(total-1)//rows)
            if int(delta)<0:
                if page>0:
                    target=max(0,(page-1)*rows+row)
                else:
                    target=0
            else:
                if page<last_page:
                    target=min(total-1,(page+1)*rows+row)
                else:
                    target=total-1
            self.moveToIndex(target)
        except Exception as exc:
            optional_failure("ui.list_page_shift",exc)

    def page_left(self):
        self.page_shift(-1)

    def page_right(self):
        self.page_shift(1)

    @staticmethod
    def _signature_value(value):
        if value is None or isinstance(value, (str, int, float, bool)):
            return value
        if isinstance(value, dict):
            try:
                return tuple(sorted((str(k), IconMenuList._signature_value(v)) for k, v in value.items()))
            except Exception:
                return ("dict", id(value))
        if isinstance(value, (list, tuple)):
            try:return tuple(IconMenuList._signature_value(v) for v in value)
            except Exception:return (type(value).__name__, id(value))
        return (type(value).__name__, id(value))

    @classmethod
    def _source_row_signature(cls, row):
        try:
            title = cls._signature_value(row[0] if len(row) > 0 else "")
            icon = cls._signature_value(row[1] if len(row) > 1 else "")
            data = row[2] if len(row) > 2 else None
            # The payload is returned by selection logic even when it is not
            # visually rendered. Preserve object identity for mutable rows.
            data_sig = cls._signature_value(data) if isinstance(data, (str, int, float, bool, type(None))) else (type(data).__name__, id(data))
            secondary = cls._signature_value(row[3] if len(row) > 3 else "")
            return (title, icon, data_sig, secondary)
        except Exception:
            return ("row", id(row))

    def set_icon_rows(self, rows):
        rows = list(rows or [])
        signatures = [self._source_row_signature(row) for row in rows]
        previous = getattr(self, "_render_row_signatures", None)
        current_layout = self._layout_signature()
        layout_same = current_layout == getattr(self, "_render_layout_signature", None)

        if layout_same and previous is not None and len(previous) == len(signatures):
            changed = [i for i, (old, new) in enumerate(zip(previous, signatures)) if old != new]
            if not changed:
                return False
            # Small refreshes (selection chrome, EPG, watched state) should not
            # force Enigma2 to rebuild every MultiContent row. For a wholesale
            # data replacement, one native setList remains cheaper.
            partial_limit = max(8, min(32, max(1, len(signatures) // 3)))
            if len(changed) <= partial_limit:
                try:
                    for index in changed:
                        row = rows[index]
                        built = self.make_row(row[0], row[1], row[2], row[3] if len(row) >= 4 else "")
                        self.list[index] = built
                        self.l.invalidateEntry(index)
                    self._render_row_signatures = signatures
                    return True
                except Exception:
                    # Fall through to a complete rebuild if this Enigma2 build
                    # does not support per-entry invalidation reliably.
                    pass

        built = []
        for row in rows:
            if len(row) >= 4:
                built.append(self.make_row(row[0], row[1], row[2], row[3]))
            else:
                built.append(self.make_row(row[0], row[1], row[2]))
        self.setList(built)
        self._render_row_signatures = signatures
        self._render_layout_signature = current_layout
        return True

    def update_icon_row(self, index, row):
        """Replace one rendered row without rebuilding the entire list."""
        try:
            index = int(index)
            if index < 0 or index >= len(self.list):
                return False
            signature = self._source_row_signature(row)
            known = getattr(self, "_render_row_signatures", None)
            if known is not None and index < len(known) and known[index] == signature:
                return False
            built = self.make_row(row[0], row[1], row[2], row[3] if len(row) >= 4 else "")
            self.list[index] = built
            try:
                self.l.invalidateEntry(index)
            except Exception:
                self.l.setList(self.list)
            if known is not None and index < len(known):
                known[index] = signature
            return True
        except Exception:
            return False

