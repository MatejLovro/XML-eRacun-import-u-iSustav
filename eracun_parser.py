"""
Modul A: Parsiranje UBL 2.1 XML eRačuna.

Pokriva standardnu strukturu UBL 2.1 Invoice dokumenta. Credit note (odobrenje)
XML-ovi imaju drugačije nazive tagova stavki (CreditNoteLine) i trenutno
NISU podržani - ako zatreba, javiti pa se dodaje zasebna grana parsiranja.
"""
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import List, Optional

NS = {
    "cbc": "urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2",
    "cac": "urn:oasis:names:specification:ubl:schema:xsd:CommonAggregateComponents-2",
}


@dataclass
class EracunStavka:
    redni_broj: str
    sifra_dobavljaca: str
    naziv_dobavljaca: str
    kolicina: Decimal
    cijena: Decimal
    porez_posto: Decimal = Decimal("0")  # cac:ClassifiedTaxCategory/cbc:Percent


@dataclass
class EracunZaglavlje:
    broj_dokumenta: str        # XML: cbc:ID (root)
    oib_dobavljaca: str        # XML: AccountingSupplierParty//cbc:CompanyID
    oib_kupca: str             # XML: AccountingCustomerParty//cbc:CompanyID
    naziv_kupca: str           # XML: AccountingCustomerParty//cbc:RegistrationName
    datum_izdavanja: str       # ISO YYYY-MM-DD, iz cbc:IssueDate
    datum_zaprimanja: str      # ISO YYYY-MM-DD, iz ActualDeliveryDate (ili IssueDate)
    ref_key: str                # jedinstveni ključ za ERACUN_PRIMKE (spr. dupli uvoz)
    stavke: List[EracunStavka] = field(default_factory=list)


def _text(el: Optional[ET.Element]) -> str:
    return el.text.strip() if el is not None and el.text else ""


def _decimal(text: str) -> Decimal:
    try:
        return Decimal(text.replace(",", "."))
    except (InvalidOperation, AttributeError):
        return Decimal("0")


def _normalize_oib(raw: str) -> str:
    """
    UBL XML često zapisuje OIB s 'HR' prefiksom (ISO porezni broj, npr.
    'HR12345678901'), dok se u FB.TVRTKE.OIB čuva samo brojčani dio.
    Uklanja prefiks (case-insensitive) i eventualne razmake/crtice.
    """
    text = (raw or "").strip().replace(" ", "").replace("-", "")
    if text.upper().startswith("HR"):
        text = text[2:]
    return text


def parse_ubl_invoice(xml_path: str) -> EracunZaglavlje:
    """
    Parsira UBL 2.1 XML eRačun i vraća EracunZaglavlje sa svim stavkama.
    Baca ValueError s razumljivom porukom ako XML nije u očekivanom formatu -
    GUI sloj tu poruku prikazuje korisniku u pop-upu.
    """
    try:
        tree = ET.parse(xml_path)
    except ET.ParseError as e:
        raise ValueError(f"Datoteka nije valjan XML: {e}")

    root = tree.getroot()

    broj_dokumenta = _text(root.find("cbc:ID", NS))
    if not broj_dokumenta:
        raise ValueError("XML ne sadrži broj dokumenta (cbc:ID) - provjerite je li ovo eRačun.")

    supplier = root.find("cac:AccountingSupplierParty", NS)
    if supplier is None:
        raise ValueError("XML ne sadrži podatke o dobavljaču (cac:AccountingSupplierParty).")

    oib = _normalize_oib(_text(supplier.find(".//cbc:CompanyID", NS)))
    if not oib:
        raise ValueError("Nije pronađen OIB dobavljača (cbc:CompanyID) u XML-u.")

    customer = root.find("cac:AccountingCustomerParty", NS)
    if customer is None:
        raise ValueError("XML ne sadrži podatke o kupcu (cac:AccountingCustomerParty).")

    oib_kupca = _normalize_oib(_text(customer.find(".//cbc:CompanyID", NS)))
    if not oib_kupca:
        raise ValueError("Nije pronađen OIB kupca (cbc:CompanyID) u XML-u.")

    naziv_kupca = _text(customer.find(".//cbc:RegistrationName", NS))

    datum_izdavanja = _text(root.find("cbc:IssueDate", NS))

    datum_zaprimanja = _text(root.find(".//cac:Delivery/cbc:ActualDeliveryDate", NS))
    if not datum_zaprimanja:
        datum_zaprimanja = datum_izdavanja

    uuid_text = _text(root.find("cbc:UUID", NS))
    if uuid_text:
        ref_key = uuid_text
    else:
        ref_key = f"{oib}_{broj_dokumenta}_{datum_izdavanja}"

    zaglavlje = EracunZaglavlje(
        broj_dokumenta=broj_dokumenta,
        oib_dobavljaca=oib,
        oib_kupca=oib_kupca,
        naziv_kupca=naziv_kupca,
        datum_izdavanja=datum_izdavanja,
        datum_zaprimanja=datum_zaprimanja,
        ref_key=ref_key,
    )

    for line in root.findall("cac:InvoiceLine", NS):
        redni_broj = _text(line.find("cbc:ID", NS))

        item = line.find("cac:Item", NS)
        sifra = ""
        naziv = ""
        porez_posto = Decimal("0")
        if item is not None:
            sifra = _text(item.find("cac:SellersItemIdentification/cbc:ID", NS))
            naziv = _text(item.find("cbc:Name", NS))
            tax_cat = item.find("cac:ClassifiedTaxCategory", NS)
            if tax_cat is not None:
                porez_posto = _decimal(_text(tax_cat.find("cbc:Percent", NS)))

        kol_el = line.find("cbc:InvoicedQuantity", NS)
        if kol_el is None:
            kol_el = line.find("cbc:BaseQuantity", NS)
        kolicina = _decimal(_text(kol_el))

        cijena = _decimal(_text(line.find("cac:Price/cbc:PriceAmount", NS)))

        zaglavlje.stavke.append(
            EracunStavka(
                redni_broj=redni_broj,
                sifra_dobavljaca=sifra,
                naziv_dobavljaca=naziv,
                kolicina=kolicina,
                cijena=cijena,
                porez_posto=porez_posto,
            )
        )

    if not zaglavlje.stavke:
        raise ValueError("XML ne sadrži nijednu stavku računa (cac:InvoiceLine).")

    return zaglavlje
