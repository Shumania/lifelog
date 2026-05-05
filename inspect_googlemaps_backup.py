#!/usr/bin/env python3
"""
Inspect Google Maps data in iPhone backup.
Lists all Google Maps files and inspects any SQLite databases found.
"""

import sqlite3
import os
import sys
import json
import shutil
import hashlib

# Find backup directory
BACKUP_PATHS = [
    r"C:\Users\andre\Apple\MobileSync\Backup",
    r"C:\Users\andre\AppData\Roaming\Apple Computer\MobileSync\Backup",
    os.path.expanduser(r"~\Apple\MobileSync\Backup"),
    os.path.expanduser(r"~\AppData\Roaming\Apple Computer\MobileSync\Backup"),
]

def find_backup():
    for path in BACKUP_PATHS:
        if os.path.exists(path):
            backups = [d for d in os.listdir(path) if os.path.isdir(os.path.join(path, d))]
            if backups:
                # Pick most recently modified
                backups.sort(key=lambda d: os.path.getmtime(os.path.join(path, d)), reverse=True)
                return os.path.join(path, backups[0])
    return None

def get_backup_file(backup_dir, domain, relative_path):
    """Get the hashed filename for a backup file."""
    combined = f"{domain}-{relative_path}"
    hash_val = hashlib.sha1(combined.encode()).hexdigest()
    return os.path.join(backup_dir, hash_val[:2], hash_val)

def main():
    backup_dir = find_backup()
    if not backup_dir:
        print("ERROR: Could not find iPhone backup directory")
        sys.exit(1)
    
    print(f"Using backup: {backup_dir}")
    
    manifest_path = os.path.join(backup_dir, "Manifest.db")
    if not os.path.exists(manifest_path):
        print("ERROR: Manifest.db not found - backup may be encrypted or incomplete")
        sys.exit(1)
    
    # Find all Google Maps files
    conn = sqlite3.connect(manifest_path)
    cursor = conn.cursor()
    
    print("\n=== Google Maps files in backup ===")
    cursor.execute("""
        SELECT domain, relativePath, fileID, flags
        FROM Files 
        WHERE domain LIKE '%google%' OR domain LIKE '%Google%'
        ORDER BY domain, relativePath
    """)
    
    rows = cursor.fetchall()
    if not rows:
        print("No Google-related domains found.")
        # Try broader search
        cursor.execute("SELECT DISTINCT domain FROM Files WHERE domain LIKE '%com.google%'")
        domains = cursor.fetchall()
        print(f"Domains with 'com.google': {domains}")
    else:
        print(f"Found {len(rows)} files in Google domains:")
        for domain, rel_path, file_id, flags in rows:
            print(f"  [{domain}] {rel_path}")
    
    print("\n=== Looking for SQLite databases in Google Maps ===")
    cursor.execute("""
        SELECT domain, relativePath, fileID
        FROM Files 
        WHERE (domain LIKE '%google.Maps%' OR domain LIKE '%com.google.Maps%')
        AND (relativePath LIKE '%.sqlite' OR relativePath LIKE '%.db' OR relativePath LIKE '%.sqlite3')
        ORDER BY relativePath
    """)
    
    sqlite_files = cursor.fetchall()
    if sqlite_files:
        print(f"Found {len(sqlite_files)} SQLite files:")
        for domain, rel_path, file_id in sqlite_files:
            backup_file = os.path.join(backup_dir, file_id[:2], file_id)
            size = os.path.getsize(backup_file) if os.path.exists(backup_file) else 0
            print(f"  {rel_path} ({size:,} bytes) [fileID: {file_id}]")
            
            # Try to inspect the database
            if os.path.exists(backup_file) and size > 0:
                try:
                    tmp_copy = f"/tmp/gmaps_inspect_{file_id[:8]}.sqlite"
                    shutil.copy2(backup_file, tmp_copy)
                    db = sqlite3.connect(tmp_copy)
                    db_cursor = db.cursor()
                    db_cursor.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
                    tables = [r[0] for r in db_cursor.fetchall()]
                    print(f"    Tables: {tables}")
                    
                    # Look for location/timeline related tables
                    for table in tables:
                        if any(kw in table.lower() for kw in ['location', 'timeline', 'place', 'visit', 'trip', 'route', 'history']):
                            db_cursor.execute(f"SELECT COUNT(*) FROM [{table}]")
                            count = db_cursor.fetchone()[0]
                            db_cursor.execute(f"PRAGMA table_info([{table}])")
                            cols = [r[1] for r in db_cursor.fetchall()]
                            print(f"    *** {table}: {count} rows, columns: {cols}")
                    db.close()
                except Exception as e:
                    print(f"    Could not inspect: {e}")
    else:
        print("No SQLite databases found in Google Maps domain.")
    
    print("\n=== All files in Google Maps domain ===")
    cursor.execute("""
        SELECT domain, relativePath, fileID
        FROM Files 
        WHERE domain LIKE '%com.google.Maps%'
        ORDER BY relativePath
        LIMIT 100
    """)
    all_files = cursor.fetchall()
    for domain, rel_path, file_id in all_files:
        backup_file = os.path.join(backup_dir, file_id[:2], file_id)
        size = os.path.getsize(backup_file) if os.path.exists(backup_file) else 0
        print(f"  {rel_path} ({size:,} bytes)")
    
    # Also look for JSON files that might contain timeline data
    print("\n=== JSON files in Google Maps domain ===")
    cursor.execute("""
        SELECT domain, relativePath, fileID
        FROM Files 
        WHERE domain LIKE '%com.google.Maps%'
        AND relativePath LIKE '%.json'
        ORDER BY relativePath
    """)
    json_files = cursor.fetchall()
    for domain, rel_path, file_id in json_files:
        backup_file = os.path.join(backup_dir, file_id[:2], file_id)
        size = os.path.getsize(backup_file) if os.path.exists(backup_file) else 0
        print(f"  {rel_path} ({size:,} bytes)")
        if size > 0 and size < 100000:
            try:
                shutil.copy2(backup_file, f"/tmp/gmaps_{file_id[:8]}.json")
                with open(f"/tmp/gmaps_{file_id[:8]}.json") as f:
                    data = json.load(f)
                print(f"    Keys: {list(data.keys()) if isinstance(data, dict) else 'array'}")
            except:
                pass
    
    conn.close()
    print("\nDone! Share this output to identify Google Maps data structure.")

if __name__ == "__main__":
    main()
