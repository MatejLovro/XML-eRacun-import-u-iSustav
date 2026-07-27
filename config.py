"""
Čitanje konfiguracije iz param.ini datoteke.

param.ini se očekuje u istom direktoriju iz kojeg se aplikacija pokreće
(izvršni direktorij). Ako datoteka ne postoji ili nedostaje obavezna
sekcija/parametar, baca se iznimka koju GUI sloj hvata i prikazuje
korisniku kao pop-up poruku.
"""
import configparser
import os
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation


@dataclass
class Config:
    host: str
    port: int
    user: str
    password: str
    database: str   # puna putanja do .FDB datoteke (dir_fdb + fdb_file)
    dir_xml: str    # zadani folder za dijalog "Učitaj XML"
    charset: str
    idsisuser: int          # FB.KORISNICI.IDKOR - upisuje se kod knjiženja primke
    idtransakcijski: int    # koristi se kod knjiženja primke (GASZG/GASST)
    povratna_naknada: Decimal  # iznos povratne naknade (GASST.AMBCIJENA)


def load_config(ini_path: str = "param.ini") -> Config:
    if not os.path.exists(ini_path):
        raise FileNotFoundError(
            f"Konfiguracijska datoteka '{ini_path}' nije pronađena u "
            f"izvršnom direktoriju aplikacije."
        )

    parser = configparser.ConfigParser()
    parser.read(ini_path, encoding="utf-8")

    if "firebird" not in parser:
        raise ValueError("Sekcija [firebird] nije pronađena u param.ini datoteci.")

    sec = parser["firebird"]

    dir_fdb = sec.get("dir_fdb", "").strip()
    fdb_file = sec.get("fdb_file", "").strip()
    if not dir_fdb or not fdb_file:
        raise ValueError("Parametri 'dir_fdb' i 'fdb_file' moraju biti postavljeni u param.ini.")

    database = os.path.join(dir_fdb, fdb_file)

    povratna_naknada_text = sec.get("povratna_naknada", "0.1").strip().replace(",", ".")
    try:
        povratna_naknada = Decimal(povratna_naknada_text)
    except InvalidOperation:
        raise ValueError(
            f"Parametar 'povratna_naknada' u sekciji [firebird] mora biti broj "
            f"(trenutna vrijednost '{povratna_naknada_text}' nije valjan broj)."
        )

    if "primka" not in parser:
        raise ValueError(
            "Sekcija [primka] nije pronađena u param.ini datoteci. Dodajte:\n\n"
            "[primka]\n"
            "idsisuser=<ID korisnika iz FB.KORISNICI.IDKOR>\n"
            "idtransakcijski=<ID transakcijske oznake>"
        )
    primka_sec = parser["primka"]

    try:
        idsisuser = int(primka_sec.get("idsisuser", "").strip())
        idtransakcijski = int(primka_sec.get("idtransakcijski", "").strip())
    except (ValueError, AttributeError):
        raise ValueError(
            "Parametri 'idsisuser' i 'idtransakcijski' u sekciji [primka] moraju "
            "biti postavljeni i biti cijeli brojevi (provjerite nedostaje li "
            "koji od njih, ili ima tipfeler u nazivu)."
        )

    if idsisuser <= 0:
        raise ValueError(
            "Parametar 'idsisuser' u sekciji [primka] mora biti pozitivan broj "
            "(ID stvarnog korisnika iz FB.KORISNICI.IDKOR) - trenutna vrijednost "
            f"je {idsisuser}, što bi kod knjiženja izazvalo grešku FK ograničenja "
            "na GASZG.SISUSER."
        )

    return Config(
        host=sec.get("host", "localhost").strip(),
        port=sec.getint("port", fallback=3050),
        user=sec.get("user", "SYSDBA").strip(),
        password=sec.get("password", "masterkey"),
        database=database,
        dir_xml=sec.get("dir_xml", "").strip(),
        charset=sec.get("charset", "WIN1250").strip(),
        idsisuser=idsisuser,
        idtransakcijski=idtransakcijski,
        povratna_naknada=povratna_naknada,
    )
