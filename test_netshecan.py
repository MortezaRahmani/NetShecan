import json
import unittest
from unittest.mock import Mock, call, patch
from urllib.error import URLError

import netshecan as app


class FlakyOpener:
    def __init__(self):
        self.calls = 0

    def open(self, req, timeout):
        self.calls += 1
        if self.calls < 3:
            cause = PermissionError(13, "permission denied", None, 10013)
            raise URLError(cause)
        return "ok"


class NetworkRetryTest(unittest.TestCase):
    def test_retries_wrapped_winerror_with_backoff(self):
        opener = FlakyOpener()
        with patch.object(app, "_direct_opener", opener), \
             patch.object(app.time, "sleep") as sleep:
            self.assertEqual(app._open(object(), timeout=1), "ok")

        self.assertEqual(opener.calls, 3)
        self.assertEqual(sleep.call_args_list, [call(1.0), call(2.0)])


class RefreshIsolationTest(unittest.TestCase):
    def test_provider_refresh_runs_while_shecan_is_checking(self):
        ui = object.__new__(app.NetShecanApp)
        ui.busy = False
        ui._shecan_busy = True
        ui.cfg = {"provider": "mci"}
        ui.status_text = Mock()
        ui.page = Mock()

        with patch.object(app.threading, "Thread") as thread:
            ui.refresh()

        self.assertTrue(ui.busy)
        thread.return_value.start.assert_called_once_with()

    def test_auto_refresh_schedules_shecan_after_provider_data(self):
        ui = object.__new__(app.NetShecanApp)
        ui._fetch = Mock(return_value={"provider_name": "MCI"})
        ui._run_on_ui = Mock()

        ui._work({"provider": "mci"}, with_shecan=True)

        self.assertEqual(ui._run_on_ui.call_args.args[1:],
                         ({"provider_name": "MCI"}, None, True))


class AlertRestoreTest(unittest.TestCase):
    def test_alert_restores_without_foregrounding(self):
        ui = object.__new__(app.NetShecanApp)
        ui._startup = False

        with patch.object(ui, "_restore_in_background") as restore, \
             patch.object(ui, "_bring_to_front") as foreground, \
             patch.object(app.threading, "Thread") as thread:
            ui._fire_alert("assets/audio/alert.mp3")

        restore.assert_called_once_with()
        foreground.assert_not_called()
        thread.return_value.start.assert_called_once_with()


class PrettyNameTest(unittest.TestCase):
    def test_english_days_name(self):
        self.assertEqual(app.pretty_name("30Days 20GB"), "30 Days - 20GB")

    def test_already_pretty_unchanged(self):
        self.assertEqual(app.pretty_name("30 Days - 20GB"), "30 Days - 20GB")


class IrancellDisplayNameTest(unittest.TestCase):
    def test_regular_package(self):
        self.assertEqual(app.irancell_display_name("30روزه 20گیگابایت", False),
                         "30 Days - 20GB")

    def test_gift_package_with_window(self):
        self.assertEqual(
            app.irancell_display_name("30روزه 100گیگابایت رایگان (2 تا 7 صبح)", True),
            "30 Days - 100GB (Free) (2 تا 7 صبح)")


class JwtHelpersTest(unittest.TestCase):
    def test_needs_refresh_on_garbage(self):
        self.assertTrue(app._needs_refresh("not-a-jwt"))

    def test_payload_decode(self):
        # header.payload.signature with an unexpired exp
        import base64, json, time
        payload = {"exp": int(time.time()) + 3600, "sub": "x"}
        b64 = base64.urlsafe_b64encode(json.dumps(payload).encode()).rstrip(b"=").decode()
        token = "a." + b64 + ".sig"
        self.assertFalse(app._needs_refresh(token))


class ParsePasteJsonTest(unittest.TestCase):
    def test_shatel(self):
        key, fields = app.parse_paste_json(json.dumps({
            "provider": "shatel", "refresh_token": "RT", "access_token": "AT",
            "client_id": "MyShatelB2cWeb",
        }))
        self.assertEqual(key, "shatel")
        self.assertEqual(fields["refresh_token"], "RT")
        self.assertEqual(fields["access_token"], "AT")
        self.assertEqual(fields["client_id"], "MyShatelB2cWeb")

    def test_excludes_unknown_and_preference_keys(self):
        key, fields = app.parse_paste_json(json.dumps({
            "provider": "shatel", "refresh_token": "RT",
            "include_additional_packages": True,   # preference: must be dropped
            "some_unknown": 1,
        }))
        self.assertEqual(key, "shatel")
        self.assertNotIn("include_additional_packages", fields)
        self.assertNotIn("some_unknown", fields)

    def test_unknown_provider_raises(self):
        with self.assertRaises(ValueError):
            app.parse_paste_json('{"provider": "nope", "refresh_token": "x"}')

    def test_not_json_raises(self):
        with self.assertRaises(json.JSONDecodeError):
            app.parse_paste_json("not json at all")

    def test_no_fields_raises(self):
        with self.assertRaises(ValueError):
            app.parse_paste_json('{"provider": "shatel"}')

    def test_mci(self):
        key, fields = app.parse_paste_json(json.dumps({
            "provider": "mci", "username": "9121112233",
            "refresh_token": "RT", "access_token": "AT",
            "version": "1.31.8", "platform": "WEB", "accept_language": "en-GB",
        }))
        self.assertEqual(key, "mci")
        self.assertEqual(fields["username"], "9121112233")

    def test_tci(self):
        key, fields = app.parse_paste_json(json.dumps({
            "provider": "tci", "refresh_token": "RT", "access_token": "AT",
        }))
        self.assertEqual(key, "tci")
        self.assertEqual(fields, {"refresh_token": "RT", "access_token": "AT"})


class ShatelProviderTest(unittest.TestCase):
    def test_package_name(self):
        prov = app.ShatelProvider(None)
        self.assertEqual(
            prov._pkg_name({"type": "Base", "totalKb": 15728640}, 30, True, "2026-09-16"),
            "30 Days - 15GB")
        self.assertEqual(
            prov._pkg_name({"type": "Timebound", "totalKb": 10485760}, 360, False, "2027-08-13"),
            "360 Days - 10GB Timebound (Exp: 2027-08-13)")

    def _payload(self):
        return {"result": {
            "remainingKb": 22782285, "totalKb": 26214400,
            "packages": [
                {"type": "Base", "name": "بسته پایه 15GB", "remainingKb": 13095251,
                 "totalKb": 15728640, "expirationDate": "2026-09-16T00:00:00", "inUse": True},
                {"type": "Timebound", "name": "بسته 10GB روزانه", "remainingKb": 9687034,
                 "totalKb": 10485760, "expirationDate": "2027-08-13T09:45:24+03:30", "inUse": False},
            ]}}, {"result": {"durationInMonths": 1, "name": "FairLite-16384-FG-1"}}

    def test_fetch_includes_additional(self):
        prov = app.ShatelProvider(None)
        cfg = {"access_token": "x", "client_id": "MyShatelB2cWeb",
               "refresh_token": "rt", "include_additional_packages": True}
        with patch.object(prov, "_get", side_effect=self._payload()), \
             patch.object(prov, "ensure_token"):
            data = prov.fetch(cfg)
        self.assertEqual(round(data["aggregate_remaining_mb"]), 22248)
        self.assertEqual(round(data["aggregate_total_mb"]), 25600)

    def test_fetch_excludes_additional(self):
        prov = app.ShatelProvider(None)
        cfg = {"access_token": "x", "client_id": "MyShatelB2cWeb",
               "refresh_token": "rt", "include_additional_packages": False}
        with patch.object(prov, "_get", side_effect=self._payload()), \
             patch.object(prov, "ensure_token"):
            data = prov.fetch(cfg)
        self.assertEqual(round(data["aggregate_remaining_mb"]), 12788)
        self.assertEqual(round(data["aggregate_total_mb"]), 15360)


class MciProviderTest(unittest.TestCase):
    def test_fetch_normalizes(self):
        prov = app.MciProvider(None)
        cfg = {"access_token": "x", "refresh_token": "rt", "username": "9121112233"}
        payload = {"packageItems": [
            {"type": "internet", "offerName": "بسته اینترنت یکماهه 10گیگابایت",
             "totalInitValue": 10.06, "totalUnusedValue": 5.33,
             "expireTime": "2026-09-11T18:22:44", "packageStatus": "active"},
        ], "totalInitBytes": 10.06, "totalUnusedBytes": 5.33}

        with patch.object(prov, "_get", return_value=payload), \
             patch.object(prov, "ensure_token"):
            data = prov.fetch(cfg)

        self.assertEqual(data["provider_name"], "MCI")
        self.assertEqual(data["main_index"], 0)
        self.assertEqual(len(data["active_offers"]), 1)
        main = data["active_offers"][0]
        self.assertEqual(main["expiry_date"], "2026-09-11")
        self.assertAlmostEqual(main["global_data_remaining"], 5.33 * 1024)
        self.assertAlmostEqual(main["total_amount"], 10.06 * 1024)
        self.assertAlmostEqual(data["aggregate_remaining_mb"], 5.33 * 1024)
        self.assertAlmostEqual(data["aggregate_total_mb"], 10.06 * 1024)

    def test_fetch_mixed_units(self):
        # remaining below 1 GB is reported in MB (unUsedUnitName "مگ") while
        # the total stays in GB (totalUnitName "گیگ")
        prov = app.MciProvider(None)
        cfg = {"access_token": "x", "refresh_token": "rt", "username": "9121112233"}
        payload = {"packageItems": [
            {"type": "internet", "offerName": "بسته اینترنت 1روزه 2گیگابایت",
             "totalInitValue": 2.01, "totalUnitName": "گیگ",
             "totalUnusedValue": 228.05, "unUsedUnitName": "مگ",
             "expireTime": "2026-08-25T13:22:06"},
        ], "totalInitBytes": 2.01, "bytesInitUnit": "گیگ",
           "totalUnusedBytes": 228.05, "bytesUnusedUnit": "مگ"}

        with patch.object(prov, "_get", return_value=payload), \
             patch.object(prov, "ensure_token"):
            data = prov.fetch(cfg)

        main = data["active_offers"][0]
        self.assertAlmostEqual(main["global_data_remaining"], 228.05)
        self.assertAlmostEqual(main["total_amount"], 2.01 * 1024)
        self.assertAlmostEqual(data["aggregate_remaining_mb"], 228.05)
        self.assertAlmostEqual(data["aggregate_total_mb"], 2.01 * 1024)
        self.assertTrue(data["aggregate_remaining_mb"] < data["aggregate_total_mb"])

    def test_reserved_package_does_not_replace_active_package(self):
        prov = app.MciProvider(None)
        cfg = {"access_token": "x", "refresh_token": "rt", "username": "9121112233"}
        payload = {"packageItems": [
            {"type": "internet", "packageStatus": "reserve",
             "totalInitValue": 80, "totalUnusedValue": 80,
             "totalUnitName": "گیگ", "unUsedUnitName": "گیگ"},
            {"type": "internet", "packageStatus": "active",
             "totalInitValue": 80.06, "totalUnusedValue": 6.5,
             "totalUnitName": "گیگ", "unUsedUnitName": "گیگ",
             "expireTime": "2026-11-22T13:49:44"},
        ]}

        with patch.object(prov, "_get", return_value=payload), \
             patch.object(prov, "ensure_token"):
            data = prov.fetch(cfg)

        self.assertEqual(data["main_index"], 1)
        self.assertAlmostEqual(data["aggregate_remaining_mb"], 6.5 * 1024)

    def test_aggregate_respects_unit_fields(self):
        prov = app.MciProvider(None)
        cfg = {"access_token": "x", "refresh_token": "rt", "username": "9121112233",
               "include_additional_packages": True}
        payload = {"packageItems": [
            {"type": "internet", "totalInitValue": 2.01, "totalUnitName": "گیگ",
             "totalUnusedValue": 228.05, "unUsedUnitName": "مگ",
             "expireTime": "2026-08-25T13:22:06"},
            {"type": "internet", "totalInitValue": 80.06, "totalUnitName": "گیگ",
             "totalUnusedValue": 80.06, "unUsedUnitName": "گیگ",
             "expireTime": "2026-11-22T13:49:44"},
        ], "totalInitBytes": 82.07, "bytesInitUnit": "گیگ",
           "totalUnusedBytes": 80.28, "bytesUnusedUnit": "گیگ"}

        with patch.object(prov, "_get", return_value=payload), \
             patch.object(prov, "ensure_token"):
            data = prov.fetch(cfg)

        self.assertAlmostEqual(data["aggregate_remaining_mb"], 80.28 * 1024)
        self.assertAlmostEqual(data["aggregate_total_mb"], 82.07 * 1024)

    def test_aggregate_uses_aggregate_when_included(self):
        prov = app.MciProvider(None)
        cfg = {"access_token": "x", "refresh_token": "rt", "username": "9121112233",
               "include_additional_packages": True}
        payload = {"packageItems": [
            {"type": "internet", "totalInitValue": 10.06, "totalUnusedValue": 5.33,
             "expireTime": "2026-09-11T18:22:44"},
        ], "totalInitBytes": 20.0, "totalUnusedBytes": 8.0}
        with patch.object(prov, "_get", return_value=payload), \
             patch.object(prov, "ensure_token"):
            data = prov.fetch(cfg)
        self.assertAlmostEqual(data["aggregate_remaining_mb"], 8.0 * 1024)
        self.assertAlmostEqual(data["aggregate_total_mb"], 20.0 * 1024)

    def test_offer_name_days(self):
        from datetime import datetime, timedelta
        prov = app.MciProvider(None)
        exp = datetime.now() + timedelta(days=24, hours=1)
        expected_days = (exp - datetime.now()).days
        self.assertEqual(prov._offer_name({"expireTime": exp.isoformat()}, 10.06),
                         f"{expected_days} Days - 10GB")


class TciProviderTest(unittest.TestCase):
    def test_converts_jalali_expiry(self):
        self.assertEqual(app._tci_expiry("1405-06-07T12:00:00").date().isoformat(), "2026-08-29")
        self.assertEqual(app._tci_expiry("1405/06/07 12:00:00").date().isoformat(), "2026-08-29")
        self.assertEqual(
            app._tci_expiry("۱۴۰۵/۰۶/۰۷ ساعت ۱۲:۰۰").date().isoformat(),
            "2026-08-29",
        )
        self.assertEqual(app._tci_expiry("14050718T200941").date().isoformat(), "2026-10-10")

    def test_fetches_active_adsl_quota(self):
        prov = app.TciProvider(None)
        cfg = {"access_token": "AT", "refresh_token": "RT"}
        services = {"data": [
            {"tel_number": "02100000000", "active_adsl_service": True},
        ]}
        detail = {"acc_info": {
            "credit": 5474,
            "expireDateTime": "1405-07-08T00:00:00",
            "activeService": {"serviceType": "TCI ADSL", "baseTraffic": 261120},
        }}
        with patch.object(prov, "ensure_token"), \
             patch.object(prov, "_get", side_effect=[services, detail]):
            data = prov.fetch(cfg)
        self.assertEqual(data["provider_name"], "TCI")
        self.assertEqual(data["aggregate_remaining_mb"], 5474)
        self.assertEqual(data["aggregate_total_mb"], 261120)
        self.assertRegex(data["active_offers"][0]["name"], r"^\d+ Days - 255GB$")
        self.assertEqual(data["active_offers"][0]["expiry_date"], "2026-09-30")

    def test_clamps_negative_credit_and_includes_extra_traffic(self):
        prov = app.TciProvider(None)
        cfg = {"access_token": "AT", "refresh_token": "RT"}
        services = {"data": [{"tel_number": "02100000000", "active_adsl_service": True}]}
        detail = {"acc_info": {
            "credit": -20, "deposit": 29030.08, "expireDateTime": "14050718T200941",
            "activeService": {"baseTraffic": 261120},
        }}
        with patch.object(prov, "ensure_token"), \
             patch.object(prov, "_get", side_effect=[services, detail]):
            data = prov.fetch(cfg)

        self.assertEqual(data["active_offers"][0]["global_data_remaining"], 0)
        self.assertEqual(data["active_offers"][1]["name"], "Extra Traffic - 28GB")
        self.assertAlmostEqual(data["aggregate_remaining_mb"], 29030.08)
        self.assertAlmostEqual(data["aggregate_total_mb"], 261120 + 29030.08)

    def test_refreshes_tokens(self):
        class FakeApp:
            def __init__(self):
                self.saved = False

            def _save_config(self):
                self.saved = True

        owner = FakeApp()
        prov = app.TciProvider(owner)
        cfg = {"access_token": "expired", "refresh_token": "RT"}
        with patch.object(app, "_open", return_value=FakeResponse({
            "access_token": "NEW_AT", "refresh_token": "NEW_RT",
        })):
            prov.ensure_token(cfg)
        self.assertEqual(cfg, {"access_token": "NEW_AT", "refresh_token": "NEW_RT"})
        self.assertTrue(owner.saved)


class FakeResponse:
    def __init__(self, data):
        self._data = data
    def __enter__(self):
        return self
    def __exit__(self, *a):
        return False
    def __call__(self, *a, **k):
        return self
    def read(self, *a, **k):
        return json.dumps(self._data).encode()


class IrancellAggregateTest(unittest.TestCase):
    def _payload(self):
        return {"active_offers": [
            {"name": "30روزه 20گیگابایت", "is_gift": False,
             "global_data_remaining": 13346.1, "total_amount": 20480},
            {"name": "30روزه 100گیگابایت رایگان", "is_gift": True,
             "global_data_remaining": 92346.06, "total_amount": 102400},
        ], "cumulative_amounts": [{"type": "data", "total": 122880, "remained": 105692.16}]}

    def test_excludes_gift_by_default(self):
        prov = app.IrancellProvider(None)
        cfg = {"authorization": "x", "include_additional_packages": False}
        with patch.object(prov, "ensure_token"), \
             patch.object(app, "_open", return_value=FakeResponse(self._payload())):
            data = prov.fetch(cfg)
        self.assertEqual(round(data["aggregate_remaining_mb"]), 13346)
        self.assertEqual(round(data["aggregate_total_mb"]), 20480)
        # main is the non-gift 20GB offer
        self.assertEqual(data["active_offers"][data["main_index"]]["total_amount"], 20480)

    def test_includes_additional_when_set(self):
        prov = app.IrancellProvider(None)
        cfg = {"authorization": "x", "include_additional_packages": True}
        with patch.object(prov, "ensure_token"), \
             patch.object(app, "_open", return_value=FakeResponse(self._payload())):
            data = prov.fetch(cfg)
        self.assertEqual(round(data["aggregate_remaining_mb"]), 105692)
        self.assertEqual(round(data["aggregate_total_mb"]), 122880)


if __name__ == "__main__":
    unittest.main()
