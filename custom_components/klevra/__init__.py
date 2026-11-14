"""
Klevra Home — Home Assistant -integraatio (silta).

Yhdistää Home Assistantin Klevra Homeen **ilman pilvipalvelua, porttiohjausta tai
julkista IP-osoitetta**: tämä komponentti ottaa LÄHTEVÄN yhteyden Klevraan, eikä
lähtevää yhteyttä estä operaattorin CGNAT eikä se vaadi reitittimen asetuksia.

🔴 MIKSI TÄMÄ ON OMA LISÄOSA EIKÄ `ohjaamo`-LISÄOSAN KOPIO.
Mitattu 14.11.2026 tiedostosta `ha-komponentti/custom_components/ohjaamo/__init__.py`:

  | mitä | ohjaamo-lisäosa | tämä |
  |---|---|---|
  | lähetetyt kentät | 4 (`entity_id`, `state`, `yksikko`, `nimi`) | tila + attribuutit + huone + laji |
  | attribuutit | **ei yhtään** | lähetetään (katkaistuna) |
  | komennot | 4 avainta (`tila`, `tavoite_c`, `kirkkaus`, `arvo`) | palvelu + tiedot, domainkohtainen sallittu lista |
  | kysely | 30 s kiinteä | pitkä kysely, vastaus heti kun komento tulee |

Ilman attribuutteja Klevra EI voi laskea laitteen kykyjä: `min_temp`, `max_temp`,
`supported_color_modes`, `options`, `hvac_modes` ja `percentage_step` tulevat
kaikki attribuuteista. Siksi vanhalla lisäosalla laite näkyy lukemana jota ei voi
ohjata — ja juuri sen Niko pyysi korjattavaksi 13.11.2026 klo 20.46.

Asennus: ks. `ha-lisaosa-klevra/README.md`.

🔴 TÄTÄ EI ASENNETA `nikokuikka-sys/laskuhelposti`-reposta. Se on YKSITYINEN, ja
HACS vastaa yksityiselle repolle `GitHub returned 404` silloinkin kun olet itse
sen omistaja — se hakee tiedot `api.github.com`-rajapinnasta ilman oikeuksia.
[Mitattu 14.11.2026 klo 06.58 Nikon puhelimesta.] Tämä kansio on sellaisenaan
JULKISEN repon sisältö: `hacs.json`, `README.md` ja `custom_components/klevra/`.
"""
import asyncio
import logging

import voluptuous as vol
import homeassistant.helpers.config_validation as cv
from homeassistant.const import EVENT_HOMEASSISTANT_STARTED
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import (
    ATTR_MAARA_KATTO,
    ATTR_MERKKI_KATTO,
    DOMAIN,
    KIELLETYT_KENTAT,
    KIINNOSTAVAT,
    ODOTUS_S,
    OLETUS_PALVELIN,
    OLETUS_VALI,
    OTSAKE,
    POLKU_HAVAINNOT,
    SALLITUT,
    VIRHE_KATTO_S,
)

_LOGGER = logging.getLogger(__name__)

CONFIG_SCHEMA = vol.Schema(
    {
        DOMAIN: vol.Schema(
            {
                vol.Required("tunnus"): cv.string,
                vol.Optional("palvelin", default=OLETUS_PALVELIN): cv.string,
                vol.Optional("entiteetit", default=[]): vol.All(cv.ensure_list, [cv.string]),
            }
        )
    },
    extra=vol.ALLOW_EXTRA,
)


async def async_setup_entry(hass, entry):
    """Käynnistä silta käyttöliittymästä tehdystä asennuksesta."""
    silta = Klevrasilta(
        hass,
        tunnus=entry.data["tunnus"],
        palvelin=entry.data.get("palvelin", OLETUS_PALVELIN).rstrip("/"),
        entiteetit=entry.options.get("entiteetit") or [],
    )
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = silta
    silta._tehtava = hass.async_create_task(silta.aja())
    return True


async def async_unload_entry(hass, entry):
    """Sammuta silta kun integraatio poistetaan."""
    silta = hass.data.get(DOMAIN, {}).pop(entry.entry_id, None)
    if silta and getattr(silta, "_tehtava", None):
        silta._tehtava.cancel()
    return True


async def async_setup(hass, config):
    """YAML-asennus. Säilytetty, mutta HACS + config flow on se polku jota käytetään."""
    if DOMAIN not in config:
        return True
    asetukset = config[DOMAIN]
    silta = Klevrasilta(
        hass,
        tunnus=asetukset["tunnus"],
        palvelin=asetukset.get("palvelin", OLETUS_PALVELIN).rstrip("/"),
        entiteetit=asetukset.get("entiteetit") or [],
    )

    async def kaynnista(_event):
        hass.async_create_task(silta.aja())

    hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STARTED, kaynnista)
    hass.data[DOMAIN] = silta
    return True


class Klevrasilta:
    """Lähettää tilat attribuutteineen ja suorittaa komennot."""

    def __init__(self, hass, tunnus, palvelin, entiteetit):
        self.hass = hass
        self.tunnus = tunnus
        self.palvelin = palvelin
        self.suodatin = set(entiteetit)
        self.vali = OLETUS_VALI
        self._virheita = 0
        self._huoneet = {}

    # ── Havainnot ────────────────────────────────────────────────────────────

    def _huone(self, eid):
        """Entiteetin alue (huone) Home Assistantin rekistereistä.

        🔴 ALUE EI OLE ATTRIBUUTTI. Se on entiteetti-, laite- ja aluerekisterin
        yhdistelmä, eikä se näy `/api/states`-vastauksessa lainkaan. Siksi
        palvelimen puolella huonetta joudutaan hakemaan templatella — mutta
        lisäosa pyörii Home Assistantin SISÄLLÄ, jossa tieto on ilmaiseksi.
        """
        try:
            from homeassistant.helpers import area_registry, device_registry, entity_registry

            er = entity_registry.async_get(self.hass)
            merkinta = er.async_get(eid)
            if merkinta is None:
                return None, None
            alue_id = merkinta.area_id
            if alue_id is None and merkinta.device_id:
                dr = device_registry.async_get(self.hass)
                laite = dr.async_get(merkinta.device_id)
                alue_id = laite.area_id if laite else None
            nimi = None
            if alue_id:
                ar = area_registry.async_get(self.hass)
                alue = ar.async_get_area(alue_id)
                nimi = alue.name if alue else None
            # entity_category: 'config' tai 'diagnostic' = ei käyttäjän ohjain.
            luokka = merkinta.entity_category
            return nimi, (luokka.value if hasattr(luokka, "value") else luokka)
        except Exception as e:  # noqa: BLE001
            # Rekisterin rakenne on vaihtunut Home Assistantin versioiden välillä.
            # Huoneen puuttuminen ei ole syy jättää tilat lähettämättä.
            if self._virheita == 0:
                _LOGGER.debug("Klevra: aluetta ei saatu (%s)", e)
            return None, None

    def _attribuutit(self, tila):
        """Attribuutit katkaistuina. Ei kuvia, ei soittolistoja, ei tokeneita."""
        ulos = {}
        for avain, arvo in list(tila.attributes.items())[:ATTR_MAARA_KATTO]:
            if avain in ("entity_picture", "access_token", "friendly_name"):
                continue
            if isinstance(arvo, (int, float, bool)) or arvo is None:
                ulos[avain] = arvo
            elif isinstance(arvo, str):
                ulos[avain] = arvo[:ATTR_MERKKI_KATTO]
            elif isinstance(arvo, (list, tuple)):
                # Lista pidetään vain jos se on lyhyt ja alkiot yksinkertaisia:
                # juuri näitä ovat hvac_modes, options, supported_color_modes ja rgb_color.
                alkiot = [x for x in arvo if isinstance(x, (int, float, bool, str))][:24]
                if alkiot:
                    ulos[avain] = [x[:60] if isinstance(x, str) else x for x in alkiot]
        return ulos

    def _kerää(self):
        """Kerää lähetettävät entiteettitilat attribuutteineen."""
        ulos = []
        for tila in self.hass.states.async_all():
            eid = tila.entity_id
            if self.suodatin:
                if eid not in self.suodatin:
                    continue
            elif not eid.startswith(KIINNOSTAVAT):
                continue
            huone, luokka = self._huone(eid)
            ulos.append(
                {
                    "entiteetti": eid,
                    "tila": tila.state,
                    "yksikko": tila.attributes.get("unit_of_measurement"),
                    "nimi": tila.attributes.get("friendly_name"),
                    "huone": huone,
                    # 🔴 `laji` ON device_class, EI entity_category.
                    # Palvelin kirjoittaa `laji`:n suoraan `device_class`-
                    # attribuuttiin (kotiSilta.js), ja device_class ratkaisee
                    # laitteen kategorian. Jos tähän laittaisi 'diagnostic',
                    # jokainen diagnostiikkaentiteetti luokiteltaisiin väärin.
                    # entity_category kulkee omassa kentässään `luokka`.
                    "laji": tila.attributes.get("device_class"),
                    "luokka": luokka,
                    "attribuutit": self._attribuutit(tila),
                }
            )
        return ulos

    # ── Komennot ─────────────────────────────────────────────────────────────

    async def _aja_komento(self, komento):
        """Suorita Klevran lähettämä komento paikallisesti.

        Komennon muoto on `{palvelu: "domain.service", entiteetti, tiedot}` —
        sama kuin palvelin jonottaa (`src/services/kotiSilta.js`).
        """
        try:
            entiteetti = komento.get("entiteetti") or komento.get("entity_id")
            palvelu = str(komento.get("palvelu") or "")
            tiedot = komento.get("tiedot") or {}
            if not entiteetti or "." not in palvelu:
                _LOGGER.warning("Klevra: vaillinainen komento, ohitetaan")
                return

            domain, nimi = palvelu.split(".", 1)

            # 🔴 KAKSI PORTTIA, JA KUMPIKIN TARVITAAN.
            # 1) palvelu on domainin sallitulla listalla
            if nimi not in SALLITUT.get(domain, set()):
                _LOGGER.warning("Klevra: palvelu %s ei ole sallittu, ohitetaan", palvelu)
                return
            # 2) palvelun domain on sama kuin entiteetin domain
            #    Ilman tätä `climate.set_temperature` menisi water_heater-
            #    entiteetille — sama vika kuin palvelimella 10.11.2026.
            if entiteetti.split(".")[0] != domain:
                _LOGGER.warning(
                    "Klevra: %s ei sovi entiteetille %s, ohitetaan", palvelu, entiteetti
                )
                return

            puhdas = {k: v for k, v in tiedot.items() if k not in KIELLETYT_KENTAT}
            puhdas["entity_id"] = entiteetti

            await self.hass.services.async_call(domain, nimi, puhdas, blocking=False)
        except Exception as e:  # noqa: BLE001
            # Sivuvaikutus: yhden komennon kaatuminen ei saa pysäyttää siltaa.
            _LOGGER.error("Klevra: komennon suoritus epaonnistui: %s", e)

    # ── Pääsilmukka ──────────────────────────────────────────────────────────

    async def aja(self):
        """Lähetä tilat, odota komentoja samassa kutsussa, suorita, toista."""
        istunto = async_get_clientsession(self.hass)
        _LOGGER.info("Klevra-silta kaynnistyi, palvelin %s", self.palvelin)

        while True:
            try:
                vastaus = await istunto.post(
                    f"{self.palvelin}{POLKU_HAVAINNOT}",
                    json={"havainnot": self._kerää(), "odota_s": ODOTUS_S},
                    headers={OTSAKE: self.tunnus},
                    timeout=ODOTUS_S + 15,
                )
                if vastaus.status == 401:
                    _LOGGER.error(
                        "Klevra: tunnus ei kelpaa. Luo uusi: Klevra Home -> "
                        "Kodin asetukset -> Home Assistant."
                    )
                    await asyncio.sleep(VIRHE_KATTO_S)
                    continue

                data = await vastaus.json()
                for komento in data.get("komennot", []):
                    await self._aja_komento(komento)

                self.vali = int(data.get("seuraava_s") or OLETUS_VALI)
                self._virheita = 0

            except Exception as e:  # noqa: BLE001
                self._virheita += 1
                self.vali = min(VIRHE_KATTO_S, OLETUS_VALI * (2 ** min(self._virheita, 6)))
                if self._virheita in (1, 5, 20):
                    _LOGGER.warning(
                        "Klevra: yhteysvirhe (%s). Yritetaan %s s paasta.", e, self.vali
                    )

            await asyncio.sleep(self.vali)
