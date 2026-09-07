"""Cruza recordings/ com o SQLite: quais audios ja tem transcricao salva no BD.

Uso: python scripts/audit_recordings.py

Responde "posso arquivar/apagar os WAV/MP3 de recordings/?" — so e seguro mexer
nos que ja tem texto salvo em jobs.text. Os demais (job com erro/cancelado, ou
sem job nenhum) sao a UNICA copia daquele audio: nao mover sem retranscrever.

Abre o banco em modo read-only, entao pode rodar com o servidor no ar.
"""
import sqlite3, os, pathlib, collections

BASE = pathlib.Path(__file__).resolve().parent.parent
DB = BASE / "transcriptions" / "data.db"
REC = BASE / "recordings"

con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
con.row_factory = sqlite3.Row
cols = [r[1] for r in con.execute("PRAGMA table_info(jobs)")]
rows = con.execute("SELECT * FROM jobs").fetchall()

by_name = collections.defaultdict(list)
for r in rows:
    if r["file_path"]:
        by_name[os.path.basename(r["file_path"]).lower()].append(r)

def has_text(r):
    return bool(r["text"] and r["text"].strip())

print(f"jobs no BD: {len(rows)}  |  status: {dict(collections.Counter(r['status'] for r in rows))}")
print(f"jobs com texto salvo: {sum(1 for r in rows if has_text(r))}\n")

files = [p for p in REC.rglob("*") if p.is_file()]
ok, sem_texto, sem_job = [], [], []
for p in files:
    js = by_name.get(p.name.lower(), [])
    if not js:
        sem_job.append(p)
    elif any(has_text(j) for j in js):
        ok.append(p)
    else:
        sem_texto.append(p)

def gb(lst):
    return sum(p.stat().st_size for p in lst) / 1024**3

print(f"arquivos em recordings/: {len(files)}  ({gb(files):.2f} GB)")
print(f"  ARQUIVAVEL (texto no BD)    : {len(ok):5}  ({gb(ok):.2f} GB)")
print(f"  NAO mover (job sem texto)   : {len(sem_texto):5}  ({gb(sem_texto):.2f} GB)")
print(f"  NAO mover (sem job no BD)   : {len(sem_job):5}  ({gb(sem_job):.2f} GB)")

for titulo, lst in (("job sem texto", sem_texto), ("sem job no BD", sem_job)):
    if lst:
        print(f"\n--- maiores: {titulo} ---")
        for p in sorted(lst, key=lambda x: -x.stat().st_size)[:15]:
            print(f"  {p.stat().st_size/1024**2:9.1f} MB  {p.name}")

con.close()
