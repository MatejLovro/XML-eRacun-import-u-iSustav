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

    primka_sec = parser["primka"] if "primka" in parser else {}
    try:
        idsisuser = int(primka_sec.get("idsisuser", "0"))
        idtransakcijski = int(primka_sec.get("idtransakcijski", "0"))
    except ValueError:
        raise ValueError(
            "Parametri 'idsisuser' i 'idtransakcijski' u sekciji [primka] "
            "moraju biti cijeli brojevi."
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
    )
