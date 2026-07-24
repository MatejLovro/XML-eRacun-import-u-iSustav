"""
Sve operacije nad Firebird 3.0 bazom potrebne za učitavanje eRačuna i
mapiranje artikala (Modul B). Upis stvarne primke (GASZG/GASST) dodat
će se naknadno.
"""
from decimal import Decimal
from typing import List, Optional, Tuple

import fdb

from config import Config


def connect(cfg: Config) -> fdb.Connection:
    """
    Uspostavlja konekciju na Firebird bazu.
    Baca fdb.DatabaseError (ili OSError) ako spajanje ne uspije - GUI sloj
    hvata iznimku i prikazuje pop-up poruku, te onemogućuje rad.
    """
    dsn = f"{cfg.host}/{cfg.port}:{cfg.database}"
    return fdb.connect(dsn=dsn, user=cfg.user, password=cfg.password, charset=cfg.charset)


# ---------------------------------------------------------------------------
# Provjera/kreiranje vlastitih tabela (ERACUN_VEZE, ERACUN_PRIMKE) pri startu
# ---------------------------------------------------------------------------

def ensure_schema(conn: fdb.Connection) -> None:
    """
    Poziva se jednom, odmah nakon spajanja na bazu (prije otvaranja GUI-ja).
    Provjerava postoje li tabele ERACUN_VEZE i ERACUN_PRIMKE (te pripadne
    sekvence za generiranje ID-a) i kreira ih ako nedostaju. Izvorna
    iSustav baza NE sadrži ove tabele - one pripadaju isključivo ovoj
    aplikaciji.

    Ako tabele/sekvence već postoje, funkcija ne radi ništa (idempotentno -
    sigurno je zvati je pri svakom pokretanju).
    """
    _ensure_table_eracun_veze(conn)
    _ensure_table_eracun_primke(conn)
    _ensure_sequence(conn, "GEN_ERACUN_VEZE_ID")
    _ensure_sequence(conn, "GEN_ERACUN_PRIMKE_ID")


def _table_exists(conn: fdb.Connection, table_name: str) -> bool:
    cur = conn.cursor()
    cur.execute(
        "SELECT 1 FROM RDB$RELATIONS WHERE RDB$RELATION_NAME = ?",
        (table_name.upper(),),
    )
    return cur.fetchone() is not None


def _sequence_exists(conn: fdb.Connection, seq_name: str) -> bool:
    cur = conn.cursor()
    cur.execute(
        "SELECT 1 FROM RDB$GENERATORS WHERE RDB$GENERATOR_NAME = ?",
        (seq_name.upper(),),
    )
    return cur.fetchone() is not None


def _ensure_sequence(conn: fdb.Connection, seq_name: str) -> None:
    if _sequence_exists(conn, seq_name):
        return
    cur = conn.cursor()
    cur.execute(f"CREATE SEQUENCE {seq_name}")
    conn.commit()


def _ensure_table_eracun_veze(conn: fdb.Connection) -> None:
    """
    Kreira ERACUN_VEZE - mapiranje artikala dobavljača na interne artikle.
    IDROBE referencira FB.OSNROBA.IDOSNROBE (matična tabela artikala).
    """
    if _table_exists(conn, "ERACUN_VEZE"):
        return

    cur = conn.cursor()
    cur.execute(
        """
        CREATE TABLE ERACUN_VEZE (
          ID                      BIGINT                 NOT NULL
        , IDTVRTKE                BIGINT                 NOT NULL
        , SIFRA_ROBE_DOBAVLJACA   VARCHAR( 30 )
        , NAZ_ROBE_DOBAVLJACA     VARCHAR( 60 ) CHARACTER SET WIN1250 COLLATE PXW_SLOV
        , KOL_ROBE_DOBAVLJACA     NUMERIC( 18, 3 )
        , IDROBE                  BIGINT                 NOT NULL
        , KOLROBE                 NUMERIC( 18, 3 )
        , POV_NAK                 CHAR( 1 ) CHARACTER SET WIN1250 DEFAULT 'F'   NOT NULL
        , NE_UNOSI                CHAR( 1 ) CHARACTER SET WIN1250 DEFAULT 'F'   NOT NULL
        , CONSTRAINT RBE_ID
            PRIMARY KEY ( ID )
        )
        """
    )
    conn.commit()

    cur.execute(
        """
        ALTER TABLE ERACUN_VEZE
          ADD CONSTRAINT RBE_IDTVRTKE
                FOREIGN KEY ( IDTVRTKE )
                REFERENCES TVRTKE ( IDTVRTKE )
                  ON UPDATE CASCADE
        """
    )
    cur.execute(
        """
        ALTER TABLE ERACUN_VEZE
          ADD CONSTRAINT RBE_IDROBE
                FOREIGN KEY ( IDROBE )
                REFERENCES OSNROBA ( IDOSNROBE )
                  ON UPDATE CASCADE
        """
    )
    conn.commit()


def _ensure_table_eracun_primke(conn: fdb.Connection) -> None:
    """
    Kreira ERACUN_PRIMKE - evidencija uvezenih eRačuna (sprječava dupli uvoz).
    """
    if _table_exists(conn, "ERACUN_PRIMKE"):
        return

    cur = conn.cursor()
    cur.execute(
        """
        CREATE TABLE ERACUN_PRIMKE (
          ID                BIGINT                 NOT NULL
        , REF_KEY_ERACUN    VARCHAR( 50 )
        , IDKUPCA           BIGINT                 NOT NULL
        , CONSTRAINT ERP_ID
            PRIMARY KEY ( ID )
        )
        """
    )
    conn.commit()

    cur.execute(
        """
        ALTER TABLE ERACUN_PRIMKE
          ADD CONSTRAINT ERP_IDKUPCA
                FOREIGN KEY ( IDKUPCA )
                REFERENCES TVRTKE ( IDTVRTKE )
                  ON UPDATE CASCADE
        """
    )
    conn.commit()


# ---------------------------------------------------------------------------
# Zaglavlje forme: Poslovnica / Skladište / Dobavljač
# ---------------------------------------------------------------------------

def fetch_posjed_list(conn: fdb.Connection) -> List[Tuple[int, str]]:
    """Vraća listu (IDPOSJED, NAZIV) za dropdown Poslovnica."""
    cur = conn.cursor()
    cur.execute("SELECT IDPOSJED, NAZIV FROM POSJED ORDER BY NAZIV")
    return cur.fetchall()


def fetch_sklad_list(conn: fdb.Connection, id_posjed: Optional[int] = None) -> List[Tuple[int, str]]:
    """
    Vraća listu (IDSKLAD, NAZIV) za dropdown Skladište.
    Ako je zadan id_posjed, filtrira samo skladišta te poslovnice
    (SKLAD.IDPOSJED je FK na POSJED.IDPOSJED).
    """
    cur = conn.cursor()
    if id_posjed is not None:
        cur.execute("SELECT IDSKLAD, NAZIV FROM SKLAD WHERE IDPOSJED = ? ORDER BY NAZIV", (id_posjed,))
    else:
        cur.execute("SELECT IDSKLAD, NAZIV FROM SKLAD ORDER BY NAZIV")
    return cur.fetchall()


def fetch_tvrtka_by_oib(conn: fdb.Connection, oib: str) -> Optional[Tuple[int, str]]:
    """
    Traži dobavljača po OIB-u u FB.TVRTKE.
    Vraća (IDTVRTKE, NAZIV1) ili None ako zapis ne postoji.
    """
    cur = conn.cursor()
    cur.execute("SELECT IDTVRTKE, NAZIV1 FROM TVRTKE WHERE OIB = ?", (oib,))
    return cur.fetchone()


# ---------------------------------------------------------------------------
# Modul B: Mapiranje artikala (ERACUN_VEZE / OSNROBA)
# ---------------------------------------------------------------------------

def fetch_veza(conn: fdb.Connection, id_tvrtke: int, sifra_dobavljaca: str) -> Optional[dict]:
    """
    Traži postojeću vezu artikla dobavljača s internim artiklom.
    Vraća dict (id, idrobe, naziv_robe, kolrobe, pov_nak, ne_unosi) ili None.
    """
    cur = conn.cursor()
    cur.execute(
        """
        SELECT V.ID, V.IDROBE, R.NAZIVROBE, V.KOLROBE, V.POV_NAK, V.NE_UNOSI
        FROM ERACUN_VEZE V
        JOIN OSNROBA R ON R.IDOSNROBE = V.IDROBE
        WHERE V.IDTVRTKE = ? AND V.SIFRA_ROBE_DOBAVLJACA = ?
        """,
        (id_tvrtke, sifra_dobavljaca),
    )
    row = cur.fetchone()
    if row is None:
        return None
    return {
        "id": row[0],
        "idrobe": row[1],
        "naziv_robe": row[2],
        "kolrobe": row[3],
        "pov_nak": row[4] == "T",
        "ne_unosi": row[5] == "T",
    }


def search_roba(conn: fdb.Connection, text: str, limit: Optional[int] = None) -> List[Tuple[int, str]]:
    """
    Pretražuje FB.OSNROBA po nazivu (CONTAINING - case-insensitive u Firebirdu,
    pronalazi tekst bilo gdje unutar naziva) za potrebe autocomplete polja
    "Naziv artikla".

    Ako je text prazan, vraća CIJELI katalog artikala po abecedi (za
    pregled/browse klikom, bez upisivanja) - limit=None znači bez FIRST
    ograničenja. Ako je zadan limit, koristi se za ograničavanje broja
    redaka (npr. kod vrlo velikih kataloga, ako ikad zatreba).
    """
    cur = conn.cursor()
    text = (text or "").strip()
    first_clause = f"FIRST {int(limit)} " if limit is not None else ""

    if text:
        cur.execute(
            f"SELECT {first_clause}IDOSNROBE, NAZIVROBE FROM OSNROBA "
            f"WHERE NAZIVROBE CONTAINING ? ORDER BY NAZIVROBE",
            (text,),
        )
    else:
        cur.execute(
            f"SELECT {first_clause}IDOSNROBE, NAZIVROBE FROM OSNROBA ORDER BY NAZIVROBE"
        )
    return cur.fetchall()


def save_veza(
    conn: fdb.Connection,
    id_tvrtke: int,
    sifra_dobavljaca: str,
    naziv_dobavljaca: str,
    kol_dobavljaca: Decimal,
    id_robe: int,
    kolrobe: Decimal,
    pov_nak: bool,
    ne_unosi: bool,
) -> None:
    """
    Sprema (insert ili update) vezu artikla dobavljača s internim artiklom.
    NE commita transakciju - to radi pozivatelj (gumb Knjiži) nakon što
    spremi sve retke i ERACUN_PRIMKE zapis u istoj transakciji.

    Napomena: koristi sekvencu GEN_ERACUN_VEZE_ID koju treba kreirati u bazi:
        CREATE SEQUENCE GEN_ERACUN_VEZE_ID;
    """
    naziv_dobavljaca = (naziv_dobavljaca or "")[:60]  # NAZ_ROBE_DOBAVLJACA je VARCHAR(60)

    cur = conn.cursor()
    cur.execute(
        "SELECT ID FROM ERACUN_VEZE WHERE IDTVRTKE = ? AND SIFRA_ROBE_DOBAVLJACA = ?",
        (id_tvrtke, sifra_dobavljaca),
    )
    existing = cur.fetchone()

    if existing:
        cur.execute(
            """
            UPDATE ERACUN_VEZE
            SET NAZ_ROBE_DOBAVLJACA = ?, KOL_ROBE_DOBAVLJACA = ?, IDROBE = ?,
                KOLROBE = ?, POV_NAK = ?, NE_UNOSI = ?
            WHERE ID = ?
            """,
            (
                naziv_dobavljaca, kol_dobavljaca, id_robe, kolrobe,
                "T" if pov_nak else "F", "T" if ne_unosi else "F",
                existing[0],
            ),
        )
    else:
        cur.execute("SELECT NEXT VALUE FOR GEN_ERACUN_VEZE_ID FROM RDB$DATABASE")
        new_id = cur.fetchone()[0]
        cur.execute(
            """
            INSERT INTO ERACUN_VEZE
                (ID, IDTVRTKE, SIFRA_ROBE_DOBAVLJACA, NAZ_ROBE_DOBAVLJACA,
                 KOL_ROBE_DOBAVLJACA, IDROBE, KOLROBE, POV_NAK, NE_UNOSI)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                new_id, id_tvrtke, sifra_dobavljaca, naziv_dobavljaca,
                kol_dobavljaca, id_robe, kolrobe,
                "T" if pov_nak else "F", "T" if ne_unosi else "F",
            ),
        )


# ---------------------------------------------------------------------------
# ERACUN_PRIMKE - sprječavanje duplog uvoza istog računa
# ---------------------------------------------------------------------------

def eracun_vec_uvezen(conn: fdb.Connection, ref_key: str) -> bool:
    """Provjerava je li račun s ovim REF_KEY_ERACUN već uvezen ranije."""
    cur = conn.cursor()
    cur.execute("SELECT ID FROM ERACUN_PRIMKE WHERE REF_KEY_ERACUN = ?", (ref_key,))
    return cur.fetchone() is not None


def save_eracun_primka(conn: fdb.Connection, ref_key: str, id_kupca: int) -> None:
    """
    Upisuje evidenciju da je račun uvezen. NE commita - poziva se unutar
    iste transakcije kao i save_veza() pozivi, pri kliku na Knjiži.

    Napomena: koristi sekvencu GEN_ERACUN_PRIMKE_ID koju treba kreirati:
        CREATE SEQUENCE GEN_ERACUN_PRIMKE_ID;
    """
    cur = conn.cursor()
    cur.execute("SELECT NEXT VALUE FOR GEN_ERACUN_PRIMKE_ID FROM RDB$DATABASE")
    new_id = cur.fetchone()[0]
    cur.execute(
        "INSERT INTO ERACUN_PRIMKE (ID, REF_KEY_ERACUN, IDKUPCA) VALUES (?, ?, ?)",
        (new_id, ref_key, id_kupca),
    )
