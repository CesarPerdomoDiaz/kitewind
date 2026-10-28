# Kite Wind

**Can we fly the kite today?** Kite Wind answers that in one look. It is a go/no-go wind
forecast for an airborne wind energy (AWES) test site: for every hour of the next three days it
says **GO**, **MARGINAL** or **NO-GO**, and why.

### 👉 **[Open the forecast](https://cesarperdomodiaz.github.io/kitewind/)**

It works on any phone, tablet or computer, in English or Spanish (flags in the top-right corner).
To keep it on your phone like an app:

- **iPhone:** open the link in Safari, tap **Share**, then **Add to Home Screen**.
- **Android:** open the link in Chrome, tap **⋮**, then **Install app** (or **Add to Home screen**).

The page shows when the forecast was last updated, and warns you in red if it is getting old.

## How the prediction is made

**1. Many forecasts, not just one.** Every 30 minutes the app downloads the latest runs of seven
weather models for the site, from the very detailed (AROME HD, a 1.3 km grid) to the global
ones (ECMWF, GFS, and others). Every hour it also downloads four *ensembles*: the same model run
dozens of times with slightly different starting conditions, 140 runs in total. Where the
ensemble runs agree, the forecast is reliable; where they spread out, it is uncertain.

**2. The wind where the kite flies, not on the ground.** Weather apps usually give the wind at
10 m. The kite works between 10 and 150 m, where the wind is usually stronger. The models give
the wind at several heights (10, 20, 50, 80, 100 m and more); the app fills in the heights in
between and averages the whole 10–150 m band. A model that only gives the 10 m wind borrows the
way wind grows with height from the best model that has it.

**3. The best model decides; the others can only make it more careful.** For each hour, the most
detailed model that covers it decides. Then:

| | NO-GO | MARGINAL |
|---|---|---|
| **Wind** (mean over 10–150 m) | below 5 m/s, or too strong for the kite | less than a 50 % chance (from the ensembles) of reaching 5 m/s, or the main models disagree |
| **Rain** | any real rain (0.05 mm/h or more), snow or ice | a 20 % chance of rain, or another model shows rain |
| **Thunderstorms** | forecast by the best model, or by 20 % of the ensemble runs | another model or a few runs show one; very unstable air |
| **Fog** | visibility below 100 m | – |

Night hours are shown but never GO. Gusts are not used: the system is rated on mean wind.

**4. One honest picture.** The page shows the verdict for now, the next flyable window, the
chance of enough wind for every hour, the likely range of the wind (the middle 80 % of the
ensemble runs), the wind at each height, and the chances of rain and of a thunderstorm. Hover or
tap any hour to see every model's value.

## Alerts on your phone

The team follows a Telegram channel where only the Kite Wind bot posts. As soon as a forecast
shows a flyable window (at least 2 GO hours in a row in the next three days) it posts the day,
the hours, the expected wind and its chance, in Spanish and English. It does not repeat itself:
you only hear about that window again if it moves or changes length by 2 hours or more, if two
forecasts in a row drop it (with the reason), and on the morning of the day.

## How it runs

One always-on computer runs the app (plain Python, no extra packages). It downloads the
forecasts, makes the decision for every hour, stores everything in a small database, and every
10 minutes publishes the result to the `data` branch of this repository. GitHub Pages serves the
page in `kitewind/web`, which reads that file. Viewers need nothing but a browser.

## Make your own

You need Python 3.11 or newer and nothing else.

```
python -m kitewind --demo                    # made-up forecasts, try the page on port 8750
python -m kitewind                           # real forecasts on http://127.0.0.1:8740
python -m unittest discover -s tests
```

1. **Your site and limits.** `config.toml` has an example site and every limit, with comments.
   Put your own values in `data/config.local.toml` (same sections, only what you change); that
   file stays on your computer.
2. **Forecast sources.** [Open-Meteo](https://open-meteo.com/) needs no key (free for
   non-commercial use, CC BY 4.0). The [Windy Point Forecast API](https://api.windy.com/point-forecast/docs)
   is optional and needs your own key, given to the app as an environment variable
   (see `kitewind/config.py`). Windy's free plan returns deliberately shuffled data, so the app
   shows it but does not decide with it.
3. **Keep it running.** Start `python -m kitewind` as a service on any always-on computer: a
   systemd unit on Linux, a scheduled task on Windows, a launchd agent on macOS. Give it its own
   data folder with `--data`.
4. **Publish it (optional).** Fork this repository, enable GitHub Pages with "GitHub Actions" as
   the source, and add a deploy key with write access (Settings → Deploy keys). Save the private
   half as `deploy_key` in the app's data folder, readable only by the account that runs the
   app, and set `repo` under `[publish]` in your `config.local.toml`. Publishing starts by itself.
5. **Alerts (optional).** Create a bot with Telegram's @BotFather and a channel, and add the bot
   to the channel as an administrator allowed to post. Give the app the bot token as the
   `TELEGRAM_BOT_TOKEN` environment variable, run `python -m kitewind --telegram-chats` to find
   the channel's id, put it as `chat_id` under `[notify]` in `config.local.toml`, and check with
   `python -m kitewind --telegram-test`.

## Credits

Weather data from [Open-Meteo](https://open-meteo.com/) (Météo-France, DWD, ECMWF, NOAA, UK Met
Office models) and the Windy Point Forecast API.

---

### En español

**Kite Wind** dice de un vistazo si se puede volar la cometa: para cada hora de los próximos tres
días indica **APTO**, **MARGINAL** o **NO APTO**, y por qué. **[Abrir el pronóstico](https://cesarperdomodiaz.github.io/kitewind/)**
(cambia el idioma con las banderas). En iPhone: Safari → Compartir → **Añadir a pantalla de
inicio**. Combina siete modelos meteorológicos y 140 ejecuciones de conjuntos (ensembles), calcula
el viento medio entre 10 y 150 m, y aplica los límites: viento medio de al menos 5 m/s, nada de
lluvia, nada de tormentas y niebla solo si la visibilidad baja de 100 m. Un canal de Telegram
avisa en cuanto aparece una ventana apta de al menos 2 horas.
