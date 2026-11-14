"""
Klevran asennusdialogi (Config Flow).

Asennus on: Asetukset → Laitteet ja palvelut → Lisää integraatio → Klevra Home
→ liitä siltatunnus. Tiedostoja ei kopioida eikä configuration.yaml:ia muokata.

🔴 TUNNUS TARKISTETAAN HETI. Jos tunnus on väärä, se kerrotaan dialogissa — ei
tuntia myöhemmin lokissa. Tarkistus käyttää samaa `GET /api/silta/tila`-reittiä
jonka palvelin tarjoaa, eikä omaa tarkistusreittiä: yksi reitti vähemmän
rikkoutuvaa pintaa.
"""
import voluptuous as vol

from homeassistant import config_entries
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import DOMAIN, OLETUS_PALVELIN, OTSAKE, POLKU_TILA


class KlevraConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Asennus käyttöliittymästä."""

    VERSION = 1

    async def async_step_user(self, user_input=None):
        virheet = {}

        if user_input is not None:
            tunnus = user_input["tunnus"].strip()
            palvelin = user_input.get("palvelin", OLETUS_PALVELIN).rstrip("/")

            tulos = await self._tarkista(palvelin, tunnus)
            if tulos.get("ok"):
                # Sama silta ei saa olla kahdesti — muuten dataa lähetettäisiin tuplana.
                await self.async_set_unique_id(tunnus[:16])
                self._abort_if_unique_id_configured()
                return self.async_create_entry(
                    title="Klevra Home",
                    data={"tunnus": tunnus, "palvelin": palvelin},
                )
            virheet["base"] = tulos.get("syy") or "yhteysvirhe"

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required("tunnus"): str,
                    vol.Optional("palvelin", default=OLETUS_PALVELIN): str,
                }
            ),
            errors=virheet,
        )

    async def _tarkista(self, palvelin, tunnus):
        """Varmista että tunnus kelpaa ennen kuin integraatio luodaan."""
        try:
            istunto = async_get_clientsession(self.hass)
            vastaus = await istunto.get(
                f"{palvelin}{POLKU_TILA}",
                headers={OTSAKE: tunnus},
                timeout=15,
            )
            if vastaus.status == 401:
                return {"ok": False, "syy": "tunnus_ei_kelpaa"}
            if vastaus.status != 200:
                return {"ok": False, "syy": "palvelin_ei_vastaa"}
            return await vastaus.json()
        except Exception:  # noqa: BLE001
            return {"ok": False, "syy": "yhteysvirhe"}
