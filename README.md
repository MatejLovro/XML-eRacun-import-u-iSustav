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

## Preduvjeti u bazi

Nema ručnih koraka - tablice `ERACUN_VEZE` i `ERACUN_PRIMKE`, kao i
pripadne sekvence (`GEN_ERACUN_VEZE_ID`, `GEN_ERACUN_PRIMKE_ID`), aplikacija
sama provjerava i kreira pri svakom pokretanju (`db.ensure_schema()`), ako
već ne postoje.

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
  poslovnici). Ako `POSJED` ima samo jedan slog, automatski se odabire (i
  isto za `SKLAD`, filtrirano ili globalno ako ima samo jedan slog u bazi).
- Učitavanje i parsiranje UBL 2.1 XML eRačuna (zaglavlje + stavke), s
  normalizacijom OIB-a (uklanja "HR" prefiks ako postoji).
- Pronalazak dobavljača po OIB-u u `FB.TVRTKE`; poruka i prekid ako
  dobavljač ne postoji.
- Provjera je li račun već ranije uvezen (`FB.ERACUN_PRIMKE`), uz upit
  korisniku želi li ga svejedno učitati.
- Tablica stavki: za svaki redak automatska primjena postojeće veze iz
  `FB.ERACUN_VEZE`, ili ručno pretraživanje/odabir artikla iz `FB.OSNROBA`
  (vlastiti autocomplete - Entry + popup lista, s tipkanjem, strelicama,
  Enter/Tab za odabir).
- Kolona "Količina mjere" (jedinica u kojoj se artikl prodaje/zaprima) -
  prazna kod inicijalnog učitavanja, korisnik je mora ručno upisati
  (osim za retke označene "Ne unosi"); validacija unosa (samo brojevi,
  do 2 decimale).
- Checkboxovi "Povratna naknada" i "Ne unosi" po retku (isključeni iz
  tab-redoslijeda, biraju se samo mišem).
- Gumb "Knjiži" aktivan tek kad je svaki redak mapiran NA artikl I ima
  upisanu količinu mjere, ili je označen "Ne unosi".
- Klik na "Knjiži" - sve u jednoj transakciji (commit/rollback):
  - sprema/ažurira mapiranja u `ERACUN_VEZE` i evidenciju u `ERACUN_PRIMKE`
  - upisuje zaglavlje primke u `FB.GASZG` (`VK=408`, `BRDOK` = sljedeći
    slobodan broj za taj VK, `IDGASZG` iz generatora `GENIDDOKZG`)
  - upisuje stavke primke u `FB.GASST` (`IDGASST` iz generatora
    `GENIDDOKST`, `KOL` = XML količina × Količina mjere, `SIFPORGR` iz
    `FB.OSNROBA.ULPORGR`) - za svaki redak koji nije označen "Ne unosi"

## Što NIJE još implementirano (sljedeći koraci)

- **Cijene, iznosi i porezni izračuni** u `GASST` (`FAKCIJENA`, `FAKIZNOS`,
  `NABCIJENA`, `NABIZNOS`, `RABPOS`, `PRODCIJENA` i sl.) - namjerno
  izostavljeno u ovoj fazi, dogovoreno da se radi kao zaseban korak.
- Ostala nemapirana nullable polja u `GASZG`/`GASST` (`SIFTROSKA`, `MODEL`,
  `POZIV`, fiskalna polja i sl.) - ostaju `NULL`.
- Credit note (odobrenje) XML-ovi - trenutno parser podržava samo
  `Invoice` strukturu (`cac:InvoiceLine`).
- Indeksi na novim tabelama (dogovoreno da se rade naknadno).
- Rukovanje povratnom naknadom kao zasebnom stavkom na računu (trenutno
  se ta logika svodi na checkbox po retku; XML redak "POVRATNA NAKNADA"
  kao zaseban artikl dobavljača se učitava kao i svaki drugi redak i
  korisnik ga treba označiti "Ne unosi" ako je dobavljač već uračunao
  naknadu u cijene ostalih stavki).
- Zaštita od utrke uvjeta (race condition) kod `BRDOK` izračuna
  (`MAX(BRDOK)+1`) ako dva korisnika knjiže istovremeno.
- `GASST.SIFPORGR` je `NOT NULL` u bazi - ako artikl u `FB.OSNROBA` nema
  postavljenu `ULPORGR`, knjiženje puca s jasnom porukom greške.

## Napomene o pretpostavkama u parseru

- OIB dobavljača se traži kao prvi `cbc:CompanyID` unutar
  `cac:AccountingSupplierParty` podstabla - ako neki dobavljači XML
  strukturiraju drugačije (npr. OIB u `cac:PartyTaxScheme` umjesto
  `cac:PartyLegalEntity`), možda će trebati doraditi pretragu na
  konkretnim primjerima XML-ova.
- `REF_KEY_ERACUN` koristi `cbc:UUID` ako postoji u XML-u, inače se
  generira kao `OIB_BrojRačuna_DatumIzdavanja`.

## Generatori korišteni u bazi

- `GEN_ERACUN_VEZE_ID`, `GEN_ERACUN_PRIMKE_ID` - kreira ih aplikacija sama
  (`db.ensure_schema()`) ako ne postoje, zajedno s tabelama.
- `GENIDDOKZG`, `GENIDDOKST` - već postoje u iSustav bazi (potvrđeno da
  odgovaraju `IDGASZG`/`IDGASST`), aplikacija ih samo koristi.

