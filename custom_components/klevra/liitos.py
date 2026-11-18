"""
🪪 LIITTYMINEN KORTILTA — tunnus tulee ulkoa, kukaan ei avaa Home Assistantia.
═══════════════════════════════════════════════════════════════════════════════

Niko 18.11.2026: «asennus tapahtuu ilman koskemista home assistant ja tehdään
sovelluksesta käsin tällä qr koodilla tai pelkällä koodilla ilman että tarvitsee
kirjautua sisään home assistant ollenkaan vaan se on rakennettu tähän
muistikortille tulevaan asennustiedostoon».

🔴 MITÄ TÄTÄ ENNEN OLI (mitattu 18.11.2026, lisäosa 1.3.0): siltatunnus meni
perille VAIN kahdella tavalla —

    config_flow.py            HA:n lomake: «Lisää integraatio → Klevra Home»
    async_setup (YAML)        configuration.yaml:in muokkaus

Kummassakin joku avaa Home Assistantin. Kortti ei voi tehdä kumpaakaan, koska
kortin kirjoitushetkellä kiinteistöä — eikä siis tunnusta — ole vielä olemassa:
Niko skannaa QR:n vasta kun laite on asiakkaan nurkassa.

🔴 TÄMÄ MODUULI ON SE KOLMAS POLKU, eikä se korvaa kahta ensimmäistä. Ketju on:

    1. Kortti käynnistyy. Klevra-integraatio latautuu ILMAN tunnusta ja jää
       vahtimaan (`vahdi`). Mitään ei ole vielä liitetty.
    2. Niko skannaa tarran QR:n Klevra-sovelluksessa (/api/klevra-hub/liita).
    3. Owella Hub tietää mikä laite koodiin kuuluu ja lähettää laitteelle
       TÄMÄN kiinteistön siltatunnuksen.
    4. Laitteen oma agentti kirjoittaa tunnuksen tiedostoon (`LIITOS_TIEDOSTOT`)
       tai ympäristömuuttujaan.
    5. Tämä vahti huomaa sen ≤ `VALI_S` sekunnissa, tarkistaa tunnuksen
       palvelimelta ja luo asennuksen itse (`flow.async_init`, source=import).
    6. Tunnus pyyhitään tiedostosta heti kun asennus on syntynyt.

Askel 4 tapahtuu laitteen agentissa, joka EI ole tässä repossa. Siksi tämä
moduuli lukee KOLMEA paikkaa: agentti ei joudu arvaamaan oikeaa, ja kortin
kuvaa voi kokeilla pelkällä ympäristömuuttujalla ennen kuin agenttia on.

🔴 TUNNUSTA EI KIRJOITETA LOKIIN. Lokiin menee vain lähde ja pituus. Sama syy
kuin palvelimen päässä: `koti_silta` pitää tunnuksesta vain tiivisteen.
"""
import asyncio
import json
import logging
import os
import re

from .const import DOMAIN, OLETUS_PALVELIN

_LOGGER = logging.getLogger(__name__)

# Paikat järjestyksessä. `/config` on HA:n oma kansio (säilyy päivityksissä),
# `/share` on add-onien yhteinen, `/data` on add-onin oma.
LIITOS_TIEDOSTOT = (
    "/config/klevra-liitos.json",
    "/share/klevra/liitos.json",
    "/data/klevra-liitos.json",
)

# Vahdin väli. Alkaa tästä ja venyy kattoon, jos mitään ei löydy: laite voi
# seisoa nurkassa viikon ennen kuin QR skannataan, eikä se saa jauhaa turhaan.
VALI_S = 10
VALI_KATTO_S = 120

# 🔴 TUNNUKSEN MUOTO EI OLE KIINNITETTY ETULIITTEESEEN. Mitattu palvelimelta
# 18.11.2026: tunnus on `klv_` + base64url(24) = 36 merkkiä (testitunnus
# `klv_testi_` + 32 = 42). Jos vartija vaatisi juuri `klv_`-alkua, etuliitteen
# vaihto palvelimessa rikkoisi liittymisen hiljaa — ja se on juuri se vika,
# jonka sääntö 7 kieltää. Tarkistetaan siis vain se mikä pitää paikkansa
# kaikille: merkkijoukko ja järkevä pituus.
TUNNUS_RE = re.compile(r"^[A-Za-z0-9._:\-]{16,300}$")
PALVELIN_RE = re.compile(r"^https?://[A-Za-z0-9._\-]+(:\d{1,5})?(/[^\s]*)?$")


def siisti_tunnus(x):
    """Tunnus tai None. Ei nosta poikkeusta millään syötteellä."""
    if not isinstance(x, str):
        return None
    t = x.strip()
    return t if TUNNUS_RE.match(t) else None


def siisti_palvelin(x):
    """Osoite tai None. Loppukenoviiva pois, jotta polut eivät tuplaudu."""
    if not isinstance(x, str):
        return None
    p = x.strip().rstrip("/")
    return p if PALVELIN_RE.match(p) else None


def lue_tiedosto(polku):
    """Yksi tiedosto. Puuttuva, rikki tai vajaa tiedosto = None, ei virhe."""
    try:
        with open(polku, "r", encoding="utf-8") as f:
            d = json.load(f)
    except (OSError, ValueError):
        # Sivuvaikutus: tiedostoa ei ole tai se on kesken kirjoitusta. Vahti
        # kysyy uudelleen `VALI_S`:n päästä — hiljaisuus on tässä oikea vastaus.
        return None
    if not isinstance(d, dict):
        return None
    tunnus = siisti_tunnus(d.get("tunnus") or d.get("token"))
    if not tunnus:
        return None
    return {
        "tunnus": tunnus,
        "palvelin": siisti_palvelin(d.get("palvelin") or d.get("server")) or OLETUS_PALVELIN,
        "lahde": polku,
    }


def lue_liitos(tiedostot=None, ymparisto=None):
    """Ensimmäinen kelpaava lähde: ympäristömuuttuja, sitten tiedostot.

    Ympäristö on ensin, jotta kortin kuvaa voi kokeilla ilman agenttia ja jotta
    kokeilu voittaa levylle jääneen vanhan tiedoston.
    """
    ymp = os.environ if ymparisto is None else ymparisto
    tunnus = siisti_tunnus(ymp.get("KLEVRA_TUNNUS"))
    if tunnus:
        return {
            "tunnus": tunnus,
            "palvelin": siisti_palvelin(ymp.get("KLEVRA_PALVELIN")) or OLETUS_PALVELIN,
            "lahde": "ymparisto:KLEVRA_TUNNUS",
        }
    polut = list(LIITOS_TIEDOSTOT if tiedostot is None else tiedostot)
    oma = ymp.get("KLEVRA_LIITOS_TIEDOSTO")
    if oma:
        polut.insert(0, oma)
    for p in polut:
        osuma = lue_tiedosto(p)
        if osuma:
            return osuma
    return None


def pyyhi_tunnus(polku):
    """Tunnus pois levyltä kun asennus on syntynyt. True jos pyyhittiin.

    🔴 Tiedostoa EI poisteta vaan korvataan kuittauksella: laitteen agentti saa
    nähdä että liitos onnistui, eikä se yritä uudelleen. Jos levy on
    kirjoitussuojattu, se ei ole virhe — asennus on silti olemassa.
    """
    if not polku or not str(polku).startswith("/"):
        return False
    try:
        with open(polku, "w", encoding="utf-8") as f:
            json.dump({"liitetty": True, "tunnus": None}, f)
        return True
    except OSError:
        # Sivuvaikutus: kirjoitus ei onnistunut. Asennus on jo tehty, joten
        # tämä ei saa kaataa käynnistystä. Kerrotaan lokissa, ei nostaen.
        _LOGGER.warning("Klevra: liitostiedostoa %s ei voitu pyyhkiä", polku)
        return False


def jo_asennettu(hass):
    """Onko Klevra jo liitetty. Toinen asennus lähettäisi datan tuplana."""
    try:
        return bool(hass.config_entries.async_entries(DOMAIN))
    except Exception:  # pragma: no cover - HA:n sisäinen muutos
        return False


async def liita(hass, liitos):
    """Luo asennus itse. Palauttaa True jos asennus syntyi tai on jo olemassa."""
    if jo_asennettu(hass):
        return True
    tulos = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": "import"},
        data={"tunnus": liitos["tunnus"], "palvelin": liitos["palvelin"]},
    )
    tyyppi = tulos.get("type") if isinstance(tulos, dict) else None
    # 🔴 «abort» kelpaa vain kun syy on että sama silta on jo asennettu.
    # Muu abort (esim. tunnus ei kelpaa) ei ole onnistuminen: vahti jatkaa.
    if tyyppi == "create_entry":
        _LOGGER.info("Klevra: liitettiin kortilta (lähde %s)", liitos.get("lahde"))
        return True
    if tyyppi == "abort" and tulos.get("reason") == "already_configured":
        return True
    _LOGGER.warning(
        "Klevra: liitos lähteestä %s ei onnistunut (%s/%s) — yritetään uudelleen",
        liitos.get("lahde"), tyyppi, (tulos or {}).get("reason") or (tulos or {}).get("errors"),
    )
    return False


async def vahdi(hass, tiedostot=None, ymparisto=None, vali=VALI_S, katto=VALI_KATTO_S, kierroksia=None):
    """Odota tunnusta ja liity heti kun se tulee. Palauttaa kierrosten määrän.

    `kierroksia` on vartijaa varten: ilman sitä vahti jatkaa kunnes liitos
    syntyy. Tuotannossa tämä on taustatehtävä, joka päättyy kun kortti on
    liitetty — tai kun Home Assistant sammuu.
    """
    v = vali
    n = 0
    while kierroksia is None or n < kierroksia:
        n += 1
        if jo_asennettu(hass):
            return n
        liitos = lue_liitos(tiedostot, ymparisto)
        if liitos and await liita(hass, liitos):
            pyyhi_tunnus(liitos["lahde"] if str(liitos["lahde"]).startswith("/") else None)
            return n
        if kierroksia is not None and n >= kierroksia:
            return n
        await asyncio.sleep(v)
        v = min(v * 2, katto)
    return n
