# Uvoz eRačuna (UBL 2.1) u iSustav

Desktop aplikacija (Python 3.13 + Tkinter) koja iz eRačuna u UBL 2.1 XML formatu
priprema primku za iSustav (Firebird 3.0), uz mapiranje artikala dobavljača na
interne artikle.

## Struktura projekta

| Datoteka             | Sadržaj |
|-----------------------|---------|
| `param.ini`            | Konfiguracija (baza, putanje) - prilagoditi za svako računalo/instalaciju |
| `config.py`             | Čitanje `param.ini` |
| `db.py`                 | Sve operacije nad Firebird bazom (Poslovnica/Skladište/Dobavljač, mapiranje artikala, evidencija uvezenih računa) |
| `eracun_parser.py`      | Parsiranje UBL 2.1 XML eRačuna (Modul A) |
| `gui.py`                | Tkinter forma - zaglavlje, tablica stavki, mapiranje (Modul B), gumb Knjiži |
| `main.py`               | Ulazna točka - `python main.py` |
| `requirements.txt`      | `pip install -r requirements.txt` |

## Preduvjeti u bazi (potrebno izvršiti PRIJE prvog pokretanja)

Tablice `ERACUN_VEZE` i `ERACUN_PRIMKE` su već kreirane, ali nemaju mehanizam
automatskog generiranja ID-a. Aplikacija koristi sekvence (Firebird 3.0
`NEXT VALUE FOR`), pa je potrebno u DBeaveru izvršiti:

```sql
CREATE SEQUENCE GEN_ERACUN_VEZE_ID;
CREATE SEQUENCE GEN_ERACUN_PRIMKE_ID;
```

## Pokretanje

```bash
pip install -r requirements.txt
python main.py
```

`param.ini` mora biti u istom direktoriju kao `main.py`, s ispravnim podacima
o bazi (`dir_fdb`, `fdb_file`, `host`, `port`, `user`, `password`) i putanjom
do XML datoteka (`dir_xml`).

## Trenutni opseg funkcionalnosti (implementirano)

- Dropdown Poslovnica (`FB.POSJED`) i Skladište (`FB.SKLAD`, filtrirano po
  poslovnici).
- Učitavanje i parsiranje UBL 2.1 XML eRačuna (zaglavlje + stavke).
- Pronalazak dobavljača po OIB-u u `FB.TVRTKE`; poruka i prekid ako
  dobavljač ne postoji.
- Provjera je li račun već ranije uvezen (`FB.ERACUN_PRIMKE`), uz upit
  korisniku želi li ga svejedno učitati.
- Tablica stavki: za svaki redak automatska primjena postojeće veze iz
  `FB.ERACUN_VEZE`, ili ručno pretraživanje/odabir artikla iz `FB.ROBA`
  (autocomplete po nazivu).
- Checkboxovi "Povratna naknada" i "Ne unosi" po retku.
- Gumb "Knjiži" aktivan tek kad je svaki redak mapiran ili označen
  "Ne unosi".
- Klik na "Knjiži" trenutno: sprema/ažurira mapiranja u `ERACUN_VEZE` i
  upisuje evidenciju u `ERACUN_PRIMKE`, u jednoj transakciji (commit/rollback).

## Što NIJE još implementirano (sljedeći koraci)

- **Stvarni upis primke** u `FB.GASZG` (zaglavlje) i `FB.GASST` (stavke) -
  ovo se nadovezuje na klik "Knjiži", unutar iste transakcije, nakon što
  definiramo logiku izračuna cijena (`FAKCIJENA`, `NABCIJENA`, `RABPOS`,
  porezne šifre `SIFPORGR`, brojevi dokumenata `BRDOK` i sl.).
- Credit note (odobrenje) XML-ovi - trenutno parser podržava samo
  `Invoice` strukturu (`cac:InvoiceLine`).
- Indeksi na novim tabelama (dogovoreno da se rade naknadno).
- Rukovanje povratnom naknadom kao zasebnom stavkom na računu (trenutno
  se ta logika svodi na checkbox po retku; XML redak "POVRATNA NAKNADA"
  kao zaseban artikl dobavljača se učitava kao i svaki drugi redak i
  korisnik ga treba označiti "Ne unosi" ako je dobavljač već uračunao
  naknadu u cijene ostalih stavki).

## Napomene o pretpostavkama u parseru

- OIB dobavljača se traži kao prvi `cbc:CompanyID` unutar
  `cac:AccountingSupplierParty` podstabla - ako neki dobavljači XML
  strukturiraju drugačije (npr. OIB u `cac:PartyTaxScheme` umjesto
  `cac:PartyLegalEntity`), možda će trebati doraditi pretragu na
  konkretnim primjerima XML-ova.
- `REF_KEY_ERACUN` koristi `cbc:UUID` ako postoji u XML-u, inače se
  generira kao `OIB_BrojRačuna_DatumIzdavanja`.
