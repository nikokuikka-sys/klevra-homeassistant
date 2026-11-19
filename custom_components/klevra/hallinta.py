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
