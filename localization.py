# -*- coding: utf-8 -*-
"""Ultra Stalker UI-only localization.

This module deliberately translates only strings explicitly passed through
``translate()`` / ``_()`` by Ultra Stalker's own UI. Provider data (Portal,
M3U, Xtream, category names, channel names, movie/series titles and metadata)
is never routed through this translator.
"""
import gettext
import json
import os
import threading

from .language_catalog import (
    installed_interface_language_codes, interface_language_name,
    normalize_interface_code, pack_path,
)

CONFIG_FILE = "/etc/enigma2/ultrastalker/settings.json"
LOCALE_DIR = os.path.join(os.path.dirname(__file__), "locale")
SUPPORTED_LANGUAGES = installed_interface_language_codes()

_LOCK = threading.RLock()
_LANG_OVERRIDE = None
_CACHE_SIGNATURE = None
_CACHE_LANGUAGE = "en"
_AR_GETTEXT = None
_EXTERNAL_PACKS = {}

# Exact, reviewed UI translations. Keep placeholders and punctuation stable.
# Provider-originated strings must never be added here merely because they look
# like an English word; only plugin-owned chrome/status text belongs here.
_T = {'Plugin Language': {'ar': 'لغة الإضافة', 'de': 'Plugin-Sprache', 'fr': 'Langue du plugin', 'tr': 'Eklenti Dili'},
 'Font Size': {'ar': 'حجم الخط', 'de': 'Schriftgröße', 'fr': 'Taille du texte', 'tr': 'Yazı Boyutu'},
 'Normal': {'ar': 'عادي', 'de': 'Normal', 'fr': 'Normal', 'tr': 'Normal'},
 'Large': {'ar': 'كبير', 'de': 'Groß', 'fr': 'Grand', 'tr': 'Büyük'},
 'Larger': {'ar': 'أكبر', 'de': 'Größer', 'fr': 'Plus grand', 'tr': 'Daha Büyük'},
 'Extra Large': {'ar': 'كبير جدًا', 'de': 'Sehr groß', 'fr': 'Très grand', 'tr': 'Çok Büyük'},
 'Choose the text size used across Ultra Stalker. Restart Enigma2 to apply it everywhere.': {'ar': 'اختر حجم النص المستخدم في جميع واجهات Ultra Stalker. أعد تشغيل Enigma2 لتطبيقه '
                                                                                                   'في كل مكان.',
                                                                                             'de': 'Wählen Sie die in Ultra Stalker verwendete Textgröße. Starten Sie Enigma2 neu, '
                                                                                                   'um sie überall anzuwenden.',
                                                                                             'fr': 'Choisissez la taille du texte utilisée dans Ultra Stalker. Redémarrez Enigma2 '
                                                                                                   'pour l’appliquer partout.',
                                                                                             'tr': 'Ultra Stalker genelinde kullanılan yazı boyutunu seçin. Her yerde uygulamak '
                                                                                                   "için Enigma2'yi yeniden başlatın."},
 'Font size saved. Restart Enigma2 to apply it everywhere.': {'ar': 'تم حفظ حجم الخط. أعد تشغيل Enigma2 لتطبيقه في كل مكان.',
                                                              'de': 'Schriftgröße gespeichert. Starten Sie Enigma2 neu, um sie überall anzuwenden.',
                                                              'fr': 'Taille du texte enregistrée. Redémarrez Enigma2 pour l’appliquer partout.',
                                                              'tr': "Yazı boyutu kaydedildi. Her yerde uygulamak için Enigma2'yi yeniden başlatın."},
 'Show in Main Menu': {'ar': 'إظهار في القائمة الرئيسية', 'de': 'Im Hauptmenü anzeigen', 'fr': 'Afficher dans le menu principal', 'tr': 'Ana Menüde Göster'},
 'Show Ultra Stalker directly in the Enigma2 Main Menu.': {'ar': 'أظهر Ultra Stalker مباشرة في القائمة الرئيسية لـ Enigma2.',
                                                           'de': 'Ultra Stalker direkt im Enigma2-Hauptmenü anzeigen.',
                                                           'fr': 'Affiche Ultra Stalker directement dans le menu principal Enigma2.',
                                                           'tr': "Ultra Stalker'ı doğrudan Enigma2 Ana Menüsünde gösterir."},
 'Choose the language used by Ultra Stalker menus and controls. Portal, M3U and Xtream content is never translated.': {'ar': 'اختر اللغة المستخدمة في قوائم وتحكم Ultra Stalker. '
                                                                                                                             'محتوى Portal وM3U وXtream لا تتم ترجمته إطلاقًا.',
                                                                                                                       'de': 'Wählen Sie die Sprache für Menüs und Bedienelemente '
                                                                                                                             'von Ultra Stalker. Portal-, M3U- und Xtream-Inhalte '
                                                                                                                             'werden nie übersetzt.',
                                                                                                                       'fr': 'Choisissez la langue des menus et commandes d’Ultra '
                                                                                                                             'Stalker. Le contenu Portal, M3U et Xtream n’est '
                                                                                                                             'jamais traduit.',
                                                                                                                       'tr': 'Ultra Stalker menüleri ve kontrolleri için dili '
                                                                                                                             'seçin. Portal, M3U ve Xtream içeriği hiçbir zaman '
                                                                                                                             'çevrilmez.'},
 'Language saved': {'ar': 'تم حفظ اللغة', 'de': 'Sprache gespeichert', 'fr': 'Langue enregistrée', 'tr': 'Dil kaydedildi'},
 'Settings': {'ar': 'الإعدادات', 'de': 'Einstellungen', 'fr': 'Paramètres', 'tr': 'Ayarlar'},
 'Refresh Content': {'ar': 'تحديث المحتوى', 'de': 'Inhalt aktualisieren', 'fr': 'Actualiser le contenu', 'tr': 'İçeriği Yenile'},
 'REFRESH': {'ar': 'تحديث', 'de': 'AKTUALISIEREN', 'fr': 'ACTUALISER', 'tr': 'YENİLE'},
 'Reload fresh Portal, Xtream or M3U catalogue data for the currently selected source without deleting persistent artwork.': {'ar': 'أعد تحميل بيانات المحتوى الجديدة للمصدر '
                                                                                                                                    'المحدد Portal أو Xtream أو M3U بدون حذف الصور '
                                                                                                                                    'المحفوظة بشكل دائم.',
                                                                                                                              'de': 'Lädt frische Portal-, Xtream- oder '
                                                                                                                                    'M3U-Katalogdaten für die aktuell ausgewählte '
                                                                                                                                    'Quelle neu, ohne dauerhaft gespeicherte '
                                                                                                                                    'Bilder zu löschen.',
                                                                                                                              'fr': 'Recharge les données fraîches du catalogue '
                                                                                                                                    'Portal, Xtream ou M3U pour la source '
                                                                                                                                    'sélectionnée sans supprimer les illustrations '
                                                                                                                                    'persistantes.',
                                                                                                                              'tr': 'Seçili Portal, Xtream veya M3U kaynağının '
                                                                                                                                    'güncel katalog verilerini kalıcı görselleri '
                                                                                                                                    'silmeden yeniden yükler.'},
 'Refreshing current source content…': {'ar': 'جاري تحديث محتوى المصدر الحالي…',
                                        'de': 'Inhalt der aktuellen Quelle wird aktualisiert…',
                                        'fr': 'Actualisation du contenu de la source actuelle…',
                                        'tr': 'Mevcut kaynak içeriği yenileniyor…'},
 'This source does not support content refresh.': {'ar': 'هذا المصدر لا يدعم تحديث المحتوى.',
                                                   'de': 'Diese Quelle unterstützt keine Inhaltsaktualisierung.',
                                                   'fr': 'Cette source ne prend pas en charge l’actualisation du contenu.',
                                                   'tr': 'Bu kaynak içerik yenilemeyi desteklemiyor.'},
 'Content refresh cancelled': {'ar': 'تم إلغاء تحديث المحتوى',
                               'de': 'Inhaltsaktualisierung abgebrochen',
                               'fr': 'Actualisation du contenu annulée',
                               'tr': 'İçerik yenileme iptal edildi'},
 'Xtream content refreshed. Categories are fresh now; each folder and series will fetch fresh provider data when opened.': {'ar': 'تم تحديث محتوى Xtream. التصنيفات محدثة الآن، '
                                                                                                                                  'وكل مجلد ومسلسل سيجلب بيانات جديدة من السيرفر '
                                                                                                                                  'عند فتحه.',
                                                                                                                            'de': 'Xtream-Inhalt aktualisiert. Die Kategorien sind '
                                                                                                                                  'jetzt aktuell; jeder Ordner und jede Serie lädt '
                                                                                                                                  'beim Öffnen frische Anbieterdaten.',
                                                                                                                            'fr': 'Contenu Xtream actualisé. Les catégories sont à '
                                                                                                                                  'jour ; chaque dossier et série récupérera des '
                                                                                                                                  'données fraîches à l’ouverture.',
                                                                                                                            'tr': 'Xtream içeriği yenilendi. Kategoriler güncel; '
                                                                                                                                  'her klasör ve dizi açıldığında sağlayıcıdan '
                                                                                                                                  'güncel veri alınacak.'},
 'M3U playlist refreshed from the server. %d catalogue items are ready.': {'ar': 'تم تحديث قائمة M3U من السيرفر. %d عنصرًا في المحتوى جاهز الآن.',
                                                                           'de': 'M3U-Playlist vom Server aktualisiert. %d Katalogeinträge sind bereit.',
                                                                           'fr': 'Playlist M3U actualisée depuis le serveur. %d éléments du catalogue sont prêts.',
                                                                           'tr': 'M3U oynatma listesi sunucudan yenilendi. %d katalog öğesi hazır.'},
 'Portal content refreshed. %d categories were reloaded; content pages and series will now open from fresh provider data.': {'ar': 'تم تحديث محتوى Portal. أُعيد تحميل %d تصنيفًا، '
                                                                                                                                   'وستفتح صفحات المحتوى والمسلسلات الآن ببيانات '
                                                                                                                                   'جديدة من السيرفر.',
                                                                                                                             'de': 'Portal-Inhalt aktualisiert. %d Kategorien '
                                                                                                                                   'wurden neu geladen; Inhaltsseiten und Serien '
                                                                                                                                   'öffnen sich jetzt mit frischen Anbieterdaten.',
                                                                                                                             'fr': 'Contenu Portal actualisé. %d catégories ont '
                                                                                                                                   'été rechargées ; les pages de contenu et les '
                                                                                                                                   'séries utiliseront désormais des données '
                                                                                                                                   'fraîches.',
                                                                                                                             'tr': 'Portal içeriği yenilendi. %d kategori yeniden '
                                                                                                                                   'yüklendi; içerik sayfaları ve diziler artık '
                                                                                                                                   'güncel sağlayıcı verileriyle açılacak.'},
 'Content refresh failed: %s': {'ar': 'فشل تحديث المحتوى: %s',
                                'de': 'Inhaltsaktualisierung fehlgeschlagen: %s',
                                'fr': 'Échec de l’actualisation du contenu : %s',
                                'tr': 'İçerik yenileme başarısız: %s'},
 'Portal Settings': {'ar': 'إعدادات البوابة', 'de': 'Portal-Einstellungen', 'fr': 'Paramètres du portail', 'tr': 'Portal Ayarları'},
 'Advanced Settings': {'ar': 'الإعدادات المتقدمة', 'de': 'Erweiterte Einstellungen', 'fr': 'Paramètres avancés', 'tr': 'Gelişmiş Ayarlar'},
 'Expert controls': {'ar': 'خيارات متقدمة', 'de': 'Expertenoptionen', 'fr': 'Contrôles avancés', 'tr': 'Uzman kontrolleri'},
 'Performance, filtering and integration': {'ar': 'الأداء والتصفية والتكامل',
                                            'de': 'Leistung, Filterung und Integration',
                                            'fr': 'Performances, filtrage et intégration',
                                            'tr': 'Performans, filtreleme ve entegrasyon'},
 'Performance, filtering, bouquet/EPG and diagnostic controls.': {'ar': 'إعدادات الأداء والتصفية والباقات/EPG والتشخيص.',
                                                                  'de': 'Leistung, Filterung, Bouquet/EPG und Diagnoseoptionen.',
                                                                  'fr': 'Contrôles des performances, du filtrage, des bouquets/EPG et du diagnostic.',
                                                                  'tr': 'Performans, filtreleme, buket/EPG ve tanılama kontrolleri.'},
 'Change only the options you understand. Defaults are safe for most receivers.': {'ar': 'غيّر فقط الخيارات التي تعرف تأثيرها. الإعدادات الافتراضية مناسبة لمعظم أجهزة الاستقبال.',
                                                                                   'de': 'Ändern Sie nur Optionen, die Sie verstehen. Die Standardwerte sind für die meisten '
                                                                                         'Receiver sicher.',
                                                                                   'fr': 'Modifiez uniquement les options que vous comprenez. Les valeurs par défaut conviennent à '
                                                                                         'la plupart des récepteurs.',
                                                                                   'tr': 'Yalnızca ne yaptığını bildiğiniz seçenekleri değiştirin. Varsayılanlar çoğu alıcı için '
                                                                                         'güvenlidir.'},
 'Changes are saved immediately': {'ar': 'يتم حفظ التغييرات فورًا',
                                   'de': 'Änderungen werden sofort gespeichert',
                                   'fr': 'Les modifications sont enregistrées immédiatement',
                                   'tr': 'Değişiklikler hemen kaydedilir'},
 'Choose an option to configure playback, lists and privacy.': {'ar': 'اختر إعدادًا لضبط التشغيل والقوائم والخصوصية.',
                                                                'de': 'Wählen Sie eine Option, um Wiedergabe, Listen und Datenschutz einzurichten.',
                                                                'fr': 'Choisissez une option pour configurer la lecture, les listes et la confidentialité.',
                                                                'tr': 'Oynatma, listeler ve gizlilik için bir seçenek seçin.'},
 'ARROWS Navigate  •  OK Select  •  BACK Home': {'ar': 'الأسهم للتنقل  •  OK اختيار  •  BACK الرئيسية',
                                                 'de': 'PFEILE Navigieren  •  OK Auswählen  •  ZURÜCK Startseite',
                                                 'fr': 'FLÈCHES Naviguer  •  OK Sélectionner  •  BACK Accueil',
                                                 'tr': 'OKLAR Gezin  •  OK Seç  •  BACK Ana Sayfa'},
 'Back': {'ar': 'رجوع', 'de': 'Zurück', 'fr': 'Retour', 'tr': 'Geri'},
 'Change': {'ar': 'تغيير', 'de': 'Ändern', 'fr': 'Modifier', 'tr': 'Değiştir'},
 'Select': {'ar': 'اختيار', 'de': 'Auswählen', 'fr': 'Sélectionner', 'tr': 'Seç'},
 'Close': {'ar': 'إغلاق', 'de': 'Schließen', 'fr': 'Fermer', 'tr': 'Kapat'},
 'Yes': {'ar': 'نعم', 'de': 'Ja', 'fr': 'Oui', 'tr': 'Evet'},
 'No': {'ar': 'لا', 'de': 'Nein', 'fr': 'Non', 'tr': 'Hayır'},
 'Save': {'ar': 'حفظ', 'de': 'Speichern', 'fr': 'Enregistrer', 'tr': 'Kaydet'},
 'Cancel': {'ar': 'إلغاء', 'de': 'Abbrechen', 'fr': 'Annuler', 'tr': 'İptal'},
 'Done': {'ar': 'تم', 'de': 'Fertig', 'fr': 'Terminé', 'tr': 'Bitti'},
 'Open': {'ar': 'فتح', 'de': 'Öffnen', 'fr': 'Ouvrir', 'tr': 'Aç'},
 'OPEN': {'ar': 'فتح', 'de': 'ÖFFNEN', 'fr': 'OUVRIR', 'tr': 'AÇ'},
 'START': {'ar': 'بدء', 'de': 'START', 'fr': 'DÉMARRER', 'tr': 'BAŞLAT'},
 'EXPORT': {'ar': 'تصدير', 'de': 'EXPORTIEREN', 'fr': 'EXPORTER', 'tr': 'DIŞA AKTAR'},
 'TEST': {'ar': 'اختبار', 'de': 'TESTEN', 'fr': 'TESTER', 'tr': 'TEST'},
 'CHANGE': {'ar': 'تغيير', 'de': 'ÄNDERN', 'fr': 'MODIFIER', 'tr': 'DEĞİŞTİR'},
 'ON': {'ar': 'تشغيل', 'de': 'EIN', 'fr': 'ACTIVÉ', 'tr': 'AÇIK'},
 'OFF': {'ar': 'إيقاف', 'de': 'AUS', 'fr': 'DÉSACTIVÉ', 'tr': 'KAPALI'},
 'EASY': {'ar': 'سهل', 'de': 'EINFACH', 'fr': 'FACILE', 'tr': 'KOLAY'},
 'PIN / QR': {'ar': 'PIN / QR', 'de': 'PIN / QR', 'fr': 'PIN / QR', 'tr': 'PIN / QR'},
 'NOT SET': {'ar': 'غير مضبوط', 'de': 'NICHT GESETZT', 'fr': 'NON DÉFINI', 'tr': 'AYARLANMADI'},
 'UNKNOWN': {'ar': 'غير معروف', 'de': 'UNBEKANNT', 'fr': 'INCONNU', 'tr': 'BİLİNMİYOR'},
 'LOCKED': {'ar': 'مقفول', 'de': 'GESPERRT', 'fr': 'VERROUILLÉ', 'tr': 'KİLİTLİ'},
 'RUNNING': {'ar': 'يعمل', 'de': 'LÄUFT', 'fr': 'EN COURS', 'tr': 'ÇALIŞIYOR'},
 'Ready': {'ar': 'جاهز', 'de': 'Bereit', 'fr': 'Prêt', 'tr': 'Hazır'},
 'Categories unavailable': {'ar': 'الأقسام غير متاحة', 'de': 'Kategorien nicht verfügbar', 'fr': 'Catégories indisponibles', 'tr': 'Kategoriler kullanılamıyor'},
 'Categories could not be loaded': {'ar': 'تعذر تحميل الأقسام',
                                    'de': 'Kategorien konnten nicht geladen werden',
                                    'fr': 'Impossible de charger les catégories',
                                    'tr': 'Kategoriler yüklenemedi'},
 'Content temporarily unavailable. Try again.': {'ar': 'المحتوى غير متاح مؤقتًا. حاول مرة أخرى.',
                                                 'de': 'Inhalt vorübergehend nicht verfügbar. Bitte erneut versuchen.',
                                                 'fr': 'Contenu temporairement indisponible. Réessayez.',
                                                 'tr': 'İçerik geçici olarak kullanılamıyor. Tekrar deneyin.'},
 'Unknown error': {'ar': 'خطأ غير معروف', 'de': 'Unbekannter Fehler', 'fr': 'Erreur inconnue', 'tr': 'Bilinmeyen hata'},
 'Information': {'ar': 'معلومات', 'de': 'Informationen', 'fr': 'Informations', 'tr': 'Bilgi'},
 'Ultra Stalker Information': {'ar': 'معلومات Ultra Stalker', 'de': 'Ultra-Stalker-Informationen', 'fr': 'Informations Ultra Stalker', 'tr': 'Ultra Stalker Bilgileri'},
 'Diagnostics': {'ar': 'التشخيص', 'de': 'Diagnose', 'fr': 'Diagnostics', 'tr': 'Tanılama'},
 'Support Bundle': {'ar': 'حزمة الدعم', 'de': 'Support-Paket', 'fr': 'Paquet de support', 'tr': 'Destek Paketi'},
 'About': {'ar': 'حول الإضافة', 'de': 'Über', 'fr': 'À propos', 'tr': 'Hakkında'},
 'Version, build identity and runtime information.': {'ar': 'معلومات الإصدار والبنية وبيئة التشغيل.',
                                                      'de': 'Version, Build-Kennung und Laufzeitinformationen.',
                                                      'fr': 'Informations sur la version, la build et l’environnement d’exécution.',
                                                      'tr': 'Sürüm, yapı kimliği ve çalışma zamanı bilgileri.'},
 'Visual Theme': {'ar': 'المظهر اللوني', 'de': 'Design', 'fr': 'Thème visuel', 'tr': 'Görsel Tema'},
 'Choose visual theme': {'ar': 'اختر المظهر اللوني', 'de': 'Design auswählen', 'fr': 'Choisir le thème visuel', 'tr': 'Görsel temayı seçin'},
 'Select the visual style': {'ar': 'اختر النمط المرئي', 'de': 'Visuellen Stil auswählen', 'fr': 'Sélectionnez le style visuel', 'tr': 'Görsel stili seçin'},
 'Nova FHD': {'ar': 'نوفا FHD', 'de': 'Nova FHD', 'fr': 'Nova FHD', 'tr': 'Nova FHD'},
 'OLED Black': {'ar': 'OLED أسود', 'de': 'OLED Black', 'fr': 'OLED Noir', 'tr': 'OLED Siyah'},
 'Midnight Purple': {'ar': 'بنفسجي منتصف الليل', 'de': 'Midnight Purple', 'fr': 'Violet Minuit', 'tr': 'Gece Moru'},
 'Choose Nova FHD, OLED Black, or Midnight Purple. Restart is required.': {'ar': 'اختر Nova FHD أو OLED Black أو Midnight Purple. يلزم إعادة التشغيل.',
                                                                           'de': 'Wählen Sie Nova FHD, OLED Black oder Midnight Purple. Ein Neustart ist erforderlich.',
                                                                           'fr': 'Choisissez Nova FHD, OLED Black ou Midnight Purple. Un redémarrage est nécessaire.',
                                                                           'tr': 'Nova FHD, OLED Black veya Midnight Purple seçin. Yeniden başlatma gerekir.'},
 'Theme saved. Restart Enigma2 to apply: %s': {'ar': 'تم حفظ المظهر. أعد تشغيل Enigma2 لتطبيقه: %s',
                                               'de': 'Design gespeichert. Starten Sie Enigma2 neu, um es anzuwenden: %s',
                                               'fr': 'Thème enregistré. Redémarrez Enigma2 pour l’appliquer : %s',
                                               'tr': 'Tema kaydedildi. Uygulamak için Enigma2’yi yeniden başlatın: %s'},
 'Theme save failed: %s': {'ar': 'فشل حفظ المظهر: %s',
                           'de': 'Design speichern fehlgeschlagen: %s',
                           'fr': 'Échec de l’enregistrement du thème : %s',
                           'tr': 'Tema kaydedilemedi: %s'},
 'Playback Engine': {'ar': 'محرك التشغيل', 'de': 'Wiedergabe-Engine', 'fr': 'Moteur de lecture', 'tr': 'Oynatma Motoru'},
 'Choose the Enigma2 playback service used for streams.': {'ar': 'اختر خدمة تشغيل Enigma2 المستخدمة للبث.',
                                                           'de': 'Wählen Sie den für Streams verwendeten Enigma2-Wiedergabedienst.',
                                                           'fr': 'Choisissez le service de lecture Enigma2 utilisé pour les flux.',
                                                           'tr': 'Yayınlar için kullanılacak Enigma2 oynatma hizmetini seçin.'},
 'Player Engine Lock': {'ar': 'تثبيت محرك التشغيل', 'de': 'Player-Engine-Sperre', 'fr': 'Verrouillage du moteur de lecture', 'tr': 'Oynatma Motoru Kilidi'},
 'Every Live, movie, series, episode and catch-up stream uses the Playback Engine selected in Settings.': {'ar': 'كل بث مباشر أو فيلم أو مسلسل أو حلقة أو Catch-up يستخدم محرك '
                                                                                                                 'التشغيل المحدد في الإعدادات.',
                                                                                                           'de': 'Jeder Live-, Film-, Serien-, Episoden- und Catch-up-Stream '
                                                                                                                 'verwendet die in den Einstellungen gewählte Wiedergabe-Engine.',
                                                                                                           'fr': 'Tous les flux Live, films, séries, épisodes et Catch-up '
                                                                                                                 'utilisent le moteur de lecture choisi dans les paramètres.',
                                                                                                           'tr': 'Canlı yayın, film, dizi, bölüm ve Catch-up akışlarının tümü '
                                                                                                                 'Ayarlar’da seçilen Oynatma Motorunu kullanır.'},
 'Automatic Engine Switching': {'ar': 'التبديل التلقائي للمحرك', 'de': 'Automatischer Engine-Wechsel', 'fr': 'Changement automatique du moteur', 'tr': 'Otomatik Motor Değiştirme'},
 'Smart Engine and per-title engine overrides are disabled to keep playback on the selected engine.': {'ar': 'تم تعطيل المحرك الذكي وتجاوزات المحرك لكل عنوان للحفاظ على التشغيل '
                                                                                                             'بالمحرك المحدد.',
                                                                                                       'de': 'Smart Engine und titelspezifische Engine-Überschreibungen sind '
                                                                                                             'deaktiviert, damit die Wiedergabe auf der gewählten Engine bleibt.',
                                                                                                       'fr': 'Le moteur intelligent et les changements de moteur par titre sont '
                                                                                                             'désactivés afin de conserver le moteur sélectionné.',
                                                                                                       'tr': 'Oynatmayı seçilen motorda tutmak için Akıllı Motor ve başlık bazlı '
                                                                                                             'motor geçersiz kılmaları devre dışıdır.'},
 'Resume Behavior': {'ar': 'سلوك استكمال المشاهدة', 'de': 'Fortsetzungsverhalten', 'fr': 'Comportement de reprise', 'tr': 'Devam Etme Davranışı'},
 'Ask, always resume, or always start from the beginning.': {'ar': 'اسأل، أو استكمل دائمًا، أو ابدأ دائمًا من البداية.',
                                                             'de': 'Nachfragen, immer fortsetzen oder immer von vorn beginnen.',
                                                             'fr': 'Demander, toujours reprendre ou toujours recommencer depuis le début.',
                                                             'tr': 'Sor, her zaman devam et veya her zaman baştan başla.'},
 'Crash-Safe Progress': {'ar': 'حفظ التقدم الآمن', 'de': 'Absturzsicherer Fortschritt', 'fr': 'Progression protégée contre les crashs', 'tr': 'Çökme Güvenli İlerleme'},
 'Save VOD/episode progress frequently and on pause/engine changes.': {'ar': 'احفظ تقدم الأفلام/الحلقات بشكل متكرر وعند الإيقاف المؤقت أو تغيير المحرك.',
                                                                       'de': 'Speichert den VOD-/Episodenfortschritt häufig sowie bei Pause/Engine-Wechsel.',
                                                                       'fr': 'Enregistre fréquemment la progression VOD/épisode, ainsi qu’en pause ou lors d’un changement de '
                                                                             'moteur.',
                                                                       'tr': 'VOD/bölüm ilerlemesini sık sık ve duraklatma/motor değişikliklerinde kaydeder.'},
 'Progress Save Interval': {'ar': 'فاصل حفظ التقدم',
                            'de': 'Speicherintervall für Fortschritt',
                            'fr': 'Intervalle d’enregistrement de la progression',
                            'tr': 'İlerleme Kayıt Aralığı'},
 'Crash-safe bookmark interval for movies and episodes.': {'ar': 'فاصل حفظ موضع المشاهدة الآمن للأفلام والحلقات.',
                                                           'de': 'Absturzsicheres Lesezeichen-Intervall für Filme und Episoden.',
                                                           'fr': 'Intervalle de sauvegarde sécurisé de la position pour les films et épisodes.',
                                                           'tr': 'Film ve bölümler için çökme güvenli yer imi aralığı.'},
 'Next Episode Countdown': {'ar': 'العد التنازلي للحلقة التالية',
                            'de': 'Countdown für nächste Episode',
                            'fr': 'Compte à rebours du prochain épisode',
                            'tr': 'Sonraki Bölüm Geri Sayımı'},
 'Choose the cancelable autoplay countdown after an episode ends.': {'ar': 'اختر مدة العد التنازلي القابل للإلغاء للتشغيل التلقائي بعد انتهاء الحلقة.',
                                                                     'de': 'Wählen Sie den abbrechbaren Autoplay-Countdown nach Ende einer Episode.',
                                                                     'fr': 'Choisissez le compte à rebours annulable avant la lecture automatique après la fin d’un épisode.',
                                                                     'tr': 'Bölüm bittikten sonra iptal edilebilir otomatik oynatma geri sayımını seçin.'},
 'Auto Remove Completed': {'ar': 'حذف المكتمل تلقائيًا',
                           'de': 'Abgeschlossene automatisch entfernen',
                           'fr': 'Retirer automatiquement les éléments terminés',
                           'tr': 'Tamamlananları Otomatik Kaldır'},
 'Automatically remove titles from Continue Watching after the completion threshold.': {'ar': 'احذف العناوين تلقائيًا من متابعة المشاهدة بعد بلوغ نسبة الاكتمال.',
                                                                                        'de': 'Entfernt Titel nach Erreichen der Abschlussschwelle automatisch aus '
                                                                                              '„Weiterschauen“.',
                                                                                        'fr': 'Retire automatiquement les titres de Continuer à regarder une fois le seuil de fin '
                                                                                              'atteint.',
                                                                                        'tr': 'Tamamlama eşiğine ulaşıldığında başlıkları İzlemeye Devam’dan otomatik kaldırır.'},
 'Completion Threshold': {'ar': 'نسبة اكتمال المشاهدة', 'de': 'Abschlussschwelle', 'fr': 'Seuil de fin', 'tr': 'Tamamlama Eşiği'},
 'Percentage at which a title is considered completed and removed from Continue Watching.': {'ar': 'النسبة التي عندها يعتبر العنوان مكتملًا ويُحذف من متابعة المشاهدة.',
                                                                                             'de': 'Prozentsatz, ab dem ein Titel als abgeschlossen gilt und aus „Weiterschauen“ '
                                                                                                   'entfernt wird.',
                                                                                             'fr': 'Pourcentage à partir duquel un titre est considéré comme terminé et retiré de '
                                                                                                   'Continuer à regarder.',
                                                                                             'tr': 'Bir başlığın tamamlanmış sayılıp İzlemeye Devam’dan kaldırılacağı yüzde.'},
 'Completion Remaining Time': {'ar': 'الوقت المتبقي للاكتمال', 'de': 'Verbleibende Zeit für Abschluss', 'fr': 'Temps restant avant fin', 'tr': 'Tamamlanma İçin Kalan Süre'},
 'Also mark a title completed when this little time remains.': {'ar': 'اعتبر العنوان مكتملًا أيضًا عندما يتبقى هذا القدر القليل من الوقت.',
                                                                'de': 'Markiert einen Titel auch als abgeschlossen, wenn nur noch so wenig Zeit übrig ist.',
                                                                'fr': 'Considère également le titre comme terminé lorsqu’il reste aussi peu de temps.',
                                                                'tr': 'Bu kadar az süre kaldığında başlığı tamamlanmış olarak da işaretler.'},
 'Portal Timeout': {'ar': 'مهلة اتصال البوابة', 'de': 'Portal-Zeitlimit', 'fr': 'Délai du portail', 'tr': 'Portal Zaman Aşımı'},
 'Network timeout for portal API calls.': {'ar': 'مهلة الشبكة لاتصالات API الخاصة بالبوابة.',
                                           'de': 'Netzwerk-Zeitlimit für Portal-API-Aufrufe.',
                                           'fr': 'Délai réseau des appels API du portail.',
                                           'tr': 'Portal API çağrıları için ağ zaman aşımı.'},
 'EPG Window': {'ar': 'نطاق EPG', 'de': 'EPG-Zeitfenster', 'fr': 'Fenêtre EPG', 'tr': 'EPG Aralığı'},
 'How much EPG data to request for live channels.': {'ar': 'كمية بيانات EPG المطلوب جلبها للقنوات المباشرة.',
                                                     'de': 'Wie viele EPG-Daten für Live-Kanäle abgerufen werden.',
                                                     'fr': 'Quantité de données EPG à demander pour les chaînes en direct.',
                                                     'tr': 'Canlı kanallar için istenecek EPG verisi miktarı.'},
 'Catch-up History': {'ar': 'سجل المشاهدة المؤجلة', 'de': 'Catch-up-Verlauf', 'fr': 'Historique Catch-up', 'tr': 'Catch-up Geçmişi'},
 'How far back archive EPG should be collected.': {'ar': 'الفترة السابقة التي يتم جمع بيانات EPG المؤرشفة لها.',
                                                   'de': 'Wie weit zurück Archiv-EPG erfasst werden soll.',
                                                   'fr': 'Jusqu’où remonter pour collecter l’EPG d’archive.',
                                                   'tr': 'Arşiv EPG verisinin ne kadar geriye dönük toplanacağını belirler.'},
 'Poster Loading': {'ar': 'تحميل الصور', 'de': 'Poster-Laden', 'fr': 'Chargement des affiches', 'tr': 'Poster Yükleme'},
 'Enable or disable remote poster and logo artwork.': {'ar': 'فعّل أو عطّل تحميل البوسترات والشعارات من الشبكة.',
                                                       'de': 'Aktiviert oder deaktiviert das Laden von Postern und Logos aus dem Netz.',
                                                       'fr': 'Active ou désactive le chargement distant des affiches et logos.',
                                                       'tr': 'Uzak poster ve logo görsellerini açar veya kapatır.'},
 'Movies View': {'ar': 'عرض الأفلام', 'de': 'Filmansicht', 'fr': 'Vue Films', 'tr': 'Film Görünümü'},
 'Series View': {'ar': 'عرض المسلسلات', 'de': 'Serienansicht', 'fr': 'Vue Séries', 'tr': 'Dizi Görünümü'},
 'CINEMATIC': {'ar': 'سينمائي', 'de': 'CINEMATIC', 'fr': 'CINÉMATIQUE', 'tr': 'SİNEMATİK'},
 'POSTER GRID': {'ar': 'شبكة البوسترات', 'de': 'POSTER-RASTER', 'fr': 'GRILLE D’AFFICHES', 'tr': 'POSTER IZGARASI'},
 'Choose Poster Grid or the Global-Library Cinematic presentation for Movies.': {'ar': 'اختر شبكة البوسترات أو العرض السينمائي للمكتبة العامة للأفلام.',
                                                                                 'de': 'Wählen Sie für Filme das Poster-Raster oder die Cinematic-Darstellung der globalen '
                                                                                       'Bibliothek.',
                                                                                 'fr': 'Choisissez la grille d’affiches ou la présentation Cinématique de la bibliothèque globale '
                                                                                       'pour les films.',
                                                                                 'tr': 'Filmler için Poster Izgarası veya Global Kütüphane Sinematik görünümünü seçin.'},
 'Choose Poster Grid or the Global-Library Cinematic presentation for Series.': {'ar': 'اختر شبكة البوسترات أو العرض السينمائي للمكتبة العامة للمسلسلات.',
                                                                                 'de': 'Wählen Sie für Serien das Poster-Raster oder die Cinematic-Darstellung der globalen '
                                                                                       'Bibliothek.',
                                                                                 'fr': 'Choisissez la grille d’affiches ou la présentation Cinématique de la bibliothèque globale '
                                                                                       'pour les séries.',
                                                                                 'tr': 'Diziler için Poster Izgarası veya Global Kütüphane Sinematik görünümünü seçin.'},
 'Image Cache': {'ar': 'ذاكرة الصور', 'de': 'Bild-Cache', 'fr': 'Cache des images', 'tr': 'Görsel Önbelleği'},
 'Clear downloaded artwork from persistent/temporary storage.': {'ar': 'امسح الصور المحمّلة من التخزين الدائم/المؤقت.',
                                                                 'de': 'Löscht heruntergeladene Bilder aus dem dauerhaften/temporären Speicher.',
                                                                 'fr': 'Efface les illustrations téléchargées du stockage permanent/temporaire.',
                                                                 'tr': 'İndirilen görselleri kalıcı/geçici depolamadan temizler.'},
 'Download Disk Reserve': {'ar': 'مساحة احتياطية للتنزيل',
                           'de': 'Reservierter Speicherplatz für Downloads',
                           'fr': 'Réserve disque pour les téléchargements',
                           'tr': 'İndirme Disk Rezervi'},
 'Keep this much disk space free while downloads are running.': {'ar': 'احتفظ بهذه المساحة فارغة أثناء عمليات التنزيل.',
                                                                 'de': 'Hält während laufender Downloads so viel Speicherplatz frei.',
                                                                 'fr': 'Conserve cet espace disque libre pendant les téléchargements.',
                                                                 'tr': 'İndirmeler çalışırken bu kadar disk alanını boş tutar.'},
 'Artwork Prefetch': {'ar': 'تحميل الصور مسبقًا', 'de': 'Bilder vorab laden', 'fr': 'Préchargement des illustrations', 'tr': 'Görsel Ön Yükleme'},
 'Prefetch selected movie/series details and backdrop while browsing.': {'ar': 'حمّل مسبقًا تفاصيل وخلفية الفيلم/المسلسل المحدد أثناء التصفح.',
                                                                         'de': 'Lädt Details und Hintergrundbild des ausgewählten Films/der Serie beim Durchsuchen vorab.',
                                                                         'fr': 'Précharge les détails et l’arrière-plan du film/de la série sélectionné pendant la navigation.',
                                                                         'tr': 'Gezinirken seçili film/dizi ayrıntılarını ve arka planını önceden yükler.'},
 'Preload Current Artwork': {'ar': 'تحميل صور المصدر الحالي مسبقًا',
                             'de': 'Aktuelle Bilder vorladen',
                             'fr': 'Précharger les illustrations actuelles',
                             'tr': 'Mevcut Görselleri Önceden Yükle'},
 'Prepare Movies/Series artwork for the current Portal/Xtream source; shared HDD-ready items are skipped.': {'ar': 'جهّز صور الأفلام/المسلسلات للمصدر الحالي Portal/Xtream؛ '
                                                                                                                   'العناصر الجاهزة على الهارد يتم تجاوزها.',
                                                                                                             'de': 'Bereitet Film-/Serienbilder für die aktuelle '
                                                                                                                   'Portal-/Xtream-Quelle vor; bereits auf der HDD vorhandene '
                                                                                                                   'Einträge werden übersprungen.',
                                                                                                             'fr': 'Prépare les illustrations Films/Séries pour la source '
                                                                                                                   'Portal/Xtream actuelle ; les éléments déjà prêts sur le disque '
                                                                                                                   'sont ignorés.',
                                                                                                             'tr': 'Mevcut Portal/Xtream kaynağı için Film/Dizi görsellerini '
                                                                                                                   'hazırlar; diskte hazır olan ortak öğeler atlanır.'},
 'Watched Badges': {'ar': 'علامات تمت المشاهدة', 'de': 'Gesehen-Markierungen', 'fr': 'Badges Vu', 'tr': 'İzlendi Rozetleri'},
 'Show WATCHED state on movie and series cards.': {'ar': 'أظهر حالة تمت المشاهدة على بطاقات الأفلام والمسلسلات.',
                                                   'de': 'Zeigt den Status GESEHEN auf Film- und Serienkacheln.',
                                                   'fr': 'Affiche l’état VU sur les cartes de films et séries.',
                                                   'tr': 'Film ve dizi kartlarında İZLENDİ durumunu gösterir.'},
 'Live Preview': {'ar': 'المعاينة المباشرة', 'de': 'Live-Vorschau', 'fr': 'Aperçu en direct', 'tr': 'Canlı Önizleme'},
 'Enable portal live-preview behavior where supported.': {'ar': 'فعّل المعاينة المباشرة للبوابة حيث تكون مدعومة.',
                                                          'de': 'Aktiviert die Portal-Live-Vorschau, sofern unterstützt.',
                                                          'fr': 'Active l’aperçu en direct du portail lorsqu’il est pris en charge.',
                                                          'tr': 'Desteklenen yerlerde portal canlı önizlemesini etkinleştirir.'},
 'Parental Lock': {'ar': 'الرقابة الأبوية', 'de': 'Kindersicherung', 'fr': 'Verrouillage parental', 'tr': 'Ebeveyn Kilidi'},
 'Protect configured/adult content categories.': {'ar': 'احمِ أقسام المحتوى المحددة/للبالغين.',
                                                  'de': 'Schützt konfigurierte Kategorien bzw. Erwachseneninhalte.',
                                                  'fr': 'Protège les catégories configurées/de contenu adulte.',
                                                  'tr': 'Yapılandırılmış/yetişkin içerik kategorilerini korur.'},
 'Parental Mode': {'ar': 'وضع الرقابة الأبوية', 'de': 'Kindersicherungsmodus', 'fr': 'Mode parental', 'tr': 'Ebeveyn Modu'},
 'PIN-protect sensitive categories or hide them completely.': {'ar': 'احمِ الأقسام الحساسة برقم PIN أو أخفها بالكامل.',
                                                               'de': 'Sensible Kategorien per PIN schützen oder vollständig ausblenden.',
                                                               'fr': 'Protège les catégories sensibles par PIN ou masque-les complètement.',
                                                               'tr': 'Hassas kategorileri PIN ile korur veya tamamen gizler.'},
 'Parental Unlock': {'ar': 'مدة فتح الرقابة', 'de': 'Kindersicherung entsperren', 'fr': 'Déverrouillage parental', 'tr': 'Ebeveyn Kilidi Açma'},
 'Keep a correct PIN unlocked for this session duration.': {'ar': 'احتفظ بفتح PIN الصحيح طوال مدة الجلسة المحددة.',
                                                            'de': 'Hält eine korrekte PIN für diese Sitzungsdauer entsperrt.',
                                                            'fr': 'Conserve le PIN validé pendant cette durée de session.',
                                                            'tr': 'Doğru PIN’i bu oturum süresi boyunca açık tutar.'},
 'Parental PIN': {'ar': 'الرقم السري للرقابة', 'de': 'Kindersicherungs-PIN', 'fr': 'PIN parental', 'tr': 'Ebeveyn PIN’i'},
 'Set a 4–8 digit parental-control PIN.': {'ar': 'عيّن رقم PIN للرقابة الأبوية من 4 إلى 8 أرقام.',
                                           'de': 'Legen Sie eine 4–8-stellige Kindersicherungs-PIN fest.',
                                           'fr': 'Définissez un PIN de contrôle parental de 4 à 8 chiffres.',
                                           'tr': '4–8 haneli ebeveyn kontrol PIN’i ayarlayın.'},
 'Adult Keywords': {'ar': 'كلمات محتوى البالغين', 'de': 'Schlüsselwörter für Erwachseneninhalte', 'fr': 'Mots-clés adulte', 'tr': 'Yetişkin Anahtar Kelimeleri'},
 'Comma-separated words used to detect sensitive categories.': {'ar': 'كلمات مفصولة بفواصل تُستخدم لاكتشاف الأقسام الحساسة.',
                                                                'de': 'Durch Kommas getrennte Wörter zur Erkennung sensibler Kategorien.',
                                                                'fr': 'Mots séparés par des virgules utilisés pour détecter les catégories sensibles.',
                                                                'tr': 'Hassas kategorileri algılamak için kullanılan virgülle ayrılmış kelimeler.'},
 'Lock Parental Session': {'ar': 'قفل جلسة الرقابة', 'de': 'Kindersicherungssitzung sperren', 'fr': 'Verrouiller la session parentale', 'tr': 'Ebeveyn Oturumunu Kilitle'},
 'Immediately clear the temporary parental unlock.': {'ar': 'ألغِ فورًا فتح الرقابة المؤقت.',
                                                      'de': 'Hebt die temporäre Entsperrung der Kindersicherung sofort auf.',
                                                      'fr': 'Annule immédiatement le déverrouillage parental temporaire.',
                                                      'tr': 'Geçici ebeveyn kilidi açma durumunu hemen temizler.'},
 'UNLOCKED %d min': {'ar': 'مفتوح %d دقيقة', 'de': 'ENTSPERRT %d Min', 'fr': 'DÉVERROUILLÉ %d min', 'tr': 'KİLİT AÇIK %d dk'},
 'Clean Titles': {'ar': 'تنظيف العناوين', 'de': 'Titel bereinigen', 'fr': 'Nettoyer les titres', 'tr': 'Başlıkları Temizle'},
 'Remove common technical prefixes from portal titles.': {'ar': 'احذف البادئات التقنية الشائعة من عناوين البوابة.',
                                                          'de': 'Entfernt gängige technische Präfixe aus Portal-Titeln.',
                                                          'fr': 'Supprime les préfixes techniques courants des titres du portail.',
                                                          'tr': 'Portal başlıklarındaki yaygın teknik önekleri kaldırır.'},
 'Quality Badges': {'ar': 'شارات الجودة', 'de': 'Qualitäts-Markierungen', 'fr': 'Badges de qualité', 'tr': 'Kalite Rozetleri'},
 'Show 4K/HD and similar quality metadata when detected.': {'ar': 'أظهر 4K/HD ومعلومات الجودة المشابهة عند اكتشافها.',
                                                            'de': 'Zeigt 4K/HD und ähnliche Qualitäts-Metadaten, sofern erkannt.',
                                                            'fr': 'Affiche les indications 4K/HD et autres informations de qualité lorsqu’elles sont détectées.',
                                                            'tr': 'Algılandığında 4K/HD ve benzeri kalite bilgilerini gösterir.'},
 'Channel List': {'ar': 'قائمة القنوات', 'de': 'Kanalliste', 'fr': 'Liste des chaînes', 'tr': 'Kanal Listesi'},
 'Choose EPG, compact or large live-channel list density.': {'ar': 'اختر نمط قائمة القنوات المباشرة: EPG أو مضغوط أو كبير.',
                                                             'de': 'Wählen Sie die Dichte der Live-Kanalliste: EPG, kompakt oder groß.',
                                                             'fr': 'Choisissez la densité de la liste des chaînes en direct : EPG, compacte ou grande.',
                                                             'tr': 'Canlı kanal listesi yoğunluğunu EPG, kompakt veya büyük olarak seçin.'},
 'Show Channel Numbers': {'ar': 'إظهار أرقام القنوات', 'de': 'Kanalnummern anzeigen', 'fr': 'Afficher les numéros de chaîne', 'tr': 'Kanal Numaralarını Göster'},
 'Display channel numbers in Live TV lists.': {'ar': 'أظهر أرقام القنوات في قوائم البث المباشر.',
                                               'de': 'Zeigt Kanalnummern in Live-TV-Listen an.',
                                               'fr': 'Affiche les numéros des chaînes dans les listes TV en direct.',
                                               'tr': 'Canlı TV listelerinde kanal numaralarını gösterir.'},
 'Smart Recovery': {'ar': 'الاستعادة الذكية', 'de': 'Intelligente Wiederherstellung', 'fr': 'Récupération intelligente', 'tr': 'Akıllı Kurtarma'},
 'Refresh an expired stream link after all playback engines fail.': {'ar': 'حدّث رابط البث المنتهي بعد فشل جميع محركات التشغيل.',
                                                                     'de': 'Erneuert einen abgelaufenen Stream-Link, nachdem alle Wiedergabe-Engines fehlgeschlagen sind.',
                                                                     'fr': 'Actualise un lien de flux expiré après l’échec de tous les moteurs de lecture.',
                                                                     'tr': 'Tüm oynatma motorları başarısız olduktan sonra süresi dolmuş akış bağlantısını yeniler.'},
 'Recovery Retries': {'ar': 'محاولات الاستعادة', 'de': 'Wiederherstellungsversuche', 'fr': 'Tentatives de récupération', 'tr': 'Kurtarma Denemeleri'},
 'Maximum stream-link refresh cycles after engine fallback.': {'ar': 'أقصى عدد لمحاولات تحديث رابط البث بعد فشل المحركات.',
                                                               'de': 'Maximale Anzahl an Stream-Link-Erneuerungen nach Engine-Fallback.',
                                                               'fr': 'Nombre maximal de cycles d’actualisation du lien après le repli des moteurs.',
                                                               'tr': 'Motor geri dönüşünden sonra en fazla akış bağlantısı yenileme döngüsü.'},
 'Multi-Portal Search': {'ar': 'البحث في عدة بوابات', 'de': 'Portalübergreifende Suche', 'fr': 'Recherche multi-portails', 'tr': 'Çoklu Portal Araması'},
 'Global Search checks every enabled portal, not only the current one.': {'ar': 'البحث العام يفحص كل البوابات المفعّلة وليس البوابة الحالية فقط.',
                                                                          'de': 'Die globale Suche durchsucht jedes aktivierte Portal, nicht nur das aktuelle.',
                                                                          'fr': 'La recherche globale interroge tous les portails activés, pas seulement le portail actuel.',
                                                                          'tr': 'Global Arama yalnızca mevcut portalı değil, etkin tüm portalları tarar.'},
 'Search Limit / Portal': {'ar': 'حد النتائج لكل بوابة', 'de': 'Suchlimit / Portal', 'fr': 'Limite de recherche / portail', 'tr': 'Arama Limiti / Portal'},
 'Maximum Global Search results returned from each portal.': {'ar': 'أقصى عدد نتائج البحث العام من كل بوابة.',
                                                              'de': 'Maximale Anzahl an Ergebnissen der globalen Suche pro Portal.',
                                                              'fr': 'Nombre maximal de résultats de recherche globale renvoyés par chaque portail.',
                                                              'tr': 'Her portaldan döndürülecek en fazla Global Arama sonucu.'},
 'TMDB Metadata': {'ar': 'بيانات TMDB', 'de': 'TMDB-Metadaten', 'fr': 'Métadonnées TMDB', 'tr': 'TMDB Meta Verisi'},
 'Use TMDB to enrich movie/series metadata and artwork.': {'ar': 'استخدم TMDB لإثراء بيانات وصور الأفلام والمسلسلات.',
                                                           'de': 'Nutzt TMDB, um Film-/Serien-Metadaten und Bilder anzureichern.',
                                                           'fr': 'Utilise TMDB pour enrichir les métadonnées et illustrations des films/séries.',
                                                           'tr': 'Film/dizi meta verilerini ve görsellerini zenginleştirmek için TMDB kullanır.'},
 'TMDB API Key / Token': {'ar': 'مفتاح / رمز TMDB', 'de': 'TMDB-API-Schlüssel / Token', 'fr': 'Clé / jeton API TMDB', 'tr': 'TMDB API Anahtarı / Token'},
 'Stored privately in api_keys.conf; never copied into settings.json.': {'ar': 'يُحفظ بشكل خاص في api_keys.conf ولا يُنسخ إلى settings.json.',
                                                                         'de': 'Wird privat in api_keys.conf gespeichert; niemals in settings.json kopiert.',
                                                                         'fr': 'Stocké de façon privée dans api_keys.conf ; jamais copié dans settings.json.',
                                                                         'tr': 'api_keys.conf içinde özel olarak saklanır; settings.json dosyasına asla kopyalanmaz.'},
 'TMDB Language': {'ar': 'لغة TMDB', 'de': 'TMDB-Sprache', 'fr': 'Langue TMDB', 'tr': 'TMDB Dili'},
 'Preferred TMDB metadata language. English is used as fallback for missing overview text.': {'ar': 'لغة بيانات TMDB المفضلة. تُستخدم الإنجليزية كبديل عند غياب الملخص.',
                                                                                              'de': 'Bevorzugte Sprache für TMDB-Metadaten. Für fehlende Beschreibungen wird '
                                                                                                    'Englisch als Ausweichlösung verwendet.',
                                                                                              'fr': 'Langue préférée des métadonnées TMDB. L’anglais sert de secours si le résumé '
                                                                                                    'est absent.',
                                                                                              'tr': 'Tercih edilen TMDB meta veri dili. Özet eksikse İngilizce yedek olarak '
                                                                                                    'kullanılır.'},
 'TMDB Connection Test': {'ar': 'اختبار اتصال TMDB', 'de': 'TMDB-Verbindungstest', 'fr': 'Test de connexion TMDB', 'tr': 'TMDB Bağlantı Testi'},
 'Validate the saved TMDB credential against the official API.': {'ar': 'تحقق من بيانات اعتماد TMDB المحفوظة باستخدام الـAPI الرسمي.',
                                                                  'de': 'Überprüft die gespeicherten TMDB-Zugangsdaten anhand der offiziellen API.',
                                                                  'fr': 'Vérifie les identifiants TMDB enregistrés auprès de l’API officielle.',
                                                                  'tr': 'Kaydedilen TMDB kimlik bilgisini resmi API ile doğrular.'},
 'Online Arabic Subtitles': {'ar': 'ترجمة عربية أونلاين', 'de': 'Arabische Online-Untertitel', 'fr': 'Sous-titres arabes en ligne', 'tr': 'Çevrimiçi Arapça Altyazılar'},
 'SubDL API key for online Arabic movie and episode subtitles.': {'ar': 'مفتاح SubDL API للترجمة العربية الأونلاين للأفلام والحلقات.',
                                                                  'de': 'SubDL-API-Schlüssel für arabische Online-Untertitel zu Filmen und Episoden.',
                                                                  'fr': 'Clé API SubDL pour les sous-titres arabes en ligne des films et épisodes.',
                                                                  'tr': 'Film ve bölümler için çevrimiçi Arapça altyazılarda kullanılan SubDL API anahtarı.'},
 'Backup & Restore': {'ar': 'النسخ الاحتياطي والاستعادة', 'de': 'Sichern & Wiederherstellen', 'fr': 'Sauvegarde et restauration', 'tr': 'Yedekleme ve Geri Yükleme'},
 'Backup or restore portals, settings, favorites, history and resume state.': {'ar': 'انسخ أو استعد البوابات والإعدادات والمفضلة والسجل وحالة استكمال المشاهدة.',
                                                                               'de': 'Sichert oder stellt Portale, Einstellungen, Favoriten, Verlauf und Fortsetzungsstatus wieder '
                                                                                     'her.',
                                                                               'fr': 'Sauvegarde ou restaure les portails, paramètres, favoris, historique et état de reprise.',
                                                                               'tr': 'Portalları, ayarları, favorileri, geçmişi ve devam durumunu yedekler veya geri yükler.'},
 'Create a redacted diagnostics ZIP for troubleshooting.': {'ar': 'أنشئ ملف ZIP تشخيصيًا منقحًا لاستكشاف الأعطال.',
                                                            'de': 'Erstellt ein bereinigtes Diagnose-ZIP zur Fehlersuche.',
                                                            'fr': 'Crée une archive ZIP de diagnostic expurgée pour le dépannage.',
                                                            'tr': 'Sorun giderme için hassas verileri ayıklanmış tanılama ZIP’i oluşturur.'},
 'Web Cleaner Access': {'ar': 'الوصول إلى Web Cleaner', 'de': 'Web-Cleaner-Zugriff', 'fr': 'Accès Web Cleaner', 'tr': 'Web Cleaner Erişimi'},
 'Easy opens directly on the local network; Protected requires pairing.': {'ar': 'الوضع السهل يفتح مباشرة على الشبكة المحلية؛ الوضع المحمي يتطلب الاقتران.',
                                                                           'de': '„Einfach“ öffnet direkt im lokalen Netzwerk; „Geschützt“ erfordert eine Kopplung.',
                                                                           'fr': 'Le mode Facile s’ouvre directement sur le réseau local ; le mode Protégé nécessite un appairage.',
                                                                           'tr': 'Kolay mod yerel ağda doğrudan açılır; Korumalı mod eşleştirme gerektirir.'},
 'Web Cleaner': {'ar': 'Web Cleaner', 'de': 'Web Cleaner', 'fr': 'Web Cleaner', 'tr': 'Web Cleaner'},
 'Start/stop the Premium Web Cleaner and Deep Check service on port 7725.': {'ar': 'ابدأ/أوقف خدمة Premium Web Cleaner وDeep Check على المنفذ 7725.',
                                                                             'de': 'Startet/stoppt den Premium-Web-Cleaner- und Deep-Check-Dienst auf Port 7725.',
                                                                             'fr': 'Démarre/arrête le service Premium Web Cleaner et Deep Check sur le port 7725.',
                                                                             'tr': '7725 portundaki Premium Web Cleaner ve Deep Check hizmetini başlatır/durdurur.'},
 'View package, database, cache and portal diagnostic information.': {'ar': 'اعرض معلومات تشخيص الحزمة وقاعدة البيانات والكاش والبوابة.',
                                                                      'de': 'Zeigt Diagnoseinformationen zu Paket, Datenbank, Cache und Portal an.',
                                                                      'fr': 'Affiche les informations de diagnostic du paquet, de la base de données, du cache et du portail.',
                                                                      'tr': 'Paket, veritabanı, önbellek ve portal tanılama bilgilerini görüntüler.'},
 'Diagnostic Logging': {'ar': 'سجل التشخيص', 'de': 'Diagnoseprotokollierung', 'fr': 'Journal de diagnostic', 'tr': 'Tanılama Günlüğü'},
 'Enable verbose rotating diagnostic logs after plugin restart.': {'ar': 'فعّل سجلات تشخيص تفصيلية ومتداولة بعد إعادة تشغيل البلجن.',
                                                                   'de': 'Aktiviert ausführliche, rotierende Diagnoseprotokolle nach Plugin-Neustart.',
                                                                   'fr': 'Active des journaux de diagnostic détaillés avec rotation après redémarrage du plugin.',
                                                                   'tr': 'Eklenti yeniden başlatıldıktan sonra ayrıntılı dönen tanılama günlüklerini etkinleştirir.'},
 'Hide Adult Categories': {'ar': 'إخفاء أقسام البالغين', 'de': 'Erwachsenenkategorien ausblenden', 'fr': 'Masquer les catégories adultes', 'tr': 'Yetişkin Kategorilerini Gizle'},
 'Hide categories matching adult keywords when Parental Lock is disabled.': {'ar': 'أخفِ الأقسام المطابقة لكلمات البالغين عند تعطيل الرقابة الأبوية.',
                                                                             'de': 'Blendet Kategorien mit passenden Erwachsenen-Schlüsselwörtern aus, wenn die Kindersicherung '
                                                                                   'deaktiviert ist.',
                                                                             'fr': 'Masque les catégories correspondant aux mots-clés adulte lorsque le verrouillage parental est '
                                                                                   'désactivé.',
                                                                             'tr': 'Ebeveyn Kilidi kapalıyken yetişkin anahtar kelimeleriyle eşleşen kategorileri gizler.'},
 'Hide Empty Categories': {'ar': 'إخفاء الأقسام الفارغة', 'de': 'Leere Kategorien ausblenden', 'fr': 'Masquer les catégories vides', 'tr': 'Boş Kategorileri Gizle'},
 'Hide categories detected as empty after loading.': {'ar': 'أخفِ الأقسام التي يتبين أنها فارغة بعد التحميل.',
                                                      'de': 'Blendet Kategorien aus, die nach dem Laden als leer erkannt werden.',
                                                      'fr': 'Masque les catégories détectées comme vides après chargement.',
                                                      'tr': 'Yüklendikten sonra boş olduğu belirlenen kategorileri gizler.'},
 'Remember Last Location': {'ar': 'تذكر آخر موضع', 'de': 'Letzte Position merken', 'fr': 'Mémoriser le dernier emplacement', 'tr': 'Son Konumu Hatırla'},
 'Restore the last Home/content location for each portal.': {'ar': 'استعد آخر موضع في الرئيسية/المحتوى لكل بوابة.',
                                                             'de': 'Stellt die zuletzt besuchte Start-/Inhaltsposition für jedes Portal wieder her.',
                                                             'fr': 'Restaure le dernier emplacement Accueil/contenu pour chaque portail.',
                                                             'tr': 'Her portal için son Ana Sayfa/içerik konumunu geri yükler.'},
 'Image Cache Limit': {'ar': 'حد ذاكرة الصور', 'de': 'Bild-Cache-Limit', 'fr': 'Limite du cache d’images', 'tr': 'Görsel Önbellek Limiti'},
 'Maximum persistent artwork cache size.': {'ar': 'أقصى حجم دائم لذاكرة الصور.',
                                            'de': 'Maximale Größe des dauerhaften Bild-Caches.',
                                            'fr': 'Taille maximale du cache permanent des illustrations.',
                                            'tr': 'Kalıcı görsel önbelleğinin azami boyutu.'},
 'Bouquet Proxy Port': {'ar': 'منفذ بروكسي الباقات', 'de': 'Bouquet-Proxy-Port', 'fr': 'Port proxy des bouquets', 'tr': 'Buket Proxy Portu'},
 'Local loopback port used by dynamic bouquet playback and XMLTV integration.': {'ar': 'منفذ محلي يستخدم لتشغيل الباقات الديناميكية وتكامل XMLTV.',
                                                                                 'de': 'Lokaler Loopback-Port für dynamische Bouquet-Wiedergabe und XMLTV-Integration.',
                                                                                 'fr': 'Port de boucle locale utilisé pour la lecture des bouquets dynamiques et l’intégration '
                                                                                       'XMLTV.',
                                                                                 'tr': 'Dinamik buket oynatma ve XMLTV entegrasyonunda kullanılan yerel loopback portu.'},
 'EPG Refresh Budget': {'ar': 'مهلة تحديث EPG', 'de': 'EPG-Aktualisierungsbudget', 'fr': 'Budget d’actualisation EPG', 'tr': 'EPG Yenileme Bütçesi'},
 'Maximum background XMLTV refresh time budget.': {'ar': 'أقصى مدة مسموحة لتحديث XMLTV في الخلفية.',
                                                   'de': 'Maximales Zeitbudget für die XMLTV-Aktualisierung im Hintergrund.',
                                                   'fr': 'Durée maximale allouée à l’actualisation XMLTV en arrière-plan.',
                                                   'tr': 'Arka plandaki XMLTV yenilemesi için azami süre bütçesi.'},
 'Search Scan Pages': {'ar': 'صفحات البحث القصوى', 'de': 'Durchsuchte Seiten bei der Suche', 'fr': 'Pages analysées pour la recherche', 'tr': 'Arama Tarama Sayfaları'},
 'Maximum catalogue pages scanned when native portal search is unavailable.': {'ar': 'أقصى عدد صفحات يتم فحصها عند عدم توفر بحث البوابة الأصلي.',
                                                                               'de': 'Maximale Anzahl an Katalogseiten, die durchsucht werden, wenn keine native Portal-Suche '
                                                                                     'verfügbar ist.',
                                                                               'fr': 'Nombre maximal de pages de catalogue analysées lorsque la recherche native du portail n’est '
                                                                                     'pas disponible.',
                                                                               'tr': 'Portalın yerel araması yoksa taranacak en fazla katalog sayfası.'},
 'Search Time Budget': {'ar': 'مهلة البحث', 'de': 'Zeitbudget für die Suche', 'fr': 'Budget temps de recherche', 'tr': 'Arama Süre Bütçesi'},
 'Maximum time spent searching one portal before partial results are returned.': {'ar': 'أقصى مدة للبحث في بوابة واحدة قبل إرجاع نتائج جزئية.',
                                                                                  'de': 'Maximale Suchzeit pro Portal, bevor Teilergebnisse zurückgegeben werden.',
                                                                                  'fr': 'Temps maximal passé à rechercher dans un portail avant de renvoyer des résultats '
                                                                                        'partiels.',
                                                                                  'tr': 'Kısmi sonuçlar dönmeden önce tek bir portalda harcanacak en fazla arama süresi.'},
 'Portal server': {'ar': 'خادم البوابة', 'de': 'Portal-Server', 'fr': 'Serveur du portail', 'tr': 'Portal Sunucusu'},
 'SELECT PORTAL': {'ar': 'اختر البوابة', 'de': 'PORTAL AUSWÄHLEN', 'fr': 'SÉLECTIONNER UN PORTAIL', 'tr': 'PORTAL SEÇ'},
 'Disable': {'ar': 'تعطيل', 'de': 'Deaktivieren', 'fr': 'Désactiver', 'tr': 'Devre Dışı Bırak'},
 'Rename': {'ar': 'إعادة تسمية', 'de': 'Umbenennen', 'fr': 'Renommer', 'tr': 'Yeniden Adlandır'},
 'Check All': {'ar': 'فحص الكل', 'de': 'Alle prüfen', 'fr': 'Tout vérifier', 'tr': 'Tümünü Kontrol Et'},
 'Edit Order': {'ar': 'ترتيب البوابات', 'de': 'Reihenfolge bearbeiten', 'fr': 'Modifier l’ordre', 'tr': 'Sıralamayı Düzenle'},
 'Checking portal...': {'ar': 'جارٍ فحص البوابة...', 'de': 'Portal wird geprüft...', 'fr': 'Vérification du portail...', 'tr': 'Portal kontrol ediliyor...'},
 'Edit order  •  UP/DOWN moves the selected portal  •  BLUE/OK saves': {'ar': 'تعديل الترتيب  •  أعلى/أسفل لتحريك البوابة المحددة  •  الأزرق/OK للحفظ',
                                                                        'de': 'Reihenfolge bearbeiten  •  HOCH/RUNTER verschiebt das ausgewählte Portal  •  BLAU/OK speichert',
                                                                        'fr': 'Modifier l’ordre  •  HAUT/BAS déplace le portail sélectionné  •  BLEU/OK enregistre',
                                                                        'tr': 'Sıralamayı düzenle  •  YUKARI/AŞAĞI seçili portalı taşır  •  MAVİ/OK kaydeder'},
 'Moved to position %d of %d  •  BLUE/OK when done': {'ar': 'تم النقل إلى الموضع %d من %d  •  اضغط الأزرق/OK عند الانتهاء',
                                                      'de': 'Auf Position %d von %d verschoben  •  BLAU/OK zum Abschließen',
                                                      'fr': 'Déplacé à la position %d sur %d  •  BLEU/OK lorsque terminé',
                                                      'tr': '%d / %d konumuna taşındı  •  Bitince MAVİ/OK'},
 'Portal order saved': {'ar': 'تم حفظ ترتيب البوابات', 'de': 'Portal-Reihenfolge gespeichert', 'fr': 'Ordre des portails enregistré', 'tr': 'Portal sıralaması kaydedildi'},
 'Already at the top': {'ar': 'موجود بالفعل في الأعلى', 'de': 'Bereits ganz oben', 'fr': 'Déjà tout en haut', 'tr': 'Zaten en üstte'},
 'Already at the bottom': {'ar': 'موجود بالفعل في الأسفل', 'de': 'Bereits ganz unten', 'fr': 'Déjà tout en bas', 'tr': 'Zaten en altta'},
 'Order save failed: %s': {'ar': 'فشل حفظ الترتيب: %s',
                           'de': 'Reihenfolge speichern fehlgeschlagen: %s',
                           'fr': 'Échec de l’enregistrement de l’ordre : %s',
                           'tr': 'Sıralama kaydedilemedi: %s'},
 'Open portal': {'ar': 'فتح البوابة', 'de': 'Portal öffnen', 'fr': 'Ouvrir le portail', 'tr': 'Portalı Aç'},
 'Portal name': {'ar': 'اسم البوابة', 'de': 'Portal-Name', 'fr': 'Nom du portail', 'tr': 'Portal Adı'},
 'Portal URL (HTTPS preferred)': {'ar': 'رابط البوابة (يفضل HTTPS)',
                                  'de': 'Portal-URL (HTTPS bevorzugt)',
                                  'fr': 'URL du portail (HTTPS recommandé)',
                                  'tr': 'Portal URL’si (HTTPS önerilir)'},
 'MAC address': {'ar': 'عنوان MAC', 'de': 'MAC-Adresse', 'fr': 'Adresse MAC', 'tr': 'MAC adresi'},
 'Enter URL': {'ar': 'إدخال الرابط', 'de': 'URL eingeben', 'fr': 'Saisir l’URL', 'tr': 'URL Gir'},
 'Enter MAC': {'ar': 'إدخال MAC', 'de': 'MAC eingeben', 'fr': 'Saisir le MAC', 'tr': 'MAC Gir'},
 'Portal connection failed: %s': {'ar': 'فشل الاتصال بالبوابة: %s',
                                  'de': 'Portal-Verbindung fehlgeschlagen: %s',
                                  'fr': 'Échec de connexion au portail : %s',
                                  'tr': 'Portal bağlantısı başarısız: %s'},
 'First-run wizard will start on the next plugin launch.': {'ar': 'سيبدأ معالج الإعداد الأول عند تشغيل البلجن مرة أخرى.',
                                                            'de': 'Der Einrichtungsassistent startet beim nächsten Öffnen des Plugins.',
                                                            'fr': 'L’assistant de premier démarrage s’ouvrira au prochain lancement du plugin.',
                                                            'tr': 'İlk kurulum sihirbazı eklentinin bir sonraki açılışında başlayacak.'},
 'Reset wizard': {'ar': 'إعادة معالج الإعداد', 'de': 'Assistent zurücksetzen', 'fr': 'Réinitialiser l’assistant', 'tr': 'Sihirbazı Sıfırla'},
 'WELCOME': {'ar': 'مرحبًا', 'de': 'WILLKOMMEN', 'fr': 'BIENVENUE', 'tr': 'HOŞ GELDİNİZ'},
 'Start setup': {'ar': 'بدء الإعداد', 'de': 'Einrichtung starten', 'fr': 'Démarrer la configuration', 'tr': 'Kurulumu Başlat'},
 'Skip wizard': {'ar': 'تخطي المعالج', 'de': 'Assistent überspringen', 'fr': 'Ignorer l’assistant', 'tr': 'Sihirbazı Atla'},
 'Retry setup': {'ar': 'إعادة المحاولة', 'de': 'Einrichtung erneut versuchen', 'fr': 'Réessayer la configuration', 'tr': 'Kurulumu Yeniden Dene'},
 'Setup complete. Opening your portal…': {'ar': 'اكتمل الإعداد. جارٍ فتح البوابة…',
                                          'de': 'Einrichtung abgeschlossen. Ihr Portal wird geöffnet…',
                                          'fr': 'Configuration terminée. Ouverture du portail…',
                                          'tr': 'Kurulum tamamlandı. Portal açılıyor…'},
 'Testing…': {'ar': 'جارٍ الاختبار…', 'de': 'Wird getestet…', 'fr': 'Test en cours…', 'tr': 'Test ediliyor…'},
 '%d files / %.1f MB': {'ar': '%d ملف / %.1f م.ب', 'de': '%d Dateien / %.1f MB', 'fr': '%d fichiers / %.1f Mo', 'tr': '%d dosya / %.1f MB'},
 '%d MB': {'ar': '%d م.ب', 'de': '%d MB', 'fr': '%d Mo', 'tr': '%d MB'},
 '%d min': {'ar': '%d دقيقة', 'de': '%d Min', 'fr': '%d min', 'tr': '%d dk'},
 '%d options': {'ar': '%d خيار', 'de': '%d Optionen', 'fr': '%d options', 'tr': '%d seçenek'},
 '%d seconds': {'ar': '%d ثانية', 'de': '%d Sekunden', 'fr': '%d secondes', 'tr': '%d saniye'},
 '%d words': {'ar': '%d كلمة', 'de': '%d Wörter', 'fr': '%d mots', 'tr': '%d kelime'},
 '%sh': {'ar': '%s س', 'de': '%sh', 'fr': '%s h', 'tr': '%s sa'},
 '%ss': {'ar': '%s ث', 'de': '%ss', 'fr': '%s s', 'tr': '%s sn'},
 '%s%%': {'ar': '%s%%', 'de': '%s%%', 'fr': '%s%%', 'tr': '%s%%'},
 'Current: %s': {'ar': 'الحالي: %s', 'de': 'Aktuell: %s', 'fr': 'Actuel : %s', 'tr': 'Mevcut: %s'},
 'Setting saved': {'ar': 'تم حفظ الإعداد', 'de': 'Einstellung gespeichert', 'fr': 'Paramètre enregistré', 'tr': 'Ayar kaydedildi'},
 'Setting updated': {'ar': 'تم تحديث الإعداد', 'de': 'Einstellung aktualisiert', 'fr': 'Paramètre mis à jour', 'tr': 'Ayar güncellendi'},
 'Proxy port saved. Restart the plugin before exporting bouquets again.': {'ar': 'تم حفظ منفذ البروكسي. أعد تشغيل البلجن قبل تصدير الباقات مرة أخرى.',
                                                                           'de': 'Proxy-Port gespeichert. Starten Sie das Plugin neu, bevor Sie erneut Bouquets exportieren.',
                                                                           'fr': 'Port proxy enregistré. Redémarrez le plugin avant d’exporter à nouveau les bouquets.',
                                                                           'tr': 'Proxy portu kaydedildi. Buketleri yeniden dışa aktarmadan önce eklentiyi yeniden başlatın.'},
 'Unable to reset wizard: %s': {'ar': 'تعذر إعادة معالج الإعداد: %s',
                                'de': 'Assistent kann nicht zurückgesetzt werden: %s',
                                'fr': 'Impossible de réinitialiser l’assistant : %s',
                                'tr': 'Sihirbaz sıfırlanamadı: %s'},
 'Live TV': {'ar': 'البث المباشر', 'de': 'Live-TV', 'fr': 'TV en direct', 'tr': 'Canlı TV'},
 'Watch live channels': {'ar': 'شاهد القنوات المباشرة', 'de': 'Live-Kanäle ansehen', 'fr': 'Regarder les chaînes en direct', 'tr': 'Canlı kanalları izle'},
 'Movies': {'ar': 'الأفلام', 'de': 'Filme', 'fr': 'Films', 'tr': 'Filmler'},
 'Browse movie library': {'ar': 'تصفح مكتبة الأفلام', 'de': 'Filmbibliothek durchsuchen', 'fr': 'Parcourir la bibliothèque de films', 'tr': 'Film kütüphanesine göz at'},
 'Series': {'ar': 'المسلسلات', 'de': 'Serien', 'fr': 'Séries', 'tr': 'Diziler'},
 'Explore TV series': {'ar': 'استكشف المسلسلات', 'de': 'TV-Serien entdecken', 'fr': 'Explorer les séries TV', 'tr': 'TV dizilerini keşfet'},
 'Catch-up TV': {'ar': 'المشاهدة المؤجلة', 'de': 'Catch-up-TV', 'fr': 'TV Catch-up', 'tr': 'Catch-up TV'},
 'Watch past programs': {'ar': 'شاهد البرامج السابقة', 'de': 'Vergangene Sendungen ansehen', 'fr': 'Regarder les programmes passés', 'tr': 'Geçmiş programları izle'},
 'Favorites': {'ar': 'المفضلة', 'de': 'Favoriten', 'fr': 'Favoris', 'tr': 'Favoriler'},
 'Your saved content': {'ar': 'المحتوى المحفوظ', 'de': 'Ihre gespeicherten Inhalte', 'fr': 'Votre contenu enregistré', 'tr': 'Kaydettiğiniz içerik'},
 'Search': {'ar': 'بحث', 'de': 'Suche', 'fr': 'Recherche', 'tr': 'Ara'},
 'Global Search': {'ar': 'البحث العام', 'de': 'Globale Suche', 'fr': 'Recherche globale', 'tr': 'Global Arama'},
 'Find content quickly': {'ar': 'اعثر على المحتوى بسرعة', 'de': 'Inhalte schnell finden', 'fr': 'Trouver rapidement du contenu', 'tr': 'İçeriği hızlıca bul'},
 'Portal & player settings': {'ar': 'إعدادات البوابة والمشغل',
                              'de': 'Portal- & Player-Einstellungen',
                              'fr': 'Paramètres du portail et du lecteur',
                              'tr': 'Portal ve oynatıcı ayarları'},
 'Continue Watching': {'ar': 'متابعة المشاهدة', 'de': 'Weiterschauen', 'fr': 'Continuer à regarder', 'tr': 'İzlemeye Devam'},
 'Account Information': {'ar': 'معلومات الحساب', 'de': 'Kontoinformationen', 'fr': 'Informations du compte', 'tr': 'Hesap Bilgileri'},
 'CONTINUE / RECENT': {'ar': 'متابعة / الأحدث', 'de': 'WEITERSCHAUEN / KÜRZLICH', 'fr': 'CONTINUER / RÉCENT', 'tr': 'DEVAM / SON'},
 'Last Live': {'ar': 'آخر بث مباشر', 'de': 'Zuletzt Live', 'fr': 'Dernier direct', 'tr': 'Son Canlı'},
 'Last Movie': {'ar': 'آخر فيلم', 'de': 'Zuletzt Film', 'fr': 'Dernier film', 'tr': 'Son Film'},
 'Last Series': {'ar': 'آخر مسلسل', 'de': 'Zuletzt Serie', 'fr': 'Dernière série', 'tr': 'Son Dizi'},
 'Nothing played yet': {'ar': 'لم يتم تشغيل شيء بعد', 'de': 'Noch nichts abgespielt', 'fr': 'Rien n’a encore été lu', 'tr': 'Henüz hiçbir şey oynatılmadı'},
 'Open something and it will appear here': {'ar': 'افتح أي محتوى وسيظهر هنا',
                                            'de': 'Öffnen Sie etwas, dann erscheint es hier',
                                            'fr': 'Ouvrez un contenu et il apparaîtra ici',
                                            'tr': 'Bir içerik açın, burada görünecek'},
 'Portal sections': {'ar': 'أقسام البوابة', 'de': 'Portal-Bereiche', 'fr': 'Sections du portail', 'tr': 'Portal Bölümleri'},
 'Choose a section': {'ar': 'اختر قسمًا', 'de': 'Bereich auswählen', 'fr': 'Choisissez une section', 'tr': 'Bir bölüm seçin'},
 'Live, Movies, Series, Favorites and Continue Watching': {'ar': 'البث المباشر، الأفلام، المسلسلات، المفضلة ومتابعة المشاهدة',
                                                           'de': 'Live, Filme, Serien, Favoriten und Weiterschauen',
                                                           'fr': 'Direct, Films, Séries, Favoris et Continuer à regarder',
                                                           'tr': 'Canlı, Filmler, Diziler, Favoriler ve İzlemeye Devam'},
 'Authorize': {'ar': 'إعادة التوثيق', 'de': 'Autorisieren', 'fr': 'Autoriser', 'tr': 'Yetkilendir'},
 'Refresh': {'ar': 'تحديث', 'de': 'Aktualisieren', 'fr': 'Actualiser', 'tr': 'Yenile'},
 'Open / Play': {'ar': 'فتح / تشغيل', 'de': 'Öffnen / Abspielen', 'fr': 'Ouvrir / Lire', 'tr': 'Aç / Oynat'},
 'Portal Manager': {'ar': 'إدارة البوابات', 'de': 'Portal-Manager', 'fr': 'Gestionnaire de portails', 'tr': 'Portal Yöneticisi'},
 'Free Portal Library': {'ar': 'مكتبة البوابات المجانية', 'de': 'Kostenlose Portal-Bibliothek', 'fr': 'Bibliothèque de portails gratuits', 'tr': 'Ücretsiz Portal Kütüphanesi'},
 'Free Xtream Library': {'ar': 'مكتبة Xtream المجانية', 'de': 'Kostenlose Xtream-Bibliothek', 'fr': 'Bibliothèque Xtream gratuite', 'tr': 'Ücretsiz Xtream Kütüphanesi'},
 'Import Selected': {'ar': 'استيراد المحدد', 'de': 'Auswahl importieren', 'fr': 'Importer la sélection', 'tr': 'Seçilenleri İçe Aktar'},
 'Select All': {'ar': 'تحديد الكل', 'de': 'Alle auswählen', 'fr': 'Tout sélectionner', 'tr': 'Tümünü Seç'},
 'Clear': {'ar': 'مسح التحديد', 'de': 'Leeren', 'fr': 'Effacer', 'tr': 'Temizle'},
 'Options': {'ar': 'الخيارات', 'de': 'Optionen', 'fr': 'Options', 'tr': 'Seçenekler'},
 'INFORMATION': {'ar': 'معلومات', 'de': 'INFORMATIONEN', 'fr': 'INFORMATIONS', 'tr': 'BİLGİ'},
 'OVERVIEW': {'ar': 'الملخص', 'de': 'ÜBERBLICK', 'fr': 'RÉSUMÉ', 'tr': 'ÖZET'},
 'Cast': {'ar': 'طاقم التمثيل', 'de': 'Besetzung', 'fr': 'Distribution', 'tr': 'Oyuncular'},
 'Director': {'ar': 'المخرج', 'de': 'Regie', 'fr': 'Réalisateur', 'tr': 'Yönetmen'},
 'Writer': {'ar': 'الكاتب', 'de': 'Drehbuch', 'fr': 'Scénariste', 'tr': 'Yazar'},
 'Downloads': {'ar': 'التنزيلات', 'de': 'Downloads', 'fr': 'Téléchargements', 'tr': 'İndirmeler'},
 'Cancel selected': {'ar': 'إلغاء المحدد', 'de': 'Auswahl abbrechen', 'fr': 'Annuler la sélection', 'tr': 'Seçileni İptal Et'},
 'Retry failed': {'ar': 'إعادة محاولة الفاشل', 'de': 'Fehlgeschlagene erneut versuchen', 'fr': 'Réessayer les échecs', 'tr': 'Başarısızları Yeniden Dene'},
 'No description available.': {'ar': 'لا يوجد وصف متاح.', 'de': 'Keine Beschreibung verfügbar.', 'fr': 'Aucune description disponible.', 'tr': 'Açıklama mevcut değil.'},
 'No results': {'ar': 'لا توجد نتائج', 'de': 'Keine Ergebnisse', 'fr': 'Aucun résultat', 'tr': 'Sonuç yok'},
 'No content': {'ar': 'لا يوجد محتوى', 'de': 'Kein Inhalt', 'fr': 'Aucun contenu', 'tr': 'İçerik yok'},
 'No seasons': {'ar': 'لا توجد مواسم', 'de': 'Keine Staffeln', 'fr': 'Aucune saison', 'tr': 'Sezon yok'},
 'No episodes': {'ar': 'لا توجد حلقات', 'de': 'Keine Episoden', 'fr': 'Aucun épisode', 'tr': 'Bölüm yok'},
 'Choose a season': {'ar': 'اختر موسمًا', 'de': 'Staffel auswählen', 'fr': 'Choisissez une saison', 'tr': 'Bir sezon seçin'},
 'SEASONS': {'ar': 'المواسم', 'de': 'STAFFELN', 'fr': 'SAISONS', 'tr': 'SEZONLAR'},
 'EPISODES': {'ar': 'الحلقات', 'de': 'EPISODEN', 'fr': 'ÉPISODES', 'tr': 'BÖLÜMLER'},
 'EPISODE DETAILS': {'ar': 'تفاصيل الحلقة', 'de': 'EPISODENDETAILS', 'fr': 'DÉTAILS DE L’ÉPISODE', 'tr': 'BÖLÜM AYRINTILARI'},
 'Cache Artwork': {'ar': 'تخزين الصور', 'de': 'Bilder zwischenspeichern', 'fr': 'Mettre les illustrations en cache', 'tr': 'Görselleri Önbelleğe Al'},
 'Cache Picons': {'ar': 'تخزين شعارات القنوات', 'de': 'Picons zwischenspeichern', 'fr': 'Mettre les picons en cache', 'tr': 'Piconları Önbelleğe Al'},
 'Loading...': {'ar': 'جارٍ التحميل...', 'de': 'Wird geladen...', 'fr': 'Chargement...', 'tr': 'Yükleniyor...'},
 'Loading': {'ar': 'جارٍ التحميل', 'de': 'Wird geladen', 'fr': 'Chargement', 'tr': 'Yükleniyor'},
 'Please wait': {'ar': 'يرجى الانتظار', 'de': 'Bitte warten', 'fr': 'Veuillez patienter', 'tr': 'Lütfen bekleyin'},
 'Initializing premium interface': {'ar': 'جارٍ تهيئة الواجهة',
                                    'de': 'Premium-Oberfläche wird initialisiert',
                                    'fr': 'Initialisation de l’interface premium',
                                    'tr': 'Premium arayüz başlatılıyor'},
 'Web Cleaner Ready': {'ar': 'Web Cleaner جاهز', 'de': 'Web Cleaner bereit', 'fr': 'Web Cleaner prêt', 'tr': 'Web Cleaner Hazır'},
 'Open this address on the same local network:': {'ar': 'افتح هذا العنوان على نفس الشبكة المحلية:',
                                                  'de': 'Öffnen Sie diese Adresse im selben lokalen Netzwerk:',
                                                  'fr': 'Ouvrez cette adresse sur le même réseau local :',
                                                  'tr': 'Bu adresi aynı yerel ağda açın:'},
 'PAIRING CODE': {'ar': 'كود الاقتران', 'de': 'KOPPLUNGSCODE', 'fr': 'CODE D’APPAIRAGE', 'tr': 'EŞLEŞTİRME KODU'},
 'Enter the code once. Your browser keeps the secure session until Web Cleaner is stopped.': {'ar': 'أدخل الكود مرة واحدة. سيحتفظ المتصفح بالجلسة الآمنة حتى يتم إيقاف Web '
                                                                                                    'Cleaner.',
                                                                                              'de': 'Geben Sie den Code einmal ein. Ihr Browser behält die sichere Sitzung, bis '
                                                                                                    'der Web Cleaner gestoppt wird.',
                                                                                              'fr': 'Saisissez le code une fois. Le navigateur conserve la session sécurisée '
                                                                                                    'jusqu’à l’arrêt de Web Cleaner.',
                                                                                              'tr': 'Kodu bir kez girin. Web Cleaner durdurulana kadar tarayıcınız güvenli oturumu '
                                                                                                    'korur.'},
 'Scan QR for instant access': {'ar': 'امسح QR للدخول فورًا',
                                'de': 'QR-Code scannen für sofortigen Zugriff',
                                'fr': 'Scannez le QR pour un accès instantané',
                                'tr': 'Anında erişim için QR’ı tara'},
 'QR unavailable • use the 6-digit code': {'ar': 'QR غير متاح • استخدم الكود المكوّن من 6 أرقام',
                                           'de': 'QR nicht verfügbar • verwenden Sie den 6-stelligen Code',
                                           'fr': 'QR indisponible • utilisez le code à 6 chiffres',
                                           'tr': 'QR kullanılamıyor • 6 haneli kodu kullanın'},
 'EASY ACCESS': {'ar': 'دخول سهل', 'de': 'EINFACHER ZUGRIFF', 'fr': 'ACCÈS FACILE', 'tr': 'KOLAY ERİŞİM'},
 'NO PIN': {'ar': 'بدون PIN', 'de': 'KEINE PIN', 'fr': 'SANS PIN', 'tr': 'PIN YOK'},
 'Direct access is limited to devices on the local network. Use Stop Service when finished.': {'ar': 'الدخول المباشر متاح فقط للأجهزة على الشبكة المحلية. استخدم إيقاف الخدمة عند '
                                                                                                     'الانتهاء.',
                                                                                               'de': 'Der Direktzugriff ist auf Geräte im lokalen Netzwerk beschränkt. Nutzen Sie '
                                                                                                     '„Dienst stoppen“, wenn Sie fertig sind.',
                                                                                               'fr': 'L’accès direct est limité aux appareils du réseau local. Utilisez Arrêter le '
                                                                                                     'service lorsque vous avez terminé.',
                                                                                               'tr': 'Doğrudan erişim yalnızca yerel ağdaki cihazlarla sınırlıdır. İşiniz bitince '
                                                                                                     'Hizmeti Durdur’u kullanın.'},
 'PIN / QR protection can be enabled from Settings': {'ar': 'يمكن تفعيل حماية PIN / QR من الإعدادات',
                                                      'de': 'PIN-/QR-Schutz kann in den Einstellungen aktiviert werden',
                                                      'fr': 'La protection PIN / QR peut être activée dans les Paramètres',
                                                      'tr': 'PIN / QR koruması Ayarlar’dan etkinleştirilebilir'},
 'SYSTEM DIAGNOSTICS  •  NOVA FHD': {'ar': 'تشخيص النظام  •  NOVA FHD',
                                     'de': 'SYSTEMDIAGNOSE  •  NOVA FHD',
                                     'fr': 'DIAGNOSTIC SYSTÈME  •  NOVA FHD',
                                     'tr': 'SİSTEM TANILAMA  •  NOVA FHD'},
 'GREEN: refresh  •  YELLOW: support bundle': {'ar': 'الأخضر: تحديث  •  الأصفر: حزمة الدعم',
                                               'de': 'GRÜN: aktualisieren  •  GELB: Support-Paket',
                                               'fr': 'VERT : actualiser  •  JAUNE : paquet de support',
                                               'tr': 'YEŞİL: yenile  •  SARI: destek paketi'},
 'Professional channel list with lazy Now / Next EPG': {'ar': 'قائمة قنوات احترافية مع EPG الآن/التالي عند الطلب',
                                                        'de': 'Professionelle Kanalliste mit nachgeladenem Jetzt/Danach-EPG',
                                                        'fr': 'Liste de chaînes professionnelle avec EPG Maintenant / Suivant à la demande',
                                                        'tr': 'İsteğe bağlı Şimdi / Sonraki EPG’li profesyonel kanal listesi'},
 'Movies with artwork, details, favorites and watched state': {'ar': 'أفلام مع الصور والتفاصيل والمفضلة وحالة المشاهدة',
                                                               'de': 'Filme mit Bildern, Details, Favoriten und Gesehen-Status',
                                                               'fr': 'Films avec illustrations, détails, favoris et état de visionnage',
                                                               'tr': 'Görseller, ayrıntılar, favoriler ve izlenme durumuyla filmler'},
 'Series, seasons, episodes and resume support': {'ar': 'مسلسلات ومواسم وحلقات مع دعم استكمال المشاهدة',
                                                  'de': 'Serien, Staffeln, Episoden und Fortsetzungsunterstützung',
                                                  'fr': 'Séries, saisons, épisodes et reprise de lecture',
                                                  'tr': 'Diziler, sezonlar, bölümler ve devam desteği'},
 'Archived programmes and replay TV': {'ar': 'برامج مؤرشفة وإعادة مشاهدة التلفزيون',
                                       'de': 'Archivierte Sendungen und Replay-TV',
                                       'fr': 'Programmes archivés et TV en replay',
                                       'tr': 'Arşiv programları ve tekrar TV'},
 'Your saved content  •  %d items': {'ar': 'المحتوى المحفوظ  •  %d عنصر',
                                     'de': 'Ihre gespeicherten Inhalte  •  %d Einträge',
                                     'fr': 'Votre contenu enregistré  •  %d éléments',
                                     'tr': 'Kaydettiğiniz içerik  •  %d öğe'},
 'Continue watching  •  %d items': {'ar': 'متابعة المشاهدة  •  %d عنصر',
                                    'de': 'Weiterschauen  •  %d Einträge',
                                    'fr': 'Continuer à regarder  •  %d éléments',
                                    'tr': 'İzlemeye devam  •  %d öğe'},
 'Subscription and portal health details': {'ar': 'تفاصيل الاشتراك وحالة البوابة',
                                            'de': 'Abonnement- und Portal-Statusdetails',
                                            'fr': 'Détails de l’abonnement et état du portail',
                                            'tr': 'Abonelik ve portal durum ayrıntıları'},
 'One search across Live, Movies and Series': {'ar': 'بحث واحد في البث المباشر والأفلام والمسلسلات',
                                               'de': 'Eine Suche über Live, Filme und Serien',
                                               'fr': 'Une recherche dans Direct, Films et Séries',
                                               'tr': 'Canlı, Filmler ve Dizilerde tek arama'},
 'Themes, lists, Smart Engine, privacy and cache': {'ar': 'المظاهر والقوائم والخصوصية والكاش',
                                                    'de': 'Designs, Listen, Smart Engine, Datenschutz und Cache',
                                                    'fr': 'Thèmes, listes, moteur intelligent, confidentialité et cache',
                                                    'tr': 'Temalar, listeler, Akıllı Motor, gizlilik ve önbellek'},
 'Press OK to browse %s': {'ar': 'اضغط OK لتصفح %s', 'de': 'OK drücken, um %s zu durchsuchen', 'fr': 'Appuyez sur OK pour parcourir %s', 'tr': '%s göz atmak için OK’a basın'},
 'PORTAL HOME  •  %d sections': {'ar': 'الرئيسية  •  %d أقسام',
                                 'de': 'PORTAL-STARTSEITE  •  %d Bereiche',
                                 'fr': 'ACCUEIL PORTAIL  •  %d sections',
                                 'tr': 'PORTAL ANA SAYFA  •  %d bölüm'},
 'Loading content...': {'ar': 'جارٍ تحميل المحتوى...', 'de': 'Inhalt wird geladen...', 'fr': 'Chargement du contenu...', 'tr': 'İçerik yükleniyor...'},
 'Loading categories...': {'ar': 'جارٍ تحميل الأقسام...', 'de': 'Kategorien werden geladen...', 'fr': 'Chargement des catégories...', 'tr': 'Kategoriler yükleniyor...'},
 'Loading EPG...': {'ar': 'جارٍ تحميل EPG...', 'de': 'EPG wird geladen...', 'fr': 'Chargement de l’EPG...', 'tr': 'EPG yükleniyor...'},
 'Loading account information...': {'ar': 'جارٍ تحميل معلومات الحساب...',
                                    'de': 'Kontoinformationen werden geladen...',
                                    'fr': 'Chargement des informations du compte...',
                                    'tr': 'Hesap bilgileri yükleniyor...'},
 'Loading catch-up channels...  •  BACK cancels': {'ar': 'جارٍ تحميل قنوات المشاهدة المؤجلة...  •  BACK للإلغاء',
                                                   'de': 'Catch-up-Kanäle werden geladen...  •  ZURÜCK bricht ab',
                                                   'fr': 'Chargement des chaînes Catch-up...  •  BACK annule',
                                                   'tr': 'Catch-up kanalları yükleniyor...  •  BACK iptal eder'},
 'Loading archive programmes...  •  BACK cancels': {'ar': 'جارٍ تحميل البرامج المؤرشفة...  •  BACK للإلغاء',
                                                    'de': 'Archivsendungen werden geladen...  •  ZURÜCK bricht ab',
                                                    'fr': 'Chargement des programmes archivés...  •  BACK annule',
                                                    'tr': 'Arşiv programları yükleniyor...  •  BACK iptal eder'},
 'Creating stream link...': {'ar': 'جارٍ إنشاء رابط البث...', 'de': 'Stream-Link wird erstellt...', 'fr': 'Création du lien de flux...', 'tr': 'Akış bağlantısı oluşturuluyor...'},
 'Creating catch-up stream...': {'ar': 'جارٍ إنشاء بث المشاهدة المؤجلة...',
                                 'de': 'Catch-up-Stream wird erstellt...',
                                 'fr': 'Création du flux Catch-up...',
                                 'tr': 'Catch-up akışı oluşturuluyor...'},
 'No stream command': {'ar': 'لا يوجد أمر تشغيل', 'de': 'Kein Stream-Befehl', 'fr': 'Aucune commande de flux', 'tr': 'Akış komutu yok'},
 'Portal returned an invalid stream link': {'ar': 'أعادت البوابة رابط بث غير صالح',
                                            'de': 'Portal hat einen ungültigen Stream-Link zurückgegeben',
                                            'fr': 'Le portail a renvoyé un lien de flux invalide',
                                            'tr': 'Portal geçersiz akış bağlantısı döndürdü'},
 'Player closed': {'ar': 'تم إغلاق المشغل', 'de': 'Player geschlossen', 'fr': 'Lecteur fermé', 'tr': 'Oynatıcı kapandı'},
 'Reauthorizing...': {'ar': 'جارٍ إعادة التوثيق...', 'de': 'Erneute Autorisierung...', 'fr': 'Nouvelle autorisation...', 'tr': 'Yeniden yetkilendiriliyor...'},
 'Authorized': {'ar': 'تم التوثيق', 'de': 'Autorisiert', 'fr': 'Autorisé', 'tr': 'Yetkilendirildi'},
 'No more items': {'ar': 'لا توجد عناصر أخرى', 'de': 'Keine weiteren Einträge', 'fr': 'Aucun autre élément', 'tr': 'Başka öğe yok'},
 'Folder is empty': {'ar': 'المجلد فارغ', 'de': 'Ordner ist leer', 'fr': 'Le dossier est vide', 'tr': 'Klasör boş'},
 'No matching content': {'ar': 'لا يوجد محتوى مطابق', 'de': 'Kein passender Inhalt', 'fr': 'Aucun contenu correspondant', 'tr': 'Eşleşen içerik yok'},
 'Search is available inside content lists': {'ar': 'البحث متاح داخل قوائم المحتوى',
                                              'de': 'Die Suche ist innerhalb von Inhaltslisten verfügbar',
                                              'fr': 'La recherche est disponible dans les listes de contenu',
                                              'tr': 'Arama içerik listelerinde kullanılabilir'},
 'Searching enabled portals...': {'ar': 'جارٍ البحث في البوابات المفعّلة...',
                                  'de': 'Aktivierte Portale werden durchsucht...',
                                  'fr': 'Recherche dans les portails activés...',
                                  'tr': 'Etkin portallarda aranıyor...'},
 'Searching current portal...': {'ar': 'جارٍ البحث في البوابة الحالية...',
                                 'de': 'Aktuelles Portal wird durchsucht...',
                                 'fr': 'Recherche dans le portail actuel...',
                                 'tr': 'Mevcut portalda aranıyor...'},
 'Results for: %s': {'ar': 'نتائج البحث عن: %s', 'de': 'Ergebnisse für: %s', 'fr': 'Résultats pour : %s', 'tr': 'Şunun için sonuçlar: %s'},
 'Added to favorites': {'ar': 'تمت الإضافة إلى المفضلة', 'de': 'Zu Favoriten hinzugefügt', 'fr': 'Ajouté aux favoris', 'tr': 'Favorilere eklendi'},
 'Removed from favorites': {'ar': 'تمت الإزالة من المفضلة', 'de': 'Aus Favoriten entfernt', 'fr': 'Retiré des favoris', 'tr': 'Favorilerden kaldırıldı'},
 'Marked watched': {'ar': 'تم التعليم كمُشاهد', 'de': 'Als gesehen markiert', 'fr': 'Marqué comme vu', 'tr': 'İzlendi olarak işaretlendi'},
 'Marked unwatched': {'ar': 'تم التعليم كغير مُشاهد', 'de': 'Als ungesehen markiert', 'fr': 'Marqué comme non vu', 'tr': 'İzlenmedi olarak işaretlendi'},
 'Nothing selected  •  OK Select  •  GREEN Import Selected  •  BACK Portal Manager': {'ar': 'لا يوجد تحديد  •  OK تحديد  •  الأخضر استيراد المحدد  •  BACK إدارة البوابات',
                                                                                      'de': 'Nichts ausgewählt  •  OK Auswählen  •  GRÜN Auswahl importieren  •  ZURÜCK '
                                                                                            'Portal-Manager',
                                                                                      'fr': 'Aucune sélection  •  OK Sélectionner  •  VERT Importer la sélection  •  BACK '
                                                                                            'Gestionnaire de portails',
                                                                                      'tr': 'Seçim yok  •  OK Seç  •  YEŞİL Seçilenleri İçe Aktar  •  BACK Portal Yöneticisi'},
 'Import complete  •  %d added  •  %d already existed  •  no server checks were run': {'ar': 'اكتمل الاستيراد  •  تمت إضافة %d  •  %d موجود بالفعل  •  لم يتم فحص السيرفرات',
                                                                                       'de': 'Import abgeschlossen  •  %d hinzugefügt  •  %d bereits vorhanden  •  keine '
                                                                                             'Serverprüfungen durchgeführt',
                                                                                       'fr': 'Import terminé  •  %d ajoutés  •  %d existaient déjà  •  aucun contrôle serveur '
                                                                                             'effectué',
                                                                                       'tr': 'İçe aktarma tamamlandı  •  %d eklendi  •  %d zaten vardı  •  sunucu kontrolü '
                                                                                             'yapılmadı'},
 'Import failed  •  BACK Portal Manager': {'ar': 'فشل الاستيراد  •  BACK إدارة البوابات',
                                           'de': 'Import fehlgeschlagen  •  ZURÜCK Portal-Manager',
                                           'fr': 'Échec de l’import  •  BACK Gestionnaire de portails',
                                           'tr': 'İçe aktarma başarısız  •  BACK Portal Yöneticisi'},
 'Import failed': {'ar': 'فشل الاستيراد', 'de': 'Import fehlgeschlagen', 'fr': 'Échec de l’import', 'tr': 'İçe aktarma başarısız'},
 'Library is empty': {'ar': 'المكتبة فارغة', 'de': 'Bibliothek ist leer', 'fr': 'La bibliothèque est vide', 'tr': 'Kütüphane boş'},
 'BACK Portal Manager': {'ar': 'BACK إدارة البوابات', 'de': 'ZURÜCK Portal-Manager', 'fr': 'BACK Gestionnaire de portails', 'tr': 'BACK Portal Yöneticisi'},
 'OK  Select   •   BACK  Cancel': {'ar': 'OK اختيار   •   BACK إلغاء',
                                   'de': 'OK  Auswählen   •   ZURÜCK  Abbrechen',
                                   'fr': 'OK Sélectionner   •   BACK Annuler',
                                   'tr': 'OK Seç   •   BACK İptal'},
 'OK  Save   •   BACK  Cancel': {'ar': 'OK حفظ   •   BACK إلغاء',
                                 'de': 'OK  Speichern   •   ZURÜCK  Abbrechen',
                                 'fr': 'OK Enregistrer   •   BACK Annuler',
                                 'tr': 'OK Kaydet   •   BACK İptal'},
 'OK  Select   •   BACK  Close   •   UP / DOWN  Navigate': {'ar': 'OK اختيار   •   BACK إغلاق   •   أعلى / أسفل للتنقل',
                                                            'de': 'OK  Auswählen   •   ZURÜCK  Schließen   •   HOCH / RUNTER  Navigieren',
                                                            'fr': 'OK Sélectionner   •   BACK Fermer   •   HAUT / BAS Naviguer',
                                                            'tr': 'OK Seç   •   BACK Kapat   •   YUKARI / AŞAĞI Gezin'},
 'OK / BACK  Close': {'ar': 'OK / BACK إغلاق', 'de': 'OK / ZURÜCK  Schließen', 'fr': 'OK / BACK Fermer', 'tr': 'OK / BACK Kapat'},
 'UP / DOWN  Scroll': {'ar': 'أعلى / أسفل للتمرير', 'de': 'HOCH / RUNTER  Scrollen', 'fr': 'HAUT / BAS  Faire défiler', 'tr': 'YUKARI / AŞAĞI  Kaydır'},
 'LEFT / RIGHT Navigate • DOWN Recent • OK Open • BACK Portals': {'ar': 'يسار / يمين للتنقل • أسفل للأحدث • OK فتح • BACK البوابات',
                                                                  'de': 'LINKS / RECHTS Navigieren • RUNTER Kürzlich • OK Öffnen • ZURÜCK Portale',
                                                                  'fr': 'GAUCHE / DROITE Naviguer • BAS Récent • OK Ouvrir • BACK Portails',
                                                                  'tr': 'SOL / SAĞ Gezin • AŞAĞI Son • OK Aç • BACK Portallar'},
 'Featured artwork is still warming on HDD': {'ar': 'جارٍ تجهيز الصورة المميزة على الهارد',
                                              'de': 'Empfohlene Bilder werden noch auf der HDD vorbereitet',
                                              'fr': 'L’illustration mise en avant est encore en préparation sur le disque',
                                              'tr': 'Öne çıkan görsel diskte hâlâ hazırlanıyor'},
 'Home Hero pinned • choose another from Movie/Series Details MENU': {'ar': 'تم تثبيت صورة الرئيسية • اختر غيرها من MENU داخل تفاصيل الفيلم/المسلسل',
                                                                      'de': 'Start-Hero angeheftet • wählen Sie über das MENÜ in den Film-/Seriendetails ein anderes',
                                                                      'fr': 'Visuel d’accueil épinglé • choisissez-en un autre via MENU dans les détails Film/Série',
                                                                      'tr': 'Ana Sayfa Hero sabitlendi • Film/Dizi Ayrıntıları MENU’den başka bir tane seçin'},
 'Nothing has been played in this section yet': {'ar': 'لم يتم تشغيل شيء في هذا القسم بعد',
                                                 'de': 'In diesem Bereich wurde noch nichts abgespielt',
                                                 'fr': 'Rien n’a encore été lu dans cette section',
                                                 'tr': 'Bu bölümde henüz hiçbir şey oynatılmadı'},
 'Saved item has no stream command': {'ar': 'العنصر المحفوظ لا يحتوي على أمر تشغيل',
                                      'de': 'Gespeicherter Eintrag hat keinen Stream-Befehl',
                                      'fr': 'L’élément enregistré ne contient aucune commande de flux',
                                      'tr': 'Kaydedilen öğede akış komutu yok'},
 'Open failed: %s': {'ar': 'فشل الفتح: %s', 'de': 'Öffnen fehlgeschlagen: %s', 'fr': 'Échec de l’ouverture : %s', 'tr': 'Açma başarısız: %s'},
 'Portal Manager • %s • %s • ARROWS Navigate • OK Select • BACK Portals%s': {'ar': 'إدارة البوابات • %s • %s • الأسهم للتنقل • OK اختيار • BACK البوابات%s',
                                                                             'de': 'Portal-Manager • %s • %s • PFEILE Navigieren • OK Auswählen • ZURÜCK Portale%s',
                                                                             'fr': 'Gestionnaire de portails • %s • %s • FLÈCHES Naviguer • OK Sélectionner • BACK Portails%s',
                                                                             'tr': 'Portal Yöneticisi • %s • %s • OKLAR Gezin • OK Seç • BACK Portallar%s'},
 'Profiles reloaded': {'ar': 'تمت إعادة تحميل الملفات الشخصية', 'de': 'Profile neu geladen', 'fr': 'Profils rechargés', 'tr': 'Profiller yeniden yüklendi'},
 'Checking source type…': {'ar': 'جارٍ تحديد نوع المصدر…', 'de': 'Quelltyp wird geprüft…', 'fr': 'Détection du type de source…', 'tr': 'Kaynak türü kontrol ediliyor…'},
 'M3U playlist added': {'ar': 'تمت إضافة قائمة M3U', 'de': 'M3U-Playlist hinzugefügt', 'fr': 'Playlist M3U ajoutée', 'tr': 'M3U oynatma listesi eklendi'},
 'Portal updated': {'ar': 'تم تحديث البوابة', 'de': 'Portal aktualisiert', 'fr': 'Portail mis à jour', 'tr': 'Portal güncellendi'},
 'Portal duplicated': {'ar': 'تم تكرار البوابة', 'de': 'Portal dupliziert', 'fr': 'Portail dupliqué', 'tr': 'Portal çoğaltıldı'},
 'Portal enabled': {'ar': 'تم تفعيل البوابة', 'de': 'Portal aktiviert', 'fr': 'Portail activé', 'tr': 'Portal etkinleştirildi'},
 'Portal permanently deleted': {'ar': 'تم حذف البوابة نهائيًا', 'de': 'Portal endgültig gelöscht', 'fr': 'Portail supprimé définitivement', 'tr': 'Portal kalıcı olarak silindi'},
 'Portal renamed': {'ar': 'تمت إعادة تسمية البوابة', 'de': 'Portal umbenannt', 'fr': 'Portail renommé', 'tr': 'Portal yeniden adlandırıldı'},
 'No Portal / M3U sources': {'ar': 'لا توجد مصادر Portal / M3U', 'de': 'Keine Portal-/M3U-Quellen', 'fr': 'Aucune source Portal / M3U', 'tr': 'Portal / M3U kaynağı yok'},
 'MENU Portal Manager  •  Add a Portal or M3U source to begin': {'ar': 'MENU إدارة البوابات  •  أضف مصدر Portal أو M3U للبدء',
                                                                 'de': 'MENÜ Portal-Manager  •  Fügen Sie zum Starten eine Portal- oder M3U-Quelle hinzu',
                                                                 'fr': 'MENU Gestionnaire de portails  •  Ajoutez une source Portal ou M3U pour commencer',
                                                                 'tr': 'MENU Portal Yöneticisi  •  Başlamak için Portal veya M3U kaynağı ekleyin'},
 'Testing TMDB connection...': {'ar': 'جارٍ اختبار اتصال TMDB...',
                                'de': 'TMDB-Verbindung wird getestet...',
                                'fr': 'Test de la connexion TMDB...',
                                'tr': 'TMDB bağlantısı test ediliyor...'},
 'TMDB ready': {'ar': 'TMDB جاهز', 'de': 'TMDB bereit', 'fr': 'TMDB prêt', 'tr': 'TMDB hazır'},
 'Parental PIN saved securely': {'ar': 'تم حفظ PIN الرقابة بأمان',
                                 'de': 'Kindersicherungs-PIN sicher gespeichert',
                                 'fr': 'PIN parental enregistré de façon sécurisée',
                                 'tr': 'Ebeveyn PIN’i güvenli şekilde kaydedildi'},
 'Parental session locked': {'ar': 'تم قفل جلسة الرقابة', 'de': 'Kindersicherungssitzung gesperrt', 'fr': 'Session parentale verrouillée', 'tr': 'Ebeveyn oturumu kilitlendi'},
 'Adult keywords saved': {'ar': 'تم حفظ كلمات محتوى البالغين',
                          'de': 'Erwachsenen-Schlüsselwörter gespeichert',
                          'fr': 'Mots-clés adulte enregistrés',
                          'tr': 'Yetişkin anahtar kelimeleri kaydedildi'},
 'Web Cleaner running on port 7725': {'ar': 'Web Cleaner يعمل على المنفذ 7725',
                                      'de': 'Web Cleaner läuft auf Port 7725',
                                      'fr': 'Web Cleaner fonctionne sur le port 7725',
                                      'tr': 'Web Cleaner 7725 portunda çalışıyor'},
 'Web Cleaner stopped': {'ar': 'تم إيقاف Web Cleaner', 'de': 'Web Cleaner gestoppt', 'fr': 'Web Cleaner arrêté', 'tr': 'Web Cleaner durduruldu'},
 '%s season': {'ar': '%s موسم', 'de': '%s Staffel', 'fr': '%s saison', 'tr': '%s sezon'},
 '%s seasons': {'ar': '%s موسم', 'de': '%s Staffeln', 'fr': '%s saisons', 'tr': '%s sezon'},
 'Check all portals': {'ar': 'فحص كل البوابات', 'de': 'Alle Portale prüfen', 'fr': 'Vérifier tous les portails', 'tr': 'Tüm portalları kontrol et'},
 'Re-enable disabled portal': {'ar': 'إعادة تفعيل بوابة معطلة',
                               'de': 'Deaktiviertes Portal wieder aktivieren',
                               'fr': 'Réactiver un portail désactivé',
                               'tr': 'Devre dışı portalı yeniden etkinleştir'},
 'Rename portal': {'ar': 'إعادة تسمية البوابة', 'de': 'Portal umbenennen', 'fr': 'Renommer le portail', 'tr': 'Portalı yeniden adlandır'},
 'Edit URL / MAC': {'ar': 'تعديل URL / MAC', 'de': 'URL / MAC bearbeiten', 'fr': 'Modifier URL / MAC', 'tr': 'URL / MAC düzenle'},
 'MAG device profile': {'ar': 'ملف جهاز MAG', 'de': 'MAG-Geräteprofil', 'fr': 'Profil appareil MAG', 'tr': 'MAG cihaz profili'},
 'Export Live bouquet + EPG': {'ar': 'تصدير باقة البث المباشر + EPG',
                               'de': 'Live-Bouquet + EPG exportieren',
                               'fr': 'Exporter bouquet Live + EPG',
                               'tr': 'Canlı buketi + EPG dışa aktar'},
 'Remove exported bouquet + EPG': {'ar': 'إزالة الباقة المصدرة + EPG',
                                   'de': 'Exportiertes Bouquet + EPG entfernen',
                                   'fr': 'Supprimer le bouquet exporté + EPG',
                                   'tr': 'Dışa aktarılan buket + EPG’yi kaldır'},
 'Duplicate with another MAC': {'ar': 'نسخ باستخدام MAC آخر', 'de': 'Mit anderer MAC duplizieren', 'fr': 'Dupliquer avec un autre MAC', 'tr': 'Başka MAC ile çoğalt'},
 'Move up': {'ar': 'تحريك لأعلى', 'de': 'Nach oben', 'fr': 'Monter', 'tr': 'Yukarı taşı'},
 'Move down': {'ar': 'تحريك لأسفل', 'de': 'Nach unten', 'fr': 'Descendre', 'tr': 'Aşağı taşı'},
 'Disable portal': {'ar': 'تعطيل البوابة', 'de': 'Portal deaktivieren', 'fr': 'Désactiver le portail', 'tr': 'Portalı devre dışı bırak'},
 'Clear portal data': {'ar': 'مسح بيانات البوابة', 'de': 'Portaldaten löschen', 'fr': 'Effacer les données du portail', 'tr': 'Portal verilerini temizle'},
 'Delete permanently': {'ar': 'حذف نهائي', 'de': 'Endgültig löschen', 'fr': 'Supprimer définitivement', 'tr': 'Kalıcı olarak sil'},
 'Select portals': {'ar': 'تحديد البوابات', 'de': 'Portale auswählen', 'fr': 'Sélectionner des portails', 'tr': 'Portalları seç'},
 'Browse the bundled Portal catalog and import selected entries': {'ar': 'تصفح مكتبة Portal المدمجة واستورد العناصر المحددة',
                                                                   'de': 'Mitgelieferten Portal-Katalog durchsuchen und ausgewählte Einträge importieren',
                                                                   'fr': 'Parcourir le catalogue Portal intégré et importer les éléments sélectionnés',
                                                                   'tr': 'Dahili Portal kataloğuna göz at ve seçilenleri içe aktar'},
 'Browse the bundled Xtream catalog and import selected entries': {'ar': 'تصفح مكتبة Xtream المدمجة واستورد العناصر المحددة',
                                                                   'de': 'Mitgelieferten Xtream-Katalog durchsuchen und ausgewählte Einträge importieren',
                                                                   'fr': 'Parcourir le catalogue Xtream intégré et importer les éléments sélectionnés',
                                                                   'tr': 'Dahili Xtream kataloğuna göz at ve seçilenleri içe aktar'},
 'Health check for every enabled portal': {'ar': 'فحص حالة كل بوابة مفعلة',
                                           'de': 'Statusprüfung für jedes aktivierte Portal',
                                           'fr': 'Contrôle de l’état de chaque portail activé',
                                           'tr': 'Etkin tüm portallar için durum kontrolü'},
 'Restore a disabled portal': {'ar': 'استعادة بوابة معطلة',
                               'de': 'Ein deaktiviertes Portal wiederherstellen',
                               'fr': 'Restaurer un portail désactivé',
                               'tr': 'Devre dışı portalı geri yükle'},
 'Rename the selected portal': {'ar': 'أعد تسمية البوابة المحددة',
                                'de': 'Das ausgewählte Portal umbenennen',
                                'fr': 'Renommer le portail sélectionné',
                                'tr': 'Seçili portalı yeniden adlandır'},
 'Edit portal address or MAC': {'ar': 'عدّل عنوان البوابة أو MAC',
                                'de': 'Portal-Adresse oder MAC bearbeiten',
                                'fr': 'Modifier l’adresse du portail ou le MAC',
                                'tr': 'Portal adresini veya MAC’i düzenle'},
 'Choose the MAG device profile': {'ar': 'اختر ملف جهاز MAG', 'de': 'MAG-Geräteprofil auswählen', 'fr': 'Choisir le profil de l’appareil MAG', 'tr': 'MAG cihaz profilini seç'},
 'Create Live bouquet and EPG source': {'ar': 'أنشئ باقة بث مباشر ومصدر EPG',
                                        'de': 'Live-Bouquet und EPG-Quelle erstellen',
                                        'fr': 'Créer un bouquet Live et une source EPG',
                                        'tr': 'Canlı buket ve EPG kaynağı oluştur'},
 'Remove the exported Live integration': {'ar': 'أزل تكامل البث المباشر المُصدّر',
                                          'de': 'Die exportierte Live-Integration entfernen',
                                          'fr': 'Supprimer l’intégration Live exportée',
                                          'tr': 'Dışa aktarılan Canlı entegrasyonunu kaldır'},
 'Clone this portal with another MAC': {'ar': 'انسخ هذه البوابة باستخدام MAC آخر',
                                        'de': 'Dieses Portal mit anderer MAC klonen',
                                        'fr': 'Cloner ce portail avec un autre MAC',
                                        'tr': 'Bu portalı başka MAC ile klonla'},
 'Move the selected portal up': {'ar': 'حرّك البوابة المحددة لأعلى',
                                 'de': 'Das ausgewählte Portal nach oben verschieben',
                                 'fr': 'Déplacer le portail sélectionné vers le haut',
                                 'tr': 'Seçili portalı yukarı taşı'},
 'Move the selected portal down': {'ar': 'حرّك البوابة المحددة لأسفل',
                                   'de': 'Das ausgewählte Portal nach unten verschieben',
                                   'fr': 'Déplacer le portail sélectionné vers le bas',
                                   'tr': 'Seçili portalı aşağı taşı'},
 'Disable the selected portal': {'ar': 'عطّل البوابة المحددة',
                                 'de': 'Das ausgewählte Portal deaktivieren',
                                 'fr': 'Désactiver le portail sélectionné',
                                 'tr': 'Seçili portalı devre dışı bırak'},
 'Clear plugin-owned data but keep portal': {'ar': 'امسح بيانات البلجن مع الاحتفاظ بالبوابة',
                                             'de': 'Plugin-eigene Daten löschen, aber Portal behalten',
                                             'fr': 'Effacer les données du plugin tout en conservant le portail',
                                             'tr': 'Portalı koruyup eklenti verilerini temizle'},
 'Delete this portal and related plugin data': {'ar': 'احذف هذه البوابة وبيانات البلجن المرتبطة',
                                                'de': 'Dieses Portal und zugehörige Plugin-Daten löschen',
                                                'fr': 'Supprimer ce portail et les données associées du plugin',
                                                'tr': 'Bu portalı ve ilgili eklenti verilerini sil'},
 'Open diagnostics for the selected portal': {'ar': 'افتح التشخيص للبوابة المحددة',
                                              'de': 'Diagnose für das ausgewählte Portal öffnen',
                                              'fr': 'Ouvrir les diagnostics du portail sélectionné',
                                              'tr': 'Seçili portal için tanılamayı aç'},
 'Select multiple portals for one permanent delete': {'ar': 'حدد عدة بوابات لحذفها نهائيًا دفعة واحدة',
                                                      'de': 'Mehrere Portale für eine einmalige endgültige Löschung auswählen',
                                                      'fr': 'Sélectionner plusieurs portails pour une suppression définitive groupée',
                                                      'tr': 'Tek seferde kalıcı silme için birden fazla portal seç'},
 'Choose a portal tool': {'ar': 'اختر أداة للبوابة', 'de': 'Portal-Werkzeug auswählen', 'fr': 'Choisissez un outil de portail', 'tr': 'Bir portal aracı seçin'},
 'No portal selected': {'ar': 'لا توجد بوابة محددة', 'de': 'Kein Portal ausgewählt', 'fr': 'Aucun portail sélectionné', 'tr': 'Portal seçilmedi'},
 'Portal Library': {'ar': 'مكتبة البوابات', 'de': 'Portal-Bibliothek', 'fr': 'Bibliothèque de portails', 'tr': 'Portal Kütüphanesi'},
 'Xtream Library': {'ar': 'مكتبة Xtream', 'de': 'Xtream-Bibliothek', 'fr': 'Bibliothèque Xtream', 'tr': 'Xtream Kütüphanesi'},
 'Nothing selected • OK Toggle • GREEN Import Selected • BACK Settings': {'ar': 'لا يوجد تحديد • OK تحديد/إلغاء • الأخضر استيراد المحدد • BACK الإعدادات',
                                                                          'de': 'Nichts ausgewählt • OK Umschalten • GRÜN Auswahl importieren • ZURÜCK Einstellungen',
                                                                          'fr': 'Aucune sélection • OK Basculer • VERT Importer la sélection • BACK Paramètres',
                                                                          'tr': 'Seçim yok • OK Değiştir • YEŞİL Seçilenleri İçe Aktar • BACK Ayarlar'},
 'Import complete • %d added • %d already existed • no server checks were run': {'ar': 'اكتمل الاستيراد • تمت إضافة %d • %d موجود بالفعل • لم يتم فحص السيرفرات',
                                                                                 'de': 'Import abgeschlossen • %d hinzugefügt • %d bereits vorhanden • keine Serverprüfungen '
                                                                                       'durchgeführt',
                                                                                 'fr': 'Import terminé • %d ajoutés • %d existaient déjà • aucun contrôle serveur effectué',
                                                                                 'tr': 'İçe aktarma tamamlandı • %d eklendi • %d zaten vardı • sunucu kontrolü yapılmadı'},
 '%s • Library is empty • BACK Settings': {'ar': '%s • المكتبة فارغة • BACK الإعدادات',
                                           'de': '%s • Bibliothek ist leer • ZURÜCK Einstellungen',
                                           'fr': '%s • bibliothèque vide • BACK Paramètres',
                                           'tr': '%s • kütüphane boş • BACK Ayarlar'},
 '%s • %d entries • %d selected • OK Toggle • GREEN Import • YELLOW Select all • RED Clear • BACK Settings': {'ar': '%s • %d عنصر • %d محدد • OK تحديد/إلغاء • الأخضر استيراد • '
                                                                                                                    'الأصفر تحديد الكل • الأحمر مسح • BACK الإعدادات',
                                                                                                              'de': '%s • %d Einträge • %d ausgewählt • OK Umschalten • GRÜN '
                                                                                                                    'Importieren • GELB Alle auswählen • ROT Leeren • ZURÜCK '
                                                                                                                    'Einstellungen',
                                                                                                              'fr': '%s • %d entrées • %d sélectionnées • OK Basculer • VERT '
                                                                                                                    'Importer • JAUNE Tout sélectionner • ROUGE Effacer • BACK '
                                                                                                                    'Paramètres',
                                                                                                              'tr': '%s • %d kayıt • %d seçili • OK Değiştir • YEŞİL İçe Aktar • '
                                                                                                                    'SARI Tümünü Seç • KIRMIZI Temizle • BACK Ayarlar'},
 'Play': {'ar': 'تشغيل', 'de': 'Abspielen', 'fr': 'Lire', 'tr': 'Oynat'},
 'Episodes': {'ar': 'الحلقات', 'de': 'Episoden', 'fr': 'Épisodes', 'tr': 'Bölümler'},
 'Resume': {'ar': 'استكمال', 'de': 'Fortsetzen', 'fr': 'Reprendre', 'tr': 'Devam Et'},
 'Favorite': {'ar': 'المفضلة', 'de': 'Favorit', 'fr': 'Favori', 'tr': 'Favori'},
 'Default': {'ar': 'افتراضي', 'de': 'Standard', 'fr': 'Par défaut', 'tr': 'Varsayılan'},
 'Previous page': {'ar': 'الصفحة السابقة', 'de': 'Vorherige Seite', 'fr': 'Page précédente', 'tr': 'Önceki sayfa'},
 'Next page': {'ar': 'الصفحة التالية', 'de': 'Nächste Seite', 'fr': 'Page suivante', 'tr': 'Sonraki sayfa'},
 'Page 1': {'ar': 'الصفحة 1', 'de': 'Seite 1', 'fr': 'Page 1', 'tr': 'Sayfa 1'},
 'TMDB rating': {'ar': 'تقييم TMDB', 'de': 'TMDB-Bewertung', 'fr': 'Note TMDB', 'tr': 'TMDB puanı'},
 'Metadata': {'ar': 'البيانات', 'de': 'Metadaten', 'fr': 'Métadonnées', 'tr': 'Meta veri'},
 'Provider rating': {'ar': 'تقييم المزود', 'de': 'Anbieter-Bewertung', 'fr': 'Note du fournisseur', 'tr': 'Sağlayıcı puanı'},
 'TMDb • portal metadata': {'ar': 'TMDb • بيانات البوابة', 'de': 'TMDb • Portal-Metadaten', 'fr': 'TMDb • métadonnées du portail', 'tr': 'TMDb • portal meta verisi'},
 'Server • TMDb enrichment': {'ar': 'السيرفر • إثراء TMDb', 'de': 'Server • TMDb-Anreicherung', 'fr': 'Serveur • enrichissement TMDb', 'tr': 'Sunucu • TMDb zenginleştirme'},
 'Server metadata loaded': {'ar': 'تم تحميل بيانات السيرفر', 'de': 'Server-Metadaten geladen', 'fr': 'Métadonnées du serveur chargées', 'tr': 'Sunucu meta verisi yüklendi'},
 'Open season': {'ar': 'فتح الموسم', 'de': 'Staffel öffnen', 'fr': 'Ouvrir la saison', 'tr': 'Sezonu aç'},
 'Play episode %s': {'ar': 'تشغيل الحلقة %s', 'de': 'Episode %s abspielen', 'fr': 'Lire l’épisode %s', 'tr': '%s. bölümü oynat'},
 'OK  Play episode': {'ar': 'OK تشغيل الحلقة', 'de': 'OK  Episode abspielen', 'fr': 'OK  Lire l’épisode', 'tr': 'OK  Bölümü oynat'},
 '%d seasons': {'ar': '%d موسم', 'de': '%d Staffeln', 'fr': '%d saisons', 'tr': '%d sezon'},
 '%d episodes': {'ar': '%d حلقة', 'de': '%d Episoden', 'fr': '%d épisodes', 'tr': '%d bölüm'},
 '%d episode': {'ar': '%d حلقة', 'de': '%d Episode', 'fr': '%d épisode', 'tr': '%d bölüm'},
 'Loading seasons...': {'ar': 'جارٍ تحميل المواسم...', 'de': 'Staffeln werden geladen...', 'fr': 'Chargement des saisons...', 'tr': 'Sezonlar yükleniyor...'},
 'Loading episodes...': {'ar': 'جارٍ تحميل الحلقات...', 'de': 'Episoden werden geladen...', 'fr': 'Chargement des épisodes...', 'tr': 'Bölümler yükleniyor...'},
 'Seasons failed: %s': {'ar': 'فشل تحميل المواسم: %s', 'de': 'Staffeln fehlgeschlagen: %s', 'fr': 'Échec du chargement des saisons : %s', 'tr': 'Sezonlar yüklenemedi: %s'},
 'Episodes failed: %s': {'ar': 'فشل تحميل الحلقات: %s', 'de': 'Episoden fehlgeschlagen: %s', 'fr': 'Échec du chargement des épisodes : %s', 'tr': 'Bölümler yüklenemedi: %s'},
 'Episode has no stream command': {'ar': 'الحلقة لا تحتوي على أمر تشغيل',
                                   'de': 'Episode hat keinen Stream-Befehl',
                                   'fr': 'L’épisode ne contient aucune commande de flux',
                                   'tr': 'Bölümde akış komutu yok'},
 'Creating episode stream...': {'ar': 'جارٍ إنشاء بث الحلقة...',
                                'de': 'Episoden-Stream wird erstellt...',
                                'fr': 'Création du flux de l’épisode...',
                                'tr': 'Bölüm akışı oluşturuluyor...'},
 'Portal returned an invalid episode link': {'ar': 'أعادت البوابة رابط حلقة غير صالح',
                                             'de': 'Portal hat einen ungültigen Episoden-Link zurückgegeben',
                                             'fr': 'Le portail a renvoyé un lien d’épisode invalide',
                                             'tr': 'Portal geçersiz bölüm bağlantısı döndürdü'},
 'Opening player: %s': {'ar': 'جارٍ فتح المشغل: %s', 'de': 'Player wird geöffnet: %s', 'fr': 'Ouverture du lecteur : %s', 'tr': 'Oynatıcı açılıyor: %s'},
 'Player closed  •  engine %s': {'ar': 'تم إغلاق المشغل  •  المحرك %s',
                                 'de': 'Player geschlossen  •  Engine %s',
                                 'fr': 'Lecteur fermé  •  moteur %s',
                                 'tr': 'Oynatıcı kapandı  •  motor %s'},
 'Player failed: %s': {'ar': 'فشل المشغل: %s', 'de': 'Player fehlgeschlagen: %s', 'fr': 'Échec du lecteur : %s', 'tr': 'Oynatıcı başarısız: %s'},
 'Play failed: %s': {'ar': 'فشل التشغيل: %s', 'de': 'Wiedergabe fehlgeschlagen: %s', 'fr': 'Échec de la lecture : %s', 'tr': 'Oynatma başarısız: %s'},
 'Open a season first': {'ar': 'افتح موسمًا أولًا', 'de': 'Öffnen Sie zuerst eine Staffel', 'fr': 'Ouvrez d’abord une saison', 'tr': 'Önce bir sezon açın'},
 'Series UI error: %s': {'ar': 'خطأ في واجهة المسلسلات: %s', 'de': 'Serien-UI-Fehler: %s', 'fr': 'Erreur d’interface Séries : %s', 'tr': 'Dizi arayüz hatası: %s'},
 'Preparing...': {'ar': 'جارٍ التجهيز...', 'de': 'Wird vorbereitet...', 'fr': 'Préparation...', 'tr': 'Hazırlanıyor...'},
 'Loading folder index...': {'ar': 'جارٍ تحميل فهرس المجلد...',
                             'de': 'Ordnerindex wird geladen...',
                             'fr': 'Chargement de l’index du dossier...',
                             'tr': 'Klasör dizini yükleniyor...'},
 'Artwork cache is already running': {'ar': 'تخزين الصور يعمل بالفعل',
                                      'de': 'Bild-Cache läuft bereits',
                                      'fr': 'La mise en cache des illustrations est déjà en cours',
                                      'tr': 'Görsel önbellekleme zaten çalışıyor'},
 'Preparing current folder artwork cache...': {'ar': 'جارٍ تجهيز كاش صور المجلد الحالي...',
                                               'de': 'Bild-Cache des aktuellen Ordners wird vorbereitet...',
                                               'fr': 'Préparation du cache d’illustrations du dossier actuel...',
                                               'tr': 'Mevcut klasörün görsel önbelleği hazırlanıyor...'},
 'Resuming %d missing titles...': {'ar': 'جارٍ استكمال %d عنوان ناقص...',
                                   'de': '%d fehlende Titel werden fortgesetzt...',
                                   'fr': 'Reprise de %d titres manquants...',
                                   'tr': '%d eksik başlık devam ettiriliyor...'},
 'Caching %d / %d': {'ar': 'جارٍ التخزين %d / %d', 'de': 'Wird zwischengespeichert %d / %d', 'fr': 'Mise en cache %d / %d', 'tr': 'Önbelleğe alınıyor %d / %d'},
 'Artwork ready • %d/%d': {'ar': 'الصور جاهزة • %d/%d', 'de': 'Bilder bereit • %d/%d', 'fr': 'Illustrations prêtes • %d/%d', 'tr': 'Görseller hazır • %d/%d'},
 'Loading page %d...': {'ar': 'جارٍ تحميل الصفحة %d...', 'de': 'Seite %d wird geladen...', 'fr': 'Chargement de la page %d...', 'tr': '%d. sayfa yükleniyor...'},
 'Page %d / %d  •  %s %d / %d': {'ar': 'الصفحة %d / %d  •  %s %d / %d',
                                 'de': 'Seite %d / %d  •  %s %d / %d',
                                 'fr': 'Page %d / %d  •  %s %d / %d',
                                 'tr': 'Sayfa %d / %d  •  %s %d / %d'},
 'NOW  %s': {'ar': 'الآن  %s', 'de': 'JETZT  %s', 'fr': 'MAINTENANT  %s', 'tr': 'ŞİMDİ  %s'},
 'NEXT  ': {'ar': 'التالي  ', 'de': 'DANACH  ', 'fr': 'SUIVANT  ', 'tr': 'SONRAKİ  '},
 '1 / 4\n\nSet up your first portal in a few steps.\nYou will enter the Portal URL and MAC address, then Ultra Stalker will test the connection before saving it.': {'ar': '1 / 4\n'
                                                                                                                                                                           '\n'
                                                                                                                                                                           'أعدّ '
                                                                                                                                                                           'بوابتك '
                                                                                                                                                                           'الأولى '
                                                                                                                                                                           'في '
                                                                                                                                                                           'خطوات '
                                                                                                                                                                           'بسيطة.\n'
                                                                                                                                                                           'ستدخل '
                                                                                                                                                                           'رابط '
                                                                                                                                                                           'البوابة '
                                                                                                                                                                           'وعنوان '
                                                                                                                                                                           'MAC، '
                                                                                                                                                                           'ثم '
                                                                                                                                                                           'سيختبر '
                                                                                                                                                                           'Ultra '
                                                                                                                                                                           'Stalker '
                                                                                                                                                                           'الاتصال '
                                                                                                                                                                           'قبل '
                                                                                                                                                                           'الحفظ.',
                                                                                                                                                                     'de': '1 / 4\n'
                                                                                                                                                                           '\n'
                                                                                                                                                                           'Richten '
                                                                                                                                                                           'Sie '
                                                                                                                                                                           'Ihr '
                                                                                                                                                                           'erstes '
                                                                                                                                                                           'Portal '
                                                                                                                                                                           'in '
                                                                                                                                                                           'wenigen '
                                                                                                                                                                           'Schritten '
                                                                                                                                                                           'ein.\n'
                                                                                                                                                                           'Sie '
                                                                                                                                                                           'geben '
                                                                                                                                                                           'die '
                                                                                                                                                                           'Portal-URL '
                                                                                                                                                                           'und '
                                                                                                                                                                           'die '
                                                                                                                                                                           'MAC-Adresse '
                                                                                                                                                                           'ein, '
                                                                                                                                                                           'danach '
                                                                                                                                                                           'testet '
                                                                                                                                                                           'Ultra '
                                                                                                                                                                           'Stalker '
                                                                                                                                                                           'die '
                                                                                                                                                                           'Verbindung, '
                                                                                                                                                                           'bevor '
                                                                                                                                                                           'sie '
                                                                                                                                                                           'gespeichert '
                                                                                                                                                                           'wird.',
                                                                                                                                                                     'fr': '1 / 4\n'
                                                                                                                                                                           '\n'
                                                                                                                                                                           'Configurez '
                                                                                                                                                                           'votre '
                                                                                                                                                                           'premier '
                                                                                                                                                                           'portail '
                                                                                                                                                                           'en '
                                                                                                                                                                           'quelques '
                                                                                                                                                                           'étapes.\n'
                                                                                                                                                                           'Vous '
                                                                                                                                                                           'saisirez '
                                                                                                                                                                           'l’URL '
                                                                                                                                                                           'du '
                                                                                                                                                                           'portail '
                                                                                                                                                                           'et '
                                                                                                                                                                           'l’adresse '
                                                                                                                                                                           'MAC, '
                                                                                                                                                                           'puis '
                                                                                                                                                                           'Ultra '
                                                                                                                                                                           'Stalker '
                                                                                                                                                                           'testera '
                                                                                                                                                                           'la '
                                                                                                                                                                           'connexion '
                                                                                                                                                                           'avant '
                                                                                                                                                                           'l’enregistrement.',
                                                                                                                                                                     'tr': '1 / 4\n'
                                                                                                                                                                           '\n'
                                                                                                                                                                           'İlk '
                                                                                                                                                                           'portalınızı '
                                                                                                                                                                           'birkaç '
                                                                                                                                                                           'adımda '
                                                                                                                                                                           'kurun.\n'
                                                                                                                                                                           'Portal '
                                                                                                                                                                           'URL’sini '
                                                                                                                                                                           've MAC '
                                                                                                                                                                           'adresini '
                                                                                                                                                                           'gireceksiniz; '
                                                                                                                                                                           'ardından '
                                                                                                                                                                           'Ultra '
                                                                                                                                                                           'Stalker '
                                                                                                                                                                           'kaydetmeden '
                                                                                                                                                                           'önce '
                                                                                                                                                                           'bağlantıyı '
                                                                                                                                                                           'test '
                                                                                                                                                                           'edecek.'},
 '2 / 4\n\nEnter the Portal URL. HTTPS is recommended whenever your provider supports it.': {'ar': '2 / 4\n\nأدخل رابط البوابة. يُفضّل استخدام HTTPS إذا كان مزود الخدمة يدعمه.',
                                                                                             'de': '2 / 4\n'
                                                                                                   '\n'
                                                                                                   'Geben Sie die Portal-URL ein. HTTPS wird empfohlen, sofern Ihr Anbieter es '
                                                                                                   'unterstützt.',
                                                                                             'fr': '2 / 4\n'
                                                                                                   '\n'
                                                                                                   'Saisissez l’URL du portail. HTTPS est recommandé lorsque votre fournisseur le '
                                                                                                   'prend en charge.',
                                                                                             'tr': '2 / 4\n'
                                                                                                   '\n'
                                                                                                   'Portal URL’sini girin. Sağlayıcınız destekliyorsa HTTPS kullanmanız önerilir.'},
 '3 / 4\n\nEnter the MAG/STB MAC address supplied for this subscription.\nExample: 00:1A:79:XX:XX:XX': {'ar': '3 / 4\n'
                                                                                                              '\n'
                                                                                                              'أدخل عنوان MAC الخاص بجهاز MAG/STB والمخصص لهذا الاشتراك.\n'
                                                                                                              'مثال: 00:1A:79:XX:XX:XX',
                                                                                                        'de': '3 / 4\n'
                                                                                                              '\n'
                                                                                                              'Geben Sie die für dieses Abonnement bereitgestellte '
                                                                                                              'MAG-/STB-MAC-Adresse ein.\n'
                                                                                                              'Beispiel: 00:1A:79:XX:XX:XX',
                                                                                                        'fr': '3 / 4\n'
                                                                                                              '\n'
                                                                                                              'Saisissez l’adresse MAC MAG/STB fournie pour cet abonnement.\n'
                                                                                                              'Exemple : 00:1A:79:XX:XX:XX',
                                                                                                        'tr': '3 / 4\n'
                                                                                                              '\n'
                                                                                                              'Bu abonelik için verilen MAG/STB MAC adresini girin.\n'
                                                                                                              'Örnek: 00:1A:79:XX:XX:XX'},
 '4 / 4\n\nTesting the M3U playlist…\nChecking network access and playlist content.': {'ar': '4 / 4\n\nجارٍ اختبار قائمة M3U…\nيتم فحص اتصال الشبكة ومحتوى القائمة.',
                                                                                       'de': '4 / 4\n'
                                                                                             '\n'
                                                                                             'Die M3U-Playlist wird getestet…\n'
                                                                                             'Netzwerkzugriff und Playlist-Inhalt werden geprüft.',
                                                                                       'fr': '4 / 4\n'
                                                                                             '\n'
                                                                                             'Test de la playlist M3U…\n'
                                                                                             'Vérification de l’accès réseau et du contenu de la playlist.',
                                                                                       'tr': '4 / 4\n'
                                                                                             '\n'
                                                                                             'M3U oynatma listesi test ediliyor…\n'
                                                                                             'Ağ erişimi ve liste içeriği kontrol ediliyor.'},
 '4 / 4\n\nTesting the portal connection…\nChecking network access, MAG authentication and account response.': {'ar': '4 / 4\n'
                                                                                                                      '\n'
                                                                                                                      'جارٍ اختبار اتصال البوابة…\n'
                                                                                                                      'يتم فحص الشبكة وتوثيق MAG واستجابة الحساب.',
                                                                                                                'de': '4 / 4\n'
                                                                                                                      '\n'
                                                                                                                      'Die Portal-Verbindung wird getestet…\n'
                                                                                                                      'Netzwerkzugriff, MAG-Authentifizierung und Kontoantwort '
                                                                                                                      'werden geprüft.',
                                                                                                                'fr': '4 / 4\n'
                                                                                                                      '\n'
                                                                                                                      'Test de la connexion au portail…\n'
                                                                                                                      'Vérification du réseau, de l’authentification MAG et de la '
                                                                                                                      'réponse du compte.',
                                                                                                                'tr': '4 / 4\n'
                                                                                                                      '\n'
                                                                                                                      'Portal bağlantısı test ediliyor…\n'
                                                                                                                      'Ağ erişimi, MAG kimlik doğrulaması ve hesap yanıtı kontrol '
                                                                                                                      'ediliyor.'},
 'Bouquet proxy port (1024-65535)': {'ar': 'منفذ بروكسي الباقات (1024-65535)',
                                     'de': 'Bouquet-Proxy-Port (1024–65535)',
                                     'fr': 'Port proxy des bouquets (1024-65535)',
                                     'tr': 'Buket proxy portu (1024-65535)'},
 'Connection test failed.\n\n%s\n\nPress GREEN to edit the Portal URL and try again, or BLUE to finish setup later from Portal Manager.': {'ar': 'فشل اختبار الاتصال.\n'
                                                                                                                                                 '\n'
                                                                                                                                                 '%s\n'
                                                                                                                                                 '\n'
                                                                                                                                                 'اضغط الأخضر لتعديل رابط البوابة '
                                                                                                                                                 'وإعادة المحاولة، أو الأزرق '
                                                                                                                                                 'لإكمال الإعداد لاحقًا من إدارة '
                                                                                                                                                 'البوابات.',
                                                                                                                                           'de': 'Verbindungstest fehlgeschlagen.\n'
                                                                                                                                                 '\n'
                                                                                                                                                 '%s\n'
                                                                                                                                                 '\n'
                                                                                                                                                 'Drücken Sie GRÜN, um die '
                                                                                                                                                 'Portal-URL zu bearbeiten und es '
                                                                                                                                                 'erneut zu versuchen, oder BLAU, '
                                                                                                                                                 'um die Einrichtung später im '
                                                                                                                                                 'Portal-Manager abzuschließen.',
                                                                                                                                           'fr': 'Échec du test de connexion.\n'
                                                                                                                                                 '\n'
                                                                                                                                                 '%s\n'
                                                                                                                                                 '\n'
                                                                                                                                                 'Appuyez sur VERT pour modifier '
                                                                                                                                                 'l’URL du portail et réessayer, '
                                                                                                                                                 'ou sur BLEU pour terminer plus '
                                                                                                                                                 'tard depuis le Gestionnaire de '
                                                                                                                                                 'portails.',
                                                                                                                                           'tr': 'Bağlantı testi başarısız.\n'
                                                                                                                                                 '\n'
                                                                                                                                                 '%s\n'
                                                                                                                                                 '\n'
                                                                                                                                                 'Portal URL’sini düzenleyip '
                                                                                                                                                 'tekrar denemek için YEŞİL’e, '
                                                                                                                                                 'kurulumu daha sonra Portal '
                                                                                                                                                 'Yöneticisi’nden tamamlamak için '
                                                                                                                                                 'MAVİ’ye basın.'},
 'Connection timed out. Check the portal address, internet connection, and server availability.': {'ar': 'انتهت مهلة الاتصال. تحقق من رابط البوابة والإنترنت وحالة الخادم.',
                                                                                                   'de': 'Zeitüberschreitung der Verbindung. Prüfen Sie die Portal-Adresse, die '
                                                                                                         'Internetverbindung und die Serververfügbarkeit.',
                                                                                                   'fr': 'Délai de connexion dépassé. Vérifiez l’adresse du portail, la connexion '
                                                                                                         'Internet et la disponibilité du serveur.',
                                                                                                   'tr': 'Bağlantı zaman aşımına uğradı. Portal adresini, internet bağlantısını ve '
                                                                                                         'sunucu durumunu kontrol edin.'},
 'Connection worked, but the profile could not be saved: %s': {'ar': 'نجح الاتصال لكن تعذر حفظ الملف الشخصي: %s',
                                                               'de': 'Die Verbindung hat funktioniert, das Profil konnte jedoch nicht gespeichert werden: %s',
                                                               'fr': 'La connexion a réussi, mais le profil n’a pas pu être enregistré : %s',
                                                               'tr': 'Bağlantı başarılı, ancak profil kaydedilemedi: %s'},
 'Enter a port number between 1024 and 65535.': {'ar': 'أدخل رقم منفذ بين 1024 و65535.',
                                                 'de': 'Geben Sie eine Portnummer zwischen 1024 und 65535 ein.',
                                                 'fr': 'Saisissez un numéro de port compris entre 1024 et 65535.',
                                                 'tr': '1024 ile 65535 arasında bir port numarası girin.'},
 'EPG refresh budget': {'ar': 'مهلة تحديث EPG', 'de': 'EPG-Aktualisierungsbudget', 'fr': 'Budget d’actualisation EPG', 'tr': 'EPG yenileme bütçesi'},
 'FILE ••••': {'ar': 'ملف ••••', 'de': 'DATEI ••••', 'fr': 'FICHIER ••••', 'tr': 'DOSYA ••••'},
 'Image cache limit': {'ar': 'حد كاش الصور', 'de': 'Bild-Cache-Limit', 'fr': 'Limite du cache d’images', 'tr': 'Görsel önbellek limiti'},
 'Invalid port': {'ar': 'منفذ غير صالح', 'de': 'Ungültiger Port', 'fr': 'Port invalide', 'tr': 'Geçersiz port'},
 'Maximum search scan pages': {'ar': 'أقصى عدد صفحات البحث', 'de': 'Maximal durchsuchte Seiten', 'fr': 'Nombre maximal de pages analysées', 'tr': 'Azami arama tarama sayfası'},
 'Search time budget per portal': {'ar': 'مهلة البحث لكل بوابة',
                                   'de': 'Zeitbudget für die Suche pro Portal',
                                   'fr': 'Budget temps de recherche par portail',
                                   'tr': 'Portal başına arama süre bütçesi'},
 'Searching': {'ar': 'جارٍ البحث', 'de': 'Suche läuft', 'fr': 'Recherche', 'tr': 'Aranıyor'},
 'The MAC address is invalid. Use a format such as 00:1A:79:XX:XX:XX.': {'ar': 'عنوان MAC غير صحيح. استخدم صيغة مثل 00:1A:79:XX:XX:XX.',
                                                                         'de': 'Die MAC-Adresse ist ungültig. Verwenden Sie ein Format wie 00:1A:79:XX:XX:XX.',
                                                                         'fr': 'L’adresse MAC est invalide. Utilisez un format tel que 00:1A:79:XX:XX:XX.',
                                                                         'tr': 'MAC adresi geçersiz. 00:1A:79:XX:XX:XX gibi bir biçim kullanın.'},
 'The portal hostname could not be resolved. Check DNS/network settings and the portal address.': {'ar': 'تعذر العثور على اسم خادم البوابة. تحقق من DNS والشبكة ورابط البوابة.',
                                                                                                   'de': 'Der Portal-Hostname konnte nicht aufgelöst werden. Prüfen Sie '
                                                                                                         'DNS-/Netzwerkeinstellungen und die Portal-Adresse.',
                                                                                                   'fr': 'Le nom d’hôte du portail n’a pas pu être résolu. Vérifiez le DNS, le '
                                                                                                         'réseau et l’adresse du portail.',
                                                                                                   'tr': 'Portal ana makine adı çözümlenemedi. DNS/ağ ayarlarını ve portal '
                                                                                                         'adresini kontrol edin.'},
 'The portal rejected authentication. Check the MAC address and whether the subscription is active.': {'ar': 'رفضت البوابة بيانات الدخول. تحقق من عنوان MAC ومن أن الاشتراك نشط.',
                                                                                                       'de': 'Das Portal hat die Authentifizierung abgelehnt. Prüfen Sie die '
                                                                                                             'MAC-Adresse und ob das Abonnement aktiv ist.',
                                                                                                       'fr': 'Le portail a refusé l’authentification. Vérifiez l’adresse MAC et '
                                                                                                             'que l’abonnement est actif.',
                                                                                                       'tr': 'Portal kimlik doğrulamayı reddetti. MAC adresini ve aboneliğin etkin '
                                                                                                             'olup olmadığını kontrol edin.'},
 'The portal server could not be reached. Check the address, network, and whether the server is online.': {'ar': 'تعذر الوصول إلى خادم البوابة. تحقق من الرابط والشبكة وأن الخادم '
                                                                                                                 'يعمل.',
                                                                                                           'de': 'Der Portal-Server konnte nicht erreicht werden. Prüfen Sie die '
                                                                                                                 'Adresse, das Netzwerk und ob der Server online ist.',
                                                                                                           'fr': 'Le serveur du portail est inaccessible. Vérifiez l’adresse, le '
                                                                                                                 'réseau et que le serveur est en ligne.',
                                                                                                           'tr': 'Portal sunucusuna ulaşılamadı. Adresi, ağı ve sunucunun '
                                                                                                                 'çevrimiçi olup olmadığını kontrol edin.'},
 'TLS certificate validation failed. Verify the portal URL or adjust TLS mode later from Portal Manager.': {'ar': 'فشل التحقق من شهادة TLS. تحقق من رابط البوابة أو عدّل وضع TLS '
                                                                                                                  'لاحقًا من إدارة البوابات.',
                                                                                                            'de': 'TLS-Zertifikatsprüfung fehlgeschlagen. Überprüfen Sie die '
                                                                                                                  'Portal-URL oder passen Sie den TLS-Modus später im '
                                                                                                                  'Portal-Manager an.',
                                                                                                            'fr': 'Échec de validation du certificat TLS. Vérifiez l’URL du '
                                                                                                                  'portail ou ajustez le mode TLS plus tard dans le Gestionnaire '
                                                                                                                  'de portails.',
                                                                                                            'tr': 'TLS sertifikası doğrulanamadı. Portal URL’sini kontrol edin '
                                                                                                                  'veya TLS modunu daha sonra Portal Yöneticisi’nden ayarlayın.'},
 'Ultra Stalker': {'ar': 'Ultra Stalker', 'de': 'Ultra Stalker', 'fr': 'Ultra Stalker', 'tr': 'Ultra Stalker'},
 'Ultra Stalker failed to start:\n\n%s': {'ar': 'فشل تشغيل Ultra Stalker:\n\n%s',
                                          'de': 'Ultra Stalker konnte nicht gestartet werden:\n\n%s',
                                          'fr': 'Échec du démarrage d’Ultra Stalker :\n\n%s',
                                          'tr': 'Ultra Stalker başlatılamadı:\n\n%s'},
 'Ultra Stalker requires Pillow for artwork and Adaptive UI.\n\nInstall the required runtime capability, then reopen the plugin.': {'ar': 'يتطلب Ultra Stalker مكتبة Pillow للصور '
                                                                                                                                          'والواجهة التكيفية.\n'
                                                                                                                                          '\n'
                                                                                                                                          'ثبّت المتطلب اللازم ثم أعد فتح الإضافة.',
                                                                                                                                    'de': 'Ultra Stalker benötigt Pillow für '
                                                                                                                                          'Bilder und die adaptive Oberfläche.\n'
                                                                                                                                          '\n'
                                                                                                                                          'Installieren Sie die erforderliche '
                                                                                                                                          'Laufzeitkomponente und öffnen Sie das '
                                                                                                                                          'Plugin dann erneut.',
                                                                                                                                    'fr': 'Ultra Stalker nécessite Pillow pour les '
                                                                                                                                          'illustrations et l’interface '
                                                                                                                                          'adaptative.\n'
                                                                                                                                          '\n'
                                                                                                                                          'Installez le composant requis, puis '
                                                                                                                                          'rouvrez le plugin.',
                                                                                                                                    'tr': 'Ultra Stalker görseller ve '
                                                                                                                                          'Uyarlanabilir Arayüz için Pillow '
                                                                                                                                          'gerektirir.\n'
                                                                                                                                          '\n'
                                                                                                                                          'Gerekli çalışma zamanı bileşenini kurup '
                                                                                                                                          'eklentiyi yeniden açın.'},
 'Version: %s\nBuild: %s\nPython support: 3.12 / 3.13 / 3.14 / 3.15\nInterface: Full HD • Adaptive 3D Glass UI': {'ar': 'الإصدار: %s\n'
                                                                                                                 'البنية: %s\n'
                                                                                                                 'دعم Python: 3.12 / 3.13 / 3.14 / 3.15\n'
                                                                                                                 'الواجهة: Full HD • Adaptive 3D Glass UI',
                                                                                                           'de': 'Version: %s\n'
                                                                                                                 'Build: %s\n'
                                                                                                                 'Python-Unterstützung: 3.12 / 3.13 / 3.14 / 3.15\n'
                                                                                                                 'Oberfläche: Full HD • Adaptive 3D-Glass-UI',
                                                                                                           'fr': 'Version : %s\n'
                                                                                                                 'Build : %s\n'
                                                                                                                 'Python pris en charge : 3.12 / 3.13 / 3.14 / 3.15\n'
                                                                                                                 'Interface : Full HD • Adaptive 3D Glass UI',
                                                                                                           'tr': 'Sürüm: %s\n'
                                                                                                                 'Yapı: %s\n'
                                                                                                                 'Python desteği: 3.12 / 3.13 / 3.14 / 3.15\n'
                                                                                                                 'Arayüz: Full HD • Adaptive 3D Glass UI'},
 '%d season': {'ar': '%d موسم', 'de': '%d Staffel', 'fr': '%d saison', 'tr': '%d sezon'},
 'NOW / NEXT': {'ar': 'الآن / التالي', 'de': 'JETZT / DANACH', 'fr': 'MAINTENANT / SUIVANT', 'tr': 'ŞİMDİ / SONRAKİ'},
 'AUTO': {'ar': 'تلقائي', 'de': 'AUTO', 'fr': 'AUTO', 'tr': 'OTOMATİK'},
 'LIVE': {'ar': 'مباشر', 'de': 'LIVE', 'fr': 'DIRECT', 'tr': 'CANLI'},
 'CHANNEL': {'ar': 'القناة', 'de': 'KANAL', 'fr': 'CHAÎNE', 'tr': 'KANAL'},
 'OK Preview': {'ar': 'OK معاينة', 'de': 'OK Vorschau', 'fr': 'OK Aperçu', 'tr': 'OK Önizleme'},
 'Opening channel...': {'ar': 'جارٍ فتح القناة...', 'de': 'Kanal wird geöffnet...', 'fr': 'Ouverture de la chaîne...', 'tr': 'Kanal açılıyor...'},
 'Opening...': {'ar': 'جارٍ الفتح...', 'de': 'Wird geöffnet...', 'fr': 'Ouverture...', 'tr': 'Açılıyor...'},
 'Loading preview...': {'ar': 'جارٍ تحميل المعاينة...', 'de': 'Vorschau wird geladen...', 'fr': 'Chargement de l’aperçu...', 'tr': 'Önizleme yükleniyor...'},
 'Preview unavailable': {'ar': 'المعاينة غير متاحة', 'de': 'Vorschau nicht verfügbar', 'fr': 'Aperçu indisponible', 'tr': 'Önizleme kullanılamıyor'},
 'Starting preview...': {'ar': 'جارٍ بدء المعاينة...', 'de': 'Vorschau wird gestartet...', 'fr': 'Démarrage de l’aperçu...', 'tr': 'Önizleme başlatılıyor...'},
 'Channel unavailable': {'ar': 'القناة غير متاحة', 'de': 'Kanal nicht verfügbar', 'fr': 'Chaîne indisponible', 'tr': 'Kanal kullanılamıyor'},
 'Preparing recording timer...': {'ar': 'جارٍ تجهيز مؤقت التسجيل...',
                                  'de': 'Aufnahme-Timer wird vorbereitet...',
                                  'fr': 'Préparation de la programmation d’enregistrement...',
                                  'tr': 'Kayıt zamanlayıcısı hazırlanıyor...'},
 'MENU: tools  •  OK: open / play': {'ar': 'MENU: أدوات  •  OK: فتح / تشغيل',
                                     'de': 'MENÜ: Werkzeuge  •  OK: öffnen / abspielen',
                                     'fr': 'MENU : outils  •  OK : ouvrir / lire',
                                     'tr': 'MENU: araçlar  •  OK: aç / oynat'},
 'MENU: new search  •  BLUE: restore list': {'ar': 'MENU: بحث جديد  •  الأزرق: استعادة القائمة',
                                             'de': 'MENÜ: neue Suche  •  BLAU: Liste wiederherstellen',
                                             'fr': 'MENU : nouvelle recherche  •  BLEU : restaurer la liste',
                                             'tr': 'MENU: yeni arama  •  MAVİ: listeyi geri yükle'},
 'Continue Watching cleared': {'ar': 'تم مسح متابعة المشاهدة', 'de': '„Weiterschauen“ geleert', 'fr': 'Continuer à regarder a été vidé', 'tr': 'İzlemeye Devam listesi temizlendi'},
 'EPG is available for live channels': {'ar': 'دليل EPG متاح للقنوات المباشرة',
                                        'de': 'EPG ist für Live-Kanäle verfügbar',
                                        'fr': 'L’EPG est disponible pour les chaînes en direct',
                                        'tr': 'EPG canlı kanallar için kullanılabilir'},
 'Channel has no EPG identifier': {'ar': 'لا يوجد معرّف EPG لهذه القناة',
                                   'de': 'Kanal hat keine EPG-Kennung',
                                   'fr': 'Cette chaîne n’a pas d’identifiant EPG',
                                   'tr': 'Kanalın EPG kimliği yok'},
 'Choose a programme': {'ar': 'اختر برنامجًا', 'de': 'Sendung auswählen', 'fr': 'Choisissez un programme', 'tr': 'Bir program seçin'},
 'Theme saved. Restart Enigma2 to apply': {'ar': 'تم حفظ المظهر. أعد تشغيل Enigma2 لتطبيقه',
                                           'de': 'Design gespeichert. Starten Sie Enigma2 neu, um es anzuwenden',
                                           'fr': 'Thème enregistré. Redémarrez Enigma2 pour l’appliquer',
                                           'tr': 'Tema kaydedildi. Uygulamak için Enigma2’yi yeniden başlatın'},
 'LEFT / RIGHT browse  •  OK open / play': {'ar': 'LEFT / RIGHT للتصفح  •  OK فتح / تشغيل',
                                            'de': 'LINKS / RECHTS Durchsuchen  •  OK öffnen / abspielen',
                                            'fr': 'GAUCHE / DROITE parcourir  •  OK ouvrir / lire',
                                            'tr': 'SOL / SAĞ gözat  •  OK aç / oynat'},
 'Picon cache is already running': {'ar': 'تخزين Picon يعمل بالفعل',
                                    'de': 'Picon-Cache läuft bereits',
                                    'fr': 'La mise en cache des picons est déjà en cours',
                                    'tr': 'Picon önbellekleme zaten çalışıyor'},
 'Scanning...': {'ar': 'جارٍ الفحص...', 'de': 'Wird durchsucht...', 'fr': 'Analyse...', 'tr': 'Taranıyor...'},
 'Scanning Live categories...': {'ar': 'جارٍ فحص أقسام البث المباشر...',
                                 'de': 'Live-Kategorien werden durchsucht...',
                                 'fr': 'Analyse des catégories Live...',
                                 'tr': 'Canlı kategoriler taranıyor...'},
 'Open a category list first': {'ar': 'افتح قائمة أقسام أولًا',
                                'de': 'Öffnen Sie zuerst eine Kategorieliste',
                                'fr': 'Ouvrez d’abord une liste de catégories',
                                'tr': 'Önce bir kategori listesi açın'},
 'Section visibility updated': {'ar': 'تم تحديث ظهور القسم',
                                'de': 'Sichtbarkeit des Bereichs aktualisiert',
                                'fr': 'Visibilité de la section mise à jour',
                                'tr': 'Bölüm görünürlüğü güncellendi'},
 'Open a category first': {'ar': 'افتح قسمًا أولًا', 'de': 'Öffnen Sie zuerst eine Kategorie', 'fr': 'Ouvrez d’abord une catégorie', 'tr': 'Önce bir kategori açın'},
 'This category cannot be hidden': {'ar': 'لا يمكن إخفاء هذا القسم',
                                    'de': 'Diese Kategorie kann nicht ausgeblendet werden',
                                    'fr': 'Cette catégorie ne peut pas être masquée',
                                    'tr': 'Bu kategori gizlenemez'},
 'Catch-up TV  /  Channels': {'ar': 'Catch-up TV  /  القنوات', 'de': 'Catch-up-TV  /  Kanäle', 'fr': 'Catch-up TV  /  Chaînes', 'tr': 'Catch-up TV  /  Kanallar'},
 'Channel has no archive identifier': {'ar': 'لا يوجد معرّف أرشيف لهذه القناة',
                                       'de': 'Kanal hat keine Archivkennung',
                                       'fr': 'Cette chaîne n’a pas d’identifiant d’archive',
                                       'tr': 'Kanalın arşiv kimliği yok'},
 'Opening Ultra Stalker player...': {'ar': 'جارٍ فتح مشغل Ultra Stalker...',
                                     'de': 'Ultra-Stalker-Player wird geöffnet...',
                                     'fr': 'Ouverture du lecteur Ultra Stalker...',
                                     'tr': 'Ultra Stalker oynatıcı açılıyor...'},
 'Portal returned an invalid catch-up link': {'ar': 'أعاد الـPortal رابط Catch-up غير صالح',
                                              'de': 'Portal hat einen ungültigen Catch-up-Link zurückgegeben',
                                              'fr': 'Le portail a renvoyé un lien Catch-up invalide',
                                              'tr': 'Portal geçersiz bir Catch-up bağlantısı döndürdü'},
 'Category folder': {'ar': 'مجلد القسم', 'de': 'Kategorieordner', 'fr': 'Dossier de catégorie', 'tr': 'Kategori klasörü'},
 'Artwork + metadata cache cleaned': {'ar': 'تم تنظيف كاش الصور والبيانات الوصفية',
                                      'de': 'Bild- + Metadaten-Cache bereinigt',
                                      'fr': 'Cache des illustrations et métadonnées nettoyé',
                                      'tr': 'Görsel ve meta veri önbelleği temizlendi'},
 'Reloading external artwork...': {'ar': 'جارٍ إعادة تحميل الصور الخارجية...',
                                   'de': 'Externe Bilder werden neu geladen...',
                                   'fr': 'Rechargement des illustrations externes...',
                                   'tr': 'Harici görseller yeniden yükleniyor...'},
 'TMDb match unavailable': {'ar': 'لا توجد مطابقة TMDb', 'de': 'Kein TMDb-Treffer verfügbar', 'fr': 'Correspondance TMDb indisponible', 'tr': 'TMDb eşleşmesi kullanılamıyor'},
 'Portal metadata • external match ignored': {'ar': 'بيانات الـPortal • تم تجاهل المطابقة الخارجية',
                                              'de': 'Portal-Metadaten • externer Treffer ignoriert',
                                              'fr': 'Métadonnées du portail • correspondance externe ignorée',
                                              'tr': 'Portal meta verisi • harici eşleşme yok sayıldı'},
 'Home Hero not set • backdrop is not ready yet': {'ar': 'لم يتم تثبيت Home Hero • الـBackdrop غير جاهز بعد',
                                                   'de': 'Start-Hero nicht gesetzt • Hintergrundbild ist noch nicht bereit',
                                                   'fr': 'Home Hero non défini • le backdrop n’est pas encore prêt',
                                                   'tr': 'Home Hero ayarlanmadı • backdrop henüz hazır değil'},
 'Home Hero pinned • stays fixed until you choose another': {'ar': 'تم تثبيت Home Hero • سيظل ثابتًا حتى تختار غيره',
                                                             'de': 'Start-Hero angeheftet • bleibt fest, bis Sie ein anderes wählen',
                                                             'fr': 'Home Hero épinglé • reste fixe jusqu’à votre prochain choix',
                                                             'tr': 'Home Hero sabitlendi • başka birini seçene kadar değişmez'},
 'Cached • HDD': {'ar': 'محفوظ • HDD', 'de': 'Zwischengespeichert • HDD', 'fr': 'En cache • HDD', 'tr': 'Önbellekte • HDD'},
 'Home Hero not set • backdrop could not be prepared': {'ar': 'لم يتم تثبيت Home Hero • تعذر تجهيز الـBackdrop',
                                                        'de': 'Start-Hero nicht gesetzt • Hintergrundbild konnte nicht vorbereitet werden',
                                                        'fr': 'Home Hero non défini • impossible de préparer le backdrop',
                                                        'tr': 'Home Hero ayarlanmadı • backdrop hazırlanamadı'},
 'Backdrop ready • HDD': {'ar': 'الـBackdrop جاهز • HDD', 'de': 'Hintergrundbild bereit • HDD', 'fr': 'Backdrop prêt • HDD', 'tr': 'Backdrop hazır • HDD'},
 'Support bundle': {'ar': 'حزمة الدعم', 'de': 'Support-Paket', 'fr': 'Paquet de support', 'tr': 'Destek paketi'},
 'Live TV • reopen channel': {'ar': 'البث المباشر • إعادة فتح القناة',
                              'de': 'Live-TV • Kanal erneut öffnen',
                              'fr': 'TV en direct • rouvrir la chaîne',
                              'tr': 'Canlı TV • kanalı yeniden aç'},
 'Exporting full Live bouquet...': {'ar': 'جارٍ تصدير باقة البث المباشر كاملة...',
                                    'de': 'Vollständiges Live-Bouquet wird exportiert...',
                                    'fr': 'Export du bouquet Live complet...',
                                    'tr': 'Tam Canlı buketi dışa aktarılıyor...'},
 'TLS security preference saved': {'ar': 'تم حفظ إعداد أمان TLS',
                                   'de': 'TLS-Sicherheitseinstellung gespeichert',
                                   'fr': 'Préférence de sécurité TLS enregistrée',
                                   'tr': 'TLS güvenlik tercihi kaydedildi'},
 'HTTP fallback disabled by hardened build': {'ar': 'تم تعطيل الرجوع إلى HTTP في هذه البنية المحمية',
                                              'de': 'HTTP-Fallback durch gehärteten Build deaktiviert',
                                              'fr': 'Repli HTTP désactivé par cette build renforcée',
                                              'tr': 'HTTP geri dönüşü güçlendirilmiş yapıda devre dışı'},
 'HTTP portal update cancelled': {'ar': 'تم إلغاء تحديث Portal عبر HTTP',
                                  'de': 'HTTP-Portal-Aktualisierung abgebrochen',
                                  'fr': 'Mise à jour du portail HTTP annulée',
                                  'tr': 'HTTP portal güncellemesi iptal edildi'},
 'M3U source updated': {'ar': 'تم تحديث مصدر M3U', 'de': 'M3U-Quelle aktualisiert', 'fr': 'Source M3U mise à jour', 'tr': 'M3U kaynağı güncellendi'},
 'HTTP source was not added': {'ar': 'لم تتم إضافة مصدر HTTP',
                               'de': 'HTTP-Quelle wurde nicht hinzugefügt',
                               'fr': 'La source HTTP n’a pas été ajoutée',
                               'tr': 'HTTP kaynağı eklenmedi'},
 'Enter MAC for Stalker portal': {'ar': 'أدخل MAC لـStalker Portal',
                                  'de': 'MAC für Stalker-Portal eingeben',
                                  'fr': 'Saisissez le MAC du portail Stalker',
                                  'tr': 'Stalker portalı için MAC girin'},
 'HTTP portal was not opened': {'ar': 'لم يتم فتح Portal عبر HTTP',
                                'de': 'HTTP-Portal wurde nicht geöffnet',
                                'fr': 'Le portail HTTP n’a pas été ouvert',
                                'tr': 'HTTP portalı açılmadı'},
 'Opening M3U playlist…': {'ar': 'جارٍ فتح قائمة M3U…', 'de': 'M3U-Playlist wird geöffnet…', 'fr': 'Ouverture de la playlist M3U…', 'tr': 'M3U oynatma listesi açılıyor…'},
 'Bouquet export failed: dynamic proxy is not running': {'ar': 'فشل تصدير الباقة: البروكسي الديناميكي لا يعمل',
                                                         'de': 'Bouquet-Export fehlgeschlagen: Der dynamische Proxy läuft nicht',
                                                         'fr': 'Échec de l’export du bouquet : le proxy dynamique n’est pas actif',
                                                         'tr': 'Buket dışa aktarılamadı: dinamik proxy çalışmıyor'},
 'Portal data cleared; portal profile kept': {'ar': 'تم مسح بيانات الـPortal مع الاحتفاظ بملفه',
                                              'de': 'Portaldaten gelöscht; Portal-Profil beibehalten',
                                              'fr': 'Données du portail effacées ; profil conservé',
                                              'tr': 'Portal verisi temizlendi; portal profili korundu'},
 'Stalker portal detected': {'ar': 'تم اكتشاف Stalker Portal', 'de': 'Stalker-Portal erkannt', 'fr': 'Portail Stalker détecté', 'tr': 'Stalker portalı algılandı'},
 'TLS verification is locked to strict mode in this hardened build': {'ar': 'التحقق من TLS مثبت على الوضع الصارم في هذه البنية المحمية',
                                                                      'de': 'Die TLS-Prüfung ist in diesem gehärteten Build auf den strengen Modus festgelegt',
                                                                      'fr': 'La vérification TLS est verrouillée en mode strict dans cette build renforcée',
                                                                      'tr': 'TLS doğrulaması bu güçlendirilmiş yapıda katı moda kilitlidir'},
 'HTTP fallback is disabled in this hardened build': {'ar': 'الرجوع إلى HTTP معطل في هذه البنية المحمية',
                                                      'de': 'Der HTTP-Fallback ist in diesem gehärteten Build deaktiviert',
                                                      'fr': 'Le repli HTTP est désactivé dans cette build renforcée',
                                                      'tr': 'HTTP geri dönüşü bu güçlendirilmiş yapıda devre dışıdır'},
 'Exported bouquet and EPG removed': {'ar': 'تم حذف الباقة وEPG المصدّرين',
                                      'de': 'Exportiertes Bouquet und EPG entfernt',
                                      'fr': 'Bouquet et EPG exportés supprimés',
                                      'tr': 'Dışa aktarılan buket ve EPG kaldırıldı'},
 'Current Artwork • opening current source catalog...': {'ar': 'الصور الحالية • جارٍ فتح كتالوج المصدر الحالي...',
                                                         'de': 'Aktuelle Bilder • Katalog der aktuellen Quelle wird geöffnet...',
                                                         'fr': 'Illustrations actuelles • ouverture du catalogue de la source...',
                                                         'tr': 'Mevcut Görseller • geçerli kaynak kataloğu açılıyor...'},
 'Creating backup…': {'ar': 'جارٍ إنشاء النسخة الاحتياطية…', 'de': 'Sicherung wird erstellt…', 'fr': 'Création de la sauvegarde…', 'tr': 'Yedek oluşturuluyor…'},
 'Validating and restoring backup…': {'ar': 'جارٍ التحقق من النسخة الاحتياطية واستعادتها…',
                                      'de': 'Sicherung wird geprüft und wiederhergestellt…',
                                      'fr': 'Validation et restauration de la sauvegarde…',
                                      'tr': 'Yedek doğrulanıyor ve geri yükleniyor…'},
 'Current Artwork preload is already running': {'ar': 'تحميل الصور الحالية مسبقًا يعمل بالفعل',
                                                'de': 'Vorladen der aktuellen Bilder läuft bereits',
                                                'fr': 'Le préchargement des illustrations actuelles est déjà en cours',
                                                'tr': 'Mevcut görsel ön yükleme zaten çalışıyor'},
 'Current Artwork preload cancelled': {'ar': 'تم إلغاء التحميل المسبق للصور الحالية',
                                       'de': 'Vorladen der aktuellen Bilder abgebrochen',
                                       'fr': 'Préchargement des illustrations actuelles annulé',
                                       'tr': 'Mevcut görsel ön yükleme iptal edildi'},
 'Artwork + metadata cache cleared': {'ar': 'تم مسح كاش الصور والبيانات الوصفية',
                                      'de': 'Bild- + Metadaten-Cache geleert',
                                      'fr': 'Cache des illustrations et métadonnées vidé',
                                      'tr': 'Görsel ve meta veri önbelleği temizlendi'},
 'Series information will appear here': {'ar': 'ستظهر معلومات المسلسل هنا',
                                         'de': 'Serieninformationen erscheinen hier',
                                         'fr': 'Les informations de la série apparaîtront ici',
                                         'tr': 'Dizi bilgileri burada görünecek'},
 'EPISODES  %s': {'ar': 'الحلقات  %s', 'de': 'EPISODEN  %s', 'fr': 'ÉPISODES  %s', 'tr': 'BÖLÜMLER  %s'},
 'YEAR  %s': {'ar': 'السنة  %s', 'de': 'JAHR  %s', 'fr': 'ANNÉE  %s', 'tr': 'YIL  %s'},
 'Season %s': {'ar': 'الموسم %s', 'de': 'Staffel %s', 'fr': 'Saison %s', 'tr': 'Sezon %s'},
 '%d EPISODES': {'ar': '%d حلقة', 'de': '%d EPISODEN', 'fr': '%d ÉPISODES', 'tr': '%d BÖLÜM'},
 'Ready to browse': {'ar': 'جاهز للتصفح', 'de': 'Bereit zum Durchsuchen', 'fr': 'Prêt à parcourir', 'tr': 'Gözatmaya hazır'},
 'SELECTED': {'ar': 'محدد', 'de': 'AUSGEWÄHLT', 'fr': 'SÉLECTIONNÉ', 'tr': 'SEÇİLİ'},
 'No seasons returned': {'ar': 'لم يتم العثور على مواسم', 'de': 'Keine Staffeln zurückgegeben', 'fr': 'Aucune saison renvoyée', 'tr': 'Sezon bulunamadı'},
 'Episode %s': {'ar': 'الحلقة %s', 'de': 'Episode %s', 'fr': 'Épisode %s', 'tr': 'Bölüm %s'},
 'WATCHED': {'ar': 'تمت المشاهدة', 'de': 'GESEHEN', 'fr': 'VU', 'tr': 'İZLENDİ'},
 'CONTINUE': {'ar': 'متابعة', 'de': 'WEITERSCHAUEN', 'fr': 'CONTINUER', 'tr': 'DEVAM'},
 'No episodes returned': {'ar': 'لم يتم العثور على حلقات', 'de': 'Keine Episoden zurückgegeben', 'fr': 'Aucun épisode renvoyé', 'tr': 'Bölüm bulunamadı'},
 'Choose an option': {'ar': 'اختر خيارًا', 'de': 'Option auswählen', 'fr': 'Choisissez une option', 'tr': 'Bir seçenek seçin'},
 'Current source': {'ar': 'المصدر الحالي', 'de': 'Aktuelle Quelle', 'fr': 'Source actuelle', 'tr': 'Geçerli kaynak'},
 '%d/%d READY': {'ar': '%d/%d جاهز', 'de': '%d/%d BEREIT', 'fr': '%d/%d PRÊT', 'tr': '%d/%d HAZIR'},
 ' • %d MISS': {'ar': ' • %d ناقص', 'de': ' • %d fehlen', 'fr': ' • %d MANQUANT', 'tr': ' • %d EKSİK'},
 'Current Artwork • %s • %s categories %d/%d • %d titles counted': {'ar': 'الصور الحالية • %s • أقسام %s %d/%d • تم عدّ %d عنوان',
                                                                    'de': 'Aktuelle Bilder • %s • %s Kategorien %d/%d • %d Titel gezählt',
                                                                    'fr': 'Illustrations actuelles • %s • catégories %s %d/%d • %d titres comptés',
                                                                    'tr': 'Mevcut Görseller • %s • %s kategorileri %d/%d • %d başlık sayıldı'},
 'Current Artwork • %s • %s • reading %s page %d • %d titles counted': {'ar': 'الصور الحالية • %s • %s • قراءة %s الصفحة %d • تم عدّ %d عنوان',
                                                                        'de': 'Aktuelle Bilder • %s • %s • lese %s Seite %d • %d Titel gezählt',
                                                                        'fr': 'Illustrations actuelles • %s • %s • lecture de %s page %d • %d titres comptés',
                                                                        'tr': 'Mevcut Görseller • %s • %s • %s sayfa %d okunuyor • %d başlık sayıldı'},
 'Current Artwork • %s • %s catalog pages %d/%d • %d/%d titles counted': {'ar': 'الصور الحالية • %s • كتالوج %s الصفحات %d/%d • تم عدّ %d/%d عنوان',
                                                                          'de': 'Aktuelle Bilder • %s • %s Katalogseiten %d/%d • %d/%d Titel gezählt',
                                                                          'fr': 'Illustrations actuelles • %s • catalogue %s pages %d/%d • %d/%d titres comptés',
                                                                          'tr': 'Mevcut Görseller • %s • %s kataloğu sayfalar %d/%d • %d/%d başlık sayıldı'},
 'Current Artwork • %s • %s catalog page %d • %d titles counted': {'ar': 'الصور الحالية • %s • كتالوج %s الصفحة %d • تم عدّ %d عنوان',
                                                                   'de': 'Aktuelle Bilder • %s • %s Katalogseite %d • %d Titel gezählt',
                                                                   'fr': 'Illustrations actuelles • %s • catalogue %s page %d • %d titres comptés',
                                                                   'tr': 'Mevcut Görseller • %s • %s kataloğu sayfa %d • %d başlık sayıldı'},
 ' • page %d/%d': {'ar': ' • الصفحة %d/%d', 'de': ' • Seite %d/%d', 'fr': ' • page %d/%d', 'tr': ' • sayfa %d/%d'},
 ' • page %d': {'ar': ' • الصفحة %d', 'de': ' • Seite %d', 'fr': ' • page %d', 'tr': ' • sayfa %d'},
 'Current Artwork • %s • %s categories %d/%d%s • %d titles counted': {'ar': 'الصور الحالية • %s • أقسام %s %d/%d%s • تم عدّ %d عنوان',
                                                                      'de': 'Aktuelle Bilder • %s • %s Kategorien %d/%d%s • %d Titel gezählt',
                                                                      'fr': 'Illustrations actuelles • %s • catégories %s %d/%d%s • %d titres comptés',
                                                                      'tr': 'Mevcut Görseller • %s • %s kategorileri %d/%d%s • %d başlık sayıldı'},
 'Current Artwork • HDD check %d/%d • Ready %d • Need work %d': {'ar': 'الصور الحالية • فحص HDD %d/%d • جاهز %d • يحتاج تجهيز %d',
                                                                 'de': 'Aktuelle Bilder • HDD-Prüfung %d/%d • Bereit %d • Zu erledigen %d',
                                                                 'fr': 'Illustrations actuelles • vérification HDD %d/%d • prêtes %d • à préparer %d',
                                                                 'tr': 'Mevcut Görseller • HDD kontrolü %d/%d • Hazır %d • İşlem gerekli %d'},
 'Current Artwork • Total %d • Ready on HDD %d • Need work %d': {'ar': 'الصور الحالية • الإجمالي %d • جاهز على HDD %d • يحتاج تجهيز %d',
                                                                 'de': 'Aktuelle Bilder • Gesamt %d • Bereit auf HDD %d • Zu erledigen %d',
                                                                 'fr': 'Illustrations actuelles • total %d • prêtes sur HDD %d • à préparer %d',
                                                                 'tr': "Mevcut Görseller • Toplam %d • HDD'de hazır %d • İşlem gerekli %d"},
 'Current Artwork • %d/%d processed • Already ready %d • Downloaded %d • Local build %d • Remaining %d • Unavailable %d': {'ar': 'الصور الحالية • تمت معالجة %d/%d • جاهز مسبقًا '
                                                                                                                                 '%d • تم تنزيله %d • تجهيز محلي %d • متبقٍ %d • '
                                                                                                                                 'غير متاح %d',
                                                                                                                           'de': 'Aktuelle Bilder • %d/%d verarbeitet • Bereits '
                                                                                                                                 'bereit %d • Heruntergeladen %d • Lokal erstellt '
                                                                                                                                 '%d • Verbleibend %d • Nicht verfügbar %d',
                                                                                                                           'fr': 'Illustrations actuelles • %d/%d traitées • déjà '
                                                                                                                                 'prêtes %d • téléchargées %d • générées '
                                                                                                                                 'localement %d • restantes %d • indisponibles %d',
                                                                                                                           'tr': 'Mevcut Görseller • %d/%d işlendi • Önceden hazır '
                                                                                                                                 '%d • İndirilen %d • Yerel oluşturma %d • Kalan '
                                                                                                                                 '%d • Kullanılamayan %d'},
 'Current Artwork already ready • checked %d/%d • everything is on HDD • 0 remaining': {'ar': 'الصور الحالية جاهزة بالفعل • تم فحص %d/%d • كل شيء على HDD • المتبقي 0',
                                                                                        'de': 'Aktuelle Bilder bereits bereit • %d/%d geprüft • alles auf der HDD • 0 verbleibend',
                                                                                        'fr': 'Illustrations actuelles déjà prêtes • %d/%d vérifiées • tout est sur HDD • 0 '
                                                                                              'restante',
                                                                                        'tr': "Mevcut Görseller zaten hazır • %d/%d kontrol edildi • her şey HDD'de • kalan 0"},
 'Current Artwork ready • %d/%d on HDD • %d already ready • %d downloaded • %d local builds • 0 remaining': {'ar': 'الصور الحالية جاهزة • %d/%d على HDD • جاهز مسبقًا %d • تم '
                                                                                                                   'تنزيله %d • تجهيز محلي %d • المتبقي 0',
                                                                                                             'de': 'Aktuelle Bilder bereit • %d/%d auf HDD • %d bereits bereit • '
                                                                                                                   '%d heruntergeladen • %d lokal erstellt • 0 verbleibend',
                                                                                                             'fr': 'Illustrations actuelles prêtes • %d/%d sur HDD • %d déjà '
                                                                                                                   'prêtes • %d téléchargées • %d générées localement • 0 restante',
                                                                                                             'tr': "Mevcut Görseller hazır • %d/%d HDD'de • %d önceden hazır • %d "
                                                                                                                   'indirildi • %d yerel oluşturuldu • kalan 0'},
 'Current Artwork finished • %d/%d ready • %d downloaded • %d local builds • %d unavailable': {'ar': 'اكتملت الصور الحالية • جاهز %d/%d • تم تنزيله %d • تجهيز محلي %d • غير متاح '
                                                                                                     '%d',
                                                                                               'de': 'Aktuelle Bilder abgeschlossen • %d/%d bereit • %d heruntergeladen • %d lokal '
                                                                                                     'erstellt • %d nicht verfügbar',
                                                                                               'fr': 'Illustrations actuelles terminées • %d/%d prêtes • %d téléchargées • %d '
                                                                                                     'générées localement • %d indisponibles',
                                                                                               'tr': 'Mevcut Görseller tamamlandı • %d/%d hazır • %d indirildi • %d yerel '
                                                                                                     'oluşturuldu • %d kullanılamıyor'},
 'Current Artwork failed: %s': {'ar': 'فشل تجهيز الصور الحالية: %s',
                                'de': 'Aktuelle Bilder fehlgeschlagen: %s',
                                'fr': 'Échec des illustrations actuelles : %s',
                                'tr': 'Mevcut Görseller başarısız: %s'},
 '%s   •   Current: %s   •   ARROWS Navigate   •   OK Select   •   BACK Home': {'ar': '%s   •   الحالي: %s   •   الأسهم للتنقل   •   OK اختيار   •   BACK الرئيسية',
                                                                                'de': '%s   •   Aktuell: %s   •   PFEILE Navigieren   •   OK Auswählen   •   ZURÜCK Startseite',
                                                                                'fr': '%s   •   Actuel : %s   •   FLÈCHES Naviguer   •   OK Sélectionner   •   BACK Accueil',
                                                                                'tr': '%s   •   Geçerli: %s   •   OKLAR Gezin   •   OK Seç   •   BACK Ana Sayfa'},
 'HIDE': {'ar': 'إخفاء', 'de': 'AUSBLENDEN', 'fr': 'MASQUER', 'tr': 'GİZLE'},
 'PIN': {'ar': 'PIN', 'de': 'PIN', 'fr': 'PIN', 'tr': 'PIN'},
 'EPG': {'ar': 'EPG', 'de': 'EPG', 'fr': 'EPG', 'tr': 'EPG'},
 'COMPACT': {'ar': 'مدمج', 'de': 'KOMPAKT', 'fr': 'COMPACT', 'tr': 'KOMPAKT'},
 'LARGE': {'ar': 'كبير', 'de': 'GROSS', 'fr': 'GRAND', 'tr': 'BÜYÜK'},
 'ASK': {'ar': 'اسأل', 'de': 'NACHFRAGEN', 'fr': 'DEMANDER', 'tr': 'SOR'},
 'ALWAYS': {'ar': 'دائمًا', 'de': 'IMMER', 'fr': 'TOUJOURS', 'tr': 'HER ZAMAN'},
 'FROM START': {'ar': 'من البداية', 'de': 'VON ANFANG', 'fr': 'DEPUIS LE DÉBUT', 'tr': 'BAŞTAN'},
 'Playback engine': {'ar': 'محرك التشغيل', 'de': 'Wiedergabe-Engine', 'fr': 'Moteur de lecture', 'tr': 'Oynatma motoru'},
 'Choose the Enigma2 playback service': {'ar': 'اختر خدمة تشغيل Enigma2',
                                         'de': 'Enigma2-Wiedergabedienst auswählen',
                                         'fr': 'Choisissez le service de lecture Enigma2',
                                         'tr': 'Enigma2 oynatma hizmetini seçin'},
 'Playback is locked to the engine selected in Playback Engine. Smart Engine and per-title engine switching are disabled.': {'ar': 'التشغيل مثبت على المحرك المحدد في محرك '
                                                                                                                                   'التشغيل. تم تعطيل Smart Engine والتبديل حسب '
                                                                                                                                   'العنوان.',
                                                                                                                             'de': 'Die Wiedergabe ist auf die unter '
                                                                                                                                   '„Wiedergabe-Engine“ gewählte Engine '
                                                                                                                                   'festgelegt. Smart Engine und titelspezifischer '
                                                                                                                                   'Engine-Wechsel sind deaktiviert.',
                                                                                                                             'fr': 'La lecture est verrouillée sur le moteur '
                                                                                                                                   'choisi dans Moteur de lecture. Smart Engine et '
                                                                                                                                   'le changement par titre sont désactivés.',
                                                                                                                             'tr': "Oynatma, Oynatma Motoru'nda seçilen motora "
                                                                                                                                   'kilitlidir. Smart Engine ve başlık bazlı motor '
                                                                                                                                   'geçişi devre dışıdır.'},
 'Both views read the same single Global TMDB Library record': {'ar': 'كلا العرضين يقرآن نفس سجل مكتبة TMDB العالمية',
                                                                'de': 'Beide Ansichten lesen denselben einzelnen Eintrag der globalen TMDB-Bibliothek',
                                                                'fr': 'Les deux vues lisent le même enregistrement de la bibliothèque TMDB globale',
                                                                'tr': 'Her iki görünüm de aynı Global TMDB Library kaydını okur'},
 'Poster Grid': {'ar': 'شبكة البوسترات', 'de': 'Poster-Raster', 'fr': 'Grille d’affiches', 'tr': 'Poster Izgarası'},
 'Portal timeout': {'ar': 'مهلة اتصال Portal', 'de': 'Portal-Zeitlimit', 'fr': 'Délai du portail', 'tr': 'Portal zaman aşımı'},
 'Resume behavior': {'ar': 'سلوك استكمال المشاهدة', 'de': 'Fortsetzungsverhalten', 'fr': 'Comportement de reprise', 'tr': 'Devam etme davranışı'},
 'Ask every time': {'ar': 'اسأل في كل مرة', 'de': 'Jedes Mal nachfragen', 'fr': 'Demander à chaque fois', 'tr': 'Her seferinde sor'},
 'Always resume': {'ar': 'استكمل دائمًا', 'de': 'Immer fortsetzen', 'fr': 'Toujours reprendre', 'tr': 'Her zaman devam et'},
 'Always start from beginning': {'ar': 'ابدأ دائمًا من البداية', 'de': 'Immer von vorn beginnen', 'fr': 'Toujours recommencer depuis le début', 'tr': 'Her zaman baştan başla'},
 'Progress save interval': {'ar': 'فاصل حفظ التقدم',
                            'de': 'Speicherintervall für Fortschritt',
                            'fr': 'Intervalle d’enregistrement de la progression',
                            'tr': 'İlerleme kayıt aralığı'},
 'Next episode countdown': {'ar': 'العد التنازلي للحلقة التالية',
                            'de': 'Countdown für nächste Episode',
                            'fr': 'Compte à rebours du prochain épisode',
                            'tr': 'Sonraki bölüm geri sayımı'},
 'Completion threshold': {'ar': 'نسبة اكتمال المشاهدة', 'de': 'Abschlussschwelle', 'fr': 'Seuil de fin', 'tr': 'Tamamlama eşiği'},
 'Completion remaining time': {'ar': 'الوقت المتبقي للاكتمال', 'de': 'Verbleibende Zeit für Abschluss', 'fr': 'Temps restant avant fin', 'tr': 'Tamamlanma için kalan süre'},
 '%d hours': {'ar': '%d ساعة', 'de': '%d Stunden', 'fr': '%d heures', 'tr': '%d saat'},
 'EPG window': {'ar': 'نطاق EPG', 'de': 'EPG-Zeitfenster', 'fr': 'Fenêtre EPG', 'tr': 'EPG aralığı'},
 'Catch-up history': {'ar': 'سجل Catch-up', 'de': 'Catch-up-Verlauf', 'fr': 'Historique Catch-up', 'tr': 'Catch-up geçmişi'},
 'Choose ON or OFF': {'ar': 'اختر تشغيل أو إيقاف', 'de': 'EIN oder AUS wählen', 'fr': 'Choisissez ACTIVÉ ou DÉSACTIVÉ', 'tr': 'AÇIK veya KAPALI seçin'},
 'Download disk safety reserve': {'ar': 'المساحة الاحتياطية الآمنة للتنزيل',
                                  'de': 'Sicherheitsreserve für Download-Speicher',
                                  'fr': 'Réserve de sécurité du disque pour les téléchargements',
                                  'tr': 'İndirme disk güvenlik payı'},
 'Search results per portal': {'ar': 'نتائج البحث لكل Portal', 'de': 'Suchergebnisse pro Portal', 'fr': 'Résultats de recherche par portail', 'tr': 'Portal başına arama sonucu'},
 'Smart Recovery retries': {'ar': 'محاولات Smart Recovery',
                            'de': 'Versuche der intelligenten Wiederherstellung',
                            'fr': 'Tentatives Smart Recovery',
                            'tr': 'Smart Recovery denemeleri'},
 'Parental mode': {'ar': 'وضع الرقابة الأبوية', 'de': 'Kindersicherungsmodus', 'fr': 'Mode parental', 'tr': 'Ebeveyn modu'},
 'PIN protect': {'ar': 'حماية بـPIN', 'de': 'Per PIN schützen', 'fr': 'Protéger par PIN', 'tr': 'PIN ile koru'},
 'Hide sensitive categories': {'ar': 'إخفاء الأقسام الحساسة', 'de': 'Sensible Kategorien ausblenden', 'fr': 'Masquer les catégories sensibles', 'tr': 'Hassas kategorileri gizle'},
 'Parental unlock duration': {'ar': 'مدة فتح الرقابة الأبوية',
                              'de': 'Entsperrdauer der Kindersicherung',
                              'fr': 'Durée du déverrouillage parental',
                              'tr': 'Ebeveyn kilidi açma süresi'},
 'One action': {'ar': 'إجراء واحد', 'de': 'Eine Aktion', 'fr': 'Une action', 'tr': 'Tek işlem'},
 '15 minutes': {'ar': '15 دقيقة', 'de': '15 Minuten', 'fr': '15 minutes', 'tr': '15 dakika'},
 '30 minutes': {'ar': '30 دقيقة', 'de': '30 Minuten', 'fr': '30 minutes', 'tr': '30 dakika'},
 '60 minutes': {'ar': '60 دقيقة', 'de': '60 Minuten', 'fr': '60 minutes', 'tr': '60 dakika'},
 '120 minutes': {'ar': '120 دقيقة', 'de': '120 Minuten', 'fr': '120 minutes', 'tr': '120 dakika'},
 'Adult keywords (comma separated)': {'ar': 'كلمات المحتوى الحساس (مفصولة بفواصل)',
                                      'de': 'Erwachsenen-Schlüsselwörter (durch Kommas getrennt)',
                                      'fr': 'Mots-clés sensibles (séparés par des virgules)',
                                      'tr': 'Yetişkin içerik anahtar kelimeleri (virgülle ayrılmış)'},
 'New parental PIN (4-8 digits)': {'ar': 'PIN أبوي جديد (4-8 أرقام)',
                                   'de': 'Neue Kindersicherungs-PIN (4–8 Ziffern)',
                                   'fr': 'Nouveau PIN parental (4 à 8 chiffres)',
                                   'tr': "Yeni ebeveyn PIN'i (4-8 rakam)"},
 'Channel list layout': {'ar': 'تخطيط قائمة القنوات', 'de': 'Layout der Kanalliste', 'fr': 'Disposition de la liste des chaînes', 'tr': 'Kanal listesi düzeni'},
 'Professional EPG': {'ar': 'EPG احترافي', 'de': 'Professionelles EPG', 'fr': 'EPG professionnel', 'tr': 'Profesyonel EPG'},
 'Compact': {'ar': 'مدمج', 'de': 'Kompakt', 'fr': 'Compact', 'tr': 'Kompakt'},
 'API Keys': {'ar': 'مفاتيح API', 'de': 'API-Schlüssel', 'fr': 'Clés API', 'tr': 'API Anahtarları'},
 'Put long credentials in:\n/etc/enigma2/ultrastalker/api_keys.conf\n\nTMDB_API_KEY=...\nTMDB_READ_TOKEN=...\nIMDB_API_KEY=...\nIMDB_API_ENDPOINT=... (generic mode)\nSUBDL_API_KEY=...\n\nOfficial IMDb/AWS Data Exchange:\nIMDB_ACCESS_KEY_ID=...\nIMDB_SECRET_ACCESS_KEY=...\nIMDB_SESSION_TOKEN=... (optional)\nIMDB_REGION=us-east-1\nIMDB_DATASET_ID=...\nIMDB_REVISION_ID=...\nIMDB_ASSET_ID=...': {'ar': 'ضع '
                                                                                                                                                                                                                                                                                                                                                                                                                 'بيانات '
                                                                                                                                                                                                                                                                                                                                                                                                                 'الاعتماد '
                                                                                                                                                                                                                                                                                                                                                                                                                 'الطويلة '
                                                                                                                                                                                                                                                                                                                                                                                                                 'في:\n'
                                                                                                                                                                                                                                                                                                                                                                                                                 '/etc/enigma2/ultrastalker/api_keys.conf\n'
                                                                                                                                                                                                                                                                                                                                                                                                                 '\n'
                                                                                                                                                                                                                                                                                                                                                                                                                 'TMDB_API_KEY=...\n'
                                                                                                                                                                                                                                                                                                                                                                                                                 'TMDB_READ_TOKEN=...\n'
                                                                                                                                                                                                                                                                                                                                                                                                                 'IMDB_API_KEY=...\n'
                                                                                                                                                                                                                                                                                                                                                                                                                 'IMDB_API_ENDPOINT=... '
                                                                                                                                                                                                                                                                                                                                                                                                                 '(الوضع '
                                                                                                                                                                                                                                                                                                                                                                                                                 'العام)\n'
                                                                                                                                                                                                                                                                                                                                                                                                                 'SUBDL_API_KEY=...\n'
                                                                                                                                                                                                                                                                                                                                                                                                                 '\n'
                                                                                                                                                                                                                                                                                                                                                                                                                 'IMDb/AWS '
                                                                                                                                                                                                                                                                                                                                                                                                                 'Data '
                                                                                                                                                                                                                                                                                                                                                                                                                 'Exchange '
                                                                                                                                                                                                                                                                                                                                                                                                                 'الرسمي:\n'
                                                                                                                                                                                                                                                                                                                                                                                                                 'IMDB_ACCESS_KEY_ID=...\n'
                                                                                                                                                                                                                                                                                                                                                                                                                 'IMDB_SECRET_ACCESS_KEY=...\n'
                                                                                                                                                                                                                                                                                                                                                                                                                 'IMDB_SESSION_TOKEN=... '
                                                                                                                                                                                                                                                                                                                                                                                                                 '(اختياري)\n'
                                                                                                                                                                                                                                                                                                                                                                                                                 'IMDB_REGION=us-east-1\n'
                                                                                                                                                                                                                                                                                                                                                                                                                 'IMDB_DATASET_ID=...\n'
                                                                                                                                                                                                                                                                                                                                                                                                                 'IMDB_REVISION_ID=...\n'
                                                                                                                                                                                                                                                                                                                                                                                                                 'IMDB_ASSET_ID=...',
                                                                                                                                                                                                                                                                                                                                                                                                           'de': 'Lange '
                                                                                                                                                                                                                                                                                                                                                                                                                 'Zugangsdaten '
                                                                                                                                                                                                                                                                                                                                                                                                                 'hier '
                                                                                                                                                                                                                                                                                                                                                                                                                 'eintragen:\n'
                                                                                                                                                                                                                                                                                                                                                                                                                 '/etc/enigma2/ultrastalker/api_keys.conf\n'
                                                                                                                                                                                                                                                                                                                                                                                                                 '\n'
                                                                                                                                                                                                                                                                                                                                                                                                                 'TMDB_API_KEY=...\n'
                                                                                                                                                                                                                                                                                                                                                                                                                 'TMDB_READ_TOKEN=...\n'
                                                                                                                                                                                                                                                                                                                                                                                                                 'IMDB_API_KEY=...\n'
                                                                                                                                                                                                                                                                                                                                                                                                                 'IMDB_API_ENDPOINT=... '
                                                                                                                                                                                                                                                                                                                                                                                                                 '(generischer '
                                                                                                                                                                                                                                                                                                                                                                                                                 'Modus)\n'
                                                                                                                                                                                                                                                                                                                                                                                                                 'SUBDL_API_KEY=...\n'
                                                                                                                                                                                                                                                                                                                                                                                                                 '\n'
                                                                                                                                                                                                                                                                                                                                                                                                                 'Offizieller '
                                                                                                                                                                                                                                                                                                                                                                                                                 'IMDb/AWS '
                                                                                                                                                                                                                                                                                                                                                                                                                 'Data '
                                                                                                                                                                                                                                                                                                                                                                                                                 'Exchange:\n'
                                                                                                                                                                                                                                                                                                                                                                                                                 'IMDB_ACCESS_KEY_ID=...\n'
                                                                                                                                                                                                                                                                                                                                                                                                                 'IMDB_SECRET_ACCESS_KEY=...\n'
                                                                                                                                                                                                                                                                                                                                                                                                                 'IMDB_SESSION_TOKEN=... '
                                                                                                                                                                                                                                                                                                                                                                                                                 '(optional)\n'
                                                                                                                                                                                                                                                                                                                                                                                                                 'IMDB_REGION=us-east-1\n'
                                                                                                                                                                                                                                                                                                                                                                                                                 'IMDB_DATASET_ID=...\n'
                                                                                                                                                                                                                                                                                                                                                                                                                 'IMDB_REVISION_ID=...\n'
                                                                                                                                                                                                                                                                                                                                                                                                                 'IMDB_ASSET_ID=...',
                                                                                                                                                                                                                                                                                                                                                                                                           'fr': 'Placez '
                                                                                                                                                                                                                                                                                                                                                                                                                 'les '
                                                                                                                                                                                                                                                                                                                                                                                                                 'identifiants '
                                                                                                                                                                                                                                                                                                                                                                                                                 'longs '
                                                                                                                                                                                                                                                                                                                                                                                                                 'dans '
                                                                                                                                                                                                                                                                                                                                                                                                                 ':\n'
                                                                                                                                                                                                                                                                                                                                                                                                                 '/etc/enigma2/ultrastalker/api_keys.conf\n'
                                                                                                                                                                                                                                                                                                                                                                                                                 '\n'
                                                                                                                                                                                                                                                                                                                                                                                                                 'TMDB_API_KEY=...\n'
                                                                                                                                                                                                                                                                                                                                                                                                                 'TMDB_READ_TOKEN=...\n'
                                                                                                                                                                                                                                                                                                                                                                                                                 'IMDB_API_KEY=...\n'
                                                                                                                                                                                                                                                                                                                                                                                                                 'IMDB_API_ENDPOINT=... '
                                                                                                                                                                                                                                                                                                                                                                                                                 '(mode '
                                                                                                                                                                                                                                                                                                                                                                                                                 'générique)\n'
                                                                                                                                                                                                                                                                                                                                                                                                                 'SUBDL_API_KEY=...\n'
                                                                                                                                                                                                                                                                                                                                                                                                                 '\n'
                                                                                                                                                                                                                                                                                                                                                                                                                 'IMDb/AWS '
                                                                                                                                                                                                                                                                                                                                                                                                                 'Data '
                                                                                                                                                                                                                                                                                                                                                                                                                 'Exchange '
                                                                                                                                                                                                                                                                                                                                                                                                                 'officiel '
                                                                                                                                                                                                                                                                                                                                                                                                                 ':\n'
                                                                                                                                                                                                                                                                                                                                                                                                                 'IMDB_ACCESS_KEY_ID=...\n'
                                                                                                                                                                                                                                                                                                                                                                                                                 'IMDB_SECRET_ACCESS_KEY=...\n'
                                                                                                                                                                                                                                                                                                                                                                                                                 'IMDB_SESSION_TOKEN=... '
                                                                                                                                                                                                                                                                                                                                                                                                                 '(facultatif)\n'
                                                                                                                                                                                                                                                                                                                                                                                                                 'IMDB_REGION=us-east-1\n'
                                                                                                                                                                                                                                                                                                                                                                                                                 'IMDB_DATASET_ID=...\n'
                                                                                                                                                                                                                                                                                                                                                                                                                 'IMDB_REVISION_ID=...\n'
                                                                                                                                                                                                                                                                                                                                                                                                                 'IMDB_ASSET_ID=...',
                                                                                                                                                                                                                                                                                                                                                                                                           'tr': 'Uzun '
                                                                                                                                                                                                                                                                                                                                                                                                                 'kimlik '
                                                                                                                                                                                                                                                                                                                                                                                                                 'bilgilerini '
                                                                                                                                                                                                                                                                                                                                                                                                                 'şuraya '
                                                                                                                                                                                                                                                                                                                                                                                                                 'yazın:\n'
                                                                                                                                                                                                                                                                                                                                                                                                                 '/etc/enigma2/ultrastalker/api_keys.conf\n'
                                                                                                                                                                                                                                                                                                                                                                                                                 '\n'
                                                                                                                                                                                                                                                                                                                                                                                                                 'TMDB_API_KEY=...\n'
                                                                                                                                                                                                                                                                                                                                                                                                                 'TMDB_READ_TOKEN=...\n'
                                                                                                                                                                                                                                                                                                                                                                                                                 'IMDB_API_KEY=...\n'
                                                                                                                                                                                                                                                                                                                                                                                                                 'IMDB_API_ENDPOINT=... '
                                                                                                                                                                                                                                                                                                                                                                                                                 '(genel '
                                                                                                                                                                                                                                                                                                                                                                                                                 'mod)\n'
                                                                                                                                                                                                                                                                                                                                                                                                                 'SUBDL_API_KEY=...\n'
                                                                                                                                                                                                                                                                                                                                                                                                                 '\n'
                                                                                                                                                                                                                                                                                                                                                                                                                 'Resmî '
                                                                                                                                                                                                                                                                                                                                                                                                                 'IMDb/AWS '
                                                                                                                                                                                                                                                                                                                                                                                                                 'Data '
                                                                                                                                                                                                                                                                                                                                                                                                                 'Exchange:\n'
                                                                                                                                                                                                                                                                                                                                                                                                                 'IMDB_ACCESS_KEY_ID=...\n'
                                                                                                                                                                                                                                                                                                                                                                                                                 'IMDB_SECRET_ACCESS_KEY=...\n'
                                                                                                                                                                                                                                                                                                                                                                                                                 'IMDB_SESSION_TOKEN=... '
                                                                                                                                                                                                                                                                                                                                                                                                                 '(isteğe '
                                                                                                                                                                                                                                                                                                                                                                                                                 'bağlı)\n'
                                                                                                                                                                                                                                                                                                                                                                                                                 'IMDB_REGION=us-east-1\n'
                                                                                                                                                                                                                                                                                                                                                                                                                 'IMDB_DATASET_ID=...\n'
                                                                                                                                                                                                                                                                                                                                                                                                                 'IMDB_REVISION_ID=...\n'
                                                                                                                                                                                                                                                                                                                                                                                                                 'IMDB_ASSET_ID=...'},
 'TMDB API key or Read Access Token': {'ar': 'مفتاح TMDB API أو Read Access Token',
                                       'de': 'TMDB-API-Schlüssel oder Read Access Token',
                                       'fr': 'Clé API TMDB ou Read Access Token',
                                       'tr': 'TMDB API anahtarı veya Read Access Token'},
 'TMDB metadata language': {'ar': 'لغة بيانات TMDB', 'de': 'Sprache der TMDB-Metadaten', 'fr': 'Langue des métadonnées TMDB', 'tr': 'TMDB meta veri dili'},
 'Arabic (Egypt)': {'ar': 'العربية (مصر)', 'de': 'Arabisch (Ägypten)', 'fr': 'Arabe (Égypte)', 'tr': 'Arapça (Mısır)'},
 'English (US)': {'ar': 'الإنجليزية (الولايات المتحدة)', 'de': 'Englisch (US)', 'fr': 'Anglais (États-Unis)', 'tr': 'İngilizce (ABD)'},
 'SubDL API key': {'ar': 'مفتاح SubDL API', 'de': 'SubDL-API-Schlüssel', 'fr': 'Clé API SubDL', 'tr': 'SubDL API anahtarı'},
 'TMDB': {'ar': 'TMDB', 'de': 'TMDB', 'fr': 'TMDB', 'tr': 'TMDB'},
 'Add a TMDB API key or Read Access Token first.': {'ar': 'أضف مفتاح TMDB API أو Read Access Token أولًا.',
                                                    'de': 'Fügen Sie zuerst einen TMDB-API-Schlüssel oder Read Access Token hinzu.',
                                                    'fr': 'Ajoutez d’abord une clé API TMDB ou un Read Access Token.',
                                                    'tr': 'Önce bir TMDB API anahtarı veya Read Access Token ekleyin.'},
 'TMDB connection successful.': {'ar': 'تم الاتصال بـTMDB بنجاح.', 'de': 'TMDB-Verbindung erfolgreich.', 'fr': 'Connexion TMDB réussie.', 'tr': 'TMDB bağlantısı başarılı.'},
 'TMDB test failed: %s': {'ar': 'فشل اختبار TMDB: %s', 'de': 'TMDB-Test fehlgeschlagen: %s', 'fr': 'Échec du test TMDB : %s', 'tr': 'TMDB testi başarısız: %s'},
 'This product uses the TMDB API but is not endorsed or certified by TMDB.\n\nTMDB data/images are used only when you configure your own credential.': {'ar': 'يستخدم هذا المنتج '
                                                                                                                                                              'TMDB API لكنه غير '
                                                                                                                                                              'معتمد أو مصدّق من '
                                                                                                                                                              'TMDB.\n'
                                                                                                                                                              '\n'
                                                                                                                                                              'تُستخدم بيانات وصور '
                                                                                                                                                              'TMDB فقط عند إعداد '
                                                                                                                                                              'بيانات الاعتماد '
                                                                                                                                                              'الخاصة بك.',
                                                                                                                                                        'de': 'Dieses Produkt '
                                                                                                                                                              'verwendet die '
                                                                                                                                                              'TMDB-API, ist '
                                                                                                                                                              'jedoch nicht von '
                                                                                                                                                              'TMDB unterstützt '
                                                                                                                                                              'oder zertifiziert.\n'
                                                                                                                                                              '\n'
                                                                                                                                                              'TMDB-Daten/-Bilder '
                                                                                                                                                              'werden nur '
                                                                                                                                                              'verwendet, wenn Sie '
                                                                                                                                                              'Ihre eigenen '
                                                                                                                                                              'Zugangsdaten '
                                                                                                                                                              'einrichten.',
                                                                                                                                                        'fr': 'Ce produit utilise '
                                                                                                                                                              'l’API TMDB, mais '
                                                                                                                                                              'n’est ni approuvé '
                                                                                                                                                              'ni certifié par '
                                                                                                                                                              'TMDB.\n'
                                                                                                                                                              '\n'
                                                                                                                                                              'Les données et '
                                                                                                                                                              'images TMDB ne sont '
                                                                                                                                                              'utilisées que si '
                                                                                                                                                              'vous configurez vos '
                                                                                                                                                              'propres '
                                                                                                                                                              'identifiants.',
                                                                                                                                                        'tr': 'Bu ürün TMDB '
                                                                                                                                                              "API'sini kullanır "
                                                                                                                                                              'ancak TMDB '
                                                                                                                                                              'tarafından '
                                                                                                                                                              'desteklenmez veya '
                                                                                                                                                              'sertifikalandırılmaz.\n'
                                                                                                                                                              '\n'
                                                                                                                                                              'TMDB '
                                                                                                                                                              'verileri/görselleri '
                                                                                                                                                              'yalnızca kendi '
                                                                                                                                                              'kimlik bilginizi '
                                                                                                                                                              'yapılandırdığınızda '
                                                                                                                                                              'kullanılır.'},
 'Choose how devices on your network open Web Cleaner': {'ar': 'اختر طريقة فتح أجهزة شبكتك لـWeb Cleaner',
                                                         'de': 'Wählen Sie, wie Geräte in Ihrem Netzwerk den Web Cleaner öffnen',
                                                         'fr': 'Choisissez comment les appareils de votre réseau ouvrent Web Cleaner',
                                                         'tr': "Ağınızdaki cihazların Web Cleaner'ı nasıl açacağını seçin"},
 'Easy - LAN direct': {'ar': 'سهل - دخول مباشر عبر LAN', 'de': 'Einfach – LAN direkt', 'fr': 'Simple - accès LAN direct', 'tr': 'Kolay - doğrudan LAN'},
 'Protected - PIN / QR': {'ar': 'محمي - PIN / QR', 'de': 'Geschützt – PIN / QR', 'fr': 'Protégé - PIN / QR', 'tr': 'Korumalı - PIN / QR'},
 'Easy LAN access': {'ar': 'دخول سهل عبر LAN', 'de': 'Einfacher LAN-Zugriff', 'fr': 'Accès LAN simple', 'tr': 'Kolay LAN erişimi'},
 'PIN / QR protected': {'ar': 'محمي بـPIN / QR', 'de': 'PIN-/QR-geschützt', 'fr': 'Protégé par PIN / QR', 'tr': 'PIN / QR korumalı'},
 'Premium Cleaner + Deep Check • %s': {'ar': 'Premium Cleaner + Deep Check • %s',
                                       'de': 'Premium Cleaner + Deep Check • %s',
                                       'fr': 'Premium Cleaner + Deep Check • %s',
                                       'tr': 'Premium Cleaner + Deep Check • %s'},
 'Start / Show Link': {'ar': 'بدء / عرض الرابط', 'de': 'Starten / Link anzeigen', 'fr': 'Démarrer / Afficher le lien', 'tr': 'Başlat / Bağlantıyı Göster'},
 'Stop Service': {'ar': 'إيقاف الخدمة', 'de': 'Dienst stoppen', 'fr': 'Arrêter le service', 'tr': 'Hizmeti Durdur'},
 'STOPPED': {'ar': 'متوقف', 'de': 'GESTOPPT', 'fr': 'ARRÊTÉ', 'tr': 'DURDU'},
 'RUNNING EASY :7725': {'ar': 'يعمل EASY :7725', 'de': 'LÄUFT EINFACH :7725', 'fr': 'ACTIF EASY :7725', 'tr': 'ÇALIŞIYOR EASY :7725'},
 'RUNNING PIN :7725': {'ar': 'يعمل PIN :7725', 'de': 'LÄUFT PIN :7725', 'fr': 'ACTIF PIN :7725', 'tr': 'ÇALIŞIYOR PIN :7725'},
 'Web Cleaner access: %s%s': {'ar': 'وصول Web Cleaner: %s%s', 'de': 'Web-Cleaner-Zugriff: %s%s', 'fr': 'Accès Web Cleaner : %s%s', 'tr': 'Web Cleaner erişimi: %s%s'},
 ' • service stopped; start again': {'ar': ' • تم إيقاف الخدمة؛ ابدأها من جديد',
                                     'de': ' • Dienst gestoppt; erneut starten',
                                     'fr': ' • service arrêté ; redémarrez-le',
                                     'tr': ' • hizmet durduruldu; yeniden başlatın'},
 'Service stopped. Port 7725 is closed.': {'ar': 'تم إيقاف الخدمة. المنفذ 7725 مغلق.',
                                           'de': 'Dienst gestoppt. Port 7725 ist geschlossen.',
                                           'fr': 'Service arrêté. Le port 7725 est fermé.',
                                           'tr': 'Hizmet durduruldu. 7725 portu kapalı.'},
 'Could not start Web Cleaner:\n%s': {'ar': 'تعذر تشغيل Web Cleaner:\n%s',
                                      'de': 'Web Cleaner konnte nicht gestartet werden:\n%s',
                                      'fr': 'Impossible de démarrer Web Cleaner :\n%s',
                                      'tr': 'Web Cleaner başlatılamadı:\n%s'},
 'PIN must contain 4-8 digits': {'ar': 'يجب أن يحتوي PIN على 4 إلى 8 أرقام',
                                 'de': 'Die PIN muss 4–8 Ziffern enthalten',
                                 'fr': 'Le PIN doit contenir de 4 à 8 chiffres',
                                 'tr': 'PIN 4-8 rakam içermelidir'},
 'Choose a backup or restore action': {'ar': 'اختر إجراء نسخ احتياطي أو استعادة',
                                       'de': 'Sicherungs- oder Wiederherstellungsaktion auswählen',
                                       'fr': 'Choisissez une action de sauvegarde ou de restauration',
                                       'tr': 'Bir yedekleme veya geri yükleme işlemi seçin'},
 'Backup API Secrets': {'ar': 'نسخ أسرار API احتياطيًا', 'de': 'API-Geheimnisse sichern', 'fr': 'Sauvegarder les secrets API', 'tr': 'API Gizli Bilgilerini Yedekle'},
 'No backups found in /etc/enigma2/ultrastalker/backups or /tmp.': {'ar': 'لم يتم العثور على نسخ احتياطية في /etc/enigma2/ultrastalker/backups أو /tmp.',
                                                                    'de': 'Keine Sicherungen in /etc/enigma2/ultrastalker/backups oder /tmp gefunden.',
                                                                    'fr': 'Aucune sauvegarde trouvée dans /etc/enigma2/ultrastalker/backups ou /tmp.',
                                                                    'tr': '/etc/enigma2/ultrastalker/backups veya /tmp içinde yedek bulunamadı.'},
 'Choose backup to restore': {'ar': 'اختر نسخة احتياطية للاستعادة',
                              'de': 'Wiederherzustellende Sicherung auswählen',
                              'fr': 'Choisissez la sauvegarde à restaurer',
                              'tr': 'Geri yüklenecek yedeği seçin'},
 'Available Ultra Stalker backups': {'ar': 'نسخ Ultra Stalker الاحتياطية المتاحة',
                                     'de': 'Verfügbare Ultra-Stalker-Sicherungen',
                                     'fr': 'Sauvegardes Ultra Stalker disponibles',
                                     'tr': 'Kullanılabilir Ultra Stalker yedekleri'},
 'Backup: %s': {'ar': 'النسخة الاحتياطية: %s', 'de': 'Sicherung: %s', 'fr': 'Sauvegarde : %s', 'tr': 'Yedek: %s'},
 'Backup Created': {'ar': 'تم إنشاء النسخة الاحتياطية', 'de': 'Sicherung erstellt', 'fr': 'Sauvegarde créée', 'tr': 'Yedek Oluşturuldu'},
 'Backup created:\n%s': {'ar': 'تم إنشاء النسخة الاحتياطية:\n%s', 'de': 'Sicherung erstellt:\n%s', 'fr': 'Sauvegarde créée :\n%s', 'tr': 'Yedek oluşturuldu:\n%s'},
 'Backup Failed': {'ar': 'فشل النسخ الاحتياطي', 'de': 'Sicherung fehlgeschlagen', 'fr': 'Échec de la sauvegarde', 'tr': 'Yedekleme Başarısız'},
 'Backup failed: %s': {'ar': 'فشل النسخ الاحتياطي: %s', 'de': 'Sicherung fehlgeschlagen: %s', 'fr': 'Échec de la sauvegarde : %s', 'tr': 'Yedekleme başarısız: %s'},
 'Invalid Backup': {'ar': 'نسخة احتياطية غير صالحة', 'de': 'Ungültige Sicherung', 'fr': 'Sauvegarde invalide', 'tr': 'Geçersiz Yedek'},
 'Invalid backup: %s': {'ar': 'نسخة احتياطية غير صالحة: %s', 'de': 'Ungültige Sicherung: %s', 'fr': 'Sauvegarde invalide : %s', 'tr': 'Geçersiz yedek: %s'},
 'Restore API secrets?': {'ar': 'استعادة أسرار API؟',
                          'de': 'API-Geheimnisse wiederherstellen?',
                          'fr': 'Restaurer les secrets API ?',
                          'tr': 'API gizli bilgileri geri yüklensin mi?'},
 'Restore state only (keep current api_keys.conf)': {'ar': 'استعادة الحالة فقط (الاحتفاظ بملف api_keys.conf الحالي)',
                                                     'de': 'Nur Status wiederherstellen (aktuelle api_keys.conf beibehalten)',
                                                     'fr': 'Restaurer uniquement l’état (conserver api_keys.conf actuel)',
                                                     'tr': 'Yalnızca durumu geri yükle (mevcut api_keys.conf kalsın)'},
 'Restore state + API secrets': {'ar': 'استعادة الحالة + أسرار API',
                                 'de': 'Status + API-Geheimnisse wiederherstellen',
                                 'fr': 'Restaurer l’état + les secrets API',
                                 'tr': 'Durum + API gizli bilgilerini geri yükle'},
 'Choose what to restore': {'ar': 'اختر ما تريد استعادته',
                            'de': 'Wählen Sie aus, was wiederhergestellt werden soll',
                            'fr': 'Choisissez ce qu’il faut restaurer',
                            'tr': 'Neyin geri yükleneceğini seçin'},
 ' API secrets will also be replaced.': {'ar': ' سيتم أيضًا استبدال أسرار API.',
                                         'de': ' API-Geheimnisse werden ebenfalls ersetzt.',
                                         'fr': ' Les secrets API seront également remplacés.',
                                         'tr': ' API gizli bilgileri de değiştirilecek.'},
 'Confirm Restore': {'ar': 'تأكيد الاستعادة', 'de': 'Wiederherstellung bestätigen', 'fr': 'Confirmer la restauration', 'tr': 'Geri Yüklemeyi Onayla'},
 'Restore this backup? A safety backup of the current state will be created first.': {'ar': 'هل تريد استعادة هذه النسخة؟ سيتم أولًا إنشاء نسخة أمان من الحالة الحالية.',
                                                                                      'de': 'Diese Sicherung wiederherstellen? Zuvor wird eine Sicherheitskopie des aktuellen '
                                                                                            'Status erstellt.',
                                                                                      'fr': 'Restaurer cette sauvegarde ? Une sauvegarde de sécurité de l’état actuel sera d’abord '
                                                                                            'créée.',
                                                                                      'tr': 'Bu yedek geri yüklensin mi? Önce mevcut durumun güvenlik yedeği oluşturulacak.'},
 'Restore cancelled before commit': {'ar': 'تم إلغاء الاستعادة قبل تطبيق التغييرات',
                                     'de': 'Wiederherstellung vor dem Übernehmen abgebrochen',
                                     'fr': 'Restauration annulée avant validation',
                                     'tr': 'Geri yükleme uygulanmadan önce iptal edildi'},
 'Restore Complete': {'ar': 'اكتملت الاستعادة', 'de': 'Wiederherstellung abgeschlossen', 'fr': 'Restauration terminée', 'tr': 'Geri Yükleme Tamamlandı'},
 'Restore Failed': {'ar': 'فشلت الاستعادة', 'de': 'Wiederherstellung fehlgeschlagen', 'fr': 'Échec de la restauration', 'tr': 'Geri Yükleme Başarısız'},
 'Restore failed: %s': {'ar': 'فشلت الاستعادة: %s', 'de': 'Wiederherstellung fehlgeschlagen: %s', 'fr': 'Échec de la restauration : %s', 'tr': 'Geri yükleme başarısız: %s'},
 'Support bundle: %s': {'ar': 'حزمة الدعم: %s', 'de': 'Support-Paket: %s', 'fr': 'Paquet de support : %s', 'tr': 'Destek paketi: %s'},
 'Redacted support bundle created:\n%s': {'ar': 'تم إنشاء حزمة دعم منقحة:\n%s',
                                          'de': 'Bereinigtes Support-Paket erstellt:\n%s',
                                          'fr': 'Paquet de support expurgé créé :\n%s',
                                          'tr': 'Hassas verileri ayıklanmış destek paketi oluşturuldu:\n%s'},
 'Support export failed: %s': {'ar': 'فشل تصدير حزمة الدعم: %s',
                               'de': 'Support-Export fehlgeschlagen: %s',
                               'fr': 'Échec de l’export du support : %s',
                               'tr': 'Destek dışa aktarımı başarısız: %s'},
 'TMDB credential saved privately in api_keys.conf': {'ar': 'تم حفظ بيانات TMDB بشكل خاص في api_keys.conf',
                                                      'de': 'TMDB-Zugangsdaten privat in api_keys.conf gespeichert',
                                                      'fr': 'Identifiant TMDB enregistré de façon privée dans api_keys.conf',
                                                      'tr': 'TMDB kimlik bilgisi api_keys.conf içinde özel olarak kaydedildi'},
 'TMDB credential cleared': {'ar': 'تم مسح بيانات TMDB', 'de': 'TMDB-Zugangsdaten gelöscht', 'fr': 'Identifiant TMDB effacé', 'tr': 'TMDB kimlik bilgisi temizlendi'},
 'TMDB credential save failed: %s': {'ar': 'فشل حفظ بيانات TMDB: %s',
                                     'de': 'Speichern der TMDB-Zugangsdaten fehlgeschlagen: %s',
                                     'fr': 'Échec de l’enregistrement de l’identifiant TMDB : %s',
                                     'tr': 'TMDB kimlik bilgisi kaydedilemedi: %s'},
 'SubDL API key saved privately in api_keys.conf': {'ar': 'تم حفظ مفتاح SubDL API بشكل خاص في api_keys.conf',
                                                    'de': 'SubDL-API-Schlüssel privat in api_keys.conf gespeichert',
                                                    'fr': 'Clé API SubDL enregistrée de façon privée dans api_keys.conf',
                                                    'tr': 'SubDL API anahtarı api_keys.conf içinde özel olarak kaydedildi'},
 'SubDL API key cleared': {'ar': 'تم مسح مفتاح SubDL API', 'de': 'SubDL-API-Schlüssel gelöscht', 'fr': 'Clé API SubDL effacée', 'tr': 'SubDL API anahtarı temizlendi'},
 'Online Subtitles': {'ar': 'الترجمات أونلاين', 'de': 'Online-Untertitel', 'fr': 'Sous-titres en ligne', 'tr': 'Çevrimiçi Altyazılar'},
 'SubDL API key save failed: %s': {'ar': 'فشل حفظ مفتاح SubDL API: %s',
                                   'de': 'Speichern des SubDL-API-Schlüssels fehlgeschlagen: %s',
                                   'fr': 'Échec de l’enregistrement de la clé API SubDL : %s',
                                   'tr': 'SubDL API anahtarı kaydedilemedi: %s'},
 'Create backup (portable, no API secrets)': {'ar': 'إنشاء نسخة احتياطية قابلة للنقل (بدون أسرار API)',
                                              'de': 'Sicherung erstellen (portabel, ohne API-Geheimnisse)',
                                              'fr': 'Créer une sauvegarde portable (sans secrets API)',
                                              'tr': 'Taşınabilir yedek oluştur (API gizli bilgileri olmadan)'},
 'Create authenticated backup (this receiver)': {'ar': 'إنشاء نسخة احتياطية موثقة (لهذا الريسيفر)',
                                                 'de': 'Authentifizierte Sicherung erstellen (dieser Receiver)',
                                                 'fr': 'Créer une sauvegarde authentifiée (ce récepteur)',
                                                 'tr': 'Kimlik doğrulamalı yedek oluştur (bu alıcı)'},
 'Create backup including api_keys.conf': {'ar': 'إنشاء نسخة احتياطية تشمل api_keys.conf',
                                           'de': 'Sicherung inklusive api_keys.conf erstellen',
                                           'fr': 'Créer une sauvegarde incluant api_keys.conf',
                                           'tr': 'api_keys.conf içeren yedek oluştur'},
 'Create authenticated backup + api_keys.conf': {'ar': 'إنشاء نسخة احتياطية موثقة + api_keys.conf',
                                                 'de': 'Authentifizierte Sicherung + api_keys.conf erstellen',
                                                 'fr': 'Créer une sauvegarde authentifiée + api_keys.conf',
                                                 'tr': 'Kimlik doğrulamalı yedek + api_keys.conf oluştur'},
 'Restore a backup': {'ar': 'استعادة نسخة احتياطية', 'de': 'Eine Sicherung wiederherstellen', 'fr': 'Restaurer une sauvegarde', 'tr': 'Bir yedeği geri yükle'},
 'This backup will contain api_keys.conf credentials. The ZIP is protected by filesystem permissions but is NOT encrypted. Create it only on trusted storage.': {'ar': 'ستحتوي هذه '
                                                                                                                                                                       'النسخة على '
                                                                                                                                                                       'بيانات '
                                                                                                                                                                       'اعتماد '
                                                                                                                                                                       'api_keys.conf. '
                                                                                                                                                                       'ملف ZIP '
                                                                                                                                                                       'محمي '
                                                                                                                                                                       'بصلاحيات '
                                                                                                                                                                       'نظام '
                                                                                                                                                                       'الملفات '
                                                                                                                                                                       'لكنه غير '
                                                                                                                                                                       'مشفر. '
                                                                                                                                                                       'أنشئه فقط '
                                                                                                                                                                       'على وحدة '
                                                                                                                                                                       'تخزين '
                                                                                                                                                                       'موثوقة.',
                                                                                                                                                                 'de': 'Diese '
                                                                                                                                                                       'Sicherung '
                                                                                                                                                                       'enthält '
                                                                                                                                                                       'die '
                                                                                                                                                                       'Zugangsdaten '
                                                                                                                                                                       'aus '
                                                                                                                                                                       'api_keys.conf. '
                                                                                                                                                                       'Das ZIP '
                                                                                                                                                                       'ist durch '
                                                                                                                                                                       'Dateisystemberechtigungen '
                                                                                                                                                                       'geschützt, '
                                                                                                                                                                       'jedoch '
                                                                                                                                                                       'NICHT '
                                                                                                                                                                       'verschlüsselt. '
                                                                                                                                                                       'Erstellen '
                                                                                                                                                                       'Sie es nur '
                                                                                                                                                                       'auf '
                                                                                                                                                                       'vertrauenswürdigem '
                                                                                                                                                                       'Speicher.',
                                                                                                                                                                 'fr': 'Cette '
                                                                                                                                                                       'sauvegarde '
                                                                                                                                                                       'contiendra '
                                                                                                                                                                       'les '
                                                                                                                                                                       'identifiants '
                                                                                                                                                                       'de '
                                                                                                                                                                       'api_keys.conf. '
                                                                                                                                                                       'Le ZIP est '
                                                                                                                                                                       'protégé '
                                                                                                                                                                       'par les '
                                                                                                                                                                       'permissions '
                                                                                                                                                                       'du système '
                                                                                                                                                                       'de '
                                                                                                                                                                       'fichiers, '
                                                                                                                                                                       'mais n’est '
                                                                                                                                                                       'PAS '
                                                                                                                                                                       'chiffré. '
                                                                                                                                                                       'Créez-le '
                                                                                                                                                                       'uniquement '
                                                                                                                                                                       'sur un '
                                                                                                                                                                       'stockage '
                                                                                                                                                                       'de '
                                                                                                                                                                       'confiance.',
                                                                                                                                                                 'tr': 'Bu yedek '
                                                                                                                                                                       'api_keys.conf '
                                                                                                                                                                       'kimlik '
                                                                                                                                                                       'bilgilerini '
                                                                                                                                                                       'içerir. '
                                                                                                                                                                       'ZIP dosya '
                                                                                                                                                                       'sistemi '
                                                                                                                                                                       'izinleriyle '
                                                                                                                                                                       'korunur '
                                                                                                                                                                       'ancak '
                                                                                                                                                                       'şifreli '
                                                                                                                                                                       'DEĞİLDİR. '
                                                                                                                                                                       'Yalnızca '
                                                                                                                                                                       'güvenilir '
                                                                                                                                                                       'depolamada '
                                                                                                                                                                       'oluşturun.'},
 '%d portal(s) working.  %d failed.\n\nFailed portals were NOT disabled; use RED if you want to disable one.': {'ar': '%d Portal يعمل. فشل %d.\n'
                                                                                                                      '\n'
                                                                                                                      'لم يتم تعطيل الـPortals الفاشلة؛ استخدم الزر الأحمر إذا '
                                                                                                                      'أردت تعطيل أحدها.',
                                                                                                                'de': '%d Portal(e) funktionieren.  %d fehlgeschlagen.\n'
                                                                                                                      '\n'
                                                                                                                      'Fehlgeschlagene Portale wurden NICHT deaktiviert; drücken '
                                                                                                                      'Sie ROT, um eines zu deaktivieren.',
                                                                                                                'fr': '%d portail(s) fonctionnent. %d ont échoué.\n'
                                                                                                                      '\n'
                                                                                                                      'Les portails en échec n’ont PAS été désactivés ; utilisez '
                                                                                                                      'ROUGE pour en désactiver un.',
                                                                                                                'tr': '%d portal çalışıyor. %d başarısız.\n'
                                                                                                                      '\n'
                                                                                                                      'Başarısız portallar devre dışı bırakılmadı; birini kapatmak '
                                                                                                                      'isterseniz KIRMIZI tuşu kullanın.'},
 '%s  •  %s  •  ARROWS Navigate  •  OK Open  •  MENU Portal Manager': {'ar': '%s  •  %s  •  الأسهم للتنقل  •  OK فتح  •  MENU إدارة الـPortals',
                                                                       'de': '%s  •  %s  •  PFEILE Navigieren  •  OK Öffnen  •  MENÜ Portal-Manager',
                                                                       'fr': '%s  •  %s  •  FLÈCHES Naviguer  •  OK Ouvrir  •  MENU Gestion des portails',
                                                                       'tr': '%s  •  %s  •  OKLAR Gezin  •  OK Aç  •  MENU Portal Yöneticisi'},
 'All %d portals are working': {'ar': 'كل الـ%d Portal تعمل', 'de': 'Alle %d Portale funktionieren', 'fr': 'Les %d portails fonctionnent', 'tr': '%d portalın tamamı çalışıyor'},
 'All portals passed the check.': {'ar': 'اجتازت كل الـPortals الفحص.',
                                   'de': 'Alle Portale haben die Prüfung bestanden.',
                                   'fr': 'Tous les portails ont réussi la vérification.',
                                   'tr': 'Tüm portallar kontrolden geçti.'},
 'Bouquet export failed: %s': {'ar': 'فشل تصدير الباقة: %s',
                               'de': 'Bouquet-Export fehlgeschlagen: %s',
                               'fr': 'Échec de l’export du bouquet : %s',
                               'tr': 'Buket dışa aktarılamadı: %s'},
 'Bouquet exported • %d channels • proxy %s': {'ar': 'تم تصدير الباقة • %d قناة • بروكسي %s',
                                               'de': 'Bouquet exportiert • %d Kanäle • Proxy %s',
                                               'fr': 'Bouquet exporté • %d chaînes • proxy %s',
                                               'tr': 'Buket dışa aktarıldı • %d kanal • proxy %s'},
 'Bulk check failed: %s': {'ar': 'فشل الفحص الجماعي: %s',
                           'de': 'Sammelprüfung fehlgeschlagen: %s',
                           'fr': 'Échec de la vérification groupée : %s',
                           'tr': 'Toplu kontrol başarısız: %s'},
 'Check complete  •  %d working / %d failed': {'ar': 'اكتمل الفحص  •  %d يعمل / %d فشل',
                                               'de': 'Prüfung abgeschlossen  •  %d funktionieren / %d fehlgeschlagen',
                                               'fr': 'Vérification terminée  •  %d fonctionnent / %d en échec',
                                               'tr': 'Kontrol tamamlandı  •  %d çalışıyor / %d başarısız'},
 'Checking %d/%d  •  %s  •  %s': {'ar': 'جارٍ الفحص %d/%d  •  %s  •  %s',
                                  'de': 'Wird geprüft %d/%d  •  %s  •  %s',
                                  'fr': 'Vérification %d/%d  •  %s  •  %s',
                                  'tr': 'Kontrol ediliyor %d/%d  •  %s  •  %s'},
 'Checks done; save failed: %s': {'ar': 'اكتمل الفحص؛ فشل الحفظ: %s',
                                  'de': 'Prüfungen abgeschlossen; Speichern fehlgeschlagen: %s',
                                  'fr': 'Vérifications terminées ; échec de l’enregistrement : %s',
                                  'tr': 'Kontroller tamamlandı; kayıt başarısız: %s'},
 'Clear plugin-owned data for this portal?\n\nThis removes its exported bouquet/EPG, matching recording refresh jobs/timers, Favorites, History and Resume data, but keeps the portal itself.': {'ar': 'مسح '
                                                                                                                                                                                                       'بيانات '
                                                                                                                                                                                                       'الإضافة '
                                                                                                                                                                                                       'الخاصة '
                                                                                                                                                                                                       'بهذا '
                                                                                                                                                                                                       'الـPortal؟\n'
                                                                                                                                                                                                       '\n'
                                                                                                                                                                                                       'سيؤدي '
                                                                                                                                                                                                       'ذلك '
                                                                                                                                                                                                       'إلى '
                                                                                                                                                                                                       'حذف '
                                                                                                                                                                                                       'الباقة/EPG '
                                                                                                                                                                                                       'المصدّرين '
                                                                                                                                                                                                       'ومهام/مؤقتات '
                                                                                                                                                                                                       'تحديث '
                                                                                                                                                                                                       'التسجيلات '
                                                                                                                                                                                                       'المطابقة '
                                                                                                                                                                                                       'والمفضلة '
                                                                                                                                                                                                       'والسجل '
                                                                                                                                                                                                       'وبيانات '
                                                                                                                                                                                                       'الاستكمال، '
                                                                                                                                                                                                       'مع '
                                                                                                                                                                                                       'الاحتفاظ '
                                                                                                                                                                                                       'بالـPortal '
                                                                                                                                                                                                       'نفسه.',
                                                                                                                                                                                                 'de': 'Plugin-eigene '
                                                                                                                                                                                                       'Daten '
                                                                                                                                                                                                       'für '
                                                                                                                                                                                                       'dieses '
                                                                                                                                                                                                       'Portal '
                                                                                                                                                                                                       'löschen?\n'
                                                                                                                                                                                                       '\n'
                                                                                                                                                                                                       'Dies '
                                                                                                                                                                                                       'entfernt '
                                                                                                                                                                                                       'das '
                                                                                                                                                                                                       'exportierte '
                                                                                                                                                                                                       'Bouquet/EPG, '
                                                                                                                                                                                                       'zugehörige '
                                                                                                                                                                                                       'Aufnahme-Aktualisierungsaufträge/-Timer, '
                                                                                                                                                                                                       'Favoriten, '
                                                                                                                                                                                                       'Verlauf '
                                                                                                                                                                                                       'und '
                                                                                                                                                                                                       'Fortsetzungsdaten, '
                                                                                                                                                                                                       'behält '
                                                                                                                                                                                                       'aber '
                                                                                                                                                                                                       'das '
                                                                                                                                                                                                       'Portal '
                                                                                                                                                                                                       'selbst.',
                                                                                                                                                                                                 'fr': 'Effacer '
                                                                                                                                                                                                       'les '
                                                                                                                                                                                                       'données '
                                                                                                                                                                                                       'du '
                                                                                                                                                                                                       'plugin '
                                                                                                                                                                                                       'pour '
                                                                                                                                                                                                       'ce '
                                                                                                                                                                                                       'portail '
                                                                                                                                                                                                       '?\n'
                                                                                                                                                                                                       '\n'
                                                                                                                                                                                                       'Cela '
                                                                                                                                                                                                       'supprime '
                                                                                                                                                                                                       'son '
                                                                                                                                                                                                       'bouquet/EPG '
                                                                                                                                                                                                       'exporté, '
                                                                                                                                                                                                       'les '
                                                                                                                                                                                                       'tâches/minuteries '
                                                                                                                                                                                                       'de '
                                                                                                                                                                                                       'rafraîchissement '
                                                                                                                                                                                                       'd’enregistrement '
                                                                                                                                                                                                       'correspondantes, '
                                                                                                                                                                                                       'les '
                                                                                                                                                                                                       'favoris, '
                                                                                                                                                                                                       'l’historique '
                                                                                                                                                                                                       'et '
                                                                                                                                                                                                       'les '
                                                                                                                                                                                                       'données '
                                                                                                                                                                                                       'de '
                                                                                                                                                                                                       'reprise, '
                                                                                                                                                                                                       'tout '
                                                                                                                                                                                                       'en '
                                                                                                                                                                                                       'conservant '
                                                                                                                                                                                                       'le '
                                                                                                                                                                                                       'portail.',
                                                                                                                                                                                                 'tr': 'Bu '
                                                                                                                                                                                                       'portal '
                                                                                                                                                                                                       'için '
                                                                                                                                                                                                       'eklentiye '
                                                                                                                                                                                                       'ait '
                                                                                                                                                                                                       'veriler '
                                                                                                                                                                                                       'temizlensin '
                                                                                                                                                                                                       'mi?\n'
                                                                                                                                                                                                       '\n'
                                                                                                                                                                                                       'Dışa '
                                                                                                                                                                                                       'aktarılan '
                                                                                                                                                                                                       'buket/EPG, '
                                                                                                                                                                                                       'eşleşen '
                                                                                                                                                                                                       'kayıt '
                                                                                                                                                                                                       'yenileme '
                                                                                                                                                                                                       'görevleri/zamanlayıcıları, '
                                                                                                                                                                                                       'Favoriler, '
                                                                                                                                                                                                       'Geçmiş '
                                                                                                                                                                                                       've '
                                                                                                                                                                                                       'Devam '
                                                                                                                                                                                                       'verileri '
                                                                                                                                                                                                       'silinir; '
                                                                                                                                                                                                       'portalın '
                                                                                                                                                                                                       'kendisi '
                                                                                                                                                                                                       'korunur.'},
 'FAILED': {'ar': 'فشل', 'de': 'FEHLGESCHLAGEN', 'fr': 'ÉCHEC', 'tr': 'BAŞARISIZ'},
 'HTTP fallback save failed: %s': {'ar': 'فشل حفظ إعداد الرجوع إلى HTTP: %s',
                                   'de': 'Speichern des HTTP-Fallbacks fehlgeschlagen: %s',
                                   'fr': 'Échec de l’enregistrement du repli HTTP : %s',
                                   'tr': 'HTTP geri dönüş ayarı kaydedilemedi: %s'},
 'Live bouquet exported with dynamic Stalker links.\n\n%d channels\n\nAn EPGImport source was also created. Open EPG-Importer, enable the Ultra Stalker source, then run an import.': {'ar': 'تم '
                                                                                                                                                                                             'تصدير '
                                                                                                                                                                                             'باقة '
                                                                                                                                                                                             'البث '
                                                                                                                                                                                             'المباشر '
                                                                                                                                                                                             'بروابط '
                                                                                                                                                                                             'Stalker '
                                                                                                                                                                                             'ديناميكية.\n'
                                                                                                                                                                                             '\n'
                                                                                                                                                                                             '%d '
                                                                                                                                                                                             'قناة\n'
                                                                                                                                                                                             '\n'
                                                                                                                                                                                             'تم '
                                                                                                                                                                                             'أيضًا '
                                                                                                                                                                                             'إنشاء '
                                                                                                                                                                                             'مصدر '
                                                                                                                                                                                             'EPGImport. '
                                                                                                                                                                                             'افتح '
                                                                                                                                                                                             'EPG-Importer '
                                                                                                                                                                                             'وفعّل '
                                                                                                                                                                                             'مصدر '
                                                                                                                                                                                             'Ultra '
                                                                                                                                                                                             'Stalker '
                                                                                                                                                                                             'ثم '
                                                                                                                                                                                             'شغّل '
                                                                                                                                                                                             'الاستيراد.',
                                                                                                                                                                                       'de': 'Live-Bouquet '
                                                                                                                                                                                             'mit '
                                                                                                                                                                                             'dynamischen '
                                                                                                                                                                                             'Stalker-Links '
                                                                                                                                                                                             'exportiert.\n'
                                                                                                                                                                                             '\n'
                                                                                                                                                                                             '%d '
                                                                                                                                                                                             'Kanäle\n'
                                                                                                                                                                                             '\n'
                                                                                                                                                                                             'Es '
                                                                                                                                                                                             'wurde '
                                                                                                                                                                                             'außerdem '
                                                                                                                                                                                             'eine '
                                                                                                                                                                                             'EPGImport-Quelle '
                                                                                                                                                                                             'erstellt. '
                                                                                                                                                                                             'Öffnen '
                                                                                                                                                                                             'Sie '
                                                                                                                                                                                             'den '
                                                                                                                                                                                             'EPG-Importer, '
                                                                                                                                                                                             'aktivieren '
                                                                                                                                                                                             'Sie '
                                                                                                                                                                                             'die '
                                                                                                                                                                                             'Ultra-Stalker-Quelle '
                                                                                                                                                                                             'und '
                                                                                                                                                                                             'starten '
                                                                                                                                                                                             'Sie '
                                                                                                                                                                                             'einen '
                                                                                                                                                                                             'Import.',
                                                                                                                                                                                       'fr': 'Bouquet '
                                                                                                                                                                                             'Live '
                                                                                                                                                                                             'exporté '
                                                                                                                                                                                             'avec '
                                                                                                                                                                                             'des '
                                                                                                                                                                                             'liens '
                                                                                                                                                                                             'Stalker '
                                                                                                                                                                                             'dynamiques.\n'
                                                                                                                                                                                             '\n'
                                                                                                                                                                                             '%d '
                                                                                                                                                                                             'chaînes\n'
                                                                                                                                                                                             '\n'
                                                                                                                                                                                             'Une '
                                                                                                                                                                                             'source '
                                                                                                                                                                                             'EPGImport '
                                                                                                                                                                                             'a '
                                                                                                                                                                                             'également '
                                                                                                                                                                                             'été '
                                                                                                                                                                                             'créée. '
                                                                                                                                                                                             'Ouvrez '
                                                                                                                                                                                             'EPG-Importer, '
                                                                                                                                                                                             'activez '
                                                                                                                                                                                             'la '
                                                                                                                                                                                             'source '
                                                                                                                                                                                             'Ultra '
                                                                                                                                                                                             'Stalker, '
                                                                                                                                                                                             'puis '
                                                                                                                                                                                             'lancez '
                                                                                                                                                                                             'l’import.',
                                                                                                                                                                                       'tr': 'Canlı '
                                                                                                                                                                                             'buket '
                                                                                                                                                                                             'dinamik '
                                                                                                                                                                                             'Stalker '
                                                                                                                                                                                             'bağlantılarıyla '
                                                                                                                                                                                             'dışa '
                                                                                                                                                                                             'aktarıldı.\n'
                                                                                                                                                                                             '\n'
                                                                                                                                                                                             '%d '
                                                                                                                                                                                             'kanal\n'
                                                                                                                                                                                             '\n'
                                                                                                                                                                                             'Bir '
                                                                                                                                                                                             'EPGImport '
                                                                                                                                                                                             'kaynağı '
                                                                                                                                                                                             'da '
                                                                                                                                                                                             'oluşturuldu. '
                                                                                                                                                                                             'EPG-Importer’ı '
                                                                                                                                                                                             'açın, '
                                                                                                                                                                                             'Ultra '
                                                                                                                                                                                             'Stalker '
                                                                                                                                                                                             'kaynağını '
                                                                                                                                                                                             'etkinleştirin '
                                                                                                                                                                                             've '
                                                                                                                                                                                             'içe '
                                                                                                                                                                                             'aktarmayı '
                                                                                                                                                                                             'başlatın.'},
 'M3U add failed: %s': {'ar': 'فشلت إضافة M3U: %s', 'de': 'M3U-Hinzufügen fehlgeschlagen: %s', 'fr': 'Échec de l’ajout M3U : %s', 'tr': 'M3U eklenemedi: %s'},
 'MAC for duplicate': {'ar': 'MAC للنسخة المكررة', 'de': 'MAC für Duplikat', 'fr': 'MAC pour la duplication', 'tr': 'Kopya için MAC'},
 'MAG profile save failed: %s': {'ar': 'فشل حفظ ملف MAG: %s',
                                 'de': 'Speichern des MAG-Profils fehlgeschlagen: %s',
                                 'fr': 'Échec de l’enregistrement du profil MAG : %s',
                                 'tr': 'MAG profili kaydedilemedi: %s'},
 'MAG profile saved: %s': {'ar': 'تم حفظ ملف MAG: %s', 'de': 'MAG-Profil gespeichert: %s', 'fr': 'Profil MAG enregistré : %s', 'tr': 'MAG profili kaydedildi: %s'},
 'OK': {'ar': 'OK', 'de': 'OK', 'fr': 'OK', 'tr': 'OK'},
 'PORTAL': {'ar': 'PORTAL', 'de': 'PORTAL', 'fr': 'PORTAIL', 'tr': 'PORTAL'},
 'Page %d / %d  •  Source %d / %d  •  %s': {'ar': 'الصفحة %d / %d  •  المصدر %d / %d  •  %s',
                                            'de': 'Seite %d / %d  •  Quelle %d / %d  •  %s',
                                            'fr': 'Page %d / %d  •  Source %d / %d  •  %s',
                                            'tr': 'Sayfa %d / %d  •  Kaynak %d / %d  •  %s'},
 'Permanently delete selected portal?\n\nIt will be removed from saved profiles, all portal import files, cached sessions and plugin-owned data.': {'ar': 'حذف الـPortal المحدد '
                                                                                                                                                          'نهائيًا؟\n'
                                                                                                                                                          '\n'
                                                                                                                                                          'سيتم حذفه من الملفات '
                                                                                                                                                          'المحفوظة وكل ملفات '
                                                                                                                                                          'استيراد الـPortal '
                                                                                                                                                          'والجلسات المخزنة '
                                                                                                                                                          'وبيانات الإضافة.',
                                                                                                                                                    'de': 'Ausgewähltes Portal '
                                                                                                                                                          'endgültig löschen?\n'
                                                                                                                                                          '\n'
                                                                                                                                                          'Es wird aus den '
                                                                                                                                                          'gespeicherten Profilen, '
                                                                                                                                                          'allen '
                                                                                                                                                          'Portal-Importdateien, '
                                                                                                                                                          'zwischengespeicherten '
                                                                                                                                                          'Sitzungen und '
                                                                                                                                                          'plugin-eigenen Daten '
                                                                                                                                                          'entfernt.',
                                                                                                                                                    'fr': 'Supprimer '
                                                                                                                                                          'définitivement le '
                                                                                                                                                          'portail sélectionné ?\n'
                                                                                                                                                          '\n'
                                                                                                                                                          'Il sera supprimé des '
                                                                                                                                                          'profils enregistrés, de '
                                                                                                                                                          'tous les fichiers '
                                                                                                                                                          'd’import de portail, '
                                                                                                                                                          'des sessions en cache '
                                                                                                                                                          'et des données du '
                                                                                                                                                          'plugin.',
                                                                                                                                                    'tr': 'Seçili portal kalıcı '
                                                                                                                                                          'olarak silinsin mi?\n'
                                                                                                                                                          '\n'
                                                                                                                                                          'Kayıtlı profillerden, '
                                                                                                                                                          'tüm portal içe aktarma '
                                                                                                                                                          'dosyalarından, önbellek '
                                                                                                                                                          'oturumlarından ve '
                                                                                                                                                          'eklenti verilerinden '
                                                                                                                                                          'kaldırılır.'},
 'Permanently delete this portal and its plugin-owned data?\n\nThis removes its exported bouquet/EPG, recording refresh jobs, Favorites, History and Resume data. Any matching Stalker recording timers will also be removed.': {'ar': 'حذف '
                                                                                                                                                                                                                                       'هذا '
                                                                                                                                                                                                                                       'الـPortal '
                                                                                                                                                                                                                                       'وبيانات '
                                                                                                                                                                                                                                       'الإضافة '
                                                                                                                                                                                                                                       'الخاصة '
                                                                                                                                                                                                                                       'به '
                                                                                                                                                                                                                                       'نهائيًا؟\n'
                                                                                                                                                                                                                                       '\n'
                                                                                                                                                                                                                                       'سيتم '
                                                                                                                                                                                                                                       'حذف '
                                                                                                                                                                                                                                       'الباقة/EPG '
                                                                                                                                                                                                                                       'المصدّرين '
                                                                                                                                                                                                                                       'ومهام '
                                                                                                                                                                                                                                       'تحديث '
                                                                                                                                                                                                                                       'التسجيل '
                                                                                                                                                                                                                                       'والمفضلة '
                                                                                                                                                                                                                                       'والسجل '
                                                                                                                                                                                                                                       'وبيانات '
                                                                                                                                                                                                                                       'الاستكمال. '
                                                                                                                                                                                                                                       'كما '
                                                                                                                                                                                                                                       'ستُحذف '
                                                                                                                                                                                                                                       'أي '
                                                                                                                                                                                                                                       'مؤقتات '
                                                                                                                                                                                                                                       'تسجيل '
                                                                                                                                                                                                                                       'Stalker '
                                                                                                                                                                                                                                       'مطابقة.',
                                                                                                                                                                                                                                 'de': 'Dieses '
                                                                                                                                                                                                                                       'Portal '
                                                                                                                                                                                                                                       'und '
                                                                                                                                                                                                                                       'seine '
                                                                                                                                                                                                                                       'plugin-eigenen '
                                                                                                                                                                                                                                       'Daten '
                                                                                                                                                                                                                                       'endgültig '
                                                                                                                                                                                                                                       'löschen?\n'
                                                                                                                                                                                                                                       '\n'
                                                                                                                                                                                                                                       'Dies '
                                                                                                                                                                                                                                       'entfernt '
                                                                                                                                                                                                                                       'das '
                                                                                                                                                                                                                                       'exportierte '
                                                                                                                                                                                                                                       'Bouquet/EPG, '
                                                                                                                                                                                                                                       'Aufnahme-Aktualisierungsaufträge, '
                                                                                                                                                                                                                                       'Favoriten, '
                                                                                                                                                                                                                                       'Verlauf '
                                                                                                                                                                                                                                       'und '
                                                                                                                                                                                                                                       'Fortsetzungsdaten. '
                                                                                                                                                                                                                                       'Zugehörige '
                                                                                                                                                                                                                                       'Stalker-Aufnahme-Timer '
                                                                                                                                                                                                                                       'werden '
                                                                                                                                                                                                                                       'ebenfalls '
                                                                                                                                                                                                                                       'entfernt.',
                                                                                                                                                                                                                                 'fr': 'Supprimer '
                                                                                                                                                                                                                                       'définitivement '
                                                                                                                                                                                                                                       'ce '
                                                                                                                                                                                                                                       'portail '
                                                                                                                                                                                                                                       'et '
                                                                                                                                                                                                                                       'ses '
                                                                                                                                                                                                                                       'données '
                                                                                                                                                                                                                                       'du '
                                                                                                                                                                                                                                       'plugin '
                                                                                                                                                                                                                                       '?\n'
                                                                                                                                                                                                                                       '\n'
                                                                                                                                                                                                                                       'Cela '
                                                                                                                                                                                                                                       'supprime '
                                                                                                                                                                                                                                       'son '
                                                                                                                                                                                                                                       'bouquet/EPG '
                                                                                                                                                                                                                                       'exporté, '
                                                                                                                                                                                                                                       'les '
                                                                                                                                                                                                                                       'tâches '
                                                                                                                                                                                                                                       'de '
                                                                                                                                                                                                                                       'rafraîchissement '
                                                                                                                                                                                                                                       'd’enregistrement, '
                                                                                                                                                                                                                                       'les '
                                                                                                                                                                                                                                       'favoris, '
                                                                                                                                                                                                                                       'l’historique '
                                                                                                                                                                                                                                       'et '
                                                                                                                                                                                                                                       'les '
                                                                                                                                                                                                                                       'données '
                                                                                                                                                                                                                                       'de '
                                                                                                                                                                                                                                       'reprise. '
                                                                                                                                                                                                                                       'Les '
                                                                                                                                                                                                                                       'minuteries '
                                                                                                                                                                                                                                       'd’enregistrement '
                                                                                                                                                                                                                                       'Stalker '
                                                                                                                                                                                                                                       'correspondantes '
                                                                                                                                                                                                                                       'seront '
                                                                                                                                                                                                                                       'également '
                                                                                                                                                                                                                                       'supprimées.',
                                                                                                                                                                                                                                 'tr': 'Bu '
                                                                                                                                                                                                                                       'portal '
                                                                                                                                                                                                                                       've '
                                                                                                                                                                                                                                       'eklentiye '
                                                                                                                                                                                                                                       'ait '
                                                                                                                                                                                                                                       'verileri '
                                                                                                                                                                                                                                       'kalıcı '
                                                                                                                                                                                                                                       'olarak '
                                                                                                                                                                                                                                       'silinsin '
                                                                                                                                                                                                                                       'mi?\n'
                                                                                                                                                                                                                                       '\n'
                                                                                                                                                                                                                                       'Dışa '
                                                                                                                                                                                                                                       'aktarılan '
                                                                                                                                                                                                                                       'buket/EPG, '
                                                                                                                                                                                                                                       'kayıt '
                                                                                                                                                                                                                                       'yenileme '
                                                                                                                                                                                                                                       'görevleri, '
                                                                                                                                                                                                                                       'Favoriler, '
                                                                                                                                                                                                                                       'Geçmiş '
                                                                                                                                                                                                                                       've '
                                                                                                                                                                                                                                       'Devam '
                                                                                                                                                                                                                                       'verileri '
                                                                                                                                                                                                                                       'silinir. '
                                                                                                                                                                                                                                       'Eşleşen '
                                                                                                                                                                                                                                       'Stalker '
                                                                                                                                                                                                                                       'kayıt '
                                                                                                                                                                                                                                       'zamanlayıcıları '
                                                                                                                                                                                                                                       'da '
                                                                                                                                                                                                                                       'kaldırılır.'},
 'Playlist was written but did not survive profile reload': {'ar': 'تمت كتابة قائمة التشغيل لكنها لم تبقَ بعد إعادة تحميل الملفات',
                                                             'de': 'Die Playlist wurde geschrieben, überstand aber das Neuladen der Profile nicht',
                                                             'fr': 'La playlist a été écrite mais n’a pas persisté après le rechargement des profils',
                                                             'tr': 'Oynatma listesi yazıldı ancak profil yeniden yüklemesinden sonra kalmadı'},
 'Portal / M3U URL': {'ar': 'رابط Portal / M3U', 'de': 'Portal-/M3U-URL', 'fr': 'URL Portal / M3U', 'tr': 'Portal / M3U URL'},
 'Portal delete failed': {'ar': 'فشل حذف الـPortal', 'de': 'Portal-Löschung fehlgeschlagen', 'fr': 'Échec de la suppression du portail', 'tr': 'Portal silinemedi'},
 'Re-enable portal': {'ar': 'إعادة تفعيل Portal', 'de': 'Portal wieder aktivieren', 'fr': 'Réactiver un portail', 'tr': 'Portalı yeniden etkinleştir'},
 'Remove export failed: %s': {'ar': 'فشل حذف التصدير: %s',
                              'de': 'Entfernen des Exports fehlgeschlagen: %s',
                              'fr': 'Échec de la suppression de l’export : %s',
                              'tr': 'Dışa aktarım kaldırılamadı: %s'},
 'Rename failed: %s': {'ar': 'فشل تغيير الاسم: %s', 'de': 'Umbenennen fehlgeschlagen: %s', 'fr': 'Échec du renommage : %s', 'tr': 'Yeniden adlandırma başarısız: %s'},
 'Save failed: %s': {'ar': 'فشل الحفظ: %s', 'de': 'Speichern fehlgeschlagen: %s', 'fr': 'Échec de l’enregistrement : %s', 'tr': 'Kayıt başarısız: %s'},
 'Source probe unavailable': {'ar': 'فحص نوع المصدر غير متاح',
                              'de': 'Quellenprüfung nicht verfügbar',
                              'fr': 'Détection du type de source indisponible',
                              'tr': 'Kaynak türü algılama kullanılamıyor'},
 'TLS mode save failed: %s': {'ar': 'فشل حفظ وضع TLS: %s',
                              'de': 'Speichern des TLS-Modus fehlgeschlagen: %s',
                              'fr': 'Échec de l’enregistrement du mode TLS : %s',
                              'tr': 'TLS modu kaydedilemedi: %s'},
 'The files were written but the dynamic bouquet proxy could not start. Remove/re-export the bouquet after resolving the port conflict.': {'ar': 'تمت كتابة الملفات لكن تعذر تشغيل '
                                                                                                                                                 'بروكسي الباقة الديناميكي. احذف '
                                                                                                                                                 'الباقة وأعد تصديرها بعد حل تعارض '
                                                                                                                                                 'المنفذ.',
                                                                                                                                           'de': 'Die Dateien wurden geschrieben, '
                                                                                                                                                 'aber der dynamische '
                                                                                                                                                 'Bouquet-Proxy konnte nicht '
                                                                                                                                                 'gestartet werden. Entfernen Sie '
                                                                                                                                                 'das Bouquet bzw. exportieren Sie '
                                                                                                                                                 'es erneut, nachdem Sie den '
                                                                                                                                                 'Portkonflikt behoben haben.',
                                                                                                                                           'fr': 'Les fichiers ont été écrits, '
                                                                                                                                                 'mais le proxy dynamique du '
                                                                                                                                                 'bouquet n’a pas pu démarrer. '
                                                                                                                                                 'Supprimez puis réexportez le '
                                                                                                                                                 'bouquet après avoir résolu le '
                                                                                                                                                 'conflit de port.',
                                                                                                                                           'tr': 'Dosyalar yazıldı ancak dinamik '
                                                                                                                                                 'buket proxy’si başlatılamadı. '
                                                                                                                                                 'Port çakışmasını çözdükten sonra '
                                                                                                                                                 'buketi kaldırıp yeniden dışa '
                                                                                                                                                 'aktarın.'},
 'Update failed: %s': {'ar': 'فشل التحديث: %s', 'de': 'Aktualisierung fehlgeschlagen: %s', 'fr': 'Échec de la mise à jour : %s', 'tr': 'Güncelleme başarısız: %s'},
 ' • %d already queued': {'ar': ' • %d موجود بالفعل في قائمة الانتظار',
                          'de': ' • %d bereits in der Warteschlange',
                          'fr': ' • %d déjà en file d’attente',
                          'tr': ' • %d zaten kuyrukta'},
 '%d%%': {'ar': '%d%%', 'de': '%d%%', 'fr': '%d%%', 'tr': '%d%%'},
 'Audio selection failed: %s': {'ar': 'فشل اختيار المسار الصوتي: %s',
                                'de': 'Audioauswahl fehlgeschlagen: %s',
                                'fr': 'Échec de la sélection de la piste audio : %s',
                                'tr': 'Ses parçası seçilemedi: %s'},
 'Audio tracks': {'ar': 'المسارات الصوتية', 'de': 'Tonspuren', 'fr': 'Pistes audio', 'tr': 'Ses parçaları'},
 'Category %s': {'ar': 'القسم %s', 'de': 'Kategorie %s', 'fr': 'Catégorie %s', 'tr': 'Kategori %s'},
 'Change parental PIN': {'ar': 'تغيير رمز الرقابة الأبوية', 'de': 'Kindersicherungs-PIN ändern', 'fr': 'Modifier le code PIN parental', 'tr': 'Ebeveyn PIN’ini değiştir'},
 'Change the default parental PIN (0000) before enabling Parental Lock.': {'ar': 'غيّر رمز الرقابة الأبوية الافتراضي (0000) قبل تفعيل القفل الأبوي.',
                                                                           'de': 'Ändern Sie die Standard-Kindersicherungs-PIN (0000), bevor Sie die Kindersicherung aktivieren.',
                                                                           'fr': 'Modifiez le code PIN parental par défaut (0000) avant d’activer le verrouillage parental.',
                                                                           'tr': 'Ebeveyn Kilidini etkinleştirmeden önce varsayılan ebeveyn PIN’ini (0000) değiştirin.'},
 'Channel layout saved: %s': {'ar': 'تم حفظ تخطيط قائمة القنوات: %s',
                              'de': 'Kanal-Layout gespeichert: %s',
                              'fr': 'Disposition de la liste des chaînes enregistrée : %s',
                              'tr': 'Kanal listesi düzeni kaydedildi: %s'},
 'Channel list  •  %s': {'ar': 'قائمة القنوات  •  %s', 'de': 'Kanalliste  •  %s', 'fr': 'Liste des chaînes  •  %s', 'tr': 'Kanal listesi  •  %s'},
 'Cinematic': {'ar': 'سينمائي', 'de': 'Cinematic', 'fr': 'Cinématique', 'tr': 'Sinematik'},
 'Clean technical prefixes  •  %s': {'ar': 'تنظيف البادئات التقنية  •  %s',
                                     'de': 'Technische Präfixe bereinigen  •  %s',
                                     'fr': 'Nettoyer les préfixes techniques  •  %s',
                                     'tr': 'Teknik önekleri temizle  •  %s'},
 'Clear Continue Watching': {'ar': 'مسح متابعة المشاهدة', 'de': '„Weiterschauen“ leeren', 'fr': 'Effacer Continuer à regarder', 'tr': 'İzlemeye Devam listesini temizle'},
 'Clear Continue Watching for this portal?': {'ar': 'هل تريد مسح قائمة متابعة المشاهدة لهذا الـPortal؟',
                                              'de': '„Weiterschauen“ für dieses Portal leeren?',
                                              'fr': 'Effacer la liste Continuer à regarder pour ce portail ?',
                                              'tr': 'Bu portal için İzlemeye Devam listesi temizlensin mi?'},
 'Clear failed: %s': {'ar': 'فشل المسح: %s', 'de': 'Leeren fehlgeschlagen: %s', 'fr': 'Échec de l’effacement : %s', 'tr': 'Temizleme başarısız: %s'},
 'Clear image cache  •  %d files / %.1f MB': {'ar': 'مسح ذاكرة الصور  •  %d ملف / %.1f م.ب',
                                              'de': 'Bild-Cache leeren  •  %d Dateien / %.1f MB',
                                              'fr': 'Vider le cache d’images  •  %d fichiers / %.1f Mo',
                                              'tr': 'Görsel önbelleğini temizle  •  %d dosya / %.1f MB'},
 'Download': {'ar': 'تنزيل', 'de': 'Herunterladen', 'fr': 'Téléchargement', 'tr': 'İndirme'},
 'Download Episode': {'ar': 'تنزيل الحلقة', 'de': 'Episode herunterladen', 'fr': 'Télécharger l’épisode', 'tr': 'Bölümü indir'},
 'Download Season': {'ar': 'تنزيل الموسم', 'de': 'Staffel herunterladen', 'fr': 'Télécharger la saison', 'tr': 'Sezonu indir'},
 'Downloads Manager': {'ar': 'مدير التنزيلات', 'de': 'Download-Manager', 'fr': 'Gestionnaire des téléchargements', 'tr': 'İndirme Yöneticisi'},
 'EPG failed: %s': {'ar': 'فشل تحميل EPG: %s', 'de': 'EPG fehlgeschlagen: %s', 'fr': 'Échec du chargement EPG : %s', 'tr': 'EPG yüklenemedi: %s'},
 'EPG for selected channel': {'ar': 'EPG للقناة المحددة', 'de': 'EPG für ausgewählten Kanal', 'fr': 'EPG de la chaîne sélectionnée', 'tr': 'Seçili kanalın EPG’si'},
 'EPG window  •  %sh': {'ar': 'نطاق EPG  •  %s س', 'de': 'EPG-Zeitfenster  •  %sh', 'fr': 'Fenêtre EPG  •  %s h', 'tr': 'EPG aralığı  •  %s sa'},
 'EPG • choose a programme': {'ar': 'EPG • اختر برنامجًا', 'de': 'EPG • Sendung auswählen', 'fr': 'EPG • choisir un programme', 'tr': 'EPG • bir program seçin'},
 'First page': {'ar': 'الصفحة الأولى', 'de': 'Erste Seite', 'fr': 'Première page', 'tr': 'İlk sayfa'},
 'Grid options': {'ar': 'خيارات الشبكة', 'de': 'Rasteroptionen', 'fr': 'Options de la grille', 'tr': 'Izgara seçenekleri'},
 'Hide / unhide current category': {'ar': 'إخفاء / إظهار القسم الحالي',
                                    'de': 'Aktuelle Kategorie aus-/einblenden',
                                    'fr': 'Masquer / afficher la catégorie actuelle',
                                    'tr': 'Geçerli kategoriyi gizle / göster'},
 'History update failed: %s': {'ar': 'فشل تحديث سجل المشاهدة: %s',
                               'de': 'Verlaufsaktualisierung fehlgeschlagen: %s',
                               'fr': 'Échec de la mise à jour de l’historique : %s',
                               'tr': 'İzleme geçmişi güncellenemedi: %s'},
 'Incorrect parental PIN': {'ar': 'رمز الرقابة الأبوية غير صحيح', 'de': 'Falsche Kindersicherungs-PIN', 'fr': 'Code PIN parental incorrect', 'tr': 'Ebeveyn PIN’i yanlış'},
 'Interface  •  Nova FHD / 1920×1080': {'ar': 'الواجهة  •  Nova FHD / 1920×1080',
                                        'de': 'Oberfläche  •  Nova FHD / 1920×1080',
                                        'fr': 'Interface  •  Nova FHD / 1920×1080',
                                        'tr': 'Arayüz  •  Nova FHD / 1920×1080'},
 'Live preview  •  %s': {'ar': 'المعاينة المباشرة  •  %s', 'de': 'Live-Vorschau  •  %s', 'fr': 'Aperçu en direct  •  %s', 'tr': 'Canlı önizleme  •  %s'},
 'Live preview disabled': {'ar': 'تم إيقاف المعاينة المباشرة', 'de': 'Live-Vorschau deaktiviert', 'fr': 'Aperçu en direct désactivé', 'tr': 'Canlı önizleme kapatıldı'},
 'Live preview enabled': {'ar': 'تم تفعيل المعاينة المباشرة', 'de': 'Live-Vorschau aktiviert', 'fr': 'Aperçu en direct activé', 'tr': 'Canlı önizleme etkinleştirildi'},
 'Manage hidden categories': {'ar': 'إدارة الأقسام المخفية', 'de': 'Ausgeblendete Kategorien verwalten', 'fr': 'Gérer les catégories masquées', 'tr': 'Gizli kategorileri yönet'},
 'Mark selected unwatched': {'ar': 'تعيين المحدد كغير مُشاهد',
                             'de': 'Auswahl als ungesehen markieren',
                             'fr': 'Marquer la sélection comme non vue',
                             'tr': 'Seçileni izlenmedi olarak işaretle'},
 'Mark selected watched': {'ar': 'تعيين المحدد كمُشاهد', 'de': 'Auswahl als gesehen markieren', 'fr': 'Marquer la sélection comme vue', 'tr': 'Seçileni izlendi olarak işaretle'},
 'Mark unwatched': {'ar': 'تعيين كغير مُشاهد', 'de': 'Als ungesehen markieren', 'fr': 'Marquer comme non vu', 'tr': 'İzlenmedi olarak işaretle'},
 'Mark watched': {'ar': 'تعيين كمُشاهد', 'de': 'Als gesehen markieren', 'fr': 'Marquer comme vu', 'tr': 'İzlendi olarak işaretle'},
 'Mark watched / unwatched': {'ar': 'تبديل حالة مُشاهد / غير مُشاهد',
                              'de': 'Als gesehen / ungesehen markieren',
                              'fr': 'Basculer vu / non vu',
                              'tr': 'İzlendi / izlenmedi durumunu değiştir'},
 'No EPG data returned': {'ar': 'لا توجد بيانات EPG متاحة', 'de': 'Keine EPG-Daten zurückgegeben', 'fr': 'Aucune donnée EPG disponible', 'tr': 'EPG verisi bulunamadı'},
 'Nova FHD is the only interface in this build. All plugin screens use the same 1920×1080 design system.': {'ar': 'Nova FHD هي الواجهة الوحيدة في هذا الإصدار. جميع شاشات البلجن '
                                                                                                                  'تستخدم نفس نظام التصميم بدقة 1920×1080.',
                                                                                                            'de': 'Nova FHD ist die einzige Oberfläche in diesem Build. Alle '
                                                                                                                  'Plugin-Bildschirme nutzen dasselbe 1920×1080-Designsystem.',
                                                                                                            'fr': 'Nova FHD est la seule interface de cette version. Tous les '
                                                                                                                  'écrans du plugin utilisent le même système de design en '
                                                                                                                  '1920×1080.',
                                                                                                            'tr': 'Bu sürümdeki tek arayüz Nova FHD’dir. Tüm eklenti ekranları '
                                                                                                                  'aynı 1920×1080 tasarım sistemini kullanır.'},
 'Parental lock  •  %s': {'ar': 'القفل الأبوي  •  %s', 'de': 'Kindersicherung  •  %s', 'fr': 'Verrouillage parental  •  %s', 'tr': 'Ebeveyn kilidi  •  %s'},
 'Parental lock disabled': {'ar': 'تم إيقاف القفل الأبوي', 'de': 'Kindersicherung deaktiviert', 'fr': 'Verrouillage parental désactivé', 'tr': 'Ebeveyn kilidi kapatıldı'},
 'Parental lock enabled': {'ar': 'تم تفعيل القفل الأبوي', 'de': 'Kindersicherung aktiviert', 'fr': 'Verrouillage parental activé', 'tr': 'Ebeveyn kilidi etkinleştirildi'},
 'Pin / unpin current category': {'ar': 'تثبيت / إلغاء تثبيت القسم الحالي',
                                  'de': 'Aktuelle Kategorie anheften / lösen',
                                  'fr': 'Épingler / désépingler la catégorie actuelle',
                                  'tr': 'Geçerli kategoriyi sabitle / sabitlemeyi kaldır'},
 'Playback Engine Lock': {'ar': 'قفل محرك التشغيل', 'de': 'Wiedergabe-Engine-Sperre', 'fr': 'Verrouillage du moteur de lecture', 'tr': 'Oynatma Motoru Kilidi'},
 'Playback engine  •  %s': {'ar': 'محرك التشغيل  •  %s', 'de': 'Wiedergabe-Engine  •  %s', 'fr': 'Moteur de lecture  •  %s', 'tr': 'Oynatma motoru  •  %s'},
 'Playback engine is locked to the value selected under Playback engine. Automatic Smart Engine switching is disabled.': {'ar': 'محرك التشغيل مقفول على القيمة المحددة في إعداد '
                                                                                                                                'محرك التشغيل. التبديل التلقائي عبر Smart Engine '
                                                                                                                                'معطل.',
                                                                                                                          'de': 'Die Wiedergabe-Engine ist auf den unter '
                                                                                                                                '„Wiedergabe-Engine“ gewählten Wert festgelegt. '
                                                                                                                                'Der automatische Smart-Engine-Wechsel ist '
                                                                                                                                'deaktiviert.',
                                                                                                                          'fr': 'Le moteur de lecture est verrouillé sur la valeur '
                                                                                                                                'choisie dans Moteur de lecture. Le changement '
                                                                                                                                'automatique Smart Engine est désactivé.',
                                                                                                                          'tr': 'Oynatma motoru, Oynatma motoru ayarında seçilen '
                                                                                                                                'değere kilitlenmiştir. Otomatik Smart Engine '
                                                                                                                                'geçişi devre dışıdır.'},
 'Playback service type (current: %s)': {'ar': 'نوع خدمة التشغيل (الحالي: %s)',
                                         'de': 'Wiedergabediensttyp (aktuell: %s)',
                                         'fr': 'Type de service de lecture (actuel : %s)',
                                         'tr': 'Oynatma hizmeti türü (mevcut: %s)'},
 'Playback service type saved: %s': {'ar': 'تم حفظ نوع خدمة التشغيل: %s',
                                     'de': 'Wiedergabediensttyp gespeichert: %s',
                                     'fr': 'Type de service de lecture enregistré : %s',
                                     'tr': 'Oynatma hizmeti türü kaydedildi: %s'},
 'Player Engine Lock  •  ON': {'ar': 'قفل محرك المشغل  •  تشغيل',
                               'de': 'Player-Engine-Sperre  •  EIN',
                               'fr': 'Verrouillage du moteur  •  ACTIVÉ',
                               'tr': 'Oynatma Motoru Kilidi  •  AÇIK'},
 'Portal settings': {'ar': 'إعدادات الـPortal', 'de': 'Portal-Einstellungen', 'fr': 'Paramètres du portail', 'tr': 'Portal ayarları'},
 'Portal timeout  •  %ss': {'ar': 'مهلة الـPortal  •  %s ث', 'de': 'Portal-Zeitlimit  •  %ss', 'fr': 'Délai du portail  •  %s s', 'tr': 'Portal zaman aşımı  •  %s sn'},
 'Portal tools': {'ar': 'أدوات الـPortal', 'de': 'Portal-Werkzeuge', 'fr': 'Outils du portail', 'tr': 'Portal araçları'},
 'Poster loading  •  %s': {'ar': 'تحميل البوسترات  •  %s', 'de': 'Poster-Laden  •  %s', 'fr': 'Chargement des affiches  •  %s', 'tr': 'Poster yükleme  •  %s'},
 'Poster loading disabled': {'ar': 'تم إيقاف تحميل البوسترات', 'de': 'Poster-Laden deaktiviert', 'fr': 'Chargement des affiches désactivé', 'tr': 'Poster yükleme kapatıldı'},
 'Poster loading enabled': {'ar': 'تم تفعيل تحميل البوسترات', 'de': 'Poster-Laden aktiviert', 'fr': 'Chargement des affiches activé', 'tr': 'Poster yükleme etkinleştirildi'},
 'Programme details': {'ar': 'تفاصيل البرنامج', 'de': 'Sendungsdetails', 'fr': 'Détails du programme', 'tr': 'Program ayrıntıları'},
 'Protect / unprotect category with PIN': {'ar': 'حماية / إلغاء حماية القسم برمز PIN',
                                           'de': 'Kategorie per PIN schützen / freigeben',
                                           'fr': 'Protéger / déprotéger la catégorie par PIN',
                                           'tr': 'Kategoriyi PIN ile koru / korumayı kaldır'},
 'Quality badges  •  %s': {'ar': 'شارات الجودة  •  %s', 'de': 'Qualitäts-Markierungen  •  %s', 'fr': 'Badges de qualité  •  %s', 'tr': 'Kalite rozetleri  •  %s'},
 'Queued %d download(s)': {'ar': 'تمت إضافة %d تنزيل إلى قائمة الانتظار',
                           'de': '%d Download(s) in Warteschlange',
                           'fr': '%d téléchargement(s) ajouté(s) à la file',
                           'tr': '%d indirme kuyruğa eklendi'},
 'Recently played': {'ar': 'المُشغّل مؤخرًا', 'de': 'Kürzlich abgespielt', 'fr': 'Lus récemment', 'tr': 'Son oynatılanlar'},
 'Record this programme': {'ar': 'تسجيل هذا البرنامج', 'de': 'Diese Sendung aufnehmen', 'fr': 'Enregistrer ce programme', 'tr': 'Bu programı kaydet'},
 'Recording timer added • %s': {'ar': 'تمت إضافة مؤقت التسجيل • %s',
                                'de': 'Aufnahme-Timer hinzugefügt • %s',
                                'fr': 'Programmation d’enregistrement ajoutée • %s',
                                'tr': 'Kayıt zamanlayıcısı eklendi • %s'},
 'Refresh page': {'ar': 'تحديث الصفحة', 'de': 'Seite aktualisieren', 'fr': 'Actualiser la page', 'tr': 'Sayfayı yenile'},
 'Remove selected from Continue Watching': {'ar': 'إزالة المحدد من متابعة المشاهدة',
                                            'de': 'Auswahl aus „Weiterschauen“ entfernen',
                                            'fr': 'Retirer la sélection de Continuer à regarder',
                                            'tr': 'Seçileni İzlemeye Devam’dan kaldır'},
 'Removed from Continue Watching': {'ar': 'تمت الإزالة من متابعة المشاهدة',
                                    'de': 'Aus „Weiterschauen“ entfernt',
                                    'fr': 'Retiré de Continuer à regarder',
                                    'tr': 'İzlemeye Devam’dan kaldırıldı'},
 'Search current list': {'ar': 'البحث في القائمة الحالية', 'de': 'Aktuelle Liste durchsuchen', 'fr': 'Rechercher dans la liste actuelle', 'tr': 'Geçerli listede ara'},
 'Subtitle selection is unavailable: %s': {'ar': 'اختيار الترجمة غير متاح: %s',
                                           'de': 'Untertitelauswahl nicht verfügbar: %s',
                                           'fr': 'La sélection des sous-titres est indisponible : %s',
                                           'tr': 'Altyazı seçimi kullanılamıyor: %s'},
 'Subtitles': {'ar': 'الترجمة', 'de': 'Untertitel', 'fr': 'Sous-titres', 'tr': 'Altyazılar'},
 'Timer failed: %s': {'ar': 'فشل مؤقت التسجيل: %s', 'de': 'Timer fehlgeschlagen: %s', 'fr': 'Échec de la programmation : %s', 'tr': 'Zamanlayıcı başarısız: %s'},
 'Toggle favorite': {'ar': 'إضافة / إزالة من المفضلة', 'de': 'Favorit umschalten', 'fr': 'Ajouter / retirer des favoris', 'tr': 'Favori durumunu değiştir'},
 'Visible home sections': {'ar': 'أقسام الرئيسية الظاهرة', 'de': 'Sichtbare Startseiten-Bereiche', 'fr': 'Sections visibles de l’accueil', 'tr': 'Görünür ana sayfa bölümleri'},
 'hidden': {'ar': 'مخفي', 'de': 'ausgeblendet', 'fr': 'masquée', 'tr': 'gizli'},
 'pinned': {'ar': 'مثبّت', 'de': 'angeheftet', 'fr': 'épinglée', 'tr': 'sabitlendi'},
 'protected': {'ar': 'محمي', 'de': 'geschützt', 'fr': 'protégée', 'tr': 'korumalı'},
 'scheduled': {'ar': 'مجدول', 'de': 'geplant', 'fr': 'programmé', 'tr': 'planlandı'},
 'unpinned': {'ar': 'غير مثبّت', 'de': 'gelöst', 'fr': 'désépinglée', 'tr': 'sabitleme kaldırıldı'},
 'unprotected': {'ar': 'غير محمي', 'de': 'ungeschützt', 'fr': 'non protégée', 'tr': 'korumasız'},
 'visible': {'ar': 'ظاهر', 'de': 'sichtbar', 'fr': 'visible', 'tr': 'görünür'},
 '%s min': {'ar': '%s دقيقة', 'de': '%s Min', 'fr': '%s min', 'tr': '%s dk'},
 'Download Movie': {'ar': 'تنزيل الفيلم', 'de': 'Film herunterladen', 'fr': 'Télécharger le film', 'tr': 'Filmi indir'},
 'Duration': {'ar': 'المدة', 'de': 'Dauer', 'fr': 'Durée', 'tr': 'Süre'},
 'Genre': {'ar': 'التصنيف', 'de': 'Genre', 'fr': 'Genre', 'tr': 'Tür'},
 'Home Hero failed: %s': {'ar': 'فشل تعيين صورة الرئيسية: %s',
                          'de': 'Start-Hero fehlgeschlagen: %s',
                          'fr': 'Échec de la définition du visuel d’accueil : %s',
                          'tr': 'Ana Sayfa görseli ayarlanamadı: %s'},
 'Loading metadata…': {'ar': 'جارٍ تحميل المعلومات…', 'de': 'Metadaten werden geladen…', 'fr': 'Chargement des métadonnées…', 'tr': 'Meta veriler yükleniyor…'},
 'Loading premium assets': {'ar': 'جارٍ تحميل عناصر الواجهة',
                            'de': 'Premium-Inhalte werden geladen',
                            'fr': 'Chargement des éléments de l’interface',
                            'tr': 'Arayüz öğeleri yükleniyor'},
 'MOVIE': {'ar': 'فيلم', 'de': 'FILM', 'fr': 'FILM', 'tr': 'FİLM'},
 'Movie options': {'ar': 'خيارات الفيلم', 'de': 'Filmoptionen', 'fr': 'Options du film', 'tr': 'Film seçenekleri'},
 'Preparing portal services': {'ar': 'جارٍ تجهيز خدمات الـPortal',
                               'de': 'Portal-Dienste werden vorbereitet',
                               'fr': 'Préparation des services du portail',
                               'tr': 'Portal hizmetleri hazırlanıyor'},
 'Rating': {'ar': 'التقييم', 'de': 'Bewertung', 'fr': 'Note', 'tr': 'Puan'},
 'Reload external artwork': {'ar': 'إعادة تحميل الصور الخارجية',
                             'de': 'Externe Bilder neu laden',
                             'fr': 'Recharger les illustrations externes',
                             'tr': 'Harici görselleri yeniden yükle'},
 'Resume %d%%': {'ar': 'استكمال %d%%', 'de': 'Fortsetzen %d%%', 'fr': 'Reprendre à %d%%', 'tr': '%d%% konumundan devam et'},
 'SERIES': {'ar': 'مسلسل', 'de': 'SERIE', 'fr': 'SÉRIE', 'tr': 'DİZİ'},
 'Season': {'ar': 'الموسم', 'de': 'Staffel', 'fr': 'Saison', 'tr': 'Sezon'},
 'Series options': {'ar': 'خيارات المسلسل', 'de': 'Serienoptionen', 'fr': 'Options de la série', 'tr': 'Dizi seçenekleri'},
 'Set as Home Hero': {'ar': 'تعيين كصورة الرئيسية', 'de': 'Als Start-Hero festlegen', 'fr': 'Définir comme visuel d’accueil', 'tr': 'Ana Sayfa görseli yap'},
 'Starting Ultra Stalker': {'ar': 'جارٍ تشغيل Ultra Stalker', 'de': 'Ultra Stalker wird gestartet', 'fr': 'Démarrage d’Ultra Stalker', 'tr': 'Ultra Stalker başlatılıyor'},
 'Unfavorite': {'ar': 'إزالة من المفضلة', 'de': 'Aus Favoriten entfernen', 'fr': 'Retirer des favoris', 'tr': 'Favorilerden çıkar'},
 'Watched': {'ar': 'تمت المشاهدة', 'de': 'Gesehen', 'fr': 'Vu', 'tr': 'İzlendi'},
 'Year': {'ar': 'السنة', 'de': 'Jahr', 'fr': 'Année', 'tr': 'Yıl'},
 'Active threads        %d': {'ar': 'الخيوط النشطة        %d', 'de': 'Aktive Threads        %d', 'fr': 'Threads actifs        %d', 'tr': 'Etkin iş parçacıkları  %d'},
 'Channel list mode      %s': {'ar': 'وضع قائمة القنوات      %s', 'de': 'Kanallistenmodus      %s', 'fr': 'Mode de liste des chaînes %s', 'tr': 'Kanal listesi modu      %s'},
 'Clean titles           %s': {'ar': 'تنظيف العناوين         %s', 'de': 'Titel bereinigen       %s', 'fr': 'Nettoyage des titres    %s', 'tr': 'Başlık temizleme        %s'},
 'Database health       %s': {'ar': 'حالة قاعدة البيانات    %s', 'de': 'Datenbankzustand       %s', 'fr': 'État de la base         %s', 'tr': 'Veritabanı durumu       %s'},
 'Diagnostics refreshed at %s': {'ar': 'تم تحديث التشخيص في %s', 'de': 'Diagnose aktualisiert um %s', 'fr': 'Diagnostics actualisés à %s', 'tr': 'Tanılama %s saatinde yenilendi'},
 'Endurance guard       %s': {'ar': 'حماية الاستمرارية      %s', 'de': 'Ausdauerschutz         %s', 'fr': 'Protection d’endurance  %s', 'tr': 'Dayanıklılık koruması   %s'},
 'Export failed: %s': {'ar': 'فشل التصدير: %s', 'de': 'Export fehlgeschlagen: %s', 'fr': 'Échec de l’export : %s', 'tr': 'Dışa aktarma başarısız: %s'},
 'Exported: %s': {'ar': 'تم التصدير: %s', 'de': 'Exportiert: %s', 'fr': 'Exporté : %s', 'tr': 'Dışa aktarıldı: %s'},
 'HTTP performance      %s req • avg %sms • keepalive %s': {'ar': 'أداء HTTP             %s طلب • المتوسط %s ملث • keepalive %s',
                                                            'de': 'HTTP-Leistung      %s Anfr. • Ø %sms • Keepalive %s',
                                                            'fr': 'Performances HTTP      %s req • moy. %s ms • keepalive %s',
                                                            'tr': 'HTTP performansı       %s istek • ort. %s ms • keepalive %s'},
 'Image cache           %d files  •  %.1f MB': {'ar': 'ذاكرة الصور            %d ملف  •  %.1f م.ب',
                                                'de': 'Bild-Cache           %d Dateien  •  %.1f MB',
                                                'fr': 'Cache d’images          %d fichiers  •  %.1f Mo',
                                                'tr': 'Görsel önbelleği       %d dosya  •  %.1f MB'},
 'Last portal error     %s': {'ar': 'آخر خطأ Portal         %s', 'de': 'Letzter Portal-Fehler     %s', 'fr': 'Dernière erreur portail %s', 'tr': 'Son portal hatası       %s'},
 'Last portal success   %s': {'ar': 'آخر نجاح Portal        %s', 'de': 'Letzter Portal-Erfolg   %s', 'fr': 'Dernier succès portail  %s', 'tr': 'Son portal başarısı     %s'},
 'Never': {'ar': 'أبدًا', 'de': 'Nie', 'fr': 'Jamais', 'tr': 'Hiçbir zaman'},
 'None': {'ar': 'لا يوجد', 'de': 'Keine', 'fr': 'Aucun', 'tr': 'Yok'},
 'Persistent cache      %d files  •  %.1f MB  •  %s': {'ar': 'الذاكرة الدائمة        %d ملف  •  %.1f م.ب  •  %s',
                                                       'de': 'Dauerhafter Cache      %d Dateien  •  %.1f MB  •  %s',
                                                       'fr': 'Cache persistant       %d fichiers  •  %.1f Mo  •  %s',
                                                       'tr': 'Kalıcı önbellek        %d dosya  •  %.1f MB  •  %s'},
 'Platform              %s': {'ar': 'المنصة                 %s', 'de': 'Plattform              %s', 'fr': 'Plateforme             %s', 'tr': 'Platform               %s'},
 'Playback preference   %s': {'ar': 'تفضيل التشغيل          %s', 'de': 'Wiedergabeeinstellung   %s', 'fr': 'Préférence de lecture  %s', 'tr': 'Oynatma tercihi        %s'},
 'Plugin version        %s  •  %s': {'ar': 'إصدار البلجن           %s  •  %s',
                                     'de': 'Plugin-Version        %s  •  %s',
                                     'fr': 'Version du plugin      %s  •  %s',
                                     'tr': 'Eklenti sürümü         %s  •  %s'},
 'Portal selected       %s': {'ar': 'الـPortal المحدد       %s', 'de': 'Portal ausgewählt       %s', 'fr': 'Portail sélectionné     %s', 'tr': 'Seçili portal           %s'},
 'Process health        %s': {'ar': 'حالة العملية           %s', 'de': 'Prozesszustand        %s', 'fr': 'État du processus       %s', 'tr': 'İşlem durumu            %s'},
 'Python                %s': {'ar': 'Python                %s', 'de': 'Python                %s', 'fr': 'Python                %s', 'tr': 'Python                %s'},
 'Python support        %s': {'ar': 'دعم Python             %s', 'de': 'Python-Unterstützung        %s', 'fr': 'Prise en charge Python %s', 'tr': 'Python desteği          %s'},
 'SQLite page state     %s runs • avg %sms': {'ar': 'حالة صفحات SQLite      %s تشغيل • المتوسط %s ملث',
                                              'de': 'SQLite-Seitenstatus     %s Durchläufe • Ø %sms',
                                              'fr': 'État des pages SQLite   %s exéc. • moy. %s ms',
                                              'tr': 'SQLite sayfa durumu     %s çalışma • ort. %s ms'},
 'Saved backups          %d': {'ar': 'النسخ الاحتياطية       %d',
                               'de': 'Gespeicherte Sicherungen          %d',
                               'fr': 'Sauvegardes enregistrées %d',
                               'tr': 'Kayıtlı yedekler        %d'},
 'Search performance    %s runs • avg %sms': {'ar': 'أداء البحث             %s تشغيل • المتوسط %s ملث',
                                              'de': 'Suchleistung    %s Durchläufe • Ø %sms',
                                              'fr': 'Performances recherche %s exéc. • moy. %s ms',
                                              'tr': 'Arama performansı       %s çalışma • ort. %s ms'},
 'Smart engine memories  %s': {'ar': 'ذاكرة Smart Engine      %s', 'de': 'Smart-Engine-Speicher  %s', 'fr': 'Mémoires Smart Engine   %s', 'tr': 'Smart Engine kayıtları  %s'},
 'Smart recovery         %s / retries %s': {'ar': 'Smart Recovery          %s / المحاولات %s',
                                            'de': 'Intelligente Wiederherstellung         %s / Versuche %s',
                                            'fr': 'Smart Recovery          %s / tentatives %s',
                                            'tr': 'Smart Recovery          %s / deneme %s'},
 'TLS security          %s': {'ar': 'أمان TLS               %s', 'de': 'TLS-Sicherheit          %s', 'fr': 'Sécurité TLS            %s', 'tr': 'TLS güvenliği           %s'},
 'Theme                 %s': {'ar': 'المظهر                 %s', 'de': 'Design                 %s', 'fr': 'Thème                  %s', 'tr': 'Tema                   %s'},
 'UNVERIFIED / COMPATIBLE': {'ar': 'غير موثّق / متوافق', 'de': 'UNGEPRÜFT / KOMPATIBEL', 'fr': 'NON VÉRIFIÉ / COMPATIBLE', 'tr': 'DOĞRULANMAMIŞ / UYUMLU'},
 'VERIFIED / STRICT-AUTO': {'ar': 'موثّق / STRICT-AUTO', 'de': 'GEPRÜFT / STRICT-AUTO', 'fr': 'VÉRIFIÉ / STRICT-AUTO', 'tr': 'DOĞRULANMIŞ / STRICT-AUTO'},
 'unknown': {'ar': 'غير معروف', 'de': 'unbekannt', 'fr': 'inconnu', 'tr': 'bilinmiyor'},
 '%d selected  •  OK Select  •  GREEN Import Selected  •  YELLOW Select All  •  RED Clear  •  BACK Portal Manager': {'ar': '%d محدد  •  OK اختيار  •  الأخضر استيراد المحدد  •  '
                                                                                                                           'الأصفر تحديد الكل  •  الأحمر مسح  •  BACK إدارة '
                                                                                                                           'البوابات',
                                                                                                                     'de': '%d ausgewählt  •  OK Auswählen  •  GRÜN Auswahl '
                                                                                                                           'importieren  •  GELB Alle auswählen  •  ROT Leeren  •  '
                                                                                                                           'ZURÜCK Portal-Manager',
                                                                                                                     'fr': '%d sélectionné(s)  •  OK Sélectionner  •  VERT '
                                                                                                                           'Importer  •  JAUNE Tout sélectionner  •  ROUGE '
                                                                                                                           'Effacer  •  BACK Gestionnaire de portails',
                                                                                                                     'tr': '%d seçili  •  OK Seç  •  YEŞİL Seçileni içe aktar  •  '
                                                                                                                           'SARI Tümünü seç  •  KIRMIZI Temizle  •  BACK Portal '
                                                                                                                           'Yöneticisi'},
 '%s  •  Page %d / %d  •  Source %d / %d': {'ar': '%s  •  الصفحة %d / %d  •  المصدر %d / %d',
                                            'de': '%s  •  Seite %d / %d  •  Quelle %d / %d',
                                            'fr': '%s  •  Page %d / %d  •  Source %d / %d',
                                            'tr': '%s  •  Sayfa %d / %d  •  Kaynak %d / %d'},
 '%s • DOWN recent • YELLOW new hero • OK open • BACK portals': {'ar': '%s • DOWN الأحدث • YELLOW صورة رئيسية جديدة • OK فتح • BACK البوابات',
                                                                 'de': '%s • RUNTER Kürzlich • GELB Neuer Hero • OK Öffnen • ZURÜCK Portale',
                                                                 'fr': '%s • BAS récents • JAUNE nouveau visuel • OK ouvrir • BACK portails',
                                                                 'tr': '%s • AŞAĞI son içerikler • SARI yeni ana görsel • OK aç • BACK portallar'},
 '%s • LEFT / RIGHT switch • UP menu • OK resume': {'ar': '%s • LEFT / RIGHT تبديل • UP القائمة • OK استكمال',
                                                    'de': '%s • LINKS / RECHTS Wechseln • HOCH Menü • OK Fortsetzen',
                                                    'fr': '%s • GAUCHE / DROITE changer • HAUT menu • OK reprendre',
                                                    'tr': '%s • SOL / SAĞ değiştir • YUKARI menü • OK devam et'},
 '%s • Library is empty': {'ar': '%s • المكتبة فارغة', 'de': '%s • Bibliothek ist leer', 'fr': '%s • La bibliothèque est vide', 'tr': '%s • Kütüphane boş'},
 'Checking %d portals...': {'ar': 'جارٍ فحص %d بوابة...', 'de': '%d Portale werden geprüft...', 'fr': 'Vérification de %d portails...', 'tr': '%d portal kontrol ediliyor...'},
 'Clear portal data failed: %s': {'ar': 'فشل مسح بيانات الـPortal: %s',
                                  'de': 'Löschen der Portaldaten fehlgeschlagen: %s',
                                  'fr': 'Échec de l’effacement des données du portail : %s',
                                  'tr': 'Portal verileri temizlenemedi: %s'},
 'Delete failed: %s': {'ar': 'فشل الحذف: %s', 'de': 'Löschen fehlgeschlagen: %s', 'fr': 'Échec de la suppression : %s', 'tr': 'Silme başarısız: %s'},
 'Disabled %d failed portal(s)': {'ar': 'تم تعطيل %d بوابة فاشلة',
                                  'de': '%d fehlgeschlagene(s) Portal(e) deaktiviert',
                                  'fr': '%d portail(s) en échec désactivé(s)',
                                  'tr': '%d başarısız portal devre dışı bırakıldı'},
 'Disabling failed portals failed: %s': {'ar': 'فشل تعطيل البوابات غير العاملة: %s',
                                         'de': 'Deaktivieren fehlgeschlagener Portale fehlgeschlagen: %s',
                                         'fr': 'Échec de la désactivation des portails en échec : %s',
                                         'tr': 'Başarısız portallar devre dışı bırakılamadı: %s'},
 'Duplicate failed: %s': {'ar': 'فشل إنشاء نسخة: %s', 'de': 'Duplizieren fehlgeschlagen: %s', 'fr': 'Échec de la duplication : %s', 'tr': 'Kopyalama başarısız: %s'},
 'Enable failed: %s': {'ar': 'فشل التفعيل: %s', 'de': 'Aktivieren fehlgeschlagen: %s', 'fr': 'Échec de l’activation : %s', 'tr': 'Etkinleştirme başarısız: %s'},
 'M3U failed: %s': {'ar': 'فشل M3U: %s', 'de': 'M3U fehlgeschlagen: %s', 'fr': 'Échec M3U : %s', 'tr': 'M3U başarısız: %s'},
 'M3U playlist could not be opened:\n\n%s': {'ar': 'تعذر فتح قائمة M3U:\n\n%s',
                                             'de': 'M3U-Playlist konnte nicht geöffnet werden:\n\n%s',
                                             'fr': 'Impossible d’ouvrir la playlist M3U :\n\n%s',
                                             'tr': 'M3U oynatma listesi açılamadı:\n\n%s'},
 'No disabled portals.': {'ar': 'لا توجد بوابات معطلة.', 'de': 'Keine deaktivierten Portale.', 'fr': 'Aucun portail désactivé.', 'tr': 'Devre dışı portal yok.'},
 'No downloads yet': {'ar': 'لا توجد تنزيلات بعد', 'de': 'Noch keine Downloads', 'fr': 'Aucun téléchargement pour le moment', 'tr': 'Henüz indirme yok'},
 'Opening %s...': {'ar': 'جارٍ فتح %s...', 'de': '%s wird geöffnet...', 'fr': 'Ouverture de %s...', 'tr': '%s açılıyor...'},
 'Permanent delete • %d portals selected • OK Confirm • BACK Return': {'ar': 'حذف نهائي • تم تحديد %d بوابة • OK تأكيد • BACK رجوع',
                                                                       'de': 'Endgültig löschen • %d Portale ausgewählt • OK Bestätigen • ZURÜCK Zurück',
                                                                       'fr': 'Suppression définitive • %d portail(s) sélectionné(s) • OK Confirmer • BACK Retour',
                                                                       'tr': 'Kalıcı silme • %d portal seçili • OK Onayla • BACK Geri'},
 'Portal and related plugin data permanently deleted': {'ar': 'تم حذف الـPortal وبيانات البلجن المرتبطة به نهائيًا',
                                                        'de': 'Portal und zugehörige Plugin-Daten endgültig gelöscht',
                                                        'fr': 'Le portail et les données associées du plugin ont été supprimés définitivement',
                                                        'tr': 'Portal ve ilişkili eklenti verileri kalıcı olarak silindi'},
 'Portal data cleared with warnings: %s': {'ar': 'تم مسح بيانات الـPortal مع تحذيرات: %s',
                                           'de': 'Portaldaten mit Warnungen gelöscht: %s',
                                           'fr': 'Données du portail effacées avec avertissements : %s',
                                           'tr': 'Portal verileri uyarılarla temizlendi: %s'},
 'Portal deleted with cleanup warning: %s': {'ar': 'تم حذف الـPortal مع تحذير أثناء التنظيف: %s',
                                             'de': 'Portal mit Bereinigungswarnung gelöscht: %s',
                                             'fr': 'Portail supprimé avec avertissement de nettoyage : %s',
                                             'tr': 'Portal temizleme uyarısıyla silindi: %s'},
 'Portal ready': {'ar': 'الـPortal جاهز', 'de': 'Portal bereit', 'fr': 'Portail prêt', 'tr': 'Portal hazır'},
 'Re-open the movie/episode and choose Download again to refresh its portal link.': {'ar': 'أعد فتح الفيلم/الحلقة واختر تنزيل مرة أخرى لتحديث رابط الـPortal.',
                                                                                     'de': 'Öffnen Sie den Film/die Episode erneut und wählen Sie erneut „Herunterladen“, um den '
                                                                                           'Portal-Link zu erneuern.',
                                                                                     'fr': 'Rouvrez le film/l’épisode puis choisissez à nouveau Télécharger pour actualiser son '
                                                                                           'lien portail.',
                                                                                     'tr': 'Portal bağlantısını yenilemek için filmi/bölümü yeniden açın ve tekrar İndir’i seçin.'},
 'Recovered malformed data safely • %s': {'ar': 'تم إصلاح البيانات غير السليمة بأمان • %s',
                                          'de': 'Fehlerhafte Daten sicher wiederhergestellt • %s',
                                          'fr': 'Données malformées récupérées en toute sécurité • %s',
                                          'tr': 'Bozuk veriler güvenle kurtarıldı • %s'},
 'Select Portals • %d selected • OK Toggle • YELLOW Select all • RED Clear • BLUE Delete selected • BACK Portal Manager': {'ar': 'تحديد البوابات • %d محدد • OK تبديل • الأصفر '
                                                                                                                                 'تحديد الكل • الأحمر مسح • الأزرق حذف المحدد • '
                                                                                                                                 'BACK إدارة البوابات',
                                                                                                                           'de': 'Portale auswählen • %d ausgewählt • OK '
                                                                                                                                 'Umschalten • GELB Alle auswählen • ROT Leeren • '
                                                                                                                                 'BLAU Auswahl löschen • ZURÜCK Portal-Manager',
                                                                                                                           'fr': 'Sélectionner les portails • %d sélectionné(s) • '
                                                                                                                                 'OK Basculer • JAUNE Tout sélectionner • ROUGE '
                                                                                                                                 'Effacer • BLEU Supprimer • BACK Gestionnaire',
                                                                                                                           'tr': 'Portalları seç • %d seçili • OK Değiştir • SARI '
                                                                                                                                 'Tümünü seç • KIRMIZI Temizle • MAVİ Seçileni sil '
                                                                                                                                 '• BACK Portal Yöneticisi'},
 'channel': {'ar': 'القناة', 'de': 'Kanal', 'fr': 'la chaîne', 'tr': 'kanal'},
 'saved item': {'ar': 'المحتوى المحفوظ', 'de': 'gespeicherter Eintrag', 'fr': 'l’élément enregistré', 'tr': 'kayıtlı içerik'},
 '%d categories': {'ar': '%d قسم', 'de': '%d Kategorien', 'fr': '%d catégories', 'tr': '%d kategori'},
 '%d channels': {'ar': '%d قناة', 'de': '%d Kanäle', 'fr': '%d chaînes', 'tr': '%d kanal'},
 '%d items': {'ar': '%d عنصر', 'de': '%d Einträge', 'fr': '%d éléments', 'tr': '%d öğe'},
 '%d items • %d/%d': {'ar': '%d عنصر • %d/%d', 'de': '%d Einträge • %d/%d', 'fr': '%d éléments • %d/%d', 'tr': '%d öğe • %d/%d'},
 '%d programmes': {'ar': '%d برنامج', 'de': '%d Sendungen', 'fr': '%d programmes', 'tr': '%d program'},
 '%d results': {'ar': '%d نتيجة', 'de': '%d Ergebnisse', 'fr': '%d résultats', 'tr': '%d sonuç'},
 '%s  /  Categories': {'ar': '%s  /  الأقسام', 'de': '%s  /  Kategorien', 'fr': '%s  /  Catégories', 'tr': '%s  /  Kategoriler'},
 '%s  /  Content': {'ar': '%s  /  المحتوى', 'de': '%s  /  Inhalt', 'fr': '%s  /  Contenu', 'tr': '%s  /  İçerik'},
 '%s  /  Search: %s': {'ar': '%s  /  بحث: %s', 'de': '%s  /  Suche: %s', 'fr': '%s  /  Recherche : %s', 'tr': '%s  /  Arama: %s'},
 'Account info failed: %s': {'ar': 'فشل تحميل معلومات الحساب: %s',
                             'de': 'Kontoinformationen fehlgeschlagen: %s',
                             'fr': 'Échec du chargement des informations du compte : %s',
                             'tr': 'Hesap bilgileri yüklenemedi: %s'},
 'Archive': {'ar': 'الأرشيف', 'de': 'Archiv', 'fr': 'Archive', 'tr': 'Arşiv'},
 'Archive EPG failed: %s': {'ar': 'فشل تحميل EPG للأرشيف: %s',
                            'de': 'Archiv-EPG fehlgeschlagen: %s',
                            'fr': 'Échec du chargement de l’EPG de l’archive : %s',
                            'tr': "Arşiv EPG'si yüklenemedi: %s"},
 'Audio selection is not available in this image': {'ar': 'اختيار مسار الصوت غير متاح في هذه الـImage',
                                                    'de': 'Die Audioauswahl ist in diesem Image nicht verfügbar',
                                                    'fr': 'La sélection de la piste audio n’est pas disponible sur cette image',
                                                    'tr': 'Bu imajda ses parçası seçimi kullanılamıyor'},
 'Average Latency: %s ms': {'ar': 'متوسط زمن الاستجابة: %s مللي ثانية', 'de': 'Durchschnittliche Latenz: %s ms', 'fr': 'Latence moyenne : %s ms', 'tr': 'Ortalama gecikme: %s ms'},
 'CATCH-UP': {'ar': 'مشاهدة مؤجلة', 'de': 'CATCH-UP', 'fr': 'CATCH-UP', 'tr': 'GERİ İZLE'},
 'Catch-up  /  %s': {'ar': 'المشاهدة المؤجلة  /  %s', 'de': 'Catch-up  /  %s', 'fr': 'Catch-up  /  %s', 'tr': 'Geri İzle  /  %s'},
 'Catch-up failed: %s': {'ar': 'فشل تحميل المشاهدة المؤجلة: %s',
                         'de': 'Catch-up fehlgeschlagen: %s',
                         'fr': 'Échec du chargement du Catch-up : %s',
                         'tr': 'Geri izleme yüklenemedi: %s'},
 'Catch-up play failed: %s': {'ar': 'فشل تشغيل المشاهدة المؤجلة: %s',
                              'de': 'Catch-up-Wiedergabe fehlgeschlagen: %s',
                              'fr': 'Échec de la lecture Catch-up : %s',
                              'tr': 'Geri izleme oynatılamadı: %s'},
 'Catch-up programme': {'ar': 'برنامج مؤجل', 'de': 'Catch-up-Sendung', 'fr': 'Programme Catch-up', 'tr': 'Geri izleme programı'},
 'Channel': {'ar': 'قناة', 'de': 'Kanal', 'fr': 'Chaîne', 'tr': 'Kanal'},
 'Hidden categories': {'ar': 'الأقسام المخفية', 'de': 'Ausgeblendete Kategorien', 'fr': 'Catégories masquées', 'tr': 'Gizli kategoriler'},
 'Loading catch-up': {'ar': 'جارٍ تحميل المشاهدة المؤجلة', 'de': 'Catch-up wird geladen', 'fr': 'Chargement du Catch-up', 'tr': 'Geri izleme yükleniyor'},
 'No EPG information': {'ar': 'لا تتوفر معلومات EPG', 'de': 'Keine EPG-Informationen', 'fr': 'Aucune information EPG', 'tr': 'EPG bilgisi yok'},
 'No account data returned': {'ar': 'لم يتم إرجاع بيانات للحساب', 'de': 'Keine Kontodaten zurückgegeben', 'fr': 'Aucune donnée de compte reçue', 'tr': 'Hesap verisi alınamadı'},
 'No archive data': {'ar': 'لا توجد بيانات أرشيف', 'de': 'Keine Archivdaten', 'fr': 'Aucune donnée d’archive', 'tr': 'Arşiv verisi yok'},
 'No archived programmes': {'ar': 'لا توجد برامج مؤرشفة', 'de': 'Keine archivierten Sendungen', 'fr': 'Aucun programme archivé', 'tr': 'Arşivlenmiş program yok'},
 'No catch-up channels': {'ar': 'لا توجد قنوات للمشاهدة المؤجلة', 'de': 'Keine Catch-up-Kanäle', 'fr': 'Aucune chaîne Catch-up', 'tr': 'Geri izleme kanalı yok'},
 'No saved content': {'ar': 'لا يوجد محتوى محفوظ', 'de': 'Keine gespeicherten Inhalte', 'fr': 'Aucun contenu enregistré', 'tr': 'Kaydedilmiş içerik yok'},
 'No search results': {'ar': 'لا توجد نتائج بحث', 'de': 'Keine Suchergebnisse', 'fr': 'Aucun résultat de recherche', 'tr': 'Arama sonucu yok'},
 'Page %d  •  %d items': {'ar': 'صفحة %d  •  %d عنصر', 'de': 'Seite %d  •  %d Einträge', 'fr': 'Page %d  •  %d éléments', 'tr': 'Sayfa %d  •  %d öğe'},
 'Picon cache failed': {'ar': 'فشل تخزين Picons مؤقتًا', 'de': 'Picon-Cache fehlgeschlagen', 'fr': 'Échec de la mise en cache des Picons', 'tr': 'Picon önbelleğe alma başarısız'},
 'Picons %d/%d • %d cached • %d downloaded • %d failed': {'ar': 'Picons %d/%d • %d مخزنة مؤقتًا • %d تم تنزيلها • %d فشلت',
                                                          'de': 'Picons %d/%d • %d zwischengespeichert • %d heruntergeladen • %d fehlgeschlagen',
                                                          'fr': 'Picons %d/%d • %d en cache • %d téléchargés • %d échecs',
                                                          'tr': 'Picon %d/%d • %d önbellekte • %d indirildi • %d başarısız'},
 'Picons ready • %d/%d • %d cached • %d downloaded • %d failed': {'ar': 'Picons جاهزة • %d/%d • %d مخزنة مؤقتًا • %d تم تنزيلها • %d فشلت',
                                                                  'de': 'Picons bereit • %d/%d • %d zwischengespeichert • %d heruntergeladen • %d fehlgeschlagen',
                                                                  'fr': 'Picons prêts • %d/%d • %d en cache • %d téléchargés • %d échecs',
                                                                  'tr': 'Piconlar hazır • %d/%d • %d önbellekte • %d indirildi • %d başarısız'},
 'Portal Health: %s': {'ar': 'حالة البوابة: %s', 'de': 'Portal-Zustand: %s', 'fr': 'État du portail : %s', 'tr': 'Portal durumu: %s'},
 'Programme': {'ar': 'برنامج', 'de': 'Sendung', 'fr': 'Programme', 'tr': 'Program'},
 'RESUME': {'ar': 'استكمال', 'de': 'FORTSETZEN', 'fr': 'REPRENDRE', 'tr': 'DEVAM ET'},
 'Scanning Live categories %d / %d': {'ar': 'جارٍ فحص أقسام البث المباشر %d / %d',
                                      'de': 'Live-Kategorien werden durchsucht %d / %d',
                                      'fr': 'Analyse des catégories Live %d / %d',
                                      'tr': 'Canlı kategoriler taranıyor %d / %d'},
 'Try another channel': {'ar': 'جرّب قناة أخرى', 'de': 'Versuchen Sie einen anderen Kanal', 'fr': 'Essayez une autre chaîne', 'tr': 'Başka bir kanal deneyin'},
 'Use MENU on a category to toggle it.': {'ar': 'استخدم MENU على القسم لإظهاره أو إخفائه.',
                                          'de': 'Verwenden Sie MENÜ auf einer Kategorie, um sie umzuschalten.',
                                          'fr': 'Utilisez MENU sur une catégorie pour l’afficher ou la masquer.',
                                          'tr': 'Bir kategoriyi göstermek veya gizlemek için üzerinde MENU tuşunu kullanın.'},
 'Watch-state update failed: %s': {'ar': 'فشل تحديث حالة المشاهدة: %s',
                                   'de': 'Aktualisierung des Gesehen-Status fehlgeschlagen: %s',
                                   'fr': 'Échec de la mise à jour de l’état de visionnage : %s',
                                   'tr': 'İzleme durumu güncellenemedi: %s'},
 ' • %d portal error(s)': {'ar': ' • %d خطأ في البوابات', 'de': ' • %d Portal-Fehler', 'fr': ' • %d erreur(s) de portail', 'tr': ' • %d portal hatası'},
 '%d results • %d portals': {'ar': '%d نتيجة • %d بوابة', 'de': '%d Ergebnisse • %d Portale', 'fr': '%d résultats • %d portails', 'tr': '%d sonuç • %d portal'},
 '%s • %d%% watched': {'ar': '%s • تمت مشاهدة %d%%', 'de': '%s • %d%% gesehen', 'fr': '%s • %d%% regardé', 'tr': '%s • %d%% izlendi'},
 '%s • Resume': {'ar': '%s • استكمال', 'de': '%s • Fortsetzen', 'fr': '%s • Reprendre', 'tr': '%s • Devam Et'},
 '%s • open': {'ar': '%s • فتح', 'de': '%s • öffnen', 'fr': '%s • ouvrir', 'tr': '%s • aç'},
 'Episode': {'ar': 'حلقة', 'de': 'Episode', 'fr': 'Épisode', 'tr': 'Bölüm'},
 'Expires: %s': {'ar': 'ينتهي: %s', 'de': 'Läuft ab: %s', 'fr': 'Expire : %s', 'tr': 'Bitiş: %s'},
 'Global portal search': {'ar': 'بحث شامل في البوابات', 'de': 'Globale Portal-Suche', 'fr': 'Recherche globale des portails', 'tr': 'Genel portal araması'},
 'LIVE TV': {'ar': 'البث المباشر', 'de': 'LIVE-TV', 'fr': 'TV EN DIRECT', 'tr': 'CANLI TV'},
 'Movie': {'ar': 'فيلم', 'de': 'Film', 'fr': 'Film', 'tr': 'Film'},
 'Open a channel and it will appear here': {'ar': 'افتح قناة وستظهر هنا',
                                            'de': 'Öffnen Sie einen Kanal, dann erscheint er hier',
                                            'fr': 'Ouvrez une chaîne et elle apparaîtra ici',
                                            'tr': 'Bir kanal açın; burada görünecektir'},
 'Portal': {'ar': 'بوابة', 'de': 'Portal', 'fr': 'Portail', 'tr': 'Portal'},
 'Portal Connected': {'ar': 'البوابة متصلة', 'de': 'Portal verbunden', 'fr': 'Portail connecté', 'tr': 'Portal bağlı'},
 'Recent: %s': {'ar': 'الأخيرة: %s', 'de': 'Kürzlich: %s', 'fr': 'Récent : %s', 'tr': 'Son: %s'},
 'Result': {'ar': 'نتيجة', 'de': 'Ergebnis', 'fr': 'Résultat', 'tr': 'Sonuç'},
 'Results will appear as portals finish': {'ar': 'ستظهر النتائج عند انتهاء كل بوابة',
                                           'de': 'Ergebnisse erscheinen, sobald Portale fertig sind',
                                           'fr': 'Les résultats apparaîtront à mesure que les portails terminent',
                                           'tr': 'Portallar tamamlandıkça sonuçlar görünecek'},
 'Search keyboard is unavailable on this image.': {'ar': 'لوحة مفاتيح البحث غير متاحة في هذه الـImage.',
                                                   'de': 'Die Suchtastatur ist in diesem Image nicht verfügbar.',
                                                   'fr': 'Le clavier de recherche n’est pas disponible sur cette image.',
                                                   'tr': 'Bu imajda arama klavyesi kullanılamıyor.'},
 'Searching...': {'ar': 'جارٍ البحث...', 'de': 'Suche läuft...', 'fr': 'Recherche...', 'tr': 'Aranıyor...'},
 'Searching... %d result(s) found': {'ar': 'جارٍ البحث... تم العثور على %d نتيجة',
                                     'de': 'Suche läuft... %d Ergebnis(se) gefunden',
                                     'fr': 'Recherche... %d résultat(s) trouvé(s)',
                                     'tr': 'Aranıyor... %d sonuç bulundu'},
 'Try another term': {'ar': 'جرّب عبارة أخرى', 'de': 'Versuchen Sie einen anderen Begriff', 'fr': 'Essayez un autre terme', 'tr': 'Başka bir terim deneyin'},
 'Watch a movie and it will appear here': {'ar': 'شاهد فيلمًا وسيظهر هنا',
                                           'de': 'Sehen Sie einen Film an, dann erscheint er hier',
                                           'fr': 'Regardez un film et il apparaîtra ici',
                                           'tr': 'Bir film izleyin; burada görünecektir'},
 'Watch a series and it will appear here': {'ar': 'شاهد مسلسلًا وسيظهر هنا',
                                            'de': 'Sehen Sie eine Serie an, dann erscheint sie hier',
                                            'fr': 'Regardez une série et elle apparaîtra ici',
                                            'tr': 'Bir dizi izleyin; burada görünecektir'},
 'Watched • play again': {'ar': 'تمت المشاهدة • تشغيل مرة أخرى', 'de': 'Gesehen • erneut abspielen', 'fr': 'Vu • relire', 'tr': 'İzlendi • tekrar oynat'},
 'Delete %d selected portals': {'ar': 'حذف %d بوابة محددة', 'de': '%d ausgewählte Portale löschen', 'fr': 'Supprimer %d portails sélectionnés', 'tr': 'Seçili %d portalı sil'},
 'PERMANENT': {'ar': 'نهائي', 'de': 'ENDGÜLTIG', 'fr': 'DÉFINITIF', 'tr': 'KALICI'},
 'Keep all portals': {'ar': 'الاحتفاظ بكل البوابات', 'de': 'Alle Portale behalten', 'fr': 'Conserver tous les portails', 'tr': 'Tüm portalları koru'},
 'UP / DOWN  Read description     •     YELLOW / OK / BACK  Close': {'ar': 'UP / DOWN قراءة الوصف     •     YELLOW / OK / BACK إغلاق',
                                                                     'de': 'HOCH / RUNTER  Beschreibung lesen     •     GELB / OK / ZURÜCK  Schließen',
                                                                     'fr': 'UP / DOWN Lire la description     •     YELLOW / OK / BACK Fermer',
                                                                     'tr': 'UP / DOWN Açıklamayı oku     •     YELLOW / OK / BACK Kapat'},
 '◀▶  Page': {'ar': '◀▶  صفحة', 'de': '◀▶  Seite', 'fr': '◀▶  Page', 'tr': '◀▶  Sayfa'},
 'OK  Play': {'ar': 'OK  تشغيل', 'de': 'OK  Abspielen', 'fr': 'OK  Lire', 'tr': 'OK  Oynat'},
 'BACK  Close': {'ar': 'BACK  إغلاق', 'de': 'ZURÜCK  Schließen', 'fr': 'BACK  Fermer', 'tr': 'BACK  Kapat'},
 'Play Next Episode': {'ar': 'تشغيل الحلقة التالية', 'de': 'Nächste Episode abspielen', 'fr': 'Lire l’épisode suivant', 'tr': 'Sonraki Bölümü Oynat'},
 'OK Play now   •   BACK Cancel   •   YELLOW Disable autoplay': {'ar': 'OK تشغيل الآن   •   BACK إلغاء   •   YELLOW تعطيل التشغيل التلقائي',
                                                                 'de': 'OK Jetzt abspielen   •   ZURÜCK Abbrechen   •   GELB Autoplay deaktivieren',
                                                                 'fr': 'OK Lire maintenant   •   BACK Annuler   •   YELLOW Désactiver la lecture auto',
                                                                 'tr': 'OK Şimdi oynat   •   BACK İptal   •   YELLOW Otomatik oynatmayı kapat'},
 'Live TV / Movies / Series': {'ar': 'البث المباشر / الأفلام / المسلسلات',
                               'de': 'Live-TV / Filme / Serien',
                               'fr': 'TV en direct / Films / Séries',
                               'tr': 'Canlı TV / Filmler / Diziler'},
 'PREPARING STREAM': {'ar': 'جارٍ تجهيز البث', 'de': 'STREAM WIRD VORBEREITET', 'fr': 'PRÉPARATION DU FLUX', 'tr': 'YAYIN HAZIRLANIYOR'},
 'PLAY': {'ar': 'تشغيل', 'de': 'ABSPIELEN', 'fr': 'LIRE', 'tr': 'OYNAT'},
 'NEXT': {'ar': 'التالي', 'de': 'WEITER', 'fr': 'SUIVANT', 'tr': 'SONRAKİ'},
 'STREAM': {'ar': 'البث', 'de': 'STREAM', 'fr': 'FLUX', 'tr': 'YAYIN'},
 'VIDEO': {'ar': 'فيديو', 'de': 'VIDEO', 'fr': 'VIDÉO', 'tr': 'VİDEO'},
 'AUDIO': {'ar': 'الصوت', 'de': 'AUDIO', 'fr': 'AUDIO', 'tr': 'SES'},
 'SUBTITLES': {'ar': 'الترجمة', 'de': 'UNTERTITEL', 'fr': 'SOUS-TITRES', 'tr': 'ALTYAZILAR'},
 '<< Rewind   >> Forward   0 Restart': {'ar': '<< رجوع   >> تقديم   0 إعادة البدء',
                                        'de': '<< Zurückspulen   >> Vorspulen   0 Neustart',
                                        'fr': '<< Retour   >> Avance   0 Recommencer',
                                        'tr': '<< Geri   >> İleri   0 Baştan'},
 'Exit': {'ar': 'خروج', 'de': 'Beenden', 'fr': 'Quitter', 'tr': 'Çıkış'},
 'Aspect Ratio': {'ar': 'نسبة العرض', 'de': 'Seitenverhältnis', 'fr': 'Format d’image', 'tr': 'En-Boy Oranı'},
 'Engine Locked': {'ar': 'المحرك مثبت', 'de': 'Engine gesperrt', 'fr': 'Moteur verrouillé', 'tr': 'Motor Kilitli'},
 'RESUMING  •  preparing bookmark': {'ar': 'جارٍ الاستكمال  •  تجهيز نقطة الحفظ',
                                     'de': 'WIRD FORTGESETZT  •  Lesezeichen wird vorbereitet',
                                     'fr': 'REPRISE  •  préparation du signet',
                                     'tr': 'DEVAM EDİLİYOR  •  yer imi hazırlanıyor'},
 'PLAYING': {'ar': 'قيد التشغيل', 'de': 'WIEDERGABE', 'fr': 'LECTURE', 'tr': 'OYNATILIYOR'},
 'STREAM INTERRUPTED  •  press 0 to restart': {'ar': 'انقطع البث  •  اضغط 0 لإعادة التشغيل',
                                               'de': 'STREAM UNTERBROCHEN  •  0 drücken für Neustart',
                                               'fr': 'FLUX INTERROMPU  •  appuyez sur 0 pour recommencer',
                                               'tr': "YAYIN KESİLDİ  •  yeniden başlatmak için 0'a basın"},
 'PLAYBACK FAILED': {'ar': 'فشل التشغيل', 'de': 'WIEDERGABE FEHLGESCHLAGEN', 'fr': 'ÉCHEC DE LA LECTURE', 'tr': 'OYNATMA BAŞARISIZ'},
 'SWITCHING CHANNEL...': {'ar': 'جارٍ تغيير القناة...', 'de': 'KANAL WIRD GEWECHSELT...', 'fr': 'CHANGEMENT DE CHAÎNE...', 'tr': 'KANAL DEĞİŞTİRİLİYOR...'},
 'PLAYING  •  resume unavailable': {'ar': 'قيد التشغيل  •  الاستكمال غير متاح',
                                    'de': 'WIEDERGABE  •  Fortsetzen nicht verfügbar',
                                    'fr': 'LECTURE  •  reprise indisponible',
                                    'tr': 'OYNATILIYOR  •  devam kullanılamıyor'},
 'SEARCHING  •  SUBDL ARABIC': {'ar': 'جارٍ البحث  •  SUBDL عربي', 'de': 'SUCHE  •  SUBDL ARABISCH', 'fr': 'RECHERCHE  •  SUBDL ARABE', 'tr': 'ARANIYOR  •  SUBDL ARAPÇA'},
 'PLAYING  •  NO ARABIC SUBTITLE': {'ar': 'قيد التشغيل  •  لا توجد ترجمة عربية',
                                    'de': 'WIEDERGABE  •  KEIN ARABISCHER UNTERTITEL',
                                    'fr': 'LECTURE  •  AUCUN SOUS-TITRE ARABE',
                                    'tr': 'OYNATILIYOR  •  ARAPÇA ALTYAZI YOK'},
 'DOWNLOADING  •  ARABIC SUBTITLE': {'ar': 'جارٍ التنزيل  •  ترجمة عربية',
                                     'de': 'WIRD HERUNTERGELADEN  •  ARABISCHER UNTERTITEL',
                                     'fr': 'TÉLÉCHARGEMENT  •  SOUS-TITRE ARABE',
                                     'tr': 'İNDİRİLİYOR  •  ARAPÇA ALTYAZI'},
 'PLAYING  •  ARABIC SUBTITLES': {'ar': 'قيد التشغيل  •  ترجمة عربية',
                                  'de': 'WIEDERGABE  •  ARABISCHE UNTERTITEL',
                                  'fr': 'LECTURE  •  SOUS-TITRES ARABES',
                                  'tr': 'OYNATILIYOR  •  ARAPÇA ALTYAZILAR'},
 'CHANNEL SWITCH UNAVAILABLE': {'ar': 'تغيير القناة غير متاح',
                                'de': 'KANALWECHSEL NICHT VERFÜGBAR',
                                'fr': 'CHANGEMENT DE CHAÎNE INDISPONIBLE',
                                'tr': 'KANAL DEĞİŞTİRME KULLANILAMIYOR'},
 'CHANNEL SWITCH IN PROGRESS...': {'ar': 'جارٍ تغيير القناة...', 'de': 'KANALWECHSEL LÄUFT...', 'fr': 'CHANGEMENT DE CHAÎNE EN COURS...', 'tr': 'KANAL DEĞİŞTİRİLİYOR...'},
 'CHANNEL SWITCH FAILED': {'ar': 'فشل تغيير القناة', 'de': 'KANALWECHSEL FEHLGESCHLAGEN', 'fr': 'ÉCHEC DU CHANGEMENT DE CHAÎNE', 'tr': 'KANAL DEĞİŞTİRME BAŞARISIZ'},
 'MEMORY PROTECTION  •  playback preserved': {'ar': 'حماية الذاكرة  •  تم الحفاظ على التشغيل',
                                              'de': 'SPEICHERSCHUTZ  •  Wiedergabe erhalten',
                                              'fr': 'PROTECTION MÉMOIRE  •  lecture préservée',
                                              'tr': 'BELLEK KORUMASI  •  oynatma korundu'},
 'MEMORY SAFETY EXIT': {'ar': 'خروج لحماية الذاكرة', 'de': 'SPEICHERSICHERHEITS-BEENDEN', 'fr': 'SORTIE DE SÉCURITÉ MÉMOIRE', 'tr': 'BELLEK GÜVENLİ ÇIKIŞI'},
 'PLAYING  •  SUBTITLES OFF': {'ar': 'قيد التشغيل  •  الترجمة متوقفة',
                               'de': 'WIEDERGABE  •  UNTERTITEL AUS',
                               'fr': 'LECTURE  •  SOUS-TITRES DÉSACTIVÉS',
                               'tr': 'OYNATILIYOR  •  ALTYAZILAR KAPALI'},
 'CHANNEL SWITCH TIMED OUT': {'ar': 'انتهت مهلة تغيير القناة',
                              'de': 'KANALWECHSEL-ZEITÜBERSCHREITUNG',
                              'fr': 'DÉLAI DE CHANGEMENT DE CHAÎNE DÉPASSÉ',
                              'tr': 'KANAL DEĞİŞTİRME ZAMAN AŞIMI'},
 'MEDIA INFO UNAVAILABLE': {'ar': 'معلومات الوسائط غير متاحة', 'de': 'MEDIENINFO NICHT VERFÜGBAR', 'fr': 'INFORMATIONS MÉDIA INDISPONIBLES', 'tr': 'MEDYA BİLGİSİ KULLANILAMIYOR'},
 ' • %d recovered': {'ar': ' • تم استرجاع %d', 'de': ' • %d wiederhergestellt', 'fr': ' • %d récupéré(s)', 'tr': ' • %d kurtarıldı'},
 '%s\n\nNOW: %s\nNEXT: %s': {'ar': '%s\n\nالآن: %s\nالتالي: %s',
                             'de': '%s\n\nJETZT: %s\nDANACH: %s',
                             'fr': '%s\n\nMAINTENANT : %s\nSUIVANT : %s',
                             'tr': '%s\n\nŞİMDİ: %s\nSONRAKİ: %s'},
 'Artwork cache • %d/%d attempted • %d already cached • %d pending retry%s': {'ar': 'كاش الصور • تمت محاولة %d/%d • %d مخزنة بالفعل • %d بانتظار إعادة المحاولة%s',
                                                                              'de': 'Bild-Cache • %d/%d versucht • %d bereits zwischengespeichert • %d warten auf erneuten '
                                                                                    'Versuch%s',
                                                                              'fr': 'Cache des illustrations • %d/%d tentées • %d déjà en cache • %d en attente de nouvel essai%s',
                                                                              'tr': 'Görsel önbelleği • %d/%d denendi • %d zaten önbellekte • %d yeniden denemeyi bekliyor%s'},
 'Artwork ready • %d/%d • %d already cached • 0 pending%s': {'ar': 'الصور جاهزة • %d/%d • %d مخزنة بالفعل • 0 معلقة%s',
                                                             'de': 'Bilder bereit • %d/%d • %d bereits zwischengespeichert • 0 ausstehend%s',
                                                             'fr': 'Illustrations prêtes • %d/%d • %d déjà en cache • 0 en attente%s',
                                                             'tr': 'Görseller hazır • %d/%d • %d zaten önbellekte • 0 beklemede%s'},
 'Caching 0 / %d': {'ar': 'جارٍ التخزين 0 / %d', 'de': 'Wird zwischengespeichert 0 / %d', 'fr': 'Mise en cache 0 / %d', 'tr': 'Önbelleğe alınıyor 0 / %d'},
 'Channel %d': {'ar': 'القناة %d', 'de': 'Kanal %d', 'fr': 'Chaîne %d', 'tr': 'Kanal %d'},
 'EPG information will appear here': {'ar': 'ستظهر معلومات EPG هنا',
                                      'de': 'EPG-Informationen erscheinen hier',
                                      'fr': 'Les informations EPG apparaîtront ici',
                                      'tr': 'EPG bilgisi burada görünecek'},
 'NOW': {'ar': 'الآن', 'de': 'JETZT', 'fr': 'MAINTENANT', 'tr': 'ŞİMDİ'},
 'No EPG': {'ar': 'لا يوجد EPG', 'de': 'Kein EPG', 'fr': 'Aucun EPG', 'tr': 'EPG yok'},
 'No expiry': {'ar': 'بدون تاريخ انتهاء', 'de': 'Kein Ablaufdatum', 'fr': 'Aucune expiration', 'tr': 'Süre sonu yok'},
 'OK Full Screen': {'ar': 'OK ملء الشاشة', 'de': 'OK Vollbild', 'fr': 'OK Plein écran', 'tr': 'OK Tam Ekran'},
 'Search in %s': {'ar': 'بحث في %s', 'de': 'In %s suchen', 'fr': 'Rechercher dans %s', 'tr': '%s içinde ara'},
 ' • IMDb linked': {'ar': ' • مرتبط بـ IMDb', 'de': ' • IMDb verknüpft', 'fr': ' • lié à IMDb', 'tr': ' • IMDb bağlı'},
 'Could not save HTTP consent: %s': {'ar': 'تعذر حفظ موافقة HTTP: %s',
                                     'de': 'HTTP-Zustimmung konnte nicht gespeichert werden: %s',
                                     'fr': 'Impossible d’enregistrer l’autorisation HTTP : %s',
                                     'tr': 'HTTP onayı kaydedilemedi: %s'},
 'Invalid profile: %s': {'ar': 'ملف تعريف غير صالح: %s', 'de': 'Ungültiges Profil: %s', 'fr': 'Profil invalide : %s', 'tr': 'Geçersiz profil: %s'},
 'Portal metadata • HDD': {'ar': 'بيانات البوابة • HDD', 'de': 'Portal-Metadaten • HDD', 'fr': 'Métadonnées du portail • HDD', 'tr': 'Portal meta verisi • HDD'},
 'Retrying %d failed picons only': {'ar': 'إعادة محاولة %d من Picons الفاشلة فقط',
                                    'de': 'Nur %d fehlgeschlagene Picons werden erneut versucht',
                                    'fr': 'Nouvel essai pour les %d Picons en échec uniquement',
                                    'tr': 'Yalnızca başarısız %d Picon yeniden deneniyor'},
 'TMDB • %d%% match': {'ar': 'TMDB • تطابق %d%%', 'de': 'TMDB • %d%% Übereinstimmung', 'fr': 'TMDB • correspondance %d%%', 'tr': 'TMDB • %d%% eşleşme'},
 'UI error: %s': {'ar': 'خطأ في الواجهة: %s', 'de': 'UI-Fehler: %s', 'fr': 'Erreur d’interface : %s', 'tr': 'Arayüz hatası: %s'},
 'Check Artwork': {'ar': 'فحص الصور', 'de': 'Bilder prüfen', 'fr': 'Vérifier les illustrations', 'tr': 'Görselleri Kontrol Et'},
 'Checking...': {'ar': 'جارٍ الفحص...', 'de': 'Wird geprüft...', 'fr': 'Vérification...', 'tr': 'Kontrol ediliyor...'},
 'Artwork check is already running': {'ar': 'فحص الصور قيد التشغيل بالفعل',
                                      'de': 'Bilderprüfung läuft bereits',
                                      'fr': 'La vérification des illustrations est déjà en cours',
                                      'tr': 'Görsel kontrolü zaten çalışıyor'},
 'Checking current folder for missing posters...': {'ar': 'جارٍ فحص المجلد الحالي للبوسترات الناقصة...',
                                                    'de': 'Aktueller Ordner wird auf fehlende Poster geprüft...',
                                                    'fr': 'Vérification des affiches manquantes dans le dossier actuel...',
                                                    'tr': 'Geçerli klasörde eksik posterler kontrol ediliyor...'},
 'Checking current folder for missing artwork...': {'ar': 'جارٍ فحص المجلد الحالي للصور الناقصة...',
                                                    'de': 'Aktueller Ordner wird auf fehlende Bilder geprüft...',
                                                    'fr': 'Vérification des illustrations manquantes dans le dossier actuel...',
                                                    'tr': 'Geçerli klasörde eksik görseller kontrol ediliyor...'},
 'Artwork check • %d/%d fully cached • 0 missing': {'ar': 'فحص الصور • %d/%d مكتمل في الكاش • 0 ناقص',
                                                    'de': 'Bilderprüfung • %d/%d vollständig zwischengespeichert • 0 fehlen',
                                                    'fr': 'Vérification • %d/%d entièrement en cache • 0 manquante',
                                                    'tr': 'Görsel kontrolü • %d/%d tamamen önbellekte • 0 eksik'},
 'Artwork cache • posters %d/%d • backdrops %d/%d • %d still missing': {'ar': 'كاش الصور • البوسترات %d/%d • الخلفيات %d/%d • ما زال %d ناقصًا',
                                                                        'de': 'Bild-Cache • Poster %d/%d • Hintergrundbilder %d/%d • %d fehlen noch',
                                                                        'fr': 'Cache images • affiches %d/%d • arrière-plans %d/%d • %d encore manquants',
                                                                        'tr': 'Görsel önbelleği • posterler %d/%d • arka planlar %d/%d • %d hâlâ eksik'},
 'Artwork cache • posters %d/%d (left %d) • backdrops %d/%d (left %d)': {'ar': 'كاش الصور • البوسترات %d/%d (متبقي %d) • الخلفيات %d/%d (متبقي %d)',
                                                                         'de': 'Bild-Cache • Poster %d/%d (übrig %d) • Hintergrundbilder %d/%d (übrig %d)',
                                                                         'fr': 'Cache images • affiches %d/%d (reste %d) • arrière-plans %d/%d (reste %d)',
                                                                         'tr': 'Görsel önbelleği • posterler %d/%d (kalan %d) • arka planlar %d/%d (kalan %d)'},
 'Artwork check • %d/%d posters ready • 0 missing': {'ar': 'فحص الصور • %d/%d بوستر جاهز • 0 ناقص',
                                                     'de': 'Bilderprüfung • %d/%d Poster bereit • 0 fehlen',
                                                     'fr': 'Vérification • %d/%d affiches prêtes • 0 manquante',
                                                     'tr': 'Görsel kontrolü • %d/%d poster hazır • 0 eksik'},
 'Artwork rescue • %d/%d missing posters recovered • %d still missing': {'ar': 'إنقاذ البوسترات • تم استرجاع %d/%d • ما زال %d ناقصًا',
                                                                         'de': 'Bilder-Rettung • %d/%d fehlende Poster wiederhergestellt • %d fehlen noch',
                                                                         'fr': 'Récupération • %d/%d affiches manquantes récupérées • %d encore manquantes',
                                                                         'tr': 'Poster kurtarma • %d/%d eksik poster kurtarıldı • %d hâlâ eksik'},
 'Artwork rescue • %d/%d missing posters recovered • 0 missing': {'ar': 'إنقاذ البوسترات • تم استرجاع %d/%d • 0 ناقص',
                                                                  'de': 'Bilder-Rettung • %d/%d fehlende Poster wiederhergestellt • 0 fehlen',
                                                                  'fr': 'Récupération • %d/%d affiches manquantes récupérées • 0 manquante',
                                                                  'tr': 'Poster kurtarma • %d/%d eksik poster kurtarıldı • 0 eksik'},
 ' • %d not prepared': {'ar': ' • %d غير جاهز', 'de': ' • %d nicht vorbereitet', 'fr': ' • %d non préparé(s)', 'tr': ' • %d hazırlanmadı'},
 ' • %d real backdrop upgrade': {'ar': ' • ترقية %d خلفية حقيقية',
                                 'de': ' • %d echter Hintergrund verbessert',
                                 'fr': ' • %d arrière-plan réel amélioré',
                                 'tr': ' • %d gerçek arka plan yükseltildi'},
 ' • %d real backdrop upgrades': {'ar': ' • تمت ترقية %d خلفيات حقيقية',
                                  'de': ' • %d echte Hintergründe verbessert',
                                  'fr': ' • %d arrière-plans réels améliorés',
                                  'tr': ' • %d gerçek arka plan yükseltildi'},
 ' • display-ready %d/%d': {'ar': ' • جاهز للعرض %d/%d', 'de': ' • anzeigebereit %d/%d', 'fr': ' • prêts à l’affichage %d/%d', 'tr': ' • gösterime hazır %d/%d'},
 '%d result(s)': {'ar': '\u200f%d نتيجة', 'de': '%d Ergebnis(se)', 'fr': '%d résultat(s)', 'tr': '%d sonuç'},
 '%s subtitle': {'ar': 'ترجمة %s', 'de': '%s-Untertitel', 'fr': 'Sous-titre %s', 'tr': '%s altyazı'},
 '%s subtitles • %d found': {'ar': '%s ترجمات • تم العثور على %d', 'de': '%s-Untertitel • %d gefunden', 'fr': 'Sous-titres %s • %d trouvé(s)', 'tr': '%s altyazılar • %d bulundu'},
 '%s • DOWN recent • OK open • BACK portals': {'ar': '%s • DOWN الأخيرة • OK فتح • BACK البوابات',
                                               'de': '%s • RUNTER zuletzt • OK öffnen • ZURÜCK Portale',
                                               'fr': '%s • BAS récents • OK ouvrir • RETOUR portails',
                                               'tr': '%s • AŞAĞI son kullanılanlar • OK aç • GERİ portallar'},
 'AVAILABLE': {'ar': 'متاح', 'de': 'VERFÜGBAR', 'fr': 'DISPONIBLE', 'tr': 'MEVCUT'},
 'Arabic Online • SubDL': {'ar': 'العربية أونلاين • SubDL', 'de': 'Arabisch online • SubDL', 'fr': 'Arabe en ligne • SubDL', 'tr': 'Arapça çevrimiçi • SubDL'},
 'German Online • SubDL': {'ar': 'الألمانية أونلاين • SubDL', 'de': 'Deutsch online • SubDL', 'fr': 'Allemand en ligne • SubDL', 'tr': 'Almanca çevrimiçi • SubDL'},
 'Artwork check • posters %d/%d • backdrops %d/%d • 0 missing': {'ar': 'فحص الصور • بوسترات %d/%d • خلفيات %d/%d • لا يوجد ناقص',
                                                                 'de': 'Artwork-Prüfung • Poster %d/%d • Hintergründe %d/%d • 0 fehlen',
                                                                 'fr': 'Vérification des images • affiches %d/%d • arrière-plans %d/%d • 0 manquant',
                                                                 'tr': 'Görsel kontrolü • posterler %d/%d • arka planlar %d/%d • 0 eksik'},
 'Aspect-ratio change failed': {'ar': 'فشل تغيير نسبة العرض إلى الارتفاع',
                                'de': 'Ändern des Seitenverhältnisses fehlgeschlagen',
                                'fr': 'Échec du changement de format d’image',
                                'tr': 'En-boy oranı değiştirilemedi'},
 'Auto': {'ar': 'تلقائي', 'de': 'Auto', 'fr': 'Auto', 'tr': 'Otomatik'},
 'Auto Sync applied • timing scale %.5f': {'ar': 'تم تطبيق Auto Sync • مقياس التوقيت %.5f',
                                           'de': 'Auto Sync angewendet • Zeitskala %.5f',
                                           'fr': 'Auto Sync appliqué • échelle temporelle %.5f',
                                           'tr': 'Auto Sync uygulandı • zaman ölçeği %.5f'},
 'Auto Sync needs a stable movie duration. Use delay +/- for this subtitle.': {'ar': 'يتطلب Auto Sync مدة فيلم ثابتة. استخدم التأخير +/- لهذه الترجمة.',
                                                                               'de': 'Auto Sync benötigt eine stabile Filmlänge. Verwenden Sie für diesen Untertitel Verzögerung '
                                                                                     '+/-.',
                                                                               'fr': 'Auto Sync nécessite une durée de film stable. Utilisez le délai +/- pour ce sous-titre.',
                                                                               'tr': 'Auto Sync için sabit bir film süresi gerekir. Bu altyazı için gecikme +/- kullanın.'},
 'Auto Sync • lightweight': {'ar': 'Auto Sync • خفيف', 'de': 'Auto Sync • leicht', 'fr': 'Auto Sync • léger', 'tr': 'Auto Sync • hafif'},
 'BACKDROP GRID': {'ar': 'شبكة الخلفيات', 'de': 'HINTERGRUNDRASTER', 'fr': 'GRILLE ARRIÈRE-PLAN', 'tr': 'ARKA PLAN IZGARASI'},
 'BACKDROP GRID 2': {'ar': 'شبكة الخلفيات 2', 'de': 'HINTERGRUNDRASTER 2', 'fr': 'GRILLE ARRIÈRE-PLAN 2', 'tr': 'ARKA PLAN IZGARASI 2'},
 'BLOCKED': {'ar': 'محظور', 'de': 'GESPERRT', 'fr': 'BLOQUÉ', 'tr': 'ENGELLİ'},
 'Backdrop Grid': {'ar': 'شبكة الخلفيات', 'de': 'Hintergrundraster', 'fr': 'Grille d’arrière-plan', 'tr': 'Arka Plan Izgarası'},
 'Backdrop Grid 2': {'ar': 'شبكة الخلفيات 2', 'de': 'Hintergrundraster 2', 'fr': 'Grille d’arrière-plan 2', 'tr': 'Arka Plan Izgarası 2'},
 'Backup all settings & portals': {'ar': 'نسخ احتياطي لكل الإعدادات والبوابات',
                                   'de': 'Alle Einstellungen & Portale sichern',
                                   'fr': 'Sauvegarder tous les réglages et portails',
                                   'tr': 'Tüm ayarları ve portalları yedekle'},
 'Black': {'ar': 'أسود', 'de': 'Schwarz', 'fr': 'Noir', 'tr': 'Siyah'},
 'Black • behind subtitle text': {'ar': 'أسود • خلف نص الترجمة',
                                  'de': 'Schwarz • hinter dem Untertiteltext',
                                  'fr': 'Noir • derrière le texte des sous-titres',
                                  'tr': 'Siyah • altyazı metninin arkasında'},
 'CANCEL': {'ar': 'إلغاء', 'de': 'ABBRECHEN', 'fr': 'ANNULER', 'tr': 'İPTAL'},
 'CHECK': {'ar': 'فحص', 'de': 'PRÜFEN', 'fr': 'VÉRIFIER', 'tr': 'KONTROL'},
 'CLEAR': {'ar': 'مسح', 'de': 'LEEREN', 'fr': 'EFFACER', 'tr': 'TEMİZLE'},
 'CONNECTING  •  %s': {'ar': 'جارٍ الاتصال  •  %s', 'de': 'VERBINDUNG  •  %s', 'fr': 'CONNEXION  •  %s', 'tr': 'BAĞLANIYOR  •  %s'},
 'CONNECTING  •  stabilizing stream': {'ar': 'جارٍ الاتصال  •  تثبيت البث',
                                       'de': 'VERBINDUNG  •  Stream wird stabilisiert',
                                       'fr': 'CONNEXION  •  stabilisation du flux',
                                       'tr': 'BAĞLANIYOR  •  yayın dengeleniyor'},
 'CONNECTING  •  verifying startup': {'ar': 'جارٍ الاتصال  •  التحقق من بدء التشغيل',
                                      'de': 'VERBINDUNG  •  Start wird geprüft',
                                      'fr': 'CONNEXION  •  vérification du démarrage',
                                      'tr': 'BAĞLANIYOR  •  başlangıç doğrulanıyor'},
 'Categories': {'ar': 'الفئات', 'de': 'Kategorien', 'fr': 'Catégories', 'tr': 'Kategoriler'},
 'Categories  %d/%d  •  OK: channels': {'ar': 'الفئات  %d/%d  •  OK: القنوات',
                                        'de': 'Kategorien  %d/%d  •  OK: Sender',
                                        'fr': 'Catégories  %d/%d  •  OK : chaînes',
                                        'tr': 'Kategoriler  %d/%d  •  OK: kanallar'},
 'Channels': {'ar': 'القنوات', 'de': 'Sender', 'fr': 'Chaînes', 'tr': 'Kanallar'},
 'Channels  %s  •  OK: preview  •  EXIT: categories': {'ar': 'القنوات  %s  •  OK: معاينة  •  EXIT: الفئات',
                                                       'de': 'Sender  %s  •  OK: Vorschau  •  EXIT: Kategorien',
                                                       'fr': 'Chaînes  %s  •  OK : aperçu  •  EXIT : catégories',
                                                       'tr': 'Kanallar  %s  •  OK: önizleme  •  EXIT: kategoriler'},
 'Check the official Ultra Stalker release channel and install a verified update safely.': {'ar': 'تحقق من قناة إصدارات Ultra Stalker الرسمية وثبّت تحديثًا موثوقًا بأمان.',
                                                                                            'de': 'Prüfen Sie den offiziellen Ultra-Stalker-Veröffentlichungskanal und '
                                                                                                  'installieren Sie ein verifiziertes Update sicher.',
                                                                                            'fr': 'Vérifiez le canal officiel des versions Ultra Stalker et installez une mise à '
                                                                                                  'jour vérifiée en toute sécurité.',
                                                                                            'tr': 'Resmi Ultra Stalker sürüm kanalını kontrol edin ve doğrulanmış güncellemeyi '
                                                                                                  'güvenle yükleyin.'},
 'Checking the official Ultra Stalker update channel...': {'ar': 'جارٍ فحص قناة تحديث Ultra Stalker الرسمية...',
                                                           'de': 'Offizieller Ultra-Stalker-Update-Kanal wird geprüft...',
                                                           'fr': 'Vérification du canal officiel de mise à jour Ultra Stalker...',
                                                           'tr': 'Resmi Ultra Stalker güncelleme kanalı kontrol ediliyor...'},
 'Choose Poster Grid, Cinematic, Backdrop Grid, or Backdrop Grid 2 for Movies.': {'ar': 'اختر Poster Grid أو Cinematic أو Backdrop Grid أو Backdrop Grid 2 للأفلام.',
                                                                                  'de': 'Wählen Sie Poster Grid, Cinematic, Backdrop Grid oder Backdrop Grid 2 für Filme.',
                                                                                  'fr': 'Choisissez Poster Grid, Cinematic, Backdrop Grid ou Backdrop Grid 2 pour les films.',
                                                                                  'tr': 'Filmler için Poster Grid, Cinematic, Backdrop Grid veya Backdrop Grid 2 seçin.'},
 'Choose Poster Grid, Cinematic, Backdrop Grid, or Backdrop Grid 2 for Series.': {'ar': 'اختر Poster Grid أو Cinematic أو Backdrop Grid أو Backdrop Grid 2 للمسلسلات.',
                                                                                  'de': 'Wählen Sie Poster Grid, Cinematic, Backdrop Grid oder Backdrop Grid 2 für Serien.',
                                                                                  'fr': 'Choisissez Poster Grid, Cinematic, Backdrop Grid ou Backdrop Grid 2 pour les séries.',
                                                                                  'tr': 'Diziler için Poster Grid, Cinematic, Backdrop Grid veya Backdrop Grid 2 seçin.'},
 'Choose a disabled portal to enable': {'ar': 'اختر بوابة معطلة لتفعيلها',
                                        'de': 'Wählen Sie ein deaktiviertes Portal zum Aktivieren',
                                        'fr': 'Choisissez un portail désactivé à activer',
                                        'tr': 'Etkinleştirmek için devre dışı bir portal seçin'},
 'Choose the device profile for this portal': {'ar': 'اختر ملف الجهاز لهذه البوابة',
                                               'de': 'Wählen Sie das Geräteprofil für dieses Portal',
                                               'fr': 'Choisissez le profil d’appareil pour ce portail',
                                               'tr': 'Bu portal için cihaz profilini seçin'},
 'Choose the visual theme': {'ar': 'اختر المظهر المرئي', 'de': 'Wählen Sie das visuelle Design', 'fr': 'Choisissez le thème visuel', 'tr': 'Görsel temayı seçin'},
 'Clear only temporary Ultra Stalker files under /tmp. Persistent HDD artwork is kept.': {'ar': 'امسح فقط ملفات Ultra Stalker المؤقتة داخل /tmp. سيتم الاحتفاظ بالصور الدائمة على '
                                                                                                'HDD.',
                                                                                          'de': 'Löscht nur temporäre Ultra-Stalker-Dateien unter /tmp. Permanente HDD-Grafiken '
                                                                                                'bleiben erhalten.',
                                                                                          'fr': 'Efface uniquement les fichiers temporaires Ultra Stalker dans /tmp. Les '
                                                                                                'illustrations persistantes sur HDD sont conservées.',
                                                                                          'tr': 'Yalnızca /tmp altındaki geçici Ultra Stalker dosyalarını temizler. Kalıcı HDD '
                                                                                                'görselleri korunur.'},
 'Clear plugin-owned data for this portal?  Favorites, History, Resume, exported bouquet/EPG and related timers are removed; the portal profile is kept.': {'ar': 'مسح بيانات '
                                                                                                                                                                  'الإضافة الخاصة '
                                                                                                                                                                  'بهذه البوابة؟ '
                                                                                                                                                                  'سيتم حذف '
                                                                                                                                                                  'المفضلة والسجل '
                                                                                                                                                                  'والاستكمال '
                                                                                                                                                                  'وBouquet/EPG '
                                                                                                                                                                  'المُصدّرة '
                                                                                                                                                                  'والمؤقتات '
                                                                                                                                                                  'المرتبطة، مع '
                                                                                                                                                                  'الإبقاء على ملف '
                                                                                                                                                                  'البوابة.',
                                                                                                                                                            'de': 'Plugin-Daten '
                                                                                                                                                                  'für dieses '
                                                                                                                                                                  'Portal löschen? '
                                                                                                                                                                  'Favoriten, '
                                                                                                                                                                  'Verlauf, '
                                                                                                                                                                  'Fortsetzen, '
                                                                                                                                                                  'exportiertes '
                                                                                                                                                                  'Bouquet/EPG und '
                                                                                                                                                                  'zugehörige '
                                                                                                                                                                  'Timer werden '
                                                                                                                                                                  'entfernt; das '
                                                                                                                                                                  'Portalprofil '
                                                                                                                                                                  'bleibt '
                                                                                                                                                                  'erhalten.',
                                                                                                                                                            'fr': 'Effacer les '
                                                                                                                                                                  'données du '
                                                                                                                                                                  'plugin pour ce '
                                                                                                                                                                  'portail ? Les '
                                                                                                                                                                  'favoris, '
                                                                                                                                                                  'l’historique, '
                                                                                                                                                                  'la reprise, le '
                                                                                                                                                                  'bouquet/EPG '
                                                                                                                                                                  'exporté et les '
                                                                                                                                                                  'minuteries '
                                                                                                                                                                  'associées '
                                                                                                                                                                  'seront '
                                                                                                                                                                  'supprimés ; le '
                                                                                                                                                                  'profil du '
                                                                                                                                                                  'portail sera '
                                                                                                                                                                  'conservé.',
                                                                                                                                                            'tr': 'Bu portala ait '
                                                                                                                                                                  'eklenti '
                                                                                                                                                                  'verileri '
                                                                                                                                                                  'temizlensin mi? '
                                                                                                                                                                  'Favoriler, '
                                                                                                                                                                  'geçmiş, devam '
                                                                                                                                                                  'bilgileri, dışa '
                                                                                                                                                                  'aktarılan '
                                                                                                                                                                  'bouquet/EPG ve '
                                                                                                                                                                  'ilgili '
                                                                                                                                                                  'zamanlayıcılar '
                                                                                                                                                                  'silinir; portal '
                                                                                                                                                                  'profili '
                                                                                                                                                                  'korunur.'},
 'Complete backup created on HDD:\n%s\n\nPortals, settings and user API credentials are included. Artwork cache is not copied.': {'ar': 'تم إنشاء نسخة احتياطية كاملة على HDD:\n'
                                                                                                                                        '%s\n'
                                                                                                                                        '\n'
                                                                                                                                        'تشمل البوابات والإعدادات وبيانات API '
                                                                                                                                        'الخاصة بالمستخدم. لن يتم نسخ كاش الصور.',
                                                                                                                                  'de': 'Vollständige Sicherung auf HDD erstellt:\n'
                                                                                                                                        '%s\n'
                                                                                                                                        '\n'
                                                                                                                                        'Portale, Einstellungen und '
                                                                                                                                        'Benutzer-API-Zugangsdaten sind enthalten. '
                                                                                                                                        'Der Grafik-Cache wird nicht kopiert.',
                                                                                                                                  'fr': 'Sauvegarde complète créée sur le HDD :\n'
                                                                                                                                        '%s\n'
                                                                                                                                        '\n'
                                                                                                                                        'Les portails, réglages et identifiants '
                                                                                                                                        'API utilisateur sont inclus. Le cache des '
                                                                                                                                        'illustrations n’est pas copié.',
                                                                                                                                  'tr': 'HDD üzerinde tam yedek oluşturuldu:\n'
                                                                                                                                        '%s\n'
                                                                                                                                        '\n'
                                                                                                                                        'Portallar, ayarlar ve kullanıcı API '
                                                                                                                                        'kimlik bilgileri dahil edildi. Görsel '
                                                                                                                                        'önbelleği kopyalanmadı.'},
 'Content could not be loaded': {'ar': 'تعذر تحميل المحتوى', 'de': 'Inhalt konnte nicht geladen werden', 'fr': 'Impossible de charger le contenu', 'tr': 'İçerik yüklenemedi'},
 'Could not check for updates right now. Your installed version is unchanged.': {'ar': 'تعذر فحص التحديثات الآن. نسختك المثبتة لم تتغير.',
                                                                                 'de': 'Updates konnten derzeit nicht geprüft werden. Ihre installierte Version bleibt '
                                                                                       'unverändert.',
                                                                                 'fr': 'Impossible de vérifier les mises à jour pour le moment. Votre version installée reste '
                                                                                       'inchangée.',
                                                                                 'tr': 'Güncellemeler şu anda kontrol edilemedi. Yüklü sürümünüz değişmedi.'},
 'Could not load subtitle: %s': {'ar': 'تعذر تحميل الترجمة: %s',
                                 'de': 'Untertitel konnte nicht geladen werden: %s',
                                 'fr': 'Impossible de charger le sous-titre : %s',
                                 'tr': 'Altyazı yüklenemedi: %s'},
 'Could not open subtitle results: %s': {'ar': 'تعذر فتح نتائج الترجمة: %s',
                                         'de': 'Untertitelergebnisse konnten nicht geöffnet werden: %s',
                                         'fr': 'Impossible d’ouvrir les résultats de sous-titres : %s',
                                         'tr': 'Altyazı sonuçları açılamadı: %s'},
 'Creating complete HDD backup…': {'ar': 'جارٍ إنشاء نسخة احتياطية كاملة على HDD…',
                                   'de': 'Vollständige HDD-Sicherung wird erstellt…',
                                   'fr': 'Création d’une sauvegarde HDD complète…',
                                   'tr': 'Tam HDD yedeği oluşturuluyor…'},
 'DELETE ARTWORK': {'ar': 'حذف الصور', 'de': 'GRAFIKEN LÖSCHEN', 'fr': 'SUPPRIMER LES ILLUSTRATIONS', 'tr': 'GÖRSELLERİ SİL'},
 'DISABLED': {'ar': 'معطل', 'de': 'DEAKTIVIERT', 'fr': 'DÉSACTIVÉ', 'tr': 'DEVRE DIŞI'},
 'DOWNLOADING  •  %s SUBTITLE': {'ar': 'جارٍ التنزيل  •  ترجمة %s',
                                 'de': 'DOWNLOAD  •  %s-UNTERTITEL',
                                 'fr': 'TÉLÉCHARGEMENT  •  SOUS-TITRE %s',
                                 'tr': 'İNDİRİLİYOR  •  %s ALTYAZI'},
 'Delete Persistent Artwork?': {'ar': 'حذف الصور الدائمة؟',
                                'de': 'Permanente Grafiken löschen?',
                                'fr': 'Supprimer les illustrations persistantes ?',
                                'tr': 'Kalıcı görseller silinsin mi?'},
 'Delete portal': {'ar': 'حذف البوابة', 'de': 'Portal löschen', 'fr': 'Supprimer le portail', 'tr': 'Portalı sil'},
 'Delete saved posters, backdrops, generated artwork, Home artwork and Live picons from the HDD. Confirmation is required.': {'ar': 'احذف البوسترات والخلفيات والصور المُنشأة وصور '
                                                                                                                                    'Home وLive picons المحفوظة من HDD. يلزم '
                                                                                                                                    'التأكيد.',
                                                                                                                              'de': 'Löscht gespeicherte Poster, Hintergründe, '
                                                                                                                                    'erzeugte Grafiken, Home-Grafiken und '
                                                                                                                                    'Live-Picons von der HDD. Bestätigung '
                                                                                                                                    'erforderlich.',
                                                                                                                              'fr': 'Supprimez du HDD les affiches, arrière-plans, '
                                                                                                                                    'illustrations générées, illustrations Home et '
                                                                                                                                    'picons Live enregistrés. Une confirmation est '
                                                                                                                                    'requise.',
                                                                                                                              'tr': 'Kaydedilmiş posterleri, arka planları, '
                                                                                                                                    'üretilen görselleri, Home görsellerini ve '
                                                                                                                                    'Live piconlarını HDD’den siler. Onay '
                                                                                                                                    'gerekir.'},
 'Disable subtitles': {'ar': 'تعطيل الترجمة', 'de': 'Untertitel deaktivieren', 'fr': 'Désactiver les sous-titres', 'tr': 'Altyazıları devre dışı bırak'},
 'Downloading and verifying the package…': {'ar': 'جارٍ تنزيل الحزمة والتحقق منها…',
                                            'de': 'Paket wird heruntergeladen und geprüft…',
                                            'fr': 'Téléchargement et vérification du paquet…',
                                            'tr': 'Paket indiriliyor ve doğrulanıyor…'},
 'Downloading update…': {'ar': 'جارٍ تنزيل التحديث…', 'de': 'Update wird heruntergeladen…', 'fr': 'Téléchargement de la mise à jour…', 'tr': 'Güncelleme indiriliyor…'},
 'Downloading update… %d%%': {'ar': 'جارٍ تنزيل التحديث… %d%%',
                              'de': 'Update wird heruntergeladen… %d%%',
                              'fr': 'Téléchargement de la mise à jour… %d%%',
                              'tr': 'Güncelleme indiriliyor… %d%%'},
 'Duplicate failed': {'ar': 'فشل النسخ', 'de': 'Duplizieren fehlgeschlagen', 'fr': 'Échec de la duplication', 'tr': 'Çoğaltma başarısız'},
 'ENGINE LOCKED  •  %s  •  change it in Settings': {'ar': 'المحرك مقفل  •  %s  •  غيّره من الإعدادات',
                                                    'de': 'ENGINE GESPERRT  •  %s  •  in Einstellungen ändern',
                                                    'fr': 'MOTEUR VERROUILLÉ  •  %s  •  modifiez-le dans Réglages',
                                                    'tr': 'MOTOR KİLİTLİ  •  %s  •  Ayarlardan değiştirin'},
 'EPG information unavailable': {'ar': 'معلومات EPG غير متاحة',
                                 'de': 'EPG-Informationen nicht verfügbar',
                                 'fr': 'Informations EPG indisponibles',
                                 'tr': 'EPG bilgisi kullanılamıyor'},
 'EXPIRED': {'ar': 'منتهي', 'de': 'ABGELAUFEN', 'fr': 'EXPIRÉ', 'tr': 'SÜRESİ DOLMUŞ'},
 'EXPIRES': {'ar': 'الانتهاء', 'de': 'LÄUFT AB', 'fr': 'EXPIRE', 'tr': 'BİTİŞ'},
 'Embedded subtitles': {'ar': 'الترجمات المدمجة', 'de': 'Eingebettete Untertitel', 'fr': 'Sous-titres intégrés', 'tr': 'Gömülü altyazılar'},
 'English Online • SubDL': {'ar': 'الإنجليزية أونلاين • SubDL', 'de': 'Englisch online • SubDL', 'fr': 'Anglais en ligne • SubDL', 'tr': 'İngilizce çevrimiçi • SubDL'},
 'Episode %s  •  %s': {'ar': 'الحلقة %s  •  %s', 'de': 'Episode %s  •  %s', 'fr': 'Épisode %s  •  %s', 'tr': 'Bölüm %s  •  %s'},
 'Expiry: %s': {'ar': 'الانتهاء: %s', 'de': 'Ablauf: %s', 'fr': 'Expiration : %s', 'tr': 'Bitiş: %s'},
 'Export current Live folder to TV': {'ar': 'تصدير مجلد Live الحالي إلى التلفزيون',
                                      'de': 'Aktuellen Live-Ordner zum TV exportieren',
                                      'fr': 'Exporter le dossier Live actuel vers la TV',
                                      'tr': 'Geçerli Live klasörünü TV’ye aktar'},
 'Export current Movies folder to TV': {'ar': 'تصدير مجلد الأفلام الحالي إلى التلفزيون',
                                        'de': 'Aktuellen Filme-Ordner zum TV exportieren',
                                        'fr': 'Exporter le dossier Films actuel vers la TV',
                                        'tr': 'Geçerli Film klasörünü TV’ye aktar'},
 'Export current Series folder to TV': {'ar': 'تصدير مجلد المسلسلات الحالي إلى التلفزيون',
                                        'de': 'Aktuellen Serien-Ordner zum TV exportieren',
                                        'fr': 'Exporter le dossier Séries actuel vers la TV',
                                        'tr': 'Geçerli Dizi klasörünü TV’ye aktar'},
 'Exporting %s folder: %s': {'ar': 'جارٍ تصدير مجلد %s: %s', 'de': '%s-Ordner wird exportiert: %s', 'fr': 'Export du dossier %s : %s', 'tr': '%s klasörü aktarılıyor: %s'},
 'Exporting Live folder: %s': {'ar': 'جارٍ تصدير مجلد Live: %s',
                               'de': 'Live-Ordner wird exportiert: %s',
                               'fr': 'Export du dossier Live : %s',
                               'tr': 'Live klasörü aktarılıyor: %s'},
 'FINDING LAST CHANNEL...': {'ar': 'جارٍ البحث عن آخر قناة...', 'de': 'LETZTEN SENDER SUCHEN...', 'fr': 'RECHERCHE DE LA DERNIÈRE CHAÎNE...', 'tr': 'SON KANAL BULUNUYOR...'},
 'FORWARD': {'ar': 'تقديم', 'de': 'VORLAUF', 'fr': 'AVANCE', 'tr': 'İLERİ'},
 'Fanart.tv': {'ar': 'Fanart.tv', 'de': 'Fanart.tv', 'fr': 'Fanart.tv', 'tr': 'Fanart.tv'},
 'Fanart.tv API Key': {'ar': 'مفتاح API لـ Fanart.tv', 'de': 'Fanart.tv API-Schlüssel', 'fr': 'Clé API Fanart.tv', 'tr': 'Fanart.tv API Anahtarı'},
 'Fanart.tv API key': {'ar': 'مفتاح API لـ Fanart.tv', 'de': 'Fanart.tv API-Schlüssel', 'fr': 'Clé API Fanart.tv', 'tr': 'Fanart.tv API anahtarı'},
 'Fanart.tv API key cleared': {'ar': 'تم مسح مفتاح API لـ Fanart.tv',
                               'de': 'Fanart.tv API-Schlüssel gelöscht',
                               'fr': 'Clé API Fanart.tv effacée',
                               'tr': 'Fanart.tv API anahtarı temizlendi'},
 'Fanart.tv API key save failed: %s': {'ar': 'فشل حفظ مفتاح API لـ Fanart.tv: %s',
                                       'de': 'Fanart.tv API-Schlüssel konnte nicht gespeichert werden: %s',
                                       'fr': 'Échec de l’enregistrement de la clé API Fanart.tv : %s',
                                       'tr': 'Fanart.tv API anahtarı kaydedilemedi: %s'},
 'Fanart.tv API key saved privately in api_keys.conf': {'ar': 'تم حفظ مفتاح API لـ Fanart.tv بشكل خاص في api_keys.conf',
                                                        'de': 'Fanart.tv API-Schlüssel wurde privat in api_keys.conf gespeichert',
                                                        'fr': 'Clé API Fanart.tv enregistrée de manière privée dans api_keys.conf',
                                                        'tr': 'Fanart.tv API anahtarı api_keys.conf içinde özel olarak kaydedildi'},
 'Folder export failed: %s': {'ar': 'فشل تصدير المجلد: %s',
                              'de': 'Ordnerexport fehlgeschlagen: %s',
                              'fr': 'Échec de l’export du dossier : %s',
                              'tr': 'Klasör aktarımı başarısız: %s'},
 'French Online • SubDL': {'ar': 'الفرنسية أونلاين • SubDL', 'de': 'Französisch online • SubDL', 'fr': 'Français en ligne • SubDL', 'tr': 'Fransızca çevrimiçi • SubDL'},
 'HTTP security warning': {'ar': 'تحذير أمان HTTP', 'de': 'HTTP-Sicherheitswarnung', 'fr': 'Avertissement de sécurité HTTP', 'tr': 'HTTP güvenlik uyarısı'},
 'Hero failed: %s': {'ar': 'فشل Hero: %s', 'de': 'Hero fehlgeschlagen: %s', 'fr': 'Échec du Hero : %s', 'tr': 'Hero başarısız: %s'},
 'Hero not set • selected backdrop is not cached on HDD': {'ar': 'لم يتم تعيين Hero • الخلفية المحددة غير محفوظة في كاش HDD',
                                                           'de': 'Hero nicht gesetzt • gewählter Hintergrund ist nicht auf HDD zwischengespeichert',
                                                           'fr': 'Hero non défini • l’arrière-plan sélectionné n’est pas en cache sur le HDD',
                                                           'tr': 'Hero ayarlanmadı • seçilen arka plan HDD önbelleğinde değil'},
 'Hero pinned': {'ar': 'تم تثبيت Hero', 'de': 'Hero angeheftet', 'fr': 'Hero épinglé', 'tr': 'Hero sabitlendi'},
 'Hero pinned • active across Ultra Stalker': {'ar': 'تم تثبيت Hero • نشط في كل Ultra Stalker',
                                               'de': 'Hero angeheftet • in ganz Ultra Stalker aktiv',
                                               'fr': 'Hero épinglé • actif dans tout Ultra Stalker',
                                               'tr': 'Hero sabitlendi • Ultra Stalker genelinde etkin'},
 'Hero selected': {'ar': 'تم اختيار Hero', 'de': 'Hero ausgewählt', 'fr': 'Hero sélectionné', 'tr': 'Hero seçildi'},
 'Hero selected • preparing adaptive materials…': {'ar': 'تم اختيار Hero • جارٍ تجهيز العناصر التكيفية…',
                                                   'de': 'Hero ausgewählt • adaptive Materialien werden vorbereitet…',
                                                   'fr': 'Hero sélectionné • préparation des éléments adaptatifs…',
                                                   'tr': 'Hero seçildi • uyarlanabilir materyaller hazırlanıyor…'},
 'Home Hero changes only from Movie/Series Details MENU': {'ar': 'يتغير Home Hero فقط من MENU داخل تفاصيل الفيلم/المسلسل',
                                                           'de': 'Der Home Hero wird nur über das MENÜ in Film-/Seriendetails geändert',
                                                           'fr': 'Le Hero Home ne change que depuis le MENU des détails Film/Série',
                                                           'tr': 'Home Hero yalnızca Film/Dizi Detayları MENÜSÜNDEN değiştirilir'},
 'Installation is complete. Enigma2 restart has already been scheduled automatically.': {'ar': 'اكتمل التثبيت. تمت جدولة إعادة تشغيل Enigma2 تلقائيًا بالفعل.',
                                                                                         'de': 'Die Installation ist abgeschlossen. Ein Enigma2-Neustart wurde bereits automatisch '
                                                                                               'geplant.',
                                                                                         'fr': 'L’installation est terminée. Le redémarrage d’Enigma2 a déjà été planifié '
                                                                                               'automatiquement.',
                                                                                         'tr': 'Kurulum tamamlandı. Enigma2 yeniden başlatması otomatik olarak zaten planlandı.'},
 'Last channel page': {'ar': 'آخر صفحة قنوات', 'de': 'Letzte Senderseite', 'fr': 'Dernière page de chaînes', 'tr': 'Son kanal sayfası'},
 'Live Export': {'ar': 'تصدير Live', 'de': 'Live-Export', 'fr': 'Export Live', 'tr': 'Live Aktarımı'},
 'Live Export Complete': {'ar': 'اكتمل تصدير Live', 'de': 'Live-Export abgeschlossen', 'fr': 'Export Live terminé', 'tr': 'Live aktarımı tamamlandı'},
 'Live channel': {'ar': 'قناة Live', 'de': 'Live-Sender', 'fr': 'Chaîne Live', 'tr': 'Live kanalı'},
 'Live folder export failed: %s': {'ar': 'فشل تصدير مجلد Live: %s',
                                   'de': 'Export des Live-Ordners fehlgeschlagen: %s',
                                   'fr': 'Échec de l’export du dossier Live : %s',
                                   'tr': 'Live klasörü aktarılamadı: %s'},
 'Live folder exported to the TV bouquet list.\n\n%s\n%d channels': {'ar': 'تم تصدير مجلد Live إلى قائمة Bouquets بالتلفزيون.\n\n%s\n%d قناة',
                                                                     'de': 'Live-Ordner in die TV-Bouquet-Liste exportiert.\n\n%s\n%d Sender',
                                                                     'fr': 'Dossier Live exporté vers la liste des bouquets TV.\n\n%s\n%d chaîne(s)',
                                                                     'tr': 'Live klasörü TV bouquet listesine aktarıldı.\n\n%s\n%d kanal'},
 'Live folder exported • %d channels': {'ar': 'تم تصدير مجلد Live • %d قناة',
                                        'de': 'Live-Ordner exportiert • %d Sender',
                                        'fr': 'Dossier Live exporté • %d chaîne(s)',
                                        'tr': 'Live klasörü aktarıldı • %d kanal'},
 'Loading Ultra Stalker': {'ar': 'جارٍ تحميل Ultra Stalker', 'de': 'Ultra Stalker wird geladen', 'fr': 'Chargement d’Ultra Stalker', 'tr': 'Ultra Stalker yükleniyor'},
 'Loading channels...': {'ar': 'جارٍ تحميل القنوات...', 'de': 'Sender werden geladen...', 'fr': 'Chargement des chaînes...', 'tr': 'Kanallar yükleniyor...'},
 'Loading folder...': {'ar': 'جارٍ تحميل المجلد...', 'de': 'Ordner wird geladen...', 'fr': 'Chargement du dossier...', 'tr': 'Klasör yükleniyor...'},
 'M3U Playlist %d': {'ar': 'قائمة M3U %d', 'de': 'M3U-Liste %d', 'fr': 'Liste M3U %d', 'tr': 'M3U Listesi %d'},
 'M3U • OFFLINE': {'ar': 'M3U • غير متصل', 'de': 'M3U • OFFLINE', 'fr': 'M3U • HORS LIGNE', 'tr': 'M3U • ÇEVRİMDIŞI'},
 'M3U • ONLINE': {'ar': 'M3U • متصل', 'de': 'M3U • ONLINE', 'fr': 'M3U • EN LIGNE', 'tr': 'M3U • ÇEVRİMİÇİ'},
 'M3U • READY': {'ar': 'M3U • جاهز', 'de': 'M3U • BEREIT', 'fr': 'M3U • PRÊT', 'tr': 'M3U • HAZIR'},
 'M3U • SLOW': {'ar': 'M3U • بطيء', 'de': 'M3U • LANGSAM', 'fr': 'M3U • LENT', 'tr': 'M3U • YAVAŞ'},
 'MAC': {'ar': 'MAC', 'de': 'MAC', 'fr': 'MAC', 'tr': 'MAC'},
 'Media information': {'ar': 'معلومات الوسائط', 'de': 'Medieninformationen', 'fr': 'Informations média', 'tr': 'Medya bilgileri'},
 'Move %d selected • ARROWS choose destination • GREEN Place Here • BACK Cancel': {'ar': 'نقل %d محدد • الأسهم لاختيار الوجهة • الأخضر وضع هنا • BACK إلغاء',
                                                                                   'de': '%d ausgewählt verschieben • PFEILE Ziel wählen • GRÜN Hier platzieren • ZURÜCK Abbrechen',
                                                                                   'fr': 'Déplacer %d sélectionné(s) • FLÈCHES choisir la destination • VERT Placer ici • RETOUR '
                                                                                         'Annuler',
                                                                                   'tr': '%d seçiliyi taşı • OKLAR hedefi seç • YEŞİL Buraya Yerleştir • GERİ İptal'},
 'Move %d selected • destination position %d • ARROWS Navigate • GREEN Place Here • BACK Cancel': {'ar': 'نقل %d محدد • موضع الوجهة %d • الأسهم للتنقل • الأخضر وضع هنا • BACK '
                                                                                                         'إلغاء',
                                                                                                   'de': '%d ausgewählt verschieben • Zielposition %d • PFEILE navigieren • GRÜN '
                                                                                                         'Hier platzieren • ZURÜCK Abbrechen',
                                                                                                   'fr': 'Déplacer %d sélectionné(s) • position cible %d • FLÈCHES naviguer • VERT '
                                                                                                         'Placer ici • RETOUR Annuler',
                                                                                                   'tr': '%d seçiliyi taşı • hedef konum %d • OKLAR gezin • YEŞİL Buraya Yerleştir '
                                                                                                         '• GERİ İptal'},
 'Move cancelled': {'ar': 'تم إلغاء النقل', 'de': 'Verschieben abgebrochen', 'fr': 'Déplacement annulé', 'tr': 'Taşıma iptal edildi'},
 'Move save failed: %s': {'ar': 'فشل حفظ النقل: %s',
                          'de': 'Verschieben konnte nicht gespeichert werden: %s',
                          'fr': 'Échec de l’enregistrement du déplacement : %s',
                          'tr': 'Taşıma kaydedilemedi: %s'},
 'Move selected portals • ARROWS choose destination • GREEN Place Here • BACK Cancel': {'ar': 'نقل البوابات المحددة • الأسهم لاختيار الوجهة • الأخضر وضع هنا • BACK إلغاء',
                                                                                        'de': 'Ausgewählte Portale verschieben • PFEILE Ziel wählen • GRÜN Hier platzieren • '
                                                                                              'ZURÜCK Abbrechen',
                                                                                        'fr': 'Déplacer les portails sélectionnés • FLÈCHES choisir la destination • VERT Placer '
                                                                                              'ici • RETOUR Annuler',
                                                                                        'tr': 'Seçili portalları taşı • OKLAR hedefi seç • YEŞİL Buraya Yerleştir • GERİ İptal'},
 'Move selected portals • GREEN Place Here • BACK Cancel': {'ar': 'نقل البوابات المحددة • الأخضر وضع هنا • BACK إلغاء',
                                                            'de': 'Ausgewählte Portale verschieben • GRÜN Hier platzieren • ZURÜCK Abbrechen',
                                                            'fr': 'Déplacer les portails sélectionnés • VERT Placer ici • RETOUR Annuler',
                                                            'tr': 'Seçili portalları taşı • YEŞİL Buraya Yerleştir • GERİ İptal'},
 'Moved %d portals • block starts at position %d': {'ar': 'تم نقل %d بوابة • تبدأ المجموعة عند الموضع %d',
                                                    'de': '%d Portale verschoben • Block beginnt an Position %d',
                                                    'fr': '%d portail(s) déplacé(s) • le bloc commence à la position %d',
                                                    'tr': '%d portal taşındı • blok %d konumunda başlıyor'},
 'Movies folder exported.\n\n%s\n%d movie(s)': {'ar': 'تم تصدير مجلد الأفلام.\n\n%s\n%d فيلم',
                                                'de': 'Filme-Ordner exportiert.\n\n%s\n%d Film(e)',
                                                'fr': 'Dossier Films exporté.\n\n%s\n%d film(s)',
                                                'tr': 'Film klasörü aktarıldı.\n\n%s\n%d film'},
 'Movies folder sent • %d movie(s)': {'ar': 'تم إرسال مجلد الأفلام • %d فيلم',
                                      'de': 'Filme-Ordner gesendet • %d Film(e)',
                                      'fr': 'Dossier Films envoyé • %d film(s)',
                                      'tr': 'Film klasörü gönderildi • %d film'},
 'NOT CHECKED': {'ar': 'لم يتم الفحص', 'de': 'NICHT GEPRÜFT', 'fr': 'NON VÉRIFIÉ', 'tr': 'KONTROL EDİLMEDİ'},
 'New fixes • Better performance • Improvements': {'ar': 'إصلاحات جديدة • أداء أفضل • تحسينات',
                                                   'de': 'Neue Fehlerbehebungen • Bessere Leistung • Verbesserungen',
                                                   'fr': 'Nouveaux correctifs • Meilleures performances • Améliorations',
                                                   'tr': 'Yeni düzeltmeler • Daha iyi performans • İyileştirmeler'},
 'Next Episode': {'ar': 'الحلقة التالية', 'de': 'Nächste Episode', 'fr': 'Épisode suivant', 'tr': 'Sonraki Bölüm'},
 'Next episode starts in %d seconds': {'ar': 'تبدأ الحلقة التالية خلال %d ثانية',
                                       'de': 'Nächste Episode startet in %d Sekunden',
                                       'fr': 'Le prochain épisode démarre dans %d secondes',
                                       'tr': 'Sonraki bölüm %d saniye içinde başlıyor'},
 'No %s subtitle found': {'ar': 'لم يتم العثور على ترجمة %s', 'de': 'Kein %s-Untertitel gefunden', 'fr': 'Aucun sous-titre %s trouvé', 'tr': '%s altyazı bulunamadı'},
 'No Live categories': {'ar': 'لا توجد فئات Live', 'de': 'Keine Live-Kategorien', 'fr': 'Aucune catégorie Live', 'tr': 'Live kategorisi yok'},
 'No additional description is available.': {'ar': 'لا يوجد وصف إضافي متاح.',
                                             'de': 'Keine zusätzliche Beschreibung verfügbar.',
                                             'fr': 'Aucune description supplémentaire n’est disponible.',
                                             'tr': 'Ek açıklama bulunmuyor.'},
 'No backups found on HDD.\n\nExpected folder: /media/hdd/UltraStalker/Backup/': {'ar': 'لم يتم العثور على نسخ احتياطية على HDD.\n'
                                                                                        '\n'
                                                                                        'المجلد المتوقع: /media/hdd/UltraStalker/Backup/',
                                                                                  'de': 'Keine Sicherungen auf HDD gefunden.\n\nErwarteter Ordner: /media/hdd/UltraStalker/Backup/',
                                                                                  'fr': 'Aucune sauvegarde trouvée sur le HDD.\n'
                                                                                        '\n'
                                                                                        'Dossier attendu : /media/hdd/UltraStalker/Backup/',
                                                                                  'tr': 'HDD üzerinde yedek bulunamadı.\n\nBeklenen klasör: /media/hdd/UltraStalker/Backup/'},
 'No channels': {'ar': 'لا توجد قنوات', 'de': 'Keine Sender', 'fr': 'Aucune chaîne', 'tr': 'Kanal yok'},
 'No channels in this category': {'ar': 'لا توجد قنوات في هذه الفئة',
                                  'de': 'Keine Sender in dieser Kategorie',
                                  'fr': 'Aucune chaîne dans cette catégorie',
                                  'tr': 'Bu kategoride kanal yok'},
 'No embedded subtitles were reported by this stream.': {'ar': 'لم يبلغ هذا البث عن ترجمات مدمجة.',
                                                         'de': 'Dieser Stream meldet keine eingebetteten Untertitel.',
                                                         'fr': 'Aucun sous-titre intégré n’a été signalé par ce flux.',
                                                         'tr': 'Bu yayın gömülü altyazı bildirmedi.'},
 'No playable episodes were found': {'ar': 'لم يتم العثور على حلقات قابلة للتشغيل',
                                     'de': 'Keine abspielbaren Episoden gefunden',
                                     'fr': 'Aucun épisode lisible trouvé',
                                     'tr': 'Oynatılabilir bölüm bulunamadı'},
 'No playable movies were found': {'ar': 'لم يتم العثور على أفلام قابلة للتشغيل',
                                   'de': 'Keine abspielbaren Filme gefunden',
                                   'fr': 'Aucun film lisible trouvé',
                                   'tr': 'Oynatılabilir film bulunamadı'},
 'No safe automatic drift detected. Use subtitle delay +/- for this release.': {'ar': 'لم يتم اكتشاف انحراف تلقائي آمن. استخدم تأخير الترجمة +/- لهذا الإصدار.',
                                                                                'de': 'Keine sichere automatische Abweichung erkannt. Verwenden Sie Untertitelverzögerung +/- für '
                                                                                      'diese Version.',
                                                                                'fr': 'Aucune dérive automatique sûre détectée. Utilisez le délai de sous-titre +/- pour cette '
                                                                                      'version.',
                                                                                'tr': 'Güvenli otomatik kayma algılanmadı. Bu sürüm için altyazı gecikmesi +/- kullanın.'},
 'No selected portals are available to move': {'ar': 'لا توجد بوابات محددة متاحة للنقل',
                                               'de': 'Keine ausgewählten Portale zum Verschieben verfügbar',
                                               'fr': 'Aucun portail sélectionné n’est disponible au déplacement',
                                               'tr': 'Taşınabilecek seçili portal yok'},
 'No series were found': {'ar': 'لم يتم العثور على مسلسلات', 'de': 'Keine Serien gefunden', 'fr': 'Aucune série trouvée', 'tr': 'Dizi bulunamadı'},
 'No source selected': {'ar': 'لم يتم تحديد مصدر', 'de': 'Keine Quelle ausgewählt', 'fr': 'Aucune source sélectionnée', 'tr': 'Kaynak seçilmedi'},
 'OFFLINE': {'ar': 'غير متصل', 'de': 'OFFLINE', 'fr': 'HORS LIGNE', 'tr': 'ÇEVRİMDIŞI'},
 'OK: full screen': {'ar': 'OK: ملء الشاشة', 'de': 'OK: Vollbild', 'fr': 'OK : plein écran', 'tr': 'OK: tam ekran'},
 'ONLINE': {'ar': 'متصل', 'de': 'ONLINE', 'fr': 'EN LIGNE', 'tr': 'ÇEVRİMİÇİ'},
 'Off': {'ar': 'إيقاف', 'de': 'Aus', 'fr': 'Désactivé', 'tr': 'Kapalı'},
 'Off • text only': {'ar': 'إيقاف • نص فقط', 'de': 'Aus • nur Text', 'fr': 'Désactivé • texte uniquement', 'tr': 'Kapalı • yalnızca metin'},
 'Online Update': {'ar': 'تحديث أونلاين', 'de': 'Online-Update', 'fr': 'Mise à jour en ligne', 'tr': 'Çevrimiçi Güncelleme'},
 'Online subtitles are available for Movies and Series.': {'ar': 'الترجمات الأونلاين متاحة للأفلام والمسلسلات.',
                                                           'de': 'Online-Untertitel sind für Filme und Serien verfügbar.',
                                                           'fr': 'Les sous-titres en ligne sont disponibles pour les films et les séries.',
                                                           'tr': 'Çevrimiçi altyazılar Filmler ve Diziler için kullanılabilir.'},
 'Optional second-source artwork fallback used only when TMDB leaves a poster or backdrop missing.': {'ar': 'مصدر صور احتياطي اختياري يُستخدم فقط عندما لا يوفر TMDB بوستر أو '
                                                                                                            'خلفية.',
                                                                                                      'de': 'Optionale zweite Grafikquelle wird nur verwendet, wenn TMDB kein '
                                                                                                            'Poster oder keinen Hintergrund liefert.',
                                                                                                      'fr': 'Source d’illustrations secondaire facultative utilisée uniquement '
                                                                                                            'lorsque TMDB ne fournit pas d’affiche ou d’arrière-plan.',
                                                                                                      'tr': 'İsteğe bağlı ikinci görsel kaynağı yalnızca TMDB poster veya arka '
                                                                                                            'plan sağlamadığında kullanılır.'},
 'PAUSED': {'ar': 'متوقف مؤقتًا', 'de': 'PAUSIERT', 'fr': 'EN PAUSE', 'tr': 'DURAKLATILDI'},
 'PIN must contain 4-8 digits. Parental Lock was not enabled.': {'ar': 'يجب أن يتكون PIN من 4-8 أرقام. لم يتم تفعيل القفل الأبوي.',
                                                                 'de': 'Die PIN muss 4-8 Ziffern enthalten. Die Kindersicherung wurde nicht aktiviert.',
                                                                 'fr': 'Le PIN doit contenir 4 à 8 chiffres. Le verrouillage parental n’a pas été activé.',
                                                                 'tr': 'PIN 4-8 rakam içermelidir. Ebeveyn Kilidi etkinleştirilmedi.'},
 'PLAYING  •  %s': {'ar': 'تشغيل  •  %s', 'de': 'WIEDERGABE  •  %s', 'fr': 'LECTURE  •  %s', 'tr': 'OYNATILIYOR  •  %s'},
 'PLAYING  •  NO %s SUBTITLE': {'ar': 'تشغيل  •  لا توجد ترجمة %s',
                                'de': 'WIEDERGABE  •  KEIN %s-UNTERTITEL',
                                'fr': 'LECTURE  •  AUCUN SOUS-TITRE %s',
                                'tr': 'OYNATILIYOR  •  %s ALTYAZI YOK'},
 'PLAYING  •  RESUMED %d:%02d': {'ar': 'تشغيل  •  استكمال %d:%02d',
                                 'de': 'WIEDERGABE  •  FORTGESETZT %d:%02d',
                                 'fr': 'LECTURE  •  REPRISE %d:%02d',
                                 'tr': 'OYNATILIYOR  •  DEVAM %d:%02d'},
 'PLAYING  •  RESUMED %d:%02d:%02d': {'ar': 'تشغيل  •  استكمال %d:%02d:%02d',
                                      'de': 'WIEDERGABE  •  FORTGESETZT %d:%02d:%02d',
                                      'fr': 'LECTURE  •  REPRISE %d:%02d:%02d',
                                      'tr': 'OYNATILIYOR  •  DEVAM %d:%02d:%02d'},
 'PLAYING  •  stabilizing stream': {'ar': 'تشغيل  •  تثبيت البث',
                                    'de': 'WIEDERGABE  •  Stream wird stabilisiert',
                                    'fr': 'LECTURE  •  stabilisation du flux',
                                    'tr': 'OYNATILIYOR  •  yayın dengeleniyor'},
 'PLAYING  •  verifying startup': {'ar': 'تشغيل  •  التحقق من بدء التشغيل',
                                   'de': 'WIEDERGABE  •  Start wird geprüft',
                                   'fr': 'LECTURE  •  vérification du démarrage',
                                   'tr': 'OYNATILIYOR  •  başlangıç doğrulanıyor'},
 'POSTER GRID V2': {'ar': 'شبكة البوسترات V2', 'de': 'POSTERRASTER V2', 'fr': 'GRILLE D’AFFICHES V2', 'tr': 'POSTER IZGARASI V2'},
 'PROTECTED': {'ar': 'محمي', 'de': 'GESCHÜTZT', 'fr': 'PROTÉGÉ', 'tr': 'KORUMALI'},
 'Page %d / %d  •  %d result(s)': {'ar': 'الصفحة %d / %d  •  %d نتيجة',
                                   'de': 'Seite %d / %d  •  %d Ergebnis(se)',
                                   'fr': 'Page %d / %d  •  %d résultat(s)',
                                   'tr': 'Sayfa %d / %d  •  %d sonuç'},
 'Parental PIN saved • Parental lock enabled': {'ar': 'تم حفظ PIN الأبوي • تم تفعيل القفل الأبوي',
                                                'de': 'Eltern-PIN gespeichert • Kindersicherung aktiviert',
                                                'fr': 'PIN parental enregistré • verrouillage parental activé',
                                                'tr': 'Ebeveyn PIN’i kaydedildi • Ebeveyn kilidi etkin'},
 'Parental PIN unavailable': {'ar': 'PIN الأبوي غير متاح', 'de': 'Eltern-PIN nicht verfügbar', 'fr': 'PIN parental indisponible', 'tr': 'Ebeveyn PIN’i kullanılamıyor'},
 'Parental lock remains disabled': {'ar': 'يظل القفل الأبوي معطلًا',
                                    'de': 'Kindersicherung bleibt deaktiviert',
                                    'fr': 'Le verrouillage parental reste désactivé',
                                    'tr': 'Ebeveyn kilidi devre dışı kalacak'},
 'Permanently delete selected portal from saved profiles, imports, cached sessions and plugin-owned data?': {'ar': 'حذف البوابة المحددة نهائيًا من الملفات المحفوظة والاستيرادات '
                                                                                                                   'والجلسات المؤقتة وبيانات الإضافة؟',
                                                                                                             'de': 'Ausgewähltes Portal dauerhaft aus gespeicherten Profilen, '
                                                                                                                   'Importen, Cache-Sitzungen und Plugin-Daten löschen?',
                                                                                                             'fr': 'Supprimer définitivement le portail sélectionné des profils '
                                                                                                                   'enregistrés, imports, sessions en cache et données du plugin ?',
                                                                                                             'tr': 'Seçili portal kayıtlı profillerden, içe aktarmalardan, '
                                                                                                                   'önbellek oturumlarından ve eklenti verilerinden kalıcı olarak '
                                                                                                                   'silinsin mi?'},
 'Permanently delete this portal and its plugin-owned data, exported bouquet/EPG, Favorites, History, Resume and related timers?': {'ar': 'حذف هذه البوابة نهائيًا مع بيانات '
                                                                                                                                          'الإضافة الخاصة بها وBouquet/EPG '
                                                                                                                                          'المُصدّرة والمفضلة والسجل والاستكمال '
                                                                                                                                          'والمؤقتات المرتبطة؟',
                                                                                                                                    'de': 'Dieses Portal und seine Plugin-Daten, '
                                                                                                                                          'exportiertes Bouquet/EPG, Favoriten, '
                                                                                                                                          'Verlauf, Fortsetzen und zugehörige '
                                                                                                                                          'Timer dauerhaft löschen?',
                                                                                                                                    'fr': 'Supprimer définitivement ce portail et '
                                                                                                                                          'ses données de plugin, bouquet/EPG '
                                                                                                                                          'exporté, favoris, historique, reprise '
                                                                                                                                          'et minuteries associées ?',
                                                                                                                                    'tr': 'Bu portal ve eklenti verileri, dışa '
                                                                                                                                          'aktarılan bouquet/EPG, Favoriler, '
                                                                                                                                          'Geçmiş, Devam ve ilgili zamanlayıcılar '
                                                                                                                                          'kalıcı olarak silinsin mi?'},
 'Persistent Artwork': {'ar': 'الصور الدائمة', 'de': 'Permanente Grafiken', 'fr': 'Illustrations persistantes', 'tr': 'Kalıcı Görseller'},
 'Persistent HDD is not available; nothing was deleted': {'ar': 'HDD الدائم غير متاح؛ لم يتم حذف شيء',
                                                          'de': 'Permanente HDD ist nicht verfügbar; nichts wurde gelöscht',
                                                          'fr': 'Le HDD persistant n’est pas disponible ; rien n’a été supprimé',
                                                          'tr': 'Kalıcı HDD kullanılamıyor; hiçbir şey silinmedi'},
 'Persistent artwork cleared • %d files • %.1f MB. Settings and metadata kept.': {'ar': 'تم مسح الصور الدائمة • %d ملف • %.1f MB. تم الاحتفاظ بالإعدادات والبيانات الوصفية.',
                                                                                  'de': 'Permanente Grafiken gelöscht • %d Dateien • %.1f MB. Einstellungen und Metadaten bleiben '
                                                                                        'erhalten.',
                                                                                  'fr': 'Illustrations persistantes effacées • %d fichier(s) • %.1f Mo. Réglages et métadonnées '
                                                                                        'conservés.',
                                                                                  'tr': 'Kalıcı görseller temizlendi • %d dosya • %.1f MB. Ayarlar ve metadata korundu.'},
 'Persistent artwork was not changed': {'ar': 'لم يتم تغيير الصور الدائمة',
                                        'de': 'Permanente Grafiken wurden nicht geändert',
                                        'fr': 'Les illustrations persistantes n’ont pas été modifiées',
                                        'tr': 'Kalıcı görseller değiştirilmedi'},
 'Place Here': {'ar': 'وضع هنا', 'de': 'Hier platzieren', 'fr': 'Placer ici', 'tr': 'Buraya Yerleştir'},
 'Player unavailable': {'ar': 'المشغل غير متاح', 'de': 'Player nicht verfügbar', 'fr': 'Lecteur indisponible', 'tr': 'Oynatıcı kullanılamıyor'},
 'Please try again later': {'ar': 'يرجى المحاولة مرة أخرى لاحقًا',
                            'de': 'Bitte versuchen Sie es später erneut',
                            'fr': 'Veuillez réessayer plus tard',
                            'tr': 'Lütfen daha sonra tekrar deneyin'},
 'Portal Check': {'ar': 'فحص البوابة', 'de': 'Portalprüfung', 'fr': 'Vérification du portail', 'tr': 'Portal Kontrolü'},
 'Portal Server %d': {'ar': 'خادم البوابة %d', 'de': 'Portal-Server %d', 'fr': 'Serveur portail %d', 'tr': 'Portal Sunucusu %d'},
 'Portal order unchanged': {'ar': 'ترتيب البوابات لم يتغير', 'de': 'Portalreihenfolge unverändert', 'fr': 'Ordre des portails inchangé', 'tr': 'Portal sırası değişmedi'},
 'Poster Grid V2': {'ar': 'شبكة البوسترات V2', 'de': 'Posterraster V2', 'fr': 'Grille d’affiches V2', 'tr': 'Poster Izgarası V2'},
 'Preparing Hero… waiting for cached backdrop': {'ar': 'جارٍ تجهيز Hero… انتظار الخلفية المحفوظة',
                                                 'de': 'Hero wird vorbereitet… warte auf zwischengespeicherten Hintergrund',
                                                 'fr': 'Préparation du Hero… attente de l’arrière-plan en cache',
                                                 'tr': 'Hero hazırlanıyor… önbellekteki arka plan bekleniyor'},
 'Preparing interface': {'ar': 'جارٍ تجهيز الواجهة', 'de': 'Oberfläche wird vorbereitet', 'fr': 'Préparation de l’interface', 'tr': 'Arayüz hazırlanıyor'},
 'Preparing the adaptive Hero in the background…': {'ar': 'جارٍ تجهيز Hero التكيفي في الخلفية…',
                                                    'de': 'Adaptiver Hero wird im Hintergrund vorbereitet…',
                                                    'fr': 'Préparation du Hero adaptatif en arrière-plan…',
                                                    'tr': 'Uyarlanabilir Hero arka planda hazırlanıyor…'},
 'Preparing your experience': {'ar': 'جارٍ تجهيز تجربتك', 'de': 'Ihre Umgebung wird vorbereitet', 'fr': 'Préparation de votre expérience', 'tr': 'Deneyiminiz hazırlanıyor'},
 'Preparing your library': {'ar': 'جارٍ تجهيز مكتبتك', 'de': 'Ihre Bibliothek wird vorbereitet', 'fr': 'Préparation de votre bibliothèque', 'tr': 'Kütüphaneniz hazırlanıyor'},
 'Press BLUE to switch playback engine if the channel does not start': {'ar': 'اضغط الأزرق لتغيير محرك التشغيل إذا لم تبدأ القناة',
                                                                        'de': 'Drücken Sie BLAU, um die Wiedergabe-Engine zu wechseln, falls der Sender nicht startet',
                                                                        'fr': 'Appuyez sur BLEU pour changer de moteur de lecture si la chaîne ne démarre pas',
                                                                        'tr': 'Kanal başlamazsa oynatma motorunu değiştirmek için MAVİ tuşa basın'},
 'Put long credentials in:\n/etc/enigma2/ultrastalker/api_keys.conf\n\nTMDB_API_KEY=...\nTMDB_READ_TOKEN=...\nIMDB_API_KEY=...\nIMDB_API_ENDPOINT=... (generic mode)\nSUBDL_API_KEY=...\nFANART_API_KEY=...\n\nOfficial IMDb/AWS Data Exchange:\nIMDB_ACCESS_KEY_ID=...\nIMDB_SECRET_ACCESS_KEY=...\nIMDB_SESSION_TOKEN=... (optional)\nIMDB_REGION=us-east-1\nIMDB_DATASET_ID=...\nIMDB_REVISION_ID=...\nIMDB_ASSET_ID=...': {'ar': 'ضع '
                                                                                                                                                                                                                                                                                                                                                                                                                                     'بيانات '
                                                                                                                                                                                                                                                                                                                                                                                                                                     'الاعتماد '
                                                                                                                                                                                                                                                                                                                                                                                                                                     'الطويلة '
                                                                                                                                                                                                                                                                                                                                                                                                                                     'في:\n'
                                                                                                                                                                                                                                                                                                                                                                                                                                     '/etc/enigma2/ultrastalker/api_keys.conf\n'
                                                                                                                                                                                                                                                                                                                                                                                                                                     '\n'
                                                                                                                                                                                                                                                                                                                                                                                                                                     'TMDB_API_KEY=...\n'
                                                                                                                                                                                                                                                                                                                                                                                                                                     'TMDB_READ_TOKEN=...\n'
                                                                                                                                                                                                                                                                                                                                                                                                                                     'IMDB_API_KEY=...\n'
                                                                                                                                                                                                                                                                                                                                                                                                                                     'IMDB_API_ENDPOINT=... '
                                                                                                                                                                                                                                                                                                                                                                                                                                     '(الوضع '
                                                                                                                                                                                                                                                                                                                                                                                                                                     'العام)\n'
                                                                                                                                                                                                                                                                                                                                                                                                                                     'SUBDL_API_KEY=...\n'
                                                                                                                                                                                                                                                                                                                                                                                                                                     'FANART_API_KEY=...\n'
                                                                                                                                                                                                                                                                                                                                                                                                                                     '\n'
                                                                                                                                                                                                                                                                                                                                                                                                                                     'IMDb/AWS '
                                                                                                                                                                                                                                                                                                                                                                                                                                     'Data '
                                                                                                                                                                                                                                                                                                                                                                                                                                     'Exchange '
                                                                                                                                                                                                                                                                                                                                                                                                                                     'الرسمي:\n'
                                                                                                                                                                                                                                                                                                                                                                                                                                     'IMDB_ACCESS_KEY_ID=...\n'
                                                                                                                                                                                                                                                                                                                                                                                                                                     'IMDB_SECRET_ACCESS_KEY=...\n'
                                                                                                                                                                                                                                                                                                                                                                                                                                     'IMDB_SESSION_TOKEN=... '
                                                                                                                                                                                                                                                                                                                                                                                                                                     '(اختياري)\n'
                                                                                                                                                                                                                                                                                                                                                                                                                                     'IMDB_REGION=us-east-1\n'
                                                                                                                                                                                                                                                                                                                                                                                                                                     'IMDB_DATASET_ID=...\n'
                                                                                                                                                                                                                                                                                                                                                                                                                                     'IMDB_REVISION_ID=...\n'
                                                                                                                                                                                                                                                                                                                                                                                                                                     'IMDB_ASSET_ID=...',
                                                                                                                                                                                                                                                                                                                                                                                                                               'de': 'Lange '
                                                                                                                                                                                                                                                                                                                                                                                                                                     'Zugangsdaten '
                                                                                                                                                                                                                                                                                                                                                                                                                                     'eintragen '
                                                                                                                                                                                                                                                                                                                                                                                                                                     'in:\n'
                                                                                                                                                                                                                                                                                                                                                                                                                                     '/etc/enigma2/ultrastalker/api_keys.conf\n'
                                                                                                                                                                                                                                                                                                                                                                                                                                     '\n'
                                                                                                                                                                                                                                                                                                                                                                                                                                     'TMDB_API_KEY=...\n'
                                                                                                                                                                                                                                                                                                                                                                                                                                     'TMDB_READ_TOKEN=...\n'
                                                                                                                                                                                                                                                                                                                                                                                                                                     'IMDB_API_KEY=...\n'
                                                                                                                                                                                                                                                                                                                                                                                                                                     'IMDB_API_ENDPOINT=... '
                                                                                                                                                                                                                                                                                                                                                                                                                                     '(generischer '
                                                                                                                                                                                                                                                                                                                                                                                                                                     'Modus)\n'
                                                                                                                                                                                                                                                                                                                                                                                                                                     'SUBDL_API_KEY=...\n'
                                                                                                                                                                                                                                                                                                                                                                                                                                     'FANART_API_KEY=...\n'
                                                                                                                                                                                                                                                                                                                                                                                                                                     '\n'
                                                                                                                                                                                                                                                                                                                                                                                                                                     'Offizieller '
                                                                                                                                                                                                                                                                                                                                                                                                                                     'IMDb/AWS '
                                                                                                                                                                                                                                                                                                                                                                                                                                     'Data '
                                                                                                                                                                                                                                                                                                                                                                                                                                     'Exchange:\n'
                                                                                                                                                                                                                                                                                                                                                                                                                                     'IMDB_ACCESS_KEY_ID=...\n'
                                                                                                                                                                                                                                                                                                                                                                                                                                     'IMDB_SECRET_ACCESS_KEY=...\n'
                                                                                                                                                                                                                                                                                                                                                                                                                                     'IMDB_SESSION_TOKEN=... '
                                                                                                                                                                                                                                                                                                                                                                                                                                     '(optional)\n'
                                                                                                                                                                                                                                                                                                                                                                                                                                     'IMDB_REGION=us-east-1\n'
                                                                                                                                                                                                                                                                                                                                                                                                                                     'IMDB_DATASET_ID=...\n'
                                                                                                                                                                                                                                                                                                                                                                                                                                     'IMDB_REVISION_ID=...\n'
                                                                                                                                                                                                                                                                                                                                                                                                                                     'IMDB_ASSET_ID=...',
                                                                                                                                                                                                                                                                                                                                                                                                                               'fr': 'Placez '
                                                                                                                                                                                                                                                                                                                                                                                                                                     'les '
                                                                                                                                                                                                                                                                                                                                                                                                                                     'identifiants '
                                                                                                                                                                                                                                                                                                                                                                                                                                     'longs '
                                                                                                                                                                                                                                                                                                                                                                                                                                     'dans '
                                                                                                                                                                                                                                                                                                                                                                                                                                     ':\n'
                                                                                                                                                                                                                                                                                                                                                                                                                                     '/etc/enigma2/ultrastalker/api_keys.conf\n'
                                                                                                                                                                                                                                                                                                                                                                                                                                     '\n'
                                                                                                                                                                                                                                                                                                                                                                                                                                     'TMDB_API_KEY=...\n'
                                                                                                                                                                                                                                                                                                                                                                                                                                     'TMDB_READ_TOKEN=...\n'
                                                                                                                                                                                                                                                                                                                                                                                                                                     'IMDB_API_KEY=...\n'
                                                                                                                                                                                                                                                                                                                                                                                                                                     'IMDB_API_ENDPOINT=... '
                                                                                                                                                                                                                                                                                                                                                                                                                                     '(mode '
                                                                                                                                                                                                                                                                                                                                                                                                                                     'générique)\n'
                                                                                                                                                                                                                                                                                                                                                                                                                                     'SUBDL_API_KEY=...\n'
                                                                                                                                                                                                                                                                                                                                                                                                                                     'FANART_API_KEY=...\n'
                                                                                                                                                                                                                                                                                                                                                                                                                                     '\n'
                                                                                                                                                                                                                                                                                                                                                                                                                                     'IMDb/AWS '
                                                                                                                                                                                                                                                                                                                                                                                                                                     'Data '
                                                                                                                                                                                                                                                                                                                                                                                                                                     'Exchange '
                                                                                                                                                                                                                                                                                                                                                                                                                                     'officiel '
                                                                                                                                                                                                                                                                                                                                                                                                                                     ':\n'
                                                                                                                                                                                                                                                                                                                                                                                                                                     'IMDB_ACCESS_KEY_ID=...\n'
                                                                                                                                                                                                                                                                                                                                                                                                                                     'IMDB_SECRET_ACCESS_KEY=...\n'
                                                                                                                                                                                                                                                                                                                                                                                                                                     'IMDB_SESSION_TOKEN=... '
                                                                                                                                                                                                                                                                                                                                                                                                                                     '(facultatif)\n'
                                                                                                                                                                                                                                                                                                                                                                                                                                     'IMDB_REGION=us-east-1\n'
                                                                                                                                                                                                                                                                                                                                                                                                                                     'IMDB_DATASET_ID=...\n'
                                                                                                                                                                                                                                                                                                                                                                                                                                     'IMDB_REVISION_ID=...\n'
                                                                                                                                                                                                                                                                                                                                                                                                                                     'IMDB_ASSET_ID=...',
                                                                                                                                                                                                                                                                                                                                                                                                                               'tr': 'Uzun '
                                                                                                                                                                                                                                                                                                                                                                                                                                     'kimlik '
                                                                                                                                                                                                                                                                                                                                                                                                                                     'bilgilerini '
                                                                                                                                                                                                                                                                                                                                                                                                                                     'şuraya '
                                                                                                                                                                                                                                                                                                                                                                                                                                     'koyun:\n'
                                                                                                                                                                                                                                                                                                                                                                                                                                     '/etc/enigma2/ultrastalker/api_keys.conf\n'
                                                                                                                                                                                                                                                                                                                                                                                                                                     '\n'
                                                                                                                                                                                                                                                                                                                                                                                                                                     'TMDB_API_KEY=...\n'
                                                                                                                                                                                                                                                                                                                                                                                                                                     'TMDB_READ_TOKEN=...\n'
                                                                                                                                                                                                                                                                                                                                                                                                                                     'IMDB_API_KEY=...\n'
                                                                                                                                                                                                                                                                                                                                                                                                                                     'IMDB_API_ENDPOINT=... '
                                                                                                                                                                                                                                                                                                                                                                                                                                     '(genel '
                                                                                                                                                                                                                                                                                                                                                                                                                                     'mod)\n'
                                                                                                                                                                                                                                                                                                                                                                                                                                     'SUBDL_API_KEY=...\n'
                                                                                                                                                                                                                                                                                                                                                                                                                                     'FANART_API_KEY=...\n'
                                                                                                                                                                                                                                                                                                                                                                                                                                     '\n'
                                                                                                                                                                                                                                                                                                                                                                                                                                     'Resmi '
                                                                                                                                                                                                                                                                                                                                                                                                                                     'IMDb/AWS '
                                                                                                                                                                                                                                                                                                                                                                                                                                     'Data '
                                                                                                                                                                                                                                                                                                                                                                                                                                     'Exchange:\n'
                                                                                                                                                                                                                                                                                                                                                                                                                                     'IMDB_ACCESS_KEY_ID=...\n'
                                                                                                                                                                                                                                                                                                                                                                                                                                     'IMDB_SECRET_ACCESS_KEY=...\n'
                                                                                                                                                                                                                                                                                                                                                                                                                                     'IMDB_SESSION_TOKEN=... '
                                                                                                                                                                                                                                                                                                                                                                                                                                     '(isteğe '
                                                                                                                                                                                                                                                                                                                                                                                                                                     'bağlı)\n'
                                                                                                                                                                                                                                                                                                                                                                                                                                     'IMDB_REGION=us-east-1\n'
                                                                                                                                                                                                                                                                                                                                                                                                                                     'IMDB_DATASET_ID=...\n'
                                                                                                                                                                                                                                                                                                                                                                                                                                     'IMDB_REVISION_ID=...\n'
                                                                                                                                                                                                                                                                                                                                                                                                                                     'IMDB_ASSET_ID=...'},
 'READY': {'ar': 'جاهز', 'de': 'BEREIT', 'fr': 'PRÊT', 'tr': 'HAZIR'},
 'RESUMING  •  %d:%02d': {'ar': 'جارٍ الاستكمال  •  %d:%02d', 'de': 'FORTSETZEN  •  %d:%02d', 'fr': 'REPRISE  •  %d:%02d', 'tr': 'DEVAM EDİYOR  •  %d:%02d'},
 'REWIND': {'ar': 'ترجيع', 'de': 'RÜCKLAUF', 'fr': 'RETOUR', 'tr': 'GERİ'},
 'Reset subtitle sync': {'ar': 'إعادة ضبط مزامنة الترجمة',
                         'de': 'Untertitelsynchronisation zurücksetzen',
                         'fr': 'Réinitialiser la synchronisation des sous-titres',
                         'tr': 'Altyazı senkronunu sıfırla'},
 'Restart now': {'ar': 'إعادة التشغيل الآن', 'de': 'Jetzt neu starten', 'fr': 'Redémarrer maintenant', 'tr': 'Şimdi yeniden başlat'},
 'Restore backup': {'ar': 'استعادة النسخة الاحتياطية', 'de': 'Sicherung wiederherstellen', 'fr': 'Restaurer la sauvegarde', 'tr': 'Yedeği geri yükle'},
 'Restore this backup completely?\n\nThe selected backup will be restored directly. No additional backup copy will be created.': {'ar': 'استعادة هذه النسخة الاحتياطية بالكامل؟\n\nسيتم استعادة النسخة المحددة مباشرةً، ولن يتم إنشاء نسخة احتياطية إضافية.', 'de': 'Diese Sicherung vollständig wiederherstellen?\n\nDie ausgewählte Sicherung wird direkt wiederhergestellt. Es wird keine zusätzliche Sicherung erstellt.', 'fr': 'Restaurer complètement cette sauvegarde ?\n\nLa sauvegarde sélectionnée sera restaurée directement. Aucune copie de sauvegarde supplémentaire ne sera créée.', 'tr': 'Bu yedek tamamen geri yüklensin mi?\n\nSeçilen yedek doğrudan geri yüklenecek. Ek bir yedek kopyası oluşturulmayacak.'},
 'Restore this backup completely? The selected backup will be restored directly. No additional backup copy will be created.': {'ar': 'استعادة هذه النسخة الاحتياطية بالكامل؟ سيتم استعادة النسخة المحددة مباشرةً ولن يتم إنشاء نسخة احتياطية إضافية.', 'de': 'Diese Sicherung vollständig wiederherstellen? Die ausgewählte Sicherung wird direkt wiederhergestellt. Es wird keine zusätzliche Sicherung erstellt.', 'fr': 'Restaurer complètement cette sauvegarde ? La sauvegarde sélectionnée sera restaurée directement. Aucune copie de sauvegarde supplémentaire ne sera créée.', 'tr': 'Bu yedek tamamen geri yüklensin mi? Seçilen yedek doğrudan geri yüklenecek ve ek bir yedek kopyası oluşturulmayacak.'},
 'Restore complete. Restart Enigma2/plugin before continuing.\n\nRestored: %s': {'ar': 'اكتملت الاستعادة. أعد تشغيل Enigma2/الإضافة قبل المتابعة.\n\nتمت الاستعادة: %s', 'de': 'Wiederherstellung abgeschlossen. Starten Sie Enigma2/Plugin neu, bevor Sie fortfahren.\n\nWiederhergestellt: %s', 'fr': 'Restauration terminée. Redémarrez Enigma2/le plugin avant de continuer.\n\nRestauré : %s', 'tr': 'Geri yükleme tamamlandı. Devam etmeden önce Enigma2/eklentiyi yeniden başlatın.\n\nGeri yüklendi: %s'},
 'Restore complete • restart Enigma2/plugin': {'ar': 'اكتملت الاستعادة • أعد تشغيل Enigma2/الإضافة',
                                               'de': 'Wiederherstellung abgeschlossen • Enigma2/Plugin neu starten',
                                               'fr': 'Restauration terminée • redémarrez Enigma2/le plugin',
                                               'tr': 'Geri yükleme tamamlandı • Enigma2/eklentiyi yeniden başlatın'},
 'Restore complete. Restart Enigma2/plugin before continuing.\n\nSafety backup: %s\n\nRestored: %s': {'ar': 'اكتملت الاستعادة. أعد تشغيل Enigma2/الإضافة قبل المتابعة.\n'
                                                                                                            '\n'
                                                                                                            'نسخة الأمان: %s\n'
                                                                                                            '\n'
                                                                                                            'تمت الاستعادة: %s',
                                                                                                      'de': 'Wiederherstellung abgeschlossen. Starten Sie Enigma2/Plugin neu, '
                                                                                                            'bevor Sie fortfahren.\n'
                                                                                                            '\n'
                                                                                                            'Sicherheitssicherung: %s\n'
                                                                                                            '\n'
                                                                                                            'Wiederhergestellt: %s',
                                                                                                      'fr': 'Restauration terminée. Redémarrez Enigma2/le plugin avant de '
                                                                                                            'continuer.\n'
                                                                                                            '\n'
                                                                                                            'Sauvegarde de sécurité : %s\n'
                                                                                                            '\n'
                                                                                                            'Restauré : %s',
                                                                                                      'tr': 'Geri yükleme tamamlandı. Devam etmeden önce Enigma2/eklentiyi yeniden '
                                                                                                            'başlatın.\n'
                                                                                                            '\n'
                                                                                                            'Güvenlik yedeği: %s\n'
                                                                                                            '\n'
                                                                                                            'Geri yüklendi: %s'},
 'Restore this backup completely?\n\nA safety backup of the current state will be created on HDD first. Portals, settings and saved API credentials in the selected backup will be restored.': {'ar': 'استعادة '
                                                                                                                                                                                                      'هذه '
                                                                                                                                                                                                      'النسخة '
                                                                                                                                                                                                      'الاحتياطية '
                                                                                                                                                                                                      'بالكامل؟\n'
                                                                                                                                                                                                      '\n'
                                                                                                                                                                                                      'سيتم '
                                                                                                                                                                                                      'أولًا '
                                                                                                                                                                                                      'إنشاء '
                                                                                                                                                                                                      'نسخة '
                                                                                                                                                                                                      'أمان '
                                                                                                                                                                                                      'من '
                                                                                                                                                                                                      'الحالة '
                                                                                                                                                                                                      'الحالية '
                                                                                                                                                                                                      'على '
                                                                                                                                                                                                      'HDD. '
                                                                                                                                                                                                      'سيتم '
                                                                                                                                                                                                      'استعادة '
                                                                                                                                                                                                      'البوابات '
                                                                                                                                                                                                      'والإعدادات '
                                                                                                                                                                                                      'وبيانات '
                                                                                                                                                                                                      'API '
                                                                                                                                                                                                      'المحفوظة '
                                                                                                                                                                                                      'في '
                                                                                                                                                                                                      'النسخة '
                                                                                                                                                                                                      'المحددة.',
                                                                                                                                                                                                'de': 'Diese '
                                                                                                                                                                                                      'Sicherung '
                                                                                                                                                                                                      'vollständig '
                                                                                                                                                                                                      'wiederherstellen?\n'
                                                                                                                                                                                                      '\n'
                                                                                                                                                                                                      'Zuerst '
                                                                                                                                                                                                      'wird '
                                                                                                                                                                                                      'eine '
                                                                                                                                                                                                      'Sicherheitssicherung '
                                                                                                                                                                                                      'des '
                                                                                                                                                                                                      'aktuellen '
                                                                                                                                                                                                      'Zustands '
                                                                                                                                                                                                      'auf '
                                                                                                                                                                                                      'HDD '
                                                                                                                                                                                                      'erstellt. '
                                                                                                                                                                                                      'Portale, '
                                                                                                                                                                                                      'Einstellungen '
                                                                                                                                                                                                      'und '
                                                                                                                                                                                                      'gespeicherte '
                                                                                                                                                                                                      'API-Zugangsdaten '
                                                                                                                                                                                                      'der '
                                                                                                                                                                                                      'gewählten '
                                                                                                                                                                                                      'Sicherung '
                                                                                                                                                                                                      'werden '
                                                                                                                                                                                                      'wiederhergestellt.',
                                                                                                                                                                                                'fr': 'Restaurer '
                                                                                                                                                                                                      'complètement '
                                                                                                                                                                                                      'cette '
                                                                                                                                                                                                      'sauvegarde '
                                                                                                                                                                                                      '?\n'
                                                                                                                                                                                                      '\n'
                                                                                                                                                                                                      'Une '
                                                                                                                                                                                                      'sauvegarde '
                                                                                                                                                                                                      'de '
                                                                                                                                                                                                      'sécurité '
                                                                                                                                                                                                      'de '
                                                                                                                                                                                                      'l’état '
                                                                                                                                                                                                      'actuel '
                                                                                                                                                                                                      'sera '
                                                                                                                                                                                                      'd’abord '
                                                                                                                                                                                                      'créée '
                                                                                                                                                                                                      'sur '
                                                                                                                                                                                                      'le '
                                                                                                                                                                                                      'HDD. '
                                                                                                                                                                                                      'Les '
                                                                                                                                                                                                      'portails, '
                                                                                                                                                                                                      'réglages '
                                                                                                                                                                                                      'et '
                                                                                                                                                                                                      'identifiants '
                                                                                                                                                                                                      'API '
                                                                                                                                                                                                      'enregistrés '
                                                                                                                                                                                                      'de '
                                                                                                                                                                                                      'la '
                                                                                                                                                                                                      'sauvegarde '
                                                                                                                                                                                                      'sélectionnée '
                                                                                                                                                                                                      'seront '
                                                                                                                                                                                                      'restaurés.',
                                                                                                                                                                                                'tr': 'Bu '
                                                                                                                                                                                                      'yedek '
                                                                                                                                                                                                      'tamamen '
                                                                                                                                                                                                      'geri '
                                                                                                                                                                                                      'yüklensin '
                                                                                                                                                                                                      'mi?\n'
                                                                                                                                                                                                      '\n'
                                                                                                                                                                                                      'Önce '
                                                                                                                                                                                                      'mevcut '
                                                                                                                                                                                                      'durumun '
                                                                                                                                                                                                      'HDD '
                                                                                                                                                                                                      'üzerinde '
                                                                                                                                                                                                      'güvenlik '
                                                                                                                                                                                                      'yedeği '
                                                                                                                                                                                                      'oluşturulacak. '
                                                                                                                                                                                                      'Seçilen '
                                                                                                                                                                                                      'yedekteki '
                                                                                                                                                                                                      'portallar, '
                                                                                                                                                                                                      'ayarlar '
                                                                                                                                                                                                      've '
                                                                                                                                                                                                      'kayıtlı '
                                                                                                                                                                                                      'API '
                                                                                                                                                                                                      'kimlik '
                                                                                                                                                                                                      'bilgileri '
                                                                                                                                                                                                      'geri '
                                                                                                                                                                                                      'yüklenecek.'},
 'Restore this backup completely? A safety backup of the current state will be created first. Portals, settings and saved API credentials will be restored.': {'ar': 'استعادة هذه '
                                                                                                                                                                     'النسخة '
                                                                                                                                                                     'الاحتياطية '
                                                                                                                                                                     'بالكامل؟ '
                                                                                                                                                                     'سيتم أولًا '
                                                                                                                                                                     'إنشاء نسخة '
                                                                                                                                                                     'أمان من '
                                                                                                                                                                     'الحالة '
                                                                                                                                                                     'الحالية. '
                                                                                                                                                                     'ستتم استعادة '
                                                                                                                                                                     'البوابات '
                                                                                                                                                                     'والإعدادات '
                                                                                                                                                                     'وبيانات API '
                                                                                                                                                                     'المحفوظة.',
                                                                                                                                                               'de': 'Diese '
                                                                                                                                                                     'Sicherung '
                                                                                                                                                                     'vollständig '
                                                                                                                                                                     'wiederherstellen? '
                                                                                                                                                                     'Zuerst wird '
                                                                                                                                                                     'eine '
                                                                                                                                                                     'Sicherheitssicherung '
                                                                                                                                                                     'des '
                                                                                                                                                                     'aktuellen '
                                                                                                                                                                     'Zustands '
                                                                                                                                                                     'erstellt. '
                                                                                                                                                                     'Portale, '
                                                                                                                                                                     'Einstellungen '
                                                                                                                                                                     'und '
                                                                                                                                                                     'gespeicherte '
                                                                                                                                                                     'API-Zugangsdaten '
                                                                                                                                                                     'werden '
                                                                                                                                                                     'wiederhergestellt.',
                                                                                                                                                               'fr': 'Restaurer '
                                                                                                                                                                     'complètement '
                                                                                                                                                                     'cette '
                                                                                                                                                                     'sauvegarde ? '
                                                                                                                                                                     'Une '
                                                                                                                                                                     'sauvegarde '
                                                                                                                                                                     'de sécurité '
                                                                                                                                                                     'de l’état '
                                                                                                                                                                     'actuel sera '
                                                                                                                                                                     'créée '
                                                                                                                                                                     'd’abord. Les '
                                                                                                                                                                     'portails, '
                                                                                                                                                                     'réglages et '
                                                                                                                                                                     'identifiants '
                                                                                                                                                                     'API '
                                                                                                                                                                     'enregistrés '
                                                                                                                                                                     'seront '
                                                                                                                                                                     'restaurés.',
                                                                                                                                                               'tr': 'Bu yedek '
                                                                                                                                                                     'tamamen geri '
                                                                                                                                                                     'yüklensin '
                                                                                                                                                                     'mi? Önce '
                                                                                                                                                                     'mevcut '
                                                                                                                                                                     'durumun '
                                                                                                                                                                     'güvenlik '
                                                                                                                                                                     'yedeği '
                                                                                                                                                                     'oluşturulacak. '
                                                                                                                                                                     'Portallar, '
                                                                                                                                                                     'ayarlar ve '
                                                                                                                                                                     'kayıtlı API '
                                                                                                                                                                     'kimlik '
                                                                                                                                                                     'bilgileri '
                                                                                                                                                                     'geri '
                                                                                                                                                                     'yüklenecek.'},
 'Resume playback from %d:%02d:%02d?': {'ar': 'استكمال التشغيل من %d:%02d:%02d؟',
                                        'de': 'Wiedergabe ab %d:%02d:%02d fortsetzen?',
                                        'fr': 'Reprendre la lecture à %d:%02d:%02d ?',
                                        'tr': 'Oynatmaya %d:%02d:%02d konumundan devam edilsin mi?'},
 'Retry': {'ar': 'إعادة المحاولة', 'de': 'Erneut versuchen', 'fr': 'Réessayer', 'tr': 'Tekrar dene'},
 'SEARCHING  •  SUBDL %s': {'ar': 'جارٍ البحث  •  SUBDL %s', 'de': 'SUCHE  •  SUBDL %s', 'fr': 'RECHERCHE  •  SUBDL %s', 'tr': 'ARANIYOR  •  SUBDL %s'},
 'SLOW': {'ar': 'بطيء', 'de': 'ZEITLUPE', 'fr': 'RALENTI', 'tr': 'YAVAŞ'},
 'STATUS': {'ar': 'الحالة', 'de': 'STATUS', 'fr': 'ÉTAT', 'tr': 'DURUM'},
 'Search Movies & Series': {'ar': 'بحث في الأفلام والمسلسلات', 'de': 'Filme & Serien suchen', 'fr': 'Rechercher Films & Séries', 'tr': 'Film & Dizi Ara'},
 'Search: %s': {'ar': 'بحث: %s', 'de': 'Suche: %s', 'fr': 'Recherche : %s', 'tr': 'Ara: %s'},
 'Searching... %d result(s)': {'ar': 'جارٍ البحث... %d نتيجة', 'de': 'Suche... %d Ergebnis(se)', 'fr': 'Recherche... %d résultat(s)', 'tr': 'Aranıyor... %d sonuç'},
 'Seek, audio tracks, subtitles and resume are available when supported by the stream': {'ar': 'التقديم والمسارات الصوتية والترجمة واستئناف التشغيل متاحة عندما يدعمها البث',
                                                                                         'de': 'Spulen, Audiospuren, Untertitel und Fortsetzen sind verfügbar, wenn der Stream sie '
                                                                                               'unterstützt',
                                                                                         'fr': 'La recherche, les pistes audio, les sous-titres et la reprise sont disponibles si '
                                                                                               'le flux les prend en charge',
                                                                                         'tr': 'Akış destekliyorsa ileri/geri sarma, ses parçaları, altyazılar ve devam etme '
                                                                                               'kullanılabilir'},
 'Select Portals • %d selected • OK Toggle • MENU Move • YELLOW Select all • RED Clear • BLUE Delete selected • BACK Portal Manager': {'ar': 'تحديد البوابات • %d محدد • OK تبديل '
                                                                                                                                             '• MENU نقل • الأصفر تحديد الكل • '
                                                                                                                                             'الأحمر مسح • الأزرق حذف المحدد • '
                                                                                                                                             'BACK مدير البوابات',
                                                                                                                                       'de': 'Portale auswählen • %d ausgewählt • '
                                                                                                                                             'OK Umschalten • MENÜ Verschieben • '
                                                                                                                                             'GELB Alle wählen • ROT Leeren • BLAU '
                                                                                                                                             'Auswahl löschen • ZURÜCK '
                                                                                                                                             'Portalmanager',
                                                                                                                                       'fr': 'Sélectionner les portails • %d '
                                                                                                                                             'sélectionné(s) • OK Basculer • MENU '
                                                                                                                                             'Déplacer • JAUNE Tout sélectionner • '
                                                                                                                                             'ROUGE Effacer • BLEU Supprimer la '
                                                                                                                                             'sélection • RETOUR Gestionnaire de '
                                                                                                                                             'portails',
                                                                                                                                       'tr': 'Portalları Seç • %d seçili • OK '
                                                                                                                                             'Değiştir • MENÜ Taşı • SARI Tümünü '
                                                                                                                                             'seç • KIRMIZI Temizle • MAVİ '
                                                                                                                                             'Seçilenleri sil • GERİ Portal '
                                                                                                                                             'Yöneticisi'},
 'Select a saved Ultra Stalker backup': {'ar': 'اختر نسخة Ultra Stalker احتياطية محفوظة',
                                         'de': 'Wählen Sie eine gespeicherte Ultra-Stalker-Sicherung',
                                         'fr': 'Sélectionnez une sauvegarde Ultra Stalker enregistrée',
                                         'tr': 'Kaydedilmiş bir Ultra Stalker yedeği seçin'},
 'Select portals first, then MENU → Move': {'ar': 'حدد البوابات أولًا، ثم MENU → نقل',
                                            'de': 'Wählen Sie zuerst Portale, dann MENÜ → Verschieben',
                                            'fr': 'Sélectionnez d’abord les portails, puis MENU → Déplacer',
                                            'tr': 'Önce portalları seçin, sonra MENÜ → Taşı'},
 'Selected portals are no longer available': {'ar': 'البوابات المحددة لم تعد متاحة',
                                              'de': 'Ausgewählte Portale sind nicht mehr verfügbar',
                                              'fr': 'Les portails sélectionnés ne sont plus disponibles',
                                              'tr': 'Seçili portallar artık kullanılamıyor'},
 'Send selected Series to Receiver': {'ar': 'إرسال المسلسلات المحددة إلى الرسيفر',
                                      'de': 'Ausgewählte Serien an Receiver senden',
                                      'fr': 'Envoyer les séries sélectionnées au récepteur',
                                      'tr': 'Seçili Dizileri Alıcıya Gönder'},
 'Send selected to Receiver': {'ar': 'إرسال المحدد إلى الرسيفر', 'de': 'Auswahl an Receiver senden', 'fr': 'Envoyer la sélection au récepteur', 'tr': 'Seçilenleri Alıcıya Gönder'},
 'Send to Receiver failed: %s': {'ar': 'فشل الإرسال إلى الرسيفر: %s',
                                 'de': 'Senden an Receiver fehlgeschlagen: %s',
                                 'fr': 'Échec de l’envoi au récepteur : %s',
                                 'tr': 'Alıcıya gönderim başarısız: %s'},
 'Sending to Receiver: %s': {'ar': 'جارٍ الإرسال إلى الرسيفر: %s', 'de': 'Wird an Receiver gesendet: %s', 'fr': 'Envoi au récepteur : %s', 'tr': 'Alıcıya gönderiliyor: %s'},
 'Sent to Receiver • %d item(s)': {'ar': 'تم الإرسال إلى الرسيفر • %d عنصر',
                                   'de': 'An Receiver gesendet • %d Element(e)',
                                   'fr': 'Envoyé au récepteur • %d élément(s)',
                                   'tr': 'Alıcıya gönderildi • %d öğe'},
 'Sent to the receiver bouquet list.\n\n%s\n%d item(s)': {'ar': 'تم الإرسال إلى قائمة Bouquets في الرسيفر.\n\n%s\n%d عنصر',
                                                          'de': 'An die Bouquet-Liste des Receivers gesendet.\n\n%s\n%d Element(e)',
                                                          'fr': 'Envoyé vers la liste des bouquets du récepteur.\n\n%s\n%d élément(s)',
                                                          'tr': 'Alıcının bouquet listesine gönderildi.\n\n%s\n%d öğe'},
 'Series folder exported.\n\n%s\n%d series • %d episodes': {'ar': 'تم تصدير مجلد المسلسلات.\n\n%s\n%d مسلسل • %d حلقة',
                                                            'de': 'Serien-Ordner exportiert.\n\n%s\n%d Serie(n) • %d Episode(n)',
                                                            'fr': 'Dossier Séries exporté.\n\n%s\n%d série(s) • %d épisode(s)',
                                                            'tr': 'Dizi klasörü aktarıldı.\n\n%s\n%d dizi • %d bölüm'},
 'Series folder sent • %d series': {'ar': 'تم إرسال مجلد المسلسلات • %d مسلسل',
                                    'de': 'Serien-Ordner gesendet • %d Serie(n)',
                                    'fr': 'Dossier Séries envoyé • %d série(s)',
                                    'tr': 'Dizi klasörü gönderildi • %d dizi'},
 'Set parental PIN to enable lock (4-8 digits)': {'ar': 'عيّن PIN أبوي لتفعيل القفل (4-8 أرقام)',
                                                  'de': 'Eltern-PIN festlegen, um die Sperre zu aktivieren (4-8 Ziffern)',
                                                  'fr': 'Définissez un PIN parental pour activer le verrouillage (4 à 8 chiffres)',
                                                  'tr': 'Kilidi etkinleştirmek için ebeveyn PIN’i belirleyin (4-8 rakam)'},
 'SubDL API key is not configured.': {'ar': 'مفتاح API لـ SubDL غير مُعد.',
                                      'de': 'SubDL API-Schlüssel ist nicht konfiguriert.',
                                      'fr': 'La clé API SubDL n’est pas configurée.',
                                      'tr': 'SubDL API anahtarı yapılandırılmamış.'},
 'Subtitle Background': {'ar': 'خلفية الترجمة', 'de': 'Untertitel-Hintergrund', 'fr': 'Arrière-plan des sous-titres', 'tr': 'Altyazı Arka Planı'},
 'Subtitle Background • %s': {'ar': 'خلفية الترجمة • %s', 'de': 'Untertitel-Hintergrund • %s', 'fr': 'Arrière-plan des sous-titres • %s', 'tr': 'Altyazı Arka Planı • %s'},
 'Subtitle Color': {'ar': 'لون الترجمة', 'de': 'Untertitelfarbe', 'fr': 'Couleur des sous-titres', 'tr': 'Altyazı Rengi'},
 'Subtitle Color • %s': {'ar': 'لون الترجمة • %s', 'de': 'Untertitelfarbe • %s', 'fr': 'Couleur des sous-titres • %s', 'tr': 'Altyazı Rengi • %s'},
 'Subtitle Position': {'ar': 'موضع الترجمة', 'de': 'Untertitelposition', 'fr': 'Position des sous-titres', 'tr': 'Altyazı Konumu'},
 'Subtitle Position • %s': {'ar': 'موضع الترجمة • %s', 'de': 'Untertitelposition • %s', 'fr': 'Position des sous-titres • %s', 'tr': 'Altyazı Konumu • %s'},
 'Subtitle Size': {'ar': 'حجم الترجمة', 'de': 'Untertitelgröße', 'fr': 'Taille des sous-titres', 'tr': 'Altyazı Boyutu'},
 'Subtitle Size • %s': {'ar': 'حجم الترجمة • %s', 'de': 'Untertitelgröße • %s', 'fr': 'Taille des sous-titres • %s', 'tr': 'Altyazı Boyutu • %s'},
 'Subtitle delay +0.5 sec': {'ar': 'تأخير الترجمة +0.5 ثانية', 'de': 'Untertitelverzögerung +0,5 Sek.', 'fr': 'Délai des sous-titres +0,5 s', 'tr': 'Altyazı gecikmesi +0.5 sn'},
 'Subtitle delay -0.5 sec': {'ar': 'تأخير الترجمة -0.5 ثانية', 'de': 'Untertitelverzögerung -0,5 Sek.', 'fr': 'Délai des sous-titres -0,5 s', 'tr': 'Altyazı gecikmesi -0.5 sn'},
 'Subtitle delay: %+.1f sec': {'ar': 'تأخير الترجمة: %+.1f ثانية',
                               'de': 'Untertitelverzögerung: %+.1f Sek.',
                               'fr': 'Délai des sous-titres : %+.1f s',
                               'tr': 'Altyazı gecikmesi: %+.1f sn'},
 'Subtitle download failed: %s': {'ar': 'فشل تنزيل الترجمة: %s',
                                  'de': 'Untertitel-Download fehlgeschlagen: %s',
                                  'fr': 'Échec du téléchargement du sous-titre : %s',
                                  'tr': 'Altyazı indirilemedi: %s'},
 'Subtitle search failed: %s': {'ar': 'فشل البحث عن الترجمة: %s',
                                'de': 'Untertitelsuche fehlgeschlagen: %s',
                                'fr': 'Échec de la recherche de sous-titres : %s',
                                'tr': 'Altyazı araması başarısız: %s'},
 'Temporary Cache': {'ar': 'الكاش المؤقت', 'de': 'Temporärer Cache', 'fr': 'Cache temporaire', 'tr': 'Geçici Önbellek'},
 'Temporary cache cleared • %d files • %.1f MB. Persistent HDD artwork kept.': {'ar': 'تم مسح الكاش المؤقت • %d ملف • %.1f MB. تم الاحتفاظ بصور HDD الدائمة.',
                                                                                'de': 'Temporärer Cache gelöscht • %d Dateien • %.1f MB. Permanente HDD-Grafiken bleiben erhalten.',
                                                                                'fr': 'Cache temporaire effacé • %d fichier(s) • %.1f Mo. Les illustrations persistantes du HDD '
                                                                                      'sont conservées.',
                                                                                'tr': 'Geçici önbellek temizlendi • %d dosya • %.1f MB. Kalıcı HDD görselleri korundu.'},
 'The stream could not start with the configured Enigma2 engine.\n\n%s\n\nTried: %s': {'ar': 'تعذر بدء البث بمحرك Enigma2 المحدد.\n\n%s\n\nتمت المحاولة: %s',
                                                                                       'de': 'Der Stream konnte mit der konfigurierten Enigma2-Engine nicht gestartet werden.\n'
                                                                                             '\n'
                                                                                             '%s\n'
                                                                                             '\n'
                                                                                             'Versucht: %s',
                                                                                       'fr': 'Le flux n’a pas pu démarrer avec le moteur Enigma2 configuré.\n'
                                                                                             '\n'
                                                                                             '%s\n'
                                                                                             '\n'
                                                                                             'Tentatives : %s',
                                                                                       'tr': 'Akış yapılandırılmış Enigma2 motoruyla başlatılamadı.\n\n%s\n\nDenenenler: %s'},
 'The update stopped safely before replacing your working version. You can try again now or go back.': {'ar': 'توقف التحديث بأمان قبل استبدال نسختك الحالية. يمكنك المحاولة مرة '
                                                                                                              'أخرى الآن أو الرجوع.',
                                                                                                        'de': 'Das Update wurde sicher gestoppt, bevor Ihre funktionierende '
                                                                                                              'Version ersetzt wurde. Sie können es jetzt erneut versuchen oder '
                                                                                                              'zurückgehen.',
                                                                                                        'fr': 'La mise à jour s’est arrêtée en toute sécurité avant de remplacer '
                                                                                                              'votre version actuelle. Vous pouvez réessayer maintenant ou revenir '
                                                                                                              'en arrière.',
                                                                                                        'tr': 'Güncelleme, çalışan sürümünüz değiştirilmeden önce güvenli şekilde '
                                                                                                              'durdu. Şimdi tekrar deneyebilir veya geri dönebilirsiniz.'},
 'The updater engine is ready, but the official release URL has not been linked to this build yet.': {'ar': 'محرك التحديث جاهز، لكن رابط الإصدار الرسمي لم يتم ربطه بهذه النسخة '
                                                                                                            'بعد.',
                                                                                                      'de': 'Die Update-Engine ist bereit, aber die offizielle Release-URL ist '
                                                                                                            'noch nicht mit diesem Build verknüpft.',
                                                                                                      'fr': 'Le moteur de mise à jour est prêt, mais l’URL officielle de version '
                                                                                                            'n’est pas encore liée à cette build.',
                                                                                                      'tr': 'Güncelleme motoru hazır, ancak resmi sürüm URL’si henüz bu yapıya '
                                                                                                            'bağlanmadı.'},
 'This removes saved HDD artwork only. Settings, portals, watch history and metadata indexes are kept.': {'ar': 'هذا يحذف صور HDD المحفوظة فقط. سيتم الاحتفاظ بالإعدادات والبوابات '
                                                                                                                'وسجل المشاهدة وفهارس البيانات الوصفية.',
                                                                                                          'de': 'Dies entfernt nur gespeicherte HDD-Grafiken. Einstellungen, '
                                                                                                                'Portale, Wiedergabeverlauf und Metadaten-Indizes bleiben '
                                                                                                                'erhalten.',
                                                                                                          'fr': 'Cela supprime uniquement les illustrations enregistrées sur le '
                                                                                                                'HDD. Les réglages, portails, historique de visionnage et index de '
                                                                                                                'métadonnées sont conservés.',
                                                                                                          'tr': 'Bu yalnızca kaydedilmiş HDD görsellerini siler. Ayarlar, '
                                                                                                                'portallar, izleme geçmişi ve metadata indeksleri korunur.'},
 'Turkish Online • SubDL': {'ar': 'التركية أونلاين • SubDL', 'de': 'Türkisch online • SubDL', 'fr': 'Turc en ligne • SubDL', 'tr': 'Türkçe çevrimiçi • SubDL'},
 'ULTRA STALKER  •  PREMIUM': {'ar': 'ULTRA STALKER  •  بريميوم', 'de': 'ULTRA STALKER  •  PREMIUM', 'fr': 'ULTRA STALKER  •  PREMIUM', 'tr': 'ULTRA STALKER  •  PREMIUM'},
 'URL': {'ar': 'الرابط', 'de': 'URL', 'fr': 'URL', 'tr': 'URL'},
 'Ultra Stalker Update': {'ar': 'تحديث Ultra Stalker', 'de': 'Ultra Stalker Update', 'fr': 'Mise à jour Ultra Stalker', 'tr': 'Ultra Stalker Güncellemesi'},
 'Ultra Stalker V%s installed': {'ar': 'تم تثبيت Ultra Stalker V%s', 'de': 'Ultra Stalker V%s installiert', 'fr': 'Ultra Stalker V%s installé', 'tr': 'Ultra Stalker V%s yüklendi'},
 'Ultra Stalker failed to initialize': {'ar': 'فشل تهيئة Ultra Stalker',
                                        'de': 'Ultra Stalker konnte nicht initialisiert werden',
                                        'fr': 'Échec de l’initialisation d’Ultra Stalker',
                                        'tr': 'Ultra Stalker başlatılamadı'},
 'Ultra Stalker is up to date': {'ar': 'Ultra Stalker مُحدّث', 'de': 'Ultra Stalker ist aktuell', 'fr': 'Ultra Stalker est à jour', 'tr': 'Ultra Stalker güncel'},
 'Ultra Stalker • Subtitles': {'ar': 'Ultra Stalker • الترجمات', 'de': 'Ultra Stalker • Untertitel', 'fr': 'Ultra Stalker • Sous-titres', 'tr': 'Ultra Stalker • Altyazılar'},
 'Unable to load categories': {'ar': 'تعذر تحميل الفئات',
                               'de': 'Kategorien konnten nicht geladen werden',
                               'fr': 'Impossible de charger les catégories',
                               'tr': 'Kategoriler yüklenemedi'},
 'Unable to load channel page': {'ar': 'تعذر تحميل صفحة القنوات',
                                 'de': 'Senderseite konnte nicht geladen werden',
                                 'fr': 'Impossible de charger la page de chaînes',
                                 'tr': 'Kanal sayfası yüklenemedi'},
 'Unable to load channels': {'ar': 'تعذر تحميل القنوات', 'de': 'Sender konnten nicht geladen werden', 'fr': 'Impossible de charger les chaînes', 'tr': 'Kanallar yüklenemedi'},
 'Update': {'ar': 'تحديث', 'de': 'Aktualisieren', 'fr': 'Mettre à jour', 'tr': 'Güncelle'},
 'Update V%s is available, but the premium update screen could not open.': {'ar': 'التحديث V%s متاح، لكن تعذر فتح شاشة التحديث المميزة.',
                                                                            'de': 'Update V%s ist verfügbar, aber der Premium-Update-Bildschirm konnte nicht geöffnet werden.',
                                                                            'fr': 'La mise à jour V%s est disponible, mais l’écran de mise à jour premium n’a pas pu s’ouvrir.',
                                                                            'tr': 'V%s güncellemesi mevcut, ancak premium güncelleme ekranı açılamadı.'},
 'Update available: V%s': {'ar': 'تحديث متاح: V%s', 'de': 'Update verfügbar: V%s', 'fr': 'Mise à jour disponible : V%s', 'tr': 'Güncelleme mevcut: V%s'},
 'Update check failed safely': {'ar': 'فشل فحص التحديث بأمان',
                                'de': 'Update-Prüfung ist sicher fehlgeschlagen',
                                'fr': 'La vérification de mise à jour a échoué en toute sécurité',
                                'tr': 'Güncelleme kontrolü güvenli şekilde başarısız oldu'},
 'Update check finished safely': {'ar': 'اكتمل فحص التحديث بأمان',
                                  'de': 'Update-Prüfung sicher abgeschlossen',
                                  'fr': 'Vérification de mise à jour terminée en toute sécurité',
                                  'tr': 'Güncelleme kontrolü güvenli şekilde tamamlandı'},
 'Update completed successfully': {'ar': 'اكتمل التحديث بنجاح',
                                   'de': 'Update erfolgreich abgeschlossen',
                                   'fr': 'Mise à jour terminée avec succès',
                                   'tr': 'Güncelleme başarıyla tamamlandı'},
 'Update couldn’t be installed': {'ar': 'تعذر تثبيت التحديث',
                                  'de': 'Update konnte nicht installiert werden',
                                  'fr': 'Impossible d’installer la mise à jour',
                                  'tr': 'Güncelleme yüklenemedi'},
 'Update failed': {'ar': 'فشل التحديث', 'de': 'Update fehlgeschlagen', 'fr': 'Échec de la mise à jour', 'tr': 'Güncelleme başarısız'},
 'Updating…': {'ar': 'جارٍ التحديث…', 'de': 'Aktualisierung…', 'fr': 'Mise à jour…', 'tr': 'Güncelleniyor…'},
 'Verified package installed • Temporary update files cleaned': {'ar': 'تم تثبيت الحزمة الموثوقة • تم تنظيف ملفات التحديث المؤقتة',
                                                                 'de': 'Verifiziertes Paket installiert • Temporäre Update-Dateien bereinigt',
                                                                 'fr': 'Paquet vérifié installé • Fichiers temporaires de mise à jour nettoyés',
                                                                 'tr': 'Doğrulanmış paket yüklendi • Geçici güncelleme dosyaları temizlendi'},
 'Verified package • Your current version stays safe until installation completes': {'ar': 'حزمة موثوقة • تظل نسختك الحالية آمنة حتى اكتمال التثبيت',
                                                                                     'de': 'Verifiziertes Paket • Ihre aktuelle Version bleibt bis zum Abschluss der Installation '
                                                                                           'erhalten',
                                                                                     'fr': 'Paquet vérifié • Votre version actuelle reste intacte jusqu’à la fin de l’installation',
                                                                                     'tr': 'Doğrulanmış paket • Kurulum tamamlanana kadar mevcut sürümünüz güvende kalır'},
 'Version %s is ready to install': {'ar': 'الإصدار %s جاهز للتثبيت',
                                    'de': 'Version %s ist zur Installation bereit',
                                    'fr': 'La version %s est prête à être installée',
                                    'tr': '%s sürümü kuruluma hazır'},
 'WAITING  •  EMBEDDED SUBTITLES': {'ar': 'انتظار  •  الترجمات المدمجة',
                                    'de': 'WARTEN  •  EINGEBETTETE UNTERTITEL',
                                    'fr': 'ATTENTE  •  SOUS-TITRES INTÉGRÉS',
                                    'tr': 'BEKLENİYOR  •  GÖMÜLÜ ALTYAZILAR'},
 'You already have the latest official version: V%s': {'ar': 'لديك بالفعل أحدث إصدار رسمي: V%s',
                                                       'de': 'Sie haben bereits die neueste offizielle Version: V%s',
                                                       'fr': 'Vous avez déjà la dernière version officielle : V%s',
                                                       'tr': 'Zaten en son resmi sürüme sahipsiniz: V%s'},
 'Your current version is unchanged': {'ar': 'نسختك الحالية لم تتغير',
                                       'de': 'Ihre aktuelle Version ist unverändert',
                                       'fr': 'Votre version actuelle reste inchangée',
                                       'tr': 'Mevcut sürümünüz değişmedi'},
 'Your selected backdrop is now the active Hero.': {'ar': 'الخلفية التي حددتها أصبحت الآن Hero النشط.',
                                                    'de': 'Ihr ausgewählter Hintergrund ist jetzt der aktive Hero.',
                                                    'fr': 'L’arrière-plan sélectionné est maintenant le Hero actif.',
                                                    'tr': 'Seçtiğiniz arka plan artık etkin Hero.'},
 'Choose Subtitles': {'ar': 'اختيار الترجمة', 'de': 'Untertitel auswählen', 'fr': 'Choisir les sous-titres', 'tr': 'Altyazı seç'},
 'Subtitle Settings': {'ar': 'إعدادات الترجمة', 'de': 'Untertitel-Einstellungen', 'fr': 'Paramètres des sous-titres', 'tr': 'Altyazı ayarları'}}


# Category visibility manager (8.4.29 source follow-up).
_T.update({
 'Manage Categories': {'ar':'إدارة الأقسام','de':'Kategorien verwalten','fr':'Gérer les catégories','tr':'Kategorileri yönet'},
 'Select Categories': {'ar':'تحديد الأقسام','de':'Kategorien auswählen','fr':'Sélectionner les catégories','tr':'Kategorileri seç'},
 'Keep selected only': {'ar':'إبقاء المحدد فقط','de':'Nur Auswahl behalten','fr':'Garder uniquement la sélection','tr':'Yalnız seçilenleri tut'},
 'Hide selected': {'ar':'إخفاء المحدد','de':'Auswahl ausblenden','fr':'Masquer la sélection','tr':'Seçilenleri gizle'},
 'Select all visible': {'ar':'تحديد كل الظاهر','de':'Alle sichtbaren auswählen','fr':'Sélectionner tout ce qui est visible','tr':'Tüm görünenleri seç'},
 'Clear selection': {'ar':'مسح التحديد','de':'Auswahl löschen','fr':'Effacer la sélection','tr':'Seçimi temizle'},
 'Cancel category selection': {'ar':'إلغاء تحديد الأقسام','de':'Kategorieauswahl abbrechen','fr':'Annuler la sélection des catégories','tr':'Kategori seçimini iptal et'},
 'Show all categories': {'ar':'إظهار كل الأقسام','de':'Alle Kategorien anzeigen','fr':'Afficher toutes les catégories','tr':'Tüm kategorileri göster'},
 'Selection mode • OK selects and moves down • MENU applies': {'ar':'وضع التحديد • OK يحدد وينزل للسطر التالي • MENU للتطبيق','de':'Auswahlmodus • OK wählt und geht nach unten • MENU wendet an','fr':'Mode sélection • OK sélectionne et descend • MENU applique','tr':'Seçim modu • OK seçer ve aşağı iner • MENU uygular'},
 '%d categories selected • MENU to apply': {'ar':'تم تحديد %d قسم • MENU للتطبيق','de':'%d Kategorien ausgewählt • MENU zum Anwenden','fr':'%d catégories sélectionnées • MENU pour appliquer','tr':'%d kategori seçildi • Uygulamak için MENU'},
 'Selection cleared • OK selects and moves down': {'ar':'تم مسح التحديد • OK يحدد وينزل للسطر التالي','de':'Auswahl gelöscht • OK wählt und geht nach unten','fr':'Sélection effacée • OK sélectionne et descend','tr':'Seçim temizlendi • OK seçer ve aşağı iner'},
 'Select at least one category first': {'ar':'حدد قسمًا واحدًا على الأقل أولًا','de':'Wählen Sie zuerst mindestens eine Kategorie','fr':'Sélectionnez d’abord au moins une catégorie','tr':'Önce en az bir kategori seçin'},
 'All categories are hidden • MENU to restore': {'ar':'كل الأقسام مخفية • MENU لإظهارها','de':'Alle Kategorien sind ausgeblendet • MENU zum Wiederherstellen','fr':'Toutes les catégories sont masquées • MENU pour restaurer','tr':'Tüm kategoriler gizli • Geri getirmek için MENU'},
 'No visible categories to select • MENU can restore hidden categories': {'ar':'لا توجد أقسام ظاهرة للتحديد • MENU يمكنه إظهار الأقسام المخفية','de':'Keine sichtbaren Kategorien zur Auswahl • MENU kann ausgeblendete Kategorien wiederherstellen','fr':'Aucune catégorie visible à sélectionner • MENU peut restaurer les catégories masquées','tr':'Seçilecek görünür kategori yok • MENU gizli kategorileri geri getirebilir'},
 'Use MENU → Manage Categories to change visibility.': {'ar':'استخدم MENU ← إدارة الأقسام لتغيير الظهور.','de':'Mit MENU → Kategorien verwalten ändern Sie die Sichtbarkeit.','fr':'Utilisez MENU → Gérer les catégories pour modifier la visibilité.','tr':'Görünürlüğü değiştirmek için MENU → Kategorileri yönet seçeneğini kullanın.'},
})

# R140 description-language controls. Keep the five built-in interface languages
# complete while external packs can supply the same source strings themselves.
_T.update({
    "Description Language": {
        "ar": "لغة الوصف", "de": "Beschreibungssprache",
        "fr": "Langue de description", "tr": "Açıklama Dili",
    },
    "Arabic + English (AR + EN)": {
        "ar": "العربية + الإنجليزية (AR + EN)",
        "de": "Arabisch + Englisch (AR + EN)",
        "fr": "Arabe + anglais (AR + EN)",
        "tr": "Arapça + İngilizce (AR + EN)",
    },
    "Choose movie and series description language independently from the interface. Missing selected-language text falls back to English only.": {
        "ar": "اختر لغة وصف الأفلام والمسلسلات بشكل مستقل عن لغة الواجهة. إذا لم يتوفر الوصف باللغة المختارة، تُستخدم الإنجليزية فقط كبديل.",
        "de": "Wählen Sie die Beschreibungssprache für Filme und Serien unabhängig von der Oberfläche. Fehlt der Text in der gewählten Sprache, wird ausschließlich Englisch verwendet.",
        "fr": "Choisissez la langue des descriptions des films et séries indépendamment de l’interface. Si le texte manque dans la langue choisie, seul l’anglais est utilisé en remplacement.",
        "tr": "Film ve dizi açıklama dilini arayüzden bağımsız seçin. Seçilen dilde metin yoksa yalnızca İngilizce yedek olarak kullanılır.",
    },
    "This changes only movie and series descriptions. Each selected language falls back to English only; titles, genres, seasons, cast and artwork keep the current Ultra metadata policy.": {
        "ar": "يغيّر هذا وصف الأفلام والمسلسلات فقط. كل لغة مختارة ترجع إلى الإنجليزية فقط عند غيابها؛ وتظل العناوين والأنواع والمواسم والممثلون والصور وفق سياسة Ultra الحالية.",
        "de": "Dies ändert nur Film- und Serienbeschreibungen. Jede gewählte Sprache fällt ausschließlich auf Englisch zurück; Titel, Genres, Staffeln, Besetzung und Grafiken behalten die aktuelle Ultra-Metadatenrichtlinie.",
        "fr": "Cela modifie uniquement les descriptions des films et séries. Chaque langue choisie revient uniquement à l’anglais si elle manque ; les titres, genres, saisons, distributions et illustrations conservent la politique de métadonnées Ultra actuelle.",
        "tr": "Bu yalnızca film ve dizi açıklamalarını değiştirir. Seçilen her dil yalnızca İngilizceye geri döner; başlıklar, türler, sezonlar, oyuncular ve görseller mevcut Ultra meta veri politikasını korur.",
    },
    "Keep Current AR + EN / English Only": {
        "ar": "الإبقاء على العربية + الإنجليزية / الإنجليزية فقط الحالية",
        "de": "Aktuelles AR + EN / Nur Englisch beibehalten",
        "fr": "Conserver AR + EN / Anglais uniquement actuel",
        "tr": "Mevcut AR + EN / Yalnızca İngilizce seçimini koru",
    },
    "Same as Interface": {
        "ar": "نفس لغة الواجهة", "de": "Wie Benutzeroberfläche",
        "fr": "Identique à l’interface", "tr": "Arayüz ile aynı",
    },
    "Change only movie and series descriptions. Arabic + English / English Only above keep their existing metadata behavior.": {
        "ar": "يغيّر وصف الأفلام والمسلسلات فقط. يظل سلوك العربية + الإنجليزية / الإنجليزية فقط أعلاه كما هو.",
        "de": "Ändert nur Film- und Serienbeschreibungen. Arabisch + Englisch / Nur Englisch oben behalten ihr bisheriges Metadatenverhalten.",
        "fr": "Modifie uniquement les descriptions des films et séries. Arabe + anglais / Anglais uniquement ci-dessus conservent leur comportement actuel des métadonnées.",
        "tr": "Yalnızca film ve dizi açıklamalarını değiştirir. Yukarıdaki Arapça + İngilizce / Yalnızca İngilizce mevcut meta veri davranışını korur.",
    },
    "This changes only movie and series descriptions. Titles, genres, seasons, cast and artwork keep the current Ultra metadata policy.": {
        "ar": "يغيّر هذا وصف الأفلام والمسلسلات فقط. تظل العناوين والأنواع والمواسم والممثلون والصور وفق سياسة Ultra الحالية.",
        "de": "Dies ändert nur Film- und Serienbeschreibungen. Titel, Genres, Staffeln, Besetzung und Grafiken behalten die aktuelle Ultra-Metadatenrichtlinie.",
        "fr": "Cela modifie uniquement les descriptions des films et séries. Les titres, genres, saisons, distributions et illustrations conservent la politique de métadonnées Ultra actuelle.",
        "tr": "Bu yalnızca film ve dizi açıklamalarını değiştirir. Başlıklar, türler, sezonlar, oyuncular ve görseller mevcut Ultra meta veri politikasını korur.",
    },
})


# R142: Settings/Portal navigation strings that were introduced after the original catalog.
_T.update({'Playback': {'ar': 'التشغيل', 'de': 'Wiedergabe', 'fr': 'Lecture', 'tr': 'Oynatma'}, 'Playback engine, resume and watch-progress behaviour.': {'ar': 'محرك التشغيل والاستئناف وسلوك تقدم المشاهدة.', 'de': 'Wiedergabe-Engine, Fortsetzen und Verhalten des Wiedergabefortschritts.', 'fr': 'Moteur de lecture, reprise et comportement de progression.', 'tr': 'Oynatma motoru, devam etme ve izleme ilerlemesi davranışı.'}, 'Live & EPG': {'ar': 'البث المباشر وEPG', 'de': 'Live & EPG', 'fr': 'Direct & EPG', 'tr': 'Canlı & EPG'}, 'Live list, preview, EPG and catch-up controls.': {'ar': 'قائمة البث المباشر والمعاينة وEPG وعناصر التحكم في Catch-up.', 'de': 'Live-Liste, Vorschau, EPG und Catch-up-Steuerung.', 'fr': 'Liste du direct, aperçu, EPG et commandes de rattrapage.', 'tr': 'Canlı liste, önizleme, EPG ve catch-up kontrolleri.'}, 'Movies & Series': {'ar': 'الأفلام والمسلسلات', 'de': 'Filme & Serien', 'fr': 'Films & Séries', 'tr': 'Filmler & Diziler'}, 'Views, artwork and catalogue presentation.': {'ar': 'طرق العرض والصور وتقديم الكتالوج.', 'de': 'Ansichten, Grafiken und Katalogdarstellung.', 'fr': 'Vues, illustrations et présentation du catalogue.', 'tr': 'Görünümler, görseller ve katalog sunumu.'}, 'Search & Metadata': {'ar': 'البحث والبيانات الوصفية', 'de': 'Suche & Metadaten', 'fr': 'Recherche & Métadonnées', 'tr': 'Arama & Meta Veriler'}, 'Search scope, TMDB and external artwork/subtitle sources.': {'ar': 'نطاق البحث وTMDB ومصادر الصور والترجمات الخارجية.', 'de': 'Suchbereich, TMDB und externe Grafik-/Untertitelquellen.', 'fr': 'Périmètre de recherche, TMDB et sources externes d’illustrations/sous-titres.', 'tr': 'Arama kapsamı, TMDB ve harici görsel/altyazı kaynakları.'}, 'Parental Control': {'ar': 'الرقابة الأبوية', 'de': 'Jugendschutz', 'fr': 'Contrôle parental', 'tr': 'Ebeveyn Denetimi'}, 'PIN, sensitive categories and temporary unlock.': {'ar': 'PIN والفئات الحساسة وفتح القفل المؤقت.', 'de': 'PIN, sensible Kategorien und temporäre Freigabe.', 'fr': 'PIN, catégories sensibles et déverrouillage temporaire.', 'tr': 'PIN, hassas kategoriler ve geçici kilit açma.'}, 'Appearance': {'ar': 'المظهر', 'de': 'Darstellung', 'fr': 'Apparence', 'tr': 'Görünüm'}, 'Language, font size, menu entry and visual theme.': {'ar': 'اللغة وحجم الخط وإدخال القائمة والسمة المرئية.', 'de': 'Sprache, Schriftgröße, Menüeintrag und visuelles Thema.', 'fr': 'Langue, taille de police, entrée de menu et thème visuel.', 'tr': 'Dil, yazı boyutu, menü girişi ve görsel tema.'}, 'Storage & Maintenance': {'ar': 'التخزين والصيانة', 'de': 'Speicher & Wartung', 'fr': 'Stockage & Maintenance', 'tr': 'Depolama & Bakım'}, 'Refresh, caches, artwork storage and backups.': {'ar': 'التحديث وذاكرات التخزين المؤقت وتخزين الصور والنسخ الاحتياطية.', 'de': 'Aktualisierung, Caches, Grafikspeicher und Sicherungen.', 'fr': 'Actualisation, caches, stockage des illustrations et sauvegardes.', 'tr': 'Yenileme, önbellekler, görsel depolama ve yedekler.'}, 'Portal / Network / Advanced': {'ar': 'البوابة / الشبكة / متقدم', 'de': 'Portal / Netzwerk / Erweitert', 'fr': 'Portail / Réseau / Avancé', 'tr': 'Portal / Ağ / Gelişmiş'}, 'Portal timeout, proxy and low-frequency tuning.': {'ar': 'مهلة البوابة والوكيل والضبط منخفض التكرار.', 'de': 'Portal-Zeitlimit, Proxy und selten benötigte Feineinstellungen.', 'fr': 'Délai du portail, proxy et réglages avancés peu fréquents.', 'tr': 'Portal zaman aşımı, proxy ve seyrek kullanılan ince ayarlar.'}, 'Tools & About': {'ar': 'الأدوات وحول', 'de': 'Werkzeuge & Info', 'fr': 'Outils & À propos', 'tr': 'Araçlar & Hakkında'}, 'Diagnostics, cleaner, updates and build information.': {'ar': 'التشخيص والمنظف والتحديثات ومعلومات البناء.', 'de': 'Diagnose, Cleaner, Updates und Build-Informationen.', 'fr': 'Diagnostics, nettoyeur, mises à jour et informations de build.', 'tr': 'Tanılama, temizleyici, güncellemeler ve yapı bilgileri.'}, 'Move portals': {'ar': 'نقل البوابات', 'de': 'Portale verschieben', 'fr': 'Déplacer les portails', 'tr': 'Portalları taşı'}, 'Select one or more portals, then press MENU to move them': {'ar': 'حدد بوابة واحدة أو أكثر، ثم اضغط MENU لنقلها', 'de': 'Wählen Sie ein oder mehrere Portale und drücken Sie dann MENU zum Verschieben', 'fr': 'Sélectionnez un ou plusieurs portails, puis appuyez sur MENU pour les déplacer', 'tr': 'Bir veya daha fazla portal seçin, sonra taşımak için MENU tuşuna basın'}, 'Backup or restore all portals, settings and user state': {'ar': 'نسخ احتياطي أو استعادة كل البوابات والإعدادات وحالة المستخدم', 'de': 'Alle Portale, Einstellungen und Benutzerdaten sichern oder wiederherstellen', 'fr': 'Sauvegarder ou restaurer tous les portails, réglages et données utilisateur', 'tr': 'Tüm portalları, ayarları ve kullanıcı durumunu yedekleyin veya geri yükleyin'}, '%d disabled': {'ar': '%d معطلة', 'de': '%d deaktiviert', 'fr': '%d désactivé(s)', 'tr': '%d devre dışı'}, 'Disabled portals': {'ar': 'البوابات المعطلة', 'de': 'Deaktivierte Portale', 'fr': 'Portails désactivés', 'tr': 'Devre dışı portallar'}, '%d portals • Multi-select': {'ar': '%d بوابات • تحديد متعدد', 'de': '%d Portale • Mehrfachauswahl', 'fr': '%d portails • Sélection multiple', 'tr': '%d portal • Çoklu seçim'}})


# R143: localization closure keys discovered by source-vs-catalog audit.
_T.update({'Recent searches': {'ar': 'عمليات البحث الأخيرة', 'de': 'Letzte Suchen', 'fr': 'Recherches récentes', 'tr': 'Son aramalar'},
 'Hero failed': {'ar': 'فشل Hero', 'de': 'Hero fehlgeschlagen', 'fr': 'Échec du Hero', 'tr': 'Hero başarısız'},
 'Clean Display Names': {'ar': 'تنظيف أسماء العرض', 'de': 'Anzeigenamen bereinigen', 'fr': 'Nettoyer les noms affichés', 'tr': 'Görünen Adları Temizle'},
 'ON shows the cleaned Ultra Stalker names across Movies, Series, Live and Player screens. OFF shows the provider names as received; TMDB matching and artwork lookup stay unchanged.': {'ar': 'يعرض '
                                                                                                                                                                                               'ON '
                                                                                                                                                                                               'أسماء '
                                                                                                                                                                                               'Ultra '
                                                                                                                                                                                               'Stalker '
                                                                                                                                                                                               'المنظفة '
                                                                                                                                                                                               'في '
                                                                                                                                                                                               'شاشات '
                                                                                                                                                                                               'الأفلام '
                                                                                                                                                                                               'والمسلسلات '
                                                                                                                                                                                               'والبث '
                                                                                                                                                                                               'المباشر '
                                                                                                                                                                                               'والمشغل. '
                                                                                                                                                                                               'يعرض '
                                                                                                                                                                                               'OFF '
                                                                                                                                                                                               'أسماء '
                                                                                                                                                                                               'المزود '
                                                                                                                                                                                               'كما '
                                                                                                                                                                                               'وردت؛ '
                                                                                                                                                                                               'تظل '
                                                                                                                                                                                               'مطابقة '
                                                                                                                                                                                               'TMDB '
                                                                                                                                                                                               'والبحث '
                                                                                                                                                                                               'عن '
                                                                                                                                                                                               'الصور '
                                                                                                                                                                                               'دون '
                                                                                                                                                                                               'تغيير.',
                                                                                                                                                                                         'de': 'EIN '
                                                                                                                                                                                               'zeigt '
                                                                                                                                                                                               'die '
                                                                                                                                                                                               'bereinigten '
                                                                                                                                                                                               'Ultra-Stalker-Namen '
                                                                                                                                                                                               'in '
                                                                                                                                                                                               'Filme-, '
                                                                                                                                                                                               'Serien-, '
                                                                                                                                                                                               'Live- '
                                                                                                                                                                                               'und '
                                                                                                                                                                                               'Player-Ansichten. '
                                                                                                                                                                                               'AUS '
                                                                                                                                                                                               'zeigt '
                                                                                                                                                                                               'die '
                                                                                                                                                                                               'vom '
                                                                                                                                                                                               'Anbieter '
                                                                                                                                                                                               'gelieferten '
                                                                                                                                                                                               'Namen; '
                                                                                                                                                                                               'TMDB-Abgleich '
                                                                                                                                                                                               'und '
                                                                                                                                                                                               'Artwork-Suche '
                                                                                                                                                                                               'bleiben '
                                                                                                                                                                                               'unverändert.',
                                                                                                                                                                                         'fr': 'ACTIVÉ '
                                                                                                                                                                                               'affiche '
                                                                                                                                                                                               'les '
                                                                                                                                                                                               'noms '
                                                                                                                                                                                               'Ultra '
                                                                                                                                                                                               'Stalker '
                                                                                                                                                                                               'nettoyés '
                                                                                                                                                                                               'dans '
                                                                                                                                                                                               'les '
                                                                                                                                                                                               'écrans '
                                                                                                                                                                                               'Films, '
                                                                                                                                                                                               'Séries, '
                                                                                                                                                                                               'Direct '
                                                                                                                                                                                               'et '
                                                                                                                                                                                               'Lecteur. '
                                                                                                                                                                                               'DÉSACTIVÉ '
                                                                                                                                                                                               'affiche '
                                                                                                                                                                                               'les '
                                                                                                                                                                                               'noms '
                                                                                                                                                                                               'reçus '
                                                                                                                                                                                               'du '
                                                                                                                                                                                               'fournisseur '
                                                                                                                                                                                               '; '
                                                                                                                                                                                               'la '
                                                                                                                                                                                               'correspondance '
                                                                                                                                                                                               'TMDB '
                                                                                                                                                                                               'et '
                                                                                                                                                                                               'la '
                                                                                                                                                                                               'recherche '
                                                                                                                                                                                               'd’illustrations '
                                                                                                                                                                                               'restent '
                                                                                                                                                                                               'inchangées.',
                                                                                                                                                                                         'tr': 'AÇIK, '
                                                                                                                                                                                               'Filmler, '
                                                                                                                                                                                               'Diziler, '
                                                                                                                                                                                               'Canlı '
                                                                                                                                                                                               've '
                                                                                                                                                                                               'Oynatıcı '
                                                                                                                                                                                               'ekranlarında '
                                                                                                                                                                                               'temizlenmiş '
                                                                                                                                                                                               'Ultra '
                                                                                                                                                                                               'Stalker '
                                                                                                                                                                                               'adlarını '
                                                                                                                                                                                               'gösterir. '
                                                                                                                                                                                               'KAPALI, '
                                                                                                                                                                                               'sağlayıcıdan '
                                                                                                                                                                                               'gelen '
                                                                                                                                                                                               'adları '
                                                                                                                                                                                               'gösterir; '
                                                                                                                                                                                               'TMDB '
                                                                                                                                                                                               'eşleştirmesi '
                                                                                                                                                                                               've '
                                                                                                                                                                                               'görsel '
                                                                                                                                                                                               'araması '
                                                                                                                                                                                               'değişmez.'},
 'Disable %d selected portals?': {'ar': 'تعطيل %d بوابات محددة؟',
                                  'de': '%d ausgewählte Portale deaktivieren?',
                                  'fr': 'Désactiver %d portails sélectionnés ?',
                                  'tr': 'Seçilen %d portal devre dışı bırakılsın mı?'},
 'Deleted %d portals': {'ar': 'تم حذف %d بوابات', 'de': '%d Portale gelöscht', 'fr': '%d portails supprimés', 'tr': '%d portal silindi'},
 'Deleted %d portals • %d failed': {'ar': 'تم حذف %d بوابات • فشل %d',
                                    'de': '%d Portale gelöscht • %d fehlgeschlagen',
                                    'fr': '%d portails supprimés • %d échec(s)',
                                    'tr': '%d portal silindi • %d başarısız'}})


# R143: Language & Metadata section keys.
_T.update({'Language & Metadata': {'ar': 'اللغة والبيانات الوصفية', 'de': 'Sprache & Metadaten', 'fr': 'Langue & Métadonnées', 'tr': 'Dil & Meta Veriler'},
 'Interface language and movie/series description language.': {'ar': 'لغة الواجهة ولغة وصف الأفلام والمسلسلات.',
                                                               'de': 'Sprache der Oberfläche und Sprache der Film-/Serienbeschreibungen.',
                                                               'fr': 'Langue de l’interface et langue des descriptions des films/séries.',
                                                               'tr': 'Arayüz dili ve film/dizi açıklama dili.'},
 'Interface Language': {'ar': 'لغة الواجهة', 'de': 'Oberflächensprache', 'fr': 'Langue de l’interface', 'tr': 'Arayüz Dili'},
 'Font size, menu entry and visual theme.': {'ar': 'حجم الخط، إدخال القائمة والمظهر المرئي.',
                                             'de': 'Schriftgröße, Menüeintrag und visuelles Design.',
                                             'fr': 'Taille du texte, entrée de menu et thème visuel.',
                                             'tr': 'Yazı boyutu, menü girişi ve görsel tema.'}})


# R143: visible runtime localization closure keys.
_T.update({'Download HDD is unavailable': {'ar': 'قرص تنزيلات HDD غير متاح',
                                 'de': 'Download-HDD ist nicht verfügbar',
                                 'fr': 'Le disque HDD des téléchargements est indisponible',
                                 'tr': 'İndirme HDD diski kullanılamıyor'},
 'Invalid download': {'ar': 'تنزيل غير صالح', 'de': 'Ungültiger Download', 'fr': 'Téléchargement non valide', 'tr': 'Geçersiz indirme'},
 'Already downloaded': {'ar': 'تم التنزيل بالفعل', 'de': 'Bereits heruntergeladen', 'fr': 'Déjà téléchargé', 'tr': 'Zaten indirildi'},
 'Already queued': {'ar': 'مضاف بالفعل إلى قائمة الانتظار', 'de': 'Bereits in der Warteschlange', 'fr': 'Déjà dans la file d’attente', 'tr': 'Zaten sırada'},
 'Invalid download path': {'ar': 'مسار تنزيل غير صالح', 'de': 'Ungültiger Download-Pfad', 'fr': 'Chemin de téléchargement non valide', 'tr': 'Geçersiz indirme yolu'},
 'Added to downloads': {'ar': 'تمت الإضافة إلى التنزيلات', 'de': 'Zu Downloads hinzugefügt', 'fr': 'Ajouté aux téléchargements', 'tr': 'İndirmelere eklendi'},
 'QUEUED': {'ar': 'في الانتظار', 'de': 'WARTESCHLANGE', 'fr': 'EN ATTENTE', 'tr': 'SIRADA'},
 'RESOLVING': {'ar': 'جارٍ تجهيز الرابط', 'de': 'LINK WIRD AUFGELÖST', 'fr': 'RÉSOLUTION DU LIEN', 'tr': 'BAĞLANTI ÇÖZÜMLENİYOR'},
 'DOWNLOADING': {'ar': 'جارٍ التنزيل', 'de': 'WIRD HERUNTERGELADEN', 'fr': 'TÉLÉCHARGEMENT', 'tr': 'İNDİRİLİYOR'},
 'COMPLETED': {'ar': 'مكتمل', 'de': 'ABGESCHLOSSEN', 'fr': 'TERMINÉ', 'tr': 'TAMAMLANDI'},
 'CANCELLED': {'ar': 'ملغي', 'de': 'ABGEBROCHEN', 'fr': 'ANNULÉ', 'tr': 'İPTAL EDİLDİ'},
 'Rating %s': {'ar': 'التقييم %s', 'de': 'Bewertung %s', 'fr': 'Note %s', 'tr': 'Puan %s'},
 'Ultra Stalker stream': {'ar': 'بث Ultra Stalker', 'de': 'Ultra-Stalker-Stream', 'fr': 'Flux Ultra Stalker', 'tr': 'Ultra Stalker yayını'},
 'Resume • Audio • Subtitles': {'ar': 'استئناف • الصوت • الترجمة',
                                'de': 'Fortsetzen • Audio • Untertitel',
                                'fr': 'Reprendre • Audio • Sous-titres',
                                'tr': 'Devam • Ses • Altyazılar'},
 'EPG information when available': {'ar': 'معلومات EPG عند توفرها', 'de': 'EPG-Informationen, falls verfügbar', 'fr': 'Informations EPG si disponibles', 'tr': 'Varsa EPG bilgisi'},
 'Retrying stream • %d/3': {'ar': 'إعادة محاولة البث • %d/3', 'de': 'Stream wird erneut versucht • %d/3', 'fr': 'Nouvelle tentative du flux • %d/3', 'tr': 'Yayın yeniden deneniyor • %d/3'}})

# V9.1.1 compact release highlights shown after a safe upgrade.
_T.update({'• Server + Live search • History up to 100\n• Aspect Ratio controls in Player\n• Portal isolation + poster identity lock\n• Native SubsSupport + SubsSupportPro\n• Pause while choosing subtitles + session restore\n• 29-language Auto-Fit + faster Splash': {'ar': '• بحث السيرفرات والبث المباشر • سجل حتى 100\n• التحكم بنسبة العرض داخل المشغل\n• عزل البورتالات + تثبيت هوية البوستر\n• دمج Native لـ SubsSupport وSubsSupportPro\n• إيقاف أثناء اختيار الترجمة + استرجاع الجلسة\n• Auto-Fit لـ29 لغة + Splash أسرع', 'de': '• Server- + Live-Suche • Verlauf bis 100\n• Seitenverhältnis-Steuerung im Player\n• Portal-Isolierung + Poster-Identitätsschutz\n• Native SubsSupport + SubsSupportPro\n• Pause bei Untertitelauswahl + Sitzungswiederherstellung\n• Auto-Fit für 29 Sprachen + schnellerer Splash', 'fr': '• Recherche serveurs + Live • Historique jusqu’à 100\n• Contrôle du format d’image dans le lecteur\n• Isolation des portails + verrou d’identité des affiches\n• SubsSupport + SubsSupportPro natifs\n• Pause pendant le choix des sous-titres + restauration de session\n• Auto-Fit pour 29 langues + Splash plus rapide', 'tr': '• Sunucu + Canlı arama • 100’e kadar geçmiş\n• Oynatıcıda En-Boy Oranı kontrolleri\n• Portal yalıtımı + poster kimliği kilidi\n• Yerel SubsSupport + SubsSupportPro\n• Altyazı seçerken duraklatma + oturum geri yükleme\n• 29 dil Auto-Fit + daha hızlı Splash'}})

# V9.1.1 release-card action.
_T.update({"Continue": {'ar': 'متابعة', 'de': 'Weiter', 'fr': 'Continuer', 'tr': 'Devam'}})

def _settings_signature():
    try:
        st = os.stat(CONFIG_FILE)
        return (getattr(st, "st_mtime_ns", int(st.st_mtime * 1000000000)), st.st_size)
    except OSError:
        return None


def _read_language():
    global _CACHE_SIGNATURE, _CACHE_LANGUAGE
    sig = _settings_signature()
    with _LOCK:
        if _LANG_OVERRIDE in SUPPORTED_LANGUAGES:
            return _LANG_OVERRIDE
        if sig == _CACHE_SIGNATURE:
            return _CACHE_LANGUAGE
        code = "en"
        try:
            if sig and os.path.getsize(CONFIG_FILE) <= 1024 * 1024:
                with open(CONFIG_FILE, "r", encoding="utf-8") as handle:
                    data = json.load(handle)
                candidate = str((data or {}).get("plugin_language") or "en").strip().lower()
                if candidate in SUPPORTED_LANGUAGES:
                    code = candidate
        except Exception:
            code = "en"
        _CACHE_SIGNATURE = sig
        _CACHE_LANGUAGE = code
        return code


def set_plugin_language(code):
    global _LANG_OVERRIDE, _CACHE_LANGUAGE
    code = str(code or "en").strip().lower()
    if code not in SUPPORTED_LANGUAGES:
        code = "en"
    with _LOCK:
        _LANG_OVERRIDE = code
        _CACHE_LANGUAGE = code
    return code


def clear_language_override():
    global _LANG_OVERRIDE, _CACHE_SIGNATURE
    with _LOCK:
        _LANG_OVERRIDE = None
        _CACHE_SIGNATURE = None


def current_language():
    return _read_language()


def language_name(code=None):
    return interface_language_name(code or _read_language())


def _external_pack(code):
    code = normalize_interface_code(code)
    with _LOCK:
        if code in _EXTERNAL_PACKS:
            return _EXTERNAL_PACKS.get(code) or {}
    data = {}
    try:
        path = pack_path(code)
        if path and os.path.isfile(path) and os.path.getsize(path) <= 4 * 1024 * 1024:
            with open(path, "r", encoding="utf-8") as handle:
                value = json.load(handle)
            if isinstance(value, dict):
                value = value.get("translations") if isinstance(value.get("translations"), dict) else {}
                data = {str(k): str(v) for k, v in value.items() if v not in (None, "")}
    except Exception:
        data = {}
    with _LOCK:
        _EXTERNAL_PACKS[code] = data
    return data


def _arabic_gettext(text):
    global _AR_GETTEXT
    try:
        if _AR_GETTEXT is None:
            path = os.path.join(LOCALE_DIR, "ar", "LC_MESSAGES", "UltraStalker.mo")
            with open(path, "rb") as handle:
                _AR_GETTEXT = gettext.GNUTranslations(handle)
        value = _AR_GETTEXT.gettext(text)
        return value if value != text else None
    except Exception:
        return None


def translate(text):
    if text is None:
        return ""
    original = str(text)
    code = _read_language()
    if code == "en" or not original:
        return original
    row = _T.get(original)
    if row:
        value = row.get(code)
        if value:
            return value
    if code == "ar":
        value = _arabic_gettext(original)
        if value:
            return value
    if code not in ("ar", "de", "fr", "tr"):
        value = _external_pack(code).get(original)
        if value:
            return value
    return original
# R145 wide localization closure additions.
_T.update({
    'Catch-up': {'ar': 'إعادة المشاهدة', 'de': 'Nachholen', 'fr': 'Rattrapage', 'tr': 'Geri izle'},
    '%d sources': {'ar': '%d مصادر', 'de': '%d Quellen', 'fr': '%d source(s)', 'tr': '%d kaynak'},
    'No profiles found': {'ar': 'لم يتم العثور على ملفات تعريف', 'de': 'Keine Profile gefunden', 'fr': 'Aucun profil trouvé', 'tr': 'Profil bulunamadı'},
    'Add a Portal or M3U source': {'ar': 'أضف بوابة أو مصدر M3U', 'de': 'Portal oder M3U-Quelle hinzufügen', 'fr': 'Ajouter un portail ou une source M3U', 'tr': 'Portal veya M3U kaynağı ekleyin'},
    'No expiry date': {'ar': 'لا يوجد تاريخ انتهاء', 'de': 'Kein Ablaufdatum', 'fr': 'Aucune date d’expiration', 'tr': 'Son kullanma tarihi yok'},
    'Top Rated': {'ar': 'الأعلى تقييمًا', 'de': 'Am besten bewertet', 'fr': 'Les mieux notés', 'tr': 'En yüksek puanlı'},
    'Newest': {'ar': 'الأحدث', 'de': 'Neueste', 'fr': 'Plus récents', 'tr': 'En yeni'},
    'Oldest': {'ar': 'الأقدم', 'de': 'Älteste', 'fr': 'Plus anciens', 'tr': 'En eski'},
    'No content returned': {'ar': 'لم يتم إرجاع أي محتوى', 'de': 'Keine Inhalte zurückgegeben', 'fr': 'Aucun contenu renvoyé', 'tr': 'İçerik döndürülmedi'},
    'Try another category': {'ar': 'جرّب فئة أخرى', 'de': 'Andere Kategorie versuchen', 'fr': 'Essayez une autre catégorie', 'tr': 'Başka bir kategori deneyin'},
    'Search content': {'ar': 'البحث في المحتوى', 'de': 'Inhalte suchen', 'fr': 'Rechercher du contenu', 'tr': 'İçerikte ara'},
    'No programme description': {'ar': 'لا يوجد وصف للبرنامج', 'de': 'Keine Programmbeschreibung', 'fr': 'Aucune description du programme', 'tr': 'Program açıklaması yok'}
})

# R146 dynamic localization closure.
_T.update({'Country': {'de': 'Land', 'tr': 'Ülke', 'fr': 'Pays', 'ar': 'البلد'}, 'TMDb linked': {'de': 'Mit TMDb verknüpft', 'tr': 'TMDb bağlantılı', 'fr': 'Lié à TMDb', 'ar': 'مرتبط بـ TMDb'}, 'White': {'de': 'Weiß', 'tr': 'Beyaz', 'fr': 'Blanc', 'ar': 'أبيض'}, 'Cream': {'de': 'Creme', 'tr': 'Krem', 'fr': 'Crème', 'ar': 'كريمي'}, 'Yellow': {'de': 'Gelb', 'tr': 'Sarı', 'fr': 'Jaune', 'ar': 'أصفر'}, 'Gold': {'de': 'Gold', 'tr': 'Altın', 'fr': 'Or', 'ar': 'ذهبي'}, 'Orange': {'de': 'Orange', 'tr': 'Turuncu', 'fr': 'Orange', 'ar': 'برتقالي'}, 'Lime': {'de': 'Limette', 'tr': 'Limon yeşili', 'fr': 'Citron vert', 'ar': 'ليموني'}, 'Green': {'de': 'Grün', 'tr': 'Yeşil', 'fr': 'Vert', 'ar': 'أخضر'}, 'Mint': {'de': 'Mint', 'tr': 'Nane', 'fr': 'Menthe', 'ar': 'نعناعي'}, 'Cyan': {'de': 'Cyan', 'tr': 'Camgöbeği', 'fr': 'Cyan', 'ar': 'سماوي'}, 'Sky Blue': {'de': 'Himmelblau', 'tr': 'Gök mavisi', 'fr': 'Bleu ciel', 'ar': 'أزرق سماوي'}, 'Blue': {'de': 'Blau', 'tr': 'Mavi', 'fr': 'Bleu', 'ar': 'أزرق'}, 'Lavender': {'de': 'Lavendel', 'tr': 'Lavanta', 'fr': 'Lavande', 'ar': 'لافندر'}, 'Purple': {'de': 'Violett', 'tr': 'Mor', 'fr': 'Violet', 'ar': 'بنفسجي'}, 'Pink': {'de': 'Rosa', 'tr': 'Pembe', 'fr': 'Rose', 'ar': 'وردي'}, 'Coral': {'de': 'Koralle', 'tr': 'Mercan', 'fr': 'Corail', 'ar': 'مرجاني'}, 'Very High': {'de': 'Sehr hoch', 'tr': 'Çok yüksek', 'fr': 'Très haut', 'ar': 'مرتفع جدًا'}, 'High': {'de': 'Hoch', 'tr': 'Yüksek', 'fr': 'Haut', 'ar': 'مرتفع'}, 'Upper': {'de': 'Oben', 'tr': 'Üst', 'fr': 'Supérieur', 'ar': 'أعلى'}, 'Lower': {'de': 'Unten', 'tr': 'Alt', 'fr': 'Inférieur', 'ar': 'أسفل'}, 'Low': {'de': 'Niedrig', 'tr': 'Düşük', 'fr': 'Bas', 'ar': 'منخفض'}, 'Very Low': {'de': 'Sehr niedrig', 'tr': 'Çok düşük', 'fr': 'Très bas', 'ar': 'منخفض جدًا'}, '4:3 Letterbox': {'de': '4:3 Letterbox', 'tr': '4:3 Sinemaskop', 'fr': '4:3 Letterbox', 'ar': '4:3 بأشرطة أفقية'}, '4:3 PanScan': {'de': '4:3 Pan & Scan', 'tr': '4:3 Kaydır ve Tara', 'fr': '4:3 Pan & Scan', 'ar': '4:3 ملء وقص'}, '16:9': {'de': '16:9', 'tr': '16:9', 'fr': '16:9', 'ar': '16:9'}, '16:9 Always': {'de': '16:9 Immer', 'tr': '16:9 Her zaman', 'fr': '16:9 Toujours', 'ar': '16:9 دائمًا'}, '16:10 Letterbox': {'de': '16:10 Letterbox', 'tr': '16:10 Sinemaskop', 'fr': '16:10 Letterbox', 'ar': '16:10 بأشرطة أفقية'}, '16:10 PanScan': {'de': '16:10 Pan & Scan', 'tr': '16:10 Kaydır ve Tara', 'fr': '16:10 Pan & Scan', 'ar': '16:10 ملء وقص'}, '16:9 Letterbox': {'de': '16:9 Letterbox', 'tr': '16:9 Sinemaskop', 'fr': '16:9 Letterbox', 'ar': '16:9 بأشرطة أفقية'}})

# R146 Web Cleaner localization closure.
_T.update({'Username': {'ar': 'اسم المستخدم', 'de': 'Benutzername', 'fr': 'Nom d’utilisateur', 'tr': 'Kullanıcı adı'}, 'Password': {'ar': 'كلمة المرور', 'de': 'Passwort', 'fr': 'Mot de passe', 'tr': 'Parola'}, 'Type': {'ar': 'النوع', 'de': 'Typ', 'fr': 'Type', 'tr': 'Tür'}, 'WORKING': {'ar': 'يعمل', 'de': 'FUNKTIONSFÄHIG', 'fr': 'FONCTIONNEL', 'tr': 'ÇALIŞIYOR'}, 'DUPLICATES': {'ar': 'مكررات', 'de': 'DUPLIKATE', 'fr': 'DOUBLONS', 'tr': 'YİNELENENLER'}, 'INVALID URL': {'ar': 'رابط غير صالح', 'de': 'UNGÜLTIGE URL', 'fr': 'URL NON VALIDE', 'tr': 'GEÇERSİZ URL'}, 'UNREACHABLE': {'ar': 'غير قابل للوصول', 'de': 'NICHT ERREICHBAR', 'fr': 'INJOIGNABLE', 'tr': 'ULAŞILAMIYOR'}, 'OTHER': {'ar': 'أخرى', 'de': 'SONSTIGE', 'fr': 'AUTRE', 'tr': 'DİĞER'}, 'Pending': {'ar': 'قيد الانتظار', 'de': 'Ausstehend', 'fr': 'En attente', 'tr': 'Beklemede'}, 'Incorrect pairing code.': {'ar': 'رمز الاقتران غير صحيح.', 'de': 'Falscher Kopplungscode.', 'fr': 'Code d’association incorrect.', 'tr': 'Eşleştirme kodu yanlış.'}})

# R147 subtitle hub localization.
_T.update({
    'Local subtitles': {'ar': 'الترجمة المحلية', 'de': 'Lokale Untertitel', 'fr': 'Sous-titres locaux', 'tr': 'Yerel altyazılar'},
    'Online subtitles': {'ar': 'الترجمة الأونلاين', 'de': 'Online-Untertitel', 'fr': 'Sous-titres en ligne', 'tr': 'Çevrimiçi altyazılar'},
})


# R210 Splash first-run copy.  These labels intentionally mirror
# the existing Settings clean/raw meaning while remaining short enough for the
# lightweight first-run Splash lane.
_T.update({
    'Choose Language': {'ar': 'اختر اللغة', 'de': 'Sprache wählen', 'fr': 'Choisir la langue', 'tr': 'Dil seçin'},
    'Clean': {'ar': 'منظّفة', 'de': 'Bereinigt', 'fr': 'Nettoyés', 'tr': 'Temizlenmiş'},
    'Original': {'ar': 'خام', 'de': 'Original', 'fr': 'Originaux', 'tr': 'Orijinal'},
    'Do you want plugin names cleaned or original?': {
        'ar': 'هل تريد النصوص في البلجن منظّفة أم خام؟',
        'de': 'Sollen die Namen im Plugin bereinigt oder original angezeigt werden?',
        'fr': 'Voulez-vous les noms nettoyés ou originaux dans le plugin ?',
        'tr': 'Eklentide adlar temizlenmiş mi yoksa orijinal mi görünsün?'
    },
})


# R251 SubSource + optional SubsSupport bridge.
_T.update({
    'SubsSupport': {'ar':'SubsSupport','de':'SubsSupport','fr':'SubsSupport','tr':'SubsSupport'},
    'SubsSupport plugin is not installed.': {'ar':'إضافة SubsSupport غير مثبتة على الجهاز.','de':'Das SubsSupport-Plugin ist nicht installiert.','fr':'Le plugin SubsSupport n’est pas installé.','tr':'SubsSupport eklentisi yüklü değil.'},
    'SubsSupport could not be opened.': {'ar':'تعذر فتح إضافة SubsSupport.','de':'SubsSupport konnte nicht geöffnet werden.','fr':'Impossible d’ouvrir SubsSupport.','tr':'SubsSupport açılamadı.'},
    'SubSource API key is not configured.': {'ar':'مفتاح API لـ SubSource غير مُعد.','de':'Der SubSource-API-Schlüssel ist nicht konfiguriert.','fr':'La clé API SubSource n’est pas configurée.','tr':'SubSource API anahtarı yapılandırılmamış.'},
    'Online Subtitles • SubDL': {'ar':'الترجمة الأونلاين • SubDL','de':'Online-Untertitel • SubDL','fr':'Sous-titres en ligne • SubDL','tr':'Çevrimiçi altyazılar • SubDL'},
    'Online Subtitles • SubSource': {'ar':'الترجمة الأونلاين • SubSource','de':'Online-Untertitel • SubSource','fr':'Sous-titres en ligne • SubSource','tr':'Çevrimiçi altyazılar • SubSource'},
    'SubSource API key': {'ar':'مفتاح API لـ SubSource','de':'SubSource-API-Schlüssel','fr':'Clé API SubSource','tr':'SubSource API anahtarı'},
    'SubSource API key saved privately in api_keys.conf': {'ar':'تم حفظ مفتاح SubSource بشكل خاص في api_keys.conf','de':'SubSource-API-Schlüssel privat in api_keys.conf gespeichert','fr':'Clé API SubSource enregistrée de façon privée dans api_keys.conf','tr':'SubSource API anahtarı api_keys.conf içinde özel olarak kaydedildi'},
    'SubSource API key cleared': {'ar':'تم مسح مفتاح SubSource','de':'SubSource-API-Schlüssel gelöscht','fr':'Clé API SubSource effacée','tr':'SubSource API anahtarı temizlendi'},
    'SEARCHING  •  SUBSOURCE %s': {'ar':'جارٍ البحث  •  SUBSOURCE %s','de':'SUCHE  •  SUBSOURCE %s','fr':'RECHERCHE  •  SUBSOURCE %s','tr':'ARANIYOR  •  SUBSOURCE %s'},
})


# R270 release-hygiene localization closure.
_T.update({'%s • LEFT / RIGHT section • UP / DOWN item • OK resume': {'ar': '%s • LEFT / RIGHT تبديل • UP القائمة • OK استكمال',
                                                            'de': '%s • LINKS / RECHTS Wechseln • HOCH Menü • OK Fortsetzen',
                                                            'fr': '%s • GAUCHE / DROITE changer • HAUT menu • OK reprendre',
                                                            'tr': '%s • SOL / SAĞ değiştir • YUKARI menü • OK devam et'},
 'Backdrop cache complete': {'ar': 'تخزين الصور • تم',
                             'de': 'Bilder zwischenspeichern • Fertig',
                             'fr': 'Mettre les illustrations en cache • Terminé',
                             'tr': 'Görselleri Önbelleğe Al • Bitti'},
 'Backdrop cache incomplete': {'ar': 'تخزين الصور • غير مكتمل',
                               'de': 'Bilder zwischenspeichern • unvollständig',
                               'fr': 'Mettre les illustrations en cache • incomplet',
                               'tr': 'Görselleri Önbelleğe Al • tamamlanmadı'},
 'Backdrop cache • %d/%d • new %d • already %d • failed %d': {'ar': 'تخزين الصور • %d/%d • جديد %d • محفوظ %d • فشل %d',
                                                              'de': 'Bilder zwischenspeichern • %d/%d • neu %d • Zwischengespeichert %d • fehlgeschlagen %d',
                                                              'fr': 'Mettre les illustrations en cache • %d/%d • nouveau %d • En cache %d • échec %d',
                                                              'tr': 'Görselleri Önbelleğe Al • %d/%d • yeni %d • Önbellekte %d • başarısız %d'},
 'Backdrop cache • checking %d/%d • new %d • already %d': {'ar': 'تخزين الصور • جارٍ الفحص %d/%d • جديد %d • محفوظ %d',
                                                           'de': 'Bilder zwischenspeichern • Wird geprüft %d/%d • neu %d • Zwischengespeichert %d',
                                                           'fr': 'Mettre les illustrations en cache • Vérification %d/%d • nouveau %d • En cache %d',
                                                           'tr': 'Görselleri Önbelleğe Al • Kontrol ediliyor %d/%d • yeni %d • Önbellekte %d'},
 'Choose movie and series TMDb information language independently from the interface. Missing fields fall back to English; Clean Names is separate.': {'ar': 'اختر '
                                                                                                                                                             'لغة '
                                                                                                                                                             'وصف '
                                                                                                                                                             'الأفلام '
                                                                                                                                                             'والمسلسلات '
                                                                                                                                                             'بشكل '
                                                                                                                                                             'مستقل '
                                                                                                                                                             'عن '
                                                                                                                                                             'لغة '
                                                                                                                                                             'الواجهة. '
                                                                                                                                                             'إذا '
                                                                                                                                                             'لم '
                                                                                                                                                             'يتوفر '
                                                                                                                                                             'الوصف '
                                                                                                                                                             'باللغة '
                                                                                                                                                             'المختارة، '
                                                                                                                                                             'تُستخدم '
                                                                                                                                                             'الإنجليزية '
                                                                                                                                                             'فقط '
                                                                                                                                                             'كبديل.  '
                                                                                                                                                             'Clean '
                                                                                                                                                             'Names: '
                                                                                                                                                             'منفصل.',
                                                                                                                                                       'de': 'Wählen '
                                                                                                                                                             'Sie '
                                                                                                                                                             'die '
                                                                                                                                                             'Beschreibungssprache '
                                                                                                                                                             'für '
                                                                                                                                                             'Filme '
                                                                                                                                                             'und '
                                                                                                                                                             'Serien '
                                                                                                                                                             'unabhängig '
                                                                                                                                                             'von '
                                                                                                                                                             'der '
                                                                                                                                                             'Oberfläche. '
                                                                                                                                                             'Fehlt '
                                                                                                                                                             'der '
                                                                                                                                                             'Text '
                                                                                                                                                             'in '
                                                                                                                                                             'der '
                                                                                                                                                             'gewählten '
                                                                                                                                                             'Sprache, '
                                                                                                                                                             'wird '
                                                                                                                                                             'ausschließlich '
                                                                                                                                                             'Englisch '
                                                                                                                                                             'verwendet.  '
                                                                                                                                                             'Clean '
                                                                                                                                                             'Names: '
                                                                                                                                                             'getrennt.',
                                                                                                                                                       'fr': 'Choisissez '
                                                                                                                                                             'la '
                                                                                                                                                             'langue '
                                                                                                                                                             'des '
                                                                                                                                                             'descriptions '
                                                                                                                                                             'des '
                                                                                                                                                             'films '
                                                                                                                                                             'et '
                                                                                                                                                             'séries '
                                                                                                                                                             'indépendamment '
                                                                                                                                                             'de '
                                                                                                                                                             'l’interface. '
                                                                                                                                                             'Si '
                                                                                                                                                             'le '
                                                                                                                                                             'texte '
                                                                                                                                                             'manque '
                                                                                                                                                             'dans '
                                                                                                                                                             'la '
                                                                                                                                                             'langue '
                                                                                                                                                             'choisie, '
                                                                                                                                                             'seul '
                                                                                                                                                             'l’anglais '
                                                                                                                                                             'est '
                                                                                                                                                             'utilisé '
                                                                                                                                                             'en '
                                                                                                                                                             'remplacement.  '
                                                                                                                                                             'Clean '
                                                                                                                                                             'Names: '
                                                                                                                                                             'séparé.',
                                                                                                                                                       'tr': 'Film '
                                                                                                                                                             've '
                                                                                                                                                             'dizi '
                                                                                                                                                             'açıklama '
                                                                                                                                                             'dilini '
                                                                                                                                                             'arayüzden '
                                                                                                                                                             'bağımsız '
                                                                                                                                                             'seçin. '
                                                                                                                                                             'Seçilen '
                                                                                                                                                             'dilde '
                                                                                                                                                             'metin '
                                                                                                                                                             'yoksa '
                                                                                                                                                             'yalnızca '
                                                                                                                                                             'İngilizce '
                                                                                                                                                             'yedek '
                                                                                                                                                             'olarak '
                                                                                                                                                             'kullanılır.  '
                                                                                                                                                             'Clean '
                                                                                                                                                             'Names: '
                                                                                                                                                             'ayrı.'},
 'Connected': {'ar': 'متصل', 'de': 'Verbunden', 'fr': 'Connecté', 'tr': 'Bağlı'},
 'Continue watching  •  %d%%': {'ar': 'متابعة المشاهدة  •  %d%%',
                                'de': 'Weiterschauen  •  %d%%',
                                'fr': 'Continuer à regarder  •  %d%%',
                                'tr': 'İzlemeye Devam  •  %d%%'},
 'Episode  •  open': {'ar': 'حلقة  •  فتح', 'de': 'Episode  •  Öffnen', 'fr': 'Épisode  •  Ouvrir', 'tr': 'Bölüm  •  Aç'},
 'Live TV  •  reopen channel': {'ar': 'البث المباشر  •  إعادة فتح القناة',
                                'de': 'Live-TV  •  Kanal erneut öffnen',
                                'fr': 'TV en direct  •  rouvrir la chaîne',
                                'tr': 'Canlı TV  •  kanalı yeniden aç'},
 'Movie  •  open': {'ar': 'فيلم  •  فتح', 'de': 'Film  •  Öffnen', 'fr': 'Film  •  Ouvrir', 'tr': 'Film  •  Aç'},
 'Preparing Home': {'ar': 'جارٍ التحميل • الرئيسية', 'de': 'Wird geladen • Startseite', 'fr': 'Chargement • Accueil', 'tr': 'Yükleniyor • Ana Sayfa'},
 'Preparing Home artwork': {'ar': 'جارٍ التحميل • الرئيسية artwork',
                            'de': 'Wird geladen • Startseite artwork',
                            'fr': 'Chargement • Accueil artwork',
                            'tr': 'Yükleniyor • Ana Sayfa artwork'},
 'Preparing Live categories': {'ar': 'جارٍ التحميل • البث المباشر / الفئات',
                               'de': 'Wird geladen • Live-TV / Kategorien',
                               'fr': 'Chargement • TV en direct / Catégories',
                               'tr': 'Yükleniyor • Canlı TV / Kategoriler'},
 'Preparing Movie categories': {'ar': 'جارٍ التحميل • فيلم / الفئات',
                                'de': 'Wird geladen • Film / Kategorien',
                                'fr': 'Chargement • Film / Catégories',
                                'tr': 'Yükleniyor • Film / Kategoriler'},
 'Preparing Series categories': {'ar': 'جارٍ التحميل • المسلسلات / الفئات',
                                 'de': 'Wird geladen • Serien / Kategorien',
                                 'fr': 'Chargement • Séries / Catégories',
                                 'tr': 'Yükleniyor • Diziler / Kategoriler'},
 'Preparing account': {'ar': 'جارٍ التحميل • معلومات الحساب',
                       'de': 'Wird geladen • Kontoinformationen',
                       'fr': 'Chargement • Informations du compte',
                       'tr': 'Yükleniyor • Hesap Bilgileri'},
 'Preparing recent cards': {'ar': 'جارٍ التحميل • الأحدث', 'de': 'Wird geladen • KÜRZLICH', 'fr': 'Chargement • RÉCENT', 'tr': 'Yükleniyor • SON'},
 'Put long credentials in:\n/etc/enigma2/ultrastalker/api_keys.conf\n\nTMDB_API_KEY=...\nTMDB_READ_TOKEN=...\nIMDB_API_KEY=...\nIMDB_API_ENDPOINT=... (generic mode)\nSUBDL_API_KEY=...\nSUBSOURCE_API_KEY=...\nFANART_API_KEY=...\n\nOfficial IMDb/AWS Data Exchange:\nIMDB_ACCESS_KEY_ID=...\nIMDB_SECRET_ACCESS_KEY=...\nIMDB_SESSION_TOKEN=... (optional)\nIMDB_REGION=us-east-1\nIMDB_DATASET_ID=...\nIMDB_REVISION_ID=...\nIMDB_ASSET_ID=...': {'ar': 'ضع '
                                                                                                                                                                                                                                                                                                                                                                                                                                                            'بيانات '
                                                                                                                                                                                                                                                                                                                                                                                                                                                            'الاعتماد '
                                                                                                                                                                                                                                                                                                                                                                                                                                                            'الطويلة '
                                                                                                                                                                                                                                                                                                                                                                                                                                                            'في:\n'
                                                                                                                                                                                                                                                                                                                                                                                                                                                            '/etc/enigma2/ultrastalker/api_keys.conf\n'
                                                                                                                                                                                                                                                                                                                                                                                                                                                            '\n'
                                                                                                                                                                                                                                                                                                                                                                                                                                                            'TMDB_API_KEY=...\n'
                                                                                                                                                                                                                                                                                                                                                                                                                                                            'TMDB_READ_TOKEN=...\n'
                                                                                                                                                                                                                                                                                                                                                                                                                                                            'IMDB_API_KEY=...\n'
                                                                                                                                                                                                                                                                                                                                                                                                                                                            'IMDB_API_ENDPOINT=... '
                                                                                                                                                                                                                                                                                                                                                                                                                                                            '(generic '
                                                                                                                                                                                                                                                                                                                                                                                                                                                            'mode)\n'
                                                                                                                                                                                                                                                                                                                                                                                                                                                            'SUBDL_API_KEY=...\n'
                                                                                                                                                                                                                                                                                                                                                                                                                                                            'SUBSOURCE_API_KEY=...\n'
                                                                                                                                                                                                                                                                                                                                                                                                                                                            'FANART_API_KEY=...\n'
                                                                                                                                                                                                                                                                                                                                                                                                                                                            '\n'
                                                                                                                                                                                                                                                                                                                                                                                                                                                            'تبادل '
                                                                                                                                                                                                                                                                                                                                                                                                                                                            'بيانات '
                                                                                                                                                                                                                                                                                                                                                                                                                                                            'IMDb/AWS '
                                                                                                                                                                                                                                                                                                                                                                                                                                                            'الرسمي:\n'
                                                                                                                                                                                                                                                                                                                                                                                                                                                            'IMDB_ACCESS_KEY_ID=...\n'
                                                                                                                                                                                                                                                                                                                                                                                                                                                            'IMDB_SECRET_ACCESS_KEY=...\n'
                                                                                                                                                                                                                                                                                                                                                                                                                                                            'IMDB_SESSION_TOKEN=... '
                                                                                                                                                                                                                                                                                                                                                                                                                                                            '(optional)\n'
                                                                                                                                                                                                                                                                                                                                                                                                                                                            'IMDB_REGION=us-east-1\n'
                                                                                                                                                                                                                                                                                                                                                                                                                                                            'IMDB_DATASET_ID=...\n'
                                                                                                                                                                                                                                                                                                                                                                                                                                                            'IMDB_REVISION_ID=...\n'
                                                                                                                                                                                                                                                                                                                                                                                                                                                            'IMDB_ASSET_ID=...',
                                                                                                                                                                                                                                                                                                                                                                                                                                                      'de': 'Lange '
                                                                                                                                                                                                                                                                                                                                                                                                                                                            'Zugangsdaten '
                                                                                                                                                                                                                                                                                                                                                                                                                                                            'hier '
                                                                                                                                                                                                                                                                                                                                                                                                                                                            'eintragen:\n'
                                                                                                                                                                                                                                                                                                                                                                                                                                                            '/etc/enigma2/ultrastalker/api_keys.conf\n'
                                                                                                                                                                                                                                                                                                                                                                                                                                                            '\n'
                                                                                                                                                                                                                                                                                                                                                                                                                                                            'TMDB_API_KEY=...\n'
                                                                                                                                                                                                                                                                                                                                                                                                                                                            'TMDB_READ_TOKEN=...\n'
                                                                                                                                                                                                                                                                                                                                                                                                                                                            'IMDB_API_KEY=...\n'
                                                                                                                                                                                                                                                                                                                                                                                                                                                            'IMDB_API_ENDPOINT=... '
                                                                                                                                                                                                                                                                                                                                                                                                                                                            '(generic '
                                                                                                                                                                                                                                                                                                                                                                                                                                                            'mode)\n'
                                                                                                                                                                                                                                                                                                                                                                                                                                                            'SUBDL_API_KEY=...\n'
                                                                                                                                                                                                                                                                                                                                                                                                                                                            'SUBSOURCE_API_KEY=...\n'
                                                                                                                                                                                                                                                                                                                                                                                                                                                            'FANART_API_KEY=...\n'
                                                                                                                                                                                                                                                                                                                                                                                                                                                            '\n'
                                                                                                                                                                                                                                                                                                                                                                                                                                                            'Offizieller '
                                                                                                                                                                                                                                                                                                                                                                                                                                                            'IMDb/AWS-Datenaustausch:\n'
                                                                                                                                                                                                                                                                                                                                                                                                                                                            'IMDB_ACCESS_KEY_ID=...\n'
                                                                                                                                                                                                                                                                                                                                                                                                                                                            'IMDB_SECRET_ACCESS_KEY=...\n'
                                                                                                                                                                                                                                                                                                                                                                                                                                                            'IMDB_SESSION_TOKEN=... '
                                                                                                                                                                                                                                                                                                                                                                                                                                                            '(optional)\n'
                                                                                                                                                                                                                                                                                                                                                                                                                                                            'IMDB_REGION=us-east-1\n'
                                                                                                                                                                                                                                                                                                                                                                                                                                                            'IMDB_DATASET_ID=...\n'
                                                                                                                                                                                                                                                                                                                                                                                                                                                            'IMDB_REVISION_ID=...\n'
                                                                                                                                                                                                                                                                                                                                                                                                                                                            'IMDB_ASSET_ID=...',
                                                                                                                                                                                                                                                                                                                                                                                                                                                      'fr': 'Placez '
                                                                                                                                                                                                                                                                                                                                                                                                                                                            'les '
                                                                                                                                                                                                                                                                                                                                                                                                                                                            'identifiants '
                                                                                                                                                                                                                                                                                                                                                                                                                                                            'longs '
                                                                                                                                                                                                                                                                                                                                                                                                                                                            'dans '
                                                                                                                                                                                                                                                                                                                                                                                                                                                            ':\n'
                                                                                                                                                                                                                                                                                                                                                                                                                                                            '/etc/enigma2/ultrastalker/api_keys.conf\n'
                                                                                                                                                                                                                                                                                                                                                                                                                                                            '\n'
                                                                                                                                                                                                                                                                                                                                                                                                                                                            'TMDB_API_KEY=...\n'
                                                                                                                                                                                                                                                                                                                                                                                                                                                            'TMDB_READ_TOKEN=...\n'
                                                                                                                                                                                                                                                                                                                                                                                                                                                            'IMDB_API_KEY=...\n'
                                                                                                                                                                                                                                                                                                                                                                                                                                                            'IMDB_API_ENDPOINT=... '
                                                                                                                                                                                                                                                                                                                                                                                                                                                            '(generic '
                                                                                                                                                                                                                                                                                                                                                                                                                                                            'mode)\n'
                                                                                                                                                                                                                                                                                                                                                                                                                                                            'SUBDL_API_KEY=...\n'
                                                                                                                                                                                                                                                                                                                                                                                                                                                            'SUBSOURCE_API_KEY=...\n'
                                                                                                                                                                                                                                                                                                                                                                                                                                                            'FANART_API_KEY=...\n'
                                                                                                                                                                                                                                                                                                                                                                                                                                                            '\n'
                                                                                                                                                                                                                                                                                                                                                                                                                                                            'Échange '
                                                                                                                                                                                                                                                                                                                                                                                                                                                            'de '
                                                                                                                                                                                                                                                                                                                                                                                                                                                            'données '
                                                                                                                                                                                                                                                                                                                                                                                                                                                            'officiel '
                                                                                                                                                                                                                                                                                                                                                                                                                                                            'IMDb/AWS '
                                                                                                                                                                                                                                                                                                                                                                                                                                                            ':\n'
                                                                                                                                                                                                                                                                                                                                                                                                                                                            'IMDB_ACCESS_KEY_ID=...\n'
                                                                                                                                                                                                                                                                                                                                                                                                                                                            'IMDB_SECRET_ACCESS_KEY=...\n'
                                                                                                                                                                                                                                                                                                                                                                                                                                                            'IMDB_SESSION_TOKEN=... '
                                                                                                                                                                                                                                                                                                                                                                                                                                                            '(optional)\n'
                                                                                                                                                                                                                                                                                                                                                                                                                                                            'IMDB_REGION=us-east-1\n'
                                                                                                                                                                                                                                                                                                                                                                                                                                                            'IMDB_DATASET_ID=...\n'
                                                                                                                                                                                                                                                                                                                                                                                                                                                            'IMDB_REVISION_ID=...\n'
                                                                                                                                                                                                                                                                                                                                                                                                                                                            'IMDB_ASSET_ID=...',
                                                                                                                                                                                                                                                                                                                                                                                                                                                      'tr': 'Uzun '
                                                                                                                                                                                                                                                                                                                                                                                                                                                            'kimlik '
                                                                                                                                                                                                                                                                                                                                                                                                                                                            'bilgilerini '
                                                                                                                                                                                                                                                                                                                                                                                                                                                            'şuraya '
                                                                                                                                                                                                                                                                                                                                                                                                                                                            'girin:\n'
                                                                                                                                                                                                                                                                                                                                                                                                                                                            '/etc/enigma2/ultrastalker/api_keys.conf\n'
                                                                                                                                                                                                                                                                                                                                                                                                                                                            '\n'
                                                                                                                                                                                                                                                                                                                                                                                                                                                            'TMDB_API_KEY=...\n'
                                                                                                                                                                                                                                                                                                                                                                                                                                                            'TMDB_READ_TOKEN=...\n'
                                                                                                                                                                                                                                                                                                                                                                                                                                                            'IMDB_API_KEY=...\n'
                                                                                                                                                                                                                                                                                                                                                                                                                                                            'IMDB_API_ENDPOINT=... '
                                                                                                                                                                                                                                                                                                                                                                                                                                                            '(generic '
                                                                                                                                                                                                                                                                                                                                                                                                                                                            'mode)\n'
                                                                                                                                                                                                                                                                                                                                                                                                                                                            'SUBDL_API_KEY=...\n'
                                                                                                                                                                                                                                                                                                                                                                                                                                                            'SUBSOURCE_API_KEY=...\n'
                                                                                                                                                                                                                                                                                                                                                                                                                                                            'FANART_API_KEY=...\n'
                                                                                                                                                                                                                                                                                                                                                                                                                                                            '\n'
                                                                                                                                                                                                                                                                                                                                                                                                                                                            'Resmî '
                                                                                                                                                                                                                                                                                                                                                                                                                                                            'IMDb/AWS '
                                                                                                                                                                                                                                                                                                                                                                                                                                                            'Veri '
                                                                                                                                                                                                                                                                                                                                                                                                                                                            'Değişimi:\n'
                                                                                                                                                                                                                                                                                                                                                                                                                                                            'IMDB_ACCESS_KEY_ID=...\n'
                                                                                                                                                                                                                                                                                                                                                                                                                                                            'IMDB_SECRET_ACCESS_KEY=...\n'
                                                                                                                                                                                                                                                                                                                                                                                                                                                            'IMDB_SESSION_TOKEN=... '
                                                                                                                                                                                                                                                                                                                                                                                                                                                            '(optional)\n'
                                                                                                                                                                                                                                                                                                                                                                                                                                                            'IMDB_REGION=us-east-1\n'
                                                                                                                                                                                                                                                                                                                                                                                                                                                            'IMDB_DATASET_ID=...\n'
                                                                                                                                                                                                                                                                                                                                                                                                                                                            'IMDB_REVISION_ID=...\n'
                                                                                                                                                                                                                                                                                                                                                                                                                                                            'IMDB_ASSET_ID=...'},
 'Recent': {'ar': 'الأحدث', 'de': 'KÜRZLICH', 'fr': 'RÉCENT', 'tr': 'SON'},
 'Series  •  open': {'ar': 'المسلسلات  •  فتح', 'de': 'Serien  •  Öffnen', 'fr': 'Séries  •  Ouvrir', 'tr': 'Diziler  •  Aç'},
 'Stream unavailable': {'ar': 'البث غير متاح', 'de': 'Stream nicht verfügbar', 'fr': 'Flux indisponible', 'tr': 'Yayın kullanılamıyor'},
 'SubDL API key for online movie and episode subtitles.': {'ar': 'مفتاح SubDL API • الترجمة الأونلاين (فيلم / حلقة)',
                                                           'de': 'SubDL-API-Schlüssel • Online-Untertitel (Film / Episode)',
                                                           'fr': 'Clé API SubDL • Sous-titres en ligne (Film / Épisode)',
                                                           'tr': 'SubDL API anahtarı • Çevrimiçi altyazılar (Film / Bölüm)'},
 'SubSource API key for online movie and episode subtitles.': {'ar': 'مفتاح SubSource API • الترجمة الأونلاين (فيلم / حلقة)',
                                                               'de': 'SubSource-API-Schlüssel • Online-Untertitel (Film / Episode)',
                                                               'fr': 'Clé API SubSource • Sous-titres en ligne (Film / Épisode)',
                                                               'tr': 'SubSource API anahtarı • Çevrimiçi altyazılar (Film / Bölüm)'},
 'SubSource API key save failed: %s': {'ar': 'فشل حفظ مفتاح SubSource API: %s',
                                       'de': 'Speichern des SubSource-API-Schlüssels fehlgeschlagen: %s',
                                       'fr': 'Échec de l’enregistrement de la clé API SubSource : %s',
                                       'tr': 'SubSource API anahtarı kaydedilemedi: %s'},
 'TMDb Information Language': {'ar': 'TMDb • لغة الوصف',
                               'de': 'TMDb • Beschreibungssprache',
                               'fr': 'TMDb • Langue de description',
                               'tr': 'TMDb • Açıklama Dili'},
 'This controls movie and series TMDb information. The selected language is requested first and missing fields fall back to English. Clean Names and artwork/title-logo language are separate.': {'ar': 'اختر '
                                                                                                                                                                                                        'لغة '
                                                                                                                                                                                                        'وصف '
                                                                                                                                                                                                        'الأفلام '
                                                                                                                                                                                                        'والمسلسلات '
                                                                                                                                                                                                        'بشكل '
                                                                                                                                                                                                        'مستقل '
                                                                                                                                                                                                        'عن '
                                                                                                                                                                                                        'لغة '
                                                                                                                                                                                                        'الواجهة. '
                                                                                                                                                                                                        'إذا '
                                                                                                                                                                                                        'لم '
                                                                                                                                                                                                        'يتوفر '
                                                                                                                                                                                                        'الوصف '
                                                                                                                                                                                                        'باللغة '
                                                                                                                                                                                                        'المختارة، '
                                                                                                                                                                                                        'تُستخدم '
                                                                                                                                                                                                        'الإنجليزية '
                                                                                                                                                                                                        'فقط '
                                                                                                                                                                                                        'كبديل.  '
                                                                                                                                                                                                        'Clean '
                                                                                                                                                                                                        'Names '
                                                                                                                                                                                                        '/ '
                                                                                                                                                                                                        'artwork '
                                                                                                                                                                                                        '/ '
                                                                                                                                                                                                        'title-logo: '
                                                                                                                                                                                                        'منفصل.',
                                                                                                                                                                                                  'de': 'Wählen '
                                                                                                                                                                                                        'Sie '
                                                                                                                                                                                                        'die '
                                                                                                                                                                                                        'Beschreibungssprache '
                                                                                                                                                                                                        'für '
                                                                                                                                                                                                        'Filme '
                                                                                                                                                                                                        'und '
                                                                                                                                                                                                        'Serien '
                                                                                                                                                                                                        'unabhängig '
                                                                                                                                                                                                        'von '
                                                                                                                                                                                                        'der '
                                                                                                                                                                                                        'Oberfläche. '
                                                                                                                                                                                                        'Fehlt '
                                                                                                                                                                                                        'der '
                                                                                                                                                                                                        'Text '
                                                                                                                                                                                                        'in '
                                                                                                                                                                                                        'der '
                                                                                                                                                                                                        'gewählten '
                                                                                                                                                                                                        'Sprache, '
                                                                                                                                                                                                        'wird '
                                                                                                                                                                                                        'ausschließlich '
                                                                                                                                                                                                        'Englisch '
                                                                                                                                                                                                        'verwendet.  '
                                                                                                                                                                                                        'Clean '
                                                                                                                                                                                                        'Names '
                                                                                                                                                                                                        '/ '
                                                                                                                                                                                                        'artwork '
                                                                                                                                                                                                        '/ '
                                                                                                                                                                                                        'title-logo: '
                                                                                                                                                                                                        'getrennt.',
                                                                                                                                                                                                  'fr': 'Choisissez '
                                                                                                                                                                                                        'la '
                                                                                                                                                                                                        'langue '
                                                                                                                                                                                                        'des '
                                                                                                                                                                                                        'descriptions '
                                                                                                                                                                                                        'des '
                                                                                                                                                                                                        'films '
                                                                                                                                                                                                        'et '
                                                                                                                                                                                                        'séries '
                                                                                                                                                                                                        'indépendamment '
                                                                                                                                                                                                        'de '
                                                                                                                                                                                                        'l’interface. '
                                                                                                                                                                                                        'Si '
                                                                                                                                                                                                        'le '
                                                                                                                                                                                                        'texte '
                                                                                                                                                                                                        'manque '
                                                                                                                                                                                                        'dans '
                                                                                                                                                                                                        'la '
                                                                                                                                                                                                        'langue '
                                                                                                                                                                                                        'choisie, '
                                                                                                                                                                                                        'seul '
                                                                                                                                                                                                        'l’anglais '
                                                                                                                                                                                                        'est '
                                                                                                                                                                                                        'utilisé '
                                                                                                                                                                                                        'en '
                                                                                                                                                                                                        'remplacement.  '
                                                                                                                                                                                                        'Clean '
                                                                                                                                                                                                        'Names '
                                                                                                                                                                                                        '/ '
                                                                                                                                                                                                        'artwork '
                                                                                                                                                                                                        '/ '
                                                                                                                                                                                                        'title-logo: '
                                                                                                                                                                                                        'séparé.',
                                                                                                                                                                                                  'tr': 'Film '
                                                                                                                                                                                                        've '
                                                                                                                                                                                                        'dizi '
                                                                                                                                                                                                        'açıklama '
                                                                                                                                                                                                        'dilini '
                                                                                                                                                                                                        'arayüzden '
                                                                                                                                                                                                        'bağımsız '
                                                                                                                                                                                                        'seçin. '
                                                                                                                                                                                                        'Seçilen '
                                                                                                                                                                                                        'dilde '
                                                                                                                                                                                                        'metin '
                                                                                                                                                                                                        'yoksa '
                                                                                                                                                                                                        'yalnızca '
                                                                                                                                                                                                        'İngilizce '
                                                                                                                                                                                                        'yedek '
                                                                                                                                                                                                        'olarak '
                                                                                                                                                                                                        'kullanılır.  '
                                                                                                                                                                                                        'Clean '
                                                                                                                                                                                                        'Names '
                                                                                                                                                                                                        '/ '
                                                                                                                                                                                                        'artwork '
                                                                                                                                                                                                        '/ '
                                                                                                                                                                                                        'title-logo: '
                                                                                                                                                                                                        'ayrı.'},
 'Watched  •  play again': {'ar': 'تمت المشاهدة  •  تشغيل مجددًا',
                            'de': 'Gesehen  •  erneut abspielen',
                            'fr': 'Vu  •  relire',
                            'tr': 'İzlendi  •  tekrar oynat'}})


# Final V9.1 carries forward the reviewed visible-UI localization closure. Localization-only; no runtime behavior changes.
_T.update({
    '%s • %d/%d • new %d • already %d • failed %d': {'ar': '%s • %d/%d • جديد %d • محفوظ %d • فشل %d', 'de': '%s • %d/%d • neu %d • Zwischengespeichert %d • fehlgeschlagen %d', 'fr': '%s • %d/%d • nouveau %d • En cache %d • échec %d', 'tr': '%s • %d/%d • yeni %d • Önbellekte %d • başarısız %d'},
    'Confirm': {'ar': 'تأكيد', 'de': 'Bestätigen', 'fr': 'Confirmer', 'tr': 'Onayla'},
    'Portal Server': {'ar': 'خادم البوابة', 'de': 'Portal-Server', 'fr': 'Serveur portail', 'tr': 'Portal Sunucusu'},
    'Xtream Server %d': {'ar': 'خادم Xtream %d', 'de': 'Xtream-Server %d', 'fr': 'Serveur Xtream %d', 'tr': 'Xtream Sunucusu %d'},
    'Live Channels': {'ar': 'القنوات المباشرة', 'de': 'Live-Kanäle', 'fr': 'Chaînes en direct', 'tr': 'Canlı Kanallar'},
    'English': {'ar': 'الإنجليزية', 'de': 'Englisch', 'fr': 'Anglais', 'tr': 'İngilizce'},
    'EPISODE': {'ar': 'حلقة', 'de': 'EPISODE', 'fr': 'ÉPISODE', 'tr': 'BÖLÜM'},
})


# Final V9.1 carries forward reviewed Arabic visible-UI wording. Existing keys only.
_T['%d portal(s) working.  %d failed.\n\nFailed portals were NOT disabled; use RED if you want to disable one.']['ar'] = '%d Portal يعمل. فشل %d.\n\nلم يتم تعطيل الـالبوابات الفاشلة؛ استخدم الزر الأحمر إذا أردت تعطيل أحدها.'
_T['%s  •  %s  •  ARROWS Navigate  •  OK Open  •  MENU Portal Manager']['ar'] = '%s  •  %s  •  الأسهم للتنقل  •  OK فتح  •  MENU إدارة الـالبوابات'
_T['Add a TMDB API key or Read Access Token first.']['ar'] = 'أضف مفتاح TMDB API أو رمز وصول للقراءة أولًا.'
_T['All portals passed the check.']['ar'] = 'اجتازت كل الـالبوابات الفحص.'
_T['Audio selection is not available in this image']['ar'] = 'اختيار مسار الصوت غير متاح في هذه الـالصورة'
_T['Auto Sync applied • timing scale %.5f']['ar'] = 'تم تطبيق المزامنة التلقائية • مقياس التوقيت %.5f'
_T['Auto Sync needs a stable movie duration. Use delay +/- for this subtitle.']['ar'] = 'يتطلب المزامنة التلقائية مدة فيلم ثابتة. استخدم التأخير +/- لهذه الترجمة.'
_T['Auto Sync • lightweight']['ar'] = 'المزامنة التلقائية • خفيف'
_T['Backdrop ready • HDD']['ar'] = 'الـالخلفية جاهز • HDD'
_T['Catch-up TV  /  Channels']['ar'] = 'المشاهدة المؤجلة  /  القنوات'
_T['Catch-up history']['ar'] = 'سجل المشاهدة المؤجلة'
_T['Choose Nova FHD, OLED Black, or Midnight Purple. Restart is required.']['ar'] = 'اختر Nova FHD أو OLED أسود أو بنفسجي منتصف الليل. يلزم إعادة التشغيل.'
_T['Choose Poster Grid, Cinematic, Backdrop Grid, or Backdrop Grid 2 for Movies.']['ar'] = 'اختر الملصق الشبكة أو سينمائي أو الخلفية الشبكة أو الخلفية الشبكة 2 للأفلام.'
_T['Choose Poster Grid, Cinematic, Backdrop Grid, or Backdrop Grid 2 for Series.']['ar'] = 'اختر الملصق الشبكة أو سينمائي أو الخلفية الشبكة أو الخلفية الشبكة 2 للمسلسلات.'
_T['Choose how devices on your network open Web Cleaner']['ar'] = 'اختر طريقة فتح أجهزة شبكتك لـمنظّف الويب'
_T['Choose movie and series TMDb information language independently from the interface. Missing fields fall back to English; Clean Names is separate.']['ar'] = 'اختر لغة وصف الأفلام والمسلسلات بشكل مستقل عن لغة الواجهة. إذا لم يتوفر الوصف باللغة المختارة، تُستخدم الإنجليزية فقط كبديل.  تنظيف الأسماء: منفصل.'
_T['Clear plugin-owned data for this portal?  Favorites, History, Resume, exported bouquet/EPG and related timers are removed; the portal profile is kept.']['ar'] = 'مسح بيانات الإضافة الخاصة بهذه البوابة؟ سيتم حذف المفضلة والسجل والاستكمال وباقة/EPG المُصدّرة والمؤقتات المرتبطة، مع الإبقاء على ملف البوابة.'
_T['Could not start Web Cleaner:\n%s']['ar'] = 'تعذر تشغيل منظّف الويب:\n%s'
_T['Delete saved posters, backdrops, generated artwork, Home artwork and Live picons from the HDD. Confirmation is required.']['ar'] = 'احذف البوسترات والخلفيات والصور المُنشأة وصور الرئيسية ومباشر picons المحفوظة من HDD. يلزم التأكيد.'
_T['Enter the code once. Your browser keeps the secure session until Web Cleaner is stopped.']['ar'] = 'أدخل الكود مرة واحدة. سيحتفظ المتصفح بالجلسة الآمنة حتى يتم إيقاف منظّف الويب.'
_T['Every Live, movie, series, episode and catch-up stream uses the Playback Engine selected in Settings.']['ar'] = 'كل بث مباشر أو فيلم أو مسلسل أو حلقة أو المشاهدة المؤجلة يستخدم محرك التشغيل المحدد في الإعدادات.'
_T['Export current Live folder to TV']['ar'] = 'تصدير مجلد مباشر الحالي إلى التلفزيون'
_T['Exporting Live folder: %s']['ar'] = 'جارٍ تصدير مجلد مباشر: %s'
_T['Hero failed']['ar'] = 'فشل الصورة الرئيسية'
_T['Hero failed: %s']['ar'] = 'فشل الصورة الرئيسية: %s'
_T['Hero not set • selected backdrop is not cached on HDD']['ar'] = 'لم يتم تعيين الصورة الرئيسية • الخلفية المحددة غير محفوظة في كاش HDD'
_T['Hero pinned']['ar'] = 'تم تثبيت الصورة الرئيسية'
_T['Hero pinned • active across Ultra Stalker']['ar'] = 'تم تثبيت الصورة الرئيسية • نشط في كل Ultra Stalker'
_T['Hero selected']['ar'] = 'تم اختيار الصورة الرئيسية'
_T['Hero selected • preparing adaptive materials…']['ar'] = 'تم اختيار الصورة الرئيسية • جارٍ تجهيز العناصر التكيفية…'
_T['Home Hero changes only from Movie/Series Details MENU']['ar'] = 'يتغير الرئيسية الصورة الرئيسية فقط من MENU داخل تفاصيل الفيلم/المسلسل'
_T['Home Hero not set • backdrop could not be prepared']['ar'] = 'لم يتم تثبيت الرئيسية الصورة الرئيسية • تعذر تجهيز الـالخلفية'
_T['Home Hero not set • backdrop is not ready yet']['ar'] = 'لم يتم تثبيت الرئيسية الصورة الرئيسية • الـالخلفية غير جاهز بعد'
_T['Home Hero pinned • stays fixed until you choose another']['ar'] = 'تم تثبيت الرئيسية الصورة الرئيسية • سيظل ثابتًا حتى تختار غيره'
_T['Live Export']['ar'] = 'تصدير مباشر'
_T['Live Export Complete']['ar'] = 'اكتمل تصدير مباشر'
_T['Live channel']['ar'] = 'قناة مباشر'
_T['Live folder export failed: %s']['ar'] = 'فشل تصدير مجلد مباشر: %s'
_T['Live folder exported to the TV bouquet list.\n\n%s\n%d channels']['ar'] = 'تم تصدير مجلد مباشر إلى قائمة باقة بالتلفزيون.\n\n%s\n%d قناة'
_T['Live folder exported • %d channels']['ar'] = 'تم تصدير مجلد مباشر • %d قناة'
_T['Live list, preview, EPG and catch-up controls.']['ar'] = 'قائمة البث المباشر والمعاينة وEPG وعناصر التحكم في المشاهدة المؤجلة.'
_T['No Live categories']['ar'] = 'لا توجد فئات مباشر'
_T['Permanently delete this portal and its plugin-owned data, exported bouquet/EPG, Favorites, History, Resume and related timers?']['ar'] = 'حذف هذه البوابة نهائيًا مع بيانات الإضافة الخاصة بها وباقة/EPG المُصدّرة والمفضلة والسجل والاستكمال والمؤقتات المرتبطة؟'
_T['Playback engine is locked to the value selected under Playback engine. Automatic Smart Engine switching is disabled.']['ar'] = 'محرك التشغيل مقفول على القيمة المحددة في إعداد محرك التشغيل. التبديل التلقائي عبر المحرك الذكي معطل.'
_T['Playback is locked to the engine selected in Playback Engine. Smart Engine and per-title engine switching are disabled.']['ar'] = 'التشغيل مثبت على المحرك المحدد في محرك التشغيل. تم تعطيل المحرك الذكي والتبديل حسب العنوان.'
_T['Portal returned an invalid catch-up link']['ar'] = 'أعاد الـPortal رابط المشاهدة المؤجلة غير صالح'
_T['Premium Cleaner + Deep Check • %s']['ar'] = 'مميز Cleaner + فحص عميق • %s'
_T['Preparing Hero… waiting for cached backdrop']['ar'] = 'جارٍ تجهيز الصورة الرئيسية… انتظار الخلفية المحفوظة'
_T['Preparing Home artwork']['ar'] = 'جارٍ التحميل • الرئيسية الصور'
_T['Preparing the adaptive Hero in the background…']['ar'] = 'جارٍ تجهيز الصورة الرئيسية التكيفي في الخلفية…'
_T['Put long credentials in:\n/etc/enigma2/ultrastalker/api_keys.conf\n\nTMDB_API_KEY=...\nTMDB_READ_TOKEN=...\nIMDB_API_KEY=...\nIMDB_API_ENDPOINT=... (generic mode)\nSUBDL_API_KEY=...\nSUBSOURCE_API_KEY=...\nFANART_API_KEY=...\n\nOfficial IMDb/AWS Data Exchange:\nIMDB_ACCESS_KEY_ID=...\nIMDB_SECRET_ACCESS_KEY=...\nIMDB_SESSION_TOKEN=... (optional)\nIMDB_REGION=us-east-1\nIMDB_DATASET_ID=...\nIMDB_REVISION_ID=...\nIMDB_ASSET_ID=...']['ar'] = 'ضع بيانات الاعتماد الطويلة في:\n/etc/enigma2/ultrastalker/api_keys.conf\n\nTMDB_API_KEY=...\nTMDB_READ_TOKEN=...\nIMDB_API_KEY=...\nIMDB_API_ENDPOINT=... (الوضع العام)\nSUBDL_API_KEY=...\nSUBSOURCE_API_KEY=...\nFANART_API_KEY=...\n\nتبادل بيانات IMDb/AWS الرسمي:\nIMDB_ACCESS_KEY_ID=...\nIMDB_SECRET_ACCESS_KEY=...\nIMDB_SESSION_TOKEN=... (اختياري)\nIMDB_REGION=us-east-1\nIMDB_DATASET_ID=...\nIMDB_REVISION_ID=...\nIMDB_ASSET_ID=...'
_T['Search keyboard is unavailable on this image.']['ar'] = 'لوحة مفاتيح البحث غير متاحة في هذه الـالصورة.'
_T['Sent to the receiver bouquet list.\n\n%s\n%d item(s)']['ar'] = 'تم الإرسال إلى قائمة باقة في الرسيفر.\n\n%s\n%d عنصر'
_T['Smart Recovery retries']['ar'] = 'محاولات ذكي الاسترداد'
_T['Smart engine memories  %s']['ar'] = 'ذاكرة المحرك الذكي      %s'
_T['Smart recovery         %s / retries %s']['ar'] = 'ذكي الاسترداد          %s / المحاولات %s'
_T['Start/stop the Premium Web Cleaner and Deep Check service on port 7725.']['ar'] = 'ابدأ/أوقف خدمة مميز منظّف الويب وفحص عميق على المنفذ 7725.'
_T['TMDB API key or Read Access Token']['ar'] = 'مفتاح TMDB API أو رمز وصول للقراءة'
_T['This controls movie and series TMDb information. The selected language is requested first and missing fields fall back to English. Clean Names and artwork/title-logo language are separate.']['ar'] = 'اختر لغة وصف الأفلام والمسلسلات بشكل مستقل عن لغة الواجهة. إذا لم يتوفر الوصف باللغة المختارة، تُستخدم الإنجليزية فقط كبديل.  تنظيف الأسماء / الصور / شعار العنوان: منفصل.'
_T['Version: %s\nBuild: %s\nPython support: 3.12 / 3.13 / 3.14 / 3.15\nInterface: Full HD • Adaptive 3D Glass UI']['ar'] = 'الإصدار: %s\nالبنية: %s\nدعم Python: 3.12 / 3.13 / 3.14 / 3.15\nالواجهة: Full HD • تكيفي 3D زجاجية UI'
_T['Web Cleaner']['ar'] = 'منظّف الويب'
_T['Web Cleaner Access']['ar'] = 'الوصول إلى منظّف الويب'
_T['Web Cleaner Ready']['ar'] = 'منظّف الويب جاهز'
_T['Web Cleaner access: %s%s']['ar'] = 'وصول منظّف الويب: %s%s'
_T['Web Cleaner running on port 7725']['ar'] = 'منظّف الويب يعمل على المنفذ 7725'
_T['Web Cleaner stopped']['ar'] = 'تم إيقاف منظّف الويب'
_T['Your selected backdrop is now the active Hero.']['ar'] = 'الخلفية التي حددتها أصبحت الآن الصورة الرئيسية النشط.'

# Final V9.1 reviewed Arabic residual visible-UI wording.
_T['Premium Cleaner + Deep Check • %s']['ar'] = 'مميز منظّف + فحص عميق • %s'

# Final V9.1 reviewed same-source UI corrections. Existing keys only.
_T['CATCH-UP']['de'] = 'ARCHIV'
_T['CINEMATIC']['de'] = 'KINOMODUS'
_T['Catch-up  /  %s']['de'] = 'ARCHIV  /  %s'
_T['Cinematic']['de'] = 'KINOMODUS'
_T['Downloads']['de'] = 'Heruntergeladene Dateien'
_T['Episode']['de'] = 'Folge'
_T['Episode %s']['de'] = 'Folge %s'
_T['Episode %s  •  %s']['de'] = 'Folge %s  •  %s'
_T['LIVE']['de'] = 'DIREKT'
_T['Live & EPG']['de'] = 'DIREKT & EPG'
_T['M3U • OFFLINE']['de'] = 'M3U • GETRENNT'
_T['M3U • ONLINE']['de'] = 'M3U • VERBUNDEN'
_T['Midnight Purple']['de'] = 'Mitternachtsviolett'
_T['OFFLINE']['de'] = 'GETRENNT'
_T['OLED Black']['de'] = 'OLED Schwarz'
_T['ONLINE']['de'] = 'VERBUNDEN'
_T['Premium Cleaner + Deep Check • %s']['de'] = 'Premium-Bereinigung + Tiefenprüfung • %s'
_T['START']['de'] = 'STARTEN'
_T['Ultra Stalker Update']['de'] = 'Ultra Stalker Aktualisierung'
_T['Web Cleaner']['de'] = 'Web-Bereinigung'
_T['CATCH-UP']['fr'] = 'RATTRAPAGE'
_T['Catch-up  /  %s']['fr'] = 'RATTRAPAGE  /  %s'
_T['Premium Cleaner + Deep Check • %s']['fr'] = 'Nettoyeur avancé + Vérification approfondie • %s'
_T['Web Cleaner']['fr'] = 'Nettoyeur Web'
_T['Catch-up TV']['tr'] = 'GERİ İZLEME'
_T['Premium Cleaner + Deep Check • %s']['tr'] = 'Gelişmiş Temizleyici + Derin Kontrol • %s'
_T['Web Cleaner']['tr'] = 'İnternet Temizleyici'
_T['EPISODE']['de'] = 'FOLGE'


# R9 manual HQ backdrop replacement UI.
_T.update({'Replace Backdrop (HQ)': {'ar': 'استبدال الخلفية (جودة عالية)', 'de': 'Hintergrund ersetzen (HQ)', 'fr': 'Remplacer l’arrière-plan (HQ)', 'tr': 'Arka planı değiştir (HQ)'}, 'Backdrop replacement is already running': {'ar': 'استبدال الخلفية قيد التشغيل بالفعل', 'de': 'Der Austausch des Hintergrunds läuft bereits', 'fr': 'Le remplacement de l’arrière-plan est déjà en cours', 'tr': 'Arka plan değiştirme zaten çalışıyor'}, 'TMDb identity is not ready for this title': {'ar': 'هوية TMDb لهذا العنوان غير جاهزة بعد', 'de': 'Die TMDb-Identität für diesen Titel ist noch nicht bereit', 'fr': 'L’identité TMDb de ce titre n’est pas encore prête', 'tr': 'Bu başlık için TMDb kimliği henüz hazır değil'}, 'Searching for a higher-quality backdrop…': {'ar': 'جارٍ البحث عن خلفية بجودة أعلى…', 'de': 'Suche nach einem höher aufgelösten Hintergrund…', 'fr': 'Recherche d’un arrière-plan de meilleure qualité…', 'tr': 'Daha yüksek kaliteli bir arka plan aranıyor…'}, 'No higher-quality backdrop found': {'ar': 'لم يتم العثور على خلفية بجودة أعلى', 'de': 'Kein Hintergrund mit höherer Qualität gefunden', 'fr': 'Aucun arrière-plan de meilleure qualité trouvé', 'tr': 'Daha yüksek kaliteli arka plan bulunamadı'}, 'Backdrop replacement failed': {'ar': 'فشل استبدال الخلفية', 'de': 'Austausch des Hintergrunds fehlgeschlagen', 'fr': 'Échec du remplacement de l’arrière-plan', 'tr': 'Arka plan değiştirilemedi'}, 'HQ backdrop applied': {'ar': 'تم تطبيق الخلفية عالية الجودة', 'de': 'HQ-Hintergrund angewendet', 'fr': 'Arrière-plan HQ appliqué', 'tr': 'HQ arka plan uygulandı'}})
