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
    HALLINTA_VERSIO,
    KIELLETYT_KENTAT,
    LISAOSA_VERSIO,
    KIINNOSTAVAT,
    ODOTUS_S,
    OLETUS_PALVELIN,
    OLETUS_VALI,
    OTSAKE,
    POLKU_HAVAINNOT,
    SALLITUT,
    VIRHE_KATTO_S,
)
from .hallinta import Hallinta
from .liitos import vahdi

_LOGGER = logging.getLogger(__name__)

CONFIG_SCHEMA = vol.Schema(
    {
        DOMAIN: vol.Schema(
            {
                # 🔴 EI PAKOLLINEN. Kortilla on pelkkä `klevra:` ilman tunnusta:
                # tunnusta ei ole olemassa kortin kirjoitushetkellä. Rivi on se,
                # mikä saa Home Assistantin lataamaan tämän integraation, jotta
                # `liitos.vahdi` pääsee odottamaan QR-skannausta. [SALMI 18.11.2026]
                vol.Optional("tunnus"): cv.string,
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
    """YAML-asennus ja kortilta liittyminen.

    Kolme tapausta, tässä järjestyksessä:
      1. `klevra:` puuttuu configuration.yaml:ista → ei tehtävää. (Ilman entryä
         Home Assistant ei kutsu tätä lainkaan.)
      2. `klevra:` on, mutta tunnusta ei → 🪪 KORTTI. Jäädään vahtimaan tunnusta
         (`liitos.vahdi`) ja asennus syntyy itsestään kun QR skannataan.
      3. `klevra:` ja tunnus → vanha YAML-polku, muuttumattomana.
    """
    if DOMAIN not in config:
        return True
    asetukset = config[DOMAIN] or {}
    if not asetukset.get("tunnus"):
        # 🪪 Kortin polku. Taustatehtävä, jotta käynnistys ei jää odottamaan:
        # laite voi seisoa nurkassa viikon ennen kuin tarra skannataan.
        hass.async_create_task(vahdi(hass))
        _LOGGER.info("Klevra: odotetaan liitostunnusta (kortti-asennus)")
        return True
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
        # 🛠 Hallintakanava [RANTA 17.11.2026]. Tulokset lähtevät SEURAAVALLA
        # havaintokutsulla — ei omaa reittiä, ei omaa yhteyttä.
        self.hallinta = Hallinta(hass)
        self._tulokset = []

    # ── Havainnot ────────────────────────────────────────────────────────────

    def _rekisteri(self, eid):
        """Entiteetin alue (huone) JA laite Home Assistantin rekistereistä.

        🔴 ALUE EI OLE ATTRIBUUTTI. Se on entiteetti-, laite- ja aluerekisterin
        yhdistelmä, eikä se näy `/api/states`-vastauksessa lainkaan. Siksi
        palvelimen puolella huonetta joudutaan hakemaan templatella — mutta
        lisäosa pyörii Home Assistantin SISÄLLÄ, jossa tieto on ilmaiseksi.

        🔴 SAMA PÄTEE LAITTEESEEN. [SALMI 14.11.2026, Nikon havainto]
        «sähköauton laturi eve01 jossa on eri toimintoja pitäisi olla yksi
        laite jonka sisällä on nämä kaikki toiminnot tai arvot» — laite on
        oma rekisteri, eikä yksikään entiteetin attribuutti kerro mihin
        laitteeseen se kuuluu. `device_id` on se tieto, ja se on tässä
        funktiossa jo käsillä: sitä käytettiin vain alueen hakuun ja
        heitettiin sen jälkeen pois.

        Palauttaa (huone, luokka, laite_id, laite_nimi, valmistaja, malli).
        """
        tyhja = (None, None, None, None, None, None)
        try:
            from homeassistant.helpers import area_registry, device_registry, entity_registry

            er = entity_registry.async_get(self.hass)
            merkinta = er.async_get(eid)
            if merkinta is None:
                return tyhja
            alue_id = merkinta.area_id
            laite = None
            if merkinta.device_id:
                dr = device_registry.async_get(self.hass)
                laite = dr.async_get(merkinta.device_id)
                if alue_id is None:
                    alue_id = laite.area_id if laite else None
            nimi = None
            if alue_id:
                ar = area_registry.async_get(self.hass)
                alue = ar.async_get_area(alue_id)
                nimi = alue.name if alue else None
            # entity_category: 'config' tai 'diagnostic' = ei käyttäjän ohjain.
            luokka = merkinta.entity_category
            # 🔴 name_by_user ENSIN: jos asukas on nimennyt laitteen HA:ssa,
            # se nimi on se jonka hän odottaa näkevänsä myös Klevrassa.
            laite_nimi = None
            if laite is not None:
                laite_nimi = getattr(laite, "name_by_user", None) or getattr(laite, "name", None)
            return (
                nimi,
                (luokka.value if hasattr(luokka, "value") else luokka),
                merkinta.device_id or None,
                (str(laite_nimi)[:80] if laite_nimi else None),
                (str(laite.manufacturer)[:60] if laite is not None and laite.manufacturer else None),
                (str(laite.model)[:60] if laite is not None and laite.model else None),
            )
        except Exception as e:  # noqa: BLE001
            # Rekisterin rakenne on vaihtunut Home Assistantin versioiden välillä.
            # Huoneen puuttuminen ei ole syy jättää tilat lähettämättä.
            if self._virheita == 0:
                _LOGGER.debug("Klevra: rekisteritietoa ei saatu (%s)", e)
            return tyhja

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
            huone, luokka, laite_id, laite_nimi, valmistaja, malli = self._rekisteri(eid)
            ulos.append(
                {
                    "entiteetti": eid,
                    "tila": tila.state,
                    "yksikko": tila.attributes.get("unit_of_measurement"),
                    "nimi": tila.attributes.get("friendly_name"),
                    "huone": huone,
                    # 🔴 LAITE, EI ENTITEETTI. Nämä neljä kenttää ovat se tieto,
                    # jolla palvelin yhdistää kahdeksan entiteettiä yhdeksi
                    # latauslaitteeksi (src/services/laiteryhmat.js). Ilman
                    # niitä ryhmä on pakko arvata nimestä.
                    "laite_id": laite_id,
                    "laite_nimi": laite_nimi,
                    "valmistaja": valmistaja,
                    "malli": malli,
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

    async def _aja_hallinta(self, tehtava):
        """Suorita yksi hallintatehtävä ja jätä tulos lähtemään."""
        tid = tehtava.get("id")
        ok, tulos = await self.hallinta.suorita(
            str(tehtava.get("toiminto") or ""), tehtava.get("tiedot") or {}
        )
        self._tulokset.append({"id": tid, "ok": ok, "tulos": tulos})

    # ── Pääsilmukka ──────────────────────────────────────────────────────────

    async def aja(self):
        """Lähetä tilat, odota komentoja samassa kutsussa, suorita, toista."""
        istunto = async_get_clientsession(self.hass)
        _LOGGER.info("Klevra-silta kaynnistyi, palvelin %s", self.palvelin)

        while True:
            try:
                lahtevat = self._tulokset[:20]
                vastaus = await istunto.post(
                    f"{self.palvelin}{POLKU_HAVAINNOT}",
                    json={
                        "havainnot": self._kerää(),
                        # 🔴 Kun tulos odottaa lähtöä, EI pitkää odotusta: käyttäjä
                        # katsoo sovelluksessa pyörivää merkkiä juuri nyt.
                        "odota_s": 0 if self._tulokset else ODOTUS_S,
                        "lisaosa": {"versio": LISAOSA_VERSIO, "hallinta": HALLINTA_VERSIO},
                        "hallinta_tulokset": lahtevat,
                    },
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
                # Lähteneet tulokset pois vasta kun palvelin vastasi.
                del self._tulokset[: len(lahtevat)]
                for komento in data.get("komennot", []):
                    await self._aja_komento(komento)
                for tehtava in (data.get("hallinta") or [])[:10]:
                    await self._aja_hallinta(tehtava)

                self.vali = 0 if self._tulokset else int(data.get("seuraava_s") or OLETUS_VALI)
                self._virheita = 0

            except Exception as e:  # noqa: BLE001
                self._virheita += 1
                self.vali = min(VIRHE_KATTO_S, OLETUS_VALI * (2 ** min(self._virheita, 6)))
                if self._virheita in (1, 5, 20):
                    _LOGGER.warning(
                        "Klevra: yhteysvirhe (%s). Yritetaan %s s paasta.", e, self.vali
                    )

            await asyncio.sleep(self.vali)
