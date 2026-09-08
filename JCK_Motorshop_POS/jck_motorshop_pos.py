import sqlite3
import os
import sys
import csv
import shutil
from datetime import datetime, timedelta
import tkinter as tk
from tkinter import ttk, messagebox, simpledialog, filedialog

import matplotlib
matplotlib.use("TkAgg")
from matplotlib.figure import Figure
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg

# When running as a plain .py script, APP is the script's folder.
# When bundled into a PyInstaller .exe, APP must point next to the actual
# .exe (not the temporary _MEIPASS extraction folder), so the database and
# backups are saved permanently instead of vanishing when the app closes.
if getattr(sys, "frozen", False):
    APP = os.path.dirname(sys.executable)
else:
    APP = os.path.dirname(os.path.abspath(__file__))

DB = os.path.join(APP, "jck_motorshop_pos.db")
BACKUP_DIR = os.path.join(APP, "backups")
os.makedirs(BACKUP_DIR, exist_ok=True)


def resource_path(filename):
    """Locate a bundled resource (like the app icon) whether running as a
    plain script or as a PyInstaller onefile exe."""
    base = getattr(sys, "_MEIPASS", APP)
    return os.path.join(base, filename)


def con():
    x = sqlite3.connect(DB, timeout=10)  # 10 second timeout to wait if database is locked
    x.row_factory = sqlite3.Row
    return x


def init():
    c = con()
    c.executescript("""
    CREATE TABLE IF NOT EXISTS products(
        id INTEGER PRIMARY KEY,
        name TEXT UNIQUE,
        category TEXT,
        type TEXT DEFAULT 'Part/Material',
        price REAL,
        cost REAL,
        stock REAL DEFAULT 0,
        reorder REAL DEFAULT 0,
        active INTEGER DEFAULT 1
    );
    CREATE TABLE IF NOT EXISTS sales(
        id INTEGER PRIMARY KEY,
        receipt TEXT UNIQUE,
        time TEXT,
        customer TEXT,
        subtotal REAL,
        discount REAL,
        total REAL,
        payment TEXT,
        received REAL,
        change REAL,
        cashier TEXT
    );
    CREATE TABLE IF NOT EXISTS items(
        id INTEGER PRIMARY KEY,
        sale_id INTEGER,
        product_id INTEGER,
        name TEXT,
        qty REAL,
        price REAL,
        cost REAL,
        total REAL,
        line_type TEXT DEFAULT 'Part/Material',
        parent_product_id INTEGER
    );
    CREATE TABLE IF NOT EXISTS expenses(
        id INTEGER PRIMARY KEY,
        time TEXT,
        category TEXT,
        description TEXT,
        amount REAL,
        payment TEXT,
        payee TEXT
    );
    CREATE TABLE IF NOT EXISTS employees(
        id INTEGER PRIMARY KEY,
        name TEXT UNIQUE,
        comm_service REAL DEFAULT 0,
        comm_item REAL DEFAULT 0,
        active INTEGER DEFAULT 1
    );
    CREATE TABLE IF NOT EXISTS restocks(
        id INTEGER PRIMARY KEY,
        product_id INTEGER,
        qty REAL,
        time TEXT,
        note TEXT
    );
    CREATE TABLE IF NOT EXISTS service_rules(
        id INTEGER PRIMARY KEY,
        service_id INTEGER NOT NULL,
        product_id INTEGER NOT NULL,
        UNIQUE(service_id, product_id)
    );
    """)

    # Migrate older databases.
    product_cols = [r["name"] for r in c.execute("PRAGMA table_info(products)").fetchall()]
    if "type" not in product_cols:
        c.execute("ALTER TABLE products ADD COLUMN type TEXT DEFAULT 'Part/Material'")
        c.execute("UPDATE products SET type='Service' WHERE lower(category)='service'")
        c.execute("UPDATE products SET type='Part/Material' WHERE type IS NULL OR type=''")

    item_cols = [r["name"] for r in c.execute("PRAGMA table_info(items)").fetchall()]
    if "line_type" not in item_cols:
        c.execute("ALTER TABLE items ADD COLUMN line_type TEXT DEFAULT 'Part/Material'")
    if "parent_product_id" not in item_cols:
        c.execute("ALTER TABLE items ADD COLUMN parent_product_id INTEGER")

    # Convert old service products correctly.
    c.execute("UPDATE products SET type='Service' WHERE lower(category)='service' AND (type IS NULL OR type='Part/Material')")

    samples = [
        ("Tire Installation", "Installation", "Service", 150, 40, 0, 0),
        ("Tire Patching", "Repair", "Service", 100, 20, 0, 0),
        ("Tire Repair", "Repair", "Service", 100, 20, 0, 0),
        ("Tire Rotation", "Maintenance", "Service", 200, 20, 0, 0),
        ("Wheel Balancing", "Balancing", "Service", 250, 80, 0, 0),
        ("Tire Valve", "Parts", "Part/Material", 50, 20, 20, 5),
        ("Tire Patch", "Materials", "Part/Material", 30, 10, 30, 10),
        ("Tube", "Parts", "Part/Material", 250, 180, 5, 2),
        ("Tire", "Tires", "Part/Material", 2500, 2000, 3, 1),
    ]
    for s in samples:
        try:
            c.execute("""INSERT INTO products(name,category,type,price,cost,stock,reorder)
                         VALUES(?,?,?,?,?,?,?)""", s)
        except sqlite3.IntegrityError:
            pass

    try:
        c.execute("INSERT INTO employees(name,comm_service,comm_item) VALUES('Owner',0,0)")
    except sqlite3.IntegrityError:
        pass

    # Default applicability rules. Only insert if the pair does not exist.
    def pid(name, typ):
        r = c.execute("SELECT id FROM products WHERE name=? AND type=?", (name, typ)).fetchone()
        return r["id"] if r else None

    service_rules = [
        ("Tire Installation", "Tire"),
        ("Wheel Balancing", "Tire"),
        ("Tire Rotation", "Tire"),
        ("Tire Repair", "Tire"),
        ("Tire Patching", "Tire"),
        ("Tire Installation", "Tube"),
        ("Tire Repair", "Tube"),
        ("Tire Patching", "Tube"),
        ("Tire Valve", "Tire"),
    ]
    for service_name, product_name in service_rules:
        sid = pid(service_name, "Service")
        prd = pid(product_name, "Part/Material")
        if sid and prd:
            c.execute("INSERT OR IGNORE INTO service_rules(service_id,product_id) VALUES(?,?)", (sid, prd))

    c.commit()
    c.close()


class POS(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("JCK Motorshop POS")
        self.set_app_icon()
        self.geometry("1250x820")
        self.minsize(1050, 700)
        self.apply_brand_theme()
        self.cart = []              # Parts/materials + special orders only
        self.services = []          # Applied services, kept separate from cart
        self.selected_product_id = None
        self.build()
        self.refresh()
        self.refresh_inv()
        self.refresh_employees()
        self.refresh_service_panel()
        self.update_total()
        self.refresh_charts()

    def apply_brand_theme(self):
        self.configure(bg="#101828")
        style = ttk.Style(self)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass

        self.tk_setPalette(
            background="#101828",
            foreground="#E2E8F0",
            highlightColor="#38BDF8",
            insertBackground="#F8FAFC",
            selectBackground="#0EA5E9",
            selectForeground="#F8FAFC",
            activeBackground="#0F172A",
            activeForeground="#F8FAFC",
        )

        style.configure(".", background="#101828", foreground="#E2E8F0", fieldbackground="#F8FAFC")
        style.configure("TFrame", background="#101828")
        style.configure("TLabel", background="#101828", foreground="#E2E8F0")
        style.configure("TEntry", fieldbackground="#F8FAFC", foreground="#0F172A")
        style.configure("TCombobox", fieldbackground="#F8FAFC", foreground="#0F172A")
        style.configure("TNotebook", background="#101828", borderwidth=0)
        style.configure("TNotebook.Tab", background="#1F2937", foreground="#DDE7F5", padding=(18, 10), font=("Segoe UI", 9, "bold"))
        style.map("TNotebook.Tab", background=[("selected", "#101828"), ("active", "#1F2937")], foreground=[("selected", "#F8FAFC"), ("active", "#F8FAFC")])
        style.configure("TLabelframe", background="#101828")
        style.configure("TLabelframe.Label", background="#101828", foreground="#F8FAFC", font=("Segoe UI", 10, "bold"))
        style.configure("TSeparator", background="#334155")
        style.configure("Treeview", background="#0F172A", fieldbackground="#0F172A", foreground="#E2E8F0", rowheight=26)
        style.map("Treeview", background=[("selected", "#0EA5E9")], foreground=[("selected", "#F8FAFC")])
        style.configure("Treeview.Heading", background="#1F2937", foreground="#F8FAFC", relief="flat", font=("Segoe UI", 9, "bold"))
        style.map("Treeview.Heading", background=[("active", "#334155")])
        style.configure("Horizontal.TScrollbar", background="#1F2937", troughcolor="#0F172A", arrowcolor="#E2E8F0")
        style.configure("Vertical.TScrollbar", background="#1F2937", troughcolor="#0F172A", arrowcolor="#E2E8F0")

        style.configure("Brand.TButton", background="#0F172A", foreground="#F8FAFC", padding=(12, 8), relief="flat", borderwidth=0, font=("Segoe UI", 9, "bold"))
        style.map("Brand.TButton", background=[("active", "#1D4ED8"), ("pressed", "#1E3A8A")], foreground=[("active", "#F8FAFC"), ("pressed", "#F8FAFC")])

        style.configure("Accent.TButton", background="#F97316", foreground="#FFF7ED", padding=(12, 8), relief="flat", borderwidth=0, font=("Segoe UI", 9, "bold"))
        style.map("Accent.TButton", background=[("active", "#EA580C"), ("pressed", "#C2410C")], foreground=[("active", "#FFF7ED"), ("pressed", "#FFF7ED")])

    def set_app_icon(self):
        """Load the shop logo as the window/taskbar icon.
        Tries the .ico file first (best for Windows taskbar), then falls
        back to the .png (works cross-platform). Fails silently if the
        icon files aren't next to the script, so the app still runs fine
        without a custom icon."""
        ico_path = resource_path("jck_logo.ico")
        png_path = resource_path("jck_logo.png")
        try:
            if os.path.exists(ico_path):
                self.iconbitmap(ico_path)
                return
        except tk.TclError:
            pass
        try:
            if os.path.exists(png_path):
                self._icon_img = tk.PhotoImage(file=png_path)  # keep a reference
                self.iconphoto(True, self._icon_img)
        except tk.TclError:
            pass

    # =========================================================
    # UI BUILD
    # =========================================================
    def build(self):
        header = tk.Frame(self, bg="#101828", height=90)
        header.pack(fill="x", pady=0)
        accent_bar = tk.Frame(header, bg="#1D4ED8", height=6)
        accent_bar.pack(fill="x")

        brand_row = tk.Frame(header, bg="#101828")
        brand_row.pack(fill="x", padx=18, pady=(12, 12))

        logo_path = resource_path("jck_logo.png")
        if os.path.exists(logo_path):
            try:
                self.brand_logo = tk.PhotoImage(file=logo_path)
                self.brand_logo = self.brand_logo.subsample(4, 4)
                tk.Label(brand_row, image=self.brand_logo, bg="#101828").pack(side="left")
            except Exception:
                pass

        title_wrap = tk.Frame(brand_row, bg="#101828")
        title_wrap.pack(side="left", padx=(14, 0))
        tk.Label(title_wrap, text="JCK", fg="#F8FAFC", bg="#101828", font=("Segoe UI", 30, "bold")).pack(anchor="w")
        tk.Label(title_wrap, text="MOTORSHOP", fg="#7DD3FC", bg="#101828", font=("Segoe UI", 14, "bold")).pack(anchor="w")

        tk.Label(brand_row, text="POS • SALES • SERVICE • INVENTORY", fg="#E2E8F0", bg="#101828", font=("Segoe UI", 9, "bold"), justify="right").pack(side="right", anchor="s", pady=(16, 0))

        n = ttk.Notebook(self)
        n.pack(fill="both", expand=True, padx=10, pady=(0, 10))
        self.sale = ttk.Frame(n)
        self.inv = ttk.Frame(n)
        self.rep = ttk.Frame(n)
        self.exp = ttk.Frame(n)
        self.hlp = ttk.Frame(n)
        n.add(self.sale, text=" SALES / POS ")
        n.add(self.inv, text=" INVENTORY ")
        n.add(self.rep, text=" DASHBOARD ")
        n.add(self.exp, text=" EXPENSES ")
        n.add(self.hlp, text=" HELP / TIPS ")
        self.build_sale()
        self.build_inv()
        self.build_dash()
        self.build_exp()
        self.build_help()

    def build_sale(self):
        # ----- Current sale / checkout -----
        # Packed FIRST with side="bottom" so this section always keeps its
        # reserved space at the bottom of the tab (including the COMPLETE SALE
        # button), no matter how tall the services list above grows.
        bottom = ttk.LabelFrame(self.sale, text="CURRENT SALE — PARTS / MATERIALS ONLY")
        bottom.pack(side="bottom", fill="x", padx=10, pady=(0, 10))

        cart_area = ttk.Frame(bottom)
        cart_area.pack(side="left", fill="both", expand=True, padx=5, pady=5)
        self.ct = ttk.Treeview(cart_area, columns=("name", "qty", "price", "total"), show="headings", height=7)
        for col, head, width in [("name", "Part / Material", 280), ("qty", "Qty", 60), ("price", "Price", 95), ("total", "Total", 105)]:
            self.ct.heading(col, text=head)
            self.ct.column(col, width=width)
        self.ct.pack(fill="both", expand=True)
        cart_btns = ttk.Frame(cart_area)
        cart_btns.pack(fill="x", pady=4)
        ttk.Button(cart_btns, text="REMOVE PART / MATERIAL", command=self.remove, style="Brand.TButton").pack(side="left", fill="x", expand=True)
        ttk.Button(cart_btns, text="CLEAR SALE", command=self.clear, style="Brand.TButton").pack(side="left", fill="x", expand=True, padx=4)

        checkout = ttk.Frame(bottom, width=390)
        checkout.pack(side="right", fill="y", padx=6, pady=5)

        self.customer = tk.StringVar()
        self.discount = tk.StringVar(value="0")
        self.received = tk.StringVar(value="0")
        self.payment = tk.StringVar(value="Cash")
        self.employee = tk.StringVar(value="Owner")

        ttk.Label(checkout, text="Employee").pack(anchor="w")
        self.emp_combo = ttk.Combobox(checkout, textvariable=self.employee, state="readonly")
        self.emp_combo.pack(fill="x")
        ttk.Label(checkout, text="Customer").pack(anchor="w", pady=(4, 0))
        ttk.Entry(checkout, textvariable=self.customer).pack(fill="x")
        ttk.Label(checkout, text="Discount").pack(anchor="w", pady=(4, 0))
        ttk.Entry(checkout, textvariable=self.discount).pack(fill="x")
        ttk.Label(checkout, text="Amount Received").pack(anchor="w", pady=(4, 0))
        ttk.Entry(checkout, textvariable=self.received).pack(fill="x")
        ttk.Label(checkout, text="Payment Method").pack(anchor="w", pady=(4, 0))
        ttk.Combobox(checkout, textvariable=self.payment, values=["Cash", "GCash", "Bank Transfer", "Card", "Credit"], state="readonly").pack(fill="x")

        self.total = ttk.Label(checkout, text="TOTAL: ₱0.00", font=("Arial", 18, "bold"))
        self.total.pack(anchor="e", pady=(5, 0))
        self.change = ttk.Label(checkout, text="Change: ₱0.00")
        self.change.pack(anchor="e")
        ttk.Button(checkout, text="COMPLETE SALE", command=self.sell, style="Accent.TButton").pack(fill="x", pady=5)

        self.discount.trace_add("write", lambda *_: self.update_total())
        self.received.trace_add("write", lambda *_: self.update_total())
        self.payment.trace_add("write", lambda *_: self.update_total())

        # ----- Main top area: products on left, service panel on right. -----
        # Packed AFTER "bottom" and given expand=True, so it takes whatever
        # space remains rather than pushing the checkout section off-screen.
        top = ttk.PanedWindow(self.sale, orient="horizontal")
        top.pack(fill="both", expand=True, padx=10, pady=(10, 4))

        products_frame = ttk.LabelFrame(top, text="PARTS / MATERIALS")
        service_frame = ttk.LabelFrame(top, text="SERVICES / ADD-ONS")
        top.add(products_frame, weight=3)
        top.add(service_frame, weight=2)

        # ----- Parts / materials -----
        search_row = ttk.Frame(products_frame)
        search_row.pack(fill="x", padx=5, pady=5)
        self.search = tk.StringVar()
        ttk.Label(search_row, text="Search:").pack(side="left")
        ttk.Entry(search_row, textvariable=self.search).pack(side="left", fill="x", expand=True, padx=5)
        self.search.trace_add("write", lambda *_: self.refresh())

        self.pt = ttk.Treeview(products_frame, columns=("name", "cat", "price", "stock"), show="headings", height=10)
        for col, head, width in [("name", "Part / Material", 260), ("cat", "Category", 110), ("price", "Price", 100), ("stock", "Stock", 80)]:
            self.pt.heading(col, text=head)
            self.pt.column(col, width=width)
        self.pt.pack(fill="both", expand=True, padx=5)
        self.pt.bind("<<TreeviewSelect>>", self.on_product_select)
        self.pt.bind("<Double-1>", lambda e: self.add())

        btnrow = ttk.Frame(products_frame)
        btnrow.pack(fill="x", padx=5, pady=5)
        ttk.Button(btnrow, text="ADD PART / MATERIAL", command=self.add, style="Brand.TButton").pack(side="left", fill="x", expand=True)
        ttk.Button(btnrow, text="SPECIAL ORDER", command=self.add_custom, style="Brand.TButton").pack(side="left", fill="x", expand=True, padx=4)

        # ----- Services panel -----
        self.service_info = ttk.Label(service_frame, text="Select a part/material to see applicable services.", wraplength=330, justify="left")
        self.service_info.pack(anchor="w", padx=8, pady=(7, 4))

        self.service_buttons = ttk.Frame(service_frame)
        self.service_buttons.pack(fill="x", padx=6)

        ttk.Separator(service_frame).pack(fill="x", padx=6, pady=8)
        ttk.Label(service_frame, text="APPLIED SERVICES", font=("Arial", 10, "bold")).pack(anchor="w", padx=8)
        self.st = ttk.Treeview(service_frame, columns=("service", "qty", "price", "total"), show="headings", height=6)
        for col, head, width in [("service", "Service", 175), ("qty", "Qty", 45), ("price", "Price", 75), ("total", "Total", 85)]:
            self.st.heading(col, text=head)
            self.st.column(col, width=width)
        self.st.pack(fill="both", expand=True, padx=6, pady=5)
        ttk.Button(service_frame, text="REMOVE SELECTED SERVICE", command=self.remove_service, style="Brand.TButton").pack(fill="x", padx=6, pady=(0, 5))

        ttk.Label(service_frame, text="How it works: select a tire/tube/etc. on the left. The applicable services appear here. You can also add services automatically when a product is added.", wraplength=330, justify="left", font=("Arial", 8)).pack(anchor="w", padx=8, pady=5)

    # =========================================================
    # PRODUCT / SERVICE UI
    # =========================================================
    def refresh(self):
        if not hasattr(self, "pt"):
            return
        for x in self.pt.get_children():
            self.pt.delete(x)
        q = self.search.get().lower()
        c = con()
        rows = c.execute("""SELECT * FROM products
                           WHERE active=1 AND type='Part/Material'
                           AND lower(name) LIKE ? ORDER BY name""", (f"%{q}%",)).fetchall()
        c.close()
        for r in rows:
            self.pt.insert("", "end", iid=str(r["id"]), values=(r["name"], r["category"], f"₱{r['price']:,.2f}", f"{r['stock']:g}"))

    def on_product_select(self, _event=None):
        sel = self.pt.selection()
        if not sel:
            self.selected_product_id = None
            self.refresh_service_panel()
            return
        self.selected_product_id = int(sel[0])
        self.refresh_service_panel()

    def get_applicable_services(self, product_id):
        c = con()
        rows = c.execute("""SELECT p.* FROM products p
                           JOIN service_rules sr ON sr.service_id=p.id
                           WHERE sr.product_id=? AND p.type='Service' AND p.active=1
                           ORDER BY p.name""", (product_id,)).fetchall()
        c.close()
        return rows

    def refresh_service_panel(self):
        if not hasattr(self, "service_buttons"):
            return
        for w in self.service_buttons.winfo_children():
            w.destroy()

        if self.selected_product_id is None:
            self.service_info.config(text="Select a part/material to see applicable services.")
            return

        c = con()
        product = c.execute("SELECT name FROM products WHERE id=?", (self.selected_product_id,)).fetchone()
        c.close()
        if not product:
            return

        services = self.get_applicable_services(self.selected_product_id)
        self.service_info.config(text=f"Selected: {product['name']}\nAvailable services for this product:")

        if not services:
            ttk.Label(self.service_buttons, text="No service rules configured for this product.").pack(anchor="w", pady=4)
            return

        for s in services:
            row = ttk.Frame(self.service_buttons)
            row.pack(fill="x", pady=2)
            ttk.Label(row, text=s["name"], width=22).pack(side="left")
            ttk.Label(row, text=f"₱{s['price']:,.2f}").pack(side="left", padx=5)
            ttk.Button(row, text="ADD / APPLY", command=lambda sid=s["id"]: self.add_service(sid, self.selected_product_id)).pack(side="right")

    def ask_services_for_product(self, product_id, product_qty=1):
        services = self.get_applicable_services(product_id)
        if not services:
            return
        c = con()
        product = c.execute("SELECT name FROM products WHERE id=?", (product_id,)).fetchone()
        c.close()
        if not product:
            return

        w = tk.Toplevel(self)
        w.title("Optional Services")
        w.geometry("460x360")
        w.transient(self)
        w.grab_set()

        ttk.Label(w, text=f"{product['name']} added to the sale.", font=("Arial", 13, "bold")).pack(anchor="w", padx=15, pady=(15, 2))
        ttk.Label(w, text="Would the customer like any of these services? Select the ones that apply.", wraplength=420).pack(anchor="w", padx=15, pady=(0, 10))

        vars_ = []
        for s in services:
            v = tk.BooleanVar(value=False)
            vars_.append((s, v))
            cb = ttk.Checkbutton(w, text=f"{s['name']} — ₱{s['price']:,.2f}", variable=v)
            cb.pack(anchor="w", padx=25, pady=4)

        def apply():
            for s, v in vars_:
                if v.get():
                    self.add_service(s["id"], product_id, product_qty, ask=False)
            w.destroy()

        ttk.Separator(w).pack(fill="x", padx=15, pady=10)
        buttons = ttk.Frame(w)
        buttons.pack(fill="x", padx=15)
        ttk.Button(buttons, text="APPLY SELECTED", command=apply).pack(side="left", fill="x", expand=True)
        ttk.Button(buttons, text="NO SERVICE / SKIP", command=w.destroy).pack(side="left", fill="x", expand=True, padx=5)

    def add(self):
        s = self.pt.selection()
        if not s:
            messagebox.showinfo("Part / Material", "Select a part/material first.")
            return
        pid = int(s[0])
        c = con()
        r = c.execute("SELECT * FROM products WHERE id=? AND type='Part/Material'", (pid,)).fetchone()
        c.close()
        if not r:
            return
        q = simpledialog.askfloat("Quantity", f"Quantity of {r['name']}:", initialvalue=1, minvalue=0.01)
        if q is None:
            return
        existing = next((x for x in self.cart if x["id"] == r["id"]), None)
        new_qty = q + (existing["qty"] if existing else 0)
        if new_qty > r["stock"]:
            messagebox.showerror("Stock", f"Not enough stock. Available: {r['stock']:g}")
            return
        if existing:
            existing["qty"] = new_qty
        else:
            self.cart.append({"id": r["id"], "name": r["name"], "qty": q, "price": r["price"], "cost": r["cost"], "type": "Part/Material"})
        self.draw_cart()
        self.selected_product_id = pid
        self.refresh_service_panel()
        # Automatic service choices after a physical item is added.
        self.after(50, lambda: self.ask_services_for_product(pid, q))

    def add_service(self, service_id, parent_product_id=None, qty=1, ask=True):
        if parent_product_id is None:
            parent_product_id = self.selected_product_id
        if parent_product_id is None:
            messagebox.showinfo("Service", "Select the part/material this service applies to first.")
            return

        c = con()
        s = c.execute("SELECT * FROM products WHERE id=? AND type='Service' AND active=1", (service_id,)).fetchone()
        p = c.execute("SELECT name FROM products WHERE id=?", (parent_product_id,)).fetchone()
        c.close()
        if not s or not p:
            return

        # Prevent duplicate application of the same service to the same parent.
        existing = next((x for x in self.services if x["service_id"] == service_id and x["parent_product_id"] == parent_product_id), None)
        if existing:
            existing["qty"] += qty
        else:
            self.services.append({
                "service_id": service_id,
                "parent_product_id": parent_product_id,
                "parent_name": p["name"],
                "name": s["name"],
                "qty": qty,
                "price": s["price"],
                "cost": s["cost"],
                "type": "Service"
            })
        self.draw_services()
        self.update_total()

    def draw_services(self):
        if not hasattr(self, "st"):
            return
        for x in self.st.get_children():
            self.st.delete(x)
        for i, x in enumerate(self.services):
            self.st.insert("", "end", iid=str(i), values=(f"{x['name']} → {x['parent_name']}", x["qty"], f"₱{x['price']:,.2f}", f"₱{x['qty']*x['price']:,.2f}"))

    def remove_service(self):
        s = self.st.selection()
        if not s:
            return
        self.services.pop(int(s[0]))
        self.draw_services()
        self.update_total()

    # =========================================================
    # CART / CHECKOUT
    # =========================================================
    def add_custom(self):
        w = tk.Toplevel(self)
        w.title("Special Order Item")
        w.geometry("340x300")
        name = tk.StringVar()
        qty = tk.StringVar(value="1")
        cost = tk.StringVar()
        price = tk.StringVar()
        for lab, var in [("Item Name", name), ("Quantity", qty), ("Your Cost", cost), ("Selling Price", price)]:
            ttk.Label(w, text=lab).pack(anchor="w", padx=15, pady=(8, 0))
            ttk.Entry(w, textvariable=var).pack(fill="x", padx=15)
        def save():
            try:
                n = name.get().strip(); q = float(qty.get()); cst = float(cost.get()); p = float(price.get())
                if not n: raise ValueError("Item name is required.")
                if q <= 0 or cst < 0 or p < 0: raise ValueError("Invalid values.")
            except Exception as e:
                messagebox.showerror("Special Order", str(e)); return
            self.cart.append({"id": None, "name": n + " (Special Order)", "qty": q, "price": p, "cost": cst, "type": "Special Order"})
            self.draw_cart(); w.destroy()
        ttk.Button(w, text="ADD TO SALE", command=save).pack(pady=14)

    def draw_cart(self):
        for x in self.ct.get_children():
            self.ct.delete(x)
        for i, x in enumerate(self.cart):
            self.ct.insert("", "end", iid=str(i), values=(x["name"], x["qty"], f"₱{x['price']:,.2f}", f"₱{x['qty']*x['price']:,.2f}"))
        self.update_total()

    def remove(self):
        s = self.ct.selection()
        if s:
            self.cart.pop(int(s[0]))
            self.draw_cart()

    def clear(self):
        self.cart = []
        self.services = []
        self.draw_cart()
        self.draw_services()
        self.refresh_service_panel()

    def update_total(self):
        if not hasattr(self, "total"):
            return
        sub_parts = sum(x["qty"] * x["price"] for x in self.cart)
        sub_services = sum(x["qty"] * x["price"] for x in self.services)
        sub = sub_parts + sub_services
        try:
            d = max(0, float(self.discount.get() or 0))
            rec = float(self.received.get() or 0)
        except ValueError:
            d = 0; rec = 0
        total = max(0, sub - d)
        change = max(0, rec - total) if self.payment.get() == "Cash" else 0
        self.total.config(text=f"TOTAL: ₱{total:,.2f}")
        self.change.config(text=f"Change: ₱{change:,.2f}")

    def sell(self):
        if not self.cart and not self.services:
            messagebox.showinfo("Sale", "Sale is empty.")
            return
        sub = sum(x["qty"] * x["price"] for x in self.cart) + sum(x["qty"] * x["price"] for x in self.services)
        try:
            d = max(0, float(self.discount.get() or 0))
            rec = float(self.received.get() or 0)
        except ValueError:
            messagebox.showerror("Payment", "Invalid discount or received amount."); return
        total = max(0, sub - d)
        pay = self.payment.get()
        if pay == "Cash" and rec < total:
            messagebox.showerror("Payment", "Insufficient cash."); return
        if pay == "Credit":
            rec = 0
        change = max(0, rec - total) if pay == "Cash" else 0
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        receipt = "VS" + datetime.now().strftime("%Y%m%d%H%M%S%f")
        c = con()
        try:
            # Validate physical stock one more time before committing.
            for x in self.cart:
                if x["id"] is not None:
                    row = c.execute("SELECT stock FROM products WHERE id=? AND type='Part/Material'", (x["id"],)).fetchone()
                    if not row or row["stock"] < x["qty"]:
                        raise ValueError(f"Not enough stock for {x['name']}.")

            cur = c.cursor()
            cur.execute("""INSERT INTO sales(receipt,time,customer,subtotal,discount,total,payment,received,change,cashier)
                           VALUES(?,?,?,?,?,?,?,?,?,?)""", (receipt, now, self.customer.get(), sub, d, total, pay, rec, change, self.employee.get()))
            sid = cur.lastrowid

            # Parts/materials remain the physical inventory lines.
            for x in self.cart:
                cur.execute("""INSERT INTO items(sale_id,product_id,name,qty,price,cost,total,line_type,parent_product_id)
                               VALUES(?,?,?,?,?,?,?,?,?)""", (sid, x["id"], x["name"], x["qty"], x["price"], x["cost"], x["qty"]*x["price"], x["type"], None))
                if x["id"] is not None and x["type"] == "Part/Material":
                    cur.execute("UPDATE products SET stock=stock-? WHERE id=?", (x["qty"], x["id"]))

            # Services are separate sale lines and never affect stock.
            for x in self.services:
                cur.execute("""INSERT INTO items(sale_id,product_id,name,qty,price,cost,total,line_type,parent_product_id)
                               VALUES(?,?,?,?,?,?,?,?,?)""", (sid, x["service_id"], x["name"], x["qty"], x["price"], x["cost"], x["qty"]*x["price"], "Service", x["parent_product_id"]))

            c.commit()
        except Exception as e:
            c.rollback(); c.close()
            messagebox.showerror("Sale", str(e)); return
        c.close()

        self.receipt(receipt, now, total, pay, rec, change)
        self.clear()
        self.customer.set("")
        self.discount.set("0")
        self.received.set("0")
        self.refresh()
        self.refresh_inv()
        self.refresh_charts()

    def receipt(self, no, now, total, pay, rec, change):
        w = tk.Toplevel(self)
        w.title("Receipt")
        t = tk.Text(w, font=("Courier New", 10), width=55, height=28)
        t.pack(fill="both", expand=True)
        lines = ["JCK MOTORSHOP", "SALES RECEIPT", "-" * 44, f"Receipt: {no}", now, "-" * 44]
        lines.append("PARTS / MATERIALS")
        for x in self.cart:
            lines.append(f"{x['name'][:25]:25} {x['qty']:>4g} {x['qty']*x['price']:>10.2f}")
        if self.services:
            lines += ["", "SERVICES"]
            for x in self.services:
                lines.append(f"{x['name'][:20]:20} {x['qty']:>4g} {x['qty']*x['price']:>10.2f}")
                lines.append(f"  Applied to: {x['parent_name']}")
        lines += ["-" * 44, f"TOTAL:    ₱{total:,.2f}", f"PAYMENT:  {pay}", f"RECEIVED: ₱{rec:,.2f}", f"CHANGE:   ₱{change:,.2f}", "-" * 44, "Thank you!"]
        t.insert("1.0", "\n".join(lines)); t.config(state="disabled")
        ttk.Button(w, text="Print", command=lambda: self.print_receipt("\n".join(lines))).pack(pady=5)

    def print_receipt(self, s):
        f = os.path.join(APP, "receipt.txt")
        with open(f, "w", encoding="utf8") as fh:
            fh.write(s)
        if os.name == "nt":
            try: os.startfile(f, "print")
            except Exception: messagebox.showinfo("Receipt", f"Saved: {f}")

    # =========================================================
    # INVENTORY
    # =========================================================
    def build_inv(self):
        top = ttk.Frame(self.inv); top.pack(fill="x", padx=5, pady=5)
        ttk.Button(top, text="ADD PART / MATERIAL", command=lambda: self.add_product("Part/Material")).pack(side="left")
        ttk.Button(top, text="ADD SERVICE", command=lambda: self.add_product("Service")).pack(side="left", padx=5)
        ttk.Button(top, text="SERVICE RULES", command=self.manage_service_rules).pack(side="left", padx=5)
        ttk.Button(top, text="MANAGE EMPLOYEES", command=self.manage_employees).pack(side="left", padx=5)
        ttk.Button(top, text="RESTOCK SELECTED", command=self.restock).pack(side="left", padx=5)
        ttk.Button(top, text="DELETE SELECTED", command=self.delete_product).pack(side="left", padx=5)
        ttk.Button(top, text="REFRESH", command=self.refresh_inv).pack(side="left", padx=5)
        ttk.Label(self.inv, text="Services do not have stock. Parts/Materials have stock and reorder levels.", font=("Arial", 9, "bold")).pack(anchor="w", padx=5)

        search_frame = ttk.Frame(self.inv); search_frame.pack(fill="x", padx=5, pady=(0, 5))
        ttk.Label(search_frame, text="SEARCH:").pack(side="left")
        self.inv_search_var = tk.StringVar()
        ttk.Entry(search_frame, textvariable=self.inv_search_var, width=40).pack(side="left", padx=(5, 0))
        self.inv_search_var.trace_add("write", lambda *_: self.refresh_inv())

        self.it = ttk.Treeview(self.inv, columns=("id", "name", "type", "cat", "price", "cost", "stock", "reorder", "status"), show="headings")
        self.it.tag_configure("low_stock", foreground="#DC2626")
        for col, head, width in [("id", "ID", 40), ("name", "Item / Service", 220), ("type", "Type", 115), ("cat", "Category", 110), ("price", "Selling", 90), ("cost", "Cost", 90), ("stock", "Stock", 80), ("reorder", "Reorder", 80), ("status", "Status", 90)]:
            self.it.heading(col, text=head); self.it.column(col, width=width)
        self.it.pack(fill="both", expand=True, padx=5, pady=5)

    def refresh_inv(self):
        if not hasattr(self, "it"): return
        for x in self.it.get_children(): self.it.delete(x)
        c = con()
        rows = c.execute("SELECT * FROM products ORDER BY type, name").fetchall()
        c.close()
        query = (getattr(self, "inv_search_var", None).get() if hasattr(self, "inv_search_var") else "").strip().lower()
        for r in rows:
            if query:
                haystack = " ".join([str(r["name"]), str(r["category"]), str(r["type"])])
                if query not in haystack.lower():
                    continue
            if r["type"] == "Service": stock, reorder, status = "—", "—", "N/A"
            else:
                stock, reorder = r["stock"], r["reorder"]
                status = "OUT" if stock <= 0 else ("REORDER" if reorder > 0 and stock <= reorder else "OK")
            tags = ("low_stock",) if r["type"] != "Service" and stock < 10 else ()
            self.it.insert("", "end", values=(r["id"], r["name"], r["type"], r["category"], f"₱{r['price']:,.2f}", f"₱{r['cost']:,.2f}", stock, reorder, status), tags=tags)

    def delete_product(self):
        selected = self.it.selection()
        if not selected:
            messagebox.showinfo("Delete Item", "Select an item or service first.")
            return
        values = self.it.item(selected[0], "values")
        product_id, name, item_type = int(values[0]), values[1], values[2]
        if not messagebox.askyesno("Delete Item", f"Delete '{name}' from inventory?\n\nThis cannot be undone."):
            return
        c = con()
        try:
            c.execute("DELETE FROM service_rules WHERE service_id=? OR product_id=?", (product_id, product_id))
            c.execute("DELETE FROM restocks WHERE product_id=?", (product_id,))
            c.execute("DELETE FROM products WHERE id=?", (product_id,))
            c.commit()
        finally:
            c.close()
        self.refresh_inv()
        self.refresh()
        self.refresh_service_panel()
        messagebox.showinfo("Delete Item", f"'{name}' was deleted from inventory.")

    def add_product(self, forced_type=None):
        w = tk.Toplevel(self)
        w.title("Add Service" if forced_type == "Service" else "Add Part / Material")
        w.geometry("390x430")
        item_type = tk.StringVar(value=forced_type or "Part/Material")
        vals = [tk.StringVar() for _ in range(6)]
        ttk.Label(w, text="Type").pack(anchor="w", padx=15, pady=(10, 0))
        box = ttk.Combobox(w, textvariable=item_type, values=["Service", "Part/Material"], state="readonly")
        box.pack(fill="x", padx=15)
        if forced_type: box.configure(state="disabled")
        entries = []
        for lab, var in zip(["Name", "Category", "Selling Price", "Unit Cost", "Stock", "Reorder Level"], vals):
            ttk.Label(w, text=lab).pack(anchor="w", padx=15, pady=(8, 0))
            e = ttk.Entry(w, textvariable=var); e.pack(fill="x", padx=15); entries.append(e)
        def toggle(*_):
            service = item_type.get() == "Service"
            if service: vals[4].set("0"); vals[5].set("0")
            entries[4].configure(state="disabled" if service else "normal")
            entries[5].configure(state="disabled" if service else "normal")
        box.bind("<<ComboboxSelected>>", toggle); toggle()
        def save():
            try:
                name = vals[0].get().strip()
                if not name: raise ValueError("Name is required.")
                category = vals[1].get().strip() or ("General Service" if item_type.get() == "Service" else "General")
                price = float(vals[2].get()); cost = float(vals[3].get())
                stock = 0 if item_type.get() == "Service" else float(vals[4].get())
                reorder = 0 if item_type.get() == "Service" else float(vals[5].get())
                if min(price, cost, stock, reorder) < 0: raise ValueError("Values cannot be negative.")
                c = con()
                try:
                    c.execute("INSERT INTO products(name,category,type,price,cost,stock,reorder) VALUES(?,?,?,?,?,?,?)", (name, category, item_type.get(), price, cost, stock, reorder))
                    c.commit()
                finally:
                    c.close()
                w.destroy(); self.refresh(); self.refresh_inv()
            except sqlite3.IntegrityError: messagebox.showerror("Error", "An item with that name already exists.")
            except Exception as e: messagebox.showerror("Error", str(e))
        ttk.Button(w, text="SAVE", command=save).pack(pady=12)

    def restock(self):
        s = self.it.selection()
        if not s: messagebox.showinfo("Restock", "Select an item first."); return
        vals = self.it.item(s[0])["values"]; pid, name, typ = vals[0], vals[1], vals[2]
        if typ == "Service":
            messagebox.showinfo("Service", "Services do not have stock and cannot be restocked."); return
        q = simpledialog.askfloat("Restock", f"Quantity to add for '{name}':", minvalue=0.01)
        if q is None: return
        c = con()
        try:
            c.execute("UPDATE products SET stock=stock+? WHERE id=?", (q, pid))
            c.execute("INSERT INTO restocks(product_id,qty,time,note) VALUES(?,?,?,?)", (pid, q, datetime.now().strftime("%Y-%m-%d %H:%M:%S"), ""))
            c.commit()
        finally:
            c.close()
        self.refresh_inv(); self.refresh(); self.refresh_charts(); messagebox.showinfo("Restock", f"Added {q:g} to stock for '{name}'.")

    # =========================================================
    # SERVICE RULES
    # =========================================================
    def manage_service_rules(self):
        w = tk.Toplevel(self); w.title("Service Applicability Rules"); w.geometry("700x500")
        ttk.Label(w, text="Choose which services are offered for each Part / Material.", font=("Arial", 11, "bold")).pack(anchor="w", padx=10, pady=8)
        frm = ttk.Frame(w); frm.pack(fill="both", expand=True, padx=10)
        left = ttk.LabelFrame(frm, text="Parts / Materials"); left.pack(side="left", fill="both", expand=True, padx=(0, 5))
        right = ttk.LabelFrame(frm, text="Services for selected part"); right.pack(side="left", fill="both", expand=True, padx=(5, 0))
        pt = ttk.Treeview(left, columns=("name",), show="headings"); pt.heading("name", text="Part / Material"); pt.column("name", width=230); pt.pack(fill="both", expand=True, padx=5, pady=5)
        srvs = ttk.Treeview(right, columns=("name", "applied"), show="headings"); srvs.heading("name", text="Service"); srvs.heading("applied", text="Applied?"); srvs.column("name", width=220); srvs.column("applied", width=80); srvs.pack(fill="both", expand=True, padx=5, pady=5)
        c = con(); products = c.execute("SELECT id,name FROM products WHERE type='Part/Material' AND active=1 ORDER BY name").fetchall(); services = c.execute("SELECT id,name FROM products WHERE type='Service' AND active=1 ORDER BY name").fetchall(); c.close()
        for p in products: pt.insert("", "end", iid=str(p["id"]), values=(p["name"],))
        def load_services(_=None):
            for x in srvs.get_children(): srvs.delete(x)
            sel = pt.selection()
            if not sel: return
            pid = int(sel[0]); c = con()
            applied = {r["service_id"] for r in c.execute("SELECT service_id FROM service_rules WHERE product_id=?", (pid,)).fetchall()}; c.close()
            for s in services: srvs.insert("", "end", iid=str(s["id"]), values=(s["name"], "YES" if s["id"] in applied else "NO"))
        pt.bind("<<TreeviewSelect>>", load_services)
        def toggle():
            ps = pt.selection(); ss = srvs.selection()
            if not ps or not ss: return
            pid, sid = int(ps[0]), int(ss[0]); c = con(); exists = c.execute("SELECT 1 FROM service_rules WHERE service_id=? AND product_id=?", (sid, pid)).fetchone()
            try:
                if exists: c.execute("DELETE FROM service_rules WHERE service_id=? AND product_id=?", (sid, pid))
                else: c.execute("INSERT INTO service_rules(service_id,product_id) VALUES(?,?)", (sid, pid))
                c.commit()
            finally:
                c.close()
            load_services()
            self.selected_product_id = pid; self.refresh_service_panel()
        ttk.Button(w, text="TOGGLE SERVICE FOR SELECTED PART", command=toggle).pack(pady=8)

    # =========================================================
    # EMPLOYEES
    # =========================================================
    def manage_employees(self):
        w = tk.Toplevel(self); w.title("Manage Employees"); w.geometry("560x620")
        name = tk.StringVar(); cs = tk.StringVar(value="0"); ci = tk.StringVar(value="0")
        ttk.Label(w, text="Add / update employee").pack(anchor="w", padx=15, pady=(10, 0))
        ttk.Label(w, text="Click an employee below to load their info for editing, adjust the %, then Save/Update.", font=("Arial", 8), foreground="#555").pack(anchor="w", padx=15)
        for lab, var in [("Name", name), ("Service Commission %", cs), ("Parts/Materials Commission %", ci)]:
            ttk.Label(w, text=lab).pack(anchor="w", padx=15, pady=(8, 0)); ttk.Entry(w, textvariable=var).pack(fill="x", padx=15)
        lst = ttk.Treeview(w, columns=("name", "cs", "ci"), show="headings", height=8)
        for col, head, wd in [("name", "Name", 180), ("cs", "Service %", 120), ("ci", "Parts/Materials %", 140)]: lst.heading(col, text=head); lst.column(col, width=wd)
        lst.pack(fill="both", expand=True, padx=15, pady=5)
        def on_select(event=None):
            s = lst.selection()
            if not s: return
            vals = lst.item(s[0], "values")
            name.set(vals[0]); cs.set(vals[1]); ci.set(vals[2])
        lst.bind("<<TreeviewSelect>>", on_select)
        def load():
            for x in lst.get_children(): lst.delete(x)
            c = con(); rows = c.execute("SELECT * FROM employees WHERE active=1 ORDER BY name").fetchall(); c.close()
            for r in rows: lst.insert("", "end", iid=str(r["id"]), values=(r["name"], r["comm_service"], r["comm_item"]))
        def save():
            try:
                n = name.get().strip(); s = float(cs.get() or 0); i = float(ci.get() or 0)
                if not n or s < 0 or i < 0: raise ValueError("Enter valid employee information.")
                c = con()
                try:
                    try: c.execute("INSERT INTO employees(name,comm_service,comm_item) VALUES(?,?,?)", (n, s, i))
                    except sqlite3.IntegrityError: c.execute("UPDATE employees SET comm_service=?,comm_item=?,active=1 WHERE name=?", (s, i, n))
                    c.commit()
                finally:
                    c.close()
                name.set(""); cs.set("0"); ci.set("0"); load(); self.refresh_employees()
            except Exception as e: messagebox.showerror("Employee", str(e))
        def remove():
            s = lst.selection()
            if not s: return
            c = con()
            try:
                c.execute("UPDATE employees SET active=0 WHERE id=?", (int(s[0]),))
                c.commit()
            finally:
                c.close()
            load(); self.refresh_employees()
        btn = ttk.Frame(w); btn.pack(fill="x", padx=15, pady=5)
        ttk.Button(btn, text="SAVE / UPDATE", command=save).pack(side="left", fill="x", expand=True)
        ttk.Button(btn, text="REMOVE SELECTED", command=remove).pack(side="left", fill="x", expand=True, padx=5)
        load()

    def refresh_employees(self):
        c = con(); names = [r["name"] for r in c.execute("SELECT name FROM employees WHERE active=1 ORDER BY name")]; c.close()
        if not names: names = ["Owner"]
        if hasattr(self, "emp_combo"):
            self.emp_combo["values"] = names
            if self.employee.get() not in names: self.employee.set(names[0])

    # =========================================================
    # DASHBOARD / REPORTS
    # =========================================================
    def build_dash(self):
        btnrow = ttk.Frame(self.rep); btnrow.pack(fill="x")
        for text, cmd in [("TODAY", self.today), ("MONTH", self.month), ("SALES HISTORY", self.history), ("ITEMS TO RESTOCK", self.restock_list), ("EMPLOYEE COMMISSIONS", self.commissions), ("RESET HISTORY", self.reset_transaction_history), ("REFRESH CHARTS", self.refresh_charts), ("BACKUP", self.backup), ("EXPORT CSV", self.export)]:
            ttk.Button(btnrow, text=text, command=cmd).pack(side="left", padx=3, pady=5)
        body = ttk.Frame(self.rep); body.pack(fill="both", expand=True)
        left = ttk.Frame(body); left.pack(side="left", fill="both", expand=True)
        right = ttk.Frame(body, width=430); right.pack(side="right", fill="y", padx=(10, 0)); right.pack_propagate(False)
        report_frame = ttk.Frame(left); report_frame.pack(fill="both", expand=True)
        self.rt = tk.Text(report_frame, font=("Consolas", 10), wrap="none")
        self.rt.tag_configure("daily_receipt", foreground="#16A34A")
        report_scroll = ttk.Scrollbar(report_frame, orient="vertical", command=self.rt.yview)
        self.rt.configure(yscrollcommand=report_scroll.set)
        self.rt.pack(side="left", fill="both", expand=True)
        report_scroll.pack(side="right", fill="y")
        self.fig = Figure(figsize=(4.6, 6.2), dpi=90); self.ax1 = self.fig.add_subplot(211); self.ax2 = self.fig.add_subplot(212); self.fig.tight_layout(pad=3.0)
        self.chart_canvas = FigureCanvasTkAgg(self.fig, master=right); self.chart_canvas.get_tk_widget().pack(fill="both", expand=True)

    def today(self):
        self.period_report("today")

    def month(self):
        self.period_report("month")

    def period_report(self, mode):
        c = con()
        if mode == "today":
            where = "date(s.time)=?"; param = datetime.now().strftime("%Y-%m-%d"); label = f"TODAY {param}"
            ewhere = "date(time)=?"
        else:
            where = "strftime('%Y-%m',s.time)=?"; param = datetime.now().strftime("%Y-%m"); label = f"MONTH {param}"
            ewhere = "strftime('%Y-%m',time)=?"
        s = c.execute(f"SELECT COUNT(*) n,COALESCE(SUM(total),0) v FROM sales s WHERE {where}", (param,)).fetchone()
        gp = c.execute(f"SELECT COALESCE(SUM((i.price-i.cost)*i.qty),0) v FROM items i JOIN sales s ON i.sale_id=s.id WHERE {where}", (param,)).fetchone()["v"]
        e = c.execute(f"SELECT COALESCE(SUM(amount),0) v FROM expenses WHERE {ewhere}", (param,)).fetchone()["v"]
        split = c.execute(f"""SELECT i.line_type type, COALESCE(SUM(i.total),0) total
                              FROM items i JOIN sales s ON i.sale_id=s.id WHERE {where}
                              GROUP BY i.line_type""", (param,)).fetchall()
        service_total = next((r["total"] for r in split if r["type"] == "Service"), 0.0)
        sales = []
        items_by_sale = {}
        if mode == "today":
            sales = c.execute(f"SELECT id,receipt,time,customer,total,payment FROM sales s WHERE {where} ORDER BY id", (param,)).fetchall()
            item_rows = c.execute(f"SELECT i.sale_id,i.name,i.qty,i.total FROM items i JOIN sales s ON i.sale_id=s.id WHERE {where} ORDER BY i.id", (param,)).fetchall()
            for item in item_rows:
                items_by_sale.setdefault(item["sale_id"], []).append(item)
        c.close()
        if mode == "today":
            lines = [label, ""]
            receipt_ranges = []
            if not sales:
                lines.append("(no sales today)")
            for receipt_number, sale in enumerate(sales, start=1):
                receipt_start = len(lines)
                lines.extend([
                    "=" * 44,
                    f"SALES RECEIPT #{receipt_number}: {sale['receipt']}",
                    f"DATE/TIME: {sale['time']}",
                    f"CUSTOMER: {sale['customer'] or 'Walk-in'}",
                    "-" * 44,
                ])
                for item in items_by_sale.get(sale["id"], []):
                    lines.append(f"{item['name'][:25]:25} {item['qty']:>4g} ₱{item['total']:>10,.2f}")
                lines.extend([
                    "-" * 44,
                    f"TOTAL: ₱{sale['total']:,.2f}",
                    f"PAYMENT: {sale['payment']}",
                    "",
                ])
                receipt_ranges.append((receipt_start, len(lines)))
            lines.extend([
                "=" * 44,
                "SUMMARY REPORT OF EVERYDAY SALE",
                f"DATE: {param}",
                f"TOTAL RECEIPTS: {s['n']}",
                "-" * 44,
                f"TOTAL SALES: ₱{s['v']:,.2f}",
                f"SERVICE SALES: ₱{service_total:,.2f}",
                f"PART/MATERIAL SALES: ₱{s['v'] - service_total:,.2f}",
                "=" * 44,
            ])
        else:
            lines = [label, f"Transactions: {s['n']}", f"Sales: ₱{s['v']:,.2f}", f"Service: ₱{service_total:,.2f}", f"Gross Profit: ₱{gp:,.2f}", f"Expenses: ₱{e:,.2f}", f"Estimated Net: ₱{gp-e:,.2f}", "", "REVENUE BREAKDOWN"]
            for r in split: lines.append(f"{r['type']}: ₱{r['total']:,.2f}")
        if mode != "today":
            self.show("\n".join(lines))
            return
        self.rt.delete("1.0", "end")
        self.rt.insert("1.0", "\n".join(lines))
        for start, end in receipt_ranges:
            self.rt.tag_add("daily_receipt", f"{start + 1}.0", f"{end + 1}.0")

    def history(self):
        c = con(); rs = c.execute("SELECT receipt,time,customer,total,payment FROM sales ORDER BY id DESC LIMIT 200").fetchall(); c.close()
        lines = ["RECEIPT          DATE/TIME            CUSTOMER                 TOTAL       PAYMENT", "-" * 90]
        if not rs:
            lines.append("(no transaction history recorded)")
        else:
            for r in rs: lines.append(f"{r['receipt']:<16}{r['time']:<21}{(r['customer'] or 'Walk-in')[:24]:<25}₱{r['total']:>9,.2f}  {r['payment']}")
        self.show("\n".join(lines))

    def reset_transaction_history(self):
        if not messagebox.askyesno("Reset Transaction History", "This will permanently remove all sales records and their item details. Continue?"):
            return

        try:
            c = con()
            c.execute("DELETE FROM items WHERE sale_id IN (SELECT id FROM sales)")
            c.execute("DELETE FROM sales")
            if c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='sqlite_sequence'").fetchone():
                c.execute("DELETE FROM sqlite_sequence WHERE name IN ('sales','items')")
            c.commit()
            c.close()
        except Exception as exc:
            messagebox.showerror("Reset Transaction History", f"Unable to clear sales history:\n{exc}")
            return

        self.refresh_charts()
        self.show("TRANSACTION HISTORY RESET\n\nAll sales records, item details, and dashboard totals have been cleared.")
        messagebox.showinfo("Reset Transaction History", "Transaction history has been successfully reset.")

    def refresh_charts(self):
        if not hasattr(self, "ax1"): return
        c = con()
        days = [(datetime.now() - timedelta(days=i)).strftime("%Y-%m-%d") for i in range(6, -1, -1)]
        totals = [c.execute("SELECT COALESCE(SUM(total),0) v FROM sales WHERE date(time)=?", (d,)).fetchone()["v"] for d in days]
        m = datetime.now().strftime("%Y-%m")
        cats = c.execute("""SELECT line_type type,SUM(i.total) v FROM items i JOIN sales s ON i.sale_id=s.id
                           WHERE strftime('%Y-%m',s.time)=? GROUP BY line_type""", (m,)).fetchall()
        c.close()
        self.ax1.clear(); self.ax2.clear()
        self.ax1.bar([d[5:] for d in days], totals); self.ax1.set_title("Sales – last 7 days", fontsize=10); self.ax1.tick_params(axis="x", labelsize=7, rotation=45); self.ax1.tick_params(axis="y", labelsize=7)
        if cats: self.ax2.pie([r["v"] for r in cats], labels=[r["type"] for r in cats], autopct="%1.0f%%", textprops={"fontsize": 7})
        else: self.ax2.text(.5, .5, "No sales this month", ha="center", va="center", fontsize=9)
        self.ax2.set_title("This month – Services vs Parts/Materials", fontsize=10); self.fig.tight_layout(pad=3.0); self.chart_canvas.draw()

    def restock_list(self):
        c = con(); rs = c.execute("SELECT name,category,stock,reorder FROM products WHERE active=1 AND type='Part/Material' AND reorder>0 AND stock<=reorder ORDER BY stock ASC").fetchall(); c.close()
        if not rs: self.show("ITEMS TO RESTOCK\n\nAll stock levels are healthy."); return
        lines = ["ITEMS TO RESTOCK", f"{'ITEM':<30}{'CATEGORY':<15}{'STOCK':>8}{'REORDER LVL':>13}", "-" * 66]
        for r in rs: lines.append(f"{r['name'][:29]:<30}{r['category'][:14]:<15}{r['stock']:>8g}{r['reorder']:>13g}")
        self.show("\n".join(lines))

    def calc_commissions(self, where, param):
        c = con(); rows = c.execute(f"SELECT s.cashier,i.qty,i.price,i.line_type FROM items i JOIN sales s ON i.sale_id=s.id WHERE {where}", (param,)).fetchall(); emps = {r["name"]:(r["comm_service"],r["comm_item"]) for r in c.execute("SELECT name,comm_service,comm_item FROM employees").fetchall()}; c.close()
        agg = {}
        total_sales = 0.0
        total_employee_commission = 0.0
        category_totals = {"Service": 0.0, "Part/Material": 0.0}
        category_employee_commission = {"Service": 0.0, "Part/Material": 0.0}
        for r in rows:
            name = r["cashier"] or "Owner"; cs, ci = emps.get(name, (0, 0)); amt = r["qty"] * r["price"]; rate = cs if r["line_type"] == "Service" else ci
            commission = amt * rate / 100
            d = agg.setdefault(name, {"sales": 0.0, "commission": 0.0, "service_commission": 0.0, "item_commission": 0.0}); d["sales"] += amt; d["commission"] += commission
            if r["line_type"] == "Service":
                d["service_commission"] += commission
            else:
                d["item_commission"] += commission
            total_sales += amt; total_employee_commission += commission
            category_totals[r["line_type"]] += amt; category_employee_commission[r["line_type"]] += commission
        if total_sales > 0:
            owner = agg.setdefault("Owner", {"sales": 0.0, "commission": 0.0, "service_commission": 0.0, "item_commission": 0.0}); owner["commission"] = max(0.0, total_sales - total_employee_commission)
            owner["service_commission"] = max(0.0, category_totals["Service"] - category_employee_commission["Service"])
            owner["item_commission"] = max(0.0, category_totals["Part/Material"] - category_employee_commission["Part/Material"])
        return agg

    def commissions(self):
        d = datetime.now().strftime("%Y-%m-%d"); m = datetime.now().strftime("%Y-%m")
        def fmt_receipts(where, param, label):
            c = con()
            sales = c.execute(f"SELECT id,receipt,time,cashier,total FROM sales s WHERE {where} ORDER BY id", (param,)).fetchall()
            items = c.execute(f"SELECT i.sale_id,i.qty,i.price,i.line_type FROM items i JOIN sales s ON i.sale_id=s.id WHERE {where} ORDER BY i.id", (param,)).fetchall()
            emps = {r["name"]: (r["comm_service"], r["comm_item"]) for r in c.execute("SELECT name,comm_service,comm_item FROM employees").fetchall()}
            c.close()
            items_by_sale = {}
            for item in items:
                items_by_sale.setdefault(item["sale_id"], []).append(item)
            lines = [label]
            if not sales:
                lines.append("(no sales)")
                return "\n".join(lines), None
            total_service_commission = 0.0
            total_item_commission = 0.0
            for receipt_number, sale in enumerate(sales, start=1):
                employee = sale["cashier"] or "Owner"
                service_sales = item_sales = 0.0
                service_commission = item_commission = 0.0
                for item in items_by_sale.get(sale["id"], []):
                    amount = item["qty"] * item["price"]
                    rate = emps.get(employee, (0, 0))[0 if item["line_type"] == "Service" else 1]
                    commission = amount * rate / 100
                    if item["line_type"] == "Service":
                        service_sales += amount
                        service_commission += commission
                    else:
                        item_sales += amount
                        item_commission += commission
                owner_service = max(0.0, service_sales - service_commission)
                owner_item = max(0.0, item_sales - item_commission)
                total_service_commission += owner_service if employee == "Owner" else service_commission
                total_item_commission += owner_item if employee == "Owner" else item_commission
                lines.extend([
                    "=" * 58,
                    f"COMMISSION RECEIPT #{receipt_number}: {sale['receipt']}",
                    f"DATE/TIME: {sale['time']}",
                    f"EMPLOYEE: {employee}",
                    f"SALE TOTAL: ₱{sale['total']:,.2f}",
                    "-" * 58,
                    f"SERVICE COMMISSION: ₱{owner_service if employee == 'Owner' else service_commission:,.2f}",
                    f"PARTS/MATERIAL COMMISSION: ₱{owner_item if employee == 'Owner' else item_commission:,.2f}",
                    "",
                ])
                if employee != "Owner":
                    lines.extend([
                        "OWNER COMMISSION:",
                        f"  SERVICE: ₱{owner_service:,.2f}",
                        f"  PARTS/MATERIAL: ₱{owner_item:,.2f}",
                        "",
                    ])
            lines.extend([
                "=" * 58,
                "COMMISSION SUMMARY",
                f"TOTAL SERVICE COMMISSION: ₱{total_service_commission:,.2f}",
                f"TOTAL PARTS/MATERIAL COMMISSION: ₱{total_item_commission:,.2f}",
                "=" * 58,
            ])
            summary_start = len(lines) - 5
            return "\n".join(lines), summary_start
        today_text, today_summary_start = fmt_receipts("date(s.time)=?", d, f"EMPLOYEE COMMISSIONS – TODAY ({d})")
        month_text, month_summary_start = fmt_receipts("strftime('%Y-%m',s.time)=?", m, f"EMPLOYEE COMMISSIONS – MONTH ({m})")
        self.rt.delete("1.0", "end")
        self.rt.insert("1.0", today_text + "\n\n" + month_text)
        self.rt.tag_configure("commission_today_summary", foreground="#16A34A")
        self.rt.tag_configure("commission_month_summary", foreground="#CA8A04")
        self.rt.tag_add("commission_today_summary", "1.0", "1.end")
        month_offset = today_text.count("\n") + 3
        self.rt.tag_add("commission_month_summary", f"{month_offset}.0", f"{month_offset}.end")
        if today_summary_start is not None:
            self.rt.tag_add("commission_today_summary", f"{today_summary_start + 1}.0", f"{today_summary_start + 6}.0")
        if month_summary_start is not None:
            self.rt.tag_add("commission_month_summary", f"{month_offset + month_summary_start + 1}.0", f"{month_offset + month_summary_start + 6}.0")

    def show(self, s):
        self.rt.delete("1.0", "end"); self.rt.insert("1.0", s)

    # =========================================================
    # EXPENSES
    # =========================================================
    def build_exp(self):
        self.ec = tk.StringVar(value="Other"); self.ed = tk.StringVar(); self.ea = tk.StringVar(); self.ep = tk.StringVar(value="Cash"); self.epayee = tk.StringVar()
        for i, (lab, var) in enumerate([("Category", self.ec), ("Description", self.ed), ("Amount", self.ea), ("Payee", self.epayee)]):
            ttk.Label(self.exp, text=lab).grid(row=0, column=i*2); ttk.Entry(self.exp, textvariable=var, width=18).grid(row=0, column=i*2+1)
        ttk.Label(self.exp, text="Payment").grid(row=1, column=0); ttk.Combobox(self.exp, textvariable=self.ep, values=["Cash", "GCash", "Bank Transfer", "Card"], state="readonly").grid(row=1, column=1)
        ttk.Button(self.exp, text="SAVE EXPENSE", command=self.save_exp).grid(row=1, column=2, pady=8)
        ttk.Button(self.exp, text="RESET EXPENSES", command=self.reset_expenses_history).grid(row=1, column=3, pady=8, padx=(4, 0))
        self.et = ttk.Treeview(self.exp, columns=("date", "cat", "desc", "amt", "pay", "payee"), show="headings")
        for col, head, width in [("date", "Date", 150), ("cat", "Category", 120), ("desc", "Description", 250), ("amt", "Amount", 100), ("pay", "Payment", 120), ("payee", "Payee", 180)]: self.et.heading(col, text=head); self.et.column(col, width=width)
        self.et.grid(row=2, column=0, columnspan=8, sticky="nsew", pady=10); self.exp.grid_rowconfigure(2, weight=1)
        for i in range(8): self.exp.grid_columnconfigure(i, weight=1)
        self.refresh_exp()

    def save_exp(self):
        try: a = float(self.ea.get())
        except ValueError: messagebox.showerror("Expense", "Invalid amount."); return
        if a < 0: messagebox.showerror("Expense", "Amount cannot be negative."); return
        c = con()
        try:
            c.execute("INSERT INTO expenses(time,category,description,amount,payment,payee) VALUES(?,?,?,?,?,?)", (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), self.ec.get(), self.ed.get(), a, self.ep.get(), self.epayee.get()))
            c.commit()
        finally:
            c.close()
        self.ea.set(""); self.ed.set(""); self.refresh_exp()

    def reset_expenses_history(self):
        if not messagebox.askyesno("Reset Expenses", "This will permanently remove all expense records. Continue?"):
            return
        try:
            c = con()
            c.execute("DELETE FROM expenses")
            if c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='sqlite_sequence'").fetchone():
                c.execute("DELETE FROM sqlite_sequence WHERE name='expenses'")
            c.commit(); c.close()
        except Exception as exc:
            messagebox.showerror("Reset Expenses", f"Unable to clear expense history:\n{exc}")
            return
        self.refresh_exp()
        self.show("EXPENSE HISTORY RESET\n\nAll expense records have been cleared.")
        messagebox.showinfo("Reset Expenses", "Expense history has been successfully reset.")

    def refresh_exp(self):
        if not hasattr(self, "et"): return
        for x in self.et.get_children(): self.et.delete(x)
        c = con(); rows = c.execute("SELECT * FROM expenses ORDER BY id DESC LIMIT 200").fetchall(); c.close()
        for r in rows: self.et.insert("", "end", values=(r["time"], r["category"], r["description"], f"₱{r['amount']:,.2f}", r["payment"], r["payee"]))

    # =========================================================
    # HELP
    # =========================================================
    def build_help(self):
        outer = ttk.Frame(self.hlp); outer.pack(fill="both", expand=True, padx=10, pady=10)
        txt = tk.Text(outer, font=("Segoe UI", 10), wrap="word", padx=14, pady=12, relief="flat")
        sb = ttk.Scrollbar(outer, orient="vertical", command=txt.yview); txt.configure(yscrollcommand=sb.set); sb.pack(side="right", fill="y"); txt.pack(side="left", fill="both", expand=True)
        txt.tag_configure("h1", font=("Segoe UI", 15, "bold"), spacing3=8); txt.tag_configure("h2", font=("Segoe UI", 11, "bold"), spacing1=10, spacing3=4); txt.tag_configure("body", font=("Segoe UI", 10), spacing3=4); txt.tag_configure("bullet", font=("Segoe UI", 10), lmargin1=18, lmargin2=32, spacing1=2); txt.tag_configure("note", font=("Segoe UI", 9, "italic"), lmargin1=18, lmargin2=18, spacing1=4, spacing3=8)
        def h1(s): txt.insert("end", s+"\n", "h1")
        def h2(s): txt.insert("end", s+"\n", "h2")
        def p(s): txt.insert("end", s+"\n", "body")
        def b(s): txt.insert("end", "• "+s+"\n", "bullet")
        def note(s): txt.insert("end", "Note: "+s+"\n", "note")
        h1("JCK Motorshop POS – Service / Parts Workflow")
        p("The POS intentionally keeps Parts/Materials and Services separate because they behave differently.")
        h2("1. PARTS / MATERIALS")
        b("The left side of SALES / POS shows physical products only: tires, tubes, valves, patches, etc.")
        b("Selecting a physical product shows the services that are applicable to it on the right.")
        h2("2. SERVICES / ADD-ONS")
        b("Services are NOT mixed into the Parts/Materials list and are NOT treated as inventory stock.")
        b("Examples: Tire Installation, Wheel Balancing, Tire Rotation, Tire Repair and Tire Patching.")
        b("The service panel shows only services configured for the selected product.")
        b("When a product is added, the POS automatically asks whether the customer wants any applicable services. The cashier can select multiple services or choose NO SERVICE / SKIP.")
        b("Applied services appear in the SERVICES box, separate from the physical-product cart.")
        h2("3. SERVICE RULES")
        b("Use INVENTORY → SERVICE RULES to decide which services apply to which parts/materials.")
        b("Example: Tire → Installation, Wheel Balancing, Rotation. Tube → Tube-related installation/repair services.")
        h2("4. CHECKOUT")
        b("The final total includes both physical products and applied services.")
        b("When the sale is completed, only Parts/Materials reduce inventory. Services never reduce stock.")
        b("The receipt lists Parts/Materials and Services in separate sections and shows which product each service was applied to.")
        h2("5. SPECIAL ORDER")
        b("Special Order is for a one-off physical item that you buy from a supplier and resell without maintaining it as regular inventory.")
        h2("6. REPORTING")
        b("Sales reports, charts and employee commissions distinguish Service revenue from Parts/Materials revenue.")
        note("This structure is designed so a tire sale can naturally become: Tire + Installation + Balancing, without putting the services into inventory.")
        txt.config(state="disabled")

    # =========================================================
    # BACKUP / EXPORT
    # =========================================================
    def backup(self):
        fn = os.path.join(BACKUP_DIR, "backup_" + datetime.now().strftime("%Y%m%d_%H%M%S") + ".db")
        shutil.copy2(DB, fn); messagebox.showinfo("Backup", f"Backup created:\n{fn}")

    def export(self):
        fn = filedialog.asksaveasfilename(defaultextension=".csv", filetypes=[("CSV", "*.csv")])
        if not fn: return
        c = con(); rows = c.execute("SELECT receipt,time,customer,total,payment,received,change,cashier FROM sales ORDER BY id").fetchall(); c.close()
        with open(fn, "w", newline="", encoding="utf-8-sig") as f:
            w = csv.writer(f); w.writerow(["Receipt", "Date/Time", "Customer", "Total", "Payment", "Received", "Change", "Cashier"]); w.writerows([list(r) for r in rows])
        messagebox.showinfo("Export", "CSV exported successfully.")


if __name__ == "__main__":
    init()
    POS().mainloop()
