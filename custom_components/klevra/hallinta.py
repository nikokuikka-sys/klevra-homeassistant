"""
🛠 Klevra-sillan HALLINTAKANAVA. [RANTA 17.11.2026, Niko klo 14.28]

«Miten olisi mahdollisuus hallita Home Assistantia ja lisätä Klevra Homen
 kautta laitteita … ettei käyttäjän tarvitsisi mennä Home Assistant
 -ympäristöön»

Tämä moduuli tekee saman, minkä HA:n oma käyttöliittymä tekee integraation
lisäyksessä: aloittaa «config flow»:n, välittää vaiheen kentät, lähettää
käyttäjän syötteen ja palauttaa seuraavan vaiheen. Klevra piirtää kentät.

🔴 KOLME RAJAA, JA JOKAINEN ON TÄSSÄ TIEDOSTOSSA — EI PALVELIMELLA.
Palvelin voi olla väärässä tai murrettu; kotona ajettava koodi päättää.

  1. Vain `HALLINTA_TOIMINNOT`-listan nimetyt toiminnot. Ei vapaata
     palvelukutsua, ei tiedostoja, ei komentoriviä.
  2. Käyttäjän ALOITTAMA lisäys vain `HALLINTA_KATALOGI`:n integraatioille.
  3. `flow_jatka` ja `flow_peru` vain flow'lle, jonka Klevra itse aloitti tai
     jonka HA itse löysi (lähde ei ole «user»). Klevra ei voi tarttua flow'hun,
     jonka joku aloitti HA:n omassa käyttöliittymässä.

🔴 SALASANAT. Osa integraatioista (esim. ViCare) kysyy valmistajan tunnukset.
Ne kulkevat KÄYTTÄJÄN omaan Home Assistantiin, eivät Klevraan: tämä moduuli ei
tallenna syötettä mihinkään eikä palauta sitä tuloksessa. Palvelin pyyhkii
syötteen jonostaan heti kun lisäosa on noutanut sen (`haHallinta.js`).

HA:n sisäiset rajapinnat tuodaan funktioiden sisällä: näin moduulin voi
testata ilman Home Assistantia (`test/ha-hallinta-lisaosa.py`).
"""
import json
import logging
import re

from .const import (
    HACS_KATALOGI,
    HALLINTA_KATALOGI,
    HALLINTA_KIELLETYT,
    HALLINTA_LAITE_KATTO,
    HALLINTA_TOIMINNOT,
    # 🔴 Nämä kaksi puuttuivat kun `tarkista_palvelu` siirtyi tänne
    # 19.11.2026. `python -m py_compile` hyväksyi tiedoston — nimi joka ei
    # ole olemassa on virhe vasta AJOSSA. CLAUDE.md sääntö 2: syntaksi-
    # tarkistus todistaa että tiedosto jäsentyy, ei että se toimii.
    KIELLETYT_KENTAT,
    SALLITUT,
    SOVELLUS_KATALOGI,
)

_LOGGER = logging.getLogger(__name__)

_MATTER_KOODI = re.compile(r"^(MT:[0-9A-Z.\-]{10,40}|[0-9]{11}|[0-9]{21})$")


class Hylatty(Exception):
    """Toiminto hylättiin säännön takia. Viesti on käyttäjälle näytettävä syy."""


def _arvo(x):
    """Enum → arvo. HA:n FlowResultType on enum vanhoissa ja uusissa versioissa."""
    return getattr(x, "value", x)


def _json_turvallinen(x):
    """Kaikki tulos menee JSONina palvelimelle. Tuntematon tyyppi → merkkijono."""
    return json.loads(json.dumps(x, default=str))


def _oletusarvo(avain):
    """voluptuous-merkin oletusarvo, tai None jos sitä ei ole.

    Merkin `default` on voluptuousissa joko kutsuttava tehdas tai UNDEFINED-
    sentinel (ei kutsuttava). Vain tehtaan tulos on oikea oletus.
    """
    tehdas = getattr(avain, "default", None)
    if not callable(tehdas):
        return None
    try:
        arvo = tehdas()
    except Exception:  # noqa: BLE001  oletus, jota ei saa laskettua, jätetään pois
        return None
    if arvo is None or type(arvo).__name__ == "Undefined":
        return None
    return arvo


def _validoijan_muoto(validoija, syvyys=0):
    """Validoijasta ne kentän tiedot, jotka Klevran lomake osaa piirtää.

    Tunnistus on ankkuroitu OMINAISUUKSIIN eikä luokkien nimiin, koska
    voluptuousia ei tuoda tänne lainkaan: `In` tunnetaan `container`-
    kentästä, HA:n selector `selector_type`-kentästä, `All`/`Any`
    `validators`-listasta.
    """
    if validoija is None or syvyys > 3:
        return {}
    ulos = {}
    osat = getattr(validoija, "validators", None)
    if isinstance(osat, (list, tuple)):
        for osa in osat:
            for k, v in _validoijan_muoto(osa, syvyys + 1).items():
                ulos.setdefault(k, v)
        return ulos
    laji = getattr(validoija, "selector_type", None)
    if isinstance(laji, str) and laji:
        ulos["selector"] = {laji: getattr(validoija, "config", None) or {}}
        return ulos
    sailio = getattr(validoija, "container", None)
    if isinstance(sailio, dict):
        ulos["options"] = [[a, b] for a, b in sailio.items()]
        return ulos
    if isinstance(sailio, (list, tuple, set, frozenset)):
        ulos["options"] = [x for x in sailio]
        return ulos
    if validoija is bool:
        return {"type": "boolean"}
    if validoija is int:
        return {"type": "integer"}
    if validoija is float:
        return {"type": "float"}
    if validoija is str:
        return {"type": "string"}
    nimi = getattr(validoija, "__name__", "")
    if nimi == "boolean":
        return {"type": "boolean"}
    if nimi in ("positive_int", "port"):
        return {"type": "integer"}
    if nimi in ("string", "url", "template"):
        return {"type": "string"}
    return ulos


def _kentat_skeemasta(skeema):
    """vol.Schema → kenttälista ILMAN voluptuous_serializea.

    🔴 Kirjoitettu 18.11.2026 (Niko, korjausehdotus #204): kodin Home
    Assistantista puuttui `voluptuous_serialize`, ja koko lisäyslomake jäi
    yhden rivin virheilmoitukseksi — laitetta ei voinut lisätä lainkaan.
    Skeema on sanakirja, jonka avaimet ovat Required/Optional-merkkejä;
    se luetaan tässä itse, jottei lomake riipu yhdestä valinnaisesta
    kirjastosta.
    """
    raaka = getattr(skeema, "schema", skeema)
    if not isinstance(raaka, dict):
        raise TypeError("skeema ei ole sanakirja (%s)" % type(raaka).__name__)
    kentat = []
    for avain, validoija in raaka.items():
        nimi = getattr(avain, "schema", avain)
        kentta = {"name": str(nimi)}
        if type(avain).__name__ == "Required":
            kentta["required"] = True
        oletus = _oletusarvo(avain)
        if oletus is not None:
            kentta["default"] = oletus
        kentta.update(_validoijan_muoto(validoija))
        kentat.append(kentta)
    if not kentat:
        raise ValueError("skeemassa ei ollut yhtään kenttää")
    return kentat


def tarkista_palvelu(entiteetti, palvelu):
    """Kaksi porttia jokaiselle palvelukutsulle. (domain, nimi) tai (None, syy).

    🔴 YKSI SÄÄNTÖ, YKSI PAIKKA. Tämä oli ennen kopioituna `__init__.py`:n
    komentopolkuun. Kun automaatioiden kirjoitus tuli kolmanneksi kutsujaksi
    (19.11.2026), kaksi kopiota olisi ollut kolme — ja kolmas ajautuu
    ensimmäisenä erilleen. Nyt kaikki kolme kysyvät samalta funktiolta.

    1) Palvelu on domainin sallitulla listalla (`SALLITUT`).
    2) Palvelun domain on SAMA kuin entiteetin domain. Ilman tätä
       `climate.set_temperature` menisi water_heater-entiteetille — se vika
       oli palvelimen puolella 10.11.2026.
    """
    entiteetti = str(entiteetti or "")
    palvelu = str(palvelu or "")
    if not entiteetti or "." not in palvelu or "." not in entiteetti:
        return None, "vaillinainen_komento"
    domain, nimi = palvelu.split(".", 1)
    if nimi not in SALLITUT.get(domain, set()):
        return None, "palvelu_ei_sallittu"
    if entiteetti.split(".")[0] != domain:
        return None, "palvelu_ei_sovi_entiteetille"
    return (domain, nimi), None


def puhdista_tiedot(tiedot, entiteetti):
    """Palvelukutsun kentät: kielletyt pois, entity_id aina paikallisesti."""
    puhdas = {k: v for k, v in (tiedot or {}).items() if k not in KIELLETYT_KENTAT}
    puhdas["entity_id"] = entiteetti
    return puhdas


class Hallinta:
    """Nimetyt hallintatoiminnot. Yksi instanssi per silta."""

    def __init__(self, hass):
        self.hass = hass
        # Flow't, jotka Klevra aloitti. Vain näitä (ja HA:n löytämiä) saa jatkaa.
        self.omat_flowt = set()

    # ── Pääportti ────────────────────────────────────────────────────────────

    async def suorita(self, toiminto, tiedot):
        """Suorita yksi toiminto. Palauttaa (ok, tulos). Ei koskaan heitä."""
        try:
            if toiminto not in HALLINTA_TOIMINNOT:
                raise Hylatty("toiminto_ei_sallittu")
            tiedot = tiedot if isinstance(tiedot, dict) else {}
            f = getattr(self, "_" + toiminto)
            return True, _json_turvallinen(await f(tiedot))
        except Hylatty as e:
            return False, {"syy": str(e)}
        except Exception as e:  # noqa: BLE001
            # 🔴 Syy palautetaan käyttäjälle, ei nielaista: «ei onnistunut» ilman
            # syytä jättäisi hänet arvaamaan, ja juuri siksi hän ei mene HA:han.
            _LOGGER.warning("Klevra-hallinta: %s epäonnistui: %s", toiminto, e)
            return False, {"syy": "virhe", "viesti": str(e)[:300]}

    # ── Tila ─────────────────────────────────────────────────────────────────

    async def _tila(self, _tiedot):
        try:
            from homeassistant.const import __version__ as ha_versio
        except Exception:  # noqa: BLE001  versio on tieto, ei edellytys
            ha_versio = None
        merkinnat = []
        for e in self.hass.config_entries.async_entries():
            if e.domain in HALLINTA_KIELLETYT:
                continue
            merkinnat.append({
                "entry_id": e.entry_id,
                "domain": e.domain,
                "otsikko": str(e.title)[:80],
                "tila": str(_arvo(e.state)),
                "poissa_kaytosta": bool(getattr(e, "disabled_by", None)),
            })
        domainit = {m["domain"] for m in merkinnat}
        komponentit = set(getattr(self.hass.config, "components", set()) or set())
        return {
            "ha_versio": ha_versio,
            "kieli": getattr(self.hass.config, "language", None),
            "supervisor": "hassio" in komponentit,
            "zigbee": "zha" in domainit,
            "matter": "matter" in domainit,
            "thread": "thread" in domainit or "otbr" in domainit,
            "integraatiot": sorted(merkinnat, key=lambda m: (m["domain"], m["otsikko"])),
            "loydettyja": len(self._loydetyt_lista()),
        }

    # ── Löydetyt ─────────────────────────────────────────────────────────────

    def _loydetyt_lista(self):
        ulos = []
        for f in self.hass.config_entries.flow.async_progress():
            konteksti = f.get("context") or {}
            lahde = konteksti.get("source")
            if lahde in (None, "user", "ignore") or f.get("handler") in HALLINTA_KIELLETYT:
                continue
            ulos.append({
                "flow_id": f.get("flow_id"),
                "domain": f.get("handler"),
                "vaihe": f.get("step_id"),
                "lahde": lahde,
                "nimi": _json_turvallinen(konteksti.get("title_placeholders") or {}),
            })
        return ulos

    async def _loydetyt(self, _tiedot):
        lista = self._loydetyt_lista()
        for x in lista:
            x["integraatio"] = await self._integraation_nimi(x["domain"])
        return {"loydetyt": lista}

    async def _integraation_nimi(self, domain):
        try:
            from homeassistant.loader import async_get_integration

            return (await async_get_integration(self.hass, domain)).name
        except Exception:  # noqa: BLE001  nimi on koriste; domain riittää
            return domain

    # ── Config flow ──────────────────────────────────────────────────────────

    async def _flow_aloita(self, tiedot):
        domain = str(tiedot.get("domain") or "")
        if domain not in HALLINTA_KATALOGI or domain in HALLINTA_KIELLETYT:
            raise Hylatty("integraatio_ei_luettelossa")
        tulos = await self.hass.config_entries.flow.async_init(domain, context={"source": "user"})
        if tulos.get("flow_id") and _arvo(tulos.get("type")) not in ("create_entry", "abort"):
            self.omat_flowt.add(tulos["flow_id"])
        return await self._sarjoita(tulos)

    def _saako_koskea(self, flow_id):
        """Flow on Klevran oma tai HA:n itse löytämä. Muuten Hylatty.

        🔴 KAKSI ERI SYYTÄ, EI YHTÄ. [Niko 18.11.2026, ehdotus #202 / issue #206]
        Aiemmin tämä palautti `None` kolmesta eri syystä ja kutsuja käänsi
        jokaisen samaksi virheeksi: «Tätä lisäystä ei aloitettu Klevrasta, eikä
        se ole Home Assistantin löytämä laite.» Nikon kuvassa se luki löydetyn
        laitteen päällä — ja väite oli epätosi: laite OLI HA:n löytämä, mutta
        «Löydetyt laitteet» -lista oli vanha, eikä sen `flow_id` ollut enää
        käynnissä. S48: tilakoodi kertoo sen mitä tapahtui, ei sitä mikä on
        lähin olemassa oleva koodi.

          flow_id tyhjä tai ei enää käynnissä → `flow_vanhentunut` (lista vanha)
          flow käynnissä mutta HA:n oma       → `flow_ei_klevran`  (ei kosketa)
        """
        if not flow_id:
            raise Hylatty("flow_vanhentunut")
        for f in self.hass.config_entries.flow.async_progress():
            if f.get("flow_id") != flow_id:
                continue
            if f.get("handler") in HALLINTA_KIELLETYT:
                raise Hylatty("flow_ei_klevran")
            lahde = (f.get("context") or {}).get("source")
            if flow_id in self.omat_flowt or lahde not in (None, "user"):
                return f
            raise Hylatty("flow_ei_klevran")
        raise Hylatty("flow_vanhentunut")

    async def _flow_jatka(self, tiedot):
        flow_id = str(tiedot.get("flow_id") or "")
        self._saako_koskea(flow_id)
        syote = tiedot.get("syote")
        # 🔴 None on HA:ssa «näytä nykyinen vaihe». Löydetty laite avataan
        # näin: ensin None (lomake/vahvistus), sitten käyttäjän syöte.
        if syote is not None and not isinstance(syote, dict):
            raise Hylatty("syote_ei_kelpaa")
        if isinstance(syote, dict):
            syote = {str(k)[:60]: v for k, v in list(syote.items())[:40]}
        tulos = await self.hass.config_entries.flow.async_configure(flow_id, syote)
        if _arvo(tulos.get("type")) in ("create_entry", "abort"):
            self.omat_flowt.discard(flow_id)
        return await self._sarjoita(tulos)

    async def _flow_peru(self, tiedot):
        flow_id = str(tiedot.get("flow_id") or "")
        self._saako_koskea(flow_id)
        self.hass.config_entries.flow.async_abort(flow_id)
        self.omat_flowt.discard(flow_id)
        return {"peruttu": flow_id}

    async def _kaannokset(self, domain):
        """Kenttien ja vaiheiden suomenkieliset (tai englanninkieliset) nimet."""
        try:
            from homeassistant.helpers.translation import async_get_translations

            kieli = getattr(self.hass.config, "language", None) or "en"
            k = await async_get_translations(self.hass, kieli, "config", [domain])
            if not k and kieli != "en":
                k = await async_get_translations(self.hass, "en", "config", [domain])
            return k or {}
        except Exception:  # noqa: BLE001  ilman käännöksiä näytetään kenttien nimet
            return {}

    async def _sarjoita(self, tulos):
        """FlowResult → JSON, jonka Klevra osaa piirtää lomakkeeksi."""
        tyyppi = str(_arvo(tulos.get("type")))
        domain = tulos.get("handler")
        vaihe = tulos.get("step_id")
        k = await self._kaannokset(domain) if domain else {}
        etu = f"component.{domain}.config"
        ulos = {
            "tyyppi": tyyppi,
            "flow_id": tulos.get("flow_id"),
            "domain": domain,
            "vaihe": vaihe,
            "otsikko": k.get(f"{etu}.step.{vaihe}.title") or tulos.get("title"),
            "kuvaus": k.get(f"{etu}.step.{vaihe}.description"),
            "selitteet": tulos.get("description_placeholders") or {},
            "virheet": {
                kentta: k.get(f"{etu}.error.{avain}") or avain
                for kentta, avain in (tulos.get("errors") or {}).items()
            },
        }
        if tyyppi == "abort":
            syy = tulos.get("reason")
            ulos["syy"] = syy
            ulos["syy_teksti"] = k.get(f"{etu}.abort.{syy}") or syy
        if tyyppi == "create_entry":
            # 🔴 `result` on ConfigEntry-olio ja `data` voi sisältää tunnukset.
            # Palautetaan vain otsikko — Klevra ei tarvitse muuta.
            ulos["luotu"] = str(tulos.get("title") or domain)[:80]
        # FlowResultType.EXTERNAL_STEP:n arvo on "external" (ei "external_step").
        if tyyppi in ("external", "external_step"):
            ulos["url"] = tulos.get("url")
        if tyyppi == "progress":
            ulos["edistyminen"] = tulos.get("progress_action")
        if tyyppi == "menu":
            valikko = tulos.get("menu_options") or []
            if isinstance(valikko, dict):
                ulos["valikko"] = [{"arvo": a, "nimi": n} for a, n in valikko.items()]
            else:
                ulos["valikko"] = [
                    {"arvo": a, "nimi": k.get(f"{etu}.step.{vaihe}.menu_options.{a}") or a}
                    for a in valikko
                ]
        skeema = tulos.get("data_schema")
        if skeema is not None:
            ulos["kentat"] = self._kentat(skeema, k, f"{etu}.step.{vaihe}")
        return ulos

    def _kentat(self, skeema, k, polku):
        try:
            import homeassistant.helpers.config_validation as cv
            from voluptuous_serialize import convert

            kentat = convert(skeema, custom_serializer=cv.custom_serializer)
        except Exception as e:  # noqa: BLE001
            # 🔴 `voluptuous_serialize` puuttui Nikon Home Assistantista
            # 18.11.2026 ja lomake jäi yhdeksi punaiseksi riviksi. Kirjasto on
            # mukavuus, ei ehto: skeema luetaan tarvittaessa itse.
            _LOGGER.debug("voluptuous_serialize ei käytettävissä (%s) — luetaan skeema itse", e)
            try:
                kentat = _kentat_skeemasta(skeema)
            except Exception as e2:  # noqa: BLE001
                # 🔴 Ilman kenttiä lomaketta ei voi täyttää. Kerrotaan se suoraan.
                return [{"name": "_", "virhe": f"kenttiä ei saatu luettua: {str(e2)[:120]}"}]
        for kentta in kentat:
            nimi = kentta.get("name")
            kentta["otsikko"] = k.get(f"{polku}.data.{nimi}") or nimi
            ohje = k.get(f"{polku}.data_description.{nimi}")
            if ohje:
                kentta["ohje"] = ohje
        return kentat

    # ── Zigbee ja Matter ─────────────────────────────────────────────────────

    async def _zigbee_liitos(self, tiedot):
        if not self.hass.config_entries.async_entries("zha"):
            raise Hylatty("zigbee_ei_kaytossa")
        try:
            s = int(tiedot.get("sekunnit") or 120)
        except (TypeError, ValueError):
            s = 120
        s = max(30, min(254, s))
        await self.hass.services.async_call("zha", "permit", {"duration": s}, blocking=True)
        return {"auki_s": s}

    async def _matter_koodi(self, tiedot):
        koodi = re.sub(r"[\s\-]", "", str(tiedot.get("koodi") or "")).upper()
        if koodi.startswith("MT:"):
            koodi = str(tiedot.get("koodi")).strip().upper()
        if not _MATTER_KOODI.match(koodi):
            raise Hylatty("matter_koodi_ei_kelpaa")
        if not self.hass.config_entries.async_entries("matter"):
            raise Hylatty("matter_ei_kaytossa")
        # 🔴 SAMA KUTSU KUIN HA:N OMA `matter/commission`-websocket. Koodi on
        # kertakäyttöinen jakokoodi, jonka toinen ohjain (Homey) näyttää;
        # network_only=True = laite on jo verkossa, Bluetoothia ei tarvita.
        from homeassistant.components.matter.helpers import get_matter

        matter = get_matter(self.hass)
        await matter.matter_client.commission_with_code(koodi, network_only=True)
        return {"lisatty": True}

    # ── Laitteet ja huoneet ──────────────────────────────────────────────────

    async def _laitteet(self, _tiedot):
        from homeassistant.helpers import area_registry, device_registry, entity_registry

        dr = device_registry.async_get(self.hass)
        er = entity_registry.async_get(self.hass)
        ar = area_registry.async_get(self.hass)
        alueet = {a.id: a.name for a in ar.async_list_areas()}
        maarat = {}
        for ent in er.entities.values():
            if ent.device_id:
                maarat[ent.device_id] = maarat.get(ent.device_id, 0) + 1
        domain_entrylle = {e.entry_id: e.domain for e in self.hass.config_entries.async_entries()}
        laitteet = []
        for d in list(dr.devices.values()):
            domainit = sorted({domain_entrylle.get(x) for x in (d.config_entries or ()) if domain_entrylle.get(x)})
            if any(x in HALLINTA_KIELLETYT for x in domainit) and len(domainit) == 1:
                continue
            laitteet.append({
                "id": d.id,
                "nimi": str(d.name_by_user or d.name or "")[:80],
                "alkuperainen": str(d.name or "")[:80],
                "valmistaja": str(d.manufacturer or "")[:60] or None,
                "malli": str(d.model or "")[:60] or None,
                "huone": alueet.get(d.area_id),
                "integraatiot": domainit,
                "entiteetteja": maarat.get(d.id, 0),
                "palvelu": str(_arvo(getattr(d, "entry_type", None)) or "") == "service",
            })
            if len(laitteet) >= HALLINTA_LAITE_KATTO:
                break
        laitteet.sort(key=lambda x: ((x["huone"] or "~"), x["nimi"].lower()))
        return {"laitteet": laitteet, "huoneet": sorted(alueet.values()),
                "katkaistu": len(dr.devices) > len(laitteet) and len(laitteet) >= HALLINTA_LAITE_KATTO}

    async def _nimea_laite(self, tiedot):
        from homeassistant.helpers import area_registry, device_registry

        dr = device_registry.async_get(self.hass)
        laite = dr.async_get(str(tiedot.get("laite_id") or ""))
        if laite is None:
            raise Hylatty("laitetta_ei_loydy")
        muutos = {}
        if "nimi" in tiedot:
            nimi = str(tiedot.get("nimi") or "").strip()[:80]
            # Tyhjä nimi = palaa valmistajan nimeen.
            muutos["name_by_user"] = nimi or None
        if "huone" in tiedot:
            huone = str(tiedot.get("huone") or "").strip()[:60]
            if huone:
                ar = area_registry.async_get(self.hass)
                alue = ar.async_get_area_by_name(huone) or ar.async_create(huone)
                muutos["area_id"] = alue.id
            else:
                muutos["area_id"] = None
        if not muutos:
            raise Hylatty("ei_muutoksia")
        dr.async_update_device(laite.id, **muutos)
        return {"laite_id": laite.id, "muutettu": sorted(muutos)}

    # ── Radiot ja signaali ───────────────────────────────────────────────────
    #
    # 📡 [Niko 19.11.2026 klo 06.06: «Mistä tiedän että tuo radio antenni toimii
    #  ja löytyy? … kohta joka erottelee nämä vahvistimet ja antennit niiden
    #  tilan ja sen alla laitteiden signaali».]
    #
    # Kolme kerrosta: ANTENNI (koordinaattori) → VAHVISTIN (reititin, verkkovirta)
    # → PÄÄTELAITE (paristo). Zigbeessä nämä ovat laitteen oikea tyyppi, ei
    # arvaus — mutta vain jos ZHA:n portti saadaan auki.
    #
    # 🔴 KAKSI LÄHDETTÄ, JA KUMPI KÄYTETTIIN KERROTAAN. ZHA:n sisäinen rajapinta
    # on vaihtunut HA-versioiden välillä (hass.data["zha"] → helpers.get_zha_gateway
    # → zha-kirjasto). Jos se ei aukea, palataan laiterekisteriin — ja silloin
    # tyyppi on PÄÄTTELY, ei mittaus. Kumpikin merkitään `laatu`-kenttään, koska
    # «vahvistin» väärällä laitteella lähettää Nikon siirtämään pistorasiaa
    # turhaan.
    #
    # 🔴 PUUTTUVA SIGNAALI EI OLE NOLLA. `lqi: None` = laite ei kerro. `lqi: 0`
    # = linkki on poikki. Nämä EIVÄT saa näyttää samalta; sama sääntö kuin
    # kodinkoneissa (src/services/kodinkoneet.js).

    @staticmethod
    def _luku(x, ala, yla):
        try:
            n = int(round(float(x)))
        except Exception:  # noqa: BLE001  ei luku = ei mittausta
            return None
        return n if ala <= n <= yla else None

    @staticmethod
    def _leima(x):
        """ZHA:n `last_seen` on epoch-sekunteja tai datetime. ISO ulos, tai None."""
        if x is None:
            return None
        try:
            if hasattr(x, "isoformat"):
                return x.isoformat()
            from datetime import datetime, timezone
            return datetime.fromtimestamp(float(x), tz=timezone.utc).isoformat()
        except Exception:  # noqa: BLE001  aika on tieto, ei edellytys
            return None

    def _zha_gateway(self):
        """ZHA:n portti, tai None. Kolme tunnettua tapaa, uusin ensin."""
        try:
            from homeassistant.components.zha.helpers import get_zha_gateway
            gw = get_zha_gateway(self.hass)
            if gw is not None:
                return gw
        except Exception:  # noqa: BLE001  vanha ZHA tai ei asennettu
            pass
        try:
            data = (getattr(self.hass, "data", None) or {}).get("zha") or {}
            gw = data.get("zha_gateway") if isinstance(data, dict) else None
            if gw is not None:
                return gw
        except Exception:  # noqa: BLE001
            pass
        return None

    @staticmethod
    def _zha_tyyppi(laite):
        """koordinaattori | vahvistin | paate | None. Kysytään monta tapaa."""
        for nimi, arvo in (("is_coordinator", "koordinaattori"),
                           ("is_router", "vahvistin"),
                           ("is_end_device", "paate")):
            try:
                if getattr(laite, nimi, None):
                    return arvo
            except Exception:  # noqa: BLE001
                pass
        try:
            t = str(_arvo(getattr(laite, "device_type", None)) or "").lower()
        except Exception:  # noqa: BLE001
            return None
        if "coordinator" in t:
            return "koordinaattori"
        if "router" in t:
            return "vahvistin"
        if "end" in t:
            return "paate"
        return None

    def _radiot_zha(self, dr, alueet):
        """ZHA:n portista. Palauttaa (solmut, kartta ieee→id) tai None."""
        gw = self._zha_gateway()
        if gw is None:
            return None
        try:
            laitteet = list((getattr(gw, "devices", None) or {}).values())
        except Exception:  # noqa: BLE001
            return None
        if not laitteet:
            return None
        solmut, idlla = [], {}
        for z in laitteet:
            ieee = str(_arvo(getattr(z, "ieee", None)) or "")
            if not ieee:
                continue
            laite = None
            try:
                laite = dr.async_get_device(identifiers={("zha", ieee)})
            except Exception:  # noqa: BLE001  rekisteri on lisätieto, ei edellytys
                laite = None
            tyyppi = self._zha_tyyppi(z)
            s = {
                "ieee": ieee,
                "id": laite.id if laite is not None else None,
                "nimi": str((laite.name_by_user if laite is not None else None)
                            or (getattr(z, "name", None) or "")
                            or ieee)[:80],
                "huone": alueet.get(laite.area_id) if laite is not None else None,
                "valmistaja": str(getattr(z, "manufacturer", None) or "")[:60] or None,
                "malli": str(getattr(z, "model", None) or "")[:60] or None,
                "tyyppi": tyyppi or "tuntematon",
                "tyyppi_laatu": "mitattu" if tyyppi else "ei_tietoa",
                "lqi": self._luku(getattr(z, "lqi", None), 0, 255),
                "rssi": self._luku(getattr(z, "rssi", None), -128, 20),
                "saatavilla": bool(getattr(z, "available", False)) if hasattr(z, "available") else None,
                "viimeksi": self._leima(getattr(z, "last_seen", None)),
                "isanta": None,
                "lapsia": 0,
                "paristo": None,
            }
            solmut.append(s)
            idlla[ieee] = s
        # Naapurit: lapsi-suhde kertoo MINKÄ vahvistimen kautta laite tulee.
        for z in laitteet:
            emo = str(_arvo(getattr(z, "ieee", None)) or "")
            try:
                naapurit = list(getattr(z, "neighbors", None) or ())
            except Exception:  # noqa: BLE001  naapurit puuttuvat osassa versioita
                naapurit = []
            for n in naapurit:
                try:
                    suhde = str(_arvo(getattr(n, "relationship", None)) or "").lower()
                    lapsi = str(_arvo(getattr(n, "ieee", None)) or "")
                except Exception:  # noqa: BLE001
                    continue
                if "child" not in suhde or lapsi not in idlla or lapsi == emo:
                    continue
                idlla[lapsi]["isanta"] = emo
        for s in solmut:
            if s["isanta"] and s["isanta"] in idlla:
                idlla[s["isanta"]]["lapsia"] += 1
        return solmut

    def _radiot_rekisteri(self, dr, er, alueet):
        """Varatie: laiterekisteri + LQI/RSSI-entiteetit. Tyyppi on PÄÄTTELY."""
        zha_entryt = {e.entry_id for e in self.hass.config_entries.async_entries("zha")}
        if not zha_entryt:
            return []
        tilat = {}
        paristot = set()
        for ent in er.entities.values():
            if not ent.device_id:
                continue
            nimi = str(ent.entity_id or "")
            if nimi.endswith("_lqi") or nimi.endswith("_rssi"):
                tila = self.hass.states.get(nimi)
                if tila is not None:
                    tilat.setdefault(ent.device_id, {})["rssi" if nimi.endswith("_rssi") else "lqi"] = tila.state
            elif nimi.endswith("_battery") or nimi.endswith("_battery_level"):
                paristot.add(ent.device_id)
        solmut = []
        for d in list(dr.devices.values()):
            if not (set(d.config_entries or ()) & zha_entryt):
                continue
            mitat = tilat.get(d.id) or {}
            paristo = d.id in paristot
            solmut.append({
                "ieee": None,
                "id": d.id,
                "nimi": str(d.name_by_user or d.name or "")[:80],
                "huone": alueet.get(d.area_id),
                "valmistaja": str(d.manufacturer or "")[:60] or None,
                "malli": str(d.model or "")[:60] or None,
                # 🔴 PÄÄTTELY, EI MITTAUS. Verkkovirtalaite EI ole takuulla
                # reititin, ja siksi tyyppi jää «tuntematon» eikä «vahvistin».
                "tyyppi": "paate" if paristo else "tuntematon",
                "tyyppi_laatu": "paattely" if paristo else "ei_tietoa",
                "lqi": self._luku(mitat.get("lqi"), 0, 255),
                "rssi": self._luku(mitat.get("rssi"), -128, 20),
                "saatavilla": None,
                "viimeksi": None,
                # Rekisterin via_device osoittaa koordinaattoriin.
                "isanta": d.via_device_id or None,
                "lapsia": 0,
                "paristo": paristo,
            })
        # Koordinaattori: ZHA-laite johon muut osoittavat via_device'lla.
        osoitetut = {s["isanta"] for s in solmut if s["isanta"]}
        for s in solmut:
            if s["id"] in osoitetut:
                s["tyyppi"] = "koordinaattori"
                s["tyyppi_laatu"] = "paattely"
                s["isanta"] = None
                s["lapsia"] = sum(1 for x in solmut if x["isanta"] == s["id"])
        return solmut

    def _muut_radiot(self):
        """Thread-, Matter- ja SMLIGHT-merkinnät: oma radio ilman Zigbee-solmuja."""
        ulos = []
        for e in self.hass.config_entries.async_entries():
            if e.domain not in ("thread", "otbr", "matter", "smlight"):
                continue
            ulos.append({
                "domain": e.domain,
                "otsikko": str(e.title or "")[:80],
                "tila": str(_arvo(e.state)),
                "poissa_kaytosta": bool(getattr(e, "disabled_by", None)),
            })
        return ulos

    async def _radiot(self, _tiedot):
        from homeassistant.helpers import area_registry, device_registry, entity_registry

        dr = device_registry.async_get(self.hass)
        er = entity_registry.async_get(self.hass)
        ar = area_registry.async_get(self.hass)
        alueet = {a.id: a.name for a in ar.async_list_areas()}

        zigbee = bool(self.hass.config_entries.async_entries("zha"))
        solmut, lahde, syy = [], None, None
        if zigbee:
            try:
                solmut = self._radiot_zha(dr, alueet)
            except Exception as e:  # noqa: BLE001  portti on lisäetu, ei edellytys
                _LOGGER.debug("Klevra-radiot: ZHA-portti ei auennut: %s", e)
                solmut = None
            if solmut:
                lahde = "zha"
            else:
                solmut = self._radiot_rekisteri(dr, er, alueet)
                lahde = "rekisteri"
                syy = "zha_portti_ei_auennut"
        else:
            syy = "zigbee_ei_kaytossa"

        solmut = solmut or []
        if len(solmut) > HALLINTA_LAITE_KATTO:
            solmut = solmut[:HALLINTA_LAITE_KATTO]
        solmut.sort(key=lambda s: ({"koordinaattori": 0, "vahvistin": 1, "paate": 2}.get(s["tyyppi"], 3),
                                   (s["huone"] or "~"), s["nimi"].lower()))
        return {
            "lahde": lahde,
            "syy": syy,
            "zigbee": zigbee,
            "solmut": solmut,
            "muut": self._muut_radiot(),
        }

    # ── Automaatiot Home Assistantiin ────────────────────────────────────────
    #
    # 🏠 [Niko 19.11.2026 klo 07.31: «ohjauksissa käytetään Home Assistant …
    #  te vain hallitsette, jolloin Home Assistant toimii lähiverkossa
    #  automaattisesti eikä ole riippuvainen internetyhteydestä».]
    #
    # 🔴 TÄMÄ ON SE KOHTA JOSSA KLEVRA LAKKAA OLEMASTA PAKOLLINEN VÄLIKÄSI.
    # Klevran oma ajastin pyörii Renderissä (`src/routes/auto/alykoti.js`:
    # minuuttiajastin). Nettikatkossa se ei laukea — se on sama riippuvuus
    # kuin Shelly Cloud, vain eri pilvi. Tänne kirjoitettu automaatio ajetaan
    # Raspberryllä ja laukeaa vaikka operaattori olisi alhaalla.
    #
    # 🔴 KLEVRA KOSKEE VAIN OMIIN RIVEIHINSÄ. Jokainen kirjoitettu automaatio
    # saa tunnuksen `klevra_<tunnus>`. Luku–muokkaus–kirjoitus säilyttää
    # KAIKKI muut rivit sellaisenaan: käyttäjän omat automaatiot ovat hänen,
    # ja niiden hiljainen ylikirjoitus olisi pahin mahdollinen vika tässä
    # tiedostossa — se ei näy mistään ennen kuin jokin lakkaa toimimasta.

    AUTOMAATIO_ETULIITE = "klevra_"
    AUTOMAATIO_KATTO = 100
    VIIKONPAIVAT = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")

    def _automaatiotiedosto(self):
        return self.hass.config.path("automations.yaml")

    def _lue_automaatiot(self):
        """Koko automations.yaml listana. Puuttuva tiedosto = tyhjä lista."""
        import os
        polku = self._automaatiotiedosto()
        if not os.path.exists(polku):
            return []
        import yaml
        with open(polku, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
        if data is None:
            return []
        if not isinstance(data, list):
            # 🔴 EI YLIKIRJOITETA SITÄ MITÄ EI YMMÄRRETÄ. Jos tiedosto on
            # jotain muuta kuin lista, käyttäjä on tehnyt sille jotain —
            # silloin tästä ei kirjoiteta mitään.
            raise Hylatty("automaatiotiedosto_ei_lista")
        return data

    def _kirjoita_automaatiot(self, rivit):
        import os
        import yaml
        polku = self._automaatiotiedosto()
        vara = polku + ".klevra-vara"
        # 🔴 VARMUUSKOPIO ENNEN KIRJOITUSTA. Jos jokin menee pieleen, käyttäjän
        # omat automaatiot ovat yhä levyllä yhden nimenvaihdon päässä.
        if os.path.exists(polku):
            import shutil
            shutil.copyfile(polku, vara)
        tmp = polku + ".klevra-tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            yaml.safe_dump(rivit, f, allow_unicode=True, sort_keys=False, default_flow_style=False)
        os.replace(tmp, polku)

    def _automaatio_yaml(self, tiedot):
        """Klevran ajastus → Home Assistantin automaatio. Tarkistettu, ei arvattu."""
        tunnus = str(tiedot.get("tunnus") or "").strip()
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,40}", tunnus):
            raise Hylatty("tunnus_ei_kelpaa")
        nimi = str(tiedot.get("nimi") or "Klevra-ajastus")[:80]

        # 🔴 KERTA-AJASTUS: PÄIVÄ LUKITSEE AUTOMAATION YHTEEN VUOROKAUTEEN.
        # Home Assistantin `platform: time` laukeaa JOKA päivä. Ilman tätä
        # lukitusta «pese tänä yönä» pesisi joka yö, hiljaa, kunnes käyttäjä
        # huomaisi sen sähkölaskusta. Päivä tarkistetaan muotoa myöten, koska
        # se menee ehtolausekkeeseen: mikä tahansa muu merkki kuin numero tai
        # väliviiva hylätään ennen kuin se päätyy templaattiin.
        paiva = tiedot.get("paiva")
        kertaehto = None
        if paiva is not None:
            paiva = str(paiva).strip()
            if not re.fullmatch(r"20\d{2}-(0[1-9]|1[0-2])-(0[1-9]|[12]\d|3[01])", paiva):
                raise Hylatty("paiva_ei_kelpaa")
            kertaehto = {
                "condition": "template",
                "value_template": "{{ now().strftime('%%Y-%%m-%%d') == '%s' }}" % paiva,
            }

        paivat = tiedot.get("paivat")
        # 🔴 KERTA JA TOISTO OVAT ERI ASIOITA, EIKÄ NIITÄ YHDISTETÄ. Jos sama
        # paketti kantaisi sekä päivämäärän että viikonpäivälistan, toinen
        # kertoisi «kerran» ja toinen «joka tiistai» — ja kumpi voittaa
        # selviäisi vasta siitä kumpi laukesi.
        if kertaehto is not None and paivat is not None:
            raise Hylatty("kerta_ja_toisto_yhdessa")
        if paivat is None:
            viikonpaivat = list(self.VIIKONPAIVAT)
        else:
            if not isinstance(paivat, list) or not paivat:
                raise Hylatty("paivat_ei_kelpaa")
            try:
                numerot = sorted({int(x) for x in paivat})
            except Exception:  # noqa: BLE001
                raise Hylatty("paivat_ei_kelpaa")
            if any(n < 0 or n > 6 for n in numerot):
                raise Hylatty("paivat_ei_kelpaa")
            # 🔴 0 = SUNNUNTAI KLEVRASSA (JS getDay), HA:ssa viikko alkaa
            # maanantaista. Ilman tätä siirtoa sunnuntain ajastus laukeaisi
            # maanantaina — hiljaa ja joka viikko.
            viikonpaivat = [self.VIIKONPAIVAT[(n + 6) % 7] for n in numerot]

        laukaisu = {}
        aurinko = str(tiedot.get("aurinko") or "")
        if aurinko in ("nousu", "lasku"):
            siirto = self._luku(tiedot.get("siirto_min"), -720, 720) or 0
            # 🔴 ETUMERKKI KIRJOITETAAN AINA. Home Assistant lukee etumerkittömän
            # arvon positiivisena, joten «01:30:00» toimisi — mutta ihminen joka
            # avaa automations.yaml:n ei näe siitä kumpaan suuntaan siirto menee.
            # Mitattu `test/automaatio-kotiin.py`:llä 19.11.2026.
            merkki = "-" if siirto < 0 else "+"
            a = abs(int(siirto))
            laukaisu = {"platform": "sun",
                        "event": "sunrise" if aurinko == "nousu" else "sunset",
                        "offset": "%s%02d:%02d:00" % (merkki, a // 60, a % 60)}
        else:
            kello = str(tiedot.get("kello") or "")
            if not re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", kello):
                raise Hylatty("kello_ei_kelpaa")
            laukaisu = {"platform": "time", "at": kello + ":00"}

        toiminnot = tiedot.get("toiminnot")
        if not isinstance(toiminnot, list) or not toiminnot:
            raise Hylatty("ei_toimintoja")
        if len(toiminnot) > 30:
            raise Hylatty("liikaa_toimintoja")
        askeleet = []
        for t in toiminnot:
            if not isinstance(t, dict):
                raise Hylatty("toiminto_ei_kelpaa")
            entiteetti = str(t.get("entity_id") or "")
            palvelu = str(t.get("palvelu") or "")
            # 🔴 SAMA PORTTI KUIN SUORALLA KOMENNOLLA. Automaatio on komento
            # joka ajetaan myöhemmin; sen tarkistus ei saa olla löysempi.
            pari, syy = tarkista_palvelu(entiteetti, palvelu)
            if pari is None:
                raise Hylatty(syy)
            domain, nimi_p = pari
            askeleet.append({
                "service": "%s.%s" % (domain, nimi_p),
                "target": {"entity_id": entiteetti},
                "data": {k: v for k, v in (t.get("data") or {}).items()
                         if k not in KIELLETYT_KENTAT},
            })

        return {
            "id": self.AUTOMAATIO_ETULIITE + tunnus,
            "alias": nimi,
            # 🔴 KUVAUS KERTOO OMISTAJAN. Käyttäjä, joka avaa Home Assistantin
            # automaatiolistan, näkee heti mikä rivi on Klevran kirjoittama ja
            # mikä hänen omansa.
            "description": ("Klevran kirjoittama kerta-ajastus %s. Muokkaa Klevrassa — tämä rivi korvataan." % paiva
                            if kertaehto is not None
                            else "Klevran kirjoittama. Muokkaa Klevrassa — tämä rivi korvataan."),
            "mode": "single",
            "trigger": [laukaisu],
            "condition": ([kertaehto] if kertaehto is not None
                          else [{"condition": "time", "weekday": viikonpaivat}]
                          if len(viikonpaivat) < 7 else []),
            "action": askeleet,
        }

    async def _lataa_automaatiot(self):
        await self.hass.services.async_call("automation", "reload", blocking=True)

    async def _automaatio_kirjoita(self, tiedot):
        uusi = self._automaatio_yaml(tiedot)
        rivit = await self.hass.async_add_executor_job(self._lue_automaatiot)
        omat = [r for r in rivit if isinstance(r, dict)
                and str(r.get("id") or "").startswith(self.AUTOMAATIO_ETULIITE)]
        korvattu = False
        ulos = []
        for r in rivit:
            if isinstance(r, dict) and r.get("id") == uusi["id"]:
                ulos.append(uusi)
                korvattu = True
            else:
                ulos.append(r)
        if not korvattu:
            if len(omat) >= self.AUTOMAATIO_KATTO:
                raise Hylatty("liikaa_automaatioita")
            ulos.append(uusi)
        await self.hass.async_add_executor_job(self._kirjoita_automaatiot, ulos)
        await self._lataa_automaatiot()
        return {"id": uusi["id"], "korvattu": korvattu, "yhteensa": len(ulos),
                "klevran": len(omat) + (0 if korvattu else 1)}

    def _paikallinen_paiva(self, hetki=None):
        """Tämä päivä Home Assistantin omassa aikavyöhykkeessä.

        🔴 EI UTC. Kerta-ajastuksen ehto vertaa HA:n `now()`:ta, joka on
        paikallinen. Jos vanhentuminen laskettaisiin UTC:stä, Suomen talvessa
        kahden tunnin ja kesässä kolmen tunnin ikkuna vuorokauden vaihteessa
        kertoisi väärää — ja juuri siinä ikkunassa yöpesut ajastetaan.

        🔴 HETKI TULEE PARAMETRINA, KOSKA MUUTEN TÄTÄ EI VOI MITATA. Mitattu
        19.11.2026: UTC-sabotaasi EI osunut, koska vartija ajettiin klo 10
        Suomen aikaa — silloin UTC ja Suomi ovat samassa päivässä, ja väärä
        toteutus näytti oikealta. Vartija on siis vihreä väärästä syystä 22
        tuntia vuorokaudesta. Nyt vyöhyke-ero mitataan hetkellä, jossa se on
        olemassa: 00.30 Suomen aikaa on UTC:ssa edellinen päivä.
        """
        from datetime import datetime, timezone
        vyohyke = None
        try:
            nimi = getattr(getattr(self.hass, "config", None), "time_zone", None)
            if nimi:
                from zoneinfo import ZoneInfo
                vyohyke = ZoneInfo(str(nimi))
        except Exception:  # noqa: BLE001
            # Tuntematon vyöhykenimi: UTC on huonompi mutta ei väärempi kuin kaatuminen.
            vyohyke = None
        kello = hetki if hetki is not None else datetime.now(timezone.utc)
        return kello.astimezone(vyohyke or timezone.utc).strftime("%Y-%m-%d")

    async def _automaatiot(self, _tiedot):
        """Mitä Home Assistantissa on — Klevran omat eriteltynä."""
        try:
            rivit = await self.hass.async_add_executor_job(self._lue_automaatiot)
        except Hylatty:
            raise
        klevran, muut = [], 0
        for r in rivit:
            if not isinstance(r, dict):
                continue
            tid = str(r.get("id") or "")
            if tid.startswith(self.AUTOMAATIO_ETULIITE):
                tila = self.hass.states.get("automation." + str(r.get("alias") or "")
                                            .lower().replace(" ", "_"))
                # 🔴 KERTA-AJASTUS KERROTAAN, JOTTA SE VOIDAAN SIIVOTA. Päivän
                # jälkeen rivi on inertti: se ei laukea, mutta se jää
                # `automations.yaml`:iin ja täyttää katon (100). Ilman tätä
                # kenttää Klevra ei voisi tietää mikä rivi on vanhentunut, ja
                # käyttäjän lista kasvaisi kuolleista automaatioista.
                kerta = None
                for ehto in (r.get("condition") or []):
                    if not isinstance(ehto, dict):
                        continue
                    osuma = re.search(r"==\s*'(20\d{2}-\d{2}-\d{2})'",
                                      str(ehto.get("value_template") or ""))
                    if osuma:
                        kerta = osuma.group(1)
                        break
                klevran.append({
                    "id": tid,
                    "tunnus": tid[len(self.AUTOMAATIO_ETULIITE):],
                    "nimi": str(r.get("alias") or "")[:80],
                    "paalla": (tila.state == "on") if tila is not None else None,
                    "kerta_paiva": kerta,
                    "vanhentunut": (kerta is not None
                                    and kerta < self._paikallinen_paiva()),
                })
            else:
                muut += 1
        # 🔴 KERROTAAN JOS LATAUS EI VOI TOIMIA. Jos `automation:` ei ole
        # mukana configuration.yaml:ssä, tiedostoon kirjoitettu rivi ei päädy
        # mihinkään — ja Klevra näyttäisi onnistuneen.
        palvelu_on = self.hass.services.has_service("automation", "reload")
        return {"klevran": klevran, "muita": muut, "lataus_mahdollinen": bool(palvelu_on),
                "tiedosto": self._automaatiotiedosto()}

    async def _automaatio_poista(self, tiedot):
        tunnus = str(tiedot.get("tunnus") or "").strip()
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,40}", tunnus):
            raise Hylatty("tunnus_ei_kelpaa")
        kohde = self.AUTOMAATIO_ETULIITE + tunnus
        rivit = await self.hass.async_add_executor_job(self._lue_automaatiot)
        jaljelle = [r for r in rivit
                    if not (isinstance(r, dict) and r.get("id") == kohde)]
        if len(jaljelle) == len(rivit):
            raise Hylatty("automaatiota_ei_loydy")
        await self.hass.async_add_executor_job(self._kirjoita_automaatiot, jaljelle)
        await self._lataa_automaatiot()
        return {"id": kohde, "poistettu": True, "yhteensa": len(jaljelle)}

    # ── Verkkoon kytketyn radion etsintä ─────────────────────────────────────
    #
    # 📡 [Niko 19.11.2026 klo 06.11: «Miten tämän pystyisi automatisoimaan että
    #  se löytää oikean osoitteen nyt se ei löydä koti verkostosta laitetta».]
    #
    # 🔴 EI VERKKOSKANNAUSTA. Osoite on jo Home Assistantissa: SMLIGHT-
    # integraatio löysi SLZB-MR2:n mDNS:llä ja TALLENSI hostin omaan
    # merkintäänsä. Skannaus olisi toinen, epävarmempi lähde samaan tietoon —
    # ja skannaus kodin verkossa on asia jota lisäosa ei tee ilman pyyntöä.
    #
    # 🔴 ZHA:N «Select a serial port» NÄYTTÄÄ VAIN USB-TIKUT. Siksi lista on
    # Nikon kuvassa tyhjä vaikka radio on verkossa ja HA puhuu sille. Tämä
    # toiminto tuottaa sen yhden rivin jota ZHA kaipaa: socket://IP:6638.

    # Verkkoradion oletusportti ja -asetukset. SMLIGHTin oma ohje (SLZB-OS):
    # Zigbee-soketti 6638, EFR32MG21 = EZSP, oletusnopeus 115200.
    RADIO_PORTTI = 6638
    RADIO_TYYPPI = "ezsp"
    RADIO_NOPEUS = 115200
    # Integraatiot joiden merkinnässä on verkkoon kytketyn Zigbee-radion host.
    RADIO_DOMAINIT = ("smlight",)

    @staticmethod
    def _host_merkinnasta(e):
        """Hostin nimi config entryn datasta. Eri integraatiot, eri avain."""
        data = dict(getattr(e, "data", None) or {})
        for avain in ("host", "ip_address", "address", "hostname"):
            arvo = str(data.get(avain) or "").strip()
            if arvo:
                return arvo[:80]
        return None

    async def _etsi_radio(self, _tiedot):
        from homeassistant.helpers import device_registry, entity_registry

        dr = device_registry.async_get(self.hass)
        er = entity_registry.async_get(self.hass)
        zha_kaytossa = bool(self.hass.config_entries.async_entries("zha"))
        loydot = []
        for e in self.hass.config_entries.async_entries():
            if e.domain not in self.RADIO_DOMAINIT:
                continue
            host = self._host_merkinnasta(e)
            # Radion omat mittaukset: laiteohjelma, radion tila, käyttöaika.
            mittaukset = []
            try:
                laitteet = [d for d in dr.devices.values() if e.entry_id in (d.config_entries or ())]
            except Exception:  # noqa: BLE001  rekisteri on lisätieto
                laitteet = []
            laite_idt = {d.id for d in laitteet}
            for ent in er.entities.values():
                if ent.device_id not in laite_idt or len(mittaukset) >= 30:
                    continue
                tila = self.hass.states.get(ent.entity_id)
                if tila is None:
                    continue
                mittaukset.append({
                    "entity_id": ent.entity_id,
                    "nimi": str((tila.attributes or {}).get("friendly_name") or ent.entity_id)[:80],
                    "arvo": str(tila.state)[:60],
                })
            loydot.append({
                "domain": e.domain,
                "entry_id": e.entry_id,
                "otsikko": str(e.title or "")[:80],
                "tila": str(_arvo(e.state)),
                "host": host,
                "laite": (str(laitteet[0].name_by_user or laitteet[0].name or "")[:80]
                          if laitteet else None),
                "malli": (str(laitteet[0].model or "")[:60] or None) if laitteet else None,
                # 🔴 EHDOTUS, EI TOTUUS. Portti ja nopeus ovat SMLIGHTin
                # oletuksia; jos käyttäjä on vaihtanut ne laitteen omalta
                # sivulta, ehdotus on väärä — siksi se on esitäyte, ei komento.
                "ehdotus": ({
                    "polku": "socket://%s:%d" % (host, self.RADIO_PORTTI),
                    "radiotyyppi": self.RADIO_TYYPPI,
                    "nopeus": self.RADIO_NOPEUS,
                } if host else None),
                "mittaukset": mittaukset,
            })
        # mDNS-löydöt joita kukaan ei ole vielä lisännyt.
        odottaa = [f for f in self._loydetyt_lista() if f.get("domain") in self.RADIO_DOMAINIT]
        return {
            "zha_kaytossa": zha_kaytossa,
            "zha_keskitin": bool(self.hass.config_entries.async_entries("zha")) and any(
                dict(getattr(e, "data", None) or {}).get("device")
                for e in self.hass.config_entries.async_entries("zha")),
            "loydot": loydot,
            "odottaa": odottaa,
            "syy": None if loydot or odottaa else "ei_verkkoradiota",
        }

    # ═══ VAIHE 2: HACS ═══════════════════════════════════════════════════════
    #
    # 🔴 HACS:llä ei ole palvelukutsuja, vain websocket-komennot, joita sen oma
    # käyttöliittymä käyttää. Teemme saman kuin ne: `hass.data["hacs"]`,
    # `async_register_repository`, `async_download_repository`. Nämä ovat HACSin
    # sisäisiä, joten jokainen kutsu on suojattu ja virhe palautetaan sanoin.

    def _hacs(self):
        hacs = (getattr(self.hass, "data", None) or {}).get("hacs")
        if hacs is None:
            raise Hylatty("hacs_puuttuu")
        return hacs

    @staticmethod
    def _hacs_repo(hacs, nimi):
        try:
            return hacs.repositories.get_by_full_name(nimi)
        except Exception:  # noqa: BLE001  ei löydy = None, sama kuin HACSilla
            return None

    async def _hacs_tila(self, _tiedot):
        hacs = (getattr(self.hass, "data", None) or {}).get("hacs")
        repot = []
        for nimi, domain in HACS_KATALOGI.items():
            r = self._hacs_repo(hacs, nimi) if hacs is not None else None
            d = getattr(r, "data", None)
            repot.append({
                "repo": nimi,
                "domain": domain,
                "ladattu": bool(getattr(d, "installed", False)),
                "versio": getattr(d, "installed_version", None),
                "uusin": getattr(d, "last_version", None),
                # Ladattu mutta ei vielä ladattu Home Assistantiin = uudelleenkäynnistys puuttuu.
                "kaytossa": domain in set(getattr(self.hass.config, "components", set()) or set())
                or bool(self.hass.config_entries.async_entries(domain)),
            })
        return {"hacs": hacs is not None, "repot": repot}

    async def _hacs_lataa(self, tiedot):
        nimi = str(tiedot.get("repo") or "")
        if nimi not in HACS_KATALOGI:
            raise Hylatty("repo_ei_luettelossa")
        hacs = self._hacs()
        r = self._hacs_repo(hacs, nimi)
        if r is None:
            await hacs.async_register_repository(repository_full_name=nimi, category="integration")
            r = self._hacs_repo(hacs, nimi)
        if r is None:
            raise Hylatty("hacs_ei_loytanyt")
        lataa = getattr(r, "async_download_repository", None) or getattr(r, "async_install", None)
        if lataa is None:
            raise Hylatty("hacs_versio_ei_tuettu")
        await lataa()
        try:
            await hacs.data.async_write()
        except Exception:  # noqa: BLE001  HACSin oma tallennus; lataus on jo levyllä
            pass
        return {"ladattu": nimi, "domain": HACS_KATALOGI[nimi], "vaatii_uudelleenkaynnistyksen": True}

    async def _kaynnista_uudelleen(self, _tiedot):
        """🔴 VIIVEELLÄ. Tulos lähtee Klevraan ennen kuin Home Assistant sammuu;
        muuten sovellus odottaisi vastausta, jota ei koskaan tule."""
        async def kaynnista():
            await self.hass.services.async_call("homeassistant", "restart", {}, blocking=False)

        self.hass.loop.call_later(5, lambda: self.hass.async_create_task(kaynnista()))
        return {"kaynnistyy_s": 5}

    # ═══ VAIHE 3: PÄIVITYKSET, SOVELLUKSET, VARMUUSKOPIOT ═══════════════════

    async def _paivitykset(self, _tiedot):
        paivitykset, varmuus = [], {}
        for st in self.hass.states.async_all():
            eid = st.entity_id
            if eid.startswith("update."):
                a = st.attributes
                paivitykset.append({
                    "entiteetti": eid,
                    "nimi": str(a.get("title") or a.get("friendly_name") or eid)[:80],
                    "saatavilla": st.state == "on",
                    "versio": a.get("installed_version"),
                    "uusin": a.get("latest_version"),
                    "kaynnissa": bool(a.get("in_progress")),
                    "ohitettu": a.get("skipped_version"),
                    "tiedotteet": a.get("release_url"),
                    "varmuuskopio": bool(int(a.get("supported_features") or 0) & 8),
                })
            elif eid.startswith("sensor.backup_"):
                varmuus[eid[len("sensor.backup_"):]] = st.state
        paivitykset.sort(key=lambda x: (not x["saatavilla"], x["nimi"].lower()))
        return {"paivitykset": paivitykset, "varmuuskopiot": varmuus}

    async def _paivita(self, tiedot):
        eid = str(tiedot.get("entiteetti") or "")
        st = self.hass.states.get(eid) if eid.startswith("update.") else None
        if st is None:
            raise Hylatty("paivitysta_ei_loydy")
        if st.state != "on":
            raise Hylatty("ei_paivitettavaa")
        data = {"entity_id": eid}
        # 🔴 Varmuuskopio ennen päivitystä aina kun laite sen tukee.
        if int(st.attributes.get("supported_features") or 0) & 8:
            data["backup"] = True
        await self.hass.services.async_call("update", "install", data, blocking=False)
        return {"aloitettu": eid, "varmuuskopio": "backup" in data}

    def _sovellushallinta(self, slug):
        if "hassio" not in set(getattr(self.hass.config, "components", set()) or set()):
            raise Hylatty("ei_supervisoria")
        from homeassistant.components.hassio import AddonManager

        return AddonManager(self.hass, _LOGGER, slug, slug)

    async def _sovellukset(self, _tiedot):
        if "hassio" not in set(getattr(self.hass.config, "components", set()) or set()):
            return {"supervisor": False, "sovellukset": []}
        ulos = []
        for slug in SOVELLUS_KATALOGI:
            try:
                info = await self._sovellushallinta(slug).async_get_addon_info()
                ulos.append({"slug": slug, "tila": str(_arvo(info.state)), "versio": info.version,
                             "paivitys": bool(info.update_available)})
            except Exception as e:  # noqa: BLE001  yksi sovellus ei kaada listaa
                ulos.append({"slug": slug, "tila": "tuntematon", "virhe": str(e)[:120]})
        return {"supervisor": True, "sovellukset": ulos}

    async def _asenna_sovellus(self, tiedot):
        slug = str(tiedot.get("slug") or "")
        if slug not in SOVELLUS_KATALOGI:
            raise Hylatty("sovellus_ei_luettelossa")
        m = self._sovellushallinta(slug)
        info = await m.async_get_addon_info()
        tila = str(_arvo(info.state))
        if tila == "not_installed":
            await m.async_install_addon()
        if tila != "running":
            await m.async_start_addon()
        return {"slug": slug, "oli": tila, "kaynnissa": True}

    async def _varmuuskopio(self, _tiedot):
        if self.hass.services.has_service("backup", "create_automatic"):
            await self.hass.services.async_call("backup", "create_automatic", {}, blocking=False)
            return {"aloitettu": "backup.create_automatic"}
        if self.hass.services.has_service("hassio", "backup_full"):
            await self.hass.services.async_call("hassio", "backup_full", {"name": "Klevra"}, blocking=False)
            return {"aloitettu": "hassio.backup_full"}
        raise Hylatty("varmuuskopio_ei_kaytossa")
