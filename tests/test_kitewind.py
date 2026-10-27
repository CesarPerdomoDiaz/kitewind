import tempfile
import unittest
from pathlib import Path

from kitewind import config, forecast, openmeteo, publish, windy
from kitewind.scheduler import Scheduler
from kitewind.store import Store

ROOT = Path(__file__).resolve().parent.parent
HOUR = 3_600_000
NOW = 1_793_095_200_000  # 2026-10-27 10:00 UTC


def load_cfg(tmp: str, demo=True):
    return config.load(ROOT / "config.toml", Path(tmp), demo=demo)


def body_with(u10, gh925=None, u925=None, units_precip="m", rain=0.0, cape=0.0, gust=5.0):
    b = {"ts": [NOW], "units": {"past3hprecip-surface": units_precip, "temp-surface": "K"},
         "wind_u-surface": [u10], "wind_v-surface": [0.0], "gust-surface": [gust],
         "past3hprecip-surface": [rain], "cape-surface": [cape], "temp-surface": [290.0],
         # 1000 hPa sits underground at 650 m: it must be ignored.
         "gh-1000h": [150.0], "wind_u-1000h": [50.0], "wind_v-1000h": [0.0]}
    if gh925 is not None:
        b.update({"gh-925h": [gh925], "wind_u-925h": [u925], "wind_v-925h": [0.0]})
    return b


class ProfileTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.cfg = load_cfg(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_underground_level_ignored_and_power_law_used(self):
        speeds, _ = forecast.wind_profile(body_with(-4.0), 0, 650, self.cfg.heights_m)
        self.assertAlmostEqual(speeds[0], 4.0)
        self.assertAlmostEqual(speeds[-1], 4.0 * 15 ** (1 / 7), places=6)

    def test_log_interpolation_to_level_above_ground(self):
        # 925 hPa at 830 m ASL = 180 m above ground, 10 m/s there.
        speeds, direction = forecast.wind_profile(body_with(-4.0, 830.0, -10.0), 0, 650, self.cfg.heights_m)
        self.assertAlmostEqual(speeds[0], 4.0)
        self.assertTrue(4.0 < speeds[3] < speeds[-1] < 10.0)
        self.assertAlmostEqual(direction, 90.0)  # u < 0: wind blows west, comes FROM the east

    def test_band_mean_weights_by_height(self):
        self.assertAlmostEqual(forecast.band_mean([1.0, 1.0], [10, 150]), 1.0)
        self.assertAlmostEqual(forecast.band_mean([0.0, 2.0, 2.0], [0, 10, 100]), (10 * 1 + 90 * 2) / 100)

    def test_precip_metres_to_mm_per_hour(self):
        m = forecast.windy_member("windy:arome", 0, body_with(-6.0, rain=0.003), self.cfg)
        self.assertAlmostEqual(m.series["rain"][0], 1.0)
        self.assertAlmostEqual(m.series["temp"][0], 290.0 - 273.15)


class DecisionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.lim = load_cfg(self.tmp.name).limits

    def tearDown(self):
        self.tmp.cleanup()

    def hour(self, **kw):
        h = dict(wind=7.0, wind_top=8.0, wind_agree=1.0, n_members=5, wind_prob=0.9,
                 rain=0.0, rain_hi=0.0, rain_prob=0.0, cape=50.0, storm=False, storm_other=False,
                 storm_prob=0.0, vis=20000, frozen=False)
        h.update(kw)
        return h

    def test_go(self):
        status, checks, reasons = forecast.decide(self.hour(), self.lim)
        self.assertEqual(status, "go")
        self.assertEqual(reasons, [])
        self.assertNotIn("gusts", checks)

    def test_low_wind_is_nogo(self):
        status, checks, reasons = forecast.decide(self.hour(wind=4.9), self.lim)
        self.assertEqual(status, "nogo")
        self.assertEqual(checks["wind"], "nogo")
        self.assertIn("Not enough wind", reasons[0]["text"])

    def test_low_ensemble_chance_is_caution(self):
        status, _, reasons = forecast.decide(self.hour(wind=5.5, wind_prob=0.3), self.lim)
        self.assertEqual(status, "caution")
        self.assertIn("only 30%", reasons[0]["text"])

    def test_disagreement_is_caution(self):
        status, _, reasons = forecast.decide(self.hour(wind=5.5, wind_prob=None, wind_agree=0.4), self.lim)
        self.assertEqual(status, "caution")
        self.assertIn("only 2 of 5", reasons[0]["text"])

    def test_any_rain_is_nogo(self):
        self.assertEqual(forecast.decide(self.hour(rain=0.06), self.lim)[0], "nogo")
        self.assertEqual(forecast.decide(self.hour(rain=0.01), self.lim)[0], "go")  # trace amounts
        self.assertEqual(forecast.decide(self.hour(rain_prob=0.3), self.lim)[0], "caution")
        self.assertEqual(forecast.decide(self.hour(frozen=True), self.lim)[0], "nogo")

    def test_storm_is_nogo(self):
        self.assertEqual(forecast.decide(self.hour(storm=True), self.lim)[0], "nogo")
        self.assertEqual(forecast.decide(self.hour(storm_prob=0.25), self.lim)[0], "nogo")
        self.assertEqual(forecast.decide(self.hour(storm_other=True), self.lim)[0], "caution")

    def test_only_thick_fog_matters(self):
        self.assertEqual(forecast.decide(self.hour(vis=500), self.lim)[0], "go")
        self.assertEqual(forecast.decide(self.hour(vis=80), self.lim)[0], "nogo")

    def test_missing_fields_do_not_block(self):
        h = self.hour(cape=None, vis=None, wind_prob=None, rain=None, rain_prob=None, storm_prob=None)
        self.assertEqual(forecast.decide(h, self.lim)[0], "go")

    def test_storm_from_weather_code(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            cfg = load_cfg(tmp)
            run = {"wind_speed_10m": [6.0, 6.0], "weather_code": [95, 95], "cape": [100, 100], "precipitation": [0, 0]}
            m = forecast.om_member("om:x", 0, run, [NOW, NOW + HOUR], cfg, "det")
            self.assertTrue(forecast._storm(m, NOW, cfg.limits))


class SunTests(unittest.TestCase):
    def test_day_and_night_in_madrid(self):
        noon = 1_793_102_400_000      # 12:00 UTC
        midnight = 1_793_059_200_000  # 00:00 UTC
        self.assertGreater(forecast.sun_elevation(noon, 40.42, -3.7), 30)
        self.assertLess(forecast.sun_elevation(midnight, 40.42, -3.7), -30)


class BudgetTests(unittest.TestCase):
    def test_plan_fits_testing_key(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            cfg = load_cfg(tmp)
            self.assertEqual(cfg.planned_requests_per_day, 456)
            self.assertLessEqual(cfg.planned_requests_per_day, cfg.daily_cap)
            self.assertLess(cfg.daily_cap, 500)

    def test_cap_stops_fetching(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            cfg = load_cfg(tmp)
            cfg.daily_cap = 3
            store = Store(Path(tmp) / "t.sqlite")
            sched = Scheduler(cfg, store)
            for j in sched.jobs:
                j.due_ms = 0
            sched.tick()
            self.assertEqual(sched.used_today(), 3)
            sched.tick()
            self.assertEqual(sched.used_today(), 3)
            store.close()

    def test_demo_snapshot_end_to_end(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            cfg = load_cfg(tmp)
            store = Store(Path(tmp) / "t.sqlite")
            sched = Scheduler(cfg, store)
            for j in sched.jobs:
                j.due_ms = 0
            sched.tick()
            sched.rebuild()
            snap = sched.snapshot
            self.assertEqual(len(snap["hours"]), cfg.horizon_hours)
            h = snap["hours"][0]
            self.assertEqual(h["primary"], "om:meteofrance_arome_france_hd")
            self.assertGreaterEqual(h["n_members"], 12)
            self.assertEqual(h["band"], "ensemble")
            self.assertEqual(h["n_runs"], 51 + 40 + 31 + 18)
            self.assertEqual(len(h["profile"]), len(cfg.heights_m))
            self.assertTrue(all(m["fetched"] for m in snap["models"]))
            store.close()


class LevelHeightTests(unittest.TestCase):
    def std(self, ps):
        # Standard atmosphere: 2 m temperature at 650 m, and at 925 hPa (about 762 m).
        return {"pressure-surface": [ps], "temp-surface": [288.15 - 0.0065 * 650],
                "temp-925h": [288.15 - 0.0065 * 762]}

    def test_sea_level_pressure(self):
        z = forecast.level_height(self.std(101325.0), 0, "925h", 650)
        self.assertAlmostEqual(z, 762, delta=10)

    def test_station_pressure(self):
        ps = 101325.0 * (1 - 0.0065 * 650 / 288.15) ** 5.2559  # about 94 kPa at 650 m
        z = forecast.level_height(self.std(ps), 0, "925h", 650)
        self.assertAlmostEqual(z, 762, delta=10)

    def test_icon_profile_uses_levels_without_gh(self):
        b = windy.fake_fetch("icon", 40.42, -3.7, NOW)
        self.assertNotIn("gh-925h", b)
        speeds, _ = forecast.wind_profile(b, 0, 650, [10, 150])
        s10 = (b["wind_u-surface"][0] ** 2 + b["wind_v-surface"][0] ** 2) ** 0.5
        self.assertNotAlmostEqual(speeds[1], s10 * 15 ** (1 / 7), places=3)  # not the fallback law


class ShearTests(unittest.TestCase):
    def test_exponent_from_profile(self):
        heights = [10, 150]
        self.assertAlmostEqual(forecast.shear_exponent([4.0, 4.0 * 15 ** 0.2], heights), 0.2)
        self.assertAlmostEqual(forecast.shear_exponent([0.2, 3.0], heights), forecast.DEFAULT_ALPHA)  # calm
        self.assertEqual(forecast.shear_exponent([5.0, 2.0], heights), 0.0)  # clipped

    def test_arome_borrows_shear_from_icon_eu(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            cfg = load_cfg(tmp)
            latest = {(m.id, p): (NOW, windy.fake_fetch(m.name, lat, lon, NOW))
                      for m in cfg.models for p, (lat, lon) in enumerate(m.points(cfg.site))}
            snap = forecast.build(cfg, latest, NOW)
            arome = next(m for m in snap["models"] if m["id"] == "windy:arome")
            self.assertIn("ICON-EU", arome["heights_from"])
            h = next(h for h in snap["hours"] if h.get("primary") == "windy:arome" and h["profile"][0] > 1)
            donor = forecast.windy_member("windy:iconEu", 0, latest[("windy:iconEu", 0)][1], cfg)
            j = donor.ts.index(h["t"])
            expected = forecast.shear_exponent(donor.profile[j], cfg.heights_m)
            got = forecast.shear_exponent(h["profile"], cfg.heights_m)
            self.assertAlmostEqual(got, expected, places=2)


class OpenMeteoTests(unittest.TestCase):
    def test_runs_split_members(self):
        b = openmeteo.fake_fetch("gfs025", True, NOW)
        runs = openmeteo.runs(b)
        self.assertEqual(len(runs), 31)
        self.assertIn("wind_speed_10m", runs[0])
        self.assertIn("wind_speed_80m", runs[30])

    def test_member_uses_measured_heights(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            cfg = load_cfg(tmp)
            run = {"wind_speed_10m": [4.0], "wind_speed_50m": [6.0], "wind_speed_100m": [7.0]}
            m = forecast.om_member("om:x", 0, run, [NOW], cfg, "det")
            self.assertTrue(m.levels)
            self.assertAlmostEqual(m.profile[0][0], 4.0)
            self.assertAlmostEqual(m.profile[0][2], 6.0)          # 50 m measured
            self.assertAlmostEqual(m.profile[0][4], 7.0)          # 100 m measured
            self.assertGreater(m.profile[0][-1], 7.0)             # 150 m extended upward

    def test_weighted_quantiles(self):
        pairs = [(1.0, 0.5), (2.0, 0.25), (3.0, 0.25)]
        self.assertEqual(forecast._quantiles(pairs, [0.1, 0.5, 0.9]), [1.0, 1.0, 3.0])

    def test_windy_testing_data_does_not_decide(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            cfg = load_cfg(tmp, demo=False)
            cfg.api_key, cfg.om_models, cfg.ensembles = "k" * 20, [], []
            latest = {(m.id, 0): (NOW, windy.fake_fetch(m.name, cfg.site.lat, cfg.site.lon, NOW)) for m in cfg.models}
            snap = forecast.build(cfg, latest, NOW)
            self.assertEqual(snap["key_mode"], "testing")
            self.assertFalse(snap["windy_used"])
            self.assertTrue(all(h["status"] == "nodata" for h in snap["hours"]))
            cfg.use_windy_testing = True
            snap = forecast.build(cfg, latest, NOW)
            self.assertTrue(any(h["status"] != "nodata" for h in snap["hours"]))

    def test_budget_counts_windy_only(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            store = Store(Path(tmp) / "t.sqlite")
            store.add(NOW, "arome", 40.42, -3.7, 200, body={})
            store.add(NOW, "om:icon_eu", 40.42, -3.7, 200, body={})
            store.add(NOW, "ens:gfs025", 40.42, -3.7, 200, body={})
            self.assertEqual(store.count_since(0, windy_only=True), 1)
            self.assertEqual(store.count_since(0), 3)
            self.assertEqual(store.drop_bodies("ens:%", NOW + 1), 1)
            store.close()


class PublishTests(unittest.TestCase):
    def snap(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            cfg = load_cfg(tmp)
            latest = {(m.id, 0): (NOW, windy.fake_fetch(m.name, cfg.site.lat, cfg.site.lon, NOW)) for m in cfg.models}
            latest[("om:meteofrance_arome_france_hd", 0)] = (NOW, openmeteo.fake_fetch("meteofrance_arome_france_hd", False, NOW))
            snap = forecast.build(cfg, latest, NOW)
            snap["budget"] = {"used_today": 5}
            return snap

    def test_public_copy_has_no_windy_and_no_budget(self):
        pub = publish.public_snapshot(self.snap(), include_windy=False)
        self.assertEqual(pub["site"], {"name": "Test site"})
        self.assertNotIn("budget", pub)
        self.assertFalse(any(m["source"] == "Windy" for m in pub["models"]))
        self.assertFalse(any("Windy" in name for h in pub["hours"] for name, _ in h.get("models", [])))

    def test_inactive_without_deploy_key(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            p = publish.Publisher(Path(tmp), {"repo": "git@github.com:x/y.git"})
            self.assertFalse(p.active)
            (Path(tmp) / "deploy_key").write_text("k")
            self.assertTrue(publish.Publisher(Path(tmp), {"repo": "git@github.com:x/y.git"}).active)
            self.assertFalse(publish.Publisher(Path(tmp), {"repo": "git@github.com:x/y.git"}, demo=True).active)

    def test_push_replaces_the_single_commit(self):
        import subprocess
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            bare = Path(tmp) / "remote.git"
            subprocess.run(["git", "init", "-q", "--bare", str(bare)], check=True)
            p = publish.Publisher(Path(tmp), {"repo": str(bare), "enabled": True})
            snap = self.snap()
            self.assertTrue(p.publish(snap, NOW))
            self.assertTrue(p.publish(snap, NOW + HOUR))
            log = subprocess.run(["git", "-C", str(bare), "log", "--oneline", "data"],
                                 capture_output=True, text=True).stdout.strip().splitlines()
            self.assertEqual(len(log), 1)
            self.assertIn("11:00 UTC", log[0])


class ParameterTests(unittest.TestCase):
    def test_lists_match_windy(self):
        self.assertNotIn("gh", windy.parameters_for("arome"))
        self.assertIn("gh", windy.parameters_for("gfs"))
        self.assertIn("visibility", windy.parameters_for("iconEu"))

    def test_narrows_list_after_windy_error(self):
        orig_post, orig = windy._post, dict(windy.MODEL_PARAMETERS)

        def fail(*a, **k):
            raise windy.WindyError(400, "Allowed parameters for model 'gfs': [temp, wind, pressure]")
        windy._post = fail
        try:
            with self.assertRaises(windy.WindyError):
                windy.fetch("k", "gfs", 40.42, -3.7)
            self.assertEqual(windy.parameters_for("gfs"), ["wind", "temp", "pressure"])
        finally:
            windy._post = orig_post
            windy.MODEL_PARAMETERS.clear()
            windy.MODEL_PARAMETERS.update(orig)


class FakeShapeTests(unittest.TestCase):
    def test_fake_has_documented_keys(self):
        b = windy.fake_fetch("iconEu", 40.42, -3.7, NOW)
        for k in ["ts", "units", "wind_u-surface", "wind_v-surface", "gust-surface", "past3hprecip-surface",
                  "cape-surface", "ptype-surface", "gh-925h", "wind_u-925h"]:
            self.assertIn(k, b)


if __name__ == "__main__":
    unittest.main()
