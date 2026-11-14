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
