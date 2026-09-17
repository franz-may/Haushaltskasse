import sqlite3

import numpy as np
import pandas as pd
import yaml

def ing_de(account):
    csv_file = account["csv_file"]
    df = pd.read_csv(csv_file, sep=';', encoding='utf-8')
    df["Verwendungszweck"] = df["Verwendungszweck"] + df["Auftraggeber/Empfänger"]
    csv_file = csv_file + 'ing_de'
    df.to_csv(csv_file, sep = ';')

# --- ADAPTER 1: Girokonto 1 (Beispiel: Trennzeichen Semikolon, deutsche Spalten) ---
def load_account(account, cat_map):
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


    # 3. Bedingungen automatisch für jede Kategorie erstellen
    conditions = [
        df["purpose"].str.contains(suchbegriffe, case=False, na=False)
        for suchbegriffe in cat_map.values()
    ]

    # 4. Die Kategorienamen als Zielwerte extrahieren
    choices = list(cat_map.keys())

    # 5. Spalte belegen ('default' greift, wenn keine Bedingung erfüllt ist)
    df["cat1"] = np.select(conditions, choices, default=None)

    # Datentypen bereinigen (Komma zu Punkt bei Zahlen)
    if df['amount'].dtype == 'object':
        df['amount'] = df['amount'].str.replace('.', '', regex=False).str.replace(',', '.', regex=False).astype(float)
    df['account'] = account["account"]  # Herkunft markieren
    return df


# --- HAUPTPROGRAMM ---
if __name__ == "__main__":
    # 1. Daten laden (Pfade zu Ihren echten CSVs anpassen)
    try:
        with open('conf/config.yaml', "r", encoding="utf-8") as f:
            config = yaml.safe_load(f)

        acc = []
        cat_map = config["cat_map"]
        for account in config["accounts"]:
            acc.append( load_account(account, cat_map) )

        # 2. Das SQL-"UNION ALL"
        # pd.concat klebt die DataFrames untereinander, solange die Spaltennamen identisch sind
        all_transactions = pd.concat(acc, ignore_index=True)
        
        # Datum sauber als Datetime-Objekt parsen
        all_transactions['date'] = pd.to_datetime(all_transactions['date'], dayfirst=True)

        # 2. Verbindung zur SQLite-Datenbank herstellen
        # Wenn die Datei 'finanzen.db' nicht existiert, wird sie automatisch erstellt!
        conn = sqlite3.connect('finanzen.db')
        
        # 3. Daten in die Datenbank schreiben (Das "INSERT/REPLACE")
        # if_exists='replace' überschreibt die Tabelle jedes Mal komplett neu.
        # Wenn Sie inkrementell Daten anhängen wollen (neue Monate), nutzen Sie 'append'.
        all_transactions.to_sql('transactions', conn, if_exists='replace', index=False)
        all_transactions.to_csv('data/transactions.csv', sep = ';')
        print("Daten erfolgreich in 'finanzen.db' in die Tabelle 'transactions' persistiert.")

        # 4. Der Beweis: Einlesen aus der Datenbank mit echtem SQL!
        query = """
            SELECT account, SUM(amount) as Gesamtumsatz 
            FROM transactions 
            WHERE amount < 0 
            GROUP BY account
        """
        df_auswertung = pd.read_sql_query(query, conn)
        
        print("\n--- Auswertung direkt via SQL-Query aus der DB ---")
        print(df_auswertung)
        
        # Verbindung schließen
        conn.close()

        # 3. Erste Auswertungen (Der SQL-Vergleich)
        #print("--- ALLE BUCHUNGEN (UNION) ---")
        #print(all_transactions.head())
        
        # Entspricht: SELECT account, SUM(amount) FROM all_transactions GROUP BY account;
        #print("\n--- KONTOSTÄNDE / UMSÄTZE PRO KONTO (GROUP BY) ---")
        #print(all_transactions.groupby('account')['amount'].sum())
        
    except FileNotFoundError as e:
        print(f"Fehler: CSV-Datei nicht gefunden. Bitte Pfade prüfen! ({e.filename})")
