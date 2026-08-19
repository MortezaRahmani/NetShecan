import base64
import json
import os
import re
import sys
import threading
import time
import urllib.request
from datetime import datetime
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request

import flet as ft

# Bypass any system/registry/env proxy so this app always connects directly
# (my.irancell.ir / gateway.shatel.ir and Shecan are reached directly).
_direct_opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def _open(req, timeout, attempts=3, delay=1.0):
    """Open a direct connection, retrying transient network failures."""
    last_err = None
    for i in range(attempts):
        try:
            return _direct_opener.open(req, timeout=timeout)
        except HTTPError:
            raise
        except URLError as e:
            last_err = e
            if i == attempts - 1:
                raise
            time.sleep(delay * (2 ** i))
    raise last_err


def _error_text(error):
    reason = getattr(error, "reason", error)
    code = getattr(reason, "winerror", None) or getattr(reason, "errno", None)
    if code == 10013:
        return "Connection blocked by Windows/VPN (10013) - will retry"
    if code == 11001:
        return "DNS lookup failed - will retry"
    if isinstance(reason, TimeoutError):
        return "Connection timed out - will retry"
    return str(error)

try:
    import pystray
    from PIL import Image
except ImportError:
    pystray = None

CONFIG = os.path.join(os.path.dirname(sys.executable), "config.json") \
    if getattr(sys, "frozen", False) else "config.json"
WIN_W = 384
WIN_H = 680

# --- Design tokens (OLED dark / midnight-blue data dashboard) ---
BG = "#0B2432"
SURFACE = "#12384E"
SURFACE_2 = "#17435E"
TRACK = "#1B4A64"
ACCENT = "#FABD32"
ACCENT_TINT = "#1E3A44"
BLUE = "#6BA7FF"
GREEN = "#5DD27A"
DANGER = "#FF8A80"
TEXT = "#FFFFFF"
TEXT_SEC = "#C3DCE6"
MUTED = "#9FC3D4"
DIM = "#6E93A7"
RADIUS = 12
MONO = "Consolas"

_FA_DIGITS = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")


def gb(mb):
    return mb / 1024


def base_path():
    if getattr(sys, "frozen", False):
        return sys._MEIPASS
    return os.path.dirname(os.path.abspath(__file__))


def pretty_name(name):
    if re.search(r"^(\d+)\s?Days\s*-\s", name):
        return name
    return re.sub(r"^(\d+)(\s?Days)\s", r"\1 \2 - ", name)


def _jwt_payload(token):
    try:
        payload = token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        return json.loads(base64.urlsafe_b64decode(payload))
    except Exception:
        return None


def _needs_refresh(token, margin=300):
    p = _jwt_payload(token)
    if not p:
        return True
    exp = p.get("exp") or 0
    return exp - time.time() < margin


def _fa_num(s):
    return s.translate(_FA_DIGITS)


def _center_geometry(width, height):
    try:
        import ctypes
        ctypes.windll.user32.GetSystemMetrics.restype = ctypes.c_int
        sw = ctypes.windll.user32.GetSystemMetrics(0)
        sh = ctypes.windll.user32.GetSystemMetrics(1)
        return (sw - width) // 2, (sh - height) // 2
    except Exception:
        return None


def play_mp3(path):
    """Play an mp3 once via Windows MCI. Blocks until finished; run in a thread."""
    if not path or not os.path.exists(path):
        return
    import ctypes
    alias = f"al{time.time_ns()}"
    mci = ctypes.windll.winmm.mciSendStringW
    mci.argtypes = [ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.c_uint, ctypes.c_void_p]
    mci(f'open "{path}" type mpegvideo alias {alias}', None, 0, 0)
    mci(f'play {alias} wait', None, 0, 0)
    mci(f'close {alias}', None, 0, 0)


# ------------------------------------------------------------------ providers

def irancell_display_name(name, is_gift):
    """Convert Persian offer names to the '30 Days - 20GB' style."""
    n = _fa_num(name)
    m = re.search(r"(\d+)\s*روزه", n)
    g = re.search(r"(\d+)\s*گیگابایت", n)
    if m and g:
        label = f"{m.group(1)} Days - {int(g.group(1))}GB"
        if is_gift:
            label += " (Free)"
        tail = re.search(r"\(([^)]*)\)\s*$", n)
        if tail and "رایگان" not in tail.group(1):
            label += f" ({tail.group(1)})"
        return label
    return name


class IrancellProvider:
    key = "irancell"
    name = "Irancell"
    TOKEN_URL = "https://my.irancell.ir/api/authorization/v1/token"
    API = "https://my.irancell.ir/api/sim/v3/account"

    def __init__(self, app):
        self.app = app
        self._changed = False

    def ensure_token(self, cfg):
        if cfg.get("authorization") and not _needs_refresh(cfg["authorization"]):
            return
        rt = cfg.get("x_authorization_extra") or cfg.get("refresh_token") or ""
        if not rt:
            raise ValueError("No refresh token. Log in and paste it in Settings.")
        body = {
            "grant_type": "refresh_token",
            "refresh_token": rt,
            "device_name": cfg.get("device_name", "Web Windows 10"),
            "client_id": cfg.get("client_id", ""),
            "client_secret": cfg.get("client_secret", ""),
            "client_version": cfg.get("client_version", "9.77.1"),
            "installation_id": cfg.get("installation_id", ""),
        }
        headers = {
            "Content-Type": "application/json",
            "Accept-Language": "fa",
            "Accept": "application/json, text/plain, */*",
            "Authorization": cfg.get("authorization") or "",
        }
        req = Request(self.TOKEN_URL, data=json.dumps(body).encode(), headers=headers)
        with _open(req, 20) as r:
            data = json.load(r)
        if "access_token" not in data:
            raise ValueError("Token refresh failed: " + str(data.get("detail", data))[:120])
        cfg["authorization"] = data["access_token"]
        if data.get("refresh_token"):
            cfg["x_authorization_extra"] = cfg["refresh_token"] = data["refresh_token"]
        self._changed = True
        self.app._save_config()

    def fetch(self, cfg):
        self.ensure_token(cfg)
        headers = {
            "accept": cfg.get("accept", "application/json, text/plain, */*"),
            "authorization": cfg["authorization"],
            "x-app-version": cfg.get("client_version", ""),
            "accept-language": cfg.get("accept_language", "fa"),
        }
        req = Request(self.API, headers=headers)
        with _open(req, 20) as r:
            data = json.load(r)
        offers_raw = data.get("active_offers") or []
        if not offers_raw:
            raise ValueError("No active data packages returned by Irancell.")
        offers = [{
            "name": irancell_display_name(o.get("name", ""), o.get("is_gift", False)),
            "is_gift": o.get("is_gift", False),
            "global_data_remaining": o.get("global_data_remaining", 0),
            "total_amount": o.get("total_amount", 0),
            "expiry_date": o.get("expiry_date", ""),
        } for o in offers_raw]
        main_index = next((i for i, o in enumerate(offers) if not o["is_gift"]), 0)
        main = offers[main_index]
        if cfg.get("include_additional_packages", False):
            agg = next((c for c in data.get("cumulative_amounts", []) if c.get("type") == "data"), None)
            if agg:
                agg_rem, agg_tot = agg.get("remained", 0), agg.get("total", 0)
            else:
                agg_rem = sum(o["global_data_remaining"] for o in offers)
                agg_tot = sum(o["total_amount"] for o in offers)
        else:
            agg_rem, agg_tot = main["global_data_remaining"], main["total_amount"]
        return {
            "provider_name": "Irancell",
            "active_offers": offers,
            "main_index": main_index,
            "aggregate_remaining_mb": agg_rem,
            "aggregate_total_mb": agg_tot,
        }


class ShatelProvider:
    key = "shatel"
    name = "Shatel"
    TOKEN_URL = "https://account-api.shatel.ir/connect/token"
    PKG_URL = "https://gateway.shatel.ir/api/v1.0/myshatelB2cWeb/traffics/active-packages"
    SVC_URL = "https://gateway.shatel.ir/api/v1.0/myshatelB2cWeb/services/current"

    def __init__(self, app):
        self.app = app
        self._changed = False

    def ensure_token(self, cfg):
        if cfg.get("access_token") and not _needs_refresh(cfg["access_token"]):
            return
        rt = cfg.get("refresh_token", "")
        if not rt:
            raise ValueError("No refresh token. Log in and paste it in Settings.")
        body = urlencode({
            "grant_type": "refresh_token",
            "refresh_token": rt,
            "client_id": cfg.get("client_id", "MyShatelB2cWeb"),
        })
        headers = {
            "Content-Type": "application/x-www-form-urlencoded",
            "Accept": "application/json",
            "Origin": "https://beta.my.shatel.ir",
        }
        req = Request(self.TOKEN_URL, data=body.encode(), headers=headers)
        with _open(req, 20) as r:
            data = json.load(r)
        if "access_token" not in data:
            raise ValueError("Token refresh failed: " + str(data.get("error", data))[:120])
        cfg["access_token"] = data["access_token"]
        if data.get("refresh_token"):
            cfg["refresh_token"] = data["refresh_token"]
        self._changed = True
        self.app._save_config()

    def _get(self, url, headers):
        req = Request(url, headers=headers)
        with _open(req, 20) as r:
            return json.load(r)

    def _pkg_name(self, p, plan_days, is_main, exp):
        gb = round(p.get("totalKb", 0) / 1048576)
        label = f"{plan_days} Days - {gb}GB"
        ptype = p.get("type", "")
        if ptype and ptype != "Base":
            label += f" {ptype}"
        if not is_main and exp:
            label += f" (Exp: {exp})"
        return label

    def fetch(self, cfg):
        self.ensure_token(cfg)
        headers = {"Authorization": "Bearer " + cfg["access_token"],
                   "Accept": "*/*", "Accept-Language": "fa"}
        pkg = self._get(self.PKG_URL, headers)
        svc = self._get(self.SVC_URL, headers)
        result = pkg.get("result") or {}
        packages = result.get("packages") or []
        if not packages:
            raise ValueError("No active traffic packages returned by Shatel.")
        svcres = svc.get("result") or {}
        plan_days = int(svcres.get("durationInMonths") or 0) * 30 or 30
        main_pkg = next((p for p in packages if p.get("inUse")), packages[0])
        offers = []
        main_index = 0
        for i, p in enumerate(packages):
            is_main = p is main_pkg
            exp = (p.get("expirationDate") or "")[:10]
            offers.append({
                "name": self._pkg_name(p, plan_days, is_main, exp),
                "is_gift": False,
                "global_data_remaining": p.get("remainingKb", 0) / 1024,
                "total_amount": p.get("totalKb", 0) / 1024,
                "expiry_date": exp,
            })
            if is_main:
                main_index = i
        main = offers[main_index]
        if cfg.get("include_additional_packages", True):
            agg_rem = result.get("remainingKb", 0) / 1024
            agg_tot = result.get("totalKb", 0) / 1024
        else:
            agg_rem, agg_tot = main["global_data_remaining"], main["total_amount"]
        return {
            "provider_name": "Shatel",
            "active_offers": offers,
            "main_index": main_index,
            "aggregate_remaining_mb": agg_rem,
            "aggregate_total_mb": agg_tot,
        }


class MciProvider:
    key = "mci"
    name = "MCI"
    TOKEN_URL = "https://my.mci.ir/api/idm/v1/auth"
    API = "https://my.mci.ir/api/unit/v1/packages/details"

    def __init__(self, app):
        self.app = app
        self._changed = False

    def ensure_token(self, cfg):
        if cfg.get("access_token") and not _needs_refresh(cfg["access_token"]):
            return
        rt = cfg.get("refresh_token", "")
        if not rt:
            raise ValueError("No refresh token. Log in and paste it in Settings.")
        body = {
            "username": cfg.get("username", ""),
            "credential_type": "REFRESH_TOKEN",
            "credential": rt,
        }
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/plain, */*",
            "version": cfg.get("version", "1.31.8"),
            "platform": "WEB",
        }
        req = Request(self.TOKEN_URL, data=json.dumps(body).encode(), headers=headers)
        with _open(req, 20) as r:
            data = json.load(r)
        if "access_token" not in data:
            raise ValueError("Token refresh failed: " + str(data.get("message", data))[:120])
        cfg["access_token"] = data["access_token"]
        if data.get("refresh_token"):
            cfg["refresh_token"] = data["refresh_token"]
        self._changed = True
        self.app._save_config()

    def _get(self, url, headers):
        req = Request(url, headers=headers)
        with _open(req, 20) as r:
            return json.load(r)

    def _offer_name(self, item, total_gb):
        days = 0
        exp = item.get("expireTime") or ""
        try:
            days = max(0, (datetime.fromisoformat(exp) - datetime.now()).days)
        except ValueError:
            pass
        return f"{days} Days - {round(total_gb)}GB"

    def fetch(self, cfg):
        self.ensure_token(cfg)
        headers = {
            "Authorization": "Bearer " + cfg["access_token"],
            "Accept": "application/json, text/plain, */*",
            "accept-language": cfg.get("accept_language", "en-GB"),
            "version": cfg.get("version", "1.31.8"),
            "platform": "WEB",
        }
        data = self._get(self.API, headers)
        offers = []
        for item in data.get("packageItems") or []:
            if item.get("type") != "internet":
                continue
            total_gb = item.get("totalInitValue", 0)
            rem_gb = item.get("totalUnusedValue", 0)
            offers.append({
                "name": self._offer_name(item, total_gb),
                "is_gift": False,
                "global_data_remaining": rem_gb * 1024,
                "total_amount": total_gb * 1024,
                "expiry_date": (item.get("expireTime") or "")[:10],
            })
        if not offers:
            raise ValueError("No active internet packages returned by MCI.")
        main = offers[0]
        if cfg.get("include_additional_packages", False):
            agg_rem = data.get("totalUnusedBytes", 0) * 1024
            agg_tot = data.get("totalInitBytes", 0) * 1024
        else:
            agg_rem, agg_tot = main["global_data_remaining"], main["total_amount"]
        return {
            "provider_name": "MCI",
            "active_offers": offers,
            "main_index": 0,
            "aggregate_remaining_mb": agg_rem,
            "aggregate_total_mb": agg_tot,
        }


PROVIDERS = {p.key: p for p in (IrancellProvider, ShatelProvider, MciProvider)}

# Per-provider keys that a pasted JSON (from the helper extension) may set.
# Preference keys like include_additional_packages are intentionally excluded
# so pasting tokens never clobbers a user's aggregation choice.
PASTE_KEYS = {
    "irancell": ["authorization", "x_authorization_extra", "client_version",
                 "client_id", "client_secret", "device_name", "installation_id",
                 "accept", "accept_language"],
    "shatel": ["refresh_token", "access_token", "client_id"],
    "mci": ["username", "refresh_token", "access_token", "version",
            "platform", "accept_language"],
}


def parse_paste_json(text):
    """Parse + validate a pasted provider JSON. Returns (provider_key, dict) or raises."""
    data = json.loads(text)
    if not isinstance(data, dict):
        raise ValueError("Clipboard does not contain a JSON object.")
    key = data.get("provider")
    if key not in PROVIDERS:
        raise ValueError(f"Unknown provider {key!r} in pasted JSON.")
    fields = {}
    for k in PASTE_KEYS[key]:
        if k in data and data[k] is not None:
            fields[k] = data[k]
    if not fields:
        raise ValueError("Pasted JSON has no recognized provider fields.")
    return key, fields


# ------------------------------------------------------------------ app

class NetShecanApp:
    def __init__(self, page: ft.Page):
        self.page = page
        self.cfg = self._load_config()
        self.provider = PROVIDERS[self.cfg.get("provider", "irancell")](self)
        page.title = "NetShecan"
        icon = os.path.join(base_path(), "assets", "icons", "netshecan-icon.ico")
        if os.path.exists(icon):
            page.window.icon = icon
        page.window.width = WIN_W
        page.window.height = WIN_H
        page.window.resizable = False
        pos = _center_geometry(WIN_W, WIN_H)
        if pos:
            page.window.left, page.window.top = pos
        page.bgcolor = BG
        page.padding = ft.Padding.symmetric(horizontal=20, vertical=16)
        page.horizontal_alignment = ft.CrossAxisAlignment.CENTER
        self.busy = False
        self.tray = None
        self._shecan_busy = False
        self._has_data = False
        self._usage_alerted = False
        self._shecan_alerted = False
        self._startup = True
        page.window.on_event = self._on_window_event
        self._build()
        self.refresh()
        threading.Thread(target=self._poll, daemon=True).start()
        page.window.visible = True
        page.update()
        threading.Timer(3.0, self._end_startup).start()

    def _end_startup(self):
        self._startup = False

    def _run_on_ui(self, callback, *args):
        async def run():
            callback(*args)

        self.page.run_task(run)

    def _on_window_event(self, e):
        if e.type == ft.WindowEventType.MINIMIZE and self.cfg.get("minimize_to_tray", False):
            self._hide_to_tray()

    def _hide_to_tray(self):
        if pystray is None:
            return
        self.page.window.visible = False
        self.page.update()
        if self.tray is None:
            png = os.path.join(base_path(), "assets", "icons", "netshecan-icon.png")
            image = Image.open(png)
            menu = pystray.Menu(
                pystray.MenuItem("Show", self._restore_from_tray, default=True),
                pystray.MenuItem("Exit", self._quit_from_tray),
            )
            self.tray = pystray.Icon("NetShecan", image, "NetShecan", menu)
            self.tray.run_detached()

    def _restore_from_tray(self, icon=None, item=None):
        self._bring_to_front()

    def _quit_from_tray(self, icon=None, item=None):
        self._stop_tray()
        self.page.run_task(self.page.window.destroy)

    def _stop_tray(self):
        if self.tray is not None:
            self.tray.stop()
            self.tray = None

    def _bring_to_front(self):
        self._stop_tray()

        async def _restore():
            self.page.window.visible = True
            if self.page.window.minimized:
                self.page.window.minimized = False
            self.page.update()
            await self.page.window.to_front()

        self.page.run_task(_restore)

    def _fire_alert(self, audio):
        if self._startup:
            threading.Timer(3.0, self._bring_to_front).start()
        else:
            self._bring_to_front()
        threading.Thread(target=play_mp3,
                         args=(os.path.join(base_path(), audio),), daemon=True).start()

    def _check_usage_alert(self, remaining_mb):
        threshold = int(self.cfg.get("usage_threshold_mb", 300))
        if remaining_mb <= threshold:
            if not self._usage_alerted:
                self._usage_alerted = True
                self._fire_alert(os.path.join("assets", "audio", "low-usage-alert.mp3"))
        else:
            self._usage_alerted = False

    def _check_shecan_alert(self, ok):
        if not ok:
            if not self._shecan_alerted:
                self._shecan_alerted = True
                self._fire_alert(os.path.join("assets", "audio", "shecan-alert.mp3"))
        else:
            self._shecan_alerted = False

    # ------------------------------------------------------------------ UI
    def _provider_icon(self, key):
        path = os.path.join(base_path(), "assets", "providers", f"{key}.png")
        return path if os.path.exists(path) else ""

    def _filled_providers(self):
        filled = []
        for key in PROVIDERS:
            p = self.cfg["providers"].get(key, {})
            if key == "irancell":
                has = bool(p.get("authorization") or p.get("x_authorization_extra"))
            else:
                has = bool(p.get("refresh_token"))
            if has:
                filled.append(key)
        return filled

    def _switch_provider(self, key):
        if key == self.cfg.get("provider"):
            return
        self.cfg["provider"] = key
        self._save_config()
        self.provider = PROVIDERS[key](self)
        self.switcher_row.controls = self._switcher_controls()
        self.refresh()

    def _switcher_controls(self):
        active = self.cfg.get("provider")
        controls = []
        for key in self._filled_providers():
            icon = self._provider_icon(key)
            is_active = key == active
            controls.append(ft.Container(
                content=ft.Image(src=icon, width=26, height=26, fit=ft.BoxFit.CONTAIN),
                width=44, height=44,
                alignment=ft.Alignment.CENTER,
                bgcolor=SURFACE_2,
                border=ft.Border.all(2, ACCENT) if is_active
                        else ft.Border.all(1, ACCENT_TINT),
                border_radius=RADIUS,
                tooltip=PROVIDERS[key].name,
                on_click=lambda e, k=key: self._switch_provider(k),
            ))
        return controls

    def _build(self):
        self.provider_icon = ft.Image(src=self._provider_icon(""),
                                      width=16, height=16, fit=ft.BoxFit.CONTAIN)
        self.provider_text = ft.Text("--", size=10, weight=ft.FontWeight.W_700,
                                     color=BLUE)
        self.name_text = ft.Text("--", size=13, weight=ft.FontWeight.W_600, color=ACCENT)
        self.switcher_row = ft.Row(
            controls=self._switcher_controls(),
            spacing=10,
            alignment=ft.MainAxisAlignment.CENTER,
        )

        # hero ring with glow + centered number
        self.remain_text = ft.Text("--", size=32, weight=ft.FontWeight.W_700,
                                   color=TEXT, font_family=MONO)
        self.unit_text = ft.Text("GB", size=13, weight=ft.FontWeight.W_500, color=MUTED)
        self.ring = ft.ProgressRing(width=148, height=148, stroke_width=12,
                                    stroke_cap=ft.StrokeCap.BUTT,
                                    color=ACCENT, bgcolor=TRACK, value=0)
        hero = ft.Stack(
            controls=[
                self.ring,
                ft.Column([self.remain_text, self.unit_text],
                          alignment=ft.MainAxisAlignment.CENTER,
                          horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                          spacing=0),
            ],
            width=158,
            height=158,
            alignment=ft.Alignment.CENTER,
        )

        self.sub_text = ft.Text("", size=12, color=TEXT_SEC)

        # stats cards
        self.rem_val = self._stat_value()
        self.used_val = self._stat_value()
        self.total_val = self._stat_value()
        stats_row = ft.Row(
            [
                self._stat_card(self.rem_val, "REMAINING", accent=True),
                self._stat_card(self.used_val, "USED"),
                self._stat_card(self.total_val, "TOTAL"),
            ],
            spacing=10,
        )

        self.expiry_text = ft.Text("", size=11, color=TEXT_SEC, font_family=MONO)
        expiry_row = ft.Row(
            [ft.Icon(ft.Icons.EVENT, size=14, color=MUTED), self.expiry_text],
            spacing=6,
            alignment=ft.MainAxisAlignment.CENTER,
        )

        # other packages card
        self.others_header = ft.Text("OTHER PACKAGES", size=10,
                                     weight=ft.FontWeight.W_700, color=TEXT_SEC)
        self.others_col = ft.Column(spacing=2)
        others_card = ft.Container(
            content=ft.Column(
                [
                    ft.Row([ft.Icon(ft.Icons.DATA_USAGE, size=14, color=MUTED),
                            self.others_header], spacing=8),
                    ft.Container(height=6),
                    self.others_col,
                ],
                spacing=0,
            ),
            bgcolor=SURFACE,
            border_radius=RADIUS,
            padding=ft.Padding.all(14),
        )

        shecan_url = self.cfg.get("shecan_url", "").strip()
        check_shecan = self.cfg.get("check_shecan", True)
        self.shecan_text = ft.Text("Shecan: checking..." if (check_shecan and shecan_url) else "",
                                   size=10, color=MUTED, visible=bool(check_shecan and shecan_url))
        self.status_text = ft.Text("", size=10, color=MUTED)
        bottom = ft.Column(
            [
                ft.Row(
                    [
                        self._btn("Refresh", ft.Icons.REFRESH, self._on_refresh_click,
                                  primary=True),
                        self._btn("Settings", ft.Icons.SETTINGS, self.open_settings),
                    ],
                    alignment=ft.MainAxisAlignment.CENTER,
                    spacing=10,
                ),
                ft.Container(height=6),
                self.shecan_text,
                ft.Container(height=2),
                self.status_text,
            ],
            horizontal_alignment=ft.CrossAxisAlignment.CENTER,
            spacing=0,
        )

        middle = ft.Column(
            [
                ft.Container(height=6),
                ft.Row([self.provider_icon, self.provider_text], spacing=4,
                       alignment=ft.MainAxisAlignment.CENTER),
                ft.Container(height=8),
                self.switcher_row,
                ft.Container(height=10),
                self.name_text,
                ft.Container(height=10),
                hero,
                ft.Container(height=8),
                self.sub_text,
                ft.Container(height=12),
                stats_row,
                ft.Container(height=10),
                expiry_row,
                ft.Divider(color=TRACK, height=18),
                others_card,
            ],
            horizontal_alignment=ft.CrossAxisAlignment.CENTER,
            spacing=0,
            alignment=ft.MainAxisAlignment.CENTER,
            scroll=ft.ScrollMode.AUTO,
        )

        self.page.add(
            ft.Column(
                [
                    self._header(),
                    ft.Container(content=middle, expand=True),
                    bottom,
                ],
                expand=True,
                spacing=0,
            )
        )

    def _header(self):
        return ft.Row(
            [
                ft.Text("NetShecan", size=16, weight=ft.FontWeight.W_700,
                        color=TEXT),
                ft.Container(
                    ft.Row(
                        [ft.Icon(ft.Icons.CIRCLE, size=8, color=GREEN),
                         ft.Text("LIVE", size=9, weight=ft.FontWeight.W_700, color=GREEN)],
                        spacing=5,
                    ),
                    padding=ft.Padding.symmetric(horizontal=9, vertical=4),
                    border_radius=12,
                    bgcolor=ft.Colors.with_opacity(0.10, GREEN),
                ),
            ],
            alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
        )

    def _stat_value(self):
        return ft.Text("--", size=16, weight=ft.FontWeight.W_700,
                       color=TEXT, font_family=MONO)

    def _stat_card(self, value, caption, accent=False):
        return ft.Container(
            expand=True,
            bgcolor=SURFACE,
            border=ft.Border.all(1, ACCENT_TINT) if accent else None,
            border_radius=RADIUS,
            padding=ft.Padding.symmetric(vertical=12),
            alignment=ft.Alignment.CENTER,
            content=ft.Column(
                [
                    value,
                    ft.Text(caption, size=9, weight=ft.FontWeight.W_700,
                            color=ACCENT if accent else DIM),
                ],
                spacing=4,
                horizontal_alignment=ft.CrossAxisAlignment.CENTER,
            ),
        )

    def _btn(self, label, icon, on_click, primary=False):
        return ft.Button(
            content=ft.Row(
                [ft.Icon(icon, size=16, color=BG if primary else BLUE),
                 ft.Text(label, size=12,
                         weight=ft.FontWeight.W_600 if primary else ft.FontWeight.W_500,
                         color=BG if primary else TEXT)],
                spacing=7,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
            ),
            style=ft.ButtonStyle(
                bgcolor=ACCENT if primary else SURFACE_2,
                color=BG if primary else TEXT,
                overlay_color=ft.Colors.with_opacity(0.18, "#000000") if primary
                            else ft.Colors.with_opacity(0.14, "#FFFFFF"),
                elevation=0,
                mouse_cursor=ft.MouseCursor.CLICK,
                shape=ft.RoundedRectangleBorder(radius=10),
                padding=ft.Padding.symmetric(horizontal=14, vertical=9),
            ),
            on_click=on_click,
        )

    # ----------------------------------------------------------------- config
    def _default_cfg(self):
        return {
            "provider": "irancell",
            "providers": {
                "irancell": {
                    "authorization": "", "x_authorization_extra": "",
                    "include_additional_packages": False,
                    "client_id": "4725a997e94b372b1c26e425086f4a17",
                    "client_secret": "7e9379a4d444a3c21cf28da6a032154dc4b644eba523e7684f71818dec3beeb7",
                    "client_version": "9.77.1",
                    "device_name": "Web Windows 10",
                    "installation_id": "",
                    "accept": "application/json, text/plain, */*",
                    "accept_language": "fa",
                },
                "shatel": {
                    "refresh_token": "", "access_token": "",
                    "include_additional_packages": True,
                    "client_id": "MyShatelB2cWeb",
                },
                "mci": {
                    "refresh_token": "", "access_token": "",
                    "include_additional_packages": False,
                    "username": "", "version": "1.31.8", "platform": "WEB",
                    "accept_language": "en-GB",
                },
            },
            "poll_seconds": 300, "minimize_to_tray": False,
            "check_shecan": True, "shecan_url": "", "usage_threshold_mb": 300,
        }

    def _load_config(self):
        try:
            with open(CONFIG, encoding="utf-8") as f:
                cfg = json.load(f)
        except (OSError, ValueError):
            cfg = {}
        defaults = self._default_cfg()
        # migrate old flat irancell-only config
        if "providers" not in cfg:
            old = dict(cfg)
            cfg = defaults
            ir = cfg["providers"]["irancell"]
            ir["authorization"] = old.get("authorization", "")
            ir["x_authorization_extra"] = old.get("x_authorization_extra", "")
            ir["client_version"] = old.get("x_app_version", ir["client_version"])
            ir["accept"] = old.get("accept", ir["accept"])
            ir["accept_language"] = old.get("accept_language", ir["accept_language"])
            for k in ("poll_seconds", "minimize_to_tray", "shecan_url", "usage_threshold_mb"):
                if k in old:
                    cfg[k] = old[k]
            self._save_config(cfg)
        # fill any missing provider defaults
        for key, prov in PROVIDERS.items():
            cfg.setdefault("providers", {}).setdefault(key, {})
            for dk, dv in defaults["providers"][key].items():
                cfg["providers"][key].setdefault(dk, dv)
        return cfg

    def _save_config(self, cfg=None):
        with open(CONFIG, "w", encoding="utf-8") as f:
            json.dump(cfg if cfg is not None else self.cfg, f, ensure_ascii=False, indent=4)

    # ----------------------------------------------------------------- fetch
    def _fetch(self, cfg):
        key = cfg.get("provider", "irancell")
        if key not in PROVIDERS:
            raise ValueError(f"Unknown provider: {key}")
        prov = PROVIDERS[key](self)
        data = prov.fetch(cfg["providers"][key])
        return data

    def refresh(self):
        if self.busy or self._shecan_busy:
            return
        self.busy = True
        self.status_text.value = "Updating..."
        self.status_text.color = MUTED
        self.page.update()
        threading.Thread(target=self._work, args=(dict(self.cfg),), daemon=True).start()

    def _work(self, cfg):
        try:
            data = self._fetch(cfg)
            self._run_on_ui(self._render, data, None)
        except HTTPError as e:
            msg = "Session expired - open Settings and paste a new token." if e.code == 401 \
                else f"HTTP error {e.code}"
            self._run_on_ui(self._render, None, msg)
        except Exception as e:
            self._run_on_ui(self._render, None, _error_text(e))

    def _on_refresh_click(self, e):
        self.refresh()

    def check_shecan(self):
        if not self.cfg.get("check_shecan", True):
            return
        url = self.cfg.get("shecan_url", "").strip()
        if not url or self._shecan_busy:
            return
        self._shecan_busy = True
        self.shecan_text.value = "Shecan: checking..."
        self.shecan_text.color = MUTED
        self.page.update()
        threading.Thread(target=self._shecan_work, args=(url,), daemon=True).start()

    def _shecan_work(self, url):
        ip = None
        try:
            with _open(Request(url), timeout=15) as r:
                ip = r.read().decode("utf-8", "replace").strip()
        except Exception:
            ip = None
        ok = False
        if ip:
            try:
                with _open(Request("https://check.shecan.ir"), timeout=15) as r:
                    ok = r.read().decode("utf-8", "replace").strip() == "2"
            except Exception:
                ok = False
        text = f"Shecan: {ip} \u2705" if ok else \
               (f"Shecan: {ip} \u274c" if ip else "Shecan: unreachable \u274c")
        self._run_on_ui(self._set_shecan, text, GREEN if ok else DANGER, ok)

    def _set_shecan(self, text, color, ok=None):
        self._shecan_busy = False
        self.shecan_text.value = text
        self.shecan_text.color = color
        if ok is not None:
            self._check_shecan_alert(ok)
        self.page.update()

    def _poll(self):
        while True:
            try:
                seconds = max(1, float(self.cfg.get("poll_seconds", 120)))
            except (TypeError, ValueError):
                seconds = 120
            time.sleep(seconds)
            self._run_on_ui(self.refresh)

    def _render(self, data, error):
        self.busy = False
        if error:
            if not self._has_data:
                self.name_text.value = "ERROR"
                self.sub_text.value = error
            self.status_text.value = f"Update failed {time.strftime('%H:%M:%S')}: {error}"
            self.status_text.color = DANGER
            self.page.update()
            self.check_shecan()
            return

        offers = data["active_offers"]
        main = offers[data["main_index"]]
        rem = gb(data["aggregate_remaining_mb"])
        tot = gb(data["aggregate_total_mb"])
        used = tot - rem

        self.provider_icon.src = self._provider_icon(self.cfg.get("provider", ""))
        self.provider_text.value = data["provider_name"].upper()
        self.name_text.value = pretty_name(main["name"])
        self.remain_text.value = f"{rem:.2f}"
        self.ring.value = rem / tot if tot else 0
        self.sub_text.value = f"{used:.2f} of {tot:.2f} GB used"
        self.expiry_text.value = f"Expires {main['expiry_date']}"
        self.rem_val.value = f"{rem:.2f}"
        self.used_val.value = f"{used:.2f}"
        self.total_val.value = f"{tot:.2f}"

        self.others_col.controls.clear()
        for i, o in enumerate(offers):
            if i == data["main_index"]:
                continue
            self.others_col.controls.append(
                ft.Row(
                    [
                        ft.Icon(ft.Icons.CIRCLE, size=7, color=ACCENT),
                        ft.Text(pretty_name(o["name"]), size=11, color=TEXT_SEC, expand=True),
                        ft.Text(f"{gb(o['global_data_remaining']):.1f} GB", size=11,
                                weight=ft.FontWeight.W_600, color=TEXT, font_family=MONO),
                    ],
                    spacing=8,
                    vertical_alignment=ft.CrossAxisAlignment.CENTER,
                )
            )
        self.others_header.value = f"OTHER PACKAGES  ({len(offers) - 1})" \
            if len(offers) > 1 else "NO OTHER PACKAGES"
        self._has_data = True
        self._check_usage_alert(data["aggregate_remaining_mb"])
        self._stamp()
        self.page.update()
        self.check_shecan()

    def _stamp(self):
        self.status_text.value = f"Updated {time.strftime('%H:%M:%S')}"
        self.status_text.color = MUTED

    def open_settings(self):
        prov_key = self.cfg.get("provider", "irancell")
        pcfg = self.cfg["providers"][prov_key]

        def field(label, value, multiline=False):
            return ft.TextField(
                label=label,
                value=value,
                multiline=multiline,
                min_lines=5 if multiline else 1,
                max_lines=8 if multiline else 1,
                text_style=ft.TextStyle(font_family=MONO) if multiline else None,
                text_size=11,
                border_radius=8,
            )

        prov_dd = ft.Dropdown(
            label="Provider",
            options=[ft.dropdown.Option(k, PROVIDERS[k].name) for k in PROVIDERS],
            value=prov_key,
            text_size=12,
        )

        self._pf = {}
        self._psw = {}
        self._prov_fields_box = ft.Column(spacing=10)

        def rebuild_provider_fields(key, initial=False):
            p = self.cfg["providers"][key]
            self._pf.clear()
            self._psw.clear()
            controls = []
            if key == "irancell":
                self._pf["authorization"] = field("Access Token", p.get("authorization", ""), True)
                self._pf["x_authorization_extra"] = field("Refresh Token", p.get("x_authorization_extra", ""), True)
                self._pf["client_version"] = field("Client Version", p.get("client_version", "9.77.1"))
                self._pf["client_id"] = field("Client ID", p.get("client_id", ""))
                self._pf["client_secret"] = field("Client Secret", p.get("client_secret", ""), True)
                self._pf["device_name"] = field("Device Name", p.get("device_name", "Web Windows 10"))
                self._pf["installation_id"] = field("Installation ID", p.get("installation_id", ""))
                self._pf["accept"] = field("Accept", p.get("accept", "application/json, text/plain, */*"))
                self._pf["accept_language"] = field("Accept-Language", p.get("accept_language", "fa"))
            elif key == "shatel":
                self._pf["refresh_token"] = field("Refresh Token", p.get("refresh_token", ""), True)
                self._pf["access_token"] = field("Access Token (auto)", p.get("access_token", ""), True)
                self._pf["client_id"] = field("Client ID", p.get("client_id", "MyShatelB2cWeb"))
            else:
                self._pf["username"] = field("Username", p.get("username", ""))
                self._pf["refresh_token"] = field("Refresh Token", p.get("refresh_token", ""), True)
                self._pf["access_token"] = field("Access Token (auto)", p.get("access_token", ""), True)
                self._pf["version"] = field("Version", p.get("version", "1.31.8"))
                self._pf["platform"] = field("Platform", p.get("platform", "WEB"))
                self._pf["accept_language"] = field("Accept-Language", p.get("accept_language", "en-GB"))
            controls.append(ft.Container(ft.Column(list(self._pf.values()), spacing=10)))
            sw = ft.Switch(
                label="Include Additional Data Packages",
                value=bool(p.get("include_additional_packages", False)),
            )
            self._psw["include_additional_packages"] = sw
            controls.append(ft.Container(
                ft.Row([sw], spacing=6),
                padding=ft.Padding.symmetric(vertical=2),
            ))
            self._prov_fields_box.controls = controls
            if not initial:
                self.dlg.update()

        def on_provider_change(e):
            rebuild_provider_fields(prov_dd.value)

        prov_dd.on_select = on_provider_change

        paste_btn = ft.Button(
            content=ft.Row(
                [ft.Icon(ft.Icons.CONTENT_PASTE, size=16, color=TEXT),
                 ft.Text("Paste from Extension", size=12, weight=ft.FontWeight.W_600,
                         color=TEXT)],
                spacing=7,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
            ),
            style=ft.ButtonStyle(
                bgcolor=SURFACE_2,
                color=TEXT,
                overlay_color=ft.Colors.with_opacity(0.14, "#FFFFFF"),
                elevation=0,
                mouse_cursor=ft.MouseCursor.CLICK,
                shape=ft.RoundedRectangleBorder(radius=10),
                padding=ft.Padding.symmetric(horizontal=14, vertical=9),
            ),
            on_click=lambda e: paste_from_extension(),
        )

        def paste_from_extension():
            async def run():
                try:
                    text = await self.page.clipboard.get()
                except Exception:
                    text = None
                if not text:
                    _paste_error("Clipboard is empty.")
                    return
                try:
                    key, fields = parse_paste_json(text)
                except (ValueError, json.JSONDecodeError) as err:
                    _paste_error(str(err))
                    return
                _paste_confirm(key, fields)

            self.page.run_task(run)

        def _paste_error(msg):
            self._paste_info_dlg = ft.AlertDialog(
                modal=True,
                title=ft.Text("Paste from Extension"),
                content=ft.Text(msg),
                actions=[ft.Button("OK", on_click=lambda e: _close_paste_info())],
                actions_alignment=ft.MainAxisAlignment.END,
            )
            self.page.overlay.append(self._paste_info_dlg)
            self._paste_info_dlg.open = True
            self.page.update()

        def _close_paste_info():
            if getattr(self, "_paste_info_dlg", None):
                self._paste_info_dlg.open = False
                self.page.update()

        def _paste_confirm(key, fields):
            names = {k: (v if len(str(v)) <= 40 else str(v)[:37] + "...")
                     for k, v in fields.items()}
            body = "\n".join(f"{k}: {v}" for k, v in names.items())
            self._paste_dlg = ft.AlertDialog(
                modal=True,
                title=ft.Text(f"Apply {PROVIDERS[key].name} tokens?"),
                content=ft.Container(
                    ft.Column(
                        [ft.Text("This will update config.json and switch provider:", size=12),
                         ft.Text(body, size=11, font_family=MONO)],
                        spacing=8,
                    ),
                    width=420,
                ),
                actions=[
                    ft.Button("Apply", on_click=lambda e: _apply_paste(key, fields)),
                    ft.Button("Cancel", on_click=lambda e: _close_paste_confirm()),
                ],
                actions_alignment=ft.MainAxisAlignment.END,
            )
            self.page.overlay.append(self._paste_dlg)
            self._paste_dlg.open = True
            self.page.update()

        def _close_paste_confirm():
            if getattr(self, "_paste_dlg", None):
                self._paste_dlg.open = False
                self.page.update()

        def _apply_paste(key, fields):
            p = self.cfg["providers"][key]
            for k, v in fields.items():
                p[k] = v
            self.cfg["provider"] = key
            self._save_config()
            self.provider = PROVIDERS[key](self)
            _close_paste_confirm()
            prov_dd.value = key
            rebuild_provider_fields(key)
            self.dlg.update()

        f_poll = field("Auto Check Interval (minutes)",
                       str(self.cfg.get("poll_seconds", 300) // 60))
        f_usage = field("Usage Threshold Alert (MB)",
                        str(self.cfg.get("usage_threshold_mb", 300)))
        f_shecan = field("Shecan URL", self.cfg.get("shecan_url", ""))
        f_shecan_check = ft.Switch(label="Check Shecan",
                                   value=bool(self.cfg.get("check_shecan", True)))
        f_tray = ft.Switch(label="Minimize to tray",
                           value=bool(self.cfg.get("minimize_to_tray", False)))

        content = ft.Column(
            [ft.Container(height=4), prov_dd, paste_btn, ft.Container(height=10),
             self._prov_fields_box,
             f_poll, f_usage, f_shecan, f_shecan_check, f_tray],
            spacing=10,
            scroll=ft.ScrollMode.AUTO,
        )

        def save(e):
            key = prov_dd.value
            self.cfg["provider"] = key
            p = self.cfg["providers"][key]
            for k, f in self._pf.items():
                p[k] = f.value.strip()
            for k, sw in self._psw.items():
                p[k] = bool(sw.value)
            self.cfg["shecan_url"] = f_shecan.value.strip()
            self.cfg["check_shecan"] = bool(f_shecan_check.value)
            self.cfg["minimize_to_tray"] = bool(f_tray.value)
            try:
                self.cfg["usage_threshold_mb"] = max(1, int(f_usage.value))
            except ValueError:
                pass
            try:
                self.cfg["poll_seconds"] = max(1, int(f_poll.value)) * 60
            except ValueError:
                pass
            self._save_config()
            self.provider = PROVIDERS[key](self)
            self.dlg.open = False
            self.page.update()
            check = self.cfg.get("check_shecan", True) and bool(self.cfg.get("shecan_url", "").strip())
            self.shecan_text.visible = check
            self.refresh()

        def cancel(e):
            self.dlg.open = False
            self.page.update()

        self.dlg = ft.AlertDialog(
            modal=True,
            title=ft.Text("Request settings"),
            content=ft.Container(content, width=480,
                                 padding=ft.Padding.only(top=8)),
            actions=[
                ft.Button("Save", on_click=save),
                ft.Button("Cancel", on_click=cancel),
            ],
            actions_alignment=ft.MainAxisAlignment.END,
        )
        rebuild_provider_fields(prov_key, initial=True)
        self.page.overlay.append(self.dlg)
        self.dlg.open = True
        self.page.update()


def main(page: ft.Page):
    NetShecanApp(page)


def _acquire_single_instance():
    import ctypes
    kernel32 = ctypes.windll.kernel32
    kernel32.CreateMutexW.restype = ctypes.c_void_p
    kernel32.CreateMutexW.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_wchar_p]
    handle = kernel32.CreateMutexW(None, False, "Local\\NetShecanApp")
    already = kernel32.GetLastError() == 183  # ERROR_ALREADY_EXISTS
    if already:
        kernel32.CloseHandle(handle)
        return None
    return handle


if __name__ == "__main__":
    _mutex = _acquire_single_instance()
    if _mutex is None:
        import ctypes
        ctypes.windll.user32.MessageBoxW(None, "NetShecan is already running.",
                                         "NetShecan", 0x40)
        sys.exit(0)
    ft.run(main, view=ft.AppView.FLET_APP_HIDDEN)