import numpy as np
import pandas as pd
import yaml
from pathlib import Path

from odf import opendocument
from odf.table import Table, TableRow, TableCell
from odf.text import P


def build_category_label(row):
    parts = [row.get('cat1', ''), row.get('cat2', ''), row.get('cat3', '')]
    return '_'.join(str(part).strip() for part in parts if str(part).strip() and str(part).strip() != 'nan')


def export_transactions_ods(df, output_path):
    ods_df = df.copy()
    ods_df['category'] = ods_df.apply(build_category_label, axis=1)

    columns = ['date', 'category', 'cat1', 'cat2', 'cat3', 'purpose', 'amount', 'account']
    data = ods_df.reindex(columns=columns, copy=False)

    doc = opendocument.OpenDocumentSpreadsheet()

    table_data = Table(name='Daten')
    header_row = TableRow()
    for col in data.columns:
        cell = TableCell(valuetype='string')
        cell.addElement(P(text=str(col)))
        header_row.addElement(cell)
    table_data.addElement(header_row)

    for _, row in data.iterrows():
        odf_row = TableRow()
        for value in row.tolist():
            if pd.isna(value):
                cell = TableCell(valuetype='string')
                cell.addElement(P(text=''))
            elif isinstance(value, pd.Timestamp):
                cell = TableCell(valuetype='string')
                cell.addElement(P(text=value.strftime('%Y-%m-%d')))
            elif isinstance(value, (int, float, np.integer, np.floating)):
                cell = TableCell(valuetype='float')
                cell.setAttribute('value', str(float(value)))
                cell.addElement(P(text=str(value)))
            else:
                cell = TableCell(valuetype='string')
                cell.addElement(P(text=str(value)))
            odf_row.addElement(cell)
        table_data.addElement(odf_row)

    doc.spreadsheet.addElement(table_data)

    categories = sorted(data['category'].dropna().astype(str).unique().tolist())
    table_summary = Table(name='Kategorien')
    header_row = TableRow()
    for col in ['Kategorie', 'Summe']:
        cell = TableCell(valuetype='string')
        cell.addElement(P(text=str(col)))
        header_row.addElement(cell)
    table_summary.addElement(header_row)

    for idx, category in enumerate(categories, start=2):
        summary_row = TableRow()

        name_cell = TableCell(valuetype='string')
        name_cell.addElement(P(text=str(category)))
        summary_row.addElement(name_cell)

        sum_formula = f"SUMMEWENN(Daten.B2:B{len(data)+1};Kategorien.A{idx};Daten.G2:G{len(data)+1})"
        sum_cell = TableCell(valuetype='float')
        sum_cell.setAttribute('formula', sum_formula)
        sum_cell.addElement(P(text=''))
        summary_row.addElement(sum_cell)

        table_summary.addElement(summary_row)

    doc.spreadsheet.addElement(table_summary)
    doc.save(str(output_path))


def export_sonstige_purposes(df, output_dir):
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    for label, cat1 in [("Ausgaben_Sonstige", "Ausgaben"), ("Einnahmen_Sonstige", "Einnahmen")]:
        rows = (
            df.loc[
                (df["cat1"].astype(str) == cat1)
                & (df["cat2"].astype(str) == "Sonstige")
                & df["purpose"].notna(),
                "purpose",
            ]
            .drop_duplicates()
            .astype(str)
            .sort_values()
        )
        content = "\n".join(f"- {purpose}" for purpose in rows)
        (output_dir / f"data/{label}.md").write_text(content + ("\n" if content else ""), encoding="utf-8")


def ing_de(account):
    csv_file = account["csv_file"]
    df = pd.read_csv(csv_file, sep=';', encoding='utf-8')
    df["Verwendungszweck"] = df["Verwendungszweck"] + df["Auftraggeber/Empfänger"]
    csv_file = csv_file + 'ing_de'
    df.to_csv(csv_file, sep = ';')


def load_account(account, cat_map, own_ibans):
    csv_file = account["csv_file"]
    if "pre_process" in account:
        if account["pre_process"] == "ing_de":
            ing_de(account)
            csv_file = csv_file + "ing_de"
    # Spalten umbenennen und nur die relevanten behalten (wie ein SELECT)
    df = pd.read_csv(csv_file, sep=';', encoding='utf-8')
    # 1. Nur die Spalten behalten, die als Keys im Dictionary existieren
    df = df[[col for col in account["column_mapping"] if col in df.columns]]

    # 2. Die verbleibenden Spalten umbenennen
    df = df.rename(columns=account["column_mapping"])
    #df = df.rename( columns = account["column_mapping"] )

    # Datentypen bereinigen (Komma zu Punkt bei Zahlen)
    if not pd.api.types.is_numeric_dtype(df['amount']):
        df['amount'] = (
            df['amount'].astype('string')
            .str.replace('.', '', regex=False)
            .str.replace(',', '.', regex=False)
        )
        df['amount'] = pd.to_numeric(df['amount'], errors='raise')

    recipient = df.get('recipient', pd.Series('', index=df.index)).astype('string')
    transfer_condition = pd.Series(False, index=df.index)
    for iban in own_ibans:
        transfer_condition |= recipient.str.contains(iban, case=False, regex=False, na=False)

    # 3. Bedingungen automatisch für jede Kategorie erstellen
    conditions = [transfer_condition]
    conditions.extend(
        df["purpose"].str.contains(suchbegriffe, case=False, na=False, regex=True)
        for suchbegriffe in cat_map.values()
    )

    # 4. Die Kategorienamen als Zielwerte extrahieren
    choices = ['Umbuchung'] + list(cat_map.keys())

    # 5. Spalte belegen; nicht zugeordnete Einträge nach Einnahmen/Ausgaben trennen
    conditions.extend([df['amount'] > 0, df['amount'] < 0])
    choices.extend(['Einnahmen_Sonstige', 'Ausgaben_Sonstige'])
    df["cat1"] = np.select(conditions, choices, default="Sonstige")
    categories = df["cat1"].str.split('_', n=2, expand=True)
    df["cat1"] = categories[0]
    df["cat2"] = categories[1].fillna('')
    df["cat3"] = categories[2].fillna('')

    df['account'] = account["account"]  # Herkunft markieren
    return df


# --- HAUPTPROGRAMM ---
if __name__ == "__main__":
    # 1. Daten laden (Pfade zu Ihren echten CSVs anpassen)
    try:
        project_root = Path(__file__).resolve().parent.parent
        config_path = project_root / 'conf' / 'config.yaml'
        cat_map_path = project_root / 'conf' / 'cat_map.yaml'

        with open(config_path, "r", encoding="utf-8") as f:
            config = yaml.safe_load(f)

        if cat_map_path.exists():
            with open(cat_map_path, "r", encoding="utf-8") as f:
                loaded_cat_map = yaml.safe_load(f) or {}
            cat_map = loaded_cat_map.get("cat_map", loaded_cat_map)
        else:
            cat_map = config.get("cat_map", {})

        if not isinstance(cat_map, dict):
            raise TypeError("cat_map muss ein Dictionary mit String-Werten sein.")

        cat_map = {key: str(value) for key, value in cat_map.items()}

        acc = []
        own_ibans = [item['iban'] for item in config['accounts']]
        for account in config["accounts"]:
            acc.append(load_account(account, cat_map, own_ibans))

        # 2. Das SQL-"UNION ALL"
        # pd.concat klebt die DataFrames untereinander, solange die Spaltennamen identisch sind
        all_transactions = pd.concat(acc, ignore_index=True)
        
        # Datum sauber als Datetime-Objekt parsen
        all_transactions['date'] = pd.to_datetime(all_transactions['date'], dayfirst=True)
        
        # 3. Daten in die Datenbank schreiben (Das "INSERT/REPLACE")
        # if_exists='replace' überschreibt die Tabelle jedes Mal komplett neu.       
        export_df = all_transactions[['date', 'cat1', 'cat2', 'cat3', 'purpose', 'amount', 'account']].copy()
        export_df.to_csv('data/transactions.csv', sep=';', decimal=',', index=False)

        try:
            export_transactions_ods(all_transactions, 'data/transactions.ods')
        except Exception as exc:
            print(f"Warnung: ODS-Export nicht möglich: {exc}")

        export_sonstige_purposes(all_transactions, Path(__file__).resolve().parent.parent)
        print("Daten erfolgreich in 'finanzen.db' in die Tabelle 'transactions' persistiert.")

        
        # Entspricht: SELECT account, SUM(amount) FROM all_transactions GROUP BY account;
        print("\n--- KONTOSTÄNDE / UMSÄTZE PRO KONTO (GROUP BY) ---")
        print(all_transactions.groupby('account')['amount'].sum())
        
    except FileNotFoundError as e:
        print(f"Fehler: CSV-Datei nicht gefunden. Bitte Pfade prüfen! ({e.filename})")
