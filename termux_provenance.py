import sqlite3
import hashlib
from datetime import datetime
import sys

DB_FILE = "termux_provenance.db"

def init_db():
    """Initialize the SQLite database with a tamper-evident audit table."""
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS audit_chain (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            data TEXT NOT NULL,
            parent_hash TEXT NOT NULL,
            current_hash TEXT NOT NULL
        )
    """)
    conn.commit()
    conn.close()

def get_latest_hash() -> str:
    """Retrieve the hash of the most recent block/entry in the chain."""
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("SELECT current_hash FROM audit_chain ORDER BY id DESC LIMIT 1")
    row = cursor.fetchone()
    conn.close()
    
    if not row:
        return "0" * 64
    return row[0]

def append_entry(data: str):
    """Append a new entry to the chain, linking it via parent-child hashing."""
    parent_hash = get_latest_hash()
    timestamp = datetime.utcnow().isoformat()
    
    # Compute current hash: SHA-256(parent_hash + timestamp + data)
    hasher = hashlib.sha256()
    hasher.update(f"{parent_hash}{timestamp}{data}".encode('utf-8'))
    current_hash = hasher.hexdigest()
    
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO audit_chain (timestamp, data, parent_hash, current_hash)
        VALUES (?, ?, ?, ?)
    """, (timestamp, data, parent_hash, current_hash))
    conn.commit()
    conn.close()
    print(f"[+] Appended: {data}")
    print(f"    Hash:   {current_hash}\n")

def verify_chain() -> bool:
    """Audit the entire SQLite chain to verify cryptographic integrity."""
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("SELECT id, timestamp, data, parent_hash, current_hash FROM audit_chain ORDER BY id ASC")
    rows = cursor.fetchall()
    conn.close()
    
    expected_parent = "0" * 64
    for row in rows:
        row_id, timestamp, data, parent_hash, current_hash = row
        
        if parent_hash != expected_parent:
            print(f"[X] Integrity failure at ID {row_id}: Parent hash mismatch.")
            return False
            
        hasher = hashlib.sha256()
        hasher.update(f"{parent_hash}{timestamp}{data}".encode('utf-8'))
        recalculated_hash = hasher.hexdigest()
        
        if recalculated_hash != current_hash:
            print(f"[X] Integrity failure at ID {row_id}: Tampering detected.")
            return False
            
        expected_parent = current_hash
        
    print("[V] CHAIN INTEGRITY VERIFIED: All cryptographic hashes match perfectly.\n")
    return True

def query_audit_logs():
    """Print all ledger entries."""
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("SELECT id, timestamp, data, current_hash FROM audit_chain")
    rows = cursor.fetchall()
    conn.close()
    
    print("--- LEDGER RECORDS ---")
    for row in rows:
        print(f"ID {row[0]} | {row[1]}")
        print(f"Data: {row[2]}")
        print(f"Hash: {row[3]}\n")

if __name__ == "__main__":
    init_db()
    
    print("=== BUILDING PROVENANCE CHAIN ===")
    append_entry("Termux environment initialized")
    append_entry("SHA-256 cryptographic provenance chain deployed")
    append_entry("SQLite ledger validation checkpoint passed")
    
    print("=== RUNNING INTEGRITY AUDIT ===")
    verify_chain()
    
    query_audit_logs()

