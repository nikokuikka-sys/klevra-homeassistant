# Näin viet tämän GitHubiin

Tämä kansio **on** julkisen repon sisältö sellaisenaan. Ei alikansiota, ei
siirtelyä: `hacs.json`, `README.md` ja `custom_components/klevra/` tulevat repon
juureen.

---

## 1. Luo repo GitHubissa

**github.com → New repository**

| | |
|---|---|
| Nimi | `klevra-homeassistant` |
| Näkyvyys | 🔴 **Public** |
| README / .gitignore / lisenssi | **älä lisää mitään** — repo jää tyhjäksi |

🔴 **Julkinen, ei yksityinen.** HACS hakee tiedot `api.github.com`-rajapinnasta
ilman oikeuksiasi ja saa yksityisestä reposta **404** silloinkin kun olet itse sen
omistaja. Juuri tämä virhe tuli 14.11.2026 klo 06.58:

```
GitHub returned 404 for https://api.github.com/repos/nikokuikka-sys/laskuhelposti
```

Osoite ei ollut väärin. Repo oli yksityinen.

---

## 2. Vie sisältö

Pura `klevra-homeassistant.zip` tyhjään kansioon ja aja komennot **sen sisällä**:

```bash
git init
git add .
git commit -m "Klevra Home -integraatio Home Assistantille"
git branch -M main
git remote add origin https://github.com/nikokuikka-sys/klevra-homeassistant.git
git push -u origin main
```

Tarkista lopuksi, että repon **juuressa** näkyy `hacs.json`, `README.md` ja
`custom_components/` — jos ne ovat alikansion sisällä, HACS ei löydä integraatiota.

---

## 3. Asenna HACSissa

**HACS → Integraatiot → ⋮ → Mukautetut tietovarastot**

| | |
|---|---|
| Tietovarasto | `https://github.com/nikokuikka-sys/klevra-homeassistant` |
| Luokka | Integration |

Etsi **Klevra Home** → **Lataa** → käynnistä Home Assistant uudelleen.

Sitten: **Asetukset → Laitteet ja palvelut → Lisää integraatio → Klevra Home**,
ja liitä siltatunnus sovelluksesta (**Kodin asetukset → 🏠 Home Assistant**).

---

## Miksi tämä ei mene Ohjaamon repoon

Ohjaamo asuu reposssa `kiwahome/HomeAssistant`. HACS olettaa **yhden integraation
per mukautettu tietovarasto**: se etsii `custom_components/`-kansion alta yhden
alikansion. Kaksi integraatiota samassa reposssa jättää valinnan HACSin varaan,
eikä sitä voi ohjata `hacs.json`:ista.

Siksi sama kuvio kuin ennenkin: **yksi repo per integraatio.** Ohjaamo ja Klevra
voivat olla asennettuina yhtä aikaa samaan Home Assistantiin — eri tunnus, eri
otsake, eri palvelin, eivät häiritse toisiaan.

---

## Päivittäminen myöhemmin

Kun lisäosa muuttuu `laskuhelposti`-reposssa, muutos **ei siirry tänne itsestään**.
Nosta versio `custom_components/klevra/manifest.json`:issa ja vie muutos samalla
tavalla:

```bash
git add . && git commit -m "Klevra-lisäosa <versio>" && git push
```

HACS tarjoaa päivitystä vasta kun **version numero kasvaa** — pelkkä uusi commit
ei riitä.
