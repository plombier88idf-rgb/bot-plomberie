from datetime import datetime, timezone, timedelta
import sqlite3

PRICE_MAX_AGE_DAYS = 31


def ensure_pricing_tables(con):
    con.execute("""
        CREATE TABLE IF NOT EXISTS supplier_prices (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            supplier TEXT NOT NULL,
            brand TEXT,
            model TEXT,
            dn TEXT,
            part_type TEXT NOT NULL,
            part_ref TEXT,
            description TEXT,
            purchase_price_ht REAL NOT NULL,
            source TEXT NOT NULL,
            checked_at TEXT NOT NULL,
            notes TEXT,
            UNIQUE(supplier, part_ref, dn, part_type)
        )
    """)
    con.execute("""
        CREATE TABLE IF NOT EXISTS quote_simulations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id INTEGER,
            option_name TEXT NOT NULL,
            purchase_cost_ht REAL NOT NULL,
            labor_ht REAL NOT NULL,
            consumables_ht REAL NOT NULL,
            selling_price_ht REAL NOT NULL,
            gross_margin_ht REAL NOT NULL,
            supplier_price_ids TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
    """)
    con.commit()


def utcnow():
    return datetime.now(timezone.utc)


def price_age_days(checked_at):
    dt = datetime.fromisoformat(checked_at)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return (utcnow() - dt).days


def is_price_fresh(checked_at, max_age_days=PRICE_MAX_AGE_DAYS):
    return price_age_days(checked_at) <= max_age_days


def save_supplier_price(
    con,
    supplier,
    part_type,
    purchase_price_ht,
    source,
    checked_at,
    brand=None,
    model=None,
    dn=None,
    part_ref=None,
    description=None,
    notes=None,
):
    if not supplier or not source or not checked_at:
        raise ValueError("Fournisseur, source et date de contrôle du prix obligatoires.")
    if purchase_price_ht is None or float(purchase_price_ht) < 0:
        raise ValueError("Prix d'achat HT invalide.")

    con.execute("""
        INSERT INTO supplier_prices(
            supplier, brand, model, dn, part_type, part_ref, description,
            purchase_price_ht, source, checked_at, notes
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(supplier, part_ref, dn, part_type)
        DO UPDATE SET
            brand=excluded.brand,
            model=excluded.model,
            description=excluded.description,
            purchase_price_ht=excluded.purchase_price_ht,
            source=excluded.source,
            checked_at=excluded.checked_at,
            notes=excluded.notes
    """, (
        supplier, brand, model, dn, part_type, part_ref, description,
        float(purchase_price_ht), source, checked_at, notes
    ))
    con.commit()


def find_prices(con, *, brand=None, model=None, dn=None, part_type=None):
    sql = """
        SELECT id, supplier, brand, model, dn, part_type, part_ref, description,
               purchase_price_ht, source, checked_at, notes
        FROM supplier_prices
        WHERE 1=1
    """
    params = []

    if brand:
        sql += " AND lower(brand)=lower(?)"
        params.append(brand)
    if model:
        sql += " AND lower(model)=lower(?)"
        params.append(model)
    if dn:
        sql += " AND lower(dn)=lower(?)"
        params.append(dn)
    if part_type:
        sql += " AND lower(part_type)=lower(?)"
        params.append(part_type)

    sql += " ORDER BY purchase_price_ht ASC"
    rows = con.execute(sql, params).fetchall()

    results = []
    for row in rows:
        item = {
            "id": row[0],
            "supplier": row[1],
            "brand": row[2],
            "model": row[3],
            "dn": row[4],
            "part_type": row[5],
            "part_ref": row[6],
            "description": row[7],
            "purchase_price_ht": row[8],
            "source": row[9],
            "checked_at": row[10],
            "notes": row[11],
        }
        item["age_days"] = price_age_days(item["checked_at"])
        item["fresh"] = is_price_fresh(item["checked_at"])
        results.append(item)
    return results


def choose_fresh_price(prices):
    fresh = [p for p in prices if p["fresh"]]
    if not fresh:
        return None
    return min(fresh, key=lambda p: p["purchase_price_ht"])


def simulate_quote_option(
    *,
    purchase_cost_ht,
    labor_ht,
    consumables_ht,
    selling_price_ht,
):
    purchase_cost_ht = float(purchase_cost_ht)
    labor_ht = float(labor_ht)
    consumables_ht = float(consumables_ht)
    selling_price_ht = float(selling_price_ht)

    direct_cost = purchase_cost_ht + consumables_ht
    gross_margin_ht = selling_price_ht - direct_cost

    return {
        "purchase_cost_ht": round(purchase_cost_ht, 2),
        "labor_ht": round(labor_ht, 2),
        "consumables_ht": round(consumables_ht, 2),
        "selling_price_ht": round(selling_price_ht, 2),
        "gross_margin_ht": round(gross_margin_ht, 2),
    }


def require_verified_price(price):
    if price is None:
        raise ValueError("Aucun prix fournisseur vérifié disponible.")
    if not price.get("source"):
        raise ValueError("Prix fournisseur sans source : simulation refusée.")
    if not price.get("checked_at"):
        raise ValueError("Prix fournisseur sans date : simulation refusée.")
    if not price.get("fresh"):
        raise ValueError(
            f"Prix fournisseur trop ancien ({price.get('age_days')} jours) : mise à jour requise."
        )
    return price
