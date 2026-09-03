# -*- coding: utf-8 -*-
import gettext
import os

PLUGIN_DOMAIN = "UltraStalker"
LOCALE_DIR = os.path.join(os.path.dirname(__file__), "locale")

def localeInit():
    try:
        from Components.Language import language
        lang = (language.getLanguage() or "en")[:2]
        os.environ["LANGUAGE"] = lang
    except Exception:
        pass
    try:
        gettext.bindtextdomain(PLUGIN_DOMAIN, LOCALE_DIR)
    except Exception:
        pass

localeInit()
try:
    from Components.Language import language
    language.addCallback(localeInit)
except Exception:
    pass

def _(text):
    try:
        return gettext.dgettext(PLUGIN_DOMAIN, text)
    except Exception:
        try:
            return gettext.translation(PLUGIN_DOMAIN, LOCALE_DIR, fallback=True).gettext(text)
        except Exception:
            return text
