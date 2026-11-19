"""Klevra-sillan vakiot. Yksi lähde, jotta __init__ ja config_flow eivät eriydy."""

DOMAIN = "klevra"
OLETUS_PALVELIN = "https://laskuposti.com"

# Kysely- ja tarkistusreitit palvelimella. Nämä vastaavat src/routes/auto/silta.js:ää
# ja test/silta-sopimus.mjs vahtii, että ne pysyvät samoina molemmissa päissä.
POLKU_HAVAINNOT = "/api/silta/havainnot"
POLKU_TILA = "/api/silta/tila"
OTSAKE = "X-Klevra-Silta"

# 🔴 PITKÄ KYSELY: kysely odottaa palvelimella, ja vastaus tulee heti kun komento
# saapuu. [Niko 13.11.2026: «lähettää aina signaalin ja kun vastaus tulee niin se
# tulee heti takaisin».] Jos palvelin ei tue odotusta, se vastaa heti ja väli
# putoaa takaisin OLETUS_VALIin — toiminta ei riko vanhaa palvelinta.
ODOTUS_S = 55
OLETUS_VALI = 5
VIRHE_KATTO_S = 300

# Attribuuttien katto per entiteetti. Ilman kattoa yksi kamera tai mediasoitin voi
# lähettää kymmeniä kilotavuja (soittolistat, lähdelistat) joka kierroksella.
ATTR_MERKKI_KATTO = 250
ATTR_MAARA_KATTO = 40

# 🔴 MITÄ LÄHETETÄÄN. Tyhjä suodatin = kaikki OHJATTAVAT ja MITTAAVAT domainit.
# Nämä valittiin siitä mitä Klevra Home osaa näyttää ja ohjata (src/services/alykoti.js
# TUETUT_DOMAINIT) — ei sen perusteella mitä Home Assistantissa sattuu olemaan.
KIINNOSTAVAT = (
    "sensor.",
    "binary_sensor.",
    "switch.",
    "light.",
    "climate.",
    "water_heater.",
    "number.",
    "select.",
    "button.",
    "fan.",
    "cover.",
    "lock.",
    "alarm_control_panel.",
    "camera.",
    "media_player.",
    # 📺 KAUKOSÄÄDIN [SALMI 18.11.2026, korjausehdotus #206]. Nikon Home
    # Assistantiin tekemä «Elisa Viihde» -kaukosäädin ei tullut Klevraan,
    # koska `remote.` puuttui tästä listasta. Mitattu, ei arvattu.
    "remote.",
    "input_boolean.",
)

# 🔴 KOMENTOJEN SALLITUT PALVELUT, domainkohtaisesti.
#
# Vanha lisäosa (ha-komponentti/) sallii neljä avainta: tila, tavoite_c, kirkkaus,
# arvo. Se tarkoittaa, että värin, lukon, hälyttimen, kaihtimen, puhaltimen
# nopeuden tai laturin virran säätäminen EI mene läpi lainkaan — ja juuri niitä
# Niko pyysi 13.11. klo 20.46 («mahdollisimman laaja rajapinta»).
#
# Tässä on laaja mutta EI vapaa lista. Vapaa `hass.services.async_call` olisi
# yhtä kuin etäkomentorivi kodissa: `shell_command`, `hassio.host_reboot` ja
# `persistent_notification` ovat kaikki palveluita. Lista on domainkohtainen,
# jotta väärä pari (esim. climate.set_temperature water_heater-entiteetille)
# ei pääse läpi — se vika oli palvelimen puolella 10.11.2026.
SALLITUT = {
    "light": {"turn_on", "turn_off", "toggle"},
    "switch": {"turn_on", "turn_off", "toggle"},
    "input_boolean": {"turn_on", "turn_off", "toggle"},
    "fan": {"turn_on", "turn_off", "toggle", "set_percentage", "oscillate", "set_preset_mode"},
    "climate": {"set_temperature", "set_hvac_mode", "set_preset_mode", "set_fan_mode", "turn_on", "turn_off"},
    "water_heater": {"set_temperature", "set_operation_mode", "turn_on", "turn_off"},
    "cover": {"open_cover", "close_cover", "stop_cover", "set_cover_position", "set_cover_tilt_position"},
    "lock": {"lock", "unlock"},
    "alarm_control_panel": {"alarm_arm_away", "alarm_arm_home", "alarm_arm_night", "alarm_disarm"},
    "number": {"set_value"},
    "select": {"select_option"},
    "button": {"press"},
    "media_player": {
        "turn_on", "turn_off", "volume_up", "volume_down", "volume_set", "volume_mute",
        "media_play", "media_pause", "media_play_pause", "media_stop",
        "media_next_track", "media_previous_track", "select_source",
    },
    # Kaukosäädin: Elisa Viihde, Apple TV, Android TV. Vain napin painallus.
    "remote": {"send_command", "turn_on", "turn_off"},
}

# 🔴 Nämä kenttänimet EI koskaan välitetä palvelukutsuun, vaikka palvelin ne
# lähettäisi. `entity_id` asetetaan aina paikallisesti komennon entiteetistä,
# jottei yksi komento voi osua koko kotiin (`entity_id: all`).
KIELLETYT_KENTAT = {"entity_id", "device_id", "area_id", "label_id"}

# ═══════════════════════════════════════════════════════════════════════════
# 🛠 HALLINTAKANAVA [RANTA 17.11.2026, Niko klo 14.28]
#
# «päästään siirtämään kaikki laitteet pois homey ja siirtämään ne home
#  assistant kautta ohjattavaksi» — ilman että käyttäjä menee Home
# Assistantin omaan käyttöliittymään.
#
# 🔴 ERI KANAVA KUIN LAITEKOMENNOT. `SALLITUT` yllä on laitteen ohjausta
# (valo päälle). Tämä on Home Assistantin RAKENTEEN muuttamista (integraatio
# lisätään, laite nimetään). Ne pidetään erillään, jotta laitekomennon
# sallittu lista ei koskaan laajene «hallinnan nimissä».
#
# 🔴 NIMETYT TOIMINNOT, EI VAPAATA KUTSUA. Jokainen alla oleva on yksi
# Python-funktio `hallinta.py`:ssa. Palvelin ei voi pyytää mitään muuta.
HALLINTA_VERSIO = 3
LISAOSA_VERSIO = "1.6.0"
HALLINTA_TOIMINNOT = (
    "tila",            # HA-versio, integraatiot, Zigbee/Matter valmiina?
    "loydetyt",        # HA:n itse löytämät laitteet (zeroconf, dhcp, ssdp…)
    "flow_aloita",     # aloita integraation lisäys (vain KATALOGI)
    "flow_jatka",      # lähetä vaiheen kentät / valitse valikosta
    "flow_peru",       # keskeytä lisäys
    "zigbee_liitos",   # avaa Zigbee-pariliitos (zha.permit) 30–254 s
    "matter_koodi",    # lisää Matter-laite jakokoodilla (esim. Homeysta)
    "laitteet",        # laiterekisteri + alueet
    "nimea_laite",     # laitteen nimi ja huone
    # ── 1.3.0 (HALLINTA_VERSIO 2): vaiheet 2 ja 3 ──
    "hacs_tila",       # HACS asennettu? luettelon repojen tila
    "hacs_lataa",      # lataa luettelon repo HACSista (vaatii uudelleenkäynnistyksen)
    "kaynnista_uudelleen",  # Home Assistantin uudelleenkäynnistys (viiveellä)
    "paivitykset",     # update-entiteetit + varmuuskopioiden tila
    "paivita",         # update.install yhdelle update-entiteetille
    "sovellukset",     # luettelon sovellusten (entiset lisäosat) tila
    "asenna_sovellus", # asenna ja käynnistä luettelon sovellus
    "varmuuskopio",    # luo varmuuskopio nyt
    # ── 1.6.0 (HALLINTA_VERSIO 3): radiot, signaali ja verkkoradion etsintä ──
    "radiot",          # antenni → vahvistimet → päätelaitteet, LQI/RSSI
    "etsi_radio",      # verkkoon kytketty Zigbee-radio ja sen socket-osoite
)

# 🔴 TARKISTETTU LUETTELO. Käyttäjän ALOITTAMA lisäys on mahdollinen vain
# näille. HA:n ITSE LÖYTÄMÄT laitteet saa vahvistaa riippumatta tästä
# listasta — ne HA on jo tunnistanut, ja «Lisää» on sama yksi painallus kuin
# HA:n omassa käyttöliittymässä. `test/ha-hallinta.mjs` vaatii, että
# palvelimen luettelo (src/services/haHallinta.js) on sama kuin tämä.
HALLINTA_KATALOGI = (
    "shelly", "zha", "matter", "thread", "esphome", "mqtt",
    # 🪪 1.4.0: lähiverkkoon kytketty radio (SMLIGHT SLZB, PoE). Ilman tätä
    # katalogi ohjasi vain USB-tikkuun. [Niko 18.11.2026: SLZB-MR2]
    "smlight",
    "vallox", "vicare", "mill", "nibe_heatpump",
    "hue", "tuya", "sonos", "cast", "apple_tv", "androidtv_remote",
    "samsungtv", "webostv", "met", "nordpool", "easee", "zaptec",
    # HACSista ladattavat (HACS_KATALOGI): lisättävissä vasta latauksen jälkeen.
    "connectlife", "hisense_ac_plugin",
)

# 🔴 HACS: VAIN NÄMÄ REPOT. HACS-integraatiot ovat yhteisön koodia, jota Home
# Assistant -projekti ei tarkista. Siksi luettelo on lyhyt ja jokainen rivi on
# valittu käsin: repo → integraation domain. [Niko 17.11.2026: Hisense-koneet]
HACS_KATALOGI = {
    "oyvindwe/connectlife-ha": "connectlife",
    "Connectlife-LLC/HomeAssistantPlugin": "hisense_ac_plugin",
}

# 🔴 SOVELLUKSET (Supervisor-lisäosat): vain Home Assistantin omat (core_*).
# Ne ovat edellytyksiä radioille, eivät mitä tahansa ohjelmia kotikoneelle.
SOVELLUS_KATALOGI = (
    "core_matter_server",
    "core_mosquitto",
    "core_openthread_border_router",
)

# Nämä EIVÄT koskaan kelpaa flow-kohteiksi, eivät löydettyinäkään: oma
# siltamme (ettei Klevra voi katkaista itseään) ja Supervisorin sisäiset.
HALLINTA_KIELLETYT = ("klevra", "hassio", "homeassistant", "backup", "cloud")

# Tuloksen katto. Yksi laiterekisteri voi olla satoja laitteita.
HALLINTA_LAITE_KATTO = 500
