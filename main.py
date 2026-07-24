"""
Ulazna točka aplikacije "Uvoz eRačuna u iSustav".

Pokretanje:  python main.py
Očekuje param.ini u istom direktoriju.
"""
import tkinter as tk
from tkinter import messagebox

from config import load_config
from gui import App


def main():
    try:
        cfg = load_config("param.ini")
    except Exception as e:
        # Prikaz greške i prije nego se glavni prozor uopće otvori
        root = tk.Tk()
        root.withdraw()
        messagebox.showerror("Greška u konfiguraciji", str(e))
        return

    root = tk.Tk()
    App(root, cfg)
    root.mainloop()


if __name__ == "__main__":
    main()
