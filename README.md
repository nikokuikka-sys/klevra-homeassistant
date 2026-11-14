# Klevra Home — Home Assistant -integraatio

Yhdistä Home Assistant **Klevra Homeen** ilman pilvipalvelua.
Ei Nabu Casaa, ei porttiohjausta, ei VPN:ää.

---

## Miten tämä toimii

Home Assistant on kotonasi, reitittimen takana. Klevra on internetissä.

**Klevra ei voi soittaa kotiisi** — se vaatisi porttiohjauksen tai VPN:n, ja molemmat
avaavat kotiverkkoosi reiän.

Siksi suunta on käännetty: **Home Assistant lähettää tiedot ulospäin**, samoin kuin
selain avaa sivun. Kotiverkkoosi ei avata mitään.

```
   KOTI                              INTERNET
 ┌─────────────────┐               ┌──────────────┐
 │ Home Assistant  │  ──────────▶  │    Klevra    │
 │                 │  ◀──────────  │              │
 └─────────────────┘  sama pyyntö  └──────────────┘
   ei avattuja portteja
```

🔴 **Nuoli osoittaa takaisin, ja se on tämän integraation koko pointti.**
Home Assistant lähettää tilat ja **jää odottamaan samaan pyyntöön** 55 sekunniksi.
Kun painat Klevrassa nappia, odottava pyyntö vastaa **samalla hetkellä** — ei
seuraavalla kierroksella. Lampun sytyttäminen kestää alle sekunnin, ei puolta
minuuttia.

---

## Mitä tämä tuo Klevraan

| | |
|---|---|
| **Laitteet huoneineen** | Huone luetaan Home Assistantin rekisteristä, jota sen oma rajapinta ei edes näytä |
| **Laitteiden kyvyt** | Himmennys, värilämpötila, väri, tavoitelämpötila, lukko, kaihtimen asento, puhaltimen nopeus |
| **Säätöluvut ja valinnat** | Esim. sähköauton laturin latausvirta (6–16 A) ja sen tila — **rajat tulevat laitteelta**, ei arvauksesta |
| **Painikkeet** | Shellyn ja muiden laitteiden toiminnot |
| **Mittaukset** | Lämpötila, kosteus, teho, energia, ovet ja liike |

Asetus- ja diagnostiikkaentiteetit (signaalin voimakkuus, laiteohjelman versio,
uudelleenkäynnistysviive) **jätetään pois** — muuten yksi Shelly täyttäisi laitelistan
kymmenellä rivillä joita ei ohjata.

---

## Asennus

### 1. Lisää tietovarasto HACSiin

**HACS → Integraatiot → ⋮ → Mukautetut tietovarastot**

| | |
|---|---|
| Tietovarasto | tämän repon osoite |
| Luokka | Integration |

🔴 **Repon on oltava JULKINEN.** HACS ei näe yksityisiä repoja: se hakee tiedot
`api.github.com`-rajapinnasta, joka vastaa yksityiselle repolle **404** silloinkin kun
olet itse sen omistaja. Virhe näyttää tältä:

```
GitHub returned 404 for https://api.github.com/repos/<omistaja>/<repo>
```

Se ei tarkoita että osoite olisi väärin — se tarkoittaa että repo on yksityinen.

### 2. Asenna

Etsi **Klevra Home** → **Lataa** → käynnistä Home Assistant uudelleen.

### 3. Yhdistä

**Asetukset → Laitteet ja palvelut → Lisää integraatio → Klevra Home**

Liitä siltatunnus. Löydät sen sovelluksesta:
**Kodin asetukset → 🏠 Home Assistant**. Samalta sivulta saat palvelimen osoitteen
valmiina.

Tunnus tarkistetaan heti: jos se on väärä, se kerrotaan dialogissa — ei tuntia
myöhemmin lokissa.

---

## Tämä ei korvaa Ohjaamo-integraatiota

Klevra ja Ohjaamo ovat eri tuotteita, ja niillä on **omat integraationsa, omat
tunnuksensa ja omat palvelimensa**. Ne voivat olla asennettuina yhtä aikaa samaan
Home Assistantiin eivätkä häiritse toisiaan.

| | Ohjaamo | Klevra Home |
|---|---|---|
| Integraation tunnus | `ohjaamo` | `klevra` |
| Otsake | `X-Ohjaamo-Silta` | `X-Klevra-Silta` |
| Attribuutit | ei lähetetä | lähetetään |
| Komennot | neljä avainta | palvelu + tiedot, domainkohtainen lista |
| Kysely | 30 s kiinteä | pitkä kysely, vastaus heti |

---

## Turvallisuus

**Mitä tämä lähettää:** entiteettien tilat, nimet, huoneet ja attribuutit —
vain niistä laitelajeista jotka Klevra osaa näyttää. Ei kuvia, ei soittolistoja,
ei pääsytokeneita.

**Mitä tämä suorittaa:** vain palvelukutsuja, jotka ovat integraation omalla
**domainkohtaisella sallitulla listalla** (`const.py`). Vapaa palvelukutsu olisi
etäkomentorivi kodissa — `shell_command` ja `hassio.host_reboot` ovat myös
palveluita. Lisäksi:

- palvelun domainin on vastattava entiteetin domainia, joten
  `climate.set_temperature` ei voi osua vedenlämmittimeen
- `entity_id` asetetaan **paikallisesti** komennon entiteetistä, joten yksi komento
  ei voi osua koko kotiin (`entity_id: all`)
- `device_id`, `area_id` ja `label_id` eivät kulje palvelimelta läpi

**Tunnuksen voi perua milloin tahansa** sovelluksesta. Perumisen jälkeen lisäosa saa
401:n, lopettaa lähettämisen ja kertoo lokissa mistä uusi tunnus haetaan.

---

## Vianetsintä

| Oire | Syy |
|---|---|
| `GitHub returned 404` HACSissa | repo on yksityinen — ks. yllä |
| «Tunnus ei kelvannut» | tunnus kopioitui vajaana, tai se on peruttu sovelluksesta |
| «Palvelin ei vastannut» | palvelimen osoite väärin; kopioi se sovelluksen sivulta |
| Laitteet näkyvät mutta eivät ohjaudu | käytössä on **vain luku -testitunnus**, joka ei saa komentoja |
| Laite näkyy lukemana jota ei voi ohjata | laite ei kerro kykyjään; katso sen attribuutit Home Assistantin kehittäjätyökaluista |
