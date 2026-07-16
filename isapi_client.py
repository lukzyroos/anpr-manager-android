"""
isapi_client.py
----------------
Klient komunikujący się z kamerami Hikvision ANPR poprzez ISAPI
(Intelligent Security API) do zarządzania listą tablic rejestracyjnych
(allowlist/blocklist).

STAN WIEDZY O FORMACIE (zweryfikowane przez przechwycenie prawdziwych
zapytań panelu WWW kamery w Chrome DevTools -> Network - to jest
najpewniejsze źródło, jakie mieliśmy, silniejsze niż oficjalna
dokumentacja ISAPI, która dla tego firmware okazała się niekompletna
lub niespójna):

  DODAWANIE tablicy - JSON:
    PUT /ISAPI/Traffic/channels/<ID>/licensePlateAuditData/record?format=json
    {"LicensePlateInfoList":[{"LicensePlate":"...","listType":"whiteList",
      "createTime":"...","effectiveStartDate":"...","effectiveTime":"...","id":""}]}
    (potwierdzone: HTTP 200 OK)

  WYSZUKIWANIE (przeglądanie listy) - CZYSTY XML, NIE JSON:
    POST /ISAPI/Traffic/channels/<ID>/searchLPListAudit
    Content-Type: application/xml
    <?xml version="1.0" encoding="UTF-8"?>
    <LPListAuditSearchDescription>
      <searchID>{uuid}</searchID>
      <maxResults>{n}</maxResults>
      <searchResultPosition>{pos}</searchResultPosition>
    </LPListAuditSearchDescription>
    Odpowiedź też XML, np. (pusty wynik):
    <LPListAuditSearchResult>
      <searchID>...</searchID>
      <responseStatus>true</responseStatus>
      <responseStatusStrg>NO MATCHES</responseStatusStrg>
      <numOfMatches>0</numOfMatches>
      <totalMatches>0</totalMatches>
      <LicensePlateInfoList></LicensePlateInfoList>
    </LPListAuditSearchResult>
    (potwierdzone: HTTP 200 OK; struktura WYPEŁNIONEGO wyniku - z realnymi
    rekordami - nie jest jeszcze potwierdzona, bo test był na pustej liście.
    Parsowanie poniżej jest napisane elastycznie pod tym kątem.)

  USUWANIE - JSON (mimo że Chrome DevTools nazwał to "Form Data", treść
  była surowym JSON-em - potwierdzone błędem kamery przy próbie wysłania
  tego jako prawdziwy x-www-form-urlencoded), po polu "id" rekordu (tablica):
    PUT /ISAPI/Traffic/channels/<ID>/DelLicensePlateAuditData?format=json
    {"id":[],"deleteAllEnabled":true}      (przykład przechwycony to akcja
                                             "usuń wszystko")
    Do usunięcia POJEDYNCZEJ tablicy potrzebne jest jej "id" (nadawane przez
    kamerę przy dodaniu) - dlatego delete_plate() najpierw wyszukuje tablicę
    po numerze, żeby poznać jej id, a dopiero potem wysyła
    {"id":[<id>],"deleteAllEnabled":false}. Ten dokładny wariant (usunięcie
    JEDNEJ tablicy) nie został jeszcze przechwycony wprost - jeśli nie
    zadziała, najpewniejszym krokiem będzie przechwycenie w DevTools
    usunięcia pojedynczego wiersza (bez "zaznacz wszystkie").
"""

import json
import uuid
import re
import xml.etree.ElementTree as ET
import requests
from requests.auth import HTTPDigestAuth, HTTPBasicAuth
from dataclasses import dataclass
from typing import Optional, List, Dict, Any
from datetime import datetime
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# ============================================================
# MAPOWANIE PÓL - patrz nagłówek pliku po szczegóły źródła
# ============================================================
FIELD_PLATE_NUMBER = "LicensePlate"   # potwierdzone przechwyconym zapytaniem (duże litery!)
FIELD_LIST_TYPE = "listType"          # potwierdzone przechwyconym zapytaniem

LIST_TYPE_ALLOW = "whiteList"   # tablica dozwolona (whitelist) - potwierdzone przechwyceniem
LIST_TYPE_BLOCK = "blackList"   # tablica zablokowana (blacklist) - zakładane analogicznie do whiteList
# ============================================================


class ISAPIError(Exception):
    """Błąd komunikacji z kamerą lub odpowiedzi ISAPI."""
    def __init__(self, message: str, raw_response: Optional[str] = None):
        super().__init__(message)
        self.raw_response = raw_response


@dataclass
class CameraProfile:
    name: str
    ip: str
    port: int
    username: str
    password: str
    use_https: bool = False
    channel_id: str = "1"

    @property
    def base_url(self) -> str:
        scheme = "https" if self.use_https else "http"
        return f"{scheme}://{self.ip}:{self.port}"


class ISAPIClient:
    """Klient do komunikacji ISAPI z pojedynczą kamerą."""

    def __init__(self, camera: CameraProfile, timeout: float = 8.0, log_callback=None):
        self.camera = camera
        self.timeout = timeout
        self.log_callback = log_callback  # funkcja(str) do wypisywania logów w GUI
        self._auth = None  # wykryty sposób autoryzacji (Digest/Basic), cache

    def _log(self, msg: str):
        if self.log_callback:
            self.log_callback(f"[{self.camera.name}] {msg}")

    def _get_auth(self):
        """Zwraca wykrytą metodę autoryzacji, wykrywając ją przy pierwszym użyciu."""
        if self._auth is not None:
            return self._auth
        self._auth = HTTPDigestAuth(self.camera.username, self.camera.password)
        return self._auth

    def _request(self, method: str, path: str, json_body: Optional[dict] = None,
                 raw_data: Optional[str] = None, form_data: Optional[dict] = None,
                 headers: Optional[dict] = None, params: Optional[dict] = None) -> requests.Response:
        """Wykonuje zapytanie HTTP do kamery.

        json_body  - wysyła jako JSON (Content-Type: application/json)
        raw_data   - wysyła surowy tekst (np. ręcznie zbudowany XML)
        form_data  - wysyła jako application/x-www-form-urlencoded
        Tylko jeden z powyższych trzech powinien być użyty naraz.
        """
        url = f"{self.camera.base_url}{path}"
        auth = self._get_auth()

        def do_request(auth_obj):
            if json_body is not None:
                return requests.request(
                    method, url, auth=auth_obj, json=json_body, params=params,
                    timeout=self.timeout, verify=False, headers=headers
                )
            elif raw_data is not None:
                return requests.request(
                    method, url, auth=auth_obj, data=raw_data.encode("utf-8"), params=params,
                    timeout=self.timeout, verify=False, headers=headers
                )
            elif form_data is not None:
                return requests.request(
                    method, url, auth=auth_obj, data=form_data, params=params,
                    timeout=self.timeout, verify=False, headers=headers
                )
            else:
                return requests.request(
                    method, url, auth=auth_obj, params=params,
                    timeout=self.timeout, verify=False, headers=headers
                )

        try:
            resp = do_request(auth)
        except requests.exceptions.RequestException as e:
            raise ISAPIError(f"Błąd połączenia: {e}")

        if resp.status_code == 401 and isinstance(auth, HTTPDigestAuth):
            self._log("Digest auth nieudany, próbuję Basic auth...")
            self._auth = HTTPBasicAuth(self.camera.username, self.camera.password)
            try:
                resp = do_request(self._auth)
            except requests.exceptions.RequestException as e:
                raise ISAPIError(f"Błąd połączenia: {e}")

        self._log(f"{method} {path} -> HTTP {resp.status_code}")
        self._log(f"Odpowiedź: {resp.text[:2000]}")
        return resp

    # ------------------------------------------------------------------
    # Test połączenia
    # ------------------------------------------------------------------
    def test_connection(self) -> Dict[str, Any]:
        """Sprawdza połączenie i autoryzację pobierając informacje o urządzeniu."""
        resp = self._request("GET", "/ISAPI/System/deviceInfo")
        if resp.status_code == 200:
            try:
                return resp.json()
            except ValueError:
                return self._parse_device_info_xml(resp.text)
        raise ISAPIError(
            f"Nie udało się połączyć z kamerą (HTTP {resp.status_code}).",
            raw_response=resp.text
        )

    @staticmethod
    def _parse_device_info_xml(xml_text: str) -> Dict[str, Any]:
        """Wyciąga podstawowe pola z odpowiedzi XML (starsze firmware)."""
        fields = {}
        for tag in ("deviceName", "model", "serialNumber", "firmwareVersion", "macAddress"):
            m = re.search(rf"<{tag}>(.*?)</{tag}>", xml_text)
            if m:
                fields[tag] = m.group(1)
        return fields or {"raw": xml_text[:500]}

    # ------------------------------------------------------------------
    # Wyszukiwanie tablic (odczyt listy zapisanej w kamerze) - XML
    # ------------------------------------------------------------------
    def search_plates(self, list_type: Optional[str] = None, plate_number: str = "",
                       position: int = 0, max_results: int = 40) -> Dict[str, Any]:
        path = f"/ISAPI/Traffic/channels/{self.camera.channel_id}/searchLPListAudit"
        search_id = str(uuid.uuid4()).upper()

        xml_body = (
            '<?xml version="1.0" encoding="UTF-8"?>'
            "<LPListAuditSearchDescription>"
            f"<searchID>{search_id}</searchID>"
            f"<maxResults>{max_results}</maxResults>"
            f"<searchResultPosition>{position}</searchResultPosition>"
            "</LPListAuditSearchDescription>"
        )

        resp = self._request(
            "POST", path, raw_data=xml_body,
            headers={"Content-Type": "application/xml"}
        )

        if resp.status_code not in (200, 201):
            raise ISAPIError(
                f"Wyszukiwanie nie powiodło się (HTTP {resp.status_code}).",
                raw_response=resp.text
            )

        records, total = self._parse_search_xml(resp.text)

        # Filtrowanie po stronie aplikacji (kamera może nie wspierać
        # filtrowania po numerze/typie listy w tym zapytaniu XML).
        if plate_number:
            needle = plate_number.strip().lower()
            records = [r for r in records if needle in r.get("LicensePlate", "").lower()]
        if list_type:
            records = [r for r in records if r.get("listType") == list_type]

        return {"records": records, "total": total, "raw": resp.text}

    @staticmethod
    def _parse_search_xml(xml_text: str) -> "tuple[list, int]":
        """Parsuje odpowiedź XML z searchLPListAudit. Napisane elastycznie,
        bo nie mieliśmy jeszcze przechwyconego przykładu z realnymi
        rekordami (tylko pusty wynik) - obsługuje dowolną nazwę tagu
        elementu-rekordu wewnątrz <LicensePlateInfoList>, i dowolne
        nazwy pól wewnątrz rekordu (w tym warianty wielkości liter)."""
        records = []
        total = 0
        try:
            # Usuwamy namespace, żeby nie komplikować ścieżek ElementTree
            cleaned = re.sub(r'xmlns="[^"]+"', "", xml_text)
            root = ET.fromstring(cleaned)
        except ET.ParseError:
            return records, 0

        def local(tag):
            return tag.split("}")[-1]

        total_el = root.find(".//totalMatches")
        if total_el is not None and total_el.text:
            try:
                total = int(total_el.text)
            except ValueError:
                total = 0

        list_el = root.find(".//LicensePlateInfoList")
        if list_el is not None:
            for item in list(list_el):
                rec = {}
                # Atrybuty samego elementu-rekordu (np. <LicensePlateInfo listType="...">)
                for k, v in item.attrib.items():
                    rec[local(k)] = v
                for child in item.iter():
                    if child is item:
                        continue
                    # Atrybuty elementów zagnieżdżonych
                    for k, v in child.attrib.items():
                        rec.setdefault(local(k), v)
                    text = (child.text or "").strip()
                    if text:
                        rec[local(child.tag)] = text
                if rec:
                    records.append(rec)

        PLATE_KEY_VARIANTS = ("LicensePlate", "licensePlate", "plateNumber", "PlateNo", "plateNo")
        for rec in records:
            for k in PLATE_KEY_VARIANTS:
                if k in rec and rec[k]:
                    rec["LicensePlate"] = rec[k]
                    break

        ID_KEY_VARIANTS = ("id", "ID", "Id", "recordID", "listID")
        for rec in records:
            for k in ID_KEY_VARIANTS:
                if k in rec and rec[k] != "":
                    rec["id"] = rec[k]
                    break

        # Potwierdzone przechwyconą odpowiedzią (2026-07-16): kamera zwraca
        # typ listy pod tagiem <type>, NIE <listType>.
        LIST_TYPE_KEY_VARIANTS = ("type", "listType", "ListType")
        for rec in records:
            for k in LIST_TYPE_KEY_VARIANTS:
                if k in rec and rec[k]:
                    rec["listType"] = rec[k]
                    break

        if not total:
            total = len(records)

        return records, total

    # ------------------------------------------------------------------
    # Dodanie / edycja tablicy - JSON (potwierdzone przechwyconym zapytaniem)
    # ------------------------------------------------------------------
    def add_plate(self, plate_number: str, list_type: str, owner_name: str = "") -> Dict[str, Any]:
        path = f"/ISAPI/Traffic/channels/{self.camera.channel_id}/licensePlateAuditData/record"

        now = datetime.now()
        try:
            end_date = now.replace(year=now.year + 10)
        except ValueError:
            # 29 lutego w roku przestępnym - cofamy o 1 dzień, żeby uniknąć błędu
            end_date = now.replace(year=now.year + 10, day=28)

        entry = {
            FIELD_PLATE_NUMBER: plate_number,
            FIELD_LIST_TYPE: list_type,
            "createTime": now.strftime("%Y-%m-%dT%H:%M:%S"),
            "effectiveStartDate": now.strftime("%Y-%m-%d"),
            "effectiveTime": end_date.strftime("%Y-%m-%d"),  # "Effective End Date" w panelu kamery
            "id": "",
        }

        body = {"LicensePlateInfoList": [entry]}
        resp = self._request("PUT", path, json_body=body, params={"format": "json"})

        if resp.status_code not in (200, 201):
            raise ISAPIError(
                f"Dodanie tablicy nie powiodło się (HTTP {resp.status_code}).",
                raw_response=resp.text
            )
        try:
            return resp.json()
        except ValueError:
            return {"raw": resp.text}

    # ------------------------------------------------------------------
    # Usunięcie tablicy - dane formularza, po ID rekordu
    # ------------------------------------------------------------------
    def delete_plate(self, plate_number: str) -> Dict[str, Any]:
        """Usuwa tablicę po numerze: najpierw wyszukuje ją, żeby poznać jej
        wewnętrzne ID nadane przez kamerę, potem usuwa po tym ID.

        UWAGA: przechwycony wzorzec dotyczył akcji "usuń wszystko"
        ({"id":[],"deleteAllEnabled":true}). Usunięcie pojedynczej tablicy
        ({"id":[<id>],"deleteAllEnabled":false}) nie zostało jeszcze
        zweryfikowane wprost - jeśli to się nie powiedzie, najlepszym krokiem
        jest przechwycenie w DevTools usunięcia POJEDYNCZEGO wiersza (bez
        zaznaczania "zaznacz wszystkie") i przesłanie tej treści."""
        found = self._find_plate_id(plate_number)
        if found is None:
            raise ISAPIError(f"Nie znaleziono tablicy '{plate_number}' na liście kamery.")

        self._log(f"Znaleziony rekord do usunięcia: {found}")

        record_id = found.get("id", "")
        if record_id == "" or record_id is None:
            raise ISAPIError(
                f"Znaleziono tablicę '{plate_number}', ale nie udało się odczytać jej ID "
                f"z odpowiedzi kamery - usuwanie przerwane (nie wysłano żądania), żeby "
                f"nie ryzykować przypadkowego usunięcia całej listy. Zobacz surową odpowiedź "
                f"wyszukiwania w Logu powyżej i prześlij ją, żeby dobrać właściwą nazwę pola ID.",
                raw_response=str(found)
            )
        record_id_val = str(record_id)

        path = f"/ISAPI/Traffic/channels/{self.camera.channel_id}/DelLicensePlateAuditData"
        body = {
            "id": [record_id_val],
            "deleteAllEnabled": False,
        }
        resp = self._request("PUT", path, json_body=body, params={"format": "json"})

        if resp.status_code not in (200, 201):
            raise ISAPIError(
                f"Usunięcie tablicy nie powiodło się (HTTP {resp.status_code}).",
                raw_response=resp.text
            )

        # Kamera potrafi zwrócić HTTP 200 / "statusCode":1,"OK" nawet gdy nic
        # faktycznie nie usunęła (np. przy niedopasowanym formacie ID) - dlatego
        # nie ufamy samej odpowiedzi i sprawdzamy stan listy jeszcze raz.
        self._log("Weryfikacja: sprawdzam, czy tablica faktycznie zniknęła z listy...")
        still_there = self._find_plate_id(plate_number)
        if still_there is not None:
            raise ISAPIError(
                f"Kamera zgłosiła powodzenie (HTTP 200, statusCode OK), ale tablica "
                f"'{plate_number}' nadal jest na liście po ponownym sprawdzeniu. "
                f"Wysłane ID: {record_id_val!r}. Prawdopodobnie oczekiwany format pola "
                f"'id' jest inny niż wysłany - potrzebne przechwycenie dokładnego usunięcia "
                f"pojedynczego wiersza w DevTools panelu WWW kamery.",
                raw_response=resp.text
            )

        try:
            return resp.json()
        except ValueError:
            return {"raw": resp.text}

    def _find_plate_id(self, plate_number: str, max_pages: int = 25) -> Optional[Dict[str, Any]]:
        """Szuka tablicy dokładnie pasującej do plate_number, przeglądając
        strony wyników (do max_pages * 40 rekordów)."""
        needle = plate_number.strip().lower()
        position = 0
        page_size = 40
        for _ in range(max_pages):
            result = self.search_plates(position=position, max_results=page_size)
            for rec in result["records"]:
                if rec.get("LicensePlate", "").strip().lower() == needle:
                    return rec
            if len(result["records"]) < page_size:
                break
            position += page_size
        return None
