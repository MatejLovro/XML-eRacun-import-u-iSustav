"""
Glavna GUI forma aplikacije (prema mockupu Forma-UNOS_PRIMKE).

Tijek rada:
  1. Korisnik odabere Poslovnicu i Skladište.
  2. Klikom na "Učitaj XML..." odabire eRačun; aplikacija parsira XML,
     pronalazi dobavljača po OIB-u i popunjava zaglavlje + tablicu stavki.
  3. Za svaki redak, ako postoji ranija veza u ERACUN_VEZE, redak se
     automatski popuni; inače korisnik ručno pretražuje i bira artikl.
  4. Gumb "Knjiži" postaje aktivan tek kad je svaki redak "gotov"
     (mapiran na artikl ili označen kao "Ne unosi").
  5. Klikom na "Knjiži" trenutno se spremaju mapiranja u ERACUN_VEZE i
     evidencija u ERACUN_PRIMKE. Upis stvarne primke (GASZG/GASST) dodaje
     se u sljedećem koraku razvoja.
"""
import os
import re
import tkinter as tk
from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal
from tkinter import filedialog, messagebox, ttk

import db
import eracun_parser as parser
from config import Config


def hr_date(iso_date: str) -> str:
    """YYYY-MM-DD -> DD.MM.YYYY. Vraća izvorni tekst ako parsiranje ne uspije."""
    try:
        return datetime.strptime(iso_date, "%Y-%m-%d").strftime("%d.%m.%Y")
    except (ValueError, TypeError):
        return iso_date or ""


def parse_iso_date(iso_date: str) -> datetime:
    """YYYY-MM-DD -> datetime objekt (za upis u TIMESTAMP kolone GASZG/GASST)."""
    return datetime.strptime(iso_date, "%Y-%m-%d")


def format_decimal(value: Decimal) -> str:
    """
    Formatira Decimal za prikaz bez suvišnih nula i BEZ znanstvene notacije.
    Napomena: Decimal.normalize() zna prijeći na znanstvenu notaciju za
    "okrugle" brojeve (npr. 180.00 -> 1.8E+2) - format(value, 'f') to
    izbjegava (uvijek fiksni zapis), pa se suvišne nule ručno režu.
    """
    text = format(value, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text or "0"


class StavkaRow:
    """Jedan redak u tablici stavki - drži tkinter varijable i widgete retka."""

    def __init__(self, parent: tk.Widget, app: "App", row_index: int, stavka):
        self.app = app
        self.stavka = stavka
        self.id_robe = None  # popunjava se kad je artikl mapiran/odabran
        self._search_results = []  # trenutna lista (idrobe, naziv) rezultata pretrage

        self.var_naziv_artikla = tk.StringVar()
        self.var_kolicina_ulaz = tk.StringVar(value="")  # prazno dok korisnik ne upiše ili se ne primijeni postojeća veza
        self.var_pov_nak = tk.BooleanVar(value=False)
        self.var_ne_unosi = tk.BooleanVar(value=False)

        r = row_index
        ttk.Label(parent, text=stavka.redni_broj, width=5, anchor="center").grid(
            row=r, column=0, sticky="nsew", padx=1, pady=1)
        ttk.Label(parent, text=stavka.sifra_dobavljaca, anchor="w").grid(
            row=r, column=1, sticky="nsew", padx=1, pady=1)
        ttk.Label(parent, text=stavka.naziv_dobavljaca, anchor="w").grid(
            row=r, column=2, sticky="nsew", padx=1, pady=1)
        ttk.Label(parent, text=str(stavka.kolicina), width=8, anchor="e").grid(
            row=r, column=3, sticky="nsew", padx=1, pady=1)
        cijena_prikaz = stavka.cijena.quantize(Decimal("0.001"), rounding=ROUND_HALF_UP)
        ttk.Label(parent, text=str(cijena_prikaz), width=8, anchor="e").grid(
            row=r, column=4, sticky="nsew", padx=1, pady=1)

        # Obično Entry polje umjesto ttk.Combobox - padajuću listu prikazujemo
        # sami (App._ac_* metode), čime izbjegavamo krhko/asinkrono ponašanje
        # ugrađenog ttk::combobox::Post mehanizma.
        self.entry = ttk.Entry(parent, textvariable=self.var_naziv_artikla, width=24)
        self.entry.grid(row=r, column=5, sticky="nsew", padx=1, pady=1)
        self.entry.bind("<KeyRelease>", self._on_key_release)
        self.entry.bind("<Down>", self._on_arrow_down)
        self.entry.bind("<Up>", self._on_arrow_up)
        self.entry.bind("<Return>", self._on_enter)
        self.entry.bind("<Tab>", self._on_tab)
        self.entry.bind("<Shift-Tab>", self._on_tab)
        self.entry.bind("<Escape>", self._on_escape)
        self.entry.bind("<Button-1>", self._on_click)
        self.entry.bind("<FocusOut>", self._on_focus_out)

        self.entry_kolicina = ttk.Entry(
            parent, textvariable=self.var_kolicina_ulaz, width=9, justify="right"
        )
        self.entry_kolicina.grid(row=r, column=6, sticky="nsew", padx=1, pady=1)
        vcmd = (parent.register(self._validate_kolicina), "%P")
        self.entry_kolicina.configure(validate="key", validatecommand=vcmd)
        # trace umjesto KeyRelease - hvata i ručno upisivanje i programske izmjene
        # (npr. kad _apply_existing_veza popuni vrijednost iz ranije veze)
        self.var_kolicina_ulaz.trace_add("write", lambda *_: self.app.refresh_knjizi_state())

        ttk.Checkbutton(parent, variable=self.var_pov_nak, takefocus=False,
                         command=self.app.refresh_knjizi_state).grid(row=r, column=7)
        self.chk_ne_unosi = ttk.Checkbutton(
            parent, variable=self.var_ne_unosi, takefocus=False,
            command=self._on_ne_unosi_toggle,
        )
        self.chk_ne_unosi.grid(row=r, column=8)

        self._apply_existing_veza()
        self._update_ne_unosi_availability()

    _NUM_RE = re.compile(r"^\d*([.,]\d{0,2})?$")

    def _validate_kolicina(self, proposed: str) -> bool:
        """
        Validacija unosa u "Količina mjere" - dopušta prazan string (brisanje),
        cijele brojeve, i decimalne brojeve s najviše 2 decimale (zarez ili
        točka kao separator). Poziva se pri svakom pritisku tipke (validate="key");
        NE poziva se kod programskog postavljanja preko var_kolicina_ulaz.set()
        (npr. kad _apply_existing_veza popuni vrijednost iz baze).
        """
        return self._NUM_RE.match(proposed) is not None

    def _apply_existing_veza(self):
        """Ako u ERACUN_VEZE već postoji mapiranje za ovog dobavljača/šifru, popuni redak."""
        veza = db.fetch_veza(self.app.conn, self.app.id_tvrtke, self.stavka.sifra_dobavljaca)
        if veza:
            self.id_robe = veza["idrobe"]
            self.var_naziv_artikla.set(veza["naziv_robe"])
            if veza["kolrobe"] is not None:
                self.var_kolicina_ulaz.set(str(veza["kolrobe"]))
            self.var_pov_nak.set(veza["pov_nak"])
            self.var_ne_unosi.set(veza["ne_unosi"])

    def _update_ne_unosi_availability(self):
        """
        "Ne unosi" smije biti označeno SAMO na retcima koji nisu mapirani na
        artikl. Ako je redak mapiran (id_robe postavljen), checkbox se
        onemogućuje i eventualno već postavljena kvačica se skida (spriječava
        kontradiktorno stanje: mapiran artikl + "ne zaprimaj ovaj redak").
        """
        if self.id_robe is not None:
            if self.var_ne_unosi.get():
                self.var_ne_unosi.set(False)
                self.app.refresh_knjizi_state()
            self.chk_ne_unosi.config(state="disabled")
        else:
            self.chk_ne_unosi.config(state="normal")

    _IGNORE_KEYS = {
        "Up", "Down", "Left", "Right", "Return", "Escape", "Tab",
        "Shift_L", "Shift_R", "Control_L", "Control_R",
    }

    def _run_search(self, text: str):
        try:
            results = db.search_roba(self.app.conn, text)
        except Exception as e:
            messagebox.showerror("Greška pri pretrazi FB.OSNROBA", str(e))
            return
        self._search_results = results
        if results:
            self.app.show_autocomplete(self, results)
        else:
            self.app.hide_autocomplete()

    def _on_key_release(self, event):
        if event.keysym in self._IGNORE_KEYS:
            return
        self.id_robe = None  # dok korisnik ne odabere iz padajuće liste, artikl nije mapiran
        self._run_search(self.var_naziv_artikla.get().strip())
        self.app.refresh_knjizi_state()

    def _on_click(self, event):
        """Klik na prazno/postojeće polje - prikaži rezultate za trenutni tekst (ili sve)."""
        self._run_search(self.var_naziv_artikla.get().strip())

    def _on_arrow_down(self, event):
        self.app.move_autocomplete_selection(self, 1)
        return "break"

    def _on_arrow_up(self, event):
        self.app.move_autocomplete_selection(self, -1)
        return "break"

    def _on_enter(self, event):
        self.app.select_autocomplete_current(self)
        return "break"

    def _on_tab(self, event):
        """
        Ako je padajuća lista otvorena, Tab prvo primjenjuje trenutno
        označenu stavku (isto kao Enter), a zatim propušta Tab dalje da
        normalno pomakne fokus na sljedeće polje (Količina mjere).
        Ne vraćamo "break" - default Tab-navigacija mora nastaviti raditi.
        """
        self.app.select_autocomplete_current(self)

    def _on_escape(self, event):
        self.app.hide_autocomplete()
        return "break"

    def _on_focus_out(self, event):
        # Odgoda: ako je fokus otišao na popup listu (klik na stavku), klik
        # treba stići obraditi PRIJE nego što popup sakrijemo.
        self.app.root.after(150, lambda: self.app.hide_autocomplete_if_not_focused(self))

    def apply_selection(self, idrobe: int, naziv: str):
        """Poziva App kad korisnik odabere stavku iz padajuće liste (klik ili Enter)."""
        self.var_naziv_artikla.set(naziv)
        self.id_robe = idrobe
        self._update_ne_unosi_availability()
        self.app.refresh_knjizi_state()

    def _on_ne_unosi_toggle(self):
        self.app.refresh_knjizi_state()

    def is_complete(self) -> bool:
        """
        Redak je spreman za knjiženje ako je označen 'Ne unosi', ILI ako je
        mapiran na artikl I ima upisanu valjanu "Količinu mjere" (jedinicu
        u kojoj se artikl prodaje/zaprima - npr. 1 za komad, 0,7 za rastočenu
        bocu od 0,7L i sl.).
        """
        if self.var_ne_unosi.get():
            return True
        if self.id_robe is None:
            return False
        return self._kolicina_mjere_valid()

    def _kolicina_mjere_valid(self) -> bool:
        text = self.var_kolicina_ulaz.get().strip()
        if not text:
            return False
        try:
            Decimal(text.replace(",", "."))
            return True
        except Exception:
            return False

    def get_kolrobe(self) -> Decimal:
        try:
            return Decimal(self.var_kolicina_ulaz.get().replace(",", "."))
        except Exception:
            return Decimal("0")


class App:
    def __init__(self, root: tk.Tk, cfg: Config):
        self.root = root
        self.cfg = cfg
        self.root.title("Uvoz eRačuna u iSustav")
        self.root.geometry("1050x750")

        self.id_posjed = None
        self.id_sklad = None
        self.id_tvrtke = None
        self.zaglavlje = None
        self.xml_path = None
        self.rows = []
        self._posjed_map = {}
        self._sklad_map = {}

        # Autocomplete popup (dijeljen među svim retcima tablice stavki)
        self._ac_popup = None
        self._ac_listbox = None
        self._ac_row = None       # StavkaRow koji trenutno koristi popup
        self._ac_items = []       # trenutna lista (idrobe, naziv) u popupu

        try:
            self.conn = db.connect(cfg)
            db.ensure_schema(self.conn)
        except Exception as e:
            messagebox.showerror(
                "Greška spajanja na bazu",
                f"Nije moguće uspostaviti vezu s bazom podataka ili pripremiti "
                f"potrebne tabele:\n{e}",
            )
            self.conn = None
            self.root.after(100, self.root.destroy)
            return

        self._build_ui()
        self._load_posjed()

    # ------------------------------------------------------------------
    # Izgradnja sučelja
    # ------------------------------------------------------------------
    def _build_ui(self):
        top = ttk.Frame(self.root, padding=10)
        top.pack(fill="x")

        ttk.Label(top, text="Poslovnica").grid(row=0, column=0, sticky="w")
        self.cb_posjed = ttk.Combobox(top, state="readonly", width=26)
        self.cb_posjed.grid(row=1, column=0, sticky="w", padx=(0, 25))
        self.cb_posjed.bind("<<ComboboxSelected>>", self._on_posjed_change)

        ttk.Label(top, text="Skladište").grid(row=0, column=1, sticky="w")
        self.cb_sklad = ttk.Combobox(top, state="readonly", width=26)
        self.cb_sklad.grid(row=1, column=1, sticky="w")
        self.cb_sklad.bind("<<ComboboxSelected>>", self._on_sklad_change)

        ttk.Label(top, text="Dobavljač").grid(row=2, column=0, sticky="w", pady=(12, 0))
        self.lbl_dobavljac = ttk.Label(top, text="", relief="sunken", width=42, anchor="w")
        self.lbl_dobavljac.grid(row=3, column=0, columnspan=2, sticky="w")

        ttk.Label(top, text="Datum").grid(row=2, column=2, sticky="w", pady=(12, 0), padx=(25, 0))
        self.lbl_datum = ttk.Label(top, text="", relief="sunken", width=16, anchor="w")
        self.lbl_datum.grid(row=3, column=2, sticky="w", padx=(25, 0))

        ttk.Label(top, text="Datum zaprimanja").grid(row=2, column=3, sticky="w", pady=(12, 0))
        self.lbl_datum_zaprimanja = ttk.Label(top, text="", relief="sunken", width=16, anchor="w")
        self.lbl_datum_zaprimanja.grid(row=3, column=3, sticky="w")

        ttk.Label(top, text="Broj ulaznog dokumenta").grid(row=4, column=0, sticky="w", pady=(12, 0))
        self.lbl_broj_dok = ttk.Label(top, text="", relief="sunken", width=26, anchor="w")
        self.lbl_broj_dok.grid(row=5, column=0, sticky="w")

        # --- Tablica stavki (scrollabilna) ---
        table_container = ttk.Frame(self.root, padding=(10, 5))
        table_container.pack(fill="both", expand=True)

        hscroll = ttk.Scrollbar(table_container, orient="horizontal")
        hscroll.pack(side="bottom", fill="x")

        canvas_row = ttk.Frame(table_container)
        canvas_row.pack(fill="both", expand=True)

        canvas = tk.Canvas(canvas_row, borderwidth=0, highlightthickness=0)
        vscroll = ttk.Scrollbar(canvas_row, orient="vertical", command=canvas.yview)
        hscroll.config(command=canvas.xview)
        self.grid_frame = ttk.Frame(canvas)

        self.grid_frame.bind(
            "<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all"))
        )
        canvas.create_window((0, 0), window=self.grid_frame, anchor="nw")
        canvas.configure(yscrollcommand=vscroll.set, xscrollcommand=hscroll.set)
        canvas.pack(side="left", fill="both", expand=True)
        vscroll.pack(side="right", fill="y")

        # Scrollanje kotačićem miša: okomito standardno, vodoravno uz Shift
        # (inače je moguće samo povlačenje scrollbara, što lako ostavlja dojam
        # da su nazivi artikala odrezani ili da nedostaju retci).
        def _on_mousewheel(event):
            canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")

        def _on_shift_mousewheel(event):
            canvas.xview_scroll(int(-1 * (event.delta / 120)), "units")

        canvas.bind("<Enter>", lambda e: (
            canvas.bind_all("<MouseWheel>", _on_mousewheel),
            canvas.bind_all("<Shift-MouseWheel>", _on_shift_mousewheel),
        ))
        canvas.bind("<Leave>", lambda e: (
            canvas.unbind_all("<MouseWheel>"),
            canvas.unbind_all("<Shift-MouseWheel>"),
        ))

        headers = [
            "R.br.", "Šifra artikla\ndobavljača", "Naziv artikla\ndobavljača",
            "Kol.", "Cijena", "Naziv artikla", "Količina\nmjere",
            "Povratna\nnaknada", "Ne unosi",
        ]
        for c, h in enumerate(headers):
            ttk.Label(
                self.grid_frame, text=h, font=("TkDefaultFont", 9, "bold"),
                anchor="center", justify="center",
            ).grid(row=0, column=c, padx=1, pady=2, sticky="nsew")

        totals_frame = ttk.Frame(self.root, padding=(10, 0))
        totals_frame.pack(fill="x")
        self.lbl_pov_nak_total = ttk.Label(
            totals_frame, text="Povratna naknada ukupno: 0",
            font=("TkDefaultFont", 9, "bold"),
        )
        self.lbl_pov_nak_total.pack(side="right", padx=(0, 25))

        bottom = ttk.Frame(self.root, padding=10)
        bottom.pack(fill="x")
        ttk.Button(bottom, text="Učitaj XML ...", command=self._on_ucitaj_xml).pack(side="left")
        self.btn_knjizi = ttk.Button(bottom, text="Knjiži", state="disabled", command=self._on_knjizi)
        self.btn_knjizi.pack(side="right")

    # ------------------------------------------------------------------
    # Dropdownovi Poslovnica / Skladište
    # ------------------------------------------------------------------
    def _load_posjed(self):
        posjed_rows = db.fetch_posjed_list(self.conn)
        self._posjed_map = {naziv: idp for idp, naziv in posjed_rows}
        self.cb_posjed["values"] = list(self._posjed_map.keys())

        # Provjeri ima li SKLAD tabela (globalno, bez filtera po poslovnici)
        # samo jedan slog - treba nam za oba scenarija ispod.
        sklad_rows_all = db.fetch_sklad_list(self.conn)
        single_sklad = sklad_rows_all[0] if len(sklad_rows_all) == 1 else None

        if len(posjed_rows) == 1:
            # POSJED ima samo jedan slog - automatski odaberi tu poslovnicu
            idposjed, naziv_posjed = posjed_rows[0]
            self.cb_posjed.set(naziv_posjed)
            self.id_posjed = idposjed

            # Popuni Skladište dropdown filtrirano po ovoj (jedinoj) poslovnici,
            # kao i inače pri promjeni poslovnice
            self._sklad_map = {naziv: ids for ids, naziv in db.fetch_sklad_list(self.conn, self.id_posjed)}
            self.cb_sklad["values"] = list(self._sklad_map.keys())

            # Ako i SKLAD (za ovu poslovnicu) ima samo jedan slog, odaberi i njega
            if len(self._sklad_map) == 1:
                naziv_sklad = next(iter(self._sklad_map))
                self.cb_sklad.set(naziv_sklad)
                self.id_sklad = self._sklad_map[naziv_sklad]

        elif single_sklad is not None:
            # Više poslovnica postoji, ali SKLAD tabela ima samo jedan slog u
            # cijeloj bazi - ipak ga izravno ponudimo u Skladište polju (ne
            # čekamo da korisnik prvo ručno odabere poslovnicu), jer druge
            # opcije ionako nema.
            idsklad, naziv_sklad = single_sklad
            self._sklad_map = {naziv_sklad: idsklad}
            self.cb_sklad["values"] = [naziv_sklad]
            self.cb_sklad.set(naziv_sklad)
            self.id_sklad = idsklad

    def _on_posjed_change(self, event):
        self.id_posjed = self._posjed_map.get(self.cb_posjed.get())
        self._sklad_map = {naziv: ids for ids, naziv in db.fetch_sklad_list(self.conn, self.id_posjed)}
        self.cb_sklad["values"] = list(self._sklad_map.keys())
        self.cb_sklad.set("")
        self.id_sklad = None

    def _on_sklad_change(self, event):
        self.id_sklad = self._sklad_map.get(self.cb_sklad.get())

    # ------------------------------------------------------------------
    # Učitaj XML
    # ------------------------------------------------------------------
    def _on_ucitaj_xml(self):
        path = filedialog.askopenfilename(
            title="Odaberi eRačun (XML)",
            initialdir=self.cfg.dir_xml or ".",
            filetypes=[("XML datoteke", "*.xml")],
        )
        if not path:
            return

        try:
            zaglavlje = parser.parse_ubl_invoice(path)
        except ValueError as e:
            messagebox.showerror("Greška u XML datoteci", str(e))
            return

        if not db.firma_oib_postoji(self.conn, zaglavlje.oib_kupca):
            messagebox.showerror(
                "Pogrešan kupac",
                f"Izabrana XML datoteka se odnosi na tvrtku {zaglavlje.naziv_kupca}",
            )
            return

        tvrtka = db.fetch_tvrtka_by_oib(self.conn, zaglavlje.oib_dobavljaca)
        if tvrtka is None:
            messagebox.showerror(
                "Dobavljač ne postoji",
                f"Dobavljač s OIB-om {zaglavlje.oib_dobavljaca} ne postoji u tablici "
                f"dobavljača. Upišite ga u aplikaciji iSustav.",
            )
            return
        self.id_tvrtke, naziv_tvrtke = tvrtka

        postojeci_broj = db.fetch_broj_primke(self.conn, zaglavlje.ref_key)
        if postojeci_broj is not None:
            nastavi = messagebox.askyesno(
                "Račun je već proknjižen",
                f"XML datoteka koju učitavate je već proknjižena kao primka "
                f"pod brojem {postojeci_broj}.\n\n"
                f"Želite li je svejedno učitati na formu (bez mogućnosti "
                f"ponovnog knjiženja)?",
            )
            if not nastavi:
                return

        self.zaglavlje = zaglavlje
        self.xml_path = path
        self.lbl_dobavljac.config(text=naziv_tvrtke)
        self.lbl_datum.config(text=hr_date(zaglavlje.datum_izdavanja))
        self.lbl_datum_zaprimanja.config(text=hr_date(zaglavlje.datum_zaprimanja))
        self.lbl_broj_dok.config(text=zaglavlje.broj_dokumenta)

        self._populate_grid(zaglavlje.stavke)

    def _populate_grid(self, stavke):
        self.hide_autocomplete()
        for widget in self.grid_frame.winfo_children():
            if widget.grid_info().get("row", 0) != 0:  # zadrži red 0 (header)
                widget.destroy()

        self.rows = [
            StavkaRow(self.grid_frame, self, i, stavka)
            for i, stavka in enumerate(stavke, start=1)
        ]
        self.refresh_knjizi_state()

    def refresh_knjizi_state(self):
        spreman = bool(self.rows) and all(r.is_complete() for r in self.rows)
        self.btn_knjizi.config(state="normal" if spreman else "disabled")
        self._update_pov_nak_total()

    def _update_pov_nak_total(self):
        """
        Zbraja XML količine (kolona "Kol.") svih redaka označenih "Povratna
        naknada" i prikazuje ukupan zbroj ispod tablice.
        """
        ukupno = sum(
            (r.stavka.kolicina for r in self.rows if r.var_pov_nak.get()),
            Decimal("0"),
        )
        self.lbl_pov_nak_total.config(text=f"Povratna naknada ukupno: {format_decimal(ukupno)}")

    # ------------------------------------------------------------------
    # Knjiži
    # ------------------------------------------------------------------
    def _on_knjizi(self):
        """
        Sprema mapiranja u ERACUN_VEZE i evidenciju u ERACUN_PRIMKE, te
        upisuje primku u GASZG (zaglavlje) i GASST (stavke) - sve u jednoj
        transakciji (commit na kraju, rollback ako bilo što pukne).

        Napomena: cijene, iznosi i porezni izračuni u GASST NISU još
        mapirani (dogovoreno da se rade u sljedećem koraku) - upisuju se
        samo polja dogovorena za ovu fazu.
        """
        if self.id_posjed is None or self.id_sklad is None:
            messagebox.showwarning(
                "Nedostaju podaci", "Odaberite poslovnicu i skladište prije knjiženja."
            )
            return

        # Čvrsta provjera duplog knjiženja - ERACUN_PRIMKE zapis se upisuje u
        # istoj transakciji kao GASZG/GASST, pa njegovo postojanje uvijek
        # znači da je taj eRačun već stvarno proknjižen (ne samo učitan).
        postojeci_broj = db.fetch_broj_primke(self.conn, self.zaglavlje.ref_key)
        if postojeci_broj is not None:
            messagebox.showerror(
                "Račun je već proknjižen",
                f"XML datoteka koju knjižite je već proknjižena kao primka "
                f"pod brojem {postojeci_broj}.",
            )
            return

        try:
            for row in self.rows:
                if row.var_ne_unosi.get():
                    continue
                db.save_veza(
                    self.conn,
                    id_tvrtke=self.id_tvrtke,
                    sifra_dobavljaca=row.stavka.sifra_dobavljaca,
                    naziv_dobavljaca=row.stavka.naziv_dobavljaca,
                    kol_dobavljaca=row.stavka.kolicina,
                    id_robe=row.id_robe,
                    kolrobe=row.get_kolrobe(),
                    pov_nak=row.var_pov_nak.get(),
                    ne_unosi=row.var_ne_unosi.get(),
                )

            # --- Upis primke: GASZG (zaglavlje) + GASST (stavke) ---
            datdok = parse_iso_date(self.zaglavlje.datum_izdavanja)

            idgaszg, brdok = db.insert_gaszg(
                self.conn,
                id_posjed=self.id_posjed,
                id_sklad=self.id_sklad,
                id_tvrtke=self.id_tvrtke,
                sisuser=self.cfg.idsisuser,
                datdok=datdok,
                brotp=self.zaglavlje.broj_dokumenta,
                oznplac=self.cfg.idtransakcijski,
            )

            db.save_eracun_primka(
                self.conn, self.zaglavlje.ref_key, self.id_tvrtke, broj_primke=brdok
            )

            for row in self.rows:
                if row.var_ne_unosi.get():
                    continue
                kol = row.stavka.kolicina * row.get_kolrobe()
                fakcijena = row.stavka.cijena
                fakiznos = row.stavka.iznos_retka  # autoritativan (cbc:LineExtensionAmount), ne kolicina*cijena
                ambcijena = self.cfg.povratna_naknada if row.var_pov_nak.get() else Decimal("0")
                db.insert_gasst(
                    self.conn,
                    id_gaszg=idgaszg,
                    id_posjed=self.id_posjed,
                    id_sklad=self.id_sklad,
                    id_tvrtke=self.id_tvrtke,
                    brdok=brdok,
                    datdok=datdok,
                    idosnrobe=row.id_robe,
                    kol=kol,
                    fakcijena=fakcijena,
                    fakiznos=fakiznos,
                    ulporpos=row.stavka.porez_posto,
                    ambcijena=ambcijena,
                )

            self.conn.commit()

            self._rename_ucitani_xml()

            messagebox.showinfo(
                "Primka knjižena",
                f"Primka je uspješno knjižena (broj dokumenta {brdok}).",
            )
            self._populate_grid([])  # isprazni tablicu - dokument je proknjižen
        except Exception as e:
            self.conn.rollback()
            messagebox.showerror("Greška pri knjiženju", str(e))

    def _rename_ucitani_xml(self):
        """
        Nakon uspješnog knjiženja, XML datoteci dodaje prefiks 'UCITANO-' u
        nazivu (na disku, u istom folderu) - tako se već obrađene datoteke
        abecedno spuštaju na dno foldera (nazivi su brojevi, 'U' je pri kraju
        abecede). Greška pri preimenovanju NE poništava već uspješno
        knjiženje - samo se korisniku prikaže upozorenje.
        """
        if not self.xml_path or not os.path.exists(self.xml_path):
            return

        directory, filename = os.path.split(self.xml_path)
        if filename.startswith("UCITANO-"):
            return  # već preimenovano (ne bi se smjelo dogoditi, ali za svaki slučaj)

        novi_put = os.path.join(directory, f"UCITANO-{filename}")
        try:
            os.rename(self.xml_path, novi_put)
            self.xml_path = novi_put
        except OSError as e:
            messagebox.showwarning(
                "Preimenovanje nije uspjelo",
                f"Primka je uspješno knjižena, ali XML datoteku nije bilo moguće "
                f"preimenovati:\n{e}",
            )

    # ------------------------------------------------------------------
    # Autocomplete popup za kolonu "Naziv artikla" (dijeljen među retcima)
    # ------------------------------------------------------------------
    def _ensure_autocomplete_popup(self):
        if self._ac_popup is not None:
            return
        popup = tk.Toplevel(self.root)
        popup.withdraw()
        popup.overrideredirect(True)
        try:
            popup.attributes("-topmost", True)
        except tk.TclError:
            pass

        frame = ttk.Frame(popup, borderwidth=1, relief="solid")
        frame.pack(fill="both", expand=True)

        listbox = tk.Listbox(frame, height=8, activestyle="dotbox", exportselection=False)
        scroll = ttk.Scrollbar(frame, orient="vertical", command=listbox.yview)
        listbox.configure(yscrollcommand=scroll.set)
        listbox.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")

        listbox.bind("<ButtonRelease-1>", self._on_autocomplete_click)

        self._ac_popup = popup
        self._ac_listbox = listbox

    def show_autocomplete(self, row: "StavkaRow", results):
        """Prikazuje padajuću listu ispod zadanog retka, s rezultatima pretrage."""
        self._ensure_autocomplete_popup()
        self._ac_row = row
        self._ac_items = list(results)

        self._ac_listbox.delete(0, "end")
        for _, naziv in self._ac_items:
            self._ac_listbox.insert("end", naziv)

        if self._ac_items:
            self._ac_listbox.selection_clear(0, "end")
            self._ac_listbox.selection_set(0)
            self._ac_listbox.activate(0)

        row.entry.update_idletasks()
        x = row.entry.winfo_rootx()
        y = row.entry.winfo_rooty() + row.entry.winfo_height()
        width = max(row.entry.winfo_width(), 240)
        height = min(160, 20 * len(self._ac_items) + 10) or 160
        self._ac_popup.geometry(f"{width}x{height}+{x}+{y}")
        self._ac_popup.deiconify()
        self._ac_popup.lift()

        # deiconify/-topmost može resetirati fokus (čak i na None) - vraćamo
        # ga eksplicitno na entry, i odmah i odgođeno (za slučaj da WM na
        # ciljnoj platformi fokus mijenja asinkrono nakon mapiranja prozora).
        row.entry.focus_force()
        self.root.after(30, lambda: row.entry.focus_force() if self._ac_row is row else None)

    def hide_autocomplete(self):
        if self._ac_popup is not None:
            self._ac_popup.withdraw()
        self._ac_row = None
        self._ac_items = []

    def hide_autocomplete_if_not_focused(self, row: "StavkaRow"):
        """
        Poziva se odgođeno (nakon FocusOut) - sakriva popup samo ako fokus
        NIJE ostao unutar entry polja niti unutar samog popupa (npr. klik
        na stavku liste ili na scrollbar).
        """
        if self._ac_row is not row:
            return
        focus_widget = self.root.focus_get()
        if focus_widget is row.entry:
            return
        if self._ac_popup is not None and focus_widget is not None:
            try:
                if str(focus_widget).startswith(str(self._ac_popup)):
                    return
            except Exception:
                pass
        self.hide_autocomplete()

    def move_autocomplete_selection(self, row: "StavkaRow", delta: int):
        if self._ac_row is not row or not self._ac_items or self._ac_popup is None:
            return
        if not self._ac_popup.winfo_viewable():
            return
        size = self._ac_listbox.size()
        if size == 0:
            return
        cur = self._ac_listbox.curselection()
        idx = cur[0] if cur else -1
        idx = max(0, min(size - 1, idx + delta))
        self._ac_listbox.selection_clear(0, "end")
        self._ac_listbox.selection_set(idx)
        self._ac_listbox.activate(idx)
        self._ac_listbox.see(idx)

    def select_autocomplete_current(self, row: "StavkaRow"):
        if self._ac_row is not row or self._ac_popup is None or not self._ac_popup.winfo_viewable():
            return
        cur = self._ac_listbox.curselection()
        if not cur:
            return
        idrobe, naziv = self._ac_items[cur[0]]
        row.apply_selection(idrobe, naziv)
        self.hide_autocomplete()
        row.entry.focus_force()
        row.entry.icursor("end")

    def _on_autocomplete_click(self, event):
        if self._ac_row is None:
            return
        sel = self._ac_listbox.curselection()
        if not sel:
            return
        idrobe, naziv = self._ac_items[sel[0]]
        row = self._ac_row
        row.apply_selection(idrobe, naziv)
        self.hide_autocomplete()
        row.entry.focus_force()
        row.entry.icursor("end")
