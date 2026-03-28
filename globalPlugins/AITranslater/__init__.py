from gui import SettingsPanel, NVDASettingsDialog, guiHelper
import config
import wx
import gui
import globalPluginHandler
import ui
import speech
import requests
import api
from scriptHandler import script
import addonHandler
from logHandler import log
import time
import threading
import os
import urllib.request
import urllib.parse
import json

try:
    addonHandler.initTranslation()
except addonHandler.AddonError:
    log.warning("Unable to init translations. This may be because the addon is running from NVDA scratchpad.")

speak = speech.speech.speak
roleSECTION = "AITranslater"
confspec = {
    "translateTo": "string(default=Indonesian Indonesia)",
    "geminiApiKey": "string(default='')",
    "useDialogForResults": "boolean(default=true)",
    "enableCache": "boolean(default=true)",
    "translationEngine": "string(default=Gemini API)",
    "beepOnProcess": "boolean(default=true)"
}

config.conf.spec[roleSECTION] = confspec

# Mapping bahasa NVDA ke kode ISO untuk Google Translate (InstantTranslate)
LANG_MAP = {
    "Arabic": "ar", "Bengali": "bn", "Chinese": "zh-CN", "Czech": "cs",
    "Danish": "da", "Dutch": "nl", "English": "en", "Finnish": "fi",
    "French": "fr", "German": "de", "Greek": "el", "Hebrew": "iw",
    "Hindi": "hi", "Hungarian": "hu", "Indonesian": "id", "Italian": "it",
    "Japanese": "ja", "Korean": "ko", "Malay": "ms", "Marathi": "mr",
    "Norwegian": "no", "Persian": "fa", "Polish": "pl", "Portuguese": "pt",
    "Punjabi": "pa", "Romanian": "ro", "Russian": "ru", "Slovak": "sk",
    "Spanish": "es", "Swedish": "sv", "Tamil": "ta", "Telugu": "te",
    "Thai": "th", "Turkish": "tr", "Ukrainian": "uk", "Urdu": "ur",
    "Vietnamese": "vi"
}

def get_translation(text: str, announce: bool = True):
    try:
        result = translate(text)
    except Exception as e:
        result = "Error:\n" + str(e)
    
    if not announce:
        return result
        
    if config.conf[roleSECTION].get("useDialogForResults", True):
        wx.CallAfter(ResultWindow, result, "Hasil Terjemahan")
    else:
        wx.CallAfter(speak, [result])
    return result

CACHE_FILE = os.path.join(os.path.dirname(__file__), "cache.txt")

def _read_cache(cache_key: str) -> str:
    if not config.conf[roleSECTION].get("enableCache", True):
        return None
    if not os.path.exists(CACHE_FILE):
        return None
    search_prefix = cache_key + "|---|out="
    try:
        with open(CACHE_FILE, "r", encoding="utf-8") as f:
            for line in f:
                if line.startswith(search_prefix):
                    return line[len(search_prefix):].strip()
    except Exception:
        pass
    return None

def _write_cache(cache_key: str, value: str):
    if not config.conf[roleSECTION].get("enableCache", True):
        return
    try:
        with open(CACHE_FILE, "a", encoding="utf-8") as f:
            clean_val = value.replace("\n", " [NEWLINE] ")
            f.write(f"{cache_key}|---|out={clean_val}\n")
    except Exception:
        pass

def _call_instant_translate(text: str, target_lang: str) -> str:
    lang_name = target_lang.split(" ")[0]
    lang_code = LANG_MAP.get(lang_name, "id")
    url = f"https://translate.googleapis.com/translate_a/single?client=gtx&sl=auto&tl={lang_code}&dt=t&q={urllib.parse.quote(text.encode('utf-8'))}&dj=1"
    
    opener = urllib.request.build_opener()
    opener.addheaders = [('User-agent', 'Mozilla/5.0')]
    try:
        response = json.load(opener.open(url, timeout=5))
        translation = "".join(sentence["trans"] for sentence in response["sentences"])
        return translation
    except Exception as e:
        return f"Error Google Translate: {str(e)}"

def _call_gemini_api_cached(text: str, target_lang: str, api_key: str, engine: str) -> str:
    clean_text = text.replace("\n", " ").strip()
    cache_key = f"{target_lang}|---|in={clean_text}"
    
    cached_val = _read_cache(cache_key)
    if cached_val:
        return cached_val.replace(" [NEWLINE] ", "\n")

    if engine == "InstantTranslate (Tanpa API Key)":
        result_text = _call_instant_translate(text, target_lang)
        if not result_text.startswith("Error"):
            _write_cache(cache_key, result_text)
        return result_text

    # Mode Gemini API
    prompt = f"""translate: 
        {text}
        to {target_lang}
        give me the translated text only don't type any things except the text. CRITICAL: The text may contain '|  ' separators. You MUST maintain all '|  ' separators in your translation exactly as they appear in the original."""
    
    # Memaksa model gemini-2.5-flash untuk menghindari limit kuota model pro tipe berat
    model_name = "gemini-2.5-flash"
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:generateContent?key={api_key}"
    headers = {'Content-Type': 'application/json'}
    payload = {"contents": [{"parts": [{"text": prompt}]}]}
    
    max_retries = 3
    for attempt in range(max_retries):
        try:
            response = requests.post(url, headers=headers, json=payload, timeout=10)
            data = response.json()
            if "candidates" in data and len(data["candidates"]) > 0:
                result_text = data["candidates"][0]["content"]["parts"][0]["text"].strip()
                _write_cache(cache_key, result_text)
                return result_text
            elif "error" in data:
                err_msg = data['error'].get('message', 'Unknown error')
                if "429" in str(err_msg) or "Too Many Requests" in str(err_msg) or "Quota exceeded" in str(err_msg):
                    if attempt < max_retries - 1:
                        time.sleep(2 ** (attempt + 1))
                        continue
                return f"API Error: {err_msg}"
            else:
                return "Error: Respons API tak terduga"
        except Exception as e:
            if attempt < max_retries - 1:
                time.sleep(2 ** (attempt + 1))
                continue
            return f"Error: {str(e)}"
    return "Error: Koneksi waktu habis atau batas mencoba tercapai."

def translate(text: str):
    if not isinstance(text, str):
        return "Error: Input bukan teks."
    
    engine = config.conf[roleSECTION].get("translationEngine", "Gemini API")
    api_key = config.conf[roleSECTION].get("geminiApiKey", "").strip()
    
    if engine == "Gemini API" and not api_key:
        return "Error: Harap isi kunci API Gemini di pengaturan Terjemahan oleh AI, atau ganti mesin ke InstantTranslate."

    target_lang = config.conf[roleSECTION]["translateTo"]
    return _call_gemini_api_cached(text, target_lang, api_key, engine)

class InfoDialog(wx.Dialog):
    def __init__(self, parent, title, content):
        super().__init__(parent, title=title, size=(650, 500), style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER)
        self.content = content
        sizer = wx.BoxSizer(wx.VERTICAL)
        self.textCtrl = wx.TextCtrl(self, value=content, style=wx.TE_MULTILINE | wx.TE_READONLY | wx.TE_RICH2)
        sizer.Add(self.textCtrl, 1, wx.EXPAND | wx.ALL, 10)
        btnSizer = wx.BoxSizer(wx.HORIZONTAL)
        self.btnCopy = wx.Button(self, label="&Salin ke Clipboard")
        self.btnCopy.Bind(wx.EVT_BUTTON, self.onCopy)
        btnSizer.Add(self.btnCopy, 0, wx.ALL, 5)
        self.btnClose = wx.Button(self, wx.ID_CANCEL, label="&Tutup")
        btnSizer.Add(self.btnClose, 0, wx.ALL, 5)
        sizer.Add(btnSizer, 0, wx.ALIGN_RIGHT | wx.BOTTOM, 10)
        self.SetSizer(sizer)
        self.Centre()
        self.textCtrl.SetFocus()

    def onCopy(self, event):
        if wx.TheClipboard.Open():
            wx.TheClipboard.SetData(wx.TextDataObject(self.content))
            wx.TheClipboard.Close()
            ui.message("Teks disalin.")
        else:
            ui.message("Gagal menyalin.")

class ResultWindow(wx.Dialog):
    def __init__(self, text, title):
        super(ResultWindow, self).__init__(gui.mainFrame, title=title)
        sizer = wx.BoxSizer(wx.VERTICAL)
        self.outputCtrl = wx.TextCtrl(self, style=wx.TE_MULTILINE | wx.TE_READONLY | wx.TE_RICH)
        self.outputCtrl.Bind(wx.EVT_KEY_DOWN, self.onOutputKeyDown)
        sizer.Add(self.outputCtrl, proportion=1, flag=wx.EXPAND)
        self.SetSizer(sizer)
        sizer.Fit(self)
        self.outputCtrl.SetValue(text)
        self.outputCtrl.SetFocus()
        self.Raise()
        self.Maximize()
        self.Show()

    def onOutputKeyDown(self, event):
        if event.GetKeyCode() == wx.WXK_ESCAPE:
            self.Destroy()
        else:
            event.Skip()

class InputText(wx.Dialog):
    def __init__(self):
        super().__init__(None, -1, title="Input Teks Terjemahan")
        sizer = wx.BoxSizer(wx.VERTICAL)
        panel = wx.Panel(self)
        self.textBox = wx.TextCtrl(panel, -1, style=wx.TE_MULTILINE | wx.TE_RICH)
        sizer.Add(self.textBox)
        self.translate = wx.Button(panel, -1, "Terjemahkan")
        self.translate.Bind(wx.EVT_BUTTON, self.onTranslate)
        sizer.Add(self.translate)
        self.close = wx.Button(panel, -1, "Tutup")
        self.close.Bind(wx.EVT_BUTTON, self.onClose)
        sizer.Add(self.close)
        panel.SetSizer(sizer)
        self.Show()

    def onClose(self, event):
        self.Destroy()

    def onTranslate(self, event):
        text = self.textBox.Value
        self.close.SetFocus()
        # Non-blocking translation utk dialog manual
        def _bg_translate():
            get_translation(text, announce=True)
        threading.Thread(target=_bg_translate, daemon=True).start()

class AITranslaterSettingsDialog(wx.Dialog):
    def __init__(self, parent):
        super().__init__(parent, title="Pengaturan Terjemahan oleh AI", size=(450, 400))
        sizer = wx.BoxSizer(wx.VERTICAL)
        
        languages = [
            "Arabic Egypt", "Bengali Bangladesh", "Chinese Mandarin (Simplified)",
            "Czech Czech Republic", "Danish Denmark", "Dutch Netherlands",
            "English United States", "Finnish Finland", "French France", 
            "German Germany", "Greek Greece", "Hebrew Israel", "Hindi India", 
            "Hungarian Hungary", "Indonesian Indonesia", "Italian Italy", 
            "Japanese Japan", "Korean South Korea", "Malay Malaysia", 
            "Marathi India", "Norwegian Norway", "Persian Iran", "Polish Poland", 
            "Portuguese Portugal", "Punjabi India", "Romanian Romania", 
            "Russian Russia", "Slovak Slovakia", "Spanish Spain", "Swedish Sweden",
            "Tamil India", "Telugu India", "Thai Thailand", "Turkish Turkey",
            "Ukrainian Ukraine", "Urdu Pakistan", "Vietnamese Vietnam"
        ]
        languages.sort()
        
        # Engine Combo
        sizer.Add(wx.StaticText(self, label="&Mesin Penterjemah:"), 0, wx.ALL, 5)
        self.engineCombo = wx.Choice(self, choices=["Gemini API", "InstantTranslate (Tanpa API Key)"])
        self.engineCombo.SetStringSelection(config.conf[roleSECTION].get("translationEngine", "Gemini API"))
        sizer.Add(self.engineCombo, 0, wx.EXPAND | wx.ALL, 5)

        # API Key
        sizer.Add(wx.StaticText(self, label="Kunci &API Gemini:"), 0, wx.ALL, 5)
        self.apiKeyInput = wx.TextCtrl(self, style=wx.TE_PASSWORD)
        self.apiKeyInput.SetValue(config.conf[roleSECTION].get("geminiApiKey", ""))
        sizer.Add(self.apiKeyInput, 0, wx.EXPAND | wx.ALL, 5)
        
        self.getApiBtn = wx.Button(self, label="&Dapatkan Kunci API (Gratis)")
        self.getApiBtn.Bind(wx.EVT_BUTTON, self.onGetApiKey)
        sizer.Add(self.getApiBtn, 0, wx.ALL, 5)
        
        # Language
        sizer.Add(wx.StaticText(self, label="Terjemah&kan ke:"), 0, wx.ALL, 5)
        self.sou1 = wx.Choice(self, choices=languages)
        try:
            self.sou1.SetStringSelection(config.conf[roleSECTION].get("translateTo", "Indonesian Indonesia"))
        except:
            self.sou1.SetSelection(0)
        sizer.Add(self.sou1, 0, wx.EXPAND | wx.ALL, 5)
        
        # Checkboxes
        self.sou2 = wx.CheckBox(self, label="Gunakan d&ialog untuk hasil manual")
        self.sou2.SetValue(config.conf[roleSECTION].get("useDialogForResults", True))
        sizer.Add(self.sou2, 0, wx.ALL, 5)
        
        self.cacheChk = wx.CheckBox(self, label="&Aktifkan File Cache (Meringankan beban AI/NVDA)")
        self.cacheChk.SetValue(config.conf[roleSECTION].get("enableCache", True))
        sizer.Add(self.cacheChk, 0, wx.ALL, 5)
        
        self.beepChk = wx.CheckBox(self, label="Aktifkan &bunyi beep saat memproses/translasi")
        self.beepChk.SetValue(config.conf[roleSECTION].get("beepOnProcess", True))
        sizer.Add(self.beepChk, 0, wx.ALL, 5)

        # Buttons
        btnSizer = wx.StdDialogButtonSizer()
        self.saveBtn = wx.Button(self, wx.ID_OK, label="&Simpan")
        self.saveBtn.Bind(wx.EVT_BUTTON, self.onSave)
        btnSizer.AddButton(self.saveBtn)
        
        self.cancelBtn = wx.Button(self, wx.ID_CANCEL, label="&Batal")
        btnSizer.AddButton(self.cancelBtn)
        btnSizer.Realize()
        
        sizer.Add(btnSizer, 0, wx.ALIGN_RIGHT | wx.ALL, 10)
        self.SetSizer(sizer)
        self.Centre()
        self.engineCombo.SetFocus()
        
    def onGetApiKey(self, event):
        import webbrowser
        webbrowser.open("https://aistudio.google.com/api-keys")

    def onSave(self, event):
        config.conf[roleSECTION]["translationEngine"] = self.engineCombo.GetStringSelection()
        config.conf[roleSECTION]["geminiApiKey"] = self.apiKeyInput.Value
        config.conf[roleSECTION]["translateTo"] = self.sou1.GetStringSelection()
        config.conf[roleSECTION]["useDialogForResults"] = self.sou2.Value
        config.conf[roleSECTION]["enableCache"] = self.cacheChk.Value
        config.conf[roleSECTION]["beepOnProcess"] = self.beepChk.Value
        self.EndModal(wx.ID_OK)


class GlobalPlugin(globalPluginHandler.GlobalPlugin):
    scriptCategory = "Terjemahan oleh AI"
    
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Tangkap speak asli untuk background replacement
        self._original_speak = speak
        speech.speech.speak = self.speech_event_override
        
        self.record_live_speech = False
        self.lastSpoken = ""
        
        self.layer_active = False
        self.layer_gestures = {
            "kb:r": "liveRecording",
            "kb:t": "textInput",
            "kb:s": "settings",
            "kb:a": "apiCheck",
            "kb:c": "clipboard",
            "kb:l": "lastSpoken",
            "kb:f1": "help",
            "kb:escape": "exitLayer"
        }

    def _play_tone(self, tone_type="on"):
        if not config.conf[roleSECTION].get("beepOnProcess", True) and tone_type == "process":
            return
        def _beep():
            try:
                import tones
                if tone_type == "on":
                    tones.beep(880, 50)
                    time.sleep(0.05)
                    tones.beep(1100, 50)
                elif tone_type == "off":
                    tones.beep(1100, 50)
                    time.sleep(0.05)
                    tones.beep(880, 50)
                elif tone_type == "process":
                    tones.beep(500, 50)
            except Exception:
                pass
        threading.Thread(target=_beep, daemon=True).start()

    def getScript(self, gesture):
        if self.layer_active:
            script_name = None
            for identifier in gesture.identifiers:
                if identifier in self.layer_gestures:
                    script_name = "script_layer_" + self.layer_gestures[identifier]
                    break
            
            if script_name:
                target_script = getattr(self, script_name)
                def _layer_wrapper(gesture):
                    self.layer_active = False
                    if script_name != "script_layer_liveRecording":
                        self._play_tone("off")
                    if script_name == "script_layer_exitLayer":
                        ui.message("Dibatalkan.")
                    else:
                        target_script(gesture)
                return _layer_wrapper
            else:
                self.layer_active = False
                self._play_tone("off")
                ui.message("Keluar dari Mode Terjemahan.")
                
        return super().getScript(gesture)

    @script(
        description="Mengaktifkan mode perintah utama Terjemahan oleh AI",
        gesture="kb:NVDA+shift+alt+t"
    )
    def script_activateCommandLayer(self, gesture):
        self.layer_active = True
        self._play_tone("on")
        ui.message("Mode Terjemahan aktif. Tekan F1 untuk bantuan instruksi pindai.")

    def script_layer_exitLayer(self, gesture):
        pass

    def script_layer_help(self, gesture):
        self.record_live_speech = False
        msg = [
            "Bantuan dan Daftar Pintasan Terjemahan oleh AI",
            "-" * 30,
            "F1  : Menampilkan bantuan kotak ini.",
            "R   : Mengaktifkan/Menonaktifkan Terjemahan Real-time berantai ke depannya (Otomatis).",
            "T   : Membuka jendela input teks manual kosong untuk diketik.",
            "S   : Membuka jendela Pengaturan Khusus add-on secara instan.",
            "A   : Mengecek validitas Mesin & Limit Token API berdasarkan koneksi.",
            "C   : Menerjemahkan isi teks dari clipboard yang habis di-copy.",
            "L   : Menerjemahkan ucapan *Teks Terakhir* sebelum layer ini aktif.",
            "Esc : Menutup layer saat ini.",
            "",
            "CATATAN: Kombinasi utama WAJIB ditekan adalah NVDA + Shift + Alt + T.",
            "Semua shortcut huruf kecil di atas DITEKAN SETELAH menekan shortcut utama tadi.",
            "",
            "Hubungi Pengembang (Laporan Bug / Saran / Donasi):",
            "📧 Email: nyamancenter1804@gmail.com",
            "📱 WhatsApp: +6289513491447"
        ]
        full_msg = "\n".join(msg)
        
        def show_help():
            gui.mainFrame.prePopup()
            dlg = InfoDialog(gui.mainFrame, "Bantuan Mode Lapisan Terjemahan", full_msg)
            dlg.ShowModal()
            dlg.Destroy()
            gui.mainFrame.postPopup()
        wx.CallAfter(show_help)

    def script_layer_liveRecording(self, gesture):
        self.record_live_speech = not self.record_live_speech
        self._play_tone("on" if self.record_live_speech else "off")
        ui.message("Terjemahan Real-time " + ("AKTIF." if self.record_live_speech else "NONAKTIF."))

    def script_layer_textInput(self, gesture):
        self.record_live_speech = False
        InputText()

    def script_layer_settings(self, gesture):
        self.record_live_speech = False
        def show_settings():
            gui.mainFrame.prePopup()
            dlg = AITranslaterSettingsDialog(gui.mainFrame)
            dlg.ShowModal()
            dlg.Destroy()
            gui.mainFrame.postPopup()
        wx.CallAfter(show_settings)

    def script_layer_apiCheck(self, gesture):
        self.record_live_speech = False
        engine = config.conf[roleSECTION].get("translationEngine", "Gemini API")
        
        if engine == "InstantTranslate (Tanpa API Key)":
            ui.message("Menghubungi Cloud Service Google Translate...")
            def check_gt():
                result = _call_instant_translate("Testing connection", "Indonesian Indonesia")
                if "Error" not in result:
                    wx.CallAfter(ui.message, "Mesin InstantTranslate Terkoneksi. Siap melayani tanpa syarat API Key!")
                else:
                    wx.CallAfter(ui.message, "Akses Google Translate terblokir atau server tumbang.")
            threading.Thread(target=check_gt, daemon=True).start()
            return
            
        api_key = config.conf[roleSECTION].get("geminiApiKey", "").strip()
        if not api_key:
            ui.message("Kunci API belum diatur. Ganti mesin atau masukkan API.")
            return
            
        ui.message("Memeriksa Pintu Logika API Google Gemini...")
        def check():
            try:
                url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash?key={api_key}"
                r = requests.get(url, timeout=5)
                if r.status_code == 200:
                    wx.CallAfter(ui.message, "API Key Anda VALID! Sisa Token dan Limit Kuota Tersedia.")
                elif r.status_code == 429:
                    wx.CallAfter(ui.message, "API Key benar, sayangnya Limit Token (Kuota) Anda HABIS/Overload (Error 429)! Segera alihkan ke InstantTranslate.")
                else:
                    wx.CallAfter(ui.message, f"Akses ditolak oleh Google. Kegagalan Status HTTP {r.status_code}")
            except Exception:
                wx.CallAfter(ui.message, "Gagal mengoneksikan PC ke layanan Google.")
        threading.Thread(target=check, daemon=True).start()

    def script_layer_clipboard(self, gesture):
        self.record_live_speech = False
        def _bg_translate():
            self._play_tone("process")
            get_translation(api.getClipData(), announce=True)
        threading.Thread(target=_bg_translate, daemon=True).start()

    def script_layer_lastSpoken(self, gesture):
        self.record_live_speech = False
        if self.lastSpoken == "":
            return
        def _bg_translate():
            self._play_tone("process")
            get_translation(self.lastSpoken, announce=True)
        threading.Thread(target=_bg_translate, daemon=True).start()

    def speech_event_override(self, sequence, *args, **kwargs):
        text_blocks = [i for i in range(len(sequence)) if isinstance(sequence[i], (str, int, float, bool, type(None))) and len(str(sequence[i])) > 1 and not str(sequence[i]).isspace()]
        
        if len(text_blocks) == 0:
            self._original_speak(sequence, *args, **kwargs)
            return
            
        spoken_text = "|  ".join([str(sequence[i]) for i in text_blocks])
        if "Error:" not in spoken_text:
            self.lastSpoken = spoken_text

        if self.record_live_speech:
            def _live_bg_task(txt_to_translate):
                self._play_tone("process")
                result = get_translation(txt_to_translate, announce=False)
                if result and result.startswith("Error"):
                    wx.CallAfter(ui.message, result)
                    self.record_live_speech = False
                elif result:
                    wx.CallAfter(self._original_speak, [result])
            
            threading.Thread(target=_live_bg_task, args=(self.lastSpoken,), daemon=True).start()
            return 
            
        self._original_speak(sequence, *args, **kwargs)

    def terminate(self):
        self.record_live_speech = False
        speech.speech.speak = self._original_speak
