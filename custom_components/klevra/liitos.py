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

# 💳 ESIPROVISIOIDUN KORTIN OMAT TIEDOT. [Niko 19.11.2026 klo 14.55]
#
# 🔴 TÄMÄ ON SE KOLMAS LÄHDE, JOKA POISTAA AGENTIN TARPEEN. Tämän moduulin
# alkuperäinen suunnitelma (yllä, askel 4) odotti laitteen omaa agenttia, joka
# kirjoittaa tunnuksen tiedostoon palvelimen työnnöstä. Mitattu 19.11.2026:
#   • agenttia EI ole tässä repossa eikä missään
#   • muokkaamattomaan Home Assistant OS:iin EI voi lisätä palvelua, joten
#     agenttia ei voi edes asentaa ilman että käyttöjärjestelmästä tehdään
#     oma haara — ja silloin me omistamme sen turvapäivitykset
#   • laite on asiakkaan NATin takana, joten palvelin ei voi työntää sille
#     mitään; tiedon on kuljettava VEDOLLA
#
# Siksi integraatio kysyy itse. Kortille kirjoitetaan kirjoitushetkellä
# `laite` + `salaisuus`, ja tämä moduuli kysyy palvelimelta «olenko liitetty».
# Ei agenttia, ei käyttöjärjestelmän muokkausta, ei työntöä.
#
# 🔴 SALAISUUS EI MENE LOKIIN EIKÄ TARRAAN. Tarrassa on lunastuskoodi, joka on
# eri asia: se ei anna tunnusta, se vain sitoo laitteen asiakkaan kotiin.
LAITE_TIEDOSTOT = (
    "/config/klevra-laite.json",
    "/share/klevra/laite.json",
    "/data/klevra-laite.json",
)
POLKU_KORTTI = "/api/kortti/tila"

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


def lue_laitetiedot(tiedostot=None, ymparisto=None):
    """Kortille kirjoitetut laitetiedot: {laite, salaisuus, palvelin}.

    🔴 EI OSITTAISTA. Jos kortilta löytyy laitetunnus ilman salaisuutta (tai
    toisin päin), sitä ei palauteta puolivalmiina: palvelinkysely ilman
    salaisuutta ei voi onnistua, ja puolivalmis arvo saisi vahdin kysymään
    turhaan ikuisesti.
    """
    ymp = os.environ if ymparisto is None else ymparisto
    laite = str(ymp.get("KLEVRA_LAITE") or "").strip()
    salaisuus = str(ymp.get("KLEVRA_SALAISUUS") or "").strip()
    if laite and salaisuus:
        return {
            "laite": laite,
            "salaisuus": salaisuus,
            "palvelin": siisti_palvelin(ymp.get("KLEVRA_PALVELIN")) or OLETUS_PALVELIN,
            "lahde": "ymparisto:KLEVRA_LAITE",
        }
    polut = list(LAITE_TIEDOSTOT if tiedostot is None else tiedostot)
    oma = ymp.get("KLEVRA_LAITE_TIEDOSTO")
    if oma:
        polut.insert(0, oma)
    for polku in polut:
        try:
            with open(polku, encoding="utf-8") as f:
                d = json.load(f)
        except (OSError, ValueError):
            continue
        if not isinstance(d, dict):
            continue
        laite = str(d.get("laite") or d.get("laite_tunnus") or "").strip()
        salaisuus = str(d.get("salaisuus") or "").strip()
        if not laite or not salaisuus:
            continue
        return {
            "laite": laite,
            "salaisuus": salaisuus,
            "palvelin": siisti_palvelin(d.get("palvelin")) or OLETUS_PALVELIN,
            "lahde": polku,
        }
    return None


def tallenna_palvelin(laitetiedot, uusi_osoite):
    """Kirjoita uusi palvelinosoite laitetiedostoon. True jos onnistui.

    🔴 SIVUVAIKUTUS, EI EHTO. Jos kirjoitus ei onnistu, liitos tehdään silti
    vanhalla osoitteella — ja uusi yritetään taas seuraavalla kierroksella.
    Kaatuva kirjoitus ei saa estää liittymistä.

    🔴 SALAISUUS SÄILYY, EIKÄ SITÄ LOKITETA. Tiedosto kirjoitetaan kokonaan
    uudelleen, joten salaisuus on mukana — mutta vain tiedostossa.
    """
    polku = laitetiedot.get("lahde")
    if not polku or not str(polku).startswith("/"):
        # Ympäristömuuttujasta tullutta ei kirjoiteta levylle: se on kokeilu,
        # ja sen ylikirjoittaminen tiedostoksi yllättäisi kokeilijan.
        return False
    try:
        with open(polku, "w", encoding="utf-8") as f:
            json.dump({
                "laite": laitetiedot["laite"],
                "salaisuus": laitetiedot["salaisuus"],
                "palvelin": uusi_osoite,
                "_ohje": "Klevra Home -laitetunniste. Palvelinosoite paivitetty automaattisesti.",
            }, f, indent=2)
        laitetiedot["palvelin"] = uusi_osoite
        return True
    except OSError:
        _LOGGER.warning("Klevra: uutta palvelinosoitetta ei voitu kirjoittaa tiedostoon %s", polku)
        return False


async def nouda_palvelimelta(hass, laitetiedot, hae=None):
    """Kysy palvelimelta onko kortti liitetty. Palauttaa liitoksen tai None.

    🔴 EI MITÄÄN EI OLE VIRHE. Liittämätön kortti saa `odottaa`, ja se on
    normaali tila viikkoja: laite voi seisoa varastossa. Siksi tästä ei
    nouse virhettä eikä lokiin tule rivi joka kierroksella.
    """
    if not laitetiedot:
        return None
    osoite = laitetiedot["palvelin"].rstrip("/") + POLKU_KORTTI
    runko = {"laite": laitetiedot["laite"], "salaisuus": laitetiedot["salaisuus"]}
    try:
        if hae is not None:
            d = await hae(osoite, runko)
        else:
            from homeassistant.helpers.aiohttp_client import async_get_clientsession

            istunto = async_get_clientsession(hass)
            vastaus = await istunto.post(osoite, json=runko, timeout=20)
            if vastaus.status != 200:
                return None
            d = await vastaus.json()
    except Exception:  # noqa: BLE001
        # Sivuvaikutus: verkko on poikki tai palvelin alhaalla. Kortti yrittää
        # uudelleen seuraavalla kierroksella; tämä ei saa kaataa käynnistystä.
        return None
    # ═══ 🌐 PALVELIN VAIHTUI ════════════════════════════════════════════════
    # [Niko 19.11.2026 klo 16.44: «ne tullaan vaihtamaan myöhemmin
    #  tulevaisuudessa».]
    #
    # 🔴 KORTILLE LEIVOTTU OSOITE ON PYSYVÄ PÄÄTÖS. Kortti kysyy sitä vuosia.
    # Jos domain vaihtuu eikä laite voi oppia uutta, kentällä oleva kortti
    # lakkaa liittymästä — ja se ei näy mistään: kortti vain odottaa.
    #
    # Siksi vastaus voi kertoa uuden osoitteen. Laite kirjoittaa sen omaan
    # tiedostoonsa ja käyttää sitä jatkossa.
    #
    # 🔴 TÄMÄ ON OHJAUSPRIMITIIVI, JOTEN SE ON RAJATTU KOLMELLA TAVALLA:
    #   1. vain https (muuten kaappausreitti: kerran väärään vastaukseen
    #      uskova kortti olisi pysyvästi väärällä palvelimella)
    #   2. uusi osoite EI korvaa vanhaa muistissa tällä kierroksella — vanha
    #      jää varalle, jos uusi ei vastaa
    #   3. kirjoitus levylle on sivuvaikutus: jos se ei onnistu (levy täynnä,
    #      kirjoitussuojattu), liitos tehdään silti vanhalla osoitteella
    if isinstance(d, dict):
        uusi_osoite = siisti_palvelin(d.get("palvelin_uusi"))
        if uusi_osoite and uusi_osoite.startswith("https://") and uusi_osoite != laitetiedot["palvelin"]:
            _LOGGER.info("Klevra: palvelin siirtyi osoitteeseen %s", uusi_osoite)
            tallenna_palvelin(laitetiedot, uusi_osoite)

    if not isinstance(d, dict) or d.get("tila") != "liitetty":
        return None
    tunnus = siisti_tunnus(d.get("tunnus"))
    if not tunnus:
        # 🔴 «liitetty» ilman kelvollista tunnusta on palvelimen vika, ei
        # liitos. Sitä ei saa tulkita onnistumiseksi: asennus syntyisi
        # tunnuksella jota ei ole, ja se näyttäisi toimivalta.
        _LOGGER.warning("Klevra: palvelin sanoi liitetty mutta tunnus ei kelpaa")
        return None
    return {
        "tunnus": tunnus,
        "palvelin": siisti_palvelin(d.get("palvelin")) or laitetiedot["palvelin"],
        "lahde": "kortti:" + str(laitetiedot["laite"]),
    }


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


async def vahdi(hass, tiedostot=None, ymparisto=None, vali=VALI_S, katto=VALI_KATTO_S,
                kierroksia=None, laitetiedostot=None, hae=None):
    """Odota tunnusta ja liity heti kun se tulee. Palauttaa kierrosten määrän.

    Kaksi lähdettä, tässä järjestyksessä joka kierroksella:

      1. PAIKALLINEN TUNNUS (`lue_liitos`) — ympäristömuuttuja tai tiedosto.
         Nopea, ei vaadi verkkoa, ja sillä voi kokeilla korttia käsin.
      2. 💳 KORTIN OMA KYSELY (`nouda_palvelimelta`) — laitetunnus ja
         salaisuus kortilta, vastaus palvelimelta.

    🔴 PAIKALLINEN ENSIN, JA SE ON PÄÄTÖS. Jos kortille on käsin kirjoitettu
    tunnus (vianetsintä, koeajo), se voittaa palvelimen vastauksen. Muuten
    käsin tehty korjaus ei vaikuttaisi mihinkään, ja korjaaja luulisi
    tehneensä jotain.

    🔴 VERKKOKYSELY EI SAA HIDASTAA ENSIMMÄISTÄ KIERROSTA TURHAAN: jos
    laitetietoja ei ole kortilla lainkaan, kyselyä ei tehdä kertaakaan.

    `kierroksia` ja `hae` ovat vartijaa varten: ilman niitä vahti jatkaa
    kunnes liitos syntyy ja käyttää HA:n omaa istuntoa.
    """
    v = vali
    n = 0
    laitetiedot = lue_laitetiedot(laitetiedostot, ymparisto)
    while kierroksia is None or n < kierroksia:
        n += 1
        if jo_asennettu(hass):
            return n
        liitos = lue_liitos(tiedostot, ymparisto)
        if liitos is None and laitetiedot is not None:
            liitos = await nouda_palvelimelta(hass, laitetiedot, hae=hae)
        if liitos and await liita(hass, liitos):
            pyyhi_tunnus(liitos["lahde"] if str(liitos["lahde"]).startswith("/") else None)
            return n
        if kierroksia is not None and n >= kierroksia:
            return n
        await asyncio.sleep(v)
        v = min(v * 2, katto)
    return n
