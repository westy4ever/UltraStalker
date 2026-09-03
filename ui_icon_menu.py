"""Icon menu list extracted from ui.py without changing class behavior."""

from Components.MenuList import MenuList

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
        self.primary_font = int(primary_font)
        self.secondary_font = int(secondary_font)
        self.row_style = str(row_style or "double")
        MenuList.__init__(self, rows or [], enableWrapAround=True, content=eListboxPythonMultiContent)
        self.l.setFont(0, gFont("Regular", self.primary_font))
        self.l.setFont(1, gFont("Regular", self.secondary_font))
        self.l.setFont(2, gFont("Regular", max(13, self.secondary_font - 2)))
        self.l.setItemHeight(self.row_height)

    def set_layout(self, item_height, icon_size, primary_font, secondary_font, row_style="single"):
        self.row_height = int(item_height)
        self.icon_size = int(icon_size)
        self.primary_font = int(primary_font)
        self.secondary_font = int(secondary_font)
        self.row_style = str(row_style or "single")
        self.l.setFont(0, gFont("Regular", self.primary_font))
        self.l.setFont(1, gFont("Regular", self.secondary_font))
        self.l.setFont(2, gFont("Regular", max(13, self.secondary_font - 2)))
        self.l.setItemHeight(self.row_height)

    def _icon_candidate(self, icon_path):
        base = os.path.basename(str(icon_path or ""))
        if not base.startswith(("ico_", "logo_letter_")):
            return icon_path
        if self.icon_size <= 34:
            folder = "list_icons_32"
        elif self.icon_size <= 48:
            folder = "list_icons_48"
        else:
            folder = "list_icons"
        candidate = os.path.join(ASSET_DIR, folder, base)
        return candidate if os.path.exists(candidate) else icon_path

    def make_row(self, text, icon_path, data=None, secondary=""):
        title = _clean_display_text(text, 150) or "Untitled"
        channel_meta = secondary if isinstance(secondary, dict) else None
        meta = _clean_display_text(secondary, 42) if channel_meta is None else ""
        png = None
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
            "settings_portal", "category_portal"
        ):
            row.append(MultiContentEntryPixmapAlphaTest(
                pos=(10, iy), size=(self.icon_size, self.icon_size), png=png
            ))

        tx = self.icon_size + 26
        usable = max(80, self.row_width - tx - 22)

        if self.row_style == "portal_card":
            details = channel_meta or {}
            selected = bool(details.get("selected"))
            health = str(details.get("health") or "unknown").lower()
            if health == "excellent":
                health = "online"
            if health not in ("online", "slow", "offline"):
                health = "unknown"

            # Premium portal row: the card itself is transparent/rounded and
            # floats over the shared page background. The selected portal gets
            # the authored glow asset instead of Enigma2's rectangular
            # selection slab, so the approved reference is preserved.
            card_x, w, h = 82, 980, 80
            card_asset = "portal_row_%s%s.png" % (health, "_selected" if selected else "")
            card_png = cached_png(asset(card_asset))
            if card_png is not None:
                row.append(MultiContentEntryPixmapAlphaBlend(pos=(card_x, 2), size=(w, h), png=card_png))

            portal_icon_name = {
                "online": "us86_portal_online_58.png",
                "offline": "us86_portal_offline_58.png",
            }.get(health, "us34_portal_%s.png" % health)
            icon = cached_png(asset(portal_icon_name))
            row.append(MultiContentEntryPixmapAlphaTest(pos=(10, 13), size=(58,58), png=icon))

            url = _clean_display_text(details.get("url"), 58)
            expiry = _clean_display_text(details.get("expiry_card") or details.get("expiry"), 24) or "No expiry"
            row.append(MultiContentEntryText(pos=(116, 7), size=(460, 35), font=0, flags=RT_HALIGN_LEFT | RT_VALIGN_CENTER, text=title))
            row.append(MultiContentEntryText(pos=(116, 43), size=(460, 28), font=1, flags=RT_HALIGN_LEFT | RT_VALIGN_CENTER, text=url))
            badge = cached_png(asset("us89_status_%s_142x34.png" % health))
            row.append(MultiContentEntryPixmapAlphaTest(pos=(592, 25), size=(142,34), png=badge))
            row.append(MultiContentEntryText(pos=(770, 17), size=(272, 52), font=2, flags=RT_HALIGN_LEFT | RT_VALIGN_CENTER, text=expiry))
        elif self.row_style == "settings_portal":
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
            # Same visual grammar as Select Portal: the icon floats outside a
            # wide glass row and the text sits comfortably inside the card.
            card_x, card_y, card_w, card_h = 82, 2, 674, 62
            if frame is not None:
                row.append(MultiContentEntryPixmapAlphaBlend(pos=(card_x,card_y), size=(card_w,card_h), png=frame))
            folder = cached_png(asset("us89_folder_yellow_42.png"))
            if folder is not None:
                row.append(MultiContentEntryPixmapAlphaBlend(pos=(14,12), size=(42,42), png=folder))
            row.append(MultiContentEntryText(pos=(116,3), size=(575,58), font=0, flags=RT_HALIGN_LEFT|RT_VALIGN_CENTER, text=title))
        elif self.row_style == "series_floating":
            details = channel_meta or {}
            selected = bool(details.get("selected"))
            kind = str(details.get("kind") or "episode")
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
            if kind == "season":
                icon_name = "us89_folder_yellow_40.png"
                icon_size = (40,40)
                icon_pos = (22,19)
            else:
                icon_name = "us86_episode_40.png"
                icon_size = (40,40)
                icon_pos = (22,19)
            icon = cached_png(asset(icon_name))
            if icon is not None:
                row.append(MultiContentEntryPixmapAlphaBlend(pos=icon_pos, size=icon_size, png=icon))
            right = _clean_display_text(details.get("right"), 24)
            badge_text = _clean_display_text(details.get("badge"), 14)
            row.append(MultiContentEntryText(pos=(80,7), size=(304,36), font=0, flags=RT_HALIGN_LEFT|RT_VALIGN_CENTER, text=title))
            sub = right or badge_text
            if sub:
                row.append(MultiContentEntryText(pos=(80,39), size=(304,26), font=1, flags=RT_HALIGN_LEFT|RT_VALIGN_CENTER, text=sub))
        elif self.row_style == "series_compact":
            details = channel_meta or {}
            selected = bool(details.get("selected"))
            kind = str(details.get("kind") or "episode")
            bg = "#102637" if selected else "#07131d"
            accent = "#55d8ff" if selected else "#17384a"
            w = max(220, self.row_width - 4)
            row.append(MultiContentEntryText(pos=(0,3), size=(w,56), font=2, flags=RT_HALIGN_LEFT|RT_VALIGN_CENTER, text="", backcolor=int(bg[1:],16)))
            row.append(MultiContentEntryText(pos=(0,3), size=(5,56), font=2, flags=RT_HALIGN_LEFT|RT_VALIGN_CENTER, text="", backcolor=int(accent[1:],16)))
            icon_name = "us89_folder_yellow_40.png" if kind == "season" else "us86_episode_40.png"
            icon = cached_png(asset(icon_name))
            row.append(MultiContentEntryPixmapAlphaTest(pos=(12,11), size=(40,40), png=icon))
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
            icon_name = ("us25_season_folder_selected.png" if selected else "us25_season_folder.png") if kind == "season" else ("us25_episode_play_selected.png" if selected else "us25_episode_play.png")
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
                icon_name = "us25_season_folder_selected.png" if selected else "us25_season_folder.png"
            else:
                icon_name = "us25_episode_play_selected.png" if selected else "us25_episode_play.png"
            icon = cached_png(asset(icon_name))
            row.append(MultiContentEntryPixmapAlphaTest(pos=(16, 9), size=(46,46), png=icon))
            right = _clean_display_text(details.get("right"), 30)
            badge_text = _clean_display_text(details.get("badge"), 14)
            row.append(MultiContentEntryText(pos=(82, 5), size=(560, 52), font=0, flags=RT_HALIGN_LEFT | RT_VALIGN_CENTER, text=title))
            row.append(MultiContentEntryText(pos=(650, 5), size=(235, 52), font=1, flags=RT_HALIGN_RIGHT | RT_VALIGN_CENTER, text=right))
            if badge_text:
                pill_name = "us25_episode_resume_pill.png" if ("RESUME" in badge_text or "WATCH" in badge_text) else "us25_episode_quality_pill.png"
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
                icon = cached_png(asset("us86_portal_online_58.png"))
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
            now_title = _clean_display_text(details.get("now"), 75) or "No EPG information"
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
            lower = ("NOW  " + now_title) if now_title else ""
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

    def set_icon_rows(self, rows):
        built = []
        for row in rows:
            if len(row) >= 4:
                built.append(self.make_row(row[0], row[1], row[2], row[3]))
            else:
                built.append(self.make_row(row[0], row[1], row[2]))
        self.setList(built)

    def update_icon_row(self, index, row):
        """Replace one rendered row without rebuilding the entire list."""
        try:
            index = int(index)
            if index < 0 or index >= len(self.list):
                return False
            built = self.make_row(row[0], row[1], row[2], row[3] if len(row) >= 4 else "")
            self.list[index] = built
            try:
                self.l.invalidateEntry(index)
            except Exception:
                self.l.setList(self.list)
            return True
        except Exception:
            return False

