#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from pathlib import Path
import re, unicodedata, os, json, shutil
from datetime import date
from urllib.parse import quote

# ===== CONFIG =====
PDF_DIR = Path("resources/pdfs")               # PDFs nuevos
READY_DIR = Path("resources/apuntes_Listos")   # PDFs procesados (destino)
APUNTES_BASE = Path("apuntes")                 # raíz malla
SITE_BASE_PDF_READY = "/resources/apuntes_Listos"  # URL pública PDFs movidos
STATE_FILE = Path(".apuntes_first_render.json")    # 1ra fecha de render
PREFERRED_CSS_NAME = "Styles_A.css"                # CSS dentro de /apuntes

# Fuerza una ruta fija al CSS en los .qmd (ignora profundidad de carpetas)
USE_FORCE_CSS = True
FORCE_CSS_PATH = "../../../Styles_A.css"          # Ruta fija deseada

MIGRAR_CARPETAS_A_SLUG = True                      # renombrar carpetas con slug (recomendado)
# ===================

# ---------- Utilidades ----------
def strip_accents(s): 
    return "".join(c for c in unicodedata.normalize("NFD", s) if unicodedata.category(c) != "Mn")

def norm_key(s):
    return re.sub(r"[^a-z0-9]+", "", strip_accents(s).lower())

def slugify(s):
    s2 = strip_accents(s).lower()
    s2 = re.sub(r"[^a-z0-9]+", "-", s2).strip("-")
    return s2 or "curso"

def smart_title(s):
    return re.sub(r"\s+", " ", s.replace("_", " ").strip()).title()

def yaml_escape(s): 
    return '"' + s.replace('"', '\\"') + '"'

# ---------- Autores / APA ----------
def split_authors(raw):
    txt = raw.strip().replace("&", " y ")
    parts = re.split(r"\s+y\s+|,\s*", txt)
    return [p for p in parts if p.strip()]

def looks_like_name_token(tok): 
    return bool(re.fullmatch(r"[A-Z][a-z'´’-]+", strip_accents(tok).strip()))

def strip_connectors(tokens):
    outs = []
    for t in tokens:
        tt = t.strip().lower()
        if tt in {"y","and","&"}: 
            continue
        outs.append(t)
    return outs

def name_to_apa(author):
    a = author.replace("_"," ").strip()
    parts = [t for t in re.split(r"\s+", a) if t]
    if len(parts) == 1: 
        return parts[0]
    def looks_last(x): 
        return bool(re.match(r"^[A-ZÁÉÍÓÚÑ][a-záéíóúñ'-]+$", x))
    if looks_last(parts[0]): 
        last, names = parts[0], parts[1:]
    else: 
        last, names = parts[-1], parts[:-1]
    initials = [(n.strip("-")[0].upper() + ".") for n in names if n.strip("-")]
    return f"{last}, {' '.join(initials)}".strip()

def join_authors_apa(lst):
    if not lst: return ""
    if len(lst)==1: return lst[0]
    if len(lst)==2: return f"{lst[0]} & {lst[1]}"
    return ", ".join(lst[:-1]) + f", & {lst[-1]}"

def to_yaml_authors(lst): 
    return ", ".join([f"\"{a}\"" for a in lst])

def make_citation_apa_html(autores_apa, anio, curso_hum, tema_hum):
    autores = join_authors_apa(autores_apa)
    titulo = f"{curso_hum}: {tema_hum}"
    return f"{autores} ({anio}). <em>{titulo}</em> [PDF]. Repositorio de Apuntes de Sociología, U. de Chile."

# ---------- Parseo de nombre ----------
def parse_filename(pdf_path: Path):
    # A) Curso_AAAA_Tema_Autor(es).pdf
    # B) Curso_AAAA_Tema_Apellido_Nombre[_Apellido_Nombre...].pdf
    stem = pdf_path.stem
    parts = stem.split("_")
    if len(parts) < 3: 
        raise ValueError(f"Nombre no cumple patrón mínimo: {pdf_path.name}")

    curso = parts[0]

    # año
    year_idx = None
    for i, p in enumerate(parts[1:], start=1):
        if re.fullmatch(r"\d{4}", p):
            year_idx = i
            break
    if year_idx is None: 
        raise ValueError(f"No se encontró año AAAA en: {pdf_path.name}")
    anio = parts[year_idx]

    tail = strip_connectors(parts[year_idx + 1 :])

    autores_list_display = []
    autores_apa = []

    # Detectar pares Apellido_Nombre desde el final
    i = len(tail) - 1
    pairs = []
    while i - 1 >= 0 and looks_like_name_token(tail[i]) and looks_like_name_token(tail[i - 1]):
        last = tail[i - 1]; first = tail[i]
        pairs.append((last, first)); i -= 2
    pairs.reverse()

    if pairs:
        tema_tokens = tail[: i + 1]
        tema = " ".join(tema_tokens).strip() or curso
        autores_list_display = [f"{ap} {no}" for (ap, no) in pairs]
        autores_apa = [name_to_apa(f"{ap}_{no}") for (ap, no) in pairs]
    else:
        if len(parts) <= year_idx + 1:
            tema = curso; raw_authors_field = ""
        else:
            pre_tema = parts[1:year_idx]
            post_tema = parts[year_idx + 1 : -1]
            tema = " ".join(pre_tema + post_tema).strip() or curso
            raw_authors_field = parts[-1]
        autores_raw = split_authors(raw_authors_field) if raw_authors_field else []
        autores_list_display = [a.replace("_", " ") for a in autores_raw]
        autores_apa = [name_to_apa(a) for a in autores_raw]

    return {
        "curso_raw": curso,
        "curso_hum": smart_title(curso),
        "anio": anio,
        "tema_hum": smart_title(tema),
        "autores_raw": autores_list_display,
        "autores_apa": autores_apa,
        "stem": stem
    }

# ---------- Indexar malla ----------
def index_course_dirs(base: Path):
    index = {}
    for anio_dir in base.glob("anio-*"):
        for sem_dir in anio_dir.glob("sem-*"):
            for course_dir in sem_dir.iterdir():
                if course_dir.is_dir():
                    k = norm_key(course_dir.name)
                    index.setdefault(k, []).append(course_dir)
    return index

# ---------- Migrar carpetas de curso a slugs (opcional) ----------
def migrar_curso_dirs_a_slug(base: Path):
    if not base.exists(): 
        return
    cambios = 0
    for anio in sorted(base.glob("anio-*")):
        for sem in sorted(anio.glob("sem-*")):
            for curso_dir in list(sem.iterdir()):
                if not curso_dir.is_dir():
                    continue
                human = curso_dir.name
                slug = slugify(human)
                if human == slug:
                    continue
                target = sem / slug
                if target.exists():
                    if not (target / "title.txt").exists():
                        (target / "title.txt").write_text(human, encoding="utf-8")
                    for p in curso_dir.iterdir():
                        destp = target / p.name
                        if not destp.exists():
                            shutil.move(str(p), str(destp))
                    try:
                        curso_dir.rmdir()
                    except OSError:
                        pass
                    cambios += 1
                    continue
                target.mkdir(parents=True, exist_ok=True)
                for p in curso_dir.iterdir():
                    shutil.move(str(p), str(target / p.name))
                (target / "title.txt").write_text(human, encoding="utf-8")
                try:
                    curso_dir.rmdir()
                except OSError:
                    pass
                cambios += 1
    if cambios:
        print(f"✓ Migradas {cambios} carpetas de curso a slugs (se guardó title.txt con el nombre humano).")

# ---------- CSS: ruta (forzada o calculada) ----------
def css_rel_from_course(destino: Path) -> str | None:
    """
    Si USE_FORCE_CSS es True, devuelve siempre FORCE_CSS_PATH.
    En caso contrario, calcula la ruta relativa hacia /apuntes/Styles_A.css.
    """
    css_abs = APUNTES_BASE / PREFERRED_CSS_NAME

    # Modo forzado: siempre la misma ruta fija
    if USE_FORCE_CSS:
        # (Opcional) Si quieres avisar cuando falte el archivo real:
        if not css_abs.exists():
            print(f"⚠️ Aviso: {css_abs} no existe, pero se insertará {FORCE_CSS_PATH} en los .qmd igualmente.")
        return FORCE_CSS_PATH

    # Modo automático (lógica previa)
    if not css_abs.exists():
        return None
    try:
        rel_parts = destino.relative_to(APUNTES_BASE).parts
    except ValueError:
        # Destino fuera de /apuntes: devolvemos ruta absoluta POSIX
        return css_abs.as_posix()
    up = Path(*([".."] * len(rel_parts))) if rel_parts else Path(".")
    return (up / PREFERRED_CSS_NAME).as_posix()

def build_css_block(css_ref: str | None) -> str:
    if css_ref:
        return "format:\n  html:\n    css: \"" + css_ref + "\"\n    code-fold: false"
    return "format:\n  html:\n    code-fold: false"

# ---------- Plantilla QMD ----------
QMD_TMPL_BASE = r'''---
title: {titulo_yaml}
page-layout: article
toc: false
categories: ["{curso_hum}", "{anio}"]
description: "{curso_hum} — {tema_hum}. PDF aportado por {autores_hum}."
date: {first_render_date}
author: [{autores_yaml}]
{css_block}
---

::: {{.page-surface}}

~~~{{=html}}
<div class="box-apa">
  <div class="box-apa-head">
    <span class="box-apa-kicker">Referencia (APA 7)</span>
  </div>
  <div class="box-apa-body">
    {cita_html}
  </div>
</div>
~~~

**Curso:** {curso_hum}  
**Año:** {anio}  
**Archivo PDF:** [{pdf_name}]({pdf_url})

---

### Vista del documento

<iframe src="{pdf_url}" width="100%" height="720" style="border:1px solid var(--borde); border-radius:12px;"></iframe>

:::
'''

# ---------- Estado ----------
def load_state():
    if STATE_FILE.exists():
        try: 
            return json.loads(STATE_FILE.read_text(encoding="utf-8"))
        except Exception: 
            return {}
    return {}

def save_state(state: dict):
    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")

# ---------- Safe format ----------
class Safe(dict):
    def __missing__(self, k): 
        return '{' + k + '}'

# ---------- Main ----------
def main():
    if not PDF_DIR.exists():
        print(f"No existe {PDF_DIR}."); return
    if not APUNTES_BASE.exists():
        print("No encuentro 'apuntes/'. ¿Ya creaste la malla?"); return

    # Migración de carpetas a slugs (una sola vez)
    if MIGRAR_CARPETAS_A_SLUG:
        migrar_curso_dirs_a_slug(APUNTES_BASE)

    READY_DIR.mkdir(parents=True, exist_ok=True)

    course_index = index_course_dirs(APUNTES_BASE)
    state = load_state(); state_dirty = False

    pdfs = sorted(PDF_DIR.glob("*.pdf"))
    if not pdfs:
        print("No se encontraron PDFs en", PDF_DIR); return

    generados = pendientes = 0

    for pdf in pdfs:
        try:
            meta = parse_filename(pdf)
        except Exception as e:
            print(f"Saltando {pdf.name} -> {e}"); pendientes += 1; continue

        # ubicar carpeta destino por clave normalizada del curso
        destinos = course_index.get(norm_key(meta["curso_raw"]), [])
        if not destinos:
            destino = APUNTES_BASE / "_pendiente" / slugify(meta["curso_hum"])
            destino.mkdir(parents=True, exist_ok=True)
            # title.txt con nombre humano del curso para que el index lo use
            (destino / "title.txt").write_text(meta["curso_hum"], encoding="utf-8")
        else:
            destino = destinos[0]

        fname = pdf.name

        # fecha primera vez
        if fname in state:
            first_render_date = state[fname]
        else:
            first_render_date = date.today().isoformat()
            state[fname] = first_render_date; state_dirty = True

        out_path = destino / (slugify(pdf.stem) + ".qmd")

        # CSS (forzado o automático)
        css_ref = css_rel_from_course(destino)
        css_block = build_css_block(css_ref)

        autores_hum = ", ".join(meta["autores_raw"])
        autores_yaml = to_yaml_authors(meta["autores_apa"])
        cita_html = make_citation_apa_html(meta["autores_apa"], meta["anio"], meta["curso_hum"], meta["tema_hum"])

        # mover PDF a "Listos" y construir URL pública (codificada)
        READY_DIR.mkdir(parents=True, exist_ok=True)
        ready_pdf_path = READY_DIR / fname
        try:
            if ready_pdf_path.exists():
                ready_pdf_path.unlink()
            shutil.move(str(pdf), str(ready_pdf_path))
            pdf_url = f"{SITE_BASE_PDF_READY}/{quote(fname)}"
        except Exception as e:
            print(f"⚠️ No se pudo mover {fname} a {READY_DIR}: {e}. Uso ruta original.")
            pdf_url = f"/{PDF_DIR.as_posix()}/{quote(fname)}"

        args = dict(
            titulo_yaml=yaml_escape(fname),
            curso_hum=meta["curso_hum"],
            anio=meta["anio"],
            tema_hum=meta["tema_hum"],
            autores_hum=autores_hum,
            autores_yaml=autores_yaml,
            cita_html=cita_html,
            pdf_name=fname,
            pdf_url=pdf_url,
            css_block=css_block,
            first_render_date=first_render_date,
        )

        qmd = QMD_TMPL_BASE.format_map(Safe(args))
        out_path.write_text(qmd, encoding="utf-8")
        generados += 1
        print(f"✓ Generado: {out_path}  (PDF → {ready_pdf_path})")

    if state_dirty:
        save_state(state)

    print(f"\nListo ✅  Generados: {generados} | Pendientes/omitidos: {pendientes}")
    if (APUNTES_BASE / "_pendiente").exists():
        print("ℹ️ Algunos apuntes fueron a /apuntes/_pendiente/ por falta de match con curso.")

if __name__ == "__main__":
    main()
