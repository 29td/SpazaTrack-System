import datetime
import pyodbc
 
SERVER = r"(localdb)\MSSQLLocalDB"
DATABASE = "SpazaDB"
 
 
# ---------------------------------------------------------------- helpers
def get_connection():
    """Connect with Windows authentication, using the newest ODBC driver found."""
    # The old driver named just "SQL Server" cannot connect to LocalDB, so only
    # modern "ODBC Driver NN for SQL Server" drivers are used (highest version first).
    drivers = [d for d in pyodbc.drivers()
               if d.startswith("ODBC Driver") and d.endswith("for SQL Server")]
    if not drivers:
        raise SystemExit(
            "No modern SQL Server ODBC driver found (installed: "
            f"{pyodbc.drivers()}).\n"
            "Install 'Microsoft ODBC Driver 18 for SQL Server' and run again."
        )
    drivers.sort(key=lambda d: int(d.split()[2]), reverse=True)
    conn_str = (
        f"DRIVER={{{drivers[0]}}};SERVER={SERVER};DATABASE={DATABASE};"
        "Trusted_Connection=yes;TrustServerCertificate=yes;"
    )
    return pyodbc.connect(conn_str)
 
 
def money(value):
    return f"R{(value or 0):,.2f}"
 
 
def print_table(cursor, money_cols=()):
    """Print the rows of the last query as an aligned text table."""
    cols = [c[0] for c in cursor.description]
    rows = cursor.fetchall()
    if not rows:
        print("  (no records)")
        return
    data = []
    for r in rows:
        data.append([money(v) if cols[i] in money_cols else str(v if v is not None else "")
                     for i, v in enumerate(r)])
    widths = [max(len(cols[i]), *(len(row[i]) for row in data)) for i in range(len(cols))]
    print("  " + " | ".join(cols[i].ljust(widths[i]) for i in range(len(cols))))
    print("  " + "-+-".join("-" * w for w in widths))
    for row in data:
        print("  " + " | ".join(row[i].ljust(widths[i]) for i in range(len(cols))))
 
 
def ask_int(prompt, minimum=None, allow_blank=False):
    while True:
        text = input(prompt).strip()
        if allow_blank and text == "":
            return None
        try:
            value = int(text)
            if minimum is not None and value < minimum:
                print(f"  Enter a number of at least {minimum}.")
                continue
            return value
        except ValueError:
            print("  Please enter a whole number.")
 
 
def ask_decimal(prompt, minimum=0.0):
    while True:
        try:
            value = float(input(prompt).strip())
            if value < minimum:
                print(f"  Must be at least {minimum}.")
                continue
            return round(value, 2)
        except ValueError:
            print("  Please enter a valid amount, e.g. 12.50")
 
 
# ---------------------------------------------------------------- features
def show_stock(conn):
    cur = conn.cursor()
    cur.execute("""SELECT ProductID, ProductName, CategoryName, StockQty, ReorderLevel,
                          SellingPrice, StockStatus
                   FROM vw_ProductStock ORDER BY ProductName""")
    print("\n=== CURRENT STOCK ===")
    print_table(cur, money_cols=("SellingPrice",))
    cur.execute("SELECT SUM(StockValue) FROM vw_ProductStock")
    print(f"\n  Total stock value (at cost): {money(cur.fetchone()[0])}")
 
 
def show_low_stock(conn, product_ids=None):
    """Restock alerts. If product_ids is given, only check those products."""
    cur = conn.cursor()
    cur.execute("SELECT * FROM vw_LowStock ORDER BY StockQty")
    rows = cur.fetchall()
    if product_ids is not None:
        rows = [r for r in rows if r.ProductID in product_ids]
    if not rows:
        if product_ids is None:
            print("\n  All products are above their reorder level.")
        return
    print("\n  *** RESTOCK ALERT ***")
    for r in rows:
        state = "OUT OF STOCK" if r.StockQty == 0 else f"only {r.StockQty} left"
        print(f"  - {r.ProductName}: {state} (reorder level {r.ReorderLevel}). "
              f"Suggested order: {r.SuggestedOrderQty} from {r.SupplierName} ({r.SupplierPhone})")
 
 
def record_sale(conn):
    cur = conn.cursor()
    print("\n=== RECORD A SALE ===")
    cur.execute("SELECT ProductID, ProductName, StockQty, SellingPrice FROM Product ORDER BY ProductID")
    products = {r.ProductID: r for r in cur.fetchall()}
    print_table_rows = [(p.ProductID, p.ProductName, p.StockQty, money(p.SellingPrice))
                        for p in products.values()]
    for pid, name, qty, price in print_table_rows:
        print(f"  {pid:>3}. {name:<22} stock: {qty:<4} price: {price}")
 
    cart = {}  # product_id -> quantity
    while True:
        pid = ask_int("\nProduct ID to add (blank to finish): ", minimum=1, allow_blank=True)
        if pid is None:
            break
        if pid not in products:
            print("  That product does not exist.")
            continue
        p = products[pid]
        available = p.StockQty - cart.get(pid, 0)
        if available <= 0:
            print(f"  No more {p.ProductName} in stock.")
            continue
        qty = ask_int(f"  Quantity of {p.ProductName} (available {available}): ", minimum=1)
        if qty > available:
            print(f"  Not enough stock. Only {available} available.")
            continue
        cart[pid] = cart.get(pid, 0) + qty
        print(f"  Added. Cart total so far: "
              f"{money(sum(products[k].SellingPrice * v for k, v in cart.items()))}")
 
    if not cart:
        print("  Nothing sold.")
        return
 
    total = sum(products[k].SellingPrice * v for k, v in cart.items())
    print(f"\n  Total due: {money(total)}")
 
    methods = {"1": "Cash", "2": "Card", "3": "Credit"}
    choice = ""
    while choice not in methods:
        choice = input("  Payment: 1=Cash 2=Card 3=Credit : ").strip()
    method = methods[choice]
 
    customer_id = None
    if method == "Credit":
        cur.execute("SELECT CustomerID, CustomerName FROM Customer ORDER BY CustomerID")
        customers = cur.fetchall()
        for c in customers:
            print(f"    {c.CustomerID}. {c.CustomerName}")
        valid = {c.CustomerID for c in customers}
        while customer_id not in valid:
            customer_id = ask_int("  Customer ID for credit sale: ", minimum=1)
 
    try:
        cur.execute("INSERT INTO Sale (CustomerID, PaymentMethod) OUTPUT INSERTED.SaleID VALUES (?, ?)",
                    customer_id, method)
        sale_id = cur.fetchone()[0]
        for pid, qty in cart.items():
            p = products[pid]
            cur.execute("""INSERT INTO SaleItem (SaleID, ProductID, Quantity, UnitPrice, UnitCost)
                           SELECT ?, ProductID, ?, SellingPrice, CostPrice FROM Product WHERE ProductID = ?""",
                        sale_id, qty, pid)
        conn.commit()
    except pyodbc.Error as err:
        conn.rollback()
        print(f"\n  Sale NOT saved (database rejected it): {err.args[-1] if err.args else err}")
        return
 
    print(f"\n  Sale #{sale_id} saved. Receipt:")
    for pid, qty in cart.items():
        p = products[pid]
        print(f"    {p.ProductName:<22} {qty:>3} x {money(p.SellingPrice):>9} = {money(p.SellingPrice * qty):>10}")
    print(f"    {'TOTAL':<22} {'':>18} {money(total):>10}  ({method})")
    show_low_stock(conn, product_ids=set(cart))
 
 
def receive_stock(conn):
    cur = conn.cursor()
    print("\n=== RECEIVE STOCK FROM SUPPLIER ===")
    cur.execute("SELECT SupplierID, SupplierName FROM Supplier ORDER BY SupplierID")
    suppliers = cur.fetchall()
    for s in suppliers:
        print(f"  {s.SupplierID}. {s.SupplierName}")
    valid = {s.SupplierID for s in suppliers}
    supplier_id = None
    while supplier_id not in valid:
        supplier_id = ask_int("Supplier ID: ", minimum=1)
 
    cur.execute("SELECT ProductID, ProductName, StockQty, CostPrice FROM Product WHERE SupplierID = ?",
                supplier_id)
    products = {r.ProductID: r for r in cur.fetchall()}
    if not products:
        print("  This supplier has no products yet.")
        return
    for p in products.values():
        print(f"  {p.ProductID:>3}. {p.ProductName:<22} stock: {p.StockQty:<4} last cost: {money(p.CostPrice)}")
 
    items = [] 
    while True:
        pid = ask_int("\nProduct ID received (blank to finish): ", minimum=1, allow_blank=True)
        if pid is None:
            break
        if pid not in products:
            print("  That product is not supplied by this supplier.")
            continue
        if any(i[0] == pid for i in items):
            print("  Already added; finish and record a separate delivery if needed.")
            continue
        qty = ask_int("  Quantity received: ", minimum=1)
        cost = ask_decimal("  Unit cost (R): ")
        items.append((pid, qty, cost))
 
    if not items:
        print("  Nothing recorded.")
        return
    try:
        cur.execute("INSERT INTO Purchase (SupplierID) OUTPUT INSERTED.PurchaseID VALUES (?)", supplier_id)
        purchase_id = cur.fetchone()[0]
        for pid, qty, cost in items:
            cur.execute("INSERT INTO PurchaseItem (PurchaseID, ProductID, Quantity, UnitCost) VALUES (?,?,?,?)",
                        purchase_id, pid, qty, cost)
            # keep cost price current; selling price must stay >= cost price
            cur.execute("""UPDATE Product SET CostPrice = ?,
                                  SellingPrice = CASE WHEN SellingPrice < ? THEN ? ELSE SellingPrice END
                           WHERE ProductID = ?""", cost, cost, cost, pid)
        conn.commit()
        print(f"\n  Delivery #{purchase_id} recorded. Stock updated.")
    except pyodbc.Error as err:
        conn.rollback()
        print(f"\n  Delivery NOT saved: {err.args[-1] if err.args else err}")
 
 
def add_product(conn):
    cur = conn.cursor()
    print("\n=== ADD NEW PRODUCT ===")
    name = input("Product name: ").strip()
    if not name:
        print("  Name cannot be empty.")
        return
    cur.execute("SELECT CategoryID, CategoryName FROM Category")
    cats = cur.fetchall()
    for c in cats:
        print(f"  {c.CategoryID}. {c.CategoryName}")
    category_id = ask_int("Category ID: ", minimum=1)
    cur.execute("SELECT SupplierID, SupplierName FROM Supplier")
    for s in cur.fetchall():
        print(f"  {s.SupplierID}. {s.SupplierName}")
    supplier_id = ask_int("Supplier ID: ", minimum=1)
    cost = ask_decimal("Cost price (R): ")
    price = ask_decimal("Selling price (R): ", minimum=cost)
    reorder = ask_int("Reorder level (alert when stock reaches this): ", minimum=0)
    try:
        cur.execute("""INSERT INTO Product (ProductName, CategoryID, SupplierID, CostPrice, SellingPrice, ReorderLevel)
                       VALUES (?,?,?,?,?,?)""", name, category_id, supplier_id, cost, price, reorder)
        conn.commit()
        print("  Product added. Use 'Receive stock' to add its first stock.")
    except pyodbc.Error as err:
        conn.rollback()
        print(f"  Could not add product: {err.args[-1] if err.args else err}")
 
 
def daily_report(conn):
    cur = conn.cursor()
    text = input("\nDate (YYYY-MM-DD, blank for today): ").strip()
    try:
        day = datetime.date.fromisoformat(text) if text else datetime.date.today()
    except ValueError:
        print("  Invalid date format.")
        return
    print(f"\n=== DAILY REPORT: {day} ===")
    cur.execute("SELECT Transactions, Income, CostOfGoods, Profit FROM vw_DailySummary WHERE SaleDay = ?", day)
    row = cur.fetchone()
    if not row:
        print("  No sales recorded on this date.")
        return
    margin = (row.Profit / row.Income * 100) if row.Income else 0
    print(f"  Transactions : {row.Transactions}")
    print(f"  Income       : {money(row.Income)}")
    print(f"  Cost of goods: {money(row.CostOfGoods)}")
    print(f"  Profit       : {money(row.Profit)}  ({margin:.1f}% margin)")
 
    print("\n  Income by payment method:")
    cur.execute("""SELECT s.PaymentMethod, SUM(si.Quantity * si.UnitPrice) AS Income
                   FROM Sale s JOIN SaleItem si ON si.SaleID = s.SaleID
                   WHERE CAST(s.SaleDate AS DATE) = ? GROUP BY s.PaymentMethod""", day)
    print_table(cur, money_cols=("Income",))
 
    print("\n  Items sold:")
    cur.execute("""SELECT p.ProductName, SUM(si.Quantity) AS Units,
                          SUM(si.Quantity * si.UnitPrice) AS Income
                   FROM Sale s JOIN SaleItem si ON si.SaleID = s.SaleID
                   JOIN Product p ON p.ProductID = si.ProductID
                   WHERE CAST(s.SaleDate AS DATE) = ?
                   GROUP BY p.ProductName ORDER BY Units DESC""", day)
    print_table(cur, money_cols=("Income",))
 
 
def sales_history(conn):
    cur = conn.cursor()
    print("\n=== INCOME AND PROFIT BY DAY ===")
    cur.execute("SELECT SaleDay, Transactions, Income, CostOfGoods, Profit FROM vw_DailySummary ORDER BY SaleDay DESC")
    print_table(cur, money_cols=("Income", "CostOfGoods", "Profit"))
 
 
def top_sellers(conn):
    cur = conn.cursor()
    print("\n=== TOP 5 PRODUCTS BY UNITS SOLD ===")
    cur.execute("""SELECT TOP 5 p.ProductName, SUM(si.Quantity) AS UnitsSold,
                          SUM(si.Quantity * (si.UnitPrice - si.UnitCost)) AS Profit
                   FROM SaleItem si JOIN Product p ON p.ProductID = si.ProductID
                   GROUP BY p.ProductName ORDER BY UnitsSold DESC""")
    print_table(cur, money_cols=("Profit",))
    print("\n  Overstocked products (stock at least 5x reorder level):")
    cur.execute("SELECT ProductName, StockQty, ReorderLevel FROM vw_ProductStock WHERE StockStatus = 'OVERSTOCKED'")
    print_table(cur)
 
 
# ---------------------------------------------------------------- main menu
MENU = """
==============================
  SPAZA SHOP MANAGER
==============================
 1. Record a sale
 2. View stock
 3. Restock alerts
 4. Receive stock from supplier
 5. Daily income and profit report
 6. Income and profit by day
 7. Top sellers / overstock
 8. Add new product
 0. Exit
"""
 
ACTIONS = {
    "1": record_sale, "2": show_stock, "3": show_low_stock, "4": receive_stock,
    "5": daily_report, "6": sales_history, "7": top_sellers, "8": add_product,
}
 
 
def main():
    try:
        conn = get_connection()
    except pyodbc.Error as err:
        raise SystemExit(f"Could not connect to {SERVER}: {err}")
    print(f"Connected to {DATABASE} on {SERVER}.")
    show_low_stock(conn)  # alert the owner as soon as the app opens
    while True:
        print(MENU)
        choice = input("Choose an option: ").strip()
        if choice == "0":
            break
        action = ACTIONS.get(choice)
        if action:
            action(conn)
        else:
            print("Invalid option.")
    conn.close()
    print("Goodbye.")
 
 
if __name__ == "__main__":
    main()